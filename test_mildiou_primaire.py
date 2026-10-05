#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du moteur mildiou primaire — météos synthétiques, une règle par test."""
import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import mildiou_primaire as mp

UTC = timezone.utc
H = timedelta(hours=1)
LAT, LON = 49.25, 4.03          # Reims
FORCE_AVRIL = {"maturation": {"date_forcee": "2026-04-11"}}   # maturité acquise avant la série
DEBUT = datetime(2026, 4, 12, 0, 0, tzinfo=UTC)


def serie(debut, n, base=None, regles=None):
    """n heures consécutives. base = valeurs par défaut ; regles = {i: {...}} par heure."""
    b = {"temp": 12.0, "hr": 60.0, "pluie": 0.0, "rosee": None, "mouille": None}
    b.update(base or {})
    rows = []
    for i in range(n):
        r = {"t": debut + i * H, **b}
        r.update((regles or {}).get(i, {}))
        rows.append(r)
    return rows


def plage(a, b, **vals):
    return {i: dict(vals) for i in range(a, b)}


def fusion(*dicts):
    out = {}
    for d in dicts:
        for k, v in d.items():
            out.setdefault(k, {}).update(v)
    return out


def lancer(rows, params=None, now=None, bbch=None):
    return mp.calculer_saison(rows, LAT, LON, params=params, now=now or DEBUT + timedelta(days=400), bbch=bbch)


def t(res_ev):
    return datetime.strptime(res_ev["t"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


class TestMaturation(unittest.TestCase):
    def setUp(self):
        # T = 10 °C constante => DJ8 = 2 par jour ; la série démarre à 00 h locale (23 h UTC la veille)
        rows = serie(datetime(2025, 12, 31, 23, 0, tzinfo=UTC), 80 * 24, base={"temp": 10.0})
        self.res = lancer(rows)
        self.j = {d["date"]: d for d in self.res["jours"]}

    def test_cumul_depuis_le_1er_janvier(self):
        self.assertAlmostEqual(self.j["2026-01-01"]["dj_cumul"], 2.0, places=1)
        self.assertAlmostEqual(self.j["2026-02-19"]["dj_cumul"], 100.0, places=1)   # jour 50

    def test_statuts_aux_paliers_du_texte(self):
        self.assertEqual(self.j["2026-02-18"]["statut_maturation"], "maturation faible")        # 98
        self.assertEqual(self.j["2026-02-19"]["statut_maturation"], "maturation en cours")      # 100
        self.assertEqual(self.j["2026-03-01"]["statut_maturation"], "fenêtre de surveillance")  # 120
        self.assertEqual(self.j["2026-03-10"]["statut_maturation"], "fenêtre de surveillance")  # 138
        self.assertEqual(self.j["2026-03-11"]["statut_maturation"], "maturité acquise")         # 140

    def test_lecture_litterale_somme_brute_des_temperatures(self):
        # T = 10 °C : degrés-jours -> 2/jour (maturité le 70e jour) ; somme brute -> 10/jour (14e jour)
        rows = serie(datetime(2025, 12, 31, 23, 0, tzinfo=UTC), 80 * 24, base={"temp": 10.0})
        dj = lancer(rows)["maturation"]["date_maturite"]
        somme = lancer(rows, {"maturation": {"mode_cumul": "somme"}})["maturation"]["date_maturite"]
        self.assertEqual(dj, "2026-03-11")
        self.assertEqual(somme, "2026-01-14")

    def test_somme_brute_ignore_les_jours_a_8_degres_ou_moins(self):
        rows = serie(datetime(2025, 12, 31, 23, 0, tzinfo=UTC), 30 * 24, base={"temp": 8.0})
        res = lancer(rows, {"maturation": {"mode_cumul": "somme"}})
        self.assertEqual(res["jours"][-1]["dj_cumul"], 0.0)

    def test_date_de_maturite(self):
        self.assertEqual(self.res["maturation"]["date_maturite"], "2026-03-11")

    def test_aucune_degre_jour_sous_la_base(self):
        rows = serie(datetime(2025, 12, 31, 23, 0, tzinfo=UTC), 10 * 24, base={"temp": 5.0})
        self.assertEqual(lancer(rows)["jours"][-1]["dj_cumul"], 0.0)

    def test_date_forcee_ancre_la_maturite(self):
        rows = serie(DEBUT, 24, base={"temp": 5.0})
        res = lancer(rows, FORCE_AVRIL)
        self.assertEqual(res["maturation"]["date_forcee"], "2026-04-11")
        self.assertIsNotNone(res["maturation"]["active_depuis"])


class TestGermination(unittest.TestCase):
    def test_rien_avant_la_maturite(self):
        rows = serie(DEBUT, 120, base={"temp": 5.0, "hr": 95.0, "pluie": 4.0})
        self.assertEqual(lancer(rows)["cycles"], [])

    def test_porte_humidite_8h_consecutives(self):
        rows = serie(DEBUT, 60, regles=plage(10, 18, hr=85.0))        # 8 h => déclenche à l'heure 17
        c = lancer(rows, FORCE_AVRIL)["cycles"][0]
        self.assertEqual(c["germination"]["porte"], "humidité")
        self.assertEqual(t(c["germination"]), DEBUT + 17 * H)

    def test_sept_heures_ne_suffisent_pas(self):
        rows = serie(DEBUT, 60, regles=plage(10, 17, hr=85.0))
        self.assertEqual(lancer(rows, FORCE_AVRIL)["cycles"], [])

    def test_hr_exactement_80_ne_compte_pas(self):
        rows = serie(DEBUT, 60, regles=plage(10, 30, hr=80.0))        # « HR > 80 % »
        self.assertEqual(lancer(rows, FORCE_AVRIL)["cycles"], [])

    def test_feuille_mouillee_remplace_hr(self):
        rows = serie(DEBUT, 60, regles=plage(10, 18, mouille=1.0))
        self.assertEqual(len(lancer(rows, FORCE_AVRIL)["cycles"]), 1)

    def test_temperature_trop_basse(self):
        rows = serie(DEBUT, 60, base={"temp": 7.0}, regles=plage(10, 30, hr=95.0))
        self.assertEqual(lancer(rows, FORCE_AVRIL)["cycles"], [])

    def test_porte_pluie_5mm_sur_48h(self):
        # 5 averses isolées de 1 mm, espacées de 6 h : jamais 8 h de feuille mouillée de suite,
        # donc seule la porte pluie peut se déclencher, quand le cumul atteint 5 mm (heure 34)
        regles = {i: {"pluie": 1.0} for i in (10, 16, 22, 28, 34)}
        c = lancer(serie(DEBUT, 100, regles=regles), FORCE_AVRIL)["cycles"][0]
        self.assertEqual(c["germination"]["porte"], "pluie")
        self.assertEqual(t(c["germination"]), DEBUT + 34 * H)

    def test_une_pluie_fine_prolongee_declenche_la_porte_humidite(self):
        # 0,5 mm/h pendant 10 h : la feuille est mouillée 8 h de suite avant que 5 mm soient cumulés
        c = lancer(serie(DEBUT, 80, regles=plage(10, 20, pluie=0.5)), FORCE_AVRIL)["cycles"][0]
        self.assertEqual(c["germination"]["porte"], "humidité")
        self.assertEqual(t(c["germination"]), DEBUT + 17 * H)

    def test_pluie_hors_fenetre_48h_ne_s_additionne_pas(self):
        regles = {10: {"pluie": 2.5}, 70: {"pluie": 2.5}}              # 60 h d'écart
        self.assertEqual(lancer(serie(DEBUT, 120, regles=regles), FORCE_AVRIL)["cycles"], [])

    def test_un_episode_donne_un_seul_cycle(self):
        rows = serie(DEBUT, 200, base={"hr": 85.0})                    # humide en continu
        res = lancer(rows, FORCE_AVRIL)
        self.assertEqual(len(res["cycles"]), 1)
        self.assertEqual(res["cycles"][0]["statut"], "germination_sans_dispersion")

    def test_deux_episodes_distincts_donnent_deux_cycles(self):
        regles = plage(10, 20, hr=85.0)
        regles.update(plage(120, 130, hr=85.0))                        # sec entre les deux
        self.assertEqual(len(lancer(serie(DEBUT, 200, regles=regles), FORCE_AVRIL)["cycles"]), 2)


class TestDispersion(unittest.TestCase):
    def base(self, **kw):
        regles = fusion(plage(10, 18, hr=85.0), {20: {"pluie": 4.0}})
        regles.update(kw)
        return serie(DEBUT, 80, regles=regles)

    def test_pluie_superieure_a_3mm_h_disperse(self):
        c = lancer(self.base(), FORCE_AVRIL)["cycles"][0]
        self.assertEqual(t(c["dispersion"]), DEBUT + 20 * H)

    def test_pluie_de_3mm_h_exactement_ne_disperse_pas(self):
        rows = serie(DEBUT, 80, regles=fusion(plage(10, 18, hr=85.0), {20: {"pluie": 3.0}}))
        self.assertNotIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])

    def test_dispersion_impossible_sous_8_degres(self):
        rows = serie(DEBUT, 80, regles=fusion(plage(10, 18, hr=85.0), {20: {"pluie": 4.0, "temp": 7.0}}))
        self.assertNotIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])

    def test_latence_plasmopy_6h(self):
        # pluie 3 h après la germination : ignorée avec latence 6 h, acceptée avec latence 0
        regles = fusion(plage(10, 18, hr=85.0), {20: {"pluie": 4.0}})   # germination h17, pluie h20
        rows = serie(DEBUT, 80, regles=regles)
        self.assertIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])
        res6 = lancer(rows, fusion(FORCE_AVRIL, {"dispersion": {"latence_h": 6}}))
        self.assertNotIn("dispersion", res6["cycles"][0])

    def test_pluie_etalee_ne_disperse_pas_sur_1h_mais_disperse_en_cumul_glissant(self):
        # 4 heures de 1 mm : jamais > 3 mm sur UNE heure, mais 4 mm cumulés sur 6 h
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 24, pluie=1.0))
        rows = serie(DEBUT, 80, regles=regles)
        self.assertNotIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])
        c = lancer(rows, fusion(FORCE_AVRIL, {"dispersion": {"fenetre_h": 6}}))["cycles"][0]
        self.assertEqual(t(c["dispersion"]), DEBUT + 23 * H)          # 4e heure : cumul 4 mm > 3
        self.assertEqual(c["dispersion"]["pluie_mm"], 4.0)
        self.assertEqual(c["dispersion"]["fenetre_h"], 6)

    def test_le_cumul_glissant_oublie_les_heures_anciennes(self):
        # 1 mm à h20 puis 1 mm à h30 : jamais 4 mm dans une fenêtre de 6 h
        regles = fusion(plage(10, 18, hr=85.0), {20: {"pluie": 1.0}, 22: {"pluie": 1.0},
                                                  30: {"pluie": 1.0}, 32: {"pluie": 1.0}})
        rows = serie(DEBUT, 80, regles=regles)
        res = lancer(rows, fusion(FORCE_AVRIL, {"dispersion": {"fenetre_h": 6, "pluie_mm": 3.0}}))
        self.assertNotIn("dispersion", res["cycles"][0])

    def test_pluie_apres_expiration_ne_disperse_plus(self):
        regles = fusion(plage(10, 18, hr=85.0), {17 + 48: {"pluie": 4.0}})   # à l'heure d'expiration
        rows = serie(DEBUT, 120, regles=regles)
        self.assertNotIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])


class TestInfection(unittest.TestCase):
    def cycle(self, temp, n_pluie, params=None):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 20 + n_pluie, pluie=4.0, temp=temp))
        rows = serie(DEBUT, 120, regles=regles)
        return lancer(rows, fusion(FORCE_AVRIL, params or {}))["cycles"][0]

    def test_50_degres_heures_a_12_degres(self):
        # base 8 : (12 - 8) = 4 °C.h par heure mouillée -> 52 à la 13e heure
        c = self.cycle(12.0, 16)
        self.assertEqual(t(c["infection"]), t(c["dispersion"]) + 12 * H)

    def test_heures_de_mouillage_necessaires_selon_la_temperature(self):
        # ceil(50 / (T - 8)) heures de mouillage continues
        for temp, heures in ((10.0, 25), (12.0, 13), (16.0, 7), (20.0, 5), (25.0, 3)):
            c = self.cycle(temp, 40)
            self.assertEqual(t(c["infection"]) - t(c["dispersion"]), (heures - 1) * H, temp)

    def test_formule_produit_t_fois_h_donne_5h_a_10_degres(self):
        # texte suisse : « à 10 °C, les feuilles doivent être mouillées au moins 5 heures » (10 x 5 = 50)
        for temp, heures in ((10.0, 5), (12.0, 5), (16.0, 4), (25.0, 2)):
            c = self.cycle(temp, 40, {"infection": {"soustraire_base": False}})
            self.assertEqual(t(c["infection"]) - t(c["dispersion"]), (heures - 1) * H, temp)

    def test_formule_produit_ne_compte_pas_sous_8_degres(self):
        c = self.cycle(7.0, 60, {"infection": {"soustraire_base": False}})
        self.assertNotIn("infection", c)

    def test_formule_produit_est_beaucoup_plus_permissive_que_la_base_soustraite(self):
        # à 10 °C : 25 h avec T-8, 5 h avec T x h ; 8 h de pluie ne suffisent que pour la seconde
        base = self.cycle(10.0, 8)
        produit = self.cycle(10.0, 8, {"infection": {"soustraire_base": False}})
        self.assertNotIn("infection", base)
        self.assertIn("infection", produit)

    def test_le_plafond_de_29_degres_exclut_les_heures_chaudes_et_peut_etre_leve(self):
        # orage d'après-midi à 32 °C : avec le plafond par défaut aucune heure n'est infectante ;
        # sans plafond et en T x h, 32 + 32 = 64 >= 50 dès la 2e heure
        defaut = self.cycle(32.0, 10, {"infection": {"soustraire_base": False}})
        sans_plafond = self.cycle(32.0, 10, {"infection": {"soustraire_base": False, "temperature_max": 99}})
        self.assertNotIn("infection", defaut)
        self.assertEqual(t(sans_plafond["infection"]) - t(sans_plafond["dispersion"]), 1 * H)
        self.assertAlmostEqual(sans_plafond["force_dh"], 320.0, places=1)       # 10 h x 32

    def test_une_fenetre_d_infection_plus_longue_cumule_davantage_de_force(self):
        # 1re heure dispersante (4 mm), puis 39 h de bruine à 1 mm/h : mouillante mais non dispersante,
        # donc elle ne prolonge pas la fenêtre ; la force suit alors la durée de validité
        regles = fusion(plage(10, 18, hr=85.0), {20: {"pluie": 4.0}}, plage(21, 60, pluie=1.0))
        rows = serie(DEBUT, 120, regles=regles)

        def force(validite):
            params = fusion(FORCE_AVRIL, {"infection": {"soustraire_base": False, "validite_h": validite}})
            return lancer(rows, params)["cycles"][0]["force_dh"]

        self.assertAlmostEqual(force(24), 24 * 12.0, places=1)
        self.assertAlmostEqual(force(48), 40 * 12.0, places=1)             # la bruine s'arrête à 40 h

    def test_une_pluie_dispersante_continue_prolonge_la_fenetre_quelle_que_soit_la_validite(self):
        c = self.cycle(12.0, 40, {"infection": {"soustraire_base": False, "validite_h": 24}})
        self.assertAlmostEqual(c["force_dh"], 40 * 12.0, places=1)         # renouvelée à chaque heure de pluie

    def test_pas_d_infection_si_trop_chaud(self):
        c = self.cycle(30.0, 8)                                   # > 29 °C : aucun cumul
        self.assertNotIn("infection", c)
        self.assertEqual(c["statut"], "dispersion_sans_infection")

    def test_pas_d_infection_si_trop_peu_de_mouillage(self):
        c = self.cycle(12.0, 3)                                   # 27 °C.h seulement
        self.assertNotIn("infection", c)

    def test_mouillage_minimum_plasmopy(self):
        sans = self.cycle(25.0, 8)                                # 22 °C.h/h : 3 h suffisent
        avec = self.cycle(25.0, 8, {"infection": {"mouillage_min_h": 6}})
        self.assertEqual(t(sans["infection"]), t(sans["dispersion"]) + 2 * H)
        self.assertEqual(t(avec["infection"]), t(avec["dispersion"]) + 5 * H)

    def test_base_3_ancienne_interpretation_raccourcit_la_duree(self):
        base8 = self.cycle(12.0, 16)
        base3 = self.cycle(12.0, 16, {"infection": {"base_degres_heures": 3.0, "temperature_min": 3.0}})
        self.assertEqual(t(base8["infection"]) - t(base8["dispersion"]), 12 * H)   # 13 x 4 = 52
        self.assertEqual(t(base3["infection"]) - t(base3["dispersion"]), 5 * H)    # 6 x 9 = 54

    def test_heures_froides_ne_comptent_pas_dans_le_mouillage_minimum(self):
        # à 7 °C aucune heure n'est infectante : ni degrés-heures, ni mouillage
        c = self.cycle(7.0, 40, {"infection": {"mouillage_min_h": 6}})
        self.assertNotIn("infection", c)

    def test_la_force_continue_apres_la_confirmation(self):
        c = self.cycle(12.0, 16)
        self.assertAlmostEqual(c["force_dh"], 64.0, places=1)     # 16 h x 4


class TestEpisodesPluvieux(unittest.TestCase):
    def test_une_seule_pluie_donne_un_seul_cycle(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0))
        res = lancer(serie(DEBUT, 120, regles=regles), FORCE_AVRIL)
        self.assertEqual(len(res["cycles"]), 1)

    def test_pluie_prolongee_cumule_la_force_dans_un_seul_cycle(self):
        # averses de 4 mm/h toutes les 20 h pendant 5 jours : chacune prolonge la fenêtre de 24 h
        regles = plage(10, 18, hr=85.0)
        for k in range(6):
            regles[20 + 20 * k] = {"pluie": 4.0}
        res = lancer(serie(DEBUT, 200, regles=regles), FORCE_AVRIL)
        self.assertEqual(len(res["cycles"]), 1)
        c = res["cycles"][0]
        self.assertAlmostEqual(c["force_dh"], 24.0, places=1)           # 6 heures de pluie x 4 °C.h
        self.assertEqual(t({"t": c["dispersion"]["derniere"]}), DEBUT + 120 * H)

    def test_pluies_espacees_de_plus_de_24h_font_deux_cycles(self):
        regles = fusion(plage(10, 18, hr=85.0), {20: {"pluie": 4.0}},
                        plage(110, 118, hr=85.0), {120: {"pluie": 4.0}})
        res = lancer(serie(DEBUT, 200, regles=regles), FORCE_AVRIL)
        self.assertEqual(len(res["cycles"]), 2)


class TestToleranceInterruption(unittest.TestCase):
    """T = 12 °C : 4 °C.h par heure mouillée, 13 h nécessaires (52). Après une nuit humide, la pluie
    commence à l'heure 20 : 8 h de pluie, une coupure sèche, puis 5 h de pluie (13 h mouillées au total)."""

    def cycle(self, coupure_h, params=None):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0),
                        plage(28 + coupure_h, 28 + coupure_h + 5, pluie=4.0))
        return lancer(serie(DEBUT, 120, regles=regles), fusion(FORCE_AVRIL, params or {}))["cycles"][0]

    def test_par_defaut_le_cumul_est_libre(self):
        self.assertIn("infection", self.cycle(3))

    def test_coupure_superieure_a_la_tolerance_remet_la_periode_a_zero(self):
        c = self.cycle(3, {"humectation": {"tolerance_h": 2}})
        self.assertNotIn("infection", c)
        self.assertEqual(c["statut"], "dispersion_sans_infection")

    def test_coupure_dans_la_tolerance_est_ponteee(self):
        c = self.cycle(2, {"humectation": {"tolerance_h": 2}})
        self.assertIn("infection", c)

    def test_continuite_stricte(self):
        self.assertNotIn("infection", self.cycle(1, {"humectation": {"tolerance_h": 0}}))
        self.assertIn("infection", self.cycle(1, {"humectation": {"tolerance_h": 1}}))

    def test_la_force_compte_toujours_toutes_les_heures_infectantes(self):
        # la remise à zéro ne concerne que la décision d'infection, pas la force affichée
        c = self.cycle(3, {"humectation": {"tolerance_h": 2}})
        self.assertAlmostEqual(c["force_dh"], 52.0, places=1)

    def test_le_mouillage_minimum_compte_la_periode_courante(self):
        # T = 25 °C (17 °C.h/h) : 4 h mouillées, coupure de 3 h, 3 h mouillées -> 7 h au total,
        # mais jamais plus de 4 h d'affilée : le minimum de 6 h n'est atteint qu'en cumul libre
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 24, pluie=4.0, temp=25.0),
                        plage(27, 30, pluie=4.0, temp=25.0))
        rows = serie(DEBUT, 120, regles=regles)
        base = fusion(FORCE_AVRIL, {"infection": {"mouillage_min_h": 6}})
        libre = lancer(rows, base)["cycles"][0]
        stricte = lancer(rows, fusion(base, {"humectation": {"tolerance_h": 2}}))["cycles"][0]
        self.assertIn("infection", libre)
        self.assertNotIn("infection", stricte)


class TestIncubationSporulation(unittest.TestCase):
    def chaine(self, temp_apres=12.0, hr_nuit=95.0, temp_nuit=12.0):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))   # infection à l'heure 32 (13 x 4 = 52)
        regles.update(plage(33, 750, temp=temp_apres))
        # après les taches (32 + 337 h), nuits très humides
        regles.update(plage(32 + 337, 32 + 337 + 48, hr=hr_nuit, temp=temp_nuit))
        return lancer(serie(DEBUT, 800, regles=regles), FORCE_AVRIL)["cycles"][0]

    def test_incubation_14_jours_a_12_degres(self):
        c = self.chaine()
        self.assertEqual(t(c["infection"]), DEBUT + 32 * H)
        self.assertEqual(t(c["taches"]), DEBUT + (32 + 337) * H)

    def test_incubation_plus_courte_a_24_degres(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 26, pluie=4.0, temp=24.0))   # infection (4 x 16 = 64)
        regles.update(plage(26, 700, temp=24.0))
        c = lancer(serie(DEBUT, 800, regles=regles), FORCE_AVRIL)["cycles"][0]
        self.assertEqual(t(c["taches"]) - t(c["infection"]), (4 * 24 + 1) * H)

    def test_pas_d_incubation_sous_11_degres(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 26, pluie=4.0, temp=20.0))   # infection (5 x 12 = 60)
        regles.update(plage(26, 800, temp=8.0))
        c = lancer(serie(DEBUT, 800, regles=regles), FORCE_AVRIL)["cycles"][0]
        self.assertNotIn("taches", c)
        self.assertEqual(c["statut"], "infection")
        self.assertEqual(c["incubation"]["progression_pct"], 0)

    def test_sporulation_apres_4h_de_nuit_humide(self):
        c = self.chaine()
        self.assertEqual(c["statut"], "sporulation")
        self.assertTrue(c["repiquage"])
        self.assertGreaterEqual(t(c["sporulation"]), t(c["taches"]))
        for k in range(4):                                         # 4 h consécutives de nuit avant l'heure
            h = t(c["sporulation"]) - k * H
            self.assertLess(mp.elevation_solaire(h + timedelta(minutes=30), LAT, LON), -0.833)

    def serie_nuit_tardive(self, decalage_h, longueur=1100):
        """Infection h32 -> taches h369 ; nuits très humides seulement `decalage_h` heures après les taches."""
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))
        regles.update(plage(33, longueur - 50, temp=12.0))
        regles.update(plage(369 + decalage_h, 369 + decalage_h + 48, hr=95.0, temp=12.0))
        return serie(DEBUT, longueur, regles=regles)

    def test_sans_fenetre_une_tache_sporule_a_la_premiere_nuit_favorable_meme_tres_tard(self):
        c = lancer(self.serie_nuit_tardive(480), FORCE_AVRIL)["cycles"][0]      # 20 jours plus tard
        self.assertEqual(c["statut"], "sporulation")

    def test_fenetre_de_sporulation_coupe_les_sporulations_tardives(self):
        res = lancer(self.serie_nuit_tardive(480), fusion(FORCE_AVRIL, {"sporulation": {"fenetre_j": 10}}))
        c = res["cycles"][0]
        self.assertNotIn("sporulation", c)
        self.assertEqual(c["statut"], "taches_sans_sporulation")
        self.assertNotIn(c["id"], [x["id"] for x in res["cycles_actifs"]])      # n'est plus un cycle actif

    def test_fenetre_assez_longue_laisse_sporuler(self):
        c = lancer(self.serie_nuit_tardive(480), fusion(FORCE_AVRIL, {"sporulation": {"fenetre_j": 30}}))["cycles"][0]
        self.assertEqual(c["statut"], "sporulation")

    def test_fenetre_encore_ouverte_en_fin_de_serie_reste_active(self):
        # série arrêtée 5 jours après les taches : la fenêtre de 10 jours n'est pas écoulée
        rows = self.serie_nuit_tardive(480, longueur=369 + 120)
        res = lancer(rows, fusion(FORCE_AVRIL, {"sporulation": {"fenetre_j": 10}}))
        self.assertEqual(res["cycles"][0]["statut"], "taches_visibles")
        self.assertEqual(len(res["cycles_actifs"]), 1)

    def test_nuit_dans_la_fenetre_sporule(self):
        c = lancer(self.serie_nuit_tardive(48), fusion(FORCE_AVRIL, {"sporulation": {"fenetre_j": 10}}))["cycles"][0]
        self.assertEqual(c["statut"], "sporulation")

    def test_pas_de_sporulation_si_hr_trop_basse(self):
        c = self.chaine(hr_nuit=88.0)
        self.assertEqual(c["statut"], "taches_visibles")
        self.assertNotIn("sporulation", c)

    def test_pas_de_sporulation_si_trop_froid(self):
        c = self.chaine(temp_nuit=11.0)
        self.assertEqual(c["statut"], "taches_visibles")

    def test_table_d_incubation(self):
        pi = mp.PARAMS["incubation"]
        self.assertIsNone(mp.duree_incubation(10.9, pi))
        self.assertEqual(mp.duree_incubation(11.0, pi), 14)
        self.assertEqual(mp.duree_incubation(12.0, pi), 14)
        self.assertEqual(mp.duree_incubation(24.0, pi), 4)
        self.assertEqual(mp.duree_incubation(35.0, pi), 6)         # borné à 28 °C


class TestSensibiliteEtPrevisionnel(unittest.TestCase):
    def test_coefficient_bbch(self):
        ps = mp.PARAMS["sensibilite"]
        self.assertEqual(mp.coef_sensibilite(None, ps), 1.0)
        self.assertEqual(mp.coef_sensibilite(9, ps), 0.0)
        self.assertEqual(mp.coef_sensibilite(10, ps), 0.25)
        self.assertEqual(mp.coef_sensibilite(12, ps), 0.75)
        self.assertEqual(mp.coef_sensibilite(13, ps), 1.0)
        self.assertEqual(mp.coef_sensibilite(60, ps), 1.0)

    def test_bbch_module_la_force_sans_toucher_au_cycle(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0, temp=20.0))
        rows = serie(DEBUT, 120, regles=regles)
        sans = lancer(rows, FORCE_AVRIL)["cycles"][0]
        avec = lancer(rows, FORCE_AVRIL, bbch={"2026-04-12": 11, "2026-04-13": 11})["cycles"][0]
        self.assertEqual(sans["statut"], avec["statut"])
        self.assertEqual(sans["infection"]["t"], avec["infection"]["t"])
        self.assertAlmostEqual(avec["force_ponderee"], avec["force_dh"] * 0.5, places=1)

    def test_evenements_futurs_marques_previsionnels(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0, temp=20.0))
        rows = serie(DEBUT, 120, regles=regles)
        c = lancer(rows, FORCE_AVRIL, now=DEBUT + 19 * H)["cycles"][0]
        self.assertFalse(c["germination"]["previsionnel"])
        self.assertTrue(c["dispersion"]["previsionnel"])
        self.assertTrue(c["infection"]["previsionnel"])


class TestSensibiliteDispersion(unittest.TestCase):
    def test_la_grille_compare_les_criteres_sur_la_meme_meteo(self):
        import sensibilite_dispersion as sd
        # nuit humide puis 6 h de pluie fine (1 mm/h) : invisible à 1 h, visible en cumul glissant
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 26, pluie=1.0))
        rows = serie(DEBUT, 120, regles=regles)
        g = sd.grille(rows, LAT, LON, fenetres=(1, 6), seuils=(3.0, 10.0), params=FORCE_AVRIL, now=DEBUT)
        par = {(r["fenetre_h"], r["seuil_mm"]): r for r in g}
        self.assertEqual(par[(1, 3.0)]["avec_dispersion"], 0)          # critère actuel
        self.assertEqual(par[(6, 3.0)]["avec_dispersion"], 1)
        self.assertEqual(par[(6, 10.0)]["avec_dispersion"], 0)         # 6 mm < 10 mm
        self.assertIsNotNone(par[(6, 3.0)]["premiere_dispersion"])
        self.assertIn("critère actuel", sd.afficher(g))


class TestJugementContreObservation(unittest.TestCase):
    """Chronologie connue : infection h32 (12 °C), taches h369 = 27 avril 09:00 UTC."""

    def serie(self, hr_nuit):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))
        regles.update(plage(33, 750, temp=12.0))
        regles.update(plage(32 + 337, 32 + 337 + 48, hr=hr_nuit, temp=12.0))
        return serie(DEBUT, 800, regles=regles)

    def juge(self, hr_nuit, debut, fin, **kw):
        import sensibilite_dispersion as sd
        obs = {"debut": debut, "fin": fin, **kw}
        g = sd.grille(self.serie(hr_nuit), LAT, LON, fenetres=(1,), seuils=(3.0,),
                      params=FORCE_AVRIL, now=DEBUT, obs=obs)
        return g[0]

    def test_taches_dans_la_periode_sans_sporulation_proche_est_compatible(self):
        r = self.juge(88.0, "2026-04-26", "2026-04-30")
        self.assertEqual((r["taches_obs"], r["taches_avant"], r["spor_proche"]), (1, 0, 0))
        self.assertTrue(r["compatible"])
        self.assertTrue(r["premiere_tache"].startswith("2026-04-27"))

    def test_la_sporulation_proche_est_une_information_pas_un_motif_d_incompatibilite(self):
        # « pas de fructification » peut venir d'un traitement : on le signale sans l'utiliser pour juger
        r = self.juge(95.0, "2026-04-26", "2026-04-30")
        self.assertEqual(r["spor_proche"], 1)
        self.assertTrue(r["compatible"])

    def test_rater_la_periode_observee_est_incompatible(self):
        r = self.juge(88.0, "2026-05-20", "2026-06-06")                 # observées 3 semaines après la prédiction
        self.assertEqual((r["taches_obs"], r["taches_avant"]), (0, 1))
        self.assertFalse(r["compatible"])
        self.assertEqual(r["ecart_j"], -23)                              # prédites 23 jours trop tôt

    def test_la_tolerance_elargit_la_periode(self):
        r = self.juge(88.0, "2026-04-30", "2026-05-02", tolerance_j=3)   # 27/04 = 30/04 - 3 j
        self.assertEqual(r["taches_obs"], 1)
        r2 = self.juge(88.0, "2026-04-30", "2026-05-02", tolerance_j=2)
        self.assertEqual(r2["taches_avant"], 1)

    def test_rater_la_periode_par_le_haut_est_incompatible(self):
        # taches prédites le 27/04 ; observées du 10 au 15/04 (+ 3 j) : la prédiction tombe APRÈS la période
        r = self.juge(88.0, "2026-04-10", "2026-04-15", jusqu_a="2026-08-31")
        self.assertEqual((r["taches_obs"], r["taches_apres"]), (0, 1))
        self.assertFalse(r["compatible"])
        self.assertEqual(r["ecart_j"], 17)

    def test_taches_hors_periode_ne_changent_pas_le_verdict(self):
        # une 2e infection (à 24 °C, incubation de 4 jours) donne des taches APRÈS la période observée :
        # sans carte de protection, ce n'est pas une fausse alerte, le verdict reste « compatible »
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 33, pluie=4.0))
        regles.update(plage(33, 750, temp=12.0))
        regles.update(plage(440, 700, temp=24.0))
        regles.update(plage(440, 448, hr=85.0, temp=24.0))
        regles.update(plage(450, 456, pluie=4.0, temp=24.0))
        import sensibilite_dispersion as sd
        obs = {"debut": "2026-04-26", "fin": "2026-04-30", "jusqu_a": "2026-08-31"}
        r = sd.grille(serie(DEBUT, 800, regles=regles), LAT, LON, fenetres=(1,), seuils=(3.0,),
                      params=FORCE_AVRIL, now=DEBUT, obs=obs)[0]
        self.assertEqual(r["taches_obs"], 1)
        self.assertGreaterEqual(r["taches_apres"], 1)
        self.assertTrue(r["compatible"])

    def test_apres_n_est_compte_que_jusqu_a_la_fin_du_suivi(self):
        r = self.juge(88.0, "2026-04-10", "2026-04-15", jusqu_a="2026-04-20")
        self.assertEqual(r["taches_apres"], 0)                          # 27/04 est hors suivi

    def test_grille_humectation_compare_tolerance_et_minimum(self):
        import sensibilite_dispersion as sd
        # 8 h de pluie, 3 h sèches, 5 h de pluie à 12 °C : infection en cumul libre, pas avec tolérance 2 h
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0), plage(31, 36, pluie=4.0))
        rows = serie(DEBUT, 800, regles=regles)
        g = sd.grille_humectation(rows, LAT, LON, (1, 3.0), tolerances=(None, 2), minimums=(None,),
                                  hr_pcts=(90.0,), params=FORCE_AVRIL, now=DEBUT)
        par = {r["tolerance_h"]: r for r in g}
        self.assertEqual(par[None]["avec_infection"], 1)
        self.assertEqual(par[2]["avec_infection"], 0)
        self.assertIn("libre", sd.afficher_humectation(g))

    def test_affichage_avec_observation(self):
        import sensibilite_dispersion as sd
        r = self.juge(88.0, "2026-04-26", "2026-04-30")
        txt = sd.afficher([r])
        self.assertIn("compatible", txt)
        self.assertIn("OUI", txt)


class TestGrilleInfection(unittest.TestCase):
    def test_la_grille_compare_les_deux_formules(self):
        import sensibilite_dispersion as sd
        # nuit humide puis 8 h de pluie à 12 °C : 32 °C.h avec T-8 (pas d'infection), 96 avec T x h
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0))
        rows = serie(DEBUT, 800, regles=regles)
        g = sd.grille_infection(rows, LAT, LON, (1, 3.0), tolerances=(None,), minimums=(None,),
                                params=FORCE_AVRIL, now=DEBUT)
        par = {r["formule"]: r for r in g}
        self.assertEqual(par["T-8"]["avec_infection"], 0)
        self.assertEqual(par["T×h"]["avec_infection"], 1)
        txt = sd.afficher_humectation(g)
        self.assertIn("formule", txt)
        self.assertIn("T×h", txt)

    def test_la_grille_complete_a_16_lignes(self):
        import sensibilite_dispersion as sd
        rows = serie(DEBUT, 200)
        self.assertEqual(len(sd.grille_infection(rows, LAT, LON, (1, 3.0), params=FORCE_AVRIL, now=DEBUT)), 16)


class TestProfil(unittest.TestCase):
    PROFIL = mp.charger_profil("calage_2026")

    def avec_profil(self, rows, **extra):
        return lancer(rows, mp.fusionner(self.PROFIL, fusion(FORCE_AVRIL, extra)))

    def test_valeurs_du_profil(self):
        p = self.PROFIL
        self.assertEqual((p["dispersion"]["fenetre_h"], p["dispersion"]["pluie_mm"], p["dispersion"]["latence_h"]), (6, 5.0, 0))
        self.assertFalse(p["infection"]["soustraire_base"])
        self.assertEqual(p["infection"]["temperature_max"], 99.0)
        self.assertIsNone(p["infection"]["mouillage_min_h"])
        self.assertIsNone(p["humectation"]["tolerance_h"])

    def test_le_profil_ne_modifie_pas_les_defauts(self):
        self.assertEqual(mp.PARAMS["dispersion"]["fenetre_h"], 1)
        self.assertTrue(mp.PARAMS["infection"]["soustraire_base"])
        self.assertEqual(mp.PARAMS["infection"]["temperature_max"], 29.0)
        self.assertEqual(mp.charger_profil("texte"), {})

    def test_chaque_appel_rend_une_copie(self):
        a = mp.charger_profil("calage_2026"); a["dispersion"]["fenetre_h"] = 99
        self.assertEqual(mp.charger_profil("calage_2026")["dispersion"]["fenetre_h"], 6)

    def test_profil_inconnu(self):
        with self.assertRaises(ValueError) as e:
            mp.charger_profil("nimporte")
        self.assertIn("calage_2026", str(e.exception))

    def test_pluie_etalee_disperse_avec_le_profil_pas_avec_le_defaut(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 26, pluie=1.0))      # 6 mm étalés sur 6 h
        rows = serie(DEBUT, 120, regles=regles)
        self.assertNotIn("dispersion", lancer(rows, FORCE_AVRIL)["cycles"][0])
        c = self.avec_profil(rows)["cycles"][0]
        self.assertEqual(t(c["dispersion"]), DEBUT + 25 * H)                  # 6e heure : 6 mm > 5

    def test_t_fois_h_infecte_en_5h_a_10_degres_avec_le_profil(self):
        regles = fusion(plage(10, 18, hr=85.0, temp=10.0), plage(20, 40, pluie=4.0, temp=10.0))
        rows = serie(DEBUT, 120, base={"temp": 10.0}, regles=regles)
        self.assertNotIn("infection", lancer(rows, FORCE_AVRIL)["cycles"][0])   # T-8 : 25 h nécessaires
        c = self.avec_profil(rows)["cycles"][0]
        self.assertEqual(t(c["infection"]) - t(c["dispersion"]), 4 * H)       # 5 heures mouillées : 5 x 10 = 50

    def test_les_heures_chaudes_comptent_avec_le_profil(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 30, pluie=4.0, temp=32.0))
        rows = serie(DEBUT, 120, regles=regles)
        self.assertNotIn("infection", lancer(rows, FORCE_AVRIL)["cycles"][0])   # plafond de 29 °C
        self.assertIn("infection", self.avec_profil(rows)["cycles"][0])

    def test_ligne_de_commande_avec_profil(self):
        import contextlib, io
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 26, pluie=1.0))
        rows = serie(DEBUT, 120, regles=regles)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            for r in rows:
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], r["pluie"], ""])
            chemin = f.name
        try:
            def cli(*args):
                s = io.StringIO()
                with contextlib.redirect_stdout(s):
                    mp.main([chemin, "--lat", "49.25", "--lon", "4.03", "--maturite", "2026-04-11", *args])
                return s.getvalue()
            sans, avec = cli(), cli("--profil", "calage_2026")
            surcharge = cli("--profil", "calage_2026", "--fenetre", "1")      # une option surcharge le profil
        finally:
            os.unlink(chemin)
        self.assertIn("0 avec dispersion", sans)
        self.assertIn("Profil : calage_2026", avec)
        self.assertIn("1 avec dispersion", avec)
        self.assertIn("0 avec dispersion", surcharge)


class TestOutils(unittest.TestCase):
    def test_elevation_solaire_reims(self):
        midi_ete = mp.elevation_solaire(datetime(2026, 6, 21, 11, 0, tzinfo=UTC), LAT, LON)
        minuit_ete = mp.elevation_solaire(datetime(2026, 6, 21, 0, 0, tzinfo=UTC), LAT, LON)
        midi_hiver = mp.elevation_solaire(datetime(2026, 12, 21, 11, 0, tzinfo=UTC), LAT, LON)
        self.assertTrue(62 < midi_ete < 66, midi_ete)
        self.assertTrue(-20 < minuit_ete < -14, minuit_ete)
        self.assertTrue(15 < midi_hiver < 19, midi_hiver)

    def test_humectation_proxy(self):
        p = mp.PARAMS
        base = {"temp": 12.0, "hr": 60.0, "pluie": 0.0, "rosee": None, "mouille": None}
        self.assertFalse(mp.est_mouille(base, p))
        self.assertTrue(mp.est_mouille({**base, "pluie": 0.2}, p))
        self.assertTrue(mp.est_mouille({**base, "hr": 92.0}, p))
        self.assertTrue(mp.est_mouille({**base, "rosee": 11.5}, p))
        self.assertFalse(mp.est_mouille({**base, "rosee": 8.0}, p))
        self.assertTrue(mp.est_mouille({**base, "mouille": 0.9}, p))      # le capteur prime
        self.assertFalse(mp.est_mouille({**base, "pluie": 5.0, "mouille": 0.1}, p))

    def test_csv_aller_retour(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            w.writerow(["2026-04-12T00:00", "12.5", "80", "0.4", "9.1"])
            w.writerow(["2026-04-12T01:00", "", "81", "", ""])
            chemin = f.name
        try:
            rows = mp.charger_csv(chemin)
        finally:
            os.unlink(chemin)
        self.assertEqual(rows[0]["t"], datetime(2026, 4, 12, 0, 0, tzinfo=UTC))
        self.assertEqual(rows[0]["temp"], 12.5)
        self.assertIsNone(rows[1]["temp"])
        propres, avert = mp.preparer(rows)
        self.assertEqual(len(propres), 1)
        self.assertTrue(avert)

    def test_avertissement_serie_discontinue(self):
        rows = serie(DEBUT, 10)
        del rows[5]
        _, avert = mp.preparer(rows)
        self.assertTrue(any("discontinuité" in a for a in avert))

    def test_ligne_de_commande_avec_criteres(self):
        import contextlib, io
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 24, pluie=1.0))      # 4 mm étalés sur 4 h
        rows = serie(DEBUT, 120, regles=regles)
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            for r in rows:
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], r["pluie"], ""])
            chemin = f.name
        try:
            def lancer_cli(*args):
                sortie = io.StringIO()
                with contextlib.redirect_stdout(sortie):
                    mp.main([chemin, "--lat", "49.25", "--lon", "4.03", "--maturite", "2026-04-11", *args])
                return sortie.getvalue()
            defaut = lancer_cli()
            large = lancer_cli("--fenetre", "6", "--seuil", "3", "--tolerance", "2", "--minimum", "6", "--hr", "88")
        finally:
            os.unlink(chemin)
        self.assertIn("0 avec dispersion", defaut)                      # critère 1 h / 3 mm : rien
        self.assertIn("1 avec dispersion", large)                       # cumul de 6 h : dispersion
        self.assertIn("4.0 mm/6 h", large)                              # la pluie retenue est affichée

    def test_ligne_de_commande_cumul_et_formule(self):
        import contextlib, io
        rows = serie(datetime(2025, 12, 31, 23, 0, tzinfo=UTC), 40 * 24, base={"temp": 10.0})
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "precipitation", "dew_point_2m"])
            for r in rows:
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], r["pluie"], ""])
            chemin = f.name
        try:
            def cli(*args):
                s = io.StringIO()
                with contextlib.redirect_stdout(s):
                    mp.main([chemin, "--lat", "49.25", "--lon", "4.03", *args])
                return s.getvalue()
            defaut = cli("--cumul", "degres_jours")
            self.assertNotIn("seuil atteint", defaut)                        # 40 j x 2 = 80 °C.j : pas encore 140
            self.assertIn("57 %", defaut)
            self.assertIn("seuil atteint le 2026-01-14", cli("--cumul", "somme"))
            cli("--dh", "produit", "--tmax", "99")                          # s'exécute sans erreur
        finally:
            os.unlink(chemin)

    def test_resume_lisible(self):
        regles = fusion(plage(10, 18, hr=85.0), plage(20, 28, pluie=4.0))
        txt = mp.resume(lancer(serie(DEBUT, 120, regles=regles), FORCE_AVRIL))
        self.assertIn("CYCLES PRIMAIRES : 1", txt)
        self.assertIn("MATURATION DES OOSPORES", txt)


if __name__ == "__main__":
    unittest.main(verbosity=1)
