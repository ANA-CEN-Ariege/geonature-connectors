# GBIF

*(↑ retour au [README](../../README.md#gbif))*

```toml
[gbif]
enabled = true
gadm_gid = "FRA.11.1_1"      # l'Ariège ; voir gbif-synchroniser-jeux pour le vôtre
```

Aucun compte ni jeton : l'API GBIF est publique. Le seul réglage indispensable est le
périmètre géographique.

## Les réglages qui comptent

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

## 1. Prévisualiser (facultatif)

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

## 2. Importer les occurrences

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

## 3. Automatiser

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
