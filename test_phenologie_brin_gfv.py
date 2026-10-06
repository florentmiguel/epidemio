#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la phénologie BRIN -> feuilles -> GFV. Attentes calculées à la main (températures constantes).

Repères : froid de Bidabé à 10 °C = 2 × 2,17^(-1) = 0,9217 par jour ; Richardson à 10 °C = 5 °C·h par heure ;
à 20 °C constants : temps thermique des feuilles 10 °C·j par jour (9 feuilles = 216 °C·j), somme GFV 20 par jour (F* = 1 217 -> 61e jour)."""
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import phenologie_brin_gfv as bg

UTC = timezone.utc
TZ = ZoneInfo("UTC")                         # tests analytiques : pas de changement d'heure, donc des jours de 24 h et des attentes exactes
TZ_PARIS = ZoneInfo("Europe/Paris")          # test dédié aux changements d'heure et saison réaliste
H = timedelta(hours=1)


def serie(debut: date, fin: date, temp=20.0, regles=None):
    """Série horaire UTC du jour `debut` 00 h au jour `fin` 23 h ; regles(t) -> température remplace la constante."""
    rows, t = [], datetime(debut.year, debut.month, debut.day, tzinfo=UTC)
    stop = datetime(fin.year, fin.month, fin.day, 23, tzinfo=UTC)
    while t <= stop:
        rows.append({"t": t, "temp": regles(t) if regles else temp})
        t += H
    return rows


class TestFormulesDeBase(unittest.TestCase):
    def test_froid_de_bidabe(self):
        self.assertAlmostEqual(bg.froid_jour(0, 0, 2.17), 2.0)
        self.assertAlmostEqual(bg.froid_jour(10, 10, 2.17), 2 / 2.17)
        self.assertAlmostEqual(bg.froid_jour(20, 0, 2.17), 2.17 ** -2 + 1.0)
        self.assertLess(bg.froid_jour(25, 25, 2.17), bg.froid_jour(5, 5, 2.17))                 # le froid est plus efficace

    def test_richardson_horaire(self):
        r = lambda t: bg.richardson_h(t, 5.0, 25.0)                                            # noqa: E731
        self.assertEqual((r(-3), r(3), r(5)), (0.0, 0.0, 0.0))
        self.assertEqual((r(15), r(25), r(40)), (10.0, 20.0, 20.0))                            # plafonné à T_haut - T_bas

    def test_stade_vegetatif(self):
        v = bg.bbch_vegetatif
        self.assertEqual((v(-1), v(0)), (9.0, 9.0))
        self.assertAlmostEqual(v(0.5), 10.0)                                                    # entre 09 et 11
        self.assertEqual((v(1), v(5), v(9)), (11.0, 15.0, 19.0))
        self.assertEqual(v(14), 19.0)                                                           # plafonné à 9 feuilles
        self.assertTrue(all(v(b / 10) <= v((b + 1) / 10) + 1e-12 for b in range(0, 120)))


class TestBRIN(unittest.TestCase):
    def test_dormance_calculee_puis_forcage(self):
        """10 °C constants depuis le 1er août : froid 0,9217 par jour -> 101,2 atteint après 110 jours ; forçage 5 °C·h par heure ->
        6 576,7 atteint à la 1 316e heure, soit 54,8 jours après la levée."""
        b = bg.brin(serie(date(2025, 8, 1), date(2026, 4, 30), 10.0), TZ, 2026)
        levee = date(2025, 8, 1) + timedelta(days=109)
        self.assertEqual((b["dormance"], b["dormance_levee"]), ("calculee", levee))
        self.assertEqual(b["debourrement"], levee + timedelta(days=1 + 54))
        self.assertAlmostEqual(b["froid"], 110 * 2 / 2.17, delta=0.01)

    def test_dormance_supposee_levee_sans_l_automne(self):
        b = bg.brin(serie(date(2026, 1, 1), date(2026, 4, 30), 10.0), TZ, 2026)
        self.assertEqual(b["dormance"], "supposee_levee")
        self.assertEqual(b["debourrement"], date(2026, 1, 1) + timedelta(days=54))              # 1 316 h à 5 °C·h depuis le 1er janvier

    def test_le_froid_seul_ne_fait_pas_debourrer(self):
        b = bg.brin(serie(date(2025, 8, 1), date(2026, 4, 30), 0.0), TZ, 2026)
        self.assertEqual(b["dormance"], "calculee")                                             # 2 par jour : levée au bout de 51 jours...
        self.assertIsNone(b["debourrement"])                                                    # ...mais aucun forçage à 0 °C

    def test_dormance_jamais_levee_en_ete_perpetuel(self):
        b = bg.brin(serie(date(2025, 8, 1), date(2026, 3, 31), 25.0), TZ, 2026)
        self.assertEqual(b["dormance"], "non_levee")                                            # 0,2866 par jour : 69,6 < 101,2
        self.assertIsNone(b["debourrement"])

    def test_le_forcage_est_plafonne_a_25_degres(self):
        b = bg.brin(serie(date(2026, 1, 1), date(2026, 4, 30), 40.0), TZ, 2026)
        self.assertEqual(b["debourrement"], date(2026, 1, 1) + timedelta(days=13))              # 20 °C·h par heure : 329 h = 13,7 jours

    def test_parametres_surcharges(self):
        b = bg.brin(serie(date(2026, 1, 1), date(2026, 1, 31), 10.0), TZ, 2026, {"brin": {"f_crit": 100.0}})
        self.assertEqual(b["debourrement"], date(2026, 1, 1))                                   # 20 heures à 5 °C·h

    def test_serie_vide_ou_sans_temperature(self):
        self.assertIsNone(bg.brin([], TZ, 2026)["debourrement"])
        self.assertIsNone(bg.brin([{"t": datetime(2026, 1, 1, tzinfo=UTC), "temp": None}], TZ, 2026)["debourrement"])


class TestGFV(unittest.TestCase):
    def test_somme_a_base_zero_depuis_le_1er_mars(self):
        s = bg.somme_gfv(serie(date(2026, 2, 15), date(2026, 5, 15), 20.0), TZ, 2026)
        self.assertNotIn(date(2026, 2, 28), s)                                                  # rien avant le 1er mars
        self.assertAlmostEqual(s[date(2026, 4, 29)], 1200.0)                                    # 60 jours à 20 °C
        self.assertAlmostEqual(s[date(2026, 4, 30)], 1220.0)

    def test_temperatures_negatives_ne_comptent_pas(self):
        s = bg.somme_gfv(serie(date(2026, 3, 1), date(2026, 3, 20), -5.0), TZ, 2026)
        self.assertTrue(all(v == 0.0 for v in s.values()))

    def test_temperature_moyenne_du_jour_est_tmin_plus_tmax_sur_deux(self):
        """23 h à 10 °C et 1 h à 30 °C : la moyenne horaire est de 10,8 °C, mais (Tmin + Tmax) / 2 = 20 °C : c'est ce que GFV somme."""
        s = bg.somme_gfv(serie(date(2026, 3, 1), date(2026, 3, 20), regles=lambda t: 30.0 if t.hour == 12 else 10.0), TZ, 2026)
        self.assertAlmostEqual(s[date(2026, 3, 12)] - s[date(2026, 3, 11)], 20.0)

    def test_parametres_de_base(self):
        s = bg.somme_gfv(serie(date(2026, 3, 1), date(2026, 3, 10), 20.0), TZ, 2026, {"gfv": {"t_base": 10.0}})
        self.assertAlmostEqual(s[date(2026, 3, 10)], 100.0)                                     # 10 jours à (20 - 10)


class TestFeuilles(unittest.TestCase):
    def test_temps_thermique_depuis_le_debourrement(self):
        tt = bg.temps_thermique_feuilles(serie(date(2026, 3, 1), date(2026, 4, 30), 20.0), date(2026, 3, 20), TZ, 10.0)
        self.assertNotIn(date(2026, 3, 19), tt)
        self.assertAlmostEqual(tt[date(2026, 3, 20)], 10.0)
        self.assertAlmostEqual(tt[date(2026, 3, 29)], 100.0)

    def test_aucune_croissance_sous_la_base(self):
        tt = bg.temps_thermique_feuilles(serie(date(2026, 3, 1), date(2026, 4, 30), 9.0), date(2026, 3, 20), TZ, 10.0)
        self.assertTrue(all(v == 0.0 for v in tt.values()))


class TestChangementDHeure(unittest.TestCase):
    def test_le_jour_de_23_heures_cumule_moins_de_temps_thermique(self):
        """Le 29/03/2026 (passage à l'heure d'été) dure 23 h à Paris : à 20 °C, 23 × 10 / 24 = 9,58 °C·j au lieu de 10. Le temps thermique est
        une intégrale sur le temps, pas un compte de jours."""
        rows = serie(date(2026, 3, 1), date(2026, 4, 30), 20.0)
        tt = bg.temps_thermique_feuilles(rows, date(2026, 3, 20), TZ_PARIS, 10.0)
        self.assertAlmostEqual(tt[date(2026, 3, 29)] - tt[date(2026, 3, 28)], 23 * 10.0 / 24.0)
        self.assertAlmostEqual(tt[date(2026, 3, 28)] - tt[date(2026, 3, 27)], 10.0)             # un jour ordinaire fait 10
        self.assertAlmostEqual(tt[date(2026, 3, 30)] - tt[date(2026, 3, 29)], 10.0)


class TestChaineComplete(unittest.TestCase):
    """20 °C constants de janvier à septembre, débourrement observé le 20 mars."""
    DEB = date(2026, 3, 20)

    def lancer(self, observations=None, params=None, rows=None, deb=DEB):
        return bg.serie_bbch_brin_gfv(rows or serie(date(2026, 1, 1), date(2026, 9, 30), 20.0), TZ, 2026, params, debourrement=deb,
                                      observations=observations)

    def test_les_jalons_de_la_chaine(self):
        r = self.lancer()
        self.assertTrue(r["actif"])
        self.assertEqual(r["neuf_feuilles"], date(2026, 4, 10))                                 # 216 °C·j à 10 par jour : 22e jour
        self.assertAlmostEqual(r["seuils_gfv"]["s19"], 820.0)                                   # 41 jours de GFV à 20
        self.assertEqual(r["calendrier"]["9 feuilles étalées"], "2026-04-10")
        self.assertEqual(r["calendrier"]["pleine floraison"], "2026-04-30")                    # 1 220 >= 1 217 au 61e jour
        self.assertEqual(r["calendrier"]["début de véraison"], "2026-06-16")                   # BBCH 81 : 1 217 + 0,697 × 1 330 = 2 144 -> 107e jour

    def test_le_stade_vegetatif_puis_le_relais_de_gfv(self):
        r = self.lancer()
        b = r["bbch"]
        self.assertAlmostEqual(b[date(2026, 3, 24)], 12.08, delta=0.02)                         # 50 °C·j = 2,08 feuilles
        self.assertAlmostEqual(b[date(2026, 4, 9)], 18.75, delta=0.02)
        self.assertAlmostEqual(b[date(2026, 4, 10)], 19.0, delta=1e-9)                          # relais exact à 9 feuilles
        self.assertAlmostEqual(b[date(2026, 4, 30)], 65.4, delta=0.3)                           # recalé sur f65 = 1 217
        # BBCH 53 est placé à 21,2 % de l'intervalle (800 -> 1 217), soit à 888 de somme GFV ;
        # au 11/04 la somme vaut 820 : 19 + 34 × (820 - 800) / (888 - 800) = 26,7 ; avant BBCH 53 au 13/04 (somme 860, ~42)
        self.assertAlmostEqual(b[date(2026, 4, 11)], 27.08, delta=0.1)
        self.assertAlmostEqual(b[date(2026, 4, 13)], 43.24, delta=0.1)

    def test_le_stade_ne_recule_jamais_et_part_du_debourrement(self):
        r = self.lancer()
        v = [r["bbch"][j] for j in sorted(r["bbch"])]
        self.assertTrue(all(y >= x - 1e-9 for x, y in zip(v, v[1:])))
        self.assertEqual(min(r["bbch"]), self.DEB)
        self.assertLessEqual(v[-1], 89.0)                                                       # plafonné à la maturité

    def test_calendrier_dans_l_ordre(self):
        cal = self.lancer()["calendrier"]
        dates = [cal[nom] for nom in ("débourrement", "4 feuilles étalées", "7-8 feuilles étalées", "9 feuilles étalées",
                                      "début de floraison", "pleine floraison", "nouaison", "grains de pois", "fermeture de la grappe",
                                      "début de véraison") if cal.get(nom)]
        self.assertEqual(len(dates), 10)
        self.assertEqual(dates, sorted(dates))

    def test_debourrement_observe_prime_sur_brin_et_l_ecart_est_rapporte(self):
        r = self.lancer()
        self.assertEqual(r["debourrement"], self.DEB)
        calcule = bg.brin(serie(date(2026, 1, 1), date(2026, 9, 30), 20.0), TZ, 2026)["debourrement"]
        self.assertEqual(r["debourrement_brin"], calcule)
        self.assertEqual(calcule, date(2026, 1, 19))                                            # 15 °C·h par heure : 438,4 h = 18,3 jours
        self.assertEqual(r["ecart_brin_j"], -60)                                                # BRIN débourre 60 jours avant le 20 mars

    def test_sans_debourrement_observe_brin_decide(self):
        r = bg.serie_bbch_brin_gfv(serie(date(2026, 1, 1), date(2026, 9, 30), 20.0), TZ, 2026)
        self.assertEqual(r["debourrement"], r["debourrement_brin"])
        self.assertIsNone(r["ecart_brin_j"])
        self.assertTrue(r["actif"])

    def test_un_stade_9_observe_tient_lieu_de_debourrement(self):
        r = bg.serie_bbch_brin_gfv(serie(date(2026, 1, 1), date(2026, 9, 30), 20.0), TZ, 2026, observations={"2026-03-20": 9})
        self.assertEqual(r["debourrement"], self.DEB)

    # --- calage sur observations -------------------------------------------------------------------------------------------
    def test_un_stade_de_feuilles_observe_ajuste_le_phyllochrone(self):
        """5 feuilles (BBCH 15) observées le 29/03, soit 100 °C·j après le débourrement : phyllochrone de 20 °C·j -> 9 feuilles à 180 °C·j."""
        r = self.lancer(observations={"2026-03-29": 15})
        self.assertAlmostEqual(r["phyllochron"], 20.0)
        self.assertEqual(r["neuf_feuilles"], date(2026, 4, 6))                                  # 18e jour
        self.assertAlmostEqual(r["bbch"][date(2026, 3, 29)], 15.0, delta=0.02)

    def test_plusieurs_stades_de_feuilles_donnent_la_moyenne(self):
        r = self.lancer(observations={"2026-03-24": 12, "2026-03-29": 15})                      # 50/2 = 25 ; 100/5 = 20
        self.assertAlmostEqual(r["phyllochron"], 22.5)

    def test_la_floraison_observee_remplace_f_star(self):
        r = self.lancer(observations={"2026-04-20": 65})                                         # 51 jours de GFV à 20 = 1 020
        self.assertAlmostEqual(r["seuils_gfv"]["f_star"], 1020.0)
        self.assertEqual(r["calendrier"]["pleine floraison"], "2026-04-20")
        self.assertAlmostEqual(r["bbch"][date(2026, 4, 20)], 65.0, delta=0.05)

    def test_la_veraison_observee_remplace_v_star(self):
        r = self.lancer(observations={"2026-06-15": 83})                                         # 107 jours de GFV à 20 = 2 140
        self.assertAlmostEqual(r["seuils_gfv"]["v_star"], 2140.0)
        self.assertAlmostEqual(r["bbch"][date(2026, 6, 15)], 83.0, delta=0.05)
        defaut = self.lancer()
        self.assertLess(r["calendrier"]["début de véraison"], defaut["calendrier"]["début de véraison"])

    def test_un_stade_intermediaire_observe_s_insere(self):
        r = self.lancer(observations={"2026-05-10": 75})                                         # grains de pois
        self.assertAlmostEqual(r["bbch"][date(2026, 5, 10)], 75.0, delta=0.05)

    def test_observations_incoherentes(self):
        with self.assertRaises(ValueError):
            self.lancer(observations={"2026-04-20": 71, "2026-05-01": 65})                      # nouaison avant floraison
        with self.assertRaises(ValueError):
            self.lancer(observations={"2026-03-25": 65})                                         # floraison avant les 9 feuilles
        with self.assertRaises(ValueError):
            self.lancer(observations={"2026-05-01": 30})                                         # stade non géré
        with self.assertRaises(ValueError):
            self.lancer(observations={"2026-03-01": 13})                                         # avant le débourrement

    # --- cas limites ------------------------------------------------------------------------------------------------------------
    def test_neuf_feuilles_jamais_atteintes(self):
        r = self.lancer(rows=serie(date(2026, 1, 1), date(2026, 9, 30), 11.0))                  # 1 °C·j par jour : 216 jours nécessaires
        self.assertIsNone(r["neuf_feuilles"])
        self.assertTrue(r["actif"])
        self.assertLessEqual(max(r["bbch"].values()), 19.0)
        self.assertIsNone(r["calendrier"]["pleine floraison"])

    def test_floraison_observee_alors_que_les_neuf_feuilles_ne_sont_pas_atteintes(self):
        with self.assertRaises(ValueError):
            self.lancer(rows=serie(date(2026, 1, 1), date(2026, 9, 30), 11.0), observations={"2026-06-01": 65})

    def test_garde_fou_floraison_trop_proche_des_neuf_feuilles(self):
        r = self.lancer(params={"gfv": {"f_star": 850.0}})                                       # 9 feuilles à 820 : 30 < écart minimal de 50
        self.assertAlmostEqual(r["seuils_gfv"]["f_star"], 870.0)
        self.assertTrue(any("presque à la floraison" in a for a in r["avertissements"]))

    def test_serie_qui_commence_apres_le_debourrement(self):
        r = self.lancer(rows=serie(date(2026, 4, 1), date(2026, 9, 30), 20.0))
        self.assertFalse(r["actif"])
        self.assertTrue(any("après le débourrement" in a for a in r["avertissements"]))

    def test_serie_qui_commence_apres_le_1er_mars(self):
        r = self.lancer(rows=serie(date(2026, 3, 10), date(2026, 9, 30), 20.0))
        self.assertTrue(any("somme GFV sous-estimée" in a for a in r["avertissements"]))

    def test_debourrement_avant_le_1er_mars(self):
        r = self.lancer(deb=date(2026, 2, 1))                                                    # 9 feuilles le 22/02 : la somme GFV n'a pas commencé
        v = [r["bbch"][j] for j in sorted(r["bbch"])]
        self.assertTrue(all(y >= x - 1e-9 for x, y in zip(v, v[1:])))
        self.assertEqual(r["neuf_feuilles"], date(2026, 2, 22))
        self.assertAlmostEqual(r["seuils_gfv"]["s19"], 0.0)

    def test_pas_de_debourrement_du_tout(self):
        r = bg.serie_bbch_brin_gfv(serie(date(2026, 1, 1), date(2026, 4, 30), 2.0), TZ, 2026)    # trop froid : BRIN ne débourre pas
        self.assertFalse(r["actif"])
        self.assertTrue(any("pas de débourrement" in a for a in r["avertissements"]))

    def test_serie_vide(self):
        r = bg.serie_bbch_brin_gfv([], TZ, 2026)
        self.assertFalse(r["actif"])

    def test_parametres_d_origine_intacts(self):
        avant = repr(bg.PARAMS)
        self.lancer(params={"gfv": {"f_star": 1000.0}, "feuilles": {"phyllochron": 30.0}}, observations={"2026-04-20": 65})
        self.assertEqual(repr(bg.PARAMS), avant)

    def test_phyllochrone_et_seuils_surcharges(self):
        r = self.lancer(params={"feuilles": {"phyllochron": 48.0}})                              # deux fois plus lent : 9 feuilles à 432 °C·j
        self.assertEqual(r["neuf_feuilles"], date(2026, 5, 2))                                   # 432 °C·j à 10 par jour : 44e jour, soit le 2 mai


class TestAjustementDesFeuilles(unittest.TestCase):
    """Base thermique et phyllochrone ajustés aux feuilles observées (au moins 3 observations)."""
    DEB = date(2026, 3, 28)

    @staticmethod
    def temp_variable(t):
        """Printemps en dents de scie : des jours sous 5 °C, d'autres au-dessus de 15 °C, pour que chaque base thermique donne un temps thermique distinct."""
        import math
        j = t.timetuple().tm_yday
        return 9.0 + 7.0 * math.sin(2 * math.pi * (j - 80) / 23.0) + 4.0 * math.sin(2 * math.pi * (t.hour - 9) / 24.0)

    def observations_vraies(self, base, phyllochron, rows):
        """Feuilles « observées » = temps thermique réel / phyllochrone vrai, au premier jour où elles atteignent 1, 2,5, 4,5, 6,5 puis 8 (un jour
        chaud ajoute moins d'une feuille : on reste sous 9, donc dans l'échelle BBCH 11 à 19)."""
        tt = bg.temps_thermique_feuilles(rows, self.DEB, TZ, base)
        obs = {}
        for cible in (1, 2.5, 4.5, 6.5, 8):
            j = next(j for j in sorted(tt) if tt[j] / phyllochron >= cible)
            obs[j.isoformat()] = 10 + tt[j] / phyllochron
        return obs

    def rows(self):
        return serie(date(2026, 1, 1), date(2026, 9, 30), regles=self.temp_variable)

    def test_retrouve_la_base_et_le_phyllochrone_vrais(self):
        rows = self.rows()
        for base_vraie, ph_vrai in ((5.0, 36.0), (2.0, 50.0), (8.0, 22.0)):
            r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=self.observations_vraies(base_vraie, ph_vrai, rows))
            self.assertEqual(r["t_base_feuilles"], base_vraie)
            self.assertAlmostEqual(r["phyllochron"], ph_vrai, places=6)
            self.assertAlmostEqual(r["rmse_feuilles"], 0.0, places=6)

    def test_ajuster_la_base_ameliore_l_erreur_par_rapport_a_la_base_de_la_litterature(self):
        rows = self.rows()
        obs = self.observations_vraies(4.0, 40.0, rows)
        libre = bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=obs)
        fixe = bg.serie_bbch_brin_gfv(rows, TZ, 2026, {"feuilles": {"ajuster_t_base": False}}, debourrement=self.DEB, observations=obs)
        self.assertEqual(fixe["t_base_feuilles"], 10.0)
        self.assertIsNone(fixe["rmse_feuilles"])                                            # phyllochrone seul : pas d'erreur calculée
        tt10 = bg.temps_thermique_feuilles(rows, self.DEB, TZ, 10.0)
        rmse10 = (sum((tt10[date.fromisoformat(j)] / fixe["phyllochron"] - (s - 10)) ** 2 for j, s in obs.items()) / len(obs)) ** 0.5
        self.assertLess(libre["rmse_feuilles"], rmse10 / 5)

    def test_moins_de_trois_observations_ne_declenchent_pas_l_ajustement_de_la_base(self):
        rows = self.rows()
        obs = dict(list(self.observations_vraies(4.0, 40.0, rows).items())[:2])
        r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=obs)
        self.assertEqual(r["t_base_feuilles"], 10.0)
        self.assertIsNone(r["rmse_feuilles"])

    def test_le_seuil_d_observations_est_configurable(self):
        rows = self.rows()
        obs = dict(list(self.observations_vraies(4.0, 40.0, rows).items())[:2])
        r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, {"feuilles": {"min_observations": 2}}, debourrement=self.DEB, observations=obs)
        self.assertIsNotNone(r["rmse_feuilles"])

    def test_a_erreur_egale_la_base_la_plus_haute_est_retenue(self):
        """À 20 °C constants, le temps thermique est proportionnel au temps pour toute base : toutes ont une erreur nulle ; on garde la plus haute (10)."""
        rows = serie(date(2026, 1, 1), date(2026, 9, 30), 20.0)
        # vrais n à 10 °C·j par jour et phyllochrone 24 : jour k après le 28/03 -> n = 10 k / 24 ; on construit des observations exactes
        obs = {(self.DEB + timedelta(days=k - 1)).isoformat(): 10 + 10.0 * k / 24.0 for k in (3, 9, 15, 21)}
        r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=obs)
        self.assertEqual(r["t_base_feuilles"], 10.0)
        self.assertAlmostEqual(r["phyllochron"], 24.0, places=6)
        self.assertAlmostEqual(r["rmse_feuilles"], 0.0, places=6)

    def test_la_base_ajustee_pilote_les_neuf_feuilles(self):
        rows = self.rows()
        obs = self.observations_vraies(4.0, 40.0, rows)
        r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=obs)
        tt = bg.temps_thermique_feuilles(rows, self.DEB, TZ, 4.0)
        attendu = next(j for j in sorted(tt) if tt[j] / 40.0 >= 9)
        self.assertEqual(r["neuf_feuilles"], attendu)

    def test_grille_personnalisee(self):
        rows = self.rows()
        obs = self.observations_vraies(5.0, 36.0, rows)
        r = bg.serie_bbch_brin_gfv(rows, TZ, 2026, {"feuilles": {"t_base_grille": [0, 10]}}, debourrement=self.DEB, observations=obs)
        self.assertIn(r["t_base_feuilles"], (0.0, 10.0))                                    # la base vraie (5) est hors grille

    def test_observation_de_feuilles_hors_serie_toujours_refusee(self):
        rows = self.rows()
        obs = {**self.observations_vraies(5.0, 36.0, rows), "2026-03-01": 12}
        with self.assertRaises(ValueError):
            bg.serie_bbch_brin_gfv(rows, TZ, 2026, debourrement=self.DEB, observations=obs)


class TestLaChaineSurUneVraieAllure(unittest.TestCase):
    """Saison réaliste : hiver froid, printemps qui se réchauffe, été chaud. Le débourrement doit survenir au printemps, les stades suivre."""
    def test_saison_progressive(self):
        import math

        def temp(t):
            j = t.timetuple().tm_yday
            return 11.0 - 9.0 * math.cos(2 * math.pi * (j - 20) / 365.0)                      # 2 °C en janvier, 20 °C en juillet

        rows = serie(date(2025, 8, 1), date(2026, 9, 30), regles=temp)
        r = bg.serie_bbch_brin_gfv(rows, TZ_PARIS, 2026)
        self.assertEqual(r["dormance"], "calculee")
        self.assertTrue(date(2026, 3, 1) <= r["debourrement"] <= date(2026, 5, 15), r["debourrement"])
        cal = r["calendrier"]
        self.assertTrue(cal["9 feuilles étalées"] < cal["pleine floraison"] < cal["début de véraison"])
        self.assertTrue("2026-05-01" <= cal["pleine floraison"] <= "2026-07-31")


if __name__ == "__main__":
    unittest.main(verbosity=1)
