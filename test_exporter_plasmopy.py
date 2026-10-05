#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de l'export au format Plasmopy."""
import os
import tempfile
import unittest
from datetime import datetime, timezone

import exporter_plasmopy as ep
import mildiou_primaire as mp

UTC = timezone.utc


def ligne(h, temp=12.34, hr=60.0, pluie=0.0, rosee=None):
    return {"t": datetime(2026, 5, 3, h, 0, tzinfo=UTC), "temp": temp, "hr": hr, "pluie": pluie, "rosee": rosee, "mouille": None}


class TestExport(unittest.TestCase):
    def test_format_de_ligne(self):
        l = ep.exporter([ligne(9, hr=85.0), ligne(10, pluie=1.25)])
        self.assertEqual(l[0], "03.05.2026 09:00;12.3;85;0.0;0")           # sec : 0 minute mouillée
        self.assertEqual(l[1], "03.05.2026 10:00;12.3;60;1.2;60")          # pluie : heure entièrement mouillée

    def test_entete_optionnel(self):
        sans = ep.exporter([ligne(9)])
        avec = ep.exporter([ligne(9)], entete=True)
        self.assertEqual(len(avec), len(sans) + 1)
        self.assertEqual(avec[0], ep.ENTETE)

    def test_l_humectation_suit_le_seuil_d_hr_du_moteur(self):
        self.assertTrue(ep.exporter([ligne(9, hr=92.0)])[0].endswith(";60"))         # >= 90 %
        self.assertTrue(ep.exporter([ligne(9, hr=88.0)])[0].endswith(";0"))
        souple = ep.exporter([ligne(9, hr=88.0)], params={"humectation": {"hr_pct": 85.0}})
        self.assertTrue(souple[0].endswith(";60"))

    def test_heures_sans_temperature_ignorees(self):
        self.assertEqual(len(ep.exporter([ligne(9), ligne(10, temp=None)])), 1)

    def test_aller_retour_avec_le_cli(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "m.csv"), os.path.join(d, "p.csv")
            with open(src, "w", encoding="utf-8") as f:
                f.write("time,temperature_2m,relative_humidity_2m,precipitation,dew_point_2m\n"
                        "2026-05-03T09:00,12.3,85,0.0,9.0\n2026-05-03T10:00,11.8,93,0.4,10.5\n")
            ep.main([src, "--sortie", dst, "--entete"])
            with open(dst, encoding="utf-8") as f:
                lignes = f.read().strip().split("\n")
        self.assertEqual(lignes[0], ep.ENTETE)
        self.assertEqual(lignes[1], "03.05.2026 09:00;12.3;85;0.0;0")
        self.assertEqual(lignes[2], "03.05.2026 10:00;11.8;93;0.4;60")


if __name__ == "__main__":
    unittest.main(verbosity=1)
