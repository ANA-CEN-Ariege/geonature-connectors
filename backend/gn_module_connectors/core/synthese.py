"""Écriture par lots dans gn_synthese.synthese.

Générique : ne connaît aucune source externe, seulement des lignes normalisées.

Deux choix structurants, tirés du comportement réel de la Synthèse :

- **Insertion par lots, en un seul statement.** Deux triggers de `synthese` sont
  déclarés `FOR EACH STATEMENT` (`tri_insert_cor_area_synthese`, qui croise chaque
  géométrie avec tout `ref_geo.l_areas`, et `tri_insert_calculate_sensitivity`). Insérer
  ligne à ligne les déclenche une fois par ligne : le coût n'est jamais amorti. Par lots
  de 1000, il l'est.
- **`ON CONFLICT (unique_id_sinp) DO UPDATE`, conditionné à la date de modification.**
  L'UUID étant déterministe, un moissonnage rejoué ne crée pas de doublon — c'est le
  défaut qui rend le parser GBIF d'api2GN inutilisable en récurrent. Mais ne rien faire
  du tout serait insuffisant : une occurrence corrigée à la source (coordonnées
  rectifiées, taxon réidentifié) resterait périmée en base indéfiniment. La mise à jour
  n'est déclenchée que si le contenu a changé, ce qui évite de réécrire tout le
  corpus à chaque passage.

⚠ `the_geom_local` doit être renseignée à l'insertion : le trigger de rattachement aux
zonages (`fct_trig_insert_in_cor_area_synthese_on_each_statement`) croise sur cette
colonne. Une ligne insérée sans elle n'est rattachée à aucune commune, maille ou ZNIEFF,
et rien ne le signale.
"""

from sqlalchemy import text
from geonature.utils.env import db

# Les colonnes de nomenclature sont listées et renseignées explicitement : l'INSERT
# étant un statement unique réutilisé pour tout le lot, on ne peut pas en omettre une
# pour une seule ligne. Le résolveur (core/nomenclatures.py) substitue le DEFAULT de la
# colonne quand la source n'apporte rien — le résultat est identique.
#
# `id_nomenclature_sensitivity` reste volontairement absente : le trigger
# `tri_insert_calculate_sensitivity` la calcule après l'insertion, et l'écrire ici
# serait de toute façon écrasé. C'est le défaut du traitement de `gn_vn2synthese`, qui
# y écrit une valeur aussitôt perdue.
#
# Quatre colonnes s'y sont ajoutées — `info_geo_type` (TYP_INF_GEO), `blurring`
# (DEE_FLOU), `grp_typ` (TYP_GRP) et `determination_method` (METH_DETERMIN). Une vue
# `v_synthese_sinp` les publie toutes, et les omettre ne les laissait pas vides : elles
# prenaient le DEFAULT de la Synthèse. Une observation que le producteur rattache à une
# commune (« Rattachement ») entrait donc en « Géoréférencement », et une donnée qu'il
# déclare floutée entrait en « Non floutée » — deux affirmations contraires à la source,
# et non de simples absences. Les trois autres connecteurs n'ont rien à en dire et
# passent le défaut, ce que `tests/test_insert_alignement.py` impose de toute façon.
#
# `id_nomenclature_diffusion_level`, en revanche, est laissée au producteur : GeoNature
# a retiré son DEFAULT et cessé de la calculer (migration « Do not auto-compute
# diffusion_level »). NULL y signifie « le producteur ne se prononce pas », et c'est une
# valeur légitime — on ne la renseigne que lorsque la source exprime une restriction.
INSERT_SQL = text(
    """
    INSERT INTO gn_synthese.synthese (
        unique_id_sinp, unique_id_sinp_grp, id_source, id_module, id_dataset,
        entity_source_pk_value, cd_nom, nom_cite, meta_v_taxref,
        date_min, date_max, count_min, count_max,
        observers, "precision", altitude_min, altitude_max,
        digital_proof, additional_data,
        id_nomenclature_obs_technique, id_nomenclature_bio_condition,
        id_nomenclature_bio_status, id_nomenclature_naturalness,
        id_nomenclature_observation_status, id_nomenclature_source_status,
        id_nomenclature_life_stage, id_nomenclature_sex,
        id_nomenclature_obj_count, id_nomenclature_type_count,
        id_nomenclature_biogeo_status, id_nomenclature_exist_proof,
        id_nomenclature_valid_status, id_nomenclature_behaviour,
        id_nomenclature_diffusion_level, id_nomenclature_geo_object_nature,
        id_nomenclature_info_geo_type, id_nomenclature_blurring,
        id_nomenclature_grp_typ, id_nomenclature_determination_method,
        comment_description,
        the_geom_4326, the_geom_point, the_geom_local,
        last_action
    )
    -- Un SELECT depuis UNNEST plutôt qu'un unique VALUES rejoué par `executemany` :
    -- même technique que `realigner_uuid` et `PREVALIDATION_SQL` plus bas, pour la même
    -- raison — obtenir un vrai statement SQL physique unique par lot. Chaque paramètre
    -- lié ci-dessous est désormais un tableau (une valeur par ligne du lot), et non plus
    -- un scalaire répété une fois par ligne : c'est ce qui manquait pour que les deux
    -- triggers `FOR EACH STATEMENT` de l'en-tête ne se déclenchent qu'une fois par lot.
    SELECT
        l.unique_id_sinp, l.unique_id_sinp_grp, l.id_source, l.id_module, l.id_dataset,
        l.entity_source_pk_value, l.cd_nom, l.nom_cite, l.meta_v_taxref,
        l.date_min, l.date_max, l.count_min, l.count_max,
        l.observers, l."precision", l.altitude_min, l.altitude_max,
        l.digital_proof, l.additional_data,
        l.id_nomenclature_obs_technique, l.id_nomenclature_bio_condition,
        l.id_nomenclature_bio_status, l.id_nomenclature_naturalness,
        l.id_nomenclature_observation_status, l.id_nomenclature_source_status,
        l.id_nomenclature_life_stage, l.id_nomenclature_sex,
        l.id_nomenclature_obj_count, l.id_nomenclature_type_count,
        l.id_nomenclature_biogeo_status, l.id_nomenclature_exist_proof,
        l.id_nomenclature_valid_status, l.id_nomenclature_behaviour,
        l.id_nomenclature_diffusion_level, l.id_nomenclature_geo_object_nature,
        l.id_nomenclature_info_geo_type, l.id_nomenclature_blurring,
        l.id_nomenclature_grp_typ, l.id_nomenclature_determination_method,
        l.comment_description,
        ST_SetSRID(ST_MakePoint(l.lon, l.lat), 4326),
        ST_SetSRID(ST_MakePoint(l.lon, l.lat), 4326),
        ST_Transform(ST_SetSRID(ST_MakePoint(l.lon, l.lat), 4326), l.local_srid),
        'I'
    FROM UNNEST(
        CAST(:unique_id_sinp AS uuid[]), CAST(:unique_id_sinp_grp AS uuid[]),
        CAST(:id_source AS integer[]), CAST(:id_module AS integer[]),
        CAST(:id_dataset AS integer[]), CAST(:entity_source_pk_value AS text[]),
        CAST(:cd_nom AS integer[]), CAST(:nom_cite AS text[]),
        CAST(:meta_v_taxref AS text[]),
        CAST(:date_min AS timestamp without time zone[]),
        CAST(:date_max AS timestamp without time zone[]),
        CAST(:count_min AS integer[]), CAST(:count_max AS integer[]),
        CAST(:observers AS text[]), CAST(:precision AS integer[]),
        CAST(:altitude_min AS integer[]), CAST(:altitude_max AS integer[]),
        CAST(:digital_proof AS text[]), CAST(:additional_data AS jsonb[]),
        CAST(:id_nomenclature_obs_technique AS integer[]),
        CAST(:id_nomenclature_bio_condition AS integer[]),
        CAST(:id_nomenclature_bio_status AS integer[]),
        CAST(:id_nomenclature_naturalness AS integer[]),
        CAST(:id_nomenclature_observation_status AS integer[]),
        CAST(:id_nomenclature_source_status AS integer[]),
        CAST(:id_nomenclature_life_stage AS integer[]),
        CAST(:id_nomenclature_sex AS integer[]),
        CAST(:id_nomenclature_obj_count AS integer[]),
        CAST(:id_nomenclature_type_count AS integer[]),
        CAST(:id_nomenclature_biogeo_status AS integer[]),
        CAST(:id_nomenclature_exist_proof AS integer[]),
        CAST(:id_nomenclature_valid_status AS integer[]),
        CAST(:id_nomenclature_behaviour AS integer[]),
        CAST(:id_nomenclature_diffusion_level AS integer[]),
        CAST(:id_nomenclature_geo_object_nature AS integer[]),
        CAST(:id_nomenclature_info_geo_type AS integer[]),
        CAST(:id_nomenclature_blurring AS integer[]),
        CAST(:id_nomenclature_grp_typ AS integer[]),
        CAST(:id_nomenclature_determination_method AS integer[]),
        CAST(:comment_description AS text[]),
        CAST(:lon AS double precision[]), CAST(:lat AS double precision[]),
        CAST(:local_srid AS integer[])
    ) AS l(
        unique_id_sinp, unique_id_sinp_grp, id_source, id_module, id_dataset,
        entity_source_pk_value, cd_nom, nom_cite, meta_v_taxref,
        date_min, date_max, count_min, count_max,
        observers, "precision", altitude_min, altitude_max,
        digital_proof, additional_data,
        id_nomenclature_obs_technique, id_nomenclature_bio_condition,
        id_nomenclature_bio_status, id_nomenclature_naturalness,
        id_nomenclature_observation_status, id_nomenclature_source_status,
        id_nomenclature_life_stage, id_nomenclature_sex,
        id_nomenclature_obj_count, id_nomenclature_type_count,
        id_nomenclature_biogeo_status, id_nomenclature_exist_proof,
        id_nomenclature_valid_status, id_nomenclature_behaviour,
        id_nomenclature_diffusion_level, id_nomenclature_geo_object_nature,
        id_nomenclature_info_geo_type, id_nomenclature_blurring,
        id_nomenclature_grp_typ, id_nomenclature_determination_method,
        comment_description, lon, lat, local_srid
    )
    ON CONFLICT (unique_id_sinp) DO UPDATE SET
        unique_id_sinp_grp = EXCLUDED.unique_id_sinp_grp,
        cd_nom = EXCLUDED.cd_nom,
        nom_cite = EXCLUDED.nom_cite,
        meta_v_taxref = EXCLUDED.meta_v_taxref,
        date_min = EXCLUDED.date_min,
        date_max = EXCLUDED.date_max,
        count_min = EXCLUDED.count_min,
        count_max = EXCLUDED.count_max,
        observers = EXCLUDED.observers,
        "precision" = EXCLUDED."precision",
        altitude_min = EXCLUDED.altitude_min,
        altitude_max = EXCLUDED.altitude_max,
        digital_proof = EXCLUDED.digital_proof,
        additional_data = EXCLUDED.additional_data,
        id_nomenclature_obs_technique = EXCLUDED.id_nomenclature_obs_technique,
        id_nomenclature_bio_condition = EXCLUDED.id_nomenclature_bio_condition,
        id_nomenclature_bio_status = EXCLUDED.id_nomenclature_bio_status,
        id_nomenclature_naturalness = EXCLUDED.id_nomenclature_naturalness,
        id_nomenclature_observation_status = EXCLUDED.id_nomenclature_observation_status,
        id_nomenclature_source_status = EXCLUDED.id_nomenclature_source_status,
        id_nomenclature_life_stage = EXCLUDED.id_nomenclature_life_stage,
        id_nomenclature_sex = EXCLUDED.id_nomenclature_sex,
        id_nomenclature_obj_count = EXCLUDED.id_nomenclature_obj_count,
        id_nomenclature_type_count = EXCLUDED.id_nomenclature_type_count,
        id_nomenclature_biogeo_status = EXCLUDED.id_nomenclature_biogeo_status,
        id_nomenclature_exist_proof = EXCLUDED.id_nomenclature_exist_proof,
        -- ⚠ `id_nomenclature_valid_status` est délibérément ABSENTE de cette clause.
        -- Elle est écrite à la création, jamais réécrite ensuite : une pré-validation est
        -- un réglage de masse, la décision d'un validateur est un jugement porté sur cette
        -- observation-là. Remettre le réglage par-dessus le jugement est toujours le
        -- mauvais sens, et cela contredisait `prevalider`, qui s'interdit déjà de réécrire
        -- `gn_commons.t_validations` pour cette raison exacte — l'historique gardait donc
        -- l'avis du validateur pendant que la Synthèse affichait le statut automatique.
        -- Une observation corrigée à la source revient au validateur par le filtre
        -- « modifiée depuis sa validation » du module Validation, qui compare
        -- `meta_update_date` à `validation_date`. C'est ce mécanisme-là qui doit jouer.
        id_nomenclature_behaviour = EXCLUDED.id_nomenclature_behaviour,
        id_nomenclature_diffusion_level = EXCLUDED.id_nomenclature_diffusion_level,
        id_nomenclature_geo_object_nature = EXCLUDED.id_nomenclature_geo_object_nature,
        id_nomenclature_info_geo_type = EXCLUDED.id_nomenclature_info_geo_type,
        id_nomenclature_blurring = EXCLUDED.id_nomenclature_blurring,
        id_nomenclature_grp_typ = EXCLUDED.id_nomenclature_grp_typ,
        id_nomenclature_determination_method = EXCLUDED.id_nomenclature_determination_method,
        comment_description = EXCLUDED.comment_description,
        the_geom_4326 = EXCLUDED.the_geom_4326,
        the_geom_point = EXCLUDED.the_geom_point,
        the_geom_local = EXCLUDED.the_geom_local,
        last_action = 'U'
    -- Ne réécrire que si le producteur a effectivement modifié la donnée. Sans cette
    -- clause, chaque exécution réécrirait tout le corpus : coût en WAL, tuples morts,
    -- et recalcul inutile des rattachements aux zonages par les triggers.
    -- L'empreinte est le critère principal : `gbif_modified` est absente de TOUS les
    -- jeux publiés par l'INPN (mesuré : 0 % sur SICEN, Faune Occitanie, INPN flore CBN,
    -- contre 100 % sur iNaturalist), soit 76 % du corpus ariégeois. La date reste un
    -- critère complémentaire pour les producteurs qui la renseignent.
    -- La clé d'empreinte est propre à chaque connecteur (`gbif_empreinte`,
    -- `vn_empreinte`) : un COALESCE permet de partager ce statement entre sources sans
    -- renommer la clé des lignes GBIF déjà en base — ce qui provoquerait la réécriture
    -- inutile de tout le corpus au prochain passage.
    -- Une clé nouvelle s'ajoute toujours **en queue** du COALESCE : celui-ci rend la
    -- première valeur non nulle, donc une ligne déjà en base, qui porte forcément la clé
    -- de sa propre source, n'est pas affectée. Renommer ou réordonner, en revanche,
    -- provoquerait la réécriture inutile de tout le corpus au prochain passage.
    WHERE COALESCE(gn_synthese.synthese.additional_data->>'gbif_empreinte',
                   gn_synthese.synthese.additional_data->>'vn_empreinte',
                   gn_synthese.synthese.additional_data->>'dbchiro_empreinte',
                   gn_synthese.synthese.additional_data->>'gn_empreinte')
          IS DISTINCT FROM COALESCE(EXCLUDED.additional_data->>'gbif_empreinte',
                                    EXCLUDED.additional_data->>'vn_empreinte',
                                    EXCLUDED.additional_data->>'dbchiro_empreinte',
                                    EXCLUDED.additional_data->>'gn_empreinte')
       OR gn_synthese.synthese.additional_data->>'gbif_modified'
          IS DISTINCT FROM EXCLUDED.additional_data->>'gbif_modified'
    RETURNING unique_id_sinp
    """
)


def local_srid() -> int:
    """SRID local de l'instance, tel que le déduit aussi le module Import."""
    return db.session.execute(
        text("SELECT Find_SRID('ref_geo', 'l_areas', 'geom')")
    ).scalar()


def version_taxref() -> str | None:
    """Version de TAXREF de l'instance, pour `synthese.meta_v_taxref`.

    Même source que `gn_vn2synthese` (08:334-336) : le paramètre `taxref_version` de
    `gn_commons.t_parameters`, que GeoNature renseigne à l'installation du référentiel.
    Sans cette colonne, un `cd_nom` devenu obsolète après une montée de version de
    TAXREF n'est plus interprétable — on ne sait plus dans quel référentiel le lire.
    """
    return db.session.execute(
        text("SELECT gn_commons.get_default_parameter('taxref_version', NULL::integer)")
    ).scalar()


def realigner_uuid(lignes: list[dict], id_source: int, cle: str) -> int:
    """Renomme les lignes déjà en base qui portent un UUID désormais supplanté.

    Raison d'être : le module a longtemps calculé lui-même l'`unique_id_sinp` (uuid5).
    Il retient maintenant l'UUID que le producteur publie, quand il existe. Sans
    précaution, le moissonnage suivant réinsérerait les mêmes observations sous leur
    nouvel identifiant : chaque ligne existante deviendrait un doublon, invisible
    puisque les deux copies auraient un UUID différent et la même source.

    On renomme donc l'ancienne ligne avant l'insertion. C'est la seule opération qui
    préserve tout ce que la Synthèse a accroché à `id_synthese` : validations,
    rattachements aux zonages, signalements, exports déjà cités.

    Trois garde-fous :

    - le renommage est borné à `id_source`, il ne peut pas toucher une ligne d'une
      autre origine ;
    - il n'écrase jamais une ligne qui porterait déjà l'UUID cible (`NOT EXISTS`) ;
    - il est idempotent : une fois renommée, la ligne n'a plus l'ancien UUID, et le
      statement suivant ne trouve plus rien.

    Le renommage ne déclenche aucun recalcul coûteux : les triggers `UPDATE OF` de
    GeoNature ne surveillent que `the_geom_local`, `the_geom_4326`, `date_min`,
    `date_max`, `cd_nom` et `id_nomenclature_bio_status` — aucun n'est touché ici.

    Retourne le nombre de lignes effectivement renommées, pour que le bilan d'import
    le dise plutôt que de le faire en silence.

    `cle` désigne l'entrée d'`additional_data` où le connecteur consigne l'UUID qu'il
    avait calculé avant de disposer de celui du producteur. Elle est propre à chaque
    source (`vn_uuid_calcule`, `gn_uuid_calcule`…) : la mutualiser sous un nom unique
    ferait qu'un connecteur renommerait sur la foi d'une clé écrite par un autre.
    """
    import json as _json

    anciens, cibles = [], []
    for ligne in lignes:
        try:
            provenance = _json.loads(ligne.get("additional_data") or "{}")
        except ValueError:
            continue
        ancien = provenance.get(cle)
        if ancien and ancien != ligne["unique_id_sinp"]:
            anciens.append(ancien)
            cibles.append(str(ligne["unique_id_sinp"]))
    if not anciens:
        return 0

    return db.session.execute(
        text("""
            UPDATE gn_synthese.synthese AS s
            SET unique_id_sinp = c.cible
            FROM UNNEST(CAST(:anciens AS uuid[]),
                        CAST(:cibles AS uuid[])) AS c(ancien, cible)
            WHERE s.unique_id_sinp = c.ancien
              AND s.id_source = :id_source
              AND NOT EXISTS (SELECT 1 FROM gn_synthese.synthese AS t
                              WHERE t.unique_id_sinp = c.cible)
        """),
        {"anciens": anciens, "cibles": cibles, "id_source": id_source},
    ).rowcount


def get_source_id(name_source: str) -> int:
    id_source = db.session.execute(
        text("SELECT id_source FROM gn_synthese.t_sources WHERE name_source = :n"),
        {"n": name_source},
    ).scalar()
    if id_source is None:
        raise RuntimeError(
            f"Source « {name_source} » absente de gn_synthese.t_sources — "
            f"la migration du module a-t-elle été jouée ? (geonature upgrade-modules-db)"
        )
    return id_source


def get_module_id(module_code: str) -> int:
    id_module = db.session.execute(
        text("SELECT id_module FROM gn_commons.t_modules WHERE module_code = :c"),
        {"c": module_code},
    ).scalar()
    if id_module is None:
        raise RuntimeError(
            f"Module « {module_code} » absent de gn_commons.t_modules — "
            f"la migration du module a-t-elle été jouée ? (geonature upgrade-modules-db)"
        )
    return id_module


# Clés d'empreinte connues, dans l'ordre de priorité du COALESCE de l'INSERT.
#
# ⚠ Cet ordre **doit** rester celui du SQL. Un désaccord ne casse aucune insertion : il
# fausse seulement le bilan, qui annonce des mises à jour que la base n'a pas faites — le
# genre de défaut qui vit longtemps sous un test vert. `test_insert_alignement` compare
# les deux ordres pour cette raison.
CLES_EMPREINTE = ("gbif_empreinte", "vn_empreinte", "dbchiro_empreinte", "gn_empreinte")


def empreinte_de(additional_data: dict) -> str | None:
    """Empreinte d'une ligne, quelle que soit la source qui l'a produite."""
    for cle in CLES_EMPREINTE:
        valeur = additional_data.get(cle)
        if valeur:
            return valeur
    return None


def compter_existants(lignes: list[dict]) -> int:
    """Combien de ces lignes sont déjà en Synthèse, par leur `unique_id_sinp`.

    Sert au `--dry-run`, qui sans cela annonce comme « à écrire » des observations déjà
    présentes. Un moissonnage complet recoupe presque toujours un incrémental antérieur :
    confondre les deux fait croire à un doublon là où l'`ON CONFLICT` ne fera rien.
    """
    if not lignes:
        return 0
    return db.session.execute(
        text("""SELECT count(*) FROM gn_synthese.synthese
                WHERE unique_id_sinp = ANY(CAST(:u AS uuid[]))"""),
        {"u": [l["unique_id_sinp"] for l in lignes]},
    ).scalar() or 0


def conflits_autre_source(lignes: list[dict], id_source: int) -> dict[str, tuple[int, str]]:
    """UUID du lot déjà présents en base sous une **autre** source.

    Raison d'être : un connecteur qui reprend l'`unique_id_sinp` publié par le producteur
    — plutôt que d'en dériver un — peut tomber sur une observation déjà importée par un
    autre chemin. Le cas est réel : le GBIF republie les données GeoNature françaises en
    plaçant leur UUID SINP dans `occurrenceID`, et `sources/gbif/transform.sinp_uuid` le
    reprend tel quel quand c'en est un.

    L'`ON CONFLICT` ne saurait pas arbitrer. Il ne réécrit ni `id_source`, ni `id_dataset`,
    ni `entity_source_pk_value`, mais il écrase tout le reste, `additional_data` compris.
    On obtiendrait une ligne chimère — provenance GBIF, contenu GeoNature — qui de surcroît
    **oscille** : le COALESCE d'empreinte comparant alors une clé à une autre, la condition
    de mise à jour est vraie à chaque passage et les deux connecteurs se réécrivent
    indéfiniment.

    Une seule requête par lot, sur le même tableau d'UUID que `compter_existants` : le coût
    est négligeable. La **décision** de ce qu'on en fait appartient à la commande, pas à
    cette fonction ni à `insert_batch` — dont le comportement doit rester exactement celui
    que les trois connecteurs plus anciens connaissent.
    """
    if not lignes:
        return {}
    return {
        str(u): (src, nom)
        for u, src, nom in db.session.execute(
            text("""SELECT s.unique_id_sinp::text, s.id_source,
                           COALESCE(t.name_source, '?')
                    FROM gn_synthese.synthese s
                    LEFT JOIN gn_synthese.t_sources t ON t.id_source = s.id_source
                    WHERE s.unique_id_sinp = ANY(CAST(:u AS uuid[]))
                      AND s.id_source IS DISTINCT FROM :src"""),
            {"u": [l["unique_id_sinp"] for l in lignes], "src": id_source},
        ).all()
    }


def lignes_ecrasees(id_source: int, cle_empreinte: str) -> tuple[int, int]:
    """(lignes écrasées par un autre connecteur, total de la source).

    Une ligne portant notre `id_source` mais **dépourvue de notre clé d'empreinte** a
    forcément vu son `additional_data` remplacé par celui d'un autre connecteur : nos
    `to_row` l'écrivent systématiquement.

    C'est la seule façon de voir un conflit devenu invisible. `INSERT_SQL` ne réécrit
    jamais `id_source` — la colonne reste au premier connecteur qui a inséré la ligne —
    mais il écrase tout le contenu, `additional_data` compris. Deux connecteurs qui
    moissonnent la même observation se réécrivent donc l'un l'autre à chaque exécution,
    et `conflits_autre_source` n'y voit plus rien puisque l'`id_source` ne les distingue
    plus. Seule la clé d'empreinte trahit encore qui a écrit en dernier.

    ⚠ Sur une ligne insérée avant l'introduction des empreintes, l'absence de clé ne
    prouve rien. Le décompte est donc un signal à interpréter, pas un verdict — et c'est
    pourquoi il est rapporté, jamais utilisé pour supprimer quoi que ce soit.
    """
    ligne = db.session.execute(
        text("""SELECT count(*) FILTER (WHERE additional_data->>:cle IS NULL),
                       count(*)
                FROM gn_synthese.synthese WHERE id_source = :s"""),
        {"s": id_source, "cle": cle_empreinte},
    ).one()
    return (ligne[0] or 0, ligne[1] or 0)


PREVALIDATION_SQL = text("""
    INSERT INTO gn_commons.t_validations
        (uuid_attached_row, id_nomenclature_valid_status, validation_auto,
         validation_comment, validation_date)
    SELECT DISTINCT u, :statut, TRUE, :commentaire, NOW()
    FROM unnest(CAST(:uuids AS uuid[])) AS u
    WHERE EXISTS (SELECT 1 FROM gn_synthese.synthese s WHERE s.unique_id_sinp = u)
      AND NOT EXISTS (SELECT 1 FROM gn_commons.t_validations v
                      WHERE v.uuid_attached_row = u)
""")


def prevalider(lignes: list[dict], statut) -> int:
    """Écrit l'historique de pré-validation des observations d'un lot. Retourne le nombre.

    Une seule fois par observation, jamais réécrite. Trois raisons :

    - un validateur qui a tranché a le dernier mot. Le module Validation retient la
      validation la plus récente ; réécrire la nôtre à chaque moissonnage annulerait sa
      décision sans trace ;
    - le filtre « modifiée depuis sa validation » compare `meta_update_date` à
      `validation_date`. Rafraîchir la date à chaque passage éteindrait ce signal, qui
      est précisément ce qui doit ramener sous les yeux une observation que la source a
      corrigée ;
    - l'historique est un journal, pas un état. Y empiler une ligne par moissonnage le
      rendrait illisible — quatorze millions d'observations moissonnées chaque nuit.

    `DISTINCT` n'est pas décoratif : le `NOT EXISTS` s'évalue contre l'état d'AVANT le
    statement, donc deux occurrences d'un même identifiant dans le lot le franchiraient
    toutes les deux et laisseraient deux lignes d'historique pour une observation.

    Le trigger `tri_insert_synthese_update_validation_status` du cœur se charge de
    reporter statut, commentaire et `meta_validation_date` dans la Synthèse. Il ne touche
    aucune colonne de la liste `UPDATE OF` des déclencheurs de zonage et de sensibilité :
    les ~9 lignes de `cor_area_synthese` par observation ne sont pas recalculées.

    ⚠ En revanche `tri_meta_dates_change_synthese` se déclenche, lui — `BEFORE UPDATE`,
    sans `UPDATE OF` — et repose `meta_update_date = NOW()`. Cela devrait rendre chaque
    observation « modifiée depuis sa validation » à l'instant même où elle est validée.
    Ce n'est pas le cas **parce que `NOW()` rend l'heure de la transaction et non celle
    du statement** : les deux colonnes, toutes deux `timestamp without time zone`,
    reçoivent la même valeur, et la comparaison du module Validation est stricte.
    L'écriture de l'historique doit donc rester dans la transaction de l'INSERT — la
    séparer casserait le filtre sans rien signaler.
    """
    if not lignes or statut is None:
        return 0
    ecrites = db.session.execute(
        PREVALIDATION_SQL,
        {"statut": statut.id_statut,
         "commentaire": statut.commentaire or None,
         "uuids": [str(l["unique_id_sinp"]) for l in lignes]},
    ).rowcount
    statut.ecrites += ecrites
    return ecrites


def insert_batch(lignes: list[dict], prevalidation=None) -> tuple[int, int]:
    """Écrit un lot, en un seul statement physique. Retourne (insérées, mises à jour).

    `INSERT_SQL` alimente désormais ses colonnes depuis des tableaux — `UNNEST`, comme
    `realigner_uuid` et `PREVALIDATION_SQL` — plutôt que depuis un `executemany` : les
    deux triggers `FOR EACH STATEMENT` de la Synthèse (voir l'en-tête du fichier) ne se
    déclenchent ainsi qu'une fois par lot, et non une fois par ligne.

    Le décompte s'appuie sur le `RETURNING` de ce même statement plutôt que sur une
    règle réécrite en Python : la clause WHERE de l'ON CONFLICT (COALESCE sur
    `gbif_empreinte`, `vn_empreinte`, `dbchiro_empreinte`, `gn_empreinte`, repli sur
    `gbif_modified`) décide déjà, ligne par ligne, si la mise à jour a lieu — une ligne
    non touchée n'apparaît pas dans le RETURNING. Il suffit donc de savoir laquelle des
    lignes renvoyées existait déjà avant l'écriture, ce qu'une requête préalable, plus
    légère que l'ancienne, établit.

    Écrit dans **deux** tables quand `prevalidation` est fourni : la Synthèse, puis
    `gn_commons.t_validations` — voir `prevalider`. Les deux dans la même transaction,
    pour qu'une observation ne puisse pas exister sans son historique de validation.
    Le nombre de lignes d'historique écrites s'accumule dans `prevalidation.ecrites`.

    Un statement unique ne peut pas affecter deux fois la même ligne pour un même
    conflit de clé : PostgreSQL lève alors une erreur qui ferait échouer tout le lot, là
    où l'ancien `executemany` traitait chaque ligne séparément et ne faussait qu'un
    compteur. Les connecteurs ne garantissent pas tous l'unicité de leur lot — seul
    GeoNature déduplique le sien — donc `insert_batch` s'en protège pour tous, en ne
    gardant que la première occurrence d'un `unique_id_sinp` dupliqué, comme le fait
    déjà `sources/geonature/api.py`.
    """
    if not lignes:
        return (0, 0)

    vus: set[str] = set()
    uniques = []
    for ligne in lignes:
        cle = str(ligne["unique_id_sinp"])
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(ligne)
    lignes = uniques

    uuids = [l["unique_id_sinp"] for l in lignes]
    deja = set(db.session.execute(
        text("""SELECT unique_id_sinp::text FROM gn_synthese.synthese
                WHERE unique_id_sinp = ANY(CAST(:u AS uuid[]))"""),
        {"u": uuids},
    ).scalars())

    # Un statement unique pour tout le lot : chaque colonne devient un tableau (une
    # valeur par ligne), et non plus un dictionnaire rejoué une fois par ligne.
    colonnes = list(lignes[0])
    parametres = {colonne: [ligne[colonne] for ligne in lignes] for colonne in colonnes}
    ecrites = [str(u) for u in db.session.execute(INSERT_SQL, parametres).scalars()]

    maj = sum(1 for u in ecrites if u in deja)
    inserees = len(ecrites) - maj

    # Après l'INSERT : le trigger de `t_validations` apparie sur `unique_id_sinp`, donc
    # la ligne de Synthèse doit exister. Dans la même transaction, pour qu'une donnée ne
    # puisse jamais être écrite sans son historique de validation.
    prevalider(lignes, prevalidation)
    return (inserees, maj)
