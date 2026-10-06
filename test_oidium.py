#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests du moteur oïdium v0 : météos synthétiques dont les résultats se déduisent des formalismes (Garin 2011 ; Calonnec et al. 2008).
Valeurs de référence : latence 6 j à 25 °C, ≈ 7 j à 20 °C, ≈ 11 j à 15 °C, bloquée au-delà de 31 °C ; sporulation de ≈ 14 j (15 °C) à ≈ 4,5 j (30 °C)."""
import contextlib
import csv
import io
import math
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

import mildiou_primaire as mp
import oidium as oi

UTC = timezone.utc
H = timedelta(hours=1)
DEBUT = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
SANS_PRIMAIRE = {"primaire": {"severite_precedente": 0}}


def serie(debut=DEBUT, heures=24 * 40, temp=25.0, hr=85.0, pluie=0.0, regles=None):
    """Série horaire constante ; regles(h) -> dict de surcharges pour l'heure h."""
    rows = []
    for h in range(heures):
        r = {"t": debut + h * H, "temp": temp, "hr": hr, "pluie": pluie, "rosee": None, "mouille": None}
        if regles:
            r.update(regles(h) or {})
        rows.append(r)
    return rows


def graine(jour="2026-06-01T00:00", n=1.0):
    return [{"t": jour, "n": n}]


def simuler(rows, **surcharge):
    params = {"primaire": {"severite_precedente": 0}, **surcharge}
    return oi.calculer_saison(rows, params, now=datetime(2026, 12, 1, tzinfo=UTC))


def jours_entre(a: str, b: str) -> float:
    fa = datetime.strptime(a, "%Y-%m-%dT%H:%MZ")
    fb = datetime.strptime(b, "%Y-%m-%dT%H:%MZ")
    return (fb - fa).total_seconds() / 86400.0


class TestFonctionsBiologiques(unittest.TestCase):
    P = oi.PARAMS

    def test_fonction_thermique(self):
        for t in (-5, 4, 5, 31, 35):
            self.assertEqual(oi.f_temp(t, self.P), 0.0, t)
        self.assertEqual(oi.f_temp(None, self.P), 0.0)
        valeurs = [(t / 10, oi.f_temp(t / 10, self.P)) for t in range(51, 310)]
        t_opt, f_max = max(valeurs, key=lambda x: x[1])
        self.assertAlmostEqual(t_opt, 26.4, delta=0.2)                              # optimum du mémoire
        self.assertAlmostEqual(f_max, 1.0, delta=0.01)
        self.assertAlmostEqual(oi.f_temp(25, self.P), 0.99, delta=0.01)
        self.assertAlmostEqual(oi.f_temp(15, self.P), 0.55, delta=0.02)

    def test_duree_de_latence(self):
        self.assertAlmostEqual(oi.duree_latence_j(25, self.P), 6.1, delta=0.15)
        self.assertAlmostEqual(oi.duree_latence_j(20, self.P), 7.4, delta=0.2)
        self.assertAlmostEqual(oi.duree_latence_j(15, self.P), 11.0, delta=0.3)
        self.assertGreater(oi.duree_latence_j(10, self.P), 20)
        self.assertEqual(oi.duree_latence_j(35, self.P), math.inf)                  # développement bloqué

    def test_duree_de_sporulation(self):
        duree = {t: 1.0 / oi.taux_fin_sporulation(t, self.P) for t in (15, 20, 25, 30, 35)}
        self.assertAlmostEqual(duree[15], 14.0, delta=0.5)
        self.assertAlmostEqual(duree[25], 6.6, delta=0.3)
        self.assertAlmostEqual(duree[30], 4.5, delta=0.3)
        self.assertTrue(all(duree[a] > duree[b] for a, b in ((15, 20), (20, 25), (25, 30), (30, 35))))   # plus chaud : plus court
        self.assertTrue(all(3.0 <= v <= 20.0 for v in duree.values()))                                  # fourchette de la littérature

    def test_facteur_d_humidite(self):
        f = lambda hr: oi.facteur_humidite(hr, self.P)                              # noqa: E731
        self.assertEqual((f(30), f(100)), (0.0, 1.0))
        self.assertAlmostEqual(f(38), 0.0, delta=0.01)                              # nul sous ≈ 38 %
        self.assertAlmostEqual(f(50), 0.26, delta=0.02)
        self.assertEqual(f(85), 1.0)                                                # maximal dès 85 %
        self.assertEqual(f(None), 0.5)

    def test_potentiel_horaire_et_eau_libre(self):
        sec = oi.potentiel_horaire(25, 80, False, self.P)
        mouille = oi.potentiel_horaire(25, 95, True, self.P)
        self.assertAlmostEqual(mouille, 0.8 * oi.f_temp(25, self.P), delta=1e-9)    # l'eau libre réduit de 20 %
        self.assertGreater(sec, 0.85)
        self.assertEqual(oi.potentiel_horaire(35, 90, False, self.P), 0.0)          # trop chaud
        self.assertEqual(oi.potentiel_horaire(25, 30, False, self.P), 0.0)          # trop sec

    def test_probabilite_d_infection_pic_de_la_figure_i(self):
        p = oi.probabilite_infection(26, 85, False, self.P)
        self.assertAlmostEqual(p, 0.34, delta=0.02)                                 # figure I du mémoire (feuille de 3 jours)


class TestCycleDUneCohorte(unittest.TestCase):
    def cohorte(self, temp, hr=85.0, heures=24 * 40):
        res = simuler(serie(temp=temp, hr=hr, heures=heures), infections_initiales=graine())
        return next(c for c in res["cohortes"] if c["source"] == "initiale"), res

    def test_latence_a_25_degres(self):
        c, _ = self.cohorte(25)
        self.assertAlmostEqual(jours_entre(c["infection"], c["symptomes"]), 6.1, delta=0.2)

    def test_latence_a_20_et_15_degres(self):
        self.assertAlmostEqual(jours_entre(*[self.cohorte(20)[0][k] for k in ("infection", "symptomes")]), 7.4, delta=0.2)
        self.assertAlmostEqual(jours_entre(*[self.cohorte(15)[0][k] for k in ("infection", "symptomes")]), 11.0, delta=0.3)

    def test_developpement_bloque_hors_de_la_plage(self):
        for t in (4.0, 35.0):
            c, _ = self.cohorte(t)
            self.assertIsNone(c["symptomes"], t)                                    # pas de symptômes en 40 jours

    def test_fin_de_sporulation(self):
        c, _ = self.cohorte(25)
        self.assertAlmostEqual(jours_entre(c["symptomes"], c["fin_sporulation"]), 6.6, delta=0.4)

    def test_en_air_sec_pas_de_nouvelle_infection(self):
        c, res = self.cohorte(25, hr=30.0)
        self.assertIsNotNone(c["symptomes"])                                        # la cohorte initiale se développe...
        self.assertEqual([x for x in res["cohortes"] if x["source"] == "secondaire"], [])   # ...mais ne contamine personne

    def test_les_cohortes_secondaires_naissent_des_symptomes_pas_avant(self):
        c, res = self.cohorte(25)
        premiere = min(x["infection"] for x in res["cohortes"] if x["source"] == "secondaire")
        self.assertGreaterEqual(premiere, c["symptomes"])                           # il faut des colonies sporulantes

    def test_plus_chaud_plus_vite_dans_la_plage(self):
        def debut_g1(temp):
            res = simuler(serie(temp=temp, heures=24 * 60), infections_initiales=graine())
            return min(x["infection"] for x in res["cohortes"] if x["source"] == "secondaire")
        self.assertLess(debut_g1(25), debut_g1(15))


class TestEpidemie(unittest.TestCase):
    def setUp(self):
        self.res = simuler(serie(temp=25, hr=85, heures=24 * 120), infections_initiales=graine())

    def test_plusieurs_generations_sont_simulees(self):
        gens = [g["generation"] for g in self.res["generations"]]
        self.assertEqual(gens[:3], [0, 1, 2])
        premiers = [g["premiers_symptomes"] for g in self.res["generations"][:4]]
        self.assertEqual(premiers, sorted(premiers))                                # chaque génération sort après la précédente
        ecart = (date.fromisoformat(premiers[1]) - date.fromisoformat(premiers[0])).days
        self.assertTrue(5 <= ecart <= 14, ecart)                                    # un cycle dure de l'ordre de 6 à 12 jours

    def test_la_fraction_malade_augmente_puis_sature(self):
        f = [d["fraction_malade"] for d in self.res["jours"]]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(f, f[1:])))              # jamais de guérison
        self.assertLessEqual(max(f), 1.0 + 1e-9)                                    # plafonnée par la capacité
        self.assertGreater(max(f), 0.5)                                             # en conditions favorables, elle devient massive

    def test_les_jalons_sont_dans_l_ordre(self):
        j = self.res["jalons"]
        self.assertLess(j["premiers_symptomes_visibles"], j["fraction_10_pct"])
        self.assertLessEqual(j["fraction_1_pct"], j["fraction_10_pct"])
        self.assertLessEqual(j["fraction_10_pct"], j["fraction_50_pct"])

    def test_plus_d_emission_epidemie_plus_rapide(self):
        lent = simuler(serie(temp=25, heures=24 * 90), infections_initiales=graine(), conidies={"emission_par_colonie_jour": 10.0})
        vif = simuler(serie(temp=25, heures=24 * 90), infections_initiales=graine(), conidies={"emission_par_colonie_jour": 60.0})
        self.assertLess(vif["jalons"]["fraction_10_pct"], lent["jalons"].get("fraction_10_pct", "9999"))

    def test_une_epidemie_sans_depart_ne_demarre_pas(self):
        res = simuler(serie(heures=24 * 60))                                        # ni primaire (sévérité 0), ni amorçage
        self.assertEqual((res["cohortes"], res["jalons"], res["generations"]), ([], {}, []))


class TestInfectionPrimaire(unittest.TestCase):
    """Après le débourrement (15/04 par défaut), une pluie >= 2,5 mm sur 6 h avec T >= 10 °C décharge des ascospores."""
    DEBUT_P = datetime(2026, 4, 10, 0, 0, tzinfo=UTC)

    def run_(self, regles, heures=24 * 30, temp=12.0, severite=2, **extra):
        res = oi.calculer_saison(serie(self.DEBUT_P, heures, temp=temp, hr=70, regles=regles),
                                 {"primaire": {"severite_precedente": severite, **extra.pop("primaire", {})}, **extra},
                                 now=datetime(2026, 12, 1, tzinfo=UTC))
        return res

    def pluie_le(self, jour, mm=3.0, heure=8):
        h0 = (datetime(2026, 4, jour, heure, tzinfo=UTC) - self.DEBUT_P) // H
        return lambda h: {"pluie": mm} if h == h0 else {}

    def test_declenchement_apres_le_debourrement(self):
        res = self.run_(self.pluie_le(20))
        self.assertEqual(len(res["primaires"]), 1)
        self.assertTrue(res["primaires"][0]["t"].startswith("2026-04-20T08"))

    def test_rien_avant_le_debourrement(self):
        self.assertEqual(self.run_(self.pluie_le(12))["primaires"], [])             # 12/04 : la vigne n'a pas débourré

    def test_pluie_insuffisante_ou_trop_froid(self):
        self.assertEqual(self.run_(self.pluie_le(20, mm=2.0))["primaires"], [])
        self.assertEqual(self.run_(self.pluie_le(20), temp=8.0)["primaires"], [])

    def test_une_seule_decharge_par_jour(self):
        h1 = (datetime(2026, 4, 20, 2, tzinfo=UTC) - self.DEBUT_P) // H
        h2 = (datetime(2026, 4, 20, 16, tzinfo=UTC) - self.DEBUT_P) // H            # deux averses séparées de plus de 6 h
        res = self.run_(lambda h: {"pluie": 3.0} if h in (h1, h2) else {})
        self.assertEqual(len(res["primaires"]), 1)

    def test_le_stock_d_ascospores_s_epuise(self):
        jours = (20, 22, 24, 26, 28)
        h0 = {(datetime(2026, 4, j, 8, tzinfo=UTC) - self.DEBUT_P) // H for j in jours}
        res = self.run_(lambda h: {"pluie": 3.0} if h in h0 else {})
        n = [e["colonies"] for e in res["primaires"]]
        self.assertEqual(len(n), 5)
        for a, b in zip(n, n[1:]):
            self.assertAlmostEqual(b / a, 0.75, delta=0.01)                         # 25 % du stock restant à chaque décharge

    def test_la_severite_precedente_echelonne_l_inoculum(self):
        n = {s: self.run_(self.pluie_le(20), severite=s)["primaires"] for s in (0, 1, 3)}
        self.assertEqual(n[0], [])
        self.assertAlmostEqual(n[3][0]["colonies"] / n[1][0]["colonies"], 1.0 / 0.12, delta=0.1)

    def test_l_indice_oidi_remplace_la_severite_et_echelonne_l_inoculum(self):
        def n(**primaire):
            res = self.run_(self.pluie_le(20), severite=primaire.pop("severite", 2), primaire=primaire)
            return res["primaires"][0]["colonies"] if res["primaires"] else 0.0
        self.assertAlmostEqual(n(indice_oidi=95) / n(severite=3), 0.95, delta=0.01)            # stock = indice / 100 ; sévérité 3 = stock 1,0
        self.assertAlmostEqual(n(indice_oidi=50) / n(indice_oidi=100), 0.5, delta=0.01)
        self.assertGreater(n(severite=0, indice_oidi=95), 0)                                    # l'indice prime sur la sévérité
        self.assertEqual(n(severite=3, indice_oidi=0), 0.0)
        self.assertAlmostEqual(n(indice_oidi=150), n(indice_oidi=100), delta=1e-9)             # borné à 100
        self.assertEqual(n(indice_oidi=-5), 0.0)                                                # et à 0

    def test_fenetre_des_ascospores(self):
        res = self.run_(self.pluie_le(20), primaire={"fenetre_jours": 3, "debourrement": "2026-04-15"})
        self.assertEqual(res["primaires"], [])                                      # 20/04 est au-delà de 15/04 + 3 j

    def test_debourrement_configurable(self):
        res = self.run_(self.pluie_le(12), primaire={"debourrement": "2026-04-11"})
        self.assertEqual(len(res["primaires"]), 1)


class TestSorties(unittest.TestCase):
    def test_potentiel_journalier(self):
        res = simuler(serie(temp=25, hr=80, heures=24 * 10), infections_initiales=graine())
        attendu = 100 * oi.f_temp(25, oi.PARAMS) * oi.facteur_humidite(80, oi.PARAMS)
        self.assertAlmostEqual(res["jours"][2]["potentiel_pct"], attendu, delta=0.3)

    def test_latence_equivalente(self):
        res = simuler(serie(temp=25, heures=24 * 5))
        self.assertAlmostEqual(res["jours"][1]["latence_equivalente_j"], 6.1, delta=0.2)
        chaud = simuler(serie(temp=35, heures=24 * 5))
        self.assertIsNone(chaud["jours"][1]["latence_equivalente_j"])               # cycle bloqué

    def test_indice_sur_7_jours(self):
        def regles(h):
            return {"hr": 40.0} if h // 24 < 7 else {"hr": 85.0}
        res = simuler(serie(heures=24 * 14, temp=25, regles=regles))
        pot = [d["potentiel_pct"] for d in res["jours"]]
        self.assertAlmostEqual(res["jours"][6]["indice_7j"], sum(pot[:7]) / 7, delta=0.2)
        self.assertAlmostEqual(res["jours"][13]["indice_7j"], sum(pot[7:14]) / 7, delta=0.2)    # moyenne glissante
        self.assertLess(res["jours"][6]["indice_7j"], res["jours"][13]["indice_7j"])

    def test_jours_previsionnels(self):
        rows = serie(heures=24 * 5)
        res = oi.calculer_saison(rows, SANS_PRIMAIRE, now=datetime(2026, 6, 3, 12, tzinfo=UTC))
        # 120 h UTC depuis minuit couvrent 6 jours locaux (UTC+2) : du 01/06 au 06/06 ; « maintenant » = 03/06 14 h locale
        self.assertEqual([d["previsionnel"] for d in res["jours"]], [False, False, False, True, True, True])

    def test_jours_locaux_europe_paris(self):
        res = simuler(serie(debut=datetime(2026, 6, 1, 23, 0, tzinfo=UTC), heures=3))
        self.assertEqual([d["date"] for d in res["jours"]], ["2026-06-02"])         # 23 h UTC = 01 h le 02/06 à Paris (UTC+2)

    def test_serie_vide_et_trous(self):
        with self.assertRaises(ValueError):
            oi.calculer_saison([], None)
        rows = serie(heures=10) + serie(DEBUT + timedelta(hours=30), 10)
        self.assertTrue(any("discontinuité" in a for a in oi.calculer_saison(rows, SANS_PRIMAIRE)["avertissements"]))

    def test_heures_sans_temperature_ignorees(self):
        rows = serie(heures=10)
        rows[3]["temp"] = None
        self.assertTrue(any("sans température" in a for a in oi.calculer_saison(rows, SANS_PRIMAIRE)["avertissements"]))

    def test_les_parametres_d_origine_ne_sont_pas_modifies(self):
        avant = repr(oi.PARAMS)
        simuler(serie(heures=48), conidies={"emission_par_colonie_jour": 99.0})
        self.assertEqual(repr(oi.PARAMS), avant)


class TestIndiceOidiEnLigneDeCommande(unittest.TestCase):
    def test_option_indice_oidi(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(heures=24 * 5):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                oi.main([chemin, "--indice-oidi", "95", "--severite", "0", "--debourrement", "2026-03-28"])
        finally:
            os.unlink(chemin)
        self.assertIn("stock d'ascospores relatif : 0.95", s.getvalue())                        # l'indice prime sur --severite


class TestADiagnostics(unittest.TestCase):
    def test_retro_horloge_de_latence(self):
        rows = serie(temp=25.0, heures=24 * 40)
        retro = oi.retro_symptomes(rows)
        for jour in (date(2026, 6, 5), date(2026, 6, 10), date(2026, 6, 20)):
            self.assertIn((retro[jour] - jour).days, (6, 7), jour)                   # 6,1 j de latence à 25 °C
        froid = oi.retro_symptomes(serie(temp=15.0, heures=24 * 40))
        self.assertIn((froid[date(2026, 6, 5)] - date(2026, 6, 5)).days, (10, 11, 12))   # ≈ 11 j à 15 °C

    def test_retro_series_trop_courte_ou_developpement_bloque(self):
        retro = oi.retro_symptomes(serie(temp=25.0, heures=24 * 10))
        self.assertIsNone(retro[date(2026, 6, 9)])                                   # les symptômes sortiraient après la fin de la série
        self.assertIsNotNone(retro[date(2026, 6, 2)])
        self.assertTrue(all(v is None for v in oi.retro_symptomes(serie(temp=35.0, heures=24 * 20)).values()))

    def test_un_jour_partiellement_couvert_par_la_fin_de_la_serie_n_a_pas_de_date(self):
        """La série finit le 13/06 à 1 h (heure de Paris). Une infection du 06/06 sort le 12/06 pour toutes ses heures ; une infection du
        07/06 ne sortirait avant la fin des données que pour certaines heures : on ne donne alors pas de date, plutôt qu'une date
        approximative qui ressemblerait à une prévision fiable."""
        retro = oi.retro_symptomes(serie(temp=25.0, heures=24 * 12))
        self.assertEqual(retro[date(2026, 6, 6)], date(2026, 6, 12))
        self.assertIsNone(retro[date(2026, 6, 7)])

    def test_fenetre_d_infection(self):
        retro = oi.retro_symptomes(serie(temp=25.0, heures=24 * 40))
        jours = oi.fenetre_infection(retro, date(2026, 6, 20), tolerance_j=0)
        self.assertTrue(all((retro[j] - date(2026, 6, 20)).days == 0 for j in jours))
        self.assertTrue(1 <= len(jours) <= 2)
        large = oi.fenetre_infection(retro, date(2026, 6, 20), tolerance_j=3)
        self.assertEqual(len(large), 7)                                              # ± 3 jours : sept jours d'infection possibles
        self.assertEqual(oi.fenetre_infection(retro, date(2027, 1, 1)), [])

    def meteo_en_deux_temps(self):
        """30 jours défavorables (12 °C, air sec) puis 30 jours très favorables (25 °C, HR 85 %)."""
        return serie(heures=24 * 60, regles=lambda h: {"temp": 12.0, "hr": 45.0} if h < 24 * 30 else {"temp": 25.0, "hr": 85.0})

    def test_analyse_retro_distingue_favorable_et_defavorable(self):
        rows = self.meteo_en_deux_temps()
        # 18/07 ± 3 j : infections du 08 au 15/07, toutes dans la phase chaude (qui commence le 01/07). Une observation trop proche du
        # changement de régime mélangerait les deux phases : la fenêtre à rebours chevauche alors le froid et le chaud.
        a = oi.analyse_retro(rows, [date(2026, 7, 18), date(2026, 6, 25)])
        favorable, defavorable = a
        self.assertGreater(favorable["potentiel_moyen_pct"], 90)
        self.assertGreaterEqual(favorable["rang_saison_pct"], 45)
        self.assertGreaterEqual(favorable["infections"][0], "2026-07-01")
        self.assertLess(defavorable["potentiel_moyen_pct"], 20)                       # symptômes le 25/06 : infections de la phase froide
        self.assertLessEqual(defavorable["rang_saison_pct"], 5)
        self.assertEqual(len(favorable["latence_j"]), 2)

    def test_le_rang_ignore_les_jours_d_avant_mai(self):
        """Avril (froid) est exclu de la référence : avec un débourrement très précoce il aurait flatté des jours seulement favorables."""
        debut = datetime(2026, 4, 1, tzinfo=UTC)
        rows = serie(debut=debut, heures=24 * 60, regles=lambda h: {"temp": 8.0, "hr": 50.0} if h < 24 * 30 else {"temp": 25.0, "hr": 85.0})
        a = oi.analyse_retro(rows, [date(2026, 5, 18)], {"primaire": {"debourrement": "2026-03-28"}})[0]
        self.assertGreater(a["potentiel_moyen_pct"], 90)                              # infections de mai : phase chaude
        self.assertEqual(a["periode_reference"], ["2026-05-01", "2026-09-30"])
        # Parmi les jours de mai, presque aucun n'est moins favorable (le 1er mai local commence à 22 h UTC le 30/04 : deux heures froides,
        # soit 1 jour sur 30). Avec avril dans la référence, le rang aurait été d'environ 50.
        self.assertLessEqual(a["rang_saison_pct"], 5)
        tard = oi.analyse_retro(rows, [date(2026, 5, 18)], {"primaire": {"debourrement": "2026-05-10"}})[0]
        self.assertEqual(tard["periode_reference"][0], "2026-05-10")                  # un débourrement tardif décale la référence

    def test_analyse_retro_date_sans_infection_possible(self):
        a = oi.analyse_retro(serie(temp=25.0, heures=24 * 20), [date(2026, 12, 25)])
        self.assertEqual(a[0], {"observation": "2026-12-25", "infections": None})

    def test_balayage_des_amorcages(self):
        rows = serie(temp=25.0, hr=85.0, heures=24 * 90)
        b = oi.balayage_graines(rows, date(2026, 6, 1), date(2026, 6, 22), pas_j=7)
        self.assertEqual([x["graine"] for x in b], ["2026-06-01", "2026-06-08", "2026-06-15", "2026-06-22"])
        for x in b:
            g0 = date.fromisoformat(x["G0"])
            self.assertIn((g0 - date.fromisoformat(x["graine"])).days, (6, 7))
            self.assertGreater(x["G1"], x["G0"])
            self.assertGreater(x["G2"], x["G1"])
        self.assertEqual([x["G0"] for x in b], sorted(x["G0"] for x in b))              # amorçage plus tardif : sorties plus tardives

    def test_options_retro_et_balayage_en_ligne_de_commande(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(heures=24 * 60, regles=lambda h: {"temp": 12.0, "hr": 45.0} if h < 24 * 30 else {"temp": 25.0, "hr": 85.0}):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                oi.main([chemin, "--retro", "2026-07-08", "2026-12-25", "--tolerance", "2"])
                oi.main([chemin, "--balayage", "2026-06-01", "2026-06-15"])
        finally:
            os.unlink(chemin)
        sortie = s.getvalue()
        self.assertIn("À REBOURS", sortie)
        self.assertIn("symptômes le 2026-07-08 (± 2 j)", sortie)
        self.assertIn("aucune infection de la série n'y conduit", sortie)             # la date du 25/12 est hors série
        self.assertIn("meilleure que", sortie)
        self.assertIn("des jours du 05-01 au 09-30", sortie)                            # la période de référence est affichée
        self.assertIn("BALAYAGE DES AMORÇAGES", sortie)
        self.assertIn("2026-06-08", sortie)


class TestLigneDeCommande(unittest.TestCase):
    def test_sortie_lisible(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(heures=24 * 60, temp=25.0, hr=85.0):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                oi.main([chemin, "--graine", "2026-06-01", "--severite", "0", "--pas", "10", "--multiplication", "40"])
        finally:
            os.unlink(chemin)
        sortie = s.getvalue()
        self.assertIn("OÏDIUM — cycle par cohortes", sortie)
        self.assertIn("JALONS", sortie)
        self.assertIn("premiers symptômes repérables", sortie)
        self.assertIn("GÉNÉRATIONS", sortie)
        self.assertIn("G0  premiers 2026-06-07", sortie)                            # amorçage le 01/06 + 6 jours de latence


if __name__ == "__main__":
    unittest.main(verbosity=1)
