# Moteur mildiou — contamination primaire (v0)

Module pur : une série météo **horaire (UTC)** en entrée, le cycle biologique de la
saison en sortie (maturation → germination → dispersion → infection → incubation →
taches → sporulation). Tout est recalculé depuis le 1er janvier à chaque appel.

## Fichiers
| Fichier | Rôle |
|---|---|
| `mildiou_primaire.py` | le moteur + ses paramètres (`PARAMS`) + une ligne de commande |
| `recuperer_meteo_horaire.py` | télécharge la météo horaire d'une position (Open-Meteo) → CSV (validé sur le VPS) |
| `exporter_plasmopy.py` | convertit meteo.csv au format d'entrée de Plasmopy (témoin de comparaison) |
| `diagnostic_infection.py` | retrouve quelles heures somment une force d'infection donnée (ex. celle de Plasmopy) |
| `lire_plasmopy.py` | résume la table d'événements de Plasmopy (une ligne par chaîne distincte) pour la comparer au moteur |
| `configurer_plasmopy.py` | applique les réglages de Plasmopy (main.yaml, secrets.yaml) pour la météo horaire |
| `sensibilite_dispersion.py` | rejoue la saison avec plusieurs critères (dispersion, puis humectation) et les juge contre l'observation de terrain |
| `test_*.py` | 124 tests (météos synthétiques, une règle par test) |

## Périmètre : le moteur évalue le danger, l'OAD décide
Le moteur évalue le **risque épidémiologique indépendamment de tout programme phytosanitaire** :
« une infection est-elle possible, avec quelle intensité, quand les taches apparaîtraient-elles ? ».

| Le moteur (ce dépôt) | L'OAD (hors de ce dépôt) |
|---|---|
| maturité des oospores, cycle, infections passées et prévues | date du dernier traitement |
| intensité (°C·h), incubation, date probable des taches | rémanence du produit, lessivage |
| pression d'infection jour par jour (passé et 7 jours) | choix de la meilleure substance active |
| faits météo bruts à la demande (ex. pluie cumulée depuis une date) | stade phénologique et organes à protéger |

Une infection prédite n'est donc ni une alerte ni une absence d'alerte : c'est l'OAD qui la rapproche de l'état de
protection. La **pluie cumulée depuis une date** est un fait météo neutre que le moteur peut fournir ; le seuil de
lessivage, propre à chaque produit, reste à l'OAD. Aucune donnée de traitement n'entre dans le moteur.

## Tester contre Plasmopy (témoin)
Plasmopy (Agroscope, licence AGPL-3.0) sert de **témoin** : on lui donne la même météo et on compare ses
événements aux nôtres. On ne copie pas son code et on ne le met pas dans ce dépôt (le garder dans `~/plasmopy`).
1. Installer : `git clone https://github.com/agroscope-ch/plasmopy.git ~/plasmopy`, Poetry, `make setup`.
2. Convertir la météo : `python3 exporter_plasmopy.py meteo.csv --sortie ~/plasmopy/data/input/reims_2026.csv`
   (ajouter `--entete` si le fichier exemple `data/input/2025_meteo_changins.csv` a une ligne d'en-tête).
3. Régler la configuration : `python3 configurer_plasmopy.py ~/plasmopy --meteo data/input/reims_2026.csv --lat 49.25 --lon 3.96`
   (8 réglages dans `main.yaml`, copie `main.yaml.orig` conservée, + `config/secrets.yaml` en UTC).
   Option `--date-maturite "23.04.2026 00:00"` pour un 2e essai avec la même maturité que le moteur.
4. `make run`, puis lire `data/output/<run>/*.log` (date de maturité) et `*.events_log.csv` (événements).
5. `python3 lire_plasmopy.py ~/plasmopy/data/output/reims_2026/reims_2026.events_table.csv` : regroupe les lignes identiques
   (Plasmopy écrit une ligne par heure de départ) et affiche chaque chaîne : germination, dispersion, infection, force (°C·h), etc.

**Premier essai (météo Open-Meteo, Reims, 2026)** : maturité de Plasmopy = **23 avril**, identique au moteur (mode `degres_jours`) ;
première germination = **2 mai 15 h UTC**, identique au moteur à l'heure près.

À comparer : date de maturité (notre mode `degres_jours`), dates d'infection de mai, et si Plasmopy voit aussi
l'épisode de mai avec son seuil de pluie de 3,0 (sinon le défaut vient de la pluie horaire lissée).

## Résultats du témoin Plasmopy (premier essai : météo Open-Meteo, Reims, 2026)
Plasmopy a tourné avec sa configuration d'origine (seuil de pluie 3,0), sur la même météo horaire.

| Étape | Plasmopy | Moteur | Verdict |
|---|---|---|---|
| Maturité | 23 avril | 23 avril (`degres_jours`) | identique |
| 1re germination | 02/05 15 h UTC | 02/05 15 h UTC | identique à l'heure |
| Dispersions | 1re le **26/06** (aucune en mai) ; puis 29/06, 19/08, 27/08, 07/10 | idem avec 1 h / 3 mm ; le cumul 6 h / 5 mm retrouve mai | le défaut est le couple seuil de pluie × pluie horaire lissée |
| 26/06 15 h, 29/06 03 h, 07/10 17 h | mêmes dates | identiques à l'heure (1 h / 3 mm) | identique |
| Latence de dispersion | 0 à 2 h après la germination | 0 h | cohérent |
| Fin d'incubation (26/06) | 02/07 16 h | 02/07 10 h | à 6 h près |
| Sporulation des taches de juin | 19/08 04 h | 19/08 22 h | même comportement : la référence n'a pas de durée de vie des taches |
| Force d'infection (26/06 ; 29/06) | 172,6 ; 87,8 °C·h | T−8 : 52,2 ; 37,0 — T×h : 76,2 ; 69,0 | voir ci-dessous |

**Test `--dh produit` (critère 1 h / 3 mm)** — un premier raisonnement (« les deux modèles comptent les mêmes
15 heures ») a été **invalidé** par ce test. Ce qu'il établit :
* **29/06 : infection à 05 h, identique à Plasmopy**, alors qu'avec T−8 aucune infection ne se déclenche ce jour-là
  (37,0 < 50). C'est l'argument le plus net en faveur de T×h. Force : 69,0 contre 87,8 (≈ une heure de nuit en moins).
* **26/06 : 76,2 contre 172,6, infection à 22 h contre 16 h.** Le moteur ne compte que 3 heures (T×h − (T−8) = 24 = 8 × 3),
  à ≈ 25 °C. Il manque 96,4 °C·h, soit ≈ 3 heures vers 32 °C : hypothèse = le **plafond de 29 °C** (plage de l'infection
  *secondaire* chez Plasmopy) exclut les heures d'après-midi chaud ; Plasmopy n'en a pas pour l'infection primaire.
  À tester : `--dh produit --tmax 99`.
* **Test `--dh produit --tmax 99`** : **les deux dates d'infection sont identiques à Plasmopy** (26/06 16 h, 29/06 05 h).
  Sans plafond, l'infection du 26/06 passe de 22 h à 16 h et la force de 76,2 à 105,3. Le plafond de 29 °C n'a donc pas
  lieu d'être pour l'infection primaire. Écart restant sur la force : 105,3 contre 172,6 et 69,0 contre 87,8 ; hypothèse :
  fenêtre d'infection plus longue chez Plasmopy que nos 24 h (`--validite-inf`). Rappel : une pluie dispersante continue
  prolonge déjà la fenêtre (une longue pluie = un cycle) ; la durée de validité ne joue qu'après la dernière pluie dispersante.
* **Balayage de la fenêtre d'infection** (`--dh produit --tmax 99 --validite-inf H`), forces du 26/06 et du 29/06 :
  24 h : 105,3 et 69,0 · 36 h : 189,9 et 69,0 · 48 h : 189,9 et 86,3 · 72 h : 380,0 (les deux épisodes fusionnent).
  Plasmopy : 172,6 et 87,8. Aucune durée fixe ne redonne les deux : 172,6 tombe entre 24 h et 36 h, 87,8 près de 48 h.
  Pas de fenêtre fixe, donc : `diagnostic_infection.py` cherche les heures consécutives dont la somme vaut la cible
  (ex. `--t0 "2026-06-26T15:00" --cible 172.6`).
* **Diagnostic `diagnostic_infection.py`** — règle de cumul de Plasmopy retrouvée : somme de T sur les heures mouillées
  de **l'heure précédant la dispersion jusqu'à 24 h après**, par-dessus les heures sèches (cumul libre), sans plafond.
  29/06 : 02 h → 06 h = **87,8 exactement**. 26/06 : 14 h → 27/06 15 h = 171,6 contre 172,6 (1,0 °C·h d'écart).
  Notre moteur démarre à la dispersion et s'arrête 1 h plus tôt (105,3 et 69,0). Conséquences : la tolérance « libre »
  est bien le comportement de la référence, et le « minimum 6 h » n'est pas appliqué (infection du 29/06 après 4 h mouillées).
  Écart résiduel assumé : quelques heures sur les dates, ≤ 1,6 × sur la force. Plasmopy s'appuie sur des capteurs
  d'humidité foliaire que nous n'avons pas : la proxy d'humectation reste la principale incertitude.
* Fin d'incubation : 02/07 09 h et 04/07 14 h chez nous contre 02/07 16 h et 04/07 05 h chez Plasmopy (≤ 9 h d'écart).

Écarts mineurs de dispersion (août, septembre) : probablement seuil « ≥ 3,0 » (nous : « > 3 ») et fenêtre
de validité plus courte que nos 48 h.

## Profil `calage_2026` (`--profil calage_2026`)
Jeu de paramètres nommé, **proposé** ; les défauts de `PARAMS` (texte de travail v0) ne changent pas.
Une option de la ligne de commande surcharge le profil.

| Étape | Valeur | Origine |
|---|---|---|
| Maturation | degrés-jours base 8 °C, 140 °C·j depuis le 1er janvier | confirmé par Plasmopy (23 avril) |
| Germination | règles du texte (8 h à T > 8 °C avec HR > 80 % ou feuille mouillée ; ou 5 mm / 48 h) | confirmé par Plasmopy, à l'heure |
| Dispersion | cumul glissant 6 h > 5 mm, latence 0 h | **calé sur l'observation** (mai 2026) ; latence cohérente avec Plasmopy |
| Infection | T × h, T > 8 °C, **sans plafond**, 50 °C·h, cumul libre, 24 h, pas de minimum | dates d'infection identiques à Plasmopy |
| Humectation | HR ≥ 90 % (proxy, sans capteur) | hypothèse, rappel observé robuste |
| Incubation | table de Goidanich | fin d'incubation à ≤ 13 h de Plasmopy |
| Sporulation | HR ≥ 92 %, T ≥ 12 °C, 4 h de nuit continues | règle du texte |
| Durée de vie d'une tache | 10 jours | **hypothèse** à confirmer (la référence n'en a pas) |

## Utilisation
```bash
python3 test_mildiou_primaire.py && python3 test_recuperer_meteo.py      # contrôle

# 1. valider le téléchargement sur une vraie réponse (à faire sur le VPS)
python3 recuperer_meteo_horaire.py --lat 49.25 --lon 3.96 --sortie meteo.csv --verifier

# 2. lancer le moteur (--maturite ancre la maturité sur une observation)
python3 mildiou_primaire.py meteo.csv --lat 49.25 --lon 3.96
python3 mildiou_primaire.py meteo.csv --lat 49.25 --lon 3.96 --maturite 2026-04-11
```
Pour voir le détail des cycles avec un critère choisi (dates, pluie retenue à la dispersion) :
```bash
python3 mildiou_primaire.py meteo.csv --lat 49.25 --lon 3.96 --fenetre 6 --seuil 5
```
Options : `--fenetre` / `--seuil` (dispersion), `--tolerance` / `--minimum` / `--hr` (humectation),
`--fenetre-spor` (durée de vie d'une tache, en jours), `--cumul` (maturation), `--dh` (formule d'infection),
`--tmax` (plafond de température d'une heure infectante), `--validite-inf` (fenêtre d'infection, en heures), `--maturite`.
En Python : `calculer_saison(rows, lat, lon, params={...}, now=..., bbch={...})`.
`params` fusionne avec `PARAMS` : pour tester une variante, on ne surcharge que ce qui change.

## Origine des paramètres
`[F]` texte de Florent · `[P]` Plasmopy (`config/main.yaml`) · `[D]` défaut proposé, à valider.

| Étape | Paramètres | Origine |
|---|---|---|
| Maturation | DJ8 depuis le 1er janvier, seuil 140, paliers 100/120/140 | [F] |
| Germination | T > 8 °C ; porte A : HR > 80 % ou feuille mouillée ≥ 8 h ; porte B : 5 mm/48 h | [F] |
| | validité d'une germination : 48 h | [D] |
| Dispersion | pluie > 3 mm (fenêtre 1 h = « 3 mm/h »), T > 8 °C, latence 0 | [F] |
| Infection | base des degrés-heures **8 °C** (corrigé), 50 °C·h, plage 8–29 °C (borne basse alignée sur la base) | [F] |
| | durée de vie des zoospores : 24 h | [D] |
| Incubation | table de température de ton modèle actuel, progression horaire | [F] |
| Sporulation | HR ≥ 92 %, T ≥ 12 °C, ≥ 4 h de nuit continue | [P] (le texte n'a pas de valeurs) |
| Sensibilité | coefficient BBCH 10→13 : 0,25 / 0,5 / 0,75 / 1 | [F] (forme : [D]) |
| Feuille mouillée | pluie ≥ 0,1 mm, ou HR ≥ 90 %, ou T − rosée ≤ 1 °C | [D] |

## À trancher : l'humectation de la feuille
Aujourd'hui, chaque heure est « mouillée » ou non (pluie ≥ 0,1 mm, ou HR ≥ 90 %, ou T − rosée ≤ 1 °C ;
le capteur prime s'il existe). Il n'existe **aucune notion de durée continue** :
* germination : ≥ 8 h consécutives d'HR > 80 % ou de feuille mouillée à T > 8 °C [F] ;
* infection : les heures mouillées s'**additionnent** pendant la fenêtre (24 h, prolongée par chaque
  pluie dispersante), même séparées par des heures sèches ; seul compte le cumul de 50 °C·h ;
* l'humectation ne compte **qu'à partir de la dispersion** (les heures mouillées avant ne servent pas).

Durée de mouillage nécessaire pour atteindre 50 °C·h (mesurée avec le moteur) :
9 °C : 50 h · 10 °C : 25 h · 12 °C : 13 h · 14 °C : 9 h · 16 °C : 7 h · 18 °C : 5 h · 20 °C : 5 h · 25 °C : 3 h.

Décisions à prendre (à trancher par l'étape 2 ci-dessous) : (1) tolérance d'interruption sèche (`humectation.tolerance_h`, défaut : cumul libre),
(2) durée minimale continue à l'infection (`infection.mouillage_min_h`, Plasmopy : 6 h),
(3) définition d'une heure mouillée (HR ≥ 90 % ?).

## Croisement avec la description suisse du modèle (VitiMeteo / Agroscope)
Texte de référence fourni (« dans nos conditions… »), comparé aux valeurs du moteur :

| Étape | Moteur | Texte | Verdict |
|---|---|---|---|
| Maturation | Σ max(0 ; Tmoy − 8) depuis le 1er janvier ≥ 140 | « moyennes journalières dépassant 8 °C cumulées depuis le 1er janvier, dès 140 °C » | seuil et départ ✔ ; **ambigu** : degrés-jours ou somme brute des Tmoy > 8 ? (`maturation.mode_cumul`) |
| Dispersion | cumul 6 h > 5 mm (calé sur données) | « éclaboussures dues aux précipitations », aucun seuil | rien à croiser |
| Infection | Σ max(0 ; T − 8) ≥ 50 | « température moyenne × durée d'humectation = 50 ; à 10 °C, 5 h » (donné pour le secondaire) | **écart majeur** : T×h donne 5 h à 10 °C, T−8 donne 25 h (`infection.soustraire_base`) |
| Interruption | cumul libre | « si elles sèchent avant, les zoospores meurent » | **contradiction** : continuité stricte (`humectation.tolerance_h`) |
| Durée limitante | minimum 6 h (Plasmopy), non appliqué par défaut | « généralement pas limitante dans nos conditions » | compatible |
| Incubation | 14 j à 12 °C … 4 j à 23-24 °C, arrêt sous 11 °C | « 4 à 12 jours selon la température » | écart léger (14 j à 12 °C) |
| Sporulation | HR ≥ 92 % **et** T ≥ 12 °C à chaque heure, 4 h de nuit continue | « feuilles mouillées **ou** HR > 92 %, T ≥ 12 °C **au début** de l'humectation, ≥ 4 h, dans l'obscurité » | écarts : « ou feuille mouillée », T au début seulement |
| Infections primaires | | « possibles durant toute la période de végétation » | ✔ : pas de fin de saison du primaire à modéliser |

À propos de la base 8 °C : dans le fichier Plasmopy, `base_temperature` est une base de cumul pour la maturation
(« degree-day accumulation ») mais une **température minimale** pour la germination et pour l'infection primaire.
8 °C est donc sûrement un seuil ; savoir s'il faut en plus le soustraire (T − 8) ou multiplier T par la durée
(T × h) est la question ouverte.

```bash
# maturité selon les deux lectures, à comparer à la date annoncée par le Comité Champagne
python3 mildiou_primaire.py meteo.csv --lat 49.25 --lon 3.96 --cumul degres_jours | head -3
python3 mildiou_primaire.py meteo.csv --lat 49.25 --lon 3.96 --cumul somme | head -3
# formule d'infection × tolérance × minimum, pour le critère de dispersion retenu
python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96 \
    --obs-debut 2026-05-20 --obs-fin 2026-06-06 --obs-jusqu-a 2026-08-31 --infection --fenetre 6 --seuil 5
```

## À trancher : le critère de dispersion (constat sur données réelles)
Premier passage sur la météo Open-Meteo 2026 (Reims) : depuis la maturité (23 avril), **7 heures**
dépassent 3 mm/h, contre **19 jours** cumulant ≥ 5 mm. La réanalyse lisse les pointes de pluie :
« > 3 mm/h » mesuré sur l'heure n'est presque jamais atteint, donc 27 cycles sur 31 restent
« germination sans dispersion ». Le critère est désormais paramétrable (`dispersion.fenetre_h`,
`dispersion.pluie_mm`) ; la valeur par défaut reste celle de ton texte (fenêtre 1 h, 3 mm).

```bash
# étape 1 : critère de dispersion
python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96 \
    --obs-debut 2026-05-20 --obs-fin 2026-06-06 --obs-jusqu-a 2026-08-31
# étape 2 : humectation, pour le critère retenu (ex. cumul de 6 h > 5 mm)
python3 sensibilite_dispersion.py meteo.csv --lat 49.25 --lon 3.96 \
    --obs-debut 2026-05-20 --obs-fin 2026-06-06 --obs-jusqu-a 2026-08-31 --humectation --fenetre 6 --seuil 5
```
L'étape 1 compare fenêtres de 1, 3, 6 et 24 h × seuils de 3, 5 et 10 mm ; l'étape 2 compare tolérance
d'interruption (libre / 0 / 2 / 4 h) × durée minimale (aucune / 6 h) × seuil d'HR (85 / 90 / 93 %).
Chaque ligne est jugée contre l'observation de terrain 2026 (taches d'huile vues du 20/05 au 06/06) sur le
**rappel seulement** : « compatible » = au moins une tache prédite dans la période (± 3 j) ; « écart (j) » =
distance entre le début observé et la tache prédite la plus proche (< 0 : prédite trop tôt).

**Ce que l'observation ne prouve pas.** L'absence de taches après le 06/06 (et l'absence de fructification)
peut venir de la protection phytosanitaire : une infection prédite sans taches n'est pas une fausse alerte.
« avant », « après » et « spor. » sont donc affichés à titre d'information, sans jouer dans le verdict.
La précision du modèle ne se juge pas sur la maladie observée sous protection (voir « Périmètre »).

Plasmopy note son seuil « 3,0 mm » (et non mm/h) : l'unité exacte est à confirmer.

## Autres choix à valider
1. **Un épisode = un cycle** : pas de nouvelle germination tant qu'un cycle est ouvert ; une nouvelle
   pluie dispersante prolonge la fenêtre d'infection (une longue période pluvieuse = un cycle).
2. **Incubation** : la progression s'accumule heure par heure avec la température moyenne du jour
   (au lieu d'une durée figée le jour de l'infection).
3. **Maturité effective dès le lendemain** du jour où le seuil est atteint, ou à la date forcée.
4. Un trou dans la série météo n'arrête pas le calcul : il est signalé dans `avertissements`.

## À tester contre Plasmopy (affinage)
Chaque écart se règle par un paramètre, sans toucher au code :

| Écart | Ton texte | Plasmopy | Paramètre |
|---|---|---|---|
| latence de dispersion | 0 h | 6 h dans le fichier, mais 0 à 2 h observées (essai du 05/10) | `dispersion.latence_h` |
| seuil de dispersion | 3 mm/h | 3,0 mm (unité à confirmer) | `dispersion.pluie_mm`, `dispersion.fenetre_h` |
| mouillage minimum à l'infection | aucun | 6 h | `infection.mouillage_min_h` |
| T minimale à la dispersion | 8 °C | aucune | `dispersion.temperature_min` |
| germination | 2 portes indépendantes | humectation du sol puis conditions (variantes 1/2) | à implémenter |
| loi d'incubation | table de ton modèle | « température moyenne » (loi inconnue) | à lever par essais |

## Limites connues de la v0
* Pas de repiquage (phase suivante) : le cycle s'arrête à la sporulation (`repiquage: true`).
* Durée de vie des taches (`sporulation.fenetre_j`, défaut : illimitée). Constat sur données réelles 2026 :
  sans fenêtre, six cycles de mai-juin sporulent tous le 19/08, première nuit favorable, 3 mois plus tard
  (artefact). Avec une fenêtre, une tache sans nuit favorable passe en `taches_sans_sporulation`.
  Valeur à fixer (test : 10 jours).
* Pas de stade BBCH par client pour l'instant : sans `bbch`, le coefficient vaut 1.
* Pluie horaire de réanalyse : les pointes sont lissées, le seuil de 3 mm/h peut être sous-détecté.
* Paramètres non calés sur des observations : le rétro-test sur les saisons passées reste à faire.
* Récupération météo testée uniquement avec des réponses simulées.
* L'API gratuite d'Open-Meteo est réservée à un usage non commercial.
