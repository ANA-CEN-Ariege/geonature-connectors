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
# serait de toute façon écrasé.
INSERT_SQL = text(
    """
    INSERT INTO gn_synthese.synthese (
        unique_id_sinp, id_source, id_module, id_dataset,
        entity_source_pk_value, cd_nom, nom_cite,
        date_min, date_max, count_min, count_max,
        observers, "precision", additional_data,
        id_nomenclature_obs_technique, id_nomenclature_bio_condition,
        id_nomenclature_bio_status, id_nomenclature_naturalness,
        id_nomenclature_observation_status, id_nomenclature_source_status,
        id_nomenclature_life_stage, id_nomenclature_sex,
        id_nomenclature_obj_count, id_nomenclature_type_count,
        id_nomenclature_biogeo_status, id_nomenclature_exist_proof,
        id_nomenclature_valid_status,
        the_geom_4326, the_geom_point, the_geom_local,
        last_action
    ) VALUES (
        :unique_id_sinp, :id_source, :id_module, :id_dataset,
        :entity_source_pk_value, :cd_nom, :nom_cite,
        :date_min, :date_max, :count_min, :count_max,
        :observers, :precision, CAST(:additional_data AS jsonb),
        :id_nomenclature_obs_technique, :id_nomenclature_bio_condition,
        :id_nomenclature_bio_status, :id_nomenclature_naturalness,
        :id_nomenclature_observation_status, :id_nomenclature_source_status,
        :id_nomenclature_life_stage, :id_nomenclature_sex,
        :id_nomenclature_obj_count, :id_nomenclature_type_count,
        :id_nomenclature_biogeo_status, :id_nomenclature_exist_proof,
        :id_nomenclature_valid_status,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
        ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :local_srid),
        'I'
    )
    ON CONFLICT (unique_id_sinp) DO UPDATE SET
        cd_nom = EXCLUDED.cd_nom,
        nom_cite = EXCLUDED.nom_cite,
        date_min = EXCLUDED.date_min,
        date_max = EXCLUDED.date_max,
        count_min = EXCLUDED.count_min,
        count_max = EXCLUDED.count_max,
        observers = EXCLUDED.observers,
        "precision" = EXCLUDED."precision",
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
        id_nomenclature_valid_status = EXCLUDED.id_nomenclature_valid_status,
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
    WHERE gn_synthese.synthese.additional_data->>'gbif_empreinte'
          IS DISTINCT FROM EXCLUDED.additional_data->>'gbif_empreinte'
       OR gn_synthese.synthese.additional_data->>'gbif_modified'
          IS DISTINCT FROM EXCLUDED.additional_data->>'gbif_modified'
    """
)


def local_srid() -> int:
    """SRID local de l'instance, tel que le déduit aussi le module Import."""
    return db.session.execute(
        text("SELECT Find_SRID('ref_geo', 'l_areas', 'geom')")
    ).scalar()


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
    return db.session.execute(
        text("SELECT id_module FROM gn_commons.t_modules WHERE module_code = :c"),
        {"c": module_code},
    ).scalar()


def insert_batch(lignes: list[dict]) -> tuple[int, int]:
    """Écrit un lot. Retourne (insérées, mises à jour).

    Le décompte se fait en interrogeant l'état AVANT écriture plutôt qu'en lisant
    `rowcount` : avec un `ON CONFLICT` et un executemany, `rowcount` n'est pas fiable
    selon le driver, et confondre « insérée », « mise à jour » et « inchangée »
    fausserait tout le bilan — c'est précisément ce qui rend un import opaque.
    """
    if not lignes:
        return (0, 0)

    uuids = [l["unique_id_sinp"] for l in lignes]
    deja = {
        str(u): m
        for u, m in db.session.execute(
            text("""SELECT unique_id_sinp::text, additional_data->>'gbif_empreinte'
                    FROM gn_synthese.synthese
                    WHERE unique_id_sinp = ANY(CAST(:u AS uuid[]))"""),
            {"u": uuids},
        ).all()
    }

    import json as _json
    maj = sum(
        1 for l in lignes
        if str(l["unique_id_sinp"]) in deja
        and deja[str(l["unique_id_sinp"])]
            != (_json.loads(l["additional_data"]).get("gbif_empreinte") or None)
    )
    inserees = len(lignes) - len(deja)

    db.session.execute(INSERT_SQL, lignes)
    return (inserees, maj)
