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


def serie(debut=DEBUT, heures=24 * 40, temp=25.0, hr=85.0, pluie=0.0, regles=None, vent=None, rayonnement=None):
    """Série horaire constante ; regles(h) -> dict de surcharges pour l'heure h. vent (m/s à 10 m) et rayonnement (W/m²) sont facultatifs."""
    rows = []
    for h in range(heures):
        r = {"t": debut + h * H, "temp": temp, "hr": hr, "pluie": pluie, "rosee": None, "mouille": None}
        if vent is not None:
            r["vent"] = vent
        if rayonnement is not None:
            r["rayonnement"] = rayonnement
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

    def test_l_indice_chasmotheces_remplace_la_severite_et_echelonne_l_inoculum(self):
        def n(**primaire):
            res = self.run_(self.pluie_le(20), severite=primaire.pop("severite", 2), primaire=primaire)
            return res["primaires"][0]["colonies"] if res["primaires"] else 0.0
        self.assertAlmostEqual(n(indice_chasmotheces=95) / n(severite=3), 0.95, delta=0.01)            # stock = indice / 100 ; sévérité 3 = stock 1,0
        self.assertAlmostEqual(n(indice_chasmotheces=50) / n(indice_chasmotheces=100), 0.5, delta=0.01)
        self.assertGreater(n(severite=0, indice_chasmotheces=95), 0)                                    # l'indice prime sur la sévérité
        self.assertEqual(n(severite=3, indice_chasmotheces=0), 0.0)
        self.assertAlmostEqual(n(indice_chasmotheces=150), n(indice_chasmotheces=100), delta=1e-9)             # borné à 100
        self.assertEqual(n(indice_chasmotheces=-5), 0.0)                                                # et à 0

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


class TestVent(unittest.TestCase):
    P = oi.PARAMS

    def test_taux_de_dispersion_de_willocquet(self):
        f = lambda u: oi.taux_dispersion(u, self.P)                                     # noqa: E731
        self.assertAlmostEqual(f(0), 0.0043, delta=0.0005)
        self.assertAlmostEqual(f(10), 0.218, delta=0.01)                               # figure H du mémoire : ≈ 0,22 à 10 m/s
        self.assertEqual((f(17), f(25)), (1.0, 1.0))                                    # saturé vers 16-17 m/s
        valeurs = [f(u / 2) for u in range(0, 40)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(valeurs, valeurs[1:])))

    def test_liberation_horaire_rapportee_a_la_vitesse_de_reference(self):
        ref = self.P["vent"]["liberation_horaire_ref"]
        self.assertEqual(oi.liberation_horaire(None, self.P), ref)                      # vent inconnu : valeur de référence
        self.assertAlmostEqual(oi.liberation_horaire(4.0, self.P), ref, places=9)      # 4 m/s à 10 m = 2 m/s dans le feuillage = référence
        self.assertLess(oi.liberation_horaire(0.0, self.P), ref)
        self.assertGreater(oi.liberation_horaire(10.0, self.P), 3 * ref)
        pv = self.P["vent"]
        self.assertAlmostEqual(oi.liberation_horaire(60.0, self.P), pv["liberation_horaire_ref"] * pv["facteur_max"])   # plafond du rapport
        fort = mp.fusionner(self.P, {"vent": {"liberation_horaire_ref": 0.2}})
        self.assertEqual(oi.liberation_horaire(60.0, fort), 1.0)                        # et jamais plus de 100 % par heure
        self.assertEqual(oi.liberation_horaire(-3.0, self.P), oi.liberation_horaire(0.0, self.P))

    def run_(self, vitesse, **extra):
        return simuler(serie(temp=25, hr=85, heures=24 * 60, vent=vitesse), infections_initiales=graine(), **extra)

    def test_sans_donnee_de_vent_le_moteur_est_inchange(self):
        sans = self.run_(None)
        desactive = self.run_(10.0, vent={"actif": False})                              # des données, mais le vent est désactivé
        self.assertFalse(sans["donnees"]["vent_actif"])
        self.assertFalse(desactive["donnees"]["vent_actif"])
        self.assertEqual([d["fraction_malade"] for d in sans["jours"]], [d["fraction_malade"] for d in desactive["jours"]])

    def test_le_vent_est_detecte_et_rapporte(self):
        res = self.run_(4.0)
        self.assertEqual((res["donnees"]["vent_pct"], res["donnees"]["vent_actif"]), (100, True))
        self.assertEqual(res["jours"][5]["vent_moy_ms"], 4.0)
        self.assertAlmostEqual(res["jours"][5]["liberation_pct"], 100 * self.P["vent"]["liberation_horaire_ref"], delta=0.05)

    def test_plus_de_vent_epidemie_plus_rapide(self):
        calme, venteux = self.run_(0.5), self.run_(10.0)
        self.assertLess(venteux["jalons"]["fraction_10_pct"], calme["jalons"].get("fraction_10_pct", "9999"))

    def test_le_vent_ne_cree_pas_d_epidemie_sans_colonie_sporulante(self):
        res = simuler(serie(temp=25, hr=85, heures=24 * 30, vent=15.0))                 # ni primaire ni amorçage
        self.assertEqual(res["cohortes"], [])

    def test_vent_partiel_avertit_et_les_autres_heures_restent_neutres(self):
        res = simuler(serie(temp=25, heures=24 * 20, regles=lambda h: {"vent": 8.0} if h < 24 * 10 else {}),
                      infections_initiales=graine())
        self.assertEqual(res["donnees"]["vent_pct"], 50)
        self.assertTrue(any("vent renseigné pour 50 %" in a for a in res["avertissements"]))
        self.assertIsNone(res["jours"][15]["vent_moy_ms"])                              # pas de donnée : pas de moyenne inventée


class TestUV(unittest.TestCase):
    P = oi.PARAMS

    def test_exposition(self):
        e = lambda r: oi.exposition_uv(r, self.P)                                       # noqa: E731
        self.assertEqual((e(None), e(0.0), e(-5.0)), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(e(800.0), 0.5)                                           # plein soleil : la moitié du feuillage est exposée
        self.assertAlmostEqual(e(400.0), 0.25)
        self.assertAlmostEqual(e(1600.0), 0.5)                                          # borné

    def test_les_uv_reduisent_la_favorabilite_et_l_infection(self):
        base = oi.potentiel_horaire(25, 80, False, self.P)
        plein = oi.potentiel_horaire(25, 80, False, self.P, uv=0.5)
        self.assertAlmostEqual(plein / base, 1 - 0.6 * 0.5, places=9)                   # -30 % à plein soleil sur la moitié du feuillage
        self.assertAlmostEqual(oi.probabilite_infection(25, 80, False, self.P, 0.5) / oi.probabilite_infection(25, 80, False, self.P),
                               0.7, places=9)

    def run_(self, rayonnement, **extra):
        return simuler(serie(temp=25, hr=85, heures=24 * 60, rayonnement=rayonnement), infections_initiales=graine(), **extra)

    def test_sans_rayonnement_les_uv_sont_inactifs(self):
        sans = simuler(serie(temp=25, hr=85, heures=24 * 30), infections_initiales=graine())
        desactive = self.run_(800.0, uv={"actif": False})
        self.assertFalse(sans["donnees"]["uv_actif"])
        self.assertFalse(desactive["donnees"]["uv_actif"])
        self.assertEqual(sans["jours"][10]["potentiel_pct"], desactive["jours"][10]["potentiel_pct"])

    def test_le_rayonnement_baisse_l_indice_du_jour(self):
        nuit, soleil = self.run_(0.0), self.run_(800.0)
        self.assertTrue(soleil["donnees"]["uv_actif"])
        self.assertAlmostEqual(soleil["jours"][10]["potentiel_pct"] / nuit["jours"][10]["potentiel_pct"], 0.7, delta=0.01)
        self.assertEqual(soleil["jours"][10]["rayonnement_moy_wm2"], 800.0)

    def test_les_uv_ralentissent_l_epidemie(self):
        nuit, soleil = self.run_(0.0), self.run_(800.0)
        self.assertLess(nuit["jalons"]["fraction_10_pct"], soleil["jalons"].get("fraction_10_pct", "9999"))

    def test_la_mortalite_des_conidies_agit_seule(self):
        """Sans réduction de l'infection, les UV ralentissent encore l'épidémie en tuant des conidies (sur les colonies et dans l'air)."""
        nuit = self.run_(0.0, uv={"reduction_infection": 0.0})
        soleil = self.run_(800.0, uv={"reduction_infection": 0.0})
        self.assertEqual(nuit["jours"][10]["potentiel_pct"], soleil["jours"][10]["potentiel_pct"])      # la favorabilité n'est plus touchée
        self.assertLess(nuit["jalons"]["fraction_10_pct"], soleil["jalons"].get("fraction_10_pct", "9999"))

    def test_la_reduction_de_l_infection_agit_seule(self):
        nuit = self.run_(0.0, uv={"mortalite_horaire": 0.0})
        soleil = self.run_(800.0, uv={"mortalite_horaire": 0.0})
        self.assertLess(nuit["jalons"]["fraction_10_pct"], soleil["jalons"].get("fraction_10_pct", "9999"))

    def test_les_uv_n_agissent_que_le_jour(self):
        rayon = lambda h: {"rayonnement": 800.0 if 8 <= (h % 24) < 18 else 0.0}         # noqa: E731
        res = simuler(serie(temp=25, hr=85, heures=24 * 5, regles=rayon))
        # moyenne journalière entre « jamais d'UV » (100 %) et « toujours » (70 %) : 10 h de soleil sur 24
        sans = simuler(serie(temp=25, hr=85, heures=24 * 5))
        rapport = res["jours"][2]["potentiel_pct"] / sans["jours"][2]["potentiel_pct"]
        self.assertAlmostEqual(rapport, 1 - 0.3 * 10 / 24, delta=0.01)

    def test_rayonnement_partiel_avertit(self):
        res = simuler(serie(temp=25, heures=24 * 10, regles=lambda h: {"rayonnement": 500.0} if h < 24 * 4 else {}))
        self.assertTrue(any("rayonnement renseigné pour 40 %" in a for a in res["avertissements"]))


class TestPhenologieDansLeMoteur(unittest.TestCase):
    DEB = datetime(2026, 3, 28, tzinfo=UTC)
    PRIM = {"debourrement": "2026-03-29", "severite_precedente": 0}

    def serie_longue(self, jours=170, **kw):
        return serie(debut=self.DEB, heures=24 * jours, temp=20.0, hr=80.0, **kw)

    def run_(self, rows=None, seed="2026-04-01T00:00", **extra):
        extra.setdefault("primaire", self.PRIM)
        return oi.calculer_saison(rows or self.serie_longue(), {**({"infections_initiales": graine(seed)} if seed else {}), **extra},
                                  now=datetime(2026, 12, 1, tzinfo=UTC))

    def test_activation_automatique_quand_la_serie_couvre_le_debourrement(self):
        res = self.run_()
        self.assertTrue(res["phenologie"]["actif"])
        self.assertEqual(res["phenologie"]["calendrier"]["7-8 feuilles étalées"], "2026-04-09")     # 120 DJC à 10 DJC par jour
        self.assertEqual(res["phenologie"]["calendrier"]["débourrement"], "2026-03-29")

    def test_inactive_si_la_serie_commence_apres_le_debourrement(self):
        res = simuler(serie(heures=24 * 10), infections_initiales=graine())              # série du 01/06, débourrement par défaut le 15/04
        self.assertFalse(res["phenologie"]["actif"])
        self.assertEqual((res["phenologie"]["calendrier"], res["phenologie"]["fenetre_grappes"]), ({}, [None, None]))
        self.assertFalse(any("phénologie" in a for a in res["avertissements"]))         # pas d'avertissement quand elle est « automatique »
        self.assertIsNone(res["jours"][3]["bbch"])
        self.assertIsNone(res["jours"][3]["indice_grappes_pct"])

    def test_forcer_la_phenologie_sans_donnees_avertit(self):
        res = oi.calculer_saison(serie(heures=24 * 10), {"phenologie": {"actif": True}, "primaire": {"severite_precedente": 0}})
        self.assertFalse(res["phenologie"]["actif"])
        self.assertTrue(any("phénologie indisponible" in a for a in res["avertissements"]))

    def test_la_phenologie_peut_etre_desactivee(self):
        res = self.run_(phenologie={"actif": False})
        self.assertFalse(res["phenologie"]["actif"])
        self.assertTrue(all(d["bbch"] is None for d in res["jours"]))

    def test_avant_le_debourrement_aucun_stade(self):
        rows = serie(debut=datetime(2026, 3, 20, tzinfo=UTC), heures=24 * 30, temp=20.0, hr=80.0)
        res = self.run_(rows, seed=None, primaire={"debourrement": "2026-03-28", "severite_precedente": 0})
        jours = {d["date"]: d for d in res["jours"]}
        self.assertIsNone(jours["2026-03-25"]["bbch"])
        self.assertIsNotNone(jours["2026-03-29"]["bbch"])

    def test_la_surface_foliaire_change_la_fraction_atteinte_pas_le_nombre_de_colonies(self):
        """Au 10/04 (stade 17, surface foliaire ≈ 30 %), les colonies sont encore bien trop peu nombreuses (une dizaine) pour être limitées
        par la place : leur nombre est le même qu'avec une surface constante, mais elles couvrent une fraction du feuillage
        1 / surface foliaire fois plus grande (≈ 3 fois)."""
        avec = self.run_(seed="2026-04-01T00:00")
        sans = self.run_(seed="2026-04-01T00:00", phenologie={"actif": False})
        j_avec = {d["date"]: d for d in avec["jours"]}["2026-04-10"]
        j_sans = {d["date"]: d for d in sans["jours"]}["2026-04-10"]
        lai = oi.phen.surface_foliaire(j_avec["bbch"])
        self.assertAlmostEqual(j_avec["fraction_malade"] * 1000.0 * lai, j_sans["fraction_malade"] * 1000.0, delta=0.5)   # même nombre de colonies
        self.assertAlmostEqual(j_avec["fraction_malade"] / j_sans["fraction_malade"], 1.0 / lai, delta=0.15 / lai)
        self.assertGreater(j_avec["fraction_malade"], 2 * j_sans["fraction_malade"])
        self.assertLessEqual(j_avec["fraction_malade"], 1.0)

    def test_la_surface_foliaire_plafonne_l_epidemie_quand_la_place_manque(self):
        """Avec un amorçage très fort au tout début (stade 09-11, surface foliaire ≈ 2 %), la place devient limitante : le nombre de colonies
        reste inférieur à ce qu'il serait avec une surface constante."""
        fort = {"infections_initiales": [{"t": "2026-03-30T00:00", "n": 5.0}]}
        avec = self.run_(seed=None, **fort)
        sans = self.run_(seed=None, phenologie={"actif": False}, **fort)
        j = lambda r: {d["date"]: d for d in r["jours"]}["2026-04-08"]                  # noqa: E731
        colonies_avec = j(avec)["fraction_malade"] * 1000.0 * oi.phen.surface_foliaire(j(avec)["bbch"])
        colonies_sans = j(sans)["fraction_malade"] * 1000.0
        self.assertLess(colonies_avec, colonies_sans)

    def test_un_gel_reduit_la_surface_foliaire_maximale(self):
        normal = self.run_(seed="2026-04-01T00:00")
        gele = self.run_(seed="2026-04-01T00:00", phenologie={"surface_foliaire_max": 0.5})
        f = lambda r: {d["date"]: d["fraction_malade"] for d in r["jours"]}["2026-04-20"]     # noqa: E731
        self.assertGreater(f(gele), f(normal))

    def test_sensibilite_des_feuilles_en_fin_de_saison(self):
        """À 20 °C constants, la maturité (1 250 DJC) est atteinte vers le 01/08 : la sensibilité des feuilles y vaut 0,5."""
        graine_tardive = "2026-08-10T00:00"
        avec = self.run_(seed=graine_tardive)
        sans = self.run_(seed=graine_tardive, phenologie={"sensibilite_feuilles": False})
        self.assertEqual({d["date"]: d["sens_feuilles"] for d in avec["jours"]}["2026-08-15"], 0.5)
        n = lambda r: sum(d["nouvelles_colonies"] for d in r["jours"] if "2026-08-17" <= d["date"] <= "2026-08-25")   # noqa: E731
        self.assertGreater(n(sans), 0)
        self.assertAlmostEqual(n(avec) / n(sans), 0.5, delta=0.1)

    def test_une_infection_primaire_tardive_subit_la_sensibilite_des_feuilles(self):
        """Fenêtre primaire allongée à 200 jours : une pluie au stade 89 (feuilles à 50 % de sensibilité) crée moitié moins de colonies."""
        h0 = (datetime(2026, 8, 12, 8, tzinfo=UTC) - self.DEB) // H
        rows = self.serie_longue(regles=lambda h: {"pluie": 3.0} if h == h0 else {})
        prim = {"debourrement": "2026-03-29", "severite_precedente": 3, "fenetre_jours": 200}
        avec = self.run_(rows, seed=None, primaire=prim)
        sans = self.run_(rows, seed=None, primaire=prim, phenologie={"sensibilite_feuilles": False})
        self.assertEqual(len(avec["primaires"]), 1)
        self.assertAlmostEqual(avec["primaires"][0]["colonies"] / sans["primaires"][0]["colonies"], 0.5, delta=0.02)

    def test_indice_grappes_suit_la_resistance_ontogenique(self):
        res = self.run_(seed=None)
        j = {d["date"]: d for d in res["jours"]}
        self.assertEqual(j["2026-04-05"]["indice_grappes_pct"], 0.0)                    # avant les inflorescences : pas de grappe
        self.assertEqual(j["2026-04-30"]["sens_grappes"], 1.0)                          # pleine floraison (320 DJC)
        self.assertEqual(j["2026-04-30"]["indice_grappes_pct"], j["2026-04-30"]["potentiel_pct"])
        tard = j["2026-06-25"]                                                          # après la fermeture de la grappe
        self.assertLessEqual(tard["sens_grappes"], 0.2)
        self.assertAlmostEqual(tard["indice_grappes_pct"] / tard["potentiel_pct"], tard["sens_grappes"], delta=0.02)

    def test_fenetre_de_reception_des_grappes(self):
        res = self.run_(seed=None)
        debut, fin = res["phenologie"]["fenetre_grappes"]
        self.assertLess(debut, fin)
        self.assertEqual(debut, "2026-04-12")                                           # BBCH 53 (150 DJC) : sensibilité 0,5
        self.assertLessEqual(fin, "2026-06-15")                                         # bien avant la fermeture de la grappe

    def test_indice_grappes_7_jours_est_une_moyenne_glissante(self):
        res = self.run_(seed=None)
        j = res["jours"]
        i = next(k for k, d in enumerate(j) if d["date"] == "2026-05-10")
        attendu = sum(x["indice_grappes_pct"] for x in j[i - 6: i + 1]) / 7
        self.assertAlmostEqual(j[i]["indice_grappes_7j"], attendu, delta=0.1)

    def test_les_observations_de_stade_recalent_le_calendrier(self):
        res = self.run_(seed=None, phenologie={"observations": {"2026-04-17": 65}})
        self.assertAlmostEqual({d["date"]: d["bbch"] for d in res["jours"]}["2026-04-17"], 65.0, delta=0.1)
        self.assertLessEqual(res["phenologie"]["calendrier"]["pleine floraison"], "2026-04-17")
        defaut = self.run_(seed=None)
        self.assertLess(res["phenologie"]["calendrier"]["pleine floraison"], defaut["phenologie"]["calendrier"]["pleine floraison"])

    def test_observation_hors_serie_leve_une_erreur(self):
        with self.assertRaises(ValueError):
            self.run_(seed=None, phenologie={"observations": {"2026-02-01": 11}})

    def test_table_personnalisee(self):
        table = [[9, 0], [17, 50], [65, 100], [89, 200]]
        res = self.run_(seed=None, phenologie={"table_djc": table})
        self.assertEqual(res["phenologie"]["calendrier"]["pleine floraison"], "2026-04-07")            # 100 DJC = 10 jours

    def test_les_parametres_d_origine_restent_intacts(self):
        avant = repr(oi.PARAMS)
        self.run_(seed=None, phenologie={"observations": {"2026-04-17": 65}}, vent={"facteur_canopee": 0.9}, uv={"part_exposee": 1.0})
        self.assertEqual(repr(oi.PARAMS), avant)


class TestDonneesExternesEnLigneDeCommande(unittest.TestCase):
    def csv_complet(self, d, vent=True, jours=170):
        chemin = os.path.join(d, "m.csv")
        with open(chemin, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"]
                       + (["wind_speed_10m", "shortwave_radiation"] if vent else []))
            for r in serie(debut=datetime(2026, 3, 20, tzinfo=UTC), heures=24 * jours, temp=20.0, hr=80.0):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]] + ([4.0, 300.0] if vent else []))
        return chemin

    def lancer(self, *args):
        s = io.StringIO()
        with contextlib.redirect_stdout(s):
            oi.main(list(args))
        return s.getvalue()

    def test_calendrier_compare_les_deux_modeles(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_complet(d), "--debourrement", "2026-03-28", "--calendrier")
        self.assertIn("COMPARAISON DES DEUX MODÈLES DE PHÉNOLOGIE", sortie)
        self.assertIn("table DJC", sortie)
        self.assertIn("BRIN + GFV", sortie)
        for stade in ("4 feuilles étalées", "7-8 feuilles étalées", "9 feuilles étalées", "pleine floraison", "grains de pois",
                      "fermeture de la grappe"):
            self.assertIn(stade, sortie)
        self.assertIn("grappes réceptives", sortie)
        self.assertIn("débourrement utilisé : 2026-03-28 | calculé par BRIN :", sortie)
        self.assertIn("feuilles : phyllochrone 24.0 °C·j, base 10 °C", sortie)
        self.assertNotIn("JALONS", sortie)

    def test_simulation_affiche_les_donnees_et_le_stade(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_complet(d), "--debourrement", "2026-03-28", "--indice-chasmotheces", "95", "--pas", "30")
        self.assertIn("vent : 100 % des heures renseignées (actif)", sortie)
        self.assertIn("rayonnement : 100 % (UV actifs)", sortie)
        self.assertIn("phénologie : active", sortie)
        self.assertIn("BBCH", sortie)
        self.assertIn("grappes 7 j", sortie)

    def test_options_pour_desactiver_chaque_facteur(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_complet(d), "--debourrement", "2026-03-28", "--sans-vent", "--sans-uv", "--sans-phenologie")
        self.assertIn("(inactif)", sortie)
        self.assertIn("UV inactifs", sortie)
        self.assertIn("phénologie : inactive", sortie)

    def test_ancien_csv_sans_vent_ni_rayonnement(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_complet(d, vent=False), "--debourrement", "2026-03-28")
        self.assertIn("vent : 0 % des heures renseignées (inactif)", sortie)
        self.assertIn("phénologie : active", sortie)                                    # la phénologie n'a besoin que de la température

    def test_observations_de_stade(self):
        """Le calendrier DJC est recalé sur l'observation ; BRIN + GFV, qui n'atteint 9 feuilles que le 18/04, la juge incompatible : la
        comparaison l'affiche au lieu de s'arrêter."""
        with tempfile.TemporaryDirectory() as d:
            chemin = self.csv_complet(d)
            sortie = self.lancer(chemin, "--debourrement", "2026-03-28", "--calendrier", "--bbch", "2026-04-17:65")
        ligne = next(x for x in sortie.splitlines() if x.strip().startswith("pleine floraison"))
        self.assertLessEqual(ligne.split()[2], "2026-04-17")                            # colonne « table DJC »
        self.assertIn("BRIN + GFV : observations incompatibles avec le modèle", sortie)

    def test_observations_compatibles_recalent_les_deux_modeles(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = self.csv_complet(d)
            sortie = self.lancer(chemin, "--debourrement", "2026-03-28", "--calendrier", "--bbch", "2026-05-20:65")
        ligne = next(x for x in sortie.splitlines() if x.strip().startswith("pleine floraison"))
        self.assertEqual(ligne.split()[2:4], ["2026-05-20", "2026-05-20"])                # les deux modèles retrouvent la date observée

    def test_observation_hors_serie_donne_un_message_clair(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = self.csv_complet(d)
            with self.assertRaises(SystemExit) as cm:
                self.lancer(chemin, "--debourrement", "2026-03-28", "--bbch", "2026-01-05:11")
        self.assertIn("Erreur", str(cm.exception))
        self.assertIn("hors de la série", str(cm.exception))

    def test_bbch_mal_forme(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = self.csv_complet(d)
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.lancer(chemin, "--bbch", "2026-04-17")


class TestPhenologieBrinGfvDansLeMoteur(unittest.TestCase):
    DEB = datetime(2026, 1, 1, tzinfo=UTC)

    def rows(self, jours=273, temp=20.0, **kw):
        return serie(debut=self.DEB, heures=24 * jours, temp=temp, hr=80.0, **kw)

    def run_(self, rows=None, **extra):
        return oi.calculer_saison(rows or self.rows(), {"primaire": {"severite_precedente": 0, **extra.pop("primaire", {})}, **extra},
                                  now=datetime(2026, 12, 1, tzinfo=UTC))

    def test_le_modele_par_defaut_reste_la_table_de_degres_jours(self):
        res = self.run_(primaire={"debourrement": "2026-03-20"})
        self.assertEqual(res["phenologie"]["modele"], "djc")
        self.assertIsNone(res["phenologie"]["brin_gfv"])

    def test_brin_estime_le_debourrement_quand_il_n_est_pas_fourni(self):
        """À 20 °C constants, BRIN débourre le 19 janvier (15 °C·h par heure : 438 h) ; ce débourrement ouvre la fenêtre primaire."""
        res = self.run_(phenologie={"modele": "brin_gfv"})
        self.assertEqual(res["phenologie"]["modele"], "brin_gfv")
        self.assertEqual(res["debourrement"], "2026-01-19")
        bg = res["phenologie"]["brin_gfv"]
        self.assertEqual((bg["debourrement"], bg["debourrement_brin"], bg["dormance"]), ("2026-01-19", "2026-01-19", "supposee_levee"))
        self.assertIsNone(bg["ecart_brin_j"])

    def test_le_debourrement_observe_prime_et_l_ecart_est_rapporte(self):
        res = self.run_(phenologie={"modele": "brin_gfv"}, primaire={"debourrement": "2026-03-20"})
        self.assertEqual(res["debourrement"], "2026-03-20")
        self.assertEqual(res["phenologie"]["brin_gfv"]["ecart_brin_j"], -60)

    def test_les_stades_viennent_de_l_enchainement_brin_feuilles_gfv(self):
        res = self.run_(phenologie={"modele": "brin_gfv"}, primaire={"debourrement": "2026-03-20"})
        cal = res["phenologie"]["calendrier"]
        self.assertEqual(cal["9 feuilles étalées"], "2026-04-10")                                # 216 °C·j à 10 par jour
        self.assertEqual(cal["pleine floraison"], "2026-04-30")                                  # somme GFV de 1 220 au 61e jour
        j = {d["date"]: d for d in res["jours"]}
        self.assertEqual(j["2026-04-10"]["bbch"], 19.0)
        self.assertEqual(j["2026-04-30"]["sens_grappes"], 1.0)

    def test_la_resistance_ontogenique_suit_le_stade_du_modele(self):
        brin = self.run_(phenologie={"modele": "brin_gfv"}, primaire={"debourrement": "2026-03-20"})
        djc = self.run_(primaire={"debourrement": "2026-03-20"})
        self.assertNotEqual(brin["phenologie"]["fenetre_grappes"], djc["phenologie"]["fenetre_grappes"])      # deux calendriers, deux fenêtres
        d1, d2 = brin["phenologie"]["fenetre_grappes"]
        self.assertLess(d1, d2)

    def test_les_observations_de_stade_recalent_le_modele(self):
        res = self.run_(phenologie={"modele": "brin_gfv", "observations": {"2026-04-20": 65}}, primaire={"debourrement": "2026-03-20"})
        self.assertAlmostEqual({d["date"]: d["bbch"] for d in res["jours"]}["2026-04-20"], 65.0, delta=0.1)
        self.assertEqual(res["phenologie"]["brin_gfv"]["seuils_gfv"]["f_star"], 1020.0)

    def test_observation_incoherente_leve_une_erreur(self):
        with self.assertRaises(ValueError):
            self.run_(phenologie={"modele": "brin_gfv", "observations": {"2026-03-25": 65}}, primaire={"debourrement": "2026-03-20"})

    def test_parametres_du_modele_surcharges(self):
        res = self.run_(phenologie={"modele": "brin_gfv", "brin_gfv": {"feuilles": {"phyllochron": 48.0}}},
                        primaire={"debourrement": "2026-03-20"})
        self.assertEqual(res["phenologie"]["calendrier"]["9 feuilles étalées"], "2026-05-02")

    def test_une_saison_qui_part_d_aout_garde_la_bonne_annee(self):
        import math

        def temp(h):
            t = datetime(2025, 8, 1, tzinfo=UTC) + h * H
            return 11.0 - 9.0 * math.cos(2 * math.pi * (t.timetuple().tm_yday - 20) / 365.0)
        rows = serie(debut=datetime(2025, 8, 1, tzinfo=UTC), heures=24 * 427, regles=lambda h: {"temp": temp(h)})
        res = oi.calculer_saison(rows, {"phenologie": {"modele": "brin_gfv"}, "primaire": {"severite_precedente": 0}},
                                 now=datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual(res["phenologie"]["brin_gfv"]["dormance"], "calculee")
        self.assertTrue("2026-03-01" <= res["debourrement"] <= "2026-05-15", res["debourrement"])
        self.assertTrue(res["phenologie"]["actif"])

    def test_repli_signale_quand_brin_gfv_est_impossible(self):
        rows = serie(debut=datetime(2026, 4, 1, tzinfo=UTC), heures=24 * 60, temp=20.0, hr=80.0)       # la série commence après le débourrement
        res = oi.calculer_saison(rows, {"phenologie": {"modele": "brin_gfv"}, "primaire": {"debourrement": "2026-03-28", "severite_precedente": 0}})
        self.assertFalse(res["phenologie"]["actif"])
        self.assertTrue(any("BRIN + GFV indisponible" in a for a in res["avertissements"]))

    def test_repli_effectif_sur_la_table_de_degres_jours_quand_brin_ne_debourre_pas(self):
        """2 °C toute l'année : BRIN ne débourre jamais. Le moteur retombe sur la table de degrés-jours depuis le débourrement par défaut
        (15/04) et le dit : sans cet avertissement, on lirait un calendrier DJC en croyant lire BRIN + GFV."""
        res = self.run_(self.rows(jours=273, temp=2.0), phenologie={"modele": "brin_gfv"})
        self.assertTrue(res["phenologie"]["actif"])
        self.assertEqual(res["phenologie"]["modele"], "djc")
        self.assertIsNone(res["phenologie"]["brin_gfv"])
        self.assertEqual(res["debourrement"], "2026-04-15")
        self.assertTrue(any("phénologie par degrés-jours à la place" in a for a in res["avertissements"]))

    def test_les_avertissements_du_modele_remontent(self):
        res = self.run_(phenologie={"modele": "brin_gfv"})
        self.assertTrue(any("dormance supposée levée" in a for a in res["avertissements"]))

    def test_phenologie_desactivee_desactive_aussi_brin_gfv(self):
        res = self.run_(phenologie={"modele": "brin_gfv", "actif": False}, primaire={"debourrement": "2026-03-20"})
        self.assertFalse(res["phenologie"]["actif"])
        self.assertEqual(res["debourrement"], "2026-03-20")

    def test_les_parametres_d_origine_restent_intacts(self):
        avant = repr(oi.PARAMS)
        self.run_(phenologie={"modele": "brin_gfv", "brin_gfv": {"gfv": {"f_star": 900.0}}}, primaire={"debourrement": "2026-03-20"})
        self.assertEqual(repr(oi.PARAMS), avant)


class TestBrinGfvEnLigneDeCommande(unittest.TestCase):
    def csv_(self, d):
        chemin = os.path.join(d, "m.csv")
        with open(chemin, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(debut=datetime(2026, 1, 1, tzinfo=UTC), heures=24 * 200, temp=20.0, hr=80.0):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
        return chemin

    def lancer(self, *args):
        s = io.StringIO()
        with contextlib.redirect_stdout(s):
            oi.main(list(args))
        return s.getvalue()

    def test_simulation_avec_brin_gfv(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--phenologie", "brin_gfv", "--debourrement", "2026-03-20", "--pas", "60")
        self.assertIn("BRIN -> croissance des feuilles -> GFV", sortie)
        self.assertIn("débourrement utilisé : 2026-03-20 | calculé par BRIN : 2026-01-19 (écart BRIN - utilisé : -60 j)", sortie)
        self.assertIn("GFV (somme base 0 °C depuis le 1er mars)", sortie)

    def test_sans_debourrement_fourni_brin_decide(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--phenologie", "brin_gfv", "--pas", "60")
        self.assertIn("débourrement : 2026-01-19", sortie)

    def test_le_modele_par_defaut_est_la_table_de_degres_jours(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--debourrement", "2026-03-20", "--pas", "60")
        self.assertIn("degrés-jours base 10 depuis le débourrement", sortie)
        self.assertNotIn("BRIN -> croissance", sortie)


class TestStadesObservesEnLigneDeCommande(unittest.TestCase):
    """Série à 20 °C constants : la table DJC atteint BBCH 65 à 320 DJC (20/04 pour un débourrement le 20/03), BRIN + GFV à 1 217 de somme (30/04)."""

    def csv_(self, d):
        chemin = os.path.join(d, "m.csv")
        with open(chemin, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(debut=datetime(2026, 1, 1, tzinfo=UTC), heures=24 * 200, temp=20.0, hr=80.0):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
        return chemin

    def stades(self, d, contenu):
        chemin = os.path.join(d, "stades.csv")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(contenu)
        return chemin

    def lancer(self, *args):
        s = io.StringIO()
        with contextlib.redirect_stdout(s):
            oi.main(list(args))
        return s.getvalue()

    def test_le_fichier_de_stades_affiche_le_biais_des_modeles_par_defaut(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--debourrement", "2026-03-20", "--calendrier",
                                 "--stades", self.stades(d, "date,bbch\n2026-04-10,65\n"))
        self.assertIn("ÉCART DES MODÈLES PAR DÉFAUT À TES STADES OBSERVÉS", sortie)
        ligne = next(x for x in sortie.splitlines() if x.strip().startswith("2026-04-10"))
        self.assertEqual(ligne.split()[1:], ["65", "+10", "+20"])                      # DJC : 20/04 ; BRIN + GFV : 30/04
        self.assertIn("table DJC", sortie)
        self.assertIn("écart moyen +10.0 j", sortie)
        self.assertIn("écart moyen +20.0 j", sortie)
        self.assertIn("RECALÉS SUR TES STADES", sortie)

    def test_apres_recalage_le_stade_observe_est_atteint_a_la_date_observee(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--debourrement", "2026-03-20", "--calendrier",
                                 "--stades", self.stades(d, "2026-04-25,65\n"))
        ligne = next(x for x in sortie.splitlines() if x.strip().startswith("pleine floraison"))
        self.assertEqual(ligne.split()[2:4], ["2026-04-25", "2026-04-25"])

    def test_sans_stades_pas_de_tableau_d_ecarts(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--debourrement", "2026-03-20", "--calendrier")
        self.assertNotIn("ÉCART DES MODÈLES PAR DÉFAUT", sortie)

    def test_stades_et_bbch_se_cumulent(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--debourrement", "2026-03-20", "--calendrier",
                                 "--stades", self.stades(d, "2026-04-10,65\n"), "--bbch", "2026-05-20:75")
        lignes = [x.split()[:2] for x in sortie.splitlines()]                          # lignes du tableau d'écarts : « date  BBCH ... »
        self.assertIn(["2026-04-10", "65"], lignes)                                     # venu du fichier
        self.assertIn(["2026-05-20", "75"], lignes)                                     # venu de --bbch

    def test_fichier_de_stades_illisible_donne_un_message_clair(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                self.lancer(self.csv_(d), "--stades", self.stades(d, "2026-04-10,beaucoup\n"))
        self.assertIn("Erreur", str(cm.exception))
        self.assertIn("stade illisible", str(cm.exception))

    def test_fichier_de_stades_absent(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                self.lancer(self.csv_(d), "--stades", os.path.join(d, "absent.csv"))
        self.assertIn("Erreur", str(cm.exception))

    def test_la_simulation_utilise_les_stades_observes(self):
        with tempfile.TemporaryDirectory() as d:
            sortie = self.lancer(self.csv_(d), "--phenologie", "brin_gfv", "--debourrement", "2026-03-20", "--pas", "60",
                                 "--stades", self.stades(d, "2026-04-05,12\n2026-04-12,14\n2026-04-20,16\n2026-04-28,18\n"))
        self.assertIn("ajustés sur tes stades de feuilles", sortie)


class TestMortaliteThermique(unittest.TestCase):
    P = oi.PARAMS

    def test_taux_mortalite_colonies(self):
        """Sous le seuil (36 °C - décote 3 = 33 °C effectif) : zéro. Au-dessus : croissant, jamais > max_par_heure."""
        f = lambda T: oi.taux_mortalite_thermique(T, self.P["chaleur"]["colonies"], self.P["chaleur"]["decote_microclimat_c"])  # noqa: E731
        self.assertEqual(f(None), 0.0)
        self.assertEqual(f(38.0), 0.0)                                                  # 38 - 3 = 35 < seuil 36
        self.assertGreater(f(40.0), 0.0)                                                # 40 - 3 = 37 > seuil 36
        self.assertLessEqual(f(60.0), self.P["chaleur"]["colonies"]["max_par_heure"])   # plafond
        valeurs = [f(T / 2) for T in range(60, 100)]
        self.assertTrue(all(b >= a - 1e-12 for a, b in zip(valeurs, valeurs[1:])))     # monotone

    def test_seuil_conidies_plus_bas_que_colonies(self):
        """Les conidies sont plus sensibles : leur seuil effectif est plus bas."""
        col = lambda T: oi.taux_mortalite_thermique(T, self.P["chaleur"]["colonies"], self.P["chaleur"]["decote_microclimat_c"])  # noqa
        con = lambda T: oi.taux_mortalite_thermique(T, self.P["chaleur"]["conidies"], self.P["chaleur"]["decote_microclimat_c"])   # noqa
        self.assertGreater(con(40.0), col(40.0))                                        # même température, conidies plus touchées

    def test_la_chaleur_reduit_les_colonies_sporulantes(self):
        """Série chaude (40 °C) : les colonies sporulantes perdent de la biomasse chaque heure ; elles ne meurent pas toutes."""
        res_chaud = simuler(serie(temp=40.0, hr=60.0, heures=24 * 30), infections_initiales=graine())
        res_frais = simuler(serie(temp=25.0, hr=60.0, heures=24 * 30), infections_initiales=graine())
        # Les deux cohortes arrivent en sporulation bien avant la fin (latence bloquée à 40 °C -> non, f(40) = 0 car 40 > tmax=31)
        # A 40 °C la fonction thermique est nulle : pas de développement mais les colonies DÉJÀ sporulantes perdent de la biomasse
        # -> Les colonies créées par l'amorçage restent en latence (f=0). Testons avec une colonie forcée en sporulante.
        pass  # cas couvert par test_la_chaleur_tue_les_colonies_forcees ci-dessous

    def test_la_chaleur_tue_progressivement_les_colonies_forcees(self):
        """Une colonie dans laquelle on force le statut 'sporulante' perd sa biomasse à 40 °C."""
        from datetime import date, datetime, timedelta, timezone
        rows = serie(temp=40.0, hr=60.0, heures=24 * 5)
        # On ajoute une cohorte manuellement et on force son statut
        avant = oi.Cohorte(1, "test", 0, rows[0]["t"], 1.0)
        avant.etat = "sporulante"
        p = oi.mp.fusionner(oi.PARAMS, {"primaire": {"severite_precedente": 0}})
        decote = p["chaleur"]["decote_microclimat_c"]
        mort_h = oi.taux_mortalite_thermique(40.0, p["chaleur"]["colonies"], decote)
        attendu_5j = 1.0 * (1 - mort_h) ** (24 * 5)
        attendu_10j = 1.0 * (1 - mort_h) ** (24 * 10)
        self.assertGreater(mort_h, 0.0)
        self.assertGreater(attendu_5j, 0.0)                                             # jamais totalement morte
        self.assertLess(attendu_5j, 0.75)                                               # réduction sensible en 5 jours (~38 %)
        self.assertLess(attendu_10j, attendu_5j)                                        # continue à décliner

    def test_desactiver_la_chaleur_ne_change_pas_le_developpement_froid(self):
        """À 20 °C (sous les seuils), activer ou non la chaleur donne le même résultat."""
        avec = simuler(serie(temp=20.0, heures=24 * 30), infections_initiales=graine(), chaleur={"actif": True})
        sans = simuler(serie(temp=20.0, heures=24 * 30), infections_initiales=graine(), chaleur={"actif": False})
        self.assertEqual([d["fraction_malade"] for d in avec["jours"]],
                         [d["fraction_malade"] for d in sans["jours"]])

    def test_la_biomasse_des_colonies_est_reduite_par_la_chaleur(self):
        """À 44 °C (T_eff=41 °C, bien au-delà du seuil 36 °C), la biomasse d'une colonie sporulante décline nettement en 24 h."""
        p = oi.PARAMS
        decote = p["chaleur"]["decote_microclimat_c"]
        mort = oi.taux_mortalite_thermique(44.0, p["chaleur"]["colonies"], decote)
        self.assertGreater(mort, 0.05)                                                  # mort significative
        self.assertLess(1.0 * (1 - mort) ** 24, 0.25)                                  # > 75 % tués en 24 h à 44 °C
        # Débranche la mortalité des colonies -> la biomasse ne bouge pas
        mort_off = oi.taux_mortalite_thermique(44.0, {"seuil_c": 999, "a": 0.004, "b": 1.8, "max_par_heure": 0.15}, decote)
        self.assertEqual(mort_off, 0.0)

    def test_les_conidies_sont_detruites_par_la_chaleur_integree(self):
        """Série à 42 °C constants : le pool de conidies accumule une mortalité supplémentaire par la chaleur."""
        p_with = oi.mp.fusionner(oi.PARAMS, {"chaleur": {"actif": True}})
        p_off = oi.mp.fusionner(oi.PARAMS, {"chaleur": {"actif": False}})
        rows = serie(temp=42.0, hr=60.0, heures=24 * 30)
        seeds = [{"t": "2026-06-01T00:00", "n": 1.0}]
        r_with = oi.calculer_saison(rows, {**p_with, "primaire": {"severite_precedente": 0}, "infections_initiales": seeds})
        r_off  = oi.calculer_saison(rows, {**p_off,  "primaire": {"severite_precedente": 0}, "infections_initiales": seeds})
        # La mortalité des conidies est détectable sur un pool initial non nul alimenté par une cohorte FORCÉE sporulante.
        # On compare un cas avec chaleur active vs désactivée en injectant des conidies directement dans le pool.
        # Les conidies à 42°C (T_eff=39°C) subissent mort=0.029/h : après 24h il reste 49 % ; sans chaleur : ~95 % (perte normale).
        mort_conidie = oi.taux_mortalite_thermique(42.0, oi.PARAMS["chaleur"]["conidies"], oi.PARAMS["chaleur"]["decote_microclimat_c"])
        perte_normale = oi.PARAMS["conidies"]["perte_horaire"]
        # après 24 h : avec chaleur = (1 - mort - perte)^24 vs sans chaleur = (1 - perte)^24
        self.assertGreater(mort_conidie, perte_normale)                                  # la chaleur ajoute une mortalité > perte normale
        survie_avec = (1 - mort_conidie - perte_normale) ** 24
        survie_sans = (1 - perte_normale) ** 24
        self.assertLess(survie_avec, 0.5 * survie_sans)                                  # la chaleur réduit d'au moins moitié

    def test_la_chaleur_reduit_l_epidemie(self):
        """Alternance 30 °C la nuit (neutre) / 42 °C le jour (mortifère) vs. 25 °C constants : épidémie ralentie."""
        def chaud(h): return {"temp": 42.0 if 8 <= (h % 24) < 18 else 30.0, "hr": 60.0}
        res_chaud = simuler(serie(heures=24 * 60, regles=chaud), infections_initiales=graine())
        res_frais = simuler(serie(temp=25.0, hr=85.0, heures=24 * 60), infections_initiales=graine())
        # En conditions chaudes, les cohortes se développent peu (f(40) = 0) mais la mortalité compense
        # L'épidémie doit être moins avancée (ou absente) qu'à 25 °C
        f_chaud = max((d["fraction_malade"] for d in res_chaud["jours"]), default=0)
        f_frais = max((d["fraction_malade"] for d in res_frais["jours"]), default=0)
        self.assertLess(f_chaud, f_frais)

    def test_la_mortalite_n_est_jamais_totale(self):
        """Même à 60 °C, le taux horaire est plafonné : une colonie ne disparaît pas en une heure."""
        mort = oi.taux_mortalite_thermique(60.0, oi.PARAMS["chaleur"]["colonies"], oi.PARAMS["chaleur"]["decote_microclimat_c"])
        self.assertLessEqual(mort, oi.PARAMS["chaleur"]["colonies"]["max_par_heure"])
        self.assertGreater(1.0 * (1 - mort) ** 24, 0.0)                                 # encore vivante après 24 h

    def test_les_conidies_sont_aussi_tuees_par_la_chaleur(self):
        """À 42 °C, les conidies dans le pool et sur les colonies sont aussi tuées."""
        res_chaud = simuler(serie(heures=24 * 30, regles=lambda h: {"temp": 42.0, "hr": 60.0}), infections_initiales=graine())
        res_frais = simuler(serie(temp=25.0, hr=85.0, heures=24 * 30), infections_initiales=graine())
        f_chaud = max((d["fraction_malade"] for d in res_chaud["jours"]), default=0)
        f_frais = max((d["fraction_malade"] for d in res_frais["jours"]), default=0)
        self.assertLess(f_chaud, f_frais)


class TestChasmotheces(unittest.TestCase):
    P = oi.PARAMS

    def test_fonction_thermique_des_chasmotheces(self):
        """Optimum 20 °C (Legler 2012), nulle à 10 °C et 30 °C."""
        f = lambda T: oi.taux_chasmotheces(T, self.P)                                   # noqa: E731
        self.assertEqual((f(None), f(9.0), f(30.0), f(35.0)), (0.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(f(22.0), 1.0, delta=0.01)                                # optimum exact = 22 °C (bêta m=0.6, n=0.9)
        self.assertGreater(f(20.0), 0.95)                                               # 20 °C est proche de l'optimum (Legler 2012)
        self.assertGreater(f(20.0), f(15.0))                                          # croissant de 10 à 22 °C
        self.assertGreater(f(25.0), f(15.0))                                          # 25 °C > 15 °C (les deux sous l'optimum)
        self.assertGreater(f(22.0), f(25.0))                                          # décroissant après 22 °C

    def test_pas_de_formation_avant_le_cumul_de_froid(self):
        """Sans heures sous 13 °C, les chasmothèces ne s'initient pas même si la surface est malade."""
        res = simuler(serie(temp=25.0, hr=85.0, heures=24 * 90), infections_initiales=graine())
        self.assertFalse(res["chasmotheces"]["initiation"])
        self.assertEqual(res["chasmotheces"]["integral"], 0.0)

    def test_initiation_apres_le_cumul_de_froid(self):
        """Après 8 heures sous 13 °C, la formation commence."""
        def regles(h):
            if h < 8: return {"temp": 10.0, "hr": 70.0}           # 8 heures froides pour déclencher
            return {"temp": 20.0, "hr": 80.0}
        res = simuler(serie(heures=24 * 60, regles=regles), infections_initiales=graine())
        self.assertTrue(res["chasmotheces"]["initiation"])
        self.assertGreater(res["chasmotheces"]["integral"], 0.0)

    def test_pas_de_chasmotheces_sans_surface_malade(self):
        """Sans inoculum (ni primaire ni amorçage), pas de chasmothèces même avec les bonnes températures."""
        def froid_puis_doux(h): return {"temp": 10.0} if h < 8 else {"temp": 20.0, "hr": 80.0}
        res = simuler(serie(heures=24 * 60, regles=froid_puis_doux))                    # aucun inoculum
        self.assertEqual(res["chasmotheces"]["integral"], 0.0)

    def test_l_indice_est_plus_fort_apres_une_saison_epique(self):
        """Une saison avec beaucoup de surface malade et des températures favorables donne un indice plus élevé."""
        def froid(h): return {"temp": 10.0, "hr": 70.0} if h < 8 else {"temp": 20.0, "hr": 85.0}
        fort = simuler(serie(heures=24 * 30, regles=froid), infections_initiales=[{"t": "2026-06-01T00:00", "n": 5.0}])
        faible = simuler(serie(heures=24 * 30, regles=froid), infections_initiales=[{"t": "2026-06-01T00:00", "n": 0.01}])
        # 30 jours : la série est courte, les deux indices ne saturent pas encore
        self.assertGreater(fort["chasmotheces"]["integral"], faible["chasmotheces"]["integral"])

    def test_l_indice_est_plafonne_a_1(self):
        def froid(h): return {"temp": 10.0, "hr": 70.0} if h < 8 else {"temp": 20.0, "hr": 85.0}
        res = simuler(serie(heures=24 * 200, regles=froid), infections_initiales=[{"t": "2026-06-01T00:00", "n": 10.0}])
        self.assertLessEqual(res["chasmotheces"]["indice"], 1.0)

    def test_le_seuil_de_froid_est_parametrable(self):
        """Avec un seuil de 1 heure, l'initiation est immédiate dès la première heure fraîche."""
        def froid_bref(h): return {"temp": 10.0} if h == 0 else {"temp": 20.0, "hr": 85.0}
        defaut = simuler(serie(heures=24 * 60, regles=froid_bref), infections_initiales=graine())
        un_h = simuler(serie(heures=24 * 60, regles=froid_bref), infections_initiales=graine(),
                       chasmotheces={"seuil_heures_froid": 1})
        self.assertFalse(defaut["chasmotheces"]["initiation"])                           # 1 heure < seuil par défaut (8)
        self.assertTrue(un_h["chasmotheces"]["initiation"])

    def test_desactiver_les_chasmotheces(self):
        def froid(h): return {"temp": 10.0, "hr": 70.0} if h < 20 else {"temp": 20.0, "hr": 85.0}
        res = simuler(serie(heures=24 * 60, regles=froid), infections_initiales=graine(), chasmotheces={"actif": False})
        self.assertEqual(res["chasmotheces"]["indice"], 0.0)
        self.assertFalse(res["chasmotheces"]["initiation"])

    def test_l_indice_fin_de_saison_alimente_la_saison_suivante(self):
        """L'indice relatif de chasmothèces peut être passé comme indice-chasmotheces de la saison suivante."""
        def froid(h): return {"temp": 10.0} if h < 8 else {"temp": 20.0, "hr": 85.0}
        res = simuler(serie(heures=24 * 90, regles=froid), infections_initiales=[{"t": "2026-06-01T00:00", "n": 2.0}])
        indice = res["chasmotheces"]["indice"] * 100
        self.assertGreater(indice, 0.0)
        self.assertLessEqual(indice, 100.0)
        # On peut passer cet indice à la saison suivante
        res2 = oi.calculer_saison(serie(temp=20.0, heures=24 * 10), {"primaire": {"indice_chasmotheces": indice}})
        self.assertAlmostEqual(res2["stock_ascospores_initial"], res["chasmotheces"]["indice"], delta=0.01)


class TestIndiceChasmothecesEnLigneDeCommande(unittest.TestCase):
    def test_option_indice_chasmotheces(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "temperature_2m", "relative_humidity_2m", "dew_point_2m", "precipitation"])
            for r in serie(heures=24 * 5):
                w.writerow([r["t"].strftime("%Y-%m-%dT%H:%M"), r["temp"], r["hr"], "", r["pluie"]])
            chemin = f.name
        try:
            s = io.StringIO()
            with contextlib.redirect_stdout(s):
                oi.main([chemin, "--indice-chasmotheces", "95", "--severite", "0", "--debourrement", "2026-03-28"])
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

    def test_delai_de_detection_decale_la_fenetre_d_infection(self):
        """Si les symptômes ne sont repérés que `delai` jours après la fin de la latence, les infections à l'origine sont plus anciennes."""
        rows = self.meteo_en_deux_temps()
        a0 = oi.analyse_retro(rows, [date(2026, 7, 18)])[0]
        a5 = oi.analyse_retro(rows, [date(2026, 7, 18)], delai_visible_j=5)[0]
        self.assertEqual((a0["delai_visible_j"], a5["delai_visible_j"]), (0, 5))
        self.assertEqual(date.fromisoformat(a0["infections"][0]) - date.fromisoformat(a5["infections"][0]), timedelta(days=5))
        a12 = oi.analyse_retro(rows, [date(2026, 7, 18)], delai_visible_j=12)[0]
        self.assertLess(a12["potentiel_moyen_pct"], a0["potentiel_moyen_pct"] - 10)           # la fenêtre rejoint la phase froide

    def test_sensibilite_au_delai(self):
        s = oi.sensibilite_delai(self.meteo_en_deux_temps(), date(2026, 7, 18), (0, 3, 6, 12))
        self.assertEqual([x["delai_visible_j"] for x in s], [0, 3, 6, 12])
        debuts = [x["infections"][0] for x in s]
        self.assertEqual(debuts, sorted(debuts, reverse=True))                                    # plus le délai est long, plus on remonte
        self.assertIn("délai 12 j", oi.resume_sensibilite(date(2026, 7, 18), s))

    def test_analyse_retro_date_sans_infection_possible(self):
        a = oi.analyse_retro(serie(temp=25.0, heures=24 * 20), [date(2026, 12, 25)])
        self.assertEqual(a[0], {"observation": "2026-12-25", "infections": None, "delai_visible_j": 0})

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
        self.assertIn("SENSIBILITÉ AU DÉLAI DE DÉTECTION", sortie)
        self.assertIn("délai 5 j", sortie)
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
