#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sensibilité des infections secondaires aux paramètres encore incertains
=======================================================================

Rejoue la saison en ne changeant qu'UN paramètre à la fois et résume les infections secondaires obtenues :
nombre, génération, première et dernière date, répartition par mois. Permet de voir si une incertitude (survie des
sporanges, durée de vie des taches, tolérance d'interruption) change vraiment ce qu'un conseiller décidera.

Balayages (chacun croise des méthodes publiées) :
  1. survie des sporanges : équations de Blaeser & Weltzien selon le déficit de saturation (Brischetto 2020), variante à
     0,01 (Franche 2012), courbe de Vinemild (Blaise & Gessler 1990), puis durées fixes de 2 à 15 jours
     (Kast & Stark-Urnau : plus d'infection à 10 jours ; Plasmopy : 12 à 17 jours)
  2. détachement des sporanges : sans condition de pluie (Plasmopy, Rossi 2021) ou pluie horaire >= 0,2 mm (Franche 2012)
  3. productivité des taches : déclin de moitié à chaque sporulation (Kennelly 2007, Franche 2012 Eq. 13)
  4. conditions de sporulation : texte de travail / Plasmopy, Franche (Lalancette), Rossi 2021
  5. durée de vie des taches : 7, 10, 15 (défaut), 20, 30 jours, illimitée
  6. tolérance d'interruption de l'humectation : 0, 1 (défaut), 2, 3 heures

Usage :
    python3 sensibilite_secondaire.py meteo.csv --lat 49.25 --lon 3.96 --profil calage_2026
"""
from __future__ import annotations

import argparse

import mildiou_primaire as mp

MOIS = {4: "avr", 5: "mai", 6: "juin", 7: "juil", 8: "août", 9: "sept", 10: "oct"}


def balayages() -> list[tuple[str, list[tuple[str, dict]]]]:
    fixe = lambda h: {"secondaire": {"survie": "fixe", "duree_vie_sporanges_h": h}}
    pluie = lambda mm: {"secondaire": {"pluie_detachement_mm": mm}}
    prod = lambda s: {"secondaire": {"productivite_min": s}}
    return [
        ("survie des sporanges", [("selon le VPD (défaut)", {"secondaire": {"survie": "vpd"}}),
                                  ("VPD, trinôme de Franche (0,01)",
                                   {"secondaire": {"survie": "vpd", "survie_vpd": {"detaches": [5.67, -0.47, 0.01],
                                                                                   "poids_attaches": 0.0}}}),
                                  ("Vinemild (annexe 5)", {"secondaire": {"survie": "vinemild"}}),
                                  ("fixe 48 h (2 j)", fixe(48)), ("fixe 96 h (4 j)", fixe(96)),
                                  ("fixe 168 h (7 j)", fixe(168)), ("fixe 240 h (10 j)", fixe(240)),
                                  ("fixe 360 h (15 j)", fixe(360))]),
        ("détachement des sporanges", [("aucune condition (défaut)", pluie(None)),
                                       ("pluie >= 0,2 mm/h (Franche)", pluie(0.2)),
                                       ("pluie >= 1 mm/h", pluie(1.0))]),
        ("productivité des taches", [("toutes les nuits (défaut)", prod(None)),
                                     ("RS >= 0,25 (2 nuits)", prod(0.25)),
                                     ("RS >= 0,1 (4 nuits)", prod(0.1)),
                                     ("RS >= 0,05 (5 nuits)", prod(0.05))]),
        ("conditions de sporulation", [("4 h, HR >= 92 %, T >= 12 (défaut)", {}),
                                       ("6 h, HR >= 90 % (Franche)",
                                        {"sporulation": {"nuit_continue_h": 6, "hr_min": 90.0}}),
                                       ("3 h, HR >= 80 %, T >= 10 (Rossi)",
                                        {"sporulation": {"nuit_continue_h": 3, "hr_min": 80.0, "temperature_min": 10.0}})]),
        ("durée de vie des taches", [("7 jours", {"sporulation": {"fenetre_j": 7}}),
                                     ("10 jours", {"sporulation": {"fenetre_j": 10}}),
                                     ("15 jours (défaut)", {"sporulation": {"fenetre_j": 15}}),
                                     ("20 jours", {"sporulation": {"fenetre_j": 20}}),
                                     ("30 jours", {"sporulation": {"fenetre_j": 30}}),
                                     ("illimitée", {"sporulation": {"fenetre_j": None}})]),
        ("tolérance d'interruption", [(f"{h} h" + (" (défaut)" if h == 1 else ""), {"secondaire": {"tolerance_h": h}})
                                      for h in (0, 1, 2, 3)]),
    ]


def resumer(res: dict) -> dict:
    """Chiffres clés des infections secondaires d'un résultat du moteur."""
    sec = res["secondaires"]
    par_gen, par_mois = {}, {}
    for e in sec:
        par_gen[e["generation"]] = par_gen.get(e["generation"], 0) + 1
        mois = int(e["infection"]["t"][5:7])
        par_mois[mois] = par_mois.get(mois, 0) + 1
    dates = [e["infection"]["t"][:10] for e in sec]
    return {"n": len(sec), "par_gen": par_gen, "par_mois": par_mois,
            "premiere": min(dates) if dates else None, "derniere": max(dates) if dates else None}


def balayer(rows, lat, lon, base: dict | None = None, now=None) -> list[tuple[str, list[tuple[str, dict]]]]:
    """[(titre du balayage, [(libellé, résumé), ...]), ...]"""
    sortie = []
    for titre, variantes in balayages():
        lignes = []
        for libelle, params in variantes:
            res = mp.calculer_saison(rows, lat, lon, params=mp.fusionner(base or {}, params), now=now)
            lignes.append((libelle, resumer(res)))
        sortie.append((titre, lignes))
    return sortie


def formater(resultats) -> str:
    L = []
    for titre, lignes in resultats:
        L.append(f"\n{titre.upper()} (un seul paramètre change à la fois)")
        L.append(f"  {'configuration':<36}{'événements':>10}  {'générations':<22}{'1re infection':<15}{'dernière':<12}par mois")
        for libelle, r in lignes:
            gens = " ".join(f"g{g}:{n}" for g, n in sorted(r["par_gen"].items())) or "—"
            mois = " ".join(f"{MOIS.get(m, m)}:{n}" for m, n in sorted(r["par_mois"].items())) or "—"
            L.append(f"  {libelle:<36}{r['n']:>10}  {gens:<22}{r['premiere'] or '—':<15}{r['derniere'] or '—':<12}{mois}")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sensibilité des infections secondaires")
    ap.add_argument("csv")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--profil", choices=sorted(mp.PROFILS), default="calage_2026")
    a = ap.parse_args(argv)
    rows = mp.charger_csv(a.csv)
    print(f"Profil de base : {a.profil}")
    print(formater(balayer(rows, a.lat, a.lon, base=mp.charger_profil(a.profil))))


if __name__ == "__main__":
    main()
