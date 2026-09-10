# gn_module_connectors

Module GeoNature d'alimentation de la Synthèse depuis des sources externes.

| Source | État |
|---|---|
| **GBIF** (Global Biodiversity Information Facility) | fonctionnel |
| **VisioNature / Biolovision** | fonctionnel — client Biolovision vendorisé depuis `Client_API_VN` |
| **dbChiro** (dbchiroweb) | fonctionnel — chiroptères, une instance régionale par configuration |

Le module tourne **dans** GeoNature. Il utilise donc `db.session` directement : aucun
identifiant PostgreSQL à distribuer, insertion par lots plutôt qu'une requête HTTP par
observation, et installation à la portée d'un administrateur fonctionnel.

---

## Installation
```bash
source ~/geonature/backend/venv/bin/activate
```
```bash
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
```

⚠️ **Redémarrer le backend juste après.** L'installation enregistre le module en base,
mais les workers gunicorn déjà lancés n'en voient pas le code Python : tant qu'ils ne sont
pas relancés, `/gn_commons/modules` renvoie une erreur 500 et l'interface est inutilisable.

```bash
sudo systemctl restart geonature
```

### Mise à jour du module

⚠️ **Copier les fichiers ne suffit pas.** Si vous mettez le module à jour par `rsync`,
`scp` ou `git pull`, le code Python change mais la métadonnée du paquet installée dans le
venv reste celle de l'installation précédente. Les dépendances déclarées dans
`requirements.in` ne sont donc pas résolues, et la première commande qui les utilise
échoue par un `ModuleNotFoundError` sans rapport apparent avec la mise à jour :

```
File ".../sources/visionature/biolovision/api.py", line 31, in <module>
    from requests_oauthlib import OAuth1
ModuleNotFoundError: No module named 'requests_oauthlib'
```

Rejouer `install-gn-module` après chaque mise à jour, puis redémarrer le service :

```bash
source ~/geonature/backend/venv/bin/activate
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
sudo systemctl restart geonature
```

Pour débloquer une instance sans réinstaller, la dépendance manquante suffit —
c'est un venv, donc pip s'y applique sans contournement :

```bash
source ~/geonature/backend/venv/bin/activate
pip install requests_oauthlib
```

Vérifier :

```bash
geonature connectors statut
```

La commande affiche le nombre d'observations en Synthèse, les sources déclarées, et
surtout **la couverture des correspondances TAXREF ↔ GBIF**. Sans elles, aucune
occurrence n'est importable : autant le savoir avant de lancer un import plutôt qu'après.

### Ce que crée l'installation

| Objet | Détail |
|---|---|
| Module `CONNECTORS` | `gn_commons.t_modules`, avec ses permissions `R` et `C` |
| Cadre d'acquisition | « Import de données externes depuis le GBIF » (UUID fixe, rejouable) |
| Source Synthèse | `GBIF`, avec `url_source = https://www.gbif.org/occurrence` et `entity_source_pk_field = gbifID` |

La source suffit à obtenir, sans une ligne de frontend, le bouton « voir la donnée
source » de la fiche d'observation : l'interface concatène `url_source` et
`entity_source_pk_value`, où le module stocke le `gbifID`.

---

## Conventions des commandes

Les commandes se nomment `<source>-<action>`, la source portant son nom entier :
`gbif-`, `visionature-`, `dbchiro-`. Une seule exception, `statut`, qui ne dépend
d'aucune source.

Les options sont en **français**, avec deux exceptions assumées : `--dry-run` et `--yes`,
que tout utilisateur de ligne de commande reconnaît et que traduire desservirait.

Le comportement par défaut est **asymétrique, et c'est voulu** :

| | par défaut | pour agir |
|---|---|---|
| `*-import` | écrit | `--dry-run` pour simuler |
| `*-purge` | simule | `--yes` pour exécuter |

Un import s'ajoute et se rejoue sans dommage — les identifiants sont déterministes, une
seconde exécution ne produit rien. Une purge détruit. Qu'elle exige un geste explicite
est une protection, pas une incohérence, et `tests/test_commandes.py` le vérifie pour
qu'on ne le « corrige » pas par mégarde.

`tests/test_commandes.py` ancre le reste de ces conventions : chaque drapeau doit figurer
dans une liste explicite, aucun ne peut employer un terme anglais hors des deux
exceptions, les trois purges doivent offrir les mêmes garanties, et toute option doit
correspondre à un paramètre de sa fonction. Cette dernière vérification n'est pas
théorique : renommer un drapeau sans figer son nom Python fait échouer la commande à
l'exécution seulement, jamais à l'import.

### Renommages

Les noms ont changé sans conserver d'alias. Correspondance :

| avant | après |
|---|---|
| `status` | `statut` |
| `gbif-sync-datasets` | `gbif-synchroniser-jeux` |
| `vn-*` | `visionature-*` |
| `vn-territoires`, `dbchiro-zonages` | `visionature-perimetres`, `dbchiro-perimetres` |
| `--batch-size` | `--lot` |
| `--dataset`, `--dataset-key` | `--jeu` |
| `--drop-empty-datasets` | `--supprimer-jdd-vides` |
| `--max-uncertainty` | `--incertitude-max` |
| `--max-results` | `--max-resultats` |
| `--limit` | `--max-jeux` |
| `--gadm-gid`, `--area`, `--territoire` | `--perimetre` |
| `--country` | `--pays` |
| `--license` | `--licence` |
| `--taxo-group` | `--groupe-taxo` |
| `--since` | `--depuis` |
| `--force` | `--forcer` |
| `--download-doi` | `--doi` |
| `--keep-specimens` | `--garder-specimens` |
| `--keep-unknown-uncertainty` | `--garder-incertitude-inconnue` |
| `--skip-gridded` | `--ecarter-jeux-maille` |
| `--taxref-fallback` | `--repli-taxref` |
| `--ignore-exclusions` | `--ignorer-exclusions` |
| `--debug` | `--trace` |
| `--q` | `--nom` |

Trois de ces renommages ne sont pas cosmétiques. `vn-territoires` et `dbchiro-zonages`
faisaient la même chose sous deux noms, en servant une option elle-même nommée
différemment sur chaque source : les trois s'appellent maintenant `perimetre`, et la
commande qui en liste les valeurs porte le même mot. `--dataset-key` et `--dataset`
désignaient le même objet dans deux commandes voisines. Et `--limit` plafonnait des jeux
là où `--max-results` plafonnait des observations, sans que rien ne le laisse deviner.

⚠️ **Les clés de configuration n'ont pas été touchées.** `connectors_config.toml` reste
tel quel — `batch_size`, `gadm_gid`, `taxo_groups` y côtoient `departements` et
`importer_absences`. C'est la même incohérence, sur une autre surface, et la corriger
casserait les configurations en place sans le dire.

---

## Configuration — GBIF

Copier `connectors_config.toml.example` en `connectors_config.toml`, **à côté de
`geonature_config.toml`**. Le fichier est facultatif : sans lui, les valeurs par défaut
s'appliquent. Toute clé inconnue est refusée au démarrage.

Emplacements recherchés, dans l'ordre :

1. le chemin donné par `GEONATURE_CONNECTORS_CONFIG_FILE` ;
2. `<dossier de geonature_config.toml>/connectors_config.toml` ;
3. `<racine du module>/config/conf_gn_module.toml`.

### Les réglages qui comptent

| Clé | Défaut | Pourquoi y regarder |
|---|---|---|
| `gadm_gid` | `""` | Seul filtre géographique fiable. Sur l'Ariège : `gadmGid` → 1 309 180 occurrences, `stateProvince` → 1 215, une bbox → 2 582 085 (elle déborde sur l'Aude et la Catalogne) |
| `occurrence_status` | `PRESENT` | Ne pas toucher sans raison : les absences deviendraient des présences fausses. Un seul jeu ariégeois en compte 116 799 |
| `licenses` | `CC0_1_0`, `CC_BY_4_0` | Le CC BY-NC est viral — une seule occurrence non commerciale rend l'agrégat entier non commercial |
| `exclude_dataset_keys` | `[]` | **Aucune clé ne permet de dédoublonner automatiquement contre votre Synthèse existante.** Cette liste est un travail de curation manuel, irréductible |
| `coordinate_uncertainty_max` | *aucun filtre* | Sur l'Ariège, ≤ 1100 m ne retient que 39,9 % des occurrences, et certains jeux publient **tout** à 5 km : un seuil trop bas n'écarte pas les mauvaises données, il annule l'import |
| `keep_unknown_uncertainty` | `true` | 34,9 % des occurrences ne déclarent aucune incertitude. Les garder revient à accepter une précision inconnue |
| `skip_gridded_datasets` | `true` | Écarte les jeux publiés au centroïde de maille |
| `download_doi` | `""` | Obligation de citation. L'API `search` n'en délivre aucun — ne vaut que pour l'API `download` |
| `batch_size` | `1000` | Les triggers de `synthese` sont `FOR EACH STATEMENT` : leur coût ne s'amortit qu'en lots |

---

## Utilisation — GBIF

### 1. Prévisualiser (facultatif)

```bash
geonature connectors gbif-synchroniser-jeux --dry-run
```

Crée ou met à jour **un JDD GeoNature par jeu de données GBIF**, rattaché au cadre
d'acquisition du module. C'est le découpage fidèle : GBIF ne produit rien, il agrège —
le producteur est l'organisation qui publie chaque jeu. Chaque JDD porte donc son
producteur, sa licence et sa **citation officielle GBIF** en description, ce qui satisfait
l'obligation d'attribution par la métadonnée elle-même.

L'opération est idempotente : l'`unique_dataset_id` est dérivé en `uuid5` du triplet
`(GBIF, datasetKey, licence)`. La licence entre dans la clé à dessein — sur iNaturalist
elle varie observation par observation, et un JDD doit rester homogène.

⚠️ **Cette commande n'est pas un préalable.** `gbif-import` crée le JDD lui-même, au
moment de la première écriture. C'est délibéré : un jeu publié à la maille, écarté pour
sa licence, ou dont toutes les occurrences tombent au filtre de précision ne doit pas
laisser un JDD vide dans le module Métadonnées. Sur un périmètre départemental, 43 des
200 jeux sont taggés « grillés » par GBIF et n'auraient jamais reçu la moindre
observation.

`gbif-synchroniser-jeux` reste utile pour prévisualiser le périmètre, et pour rafraîchir les
métadonnées — titre, citation, DOI — quand un producteur les corrige.

### 2. Importer les occurrences

```bash
geonature connectors gbif-import \
    --jeu <clé> --perimetre FRA.11.1_1 \
    --incertitude-max 1000 --dry-run
```

Options utiles : `--max-resultats` pour plafonner, `--lot`, `--doi`,
`--garder-incertitude-inconnue` / `--ecarter-incertitude-inconnue`.

Le JDD est créé à la volée si des occurrences survivent aux filtres. L'import est
**idempotent** : `unique_id_sinp` est déterministe, et réutilise l'UUID
contenu dans l'`occurrenceID` quand il y en a un. Pour les données republiées par l'INPN,
c'est l'identifiant permanent DEE — les observations importées portent donc leur identité
SINP d'origine. Une seconde exécution n'écrit rien.

Chaque observation conserve dans `additional_data` sa provenance complète :
`dataset_key`, `dataset_name`, `rights_holder`, `license`, `license_url`, `gbif_url`,
et le DOI du téléchargement s'il est configuré.

### 3. Automatiser

```toml
[gbif.schedule]
enabled = true
crontab = "0 3 * * 1"     # lundi 3 h
```

Rien à installer : GeoNature fait déjà tourner un worker Celery avec `--beat`. La tâche
traite les jeux un par un — un échec sur une source n'emporte pas les autres — et pose un
verrou consultatif PostgreSQL, libéré automatiquement si le worker meurt.

**Le connecteur saute les jeux inchangés** depuis son dernier passage, en comparant la
date de modification du jeu côté GBIF à celle des observations déjà en base
(`meta_create_date` / `meta_update_date`, entretenues par un trigger de la Synthèse —
aucune table de suivi n'est nécessaire).

| situation | ordre de grandeur |
|---|---|
| semaine sans republication | **3-5 min** |
| semaine où un gros jeu republie | quelques dizaines de minutes |
| premier import d'un périmètre départemental | plusieurs heures |

### Le seuil de pagination GBIF

Au-delà de l'**offset 10 000**, l'API `occurrence/search` bascule sur un chemin de
pagination profonde. Mesuré sur un jeu réel :

| offset | durée par page de 300 |
|---:|---:|
| 0 · 2 000 · 5 000 · 8 000 | ~0,8 s |
| **10 000** et au-delà | **~36 s** |

Un facteur 45, sur un seuil franc. Sans traitement, un jeu de 90 000 occurrences
demanderait près de trois heures pour ses seules pages profondes.

Le module **découpe donc automatiquement les gros jeux par tranches d'années**. Une
requête de facettes donne la distribution, puis les années consécutives sont regroupées
tant que le cumul reste sous le seuil ; chaque tranche se pagine alors dans la zone
rapide.

```
total : 29 163 — découpage en 4 tranches d'années
  1973,2019     9 690 occ.
  2020,2022     8 878 occ.
  2023,2023     4 750 occ.
  2024,2024     5 845 occ.
```

Si un jeu n'expose aucune année exploitable, le découpage est impossible : le module lit
les 10 000 premières occurrences et **le signale**, plutôt que de subir la pagination
profonde en silence.

Une cadence plus fine ne rapporterait rien : aucun des 60 plus gros jeux d'un périmètre
départemental n'avait été modifié dans les 7 derniers jours, et la dernière modification
de certains remontait à 901 jours.

⚠️ `--forcer` est indispensable après un changement de mapping : GBIF n'a alors rien
modifié, mais les données doivent tout de même être réécrites.

---

## VisioNature

```toml
[visionature]
enabled = true
url = "https://www.faune-ariege.fr"
user_email = "…"
user_password = "…"
client_key = "…"          # fournis par Biolovision, séparément du compte utilisateur
client_secret = "…"
pseudonymisation_secret = "…"   # obligatoire, voir plus bas
```

```bash
geonature connectors visionature-import --dry-run
geonature connectors visionature-import
geonature connectors visionature-import --depuis 2026-01-01   # incrémental
geonature connectors visionature-reanonymiser                # simulation
geonature connectors visionature-reanonymiser --yes
geonature connectors visionature-purge --taxon Reptilia       # simulation
geonature connectors visionature-purge --taxon Reptilia --yes
```

⚠️ **`--depuis` ne remonte pas au-delà de dix semaines.** C'est la fenêtre que l'API
Biolovision couvre en différentiel (`api_diff`). Au-delà, les créations et les
suppressions de l'intervalle seraient perdues sans le moindre message : la commande
refuse plutôt que de produire une base incomplète en silence, et invite à un moissonnage
complet. Le contrôle a lieu avant tout appel réseau, faute de quoi une erreur de
connexion masquerait le vrai problème.

L'incrémental traite les **suppressions avant les modifications** : une observation
supprimée puis recréée sous le même identifiant serait sinon retirée après avoir été
réécrite.

**`observations/diff` ne livre pas les observations**, seulement la liste de ce qui a
changé : `id_sighting`, `id_universal`, `modification_type`. Il sert donc à **répercuter
les suppressions**, et à cela seulement.

⚠️ Les deux voies qui permettraient d'en résoudre les identifiants — `api_get`, une
observation à la fois, et `api_list(id_sightings_list=…)`, cent à la fois comme le fait
`_store_update` de `transfer_vn` — peuvent être **refusées par l'API alors même que
`diff` répond**. Mesuré sur faune-occitanie.org : 403 sur les deux, y compris pour un
groupe dont `search` accepte les requêtes.

Les créations et modifications passent donc par `search` avec **`entry_date`**, qui fait
porter la recherche sur la date de **saisie** et non sur celle de l'observation. C'est
plus juste de toute façon : chercher par date d'observation manquerait les relevés
anciens encodés récemment, qui sont précisément ce qu'un moissonnage antérieur n'a pas pu
voir.

Un relevé peut être listé par le différentiel sans être lisible individuellement — l'API
répond alors 403. Le client vendorisé traitant tout 4xx comme irrécupérable, une seule
observation protégée faisait échouer le moissonnage entier. Ces relevés sont désormais
journalisés sous le motif `inaccessible` dans `vn_rejets.csv` et le traitement continue.
C'est une donnée manquante, pas une panne — mais elle est signalée, car ne pas savoir ce
qu'on n'a pas serait pire que l'erreur.

### Anonymat : le rattrapage a posteriori

Un observateur peut demander l'anonymat après coup, ou le lever. Ce changement porte sur
l'**observateur** et non sur l'observation : il n'entre dans aucune empreinte de contenu,
donc ni le moissonnage incrémental ni le court-circuit sur la date de modification ne le
rattrapent. Les observations déjà en Synthèse resteraient figées sur le consentement en
vigueur au moment de l'import.

`visionature-reanonymiser` réaligne `synthese.observers` sur le référentiel courant, dans les deux
sens. L'appariement se fait sur l'identifiant pseudonymisé conservé dans
`additional_data.observateur` — seule clé disponible, le nom réel n'étant pas stocké pour
les observateurs anonymisés. Les lignes dont l'observateur a disparu du référentiel sont
laissées en l'état et signalées : leur pseudonyme est la seule information dont on
dispose. Seules les lignes dont la valeur change sont réécrites.

À passer périodiquement — le rattrapage n'a pas de déclencheur naturel.

### Accélérer la mise au point : le cache des référentiels

Un moissonnage commence par trois téléchargements — espèces (63 616 sur
Faune-Occitanie), groupes taxonomiques, observateurs (246 699) — soit plusieurs minutes
avant que la première observation ne soit traitée. Pénible quand on règle un import.

```toml
[visionature]
cache_heures = 24        # 0 = désactivé, et c'est le défaut
# cache_dir = "…"        # défaut : ~/.cache/gn_module_connectors
```

```bash
geonature connectors visionature-vider-cache
```

⚠️ **Désactivé par défaut, et à laisser désactivé en production**, pour deux raisons
distinctes :

- le référentiel des observateurs contient des **noms de personnes**. L'activer les écrit
  sur disque — en 0600, mais en clair — alors que tout le dispositif d'anonymisation vise
  précisément à ne pas les conserver. Le module l'avertit explicitement à l'écriture ;
- un référentiel périmé produit des correspondances taxonomiques fausses et des
  consentements obsolètes, **sans que rien ne le signale**.

La clé de cache inclut l'URL de l'instance : passer de Faune-France à Faune-Occitanie ne
sert jamais le référentiel de l'autre. Un fichier illisible, corrompu ou sans horodatage
vaut absence de cache — une optimisation n'a pas le droit de faire échouer ce qu'elle
accélère.

### Le moissonnage complet passe par `search`, borné par territoire

⚠️ **`api_list` sur les observations est déprécié en amont et refusé par l'API.**
`Client_API_VN` le journalise sans ambiguïté : *« Download using list method is
deprecated. Please use search method only »*. Un 403 sur ce point d'entrée est donc
attendu, et ne signale aucun droit manquant.

⚠️ **Deux 403 distincts coexistent sur `search`, et ils ne veulent pas dire la même
chose.** Relevé sur faune-occitanie.org, en sondant les quarante-neuf groupes avec les
mêmes identifiants, le même territoire et la même fenêtre :

| corps de la réponse | groupes concernés | lecture |
|---|---|---|
| `"you are not authorized to access this taxonomic group"` | exactement ceux dont `access_mode` vaut `none` | refus de droit, explicite |
| **vide** | des groupes en `access_mode = full`, y compris minuscules | périmètre de la clé d'API |

Le volume ne l'explique pas : les chiroptères comptent 74 modifications quotidiennes sur
**toute** l'Occitanie, donc une poignée en Ariège, et sont refusés comme les oiseaux. Le
seul groupe servi était les reptiles.

Autrement dit, le périmètre d'export d'une clé Biolovision se décide **par groupe
taxonomique**, indépendamment de l'`access_mode` du portail, et un refus de périmètre ne
se distingue d'un refus de droit que par la présence ou l'absence d'un message. Le
diagnostic les affiche tous deux ; c'est ce qu'il faut porter à l'administrateur de
l'instance pour demander une extension.

Le moissonnage rétrécit malgré tout sa tranche deux fois avant d'abandonner : si un refus
tient au volume, il passera ; sinon on ne divise pas indéfiniment une plage qui ne sera
jamais servie. Le rétrécissement n'est tenté que s'il change effectivement la fenêtre
interrogée — sur une plage plus courte que la tranche, réduire celle-ci rejouerait la
même requête.

⚠️ **Et une recherche sans périmètre territorial est refusée elle aussi.** Mesuré sur
faune-occitanie.org : `POST /observations/search/` sans `territorial_unit_ids` renvoie
403, avec renvoie 200. `transfer_vn` n'en émet d'ailleurs jamais sans périmètre — sa
boucle pose systématiquement `location_choice` et `territorial_unit_ids`. Un balayage de
toute une instance régionale n'est pas une requête que l'API sert.

Ces deux constats ont coûté plusieurs heures parce qu'un 403 ressemble à un droit
manquant. Il n'en était rien : les mêmes identifiants fonctionnent parfaitement dès que
la requête est celle que l'API attend.

```toml
[visionature]
departements = ["09"]        # OBLIGATOIRE en moissonnage complet
date_debut = "2015-01-01"    # vide = tout l'historique
tranche_jours = 15
```

Le moissonnage parcourt la période de la fin vers le début, territoire par territoire.
La tranche est **ajustée au volume rendu** — réduite si elle déborde, élargie si elle est
creuse — pour viser le même ordre de grandeur que `transfer_vn`, qui régule par un PID
autour de 10 000 observations. Une interruption laisse donc un corpus utilisable, les
données récentes étant traitées en premier.

⚠️ **La forme du JSON n'est pas un détail de volumétrie.** `short_version=1` demande la
forme réduite, et sur faune-occitanie.org `observers[]` n'y porte que `@id`, `@uid`,
`altitude`, `comment`, `coord_lat`, `coord_lon`, `count`, `estimation_code`,
`flight_number`, `gps_lat`, `gps_lon`, `hidden`, `id_sighting`, `id_universal`.

Manquent donc `atlas_code`, `details`, `behaviours`, `timing`, `uuid`, `medias`,
`extended_info`, `project_code`, `second_hand` — et `name`, le nom de l'observateur. Soit :
ni statut de reproduction, ni heure, ni identifiant SINP natif, ni mortalité, ni preuve
d'existence, ni jeu de données par code projet, ni observateur nommé. Le module emploie
donc la **forme longue**. `transfer_vn` recommande la courte pour sa volumétrie ; ce
n'est pas notre besoin.

Le `place` de la forme courte est amputé de la même façon : il porte `loc_precision` mais
ni `county` ni `insee`, d'où l'impossibilité d'en déduire le département.

`search` renvoie des **relevés complets** (`date`, `observers`, `place`, `species`),
contrairement au différentiel qui ne livre que des identifiants. C'est ce qui rend le
moissonnage complet praticable là où le différentiel imposerait une requête par
observation.

### Diagnostiquer un 403 sur `observations`

Un 403 de l'API Biolovision ressemble à un défaut de code. Ce n'en est pas
nécessairement un, et l'établir demande d'éliminer les variables une à une. Voici le
tableau d'une investigation menée sur faune-occitanie.org, à conserver pour la prochaine.

**Ce qui a été éliminé, avec la mesure correspondante :**

| variable | vérification | verdict |
|---|---|---|
| code du client | `diff` de `biolovision/api.py` contre l'amont | identique |
| paramètres de `search` | comparés au tag `v2.12.0` de Client_API_VN | identiques |
| versions | `requests` 2.32.5, `requests_oauthlib` 2.0.0 | conformes |
| URL | comparée à la configuration LPO | identique |
| forme du JSON | `short_version` 0 et 1 sondés | 403 des deux côtés |
| périmètre territorial | avec et sans, plusieurs unités | 403 dans tous les cas |
| groupe taxonomique | les 49 sondés | seul un groupe répondait |
| volume | 74 modifications/jour sur toute la région | 403 quand même |
| ancienneté des données | 2019 et 2026 | 403 des deux côtés |

**Ce qui reste, une fois tout cela éliminé :** le périmètre attaché aux identifiants.

Le point de comparaison décisif est un journal `transfer_vn` d'un tiers, sur la **même
instance**, montrant un téléchargement abouti pour le même groupe, le même territoire et
la même période — donc une requête réputée servie. Si la nôtre est identique et refusée,
la différence est dans les identifiants, quoi qu'on en pense par ailleurs.

**Les commandes de diagnostic** existent pour mener cette élimination sans tâtonner :

```bash
geonature connectors visionature-diagnostic --groupe-taxo 6      # points d'entrée, champs reçus
geonature connectors visionature-diagnostic --perimetre 111 --fin 2019-01-20 --jours 10
geonature connectors visionature-volumetrie                     # les 49 groupes d'un coup
geonature connectors visionature-perimetres                    # identifiants à employer
geonature connectors visionature-groupes                        # codes et access_mode
```

⚠️ **401 et 403 ne disent pas la même chose**, et les confondre coûte cher :

- **401** `Can't verify request, missing oauth_consumer_key or oauth_token (3Leg)` : la
  signature OAuth n'est pas vérifiable. `client_key` ou `client_secret` est absent ou
  erroné, et l'API ne reconnaît pas le demandeur. Rien à voir avec les droits.
- **403** : le demandeur est reconnu, mais n'est pas autorisé sur cette ressource. **La
  clé est donc valide** ; c'est son périmètre qui est en cause.

Obtenir un 401 avec un second jeu d'identifiants est ainsi une façon simple de confirmer
que le premier est bien authentifié.

⚠️ Deux formes de 403 coexistent, et seule la première se nomme :

- `"you are not authorized to access this taxonomic group"` et `"you are not allowed to
  access this resource"` : refus explicites, obtenus sur les groupes en `access_mode:
  none` et sur le contrôleur `observers` ;
- **corps vide** : tout le reste. C'est celui qui coûte des heures.

### Le lien « voir la donnée source »

Le bouton de la Synthèse ouvre la fiche de l'observation sur le portail VisioNature
d'origine. Il passe par une **redirection du module** plutôt que d'y pointer directement.

⚠️ GeoNature construit ce lien en insérant systématiquement un séparateur :

```typescript
link.href = url_source + '/' + id_pk_source;   // synthese-list.component.ts:181
```

Une URL de retour en chaîne de requête — celle de Biolovision est
`…/index.php?m_id=54&id=` — devient donc `…&id=/176983543`, que le portail ne sait pas
lire. Aucune valeur d'`url_source` ne peut produire `&id=176983543` à travers ce
constructeur.

Plutôt que de détourner `entity_source_pk_value` pour y loger un fragment d'URL, le
module donne au cœur ce qu'il sait produire — **un chemin terminé par l'identifiant** :

```
url_source              <API_ENDPOINT>/connectors/visionature
entity_source_pk_value  176983543
lien produit            <API_ENDPOINT>/connectors/visionature/176983543
                        → 302 vers …/index.php?m_id=54&id=176983543
```

La colonne garde l'identifiant brut, `entity_source_pk_field` reste exact, et le cœur
n'est pas modifié. La route (`blueprint.voir_dans_visionature`) refuse tout identifiant
qui ne soit pas numérique, plutôt que de concaténer dans une redirection ce qui vient
d'une URL.

### Moissonner un gros historique

Débit mesuré sur Faune-Occitanie : **5 763 observations écrites en 3 minutes**, soit
environ **32 par seconde** — et c'est une borne basse, ces trois minutes incluant le
chargement du référentiel de 63 616 espèces, qui est un coût fixe.

| volume | durée |
|---|---|
| un mois d'un département | 3 min |
| un an d'un département | ~40 min |
| vingt ans d'un département | ~13 h |
| toute une région, quatorze millions | **~5 jours** |

Jusqu'à quelques millions, un moissonnage d'un seul tenant convient : en cas d'erreur de
correspondance, on purge et on recommence. Au-delà, deux limites deviennent bloquantes —
il n'y a **pas de reprise sur incident**, et le relevé brut n'est pas conservé, donc
corriger une correspondance impose de tout retélécharger.

La parade est de **partitionner par département et par année**, chaque partition se
rejouant en quelques heures :

```bash
for dep in 09 11 12 30 31 32 34 46 48 65 66 81 82; do
  for an in $(seq 2005 2026); do
    geonature connectors visionature-import \
      --depuis "${an}-01-01" --fin "$((an+1))-01-01" 2>&1 | tee -a moisson.log
  done
done
```

`departements` se règle dans la configuration ; l'exemple ci-dessus suppose qu'on la
modifie entre deux départements, ou qu'on lance une instance de configuration par
département. Le recouvrement est gratuit : l'`ON CONFLICT` ne réécrit que sur changement
d'empreinte, et une partition rejouée ne coûte que son téléchargement.

⚠️ **Le coût réel n'est pas dans le téléchargement mais dans les zonages.** Chaque
observation engendre environ **9 lignes de `cor_area_synthese`** — quatorze millions
d'observations en produisent donc cent vingt-six millions, avec la maintenance d'index
correspondante. Les 32 observations par seconde mesurées l'ont été sur une Synthèse
quasi vide ; le débit se dégrade à mesure qu'elle se remplit.

### Restreindre le périmètre

Sans filtre, `visionature-import` moissonne **toute l'étendue de l'instance** : treize départements
sur Faune-Occitanie, la France entière sur Faune-France. Deux réglages, complémentaires :

```toml
[visionature]
departements = ["09"]
# filtre_api = { id_territorial_unit = "..." }
```

`departements` est vérifié sur `place.county` de chaque relevé — le code de département
que porte chaque observation, à côté de `insee` et `municipality`. C'est le filtre qui
**garantit** le périmètre. Les relevés écartés sont journalisés sous le motif
`hors_perimetre`, et un lieu dont le département est indéterminable est écarté aussi :
le laisser passer ferait du filtre une passoire silencieuse.

`filtre_api` est transmis tel quel à l'API comme paramètres d'URL. C'est le seul moyen
d'éviter de **télécharger** ce qu'on va jeter. Découvrir les valeurs de l'instance :

```bash
geonature connectors visionature-perimetres
geonature connectors visionature-groupes        # groupes taxonomiques et couverture reproduction
```

Le `short_name` qu'affiche cette commande est le code employé par `Client_API_VN` — sa
configuration le précise : « use the territory short_name, not the territory id ».

⚠️ **Un paramètre inconnu de l'API est ignoré sans erreur** : rien ne distingue un filtre
appliqué d'un filtre inexistant. C'est pourquoi `filtre_api` ne fait jamais foi seul. Si
les rejets `hors_perimetre` dépassent un dixième du volume lu alors qu'un filtre serveur
est configuré, le moissonnage le signale — le filtre a été ignoré et toute l'instance a
été téléchargée avant d'être écartée localement.

### Résolution taxonomique

**L'API Biolovision n'expose aucune correspondance vers TAXREF.** Vérifié :
`/api/species/?id=94` renvoie `{"latin_name": "Anas crecca", …}`, sans `cd_nom`.
L'identifiant d'espèce est purement interne — l'espèce 94 est une Sarcelle d'hiver,
quand le `cd_nom` 94 de TAXREF désigne *Lacerta salamandra*.

Le rapprochement se fait donc sur `latin_name` contre `taxref.lb_nom`, restreint aux
taxons valides (`cd_nom = cd_ref`), et construit **une fois au démarrage**. C'est viable :
sur 300 377 taxons valides, TAXREF compte 299 065 noms distincts, soit 0,4 % d'homonymes.

⚠️ Une homonymie est traitée comme un **échec**, pas comme un choix par défaut : départager
au hasard deux taxons valides produirait une erreur que rien ne signalerait.

### Observateurs : consentement individuel

VisioNature porte un champ `anonymous` sur **chaque observateur**. Le module le respecte
plutôt que d'appliquer un réglage global :

| cas | résultat |
|---|---|
| `anonymous = 0` | nom publié — aucune demande d'anonymat n'a été exprimée |
| `anonymous = 1` | pseudonyme |
| observateur absent du référentiel | pseudonyme — l'ignorance ne vaut pas consentement |

Le pseudonyme est un HMAC-SHA256 stable : les observations d'un même contributeur restent
rapprochables sans qu'il soit identifiable. **La clé est obligatoire et vient de la
configuration** — jamais une valeur par défaut, qui rendrait les pseudonymes recalculables
par un tiers, donc réidentifiables.

Elle est exigée même si aucun observateur ne demande l'anonymat : le module écrit
systématiquement un identifiant pseudonymisé dans `additional_data.observateur`, quel que
soit le sort du nom. Sans elle, `visionature-import` refuse de démarrer.

Le consentement est lu **dans le relevé lui-même** : la forme longue de l'API porte
`anonymous` et `anonymous_in_export` sur chaque observation. C'est la source la plus
sûre — elle vaut au moment de l'observation, et non au moment où l'on consulte un
référentiel.

Le référentiel des observateurs n'est donc plus qu'un repli, pour les réponses qui ne
portent pas ces champs. Il n'est **chargé qu'au premier relevé qui en a besoin**, et le
plus souvent jamais : sur Faune-Occitanie il pèse 246 699 inscrits, soit plusieurs
minutes de téléchargement et autant de noms de personnes en mémoire, qu'il serait absurde
de payer d'avance pour un cas devenu rare.

⚠️ `visionature-reanonymiser`, lui, le charge toujours : c'est sa raison d'être, puisqu'il sert
précisément à rattraper les changements d'avis exprimés après l'import.

#### Générer la clé de pseudonymisation

Sur la machine qui héberge GeoNature :

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

48 octets d'aléa cryptographique, soit environ 64 caractères — largement suffisant pour
une clé HMAC-SHA256. `openssl rand -base64 48` fait aussi bien.

Reporter la valeur dans `connectors_config.toml`, puis redémarrer le service :

```toml
[visionature]
pseudonymisation_secret = "la-chaîne-obtenue"
```

⚠️ **Cette clé ne doit jamais changer, et doit être sauvegardée hors de la machine.**
Les pseudonymes n'en dérivent que d'elle et de l'identifiant d'observateur ; le nom réel
n'est stocké nulle part pour les observateurs anonymisés. La perdre ou la remplacer après
un import a deux conséquences irréversibles :

- `visionature-reanonymiser` ne retrouve plus aucune ligne — il apparie sur le pseudonyme conservé
  dans `additional_data.observateur`, et rien d'autre ;
- le moissonnage suivant produit des pseudonymes différents pour les mêmes observateurs,
  qui cessent donc d'être rapprochables entre eux.

La consigner dans un gestionnaire de mots de passe **au moment où on la génère**, pas
après. Et restreindre le fichier : `chmod 600 connectors_config.toml`, qui porte aussi le
mot de passe Biolovision et le `client_secret`. Il est dans le `.gitignore` du dépôt —
seul `connectors_config.toml.example`, aux valeurs vides, est suivi.

#### Observations masquées : importées, pas écartées

Dans VisioNature, on masque une observation (`hidden`) pour protéger **l'espèce ou le
site** — nid de rapace, station d'orchidée, gîte à chiroptères. Ce n'est pas une donnée
personnelle, et ce n'est pas une mise au rebut : c'est précisément la donnée à enjeu,
celle que l'accès à l'API est censé apporter. L'écarter reviendrait à ne moissonner que
le banal.

Ces observations sont donc importées, avec `id_nomenclature_diffusion_level` positionné à
`NIV_PRECIS 4` — « Aucune ». Le référentiel complet :

| cd | libellé | |
|----|---------|---|
| 0 | Standard | |
| 1 | Commune | |
| 2 | Maille | choix de `gn_vn2synthese` |
| 3 | Département | |
| 4 | Aucune | **défaut de ce module** |
| 5 | Précise | |

« Aucune » est retenu parce que c'est le code que GeoNature emploie pour traduire
`diffusable = false`, et parce qu'il correspond au comportement de VisioNature, où une
observation masquée n'apparaît pas publiquement — même dégradée. `gn_vn2synthese` préfère
« Maille », qui laisse l'observation alimenter les cartes de répartition sans livrer la
localisation précise : c'est défendable, et le réglage `niveau_diffusion_masquees` y donne
accès. Le bon choix dépend de la convention passée avec le producteur. Le fait qu'une observation était masquée
à la source est en outre conservé dans `additional_data.masquee_source`, pour que
l'information survive à une modification manuelle du niveau de diffusion.

Les observations **non** masquées gardent un niveau de diffusion NULL. Depuis la migration
« Do not auto-compute diffusion_level », GeoNature a retiré le DEFAULT de cette colonne et
ne la calcule plus : NULL y signifie « le producteur ne se prononce pas », ce qui est
exact. Y inscrire une valeur serait une affirmation que la source ne fait pas.

Un seul motif écarte réellement une observation : `admin_hidden_type = refused`, le rejet
explicite d'un modérateur. Les motifs `incomplete` et `question` signalent une vérification
en cours, pas un refus — la donnée est importée. Les commentaires réservés aux modérateurs
(`hidden_comment`) ne sortent jamais de l'outil.

Une observation portant `second_hand` — saisie rapportant l'observation d'un tiers — est
importée sans observateur : le nom enregistré est celui du saisisseur, et le porter dans
`observers` attribuerait l'observation à quelqu'un qui ne l'a pas faite.

### Les observateurs ne sont pas créés dans `utilisateurs.t_roles`

**Choix explicite.** Le module renseigne `synthese.observers` en texte libre et ne crée
ni rôle, ni lien `cor_observer_synthese`.

Trois raisons. D'abord la cohérence : sur une instance de référence, 536 948 observations
renseignent `observers` en texte et `cor_observer_synthese` est vide — c'est déjà le
fonctionnement de l'import SINP et d'Occtax. Ensuite le volume : une instance VisioNature
régionale compte des milliers de contributeurs, quand l'annuaire GeoNature en compte
quelques dizaines ; `t_roles` est un référentiel de **comptes**, pas de personnes citées.
Enfin la cohérence avec l'anonymisation : pseudonymiser dans la Synthèse tout en créant
une fiche nominative dans l'annuaire n'aurait pas de sens.

Conséquence assumée : le filtre CRUVED « mes observations » ne fonctionne pas sur ces
données — ce qui est sans objet pour des contributeurs qui n'ont pas de compte GeoNature.

Si le besoin se présentait, la bonne approche serait de ne rapprocher que les observateurs
**disposant déjà d'un compte**, par courriel — ciblé plutôt que massif.

### Jeux de données par code projet

VisioNature rattache les observations à des **codes projet**, qui correspondent à des
programmes réels : atlas, suivis, plans d'action. Le module en fait un JDD chacun, comme
`gn_vn2synthese`. Les observations sans code projet vont dans un JDD général par instance.

Les JDD restent créés à la première écriture : un projet dont toutes les observations
sont rejetées ne laisse pas de jeu vide.

### Codes atlas de nidification

Les codes EOAC alimentent `STATUT_BIO` au-delà du seuil configuré — par défaut 2, car le
code 1 (« vu en période de nidification dans un milieu favorable ») n'est pas un indice de
reproduction. Certains codes alimentent en plus `OCC_COMPORTEMENT` : chant, accouplement,
territorial, nourrissage.

⚠️ **Le code 99 signale une absence**, pas une reproduction certaine — espèce recherchée,
non trouvée. Il est versé en `STATUT_OBS = No` et exclu de la comparaison au seuil. Un
effectif nul déclaré `EXACT_VALUE` est traité de même ; un zéro sans cette mention est une
donnée incomplète, pas une absence.

Le code atlas brut est conservé dans `additional_data` : le SINP ignore la gradation
possible / probable / certaine, qui est pourtant le cœur de la donnée pour un atlas.

Le seuil, le code d'absence et la table des comportements sont surchargeables :

```toml
[visionature.atlas]
reproduction_min = 2
absence = 99
```

⚠️ **Le code atlas arrive sous trois formes**, et l'une d'elles était mal lue. Les exports
réels renvoient `{"@id": "3_13", "#text": "12"}` : l'`@id` est la clé d'énumération du
champ, le `#text` le code EOAC. Or `int("3_13")` vaut **313** en Python — l'underscore y
est un séparateur de chiffres. Le module lisait donc 313 pour 12, 32 pour 1, et 399 pour
99. Sur les 165 observations à code atlas du corpus d'exemple, les 38 codes 1 passaient en
« Reproduction » alors qu'ils en sont explicitement exclus, et les 4 absences déclarées
entraient en présence. Le `#text` fait désormais foi dès que l'`@id` porte un underscore.

### Statut de reproduction des autres groupes

Les codes atlas ne concernent que les oiseaux. Pour les amphibiens, les reptiles, les
mammifères, les chiroptères, les odonates, les orthoptères et les papillons de jour, la
reproduction se déduit de la **classe d'âge** (`details[].age`), du **sexe**
(`details[].sex`) et du **comportement** (`behaviours[].@id`), dont le sens dépend du
groupe : une exuvie prouve la reproduction chez un odonate, « imago » ne prouve rien chez
un papillon.

La table est transposée de `gn_vn2synthese` v1.6.0 (`t_c_vn_repro_matching_values`,
107 lignes) et vit dans `sources/visionature/reproduction.py`. Quatre degrés — certain,
probable, possible, inconnu — dont les trois premiers sont versés en `STATUT_BIO = 3`, le
SINP ne graduant pas la reproduction. Le degré et l'indice qui l'a emporté sont conservés
dans `additional_data` (`repro_degre`, `repro_indice`), pour la même raison que le code
atlas brut.

Le groupe est désigné par son **code** (`TAXO_GROUP_BAT`), résolu depuis le contrôleur
`taxo_groups` de l'instance, et non par son identifiant numérique comme le fait la LPO :
rien ne garantit qu'une instance numérote ses groupes comme Faune-France.

`details[]` ventile un relevé en classes d'âge et de sexe. Elles sont **agrégées** en une
ligne de Synthèse, pas dépliées en plusieurs : les entrées de `details[]` n'ont aucun
identifiant, donc aucune clé stable pour `unique_id_sinp` ; `observers[].count` est le seul
effectif qui fait foi ; et surtout la reproduction est une propriété de l'ensemble —
« 1 adulte + 2 juvéniles » la prouve, alors qu'éclaté en deux lignes le juvénile la
porterait et l'adulte passerait pour une donnée sans indice.

Un code d'âge, de sexe ou de comportement qu'aucune règle ne couvre est **compté et
signalé** en fin de moissonnage. Ce n'est pas une erreur — l'énumération VisioNature est
localement extensible — mais c'est le seul signal qu'une règle manque. Mesuré sur
56 relevés de reptiles réels : `IMM` (immature) y apparaît 9 fois et la table de la LPO ne
lui donne aucune règle chez les reptiles, alors qu'elle en donne une chez les mammifères
et les odonates.

```toml
[visionature.reproduction]
active = true

# Complète la table livrée, groupe par groupe, sans effacer le reste.
[visionature.reproduction.regles.TAXO_GROUP_BAT.age]
YOUNGNAKED = "certain"
```

### Heure d'observation

`gn_synthese.synthese.date_min` et `date_max` sont des `timestamp`. Le module y écrivait
un `date`, donc **minuit pour tout le monde**. L'heure vient de
`observers[].timing.@timestamp`, avec `@offset` pour l'heure murale locale.

Elle n'est écrite **que si elle a un sens** : chaque bloc de date Biolovision porte un
indicateur `@notime`, que `gn_vn2synthese` ignore — il écrit donc « 00:00:00 » sans
distinguer une observation réellement faite à minuit d'une heure inconnue. Mesuré sur 338
observations réelles : `timing.@notime = 0` dans 95,6 % des cas. `additional_data.heure_connue`
(`oui` / `non`) tranche l'ambiguïté sur les 4,4 % restants.

L'heure est reportée sur le **jour du relevé** : la date d'observation déclarée fait foi,
un `timing` décalé ne doit pas faire glisser `date_min` d'un jour.

### Identifiant SINP : l'UUID du producteur d'abord

`observers[].uuid` existe et est renseigné sur la totalité des observations examinées.
C'est l'identifiant sous lequel le producteur publie sa donnée. Le module recalculait
systématiquement un uuid5 : l'identifiant DEE divergeait donc de celui du producteur, et
si la même donnée arrivait aussi par un dépôt SINP, le doublon était **invisible** — deux
UUID différents, deux sources différentes, rien pour les rapprocher.

L'UUID natif prime désormais ; l'uuid5 reste le repli quand la source n'en fournit pas.

⚠️ **Migration.** Les lignes déjà importées portent l'uuid5. Elles sont **renommées** au
moissonnage suivant (`core.synthese.realigner_uuid`) plutôt que réinsérées à côté : c'est
la seule opération qui préserve ce que la Synthèse a accroché à `id_synthese`
(validations, rattachements aux zonages, signalements). Le renommage est borné à
`id_source`, n'écrase jamais une ligne portant déjà l'UUID cible, est idempotent, et le
bilan d'import annonce le nombre de lignes réalignées. L'ancien identifiant reste
consultable dans `additional_data.vn_uuid_calcule`.

⚠️ **Premier moissonnage après cette version.** Les champs nouvellement exploités entrent
dans l'empreinte de contenu : **tout le corpus VisioNature est réécrit une fois**, ce qui
est le seul moyen que les lignes existantes reçoivent l'heure, l'altitude et le reste.
Cette réécriture déclenche par ligne les triggers `tri_update_cor_area_synthese` et
`tri_update_calculate_sensitivity` — prévoir le temps de traitement. Les lignes GBIF ne
sont pas concernées : leur empreinte est inchangée.

### Mortalité

`observers[].extended_info.mortality` porte `death_cause2` (ROAD_VEHICLE, ELECTRIC,
EOLIEN, POISONING, HUNTING, PREDATION…), `wounded`, et selon la cause `road_type2` ou
`predation2`. `ETA_BIO` valait `None` en dur : toute la mortalité routière arrivait
indiscernable d'une observation ordinaire.

| cas | `ETA_BIO` |
| --- | --- |
| bloc `mortality` présent | `3` — Trouvé mort |
| bloc `mortality` avec `wounded = 1` | `2` — Observé vivant |
| `details[].condition` ∈ {PEL, MUMMIE, BONESREMAINS, REMAINS} | `3` |
| absence constatée | `1` — Non observé |
| sinon | `2` — Observé vivant |

Deux écarts avec `gn_vn2synthese` :

- ils écrivent « Trouvé mort » même quand `wounded = 1`, ce qui décrit pourtant un animal
  blessé donc vivant ;
- leur table de synonymes `ETA_BIO` mappe bien PEL, MUMMIE et BONESREMAINS vers « Trouvé
  mort », mais leur script d'upsert **ne l'interroge jamais** : un reste osseux de
  chiroptère y ressort « Observé vivant ». On lit la table qu'ils ont écrite.

La cause part dans `additional_data` (`mortalite_cause`, `mortalite_detail`,
`mortalite_blesse`) : aucune nomenclature SINP ne sait dire « collision routière », et la
perdre reviendrait à ne plus pouvoir isoler les données que les gestionnaires
d'infrastructures viennent chercher.

### Altitude, médias, complétude

| champ VisioNature | colonne Synthèse |
| --- | --- |
| `observers[].altitude` | `altitude_min` = `altitude_max` |
| `observers[].medias` | `digital_proof` (`path` + `/` + `filename`, séparés par `, `) |
| `observers[].medias` présents | `id_nomenclature_exist_proof` = `1`, sinon `2` |
| `observers[].precision` | `id_nomenclature_geo_object_nature` (table de onze synonymes : `precise` → `St`, les autres → `In`) |
| `observers[].id_form_universal` | `unique_id_sinp_grp` (uuid5 dérivé) |
| paramètre `taxref_version` | `meta_v_taxref` |

⚠️ Les médias marqués `media_is_hidden = 1` sont **écartés de `digital_proof`** : un média
masqué à la source l'est pour protéger un nid, un gîte ou une station. La preuve reste
déclarée existante — c'est sa diffusion qui est interdite, pas son existence.
`gn_vn2synthese` concatène sans regarder ce champ.

⚠️ `unique_id_sinp_grp` : `gn_vn2synthese` lit `forms_json.uuid`, qui n'est **pas** un
champ de l'API mais une colonne qu'ils ajoutent avec `DEFAULT uuid_generate_v4()` — un
UUID aléatoire, stable seulement grâce à leur table de transit. N'ayant pas de table de
transit, on dérive un uuid5 de `id_form_universal` : reproductible sans rien stocker.

`reference_biblio` n'est **pas** renseigné, contrairement à eux qui y écrivent
`t_sources.url_source || entity_source_pk_value`. C'est exactement ce que GeoNature
reconstruit déjà depuis `t_sources.url_source`, que `visionature-import` renseigne : dupliquer le
lien sur chaque ligne d'un corpus de plusieurs centaines de milliers d'observations
n'apporterait rien.

### Limites connues

La table de reproduction ne couvre que sept groupes taxonomiques : ceux que couvre le
témoin. Les papillons de nuit, les hyménoptères, les araignées, les poissons et les
mollusques n'en ont aucune règle et restent au défaut. Rien n'est déduit non plus de
`details[].condition`, dont l'énumération (VIEW, FLY, LAID, HAND, AUDIO…) alimenterait
plutôt `METH_OBS`.

`OCC_COMPORTEMENT` n'est déduit de `behaviours[]` que pour « Accouplement » et
« Territorial » : ce sont les seuls dont le `cd_nomenclature` SINP soit déjà vérifié
ailleurs dans le module. « Pond », « Tandem » ou « Émergence » n'ont pas d'équivalent
certain, et un code inventé produirait une valeur fausse mais silencieuse.

`STADE_VIE` et `SEXE` restent au défaut. L'information existe (`details[].age`,
`details[].sex`) et la décision d'agrégation est prise, mais la règle du cas ambigu reste
à écrire : « 1 mâle et 2 femelles » n'a pas de sexe unique et doit rester au défaut.
`gn_vn2synthese` ne les alimente pas davantage — mesuré sur un export de leur production,
« Inconnu » sur 95 982 lignes sur 95 982.

Le périmètre se restreint par **code de département**, pas par géométrie. Pour un
territoire qui ne suit pas les limites administratives — un bassin versant, un parc —
le zonage `VN_COVER` de la LPO reste la bonne réponse, et n'est pas implémenté ici.

---

## dbChiro

```toml
[dbchiro]
enabled = true
url = "https://dbchiroc.org"
username = "…"          # compte de service, voir plus bas
password = "…"
area = "109"            # zonage Ariège de l'instance
departements = ["09"]
```

```bash
geonature connectors dbchiro-perimetres --nom ariege   # trouver l'identifiant de zonage
geonature connectors dbchiro-import --dry-run
geonature connectors dbchiro-import --max-resultats 25   # premier essai d'écriture
geonature connectors dbchiro-import
```

⚠ `--max-resultats` ramène les observations **les plus récemment modifiées**, l'API triant
sur `-timestamp_update`. C'est fait pour éprouver une écriture sur une instance de
travail, pas pour prélever un échantillon représentatif.

### Le compte de service décide du périmètre

dbChiro n'expose aucun jeton d'API : les vues sont protégées par le `LoginRequiredMixin`
de Django et le connecteur se connecte par le formulaire, en conservant le cookie de
session. Conséquence directe : **le périmètre moissonné est celui que voit le compte
employé**, `SightingListPermissionsMixin` filtrant le queryset selon ses droits.

| compte | ce qu'il ramène |
|---|---|
| `access_all_data` | tout, y compris sessions confidentielles, gîtes masqués, études fermées |
| ordinaire, sur une instance `SEE_ALL_NON_SENSITIVE_DATA = true` | toute la donnée non sensible, gîtes masqués exclus |

**Le second profil est le bon.** Le tri de sensibilité est alors fait par le serveur, qui
en est le seul juge légitime, plutôt que par nous après coup.

### Pas d'incrémental, et ce n'est pas grave

Le queryset est trié par `-timestamp_update`, mais **le serializer ne l'expose pas** :
impossible de savoir où arrêter la pagination. Mesuré sur 8 039 observations, la
couverture du champ est nulle.

La question est sans objet à cette échelle : 8 039 observations tiennent en deux pages de
5 000 et se relisent en quelques secondes. L'empreinte de contenu fait le reste — seules
les lignes réellement modifiées sont réécrites. Si une instance venait à grossir d'un
ordre de grandeur, exposer `timestamp_update` en amont deviendrait la priorité.

### Trois champs manquent à l'appel

Tous existent en base dbChiro, aucun ne remonte dans `/api/v1/search` :

| champ | ce qu'il débloquerait |
|---|---|
| `uuid` (sur `Sighting`) | `unique_id_sinp` natif au lieu d'un uuid5 dérivé |
| `study` (sur `Session`) | un JDD par étude, comme les codes projet de VisioNature |
| `timestamp_update` | le moissonnage incrémental |

En attendant, l'identifiant SINP est un uuid5 de `(instance, id_sighting)` — l'URL entre
dans la clé, `id_sighting` étant un entier propre à chaque base. Le jour où `uuid` sera
publié, il devra primer, et les lignes déjà importées seront à réaligner via
`core.synthese.realigner_uuid`, exactement comme pour VisioNature.

L'API ne publie pas non plus `time_start` ni `date_end` : **les dates sont sans heure**.
Une nuit d'enregistrement acoustique à cheval sur minuit est ramenée à son seul jour de
début, et `additional_data.heure_connue` vaut « non » pour que l'ambiguïté soit lisible.

### 14 % des observations ne sont pas des espèces

C'est la particularité de la donnée chiroptérologique, et le cœur du travail de mapping.
Sur 8 039 observations et 60 codes espèce :

| catégorie | obs | `cd_nom` retenu |
|---|---:|---|
| espèces déterminées (29 codes) | 6 897 | l'espèce |
| « sp. » explicites | 433 | le genre |
| couples intra-genre (`Myotis myotis / blythii` : 333) | 409 | le genre |
| couples inter-genres, même famille | 47 | Vespertilionidae |
| couples à cheval sur deux familles | 14 | Chiroptera |
| `Chiroptera sp.` | 64 | Chiroptera |
| **absences** (`0obs`, `0du`) | **175** | **écartées** |

⚠ **TAXREF ne propose aucun agrégat pour les chiroptères.** Vérifié sur la v16 : ni rang
`AGES`, ni entrée à barre oblique, ni hybride — `Myotis myotis/blythii` n'existe pas. Le
repli au rang supérieur commun est donc la seule voie, et chaque cas est un arbitrage
explicite dans `sources/dbchiro/taxonomy.py` plutôt qu'une règle déduite du libellé.

Le piège à connaître : `Nyctalus / Tadarida` ressemble à un couple de Vespertilionidae,
mais Tadarida est un Molossidae — seul l'ordre les contient tous deux. Idem pour
`Pipistrellus / Miniopterus`, Miniopterus ayant quitté les Vespertilionidae.

La détermination d'origine est toujours conservée : dans `nom_cite`, et dans
`additional_data.determination`. `cd_nom` dit « Myotis », seul ce champ dit « Myotis
myotis / M. blythii ».

**Un `codesp` inconnu est rejeté**, jamais versé par défaut dans l'ordre : une espèce
nouvellement ajoutée au référentiel dbChiro doit se voir dans le journal des rejets, pas
se dissoudre en « Chiroptera sp. ».

La table est vérifiée contre TAXREF **avant** toute écriture : un `cd_nom` déprécié par
une montée de version satisfait la clé étrangère sans qu'aucun contrôle ne le signale.

### Les absences

`0obs` (« Aucune chauve-souris ou trace », 171 obs) et `0du` (« Aucun contact
acoustique », 4 obs) ne désignent aucun taxon. C'est le même piège que
`occurrenceStatus = ABSENT` du GBIF. Écartées par défaut ; `importer_absences = true` les
verse en `STATUT_OBS = « Non observé »` sur le `cd_nom` de l'ordre, avec un effectif de
**zéro** et non NULL — l'ambiguïté entre « aucun individu » et « effectif non renseigné »
fausserait toute analyse quantitative.

### Les nomenclatures se transposent presque telles quelles

dbChiro s'appuie sur `dj-sinp-nomenclatures` : son vocabulaire est déjà aligné sur le
standard, ce qui change la nature du travail par rapport à VisioNature. Les 9 méthodes de
contact couvrent 100 % du corpus :

| dbChiro | METH_OBS | remarque |
|---|---|---|
| `du` contact acoustique (3 198) | **Ultrasons** | et non « Entendu » : c'est un détecteur |
| `cr` cri audible (11) | Entendu | certaines espèces émettent des cris sociaux perceptibles |
| `vv` vu (4 271), `vm` en main (360) | Vu | |
| `gu` guano (62) | **Fèces/Guano/Epreintes** | correspondance exacte |
| `ca` cadavre (42), `ro` restes osseux (11) | Vu / Autre | + `ETA_BIO = Trouvé mort` |

Sur un guano, `ETA_BIO` reste **vide** : l'animal n'a été observé ni vivant ni mort, et
la date est celle de l'indice, pas celle de l'animal.

⚠ **Le champ `period` n'alimente jamais `STATUT_BIO`.** Deux raisons, la seconde étant un
contresens franc : il est *calculé* par dbChiro depuis la date — en faire un statut
biologique déduirait d'un calendrier ce que seul un observateur constate —, et surtout
« Estivage » n'est **pas** l'estivation du SINP, qui désigne une dormance estivale. Chez
les chiroptères d'Europe, l'été est la saison d'activité et de mise bas. La reproduction
vient donc de `breed_colo`, qui est une observation de terrain, pas un calcul.

Une détermination `is_doubtful` passe en `STATUT_VALID = Douteux`, et **prime sur la
pré-validation globale** : annoncer « Probable » sur une donnée que la source dit
incertaine la surclasserait.

### Observateurs et gîtes : deux points de convention

**Aucun marqueur de consentement individuel n'existe côté dbChiro**, contrairement au
champ `anonymous` de VisioNature. Les 8 039 observations publient un nom complet en clair.
Les diffuser suppose donc un accord de l'exploitant portant sur *l'ensemble* des
contributeurs, et non le consentement de chacun. Le connecteur suit ce choix par défaut ;
`pseudonymiser_observateurs = true` bascule sur le HMAC sans changer une ligne de code.
Comme pour VisioNature, aucun rôle n'est créé dans `utilisateurs.t_roles`.

**L'API livre les coordonnées exactes de cavités nommées** — « Trou souffleur - trois
frères ». La géométrie est conservée telle quelle en base : la flouter serait irréversible
et ruinerait tout suivi de gîte. La restriction se règle par `niveau_diffusion`
(`NIV_PRECIS`), qui n'engage que la diffusion. Le référentiel de sensibilité de GeoNature
s'applique de surcroît au déclenchement du trigger d'insertion.

### Le filtre de périmètre est doublé

`area` est appliqué côté serveur, mais **un paramètre inconnu de l'API DRF est ignoré
sans erreur** : rien ne distinguerait un filtre appliqué d'un filtre inexistant. Le code
de département est donc revérifié sur les zonages de chaque observation reçue — dbChiro
les publie dans la réponse : département, commune INSEE, maille 10 km, ZNIEFF, parc. Un
écart massif est signalé en fin de moissonnage.

### Limites connues

Le bouton « voir la donnée source » passe par la redirection
`/connectors/dbchiro/<id>` du module : le permalien dbChiro portant l'identifiant au
milieu du chemin (`/sighting/<id>/detail`), la concaténation du cœur ne pouvait rien
produire de valide.

La suppression n'est pas gérée, comme pour GBIF. Les `countdetails` (sexe, âge, état
sexuel) ne sont pas exposés par `/api/v1/search` : seul `total_count` remonte.

Enfin, une instance protégée par un filtre anti-robot bloquera le connecteur —
`demo.dbchiro.org` l'est. Le cas est détecté et signalé explicitement plutôt que de
finir en erreur de décodage JSON.

---

## Purger

Les trois sources ont la même commande, avec les mêmes garanties :

```bash
geonature connectors gbif-purge --taxon Chiroptera
geonature connectors visionature-purge --projet ATLAS --yes
geonature connectors dbchiro-purge --tout --yes
```

| | |
|---|---|
| `--taxon` | nom TAXREF : règne, phylum, classe, ordre, famille, ou début de nom scientifique |
| `--tout` | vider toute la source, sans autre critère |
| `--supprimer-jdd-vides` | supprimer ensuite les JDD du cadre devenus vides |
| `--yes` | exécuter. Sans lui, la commande simule et n'écrit rien |

Trois propriétés valent d'être connues, parce qu'elles ont manqué à l'une ou l'autre des
commandes avant leur mutualisation :

**Une purge sans critère est refusée.** Il faut `--tout` pour vider une source, et le
dire explicitement. Une suppression totale ne doit pas pouvoir arriver par omission d'un
filtre — `visionature-purge --yes` l'a permis un temps.

**Un `--taxon` sans correspondance affiche les rangs réellement présents.** « 0
observation concernée » alors que l'interface en montre des milliers laisse croire à une
panne, quand c'est le nom de rang qui n'existe pas sous cette forme dans TAXREF :

```
0 observation(s) concernée(s) — taxon « Chauvesouris »
  ⚠ aucun taxon ne correspond, alors que la source porte 25 observation(s). Rangs présents :
    classe      ordre       famille              n
    Mammalia    Chiroptera  Vespertilionidae    19
    Mammalia    Chiroptera  Rhinolophidae        5
```

**Les JDD vides sont listés avant d'être supprimés**, jamais retirés en silence.

Toute opération est bornée à une seule `id_source` : les données saisies localement,
celles d'Occtax et celles des autres connecteurs ne peuvent pas être touchées, même sur
une erreur de critère. Le trigger `tri_log_delete_synthese` consigne les suppressions
dans `gn_synthese.t_log_synthese`, elles restent donc traçables.

`dbchiro-purge` n'a ni option de jeu de données — dbChiro n'en produit qu'un par
instance, faute d'exposer `study` — ni `--incertitude-max`, la colonne `precision`
restant NULL puisque l'API ne publie aucune incertitude. L'offrir laisserait croire à un
filtre qui ne retiendrait jamais rien.

---

## Tests

```bash
python3 -m pytest tests/ -q
```

360 tests, sans dépendance à GeoNature ni à la base. Ils couvrent les cas qui ont
réellement mordu pendant le développement : le faux-ami `Nymph` / « Nymphe », les dates
en intervalle ISO, l'asymétrie énumération/URL des licences, la distinction entre origine
du taxon et état de l'individu, et le déterminisme de l'identifiant unique.

Côté dbChiro, les 52 cas portent sur ce qu'un contrôle de base ne verrait pas : les codes
d'absence qui deviendraient des présences, le couple `Nyctalus / Tadarida` qui franchit
une frontière de famille, le contresens « Estivage » → estivation, et la primauté d'une
détermination douteuse sur la pré-validation globale. Les données de référence viennent
d'un sondage réel de l'instance, pas d'exemples inventés.

`tests/test_noms_definis.py` passe le module à l'analyse statique. Les imports sont
locaux aux commandes — pour ne pas charger l'API Biolovision quand on lance une commande
GBIF —, si bien qu'un import oublié ne se voit ni à l'import du module ni à la
compilation : il attend l'exécution, après le chargement des référentiels d'espèces et
d'observateurs, soit plusieurs minutes avant le `NameError`. C'est arrivé deux fois.

`tests/test_insert_alignement.py` mérite une mention à part : il confronte les `to_row`
des trois sources au texte de `INSERT_SQL`, dans les deux sens. Un paramètre lié manquant
fait échouer l'insertion d'un lot entier ; une clé produite en trop est un calcul jeté en
silence. C'est ce contrôle qui manquait quand le connecteur VisioNature a été écrit avec
huit colonnes de nomenclature là où l'INSERT en portait quatorze.

⚠️ Un test écrit à partir du code plutôt que de la donnée ne prouve rien. Trois défauts
de ce module ont vécu sous un test vert qui vérifiait l'hypothèse fausse du code qu'il
couvrait : les champs `is_hidden` / `export_excluded` qui n'existent pas, le code atlas
lu dans `@id`, et les paramètres manquants de l'INSERT. Écrire les cas à partir d'un
export réel, pas de la fonction testée.

---

## Points de vigilance

**Ne pas reverser ces données au SINP.** GBIF exclut explicitement la republication de
données qui en sont extraites, et l'essentiel du corpus français en provient déjà via
l'INPN. Marquer les JDD en conséquence dans le module Métadonnées.

**Les occurrences dont le `cd_nom` n'est pas résolu sont rejetées**, jamais insérées avec
`cd_nom` NULL : une telle ligne fait planter l'évaluation des permissions
(`AttributeError` sur `taxref_tree`) dès qu'une permission avec filtre taxonomique existe.
Défaut différé, sans lien apparent avec sa cause.

**`rights_holder` contient souvent un nom de personne** (sur iNaturalist, le pseudo ou le
nom de l'observateur). Sa conservation est imposée par la licence, mais c'est une donnée
personnelle : à porter au registre de traitement.

**Penser à filtrer `gn_profiles.v_synthese_for_profiles`** sur `id_source`, sinon les
profils de taxons sont alimentés par de la donnée externe.

**La suppression n'est pas gérée côté GBIF** — elle l'est côté VisioNature, via
`api_diff`. Une occurrence retirée de GBIF reste en base : la
détecter supposerait de comparer l'ensemble des identifiants du périmètre à chaque
passage, ce qui annulerait le bénéfice du court-circuit.

**Le court-circuit repose sur `dataset.modified`.** Si un producteur pousse des données
sans mettre cette date à jour, le jeu sera sauté à tort. Une exécution `--forcer`
trimestrielle est une précaution raisonnable.

---

## Documentation

| Fichier | Contenu |
|---|---|
| `docs/gbif-ariege.md` | L'analyse complète : volumes, licences, contraintes GeoNature, arbitrages d'architecture, chiffres mesurés |
| *(archive)* | Les connecteurs autonomes d'origine ont été déplacés dans le dépôt `geonature-connecteurs-autonomes` |
| `docs/data/` | Listes de référence : jeux PatriNat de l'Ariège, jeux CC BY-NC |

## Développement

`sync-to-docker.sh` déploie le module vers une instance GeoNature docker de
développement, le dépôt vivant hors du volume monté.

`sources/visionature/biolovision/` est une **copie** du client de
[Client_API_VN](https://github.com/dthonon/Client_API_VN) (Daniel Thonon, GPL-3.0),
révision `376c2e1b`. Copié plutôt que dépendu : le paquet complet déclare vingt
dépendances dont aucune n'est utilisée par la couche API, qui ne demande que `requests`
et `requests_oauthlib`. Ne pas éditer ces fichiers — toute adaptation va dans
`sources/visionature/api.py`.

⚠️ **Une seule divergence avec l'amont**, à réappliquer en cas de mise à jour du client.
`BiolovisionAPI` accepte un `timeout`, mais **aucune de ses onze sous-classes ne le
relayait** : il restait donc à `None` quel que soit le contrôleur employé, et `requests`
attendait indéfiniment. Un moissonnage pouvait se figer sans fin ni message, le client
journalisant dans un logger que la CLI n'affiche pas. Les onze constructeurs acceptent et
transmettent désormais le paramètre. `tests/test_visionature.py` le vérifie sur chaque
contrôleur employé par le module : si un re-vendoring écrase le correctif, les tests le
disent. À signaler en amont.

```
backend/gn_module_connectors/
├── core/           socle générique — sans dépendance à une source précise
│   ├── datasets.py     création et mise à jour des JDD
│   ├── report.py       journal des rejets
│   └── synthese.py     insertion par lots
└── sources/gbif/   spécifique GBIF
    ├── api.py          pagination, filtres, licences, provenance
    ├── metadata.py     métadonnées des jeux de données
    ├── griddedness.py  détection des jeux publiés à la maille
    ├── taxonomy.py     résolution du cd_nom via taxonomie.taxref_liens
    └── transform.py    occurrence GBIF -> ligne de synthese
```

La frontière `core` / `sources` est délibérée : c'est elle qui permettra d'accueillir
VisioNature sans réécrire l'écriture, le suivi ni le journal des rejets.
