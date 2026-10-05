#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du balayage de sensibilité des infections secondaires (scénario synthétique déterministe)."""
import unittest

import mildiou_primaire as mp
import sensibilite_secondaire as ss
from test_mildiou_primaire import DEBUT, FORCE_AVRIL, LAT, LON, fusion, plage, serie


def scenario():
    """Chaîne primaire, une heure de sporulation (h382), puis une humectation à 25 °C 68 h plus tard (h450-451)."""
    r = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))
    r.update(plage(33, 1700, temp=12.0))
    r.update(plage(369, 383, hr=95.0, temp=12.0))
    r.update(plage(450, 452, hr=95.0, temp=25.0))
    return serie(DEBUT, 1700, regles=r)


class TestSensibiliteSecondaire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = ss.balayer(scenario(), LAT, LON, base=FORCE_AVRIL, now=DEBUT)
        cls.table = {titre: dict(lignes) for titre, lignes in cls.res}

    def test_trois_balayages(self):
        self.assertEqual([t for t, _ in self.res],
                         ["survie des sporanges", "détachement des sporanges", "productivité des taches",
                          "conditions de sporulation", "durée de vie des taches", "tolérance d'interruption"])

    def test_la_survie_change_le_nombre_d_infections(self):
        s = self.table["survie des sporanges"]
        self.assertEqual(s["fixe 48 h (2 j)"]["n"], 0)         # sporanges morts avant l'humectation (h450)
        self.assertEqual(s["fixe 96 h (4 j)"]["n"], 1)         # encore viables
        self.assertEqual(s["fixe 360 h (15 j)"]["n"], 1)

    def test_resume_d_une_infection(self):
        r = self.table["survie des sporanges"]["fixe 96 h (4 j)"]
        self.assertEqual(r["par_gen"], {1: 1})
        self.assertEqual(r["premiere"], r["derniere"])
        self.assertEqual(r["par_mois"], {4: 1})                # 28/04

    def test_la_duree_de_vie_des_taches_est_sans_effet_ici(self):
        # les taches primaires sporulent tout de suite après leur apparition : aucun balayage de la durée de vie ne change rien
        taches = self.table["durée de vie des taches"]
        self.assertEqual({r["n"] for r in taches.values()}, {1})

    def test_la_tolerance_est_sans_effet_sur_une_periode_continue(self):
        self.assertEqual({r["n"] for r in self.table["tolérance d'interruption"].values()}, {1})

    def test_survie_vinemild_et_trinome_de_franche_couvrent_l_humectation(self):
        s = self.table["survie des sporanges"]
        self.assertEqual(s["Vinemild (annexe 5)"]["n"], 1)                  # ≈ 207 h de survie à 12 °C et HR 60 %
        self.assertEqual(s["VPD, trinôme de Franche (0,01)"]["n"], 1)       # ≈ 87 h : encore viables 68 h après

    def test_exiger_une_pluie_de_detachement_supprime_l_infection_par_humidite_seule(self):
        d = self.table["détachement des sporanges"]
        self.assertEqual(d["aucune condition (défaut)"]["n"], 1)
        self.assertEqual(d["pluie >= 0,2 mm/h (Franche)"]["n"], 0)          # le scénario ne contient aucune pluie
        self.assertEqual(d["pluie >= 1 mm/h"]["n"], 0)

    def test_productivite_sans_effet_sur_une_seule_nuit_de_sporulation(self):
        self.assertEqual({r["n"] for r in self.table["productivité des taches"].values()}, {1})

    def test_six_heures_d_humidite_nocturne_suppriment_la_sporulation_du_scenario(self):
        # le scénario n'offre que 4 h d'obscurité humide : insuffisant pour les 6 h de Franche
        sp = self.table["conditions de sporulation"]
        self.assertEqual(sp["4 h, HR >= 92 %, T >= 12 (défaut)"]["n"], 1)
        self.assertEqual(sp["6 h, HR >= 90 % (Franche)"]["n"], 0)
        self.assertEqual(sp["3 h, HR >= 80 %, T >= 10 (Rossi)"]["n"], 1)

    def test_resume_sans_infection(self):
        r = ss.resumer({"secondaires": []})
        self.assertEqual((r["n"], r["premiere"], r["derniere"], r["par_gen"]), (0, None, None, {}))

    def test_les_colonnes_ne_se_chevauchent_pas(self):
        # plusieurs générations (g1:111 g2:9 g3:1) ne doivent pas coller à la date de la colonne suivante
        resultats = [("essai", [("x", {"n": 121, "par_gen": {1: 111, 2: 9, 3: 1}, "par_mois": {5: 4},
                                       "premiere": "2026-05-29", "derniere": "2026-09-16"})])]
        ligne = ss.formater(resultats).split("\n")[-1]
        self.assertIn("g1:111 g2:9 g3:1 ", ligne)
        self.assertNotIn("g3:12026", ligne)

    def test_format_lisible(self):
        txt = ss.formater(self.res)
        self.assertIn("SURVIE DES SPORANGES", txt)
        self.assertIn("fixe 168 h (7 j)", txt)
        self.assertIn("g1:1", txt)
        self.assertIn("avr:1", txt)


if __name__ == "__main__":
    unittest.main(verbosity=1)
