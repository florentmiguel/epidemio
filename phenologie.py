#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phénologie de la vigne : stade BBCH, surface foliaire et résistance ontogénique
===============================================================================

Sert au moteur oïdium (oidium.py) : l'oïdium ne menace pas les mêmes organes aux mêmes dates.

  * STADE BBCH estimé par degrés-jours (DJC, base 10 °C, moyenne journalière, cumulés depuis le débourrement) : la méthode du
    référentiel champenois (maturité à 1 250 DJC). Les seuils intermédiaires ci-dessous sont des APPROXIMATIONS pour un cépage
    précoce de Champagne (Chardonnay) : à confronter à tes relevés de stade, puis à recaler avec `calage_table`, qui remplace les seuils
    par ceux observés et garde la table cohérente.
  * RÉSISTANCE ONTOGÉNIQUE (Gadoury et al. 2003, baies ; modèle VitiMeteo-Oidium) : les grappes sont réceptives de l'apparition des
    inflorescences à la nouaison, puis deviennent rapidement résistantes ; il reste une sensibilité résiduelle après la fermeture de
    la grappe. Le BSV Champagne place la période de risque maximale entre « 7-8 feuilles étalées » et « grains de pois ».
    Les courbes sont des HYPOTHÈSES DE TRAVAIL à valider.
  * FEUILLES : tant que la vigne pousse, des feuilles jeunes (sensibles) apparaissent sans cesse ; à l'arrêt de la croissance, la
    sensibilité moyenne baisse (feuilles qui vieillissent). Courbe douce volontairement : l'oïdium s'exprime encore sur les feuilles
    en fin d'été.
  * SURFACE FOLIAIRE relative (0 à 1) : tissu sain disponible pour les colonies.

Les stades sont des nombres de l'échelle BBCH rangés dans l'ordre du temps : 09, 11, 13, 14, 15, 17, 53, 57, 61, 65, 71, 75, 77, 79, 81,
83, 85, 89 (la croissance des feuilles puis celle des inflorescences et des baies suivent la même suite croissante).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

BASE_DJC = 10.0

# (BBCH, DJC base 10 cumulés depuis le débourrement) : approximation à recaler
TABLE_DJC = (
    (9, 0.0),      # débourrement
    (11, 15.0),    # première feuille étalée
    (13, 40.0),
    (14, 60.0),    # 4 feuilles étalées : début de prise en compte du risque (BSV Champagne)
    (15, 80.0),
    (17, 120.0),   # 7-8 feuilles étalées
    (53, 150.0),   # inflorescences visibles
    (57, 215.0),   # boutons floraux séparés
    (61, 270.0),   # début de floraison
    (65, 320.0),   # pleine floraison
    (71, 400.0),   # nouaison
    (75, 570.0),   # grains de pois
    (77, 660.0),   # baies qui se touchent
    (79, 760.0),   # fermeture de la grappe
    (81, 900.0),   # début de véraison
    (83, 1000.0),
    (85, 1080.0),
    (89, 1250.0),  # maturité : seuil champenois de 1 250 DJC
)

# (BBCH, sensibilité relative 0 à 1) : hypothèses de travail
SENSIBILITE_GRAPPES = (
    (9, 0.0), (51, 0.0), (53, 0.5), (57, 0.8), (61, 1.0), (71, 1.0),
    (73, 0.85), (75, 0.6), (77, 0.4), (79, 0.2), (81, 0.15), (85, 0.1), (89, 0.1),
)
SENSIBILITE_FEUILLES = ((9, 1.0), (79, 1.0), (81, 0.9), (85, 0.7), (89, 0.5))
SURFACE_FOLIAIRE = (
    (9, 0.01), (11, 0.04), (13, 0.10), (15, 0.20), (17, 0.30), (53, 0.40), (57, 0.55),
    (61, 0.70), (71, 0.85), (75, 0.95), (79, 1.0), (89, 1.0),
)

STADES_CLES = (
    ("débourrement", 9), ("4 feuilles étalées", 14), ("7-8 feuilles étalées", 17), ("début de floraison", 61),
    ("pleine floraison", 65), ("nouaison", 71), ("grains de pois", 75), ("fermeture de la grappe", 79),
    ("début de véraison", 81), ("maturité", 89),
)


def interpoler(table, x: float | None) -> float | None:
    """Interpolation linéaire dans une table ((abscisse, valeur), ...) triée ; bornée aux extrémités."""
    if x is None:
        return None
    if x <= table[0][0]:
        return float(table[0][1])
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x < x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return float(table[-1][1])


def bbch_depuis_djc(djc: float | None, table=TABLE_DJC) -> float | None:
    """Stade BBCH (décimal) pour un cumul de DJC : interpolation entre les seuils ; au-delà du dernier, le dernier stade."""
    if djc is None:
        return None
    seuils = [(d, b) for b, d in table]
    if djc <= seuils[0][0]:
        return float(seuils[0][1])
    for (d0, b0), (d1, b1) in zip(seuils, seuils[1:]):
        if djc < d1:
            return b0 + (b1 - b0) * (djc - d0) / (d1 - d0)       # d1 > djc >= d0 : jamais de division par zéro
    return float(seuils[-1][1])


def djc_cumules(rows: list[dict], debourrement: date, tz: ZoneInfo, base: float = BASE_DJC) -> dict:
    """{jour local: DJC cumulés depuis le débourrement, fin de journée compris} ; {} si la série ne couvre pas le débourrement.
    DJC d'un jour = max(0, température moyenne du jour − base)."""
    par_jour = {}
    for r in rows:
        if r.get("temp") is None:
            continue
        par_jour.setdefault(r["t"].astimezone(tz).date(), []).append(r["temp"])
    if not par_jour or min(par_jour) > debourrement:
        return {}
    cumul, out = 0.0, {}
    for j in sorted(par_jour):
        if j < debourrement:
            continue
        t = par_jour[j]
        cumul += max(0.0, sum(t) / len(t) - base)
        out[j] = cumul
    return out


def calage_table(table, observations_djc: dict) -> tuple:
    """Remplace les seuils par ceux observés {BBCH: DJC observés} (un stade absent de la table est inséré) et garde la table croissante :
    les stades non observés sont tirés vers les stades observés. Lève ValueError si les observations se contredisent."""
    pts = {b: d for b, d in table}
    ancres = set(observations_djc)
    pts.update(observations_djc)
    stades = sorted(pts)
    anc = sorted(ancres)
    for a1, a2 in zip(anc, anc[1:]):
        if pts[a2] < pts[a1]:
            raise ValueError(f"observations incohérentes : le stade {a2} ({pts[a2]:.0f} DJC) précède le stade {a1} ({pts[a1]:.0f} DJC)")
    vals = [pts[s] for s in stades]
    for i in range(1, len(stades)):                               # un stade non observé n'est jamais en deçà du précédent
        if stades[i] not in ancres:
            vals[i] = max(vals[i], vals[i - 1])
    for i in range(len(stades) - 2, -1, -1):                      # ni au-delà du suivant
        if stades[i] not in ancres:
            vals[i] = min(vals[i], vals[i + 1])
    return tuple(zip(stades, vals))


def serie_bbch(rows: list[dict], debourrement: date, tz: ZoneInfo, observations: dict | None = None,
               table=TABLE_DJC, base: float = BASE_DJC) -> dict:
    """{jour local: stade BBCH} depuis le débourrement ; {} si la série ne le couvre pas.
    observations : {date: BBCH observé} : les seuils de la table sont recalés sur ces observations."""
    djc = djc_cumules(rows, debourrement, tz, base)
    if not djc:
        return {}
    if observations:
        obs = {}
        for jour, stade in observations.items():
            jour = date.fromisoformat(jour) if isinstance(jour, str) else jour
            if jour not in djc:
                raise ValueError(f"observation du {jour} : hors de la série depuis le débourrement ({debourrement})")
            obs[float(stade) if float(stade) != int(stade) else int(stade)] = djc[jour]
        table = calage_table(table, obs)
    return {j: bbch_depuis_djc(v, table) for j, v in djc.items()}


def sens_grappes(bbch: float | None) -> float:
    return 0.0 if bbch is None else interpoler(SENSIBILITE_GRAPPES, bbch)


def sens_feuilles(bbch: float | None) -> float:
    return 1.0 if bbch is None else interpoler(SENSIBILITE_FEUILLES, bbch)


def surface_foliaire(bbch: float | None) -> float:
    return 0.0 if bbch is None else interpoler(SURFACE_FOLIAIRE, bbch)


def calendrier(bbch_jour: dict, stades=STADES_CLES) -> dict:
    """{nom du stade: premier jour où il est atteint (ISO)} ; None si non atteint dans la série."""
    out = {}
    for nom, cible in stades:
        jours = [j for j in sorted(bbch_jour) if bbch_jour[j] >= cible - 1e-9]
        out[nom] = jours[0].isoformat() if jours else None
    return out


def fenetre(bbch_jour: dict, fonction, seuil: float = 0.5):
    """(premier, dernier) jour où fonction(stade) >= seuil, ISO ; (None, None) si jamais."""
    jours = [j for j in sorted(bbch_jour) if fonction(bbch_jour[j]) >= seuil]
    return (jours[0].isoformat(), jours[-1].isoformat()) if jours else (None, None)
