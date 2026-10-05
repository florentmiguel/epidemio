#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la maturation de Rossi (équations 1 à 4 de Franche 2012), vérifiés à la main."""
import math
import unittest
from datetime import datetime, timedelta, timezone

import croiser_maturation as cm

UTC = timezone.utc
H = timedelta(hours=1)
DEBUT = datetime(2026, 1, 1, tzinfo=UTC)


def serie(n, temp=22.0, hr=100.0, pluie=0.0):
    return [{"t": DEBUT + i * H, "temp": temp, "hr": hr, "pluie": pluie, "rosee": None, "mouille": None} for i in range(n)]


class TestMaturationRossi(unittest.TestCase):
    def test_vpd_rossi_eq_2(self):
        self.assertAlmostEqual(cm.vpd_rossi(20.0, 50.0), 11.77, places=1)       # 0,5 x 6,11 x exp(17,47 x 20 / 259)
        self.assertEqual(cm.vpd_rossi(20.0, 100.0), 0.0)

    def test_litiere_humide(self):
        self.assertTrue(cm.litiere_humide(20.0, 30.0, 0.1))                      # pluie > 0
        self.assertFalse(cm.litiere_humide(20.0, 50.0, 0.0))                     # VPD 11,8 hPa : sèche
        self.assertFalse(cm.litiere_humide(20.0, None, 0.0))                     # HR absente : sèche

    def test_seuil_de_vpd_4_5(self):
        # à 20 °C, VPD = 4,5 hPa pour une HR d'environ 81 % : 82 % humide, 80 % sèche
        self.assertTrue(cm.litiere_humide(20.0, 82.0, 0.0))
        self.assertFalse(cm.litiere_humide(20.0, 80.0, 0.0))

    def test_ht_horaire_eq_1(self):
        self.assertAlmostEqual(cm.ht_horaire(22.0, True), 1 / 44.72, places=4)   # optimum : dénominateur minimal ≈ 44,7 h
        self.assertEqual(cm.ht_horaire(22.0, False), 0.0)
        self.assertGreater(cm.ht_horaire(22.0, True), cm.ht_horaire(5.0, True))  # le froid ralentit
        self.assertGreater(cm.ht_horaire(22.0, True), cm.ht_horaire(35.0, True))  # la chaleur aussi

    def test_denominateur_toujours_positif(self):
        for t in range(-10, 46):
            self.assertGreater(cm.ht_horaire(float(t), True), 0.0)

    def test_gompertz_eq_3_et_son_inverse(self):
        for p in (0.03, 0.5, 0.97):
            self.assertAlmostEqual(cm.levee_dormance(cm.ht_pour(p)), p, places=9)
        self.assertAlmostEqual(cm.ht_pour(0.03), 1.314, places=2)                # HT pour 3 % des oospores
        self.assertAlmostEqual(cm.ht_pour(0.97), 8.58, places=2)                 # HT pour 97 %
        self.assertLess(cm.levee_dormance(0.0), 0.001)                           # au 1er janvier : ≈ 0,03 %

    def test_serie_optimale_dates_attendues(self):
        # 22 °C et air saturé en continu : HT += 1/44,72 par heure ; 3 % à HT = 1,314 -> 59e heure
        res = cm.maturation_rossi(serie(24 * 40))
        ht_h = 1 / 44.72
        self.assertEqual(res["dates"][0.03], DEBUT + (math.ceil(1.314 / ht_h) - 1) * H)
        self.assertEqual(res["dates"][0.97], DEBUT + (math.ceil(8.58 / ht_h) - 1) * H)
        self.assertLess(res["dates"][0.03], res["dates"][0.5])
        self.assertLess(res["dates"][0.5], res["dates"][0.97])

    def test_air_sec_sans_pluie_jamais_de_maturation(self):
        res = cm.maturation_rossi(serie(24 * 120, hr=30.0))
        self.assertTrue(all(d is None for d in res["dates"].values()))
        self.assertLess(res["pmo_final"], 0.001)

    def test_la_pluie_suffit_a_humecter_la_litiere(self):
        res = cm.maturation_rossi(serie(24 * 40, hr=30.0, pluie=0.2))
        self.assertIsNotNone(res["dates"][0.03])

    def test_comparaison_avec_le_moteur(self):
        c = cm.comparer(serie(24 * 150, temp=12.0, hr=95.0), 49.25, 3.96)
        self.assertEqual(set(c["rossi"]), {"3 %", "50 %", "97 %"})
        self.assertIsNotNone(c["moteur"])
        self.assertIn("MATURATION DES OOSPORES", cm.formater(c))


if __name__ == "__main__":
    unittest.main(verbosity=1)
