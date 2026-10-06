# Moteur mildiou — contaminations primaire et secondaire (v0)

Module pur : une série météo **horaire (UTC)** en entrée, le cycle biologique de la
saison en sortie (primaire : maturation → germination → dispersion → infection → incubation →
taches → sporulation ; secondaire : sporanges → infection des feuilles saines → incubation → taches →
sporulation → …). Tout est recalculé depuis le 1er janvier à chaque appel.

## Fichiers
| Fichier | Rôle |
|---|---|
| `mildiou_primaire.py` | le moteur (primaire **et secondaire**) + ses paramètres (`PARAMS`) + une ligne de commande (le nom du fichier sera changé quand le dépôt accueillera l'oïdium et le botrytis) |
| `oidium.py` | moteur oïdium v1 : cycle par cohortes (latence, sporulation, conidies, infection) au pas horaire, vent, UV, stade phénologique et résistance ontogénique, calcul à rebours des dates d'infection, balayage des amorçages |
| `phenologie_brin_gfv.py` | phénologie en chaîne : débourrement par BRIN, croissance des feuilles (temps thermique) jusqu'à 9 feuilles, puis GFV (floraison, véraison) interpolé sur l'échelle BBCH |
| `stades_bsv_2026.csv` | stades phénologiques 2026 relevés dans les BSV (17 stades, du 07/04 au 15/08) : sert à recaler la phénologie |
| `phenologie.py` | stade BBCH par degrés-jours (base 10 °C depuis le débourrement), calage sur stades observés, surface foliaire, résistance ontogénique des feuilles et des grappes |
| `historique_meteo.py` | historique météo horaire **local** (SQLite) : ne télécharge que les jours manquants, importe et exporte des CSV, compte les appels Open-Meteo |
| `recuperer_meteo_horaire.py` | télécharge la météo horaire d'une position (Open-Meteo) → CSV (validé sur le VPS) |
| `exporter_plasmopy.py` | convertit meteo.csv au format d'entrée de Plasmopy (témoin de comparaison) |
| `diagnostic_infection.py` | retrouve quelles heures somment une force d'infection donnée (ex. celle de Plasmopy) |
| `croiser_maturation.py` | date de maturité du moteur (DJ8) comparée à celle du modèle de Rossi (temps hydro-thermique) |
| `sensibilite_sporulation.py` | carte du critère de sporulation : nuits de sporulation dans une fenêtre selon le seuil d'humidité et la durée |
| `sensibilite_secondaire.py` | rejoue la saison en ne changeant qu'un paramètre secondaire à la fois (survie des sporanges, durée de vie des taches, tolérance) |
| `lire_plasmopy.py` | résume la table d'événements de Plasmopy (une ligne par chaîne distincte) pour la comparer au moteur |
| `configurer_plasmopy.py` | applique les réglages de Plasmopy (main.yaml, secrets.yaml) pour la météo horaire |
| `sensibilite_dispersion.py` | rejoue la saison avec plusieurs critères (dispersion, puis humectation) et les juge contre l'observation de terrain |
| `test_*.py` | 434 tests (météos synthétiques, une règle par test) |

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

## Historique météo local (`historique_meteo.py`)
Le passé d'une saison ne change pas : le télécharger en entier à chaque calcul coûte environ **20 unités d'appel**. Le module le
garde sur disque (`~/epidemio_data/meteo.sqlite3`, ou `$EPIDEMIO_DATA_DIR`) et ne demande que ce qui manque :

| Situation | Appels Open-Meteo | Unités |
|---|---|---|
| Démarrage à froid d'une position (une fois par an) | archive du 1er janvier à J-7, puis prévision | ≈ 20 |
| Démarrage à froid avec un CSV existant (`importer`) | prévision seulement | ≈ 1 |
| Rafraîchissement courant (durée de vie 1 h) | une requête de prévision (jours non consolidés + 7 à venir) | ≈ 1 |
| Consolidation, une fois par jour | une petite requête d'archive | ≈ 1 |

* **Priorité des sources** : `archive` et `import` sont fiables et ne sont jamais écrasées par une prévision ; une `prevision` est
  remplacée par une prévision plus récente, puis par l'archive. Les heures d'archive sans température (jours récents pas encore
  disponibles) ne sont pas stockées.
* **Unités** : une requête de 14 jours et 10 variables au plus vaut 1 unité ; au-delà, au prorata (règle d'Open-Meteo).
* **Deux processus en même temps** (deux workers gunicorn) : une réservation de 2 minutes par position évite le double téléchargement.
* **Sécurité des données** : si la série ne commence pas au 1er janvier, le module refuse de répondre (`HistoriqueIncomplet`) plutôt
  que de laisser le moteur calculer une maturité sur une saison tronquée. Les heures manquantes sont comptées et signalées.
* **Mode sans archive** (`--sans-archive`) : seule l'API de prévision (passé de 92 jours au plus) est appelée, ce qui convient au
  plan **Standard** d'Open-Meteo, dont l'offre n'inclut pas l'API historique. La série doit alors être amorcée par `importer`.

```bash
python3 historique_meteo.py importer meteo.csv --lat 49.25 --lon 3.96 --jusqu-a 2026-09-28   # amorçage sans appel d'archive
python3 historique_meteo.py mettre-a-jour --lat 49.25 --lon 3.96 [--sans-archive] [--force]
python3 historique_meteo.py exporter --lat 49.25 --lon 3.96 --sortie meteo.csv                  # remplace recuperer_meteo_horaire.py
python3 historique_meteo.py etat        # positions stockées, heures, trous
python3 historique_meteo.py usage       # requêtes et unités consommées (30 jours, aujourd'hui)
```
`importer` : sans `--jusqu-a`, les heures sont jugées fiables jusqu'à 7 jours avant la **date du fichier** (pas celle du jour) ;
au-delà ce sont des prévisions, restées corrigeables.

**Offre gratuite d'Open-Meteo** (page tarifs consultée le 05/10/2026) : 600 appels par minute, 5 000 par heure, 10 000 par jour,
300 000 par mois, par adresse IP ; usage **non commercial** uniquement ; attribution requise (CC BY 4.0). Plans commerciaux :
Standard (1 million d'appels par mois, sans API historique) et Professional (5 millions, avec) ; prix annoncés par l'éditeur :
29 et 99 dollars par mois, à confirmer à la souscription.

## Moteur oïdium (`oidium.py`, v0)
L'oïdium se développe dans des conditions météo assez larges et ne se rattache pas à des événements ponctuels (Dubuis et al. 2014) :
le moteur simule donc la **dynamique de l'épidémie** par cohortes, et non des infections datées comme pour le mildiou.

    infection -> LATENCE -> SPORULATION (symptômes visibles) -> conidies -> nouvelles infections -> ...

* **Formalismes** : Garin (2011, mémoire ITK, HAL hal-01877240) d'après Calonnec et al. (2008), Chellemi et Marois (1991) et Caffi et al.
  (2011). Fonction thermique bêta entre 5 et 31 °C (optimum ≈ 26 °C) ; latence = 6 j / F(T) (≈ 6 j à 25 °C, 11 j à 15 °C, bloquée au-delà
  de 31 °C) ; fin de sporulation de ≈ 14 j (15 °C) à ≈ 4,5 j (30 °C) ; infection par les conidies selon T, l'âge de la feuille et l'humidité
  de l'air (nulle sous ≈ 38 %, maximale dès 85 %), réduite par l'eau libre. **Deux valeurs imprimées dans le mémoire sont fautives**
  (b = 0,762 pour la sporulation, lire 0,0762 ; a = 0,0023 pour l'humidité, lire 0,0213) : elles ont été rectifiées d'après ses figures,
  et les durées de latence, de sporulation et le pic d'infection de la figure I sont retrouvés.
* **Infection primaire** (simplifiée) : après le débourrement, pluie >= 2,5 mm sur 6 h avec T >= 10 °C (Gadoury et Pearson 1990) ;
  25 % du stock d'ascospores restant est déchargé à chaque événement ; le stock initial dépend de la sévérité de l'oïdium de l'année
  précédente (0 à 3, catégories du mémoire).
* **Indice Oïdi (Modeline)** : le Comité Champagne publie dans le BSV un indice de risque de sortie d'hiver (modèle Oïdi de la société Modeline,
  adapté du modèle SOV de l'IFV, qui s'appuie sur la météo des deux années précédentes) : tendance du potentiel épidémique de l'année, non
  un modèle journalier. `--indice-oidi 95` l'utilise comme stock d'ascospores de départ (hypothèse v0 : stock = indice / 100, **à caler**) et
  remplace `--severite`. Ses équations ne sont pas publiques.
* **Mortalité thermique** (Peduto et al. 2013 ; Delp 1954) : à partir de 36-38 °C, colonies et conidies perdent de la biomasse/viabilité à un
  taux horaire croissant avec la température (courbe puissance). Une décote de 3 °C est appliquée (microclimat intérieur plus frais que l'air).
  La mortalité est toujours **partielle** : plafonnée à 15 %/h pour les colonies, 25 %/h pour les conidies. À 44 °C, une colonie perd > 75 % de sa
  biomasse en 24 h ; à 40 °C (T_eff = 37 °C), environ 38 % en 5 jours.
* **Chasmothèces** (Legler 2012 ; Gadoury et Pearson 1987) : formation initiée après un cumul de 8 h sous 13 °C, puis intégrée en continu.
  Favorabilité thermique de formation : courbe bêta entre 10 et 30 °C, optimum 22 °C. Production proportionnelle à la surface malade.
  **Indice de fin de saison** (0 à 1) : peut être passé comme `--indice-oidi` de l'année suivante, ce qui ferme la boucle pluriannuelle.
  Le lien quantitatif entre l'intégrale et le nombre réel de chasmothèces par cm² n'est pas calé : seul l'ordre de grandeur a un sens.
* **Pas dans le moteur** : cléistothèces en détail, effet du gel sur le feuillage (paramètre `phenologie.surface_foliaire_max`),
  traitements (comme pour le mildiou, le moteur évalue le danger indépendamment des traitements).
* **Unités relatives** : une capacité d'accueil fixe l'échelle ; seuls les rythmes et les dates ont un sens avant calage. Le mémoire est un
  prototype **non validé** sur des observations de terrain.

```bash
python3 oidium.py meteo.csv --debourrement 2026-03-28 --retro 2026-07-20 2026-08-06 2026-09-03 --tolerance 4   # à rebours
python3 oidium.py meteo.csv --debourrement 2026-03-28 --balayage 2026-04-05 2026-07-10    # générations simulées pour des amorçages successifs
python3 oidium.py meteo.csv --debourrement 2026-03-28 --indice-oidi 95 --pas 7            # simulation complète (indice Oïdi 2026)
```
Le calcul **à rebours** ne dépend ni de l'inoculum ni des traitements : il utilise seulement l'horloge de la latence avec la météo réelle, puis
indique si les jours d'infection ainsi reconstitués étaient réellement favorables. Le **rang** compare leur favorabilité à celle des jours du
1er mai (ou du débourrement, s'il est plus tardif) au 30 septembre : inclure un mois d'avril froid flatterait n'importe quel jour un peu favorable.
Le débourrement par défaut est le 15 avril : à préciser avec `--debourrement` (en 2026 : **28 mars**).

**Vérité terrain 2026 (clients, Champagne, sous programme phytosanitaire)** : débourrement le **28 mars** ; indice Oïdi de sortie d'hiver **95/100** (« élevé », BSV n°1 du 7 avril 2026), mais projections d'ascospores très faibles et localisées observées au vignoble et 38 % des bourgeons détruits par le gel ; premiers symptômes d'oïdium vers le **20 juillet**, puis vers
le **6 août**, puis explosion début **septembre**. Elle se juge sur le rappel (les périodes d'infection doivent être favorables), non sur
les fausses alertes, la protection pouvant retarder ou masquer les symptômes.

### Facteurs externes (v1) : vent, ultraviolets, stade phénologique, résistance ontogénique
Tous facultatifs et **neutres quand la donnée manque** (sans vent ni rayonnement dans le CSV, et sur une série qui ne couvre pas le
débourrement, le moteur se comporte exactement comme avant). Leurs paramètres sont des **hypothèses de travail**, non calées.

* **Vent** (Eq. 18 du mémoire, d'après Willocquet et al. 1998) : les conidies s'accumulent sur les colonies, perdent 1 % de viabilité par
  heure, et ne sont libérées que par le vent. La libération est rapportée à une vitesse de référence (4 m/s à 10 m, soit 2 m/s dans le
  feuillage : facteur 0,5) : de 47 % des conidies qui partent au calme à 98 % par grand vent, 67 % à la référence. `c_emit` n'a donc pas
  exactement le même sens avec et sans vent.
* **Ultraviolets** (Austin et Wilcox 2010) : le rayonnement global (W/m²) sert de proxy. À plein soleil (800 W/m²), la moitié du feuillage
  est exposée : les conidies y meurent à 12 % par heure et l'infection comme la favorabilité baissent de 60 %, soit -30 % au total.
* **Phénologie** : stade BBCH estimé en degrés-jours (DJC, moyenne journalière, base 10 °C) cumulés depuis le débourrement ; table
  ancrée sur ton référentiel (maturité à **1 250 DJC**). Contrôle fait avec les températures réelles de 2026 : 1 250 DJC sont atteints
  le **23 août**, comme l'estimation vendanges. Les seuils intermédiaires sont des approximations à recaler sur tes relevés de stade :
  `--bbch 2026-05-15:17 2026-06-12:65 ...` remplace les seuils par ceux observés et garde la table cohérente.
* **Résistance ontogénique** (Gadoury et al. 2003 ; VitiMeteo-Oidium ; BSV Champagne : risque maximal de « 7-8 feuilles » à « grains de
  pois ») : sensibilité des **grappes** maximale de la floraison à la nouaison, 60 % aux grains de pois, 20 % à la fermeture de la grappe,
  10 % ensuite ; sensibilité des **feuilles** douce en fin de saison (50 % à la maturité). Le moteur sort un **indice grappes**
  (favorabilité × sensibilité) et la fenêtre de réceptivité des grappes (sensibilité >= 50 %).
* **Surface foliaire** : la capacité d'accueil des colonies suit la surface foliaire relative (2 % au débourrement, 100 % à la fermeture
  de la grappe). Au début de saison, quelques colonies couvrent donc une fraction du feuillage bien plus grande.

#### Phénologie en chaîne : BRIN -> croissance végétative -> GFV (`phenologie_brin_gfv.py`)
Un seul modèle ne suit pas toute la saison : on enchaîne trois modèles publiés, chacun là où il est le plus fiable.

| Phase | Modèle | Principe | Paramètres Chardonnay |
|---|---|---|---|
| **Débourrement** (BBCH 09) | **BRIN** (García de Cortázar-Atauri et al. 2009) | dormance : froid de Bidabé, `Q10^(-Tmax/10) + Q10^(-Tmin/10)` par jour depuis le 1er août précédent ; puis forçage horaire de Richardson `max(min(T-5, 25-5), 0)` | Q10 = 2,17 ; froid critique 101,2 ; forçage critique 6 576,7 °C·h |
| **Croissance végétative** (09 à 19) | émission des feuilles linéaire en temps thermique (Lebon et al. 2004) | n feuilles = temps thermique base 10 °C / phyllochrone ; BBCH = 10 + n ; surface foliaire déduite (SFE relative) | phyllochrone ≈ 24 °C·j |
| **De 9 feuilles à la véraison** (19 à 83) | **GFV** (Parker et al. 2011) | somme (Tmin+Tmax)/2 à base 0 °C depuis le 1er mars ; BBCH interpolé entre 9 feuilles, la floraison (65) et la véraison (83) | floraison 1 217 ; véraison 2 547 |

* **SFE** désigne ici la surface foliaire exposée (Carbonneau 1983) : le modèle fournit sa **dynamique relative** (0 à 1) à partir du nombre de
  feuilles. Une SFE en m²/m² exigerait la géométrie du palissage (écartement, hauteur et épaisseur du feuillage), non demandée pour l'oïdium.
* **Dormance** : sans l'été et l'automne précédents dans la série (le CSV part du 1er janvier), elle est **supposée levée** au premier jour et BRIN ne
  calcule que le forçage ; ses auteurs montrent que la température de base pèse plus que la dormance. Avec une série qui part du 1er août, elle est calculée.
* **Débourrement observé** : il prime toujours sur le calculé, et l'écart est rapporté. **Observations de stade** (`--bbch DATE:STADE`) : un stade de
  feuilles (11 à 19) ajuste le phyllochrone ; un stade à partir de 53 remplace le repère GFV (65 -> floraison, 83 -> véraison) ; des observations
  incompatibles avec l'ordre des stades sont refusées avec un message clair.
* **Approximations** : paramètres du seul Chardonnay ; la table du froid et du forçage critiques a été calée avec des températures horaires
  reconstituées à partir de Tmin et Tmax ; les stades intermédiaires (53, 57, 61, 71, 75, 77, 79, 81) sont placés par fractions de l'intervalle ;
  après la véraison (BBCH 83) le stade reste constant ; l'heure d'été est prise en compte (un jour de 23 h cumule 23/24 d'un jour de temps thermique).

```bash
python3 oidium.py meteo6.csv --debourrement 2026-03-28 --calendrier                   # compare la table DJC et BRIN + GFV, stade par stade
python3 oidium.py meteo6.csv --phenologie brin_gfv --debourrement 2026-03-28 --indice-oidi 95   # simulation sur le stade BRIN + GFV
python3 oidium.py meteo6.csv --phenologie brin_gfv --indice-oidi 95                    # BRIN estime lui-même le débourrement
python3 oidium.py meteo6.csv --calendrier --bbch 2026-05-12:15 2026-06-10:65          # recale les deux modèles sur tes relevés
```
**Comparaison 2026** (températures reconstituées à partir d'un point par semaine : approximatif, à refaire sur le CSV complet). BRIN calcule un
débourrement au **23 mars**, à 5 jours du 28 mars observé et à 3 jours de la référence CIVC du 20 mars. Les deux modèles, indépendants (table de
degrés-jours calée sur 1 250 DJC à la maturité ; chaîne BRIN + feuilles + GFV tirée de la littérature), concordent à 0-7 jours près :
fermeture de la grappe les 14 et 15 juillet, grains de pois les 29 et 30 juin, grappes réceptives du 24 mai au 2 juillet contre du 29 mai au 3 juillet.
Les stades de feuilles divergent le plus (4 feuilles le 23 contre le 30 avril) : c'est le phyllochrone, à recaler.

#### Recalage sur les stades observés (BSV, relevés)
`--stades fichier.csv` (format `date,bbch[,note]`, fourchettes « 57-60 » acceptées, lignes `#` ignorées ; cumulable avec `--bbch`) recale les deux modèles
sur des stades observés. Avec `--calendrier`, la sortie affiche d'abord le **biais des modèles par défaut** (écart en jours à chaque stade observé,
+ = modèle en retard), puis les calendriers recalés et la fenêtre de réceptivité des grappes.

* **Feuilles** : avec au moins 3 stades de feuilles, la **base thermique** et le phyllochrone sont ajustés (grille de bases de 0 à 10 °C ; à erreur égale,
  la base la plus haute). Les stades entre deux stades observés gardent leur position relative, comprimée entre eux : pas de palier ni de saut.
* **Prudence** : les stades d'un BSV sont hebdomadaires et moyennent des secteurs précoces et tardifs (±3 à 5 jours). Recaler une saison corrige
  CETTE saison (suivi en cours de campagne) ; des paramètres durables exigeront plusieurs saisons. Les valeurs de la littérature restent le défaut.

```bash
python3 oidium.py meteo6.csv --debourrement 2026-03-28 --calendrier --stades stades_bsv_2026.csv
python3 oidium.py meteo6.csv --phenologie brin_gfv --debourrement 2026-03-28 --stades stades_bsv_2026.csv --indice-oidi 95
```
**Fractions des stades intermédiaires** recalculées sur les 17 stades BSV 2026 :
les stades post-floraison arrivent beaucoup plus vite après la floraison que ce que supposait une distribution linéaire : la nouaison (BBCH 71) à 3 % de
l'intervalle floraison-maturité, la fermeture de la grappe (BBCH 79) à 48 %, le début de véraison (BBCH 81) à 70 %. Ces valeurs corrigent un décalage de
~10 jours observé sur la fermeture de la grappe en 2025 (observée le 13 juillet vs modèle à 24 juillet avec les anciennes fractions). Elles ne proviennent
que de la saison 2026 et resteront à consolider sur d'autres années.

**Résultat 2026** (17 stades du BSV ; températures reconstituées à partir d'un point par semaine : approximatif, à refaire sur le CSV complet) :
* les deux modèles par défaut sont **en retard** sur les stades observés : table DJC de 8,8 jours en moyenne, BRIN + GFV de 12,1 jours, surtout pour les
  feuilles (jusqu'à 20 jours) et de la floraison aux grains de pois (13 à 17 jours) ; ils rattrapent à la véraison (3 à 5 jours) ;
* feuilles : la base de 10 °C de la littérature ajuste mal (erreur de 0,36 feuille) ; l'ajustement donne **une base de 3 °C et un phyllochrone de 46 °C·j**
  (erreur de 0,22 feuille) ; 9 feuilles le 14 mai (observé : le 12) ;
* GFV recalé : floraison à **1 068** de somme (littérature : 1 217, soit 12 % de moins) et véraison à **2 538** (littérature : 2 547) ;
* **fenêtre de réceptivité des grappes (sensibilité >= 50 %) : du 19 mai au 19 juin** d'après les stades observés, au lieu du 24-29 mai au 2-3 juillet des
  modèles par défaut. Elle ne dépend pas des températures reconstituées : elle vient directement des stades BBCH 53 (19/05) et 75-77 (16-23/06).

**Obtenir le vent et le rayonnement** (les CSV et la base existants restent valables ; la base SQLite est migrée à l'ouverture, sans perte) :
```bash
python3 recuperer_meteo_horaire.py --lat 49.25 --lon 3.96 --sortie meteo6.csv --verifier   # une fois : ≈ 20 unités d'appel (archive)
python3 historique_meteo.py importer meteo6.csv --lat 49.25 --lon 3.96 --jusqu-a 2026-10-01 # remplit la base ; un ancien CSV n'efface rien
python3 oidium.py meteo6.csv --debourrement 2026-03-28 --calendrier                         # calendrier phénologique estimé
python3 oidium.py meteo6.csv --debourrement 2026-03-28 --indice-oidi 95 --pas 7            # simulation complète
python3 oidium.py meteo6.csv --debourrement 2026-03-28 --indice-oidi 95 --sans-vent --sans-uv   # pour mesurer l'effet de chaque facteur
```
Options : `--sans-vent`, `--sans-uv`, `--sans-phenologie`, `--bbch DATE:STADE ...`, `--calendrier`. Sans accès à l'archive (plan sans API
historique), seuls les 92 derniers jours de vent et de rayonnement sont disponibles : le moteur le signale et traite les autres heures
comme neutres. Le pont Pilot ne lit pas encore ces colonnes (le mildiou n'en a pas besoin) : à brancher avec l'oïdium.

### Premiers résultats 2026 (v0, non calé ; Reims, indice Oïdi 95, débourrement 28 mars)
* **Simulation complète** : 11 infections primaires, dont **une seule en avril** (11/04) et cinq du 2 au 10 mai, ce qui est cohérent avec les
  projections d'ascospores « très faibles et localisées » observées au printemps. Premiers symptômes repérables le 11/06, 10 % du feuillage le
  24/06, 50 % le 04/07. **Beaucoup trop précoce et explosif par rapport aux observations** (premiers symptômes le 20/07) : attendu, car le
  moteur ne simule pas les traitements et l'émission de conidies n'est pas calée. Les valeurs absolues de « feuillage atteint » n'ont
  aucun sens avant calage sur des parcelles non traitées ; seuls les rythmes et la favorabilité relative en ont.
* **Indice sur 7 jours** : maximum des points hebdomadaires affichés le 3 septembre (45 %), minimum le 16 juillet (14 %).
* **À rebours** (tolérance 4 j, délai de détection 0) :
  | symptômes observés | infections correspondantes | favorabilité | rang (1er mai - 30 sept.) |
  |---|---|---|---|
  | 20 juillet | 8 au 16 juillet | 15,9 % | meilleure que 11 % des jours |
  | 6 août | 26 juillet au 2 août | 30,8 % | 48 % |
  | début septembre (3/09) | 22 au 30 août | 43,1 % | 78 % |

  L'explosion de début septembre est **soutenue par la météo** ; le 6 août est plausible ; le 20 juillet n'est **pas expliqué** à délai 0
  (infections reconstituées parmi les jours les moins favorables). Piste : les colonies ne sont repérables que quelques jours après la fin de
  la latence (`--delai`), ou les symptômes proviennent d'infections plus anciennes restées masquées (traitements). À tester avec la
  sensibilité au délai, que `--retro` affiche désormais.

```bash
python3 oidium.py meteo.csv --debourrement 2026-03-28 --retro 2026-07-20 2026-08-06 2026-09-03 --tolerance 4 --delai 3
```

## Infections secondaires (v0)
**Vocabulaire : trois étapes à ne pas confondre.**
1. **Fructification** (ce que le moteur et les textes de référence appellent « sporulation ») : les sporanges *apparaissent*
   sur la tache, c'est le duvet blanc. Le texte suisse le dit (« les sporanges apparaissent si HR > 92 %, T >= 12 °C,
   4 h, dans l'obscurité ») ; Plasmopy aussi (étape « Sporulation », suivie de la densité de sporanges). Le critère
   HR / durée porte donc sur l'apparition du duvet blanc, que le terrain permet d'observer.
2. **Libération des sporanges** (détachement et dispersion par la pluie et le vent) : des sporanges peuvent se former sans
   être détachés ni dispersés. Le moteur ne la modélise que par l'option `pluie_detachement_mm` ; par défaut, comme
   Plasmopy et Rossi et al. 2021, il suppose qu'elle a toujours lieu.
3. **Libération des zoospores et infection** : lorsqu'un sporange arrive sur une feuille saine mouillée (T × h >= 50).
   C'est l'infection secondaire du moteur.
Observer une fructification valide donc l'étape 1 seulement ; elle ne dit rien des étapes 2 et 3.

Après la première sporulation d'une tache, le moteur enchaîne les générations suivantes :
* **Sources** : chaque *nuit* de sporulation d'une tache (HR ≥ 92 %, T ≥ 12 °C, ≥ 4 h d'obscurité, pendant les 15 jours
  de vie de la tache) produit des sporanges, disponibles tant qu'ils survivent (voir plus bas). Toutes les nuits comptent.
* **Infection** : tant qu'un sporange est disponible, chaque période d'humectation (feuille mouillée, 3 ≤ T ≤ 29 °C)
  cumule des degrés-heures **T × h** ; à **50 °C·h** et ≥ 1 h de mouillage, une infection secondaire est acquise
  (une par période d'humectation). Une feuille qui sèche plus d'1 h (`tolerance_h`) perd la période.
* **Suite** : incubation (table de Goidanich, comme le primaire) → taches → nuits de sporulation → nouvelles sources.
  Chaque événement porte sa **génération** (1 = issu de taches primaires ; 2 = issu de taches secondaires…, génération
  minimale parmi les sources disponibles) et sa **source** (`P3` = taches du cycle primaire n°3, `S5` = taches de
  l'infection secondaire n°5).
* **Sortie** : `res["secondaires"]` (événements) et, par jour, `force_secondaire_dh` (degrés-heures cumulés sur les heures
  mouillées où des sporanges étaient disponibles : le danger potentiel du jour, qu'une période atteigne 50 °C·h ou non).
  Désactivable : `secondaire.actif = False` ou `--sans-secondaire`.

| Paramètre | Valeur | Origine |
|---|---|---|
| Plage de température | 3 à 29 °C | [P] Plasmopy ; Rossi et al. 2021 : 4,0 / 21,0 / 30,2 °C (min. / optimum / max.) |
| Formule | produit T × h (base 0) | [F] texte suisse : « température × durée d'humectation = 50 » |
| Seuil | 50 °C·h | [F][P] ; Rossi et al. 2021 : 2 h à l'optimum de 21 °C (≈ 2,4 h pour 50 °C·h) |
| Mouillage minimal | 1 h | [P] ≥ 60 min |
| Tolérance d'interruption | 1 h | [R] Rossi et al. 2021 : humectation continue ou interrompue au plus 1 h |
| Survie des sporanges | selon `T × (1 − HR/100)` : 2 à 9 jours (autres lois : Vinemild 7 à 9 jours, durée fixe) | [R] Blaeser & Weltzien 1979, via Brischetto et al. 2020 et Franche 2012 |
| Durée de vie d'une tache | 15 jours | [F] Orlandini et al. 2008, citée dans le texte de travail |
| Condition de pluie pour la dispersion | aucune | [R] sporanges présents dans l'air hors pluie (Caffi et al. 2013 ; Rossi et al. 2021) ; Plasmopy : idem |

**Survie des sporanges** (`secondaire.survie`, trois lois au choix). Chaque nuit de sporulation lance une cohorte de sporanges,
disponible jusqu'à ce que sa mortalité cumulée atteigne 1.
* `"vpd"` (défaut) : chaque heure, une fraction `1 / (24 × S)` meurt, où `S` est la survie en jours, trinôme de
  `V = T × (1 − HR/100)` (Steubing 1965) : sporanges attachés `S = 9,27 − 1,12·V + 0,04·V²`, détachés
  `S = 5,67 − 0,47·V + 0,02·V²` (moyenne des deux, comme Brischetto 2020). De l'ordre de 2 à 9 jours.
  La forme `1 / (24 × S)` est confirmée par deux sources indépendantes (Brischetto 2020 ; Franche 2012, Eq. 7).
  **Correction** : le moteur utilisait d'abord le déficit de saturation physique (Buck) ; Brischetto 2020 et Franche 2012
  définissent tous deux `V = T × (1 − HR/100)` pour ces trinômes, c'est donc la définition retenue (`formule_vpd`).
  **Coefficient à trancher** : Franche 2012 (d'après Rossi) donne `0,01` et non `0,02` pour le terme en `V²` des sporanges
  détachés, ce qui donne une vie de 6 h à 6 jours (valeurs que Rossi cite) au lieu de 3 à 5 jours.
* `"vinemild"` : courbe de Blaise & Gessler (1990), annexe 5 de Franche 2012 : mortalité horaire de 4,5 à 9 pour mille selon
  T et HR, soit **7 à 9 jours** dans les conditions de Champagne. Valeurs lues sur le graphique (précision ≈ 2 %).
* `"fixe"` (`--vie-sporanges H`) : durée constante, pour les comparaisons.

Au-delà du V qui minimise le trinôme (14 et 11,75) la survie est maintenue constante : la parabole remonte ensuite, ce qui
n'a pas de sens physique.

**Options issues de Franche 2012**, désactivées par défaut (le balayage `sensibilite_secondaire.py` en mesure l'effet) :
* `pluie_detachement_mm` : un sporange ne se détache qu'après une pluie horaire >= ce seuil (Franche : 0,2 mm). Sans cette
  option, comme Plasmopy et Rossi et al. 2021, les sporanges sont dans l'air même sans pluie. Dans le mémoire, ce seuil
  change fortement les résultats (18 contre 2 contaminations secondaires selon le seuil).
* `productivite_min` : productivité relative d'une tache `RS = exp(5,3 − 0,7·n) / 100` à sa n-ième sporulation (Kennelly 2007),
  divisée par 2 à chaque nuit ; seuls les sporulations dont RS reste >= ce seuil comptent (0,1 : 4 nuits).

**Limites connues de la v0**
* **La densité de sporanges n'est pas modélisée** : un sporange est disponible ou non, sans quantité. Le déclin de productivité
  d'une tache (de moitié à chaque sporulation, Kennelly 2007 d'après Franche 2012) existe en option (`productivite_min`),
  désactivée par défaut. Plasmopy module en plus la production par la température (11 à 17,5 °C) avec une latence de 4 h.
* **Sporulation** : trois critères publiés coexistent et pèsent lourd sur le résultat (voir `sensibilite_secondaire.py`) :
  texte de travail et Plasmopy (HR >= 92 %, T >= 12 °C, 4 h d'obscurité continue, le critère retenu), Franche d'après
  Lalancette (HR > 90 % pendant au moins 6 h de nuit), Rossi et al. 2021 (>= 3 h humides, 10 à 30 °C, HR >= 80 %).
* Une infection par période d'humectation, quel que soit le nombre de taches sources.
* Non validé contre Plasmopy à ce jour : `python3 lire_plasmopy.py <events_table.csv> --secondaire` affiche ses sporanges, la
  durée de vie de ses spores et ses infections secondaires.

**Premier essai contre Plasmopy** (météo Open-Meteo, Reims 2026, profil `calage_2026`). Plasmopy écrit **une seule**
infection secondaire par chaîne (la première après la sporulation) ; nous listons toutes les périodes d'humectation.

| Chaîne | Plasmopy | Moteur | Verdict |
|---|---|---|---|
| Germination du 19/08 | sporulation 27/08 23 h → infection secondaire **28/08 01 h** (341,0 °C·h) | infection **28/08 00 h** (377,5), source P17 | à 1 h près |
| Germination du 27/08 | sporulation 12/09 05 h → infection secondaire **13/09 11 h** (133,7) | infection **13/09 11 h** (98,4), source P20 | identique à l'heure |
| Germinations des 26/06 et 29/06 | sporulation 19/08 04 h → infection secondaire 19/08 06 h (433,7) | aucune | écart voulu : taches de juin limitées à 15 jours |
| Infection du 12/09 07 h (52,3) | absente | présente (source P20) | non expliqué (Plasmopy : une seule par chaîne ? seuil ?) |
| Durée de vie des spores | 12,5 / 17,1 / 15,7 jours | 3,5 à 5,4 jours (équations de Blaeser & Weltzien) | écart important, voir ci-dessous |

* Total moteur : 13 infections secondaires (11 de génération 1, 2 de génération 2). Aucune avant le 28/08 : avec HR >= 92 %,
  aucune nuit de sporulation pendant la vie des taches de mai-juin. **Ce critère a ensuite été écarté** : le terrain montre
  une fructification faible mais non nulle (voir « Observation de terrain »).
* Plasmopy : `sporangia_densities` vaut 300000 pour toutes les chaînes (valeur plafond, non utile à la comparaison).
* **Durée de vie des spores** : celle de Plasmopy (12 à 17 jours) dépasse la littérature (2 à 9 jours selon Brischetto 2020 ;
  zoospores libérés jusqu'à 7 jours, plus d'infection à 10 jours selon Kast & Stark-Urnau 1999).

**Ce que dit le `main.yaml` de Plasmopy** (paramètres lus, code non consulté) :

| Bloc | Plasmopy | Moteur |
|---|---|---|
| Sporulation | HR ≥ 92 %, T ≥ 12 °C, ≥ 4 h d'obscurité continue, aucune condition d'humectation | identique |
| Infection secondaire | 3 à 29 °C, mouillage ≥ 60 min, 50 °C·h | identique |
| Sporanges | latence de 4 h après la sporulation ; production entre 11 et 17,5 °C ; densité maximale 300000 (atteinte par les 4 chaînes) | non modélisés (disponible ou non) |
| Durée de vie des spores | « constante empirique » 11,35 et pression de vapeur ; **formule non publiée** (les articles précisent que celle de VitiMeteo n'a jamais été décrite) | équations publiées de Blaeser & Weltzien (3,5 à 5,4 jours) |

* Hypothèse sur l'infection du 12/09 07 h (présente chez nous, absente chez Plasmopy) : sa latence de 4 h retarderait la
  disponibilité des sporanges, donc l'accumulation des 50 °C·h ; nos sources démarrent à l'heure de sporulation. Non testée.
* Les heures de sporulation de Plasmopy sont décalées de 1 h par rapport aux nôtres (12/09 05 h contre 04 h) : convention
  d'horodatage (début ou fin d'heure) probable.
* Impact de ces incertitudes : `python3 sensibilite_secondaire.py meteo.csv --lat 49.25 --lon 3.96 --profil calage_2026`.

## Recoupement avec Franche (2012), mémoire de stage ITK (modèle du Vintel)
Source : J.-C. Franche, *Modélisation du cycle du mildiou de la vigne dans un OAD : intégration de nouveaux formalismes*,
Master FAGE, Université de Lorraine, 2012 (HAL hal-01871194). Modèle mécaniste à pas journalier fondé sur Rossi 2008,
Orlandini 2008, Lalancette 1987-88, Kennelly 2007 et Vinemild.

| Étape | Franche / ITK | Moteur | Plasmopy |
|---|---|---|---|
| Maturation | Rossi 2008 : temps hydro-thermique + Gompertz, période d'inoculum de 3 à 97 % ; cite Gehman (DJ base 8) | DJ8 >= 140 | idem moteur |
| Dispersion primaire | pluie horaire **0,2 mm suffit**, indépendamment de la durée (Rossi & Caffi 2012) | > 5 mm / 6 h (calé) ; texte : > 3 mm/h | 3 mm |
| Durée de vie d'une tache | 15 jours (Orlandini 2008) | 15 jours | illimitée |
| Sporulation | nuit, **HR > 90 %, >= 6 h** (Lalancette 1987) ; productivité divisée par 2 à chaque sporulation | 4 h continues, HR >= 92 %, T >= 12 °C ; pas de déclin | idem moteur |
| Détachement des conidies | pluie horaire >= 0,2 mm | aucune condition | aucune |
| Survie des conidies | Vinemild (annexe 5) : 7 à 9 jours | Blaeser : 2 à 9 jours ; Vinemild au choix | 12 à 17 jours |
| Infection secondaire | efficacité de Lalancette 1988 selon humectation et température | T × h >= 50, 3 à 29 °C | idem moteur |
| Incubation | Plasmo (Orlandini 2008), selon T **et HR** ; paramètres non donnés | Goidanich, selon T | non lue |
| Humectation | M de Rossi : pluie > 0 ou VPD <= 4,5 hPa (≈ HR 81 % à 20 °C) | pluie >= 0,1 mm, HR >= 90 %, T − Td <= 1 °C | capteur |

**Constats**
* Le mémoire confirme notre dispersion calée : le seuil de 3 mm/h est trop restrictif, Rossi retient 0,2 mm/h. Le cumul
  6 h / 5 mm est un compromis entre cette valeur et les données horaires lissées de la réanalyse.
* L'humectation de Rossi (VPD <= 4,5 hPa, soit HR >= 81 % à 20 °C) est plus permissive que notre seuil de 90 % : elle
  allongerait les périodes d'infection.
* **Maturation** : `croiser_maturation.py` compare notre date (DJ8) à celle de Rossi, méthode indépendante.
* **Mise en garde du mémoire** : avec une météo maillée, les pluies sont plus fréquentes que sur station, d'où trop de
  contaminations simulées. Chez nous le défaut inverse est possible (pointes de pluie lissées) ; dans les deux cas la qualité
  de la météo domine. Leur validation de dates et d'incubation n'est bonne que lorsque la météo est concordante.
* L'intensité de la maladie en fin de saison dépend avant tout des contaminations **secondaires** (leur conclusion).

## Résultats sur la saison 2026 (Reims, météo Open-Meteo, profil `calage_2026`)

**Maturation croisée** (`croiser_maturation.py`) : moteur (DJ8 >= 140) **23 avril** ; Rossi (temps hydro-thermique) 3 % le
11 février, **50 % le 27 avril**, 97 % le 18 juillet. Les deux méthodes indépendantes concordent à 4 jours près sur le point
médian : notre « maturité acquise le 23 avril » correspond à environ la moitié du stock d'oospores de Rossi, pas à 100 %.

**Sensibilité des infections secondaires** (`sensibilite_secondaire.py`, un paramètre à la fois) :

| Paramètre | Valeurs testées | 1re infection | Événements | Verdict |
|---|---|---|---|---|
| Survie des sporanges | VPD, Franche 0,01, Vinemild, fixe 2 à 15 jours | **28/08 partout** | 8 à 23 (défaut 14) | change la fréquence en sept.-oct., pas la date |
| Détachement par la pluie | aucune, >= 0,2 mm/h, >= 1 mm/h | 28/08 partout | 14, 12, 9 | effet faible sur cette saison |
| Productivité des taches | toutes les nuits, RS >= 0,25 / 0,1 / 0,05 | 28/08 partout | 14 partout | sans effet sur cette saison |
| Tolérance d'interruption | 0 à 3 h | 28/08 partout | 14 à 15 | sans effet |
| Durée de vie des taches | 7, 10, 15, 20, 30 jours, illimitée | 28/08 (20/08 si illimitée) | 8, 12, 14, 14, 14, 21 | 15 jours suffisent ; l'illimité ajoute 7 infections en août |
| **Conditions de sporulation** | texte/Plasmopy ; Franche (6 h, HR > 90 %) ; Rossi (3 h, HR >= 80 %, T >= 10 °C) | **28/08 ; 03/06 ; 22/05** | 14 ; 11 ; 38 | **le paramètre décisif** |

La date de la première infection secondaire ne dépend que du critère de sporulation. Avec celui du texte de travail et de
Plasmopy (HR >= 92 %), aucune nuit de sporulation n'a lieu pendant la vie des taches de mai-juin : ce qui **contredit** le
terrain, où une fructification faible mais non nulle a été vue (voir « Observation de terrain »).

**Observation de terrain** (clients de Florent, même programme phytosanitaire, 2026) : sortie de taches d'huile en mai-juin
**sans fructification marquée, mais pas nulle**. Elle écarte les deux extrêmes : le critère du texte de travail prédit zéro
sporulation (trop strict), celui de Rossi en prédit beaucoup (38 infections secondaires dont 13 en juin : trop permissif).
Réserve : le programme phytosanitaire peut lui-même limiter la fructification visible, et le duvet blanc ne se voit qu'au
petit matin. `sensibilite_sporulation.py` cartographie l'espace entre les critères : la sortie du moteur liste désormais
toutes les nuits de sporulation de chaque tache (`sporulations`).

**Carte du critère de sporulation** (`sensibilite_sporulation.py`, saison 2026, fenêtre du 20/05 au 30/06). Nuits de
sporulation distinctes / taches concernées :

| Humidité minimale | 3 h | 4 h | 5 h | 6 h |
|---|---|---|---|---|
| HR ≥ 80 % | 18 / 5 | 13 / 5 | 8 / 5 | 6 / 5 |
| HR ≥ 85 % | 10 / 5 | 6 / 5 | 4 / 5 | 4 / 5 |
| HR ≥ 88 % | 5 / 5 | 3 / 4 | 3 / 4 | 2 / 4 |
| HR ≥ 90 % | 3 / 4 | **2 / 4** | 2 / 4 | 1 / 3 |
| HR ≥ 92 % | 2 / 4 | **0 / 0** (ancien critère) | 0 / 0 | 0 / 0 |
| HR ≥ 94 % | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

* Le critère du texte de travail et de Plasmopy (HR ≥ 92 %, 4 h) est au bord d'une falaise : 0 nuit, alors que 90 % ou 3 h
  en donnent 2. Un seuil aussi serré est fragile face à l'erreur de l'humidité d'une réanalyse (quelques %).
* La zone « faible mais non nulle » (1 à 3 nuits) couvre HR 88 à 92 % pour 3 à 6 h. **Dans toutes ces cases, la première
  infection secondaire est le 03/06** (et non le 28/08) ; le nombre total d'infections varie de 11 à 23.
* **Ce que comptent les cases** : « nuits distinctes / taches » ne compte pas des taches d'huile isolées mais des **cycles
  primaires**, c'est-à-dire des épisodes d'infection dont les taches apparaissent ensemble (une cohorte) et restent vivantes
  15 jours. Chaque cycle sporule à chaque nuit favorable pendant cette durée ; une même nuit peut donc servir plusieurs
  cycles. « 2 / 4 » signifie : 2 nuits favorables dans la fenêtre, 4 cohortes de taches qui en ont profité.
  `--detail HR DUREE` (ex. `--detail 90 4`) liste ces cycles avec leurs dates d'infection, d'apparition des taches et de
  sporulation, y compris ceux dont les taches sont vivantes mais sans nuit favorable.
* **Détail réel, saison 2026** (`--detail 90 4`, fenêtre 20/05 au 30/06). Cinq cycles primaires ont des taches vivantes :

| Cycle | Infection | Taches visibles | Vivantes jusqu'au | Nuit de sporulation |
|---|---|---|---|---|
| #1 | 03/05 | 21/05 | 05/06 | 02/06 |
| #3 | 10/05 | 24/05 | 08/06 | 02/06 |
| #5 | 17/05 | 24/05 | 08/06 | 02/06 |
| #8 | 02/06 | 12/06 | 27/06 | 18/06 |
| #13 | 19/06 | 24/06 | 09/07 | aucune |

  Les trois cohortes de mai apparaissent du 21 au 24 mai, ce qui concorde avec les taches observées du 20/05 au 06/06.
  **Une seule nuit, le 02/06, fait sporuler les trois à la fois** : la fructification de mai est donc « faible mais non
  nulle » au sens strict, ce qui concorde avec l'observation. À HR >= 92 % les cinq cycles n'ont aucune nuit (0 / 5).
  **L'observation ne valide que l'étape de sporulation.** Une fructification n'est pas une contamination secondaire :
  celle-ci exige encore des sporanges dispersés et survivants, une période d'humectation de 50 °C·h sur tissu sain, et
  le programme phytosanitaire peut l'empêcher. La première infection secondaire du modèle (03/06) est un **danger
  conditionnel** (conditions réunies si les sporanges de la nuit du 02/06 existent), qui repose sur une seule nuit
  marginale (HR entre 90 et 92 %) et que les observations disponibles ne permettent ni de confirmer ni d'infirmer.
* **Effet de la libération des sporanges** (`--pluie-detachement 0,2`, profil à 90 %, saison 2026) :

| | Sans condition de pluie (défaut) | Pluie >= 0,2 mm/h après la sporulation |
|---|---|---|
| Infections secondaires | 22 (génération 1 : 20, génération 2 : 2) | 17 (15 et 2) |
| Première infection | 03/06 02 h (149 °C·h), puis 03/06 17 h (72 °C·h) | **04/06 00 h (342,5 °C·h)** |
| Suivante | 04/06 00 h (342,5 °C·h) | 06/06 14 h (130 °C·h) |

  Exiger une pluie ne fait pas disparaître l'infection : elle la décale d'environ une journée. Les deux périodes d'humidité
  du 03/06 (sans pluie) disparaissent ; la période pluvieuse du 04/06 (342,5 °C·h, soit près de 7 fois le seuil) subsiste
  dans les deux cas. L'infection est donc solide côté humectation, et fragile seulement côté source (une nuit marginale).
* Profil `calage_2026` : seuil d'humidité ramené de 92 à 90 % (durée 4 h inchangée). Les résultats de sensibilité ci-dessus
  (secondaire) ont été obtenus AVANT cet ajustement, avec 92 % ; à refaire.

**Références** (accès libre) : Brischetto, Bove, Fedele, Rossi (2021), *Front. Plant Sci.* 12:636607 ; Brischetto, Bove,
Languasco, Rossi (2020), *Front. Plant Sci.* 11:1187 ; Kennelly et al. (2007), *Phytopathology* 97:512 ; Caffi et al. (2013),
*Phytopathology* 103:64 ; Orlandini, Massetti, Dalla Marta (2008), *Comput. Electron. Agric.* 64:149 ;
Blaeser & Weltzien (1979), *J. Plant Dis. Prot.* 86:489.

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
| Sporulation | **HR ≥ 90 %** (texte et Plasmopy : 92 %), T ≥ 12 °C, 4 h de nuit continues | **calé** sur l'observation de terrain 2026 et Lalancette ; voir la carte du critère |
| Durée de vie d'une tache | 15 jours | Orlandini et al. 2008 (la référence Plasmopy n'en a pas) |

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
`--fenetre-spor` (durée de vie d'une tache, en jours ; défaut 15), `--cumul` (maturation), `--dh` (formule d'infection),
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

**Ce que l'observation ne prouve pas.** L'absence de taches après le 06/06 peut venir de la protection phytosanitaire :
une infection prédite sans taches n'est pas une fausse alerte. (La fructification, elle, a été vue faible mais non
nulle : elle teste l'étape de sporulation, pas les contaminations.)
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
* Infections secondaires : première version (voir la section dédiée) ; non validée contre Plasmopy.
* Durée de vie des taches (`sporulation.fenetre_j`, défaut **15 jours**, Orlandini et al. 2008). Constat sur données réelles
  2026 avec une fenêtre illimitée (`None`) : six cycles de mai-juin sporulent tous le 19/08, première nuit favorable,
  3 mois plus tard (artefact). Avec une fenêtre, une tache sans nuit favorable passe en `taches_sans_sporulation`.
* Pas de stade BBCH par client pour l'instant : sans `bbch`, le coefficient vaut 1.
* Pluie horaire de réanalyse : les pointes sont lissées, le seuil de 3 mm/h peut être sous-détecté.
* Paramètres non calés sur des observations : le rétro-test sur les saisons passées reste à faire.
* Récupération météo testée uniquement avec des réponses simulées.
* L'API gratuite d'Open-Meteo est réservée à un usage non commercial.
