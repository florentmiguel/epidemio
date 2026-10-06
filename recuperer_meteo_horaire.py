#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Météo horaire d'une position, du 1er janvier à la prévision (Open-Meteo)
========================================================================

Produit le CSV attendu par mildiou_primaire.py :
    time, temperature_2m, relative_humidity_2m, dew_point_2m, precipitation, wind_speed_10m, shortwave_radiation
(les deux dernières colonnes sont facultatives pour le moteur du mildiou ; le moteur de l'oïdium s'en sert : vent = dispersion des
conidies, rayonnement = proxy des ultraviolets)

* Passé : API « archive » (réanalyse), dont les données ont ~5 jours de délai.
* Jours récents + prévision : API de prévision avec past_days.
* L'archive prime quand elle a une valeur ; la prévision comble le reste.
* Toutes les heures sont en UTC (aucun décalage horaire à gérer).

Usage :
    python3 recuperer_meteo_horaire.py --lat 49.25 --lon 3.96 --sortie meteo.csv

NB : ce script n'a pu être testé qu'avec des réponses simulées. À valider sur le
VPS avec un vrai appel : `python3 recuperer_meteo_horaire.py --lat ... --lon ... --verifier`.
NB : l'API gratuite d'Open-Meteo est réservée à un usage non commercial.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

VARIABLES_BASE = ["temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"]
VARIABLES_COMPLEMENT = ["wind_speed_10m", "shortwave_radiation"]      # vent à 10 m (m/s), rayonnement global (W/m²)
VARIABLES = VARIABLES_BASE + VARIABLES_COMPLEMENT
URL_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
URL_PREVISION = "https://api.open-meteo.com/v1/forecast"


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "VITI-Sens-epidemio/0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corps = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Open-Meteo a répondu {e.code} : {corps}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Open-Meteo injoignable : {e.reason}") from e


def _lignes(data: dict) -> dict:
    """{'2026-04-12T00:00': {variable: valeur}} à partir du bloc hourly."""
    h = data.get("hourly") or {}
    out = {}
    for i, t in enumerate(h.get("time", [])):
        out[t] = {v: (h.get(v) or [None] * len(h["time"]))[i] for v in VARIABLES}
    return out


def url_archive(lat, lon, debut: date, fin: date) -> str:
    q = {"latitude": lat, "longitude": lon, "start_date": debut.isoformat(),
         "end_date": fin.isoformat(), "hourly": ",".join(VARIABLES), "wind_speed_unit": "ms"}
    return f"{URL_ARCHIVE}?{urllib.parse.urlencode(q)}"


def url_prevision(lat, lon, jours_passes: int, jours_futurs: int) -> str:
    q = {"latitude": lat, "longitude": lon, "hourly": ",".join(VARIABLES), "wind_speed_unit": "ms",
         "past_days": min(92, max(0, jours_passes)), "forecast_days": jours_futurs}
    return f"{URL_PREVISION}?{urllib.parse.urlencode(q)}"


def recuperer(lat, lon, annee=None, aujourdhui: date | None = None,
              decalage_archive_j: int = 7, jours_prevision: int = 7, get=_get_json) -> list[dict]:
    """Série horaire fusionnée, triée, du 1er janvier à la fin de la prévision."""
    aujourdhui = aujourdhui or datetime.now(timezone.utc).date()
    annee = annee or aujourdhui.year
    debut = date(annee, 1, 1)
    fin_archive = aujourdhui - timedelta(days=decalage_archive_j)

    lignes = {}
    if fin_archive >= debut:
        for t, vals in _lignes(get(url_archive(lat, lon, debut, fin_archive))).items():
            lignes[t] = vals
    # jours manquants entre la fin de l'archive et aujourd'hui + prévision
    jours_passes = (aujourdhui - max(debut, fin_archive)).days + 1
    for t, vals in _lignes(get(url_prevision(lat, lon, jours_passes, jours_prevision))).items():
        if t < debut.isoformat():
            continue
        existant = lignes.get(t)
        if existant is None or existant.get("temperature_2m") is None:
            lignes[t] = vals
    return [{"time": t, **lignes[t]} for t in sorted(lignes)]


def ecrire_csv(lignes: list[dict], chemin: str) -> None:
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time"] + VARIABLES)
        for r in lignes:
            w.writerow([r["time"]] + ["" if r.get(v) is None else r[v] for v in VARIABLES])


def verifier(lignes: list[dict]) -> str:
    """Contrôle rapide d'une vraie réponse : couverture, trous, valeurs manquantes."""
    if not lignes:
        return "AUCUNE donnée reçue"
    manquants = {v: sum(1 for r in lignes if r.get(v) is None) for v in VARIABLES}
    ts = [datetime.fromisoformat(r["time"]) for r in lignes]
    trous = sum(1 for a, b in zip(ts, ts[1:]) if b - a != timedelta(hours=1))
    return (f"{len(lignes)} heures, de {lignes[0]['time']} à {lignes[-1]['time']} (UTC)\n"
            f"trous dans la série : {trous}\n"
            f"valeurs manquantes : {manquants}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Météo horaire Open-Meteo depuis le 1er janvier")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--annee", type=int)
    ap.add_argument("--sortie", default="meteo_horaire.csv")
    ap.add_argument("--verifier", action="store_true", help="affiche un contrôle de la réponse")
    a = ap.parse_args(argv)
    try:
        lignes = recuperer(a.lat, a.lon, a.annee)
    except RuntimeError as e:
        sys.exit(f"Erreur : {e}")
    ecrire_csv(lignes, a.sortie)
    print(f"{a.sortie} écrit")
    if a.verifier:
        print(verifier(lignes))


if __name__ == "__main__":
    main()
