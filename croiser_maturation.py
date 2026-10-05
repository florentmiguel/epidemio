#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Maturation des oospores : deux méthodes croisées
================================================

Compare la date de maturité du moteur (somme de degrés-jours base 8 °C >= 140 °C·j, texte de travail et Plasmopy)
à celle du modèle de Rossi et al. (2008), méthode indépendante fondée sur le temps hydro-thermique, telle que la
rapporte Franche (2012, équations 1 à 4) :

    M   = 1 si pluie horaire > 0 mm ou VPD <= 4,5 hPa, sinon 0                       (humidité de la litière)
    VPD = (1 - HR/100) x 6,11 x exp(17,47 T / (239 + T))                              (Eq. 2, hPa)
    HT  = somme horaire de M / (1330,1 - 116,19 T + 2,6256 T²)                       (Eq. 1, depuis le 1er janvier)
    DOR = exp(-15,891 x exp(-0,653 x (HT + 1)))                                       (Eq. 3, libération de dormance)
    PMO = DOR x MMO, avec MMO = 1 dès le 1er janvier (Rossi)                         (Eq. 4)

La « période d'inoculum primaire » de Rossi est celle où 3 % à 97 % des oospores ont atteint la maturité physiologique.
Ce n'est PAS une copie de Plasmopy (AGPL) : les équations viennent d'un mémoire et d'articles publiés.

Usage :
    python3 croiser_maturation.py meteo.csv --lat 49.25 --lon 3.96
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import mildiou_primaire as mp

UTC = timezone.utc
HT_COEFS = (1330.1, -116.19, 2.6256)       # Eq. 1
DOR_COEFS = (15.891, 0.653)                # Eq. 3
VPD_LITIERE_HPA = 4.5                      # seuil de la litière humide
SEUILS = (0.03, 0.50, 0.97)                # 3 % : début de la période d'inoculum primaire ; 97 % : fin


def vpd_rossi(temp: float, hr: float) -> float:
    """Eq. 2 (Caffi et al. 2011), en hPa."""
    return (1.0 - hr / 100.0) * 6.11 * math.exp(17.47 * temp / (239.0 + temp))


def litiere_humide(temp: float, hr, pluie) -> bool:
    """M de Rossi : litière humide si pluie horaire > 0 ou VPD <= 4,5 hPa."""
    if (pluie or 0.0) > 0.0:
        return True
    return hr is not None and vpd_rossi(temp, hr) <= VPD_LITIERE_HPA


def ht_horaire(temp: float, humide: bool) -> float:
    """Eq. 1 pour une heure : progrès du temps hydro-thermique."""
    if not humide:
        return 0.0
    a, b, c = HT_COEFS
    return 1.0 / (a + b * temp + c * temp * temp)


def levee_dormance(ht: float) -> float:
    """Eq. 3 : proportion d'oospores ayant levé leur dormance (Gompertz) pour un HT cumulé."""
    a, b = DOR_COEFS
    return math.exp(-a * math.exp(-b * (ht + 1.0)))


def ht_pour(proportion: float) -> float:
    """HT cumulé nécessaire pour atteindre une proportion donnée (inverse de l'Eq. 3)."""
    a, b = DOR_COEFS
    return -math.log(-math.log(proportion) / a) / b - 1.0


def maturation_rossi(rows: list[dict], seuils=SEUILS) -> dict:
    """Premier instant (UTC) où PMO atteint chaque seuil, HT final et PMO final.
    rows : série horaire du moteur (t UTC, temp, hr, pluie), à partir du 1er janvier."""
    cible = {s: ht_pour(s) for s in seuils}
    dates = {s: None for s in seuils}
    ht = 0.0
    for r in rows:
        ht += ht_horaire(r["temp"], litiere_humide(r["temp"], r["hr"], r["pluie"]))
        for s in seuils:
            if dates[s] is None and ht >= cible[s]:
                dates[s] = r["t"]
    return {"dates": dates, "ht": ht, "pmo_final": levee_dormance(ht)}


def comparer(rows: list[dict], lat: float, lon: float, params=None) -> dict:
    """Date de maturité du moteur et dates de Rossi, côte à côte."""
    res = mp.calculer_saison(rows, lat, lon, params=params)
    tz = ZoneInfo(mp.PARAMS["fuseau_local"])
    rossi = maturation_rossi(rows)
    local = lambda t: t.astimezone(tz).date().isoformat() if t else None
    return {"moteur": res["maturation"]["date_maturite"],
            "rossi": {f"{int(s * 100)} %": local(t) for s, t in rossi["dates"].items()},
            "ht": round(rossi["ht"], 2), "pmo_final": rossi["pmo_final"]}


def formater(c: dict) -> str:
    L = ["MATURATION DES OOSPORES : MÉTHODES CROISÉES", "",
         f"  Moteur (DJ8 >= 140 °C·j, texte de travail et Plasmopy) : {c['moteur'] or 'non atteinte'}",
         "  Rossi et al. 2008 (temps hydro-thermique, Franche 2012) :"]
    for seuil, date in c["rossi"].items():
        L.append(f"      {seuil:>5} des oospores matures : {date or 'non atteint'}")
    L.append(f"      (HT cumulé {c['ht']} ; proportion finale {c['pmo_final'] * 100:.1f} %)")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Maturation des oospores : méthodes croisées")
    ap.add_argument("csv")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    a = ap.parse_args(argv)
    rows, _ = mp.preparer(mp.charger_csv(a.csv))
    print(formater(comparer(rows, a.lat, a.lon)))


if __name__ == "__main__":
    main()
