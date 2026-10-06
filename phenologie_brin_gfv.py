#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phénologie en chaîne : BRIN (débourrement) -> croissance végétative (feuilles) -> GFV (floraison, véraison)
==========================================================================================================

Un seul modèle ne suit pas toute la saison : on enchaîne trois modèles publiés, chacun là où il est le plus fiable, et on exprime le
résultat sur l'échelle BBCH.

  1. DÉBOURREMENT, modèle BRIN (García de Cortázar-Atauri, Brisson et Gaudillère 2009, Int. J. Biometeorol. 53:317-326)
       * dormance (froid de Bidabé) : chaque jour, Q10^(-Tmax/10) + Q10^(-Tmin/10), cumulé depuis le 1er août de l'année précédente jusqu'à
         l'exigence en froid C_crit ; Q10 = 2,17 ;
       * forçage (Richardson), au pas horaire : max(min(T - T_bas, T_haut - T_bas), 0) en °C·h, avec T_bas = 5 °C et T_haut = 25 °C, cumulé
         après la levée de dormance jusqu'à l'exigence en chaleur F_crit ;
       * Chardonnay : C_crit = 101,2 et F_crit = 6 576,7 °C·h (table de García de Cortázar-Atauri 2006 et 2009).
     Ses auteurs montrent que la température de base pèse plus que la dormance : sans l'automne précédent dans la série (cas du CSV
     qui commence au 1er janvier), la dormance est SUPPOSÉE LEVÉE au premier jour et seul le forçage est calculé.
     Le débourrement OBSERVÉ prime toujours sur le calculé ; l'écart est rapporté.

  2. CROISSANCE VÉGÉTATIVE, du débourrement (BBCH 09) à 9 feuilles étalées (BBCH 19) : émission des feuilles linéaire en temps thermique
     (Lebon, Pellegrino, Tardieu et Lecoeur 2004, Annals of Botany 93:263-274) : n feuilles = temps thermique base 10 °C / phyllochrone, avec un
     phyllochrone d'environ 24 °C·j. La surface foliaire (SFE) s'en déduit : voir phenologie.SURFACE_FOLIAIRE.
     BBCH : 09 au débourrement, 11 à la première feuille étalée, puis 10 + n feuilles, jusqu'à 19 pour 9 feuilles.

  3. À PARTIR DE 9 FEUILLES, modèle GFV (Parker, García de Cortázar-Atauri, van Leeuwen et Chuine 2011, Aust. J. Grape Wine Res. 17:206-216) :
     somme des températures moyennes journalières ((Tmin + Tmax) / 2) à base 0 °C depuis le 1er mars ; Chardonnay : floraison à
     F* = 1 217 et véraison à V* = 2 547. Le stade BBCH s'interpole entre ces repères : 19 à 9 feuilles, 65 (pleine floraison) à F*, 83 à V*.
     Les stades intermédiaires (53, 57, 61, 71, 75, 77, 79, 81) sont placés par FRACTIONS de l'intervalle : des approximations, à recaler sur
     des observations.

Les observations de stade recalent le modèle : avec au moins 3 stades de feuilles (BBCH 11 à 19), la BASE THERMIQUE et le phyllochrone sont ajustés
(grille de bases, phyllochrone par moindres carrés) ; avec moins, seul le phyllochrone l'est. Un stade à partir de 53 remplace le repère GFV
correspondant (65 -> F*, 83 -> V*) ; les stades non observés situés entre deux stades observés gardent leur position relative, comprimée entre
eux. Un stade observé incompatible avec l'ordre des stades lève ValueError.

Limites : paramètres du Chardonnay, hors cépage ; la table C_crit / F_crit a été calée avec des températures horaires reconstituées à partir
de Tmin et Tmax, pas sur des mesures horaires ; après la véraison, le stade reste à 83 (l'oïdium n'y a plus d'enjeu pour les grappes).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import phenologie as phen

PARAMS = {
    "brin": {"q10": 2.17, "t_bas": 5.0, "t_haut": 25.0, "c_crit": 101.2, "f_crit": 6576.7},
    # base thermique et phyllochrone : valeurs de la littérature (Lebon et al. 2004). Avec au moins `min_observations` stades de feuilles observés,
    # la base (grille ci-dessous) ET le phyllochrone sont AJUSTÉS aux observations : la base de 10 °C ne convient pas toujours à un printemps frais.
    "feuilles": {"t_base": 10.0, "phyllochron": 24.0, "feuilles_bbch_19": 9, "ajuster_t_base": True, "min_observations": 3,
                 "t_base_grille": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
    "gfv": {"t_base": 0.0, "debut_mois": 3, "debut_jour": 1, "f_star": 1217.0, "v_star": 2547.0},
    "ecart_minimal_floraison": 50.0,       # somme GFV minimale entre « 9 feuilles » et la floraison (garde-fou)
    # position des stades intermédiaires dans l'intervalle (19 -> 65) puis (65 -> 83), en fraction : approximations
    # Fractions de l'intervalle 19 -> 65 (avant floraison) et 65 -> 89 (après) : recalculées sur
    # les stades BSV 2026 Champagne (sommes GFV exactes). Les stades 57, 61 (avant floraison) et 83
    # (entre véraison et maturité, non directement observés) gardent leurs positions approchées.
    "fractions_avant_floraison": {53: 0.212, 57: 0.475, 61: 0.75},
    # Après floraison : fractions BEAUCOUP plus tassées qu'une distribution linéaire ; la nouaison (71)
    # arrive très vite après la floraison (3 % de l'intervalle), et la fermeture (79) à mi-chemin (48 %).
    "fractions_apres_floraison": {71: 0.033, 75: 0.166, 77: 0.276, 79: 0.477, 81: 0.697, 83: 0.779, 85: 0.861},
}


def _fusion(base: dict, sur: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (sur or {}).items():
        out[k] = {**out[k], **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------------------------------------------------------------------------
# 1. BRIN
# ---------------------------------------------------------------------------
def froid_jour(tmax: float, tmin: float, q10: float) -> float:
    """Unités de froid d'un jour (froid de Bidabé) : Q10^(-Tmax/10) + Q10^(-Tmin/10)."""
    return q10 ** (-tmax / 10.0) + q10 ** (-tmin / 10.0)


def richardson_h(t: float, t_bas: float, t_haut: float) -> float:
    """Unités de forçage d'une heure (Richardson) : °C·h entre t_bas et t_haut, plafonnées au-delà."""
    return max(min(t - t_bas, t_haut - t_bas), 0.0)


def jours_locaux(rows: list[dict], tz: ZoneInfo) -> dict:
    out = {}
    for r in rows:
        if r.get("temp") is not None:
            out.setdefault(r["t"].astimezone(tz).date(), []).append(r["temp"])
    return out


def brin(rows: list[dict], tz: ZoneInfo, annee: int, params: dict | None = None) -> dict:
    """Date de débourrement par BRIN. Retourne {debourrement, dormance, dormance_levee, froid, gdh}.
    dormance : 'calculee' (la série contient l'été précédent), 'supposee_levee' (série trop courte), 'non_levee' (exigence en froid jamais atteinte)."""
    p = _fusion(PARAMS, params)["brin"]
    rows = sorted((r for r in rows if r.get("temp") is not None), key=lambda r: r["t"])
    jours = jours_locaux(rows, tz)
    if not jours:
        return {"debourrement": None, "dormance": "supposee_levee", "dormance_levee": None, "froid": 0.0, "gdh": 0.0}
    premier = min(jours)
    debut_froid = date(annee - 1, 8, 1)
    froid_cumul, jour_levee = 0.0, None
    if premier <= date(annee - 1, 8, 31):                      # la série contient (presque) tout l'été précédent : dormance calculée
        dormance = "calculee"
        for j in sorted(jours):
            if j < debut_froid:
                continue
            froid_cumul += froid_jour(max(jours[j]), min(jours[j]), p["q10"])
            if froid_cumul >= p["c_crit"]:
                jour_levee = j
                break
        if jour_levee is None:
            return {"debourrement": None, "dormance": "non_levee", "dormance_levee": None, "froid": froid_cumul, "gdh": 0.0}
        debut_forcage = jour_levee + timedelta(days=1)
    else:
        dormance, debut_forcage = "supposee_levee", premier
    gdh, deb = 0.0, None
    for r in rows:
        j = r["t"].astimezone(tz).date()
        if j < debut_forcage:
            continue
        gdh += richardson_h(r["temp"], p["t_bas"], p["t_haut"])
        if gdh >= p["f_crit"]:
            deb = j
            break
    return {"debourrement": deb, "dormance": dormance, "dormance_levee": jour_levee, "froid": froid_cumul, "gdh": gdh}


# ---------------------------------------------------------------------------
# 2. Croissance végétative
# ---------------------------------------------------------------------------
def bbch_vegetatif(n_feuilles: float, n_max: int = 9) -> float:
    """BBCH selon le nombre de feuilles étalées : 09 au débourrement, 11 à la première feuille, 10 + n ensuite, plafonné à 10 + n_max."""
    if n_feuilles <= 0:
        return 9.0
    if n_feuilles < 1:
        return 9.0 + 2.0 * n_feuilles
    return 10.0 + min(n_feuilles, float(n_max))


def temps_thermique_feuilles(rows: list[dict], debourrement: date, tz: ZoneInfo, t_base: float) -> dict:
    """{jour local: temps thermique base t_base (°C·j) cumulé depuis le débourrement, fin de journée compris}."""
    horaires = {}
    for r in rows:
        if r.get("temp") is None:
            continue
        j = r["t"].astimezone(tz).date()
        if j >= debourrement:
            horaires[j] = horaires.get(j, 0.0) + max(0.0, r["temp"] - t_base) / 24.0
    cumul, out = 0.0, {}
    for j in sorted(horaires):
        cumul += horaires[j]
        out[j] = cumul
    return out


def ajuster_feuilles(rows: list[dict], debourrement: date, tz: ZoneInfo, n_observes: dict, grille) -> dict:
    """Ajuste base thermique et phyllochrone aux feuilles observées {jour: nombre de feuilles}. Pour chaque base de la grille, le phyllochrone
    est celui des moindres carrés (n = temps thermique / phyllochrone) ; on garde la base d'erreur minimale (à erreur égale, la plus haute)."""
    meilleur = None
    for tb in grille:
        tt = temps_thermique_feuilles(rows, debourrement, tz, float(tb))
        xs = [tt[j] for j in n_observes]
        sxx = sum(x * x for x in xs)
        if sxx <= 1e-9:
            continue
        k = sum(x * n for x, n in zip(xs, n_observes.values())) / sxx
        rmse = (sum((k * x - n) ** 2 for x, n in zip(xs, n_observes.values())) / len(xs)) ** 0.5
        cle = (round(rmse, 9), -float(tb))
        if meilleur is None or cle < meilleur[0]:
            meilleur = (cle, float(tb), 1.0 / k, rmse, tt)
    if meilleur is None:
        return {}
    return {"t_base": meilleur[1], "phyllochron": meilleur[2], "rmse": meilleur[3], "tt": meilleur[4]}


# ---------------------------------------------------------------------------
# 3. GFV
# ---------------------------------------------------------------------------
def somme_gfv(rows: list[dict], tz: ZoneInfo, annee: int, params: dict | None = None) -> dict:
    """{jour local: somme GFV cumulée (°C·j) depuis le 1er mars, fin de journée comprise}. Température moyenne du jour = (Tmin + Tmax) / 2."""
    g = _fusion(PARAMS, params)["gfv"]
    debut = date(annee, g["debut_mois"], g["debut_jour"])
    jours = jours_locaux(rows, tz)
    cumul, out = 0.0, {}
    for j in sorted(jours):
        if j < debut:
            continue
        cumul += max((max(jours[j]) + min(jours[j])) / 2.0 - g["t_base"], 0.0)
        out[j] = cumul
    return out


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------
def _jour(x) -> date:
    return date.fromisoformat(x) if isinstance(x, str) else (x.date() if isinstance(x, datetime) else x)


def table_s(s19: float, f_star: float, v_star: float, p: dict) -> tuple:
    """Table (BBCH, somme GFV) de 9 feuilles à la véraison : repères 19, 65, 83 et stades intermédiaires par fractions."""
    t = {19: s19, 65: f_star, 83: v_star}
    for b, fr in p["fractions_avant_floraison"].items():
        t[int(b)] = s19 + fr * (f_star - s19)
    for b, fr in p["fractions_apres_floraison"].items():
        t[int(b)] = f_star + fr * (v_star - f_star)
    return tuple(sorted(t.items()))


def serie_bbch_brin_gfv(rows: list[dict], tz: ZoneInfo, annee: int, params: dict | None = None, debourrement: date | None = None,
                        observations: dict | None = None) -> dict:
    """Stade BBCH de chaque jour, du débourrement à la véraison.
    debourrement : débourrement OBSERVÉ (prime sur BRIN) ; observations : {date: BBCH} (voir l'en-tête du module).
    Retourne {actif, bbch, debourrement, debourrement_brin, ecart_brin_j, dormance, neuf_feuilles, phyllochron, seuils_gfv, calendrier, avertissements}."""
    p = _fusion(PARAMS, params)
    avert = []
    rows = sorted((r for r in rows if r.get("temp") is not None), key=lambda r: r["t"])
    vide = {"actif": False, "bbch": {}, "debourrement": None, "debourrement_brin": None, "ecart_brin_j": None, "dormance": None,
            "neuf_feuilles": None, "phyllochron": p["feuilles"]["phyllochron"], "seuils_gfv": {}, "calendrier": {}, "avertissements": avert}
    if not rows:
        avert.append("BRIN + GFV indisponible : série météo vide")
        return vide

    # --- 1. débourrement ---
    b = brin(rows, tz, annee, params)
    obs = {}
    for k, v in (observations or {}).items():
        v = float(v)
        obs[_jour(k)] = int(v) if v == int(v) else v
    if debourrement is None:
        deb9 = [j for j, s in obs.items() if s == 9]
        debourrement = deb9[0] if deb9 else None
    deb = debourrement or b["debourrement"]
    ecart = (b["debourrement"] - debourrement).days if (debourrement and b["debourrement"]) else None
    out = {**vide, "debourrement": deb, "debourrement_brin": b["debourrement"], "ecart_brin_j": ecart, "dormance": b["dormance"],
           "dormance_levee": b["dormance_levee"]}
    if b["dormance"] == "supposee_levee":
        avert.append("dormance supposée levée au premier jour de la série (l'été et l'automne précédents n'y figurent pas) : "
                     "BRIN ne calcule que le forçage")
    if b["dormance"] == "non_levee" and debourrement is None:
        avert.append("BRIN : l'exigence en froid n'est pas atteinte, la dormance n'est pas levée")
    if deb is None:
        avert.append("BRIN + GFV indisponible : pas de débourrement (observé ni calculé) dans la série")
        return out
    if min(jours_locaux(rows, tz)) > deb:
        avert.append(f"BRIN + GFV indisponible : la série commence après le débourrement ({deb})")
        return {**out, "debourrement": deb}

    # --- 2. croissance végétative ---
    tt = temps_thermique_feuilles(rows, deb, tz, p["feuilles"]["t_base"])
    n_max = int(p["feuilles"]["feuilles_bbch_19"])
    phyllochron = float(p["feuilles"]["phyllochron"])
    obs_feuilles = {j: s for j, s in obs.items() if 10 < s <= 19}
    if obs_feuilles:
        estimations = []
        for j, s in obs_feuilles.items():
            if j not in tt:
                raise ValueError(f"observation du {j} : hors de la série depuis le débourrement ({deb})")
            estimations.append(tt[j] / (s - 10.0))
        phyllochron = sum(estimations) / len(estimations)
    out["t_base_feuilles"], out["rmse_feuilles"] = float(p["feuilles"]["t_base"]), None
    pf_ = p["feuilles"]
    if obs_feuilles and pf_["ajuster_t_base"] and len(obs_feuilles) >= pf_["min_observations"]:
        ajuste = ajuster_feuilles(rows, deb, tz, {j: s - 10.0 for j, s in obs_feuilles.items()}, pf_["t_base_grille"])
        if ajuste:
            tt, phyllochron = ajuste["tt"], ajuste["phyllochron"]
            out["t_base_feuilles"], out["rmse_feuilles"] = ajuste["t_base"], ajuste["rmse"]
    out["phyllochron"] = phyllochron
    n_feuilles = {j: v / phyllochron for j, v in tt.items()}
    neuf = next((j for j in sorted(n_feuilles) if n_feuilles[j] >= n_max), None)
    out["neuf_feuilles"] = neuf

    # --- 3. GFV à partir de 9 feuilles ---
    gfv = p["gfv"]
    s_jour = somme_gfv(rows, tz, annee, params)
    debut_gfv = date(annee, gfv["debut_mois"], gfv["debut_jour"])
    if min(jours_locaux(rows, tz)) > debut_gfv:
        avert.append(f"somme GFV sous-estimée : la série commence le {min(jours_locaux(rows, tz))}, après le {debut_gfv}")
    bbch = {j: bbch_vegetatif(n, n_max) for j, n in n_feuilles.items() if neuf is None or j < neuf}
    seuils = {}
    reproductives = {j: s for j, s in obs.items() if s >= 53}
    if neuf is None and reproductives:
        j, s = sorted(reproductives.items())[0]
        raise ValueError(f"observation du stade {s} le {j} : incohérente avec le modèle, qui n'atteint pas 9 feuilles dans la série "
                         f"(phyllochrone {phyllochron:.1f} °C·j)")
    if neuf is not None:
        if neuf < debut_gfv:                                        # 9 feuilles avant le 1er mars : la somme GFV n'a pas commencé
            s_jour = {neuf: 0.0, **s_jour}
        if neuf not in s_jour:
            avert.append("BRIN + GFV : somme GFV indisponible au moment des 9 feuilles (la série ne contient pas le 1er mars) : "
                         "le stade reste au stade végétatif")
            bbch.update({j: bbch_vegetatif(n, n_max) for j, n in n_feuilles.items() if j >= neuf})
        else:
            s19 = s_jour[neuf]
            f_star, v_star = float(gfv["f_star"]), float(gfv["v_star"])
            if f_star < s19 + p["ecart_minimal_floraison"]:
                avert.append(f"les 9 feuilles surviennent presque à la floraison (somme GFV {s19:.0f} contre F* = {f_star:.0f}) : "
                             f"floraison repoussée à {s19 + p['ecart_minimal_floraison']:.0f}")
                f_star = s19 + p["ecart_minimal_floraison"]
            ancres = {19: s19}
            for j, s in obs.items():
                if s >= 53:
                    if j not in s_jour:
                        raise ValueError(f"observation du {j} : hors de la série depuis le 1er mars")
                    ancres[s] = s_jour[j]
                elif not (s == 9 or 10 < s <= 19):
                    raise ValueError(f"stade observé {s} le {j} : non géré (stades acceptés : 9, 11 à 19, puis 53 à 83)")
            table = phen.calage_table(table_s(s19, f_star, v_star, p), ancres)
            seuils = {"s19": s19, "f_star": dict(table)[65], "v_star": dict(table)[83]}
            for j in sorted(s_jour):
                if j >= neuf:
                    bbch[j] = phen.bbch_depuis_djc(s_jour[j], table)
    out.update({"actif": True, "bbch": bbch, "seuils_gfv": seuils, "calendrier": phen.calendrier(bbch)})
    return out
