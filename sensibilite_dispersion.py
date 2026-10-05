#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sensibilité du critère de dispersion — rejoue la saison avec plusieurs critères
===============================================================================

La dispersion exige « pluie > seuil » : sur la pluie HORAIRE d'une réanalyse, les
pointes sont lissées et 3 mm/h est presque inatteignable. Ce script compare, sur
la même météo, des cumuls glissants plus larges (fenêtre en heures) et plusieurs
seuils, pour que le critère se choisisse sur des chiffres.

Avec une observation de terrain (--obs-debut / --obs-fin : période où les premières taches
d'huile ont été vues), chaque critère est jugé sur le RAPPEL uniquement :
    taches dans l'obs. : taches prédites dans la période observée (± tolérance)
    écart (j)          : jours entre le début observé et la tache prédite la plus proche (< 0 : trop tôt)
    compatible         : oui si au moins une tache est prédite dans la période observée

Informations seulement — ce ne sont PAS des fausses alertes : l'absence de taches peut venir de la
protection phytosanitaire (ou d'une infection sans symptôme), pas d'une absence d'infection.
    avant / après      : taches prédites hors de la période observée (jusqu'à --obs-jusqu-a)
    spor. proche       : parmi les taches de la période, celles qui sporulent dans les N jours

Usage :
    python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96
    python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96 \\
        --obs-debut 2026-05-20 --obs-fin 2026-06-06 --obs-jusqu-a 2026-08-31

    # 2e étape : une fois le critère de dispersion choisi, teste l'humectation
    python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96 \\
        --obs-debut 2026-05-20 --obs-fin 2026-06-06 --obs-jusqu-a 2026-08-31 --humectation --fenetre 6 --seuil 5
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import mildiou_primaire as mp

FENETRES = (1, 3, 6, 24)
SEUILS = (3.0, 5.0, 10.0)


def _jour(s: str) -> date:
    return date.fromisoformat(s[:10])


def juger(cycles, annee, obs):
    """Compare les taches prédites à l'observation de terrain."""
    tol = timedelta(days=obs.get("tolerance_j", 3))
    d0, d1 = _jour(obs["debut"]) - tol, _jour(obs["fin"]) + tol
    fin_suivi = _jour(obs["jusqu_a"]) if obs.get("jusqu_a") else date(int(annee), 8, 31)
    spor_max = obs.get("spor_max_j", 10)
    dans = avant = apres = spor = 0
    premiere, ecart = None, None
    debut_obs = _jour(obs["debut"])
    for c in cycles:
        if "taches" not in c:
            continue
        t = c["taches"]["t"]
        premiere = t if premiere is None or t < premiere else premiere
        j = _jour(t)
        e = (j - debut_obs).days
        if ecart is None or abs(e) < abs(ecart):
            ecart = e
        if j < d0:
            avant += 1
        elif j <= d1:
            dans += 1
            if "sporulation" in c and (_jour(c["sporulation"]["t"]) - j).days <= spor_max:
                spor += 1
        elif j <= fin_suivi:
            apres += 1
    # Rappel seulement : sans carte de protection, « avant/après » ne prouvent rien (une infection
    # prédite peut avoir été couverte par un traitement ; une absence de taches n'est pas une absence d'infection).
    return {"premiere_tache": premiere, "ecart_j": ecart, "taches_obs": dans, "taches_avant": avant,
            "taches_apres": apres, "spor_proche": spor, "compatible": dans >= 1}


def _ligne(res, obs):
    cycles = res["cycles"]
    disp = [c for c in cycles if "dispersion" in c]
    inf = [c for c in cycles if "infection" in c]
    annee = res["jours"][0]["date"][:4]
    ligne = {
        "cycles": len(cycles),
        "avec_dispersion": len(disp),
        "avant_juillet": sum(1 for c in disp if c["dispersion"]["t"] < f"{annee}-07-01"),
        "avec_infection": len(inf),
        "premiere_dispersion": min((c["dispersion"]["t"] for c in disp), default=None),
        "premiere_infection": min((c["infection"]["t"] for c in inf), default=None),
    }
    if obs:
        ligne.update(juger(cycles, annee, obs))
    return ligne


def grille(rows, lat, lon, fenetres=FENETRES, seuils=SEUILS, params=None, now=None, obs=None):
    """Une ligne par couple (fenêtre, seuil). Les autres paramètres restent ceux de `params`."""
    sortie = []
    for f in fenetres:
        for s in seuils:
            p = mp.fusionner(params or {}, {"dispersion": {"fenetre_h": f, "pluie_mm": s}})
            ligne = _ligne(mp.calculer_saison(rows, lat, lon, params=p, now=now), obs)
            sortie.append({"fenetre_h": f, "seuil_mm": s, **ligne})
    return sortie


TOLERANCES = (None, 0, 2, 4)
MINIMUMS = (None, 6)
HR_PCT = (85.0, 90.0, 93.0)


def grille_humectation(rows, lat, lon, dispersion, tolerances=TOLERANCES, minimums=MINIMUMS,
                       hr_pcts=HR_PCT, params=None, now=None, obs=None):
    """Teste l'humectation (tolérance d'interruption, durée minimale continue, seuil d'HR)
    pour un critère de dispersion choisi : dispersion = (fenêtre_h, seuil_mm)."""
    sortie = []
    for tol in tolerances:
        for mini in minimums:
            for hr in hr_pcts:
                p = mp.fusionner(params or {}, {
                    "dispersion": {"fenetre_h": dispersion[0], "pluie_mm": dispersion[1]},
                    "humectation": {"tolerance_h": tol, "hr_pct": hr},
                    "infection": {"mouillage_min_h": mini}})
                ligne = _ligne(mp.calculer_saison(rows, lat, lon, params=p, now=now), obs)
                sortie.append({"tolerance_h": tol, "minimum_h": mini, "hr_pct": hr, **ligne})
    return sortie


def afficher(lignes) -> str:
    obs = bool(lignes) and "compatible" in lignes[0]
    if obs:
        L = ["fenêtre  seuil   avec disp.  avec infect.  1re tache         dans l'obs.  écart (j)  compatible  | info : avant  après  spor.",
             "-" * 118]
        for r in lignes:
            ec = "—" if r["ecart_j"] is None else f"{r['ecart_j']:+d}"
            L.append(f"{r['fenetre_h']:>5} h  {r['seuil_mm']:>4} mm  {r['avec_dispersion']:>10}  {r['avec_infection']:>12}  "
                     f"{(r['premiere_tache'] or '—')[:16]:<16}  {r['taches_obs']:>11}  {ec:>9}  "
                     f"{'OUI' if r['compatible'] else 'non':>10}  | {r['taches_avant']:>13}  {r['taches_apres']:>5}  {r['spor_proche']:>5}")
        L += ["", "compatible = au moins une tache prédite dans la période observée (rappel).",
              "avant / après / spor. : informations seulement — la protection phytosanitaire peut masquer des infections."]
    else:
        L = ["fenêtre  seuil   cycles  avec disp.  avant juillet  avec infection  1re dispersion       1re infection",
             "-" * 104]
        for r in lignes:
            L.append(f"{r['fenetre_h']:>5} h  {r['seuil_mm']:>4} mm  {r['cycles']:>6}  {r['avec_dispersion']:>10}  "
                     f"{r['avant_juillet']:>13}  {r['avec_infection']:>14}  "
                     f"{(r['premiere_dispersion'] or '—'):<19}  {(r['premiere_infection'] or '—')}")
    L.append("")
    L.append("La ligne « 1 h / 3 mm » est ton critère actuel (> 3 mm/h).")
    return "\n".join(L)


FORMULES = (True, False)        # True : DH = T - 8 ; False : DH = T x h (T > 8 °C)
TOLERANCES_INF = (None, 0, 1, 2)


def grille_infection(rows, lat, lon, dispersion, formules=FORMULES, tolerances=TOLERANCES_INF,
                     minimums=MINIMUMS, params=None, now=None, obs=None):
    """Compare la formule d'infection (T - 8 contre T x h) avec la tolérance d'interruption sèche et
    la durée minimale, pour un critère de dispersion choisi : dispersion = (fenêtre_h, seuil_mm)."""
    sortie = []
    for soustraire in formules:
        for tol in tolerances:
            for mini in minimums:
                p = mp.fusionner(params or {}, {
                    "dispersion": {"fenetre_h": dispersion[0], "pluie_mm": dispersion[1]},
                    "humectation": {"tolerance_h": tol},
                    "infection": {"mouillage_min_h": mini, "soustraire_base": soustraire}})
                ligne = _ligne(mp.calculer_saison(rows, lat, lon, params=p, now=now), obs)
                sortie.append({"formule": "T-8" if soustraire else "T×h", "tolerance_h": tol,
                               "minimum_h": mini, **ligne})
    return sortie


def afficher_humectation(lignes) -> str:
    juge = bool(lignes) and "compatible" in lignes[0]
    formule = bool(lignes) and "formule" in lignes[0]
    avec_hr = bool(lignes) and "hr_pct" in lignes[0]
    pre = ("formule  " if formule else "") + "tolérance  minimum  " + ("  HR    " if avec_hr else "")
    if juge:
        L = [pre + "avec infect.  1re tache         dans l'obs.  écart (j)  compatible  | info : avant  après  spor.",
             "-" * 124]
    else:
        L = [pre + "cycles  avec disp.  avec infect.  1re infection", "-" * 90]
    for r in lignes:
        tol = "libre" if r["tolerance_h"] is None else f"{r['tolerance_h']} h"
        mini = "—" if r["minimum_h"] is None else f"{r['minimum_h']} h"
        debut = (f"{r['formule']:>7}  " if formule else "") + f"{tol:>9}  {mini:>7}  " + \
                (f"{r['hr_pct']:>4.0f}%  " if avec_hr else "")
        if juge:
            ec = "—" if r["ecart_j"] is None else f"{r['ecart_j']:+d}"
            L.append(debut + f"{r['avec_infection']:>12}  {(r['premiere_tache'] or '—')[:16]:<16}  "
                     f"{r['taches_obs']:>11}  {ec:>9}  {'OUI' if r['compatible'] else 'non':>10}  | "
                     f"{r['taches_avant']:>13}  {r['taches_apres']:>5}  {r['spor_proche']:>5}")
        else:
            L.append(debut + f"{r['cycles']:>6}  {r['avec_dispersion']:>10}  {r['avec_infection']:>12}  "
                     f"{(r['premiere_infection'] or '—')}")
    L += ["", "tolérance « libre » = cumul libre des heures mouillées ; minimum = durée continue exigée à l'infection."]
    if formule:
        L.append("formule : « T-8 » = degrés-heures avec base 8 soustraite ; « T×h » = produit température x durée (base 0).")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sensibilité du critère de dispersion")
    ap.add_argument("csv")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--maturite", help="ancre la maturité des oospores, ex. 2026-04-23")
    ap.add_argument("--obs-debut", help="début de la période où des taches ont été vues, ex. 2026-05-20")
    ap.add_argument("--obs-fin", help="fin de cette période, ex. 2026-06-06")
    ap.add_argument("--tolerance", type=int, default=3, help="tolérance en jours autour de la période (défaut 3)")
    ap.add_argument("--spor-max", type=int, default=10, help="sporulation « proche » = dans N jours (défaut 10)")
    ap.add_argument("--obs-jusqu-a", help="fin du suivi où AUCUNE tache n'a été vue, ex. 2026-08-31 (défaut : 31/08)")
    ap.add_argument("--infection", action="store_true",
                    help="compare la formule d'infection (T-8 / T×h) × tolérance × minimum (avec --fenetre, --seuil)")
    ap.add_argument("--humectation", action="store_true",
                    help="teste tolérance / minimum / HR pour un critère de dispersion (--fenetre, --seuil)")
    ap.add_argument("--fenetre", type=int, default=1, help="fenêtre de dispersion en heures (avec --humectation)")
    ap.add_argument("--seuil", type=float, default=3.0, help="seuil de dispersion en mm (avec --humectation)")
    a = ap.parse_args(argv)
    params = {"maturation": {"date_forcee": a.maturite}} if a.maturite else None
    obs = None
    if a.obs_debut and a.obs_fin:
        obs = {"debut": a.obs_debut, "fin": a.obs_fin, "tolerance_j": a.tolerance,
               "spor_max_j": a.spor_max, "jusqu_a": a.obs_jusqu_a}
    rows = mp.charger_csv(a.csv)
    if a.infection:
        print(afficher_humectation(grille_infection(rows, a.lat, a.lon, (a.fenetre, a.seuil), params=params, obs=obs)))
        print(f"\n(dispersion fixée à : cumul de {a.fenetre} h > {a.seuil} mm ; HR 90 %)")
    elif a.humectation:
        print(afficher_humectation(grille_humectation(rows, a.lat, a.lon, (a.fenetre, a.seuil), params=params, obs=obs)))
        print(f"\n(dispersion fixée à : cumul de {a.fenetre} h > {a.seuil} mm)")
    else:
        print(afficher(grille(rows, a.lat, a.lon, params=params, obs=obs)))


if __name__ == "__main__":
    main()
