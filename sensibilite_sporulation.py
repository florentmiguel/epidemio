#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carte du critère de sporulation
===============================

Trois critères publiés coexistent (texte de travail et Plasmopy : HR >= 92 % pendant 4 h ; Franche d'après Lalancette :
HR > 90 % pendant 6 h ; Rossi et al. 2021 : HR >= 80 % pendant 3 h) et donnent des résultats très différents. Ce script
balaie l'espace entre eux : pour chaque couple (seuil d'humidité, durée de nuit continue) il compte les NUITS DE
SPORULATION des taches primaires dans une fenêtre de dates, et donne la date de la première infection secondaire.

Une observation de terrain du type « taches sans fructification marquée, mais pas nulle » devient alors une cible
mesurable : quelques nuits, ni zéro ni dizaines, dans la fenêtre où les taches de mai-juin étaient vivantes.

Usage :
    python3 sensibilite_sporulation.py meteo.csv --lat 49.25 --lon 3.96 --du 05-20 --au 06-30
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import mildiou_primaire as mp

HR_DEFAUT = (80, 85, 88, 90, 92, 94)
DUREES_DEFAUT = (3, 4, 5, 6)


def nuits_dans(res: dict, du: str, au: str) -> tuple[int, int]:
    """(nuits distinctes, taches primaires concernées) avec au moins une nuit de sporulation entre les dates « MM-JJ »
    du et au (bornes comprises). La nuit est datée par l'heure UTC de l'événement."""
    nuits, cycles = set(), 0
    for c in res["cycles"]:
        dedans = [s[:10] for s in c.get("sporulations", []) if du <= s[5:10] <= au]
        if dedans:
            cycles += 1
            nuits.update(dedans)
    return len(nuits), cycles


def grille(rows, lat, lon, base=None, hrs=HR_DEFAUT, durees=DUREES_DEFAUT, du="05-20", au="06-30", now=None) -> dict:
    """{(hr, durée): {"nuits": n, "taches": k, "premiere": date ou None, "secondaires": n}}"""
    sortie = {}
    for hr in hrs:
        for d in durees:
            params = mp.fusionner(base or {}, {"sporulation": {"hr_min": float(hr), "nuit_continue_h": int(d)}})
            res = mp.calculer_saison(rows, lat, lon, params=params, now=now)
            n, k = nuits_dans(res, du, au)
            sec = res["secondaires"]
            sortie[(hr, d)] = {"nuits": n, "taches": k, "secondaires": len(sec),
                               "premiere": min((e["infection"]["t"][:10] for e in sec), default=None)}
    return sortie


def formater(g: dict, du: str, au: str, hrs=HR_DEFAUT, durees=DUREES_DEFAUT, reperes=None) -> str:
    L = [f"CRITÈRE DE SPORULATION : NUITS DE SPORULATION ENTRE LE {du} ET LE {au} (taches primaires)",
         "  Chaque case : nuits distinctes / taches concernées. T >= 12 °C et profil de base inchangés.", ""]
    entete = "  humidité minimale  " + "".join(f"{d:>3} h".rjust(11) for d in durees)
    L.append(entete)
    for hr in hrs:
        L.append(f"  HR >= {hr:>3} %        " + "".join(f"{g[(hr, d)]['nuits']:>5} /{g[(hr, d)]['taches']:>3}  " for d in durees))
    L += ["", "PREMIÈRE INFECTION SECONDAIRE (nombre total d'infections secondaires entre parenthèses)", "", entete]
    for hr in hrs:
        cases = "".join(f"{(g[(hr, d)]['premiere'] or '—')[5:]:>6} ({g[(hr, d)]['secondaires']:>2})" for d in durees)
        L.append(f"  HR >= {hr:>3} %      {cases}")
    if reperes:
        L += ["", "Repères : " + " ; ".join(reperes)]
    return "\n".join(L)


FORMAT = "%Y-%m-%dT%H:%MZ"


def detail(rows, lat, lon, hr, duree, base=None, du="05-20", au="06-30", now=None) -> dict:
    """Pour un couple (HR, durée) : les cycles primaires dont les taches sont VIVANTES dans la fenêtre, avec leurs nuits de
    sporulation dans la fenêtre. Explique les nombres « nuits / taches » de la carte."""
    params = mp.fusionner(base or {}, {"sporulation": {"hr_min": float(hr), "nuit_continue_h": int(duree)}})
    res = mp.calculer_saison(rows, lat, lon, params=params, now=now)
    vie_j = res["parametres"]["sporulation"]["fenetre_j"]
    cycles = []
    for c in res["cycles"]:
        tach = c.get("taches", {}).get("t")
        if not tach:
            continue
        an = tach[:4]
        fin_vie = (datetime.strptime(tach, FORMAT) + timedelta(days=vie_j)).strftime("%Y-%m-%d") if vie_j else None
        if tach[:10] > f"{an}-{au}" or (fin_vie is not None and fin_vie < f"{an}-{du}"):
            continue                                            # taches pas encore apparues, ou déjà mortes
        nuits = sorted({s[:10] for s in c.get("sporulations", []) if du <= s[5:10] <= au})
        cycles.append({"id": c["id"], "infection": c["infection"]["t"], "taches": tach, "fin_vie": fin_vie, "nuits": nuits})
    secondaires = sorted(e["infection"]["t"] for e in res["secondaires"])
    return {"cycles": cycles, "nuits": sorted({n for k in cycles for n in k["nuits"]}),
            "premiere_secondaire": secondaires[0] if secondaires else None}


def formater_detail(d: dict, hr, duree, du: str, au: str) -> str:
    L = [f"DÉTAIL : HR >= {hr} %, {duree} h, fenêtre du {du} au {au}",
         "  Un « cycle » est un épisode d'infection primaire : ses taches d'huile apparaissent ensemble (cohorte) et",
         "  restent vivantes 15 jours. Une même nuit favorable peut faire sporuler plusieurs cohortes à la fois.", "",
         "  cycle  infection          taches visibles    taches vivantes jusqu'au  nuits de sporulation dans la fenêtre"]
    for k in d["cycles"]:
        nuits = ", ".join(n[5:] for n in k["nuits"]) or "aucune"
        L.append(f"  #{k['id']:>4}  {k['infection'][:16]}  {k['taches'][:16]}  {(k['fin_vie'] or 'illimitée'):<24}  {nuits}")
    avec = sum(1 for k in d["cycles"] if k["nuits"])
    L += ["", f"  {len(d['cycles'])} cycle(s) avec des taches vivantes dans la fenêtre, dont {avec} avec au moins une nuit "
              f"de sporulation ; {len(d['nuits'])} nuit(s) distincte(s) : {', '.join(n[5:] for n in d['nuits']) or '—'}",
          f"  première infection secondaire de la saison : {(d['premiere_secondaire'] or '—')[:10]}"]
    return "\n".join(L)


REPERES = ["texte de travail et Plasmopy = HR >= 92 %, 4 h", "Franche = HR >= 90 %, 6 h", "Rossi 2021 = HR >= 80 %, 3 h"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Carte du critère de sporulation")
    ap.add_argument("csv")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--profil", choices=sorted(mp.PROFILS), default="calage_2026")
    ap.add_argument("--du", default="05-20", help="début de la fenêtre, MM-JJ (défaut 05-20)")
    ap.add_argument("--au", default="06-30", help="fin de la fenêtre, MM-JJ (défaut 06-30)")
    ap.add_argument("--detail", nargs=2, type=float, metavar=("HR", "DUREE"),
                    help="liste les cycles et les nuits d'un couple (ex. --detail 90 4) au lieu de la carte")
    a = ap.parse_args(argv)
    rows = mp.charger_csv(a.csv)
    if a.detail:
        hr, duree = a.detail[0], int(a.detail[1])
        d = detail(rows, a.lat, a.lon, hr, duree, base=mp.charger_profil(a.profil), du=a.du, au=a.au)
        print(f"Profil de base : {a.profil}\n")
        print(formater_detail(d, f"{hr:g}", duree, a.du, a.au))
        return
    print(f"Profil de base : {a.profil}\n")
    print(formater(grille(rows, a.lat, a.lon, base=mp.charger_profil(a.profil), du=a.du, au=a.au), a.du, a.au,
                   reperes=REPERES))


if __name__ == "__main__":
    main()
