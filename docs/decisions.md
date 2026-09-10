# Journal des décisions

Ce fichier garde le **pourquoi**. Le [README](../README.md) dit comment installer,
configurer et lancer le module ; on trouvera ici ce qui a été mesuré, essayé, écarté, et
les raisons des choix qui pourraient sembler arbitraires en lisant le code.

Il s'adresse à qui doit modifier le module, ou comprendre pourquoi il se comporte d'une
certaine façon plutôt qu'à qui veut simplement l'employer. Les chiffres qu'il contient ont
été relevés sur des instances réelles, à la date indiquée ; ils ne sont pas des ordres de
grandeur théoriques, et ils peuvent avoir vieilli.

Deux catégories s'y mêlent, et il vaut la peine de les distinguer en lisant :

- les **arbitrages**, qui pourraient être repris autrement par quelqu'un d'autre ;
- les **résultats négatifs** — ce qui a été essayé sans succès. Ceux-là ont le plus de
  valeur : ils évitent de refaire une enquête qui ne mène nulle part.

---

## Conventions
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

---

## GBIF
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

---

## VisioNature
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
departements = ["09"]        # OBLIGATOIRE — l'API refuse une recherche sans périmètre
date_debut = "2015-01-01"    # vide = tout l'historique
tranche_jours = 15
```

`--departement` et `--debut` priment sur ces deux réglages, sans les remplacer : la
configuration porte le cas courant, les options le partitionnement. L'absence de
périmètre est refusée **avant tout appel réseau** — sinon on paierait le référentiel de
63 616 espèces, plusieurs minutes, pour s'entendre refuser ensuite.

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

### Ce que `additional_data` conserve

`gn_synthese.synthese` ne sait pas tout exprimer. Ce que le SINP ignore mais que la
donnée porte est conservé en `jsonb` — indexable en GIN si l'analyse le demande :

| clé | contenu |
|---|---|
| `details` | ventilation par âge, sexe, effectif, distance |
| `behaviours` | comportements notés (ponte, tandem, stridulation…) |
| `atlas_code` | code EOAC brut, dont le SINP ignore la gradation |
| `repro_degre`, `repro_indice` | degré déduit et code qui l'a produit |
| `groupe_taxo` | groupe VisioNature, la classification de TAXREF ne le recoupant pas |
| `juridical_person` | organisme de rattachement de l'observateur |
| `precision_type`, `heure_connue`, `masquee_source`, `anonymat` | traces de décision |

`gn_vn2synthese` place l'équivalent dans une table `t_c_synthese_extended` en relation
1:1. Le choix du `jsonb` évite un schéma à maintenir et des jointures dans chaque
requête ; rien n'empêchera d'ajouter une table typée le jour où l'analyse le justifiera,
les données seront là.

⚠️ **Le relevé brut n'est PAS conservé**, et cela ne relève pas de l'oubli. Il porte
`observers[].name`, y compris pour ceux qui ont demandé l'anonymat : le stocker
contredirait le dispositif autour duquel tout le module est bâti. L'expurger d'abord
rendrait la conservation partielle — le défaut de lecture d'`anonymous_in_export`
n'aurait pas pu être réparé par retransformation, puisque les noms auraient déjà été
retirés. Conservation du brut et anonymisation sont en tension irréductible.

Pour la même raison, `juridical_person` suit le sort du nom : dans une petite structure,
l'organisme ré-identifie aussi sûrement qu'un patronyme.

Le relevé n'est enfin **pas déplié** : `details` reste un tableau dans une ligne unique
de Synthèse, et non plusieurs lignes. Les raisons sont dans `deplier` — absence de clé
stable, et le fait que la reproduction soit une propriété de l'ensemble.

### Jeux de données, producteurs et personnes morales

Un jeu de données **sans producteur n'est pas conforme au SINP**. Rien dans GeoNature ne
l'impose techniquement, d'où la facilité avec laquelle on l'oublie — ce module l'a oublié
jusqu'ici.

Trois niveaux de personne morale coexistent, et ils ne vivent pas au même endroit :

| niveau | exemple | où |
|---|---|---|
| structure qui met à disposition | Collectif Faune-Occitanie | acteur du JDD, rôle **fournisseur** (`ROLE_ACTEUR 5`) |
| structure qui produit | ANA-CEN Ariège, GOR | acteur du JDD, rôle **producteur** (`ROLE_ACTEUR 6`) |
| organisme de l'observateur | employeur d'un salarié | `additional_data.juridical_person` |

Le troisième ne peut pas être un acteur : il varie d'une observation à l'autre, alors
qu'un acteur qualifie le jeu entier. GeoNature n'offre pas de champ d'organisme par
observation, et c'est cohérent avec le standard.

**Le découpage des JDD suit donc le département**, puisque c'est là que change le
producteur. Le code projet reste un axe secondaire, distinguant des programmes au sein
d'un même producteur.

```toml
[visionature]
organisme_fournisseur = "Collectif Faune-Occitanie"

[visionature.producteurs_departementaux]
"09" = "ANA-CEN Ariège"
"66" = "GOR"
```

**Le cadre d'acquisition est qualifié lui aussi.** Créé par la migration du module, il
en sort sans territoire ni contact principal — que la migration ne peut pas connaître,
puisqu'ils dépendent de l'instance et de la structure qui l'exploite. Le formulaire de
GeoNature refuse alors de l'enregistrer, exactement comme pour un jeu de données. La
qualification a lieu à chaque import plutôt qu'à la migration, de sorte qu'une
configuration renseignée après coup rattrape un cadre déjà créé.

⚠️ **Le module ne crée jamais d'organisme.** Il les résout par leur nom dans
`utilisateurs.bib_organismes` et signale ceux qu'il ne trouve pas, sans interrompre
l'import. Les tirer des données peuplerait le référentiel de variantes d'orthographe —
« LPO Occitanie », « LPO-Occitanie », « Ligue pour la Protection des Oiseaux Occitanie » —
que plus personne ne saurait rapprocher ensuite.

Les acteurs sont posés à **chaque passage**, pas seulement à la création du jeu : une
configuration corrigée après coup rattrape ainsi les jeux déjà créés.

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

---

## dbChiro
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

### Le filtre de périmètre est doublé

`area` est appliqué côté serveur, mais **un paramètre inconnu de l'API DRF est ignoré
sans erreur** : rien ne distinguerait un filtre appliqué d'un filtre inexistant. Le code
de département est donc revérifié sur les zonages de chaque observation reçue — dbChiro
les publie dans la réponse : département, commune INSEE, maille 10 km, ZNIEFF, parc. Un
écart massif est signalé en fin de moissonnage.

---

## GeoNature
### Pourquoi pas api2GN

[api2GN](https://github.com/PnX-SI/api2GN) fait la même chose et il vise **le bon
endroit**, dont ce connecteur reprend l'idée. Il n'a pourtant pas été adopté :

- c'est un **module GeoNature à part entière**, avec ses tables, sa CLI
  (`geonature parser run`) et ses parsers déclarés dans un fichier Python de
  configuration. L'employer reviendrait à installer un second module d'alimentation de la
  Synthèse à côté de celui-ci, avec deux journaux, deux façons de purger et deux endroits
  où chercher quand une donnée manque ;
- il insère par `db.session.add(Synthese(...))`, **sans `ON CONFLICT`** — c'est le défaut
  que `core/synthese.py` documente déjà à propos de son parser GBIF : un moissonnage
  rejoué duplique ou échoue ;
- son `GeoNatureParser` passe les colonnes de nomenclature à
  `ref_nomenclatures.get_id_nomenclature()`, **qui attend un `cd_nomenclature`**, alors
  que la vue `gn_exports.v_synthese_sinp` livre des `label_default` — des libellés
  français. La fonction rend NULL, l'insertion réussit, et les quinze colonnes de
  nomenclature se remplissent de rien. Aucune erreur, aucun message.

Ce dernier point est le cœur du connecteur écrit ici, et une bonne moitié de
`tests/test_geonature.py` existe pour qu'il ne puisse pas se reproduire sans qu'un test
rougisse.

### Pourquoi l'API du module d'export, et non celle de la Synthèse

| | pagination | plafond | authentification |
|---|---|---|---|
| `POST /synthese/export_observations` | **aucune** | `NB_MAX_OBS_EXPORT`, 50 000 par défaut | compte + permission `E` |
| `GET /synthese/for_web` | `limit` seul | `NB_MAX_OBS_MAP` | compte |
| `GET /api/exports/api/<id>` | `limit` + `offset` | par page seulement | **jeton d'export** |

L'API du cœur rend un *fichier*, sans `limit` ni `offset`, et tronque en silence au-delà
du plafond. On ne peut pas bâtir un moissonnage là-dessus. `/synthese/for_web` sert une
vue d'affichage cartographique, sans nomenclatures ni identifiants de jeu.

Le distant doit donc avoir `gn_module_export` installé, un export déclaré sur
`gn_exports.v_synthese_sinp`, et un jeton pour cet export.

⚠ **Aucune commande ne peut lister les exports du distant.** La route `GET /` du module
d'export est `@permissions_required` : elle exige un compte, là où le jeton n'ouvre que
`/api/exports/api/<id>`. L'`id_export` se demande à l'administrateur distant, il ne se
découvre pas.

### Ce que le connecteur reprend du producteur

C'est le seul connecteur à recréer les métadonnées **sous les identifiants SINP du
producteur** — c'est ce que dit le standard : un jeu de données garde son identité d'une
plateforme à l'autre.

| | |
|---|---|
| `unique_id_sinp` | l'`id_perm_sinp` distant, verbatim |
| `unique_id_sinp_grp` | l'`id_perm_grp_sinp` distant, verbatim |
| jeu de données | un par `jdd_uuid` distant, portant ce même `unique_dataset_id` |
| cadre d'acquisition | un par `ca_uuid` distant, portant ce même UUID |
| `entity_source_pk_value` | l'`id_synthese` distant |
| `id_nomenclature_diffusion_level` | le `precision_diffusion` du producteur |

Cela a trois conséquences qu'il faut connaître.

**Un jeu déjà présent localement est alimenté, pas dupliqué.** Même UUID = même objet
SINP. Mais ses métadonnées ne sont pas réécrites : il a pu être créé par un dépôt SINP ou
enrichi à la main, et le nom que l'API nous donne n'est pas forcément meilleur.
`mettre_a_jour_jdd_existants = true` force le rafraîchissement. Un jeu **désactivé** est
refusé : GeoNature le masque, et y verser des observations les rendrait invisibles.

**Une observation peut déjà être en base sous une autre source**, et ce n'est pas un cas
limite. `sources/gbif/transform.sinp_uuid` reprend l'UUID contenu dans `occurrenceID`
quand c'en est un : **100 % des jeux publiés par PatriNat**, soit **75,8 % du corpus GBIF
ariégeois** (`docs/gbif-ariege.md`), et précisément ce qu'une instance partenaire détient
aussi — Faune Occitanie, SICEN, ANA, LIFE Desman, Natura 2000. Moissonner un GeoNature
voisin *et* le GBIF sur le même territoire produit des collisions par milliers.

⚠ **Le conflit ne peut pas être arbitré à l'écriture, et il faut le comprendre avant de
choisir un réglage.** `INSERT_SQL` ne réécrit jamais `id_source` — la colonne reste au
premier connecteur qui a inséré la ligne — mais son `DO UPDATE` écrase tout le contenu,
`additional_data` compris. Ce qui donne, en partant d'une ligne déjà importée du GBIF :

| étape | `id_source` | contenu |
|---|---|---|
| `gbif-import` | GBIF | GBIF |
| `geonature-import`, `« remplacer »` → DELETE puis INSERT | GeoNature | GeoNature |
| **`gbif-import` suivant** — `ON CONFLICT`, empreintes différentes → UPDATE | **GeoNature** | **GBIF** |
| `geonature-import` suivant — même comparaison en sens inverse | GeoNature | GeoNature |

À partir de la troisième ligne, `conflits_autre_source` **ne détecte plus rien** : la
ligne est bien sous notre `id_source`. Les deux connecteurs se réécrivent alors en
silence, une fois par exécution, sans fin.

`« remplacer »` ne supprime donc pas l'oscillation — **il la déclenche**, sauf si l'autre
connecteur cesse de moissonner ces observations. `« ignorer »` (le défaut) est la seule
option stable, parce que le connecteur n'écrit tout simplement pas ; le prix est de
conserver la copie du GBIF, qui a pu perdre en précision géographique et en nomenclatures
lors de la republication.

**Le bon remède est en amont** : écarter les jeux que ce GeoNature publie déjà, côté GBIF,
par `[gbif] exclude_dataset_keys` — puis seulement passer à `« remplacer »` pour reprendre
les lignes déjà en base. `geonature-import` avertit quand `« remplacer »` est demandé
alors que `[gbif] enabled` est vrai.

Comme un conflit installé devient indétectable par `id_source`, `geonature-couverture` et
le bilan d'import comptent séparément les lignes **portant notre `id_source` mais
dépourvues de `gn_empreinte`** : nos `to_row` l'écrivent systématiquement, son absence
prouve donc qu'un autre connecteur est passé après nous. C'est le seul moyen de voir la
bagarre.

**Un `id_perm_sinp` absent est le cas le plus dangereux**, parce qu'il ne casse rien :
`unique_id_sinp` est nullable, NULL n'est jamais égal à NULL, l'index unique ne
dédoublonne pas et *chaque passage recréerait tout le corpus*. Un uuid5 est donc dérivé de
`(instance, export, id_synthese)` et conservé sous `gn_uuid_calcule` — le jour où le
producteur publiera son UUID natif, `core.synthese.realigner_uuid` renommera la ligne au
lieu de la dupliquer, exactement comme cela s'est fait pour VisioNature.

---

## Pré-validation
### Deux écritures, et la seconde commande la première

| | rôle |
|---|---|
| `synthese.id_nomenclature_valid_status` | ce qu'affiche et filtre la Synthèse |
| `gn_commons.t_validations` | l'historique, avec `validation_auto` |

Le connecteur écrit **l'historique**, et le trigger `tri_insert_synthese_update_validation_status`
du cœur recopie statut, commentaire et `meta_validation_date` dans la Synthèse, apparié
sur `unique_id_sinp`. L'inverse ne marcherait pas : écrire la colonne seule laisse le
module Validation aveugle, et son filtre « masquer les validations automatiques »
s'appuie sur `last_validation.validation_auto`, qui n'existerait pas.

Cet UPDATE ne touche aucune colonne de la liste `UPDATE OF` des déclencheurs de zonage et
de sensibilité : les ~9 lignes de `cor_area_synthese` par observation ne sont pas
recalculées.

`tri_meta_dates_change_synthese` se déclenche en revanche — `BEFORE UPDATE`, sans
`UPDATE OF` — et repose `meta_update_date = NOW()`. Chaque observation devrait donc
paraître « modifiée depuis sa validation » à l'instant même où elle est validée. Elle ne
l'est pas **parce que `NOW()` rend l'heure de la transaction et non celle du statement** :
les deux colonnes, toutes deux `timestamp without time zone`, reçoivent la même valeur.
D'où une contrainte à ne pas défaire : l'historique s'écrit dans la transaction de
l'INSERT, et l'en sortir casserait ce filtre sans rien signaler.

**L'historique n'est écrit qu'une fois par observation.** Trois raisons : un validateur
qui tranche ensuite a le dernier mot — le module retient la validation la plus récente ;
le filtre « modifiée depuis sa validation » compare `meta_update_date` à
`validation_date`, et rafraîchir la date à chaque passage éteindrait ce signal ; enfin un
journal où chaque moissonnage empile une ligne cesse d'en être un.

### Ce que fait `gn_vn2synthese`, et en quoi nous divergeons

La LPO ne pose pas un statut global : elle le **déduit** de la validation VisioNature
elle-même, dans son upsert SQL.

```sql
CASE
  WHEN un comité (chr/chn) a « ACCEPTED »        → STATUT_VALID '1'  Certain
  WHEN admin_hidden OU aucun comité n'a accepté  → STATUT_VALID '3'  Douteux
  ELSE                                            → STATUT_VALID '2'  Probable
END
```

Quand `committees_validation` est absent, `'ACCEPTED' = ANY(NULL)` vaut NULL : ni la
première branche ni la seconde ne se déclenchent, et le `ELSE` s'applique. **« Probable »
est donc leur plancher**, appliqué à tout ce qui n'est jamais passé devant un comité
d'homologation — l'immense majorité.

Deux divergences assumées :

- ils écrivent **uniquement** la colonne de la Synthèse. Aucune occurrence de
  `t_validations`, `validation_auto` ni `validable` dans leur dépôt : le module
  Validation ne peut pas distinguer leurs données importées des données saisies ;
- la déduction par comité n'est pas transposée. `committees_validation` et `admin_hidden`
  sont absents des 114 relevés des exports réels dont nous disposons ; l'implémenter
  suppose d'abord de mesurer ce que l'API rend sur l'instance visée. La règle par défaut
  du module — `is_doubtful` de dbChiro prime sur la pré-validation globale — est le seul
  cas où une source contredit le statut global.

---

---

## Tests

### Ce que les cas couvrent, source par source

Le [README](../README.md#tests) dit comment lancer la suite et nomme les deux fichiers
structurants. Le détail de ce que chaque connecteur met à l'épreuve est ici, parce qu'il
raconte surtout les défauts rencontrés en chemin.

Les cas couvrent ce qui a réellement mordu pendant le développement : le faux-ami `Nymph` / « Nymphe », les dates
en intervalle ISO, l'asymétrie énumération/URL des licences, la distinction entre origine
du taxon et état de l'individu, et le déterminisme de l'identifiant unique.

Côté dbChiro, les 52 cas portent sur ce qu'un contrôle de base ne verrait pas : les codes
d'absence qui deviendraient des présences, le couple `Nyctalus / Tadarida` qui franchit
une frontière de famille, le contresens « Estivage » → estivation, et la primauté d'une
détermination douteuse sur la pré-validation globale. Les données de référence viennent
d'un sondage réel de l'instance, pas d'exemples inventés.

Côté GeoNature, les cas tournent d'abord autour du défaut d'api2GN : un libellé de
nomenclature doit être résolu **comme un libellé**, et un libellé que le référentiel local
ne connaît pas doit être *collecté* et affiché en fin d'import, jamais avalé. Le résolveur
factice y est volontairement strict — sa méthode `id()`, celle des trois autres
connecteurs, lève si on lui passe autre chose qu'un `cd_nomenclature`. Viennent ensuite
les trois pièges de pagination de l'API d'export : `offset` est un numéro de page et non
un décalage de lignes, la limite est rabotée par le serveur sans qu'il le dise, et un
`offset` ignoré boucle indéfiniment.

⚠ Les cas GeoNature sont bâtis sur un enregistrement **reconstitué depuis la définition
SQL de `gn_exports.v_synthese_sinp`**, faute d'accès à une instance distante au moment de
l'écriture. Les noms et les types de colonnes sont donc exacts, la distribution réelle des
valeurs ne l'est pas. À confronter à un sondage réel dès qu'une instance sera disponible —
c'est précisément la réserve que formule l'avertissement en fin de section.
