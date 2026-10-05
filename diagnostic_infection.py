#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic d'une force d'infection : quelles heures Plasmopy a-t-il additionnées ?
================================================================================

Donne la dispersion (t0) et la force d'infection de Plasmopy (cible, en °C·h). Le script affiche, heure par
heure, ce que le moteur compte (heure mouillée, température, contribution) puis cherche les FENÊTRES
d'heures consécutives dont la somme égale la cible. Chaque fenêtre trouvée dit où Plasmopy a commencé et
terminé son cumul.

Les départs possibles sont limités autour de t0 (par défaut de t0 − 1 h à t0 + 2 h) : sur des températures
à 0,1 °C près, une fenêtre trouvée n'est pas due au hasard seulement si l'espace de recherche est petit.

Usage :
    python3 diagnostic_infection.py meteo.csv --t0 "2026-06-26T15:00" --cible 172.6
    python3 diagnostic_infection.py meteo.csv --t0 "2026-06-29T03:00" --cible 87.8
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import mildiou_primaire as mp

UTC = timezone.utc
H = timedelta(hours=1)


def contributions(rows, params=None):
    """Contribution horaire à la force (même règle que le moteur) : heure mouillée et température dans la plage."""
    p = mp.fusionner(mp.PARAMS, params)
    pi = p["infection"]
    out = []
    for r in rows:
        mouillee = mp.est_mouille(r, p)
        infectante = mouillee and pi["temperature_min"] <= r["temp"] <= pi["temperature_max"]
        if not infectante:
            dh = 0.0
        else:
            dh = max(0.0, r["temp"] - pi["base_degres_heures"]) if pi["soustraire_base"] else r["temp"]
        out.append({"t": r["t"], "temp": r["temp"], "pluie": r["pluie"], "hr": r.get("hr"),
                    "mouillee": mouillee, "dh": dh})
    return out


def fenetres(lignes, i0, cible, avant=1, apres=2, longueur_max=96, tol=0.06):
    """Fenêtres [a, b] (indices) de départ dans [i0 - avant, i0 + apres] dont la somme vaut la cible.
    Normalisées : a et b sont la première et la dernière heure CONTRIBUTIVES (les heures sèches aux
    bords ne changent pas la somme et ne sont pas distinguables)."""
    trouvees = set()
    n = len(lignes)
    for a in range(max(0, i0 - avant), min(n, i0 + apres + 1)):
        s = 0.0
        for b in range(a, min(n, a + longueur_max)):
            s += lignes[b]["dh"]
            if abs(s - cible) <= tol:
                aa, bb = a, b
                while aa < bb and lignes[aa]["dh"] == 0:
                    aa += 1
                while bb > aa and lignes[bb]["dh"] == 0:
                    bb -= 1
                trouvees.add((aa, bb))
    return sorted(trouvees)


def decrire(lignes, a, b) -> str:
    sel = lignes[a:b + 1]
    mouillees = sum(1 for x in sel if x["dh"] > 0)
    return (f"du {sel[0]['t']:%d/%m %H:%M} au {sel[-1]['t']:%d/%m %H:%M} UTC : {len(sel)} h dont {mouillees} mouillées"
            f" (somme {sum(x['dh'] for x in sel):.1f})")


def rapport(rows, t0, cible, params=None, heures=48, **kw) -> str:
    lignes = contributions(rows, params)
    idx = {l["t"]: i for i, l in enumerate(lignes)}
    if t0 not in idx:
        return f"t0 absent de la série : {t0:%Y-%m-%d %H:%M} UTC"
    i0 = idx[t0]
    L = [f"Cible : {cible} °C·h — dispersion le {t0:%d/%m %H:%M} UTC", "",
         "heure UTC       T°C   pluie   HR  mouillée   contrib.  cumul",
         "-" * 62]
    cumul = 0.0
    for i in range(max(0, i0 - 1), min(len(lignes), i0 + heures + 1)):
        l = lignes[i]
        if i >= i0:
            cumul += l["dh"]
        hr = "—" if l["hr"] is None else f"{l['hr']:.0f}"
        L.append(f"{l['t']:%d/%m %H:%M}  {l['temp']:6.1f}  {l['pluie']:5.1f}  {hr:>3}  "
                 f"{'oui' if l['mouillee'] else 'non':>8}  {l['dh']:8.1f}  {cumul:7.1f}")
    L.append("")
    trouvees = fenetres(lignes, i0, cible, **kw)
    if not trouvees:
        L.append(f"Aucune fenêtre ne somme {cible} (± 0,06) avec un départ proche de t0 : ni la règle de départ ni "
                 "celle des heures comptées n'est celle supposée.")
    else:
        L.append(f"Fenêtres dont la somme vaut {cible} (± 0,06) :")
        L += [f"  {decrire(lignes, a, b)}" for a, b in trouvees]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quelles heures somment une force d'infection donnée ?")
    ap.add_argument("csv")
    ap.add_argument("--t0", required=True, help="heure de la dispersion, UTC, ex. 2026-06-26T15:00")
    ap.add_argument("--cible", type=float, required=True, help="force d'infection de Plasmopy, en °C·h")
    ap.add_argument("--dh", choices=("base", "produit"), default="produit")
    ap.add_argument("--tmax", type=float, default=99.0)
    ap.add_argument("--heures", type=int, default=48, help="durée affichée dans le tableau")
    ap.add_argument("--avant", type=int, default=1, help="départs possibles : heures avant t0")
    ap.add_argument("--apres", type=int, default=2, help="départs possibles : heures après t0")
    a = ap.parse_args(argv)
    t0 = datetime.fromisoformat(a.t0).replace(tzinfo=UTC)
    params = {"infection": {"soustraire_base": a.dh == "base", "temperature_max": a.tmax}}
    rows, _ = mp.preparer(mp.charger_csv(a.csv))
    print(rapport(rows, t0, a.cible, params, heures=a.heures, avant=a.avant, apres=a.apres))


if __name__ == "__main__":
    main()
