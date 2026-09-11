# Audit des nomenclatures SINP

11 septembre 2026 · `core/nomenclatures.py` et les quatre connecteurs.

Les correspondances vers les nomenclatures SINP sont la part du module la plus facile à
vérifier sur pièces, et la plus facile à croire correcte sans l'avoir vérifiée : une
valeur fausse ne lève rien, ne journalise rien, et remplit une colonne d'une valeur
plausible. Cet audit confronte le module à trois sources de vérité, et consigne ce qui
en est sorti — y compris ce qui a résisté.

## Méthode

| Source de vérité | Provenance |
|---|---|
| Référentiel SINP (50 types, valeurs, libellés, `active`) | `Nomenclature-api-module`, `migrations/data/nomenclatures_inpn_data.sql` |
| DEFAULT de chaque colonne `id_nomenclature_*` de la Synthèse | `GeoNature`, `migrations/data/core/synthese_default_values.sql` et le DDL de `gn_synthese.synthese` |
| Définition réelle de `gn_exports.v_synthese_sinp` | `gn_module_export`, `migrations/data/exports.sql` |

Deux vérifications :

1. **contrôle statique** — les 148 couples (mnémonique, `cd_nomenclature`) écrits en dur
   dans GBIF, VisioNature et dbChiro, confrontés au référentiel ;
2. **simulation d'insertion** — les `to_row` des quatre connecteurs exécutés sur les
   enregistrements d'exemple du dépôt avec un résolveur reproduisant le SQL réel
   (`get_id_nomenclature` **sans** filtre `active`, table des défauts réelle), le
   résultat étant rendu en libellés.

Le premier contrôle est devenu `tests/test_referentiel_sinp.py` : il ne servirait à rien
de l'avoir passé une fois.

## Ce qui a résisté

- **Les 148 correspondances visent des valeurs existantes et actives.** Les faux-amis
  documentés sont justes : `Nymph` → 6 (Larve) et non 13, `Pupa` → 13, `Seedling` → 18.
  `Hatchling` → 25 « Emergent » est exact lui aussi — la définition SINP dit « sortie de
  l'œuf », ce qui décrit bien un oisillon fraîchement éclos.
- **Les noms de colonnes attendus de `v_synthese_sinp` sont tous exacts**, alors que le
  connecteur a été écrit sans accès à une instance distante. Les 19 colonnes de
  nomenclature de la vue existent sous les noms employés.
- **La résolution par libellé est structurellement sûre** : dans aucun des 21 types
  employés, le libellé d'une valeur n'est égal au code d'une autre valeur du même type,
  et aucun libellé n'est dupliqué. Le repli code → libellé de `id_souple` ne peut donc
  pas produire de correspondance croisée.
- **Les défauts cités en commentaire sont les bons** : `STATUT_OBS` = `Pr` — le risque
  signalé sur les absences GBIF est réel —, `ETA_BIO` = `1`, `SEXE` = `6`,
  `METH_OBS` = `21`, `NAT_OBJ_GEO` = `NSP`, `STAT_BIOGEO` = `1`. `NIV_PRECIS` n'a aucun
  défaut, ce qui est la cause du premier constat ci-dessous.
- `STAT_BIOGEO` est bien absente de la vue, et la vue ne publie bien aucun statut de
  validation — seulement `validateur`, un nom de personne.

## Constats et suites

### 1. Un niveau de diffusion mal saisi devenait NULL, sans un mot — corrigé

`[visionature] niveau_diffusion_masquees` et `[dbchiro] niveau_diffusion` étaient passés
à `Resolver.id`, qui retombe sur le défaut du type en cas d'échec — et `NIV_PRECIS` n'en
a pas. Mesuré avant correction :

```
VisioNature, observation MASQUÉE, niveau_diffusion_masquees = « Aucune » → NULL
VisioNature, observation MASQUÉE, niveau_diffusion_masquees = « 44 »     → NULL
dbChiro,     niveau_diffusion = « Maille »                               → NULL
GeoNature,   niveau_diffusion = « Maile »                → ValueError, import refusé
```

« Aucune » est le libellé que l'interface de GeoNature affiche, et le connecteur
GeoNature l'accepte. La conséquence portait sur les données mêmes que le réglage protège
— observations masquées à la source, localisations de gîtes —, et elle était invisible.

C'est le défaut que `geonature.nomenclatures._exige` avait été écrit pour empêcher : la
règle n'avait simplement pas été portée aux deux autres connecteurs.

**Suite** : `core.nomenclatures.exiger_cd`, employée par `commands._niveau_diffusion`.
Les trois connecteurs acceptent désormais le code comme le libellé, et refusent tout le
reste **au démarrage**, avant le premier appel d'API — y compris GeoNature, qui échouait
correctement mais tardivement, après la première page moissonnée.

### 2. Deux comportements sur une valeur désactivée, et un commentaire faux — corrigé

`_charger_type` affirmait que son filtre `active` « reproduit ce que fait
`get_id_nomenclature` ». La fonction SQL ne filtre pas : définition unique dans
`nomenclatures.sql`, aucune redéfinition ultérieure. Il en résultait deux comportements
sur une valeur retirée du référentiel par l'instance — `id()` l'écrivait, `id_souple()`
la refusait.

**Suite** : `id()` résout désormais par le type déjà chargé, donc avec le même filtre.
Une requête par type au lieu d'une par valeur, au passage.

### 3. Aucun rapport de perte hors connecteur GeoNature — corrigé

Le connecteur GeoNature collectait les valeurs non résolues et les affichait en fin
d'import ; les trois autres retombaient sur le défaut en silence. Deux cas où cela
compte : une valeur désactivée par l'instance (constat 2), et les surcharges de
configuration `[visionature.atlas] comportement`, dont les `cd_nomenclature` viennent de
l'exploitant et n'étaient validés nulle part.

**Suite** : `Resolver.manques`, alimenté par `id()`, affiché en fin d'import par les
commandes GBIF, VisioNature et dbChiro.

### 4. dbChiro laissait `STATUT_SOURCE` à « Ne sait pas » — corrigé

VisioNature pose `Te` (Terrain) avec cet argument : c'est un outil de saisie de terrain,
l'origine ne fait pas de doute. dbChiroWeb l'est tout autant ; faute de constante, la
colonne prenait le défaut `NSP`, ce qui est faux et non pas prudent.

### 5. dbChiro importait un effectif sans dire ce qu'il dénombrait — corrigé pour moitié

`total_count` alimentait `count_min`/`count_max` sans que `OBJ_DENBR` ni `TYP_DENBR`
soient renseignés. `OBJ_DENBR = « Individu »` est désormais écrit.

**`TYP_DENBR` reste volontairement au défaut**, et c'est une divergence assumée avec GBIF
et VisioNature : un comptage de gîte est souvent une estimation — 200 individus en essaim
ne se comptent pas un à un — et l'API n'expose aucun équivalent de l'`estimation_code`
de VisioNature. Écrire « Compté » ferait passer une estimation pour un comptage, ce qui
fausse exactement les analyses que la colonne sert à qualifier.

### 6. Quatre nomenclatures de la vue n'atteignaient pas leur colonne — corrigé

Sur les 19 colonnes de nomenclature de `v_synthese_sinp`, quatre partaient en
`additional_data` faute d'être portées par l'INSERT commun. Ce n'était pas une simple
perte : la colonne, elle, prenait le défaut local.

| Colonne de la vue | Colonne Synthèse | Ce qui était écrit à la place |
|---|---|---|
| `type_info_geo` | `id_nomenclature_info_geo_type` | `1` Géoréférencement |
| `floutage_dee` | `id_nomenclature_blurring` | `NON` Non |
| `type_regroupement` | `id_nomenclature_grp_typ` | `NSP` |
| `methode_determination` | `id_nomenclature_determination_method` | `1` Non renseigné |

Les deux premières sont des **affirmations contraires à la source** : une observation que
le producteur rattache à une commune entrait en « Géoréférencée », une donnée qu'il
déclare floutée entrait en « Non floutée ». Le module refuse partout ailleurs d'inventer ;
ici il inventait par omission.

**Suite** : les quatre colonnes rejoignent `INSERT_SQL`. Les trois autres connecteurs
passent le défaut, ce qui leur coûte quatre lignes chacun — c'est ce coût, et la symétrie
qu'impose `tests/test_insert_alignement.py`, qui avait fait renoncer la première fois.
`methode_regroupement` reste en `additional_data` : ce n'est pas une nomenclature mais le
champ libre `synthese.grp_method`, que l'INSERT ne porte toujours pas.

### 7. `ETA_BIO = 1` était nommé « Non observé » — corrigé

Le type n'a que quatre valeurs : `0` NSP, `1` Non renseigné, `2` Observé vivant, `3`
Trouvé mort. « Non observé » appartient à `STATUT_OBS`. La valeur reste la bonne pour une
absence — aucun individu n'ayant été vu, son état biologique n'est pas renseignable —,
mais le nom laissait croire qu'`ETA_BIO` redisait l'absence que `STATUT_OBS` exprime
déjà. Constante renommée `ETA_BIO_ABSENCE`, table de `decisions.md` corrigée.

### 8. La fixture GeoNature employait des libellés impossibles — corrigé

`ITEM_GEONATURE` est reconstituée depuis le SQL de la vue, ce que son commentaire dit.
Quatre de ses libellés ne pouvaient pas sortir d'une instance :

```
ETA_BIO          « Vivant »       → « Observé vivant »
OCC_COMPORTEMENT « Alimentation » → « Chasse/alimentation »
TYP_GRP          « Session »      → « REL » (les libellés SINP de ce type sont les codes)
SENSIBILITE      « Non sensible » → « Non sensible - Diffusion précise »
```

Les tests passaient parce que le résolveur factice accepte n'importe quel libellé. Le
connecteur, lui, se comportait correctement — défaut appliqué, valeur collectée dans
`manques` —, mais la fixture surestimait ce qui était démontré.

**Suite** : libellés corrigés, et `tests/test_referentiel_sinp.py` vérifie désormais
chacun contre le référentiel versionné.

### 9. Points mineurs

- **`core.datasets.resoudre_nomenclature` ne filtrait pas `active`** : une valeur retirée
  du référentiel restait configurable pour les métadonnées, là où le `Resolver` la refuse
  partout ailleurs. Corrigé, en une requête au lieu de deux.
- **GBIF n'écrit `ETA_BIO` que sous `--garder-specimens`** : les deux seules entrées non
  nulles de la table sont `FOSSIL_SPECIMEN` et `LIVING_SPECIMEN`, exclues du périmètre par
  défaut. Cohérent, mais bon à savoir en lisant la table de couverture.
- **`PREUVE_EXIST` diverge d'une source à l'autre** : VisioNature affirme « Non » en
  l'absence de média, GBIF et dbChiro laissent « Inconnu ». Les deux positions se
  défendent — l'API Biolovision liste les médias de façon exhaustive, l'API de recherche
  GBIF non. Laissé tel quel, mais désormais écrit.
- **GBIF n'offre aucun réglage de diffusion** (`id_nomenclature_diffusion_level` câblée à
  `None`), là où les trois autres en ont un. La donnée GBIF est déjà publique ; le choix
  est maintenu.

## Couverture, après correctifs

● informé par la source · ○ toujours au défaut · — sans objet

| Colonne (mnémonique) | GBIF | VisioNature | dbChiro | GeoNature |
|---|:--:|:--:|:--:|:--:|
| `obs_technique` (METH_OBS) | ○ | ○ | ● | ● |
| `bio_condition` (ETA_BIO) | ○ sauf `--garder-specimens` | ● | ● | ● |
| `bio_status` (STATUT_BIO) | ● | ● | ● | ● |
| `naturalness` (NATURALITE) | ● | ○ | ○ | ● |
| `observation_status` (STATUT_OBS) | ● | ● | ● | ● |
| `source_status` (STATUT_SOURCE) | ● | ● | ● | ● |
| `life_stage` (STADE_VIE) | ● | ○ | ○ | ● |
| `sex` (SEXE) | ● | ○ | ○ | ● |
| `obj_count` (OBJ_DENBR) | ● | ● | ● | ● |
| `type_count` (TYP_DENBR) | ● | ● | ○ (assumé) | ● |
| `biogeo_status` (STAT_BIOGEO) | ● | ○ | ○ | ○ (absent de la vue) |
| `exist_proof` (PREUVE_EXIST) | ● | ● | ○ | ● |
| `valid_status` (STATUT_VALID) | config | config | config + `is_doubtful` | config |
| `behaviour` (OCC_COMPORTEMENT) | ○ | ● | ○ | ● |
| `geo_object_nature` (NAT_OBJ_GEO) | ○ | ● | ● | ● |
| `info_geo_type`, `blurring`, `grp_typ`, `determination_method` | ○ | ○ | ○ | ● |
| `diffusion_level` (NIV_PRECIS) | — | ● masquées | ● global | ● par observation |

Les `○` restants sont documentés dans le code, source par source : ils tiennent à ce que
la source n'exprime pas, ou à une correspondance qu'on refuse d'inventer.

## Ce qui reste à vérifier sur donnée réelle

Le connecteur GeoNature n'a toujours pas été confronté à une instance distante. La
fixture est maintenant conforme au référentiel, ce qui est une garantie de plus, mais
pas celle-là : rien ne dit encore comment se distribuent les valeurs réelles, ni ce
qu'une vue maison — que rien n'oblige à être `v_synthese_sinp` — peut livrer.
