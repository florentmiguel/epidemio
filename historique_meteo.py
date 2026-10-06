#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Historique météo horaire local (SQLite)
=======================================

Le passé d'une saison ne change pas : le télécharger en entier à chaque calcul gaspille des appels (≈ 20 unités par
rafraîchissement). Ce module le garde sur disque et ne demande à Open-Meteo que ce qui manque.

Régime normal, par position (latitude / longitude arrondies à 2 décimales) :
  * démarrage à froid : l'archive du 1er janvier à J-7 (≈ 20 unités, une fois par an et par position), ou AUCUN appel si
    on importe un CSV existant (`importer`) ;
  * ensuite, à chaque rafraîchissement : UNE requête de prévision couvrant les jours non consolidés et les 7 jours à
    venir (≈ 1 unité) ;
  * une fois par jour : une petite requête d'archive pour consolider les jours récents (≈ 1 unité).

Règles de priorité (colonne « source ») :
  archive, import  = fiable : jamais écrasée par une prévision ;
  prevision        = provisoire : remplacée par une prévision plus récente, puis par l'archive.

Mode « sans archive » (--sans-archive, plan Standard d'Open-Meteo qui n'inclut pas l'API historique) : seule la prévision
(passé de 92 jours au plus) est appelée. La série doit alors avoir été amorcée par `importer` ; si elle ne commence pas
au 1er janvier, le module refuse de répondre plutôt que de laisser le moteur calculer sur une saison incomplète.

Unités d'appel : une requête de 14 jours et 10 variables au plus vaut 1 unité ; au-delà, au prorata (règle d'Open-Meteo).

Usage :
    python3 historique_meteo.py importer meteo.csv --lat 49.25 --lon 3.96 [--jusqu-a 2026-09-28]
    python3 historique_meteo.py mettre-a-jour --lat 49.25 --lon 3.96 [--sans-archive] [--force]
    python3 historique_meteo.py exporter --lat 49.25 --lon 3.96 --sortie meteo.csv
    python3 historique_meteo.py etat
Base : $EPIDEMIO_DATA_DIR/meteo.sqlite3 (défaut ~/epidemio_data), ou --base.

NB : l'API gratuite d'Open-Meteo est réservée à un usage non commercial.
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import recuperer_meteo_horaire as rm

UTC = timezone.utc
FORMAT = "%Y-%m-%dT%H:%M"
VARIABLES = rm.VARIABLES                      # 4 variables de base + vent à 10 m et rayonnement global (facultatifs)
COMPLEMENTS = rm.VARIABLES_COMPLEMENT         # une ligne sans vent ni rayonnement (ancien CSV) ne les efface jamais
SOURCES_FIABLES = ("archive", "import")
DECALAGE_ARCHIVE_J = 7                        # l'archive a ~5 jours de retard ; on s'en tient à J-7
JOURS_PREVISION = 7
PASSE_MAX_J = 92                              # maximum de « past_days » de l'API de prévision
MARGE_SANS_ARCHIVE_J = 3                      # sans archive : on redemande les 3 derniers jours déjà stockés

SCHEMA = """
CREATE TABLE IF NOT EXISTS meteo_horaire (
    position TEXT NOT NULL, t TEXT NOT NULL,
    temperature_2m REAL, relative_humidity_2m REAL, dew_point_2m REAL, precipitation REAL,
    wind_speed_10m REAL, shortwave_radiation REAL,
    source TEXT NOT NULL, maj TEXT NOT NULL,
    PRIMARY KEY (position, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS etat (position TEXT NOT NULL, cle TEXT NOT NULL, valeur TEXT, PRIMARY KEY (position, cle));
CREATE TABLE IF NOT EXISTS usage (jour TEXT PRIMARY KEY, appels INTEGER NOT NULL, unites REAL NOT NULL);
"""


class HistoriqueIncomplet(RuntimeError):
    """La série stockée ne commence pas au 1er janvier : le moteur calculerait sur une saison incomplète."""


def estimer_unites(jours: float, variables: int = len(VARIABLES)) -> float:
    """Unités d'appel d'une requête (règle d'Open-Meteo) : 1 jusqu'à 14 jours et 10 variables, puis au prorata."""
    return round(max(1.0, jours / 14.0) * max(1.0, variables / 10.0), 2)


def _heure(s: str) -> datetime:
    return datetime.strptime(s, FORMAT).replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def chemin_base() -> str:
    d = os.path.expanduser(os.environ.get("EPIDEMIO_DATA_DIR", "~/epidemio_data"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "meteo.sqlite3")


class Historique:
    def __init__(self, chemin: str | None = None):
        self.chemin = chemin or chemin_base()
        with self._connexion() as c:
            c.executescript(SCHEMA)
            self._migrer(c)

    @staticmethod
    def _migrer(c):
        """Base créée avant l'ajout du vent et du rayonnement : on ajoute les colonnes, les données existantes restent intactes
        (valeurs NULL jusqu'à un nouvel import ou à la prochaine prévision)."""
        existantes = {r["name"] for r in c.execute("PRAGMA table_info(meteo_horaire)")}
        for v in VARIABLES:
            if v not in existantes:
                c.execute(f"ALTER TABLE meteo_horaire ADD COLUMN {v} REAL")

    # -- infrastructure -------------------------------------------------------------------------------------------
    @contextmanager
    def _connexion(self):
        c = sqlite3.connect(self.chemin, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @staticmethod
    def cle(lat, lon) -> str:
        return f"{float(lat):.2f}_{float(lon):.2f}"

    def _etat_lire(self, c, pos, cle):
        r = c.execute("SELECT valeur FROM etat WHERE position=? AND cle=?", (pos, cle)).fetchone()
        return r["valeur"] if r else None

    def _etat_ecrire(self, c, pos, cle, valeur):
        c.execute("INSERT INTO etat(position, cle, valeur) VALUES (?,?,?) "
                  "ON CONFLICT(position, cle) DO UPDATE SET valeur=excluded.valeur", (pos, cle, valeur))

    def _age_s(self, c, pos, cle, now) -> float | None:
        v = self._etat_lire(c, pos, cle)
        return None if not v else (now - _heure(v)).total_seconds()

    def _bornes(self, c, pos, annee, fiable=False):
        """(première, dernière) heure stockées de l'année ; fiable : sources archive/import seulement."""
        filtre = " AND source IN ('archive','import')" if fiable else ""
        r = c.execute(f"SELECT MIN(t) AS a, MAX(t) AS b FROM meteo_horaire WHERE position=? AND t>=?{filtre}",
                      (pos, f"{annee}-01-01T00:00")).fetchone()
        return (_heure(r["a"]) if r["a"] else None, _heure(r["b"]) if r["b"] else None)

    def _compter(self, c, now, unites):
        c.execute("INSERT INTO usage(jour, appels, unites) VALUES (?,1,?) "
                  "ON CONFLICT(jour) DO UPDATE SET appels=appels+1, unites=unites+excluded.unites",
                  (now.strftime("%Y-%m-%d"), unites))

    # -- écriture ---------------------------------------------------------------------------------------------------
    def _ecrire(self, c, pos, lignes: dict, source: str, now: datetime) -> int:
        """Écrit {heure: {variable: valeur}}. Une ligne sans température est ignorée (l'archive n'a pas encore ces heures).
        Une prévision n'écrase JAMAIS une ligne fiable ; une ligne fiable écrase tout."""
        colonnes = ", ".join(VARIABLES)
        valeurs = ",".join("?" * len(VARIABLES))
        # vent et rayonnement : une ligne qui n'en a pas (ancien CSV) ne doit jamais effacer ceux déjà stockés
        maj = ", ".join(f"{nom}=COALESCE(excluded.{nom}, meteo_horaire.{nom})" if nom in COMPLEMENTS else f"{nom}=excluded.{nom}"
                        for nom in VARIABLES)
        sql = (f"INSERT INTO meteo_horaire(position, t, {colonnes}, source, maj) VALUES (?,?,{valeurs},?,?) "
               f"ON CONFLICT(position, t) DO UPDATE SET {maj}, source=excluded.source, maj=excluded.maj "
               "WHERE excluded.source IN ('archive','import') OR meteo_horaire.source='prevision'")
        n = 0
        for t, ligne in lignes.items():
            if ligne.get("temperature_2m") is None:
                continue
            c.execute(sql, (pos, t, *[ligne.get(nom) for nom in VARIABLES], source, _iso(now)))
            n += 1
        return n

    def importer_lignes(self, lat, lon, lignes: list[dict], consolide_jusqu_a: date | None = None,
                        now: datetime | None = None) -> dict:
        """Amorce l'historique avec des lignes existantes (CSV de recuperer_meteo_horaire). Les heures jusqu'à
        consolide_jusqu_a (inclus) sont « import » (fiables) ; les suivantes restent « prevision » (provisoires)."""
        now = now or datetime.now(UTC)
        limite = consolide_jusqu_a or (now.date() - timedelta(days=DECALAGE_ARCHIVE_J))
        fin = f"{limite.isoformat()}T23:59"
        fiables = {r["time"]: r for r in lignes if r["time"] <= fin}
        autres = {r["time"]: r for r in lignes if r["time"] > fin}
        pos = self.cle(lat, lon)
        with self._connexion() as c:
            a = self._ecrire(c, pos, fiables, "import", now)
            b = self._ecrire(c, pos, autres, "prevision", now)
        return {"import": a, "prevision": b, "consolide_jusqu_a": limite.isoformat()}

    # -- lecture ------------------------------------------------------------------------------------------------------
    def serie(self, lat, lon, annee: int) -> list[dict]:
        """Lignes au format d'Open-Meteo, triées, du 1er janvier de l'année à la dernière heure stockée."""
        with self._connexion() as c:
            rows = c.execute(
                f"SELECT t, {', '.join(VARIABLES)} FROM meteo_horaire "
                "WHERE position=? AND t>=? ORDER BY t", (self.cle(lat, lon), f"{annee}-01-01T00:00")).fetchall()
        return [{"time": r["t"], **{v: r[v] for v in VARIABLES}} for r in rows]

    def trous(self, lat, lon, annee: int) -> dict:
        """Heures manquantes entre la première et la dernière heure stockées."""
        horodatages = [r["time"] for r in self.serie(lat, lon, annee)]
        manquantes, premiere = 0, None
        for a, b in zip(horodatages, horodatages[1:]):
            ecart = int((_heure(b) - _heure(a)).total_seconds() // 3600) - 1
            if ecart > 0:
                manquantes += ecart
                premiere = premiere or a
        return {"heures_manquantes": manquantes, "premier_trou_apres": premiere}

    def positions(self) -> list[str]:
        with self._connexion() as c:
            return [r["position"] for r in c.execute("SELECT DISTINCT position FROM meteo_horaire ORDER BY position")]

    def etat(self, lat, lon, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        pos, annee = self.cle(lat, lon), now.year
        with self._connexion() as c:
            premiere, derniere = self._bornes(c, pos, annee)
            _, cons = self._bornes(c, pos, annee, fiable=True)
            n = c.execute("SELECT COUNT(*) AS n FROM meteo_horaire WHERE position=? AND t>=?",
                          (pos, f"{annee}-01-01T00:00")).fetchone()["n"]
            maj_prev, maj_arch = self._etat_lire(c, pos, "maj_prevision"), self._etat_lire(c, pos, "maj_archive")
        return {"position": pos, "annee": annee, "heures": n, "premiere": _iso(premiere) if premiere else None,
                "consolidee_jusqu_a": _iso(cons) if cons else None, "derniere": _iso(derniere) if derniere else None,
                "maj_prevision": maj_prev, "maj_archive": maj_arch, **self.trous(lat, lon, annee)}

    def usage(self, jours: int = 30, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        depuis = (now - timedelta(days=jours - 1)).strftime("%Y-%m-%d")
        with self._connexion() as c:
            r = c.execute("SELECT COALESCE(SUM(appels),0) AS a, COALESCE(SUM(unites),0) AS u FROM usage WHERE jour>=?",
                          (depuis,)).fetchone()
            j = c.execute("SELECT appels, unites FROM usage WHERE jour=?", (now.strftime("%Y-%m-%d"),)).fetchone()
        return {"jours": jours, "appels": r["a"], "unites": round(r["u"], 2),
                "aujourd_hui": {"appels": j["appels"] if j else 0, "unites": round(j["unites"], 2) if j else 0.0}}

    # -- mise à jour --------------------------------------------------------------------------------------------------
    def _reserver(self, pos: str, now: datetime, duree_s: int = 120) -> bool:
        """Un seul processus rafraîchit une position à la fois (deux workers gunicorn). Réservation de 2 minutes."""
        with self._connexion() as c:
            cur = c.execute("INSERT INTO etat(position, cle, valeur) VALUES (?, 'reserve_jusqu', ?) "
                            "ON CONFLICT(position, cle) DO UPDATE SET valeur=excluded.valeur "
                            "WHERE etat.valeur IS NULL OR etat.valeur < ?",
                            (pos, _iso(now + timedelta(seconds=duree_s)), _iso(now)))
            return cur.rowcount == 1

    def _liberer(self, pos: str):
        with self._connexion() as c:
            self._etat_ecrire(c, pos, "reserve_jusqu", "")

    def mettre_a_jour(self, lat, lon, now: datetime | None = None, get=None, archive_autorisee: bool = True,
                      ttl_prevision_s: int = 3600, ttl_archive_s: int = 86400, force: bool = False) -> dict:
        """Complète l'historique avec le minimum d'appels. Retourne un rapport {appels, unites, reserve, ...}.
        Lève HistoriqueIncomplet si la série ne démarre pas le 1er janvier ; toute erreur réseau remonte telle quelle
        (rien n'est perdu : ce qui était déjà stocké reste)."""
        now = now or datetime.now(UTC)
        get = get or rm._get_json
        pos, annee = self.cle(lat, lon), now.year
        debut_annee = date(annee, 1, 1)
        rapport = {"position": pos, "appels": [], "unites": 0.0, "reserve": False, "archive_autorisee": archive_autorisee}

        with self._connexion() as c:
            age_prev = self._age_s(c, pos, "maj_prevision", now)
            age_arch = self._age_s(c, pos, "maj_archive", now)
            _, cons = self._bornes(c, pos, annee, fiable=True)

        fin_archive = now.date() - timedelta(days=DECALAGE_ARCHIVE_J)
        depuis = None
        if archive_autorisee and fin_archive >= debut_annee:
            if cons is None:
                depuis = debut_annee                                          # démarrage à froid
            elif cons.date() < fin_archive and (force or age_arch is None or age_arch > ttl_archive_s):
                depuis = cons.date()                                          # consolidation quotidienne
        besoin_prevision = force or age_prev is None or age_prev > ttl_prevision_s
        if depuis is None and not besoin_prevision:
            return rapport

        if not self._reserver(pos, now):
            rapport["reserve"] = True                                         # un autre processus s'en occupe
            return rapport
        try:
            if depuis is not None:
                jours = (fin_archive - depuis).days + 1
                lignes = rm._lignes(get(rm.url_archive(float(lat), float(lon), depuis, fin_archive)))
                u = estimer_unites(jours)
                with self._connexion() as c:
                    self._ecrire(c, pos, lignes, "archive", now)
                    self._etat_ecrire(c, pos, "maj_archive", _iso(now))
                    self._compter(c, now, u)
                rapport["appels"].append({"type": "archive", "jours": jours, "unites": u})
                rapport["unites"] += u
            if besoin_prevision:
                with self._connexion() as c:
                    premiere, derniere = self._bornes(c, pos, annee)
                    _, cons = self._bornes(c, pos, annee, fiable=True)
                if cons is not None:
                    ref = cons
                elif derniere is not None:
                    ref = derniere - timedelta(days=MARGE_SANS_ARCHIVE_J)       # sans archive : fenêtre glissante
                else:
                    ref = None
                passes = (now.date() - (ref.date() if ref else debut_annee)).days + 1
                passes = max(1, min(PASSE_MAX_J, passes))
                lignes = rm._lignes(get(rm.url_prevision(float(lat), float(lon), passes, JOURS_PREVISION)))
                u = estimer_unites(passes + JOURS_PREVISION)
                with self._connexion() as c:
                    self._ecrire(c, pos, lignes, "prevision", now)
                    self._etat_ecrire(c, pos, "maj_prevision", _iso(now))
                    self._compter(c, now, u)
                rapport["appels"].append({"type": "prevision", "jours": passes + JOURS_PREVISION, "unites": u})
                rapport["unites"] += u
        finally:
            self._liberer(pos)

        rapport["unites"] = round(rapport["unites"], 2)
        with self._connexion() as c:
            premiere, _ = self._bornes(c, pos, annee)
        if premiere is None or premiere > datetime(annee, 1, 1, tzinfo=UTC) + timedelta(days=1):
            raise HistoriqueIncomplet(
                f"l'historique de {pos} ne commence pas le 1er janvier ({_iso(premiere) if premiere else 'vide'}) : "
                "le moteur calculerait sur une saison incomplète. Amorce-le avec « importer meteo.csv » ou autorise l'archive.")
        return rapport


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------
def lire_csv(chemin: str) -> list[dict]:
    with open(chemin, newline="", encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            out.append({"time": r["time"].strip(), **{v: (None if (r.get(v) or "").strip() == "" else float(r[v]))
                                                      for v in VARIABLES}})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Historique météo horaire local")
    ap.add_argument("action", choices=("importer", "mettre-a-jour", "exporter", "etat", "usage"))
    ap.add_argument("csv", nargs="?", help="CSV à importer (action importer)")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--base", help="fichier SQLite (défaut : $EPIDEMIO_DATA_DIR/meteo.sqlite3)")
    ap.add_argument("--sans-archive", action="store_true", help="n'appelle jamais l'API historique (plan Standard)")
    ap.add_argument("--force", action="store_true", help="ignore la durée de vie du cache")
    ap.add_argument("--jusqu-a", help="importer : dernier jour consolidé (AAAA-MM-JJ ; défaut : 7 jours avant la date du fichier)")
    ap.add_argument("--sortie", help="exporter : CSV de sortie")
    ap.add_argument("--annee", type=int, help="exporter : année (défaut : l'année en cours)")
    a = ap.parse_args(argv)
    h = Historique(a.base)

    if a.action == "usage":
        u = h.usage()
        print(f"30 derniers jours : {u['appels']} requêtes, {u['unites']} unités "
              f"(offre gratuite : 10 000 par jour, 300 000 par mois)")
        print(f"aujourd'hui : {u['aujourd_hui']['appels']} requêtes, {u['aujourd_hui']['unites']} unités")
        return 0
    if a.action == "etat" and a.lat is None:
        for pos in h.positions():
            lat, lon = (float(x) for x in pos.split("_"))
            e = h.etat(lat, lon)
            print(f"{pos} : {e['heures']} h du {e['premiere']} au {e['derniere']} (consolidé jusqu'au "
                  f"{e['consolidee_jusqu_a']}), {e['heures_manquantes']} h manquantes")
        return 0
    if a.lat is None or a.lon is None:
        ap.error("--lat et --lon sont requis")

    if a.action == "importer":
        if not a.csv:
            ap.error("indique le CSV à importer")
        if a.jusqu_a:
            jusqu = date.fromisoformat(a.jusqu_a)
        else:
            # Par défaut : fiable jusqu'à J-7 de la DATE DU FICHIER (pas d'aujourd'hui). Un CSV téléchargé il y a des jours
            # contient des prévisions qui ne doivent pas être prises pour des valeurs consolidées.
            cree = datetime.fromtimestamp(os.path.getmtime(a.csv), tz=UTC).date()
            jusqu = min(cree, datetime.now(UTC).date()) - timedelta(days=DECALAGE_ARCHIVE_J)
        r = h.importer_lignes(a.lat, a.lon, lire_csv(a.csv), jusqu)
        print(f"Importé : {r['import']} heures fiables (jusqu'au {r['consolide_jusqu_a']}), {r['prevision']} provisoires.")
    elif a.action == "mettre-a-jour":
        try:
            r = h.mettre_a_jour(a.lat, a.lon, archive_autorisee=not a.sans_archive, force=a.force)
        except HistoriqueIncomplet as e:
            print(f"Historique incomplet : {e}")
            return 1
        if r["reserve"]:
            print("Un autre processus rafraîchit déjà cette position.")
        for ap_ in r["appels"]:
            print(f"  appel {ap_['type']} : {ap_['jours']} jours = {ap_['unites']} unité(s)")
        print(f"Total : {r['unites']} unité(s).")
    elif a.action == "exporter":
        annee = a.annee or datetime.now(UTC).year
        lignes = h.serie(a.lat, a.lon, annee)
        if not a.sortie:
            ap.error("indique --sortie")
        rm.ecrire_csv(lignes, a.sortie)
        print(f"{len(lignes)} heures écrites dans {a.sortie}")
    elif a.action == "etat":
        e = h.etat(a.lat, a.lon)
        for k, v in e.items():
            print(f"  {k:22s}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
