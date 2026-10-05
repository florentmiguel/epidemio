#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convertit meteo.csv (colonnes Open-Meteo, heures UTC) au format d'entrée de Plasmopy
====================================================================================

Plasmopy (Agroscope, AGPL-3.0) attend un CSV séparé par des points-virgules, colonnes dans cet ordre :
    datetime (JJ.MM.AAAA HH:MM) ; température (°C) ; humidité relative (%) ;
    intensité de pluie (mm/h) ; humectation foliaire (minutes mouillées par pas de mesure)

* Les heures restent en UTC : à déclarer dans config/secrets.yaml (site.timezone: UTC), ce qui évite
  tout problème de changement d'heure. Les « jours » de Plasmopy seront des jours UTC.
* L'humectation est calculée avec le MÊME substitut que le moteur (pluie, HR, point de rosée) :
  60 minutes si l'heure est mouillée, 0 sinon. Les deux modèles reçoivent ainsi les mêmes entrées.
* Plasmopy est utilisé comme TÉMOIN : on compare ses sorties aux nôtres, on ne copie pas son code.

Usage :
    python3 exporter_plasmopy.py meteo.csv --sortie ~/plasmopy/data/input/reims_2026.csv
    python3 exporter_plasmopy.py meteo.csv --sortie ... --entete      # si le fichier exemple a une ligne d'en-tête
"""
from __future__ import annotations

import argparse
import sys

import mildiou_primaire as mp

ENTETE = "datetime;temperature;humidity;rainfall;leaf_wetness"


def exporter(rows, params=None, entete=False, pas_min=60) -> list[str]:
    """Lignes du CSV Plasmopy. pas_min = durée d'un pas de mesure, en minutes (données horaires : 60)."""
    p = mp.fusionner(mp.PARAMS, params)
    lignes = [ENTETE] if entete else []
    for r in rows:
        if r.get("temp") is None:
            continue
        hr = "" if r.get("hr") is None else f"{r['hr']:.0f}"
        mouille = pas_min if mp.est_mouille({**r, "pluie": r.get("pluie") or 0.0}, p) else 0
        lignes.append(f"{r['t'].strftime('%d.%m.%Y %H:%M')};{r['temp']:.1f};{hr};{(r.get('pluie') or 0.0):.1f};{mouille}")
    return lignes


def main(argv=None):
    ap = argparse.ArgumentParser(description="Météo horaire -> format d'entrée de Plasmopy")
    ap.add_argument("csv", help="meteo.csv (colonnes Open-Meteo, heures UTC)")
    ap.add_argument("--sortie", required=True, help="fichier CSV Plasmopy à écrire")
    ap.add_argument("--entete", action="store_true", help="écrit une ligne d'en-tête")
    ap.add_argument("--hr", type=float, help="seuil d'HR d'une heure mouillée, en %% (défaut 90, comme le moteur)")
    a = ap.parse_args(argv)
    params = {"humectation": {"hr_pct": a.hr}} if a.hr is not None else None
    lignes = exporter(mp.charger_csv(a.csv), params=params, entete=a.entete)
    with open(a.sortie, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lignes) + "\n")
    print(f"{a.sortie} écrit : {len(lignes) - (1 if a.entete else 0)} heures")


if __name__ == "__main__":
    main()
