#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la récupération météo — réponses Open-Meteo SIMULÉES (pas d'accès réseau)."""
import os
import tempfile
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone

import mildiou_primaire as mp
import recuperer_meteo_horaire as rm

UTC = timezone.utc


def bloc(debut: datetime, fin: datetime, temp=10.0, nul=()):
    """Réponse hourly simulée entre deux instants (fin exclue) ; nul = heures sans température."""
    ts, t = [], debut
    while t < fin:
        ts.append(t.strftime("%Y-%m-%dT%H:%M"))
        t += timedelta(hours=1)
    return {"hourly": {
        "time": ts,
        "temperature_2m": [None if x in nul else temp for x in ts],
        "relative_humidity_2m": [70.0] * len(ts),
        "dew_point_2m": [5.0] * len(ts),
        "precipitation": [0.0] * len(ts),
    }}


class FauxServeur:
    def __init__(self, aujourdhui):
        self.appels = []
        self.aujourdhui = aujourdhui

    def __call__(self, url):
        self.appels.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "archive-api" in url:
            d = datetime.fromisoformat(q["start_date"][0]).replace(tzinfo=UTC)
            f = datetime.fromisoformat(q["end_date"][0]).replace(tzinfo=UTC) + timedelta(days=1)
            return bloc(d, f, temp=8.0)                      # archive : 8 °C
        passes = int(q["past_days"][0])
        futurs = int(q["forecast_days"][0])
        d = datetime.combine(self.aujourdhui - timedelta(days=passes), datetime.min.time(), tzinfo=UTC)
        f = datetime.combine(self.aujourdhui + timedelta(days=futurs), datetime.min.time(), tzinfo=UTC)
        return bloc(d, f, temp=15.0)                         # prévision : 15 °C


class TestRecuperation(unittest.TestCase):
    AUJ = date(2026, 4, 20)

    def test_serie_continue_du_1er_janvier_a_la_prevision(self):
        lignes = rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=FauxServeur(self.AUJ))
        ts = [datetime.fromisoformat(r["time"]) for r in lignes]
        self.assertEqual(ts[0], datetime(2026, 1, 1, 0, 0))
        self.assertEqual(ts[-1], datetime(2026, 4, 26, 23, 0))            # aujourd'hui + 6 jours
        self.assertTrue(all(b - a == timedelta(hours=1) for a, b in zip(ts, ts[1:])))
        self.assertEqual(len(ts), len(set(ts)))                           # aucun doublon

    def test_l_archive_prime_sur_la_prevision(self):
        lignes = {r["time"]: r for r in rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=FauxServeur(self.AUJ))}
        self.assertEqual(lignes["2026-03-01T12:00"]["temperature_2m"], 8.0)    # archive
        self.assertEqual(lignes["2026-04-18T12:00"]["temperature_2m"], 15.0)   # récent : prévision
        # chevauchement (jours couverts par les deux) : l'archive garde la main
        self.assertEqual(lignes["2026-04-13T12:00"]["temperature_2m"], 8.0)

    def test_la_prevision_comble_un_trou_recent_de_l_archive(self):
        class Trous(FauxServeur):
            def __call__(self, url):
                data = super().__call__(url)
                if "archive-api" in url:
                    data["hourly"]["temperature_2m"][-5] = None          # trou dans les derniers jours archivés
                return data
        lignes = {r["time"]: r for r in rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=Trous(self.AUJ))}
        self.assertEqual(lignes["2026-04-13T19:00"]["temperature_2m"], 15.0)   # comblé par la prévision

    def test_un_trou_hors_de_portee_de_la_prevision_reste_signale(self):
        class Trous(FauxServeur):
            def __call__(self, url):
                data = super().__call__(url)
                if "archive-api" in url:
                    data["hourly"]["temperature_2m"][5] = None          # 1er janvier 05:00
                return data
        lignes = rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=Trous(self.AUJ))
        self.assertIsNone({r["time"]: r for r in lignes}["2026-01-01T05:00"]["temperature_2m"])
        self.assertIn("'temperature_2m': 1", rm.verifier(lignes))

    def test_debut_d_annee_sans_archive(self):
        auj = date(2026, 1, 4)                                            # moins de 7 jours après le 1er
        srv = FauxServeur(auj)
        lignes = rm.recuperer(49.25, 3.96, aujourdhui=auj, get=srv)
        self.assertEqual(len(srv.appels), 1)                              # prévision seule
        self.assertEqual(lignes[0]["time"], "2026-01-01T00:00")

    def test_urls(self):
        u = rm.url_archive(49.25, 3.96, date(2026, 1, 1), date(2026, 4, 13))
        self.assertIn("start_date=2026-01-01", u)
        self.assertIn("end_date=2026-04-13", u)
        self.assertIn("precipitation", u)
        self.assertNotIn("timezone", u)                                   # défaut = GMT/UTC
        p = rm.url_prevision(49.25, 3.96, 500, 7)
        self.assertIn("past_days=92", p)                                  # plafonné à 92

    def test_csv_puis_moteur(self):
        lignes = rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=FauxServeur(self.AUJ))
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            chemin = f.name
        try:
            rm.ecrire_csv(lignes, chemin)
            rows = mp.charger_csv(chemin)
        finally:
            os.unlink(chemin)
        self.assertEqual(len(rows), len(lignes))
        res = mp.calculer_saison(rows, 49.25, 3.96, now=datetime(2026, 4, 20, 12, tzinfo=UTC))
        self.assertIsNotNone(res["maturation"]["dj_cumul"])
        self.assertEqual(res["avertissements"], [])

    def test_verification(self):
        lignes = rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=FauxServeur(self.AUJ))
        txt = rm.verifier(lignes)
        self.assertIn("trous dans la série : 0", txt)

    def test_erreur_reseau_message_clair(self):
        def en_panne(url):
            raise RuntimeError("Open-Meteo injoignable : timeout")
        with self.assertRaises(RuntimeError):
            rm.recuperer(49.25, 3.96, aujourdhui=self.AUJ, get=en_panne)


if __name__ == "__main__":
    unittest.main(verbosity=1)
