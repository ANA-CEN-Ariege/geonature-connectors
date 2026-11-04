# GeoNature

*(↑ retour au [README](../../README.md#geonature))*

Moissonne une **autre instance GeoNature**. C'est la seule source qui parle déjà le même
langage que la destination : mêmes nomenclatures SINP, même TAXREF, mêmes identifiants
permanents. Cela rend le connecteur plus simple sur bien des points — et lui pose deux
problèmes que les autres n'ont pas.

## Configuration

Minimum vital, dans `connectors_config.toml` :

```toml
[geonature]
enabled = true
url = "https://geonature.exemple.fr"
id_export = 12
jeton = "…"
territoires = ["METROP"]
organisme_contact_principal = "Association des Naturalistes de l'Ariège"
```

Le fichier `connectors_config.toml.example` commente chaque réglage.

## Diagnostiquer avant d'importer

```bash
geonature connectors geonature-couverture
```

N'écrit rien, et montre cinq choses qui ne se découvriraient sinon qu'une fois les
données en base :

```
export 12 — licence « Licence Ouverte v2.0 »
  48 210 enregistrement(s) annoncé(s)
  ⚠ le serveur semble avoir ignoré les filtres ['geometry'] : total_filtered égale total
  colonne(s) absente(s) de la vue :
    id_perm_grp_sinp — identifiant de regroupement
  ⚠ TAXREF distant Taxref V17.0 / local Taxref V16.0
  cd_nom : 1 284 distinct(s), 1 279 résolu(s) (99,6 %), 3 par cd_ref, 2 hors TAXREF
      cd_nom 452301 (cd_ref 60295) Rhinolophus ferrumequinum — 84 observation(s)
  id_perm_sinp : 48 210 / 48 210 renseigné(s)
  ⚠ 312 UUID déjà en Synthèse sous une autre source : GBIF (312)
  jeux de données : 7 distinct(s), 2 déjà présent(s) localement
      4d331cae… « Inventaire ZNIEFF de l'Ariège » — actif
  cadres d'acquisition : 3 distinct(s), 0 présent(s) localement
  ⚠ 2 libellé(s) de nomenclature non résolu(s) :
      STATUT_BIO « Reproducteur probable » — 412 observation(s)
```

Ne lancez l'import qu'une fois chaque ligne comprise.

« N'écrit rien » vaut aussi pour les verrous : la commande peut tourner pendant qu'un
import est en cours, et l'inverse. Ce n'était pas le cas avant le 14 septembre 2026 —
l'enregistrement de `url_source` gardait un verrou de ligne sur `t_sources` pendant toute
la moisson, et une seconde commande restait figée au démarrage, sans message, aussi
longtemps que durait la première.

## Importer

```bash
geonature connectors geonature-import --dry-run
geonature connectors geonature-import
geonature connectors geonature-import --perimetre 09 --jeu 4d331cae-65e4-4948-b0b2-a11bc5bb46c2
```

| | |
|---|---|
| `--export` | identifiant d'export distant (défaut : configuration) |
| `--jeu` | restreindre à un ou plusieurs `jdd_uuid` distants, répétable |
| `--depuis` | date ISO 8601 ; sans elle, le filigrane du dernier passage est calculé |
| `--tout` | relire tout le corpus, sans filtre de date — requis avant `geonature-reconcilier` |
| `--perimetre` | code d'un zonage local (`09`) ou WKT en 4326 |
| `--max-resultats` | plafonner la moisson, pour un premier essai d'écriture |

**L'incrémental fait deux passes de date, et ce n'est pas une précaution excessive.**
`date_modification` est le `meta_update_date` de la Synthèse distante, **NULL tant que la
ligne n'a jamais été modifiée**. Filtrer sur ce seul champ manquerait toutes les
*créations* — et définitivement, puisque le passage suivant remonte encore le filigrane
sans jamais revenir les chercher. Le connecteur interroge donc aussi `date_creation` et
fusionne les deux sur `id_synthese`. Un filigrane reculé de `marge_heures` (24 par
défaut) couvre l'écart d'horloge entre les deux instances.

## Répercuter les suppressions

L'API d'export ne publie aucun journal de suppression : une observation retirée là-bas
cesse simplement d'apparaître. On relit donc tout, et ce qui n'est pas revenu a disparu.

```bash
geonature connectors geonature-import --tout
geonature connectors geonature-reconcilier          # simule
geonature connectors geonature-reconcilier --yes    # exécute
```

Commande **séparée de l'import**, et qui simule par défaut : un import écrit, et y loger
une suppression de masse violerait l'asymétrie sur laquelle repose tout le module.

Quatre garde-fous, dont aucun n'est de trop — sans eux, un filtre mal réglé vide la
Synthèse :

1. **la moisson doit être complète.** Un plafond de résultats, un filtre de date ou une
   pagination interrompue rendraient absentes des lignes bien vivantes ;
2. **bornée aux jeux réellement relus.** Un jeu hors du périmètre courant n'est pas vidé
   sous prétexte qu'on ne l'a pas lu ;
3. **bornée au couple instance/export**, par `additional_data`. Deux exports alimentant la
   même source ne peuvent pas se supprimer l'un l'autre ;
4. **un plafond** (`plafond_suppressions`, 5 % du corpus, minimum 100 lignes). Un
   producteur qui republierait sous de nouveaux identifiants ferait sinon tout disparaître
   d'un coup.

## Médias

Les photos, sons, vidéos et PDF saisis dans Occtax (ou dans un autre module) ne sont
**pas** dans la Synthèse. Ils sont rangés dans `gn_commons.t_medias` et rattachés par
`uuid_attached_row` à l'UUID de l'objet saisi, qui est aussi l'`unique_id_sinp` de la
ligne de Synthèse. La Synthèse les affiche au travers de cette relation, mais rien ne les
recopie dans `digital_proof`. La vue d'export standard (`v_synthese_sinp`) n'en publie
donc **aucun**. Le champ texte « preuve numérique » d'Occtax, lui, est bien repris dans
`digital_proof`.

Pour récupérer les médias, l'administrateur distant doit publier un export qui ajoute une
colonne `medias`. Par exemple, une vue bâtie sur la vue SINP :

```sql
CREATE VIEW gn_exports.v_synthese_sinp_medias AS
SELECT v.*, m.medias
FROM gn_exports.v_synthese_sinp v
JOIN gn_synthese.synthese s ON s.id_synthese = v.id_synthese
LEFT JOIN LATERAL (
    SELECT json_agg(json_build_object(
               'uuid',        tm.unique_id_media,
               'type',        n.cd_nomenclature,
               'url',         tm.media_url,
               'chemin',      tm.media_path,
               'titre',       tm.title_fr,
               'auteur',      tm.author,
               'description', tm.description_fr,
               'public',      tm.is_public)
           ORDER BY tm.id_media) AS medias
    FROM gn_commons.t_medias tm
    JOIN ref_nomenclatures.t_nomenclatures n
      ON n.id_nomenclature = tm.id_nomenclature_media_type
    WHERE tm.uuid_attached_row = s.unique_id_sinp
      AND tm.is_public
) m ON true;
```

L'export est ensuite déclaré sur cette vue dans le module d'export du distant, et son
`id_export` reporté dans `[geonature] id_export`.

Côté connecteur :

- **seules les URL sont reprises**, dans `t_medias.media_url`. Aucun fichier n'est
  copié : le média reste hébergé chez le producteur, et cesse de s'afficher ici s'il le
  retire ou le déplace ;
- un fichier téléversé (`chemin`) donne l'URL `<url>/api/media/attachments/<chemin>`.
  Si le distant a changé `MEDIA_URL`, renseignez `[geonature] url_medias` ;
- un média que le producteur a marqué **non public** n'est pas relayé, même si sa vue le
  publie ;
- le type vient du `cd_nomenclature` (ou du libellé) `TYPE_MEDIA` publié. À défaut, il
  est déduit de l'URL (extension, YouTube, Vimeo, Dailymotion) ;
- les médias sont rattachés **directement à la ligne de Synthèse**, sous l'emplacement
  `gn_synthese.synthese` de `gn_commons.bib_tables_location`. La migration `c9d3e5f7a1b2`
  le déclare. Ils apparaissent dans la fiche d'observation et répondent au filtre « avec
  médias » de la Synthèse ;
- chaque import **synchronise** : un média ajouté, modifié ou retiré à la source l'est
  ici. Un média inchangé n'est jamais réécrit. Une observation absente de la moisson ne
  perd rien, et un export **sans** colonne `medias` ne retire rien ;
- `geonature-reconcilier`, `geonature-purge` et le remplacement d'une ligne concurrente
  retirent les médias des lignes qu'ils suppriment.

⚠ **Incrémental.** Ajouter un média à une observation existante ne modifie pas
forcément sa ligne de Synthèse, donc pas sa `date_modification`. Un import incrémental
peut alors ne pas le voir. `geonature-import --tout` le rattrape.

`geonature-couverture` indique combien de médias l'export porte, et lesquels seront
écartés.

## Limites connues

**Les géométries non ponctuelles sont ramenées à leur centroïde.** `core/synthese.py`
n'insère que des points (`ST_MakePoint`). Une placette, une maille ou un polygone de
prospection perd donc sa forme. `nature_objet_geo` et `type_info_geo` du producteur sont
repris en colonne, pour que la fiche dise au moins de quoi ce point est le centre, mais
l'information géométrique, elle, est perdue.

**`determiner` et `validator` ne vont pas en colonne.** Elles existent en Synthèse mais
pas dans `INSERT_SQL`, et les y ajouter obligerait les trois autres `to_row` à fournir le
paramètre lié — `tests/test_insert_alignement.py` l'impose dans les deux sens. Elles
partent en `additional_data` sous `gn_determinateur` et `gn_validateur`. Les ajouter à
l'INSERT commun est un suivi identifié.

**Deux nomenclatures ne sont pas transposables** faute de figurer dans la vue :
`id_nomenclature_biogeo_status` (`STAT_BIOGEO`, absente de `v_synthese_sinp`) et
`id_nomenclature_valid_status` (la vue publie `validateur`, un nom de personne, pas un
statut — il vient donc de `[validation]`). Les deux prennent le défaut de leur colonne.

**Les dix-sept autres sont reprises en colonne**, y compris les quatre qui partaient
naguère en `additional_data` : `type_info_geo`, `floutage_dee`, `type_regroupement` et
`methode_determination`. Les omettre ne les laissait pas vides — la colonne prenait le
défaut de l'instance, et deux de ces défauts **contredisent** la source : une observation
que le producteur rattache à une commune entrait en « Géoréférencement », une donnée
qu'il déclare floutée entrait en « Non floutée ». Les trois autres connecteurs n'ont rien
à en dire et passent le défaut.

**`[geonature.schedule]` n'est pas câblé**, comme `[visionature.schedule]` et
`[dbchiro.schedule]` : `tasks.py` n'ordonnance que GBIF. Planifier l'import passe par
cron.
