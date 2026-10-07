#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Moteur botrytis v1 — González-Domínguez et al. 2015 (PLoS ONE 10(10): e0140444)
================================================================================

Modèle mécaniste de Botrytis cinerea sur vigne. Trois variables accumulées :

  SEV1  : sévérité relative des infections des inflorescences et jeunes grappes par les conidies
          (fenêtre 1 : BBCH 53 → 73)
  SEV2  : sévérité relative des infections des baies mûrissantes par les conidies
          (fenêtre 2 : BBCH 79 → 89)
  SEV3  : sévérité relative des infections de baie à baie par le mycélium
          (fenêtre 2 : BBCH 79 → 89)

Classification finale de l'épidémie (faible / intermédiaire / sévère) par analyse discriminante
(coefficients canoniques publiés Table 6 de l'article) sur ln(SEV1+1) et ln(SEV2+SEV3+1).

Entrées météo (journalières) :
  - T     : température moyenne journalière (°C)
  - HR    : humidité relative journalière moyenne (%)
  - pluie : précipitations journalières (mm)
  - WD    : durée de mouillure (heures) — calculée ici depuis la série horaire (HR > 90 % ou
            pluie ≥ 0,2 mm), option 2 retenue (cohérente avec le moteur mildiou)

Entrées phénologiques :
  - BBCH journalier, fourni par phenologie.py ou phenologie_brin_gfv.py

Pas de temps : 1 jour (le modèle tourne en journalier, contrairement au moteur oïdium qui est horaire).

Limites (documentées dans l'article) :
  - Validé en Italie et France (Bordeaux) ; pas de validation Champagne connue.
  - Le modèle ne prédit pas un pourcentage de pourriture, seulement un niveau épidémique.
  - 24 % de faux positifs dans la validation de Fedele et al. 2020.
  - WD calculée depuis HR/pluie est un proxy : les auteurs utilisaient des capteurs de mouillure.
  - Les infections latentes ne sont pas modélisées (information quantitative insuffisante en 2015).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Paramètres (valeurs publiées dans l'article)
# ---------------------------------------------------------------------------
PARAMS: dict = {
    # Forme des fonctions de température. L'article imprime  a × Teq^m × (1 − Teq)^n ; cette forme donne un optimum de sporulation
    # à 2,8 °C et une sporulation DÉCROISSANTE avec l'humidité, contraire à l'article source (Ciliberti et al. 2016 : optimum
    # 15-20 °C, HR > 65,5 %), et des SEV 1 000 fois plus faibles que les moyennes publiées. La forme (a × Teq^m × (1 − Teq))^n,
    # habituelle dans les modèles de l'équipe Rossi, donne un optimum de sporulation de 16,6 °C et des SEV du bon ordre de grandeur.
    #   "corrigee" (défaut) : (a × Teq^m × (1 − Teq))^n, terme d'humidité de la sporulation logistique 1 / (1 + e^(b − c·HR + d·HR²))
    #   "imprimee"          : formes de l'article telles qu'imprimées (comparaison)
    "forme": "corrigee",
    # Fin de la fenêtre 2 : premier jour au stade de vendange (stade_vendange), et au plus tard à cette date (MM-JJ). La phénologie en degrés-jours n'atteint pas
    # BBCH 89 les années fraîches ; sans borne, la fenêtre resterait ouverte jusqu'en décembre.
    "fin_fenetre2": "10-01",
    # Stade de vendange en Champagne : la récolte se fait vers BBCH 86-87 (degré potentiel modéré), pas à la pleine maturité (89).
    # La fenêtre 2 se ferme au premier jour à ce stade.
    "stade_vendange": 87.0,
    # EXTENSION VITI Sens (hors modèle publié) : l'inoculum de grappe de la fenêtre 2 dépend de la floraison (infections latentes et
    # débris floraux colonisés). La propagation de baie à baie est multipliée par SEV1 / sev1_ref (sev1_ref = 1,0 : médiane des
    # floraisons 2012-2025 à Reims, proche de la moyenne publiée des épidémies faibles, 0,94).
    "lien_floraison": {"actif": True, "sev1_ref": 1.0},
    # Mycelium growth (Eq. 2) — Ciliberti et al. 2014
    "mygr": {"tmin": 0.0, "tmax": 40.0, "a": 3.78, "m": 0.9, "n": 0.475},
    # Sporulation (Eq. 3) — Ciliberti et al. 2015
    "spor": {"tmin": 0.0, "tmax": 35.0, "a": 3.7, "m": 0.9, "n": 10.493,
             "rh_b": 3.595, "rh_c": 0.097, "rh_d": 0.0005},
    # CISO : moyenne glissante sur 7 jours
    "ciso_fenetre": 7,
    # Infection période 1 (Eq. 4) — Ciliberti et al. 2014
    "inf1": {"tmin": 0.0, "tmax": 35.0, "a": 3.56, "m": 0.99, "n": 0.71,
             "wd_a": 1.85, "wd_b": 0.19},
    # Infection période 2 — conidies sur baies mûrissantes (Eq. 7) — Ciliberti et al. 2015
    "inf2": {"tmin": 0.0, "tmax": 35.0, "a": 6.416, "m": 1.292, "n": 0.469,
             "wd_a": 2.3, "wd_b": 0.048},
    # Infection période 2 — baie à baie par mycélium (Eq. 10) — Ciliberti et al. 2015
    "inf3": {"tmin": 0.0, "tmax": 30.0, "a": 7.75, "m": 2.14, "n": 0.469,
             "rh_a": 35.364, "rh_b": 0.26},
    # Fenêtres d'infection (BBCH)
    "fenetre1": {"debut": 53, "fin": 73},
    "fenetre2": {"debut": 79, "fin": 89},
    # Sous-fenêtres de la fenêtre 2 (CHOIX VITI Sens, l'article fait courir SEV2 et SEV3 sur toute la fenêtre 79-89) :
    #   SEV2 (conidies sur baies) de la fermeture de la grappe au début de la véraison, SEV3 (baie à baie) de la véraison à la récolte.
    "fenetre_sev2": {"debut": 79, "fin": 83},
    "fenetre_sev3": {"debut": 84, "fin": 87},
    # WD proxy : seuils pour compter une heure comme « mouillée »
    "wd_proxy": {"hr_seuil": 90.0, "pluie_seuil": 0.2},
    # Coefficients de l'analyse discriminante (Table 6 de González-Domínguez et al. 2015)
    # Fonctions : Fn = an + bn1 × ln(SEV1+1) + bn2 × ln(SEV2+SEV3+1)
    "dfa": {
        "F1": {"a": -4.191, "b1": 4.180, "b2": -0.310},
        "F2": {"a": -2.090, "b1": 0.595, "b2":  3.067},
        # Centroïdes calculés depuis les valeurs moyennes SEV publiées dans les résultats
        # (faible : SEV1=0.94, SEV2=0.15, SEV3=0.34 ; intermédiaire : 2.46, 0.20, 0.37 ; sévère : 1.81, 0.28, 0.76)
        # appliquées aux fonctions discriminantes F1 et F2 de la Table 6
        "centroides": {
            "faible":        (-1.545, -0.473),
            "intermediaire": ( 0.858,  0.032),
            "severe":        (-0.093,  0.711),
        },
        # Probabilités a priori (proportions des 21 épidémies de validation)
        "prior": {"faible": 0.19, "intermediaire": 0.48, "severe": 0.33},
    },
}


# ---------------------------------------------------------------------------
# Fonctions biologiques de base
# ---------------------------------------------------------------------------

def _teq(t: float, tmin: float, tmax: float) -> float:
    """Température équivalente Teq = (T - Tmin) / (Tmax - Tmin), bornée à [0, 1]."""
    if t <= tmin or t >= tmax:
        return 0.0
    return (t - tmin) / (tmax - tmin)


def _beta(teq: float, a: float, m: float, n: float, p: dict) -> float:
    """Réponse à la température : (a·Teq^m·(1−Teq))^n (forme corrigée) ou a·Teq^m·(1−Teq)^n (forme imprimée)."""
    if p.get("forme", "corrigee") == "imprimee":
        return a * teq ** m * (1 - teq) ** n
    return (a * teq ** m * (1 - teq)) ** n


def taux_mygr(t: float, mf: float, p: dict) -> float:
    """Taux de croissance mycélienne journalier (Eq. 2). mf = fraction d'heures mouillées (0-1)."""
    pm = p["mygr"]
    teq = _teq(t, pm["tmin"], pm["tmax"])
    if teq <= 0:
        return 0.0
    return _beta(teq, pm["a"], pm["m"], pm["n"], p) * mf


def taux_spor(t: float, hr: float, p: dict) -> float:
    """Taux de sporulation journalier (Eq. 3)."""
    ps = p["spor"]
    teq = _teq(t, ps["tmin"], ps["tmax"])
    if teq <= 0:
        return 0.0
    if p.get("forme", "corrigee") == "imprimee":
        rh_term = ps["rh_b"] + ps["rh_c"] * hr - ps["rh_d"] * hr ** 2
        if rh_term <= 0:
            return 0.0
        return _beta(teq, ps["a"], ps["m"], ps["n"], p) / rh_term
    f_hr = 1.0 / (1.0 + math.exp(ps["rh_b"] - ps["rh_c"] * hr + ps["rh_d"] * hr ** 2))
    return _beta(teq, ps["a"], ps["m"], ps["n"], p) * f_hr


def ciso_jour(historique_7j: list[tuple[float, float]]) -> float:
    """CISO : abondance relative de conidies sur les sources d'inoculum (Eq. 1).
    historique_7j : liste de (mygr_j, spor_j) des 7 derniers jours (du plus ancien au plus récent)."""
    if not historique_7j:
        return 0.0
    return sum(m * s for m, s in historique_7j) / len(historique_7j)


def sus1(bbch: float) -> float:
    if bbch < 53.0 or bbch > 73.0:
        return 0.0
    """Susceptibilité relative des inflorescences et jeunes grappes (Eq. 5).
    Polynôme cubique calé sur les données de Ciliberti et al. 2014."""
    gs = bbch / 100.0
    # Note : la version PDF de l'article a un problème de signe (-671.25 imprimé) mais la biologie
    # (maximum à BBCH 65 d'après Ciliberti 2014) impose la forme -379.09*gs³ + 671.25*gs² - 390.33*gs + 75.209
    val = -379.09 * gs ** 3 + 671.25 * gs ** 2 - 390.33 * gs + 75.209
    return max(0.0, val)


def sus2(bbch: float) -> float:
    """Susceptibilité relative des baies mûrissantes aux conidies (Eq. 8).
    Exponentielle croissante de BBCH, issue de Deytieux-Belleau et al. 2009."""
    return 5e-17 * math.exp(0.4219 * bbch)


def sus3(bbch: float) -> float:
    """Susceptibilité relative des baies mûrissantes au mycélium (Eq. 11). Plafonnée à 1."""
    return min(1.0, max(0.0, 0.0546 * bbch - 3.87))


def inf1(t: float, wd: float, bbch: float, p: dict) -> float:
    """Taux d'infection des inflorescences et jeunes grappes par les conidies (Eq. 4)."""
    pi = p["inf1"]
    teq = _teq(t, pi["tmin"], pi["tmax"])
    if teq <= 0:
        return 0.0
    wd_term = 1.0 + math.exp(pi["wd_a"] - pi["wd_b"] * wd)
    return _beta(teq, pi["a"], pi["m"], pi["n"], p) / wd_term * sus1(bbch)


def inf2(t: float, wd: float, bbch: float, p: dict) -> float:
    """Taux d'infection des baies mûrissantes par les conidies (Eq. 7)."""
    pi = p["inf2"]
    teq = _teq(t, pi["tmin"], pi["tmax"])
    if teq <= 0:
        return 0.0
    wd_term = math.exp(-pi["wd_a"] * math.exp(-pi["wd_b"] * wd))
    return _beta(teq, pi["a"], pi["m"], pi["n"], p) * wd_term * sus2(bbch)


def inf3(t: float, hr: float, bbch: float, p: dict) -> float:
    """Taux d'infection de baie à baie par le mycélium (Eq. 10)."""
    pi = p["inf3"]
    teq = _teq(t, pi["tmin"], pi["tmax"])
    if teq <= 0:
        return 0.0
    rh_term = 1.0 + math.exp((pi["rh_a"] - pi["rh_b"] * hr) / 100.0)
    return _beta(teq, pi["a"], pi["m"], pi["n"], p) / rh_term * sus3(bbch)


# ---------------------------------------------------------------------------
# Durée de mouillure proxy (option 2)
# ---------------------------------------------------------------------------

def wd_depuis_horaires(rows_jour: list[dict], p: dict) -> float:
    """Durée de mouillure (heures) calculée depuis les données horaires : compte les heures où
    HR ≥ seuil_hr OU pluie ≥ seuil_pluie. Cohérent avec le moteur mildiou."""
    seuils = p["wd_proxy"]
    return sum(
        1 for r in rows_jour
        if (r.get("hr") or 0) >= seuils["hr_seuil"] or (r.get("pluie") or 0) >= seuils["pluie_seuil"]
    )


def mf_depuis_horaires(rows_jour: list[dict], p: dict) -> float:
    """Facteur d'humidité du milieu Mf = fraction d'heures mouillées (0 à 1).
    Conditions : pluie ≥ 0,2 mm OU mouillure ≥ 30 min OU HR ≥ 90 %."""
    seuils = p["wd_proxy"]
    heures_mouillees = sum(
        1 for r in rows_jour
        if (r.get("hr") or 0) >= seuils["hr_seuil"] or (r.get("pluie") or 0) >= seuils["pluie_seuil"]
    )
    n = len(rows_jour)
    return heures_mouillees / n if n else 0.0


# ---------------------------------------------------------------------------
# Classification discriminante
# ---------------------------------------------------------------------------

def _discriminer(sev1: float, sev2: float, sev3: float, p: dict) -> dict:
    """Classe l'épidémie en faible / intermédiaire / sévère par analyse discriminante
    (González-Domínguez et al. 2015, Table 6). Retourne {classe, probabilites, F1, F2}."""
    pdfa = p["dfa"]
    x1 = math.log(sev1 + 1)
    x2 = math.log(sev2 + sev3 + 1)
    f1_val = pdfa["F1"]["a"] + pdfa["F1"]["b1"] * x1 + pdfa["F1"]["b2"] * x2
    f2_val = pdfa["F2"]["a"] + pdfa["F2"]["b1"] * x1 + pdfa["F2"]["b2"] * x2
    # Distance de Mahalanobis simplifiée (variance unitaire : pas de matrice de covariance publiée)
    # → distance euclidienne dans l'espace F1-F2 pondérée par les probabilités a priori
    scores = {}
    for groupe, (cf1, cf2) in pdfa["centroides"].items():
        dist2 = (f1_val - cf1) ** 2 + (f2_val - cf2) ** 2
        scores[groupe] = pdfa["prior"][groupe] * math.exp(-0.5 * dist2)
    total = sum(scores.values()) or 1.0
    proba = {k: round(v / total, 4) for k, v in scores.items()}
    classe = max(proba, key=proba.get)
    return {"classe": classe, "probabilites": proba, "F1": round(f1_val, 4), "F2": round(f2_val, 4)}


# ---------------------------------------------------------------------------
# Moteur principal
# ---------------------------------------------------------------------------

def calculer_saison(
    rows: list[dict],
    bbch_jour: dict[date, float],
    tz: ZoneInfo,
    params: dict | None = None,
) -> dict:
    """Calcule SEV1, SEV2, SEV3 et la classification de l'épidémie pour une saison.

    rows        : série horaire avec {t (datetime UTC), temp, hr, pluie}
    bbch_jour   : {date: BBCH} — issu de phenologie.py ou phenologie_brin_gfv.py
    tz          : fuseau horaire local (pour regrouper les heures en journées locales)
    params      : surcharge des paramètres (PARAMS est la référence)

    Retourne :
      {sev1, sev2, sev3, classification, jours, avertissements}
    """
    p = _fusion(PARAMS, params)
    avert = []

    # Regroupement horaire → journalier
    par_jour: dict[date, list[dict]] = {}
    for r in rows:
        if r.get("temp") is None:
            continue
        j = r["t"].astimezone(tz).date()
        par_jour.setdefault(j, []).append(r)

    if not par_jour:
        return {"sev1": 0.0, "sev2": 0.0, "sev3": 0.0,
                "classification": None, "jours": [], "avertissements": ["série météo vide"]}

    # Vérification de la phénologie
    if not bbch_jour:
        avert.append("phénologie absente : les fenêtres d'infection ne peuvent pas être déterminées")

    f1_debut = p["fenetre1"]["debut"]
    f1_fin   = p["fenetre1"]["fin"]
    f2_debut = p["fenetre2"]["debut"]
    f2_fin   = p["fenetre2"]["fin"]

    j89 = next((j for j in sorted(bbch_jour) if bbch_jour[j] is not None and bbch_jour[j] >= p["stade_vendange"]), None)
    jours_dispo = sorted(par_jour)
    annee = jours_dispo[-1].year if jours_dispo else None
    mm, jj = (int(x) for x in p["fin_fenetre2"].split("-"))
    fin_f2 = date(annee, mm, jj) if annee else None
    if j89 is not None and fin_f2 is not None:
        fin_f2 = min(fin_f2, j89)
    lien = p.get("lien_floraison") or {}
    sev1 = sev2 = sev3 = 0.0
    historique_mygr_spor: list[tuple[float, float]] = []
    jours_out = []

    for j in sorted(par_jour):
        rows_j = par_jour[j]
        t_j    = sum(r["temp"] for r in rows_j) / len(rows_j)
        hr_j   = sum((r.get("hr") or 0) for r in rows_j) / len(rows_j)
        wd_j   = wd_depuis_horaires(rows_j, p)
        mf_j   = mf_depuis_horaires(rows_j, p)
        bbch_j = bbch_jour.get(j)

        # Production d'inoculum (toute la saison)
        mg = taux_mygr(t_j, mf_j, p)
        sp = taux_spor(t_j, hr_j, p)
        historique_mygr_spor.append((mg, sp))
        if len(historique_mygr_spor) > p["ciso_fenetre"]:
            historique_mygr_spor.pop(0)
        ciso = ciso_jour(historique_mygr_spor)

        ris1 = ris2 = ris3 = 0.0
        in_f1 = in_f2 = False

        if bbch_j is not None:
            in_f1 = f1_debut <= bbch_j <= f1_fin
            in_f2 = f2_debut <= bbch_j <= f2_fin and (fin_f2 is None or j <= fin_f2)

            if in_f1:
                ris1 = ciso * inf1(t_j, wd_j, bbch_j, p)
                sev1 += ris1
            if in_f2:
                fs2, fs3 = p["fenetre_sev2"], p["fenetre_sev3"]
                if fs2["debut"] <= bbch_j <= fs2["fin"]:
                    ris2 = ciso * inf2(t_j, wd_j, bbch_j, p)
                    sev2 += ris2
                if fs3["debut"] <= bbch_j <= fs3["fin"]:
                    ris3 = inf3(t_j, hr_j, bbch_j, p) * mg
                    if lien.get("actif"):
                        ris3 *= sev1 / lien["sev1_ref"]                   # inoculum de grappe issu de la floraison
                    sev3 += ris3

        jours_out.append({
            "date":   j.isoformat(),
            "tmoy":   round(t_j, 1),
            "hr":     round(hr_j, 1),
            "wd":     round(wd_j, 1),
            "mf":     round(mf_j, 3),
            "bbch":   bbch_j,
            "ciso":   round(ciso, 4),
            "ris1":   round(ris1, 6),
            "ris2":   round(ris2, 6),
            "ris3":   round(ris3, 6),
            "sev1":   round(sev1, 4),
            "sev2":   round(sev2, 4),
            "sev3":   round(sev3, 4),
            "fenetre": "1" if in_f1 else ("2" if in_f2 else "-"),
        })

    classif = _discriminer(sev1, sev2, sev3, p)

    # Avertissements sur les fenêtres
    bbch_vals = [v for v in bbch_jour.values() if v is not None]
    if bbch_vals:
        if max(bbch_vals) < f1_debut:
            avert.append(f"la série ne couvre pas encore BBCH {f1_debut} : SEV1 = 0")
        elif max(bbch_vals) < f2_debut:
            avert.append(f"la série ne couvre pas encore BBCH {f2_debut} : SEV2 et SEV3 = 0")

    return {
        "sev1": round(sev1, 4),
        "sev2": round(sev2, 4),
        "sev3": round(sev3, 4),
        "classification": classif,
        "jours": jours_out,
        "avertissements": avert,
    }


def _fusion(base: dict, sur: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (sur or {}).items():
        out[k] = {**out.get(k, {}), **v} if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


# ---------------------------------------------------------------------------
# Résumé lisible
# ---------------------------------------------------------------------------

def resume(res: dict) -> str:
    """Résumé textuel de la simulation botrytis."""
    cl = res["classification"]
    if cl is None:
        return "BOTRYTIS : calcul impossible (phénologie absente ou série vide)"
    niveaux = {"faible": "FAIBLE", "intermediaire": "INTERMÉDIAIRE", "severe": "SÉVÈRE"}
    n = niveaux.get(cl["classe"], cl["classe"].upper())
    L = [
        f"BOTRYTIS — épidémie prévue : {n}",
        f"  SEV1 (inflorescences/jeunes grappes) : {res['sev1']:.3f}",
        f"  SEV2 (baies mûrissantes, conidies)   : {res['sev2']:.3f}",
        f"  SEV3 (baie à baie, mycélium)          : {res['sev3']:.3f}",
        f"  Probabilités : faible {cl['probabilites']['faible']:.0%}  "
        f"intermédiaire {cl['probabilites']['intermediaire']:.0%}  "
        f"sévère {cl['probabilites']['severe']:.0%}",
        f"  (F1={cl['F1']:.2f}, F2={cl['F2']:.2f})",
    ]
    if res["avertissements"]:
        for a in res["avertissements"]:
            L.append(f"  ! {a}")
    return "\n".join(L)
