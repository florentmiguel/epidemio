#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moteur oïdium (Erysiphe necator) — v0 : cycle par cohortes, pas de temps horaire
================================================================================

Contrairement au mildiou, l'oïdium se développe dans des conditions météo assez larges et ne se rattache pas à des événements
ponctuels (Dubuis et al. 2014). Le moteur simule donc la DYNAMIQUE de l'épidémie, cohorte par cohorte, plutôt que des
infections datées :

    infection → LATENCE → SPORULATION (symptômes visibles) → conidies → nouvelles infections → ...

Formalismes : Garin (2011, mémoire ITK, HAL hal-01877240) d'après Calonnec et al. (2008), Chellemi et Marois (1991) et
Caffi et al. (2011). Ce sont des formalismes de la littérature, NON calés sur la Champagne ; deux valeurs imprimées dans le
mémoire sont manifestement fautives (décimale perdue, chiffres inversés) et ont été rectifiées d'après ses figures :

  * fonction thermique F(T), type bêta, entre 5 et 31 °C (optimum ≈ 26 °C) : croissance, latence, sporulation, infection ;
  * latence : 6 jours / F(T) (≈ 6 j à 25 °C, 11 j à 15 °C, bloquée au-delà de 31 °C) ;
  * fin de sporulation : taux/jour = a·exp(b·T), a = 0,0227 et b = 0,0762 (imprimé 0,762) ;
  * infection par les conidies : I0·F(T)·exp(−τ·âge de la feuille)·min(1, a·HR − b), a = 0,0213 (imprimé 0,0023), b = 0,8068 ;
    réduite par l'eau libre (figure I du mémoire).

Facteurs externes (v1), tous facultatifs et neutres quand la donnée manque :
  * VENT (Willocquet et al. 1998, d'après Garin 2011, Eq. 18) : les conidies s'accumulent sur les colonies et ne sont libérées que par
    le vent ; le rapport au taux de libération de référence conserve le calage de l'émission ;
  * ULTRAVIOLETS (Austin et Wilcox 2010) : le rayonnement global sert de proxy ; il tue une part des conidies (sur les colonies et dans
    l'air) et réduit l'infection, sur la fraction exposée du feuillage seulement ;
  * STADE PHÉNOLOGIQUE et RÉSISTANCE ONTOGÉNIQUE (phenologie.py) : surface foliaire disponible, sensibilité des feuilles, et indice des
    GRAPPES (favorabilité × sensibilité ontogénique). Le stade vient, au choix, d'une table de degrés-jours depuis le débourrement (défaut) ou de
    l'enchaînement BRIN -> croissance des feuilles -> GFV (--phenologie brin_gfv, phenologie_brin_gfv.py) ; dans les deux cas il est
    recalable sur des observations de stade.
Les paramètres des trois facteurs sont des HYPOTHÈSES DE TRAVAIL, non calées sur la Champagne.

Ce que le moteur ne fait PAS : conservation hivernale détaillée (cléistothèces), effet du gel sur la surface foliaire (paramètre
`phenologie.surface_foliaire_max`), traitements phytosanitaires (le moteur évalue le danger indépendamment des traitements, comme
celui du mildiou).

Unités : les colonies sont en unités RELATIVES (une capacité d'accueil de la vigne fixe l'échelle). Seuls les rythmes et les dates
ont un sens avant calage sur le terrain.

Usage :
    python3 oidium.py meteo.csv --debourrement 2026-04-15 [--severite 2] [--graine 2026-05-20]
"""
from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import mildiou_primaire as mp
import phenologie as phen
import phenologie_brin_gfv as pbg

UTC = timezone.utc

PARAMS = {
    "fuseau_local": "Europe/Paris",
    # Fonction thermique F(T) : bêta normalisée entre tmin et tmax (Calonnec et al. 2008 ; Garin 2011, annexe III)
    "temperature": {"tmin": 5.0, "tmax": 31.0, "m": 0.27, "n": 1.24},
    "latence": {"jours_optimum": 6.0},
    # Fin de sporulation : fraction d'achèvement par jour = a·exp(b·T)
    "sporulation": {"a": 0.0227, "b": 0.0762},
    "infection": {
        "i0": 0.53,                      # taux maximal d'infection par les conidies
        "tau": 0.147,                    # perte de sensibilité de la feuille avec l'âge (par jour)
        "age_feuille_j": 3.0,            # âge moyen des feuilles réceptives (v0 : constant)
        "hr_a": 0.0213, "hr_b": 0.8068,  # facteur d'humidité : min(1, a·HR − b) ; nul sous ≈ 38 %, maximal dès 85 %
        "eau_libre": 0.8,                # facteur appliqué quand la feuille est mouillée (figure I : 0,27 / 0,34)
    },
    "humectation": {"seuil_capteur": 0.5, "pluie_mm": 0.2, "hr_pct": 90.0, "ecart_rosee_c": 1.0},
    # Production et dispersion des conidies (relatif : à caler)
    "conidies": {
        "emission_par_colonie_jour": 30.0,   # conidies « équivalent-infection » par colonie sporulante et par jour à F(T) = 1
        "depot_horaire": 0.05,               # part du stock de conidies qui se dépose chaque heure sur le feuillage
        "perte_horaire": 0.03,               # viabilité perdue / emportées chaque heure
    },
    "primaire": {
        "pluie_mm": 2.5, "pluie_heures": 6,  # déclencheur de décharge des ascospores (Gadoury et Pearson 1990 : 2,5 mm, 10 °C)
        "temperature_min": 10.0,
        "debourrement": None,                # date AAAA-MM-JJ ; défaut : 15 avril de l'année de la série
        "fenetre_jours": 60,                 # durée pendant laquelle les ascospores peuvent encore infecter
        "fraction_par_evenement": 0.25,      # part du stock d'ascospores déchargée à chaque événement
        "colonies_par_evenement": 0.02,      # colonies créées par un événement, stock plein, F(T) = 1 (relatif)
        # sévérité de l'oïdium l'année précédente (0 à 3, catégories du mémoire) -> stock d'ascospores relatif (80/320/680 par cm²)
        "stock_selon_severite": {0: 0.0, 1: 0.12, 2: 0.47, 3: 1.0},
        "severite_precedente": 2,
        # Indice chasmothèces (0 à 100) : inoculum primaire de la saison, issu de l'indice de formation des chasmothèces calculé par ce
        # moteur en fin de saison précédente (chasmotheces.indice x 100). S'il est donné, il remplace la
        # sévérité de l'année précédente. Hypothèse v0, à caler : stock d'ascospores relatif = indice / 100.
        "indice_chasmotheces": None,
    },
    # Vent : libération des conidies (Eq. 18 de Garin 2011, d'après Willocquet et al. 1998). Actif dès qu'une heure porte une valeur de vent.
    "vent": {
        "actif": None,                          # None : automatique ; False : désactivé (compare les scénarios)
        "facteur_canopee": 0.5,                 # vent dans le feuillage / vent à 10 m (hypothèse)
        "willocquet": {"r": 0.41, "a": 0.71, "b": -5.8},
        "vitesse_reference_ms": 2.0,            # vitesse dans le feuillage pour laquelle la libération horaire vaut `liberation_horaire_ref`
        "liberation_horaire_ref": 0.02,         # part du stock de conidies libérée chaque heure à la vitesse de référence (lente devant la
                                                # perte de viabilité : sinon le vent ne ferait que décaler les conidies de quelques heures)
        "facteur_max": 20.0,                    # plafond du rapport (taux à la vitesse observée / taux de référence)
        "perte_horaire_sur_colonies": 0.01,     # viabilité perdue chaque heure par les conidies qui attendent le vent
    },
    # Ultraviolets (Austin et Wilcox 2010) : le rayonnement global sert de proxy.
    "uv": {
        "actif": None,
        "rayonnement_reference_wm2": 800.0,     # plein soleil
        "part_exposee": 0.5,                    # fraction du feuillage directement exposée (le reste est à l'ombre)
        "mortalite_horaire": 0.12,              # part des conidies tuées chaque heure à pleine exposition (sur la fraction exposée)
        "reduction_infection": 0.6,             # réduction de l'infection et de la favorabilité à pleine exposition (sur la fraction exposée)
    },
    # Mortalité thermique (Peduto et al. 2013 ; Delp 1954). Effets létaux à partir de 36-38 °C, temps-dépendants.
    # Le microclimat intérieur est 3-5 °C plus frais que l'air : on applique un décote de 3 °C sur la temperature mesurée.
    # Conidies (moins tolérantes) : courbe plus agressive que pour les colonies.
    # La mortalité n'est jamais totale : on plafonne à max_mortalite par heure.
    "chaleur": {
        "actif": True,
        "decote_microclimat_c": 3.0,           # température ressentie par le champignon = T_air - décote
        # Courbe de mortalite HORAIRE des colonies existantes : max(0, a × (T_eff − seuil)^b) si T_eff > seuil
        "colonies": {"seuil_c": 36.0, "a": 0.004, "b": 1.8, "max_par_heure": 0.15},
        # Conidies (pool et stock sur colonies) : plus sensibles, seuil légèrement plus bas
        "conidies": {"seuil_c": 34.0, "a": 0.006, "b": 1.8, "max_par_heure": 0.25},
    },
    # Chasmothèces : formation proportionnelle à la surface malade × favorabilité thermique (Legler 2012 : optimum 20 °C)
    # L'indice relatif de fin de saison devient le stock d'ascospores de l'année suivante (0 à 1).
    "chasmotheces": {
        "actif": True,
        "seuil_heures_froid": 8,               # cumul d'heures sous 13 °C déclenchant l'initiation (Gadoury et Pearson 1987)
        "temperature_seuil_froid_c": 13.0,
        "t_opt": 20.0, "t_min": 10.0, "t_max": 30.0,   # courbe bêta de Legler 2012
        "m_beta": 0.6, "n_beta": 0.9,
        "surface_poids": 1.0,                  # poids de la surface malade dans l'intégrale (à caler)
    },
    # Phénologie et résistance ontogénique : voir phenologie.py
    "phenologie": {
        "actif": None,                          # None : automatique (actif si la série couvre le débourrement) ; False : désactivé
        "base_djc": 10.0,
        "table_djc": None,                      # [[BBCH, DJC], ...] pour remplacer la table par défaut
        "observations": {},                     # {"2026-05-15": 17, ...} stades observés : recalent la table
        "modele": "djc",                        # "djc" : table de degrés-jours (défaut) ; "brin_gfv" : BRIN -> feuilles -> GFV (phenologie_brin_gfv.py)
        "brin_gfv": None,                       # surcharges des paramètres de phenologie_brin_gfv.PARAMS (Chardonnay par défaut)
        "surface_foliaire_max": 1.0,            # < 1 après un gel : la surface foliaire maximale est réduite
        "sensibilite_feuilles": True,
    },
    "capacite_colonies": 1000.0,             # colonies maximales avant saturation du feuillage (échelle relative)
    "seuil_visible": 0.001,                  # fraction de la capacité sporulante à partir de laquelle des symptômes sont repérables
    "infections_initiales": [],              # [{"t": "2026-05-20T00:00", "n": 1.0}] : amorçage manuel (tests, scénarios)
}


# ---------------------------------------------------------------------------
# Fonctions biologiques
# ---------------------------------------------------------------------------
def f_temp(t: float | None, p: dict) -> float:
    """Taux relatif (0 à 1) lié à la température, bêta normalisée ; nul hors de ]tmin ; tmax[."""
    if t is None:
        return 0.0
    c = p["temperature"]
    if t <= c["tmin"] or t >= c["tmax"]:
        return 0.0
    x = (t - c["tmin"]) / (c["tmax"] - c["tmin"])
    m, n = c["m"], c["n"]
    return ((m + n) ** (m + n)) / (n ** n * m ** m) * x ** n * (1 - x) ** m


def duree_latence_j(t: float, p: dict) -> float:
    """Durée de latence (jours) à température constante ; infinie si le développement est bloqué."""
    f = f_temp(t, p)
    return p["latence"]["jours_optimum"] / f if f > 1e-9 else math.inf


def taux_fin_sporulation(t: float, p: dict) -> float:
    """Fraction de la période de sporulation achevée par jour à la température t (3 à 20 jours selon la température)."""
    s = p["sporulation"]
    return s["a"] * math.exp(s["b"] * t)


def facteur_humidite(hr: float | None, p: dict) -> float:
    if hr is None:
        return 0.5
    i = p["infection"]
    return max(0.0, min(1.0, i["hr_a"] * hr - i["hr_b"]))


def taux_mortalite_thermique(t_air: float | None, config: dict, decote: float) -> float:
    """Taux de mortalite horaire (0 à max_par_heure) pour colonies ou conidies selon leur config, après décote microclimat."""
    if t_air is None:
        return 0.0
    t_eff = t_air - decote
    if t_eff <= config["seuil_c"]:
        return 0.0
    return min(config["max_par_heure"], config["a"] * (t_eff - config["seuil_c"]) ** config["b"])


def taux_chasmotheces(t: float | None, p: dict) -> float:
    """Favorabilité thermique de formation des chasmothèces : bêta entre t_min et t_max (Legler 2012, optimum 20 °C)."""
    if t is None:
        return 0.0
    pc = p["chasmotheces"]
    tmin, tmax, m, n = pc["t_min"], pc["t_max"], pc["m_beta"], pc["n_beta"]
    if t <= tmin or t >= tmax:
        return 0.0
    x = (t - tmin) / (tmax - tmin)
    return ((m + n) ** (m + n)) / (n ** n * m ** m) * x ** n * (1 - x) ** m


def taux_dispersion(vitesse: float, p: dict) -> float:
    """Part des conidies décrochées d'une colonie en fonction du vent (m/s) : Eq. 18 de Garin 2011 (Willocquet et al. 1998)."""
    w = p["vent"]["willocquet"]
    e = math.exp(w["r"] * vitesse + w["b"])
    return min(1.0, e / (w["a"] * (1.0 + e)))


def liberation_horaire(vent_10m: float | None, p: dict) -> float:
    """Part du stock de conidies libérée en une heure. À la vitesse de référence (dans le feuillage), elle vaut `liberation_horaire_ref`,
    ce qui garde à l'émission sa signification ; plus de vent la multiplie (jusqu'à `facteur_max`). Vent inconnu : valeur de référence."""
    pv = p["vent"]
    ref = pv["liberation_horaire_ref"]
    if vent_10m is None:
        return ref
    u = max(0.0, vent_10m) * pv["facteur_canopee"]
    rapport = min(pv["facteur_max"], taux_dispersion(u, p) / taux_dispersion(pv["vitesse_reference_ms"], p))
    return min(1.0, ref * rapport)


def exposition_uv(rayonnement: float | None, p: dict) -> float:
    """Exposition aux UV de la fraction du feuillage éclairée : 0 la nuit ou sans donnée, `part_exposee` en plein soleil."""
    if rayonnement is None:
        return 0.0
    pu = p["uv"]
    return min(1.0, max(0.0, rayonnement) / pu["rayonnement_reference_wm2"]) * pu["part_exposee"]


def potentiel_horaire(t: float, hr: float | None, mouille: bool, p: dict, uv: float = 0.0) -> float:
    """Favorabilité de l'heure à l'épidémie, 0 à 1 : température × humidité de l'air, réduite par l'eau libre et par les UV."""
    return (f_temp(t, p) * facteur_humidite(hr, p) * (p["infection"]["eau_libre"] if mouille else 1.0)
            * (1.0 - p["uv"]["reduction_infection"] * uv))


def probabilite_infection(t: float, hr: float | None, mouille: bool, p: dict, uv: float = 0.0) -> float:
    i = p["infection"]
    return i["i0"] * math.exp(-i["tau"] * i["age_feuille_j"]) * potentiel_horaire(t, hr, mouille, p, uv)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
class Cohorte:
    __slots__ = ("id", "source", "gen", "t_inf", "n", "lat", "spo", "etat", "t_symp", "t_fin")

    def __init__(self, id_, source, gen, t_inf, n):
        self.id, self.source, self.gen, self.t_inf, self.n = id_, source, gen, t_inf, n
        self.lat, self.spo, self.etat, self.t_symp, self.t_fin = 0.0, 0.0, "latente", None, None


def _instant(s: str) -> datetime:
    t = datetime.fromisoformat(s.strip().replace("Z", ""))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def _iso(t: datetime | None):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ") if t else None


def calculer_saison(rows: list[dict], params: dict | None = None, now: datetime | None = None) -> dict:
    """
    rows   : série horaire [{t (UTC), temp, hr, pluie, rosee?, mouille?}, ...] (mêmes lignes que le moteur du mildiou)
    params : surcharges de PARAMS
    now    : instant de référence : les jours postérieurs sont marqués « previsionnel »
    """
    p = mp.fusionner(PARAMS, params)
    tz = ZoneInfo(p["fuseau_local"])
    now = now or datetime.now(UTC)
    rows, avert = mp.preparer(rows)
    if not rows:
        raise ValueError("série météo vide")

    pp, pc, pi_ = p["primaire"], p["conidies"], p["infection"]
    # Année de la saison : celle du débourrement fourni s'il l'est, sinon la dernière ligne de la série.
    # Utiliser rows[-1] quand la série couvre deux années (ex. archive 2025 + 2026) donnerait l'année suivante,
    # ce qui décalerait la somme GFV et rendrait les stades observés de la saison voulue introuvables.
    annee_serie = rows[-1]["t"].astimezone(tz).year
    annee_debourrement = int(pp["debourrement"][:4]) if pp.get("debourrement") else None
    annee = annee_debourrement or annee_serie
    pf = p["phenologie"]
    res_bg = None
    if pf["modele"] == "brin_gfv" and pf["actif"] is not False:
        res_bg = pbg.serie_bbch_brin_gfv(rows, tz, annee, pf["brin_gfv"],
                                         debourrement=date.fromisoformat(pp["debourrement"]) if pp["debourrement"] else None,
                                         observations=pf["observations"] or None)
        avert += res_bg["avertissements"]
    if pp["debourrement"]:
        debourrement = date.fromisoformat(pp["debourrement"])
    elif res_bg and res_bg["debourrement"]:
        debourrement = res_bg["debourrement"]
    else:
        debourrement = date(annee, 4, 15)
    fin_primaire = debourrement + timedelta(days=pp["fenetre_jours"])
    stock_asc = pp["stock_selon_severite"].get(pp["severite_precedente"], pp["stock_selon_severite"].get(str(pp["severite_precedente"]), 0.0))
    if pp.get("indice_chasmotheces") is not None:
        stock_asc = max(0.0, min(100.0, float(pp["indice_chasmotheces"]))) / 100.0
    stock_asc0 = stock_asc
    capacite = p["capacite_colonies"]
    seuil_vis = p["seuil_visible"] * capacite
    ph = {"humectation": p["humectation"]}
    pv, pu = p["vent"], p["uv"]

    # --- données externes : disponibilité et activation (neutres quand la donnée manque) ---
    vent_pct = 100.0 * sum(1 for r in rows if r.get("vent") is not None) / len(rows)
    ray_pct = 100.0 * sum(1 for r in rows if r.get("rayonnement") is not None) / len(rows)
    vent_actif = pv["actif"] is not False and vent_pct > 0
    uv_actif = pu["actif"] is not False and ray_pct > 0
    if vent_actif and vent_pct < 95:
        avert.append(f"vent renseigné pour {vent_pct:.0f} % des heures : les autres reçoivent la libération de référence")
    if uv_actif and ray_pct < 95:
        avert.append(f"rayonnement renseigné pour {ray_pct:.0f} % des heures : les autres sont sans effet des UV")
    bbch_jour, modele_actif = {}, None
    if res_bg and res_bg["actif"]:
        bbch_jour, modele_actif = res_bg["bbch"], "brin_gfv"
    elif pf["actif"] is not False:
        if res_bg is not None:
            avert.append("BRIN + GFV indisponible : phénologie par degrés-jours à la place")
        table = (tuple((float(b) if float(b) != int(b) else int(b), float(d)) for b, d in pf["table_djc"])
                 if pf["table_djc"] else phen.TABLE_DJC)
        bbch_jour = phen.serie_bbch(rows, debourrement, tz, pf["observations"] or None, table, pf["base_djc"])
        modele_actif = "djc"
        if not bbch_jour and pf["actif"] is True:
            avert.append("phénologie indisponible : la série ne couvre pas le débourrement")
    phen_actif = bool(bbch_jour)

    # pluie sur les k dernières heures
    k = max(1, int(pp["pluie_heures"]))
    pluie6, fen = [], []
    for r in rows:
        fen.append(r["pluie"] or 0.0)
        if len(fen) > k:
            fen.pop(0)
        pluie6.append(sum(fen))

    seeds = sorted(({"t": _instant(s["t"]), "n": float(s["n"])} for s in p["infections_initiales"]), key=lambda s: s["t"])
    seed_i = 0

    actives, toutes = [], []
    pool, stock_col, d_total, prochain_id = 0.0, 0.0, 0.0, 1
    integral_chasmotheces, heures_froid, initiation_chasmotheces = 0.0, 0, False
    primaires, jours, jalons = [], {}, {}
    dernier_primaire = None

    def jour_local(t):
        return t.astimezone(tz).date()

    def creer(source, gen, t, n):
        nonlocal prochain_id, d_total
        c = Cohorte(prochain_id, source, gen, t, n)
        prochain_id += 1
        actives.append(c)
        toutes.append(c)
        d_total += n
        return c

    for i, r in enumerate(rows):
        t, T, hr = r["t"], r["temp"], r.get("hr")
        j = jour_local(t)
        mouille = mp.est_mouille(r, ph)
        f = f_temp(T, p)
        d = jours.setdefault(j, {"temps": [], "hr": [], "pluie": 0.0, "potentiel": [], "lat_vitesse": 0.0, "nouvelles": 0.0,
                                 "vent": [], "ray": [], "liberation": [], "grappes": [], "bbch": None})
        # phénologie du jour : stade, surface foliaire disponible, sensibilité des feuilles et des grappes
        bb = bbch_jour.get(j) if phen_actif else None
        if phen_actif:
            cap_t = max(1e-9, capacite * phen.surface_foliaire(bb) * pf["surface_foliaire_max"])
            sf = phen.sens_feuilles(bb) if pf["sensibilite_feuilles"] else 1.0
            sb = phen.sens_grappes(bb)
            d["bbch"] = bb
        else:
            cap_t, sf, sb = capacite, 1.0, None
        expo = exposition_uv(r.get("rayonnement"), p) if uv_actif else 0.0
        uv_mort = pu["mortalite_horaire"] * expo
        # mortalité thermique (colonies et conidies séparément)
        pch = p["chaleur"]
        if pch["actif"] and T is not None:
            decote = pch["decote_microclimat_c"]
            mort_col = taux_mortalite_thermique(T, pch["colonies"], decote)
            mort_conidie = taux_mortalite_thermique(T, pch["conidies"], decote)
        else:
            mort_col = mort_conidie = 0.0
        d["temps"].append(T)
        if hr is not None:
            d["hr"].append(hr)
        if r.get("vent") is not None:
            d["vent"].append(r["vent"])
        if r.get("rayonnement") is not None:
            d["ray"].append(r["rayonnement"])
        d["pluie"] += r["pluie"] or 0.0
        pot_h = potentiel_horaire(T, hr, mouille, p, expo)
        d["potentiel"].append(pot_h)
        if sb is not None:
            d["grappes"].append(pot_h * sb)
        d["lat_vitesse"] += f / (p["latence"]["jours_optimum"] * 24.0)

        # --- amorçages manuels ---
        while seed_i < len(seeds) and seeds[seed_i]["t"] <= t:
            creer("initiale", 0, seeds[seed_i]["t"], seeds[seed_i]["n"])
            seed_i += 1

        # --- infection primaire : décharge d'ascospores déclenchée par la pluie, après le débourrement ---
        if (debourrement <= j <= fin_primaire and stock_asc > 1e-9 and pluie6[i] >= pp["pluie_mm"]
                and T is not None and T >= pp["temperature_min"] and 0.0 < f and dernier_primaire != j):
            q = stock_asc * pp["fraction_par_evenement"]
            stock_asc -= q
            n = pp["colonies_par_evenement"] * q * f * sf     # q est en unités de stock (1,0 = stock plein) : la sévérité l'échelonne
            if n > 1e-12:
                c = creer("primaire", 0, t, n)
                primaires.append({"t": _iso(t), "colonies": round(n, 6), "pluie_mm": round(pluie6[i], 1), "temperature": T})
                dernier_primaire = j

        # --- développement des cohortes : latence puis sporulation ---
        emission = 0.0
        dom, dom_n = None, 0.0
        for c in actives:
            if c.etat == "latente":
                c.lat += f / (p["latence"]["jours_optimum"] * 24.0)
                if c.lat >= 1.0:
                    c.etat, c.t_symp = "sporulante", t
            elif c.etat == "sporulante":
                c.spo += taux_fin_sporulation(T, p) / 24.0
                if c.spo >= 1.0:
                    c.etat, c.t_fin = "terminee", t
            if c.etat == "sporulante":
                # la chaleur tue une fraction de la biomasse de la colonie (mortalité partielle, jamais totale grâce au plafond)
                if mort_col > 0:
                    c.n *= (1.0 - mort_col)
                emission += c.n * f * pc["emission_par_colonie_jour"] / 24.0
                if c.n > dom_n:
                    dom, dom_n = c, c.n
        if any(c.etat == "terminee" for c in actives):
            actives = [c for c in actives if c.etat != "terminee"]

        # --- conidies : émission, dépôt, infection de tissu sain ---
        if vent_actif:                                   # les conidies attendent le vent sur les colonies
            stock_col += emission
            lib_h = liberation_horaire(r.get("vent"), p)
            liberees = stock_col * lib_h
            stock_col = (stock_col - liberees) * (1.0 - min(1.0, pv["perte_horaire_sur_colonies"] + max(uv_mort, mort_conidie)))
            pool += liberees
            d["liberation"].append(lib_h)
        else:
            pool += emission
        deposees = pool * pc["depot_horaire"]
        pool -= deposees + pool * (pc["perte_horaire"] + uv_mort)
        sain = max(0.0, 1.0 - d_total / cap_t)
        nouvelles = deposees * probabilite_infection(T, hr, mouille, p, expo) * sf * sain if T is not None else 0.0
        if nouvelles > 1e-12:
            creer("secondaire", (dom.gen + 1) if dom else 1, t, nouvelles)
            d["nouvelles"] += nouvelles

        # --- chasmothèces : cumul des heures froides pour déclencher l'initiation, puis intégrale de formation ---
        pcs = p["chasmotheces"]
        fraction_courante = min(1.0, d_total / cap_t)
        if pcs["actif"] and T is not None:
            if not initiation_chasmotheces:
                if T < pcs["temperature_seuil_froid_c"]:
                    heures_froid += 1
                if heures_froid >= pcs["seuil_heures_froid"]:
                    initiation_chasmotheces = True
            if initiation_chasmotheces and fraction_courante > 0:
                integral_chasmotheces += taux_chasmotheces(T, p) * fraction_courante * pcs["surface_poids"] / 24.0
        d["sporulantes"] = sum(c.n for c in actives if c.etat == "sporulante")
        d["fraction"] = min(1.0, d_total / cap_t)
        for seuil, cle in ((0.01, "fraction_1_pct"), (0.10, "fraction_10_pct"), (0.50, "fraction_50_pct")):
            if cle not in jalons and d["fraction"] >= seuil:
                jalons[cle] = j.isoformat()
        if "premiers_symptomes_visibles" not in jalons and d["sporulantes"] >= seuil_vis:
            jalons["premiers_symptomes_visibles"] = j.isoformat()

    # --- sorties ---
    ref = now.astimezone(tz).date()
    sortie_jours = []
    for j in sorted(jours):
        d = jours[j]
        lat_eq = (1.0 / d["lat_vitesse"]) if d["lat_vitesse"] > 1e-9 else None
        sortie_jours.append({
            "date": j.isoformat(), "tmoy": round(sum(d["temps"]) / len(d["temps"]), 1),
            "hr_moy": round(sum(d["hr"]) / len(d["hr"]), 0) if d["hr"] else None, "pluie_mm": round(d["pluie"], 1),
            "potentiel_pct": round(100.0 * sum(d["potentiel"]) / len(d["potentiel"]), 1),
            "latence_equivalente_j": round(min(lat_eq, 99.0), 1) if lat_eq else None,
            "nouvelles_colonies": round(d["nouvelles"], 6), "colonies_sporulantes": round(d["sporulantes"], 4),
            "fraction_malade": round(d["fraction"], 6), "previsionnel": j > ref,
            "vent_moy_ms": round(sum(d["vent"]) / len(d["vent"]), 1) if d["vent"] else None,
            "rayonnement_moy_wm2": round(sum(d["ray"]) / len(d["ray"]), 0) if d["ray"] else None,
            "liberation_pct": round(100.0 * sum(d["liberation"]) / len(d["liberation"]), 1) if d["liberation"] else None,
            "bbch": round(d["bbch"], 1) if d["bbch"] is not None else None,
            "sens_feuilles": round(phen.sens_feuilles(d["bbch"]), 2) if d["bbch"] is not None else None,
            "sens_grappes": round(phen.sens_grappes(d["bbch"]), 2) if d["bbch"] is not None else None,
            "indice_grappes_pct": round(100.0 * sum(d["grappes"]) / len(d["grappes"]), 1) if d["grappes"] else None})

    gens = {}
    for c in toutes:
        if c.t_symp:
            gens.setdefault(c.gen, []).append(c)
    generations = []
    for g in sorted(gens):
        cs = sorted(gens[g], key=lambda c: c.t_symp)
        total = sum(c.n for c in cs)
        cumul, mediane = 0.0, cs[-1].t_symp
        for c in cs:
            cumul += c.n
            if cumul >= total / 2.0:
                mediane = c.t_symp
                break
        generations.append({"generation": g, "premiers_symptomes": _iso(cs[0].t_symp)[:10], "mediane": _iso(mediane)[:10],
                            "cohortes": len(cs), "colonies": round(total, 4)})

    # moyenne glissante de 7 jours du potentiel (présentation à la manière de VitiMeteo-Oidium)
    pots = [d["potentiel_pct"] for d in sortie_jours]
    grap = [d["indice_grappes_pct"] for d in sortie_jours]
    for i_, d in enumerate(sortie_jours):
        fen7 = pots[max(0, i_ - 6): i_ + 1]
        d["indice_7j"] = round(sum(fen7) / len(fen7), 1)
        fen7g = [x for x in grap[max(0, i_ - 6): i_ + 1] if x is not None]
        d["indice_grappes_7j"] = round(sum(fen7g) / len(fen7g), 1) if fen7g else None

    # Indice relatif de fin de saison -> stock d'ascospores de l'année suivante (normalisé à 1 si la saison est forte)
    chasmotheces_indice = min(1.0, integral_chasmotheces)
    return {"parametres": p, "avertissements": avert, "debourrement": debourrement.isoformat(),
            "chasmotheces": {"indice": round(chasmotheces_indice, 4), "initiation": initiation_chasmotheces,
                             "heures_froid_cumul": heures_froid, "integral": round(integral_chasmotheces, 6)},
            "stock_ascospores_initial": stock_asc0, "primaires": primaires, "jalons": jalons, "generations": generations,
            "jours": sortie_jours,
            "donnees": {"vent_pct": round(vent_pct), "rayonnement_pct": round(ray_pct), "vent_actif": vent_actif, "uv_actif": uv_actif},
            "phenologie": {"actif": phen_actif, "modele": modele_actif if phen_actif else None,
                           "bbch_jour": {j.isoformat(): round(v, 3) for j, v in bbch_jour.items()} if phen_actif else {},
                           "calendrier": phen.calendrier(bbch_jour) if phen_actif else {},
                           "fenetre_grappes": list(phen.fenetre(bbch_jour, phen.sens_grappes, 0.5)) if phen_actif else [None, None],
                           "brin_gfv": ({k: (v.isoformat() if isinstance(v, date) else v) for k, v in res_bg.items()
                                         if k in ("debourrement", "debourrement_brin", "ecart_brin_j", "dormance", "neuf_feuilles", "phyllochron",
                                                  "t_base_feuilles", "rmse_feuilles")}
                                        | {"seuils_gfv": {k: round(x, 1) for k, x in res_bg["seuils_gfv"].items()}})
                           if (phen_actif and modele_actif == "brin_gfv") else None},
            "cohortes": [{"id": c.id, "source": c.source, "generation": c.gen, "infection": _iso(c.t_inf), "colonies": round(c.n, 8),
                          "symptomes": _iso(c.t_symp), "fin_sporulation": _iso(c.t_fin)} for c in toutes]}


# ---------------------------------------------------------------------------
# Diagnostics qui n'exigent ni inoculum ni calage : horloge de la latence, balayage des amorçages
# ---------------------------------------------------------------------------
def retro_symptomes(rows: list[dict], params: dict | None = None) -> dict:
    """Pour chaque jour local, date de sortie des symptômes d'une infection survenue ce jour-là : la latence avance chaque heure de
    F(T)/(6 j × 24 h) jusqu'à 1. Ne dépend que de la température (ni inoculum, ni traitements) ; None si la série finit avant."""
    from bisect import bisect_left
    p = mp.fusionner(PARAMS, params)
    tz = ZoneInfo(p["fuseau_local"])
    rows, _ = mp.preparer(rows)
    L = p["latence"]["jours_optimum"] * 24.0
    S = [0.0]
    for r in rows:
        S.append(S[-1] + f_temp(r["temp"], p) / L)
    par_jour = {}
    for i, r in enumerate(rows):
        j = bisect_left(S, S[i] + 1.0, lo=i + 1)
        sortie = rows[j - 1]["t"].astimezone(tz).date() if j <= len(rows) else None
        par_jour.setdefault(r["t"].astimezone(tz).date(), []).append(sortie)
    out = {}
    for jour, sorties in par_jour.items():
        valides = sorted(s for s in sorties if s)
        out[jour] = valides[len(valides) // 2] if len(valides) == len(sorties) and valides else None   # médiane des 24 heures
    return out


def fenetre_infection(retro: dict, observation: date, tolerance_j: int = 3) -> list[date]:
    """Jours d'infection dont les symptômes sortiraient à ± tolerance_j de la date observée."""
    return sorted(j for j, s in retro.items() if s and abs((s - observation).days) <= tolerance_j)


def analyse_retro(rows: list[dict], observations: list[date], params: dict | None = None, tolerance_j: int = 3,
                  delai_visible_j: int = 0) -> list[dict]:
    """Pour chaque date d'observation de symptômes : fenêtre d'infection correspondante, latence, favorabilité de ces jours et rang dans la saison.
    delai_visible_j : jours qui séparent la fin de la latence (début de sporulation) du moment où les symptômes sont repérés au vignoble ;
    la date de fin de latence recherchée est l'observation moins ce délai."""
    p = mp.fusionner(PARAMS, params)
    res = calculer_saison(rows, {**(params or {}), "primaire": {**(params or {}).get("primaire", {}), "severite_precedente": 0}})
    retro = retro_symptomes(rows, params)
    pot = {date.fromisoformat(d["date"]): d["potentiel_pct"] for d in res["jours"]}
    # Période de référence du rang : du 1er mai (ou du débourrement s'il est plus tardif) au 30 septembre. Inclure un mois d'avril froid
    # flatterait n'importe quel jour un peu favorable.
    debourrement = date.fromisoformat(res["debourrement"])
    ref_debut, ref_fin = max(debourrement, date(debourrement.year, 5, 1)), date(debourrement.year, 9, 30)
    saison = sorted(v for j, v in pot.items() if ref_debut <= j <= ref_fin)
    sortie = []
    for obs in observations:
        jours = fenetre_infection(retro, obs - timedelta(days=delai_visible_j), tolerance_j)
        if not jours:
            sortie.append({"observation": obs.isoformat(), "infections": None, "delai_visible_j": delai_visible_j})
            continue
        moyenne = sum(pot[j] for j in jours if j in pot) / len([j for j in jours if j in pot])
        rang = 100.0 * sum(1 for v in saison if v < moyenne) / len(saison) if saison else None
        lats = [(retro[j] - j).days for j in jours]
        sortie.append({"observation": obs.isoformat(), "infections": [jours[0].isoformat(), jours[-1].isoformat()],
                       "latence_j": [min(lats), max(lats)], "potentiel_moyen_pct": round(moyenne, 1),
                       "rang_saison_pct": round(rang, 0) if rang is not None else None,
                       "periode_reference": [ref_debut.isoformat(), ref_fin.isoformat()], "delai_visible_j": delai_visible_j,
                       "meilleur_jour": max(jours, key=lambda j: pot.get(j, -1)).isoformat()})
    return sortie


def sensibilite_delai(rows: list[dict], observation: date, delais=(0, 3, 5, 7), params: dict | None = None, tolerance_j: int = 3) -> list[dict]:
    """Une même observation lue avec plusieurs délais de détection : montre si l'explication météo tient quand on tient compte du temps
    que mettent les colonies à devenir repérables."""
    return [analyse_retro(rows, [observation], params, tolerance_j, d)[0] for d in delais]


def balayage_graines(rows: list[dict], debut: date, fin: date, pas_j: int = 7, params: dict | None = None) -> list[dict]:
    """Pour chaque date d'amorçage entre debut et fin : dates de sortie des 3 premières générations et du seuil de visibilité."""
    sortie, j = [], debut
    while j <= fin:
        surcharge = {**(params or {}), "infections_initiales": [{"t": j.isoformat() + "T12:00", "n": 1.0}],
                     "primaire": {**(params or {}).get("primaire", {}), "severite_precedente": 0}}
        res = calculer_saison(rows, surcharge)
        g = {x["generation"]: x["premiers_symptomes"] for x in res["generations"]}
        sortie.append({"graine": j.isoformat(), "G0": g.get(0), "G1": g.get(1), "G2": g.get(2), "G3": g.get(3),
                       "visible": res["jalons"].get("premiers_symptomes_visibles"), "dix_pct": res["jalons"].get("fraction_10_pct")})
        j += timedelta(days=pas_j)
    return sortie


# ---------------------------------------------------------------------------
# Lecture humaine et ligne de commande
# ---------------------------------------------------------------------------
def resume(res: dict, pas_j: int = 7) -> str:
    dn = res["donnees"]
    L = [f"OÏDIUM — cycle par cohortes (v1, formalismes de la littérature, non calé)",
         f"  débourrement : {res['debourrement']} | stock d'ascospores relatif : {res['stock_ascospores_initial']}",
         f"  vent : {dn['vent_pct']} % des heures renseignées ({'actif' if dn['vent_actif'] else 'inactif'}) | "
         f"rayonnement : {dn['rayonnement_pct']} % ({'UV actifs' if dn['uv_actif'] else 'UV inactifs'}) | "
         f"phénologie : {'active' if res['phenologie']['actif'] else 'inactive'}"]
    L += [f"  ! {a}" for a in res["avertissements"]]
    L.append(f"  infections primaires : {len(res['primaires'])}")
    for e in res["primaires"][:6]:
        L.append(f"    {e['t']}  pluie {e['pluie_mm']} mm  T {e['temperature']} °C  -> {e['colonies']} colonie(s)")
    L.append("\nJALONS")
    noms = {"premiers_symptomes_visibles": "premiers symptômes repérables", "fraction_1_pct": "1 % du feuillage atteint",
            "fraction_10_pct": "10 % du feuillage atteint", "fraction_50_pct": "50 % du feuillage atteint"}
    for k, v in noms.items():
        L.append(f"  {v:<34}: {res['jalons'].get(k, 'jamais')}")
    L.append("\nGÉNÉRATIONS (dates de sortie des symptômes ; 0 = infections primaires)")
    for g in res["generations"][:8]:
        L.append(f"  G{g['generation']}  premiers {g['premiers_symptomes']}  médiane {g['mediane']}  ({g['cohortes']} cohortes)")
    if res["phenologie"]["actif"]:
        L.append("\n" + resume_calendrier(res))
    L.append(f"\nÉVOLUTION (un point tous les {pas_j} jours)")
    L.append("  date        T moy  BBCH  potentiel  indice 7 j  grappes 7 j  latence éq.  sporulantes  feuillage atteint")
    for d in res["jours"][::pas_j]:
        lat = f"{d['latence_equivalente_j']:>5.1f} j" if d["latence_equivalente_j"] is not None else "  bloquée"
        bb = f"{d['bbch']:>4.0f}" if d["bbch"] is not None else "   -"
        gr = f"{d['indice_grappes_7j']:>9.1f} %" if d["indice_grappes_7j"] is not None else "        -  "
        L.append(f"  {d['date']}  {d['tmoy']:>5.1f}  {bb}  {d['potentiel_pct']:>7.1f} %  {d['indice_7j']:>8.1f} %  {gr}  {lat}   "
                 f"{d['colonies_sporulantes']:>10.3f}   {100 * d['fraction_malade']:>9.4f} %")
    return "\n".join(L)


def _lignes_brin(bg: dict) -> list[str]:
    """Détail BRIN + GFV : débourrement utilisé et calculé, dormance, phyllochrone, repères GFV."""
    dormance = {"calculee": "calculée (froid de Bidabé depuis le 1er août)", "supposee_levee": "supposée levée au début de la série",
                "non_levee": "jamais levée"}.get(bg["dormance"], bg["dormance"])
    L = [f"  débourrement utilisé : {bg['debourrement']} | calculé par BRIN : {bg['debourrement_brin'] or 'non atteint'}"
         + (f" (écart BRIN - utilisé : {bg['ecart_brin_j']:+d} j)" if bg["ecart_brin_j"] is not None else ""),
         f"  dormance : {dormance} | 9 feuilles : {bg['neuf_feuilles'] or 'non atteint'}",
         f"  feuilles : phyllochrone {bg['phyllochron']:.1f} °C·j, base {bg['t_base_feuilles']:g} °C"
         + (f", ajustés sur tes stades de feuilles (erreur moyenne {bg['rmse_feuilles']:.2f} feuille)" if bg.get("rmse_feuilles") is not None
            else " (valeurs de la littérature, ou phyllochrone seul recalé)")]
    if bg["seuils_gfv"]:
        s = bg["seuils_gfv"]
        L.append(f"  GFV (somme base 0 °C depuis le 1er mars) : 9 feuilles à {s['s19']:.0f} | floraison à {s['f_star']:.0f} | véraison à {s['v_star']:.0f}")
    return L


def resume_calendrier(res: dict) -> str:
    """Calendrier phénologique estimé et fenêtre de réceptivité des grappes."""
    f = res["phenologie"]
    if not f["actif"]:
        return "PHÉNOLOGIE INACTIVE : la série ne couvre pas le débourrement, ou la phénologie est désactivée."
    if f["modele"] == "brin_gfv":
        L = ["CALENDRIER PHÉNOLOGIQUE ESTIMÉ (BRIN -> croissance des feuilles -> GFV ; à confronter à tes relevés, puis à recaler avec --bbch DATE:STADE)"]
        L += _lignes_brin(f["brin_gfv"])
    else:
        L = [f"CALENDRIER PHÉNOLOGIQUE ESTIMÉ (degrés-jours base 10 depuis le débourrement du {res['debourrement']} ; "
             "à confronter à tes relevés, puis à recaler avec --bbch DATE:STADE)"]
    for nom, jour in f["calendrier"].items():
        L.append(f"  {nom:<26}: {jour or 'non atteint'}")
    d1, d2 = f["fenetre_grappes"]
    L.append(f"  grappes réceptives (sensibilité >= 50 %) : du {d1 or '-'} au {d2 or '-'}")
    return "\n".join(L)


def _sans_observations(surcharge: dict | None) -> dict:
    import copy
    s = copy.deepcopy(surcharge or {})
    s.get("phenologie", {}).pop("observations", None)
    return s


def _ecarts_par_defaut(rows: list[dict], surcharge: dict | None, observations: dict) -> str:
    """Écart des deux modèles PAR DÉFAUT (sans recalage) à des stades observés : mesure leur biais avant tout calage."""
    sans = _sans_observations(surcharge)
    col = {}
    for nom in ("djc", "brin_gfv"):
        r = calculer_saison(rows, mp.fusionner(sans, {"phenologie": {"modele": nom, "actif": None}}))
        f = r["phenologie"]
        col[nom] = ({e["date"]: e for e in phen.ecarts_observations({date.fromisoformat(k): v for k, v in f["bbch_jour"].items()}, observations)}
                    if f["actif"] and f["modele"] == nom else {})
    L = ["ÉCART DES MODÈLES PAR DÉFAUT À TES STADES OBSERVÉS (en jours ; + = le modèle est en RETARD sur l'observation)",
         f"  {'date':<12}{'BBCH':>6}   {'table DJC':>10}   {'BRIN + GFV':>10}"]
    for jour in sorted(observations):
        if observations[jour] <= 9:
            continue
        cellules = []
        for nom in ("djc", "brin_gfv"):
            e = col[nom].get(jour)
            cellules.append(f"{e['ecart_j']:+d}" if e and e["ecart_j"] is not None else "-")
        L.append(f"  {jour:<12}{observations[jour]:>6g}   {cellules[0]:>10}   {cellules[1]:>10}")
    for nom, etiquette in (("djc", "table DJC"), ("brin_gfv", "BRIN + GFV")):
        v = [e["ecart_j"] for e in col[nom].values() if e["ecart_j"] is not None]
        if v:
            L.append(f"  {etiquette:<12} écart moyen {sum(v) / len(v):+.1f} j | écart absolu moyen {sum(abs(x) for x in v) / len(v):.1f} j | "
                     f"sur {len(v)} stades")
    return "\n".join(L)


def resume_calendriers_compares(rows: list[dict], surcharge: dict | None = None) -> str:
    """Les deux modèles de phénologie côte à côte : table de degrés-jours et BRIN + feuilles + GFV. Avec des stades observés, affiche d'abord
    le biais des modèles par défaut, puis les calendriers recalés sur ces observations."""
    obs = ((surcharge or {}).get("phenologie") or {}).get("observations") or {}
    obs = {(k if isinstance(k, str) else k.isoformat()): v for k, v in obs.items()}
    a = calculer_saison(rows, mp.fusionner(surcharge or {}, {"phenologie": {"modele": "djc", "actif": None}}))
    try:
        b = calculer_saison(rows, mp.fusionner(surcharge or {}, {"phenologie": {"modele": "brin_gfv", "actif": None}}))
        erreur_b = None
    except ValueError as e:                              # observations de stade incompatibles avec l'enchaînement BRIN + feuilles + GFV
        b, erreur_b = None, str(e)
    fa = a["phenologie"]
    fb = b["phenologie"] if b else {"modele": None, "calendrier": {}, "fenetre_grappes": [None, None]}
    L = []
    if obs:
        L += [_ecarts_par_defaut(rows, surcharge, obs), ""]
    L.append("COMPARAISON DES DEUX MODÈLES DE PHÉNOLOGIE" + (" RECALÉS SUR TES STADES" if obs else " (à confronter à tes relevés de stade)"))
    if fb["modele"] == "brin_gfv":
        L += _lignes_brin(fb["brin_gfv"])
    elif erreur_b:
        L.append(f"  BRIN + GFV : observations incompatibles avec le modèle ({erreur_b})")
    else:
        L.append("  BRIN + GFV indisponible sur cette série : " + ("; ".join(b["avertissements"]) or "voir les avertissements"))
    L.append(f"  {'stade':<26}{'table DJC':<14}{'BRIN + GFV':<14}{'écart (j)'}")
    for nom in fa["calendrier"] or fb["calendrier"]:
        ja, jb = fa["calendrier"].get(nom), fb["calendrier"].get(nom) if fb["modele"] == "brin_gfv" else None
        ecart = f"{(date.fromisoformat(jb) - date.fromisoformat(ja)).days:+d}" if ja and jb else ""
        L.append(f"  {nom:<26}{ja or '-':<14}{jb or '-':<14}{ecart}")
    ga, gb = fa["fenetre_grappes"], fb["fenetre_grappes"] if fb["modele"] == "brin_gfv" else [None, None]
    L.append(f"  {'grappes réceptives':<26}{(ga[0] or '-') + ' → ' + (ga[1] or '-')}   |   {(gb[0] or '-') + ' → ' + (gb[1] or '-')}")
    return "\n".join(L)


def resume_retro(analyses: list[dict], tolerance: int) -> str:
    L = ["À REBOURS : quelles infections expliquent les symptômes observés ?",
         "  (latence calculée avec la météo réelle ; ne dépend ni de l'inoculum ni des traitements)", ""]
    for a in analyses:
        if not a["infections"]:
            L.append(f"  symptômes le {a['observation']} : aucune infection de la série n'y conduit")
            continue
        L.append(f"  symptômes le {a['observation']} (± {tolerance} j)")
        L.append(f"    infections correspondantes : du {a['infections'][0]} au {a['infections'][1]}  (latence {a['latence_j'][0]} à {a['latence_j'][1]} j)")
        rang = (f"meilleure que {a['rang_saison_pct']:.0f} % des jours du {a['periode_reference'][0][5:]} au {a['periode_reference'][1][5:]}"
                if a["rang_saison_pct"] is not None else "rang indisponible (aucun jour de référence dans la série)")
        L.append(f"    favorabilité moyenne de ces jours : {a['potentiel_moyen_pct']} %  -> {rang} ; jour le plus favorable : {a['meilleur_jour']}")
    return "\n".join(L)


def resume_sensibilite(observation: date, lignes: list[dict]) -> str:
    L = [f"  symptômes le {observation.isoformat()} : selon le délai entre fin de latence et repérage au vignoble"]
    for a in lignes:
        if not a["infections"]:
            L.append(f"    délai {a['delai_visible_j']} j : aucune infection de la série n'y conduit")
            continue
        rang = f"{a['rang_saison_pct']:.0f} %" if a["rang_saison_pct"] is not None else "n.d."
        L.append(f"    délai {a['delai_visible_j']} j : infections du {a['infections'][0][5:]} au {a['infections'][1][5:]}  "
                 f"favorabilité {a['potentiel_moyen_pct']:>5} %  (meilleure que {rang} des jours)")
    return "\n".join(L)


def resume_balayage(lignes: list[dict]) -> str:
    L = ["BALAYAGE DES AMORÇAGES (une infection initiale à midi à chaque date ; dates de sortie des symptômes par génération)", "",
         "  amorçage      G0          G1          G2          G3          symptômes repérables   10 % du feuillage"]
    for x in lignes:
        L.append(f"  {x['graine']}  {x['G0'] or '-':<10}  {x['G1'] or '-':<10}  {x['G2'] or '-':<10}  {x['G3'] or '-':<10}  "
                 f"{x['visible'] or '-':<21}  {x['dix_pct'] or '-'}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Moteur oïdium (cycle par cohortes, v0)")
    ap.add_argument("csv")
    ap.add_argument("--debourrement", help="date de débourrement AAAA-MM-JJ (défaut : 15 avril)")
    ap.add_argument("--severite", type=int, choices=(0, 1, 2, 3), help="sévérité de l'oïdium l'année précédente (0 à 3)")
    ap.add_argument("--indice-chasmotheces", type=float, help="indice chasmothèces 0 à 100 (inoculum primaire, issu de la saison précédente) ; remplace --severite")
    ap.add_argument("--graine", action="append", default=[], help="amorçage manuel : AAAA-MM-JJ[THH:MM] (répétable)")
    ap.add_argument("--multiplication", type=float, help="émission de conidies par colonie et par jour (calage)")
    ap.add_argument("--bbch", nargs="+", metavar="DATE:STADE", help="stades observés (ex. 2026-05-15:17) : recalent la phénologie")
    ap.add_argument("--stades", metavar="FICHIER", help="fichier CSV « date,bbch[,note] » de stades observés (BSV, relevés) : recale la phénologie")
    ap.add_argument("--calendrier", action="store_true",
                    help="compare les deux modèles de phénologie (degrés-jours et BRIN + feuilles + GFV) et s'arrête")
    ap.add_argument("--phenologie", choices=("djc", "brin_gfv"),
                    help="modèle de phénologie de la simulation : djc (défaut) ou brin_gfv (débourrement estimé par BRIN si --debourrement manque)")
    ap.add_argument("--sans-vent", action="store_true", help="ignore le vent (compare les scénarios)")
    ap.add_argument("--sans-uv", action="store_true", help="ignore le rayonnement (UV)")
    ap.add_argument("--sans-phenologie", action="store_true", help="ignore le stade, la surface foliaire et la résistance ontogénique")
    ap.add_argument("--fenetre", type=int, help="durée (jours) pendant laquelle les ascospores peuvent infecter après le débourrement (défaut 60)")
    ap.add_argument("--pas", type=int, default=7, help="un point d'affichage tous les N jours")
    ap.add_argument("--retro", nargs="+", metavar="DATE", help="dates observées de symptômes (AAAA-MM-JJ) : remonte aux jours d'infection")
    ap.add_argument("--tolerance", type=int, default=3, help="tolérance de la date observée, en jours (défaut 3)")
    ap.add_argument("--delai", type=int, default=0, help="délai (jours) entre fin de latence et repérage des symptômes au vignoble (défaut 0)")
    ap.add_argument("--balayage", nargs=2, metavar=("DEBUT", "FIN"), help="amorçages successifs entre deux dates : générations simulées")
    a = ap.parse_args(argv)
    surcharge = {"primaire": {}, "conidies": {}, "vent": {}, "uv": {}, "phenologie": {}}
    if a.sans_vent:
        surcharge["vent"]["actif"] = False
    if a.sans_uv:
        surcharge["uv"]["actif"] = False
    if a.sans_phenologie:
        surcharge["phenologie"]["actif"] = False
    if a.phenologie:
        surcharge["phenologie"]["modele"] = a.phenologie
    observations = {}
    if a.stades:
        try:
            observations.update(phen.lire_stades(a.stades))
        except (OSError, ValueError) as e:
            sys.exit(f"Erreur : {e}")
    if a.bbch:
        try:
            observations.update({x.split(":")[0]: float(x.split(":")[1]) for x in a.bbch})
        except (IndexError, ValueError):
            ap.error("--bbch attend des paires DATE:STADE, par exemple 2026-05-15:17")
    if observations:
        surcharge["phenologie"]["observations"] = observations
    if a.debourrement:
        surcharge["primaire"]["debourrement"] = a.debourrement
    if a.indice_chasmotheces is not None:
        surcharge["primaire"]["indice_chasmotheces"] = a.indice_chasmotheces
    if a.fenetre:
        surcharge["primaire"]["fenetre_jours"] = a.fenetre
    if a.severite is not None:
        surcharge["primaire"]["severite_precedente"] = a.severite
    if a.multiplication:
        surcharge["conidies"]["emission_par_colonie_jour"] = a.multiplication
    if a.graine:
        surcharge["infections_initiales"] = [{"t": g if "T" in g else g + "T00:00", "n": 1.0} for g in a.graine]
    rows = mp.charger_csv(a.csv)
    try:
        if a.calendrier:
            print(resume_calendriers_compares(rows, surcharge))
        elif a.retro:
            obs = [date.fromisoformat(x) for x in a.retro]
            print(resume_retro(analyse_retro(rows, obs, surcharge, a.tolerance, a.delai), a.tolerance))
            print("\nSENSIBILITÉ AU DÉLAI DE DÉTECTION (les colonies ne sont repérées que quelques jours après la fin de la latence)")
            for o in obs:
                print(resume_sensibilite(o, sensibilite_delai(rows, o, (0, 3, 5, 7), surcharge, a.tolerance)))
        elif a.balayage:
            print(resume_balayage(balayage_graines(rows, date.fromisoformat(a.balayage[0]), date.fromisoformat(a.balayage[1]), 7, surcharge)))
        else:
            print(resume(calculer_saison(rows, surcharge), a.pas))
    except ValueError as e:                              # observation de stade hors série, date mal écrite, série vide...
        sys.exit(f"Erreur : {e}")

if __name__ == "__main__":
    main()
