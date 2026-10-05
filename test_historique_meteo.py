#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de l'historique météo local, avec un faux Open-Meteo au format réel (archive 00 h-23 h, prévision past_days/forecast_days).

Attentes calculées à la main (aujourd'hui = 05/10/2026 12 h UTC) :
  archive du 01/01 au 28/09 = 271 jours -> 271 / 14 = 19,36 unités ; prévision de 8 jours passés + 7 à venir = 15 jours -> 1,07 ;
  série complète du 01/01 00 h au 11/10 23 h = 6 816 heures (6 504 d'archive + 312 de prévision)."""
import contextlib
import io
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import historique_meteo as hm
import recuperer_meteo_horaire as rm

UTC = timezone.utc
H = timedelta(hours=1)
MAINTENANT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LAT, LON = 49.25, 3.96


def base(t: datetime) -> float:
    """Température « vraie » d'une heure, unique par heure : les écarts de source se lisent dans la valeur."""
    return round(5 + int((t - datetime(2026, 1, 1, tzinfo=UTC)).total_seconds() // 3600) * 0.001, 3)


def heures(debut: datetime, fin: datetime):
    t = debut
    while t <= fin:
        yield t
        t += H


class FauxOpenMeteo:
    """Archive : valeur base(t). Prévision : base(t) + 0,5 + 0,1 × version (une prévision se révise)."""

    def __init__(self, maintenant, nulls_fin_archive=0, panne=False):
        self.maintenant, self.nulls, self.panne, self.version, self.appels = maintenant, nulls_fin_archive, panne, 0, []

    def __call__(self, url):
        if self.panne:
            raise RuntimeError("Open-Meteo injoignable")
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if "archive-api" in url:
            debut = datetime.fromisoformat(q["start_date"]).replace(tzinfo=UTC)
            fin = datetime.fromisoformat(q["end_date"]).replace(tzinfo=UTC) + timedelta(hours=23)
            ts = list(heures(debut, fin))
            temps = [None if i >= len(ts) - self.nulls else base(t) for i, t in enumerate(ts)]
            self.appels.append(("archive", q["start_date"], q["end_date"]))
        else:
            minuit = datetime.combine(self.maintenant.date(), datetime.min.time(), tzinfo=UTC)
            debut = minuit - timedelta(days=int(q["past_days"]))
            fin = minuit + timedelta(days=int(q["forecast_days"])) - H
            ts = list(heures(debut, fin))
            temps = [round(base(t) + 0.5 + 0.1 * self.version, 3) for t in ts]
            self.appels.append(("prevision", int(q["past_days"]), int(q["forecast_days"])))
        n = len(ts)
        return {"hourly": {"time": [t.strftime(hm.FORMAT) for t in ts], "temperature_2m": temps,
                           "relative_humidity_2m": [80.0] * n, "dew_point_2m": [5.0] * n, "precipitation": [0.0] * n}}


def lignes_synthetiques(debut: datetime, fin: datetime):
    return [{"time": t.strftime(hm.FORMAT), "temperature_2m": base(t), "relative_humidity_2m": 80.0, "dew_point_2m": 5.0,
             "precipitation": 0.0} for t in heures(debut, fin)]


class Base(unittest.TestCase):
    def setUp(self):
        self.dossier = tempfile.mkdtemp()
        self.chemin = os.path.join(self.dossier, "meteo.sqlite3")
        self.h = hm.Historique(self.chemin)
        self.faux = FauxOpenMeteo(MAINTENANT)

    def tearDown(self):
        shutil.rmtree(self.dossier, ignore_errors=True)

    def maj(self, now=None, **kw):
        now = now or MAINTENANT
        self.faux.maintenant = now
        return self.h.mettre_a_jour(LAT, LON, now=now, get=self.faux, **kw)

    def ligne(self, t: str):
        c = sqlite3.connect(self.chemin)
        try:
            return c.execute("SELECT temperature_2m, source FROM meteo_horaire WHERE t=?", (t,)).fetchone()
        finally:
            c.close()


class TestUnites(unittest.TestCase):
    def test_estimer_unites(self):
        self.assertEqual(hm.estimer_unites(7), 1.0)
        self.assertEqual(hm.estimer_unites(14), 1.0)
        self.assertEqual(hm.estimer_unites(271), 19.36)
        self.assertEqual(hm.estimer_unites(15), 1.07)
        self.assertEqual(hm.estimer_unites(28, variables=15), 3.0)           # exemple d'Open-Meteo : 4 semaines, 15 variables

    def test_cle_arrondie_a_deux_decimales(self):
        self.assertEqual(hm.Historique.cle(49.2549, 3.9551), "49.25_3.96")


class TestDemarrageAFroid(Base):
    def test_deux_appels_et_serie_complete(self):
        r = self.maj()
        self.assertEqual([a["type"] for a in r["appels"]], ["archive", "prevision"])
        self.assertEqual((r["appels"][0]["jours"], r["appels"][0]["unites"]), (271, 19.36))
        self.assertEqual((r["appels"][1]["jours"], r["appels"][1]["unites"]), (15, 1.07))
        self.assertEqual(r["unites"], 20.43)
        self.assertEqual(self.faux.appels[0], ("archive", "2026-01-01", "2026-09-28"))
        self.assertEqual(self.faux.appels[1], ("prevision", 8, 7))
        serie = self.h.serie(LAT, LON, 2026)
        self.assertEqual(len(serie), 6816)
        self.assertEqual((serie[0]["time"], serie[-1]["time"]), ("2026-01-01T00:00", "2026-10-11T23:00"))

    def test_priorite_des_sources(self):
        self.maj()
        self.assertEqual(self.ligne("2026-09-28T23:00"), (base(datetime(2026, 9, 28, 23, tzinfo=UTC)), "archive"))
        t = datetime(2026, 9, 29, 0, tzinfo=UTC)
        self.assertEqual(self.ligne("2026-09-29T00:00"), (round(base(t) + 0.5, 3), "prevision"))

    def test_une_nouvelle_demande_dans_la_duree_de_vie_ne_rappelle_rien(self):
        self.maj()
        n = len(self.faux.appels)
        r = self.maj(now=MAINTENANT + timedelta(minutes=30))
        self.assertEqual((r["appels"], r["unites"]), ([], 0.0))
        self.assertEqual(len(self.faux.appels), n)


class TestRegimeNormal(Base):
    def setUp(self):
        super().setUp()
        self.maj()
        self.faux.appels.clear()

    def test_apres_une_heure_seule_la_prevision_est_rappelee(self):
        r = self.maj(now=MAINTENANT + timedelta(hours=2))
        self.assertEqual([a["type"] for a in r["appels"]], ["prevision"])
        self.assertEqual(r["unites"], 1.07)                                   # 20 fois moins que le démarrage à froid

    def test_l_archive_n_est_jamais_ecrasee_par_une_prevision(self):
        self.maj(now=MAINTENANT + timedelta(hours=2))
        v = self.ligne("2026-09-28T23:00")                                    # dans la fenêtre de la prévision (depuis le 27/09)
        self.assertEqual(v[1], "archive")
        self.assertEqual(v[0], base(datetime(2026, 9, 28, 23, tzinfo=UTC)))

    def test_une_prevision_est_remplacee_par_une_prevision_plus_recente(self):
        t = datetime(2026, 10, 8, 12, tzinfo=UTC)
        self.assertEqual(self.ligne("2026-10-08T12:00")[0], round(base(t) + 0.5, 3))
        self.faux.version = 1
        self.maj(now=MAINTENANT + timedelta(hours=2))
        self.assertEqual(self.ligne("2026-10-08T12:00")[0], round(base(t) + 0.6, 3))

    def test_le_lendemain_une_petite_archive_consolide_les_jours_recents(self):
        demain = MAINTENANT + timedelta(days=1, hours=1)                      # 25 h plus tard : l'archive a plus de 24 h
        r = self.maj(now=demain)
        self.assertEqual([a["type"] for a in r["appels"]], ["archive", "prevision"])
        self.assertEqual((r["appels"][0]["jours"], r["appels"][0]["unites"]), (2, 1.0))
        self.assertEqual(self.faux.appels[0], ("archive", "2026-09-28", "2026-09-29"))
        v = self.ligne("2026-09-29T00:00")
        self.assertEqual(v[1], "archive")                                     # la prévision du 29/09 est devenue archive
        self.assertEqual(v[0], base(datetime(2026, 9, 29, tzinfo=UTC)))
        self.assertEqual(self.h.etat(LAT, LON, demain)["consolidee_jusqu_a"], "2026-09-29T23:00")

    def test_comptabilite_des_unites(self):
        u = self.h.usage(now=MAINTENANT)
        self.assertEqual((u["appels"], u["unites"]), (2, 20.43))
        self.assertEqual(u["aujourd_hui"], {"appels": 2, "unites": 20.43})
        self.maj(now=MAINTENANT + timedelta(hours=2))
        self.assertEqual(self.h.usage(now=MAINTENANT)["unites"], 21.5)


class TestDonneesImparfaites(Base):
    def test_heures_d_archive_sans_valeur_ne_sont_pas_stockees_comme_archive(self):
        self.faux.nulls = 24                                                  # le dernier jour d'archive (28/09) est vide
        self.maj()
        self.assertEqual(self.h.etat(LAT, LON, MAINTENANT)["consolidee_jusqu_a"], "2026-09-27T23:00")
        v = self.ligne("2026-09-28T12:00")
        self.assertEqual(v[1], "prevision")                                   # comblé par la prévision, pas par du vide
        self.assertEqual(v[0], round(base(datetime(2026, 9, 28, 12, tzinfo=UTC)) + 0.5, 3))
        self.assertEqual(len(self.h.serie(LAT, LON, 2026)), 6816)             # aucun trou

    def test_trous_comptes(self):
        self.maj()
        c = sqlite3.connect(self.chemin)
        c.execute("DELETE FROM meteo_horaire WHERE t BETWEEN '2026-05-10T00:00' AND '2026-05-10T04:00'")
        c.commit()
        c.close()
        t = self.h.trous(LAT, LON, 2026)
        self.assertEqual((t["heures_manquantes"], t["premier_trou_apres"]), (5, "2026-05-09T23:00"))
        self.assertEqual(self.h.etat(LAT, LON, MAINTENANT)["heures_manquantes"], 5)

    def test_l_annee_precedente_n_entre_pas_dans_la_serie(self):
        self.h.importer_lignes(LAT, LON, lignes_synthetiques(datetime(2025, 12, 31, 22, tzinfo=UTC),
                                                             datetime(2026, 1, 1, 1, tzinfo=UTC)),
                               consolide_jusqu_a=date(2026, 1, 1), now=MAINTENANT)
        self.assertEqual([r["time"] for r in self.h.serie(LAT, LON, 2026)], ["2026-01-01T00:00", "2026-01-01T01:00"])
        self.assertEqual(len(self.h.serie(LAT, LON, 2025)), 4)

    def test_positions_independantes(self):
        self.h.importer_lignes(LAT, LON, lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC),
                                                             datetime(2026, 1, 1, 2, tzinfo=UTC)), now=MAINTENANT)
        self.h.importer_lignes(48.0, 4.0, lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC),
                                                              datetime(2026, 1, 1, 0, tzinfo=UTC)), now=MAINTENANT)
        self.assertEqual((len(self.h.serie(LAT, LON, 2026)), len(self.h.serie(48.0, 4.0, 2026))), (3, 1))
        self.assertEqual(self.h.positions(), ["48.00_4.00", "49.25_3.96"])


class TestDebutDAnnee(Base):
    def test_au_3_janvier_pas_d_archive_et_la_prevision_couvre_le_1er(self):
        now = datetime(2026, 1, 3, 10, tzinfo=UTC)
        r = self.maj(now=now)
        self.assertEqual([a["type"] for a in r["appels"]], ["prevision"])
        self.assertEqual(self.faux.appels[0], ("prevision", 3, 7))
        self.assertEqual(r["unites"], 1.0)
        self.assertEqual(self.h.serie(LAT, LON, 2026)[0]["time"], "2026-01-01T00:00")


class TestModeSansArchive(Base):
    """Plan Standard : pas d'API historique. La série est amorcée par un import."""

    def test_sans_amorcage_la_serie_est_refusee_mais_le_telechargement_est_conserve(self):
        with self.assertRaises(hm.HistoriqueIncomplet) as e:
            self.maj(archive_autorisee=False)
        self.assertIn("ne commence pas le 1er janvier", str(e.exception))
        self.assertEqual(self.faux.appels, [("prevision", 92, 7)])            # 92 jours au plus : pas de remontée jusqu'en janvier
        self.assertGreater(len(self.h.serie(LAT, LON, 2026)), 2000)

    def test_apres_import_seules_des_previsions_sont_appelees(self):
        lignes = lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 11, 23, tzinfo=UTC))
        r = self.h.importer_lignes(LAT, LON, lignes, now=MAINTENANT)         # défaut : fiable jusqu'à J-7 (28/09)
        self.assertEqual((r["import"], r["prevision"], r["consolide_jusqu_a"]), (6504, 312, "2026-09-28"))
        self.assertEqual(self.ligne("2026-09-28T23:00")[1], "import")
        self.assertEqual(self.ligne("2026-09-29T00:00")[1], "prevision")
        rap = self.maj(archive_autorisee=False)
        self.assertEqual(self.faux.appels, [("prevision", 8, 7)])
        self.assertEqual(rap["unites"], 1.07)
        self.assertEqual(self.ligne("2026-09-28T23:00")[1], "import")        # jamais écrasé par la prévision
        self.assertEqual(len(self.h.serie(LAT, LON, 2026)), 6816)

    def test_sans_archive_une_longue_inactivite_laisse_un_trou_signale(self):
        lignes = lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 30, 23, tzinfo=UTC))
        self.h.importer_lignes(LAT, LON, lignes, consolide_jusqu_a=date(2026, 6, 30), now=MAINTENANT)
        self.maj(archive_autorisee=False)                                    # 97 jours plus tard : la fenêtre de 92 jours ne suffit pas
        t = self.h.trous(LAT, LON, 2026)
        self.assertGreater(t["heures_manquantes"], 0)
        self.assertEqual(t["premier_trou_apres"], "2026-06-30T23:00")


class TestConcurrenceEtPannes(Base):
    def test_un_seul_processus_rafraichit_a_la_fois(self):
        pos = hm.Historique.cle(LAT, LON)
        self.assertTrue(self.h._reserver(pos, MAINTENANT))
        self.assertFalse(self.h._reserver(pos, MAINTENANT))                  # déjà réservée
        r = self.maj()
        self.assertTrue(r["reserve"])
        self.assertEqual(self.faux.appels, [])
        self.h._liberer(pos)
        self.assertFalse(self.maj()["reserve"])

    def test_la_reservation_expire_toute_seule(self):
        pos = hm.Historique.cle(LAT, LON)
        self.assertTrue(self.h._reserver(pos, MAINTENANT, duree_s=120))
        self.assertTrue(self.h._reserver(pos, MAINTENANT + timedelta(minutes=3)))   # processus mort : on reprend la main

    def test_une_panne_reseau_remonte_sans_rien_perdre_et_libere_la_reservation(self):
        self.maj()
        avant = len(self.h.serie(LAT, LON, 2026))
        self.faux.panne = True
        with self.assertRaises(RuntimeError):
            self.maj(now=MAINTENANT + timedelta(hours=2))
        self.assertEqual(len(self.h.serie(LAT, LON, 2026)), avant)           # l'historique stocké reste utilisable
        self.faux.panne = False
        r = self.maj(now=MAINTENANT + timedelta(hours=2))
        self.assertFalse(r["reserve"])                                       # la réservation a bien été libérée
        self.assertEqual([a["type"] for a in r["appels"]], ["prevision"])


class TestLigneDeCommande(Base):
    def lancer(self, *args):
        s = io.StringIO()
        with contextlib.redirect_stdout(s):
            code = hm.main([*args, "--base", self.chemin])
        return code, s.getvalue()

    def test_importer_etat_exporter(self):
        csv_in = os.path.join(self.dossier, "meteo.csv")
        rm.ecrire_csv(lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, 23, tzinfo=UTC)), csv_in)
        code, sortie = self.lancer("importer", csv_in, "--lat", "49.25", "--lon", "3.96", "--jusqu-a", "2026-01-10")
        self.assertEqual(code, 0)
        self.assertIn("Importé : 240 heures fiables", sortie)
        code, sortie = self.lancer("etat")
        self.assertIn("49.25_3.96 : 240 h du 2026-01-01T00:00 au 2026-01-10T23:00", sortie)
        csv_out = os.path.join(self.dossier, "sortie.csv")
        code, sortie = self.lancer("exporter", "--lat", "49.25", "--lon", "3.96", "--sortie", csv_out, "--annee", "2026")
        self.assertIn("240 heures écrites", sortie)
        with open(csv_in, encoding="utf-8") as f1, open(csv_out, encoding="utf-8") as f2:
            self.assertEqual(f1.read(), f2.read())                                # aller-retour identique

    def test_importer_par_defaut_se_fie_a_la_date_du_fichier_pas_a_celle_du_jour(self):
        """Un CSV daté du 15/01 contient des prévisions après le 08/01 : elles ne doivent pas devenir « fiables »."""
        csv_in = os.path.join(self.dossier, "ancien.csv")
        rm.ecrire_csv(lignes_synthetiques(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 22, 23, tzinfo=UTC)), csv_in)
        ancien = datetime(2026, 1, 15, 12, tzinfo=UTC).timestamp()
        os.utime(csv_in, (ancien, ancien))
        code, sortie = self.lancer("importer", csv_in, "--lat", "49.25", "--lon", "3.96")
        self.assertIn("jusqu'au 2026-01-08", sortie)                          # 15/01 - 7 jours, et non aujourd'hui - 7
        self.assertEqual(self.ligne("2026-01-08T23:00")[1], "import")
        self.assertEqual(self.ligne("2026-01-09T00:00")[1], "prevision")      # la prévision reste corrigeable

    def test_usage(self):
        code, sortie = self.lancer("usage")
        self.assertEqual(code, 0)
        self.assertIn("0 requêtes, 0.0 unités", sortie)
        self.assertIn("offre gratuite : 10 000 par jour", sortie)

    def test_lat_lon_requis(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                hm.main(["mettre-a-jour", "--base", self.chemin])


if __name__ == "__main__":
    unittest.main(verbosity=1)
