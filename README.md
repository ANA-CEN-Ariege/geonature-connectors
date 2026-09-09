# gn_module_connectors

Module GeoNature d'alimentation de la Synthèse depuis des sources externes.

| Source | État |
|---|---|
| **GBIF** (Global Biodiversity Information Facility) | fonctionnel |
| **VisioNature / Biolovision** | à porter — code d'origine dans le dépôt d'archive `geonature-connecteurs-autonomes` |

Le module tourne **dans** GeoNature. Il utilise donc `db.session` directement : aucun
identifiant PostgreSQL à distribuer, insertion par lots plutôt qu'une requête HTTP par
observation, et installation à la portée d'un administrateur fonctionnel.

---

## Installation

```bash
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
```

⚠️ **Redémarrer le backend juste après.** L'installation enregistre le module en base,
mais les workers gunicorn déjà lancés n'en voient pas le code Python : tant qu'ils ne sont
pas relancés, `/gn_commons/modules` renvoie une erreur 500 et l'interface est inutilisable.

```bash
docker restart <conteneur-backend>     # ou : systemctl restart geonature
```

Vérifier :

```bash
geonature connectors status
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

## Configuration

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

## Utilisation

### 1. Prévisualiser (facultatif)

```bash
geonature connectors gbif-sync-datasets --dry-run
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

`gbif-sync-datasets` reste utile pour prévisualiser le périmètre, et pour rafraîchir les
métadonnées — titre, citation, DOI — quand un producteur les corrige.

### 2. Importer les occurrences

```bash
geonature connectors gbif-import \
    --dataset-key <clé> --gadm-gid FRA.11.1_1 \
    --max-uncertainty 1000 --dry-run
```

Options utiles : `--max-results` pour plafonner, `--batch-size`, `--download-doi`,
`--keep-unknown-uncertainty` / `--drop-unknown-uncertainty`.

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

⚠️ `--force` est indispensable après un changement de mapping : GBIF n'a alors rien
modifié, mais les données doivent tout de même être réécrites.

---

## Tests

```bash
python3 -m pytest tests/ -q
```

54 tests, sans dépendance à GeoNature ni à la base. Ils couvrent les cas qui ont
réellement mordu pendant le développement : le faux-ami `Nymph` / « Nymphe », les dates
en intervalle ISO, l'asymétrie énumération/URL des licences, la distinction entre origine
du taxon et état de l'individu, et le déterminisme de l'identifiant unique.

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

**La suppression n'est pas gérée.** Une occurrence retirée de GBIF reste en base : la
détecter supposerait de comparer l'ensemble des identifiants du périmètre à chaque
passage, ce qui annulerait le bénéfice du court-circuit.

**Le court-circuit repose sur `dataset.modified`.** Si un producteur pousse des données
sans mettre cette date à jour, le jeu sera sauté à tort. Une exécution `--force`
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
