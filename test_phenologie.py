#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la phénologie : DJC base 10 depuis le débourrement, stade BBCH, calage sur observations, résistance ontogénique.
Attentes calculées à la main : 20 °C constants = 10 DJC par jour ; la table donne BBCH 17 à 120 DJC, 65 à 320 DJC, 89 à 1 250 DJC."""
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import phenologie as ph

UTC = timezone.utc
TZ = ZoneInfo("Europe/Paris")
H = timedelta(hours=1)


def serie(debut: date, jours: int, temp=20.0, regles=None):
    """Série horaire UTC qui démarre à minuit UTC du jour donné, durant `jours` jours ; regles(jour_index) -> température."""
    rows = []
    t0 = datetime(debut.year, debut.month, debut.day, tzinfo=UTC)
    for h in range(jours * 24):
        temperature = regles(h // 24) if regles else temp
        rows.append({"t": t0 + h * H, "temp": temperature, "hr": 70.0, "pluie": 0.0})
    return rows


class TestInterpolation(unittest.TestCase):
    T = ((0, 0.0), (10, 1.0), (20, 0.5))

    def test_points_et_milieux(self):
        self.assertEqual(ph.interpoler(self.T, 10), 1.0)
        self.assertAlmostEqual(ph.interpoler(self.T, 5), 0.5)
        self.assertAlmostEqual(ph.interpoler(self.T, 15), 0.75)

    def test_bornes_et_none(self):
        self.assertEqual(ph.interpoler(self.T, -3), 0.0)
        self.assertEqual(ph.interpoler(self.T, 99), 0.5)
        self.assertIsNone(ph.interpoler(self.T, None))


class TestTableParDefaut(unittest.TestCase):
    def test_table_croissante_et_ancree_sur_le_referentiel(self):
        bbch = [b for b, _ in ph.TABLE_DJC]
        djc = [d for _, d in ph.TABLE_DJC]
        self.assertEqual(bbch, sorted(bbch))
        self.assertTrue(all(b > a for a, b in zip(djc, djc[1:])))                      # strictement croissante
        self.assertEqual(dict(ph.TABLE_DJC)[89], 1250.0)                               # maturité : 1 250 DJC
        self.assertEqual(dict(ph.TABLE_DJC)[9], 0.0)
        for stade in (14, 17, 61, 65, 71, 75, 79, 81):
            self.assertIn(stade, bbch)

    def test_bbch_depuis_djc(self):
        self.assertEqual(ph.bbch_depuis_djc(0.0), 9.0)
        self.assertEqual(ph.bbch_depuis_djc(120.0), 17.0)
        self.assertEqual(ph.bbch_depuis_djc(320.0), 65.0)
        self.assertAlmostEqual(ph.bbch_depuis_djc(7.5), 10.0)                           # mi-chemin de 9 à 11
        self.assertEqual(ph.bbch_depuis_djc(5000.0), 89.0)                              # au-delà : dernier stade
        self.assertIsNone(ph.bbch_depuis_djc(None))


class TestDJC(unittest.TestCase):
    def test_vingt_degres_font_dix_djc_par_jour(self):
        djc = ph.djc_cumules(serie(date(2026, 3, 28), 12, 20.0), date(2026, 3, 29), TZ)
        self.assertAlmostEqual(djc[date(2026, 3, 29)], 10.0)
        self.assertAlmostEqual(djc[date(2026, 3, 30)], 20.0)
        self.assertAlmostEqual(djc[date(2026, 4, 5)], 80.0)

    def test_sous_la_base_ou_a_la_base_rien_ne_s_accumule(self):
        for t in (3.0, 10.0):
            djc = ph.djc_cumules(serie(date(2026, 3, 28), 5, t), date(2026, 3, 29), TZ)
            self.assertTrue(all(v == 0.0 for v in djc.values()), t)

    def test_les_jours_avant_le_debourrement_ne_comptent_pas(self):
        djc = ph.djc_cumules(serie(date(2026, 3, 20), 15, 20.0), date(2026, 3, 28), TZ)
        self.assertNotIn(date(2026, 3, 27), djc)
        self.assertAlmostEqual(djc[date(2026, 3, 28)], 10.0)

    def test_une_serie_qui_commence_apres_le_debourrement_n_est_pas_exploitable(self):
        self.assertEqual(ph.djc_cumules(serie(date(2026, 4, 2), 10), date(2026, 3, 28), TZ), {})
        self.assertEqual(ph.djc_cumules([], date(2026, 3, 28), TZ), {})

    def test_jours_locaux_europe_paris(self):
        """Seul le premier jour UTC est chaud (20 °C), les suivants sont à 10 °C (0 DJC). À Paris (UTC+2), le jour local du 02/06 contient
        les 2 dernières heures chaudes du 01/06 UTC : sa moyenne est (2 × 20 + 22 × 10) / 24 = 10,83 °C, soit 0,83 DJC. En jours UTC,
        ce 2 juin ferait 0 DJC : la valeur 0,83 prouve que les jours sont bien locaux."""
        rows = serie(date(2026, 6, 1), 3, regles=lambda j: 20.0 if j == 0 else 10.0)
        djc = ph.djc_cumules(rows, date(2026, 6, 1), TZ)
        self.assertAlmostEqual(djc[date(2026, 6, 1)], 10.0, places=6)                    # 22 heures chaudes seulement dans la série
        self.assertAlmostEqual(djc[date(2026, 6, 2)] - djc[date(2026, 6, 1)], 10.0 / 12.0, places=6)

    def test_heures_sans_temperature_ignorees(self):
        rows = serie(date(2026, 3, 28), 2, 20.0)
        rows[3]["temp"] = None
        self.assertAlmostEqual(ph.djc_cumules(rows, date(2026, 3, 28), TZ)[date(2026, 3, 29)], 20.0, delta=0.5)


class TestSerieBBCH(unittest.TestCase):
    def test_le_stade_suit_les_djc(self):
        b = ph.serie_bbch(serie(date(2026, 3, 28), 40, 20.0), date(2026, 3, 29), TZ)
        self.assertAlmostEqual(b[date(2026, 4, 9)], 17.0, delta=0.05)                   # 12 jours après le 29/03 : 120 DJC
        self.assertAlmostEqual(b[date(2026, 3, 29)], 10.33, delta=0.05)                 # 10 DJC : 9 + 2 × 10/15

    def test_le_stade_ne_recule_jamais(self):
        regles = lambda j: 25.0 if j % 5 else 4.0                                       # noqa: E731  semaine en dents de scie
        b = ph.serie_bbch(serie(date(2026, 3, 28), 80, regles=regles), date(2026, 3, 29), TZ)
        v = [b[j] for j in sorted(b)]
        self.assertTrue(all(y >= x - 1e-12 for x, y in zip(v, v[1:])))

    def test_serie_qui_ne_couvre_pas_le_debourrement(self):
        self.assertEqual(ph.serie_bbch(serie(date(2026, 4, 10), 20), date(2026, 3, 29), TZ), {})

    def test_froid_le_stade_reste_au_debourrement(self):
        b = ph.serie_bbch(serie(date(2026, 3, 28), 30, 6.0), date(2026, 3, 29), TZ)
        self.assertTrue(all(v == 9.0 for v in b.values()))


class TestCalage(unittest.TestCase):
    def test_un_stade_observe_remplace_son_seuil(self):
        t = ph.calage_table(ph.TABLE_DJC, {65: 200.0})
        self.assertEqual(dict(t)[65], 200.0)
        djc = [d for _, d in t]
        self.assertTrue(all(b >= a for a, b in zip(djc, djc[1:])))                       # table toujours croissante

    def test_les_stades_non_observes_sont_tires_vers_les_observations(self):
        t = dict(ph.calage_table(ph.TABLE_DJC, {65: 200.0}))
        self.assertLessEqual(t[61], 200.0)                                               # avant la pleine floraison : pas au-delà
        self.assertGreaterEqual(t[71], 200.0)                                            # après : pas en deçà
        self.assertEqual(t[17], 120.0)                                                   # les stades éloignés ne bougent pas

    def test_un_stade_hors_table_est_insere(self):
        t = dict(ph.calage_table(ph.TABLE_DJC, {12: 25.0}))
        self.assertEqual(t[12], 25.0)
        self.assertEqual(sorted(t), list(t))

    def test_observations_incoherentes(self):
        with self.assertRaises(ValueError):
            ph.calage_table(ph.TABLE_DJC, {65: 200.0, 71: 150.0})                        # la nouaison ne précède pas la pleine floraison

    def test_serie_recalee_sur_observation(self):
        rows = serie(date(2026, 3, 28), 90, 20.0)
        defaut = ph.serie_bbch(rows, date(2026, 3, 29), TZ)
        # on observe la pleine floraison (BBCH 65) à 200 DJC, soit 20 jours après le débourrement : plus tôt que la table (320 DJC)
        recale = ph.serie_bbch(rows, date(2026, 3, 29), TZ, observations={date(2026, 4, 17): 65})
        self.assertAlmostEqual(recale[date(2026, 4, 17)], 65.0, delta=0.05)
        self.assertAlmostEqual(defaut[date(2026, 4, 17)], 56.08, delta=0.05)              # table par défaut : 53 à 150 DJC, 57 à 215 DJC
        self.assertGreater(recale[date(2026, 5, 5)], defaut[date(2026, 5, 5)])           # le reste de la saison est avancé

    def test_observation_au_format_texte(self):
        rows = serie(date(2026, 3, 28), 60, 20.0)
        b = ph.serie_bbch(rows, date(2026, 3, 29), TZ, observations={"2026-04-17": 65})
        self.assertAlmostEqual(b[date(2026, 4, 17)], 65.0, delta=0.05)

    def test_observation_hors_de_la_serie(self):
        with self.assertRaises(ValueError):
            ph.serie_bbch(serie(date(2026, 3, 28), 30), date(2026, 3, 29), TZ, observations={date(2026, 3, 1): 11})
        with self.assertRaises(ValueError):
            ph.serie_bbch(serie(date(2026, 3, 28), 30), date(2026, 3, 29), TZ, observations={date(2027, 1, 1): 11})


class TestResistanceOntogenique(unittest.TestCase):
    def test_sensibilite_des_grappes(self):
        self.assertEqual(ph.sens_grappes(17), 0.0)                                       # pas encore de grappes
        self.assertEqual(ph.sens_grappes(None), 0.0)
        self.assertEqual(ph.sens_grappes(65), 1.0)                                       # floraison : maximale
        self.assertEqual(ph.sens_grappes(71), 1.0)                                       # nouaison : maximale
        self.assertAlmostEqual(ph.sens_grappes(75), 0.6)                                 # grains de pois : en baisse
        self.assertAlmostEqual(ph.sens_grappes(79), 0.2)                                 # fermeture : résiduelle de 20 %
        self.assertGreaterEqual(ph.sens_grappes(89), 0.05)                               # jamais nulle

    def test_la_sensibilite_des_grappes_decroit_apres_la_nouaison(self):
        v = [ph.sens_grappes(b) for b in range(71, 90)]
        self.assertTrue(all(y <= x + 1e-12 for x, y in zip(v, v[1:])))

    def test_sensibilite_des_feuilles_douce_en_fin_de_saison(self):
        self.assertEqual(ph.sens_feuilles(40), 1.0)
        self.assertEqual(ph.sens_feuilles(79), 1.0)
        self.assertLess(ph.sens_feuilles(89), 1.0)
        self.assertGreaterEqual(ph.sens_feuilles(89), 0.4)                               # les feuilles restent attaquables
        self.assertEqual(ph.sens_feuilles(None), 1.0)

    def test_surface_foliaire(self):
        self.assertEqual(ph.surface_foliaire(None), 0.0)
        v = [ph.surface_foliaire(b) for b in range(9, 90)]
        self.assertTrue(all(y >= x - 1e-12 for x, y in zip(v, v[1:])))                   # elle ne fait que croître
        self.assertEqual(ph.surface_foliaire(79), 1.0)
        self.assertLess(ph.surface_foliaire(9), 0.05)


class TestCalendrierEtFenetre(unittest.TestCase):
    def setUp(self):
        self.b = ph.serie_bbch(serie(date(2026, 3, 28), 150, 20.0), date(2026, 3, 29), TZ)

    def test_calendrier_dans_l_ordre_du_temps(self):
        cal = ph.calendrier(self.b)
        dates = [cal[nom] for nom, _ in ph.STADES_CLES if cal[nom]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(cal["débourrement"], "2026-03-29")
        self.assertEqual(cal["7-8 feuilles étalées"], "2026-04-09")                    # 120 DJC à 10 DJC/jour = 12 jours

    def test_stade_non_atteint(self):
        court = ph.serie_bbch(serie(date(2026, 3, 28), 8, 20.0), date(2026, 3, 29), TZ)
        self.assertIsNone(ph.calendrier(court)["maturité"])

    def test_fenetre_de_sensibilite_des_grappes(self):
        debut, fin = ph.fenetre(self.b, ph.sens_grappes, 0.5)
        self.assertLess(debut, fin)
        self.assertTrue(self.b[date.fromisoformat(debut)] >= 53 and self.b[date.fromisoformat(fin)] <= 77.5)
        self.assertEqual(ph.fenetre(self.b, lambda x: 0.0, 0.5), (None, None))


class TestDeformationEntreStadesObserves(unittest.TestCase):
    def test_un_stade_entre_deux_observes_garde_sa_position_relative(self):
        """65 observé à 200 DJC et 79 à 500 : le stade 71 (400 DJC dans la table, entre 320 et 760) se place à la même fraction de l'intervalle
        observé : 200 + (400 - 320) / (760 - 320) × (500 - 200) = 254,5. Sans cela il garderait 400 : un palier puis un saut."""
        t = dict(ph.calage_table(ph.TABLE_DJC, {65: 200.0, 79: 500.0}))
        self.assertAlmostEqual(t[71], 200.0 + 80.0 / 440.0 * 300.0, places=6)
        self.assertAlmostEqual(t[75], 200.0 + 250.0 / 440.0 * 300.0, places=6)
        djc = [d for _, d in sorted(t.items())]
        self.assertTrue(all(b >= a for a, b in zip(djc, djc[1:])))

    def test_les_stades_hors_des_observations_gardent_l_ancien_comportement(self):
        t = dict(ph.calage_table(ph.TABLE_DJC, {65: 200.0, 79: 500.0}))
        self.assertEqual(t[17], 120.0)                                                   # avant la première observation : inchangé
        self.assertGreaterEqual(t[89], 500.0)                                            # après la dernière : jamais en deçà

    def test_une_serie_complete_d_observations_est_reproduite(self):
        rows = serie(date(2026, 3, 20), 120, 20.0)
        obs = {"2026-04-05": 15, "2026-04-20": 53, "2026-05-05": 65, "2026-05-25": 75, "2026-06-15": 79}
        b = ph.serie_bbch(rows, date(2026, 3, 20), TZ, observations=obs)
        for j, s in obs.items():
            self.assertAlmostEqual(b[date.fromisoformat(j)], float(s), delta=0.1)
        v = [b[j] for j in sorted(b)]
        self.assertTrue(all(y >= x - 1e-9 for x, y in zip(v, v[1:])))


class TestLectureDesStades(unittest.TestCase):
    def ecrire(self, texte):
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(texte)
        f.close()
        self.addCleanup(lambda: __import__("os").unlink(f.name))
        return f.name

    def test_lecture_nominale(self):
        r = ph.lire_stades(self.ecrire("date,bbch,note\n2026-04-07,11,1 feuille\n2026-05-26,57-60,BSV\n2026-06-02,68-71\n"))
        self.assertEqual(r, {"2026-04-07": 11, "2026-05-26": 58.5, "2026-06-02": 69.5})

    def test_en_tete_commentaires_lignes_vides_et_virgule_decimale(self):
        r = ph.lire_stades(self.ecrire("# stades du BSV\n\ndate,bbch\n2026-05-05,\"17,5\"\n  \n2026-05-12,19\n"))
        self.assertEqual(r, {"2026-05-05": 17.5, "2026-05-12": 19})

    def test_sans_en_tete(self):
        self.assertEqual(ph.lire_stades(self.ecrire("2026-05-12,19\n")), {"2026-05-12": 19})

    def test_erreurs_precises(self):
        for contenu, attendu in (("2026-13-45,19\n", "ligne 1 : date illisible"), ("2026-05-12\n", "attendu « date,bbch »"),
                                 ("date,bbch\n2026-05-12,beaucoup\n", "ligne 2 : stade illisible")):
            with self.assertRaises(ValueError) as cm:
                ph.lire_stades(self.ecrire(contenu))
            self.assertIn(attendu, str(cm.exception))

    def test_fichier_absent(self):
        with self.assertRaises(OSError):
            ph.lire_stades("/chemin/qui/n/existe/pas.csv")


class TestEcartsAuxObservations(unittest.TestCase):
    def setUp(self):
        self.b = ph.serie_bbch(serie(date(2026, 3, 20), 100, 20.0), date(2026, 3, 20), TZ)      # 65 atteint à 320 DJC : 32e jour = 20/04

    def test_ecart_positif_quand_le_modele_est_en_retard(self):
        e = ph.ecarts_observations(self.b, {"2026-04-10": 65})[0]
        self.assertEqual((e["modele"], e["ecart_j"]), ("2026-04-20", 10))

    def test_ecart_negatif_quand_le_modele_est_en_avance(self):
        self.assertEqual(ph.ecarts_observations(self.b, {"2026-05-01": 65})[0]["ecart_j"], -11)

    def test_stade_jamais_atteint(self):
        e = ph.ecarts_observations(self.b, {"2026-06-01": 89})[0]
        self.assertEqual((e["modele"], e["ecart_j"]), (None, None))

    def test_le_debourrement_est_ignore_et_l_ordre_est_chronologique(self):
        e = ph.ecarts_observations(self.b, {"2026-05-01": 65, "2026-03-20": 9, "2026-04-05": 17})
        self.assertEqual([x["date"] for x in e], ["2026-04-05", "2026-05-01"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
