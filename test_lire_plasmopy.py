#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du résumé de la table d'événements de Plasmopy (format reproduit d'après la sortie réelle)."""
import os
import tempfile
import unittest

import lire_plasmopy as lp

ENTETE = ("id,start,oospore_maturation,oospore_germination,oospore_dispersion,oospore_infection,incubation_days,"
          "completed_incubation,sporulations,sporangia_densities,spore_lifespan_days,secondary_infections,"
          "oospore_infection_strength,secondary_infection_strengths")
# Format réel observé : id, start, maturation, germination, puis 10 colonnes (dont 9 « None » ici)
SANS = "2026-04-23 00:00:00+00:00,2026-05-02 15:00:00+00:00,None,None,None,None,None,None,None,None,None,None"
# Chaîne complète : maturation, germination, dispersion, infection, incubation_days, completed_incubation,
# sporulations (liste entre guillemets, avec virgules), sporangia_densities, spore_lifespan_days,
# secondary_infections, oospore_infection_strength, secondary_infection_strengths
AVEC = ("2026-04-23 00:00:00+00:00,2026-06-26 15:00:00+00:00,2026-06-26 15:00:00+00:00,2026-06-26 23:00:00+00:00,"
        "5.4,2026-07-02 10:00:00+00:00,\"[Timestamp('2026-07-03 01:00:00+0000', tz='UTC'), 2]\",None,None,None,52.2,None")


def csv_exemple():
    lignes = [ENTETE]
    for i in range(3):                                                  # même chaîne répétée sur 3 heures de départ
        lignes.append(f"{100 + i},2026-04-23 0{i}:00:00+00:00,{SANS}")
    for i in range(2):
        lignes.append(f"{200 + i},2026-06-26 1{i}:00:00+00:00,{AVEC}")
    return "\n".join(lignes) + "\n"


class TestLirePlasmopy(unittest.TestCase):
    def charger(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.csv")
            with open(p, "w", encoding="utf-8") as f:
                f.write(csv_exemple())
            return lp.charger(p)

    def test_regroupe_les_lignes_identiques(self):
        ch = lp.chaines(self.charger())
        self.assertEqual(len(ch), 2)
        self.assertEqual(sorted(len(v) for v in ch.values()), [2, 3])

    def test_resume_compte_les_chaines_avec_dispersion_et_infection(self):
        txt = lp.resume(self.charger())
        self.assertIn("5 heures de départ", txt)
        self.assertIn("2 chaînes distinctes : 1 avec dispersion, 1 avec infection", txt)
        self.assertIn("52.2", txt)                                       # la force d'infection apparaît
        self.assertIn("1 chaîne(s) sans dispersion", txt)

    def test_tout_affiche_aussi_les_chaines_sans_dispersion(self):
        txt = lp.resume(self.charger(), tout=True)
        self.assertNotIn("sans dispersion : --tout", txt)
        self.assertIn("2026-05-02", txt)

    def test_cellules_avec_virgules_dans_une_liste_ne_cassent_pas_les_colonnes(self):
        ch = lp.chaines(self.charger())
        cle = [k for k in ch if not lp.vide(k[1])][0]
        self.assertEqual(cle[3], "52.2")
        self.assertIn("Timestamp", cle[6])

    def test_vide(self):
        for v in ("", "None", "[]", None, "NaT"):
            self.assertTrue(lp.vide(v))
        self.assertFalse(lp.vide("2026-05-02"))

    def test_table_vide(self):
        self.assertEqual(lp.resume([]), "Table vide.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
