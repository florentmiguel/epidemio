#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du diagnostic de force d'infection."""
import unittest
from datetime import datetime, timedelta, timezone

import diagnostic_infection as di
import mildiou_primaire as mp

UTC = timezone.utc
H = timedelta(hours=1)
T0 = datetime(2026, 6, 26, 15, 0, tzinfo=UTC)

# températures et pluie horaires, à partir de t0 (la pluie rend l'heure mouillée) ; 0 = heure sèche
TEMPS = [25.1, 29.3, 31.2, 30.4, 27.0, 24.2, 22.0, 19.5, 17.3, 16.1, 15.8, 14.9, 14.0, 13.5, 13.1, 12.8]
MOUILLE = [1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 0]


def serie():
    rows = []
    for i in range(-3, 30):
        k = i if 0 <= i < len(TEMPS) else None
        temp = TEMPS[k] if k is not None else 15.0
        mouille = MOUILLE[k] if k is not None else 0
        rows.append({"t": T0 + i * H, "temp": temp, "hr": 70.0, "pluie": 1.0 if mouille else 0.0,
                     "rosee": None, "mouille": None})
    return rows


class TestDiagnostic(unittest.TestCase):
    def test_contribution_horaire_t_fois_h_sans_plafond(self):
        l = di.contributions(serie(), {"infection": {"soustraire_base": False, "temperature_max": 99}})
        par_heure = {x["t"]: x for x in l}
        self.assertAlmostEqual(par_heure[T0 + 2 * H]["dh"], 31.2)         # mouillée, 31 °C : comptée sans plafond
        self.assertEqual(par_heure[T0 + 3 * H]["dh"], 0.0)                # heure sèche
        plafonnee = di.contributions(serie(), {"infection": {"soustraire_base": False, "temperature_max": 29}})
        self.assertEqual({x["t"]: x for x in plafonnee}[T0 + 2 * H]["dh"], 0.0)

    def test_retrouve_la_fenetre_dont_la_somme_est_la_cible(self):
        params = {"infection": {"soustraire_base": False, "temperature_max": 99}}
        lignes = di.contributions(serie(), params)
        idx = {l["t"]: i for i, l in enumerate(lignes)}
        a, b = idx[T0], idx[T0 + 9 * H]                                   # fenêtre cherchée : t0 .. t0 + 9 h
        cible = round(sum(l["dh"] for l in lignes[a:b + 1]), 1)
        trouvees = di.fenetres(lignes, idx[T0], cible)
        self.assertIn((a, b), trouvees)

    def test_aucune_fenetre_pour_une_cible_impossible(self):
        lignes = di.contributions(serie(), {"infection": {"soustraire_base": False, "temperature_max": 99}})
        self.assertEqual(di.fenetres(lignes, 3, 99999.0), [])

    def test_les_heures_seches_aux_bords_sont_normalisees(self):
        params = {"infection": {"soustraire_base": False, "temperature_max": 99}}
        lignes = di.contributions(serie(), params)
        idx = {l["t"]: i for i, l in enumerate(lignes)}
        # t0+3 et t0+4 sont sèches : la fenêtre t0..t0+4 a la même somme que t0..t0+2 ; on doit voir t0..t0+2
        cible = round(sum(l["dh"] for l in lignes[idx[T0]:idx[T0 + 2 * H] + 1]), 1)
        self.assertIn((idx[T0], idx[T0 + 2 * H]), di.fenetres(lignes, idx[T0], cible))

    def test_rapport_lisible(self):
        txt = di.rapport(serie(), T0, 100.0, {"infection": {"soustraire_base": False, "temperature_max": 99}}, heures=10)
        self.assertIn("Cible : 100.0", txt)
        self.assertIn("26/06 15:00", txt)
        self.assertIn("contrib.", txt)

    def test_t0_absent(self):
        self.assertIn("absent", di.rapport(serie(), datetime(2030, 1, 1, tzinfo=UTC), 10.0))


if __name__ == "__main__":
    unittest.main(verbosity=1)
