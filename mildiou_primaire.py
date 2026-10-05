#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VITI Sens — Moteur mildiou : contaminations primaire et secondaire (v0)
=======================================================================

Module pur : il reçoit une série météo HORAIRE (UTC) et rend le cycle
biologique de la saison, étape par étape :

  PRIMAIRE   maturation des oospores -> germination -> dispersion -> infection
             -> incubation -> taches d'huile -> sporulation
  SECONDAIRE sporanges des taches -> infection des feuilles saines (T x h >= 50,
             feuille mouillée) -> incubation -> taches -> sporulation -> ...
             (un cycle par période d'humectation tant que des sporanges sont disponibles)

Principes
---------
* Aucun chiffre en dur dans la logique : tout vient de PARAMS, et chaque
  paramètre porte son ORIGINE en commentaire :
    [F]  texte de Florent (algorithme de travail)
    [P]  Plasmopy / VitiMeteo-Plasmopara (config/main.yaml) — valeur reprise
         seulement là où le texte de Florent est muet
    [D]  défaut proposé, à valider ou à caler au rétro-test
* Déterministe : on recalcule TOUTE la saison depuis le 1er janvier à chaque
  appel (pas d'état incrémental), donc facile à corriger et à versionner.
* La sensibilité (stade BBCH) module la force d'infection, elle ne casse pas
  le cycle biologique.
* Les événements postérieurs à "maintenant" sont marqués previsionnel=True.

Ce n'est PAS une implémentation de Plasmopy (licence AGPL-3.0) : c'est
l'algorithme décrit dans le texte de travail, avec des paramètres sourcés.
"""
from __future__ import annotations

import argparse
import copy
import csv
import math
import sys
from collections import deque
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
H = timedelta(hours=1)

# ---------------------------------------------------------------------------
# PARAMÈTRES
# ---------------------------------------------------------------------------
PARAMS = {
    "fuseau_local": "Europe/Paris",            # [D] pour les « jours » (DJ, incubation)

    "maturation": {
        "debut_cumul": "01-01",                # [F] cumul depuis le 1er janvier
        "temperature_base": 8.0,               # [F] DJ8 = max(0 ; Tmoy - 8)
        "seuil_dj": 140.0,                     # [F] maturité acquise à >= 140 °C.j
        "paliers": [100.0, 120.0],             # [F] <100 faible, 100-120 en cours, 120-140 surveillance
        "methode_tmoy": "horaire",             # [D] "horaire" (moyenne des 24 h) ou "minmax"
        "mode_cumul": "degres_jours",          # [P] "degres_jours" : somme des (Tmoy - 8) ;
                                               #     "somme" : somme brute des Tmoy > 8 °C (lecture littérale du texte
                                               #     suisse « moyennes journalières dépassant 8 °C cumulées ») — À TRANCHER
        "date_forcee": None,                   # [F/P] ex. "2026-04-11" : ancre la maturité sur une observation
    },

    "humectation": {                           # [D] substitut du capteur d'humectation foliaire
        "seuil_capteur": 0.5,                  #     si la série fournit leaf_wetness (0-1)
        "pluie_mm": 0.1,                       #     feuille mouillée si pluie >= 0,1 mm dans l'heure
        "hr_pct": 90.0,                        #     ... ou HR >= 90 %
        "ecart_rosee_c": 1.0,                  #     ... ou T - point de rosée <= 1 °C
        "tolerance_h": None,                   # [D] heures non infectantes tolérées au sein d'une période d'humectation.
                                               #     None = aucune coupure (cumul libre, comportement v0) ;
                                               #     0 = continuité stricte ; au-delà, la période est remise à zéro.
    },

    "germination": {
        "temperature_min": 8.0,                # [F] T > 8 °C
        "hr_min": 80.0,                        # [F] porte A : HR > 80 % OU feuille mouillée
        "duree_h": 8,                          # [F] ... pendant >= 8 h consécutives
        "pluie_mm": 5.0,                       # [F] porte B : pluie cumulée >= 5 mm
        "pluie_fenetre_h": 48,                 # [F] ... sur 48 h glissantes
        "validite_h": 48,                      # [D] durée pendant laquelle les sporanges primaires
                                               #     peuvent encore être dispersés
    },

    "dispersion": {
        "pluie_mm": 3.0,                       # [F] seuil de pluie (strictement supérieur)
        "fenetre_h": 1,                        # [F] 1 = « > 3 mm/h » (ton texte). > 1 : cumul glissant sur N heures.
                                               #     À TRANCHER : la réanalyse lisse les pointes (voir LISEZMOI)
        "temperature_min": 8.0,                # [F] T > 8 °C (absent de Plasmopy)
        "latence_h": 0,                        # [F] texte muet -> 0 ; Plasmopy : 6 h
    },

    "infection": {
        "temperature_min": 8.0,                # [F] aligné sur la base : une heure mouillée ne compte que si elle est
                                               #     infectante (ta plage 3-29 °C est celle de l'infection secondaire
                                               #     chez Plasmopy ; avec une base de 8 °C, la borne 3 °C est sans effet)
        "temperature_max": 29.0,               # [F]
        "base_degres_heures": 8.0,             # [F, corrigé] DH = somme des max(0 ; T - 8) pendant l'humectation
        "soustraire_base": True,               # False : DH = somme de T (produit T x h, base 0) sur les heures où T > temperature_min.
                                               #     Le texte suisse donne « température x durée d'humectation = 50 ;
                                               #     à 10 °C, 5 h » (secondaire). Plasmopy note 8 °C comme « température
                                               #     minimale » de l'infection primaire — À TRANCHER
        "seuil_dh": 50.0,                      # [F] infection acquise à >= 50 °C.h
        "mouillage_min_h": None,               # [F] texte muet -> aucun minimum ; Plasmopy : 6 h  (À TRANCHER)
        "validite_h": 24,                      # [D] durée de vie des zoospores après dispersion
    },

    "incubation": {                            # [F] table de ton modèle actuel (Goidanich simplifiée)
        "temperature_min": 11,                 #     aucune incubation sous 11 °C
        "table": {12: 14, 13: 12, 14: 10, 15: 9, 16: 8, 17: 7, 18: 7, 19: 6, 20: 6,
                  21: 5, 22: 5, 23: 4, 24: 4, 25: 5, 26: 5, 27: 5, 28: 6},
    },

    "sporulation": {                           # [P] le texte de Florent n'a pas de valeurs
        "hr_min": 92.0,                        #     HR >= 92 %
        "temperature_min": 12.0,               #     T >= 12 °C
        "nuit_continue_h": 4,                  #     >= 4 h consécutives d'obscurité
        "elevation_nuit_deg": -0.833,          # [D] soleil sous l'horizon = nuit
        "fenetre_j": 15,                       # [F] durée de vie d'une tache d'huile : 15 jours au plus, en jours après son
                                               #     apparition (Orlandini et al. 2008, citée dans le texte de travail).
                                               #     None = illimitée : artefact (des taches de mai sporulent 3 mois plus tard)
    },

    "secondaire": {                            # infections secondaires (repiquage) : sporanges des taches -> feuilles saines
        "actif": True,
        "temperature_min": 3.0,                # [P] plage 3-29 °C de l'infection secondaire (Plasmopy) ; Rossi et al. 2021 :
        "temperature_max": 29.0,               #     4,0 / 21,0 / 30,2 °C (minimum / optimum / maximum)
        "base_degres_heures": 0.0,             # [F] « température x durée d'humectation = 50 » : produit T x h (base 0)
        "seuil_dh": 50.0,                      # [F][P] 50 °C.h (à 10 °C : 5 h) ; Rossi et al. 2021 : 2 h à l'optimum de 21 °C
        "mouillage_min_h": 1,                  # [P] mouillage >= 60 min
        "tolerance_h": 1,                      # [R] Rossi et al. 2021 : période d'infection = humectation continue ou
                                               #     interrompue au plus 1 h
        "survie": "vpd",                       # "vpd" : mortalité fonction du déficit de saturation (Blaeser & Weltzien 1979,
                                               #     d'après Brischetto et al. 2020 et Franche 2012, Eq. 7) ; vie de 2 à 9 jours
                                               # "vinemild" : courbe de Blaise & Gessler 1990 (Franche 2012, annexe 5) ; 4,5 à 9 jours
                                               # "fixe" : durée_vie_sporanges_h
        "duree_vie_sporanges_h": 72,           # [D] mode « fixe » seulement
        "survie_vpd": {
            "attaches": [9.27, -1.12, 0.04],   # [R] survie (jours) d'un sporange encore sur sporangiophore : a + b.V + c.V²
            "detaches": [5.67, -0.47, 0.02],   # [R] idem, sporange détaché. Brischetto 2020 : c = 0,02 ; Franche 2012 (Eq. 7,
                                               #     d'après Rossi) : c = 0,01 -> vie de 6 h à 6 jours : À TRANCHER
            "poids_attaches": 0.5,             # [R] Brischetto 2020 : même probabilité de mourir avant ou après détachement
            "formule_vpd": "steubing",         # [R] V = T x (1 - HR/100) (Steubing 1965), définition de Brischetto 2020 et de
                                               #     Franche 2012 pour ces trinômes ; "buck" : déficit de saturation physique
            # Au-delà du V qui minimise le trinôme (14 et 11,75), la survie reste constante : la parabole remonte ensuite
            # (non physique), nous la plafonnons.
        },
        "survie_vinemild": {                   # [R] progrès horaire de la mortalité des conidies (x 1e-3 par heure), LU SUR LE
                                               #     GRAPHIQUE de l'annexe 5 de Franche 2012 (précision d'environ 2 %)
            "temperatures": [0, 10, 20, 30, 40],
            "hr": [30, 50, 70, 90],
            "mortalite_pour_mille": [[4.75, 5.00, 5.50, 6.60, 9.15],     # HR 30 %
                                     [4.70, 4.85, 5.20, 5.85, 7.35],     # HR 50 %
                                     [4.65, 4.70, 4.90, 5.25, 6.00],     # HR 70 %
                                     [4.52, 4.58, 4.65, 4.75, 4.93]],    # HR 90 %
        },
        "pluie_detachement_mm": None,          # None : les sporanges sont dans l'air même sans pluie (Plasmopy, Rossi et al. 2021).
                                               #     0,2 : un sporange ne se détache qu'après une pluie horaire >= 0,2 mm
                                               #     (Franche 2012, modèles « pluie seule ») — À TRANCHER, effet important
        "productivite_min": None,              # None : toutes les nuits de sporulation comptent. Sinon, une tache n'alimente
                                               #     que les nuits dont la productivité relative RS = exp(5.3 - 0.7 n) / 100
                                               #     (Kennelly 2007 via Franche 2012, Eq. 13) reste >= ce seuil (0,1 : 4 nuits)
    },

    "sensibilite": {                           # [F] BBCH comme coefficient, pas comme condition binaire
        "bbch_debut": 10,                      #     premières feuilles
        "bbch_plein": 13,                      #     pleine réceptivité
    },
}

# Profils nommés : jeux de paramètres à passer à calculer_saison(params=...) ou à --profil. Les défauts de PARAMS
# restent ceux du texte de travail (v0) ; un profil ne surcharge que ce qui change.
PROFILS = {
    "texte": {},
    "calage_2026": {
        # Dispersion : cumul glissant 6 h > 5 mm (la pluie horaire de réanalyse lisse les pointes : « > 3 mm/h » rate
        # l'épisode de mai 2026 observé au champ, chez nous comme chez Plasmopy). Latence 0 h (0 à 2 h observées).
        "dispersion": {"fenetre_h": 6, "pluie_mm": 5.0, "latence_h": 0},
        # Infection : produit T x h (T > 8 °C), SANS plafond, 50 °C·h, cumul libre sur 24 h, aucun minimum de
        # mouillage. Vérifié contre Plasmopy : dates d'infection identiques (26/06 16 h, 29/06 05 h).
        "infection": {"soustraire_base": False, "temperature_min": 8.0, "temperature_max": 99.0, "seuil_dh": 50.0,
                      "mouillage_min_h": None, "validite_h": 24},
        "humectation": {"hr_pct": 90.0, "tolerance_h": None},
        # Durée de vie d'une tache : 15 jours (Orlandini et al. 2008 ; la référence n'en a pas : ses taches de juin
        # sporulent le 19/08).
        "sporulation": {"fenetre_j": 15},
    },
}


def charger_profil(nom: str) -> dict:
    if nom not in PROFILS:
        raise ValueError(f"profil inconnu : {nom} (profils disponibles : {', '.join(PROFILS)})")
    return copy.deepcopy(PROFILS[nom])


STATUTS_ACTIFS = ("germination_en_attente", "dispersion_en_cours", "infection", "taches_visibles")


def fusionner(base: dict, surcharge: dict | None) -> dict:
    res = copy.deepcopy(base)
    for k, v in (surcharge or {}).items():
        if isinstance(v, dict) and isinstance(res.get(k), dict):
            res[k] = fusionner(res[k], v)
        else:
            res[k] = v
    return res


# ---------------------------------------------------------------------------
# ENTRÉE : série horaire
# ---------------------------------------------------------------------------
def _flottant(s):
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s.lower() in ("nan", "none", "null"):
        return None
    return float(s)


def charger_csv(chemin: str) -> list[dict]:
    """CSV aux noms de colonnes Open-Meteo : time (UTC), temperature_2m,
    relative_humidity_2m, precipitation, dew_point_2m, [leaf_wetness]."""
    lignes = []
    with open(chemin, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["time"].strip())
            t = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
            lignes.append({
                "t": t,
                "temp": _flottant(r.get("temperature_2m")),
                "hr": _flottant(r.get("relative_humidity_2m")),
                "pluie": _flottant(r.get("precipitation")),
                "rosee": _flottant(r.get("dew_point_2m")),
                "mouille": _flottant(r.get("leaf_wetness")),
            })
    lignes.sort(key=lambda x: x["t"])
    return lignes


def preparer(rows: list[dict]):
    avert, propres, ignorees = [], [], 0
    for r in sorted(rows, key=lambda x: x["t"]):
        if r.get("temp") is None:
            ignorees += 1
            continue
        propres.append({**r, "pluie": r.get("pluie") or 0.0})
    if ignorees:
        avert.append(f"{ignorees} heure(s) sans température ignorée(s)")
    trous = sum(1 for a, b in zip(propres, propres[1:]) if b["t"] - a["t"] != H)
    if trous:
        avert.append(f"{trous} discontinuité(s) dans la série horaire : "
                     "fenêtres glissantes et durées approximatives")
    return propres, avert


# ---------------------------------------------------------------------------
# OUTILS
# ---------------------------------------------------------------------------
def elevation_solaire(t_utc: datetime, lat: float, lon: float) -> float:
    """Élévation du soleil en degrés (formules NOAA simplifiées)."""
    t_utc = t_utc.astimezone(UTC)
    jour = t_utc.timetuple().tm_yday
    heure = t_utc.hour + t_utc.minute / 60.0
    g = 2 * math.pi / 365.0 * (jour - 1 + (heure - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = heure * 60.0 + eqtime + 4.0 * lon
    ha = math.radians(tst / 4.0 - 180.0)
    la = math.radians(lat)
    s = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def est_mouille(r: dict, p: dict) -> bool:
    h = p["humectation"]
    if r.get("mouille") is not None:
        return r["mouille"] >= h["seuil_capteur"]
    if (r["pluie"] or 0.0) >= h["pluie_mm"]:
        return True
    if r.get("hr") is not None and r["hr"] >= h["hr_pct"]:
        return True
    if r.get("rosee") is not None and r["temp"] - r["rosee"] <= h["ecart_rosee_c"]:
        return True
    return False


def duree_incubation(tmoy, pi: dict):
    if tmoy is None or tmoy < pi["temperature_min"]:
        return None
    cles = sorted(pi["table"])
    t_int = max(cles[0], min(cles[-1], round(tmoy)))
    return pi["table"][t_int]


def coef_sensibilite(bbch, ps: dict) -> float:
    """0 avant BBCH 10, puis 0,25 / 0,5 / 0,75 / 1 de BBCH 10 à 13."""
    if bbch is None:
        return 1.0
    if bbch < ps["bbch_debut"]:
        return 0.0
    n = ps["bbch_plein"] - ps["bbch_debut"] + 1
    return min(1.0, (bbch - ps["bbch_debut"] + 1) / n)


def statut_maturation(cumul: float, pm: dict) -> str:
    if cumul >= pm["seuil_dj"]:
        return "maturité acquise"
    p1, p2 = pm["paliers"]
    if cumul >= p2:
        return "fenêtre de surveillance"
    if cumul >= p1:
        return "maturation en cours"
    return "maturation faible"


def _local_vers_utc(s: str, tz: ZoneInfo) -> datetime:
    d = datetime.fromisoformat(s)
    return (d.replace(tzinfo=tz) if d.tzinfo is None else d).astimezone(UTC)


def _iso(v):
    if isinstance(v, datetime):
        return v.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    return v


def vpd_hpa(temp, hr) -> float:
    """Déficit de saturation de l'air, en hPa (pression de vapeur saturante de Buck 1981). HR absente : air saturé."""
    if hr is None:
        return 0.0
    es = 6.1121 * math.exp((18.678 - temp / 234.5) * temp / (257.14 + temp))
    return max(0.0, es * (1.0 - hr / 100.0))


def deficit_survie(temp, hr, formule: str = "steubing") -> float:
    """Variable des trinômes de survie : T x (1 - HR/100) (Steubing 1965, définition des sources) ou VPD physique (Buck)."""
    if hr is None:
        return 0.0
    return vpd_hpa(temp, hr) if formule == "buck" else max(0.0, temp * (1.0 - hr / 100.0))


def survie_jours(coefs, vpd: float) -> float:
    """Survie (jours) d'un sporange : trinôme en VPD, maintenu constant au-delà de son minimum (sommet de la parabole)."""
    a, b, cc = coefs
    v = min(vpd, -b / (2.0 * cc)) if cc > 0 else vpd
    return a + b * v + cc * v * v


def mortalite_horaire(temp, hr, psv: dict) -> float:
    """Fraction de sporanges qui meurent pendant une heure : 1 / (24 x survie en jours), moyenne pondérée des sporanges
    attachés et détachés (Blaeser & Weltzien 1979, d'après Brischetto et al. 2020)."""
    v = deficit_survie(temp, hr, psv.get("formule_vpd", "steubing"))
    w = psv["poids_attaches"]
    return (w / survie_jours(psv["attaches"], v) + (1.0 - w) / survie_jours(psv["detaches"], v)) / 24.0


def mortalite_vinemild(temp, hr, pv: dict) -> float:
    """Fraction de conidies qui meurent pendant une heure selon la courbe de Vinemild (interpolation bilinéaire du tableau,
    hors bornes : valeur du bord ; HR absente : 90 %)."""
    temps, hrs, tab = pv["temperatures"], pv["hr"], pv["mortalite_pour_mille"]

    def pos(x, grille):
        x = min(max(x, grille[0]), grille[-1])
        for k in range(len(grille) - 1):
            if x <= grille[k + 1]:
                return k, (x - grille[k]) / (grille[k + 1] - grille[k])
        return len(grille) - 2, 1.0

    i, a = pos(90.0 if hr is None else hr, hrs)
    j, b = pos(temp, temps)
    bas = tab[i][j] * (1 - b) + tab[i][j + 1] * b
    haut = tab[i + 1][j] * (1 - b) + tab[i + 1][j + 1] * b
    return (bas * (1 - a) + haut * a) / 1000.0


def productivite_relative(n: int) -> float:
    """Productivité relative d'une tache à sa n-ième sporulation (n >= 1) : exp(5,3 - 0,7 n) / 100 (Kennelly 2007, d'après
    Franche 2012, Eq. 13). Elle est divisée par 2 à chaque sporulation."""
    return math.exp(5.3 - 0.7 * n) / 100.0


def nuits_productives(nuits: list, seuil) -> list:
    """Les premières nuits de sporulation d'une tache dont la productivité relative reste >= seuil (None : toutes)."""
    if seuil is None:
        return nuits
    k = 0
    while k < len(nuits) and productivite_relative(k + 1) >= seuil:
        k += 1
    return nuits[:k]


def fin_disponibilite(mor, i0: int) -> int:
    """Premier indice où les sporanges apparus en i0 ne sont plus disponibles : le cumul des mortalités horaires
    (mor) atteint 1. Rend len(mor) si la série s'arrête avant."""
    cumul, i = 0.0, i0
    while i < len(mor) and cumul < 1.0:
        cumul += mor[i]
        i += 1
    return i


def evolution_lesions(rows, idx, i_infection, tmoy_jour, tz, pinc, ps, nuits) -> dict:
    """Suite d'une infection survenue à l'heure d'indice i_infection : incubation, apparition des taches,
    puis TOUTES les nuits de sporulation (une par nuit où HR, T et obscurité sont réunies >= nuit_continue_h)
    jusqu'à la fin de vie de la tache (sporulation.fenetre_j, illimitée si None).
    Commun aux infections primaires et secondaires.
    Retourne {progression_pct, t_sym, nuits (indices de ligne), fenetre_ecoulee}."""
    prog, t_sym = 0.0, None
    for i in range(i_infection + 1, len(rows)):
        dur = duree_incubation(tmoy_jour[rows[i]["t"].astimezone(tz).date()], pinc)
        if dur:
            prog += 1.0 / (24.0 * dur)
        if prog >= 1.0 - 1e-9:
            t_sym = rows[i]["t"] + H
            break
    out = {"progression_pct": min(100, int(prog * 100)), "t_sym": t_sym, "nuits": [], "fenetre_ecoulee": False}
    if t_sym is None:
        return out
    fin_spor = t_sym + timedelta(days=ps["fenetre_j"]) if ps.get("fenetre_j") else None
    run = 0
    for i in range(idx.get(t_sym, len(rows)), len(rows)):
        r = rows[i]
        if fin_spor is not None and r["t"] >= fin_spor:
            break
        ok = (nuits[i] and r["hr"] is not None and r["hr"] >= ps["hr_min"]
              and r["temp"] >= ps["temperature_min"])
        run = run + 1 if ok else 0
        if run == ps["nuit_continue_h"]:
            out["nuits"].append(i)
    # fenêtre écoulée dans la série sans nuit favorable : la tache ne sporulera plus
    out["fenetre_ecoulee"] = (not out["nuits"] and fin_spor is not None and rows[-1]["t"] + H >= fin_spor)
    return out


def calculer_secondaires(rows, idx, p, tz, tmoy_jour, nuits, cycles):
    """Infections secondaires, heure par heure.

    Sources : chaque nuit de sporulation d'une tache (primaire, puis secondaire) produit des sporanges disponibles
    pendant duree_vie_sporanges_h. Tant qu'un sporange est disponible, chaque période d'humectation (feuille mouillée,
    T dans la plage) cumule des degrés-heures ; à seuil_dh, une infection secondaire est acquise (une par période).
    Elle suit le même chemin que le primaire : incubation, taches, nuits de sporulation -> nouvelles sources.

    Les nouvelles sources sont toujours situées après l'infection qui les produit (incubation >= 4 jours) : le calcul
    en un seul passage chronologique est donc exact. Retourne (événements, force par jour local)."""
    ps2, pinc, ps = p["secondaire"], p["incubation"], p["sporulation"]
    n, duree = len(rows), int(ps2["duree_vie_sporanges_h"])
    couv_gen, couv_src = [None] * n, [None] * n        # génération minimale et source qui couvrent chaque heure
    if ps2["survie"] == "vpd":
        mor = [mortalite_horaire(r["temp"], r["hr"], ps2["survie_vpd"]) for r in rows]
    elif ps2["survie"] == "vinemild":
        mor = [mortalite_vinemild(r["temp"], r["hr"], ps2["survie_vinemild"]) for r in rows]
    else:
        mor = None
    pluie_min = ps2.get("pluie_detachement_mm")

    def ajouter_source(i0, gen, src):
        """Une nuit de sporulation en i0 : les sporanges sont disponibles jusqu'à leur mort (cumul de mortalité >= 1)
        ou, en mode « fixe », pendant duree_vie_sporanges_h. Avec pluie_detachement_mm, ils ne sont disponibles qu'à partir
        de la première pluie horaire suffisante qui suit la sporulation (aucune pluie : jamais)."""
        fin = min(n, i0 + duree) if mor is None else fin_disponibilite(mor, i0)
        debut = i0
        if pluie_min is not None:
            debut = next((k for k in range(i0, fin) if (rows[k]["pluie"] or 0.0) >= pluie_min), fin)
        for i in range(debut, fin):
            if couv_gen[i] is None or gen < couv_gen[i]:
                couv_gen[i], couv_src[i] = gen, src

    for c in cycles:
        for i0 in nuits_productives(c.get("_nuits_spor", []), ps2.get("productivite_min")):
            ajouter_source(i0, 0, {"type": "primaire", "id": c["id"]})

    evenements, force_jour = [], {}
    vide = {"dh": 0.0, "n": 0, "gap": 0, "ev": None}
    per = dict(vide)
    for i, r in enumerate(rows):
        T = r["temp"]
        if not (est_mouille(r, p) and ps2["temperature_min"] <= T <= ps2["temperature_max"]):
            per["gap"] += 1
            if per["gap"] > ps2["tolerance_h"]:
                per = dict(vide)                         # la feuille a séché : la période est perdue
            continue
        per["gap"] = 0
        if couv_gen[i] is None:                          # feuille mouillée mais aucun sporange disponible
            continue
        dh = max(0.0, T - ps2["base_degres_heures"])
        per["dh"] += dh
        per["n"] += 1
        j = r["t"].astimezone(tz).date()
        force_jour[j] = force_jour.get(j, 0.0) + dh
        if per["ev"] is not None:                        # période déjà infectante : la force continue de croître
            per["ev"]["_dh"] += dh
            continue
        if per["dh"] >= ps2["seuil_dh"] - 1e-9 and per["n"] >= ps2["mouillage_min_h"]:
            ev = {"id": len(evenements) + 1, "generation": couv_gen[i] + 1, "source": couv_src[i],
                  "infection": {"t": r["t"]}, "statut": "infection", "_dh": per["dh"]}
            evo = evolution_lesions(rows, idx, i, tmoy_jour, tz, pinc, ps, nuits)
            ev["incubation"] = {"progression_pct": evo["progression_pct"], "debut": r["t"] + H}
            if evo["t_sym"] is not None:
                ev["taches"] = {"t": evo["t_sym"]}
                ev["statut"] = "taches_visibles"
                if evo["nuits"]:
                    ev["sporulation"] = {"t": rows[evo["nuits"][0]]["t"]}
                    ev["nuits_sporulation"] = len(evo["nuits"])
                    ev["statut"] = "sporulation"
                    for i0 in nuits_productives(evo["nuits"], ps2.get("productivite_min")):
                        ajouter_source(i0, ev["generation"], {"type": "secondaire", "id": ev["id"]})
                elif evo["fenetre_ecoulee"]:
                    ev["statut"] = "taches_sans_sporulation"
            evenements.append(ev)
            per["ev"] = ev
    for ev in evenements:
        ev["force_dh"] = round(ev["_dh"], 1)
    return evenements, force_jour


# ---------------------------------------------------------------------------
# MOTEUR
# ---------------------------------------------------------------------------
def calculer_saison(rows, lat, lon, params=None, now=None, bbch=None) -> dict:
    """
    rows  : série horaire [{t (UTC), temp, hr, pluie, rosee?, mouille?}, ...]
    bbch  : {date locale (date ou 'AAAA-MM-JJ'): stade BBCH}, optionnel
    now   : instant de référence (défaut : maintenant) pour marquer le prévisionnel
    """
    p = fusionner(PARAMS, params)
    tz = ZoneInfo(p["fuseau_local"])
    now = now or datetime.now(UTC)
    rows, avert = preparer(rows)
    if not rows:
        raise ValueError("série météo vide")
    bbch = {(date.fromisoformat(k) if isinstance(k, str) else k): v for k, v in (bbch or {}).items()}

    pm, pg, pd_, pi_, pinc, ps, psens = (p["maturation"], p["germination"], p["dispersion"],
                                         p["infection"], p["incubation"], p["sporulation"], p["sensibilite"])

    # --- jours locaux ---------------------------------------------------
    par_jour = {}
    for r in rows:
        par_jour.setdefault(r["t"].astimezone(tz).date(), []).append(r["temp"])
    tmoy_jour = {}
    for j, temps in par_jour.items():
        tmoy_jour[j] = ((max(temps) + min(temps)) / 2.0 if pm["methode_tmoy"] == "minmax"
                        else sum(temps) / len(temps))

    # --- étape 1 : maturation des oospores ------------------------------
    annee = rows[0]["t"].astimezone(tz).year
    mm, dd = (int(x) for x in pm["debut_cumul"].split("-"))
    debut = date(annee, mm, dd)
    if min(tmoy_jour) > debut:
        avert.append(f"la série commence le {min(tmoy_jour)} : le cumul de DJ ne part pas du {debut}")
    cumul, date_mat, mat_jours = 0.0, None, {}
    for j in sorted(tmoy_jour):
        if j < debut:
            continue
        if pm["mode_cumul"] == "somme":
            dj = tmoy_jour[j] if tmoy_jour[j] > pm["temperature_base"] else 0.0
        else:
            dj = max(0.0, tmoy_jour[j] - pm["temperature_base"])
        cumul += dj
        if date_mat is None and cumul >= pm["seuil_dj"]:
            date_mat = j
        mat_jours[j] = {"tmoy": tmoy_jour[j], "dj": dj, "cumul": cumul}

    if pm["date_forcee"]:
        t_mat = _local_vers_utc(pm["date_forcee"], tz)
    elif date_mat:
        t_mat = datetime.combine(date_mat + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(UTC)
    else:
        t_mat = None

    # --- étapes 2 à 5 : boucle horaire ----------------------------------
    nuits = [elevation_solaire(r["t"] + timedelta(minutes=30), lat, lon) < ps["elevation_nuit_deg"] for r in rows]
    fen_pluie = deque(maxlen=pg["pluie_fenetre_h"])
    fen_disp = deque(maxlen=pd_["fenetre_h"])
    somme_disp = 0.0
    somme_pluie, run_a, a_prec, b_prec = 0.0, 0, False, False
    armed, fenetres, cycles, force_jour = None, [], [], {}

    def finaliser_fenetre(c):
        c["_clos"] = True
        c["force_dh"] = round(c["_dh"], 1)
        if "infection" not in c:
            c["statut"] = "dispersion_sans_infection"

    for r in rows:
        t, T, hr, pluie = r["t"], r["temp"], r["hr"], r["pluie"]
        mouille = est_mouille(r, p)

        if len(fen_pluie) == fen_pluie.maxlen:
            somme_pluie -= fen_pluie[0]
        fen_pluie.append(pluie)
        somme_pluie += pluie
        if len(fen_disp) == fen_disp.maxlen:
            somme_disp -= fen_disp[0]
        fen_disp.append(pluie)
        somme_disp += pluie
        disp_ok = somme_disp > pd_["pluie_mm"] + 1e-9 and T > pd_["temperature_min"]

        mat_ok = t_mat is not None and t >= t_mat
        cond_a = T > pg["temperature_min"] and (mouille or (hr is not None and hr > pg["hr_min"]))
        run_a = run_a + 1 if cond_a else 0
        a_eff = mat_ok and run_a >= pg["duree_h"]
        b_eff = mat_ok and somme_pluie >= pg["pluie_mm"] - 1e-9
        nouveau_a, nouveau_b = a_eff and not a_prec, b_eff and not b_prec
        a_prec, b_prec = a_eff, b_eff

        # germination : un épisode (front montant) donne au plus un cycle, et aucune
        # nouvelle germination n'est ouverte tant qu'un cycle est en cours
        ouvert = any(not c.get("_clos") and t < c["_fin"] for c in fenetres)
        if armed is None and not ouvert and (nouveau_a or nouveau_b):
            porte = ("humidité + pluie" if nouveau_a and nouveau_b
                     else "humidité" if nouveau_a else "pluie")
            c = {"id": len(cycles) + 1, "germination": {"t": t, "porte": porte},
                 "statut": "germination_en_attente"}
            cycles.append(c)
            armed = {"c": c, "expire": t + timedelta(hours=pg["validite_h"])}

        # dispersion
        if armed is not None:
            c = armed["c"]
            if (t >= c["germination"]["t"] + timedelta(hours=pd_["latence_h"]) and t < armed["expire"]
                    and disp_ok):
                c["dispersion"] = {"t": t, "pluie_mm": round(somme_disp, 1),
                                   "fenetre_h": pd_["fenetre_h"], "derniere": t}
                c["statut"] = "dispersion_en_cours"
                c["_fin"] = t + timedelta(hours=pi_["validite_h"])
                c["_dh"], c["_dhp"], c["_mhp"], c["_gap"] = 0.0, 0.0, 0, 0
                fenetres.append(c)
                armed = None
            elif t + H >= armed["expire"]:
                armed["c"]["statut"] = "germination_sans_dispersion"
                armed = None

        # une nouvelle pluie dispersante prolonge la fenêtre d'infection en cours :
        # une longue période pluvieuse reste un seul cycle dont la force cumule
        if disp_ok:
            for c in fenetres:
                if not c.get("_clos") and t < c["_fin"]:
                    c["_fin"] = t + timedelta(hours=pi_["validite_h"])
                    c["dispersion"]["derniere"] = t

        # infection : degrés-heures cumulés pendant l'humectation. Le cumul « de période » décide de
        # l'infection ; le cumul total (_dh) ne sert qu'à la force. Une coupure de plus de
        # tolerance_h heures non infectantes remet la période à zéro (tolérance None : jamais).
        dh_h, en_fenetre = 0.0, False
        tol = p["humectation"]["tolerance_h"]
        for c in fenetres:
            if c.get("_clos"):
                continue
            if t >= c["_fin"]:
                finaliser_fenetre(c)
                continue
            if mouille and pi_["temperature_min"] <= T <= pi_["temperature_max"]:
                dh_h = max(0.0, T - pi_["base_degres_heures"]) if pi_["soustraire_base"] else T
                en_fenetre = True
                c["_dh"] += dh_h
                c["_dhp"] += dh_h
                c["_mhp"] += 1
                c["_gap"] = 0
                if ("infection" not in c and c["_dhp"] >= pi_["seuil_dh"] - 1e-9
                        and (pi_["mouillage_min_h"] is None or c["_mhp"] >= pi_["mouillage_min_h"])):
                    c["infection"] = {"t": t}
                    c["statut"] = "infection"
            else:
                c["_gap"] += 1
                if tol is not None and c["_gap"] > tol:
                    c["_dhp"], c["_mhp"] = 0.0, 0
        if en_fenetre:
            j = t.astimezone(tz).date()
            force_jour[j] = force_jour.get(j, 0.0) + dh_h

    for c in fenetres:                       # fenêtres encore ouvertes en fin de série
        if not c.get("_clos"):
            c["force_dh"] = round(c["_dh"], 1)

    # --- étapes 10 à 12 : incubation -> taches -> sporulation -----------
    idx = {r["t"]: i for i, r in enumerate(rows)}
    for c in cycles:
        if "infection" not in c:
            continue
        evo = evolution_lesions(rows, idx, idx[c["infection"]["t"]], tmoy_jour, tz, pinc, ps, nuits)
        c["incubation"] = {"progression_pct": evo["progression_pct"], "debut": c["infection"]["t"] + H}
        if evo["t_sym"] is None:
            continue
        c["taches"] = {"t": evo["t_sym"]}
        c["statut"] = "taches_visibles"
        if evo["nuits"]:
            c["sporulation"] = {"t": rows[evo["nuits"][0]]["t"]}
            c["statut"] = "sporulation"
            c["repiquage"] = True          # passage aux infections secondaires (étage suivant)
            c["_nuits_spor"] = evo["nuits"]
            c["sporulations"] = [_iso(rows[i]["t"]) for i in evo["nuits"]]      # toutes les nuits de sporulation de la tache
        elif evo["fenetre_ecoulee"]:
            c["statut"] = "taches_sans_sporulation"

    # --- étage secondaire (repiquage) -----------------------------------
    secondaires, force_sec_jour = [], {}
    if p["secondaire"]["actif"]:
        secondaires, force_sec_jour = calculer_secondaires(rows, idx, p, tz, tmoy_jour, nuits, cycles)

    # --- sorties --------------------------------------------------------
    for c in cycles:
        if "dispersion" in c and "force_dh" not in c:
            c["force_dh"] = round(c.get("_dh", 0.0), 1)
        if bbch and "infection" in c:
            c["force_ponderee"] = round(c["force_dh"] * coef_sensibilite(
                bbch.get(c["infection"]["t"].astimezone(tz).date()), psens), 1)
        for ev in ("germination", "dispersion", "infection", "taches", "sporulation"):
            if ev in c:
                c[ev]["previsionnel"] = c[ev]["t"] > now

    def public(c):
        out = {}
        for k, v in c.items():
            if k.startswith("_"):
                continue
            out[k] = ({kk: _iso(vv) for kk, vv in v.items()} if isinstance(v, dict) else v)
        return out

    sorties = [public(c) for c in cycles]
    for ev in secondaires:
        for nom in ("infection", "taches", "sporulation"):
            if nom in ev:
                ev[nom]["previsionnel"] = ev[nom]["t"] > now
    sorties_sec = [public(ev) for ev in secondaires]
    jours_out = []
    for j in sorted(tmoy_jour):
        d = {"date": j.isoformat(), "tmoy": round(tmoy_jour[j], 1),
             "force_infection_dh": round(force_jour.get(j, 0.0), 1),
             "force_secondaire_dh": round(force_sec_jour.get(j, 0.0), 1)}
        if j in mat_jours:
            m = mat_jours[j]
            d.update({"dj": round(m["dj"], 2), "dj_cumul": round(m["cumul"], 1),
                      "pct_maturite": min(100, round(m["cumul"] / pm["seuil_dj"] * 100)),
                      "statut_maturation": statut_maturation(m["cumul"], pm)})
        if bbch:
            d["force_ponderee"] = round(d["force_infection_dh"] * coef_sensibilite(bbch.get(j), psens), 1)
        jours_out.append(d)

    j_now = now.astimezone(tz).date()
    ref = [d for d in jours_out if "dj_cumul" in d and d["date"] <= j_now.isoformat()]
    ref = ref[-1] if ref else None
    return {
        "parametres": p,
        "avertissements": avert,
        "maturation": {
            "date_maturite": (date_mat.isoformat() if date_mat else None),
            "date_forcee": pm["date_forcee"],
            "active_depuis": _iso(t_mat) if t_mat else None,
            "dj_cumul": ref["dj_cumul"] if ref else None,
            "pct": ref["pct_maturite"] if ref else None,
            "statut": ref["statut_maturation"] if ref else None,
        },
        "jours": jours_out,
        "cycles": sorties,
        "secondaires": sorties_sec,
        "cycles_actifs": [c for c in sorties if c["statut"] in STATUTS_ACTIFS],
    }


# ---------------------------------------------------------------------------
# LIGNE DE COMMANDE
# ---------------------------------------------------------------------------
def resume(res: dict) -> str:
    m = res["maturation"]
    L = []
    for a in res["avertissements"]:
        L.append(f"⚠ {a}")
    L.append("MATURATION DES OOSPORES")
    if m["date_forcee"]:
        L.append(f"  maturité ancrée manuellement au {m['date_forcee']}")
    L.append(f"  {m['dj_cumul']} °C.j ({m['pct']} %) — {m['statut']}"
             + (f" — seuil atteint le {m['date_maturite']}" if m["date_maturite"] else ""))
    L.append("")
    cycles = res["cycles"]
    avec = [c for c in cycles if "dispersion" in c]
    sans = len(cycles) - len(avec)
    L.append(f"CYCLES PRIMAIRES : {len(cycles)} ({len(avec)} avec dispersion, {sans} germination(s) sans dispersion)")
    for c in avec:
        ev = []
        for nom, lib in (("germination", "germ."), ("dispersion", "disp."), ("infection", "infect."),
                         ("taches", "taches"), ("sporulation", "spor.")):
            if nom in c:
                extra = (f" ({c[nom]['pluie_mm']} mm/{c[nom]['fenetre_h']} h)" if nom == "dispersion" else "")
                ev.append(f"{lib} {c[nom]['t']}{extra}" + ("*" if c[nom].get("previsionnel") else ""))
        force = f" | force {c['force_dh']} °C.h" if "force_dh" in c else ""
        L.append(f"  #{c['id']:>2} [{c['statut']}] " + " → ".join(ev) + force)
    L.append("  (* = prévisionnel)")
    L.append("")
    if res["parametres"]["secondaire"]["actif"]:
        sec = res.get("secondaires", [])
        par_gen = {}
        for e in sec:
            par_gen[e["generation"]] = par_gen.get(e["generation"], 0) + 1
        detail = ", ".join(f"génération {g} : {n}" for g, n in sorted(par_gen.items()))
        L.append(f"INFECTIONS SECONDAIRES : {len(sec)}" + (f" ({detail})" if sec else ""))
        for e in sec:
            ev = []
            for nom, lib in (("infection", "infect."), ("taches", "taches"), ("sporulation", "spor.")):
                if nom in e:
                    ev.append(f"{lib} {e[nom]['t']}" + ("*" if e[nom].get("previsionnel") else ""))
            src = e["source"]
            L.append(f"  #{e['id']:>2} g{e['generation']} [{e['statut']}] " + " → ".join(ev)
                     + f" | force {e['force_dh']} °C.h | source {src['type'][0].upper()}{src['id']}")
        L.append("  (source P = taches primaires, S = taches secondaires ; * = prévisionnel)")
        L.append("")
    L.append("OÙ EN EST LE CYCLE AUJOURD'HUI")
    actifs = res["cycles_actifs"]
    if not actifs:
        L.append("  aucun cycle en cours")
    for c in actifs:
        suite = {"germination_en_attente": "en attente d'une pluie dispersante",
                 "dispersion_en_cours": "fenêtre d'infection ouverte",
                 "infection": f"en incubation ({c.get('incubation', {}).get('progression_pct', 0)} %)",
                 "taches_visibles": "taches visibles, en attente d'une nuit de sporulation"}[c["statut"]]
        L.append(f"  #{c['id']:>2} {suite}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mildiou — contamination primaire (v0)")
    ap.add_argument("csv", help="série horaire (colonnes Open-Meteo, heures UTC)")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--profil", choices=sorted(PROFILS),
                    help="jeu de paramètres nommé (les options ci-dessous le surchargent)")
    ap.add_argument("--maturite", help="ancre la maturité des oospores, ex. 2026-04-11")
    ap.add_argument("--fenetre", type=int, help="dispersion : fenêtre de cumul de pluie, en heures (défaut 1)")
    ap.add_argument("--seuil", type=float, help="dispersion : seuil de pluie en mm (défaut 3)")
    ap.add_argument("--tolerance", type=int, help="humectation : heures sèches tolérées (défaut : cumul libre)")
    ap.add_argument("--minimum", type=int, help="infection : durée minimale continue d'humectation, en heures")
    ap.add_argument("--hr", type=float, help="humectation : seuil d'HR d'une heure mouillée, en %% (défaut 90)")
    ap.add_argument("--fenetre-spor", type=int, help="sporulation : durée de vie d'une tache, en jours (défaut : illimitée)")
    ap.add_argument("--cumul", choices=("degres_jours", "somme"),
                    help="maturation : somme des (Tmoy - 8) [défaut] ou somme brute des Tmoy > 8 °C")
    ap.add_argument("--tmax", type=float,
                    help="infection : température maximale d'une heure infectante, en °C (défaut 29, plage de l'infection "
                         "secondaire chez Plasmopy ; mettre 99 pour supprimer le plafond)")
    ap.add_argument("--validite-inf", type=int,
                    help="infection : durée de la fenêtre d'infection après la dispersion, en heures (défaut 24)")
    ap.add_argument("--sans-secondaire", action="store_true", help="désactive l'étage des infections secondaires")
    ap.add_argument("--vie-sporanges", type=int,
                    help="secondaire : durée FIXE de disponibilité des sporanges après une nuit de sporulation, en heures "
                         "(désactive la survie selon le déficit de saturation)")
    ap.add_argument("--survie", choices=("vpd", "vinemild"),
                    help="secondaire : loi de survie des sporanges (vpd : Blaeser & Weltzien ; vinemild : Blaise & Gessler)")
    ap.add_argument("--pluie-detachement", type=float,
                    help="secondaire : pluie horaire minimale (mm) pour détacher les sporanges (défaut : aucune condition)")
    ap.add_argument("--productivite-min", type=float,
                    help="secondaire : ne compter que les nuits où la productivité relative d'une tache reste >= ce seuil")
    ap.add_argument("--tol-sec", type=int,
                    help="secondaire : heures sèches tolérées au sein d'une période d'humectation (défaut 1)")
    ap.add_argument("--dh", choices=("base", "produit"),
                    help="infection : degrés-heures avec base soustraite (T - 8) [défaut] ou produit T x h")
    a = ap.parse_args(argv)
    surcharge = {}
    if a.maturite:
        surcharge["maturation"] = {"date_forcee": a.maturite}
    disp = {k: v for k, v in (("fenetre_h", a.fenetre), ("pluie_mm", a.seuil)) if v is not None}
    if disp:
        surcharge["dispersion"] = disp
    hum = {k: v for k, v in (("tolerance_h", a.tolerance), ("hr_pct", a.hr)) if v is not None}
    if hum:
        surcharge["humectation"] = hum
    inf = {}
    if a.minimum is not None:
        inf["mouillage_min_h"] = a.minimum
    if a.dh:
        inf["soustraire_base"] = (a.dh == "base")
    if a.tmax is not None:
        inf["temperature_max"] = a.tmax
    if a.validite_inf is not None:
        inf["validite_h"] = a.validite_inf
    if inf:
        surcharge["infection"] = inf
    sec = {}
    if a.sans_secondaire:
        sec["actif"] = False
    if a.vie_sporanges is not None:
        sec["survie"], sec["duree_vie_sporanges_h"] = "fixe", a.vie_sporanges
    if a.survie:
        sec["survie"] = a.survie
    if a.pluie_detachement is not None:
        sec["pluie_detachement_mm"] = a.pluie_detachement
    if a.productivite_min is not None:
        sec["productivite_min"] = a.productivite_min
    if a.tol_sec is not None:
        sec["tolerance_h"] = a.tol_sec
    if sec:
        surcharge["secondaire"] = sec
    if a.cumul:
        surcharge.setdefault("maturation", {})["mode_cumul"] = a.cumul
    if a.fenetre_spor is not None:
        surcharge["sporulation"] = {"fenetre_j": a.fenetre_spor}
    params = fusionner(charger_profil(a.profil), surcharge) if a.profil else (surcharge or None)
    if a.profil:
        print(f"Profil : {a.profil}\n")
    print(resume(calculer_saison(charger_csv(a.csv), a.lat, a.lon, params=params)))


if __name__ == "__main__":
    main()
