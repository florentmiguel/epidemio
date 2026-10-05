#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la carte du critère de sporulation (scénario synthétique : deux nuits humides à HR 93 %)."""
import contextlib
import csv
import io
import os
import tempfile
import unittest

import mildiou_primaire as mp
import sensibilite_sporulation as sp
from test_mildiou_primaire import DEBUT, FORCE_AVRIL, LAT, LON, fusion, plage, serie


def scenario(hr_nuit=93.0):
    """Infection primaire à h32 (12 °C), taches à h369 (27/04), puis 48 h d'air à HR 93 % et 12 °C : deux nuits entières."""
    r = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))
    r.update(plage(33, 1700, temp=12.0))
    r.update(plage(369, 417, hr=hr_nuit, temp=12.0))
    return serie(DEBUT, 1700, regles=r)


class TestCarteSporulation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = sp.grille(scenario(), LAT, LON, base=FORCE_AVRIL, hrs=(90, 92, 94), durees=(4, 6), du="04-20", au="05-10",
                          now=DEBUT)

    def test_un_seuil_d_humidite_sous_93_laisse_passer_les_deux_nuits(self):
        for hr in (90, 92):
            for d in (4, 6):
                self.assertEqual(self.g[(hr, d)]["nuits"], 2, (hr, d))
                self.assertEqual(self.g[(hr, d)]["taches"], 1)

    def test_un_seuil_au_dessus_de_93_supprime_la_sporulation(self):
        for d in (4, 6):
            self.assertEqual(self.g[(94, d)]["nuits"], 0)
            self.assertIsNone(self.g[(94, d)]["premiere"])
            self.assertEqual(self.g[(94, d)]["secondaires"], 0)

    def test_premiere_infection_secondaire_apres_la_sporulation(self):
        premiere = self.g[(92, 4)]["premiere"]
        self.assertIsNotNone(premiere)
        self.assertGreaterEqual(premiere, "2026-04-27")
        self.assertGreater(self.g[(92, 4)]["secondaires"], 0)

    def test_fenetre_sans_les_nuits(self):
        g = sp.grille(scenario(), LAT, LON, base=FORCE_AVRIL, hrs=(92,), durees=(4,), du="06-01", au="06-30", now=DEBUT)
        self.assertEqual(g[(92, 4)]["nuits"], 0)
        self.assertEqual(g[(92, 4)]["taches"], 0)

    def test_nuits_dans_compte_des_nuits_distinctes_pas_des_taches(self):
        res = {"cycles": [{"sporulations": ["2026-05-25T22:00Z", "2026-05-26T22:00Z"]},
                          {"sporulations": ["2026-05-25T22:00Z"]},               # même nuit, autre tache
                          {"sporulations": ["2026-08-01T22:00Z"]}]}              # hors fenêtre
        self.assertEqual(sp.nuits_dans(res, "05-20", "06-30"), (2, 2))
        self.assertEqual(sp.nuits_dans({"cycles": [{}]}, "05-20", "06-30"), (0, 0))

    def test_detail_liste_le_cycle_et_ses_deux_nuits(self):
        d = sp.detail(scenario(), LAT, LON, 92, 4, base=FORCE_AVRIL, du="04-20", au="05-10", now=DEBUT)
        self.assertEqual(len(d["cycles"]), 1)
        k = d["cycles"][0]
        self.assertEqual(len(k["nuits"]), 2)
        self.assertEqual(d["nuits"], k["nuits"])
        self.assertEqual(k["taches"][:10], "2026-04-27")
        self.assertEqual(k["fin_vie"], "2026-05-12")                              # 27/04 + 15 jours
        self.assertIsNotNone(d["premiere_secondaire"])

    def test_detail_montre_aussi_les_cycles_vivants_sans_nuit(self):
        d = sp.detail(scenario(), LAT, LON, 94, 4, base=FORCE_AVRIL, du="04-20", au="05-10", now=DEBUT)
        self.assertEqual([len(k["nuits"]) for k in d["cycles"]], [0])             # vivant, mais aucune nuit favorable
        self.assertEqual(d["nuits"], [])

    def test_detail_exclut_les_cycles_dont_les_taches_sont_mortes_ou_absentes(self):
        mort = sp.detail(scenario(), LAT, LON, 92, 4, base=FORCE_AVRIL, du="06-01", au="06-30", now=DEBUT)
        self.assertEqual(mort["cycles"], [])                                      # taches mortes le 12/05
        avant = sp.detail(scenario(), LAT, LON, 92, 4, base=FORCE_AVRIL, du="04-01", au="04-20", now=DEBUT)
        self.assertEqual(avant["cycles"], [])                                     # taches visibles le 27/04 seulement

    def test_detail_est_coherent_avec_la_grille(self):
        g = sp.grille(scenario(), LAT, LON, base=FORCE_AVRIL, hrs=(92,), durees=(4,), du="04-20", au="05-10", now=DEBUT)
        d = sp.detail(scenario(), LAT, LON, 92, 4, base=FORCE_AVRIL, du="04-20", au="05-10", now=DEBUT)
        self.assertEqual(len(d["nuits"]), g[(92, 4)]["nuits"])
        self.assertEqual(sum(1 for k in d["cycles"] if k["nuits"]), g[(92, 4)]["taches"])

    def test_format_du_detail(self):
        d = sp.detail(scenario(), LAT, LON, 92, 4, base=FORCE_AVRIL, du="04-20", au="05-10", now=DEBUT)
        txt = sp.formater_detail(d, "92", 4, "04-20", "05-10")
        self.assertIn("DÉTAIL : HR >= 92 %, 4 h", txt)
        self.assertIn("Un « cycle » est un épisode d'infection primaire", txt)
        self.assertIn("1 cycle(s) avec des taches vivantes dans la fenêtre, dont 1 avec au moins une nuit", txt)
        self.assertIn("2 nuit(s) distincte(s)", txt)

    def test_ligne_de_commande_detail(self):
        rows = scenario()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            for r in rows:
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], r["pluie"], ""])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                sp.main([chemin, "--lat", "49.25", "--lon", "4.03", "--du", "04-20", "--au", "05-10", "--detail", "90", "4"])
        finally:
            os.unlink(chemin)
        self.assertIn("DÉTAIL : HR >= 90 %, 4 h", s.getvalue())
        self.assertNotIn("CRITÈRE DE SPORULATION", s.getvalue())

    def test_format_lisible(self):
        txt = sp.formater(self.g, "04-20", "05-10", hrs=(90, 92, 94), durees=(4, 6), reperes=sp.REPERES)
        self.assertIn("NUITS DE SPORULATION ENTRE LE 04-20 ET LE 05-10", txt)
        self.assertIn("HR >=  92 %", txt)
        self.assertIn("PREMIÈRE INFECTION SECONDAIRE", txt)
        self.assertIn("Repères", txt)
        self.assertIn("    2 /  1", txt)                                              # 2 nuits / 1 tache

    def test_ligne_de_commande(self):
        rows = scenario()
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            for r in rows:
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], r["pluie"], ""])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                sp.main([chemin, "--lat", "49.25", "--lon", "4.03", "--du", "04-20", "--au", "05-10"])
        finally:
            os.unlink(chemin)
        sortie = s.getvalue()
        self.assertIn("Profil de base : calage_2026", sortie)
        self.assertIn("HR >=  94 %", sortie)


if __name__ == "__main__":
    unittest.main(verbosity=1)
