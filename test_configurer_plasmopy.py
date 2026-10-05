#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la configuration de Plasmopy (copie exacte d'un main.yaml de référence)."""
import os
import re
import tempfile
import unittest

import configurer_plasmopy as cp

MAIN_YAML = r'''hydra:
  output_subdir: null
  run:
    dir: .

# -----------------------------------------------------------------------------
# INPUT DATA
# -----------------------------------------------------------------------------
input_data:
  meteo: data/input/2025_meteo_changins.csv
  spore_counts: data/input/2025_qPCR_changins.labo.exterieur.csv
  automated_spore_pull: false
  spore_counts_api_query: null   # set in config/secrets.yaml
  spore_counts_graph: null       # URL to spore graph HTML page; set in config/secrets.yaml
  automated_weather_pull: false
  weather_api_query: null        # set in config/secrets.yaml
# -----------------------------------------------------------------------------
# OUTPUT / RUN IDENTIFIER
# -----------------------------------------------------------------------------
output:
  directory: data/output       # base directory for results (can be overridden)
  run_name: example_2025       # custom run name (null to derive from meteo file)

# -----------------------------------------------------------------------------
# SPORE-DRIVEN MODEL (integrated model — spore counts fed into the algorithm)
# -----------------------------------------------------------------------------
spore_driven_model:
  enabled: true
  spore_count_threshold: 50          # [counts] condition 1
  spore_count_lookback_days: 5       # [days]   condition 2
  spore_count_percent_increase: 50   # [%]      condition 2

risk_heatmap:
  model_thresholds:   [50, 100, 200]  # [°C·h]   Weather row band boundaries
  spore_count_thresholds: [50,  500,  5000]  # [counts]  Spore-count row band boundaries

site:
  latitude: null       # set in config/secrets.yaml
  longitude: null      # set in config/secrets.yaml
  elevation: null      # set in config/secrets.yaml
  timezone: null       # set in config/secrets.yaml

run_settings:
  algorithmic_time_steps: 1 # number of time steps to compute per measurement interval (e.g. 1 = 4-hour steps)
  computational_time_steps: 6 # number of time steps to simulate (e.g. 6 = 1 day with 4-hour steps)
  measurement_time_interval: 10 # minutes between consecutive measurements in the input data
  fast_mode: true

data_columns:
  use_columns:
    - 0
    - 1
    - 2
    - 3
    - 4
  rename_columns:
    0: datetime
    1: temperature
    2: humidity
    3: rainfall
    4: leaf_wetness
  format_columns:
    0: '%d.%m.%Y %H:%M'
    1:
      - -20
      - 50
    2:
      - 0
      - 100
    3:
      - 0
      - 200
    4:
      - 0
      - 10

oospore_maturation:
  date: 11.04.2025 00:00            # pre-set date (DD.MM.YYYY HH:MM); null = compute
  base_temperature: 8.0             # [°C] base temperature for degree-day accumulation
  sum_degree_days_threshold: 140.0  # [°C·day] cumulative threshold to reach maturation

oospore_germination:
  algorithm: 2
  base_temperature: 8
  base_duration: 8
  leaf_wetness_threshold: 1.0
  relative_humidity_threshold: 80
'''


class TestConfigurer(unittest.TestCase):
    def test_applique_les_neuf_reglages(self):
        nouveau, faits = cp.modifier(MAIN_YAML, "data/input/reims_2026.csv", "reims_2026")
        self.assertEqual(len(faits), 8)
        self.assertIn("  meteo: data/input/reims_2026.csv", nouveau)
        self.assertIn("  spore_counts: null", nouveau)
        self.assertIn("  run_name: reims_2026", nouveau)
        self.assertRegex(nouveau, r"spore_driven_model:\n  enabled: false")
        self.assertRegex(nouveau, r"measurement_time_interval: 60")
        self.assertRegex(nouveau, r"computational_time_steps: 1 ")
        self.assertRegex(nouveau, r"    4:\n      - 0\n      - 60")
        self.assertRegex(nouveau, r"  date: null")

    def test_ne_touche_a_rien_d_autre(self):
        nouveau, _ = cp.modifier(MAIN_YAML, "x.csv", "r")
        avant, apres = MAIN_YAML.split("\n"), nouveau.split("\n")
        self.assertEqual(len(avant), len(apres))
        self.assertEqual(sum(1 for a, b in zip(avant, apres) if a != b), 8)
        self.assertIn("spore_driven_model", nouveau)
        self.assertIn("  algorithmic_time_steps: 1", nouveau)
        self.assertIn("fast_mode: true", nouveau)

    def test_idempotent(self):
        une_fois, _ = cp.modifier(MAIN_YAML, "x.csv", "r")
        deux_fois, _ = cp.modifier(une_fois, "x.csv", "r")
        self.assertEqual(une_fois, deux_fois)

    def test_date_de_maturite_forcee(self):
        nouveau, _ = cp.modifier(MAIN_YAML, "x.csv", "r", date_maturite="23.04.2026 00:00")
        self.assertIn("  date: '23.04.2026 00:00'", nouveau)
        retour, _ = cp.modifier(nouveau, "x.csv", "r")                  # on peut revenir à null
        self.assertIn("  date: null", retour)

    def test_reglage_introuvable_leve_une_erreur_claire(self):
        with self.assertRaises(ValueError) as e:
            cp.modifier("input_data:\n  autre: 1\n", "x.csv", "r")
        self.assertIn("introuvable", str(e.exception))

    def test_secrets(self):
        s = cp.secrets(49.25, 3.96, 90.0)
        self.assertIn("latitude: 49.25", s)
        self.assertIn("timezone: UTC", s)

    def test_cli_ecrit_main_secrets_et_sauvegarde(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "config"))
            with open(os.path.join(d, "config", "main.yaml"), "w", encoding="utf-8") as f:
                f.write(MAIN_YAML)
            cp.main([d, "--meteo", "data/input/reims_2026.csv", "--lat", "49.25", "--lon", "3.96"])
            self.assertTrue(os.path.exists(os.path.join(d, "config", "main.yaml.orig")))
            with open(os.path.join(d, "config", "secrets.yaml"), encoding="utf-8") as f:
                self.assertIn("longitude: 3.96", f.read())
            with open(os.path.join(d, "config", "main.yaml.orig"), encoding="utf-8") as f:
                self.assertIn("example_2025", f.read())               # l'original est conservé

    def test_cli_dossier_inconnu(self):
        with self.assertRaises(SystemExit):
            cp.main(["/dossier/inexistant", "--meteo", "x.csv", "--lat", "1", "--lon", "1"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
