# Import GBIF → GeoNature (Ariège)

Intégration d'occurrences GBIF dans la Synthèse GeoNature de l'ANA-CEN Ariège.

État des connaissances au **2026-09-08**. Sauf mention contraire, tous les chiffres
ont été mesurés (API `api.gbif.org/v1`, base GeoNature locale, lecture du code source),
pas estimés.

---

## 1. Décisions prises

| Sujet | Décision |
|---|---|
| Territoire | Ariège seule, via `gadmGid=FRA.11.1_1` |
| Statut d'occurrence | `PRESENT` uniquement (les absences sont écartées) |
| Données CC BY-NC | **Conservées**, mais dans un JDD séparé, non exportable |
| Données INPN/PatriNat | **Conservées sélectivement** — tri par jeu de données à faire |
| Architecture | Import en base (pas de consultation à la volée) |
| Outillage | **`gbif2geonature`, connecteur existant** (`~/git/geonature-connectors`) — voir §6 |
| Destination d'écriture | **Synthèse directe + `t_sources` dédiée**, sans module — voir §7. Occtax nu est à proscrire. |

### Reste à trancher

- **Quels jeux PatriNat verser.** C'est la seule étape qui demande un arbitrage humain,
  et elle divise le volume par dix. Liste dans `data/jdd-patrinat-ariege.csv`.
- **Usage commercial des données CC BY-NC** : position interne à arrêter (voir §4).

---

## 2. Chiffres de référence — GBIF Ariège

### Volume global

| | occurrences | part |
|---|---:|---:|
| Total (`gadmGid=FRA.11.1_1`) | 1 309 180 | 100 % |
| dont **UAR PatriNat** (`1928bdf0-f5d2-11dc-8c12-b8a03c50a862`) | 992 352 | 75,8 % |
| hors PatriNat | 316 828 | 24,2 % |
| `occurrenceStatus=PRESENT` | 1 190 194 | 90,9 % |
| `occurrenceStatus=ABSENT` | **118 986** | 9,1 % |

⚠️ Les 118 986 absences sont à **116 799 issues du seul jeu Culicoides du Cirad**.
Importées telles quelles elles deviendraient des présences fausses.

### Licences (ensemble du territoire)

| licence | occurrences | part |
|---|---:|---:|
| CC BY 4.0 | 1 185 709 | 90,6 % |
| CC BY-NC 4.0 | 121 605 | 9,3 % |
| CC0 1.0 | 1 866 | 0,1 % |

Aucun `unspecified` sur ce territoire.

### Nature des enregistrements

`HUMAN_OBSERVATION` 1 244 449 (95,05 %) · `MACHINE_OBSERVATION` 47 787 · `OCCURRENCE` 9 639 ·
`PRESERVED_SPECIMEN` 5 611 · `MATERIAL_SAMPLE` 968 · `MATERIAL_CITATION` 458 ·
`FOSSIL_SPECIMEN` 247 · `LIVING_SPECIMEN` 18 · `OBSERVATION` 3.

### Filtrage géographique — comparatif mesuré

| méthode | compte | verdict |
|---|---:|---|
| `gadmGid=FRA.11.1_1` | 1 309 180 | ✅ **à utiliser** |
| WKT contour officiel (222 pts) | 1 315 607 | ±0,5 %, fragile |
| bbox lat/lon | 2 582 085 | ❌ +97 % (Haute-Garonne, Aude, Catalogne) |

Le GID se résout via `GET /v1/geocode/gadm/search?q=Ariege`.
Sous-découpage disponible : arrondissements `FRA.11.1.1_1` (Foix), `.2_1` (Pamiers), `.3_1` (Saint-Girons).

À noter : `country=FR&gadmGid=FRA.11.1_1` → 1 296 451, soit **12 729 enregistrements**
rattachés à l'Ariège avec un `country` hors France (frontière pyrénéenne). À arbitrer.

Pièges WKT : anneau extérieur obligatoirement **antihoraire** ; la requête casse au-delà
d'environ 3 500 caractères en GET (limite d'en-tête).

---

## 3. Le téléchargement en cours

Clé GBIF : `0006575-260903145123482` — `data/0006575-260903145123482.csv`
(SIMPLE_CSV, donc **TSV**, 612 Mo décompressés).

| | lignes |
|---|---:|
| Total | 1 176 685 |
| `occurrenceStatus=PRESENT` | 1 176 685 ✅ |
| dont PatriNat | 991 563 (84 %) |
| CC BY 4.0 | 1 066 334 |
| CC BY-NC 4.0 | 108 628 |
| CC0 1.0 | 1 723 |

Filtré sur le statut uniquement — **ni la licence ni le publicateur n'ont été filtrés**.

### Scission par licence (faite)

| fichier | lignes |
|---|---:|
| `data/GBIF_ariege_reutilisable.csv` | 1 068 057 (CC BY + CC0) |
| `data/GBIF_ariege_noncommercial.csv` | 108 628 (CC BY-NC) |

La scission est faite sur la **colonne `license` (colonne 43)**, jamais sur `datasetKey` —
c'est indispensable, voir §4.

`scientificName` distincts sur l'ensemble : **20 763**.

---

## 4. Licences et obligations

### Ce qui s'applique à tout (CC BY comme CC BY-NC)

1. **Attribuer par jeu de données source**, pas globalement. Le `citations.txt` de
   l'archive DwC donne la chaîne exacte. Le portail doit pouvoir afficher, par
   observation, sa source.
2. **Conserver l'identifiant de propriété avec chaque enregistrement rediffusé**
   (*Data user agreement* GBIF, explicite) → garder `datasetKey`, `license`,
   `rightsHolder` par ligne, dans `additional_data`.
3. **Citer le DOI du téléchargement**. Chaque téléchargement produit un DOI distinct :
   tracer, par observation, le DOI du lot qui l'a alimentée.
4. **Signaler les modifications** (remapping de nomenclatures, reprojection, filtrage
   sont des modifications au sens de la licence).
5. **Ne pas ajouter de restrictions** techniques au-delà de la licence.

### Spécifique au CC BY-NC

- **Usage non commercial uniquement.** Le piège pour un CEN n'est pas le portail public
  mais la **prestation facturée** : produire un livrable facturé à partir de ces données
  est un usage commercial.
- **Clause virale en aval** : GBIF applique explicitement « la plus restrictive gagne »
  (`License.getMostRestrictive()`). Un export mélangeant NC et CC BY contamine l'ensemble.
- **Interdit de reverser au SINP** — le SINP remonte à l'INPN qui publie en CC BY sur
  GBIF : ce serait à la fois une violation de licence et la boucle de ré-agrégation que
  GBIF refuse (cf. « Which data can be shared through GBIF », rubrique *Examples of data
  that does not fit in GBIF* : « datasets based on data extracted from GBIF and somehow
  modified or cleaned »).

### ⚠️ La licence peut varier à l'intérieur d'un même jeu

**iNaturalist est le seul jeu à licence mixte du corpus ariégeois** (la licence y est
choisie par l'observateur, observation par observation) :

| iNaturalist Research-grade, Ariège | occurrences |
|---|---:|
| CC BY-NC 4.0 | 31 300 |
| CC BY 4.0 | 2 246 |
| CC0 1.0 | 966 |
| **total** | **34 512** |

Vérifié en revanche comme **100 % CC BY-NC** : Observation.org (17 483),
GEODE (47 699), Tela Botanica (1 819).

**Règle : filtrer sur la colonne `license`, jamais sur `datasetKey`.**

### Concentration du CC BY-NC

76 jeux, 108 628 occurrences, dont 97 % chez quatre organisations :

| occ. | part | organisation |
|---:|---:|---|
| 47 699 | 43,9 % | UMR 5602 GEODE (CNRS/UT2J) — pièges photo pyrénéens |
| 31 300 | 28,8 % | iNaturalist.org |
| 17 483 | 16,1 % | Observation.org |
| 8 843 | 8,1 % | **UAR PatriNat** (flore CBN, araignées, CRBPO, frelon asiatique) |

Les 48 derniers jeux totalisent **163 occurrences** (muséums étrangers, Paleobiology
Database, Xeno-canto) — négligeables.

Détail complet : `data/jdd-cc-by-nc.csv`.

> Piste : GEODE pèse 44 % du NC. Une autorisation négociée de gré à gré avec l'UMR
> lèverait la contrainte commerciale sur ce corpus.

---

## 5. Contraintes techniques mesurées

### API GBIF

- **`occurrence/search` est plafonnée à `offset + limit ≤ 100 001`** (HTTP 400 au-delà) et
  la taille de page est ramenée silencieusement à **300**. Inutilisable pour une moisson
  exhaustive. Elle ne délivre par ailleurs **aucun DOI**.
- **`occurrence/download`** est la voie correcte : asynchrone, authentifiée (compte GBIF,
  *username* et non e-mail), prédicats JSON (`GADM_GID`, `LICENSE`, `PUBLISHING_ORG`,
  `OCCURRENCE_STATUS`, `GEOMETRY`…), DOI systématique, `citations.txt` + `rights.txt`
  dans l'archive DwC.
  - 3 téléchargements simultanés max (HTTP 420 au-delà).
  - Une requête identique rejouée sous 48 h **renvoie l'ancien lot** sans rafraîchir.
  - Fichiers conservés 6 mois ; DOI conservé indéfiniment.
- `pygbif` (0.6.6, 2025-11) couvre les deux API, y compris `download()`. Maintenu.

### ⚠️ Le `taxonKey` du format de téléchargement a changé

Les téléchargements émettent désormais des identifiants **alphanumériques**
(`taxonKey=CG59K`), alors que l'API v1 renvoie toujours les **entiers hérités**.
Vérifié sur la même occurrence `gbifID=665828767` : `CG59K` dans le CSV,
`8941235` via `/v1/occurrence/665828767`.

`taxonomie.taxref_liens.ct_sp_id` contient les entiers (664 067 sur 664 083 sont
purement numériques). **La colonne `taxonKey` du CSV est donc inutilisable telle quelle.**

Contournement : résoudre les **20 763 noms distincts** via
`GET /v1/species/match?name=<scientificName>` → `usageKey` entier → jointure sur
`taxref_liens`. Vérifié : `Neomys fodiens fodiens` → `8941235`, match `EXACT`.
À faire une fois et mettre en cache.

### Base GeoNature locale (mesuré)

| | |
|---|---:|
| `gn_synthese.synthese` | 488 153 observations |
| `gn_meta.t_datasets` | 272 JDD |
| `taxref_liens` où `ct_name='GBIF'` | 664 083 lignes, 664 083 `cd_nom` distincts |
| `taxonomie.taxref` | 708 685 |
| → couverture GBIF de TaxRef | **93,7 %** |

### ⚠️ Aucune clé de dédoublonnage contre l'existant

Deux pistes testées, **toutes deux négatives** :

- **Par UUID de jeu de données** : aucun des `unique_dataset_id` locaux n'est connu de
  l'index GBIF (testé sur les 6 plus gros JDD → 0 résultat). Le champ `identifiers[].type=UUID`
  exposé par GBIF est un identifiant de registre (`createdBy: crawler.gbif.org`), pas l'UUID SINP.
- **Par identifiant d'observation** : les `occurrenceID` de l'INPN sur GBIF sont les
  identifiants permanents DEE (style Oracle majuscule,
  `F4BBDF27-3B84-2F30-E053-0514A8C06E0C`). Les `unique_id_sinp` locaux sont des UUID v4/v5
  minuscules générés à l'import. **0 correspondance sur 660 identifiants testés,
  répartis sur 12 jeux.** La campagne d'import SINP n'a pas conservé les identifiants
  permanents DEE.

**Conséquence** : le dédoublonnage contre la Synthèse existante ne peut se faire que par
rapprochement flou (taxon + date + localisation + observateur) ou par **exclusion manuelle
au niveau du jeu de données**. C'est le principal coût caché du projet.

### Contraintes de `gn_synthese.synthese`

- `nom_cite` est **NOT NULL**, `cd_nom` est **nullable** (FK vers `taxref`).
  Une occurrence non résolue est donc insérable — mais attention, l'évaluation des
  permissions plante sur `cd_nom NULL` dès qu'un filtre taxonomique est en jeu
  (`Synthese.taxref_tree.path.split(".")`).
- `id_source` est **NOT NULL** → créer une entrée `gn_synthese.t_sources`
  (`name_source='GBIF'`, `url_source='https://www.gbif.org/occurrence'`,
  `entity_source_pk_field='gbifID'`). Suffit à rendre la donnée filtrable dans l'UI
  Synthèse **sans une ligne de code frontend** (filtre `id_source` déjà présent).
- `unique_id_sinp` porte une contrainte **UNIQUE** → un UUID v5 déterministe dérivé de
  l'`occurrenceID` rend le moissonnage idempotent.
- **Le trigger `tri_insert_calculate_sensitivity` écrase systématiquement
  `id_nomenclature_sensitivity`** à l'insert (l'`UPDATE` n'a pas de clause
  `WHERE ... IS NULL`). Impossible d'imposer « non sensible » à l'insert.
  Porte de sortie : un `UPDATE` post-insert de cette seule colonne ne redéclenche pas
  `tri_update_calculate_sensitivity` (qui est `BEFORE UPDATE OF <colonnes listées>`).
- `tri_insert_cor_area_synthese` fait **N intersections PostGIS par ligne** — poste de coût
  dominant. Insérer par lots ≥ 1000 (le module Import utilise `INSERT_BATCH_SIZE=1000`).
- Penser à filtrer `gn_profiles.v_synthese_for_profiles` sur `id_source`, sinon les
  profils de taxons sont pollués.

### Outillage existant — inutilisable en l'état

- **Le cœur de GeoNature 2.17.2 ne contient aucun code GBIF / Darwin Core / DwC-A / IPT.**
  Tout est à écrire.
- **Module Import du cœur** : CSV avec séparateur `,` ou `;` uniquement (le SIMPLE_CSV
  GBIF est un TSV zippé), `cd_nom` **obligatoire** (`CD_NOM_NOT_FOUND`), et **aucun mode
  upsert** — une ré-exécution rejette (`EXISTING_UUID`) ou saute. Il force en outre
  `id_source` sur la source `"Import"`.
- **api2GN** (`PnX-SI/api2GN`, branche `feat/gbif_parser`, non fusionnée) : projet en
  quasi-abandon (dernière release 2023-08-11, PR #2 ouverte depuis avril 2025 avec une
  revue non traitée, PR #6 sans réponse, issue #7 sans réponse). Défauts rédhibitoires :
  plafond 100 000 hérité de l'API `search`, `unique_id_sinp` jamais renseigné (il tente
  `UUID(identifiers[0].identifier)` alors que GBIF y met un entier), `eventDate` en
  intervalle → perte de tout le lot, commit unique global, `additional_data` jamais
  alimenté, zéro test.
- **Voie SQL peu connue** : `gn_synthese.import_json_row(jsonb, geojson)` et
  `import_row_from_table(...)` font un **UPSERT sur `unique_id_sinp`**. Toujours maintenues
  (réécrites par la migration `ca052245c6ec`). Aucun contrôle métier, une ligne par appel,
  et `import_json_row` crée une table temporaire non schéma-qualifiée → non concurrent-safe.

---

## 6. Le connecteur existant : `gbif2geonature`

Emplacement : `~/git/geonature-connectors` (non versionné, pas un dépôt git).
Paquet Python installable, commandes `gbif2geonature` et `vn2geonature`.

**Architecture** : écrit via l'**API Occtax** de GeoNature, pas en SQL direct dans
`gn_synthese.synthese`. Les données transitent donc par Occtax et arrivent en Synthèse
par le trigger `pr_occtax.insert_in_synthese`.

**Points forts vérifiés**

- `sinp_uuid()` (`gbif2geonature/geonature.py`) réutilise l'UUID contenu dans
  l'`occurrenceID` s'il existe, sinon dérive un `uuid5` du `gbifID`. Pour les données
  INPN, les observations importées portent donc **les identifiants permanents DEE**
  comme `unique_id_sinp` → dédoublonnage exact avec toute reprise SINP ultérieure.
- Mapping de nomenclatures complet : `ETA_BIO`, `STATUT_SOURCE`, `METH_OBS`,
  `STATUT_OBS`, `OBJ_DENBR`, `TYP_DENBR`, avec traitement du cas `FOSSIL_SPECIMEN`.
- Résolution `cd_nom` en deux voies : fichier `TAXREFv17.txt` local (index `LB_NOM` →
  `CD_NOM`), avec repli sur `species/{taxonKey}/related` filtré sur le référentiel
  TAXREF publié dans GBIF (`datasetKey=0e61f8fe-7d25-4f81-ada7-d970bbb2c6d6`).
- Suivi local (`tracking.py`) en `append` O(n) et `save()` atomique par `os.replace`.
- Filtres : `exclude_dataset_terms`, `exclude_dataset_keys`, `include_observers`,
  `coordinate_uncertainty_max`. La liste d'exclusion couvre déjà Faune Occitanie,
  Faune France, SICEN, ANA, LIFE Desman, Natura 2000, PRA.
- `config.toml` utilise bien `gadmGid = "FRA.11.1_1"` (transmis via `GBIFConfig.extra`,
  `config.py:114-115`). ⚠️ Ne **pas** utiliser `state_province` : `stateProvince=Ariège`
  ne renvoie que **1 215** occurrences contre 1 309 180 par `gadmGid` — le champ est
  verbatim et rarement renseigné, avec des orthographes variables (`Ariege` : 140).

**Manques identifiés**

- ~~Aucun filtrage par licence~~ — ✅ corrigé le 2026-09-08 (clés `licenses` et `dataset_id_nc`).
- Plafond de 100 001 de l'API `search`, contourné par `import_tranches.sh` et un
  découpage par tranches de dates (`tranches/config_1856_2000.toml`).
- Débit : une requête HTTP Occtax par relevé (~10/s → 3 h pour 100 000 occurrences).

**Arbitrage `search` ou `download`**

| | `search` (actuel) | `download` |
|---|---|---|
| Volume | plafonné à 100 001 par requête → découpage par dates | illimité |
| `taxonKey` | entiers hérités ✅ | nouveau format alphanumérique ⚠️ (voir §5) |
| DOI de citation | ❌ | ✅ |
| Prédicat `LICENSE` | paramètre `license=` | ✅ |

Rester sur `search` évite entièrement le problème de format du `taxonKey`, qui ne
concerne que les téléchargements.

**État d'avancement** : `gbif_imported_ids.csv` est vide (en-tête seul) ;
`gbif_imported_ids.csv.bak` contient 2 lignes de test du 2026-06-22.

---

## 7. Où écrire : Occtax, module dédié, ou Synthèse directe ?

**Testé sur l'instance le 2026-09-08.**

### État de l'instance

| | |
|---|---:|
| `gn_synthese.synthese` | 488 153 obs., **toutes du module `IMPORT`** (Occtax : 0) |
| source `Import` | `url_source = NULL` → aucun lien retour vers l'origine aujourd'hui |
| `cor_permission_taxref` | 0 |
| `cor_permission_area` | 0 |
| permissions `sensitivity_filter` | 0 |
| lignes `cd_nom IS NULL` | 0 |

Schéma vérifié : `id_module` **nullable**, `id_source` **NOT NULL**,
`cd_nom` et `id_dataset` nullables.

### `id_module = NULL` est sans danger pour le CRUVED

`Synthese._has_permissions_grant()` (`backend/geonature/core/gn_synthese/models.py`)
**ne référence jamais `id_module`**. Elle évalue la sensibilité, la portée
(`scope_value` : numérisateur / observateur / accès au JDD), le filtre géographique
et le filtre taxonomique. Les permissions sont évaluées au titre du module `SYNTHESE`,
quel que soit le `id_module` de la ligne.

`id_module` n'apparaît qu'en `query_select_sqla.py:450-451`, comme **filtre fourni par
l'IHM**, pas comme contrôle d'accès.

→ Seule conséquence, cosmétique : les lignes à `id_module` NULL ne remontent pas si
l'utilisateur filtre par module, et la colonne « module » reste vide. Le filtre
« Source de la donnée » les trouve.

### ⚠️ Ne jamais insérer avec `cd_nom` NULL

Le filtre taxonomique fait `self.taxref_tree.path.split(".")`. Sur une ligne à
`cd_nom` NULL, `taxref_tree` est `None` → **`AttributeError`**.

Aujourd'hui le risque est nul (0 permission taxonomique, 0 ligne à `cd_nom` NULL),
mais c'est une bombe à retardement : elle n'explosera que le jour où une permission
avec restriction taxonomique sera créée, sans lien évident avec la cause.

**Règle : rejeter et journaliser les occurrences dont le `cd_nom` n'est pas résolu,
plutôt que les insérer avec `cd_nom` NULL.**

### Comparatif des trois destinations

| | Occtax nu | Occtax dupliqué | **Synthèse directe** |
|---|---|---|---|
| Sémantique | ❌ module de saisie | ~ moteur de saisie détourné | ✅ table d'agrégation |
| Producteur déclaré | ❌ toi (mensonge SINP) | ~ | ✅ source externe |
| Éditable par les agents | ❌ oui | ~ oui | ✅ non |
| Filtre « source = GBIF » | ❌ non | ✅ | ✅ |
| Lien vers `gbif.org` | ❌ | ❌ (`url_source` figé sur la route Occtax) | ✅ |
| Coût | nul | 1 commande | mécaniques d'insert à reprendre |

**Occtax nu est à proscrire** : le JDD te désignerait comme producteur, et un versement
SINP republierait de la donnée GBIF sous ton nom — violation d'attribution CC BY *et*
boucle de ré-agrégation refusée par GBIF.

**Occtax dupliqué** : `geonature occtax create-duplicated-module GBIF "GBIF"`
(`contrib/occtax/backend/occtax/commands.py:48-140`) crée module + `t_sources` +
permissions en une commande, en réutilisant le moteur Occtax (`ng_module="occtax"`).
Compromis acceptable pour une première mise en service. Limite : `url_source` est figé
sur `#/<module>/info/id_counting` et `entity_source_pk_value` reste l'`id_counting_occtax`
— pas de lien vers `gbif.org` sans retouche.

**Synthèse directe + `t_sources` dédiée** : option retenue. C'est ce pour quoi le couple
Synthèse / `t_sources` est conçu. Aucun module nécessaire (`id_module` nullable).
Donne gratuitement le filtre par source et le bouton « voir la donnée source » vers
`https://www.gbif.org/occurrence/<gbifID>`.

### Le connecteur double-t-il le module Import ?

Non. Import est un **workflow de téléversement de fichier** (dépôt CSV → mapping →
contrôles → destination) ; sa notion de « destination » désigne *où la donnée atterrit*,
pas d'où elle vient. Un moissonneur tire périodiquement depuis une API, sans interaction.
Le recouvrement se limite à la couche transformation/contrôles — et Import ne peut de
toute façon pas faire le travail (CSV `,`/`;` seulement, `cd_nom` obligatoire, pas d'upsert).

En revanche, **un module complet avec IHM redévelopperait beaucoup de plomberie Import
pour peu de gain** : c'est là que le soupçon de doublon est fondé.

---

### Précision géographique — repères mesurés (2026-09-08)

Sur l'Ariège, `occurrenceStatus=PRESENT` (1 190 194 occurrences) :

| seuil d'incertitude | occurrences | part |
|---|---:|---:|
| ≤ 10 m | 139 279 | 11,7 % |
| ≤ 100 m | 327 791 | 27,5 % |
| ≤ 500 m | 395 623 | 33,2 % |
| ≤ 1 100 m | 474 689 | 39,9 % |
| ≤ 5 000 m | 710 463 | 59,7 % |
| ≤ 10 000 m | 768 278 | 64,6 % |
| **sans incertitude déclarée** | **415 295** | **34,9 %** |

Deux conséquences pratiques :

- **Un seuil de 1 100 m écarte 60 % du corpus.** Certains jeux publient intégralement à
  faible précision : « INPN - Données flore des CBN » est à 5 km sur la totalité de ses
  occurrences ariégeoises. Un seuil trop bas ne dégrade pas l'import, il l'annule.
- **Un tiers des occurrences ne déclarent aucune incertitude.** Les conserver revient à
  accepter une précision inconnue, potentiellement pire que le seuil qu'on rejette par
  ailleurs. D'où l'option explicite `--keep-unknown-uncertainty` /
  `--drop-unknown-uncertainty`.

⚠️ **Le filtre API et le filtre local ne sont pas équivalents** :
`coordinateUncertaintyInMeters=0,N` côté GBIF ne retient que les enregistrements *ayant*
le champ, donc écarte les 34,9 % d'inconnues. Le module ne pousse donc le filtre à l'API
que si `--drop-unknown-uncertainty` est demandé ; sinon il filtre localement, ce qui
permet de tracer chaque rejet avec son incertitude réelle.

---

## 8. Reste à faire

1. ~~**Ajouter le filtrage par licence à `gbif2geonature`**~~ — ✅ **fait le 2026-09-08**.
   Clé `licenses` dans `[filter]` (filtre côté API *et* local),
   `dataset_id_nc` dans `[geonature]` pour le routage vers un second JDD,
   `gbif.normalize_license()` pour l'asymétrie énumération/URL de l'API.
   Sauvegarde des fichiers d'origine dans ``.backup-*/` à la racine du dépôt`.
2. ~~**Traçabilité des rejets**~~ — ✅ **fait le 2026-09-08**. Module générique
   `gbif2geonature/report.py` (`Rejects`), branché sur les cinq filtres et les quatre
   points de rejet de `cli.py`. Écrit `gbif_rejets.csv` (`reason;record_id;label;detail`).
   Conçu pour être remonté tel quel dans le socle commun lors de la fusion avec
   `vn2geonature`.
3. ~~**Mapping des nomenclatures**~~ · ~~**ordonnanceur**~~ · ~~**tests**~~ —
   ✅ faits le 2026-09-08. Mapping complet (`sources/gbif/nomenclatures.py`), tâche
   Celery hebdomadaire avec court-circuit sur les jeux inchangés, 54 tests unitaires.
4. **Compléter la liste d'exclusion `datasetKey`** à partir de
   `data/jdd-patrinat-ariege.csv` (200 jeux) — la liste actuelle couvre déjà
   Faune Occitanie, Faune France, SICEN, ANA, LIFE Desman, Natura 2000, PRA.
4. **Décider `search` ou `download`** (voir §6) — arbitrage entre le plafond
   de pagination et le format du `taxonKey`.
5. ~~**Porter les métadonnées de licence**~~ — ✅ **fait le 2026-09-08**.
   `gbif.provenance()` (13 clés : `dataset_key`, `rights_holder`, `license`,
   `license_url`, `gbif_url`, `gbif_download_doi`…) écrit dans `additional_fields`,
   et `gbif.attribution_text()` dans le `comment` pour l'exigence de visibilité.
   DOI configurable dans `[provenance] download_doi`.
   **Reste** : marquer les JDD non re-partageables vers le SINP (action Métadonnées).
6. **Basculer l'écriture d'Occtax vers la Synthèse** (`gbif2geonature/geonature.py`) —
   décision §7. Implique de créer la source `t_sources` GBIF
   (`url_source='https://www.gbif.org/occurrence'`,
   `entity_source_pk_field='gbifID'`), de stocker le `gbifID` en
   `entity_source_pk_value`, et de reprendre les mécaniques d'insert (lots ≥ 1000,
   sensibilité écrasée par le trigger, coût `cor_area_synthese` — voir §5).
   `id_module` peut rester NULL.
7. ~~**Rejeter les occurrences à `cd_nom` non résolu**~~ — ✅ déjà en place, désormais tracé au lieu de les insérer avec
   `cd_nom` NULL (voir §7, `AttributeError` différée sur le filtre taxonomique).

---

## 9. Fichiers

| chemin | contenu |
|---|---|
| `data/jdd-patrinat-ariege.csv` | 200 jeux PatriNat sur l'Ariège : volume, licence, DOI, `datasetKey` |
| `data/jdd-cc-by-nc.csv` | 76 jeux CC BY-NC : volume, organisation, `datasetKey` |
| `data/GBIF_ariege_reutilisable.csv` | 1 068 057 lignes CC BY + CC0 |
| `data/GBIF_ariege_noncommercial.csv` | 108 628 lignes CC BY-NC |
| `data/0006575-260903145123482.csv` | téléchargement brut (612 Mo) |

> Une copie du téléchargement traîne à la racine de `~/dev/GeoNature`
> (`0006575-260903145123482.csv`, non suivie par git) — à déplacer ou exclure.
