#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Résumé de la table d'événements de Plasmopy (une ligne par chaîne distincte)
===========================================================================

Plasmopy écrit une ligne par HEURE DE DÉPART (data/output/<run>/<run>.events_table.csv) : la même chaîne
(germination -> dispersion -> infection -> ...) est donc répétée des centaines de fois. Ce script regroupe
les lignes identiques pour comparer, chaîne par chaîne, avec les cycles du moteur.

Aucune hypothèse sur le format des cellules : elles sont lues comme du texte (None = événement absent).

Usage :
    python3 lire_plasmopy.py ~/plasmopy/data/output/reims_2026/reims_2026.events_table.csv
    python3 lire_plasmopy.py ... --tout          # affiche aussi les chaînes sans dispersion
    python3 lire_plasmopy.py ... --secondaire    # sporanges et infections secondaires de Plasmopy
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import OrderedDict

COLONNES = ["oospore_germination", "oospore_dispersion", "oospore_infection", "oospore_infection_strength",
            "incubation_days", "completed_incubation", "sporulations"]


def vide(v) -> bool:
    return v is None or str(v).strip() in ("", "None", "[]", "nan", "NaT")


def court(v, n=25) -> str:
    """Texte compact : les horodatages « 2026-06-26 15:00:00+00:00 » deviennent « 2026-06-26 15:00Z » (à l'heure près)."""
    s = "—" if vide(v) else str(v).strip()
    s = re.sub(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}):\d{2}\+00:00", r"\1Z", s)
    s = s.replace(" 00:00Z", "Z") if re.fullmatch(r"\d{4}-\d{2}-\d{2} 00:00Z", s) else s
    return s if len(s) <= n else s[: n - 1] + "…"


def charger(chemin: str) -> list[dict]:
    with open(chemin, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def chaines(rows: list[dict]):
    """Chaînes distinctes, dans l'ordre d'apparition : {clé: [dates de départ]}."""
    out: "OrderedDict[tuple, list[str]]" = OrderedDict()
    for r in rows:
        cle = tuple(r.get(c, "") for c in COLONNES)
        out.setdefault(cle, []).append(r.get("start", ""))
    return out


def resume(rows: list[dict], tout: bool = False) -> str:
    if not rows:
        return "Table vide."
    ch = chaines(rows)
    avec_disp = [(k, s) for k, s in ch.items() if not vide(k[1])]
    L = [f"{len(rows)} heures de départ ; maturité : {court(rows[0].get('oospore_maturation'))}",
         f"{len(ch)} chaînes distinctes : {len(avec_disp)} avec dispersion, "
         f"{sum(1 for k, _ in avec_disp if not vide(k[2]))} avec infection, "
         f"{sum(1 for k, _ in avec_disp if not vide(k[5]))} avec fin d'incubation", ""]
    en_tete = ["germination", "dispersion", "infection", "force", "incub.(j)", "fin incub.", "sporulation"]
    largeurs = [17, 17, 17, 8, 9, 17, 24]
    L.append("  ".join(f"{t:<{w}}" for t, w in zip(en_tete, largeurs)) + "  départs")
    L.append("-" * 150)
    for k, starts in ch.items():
        if vide(k[1]) and not tout:
            continue
        cellules = [court(v, w) for v, w in zip(k, largeurs)]
        L.append("  ".join(f"{cel:<{w}}" for cel, w in zip(cellules, largeurs))
                 + f"  {len(starts)} ({court(starts[0], 17)} → {court(starts[-1], 17)})")
    sans = len(ch) - len(avec_disp)
    if sans and not tout:
        L.append(f"\n(+ {sans} chaîne(s) sans dispersion : --tout pour les voir)")
    return "\n".join(L)


COLONNES_SEC = ["sporulations", "sporangia_densities", "spore_lifespan_days", "secondary_infections",
                "secondary_infection_strengths"]


def resume_secondaire(rows: list[dict], largeur: int = 600) -> str:
    """Chaînes distinctes qui comportent au moins une infection secondaire : sporulations, densité et durée de vie des
    sporanges, infections secondaires et leur force, tels que Plasmopy les écrit (cellules affichées brutes)."""
    ch: "OrderedDict[tuple, list[str]]" = OrderedDict()
    for r in rows:
        cle = (r.get("oospore_germination", ""),) + tuple(r.get(c, "") for c in COLONNES_SEC)
        ch.setdefault(cle, []).append(r.get("start", ""))
    avec = [(k, s) for k, s in ch.items() if not vide(k[4])]
    L = [f"{len(ch)} chaînes distinctes ; {len(avec)} avec infection(s) secondaire(s)", ""]
    for k, starts in avec:
        L.append(f"chaîne de germination {court(k[0], 20)} ({len(starts)} heures de départ)")
        for nom, v in zip(COLONNES_SEC, k[1:]):
            s = "—" if vide(v) else str(v).strip()
            L.append(f"  {nom:<30} {s if len(s) <= largeur else s[: largeur - 1] + '…'}")
        L.append("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Résumé de la table d'événements de Plasmopy")
    ap.add_argument("csv")
    ap.add_argument("--tout", action="store_true")
    ap.add_argument("--secondaire", action="store_true",
                    help="affiche les sporanges et les infections secondaires au lieu de la chaîne primaire")
    a = ap.parse_args(argv)
    rows = charger(a.csv)
    print(resume_secondaire(rows) if a.secondaire else resume(rows, a.tout))


if __name__ == "__main__":
    main()
