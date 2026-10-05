#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configure Plasmopy pour tourner sur la météo horaire du moteur
==============================================================

Modifie ~/plasmopy/config/main.yaml (une copie main.yaml.orig est gardée) et écrit config/secrets.yaml.
Aucune ligne de code de Plasmopy n'est copiée : on ne touche qu'à sa configuration.

Réglages appliqués :
    input_data.meteo               -> le CSV converti
    input_data.spore_counts        -> null          (pas de piégeage de spores)
    spore_driven_model.enabled     -> false
    oospore_maturation.date        -> null          (maturité calculée), ou --date-maturite
    output.run_name                -> nom du run
    run_settings.measurement_time_interval -> 60    (données horaires)
    run_settings.computational_time_steps  -> 1
    data_columns.format_columns[4] (humectation) -> plage 0 à 60 minutes
    secrets.yaml : site.latitude / longitude / elevation, timezone UTC

Usage :
    python3 configurer_plasmopy.py ~/plasmopy --meteo data/input/reims_2026.csv --lat 49.25 --lon 3.96
    python3 configurer_plasmopy.py ~/plasmopy --meteo data/input/reims_2026.csv --lat 49.25 --lon 3.96 \\
        --date-maturite "23.04.2026 00:00"          # 2e essai : même maturité que le moteur
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys


def modifier(texte: str, meteo: str, run_name: str, date_maturite: str | None = None):
    """Applique les réglages à main.yaml. Retourne (nouveau texte, liste des réglages faits)."""
    faits = []

    def sub(motif: str, remplacement: str, libelle: str):
        nonlocal texte
        nouveau, n = re.subn(motif, remplacement, texte, count=1, flags=re.M)
        if n == 0:
            raise ValueError(f"réglage introuvable dans main.yaml : {libelle}")
        texte = nouveau
        faits.append(libelle)

    sub(r"^(  meteo:[ \t]*)\S+", rf"\g<1>{meteo}", f"input_data.meteo = {meteo}")
    sub(r"^(  spore_counts:[ \t]*)\S+", r"\g<1>null", "input_data.spore_counts = null")
    sub(r"^(  run_name:[ \t]*)\S+", rf"\g<1>{run_name}", f"output.run_name = {run_name}")
    sub(r"(^spore_driven_model:[ \t]*\n[ \t]+enabled:[ \t]*)\S+", r"\g<1>false", "spore_driven_model.enabled = false")
    sub(r"^(  measurement_time_interval:[ \t]*)\S+", r"\g<1>60", "run_settings.measurement_time_interval = 60")
    sub(r"^(  computational_time_steps:[ \t]*)\S+", r"\g<1>1", "run_settings.computational_time_steps = 1")
    sub(r"(^    4:[ \t]*\n[ \t]+-[ \t]*0[ \t]*\n[ \t]+-[ \t]*)\d+", r"\g<1>60",
        "data_columns.format_columns[4] (humectation) = 0 à 60")
    valeur = f"'{date_maturite}'" if date_maturite else "null"
    sub(r"^(  date:[ \t]*)(?:null|'?\d{2}\.\d{2}\.\d{4}[ \t]+\d{2}:\d{2}'?)", rf"\g<1>{valeur}",
        f"oospore_maturation.date = {valeur}")
    return texte, faits


def secrets(lat: float, lon: float, altitude: float) -> str:
    return ("site:\n"
            f"  latitude: {lat}\n"
            f"  longitude: {lon}\n"
            f"  elevation: {altitude}\n"
            "  timezone: UTC\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Configure Plasmopy pour la météo horaire du moteur")
    ap.add_argument("dossier", help="dossier de Plasmopy, ex. ~/plasmopy")
    ap.add_argument("--meteo", required=True, help="CSV converti, relatif au dossier, ex. data/input/reims_2026.csv")
    ap.add_argument("--run-name", default="reims_2026")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--altitude", type=float, default=90.0)
    ap.add_argument("--date-maturite", help="force la maturité des oospores, ex. \"23.04.2026 00:00\"")
    a = ap.parse_args(argv)

    base = os.path.expanduser(a.dossier)
    main_yaml = os.path.join(base, "config", "main.yaml")
    if not os.path.isfile(main_yaml):
        sys.exit(f"Introuvable : {main_yaml} (le dossier de Plasmopy est-il le bon ?)")
    with open(main_yaml, encoding="utf-8") as f:
        original = f.read()
    try:
        nouveau, faits = modifier(original, a.meteo, a.run_name, a.date_maturite)
    except ValueError as e:
        sys.exit(f"Erreur : {e}\nmain.yaml n'a pas été modifié ; colle-moi ce message.")

    orig = main_yaml + ".orig"
    if not os.path.exists(orig):
        shutil.copy2(main_yaml, orig)
    with open(main_yaml, "w", encoding="utf-8") as f:
        f.write(nouveau)
    chemin_secrets = os.path.join(base, "config", "secrets.yaml")
    if os.path.exists(chemin_secrets) and not os.path.exists(chemin_secrets + ".orig"):
        shutil.copy2(chemin_secrets, chemin_secrets + ".orig")
    with open(chemin_secrets, "w", encoding="utf-8") as f:
        f.write(secrets(a.lat, a.lon, a.altitude))

    for ligne in faits:
        print("  ✔", ligne)
    print(f"  ✔ secrets.yaml : latitude {a.lat}, longitude {a.lon}, altitude {a.altitude}, timezone UTC")
    print(f"\nSauvegarde de l'original : {orig}")


if __name__ == "__main__":
    main()
