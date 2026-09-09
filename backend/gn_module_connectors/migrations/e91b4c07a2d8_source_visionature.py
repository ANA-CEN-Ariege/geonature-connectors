"""Crée la source Synthèse « VisioNature » et son cadre d'acquisition

Une source distincte de GBIF : le filtre « Source de la donnée » de la Synthèse doit
permettre de distinguer les deux, et `url_source` diffère — VisioNature renvoie vers la
fiche d'observation de l'instance d'origine, GBIF vers gbif.org.

⚠ `url_source` reste vide ici : chaque instance VisioNature a sa propre URL, inconnue
à la migration. Elle est renseignée au premier import, depuis la configuration.

Revision ID: e91b4c07a2d8
Create Date: 2026-09-09
"""

from alembic import op

revision = "e91b4c07a2d8"
down_revision = "d5f1a83c7e29"
branch_labels = None
depends_on = None

SOURCE_NAME = "VisioNature"
CA_UUID = "3d8b7e14-9a2c-5f63-b481-6e0d5a3c9f27"


def upgrade():
    op.execute(
        f"""
        INSERT INTO gn_meta.t_acquisition_frameworks (
            unique_acquisition_framework_id, acquisition_framework_name,
            acquisition_framework_desc, acquisition_framework_start_date
        ) VALUES (
            '{CA_UUID}'::uuid,
            'Import de données externes depuis VisioNature',
            'Cadre d''acquisition regroupant les jeux de données moissonnés depuis une '
            'instance VisioNature (Biolovision) par le module CONNECTORS. Les codes atlas '
            'de nidification sont conservés dans additional_data : le SINP ne connaît pas '
            'leur gradation possible/probable/certaine, qui est pourtant le cœur de la '
            'donnée pour un atlas ornithologique.',
            CURRENT_DATE
        )
        ON CONFLICT (unique_acquisition_framework_id) DO NOTHING
        """
    )
    op.execute(
        f"""
        INSERT INTO gn_synthese.t_sources
            (name_source, desc_source, entity_source_pk_field, url_source, id_module)
        SELECT
            '{SOURCE_NAME}',
            'Observations moissonnées depuis une instance VisioNature (Biolovision). '
            'Chaque observation conserve son identifiant de relevé, son observateur et '
            'son code atlas dans additional_data.',
            'sighting_id', NULL, m.id_module
        FROM gn_commons.t_modules m WHERE m.module_code = 'CONNECTORS'
        ON CONFLICT (name_source) DO UPDATE
            SET entity_source_pk_field = EXCLUDED.entity_source_pk_field,
                id_module = EXCLUDED.id_module
        """
    )


def downgrade():
    op.execute(
        f"""
        DELETE FROM gn_synthese.t_sources s
        WHERE s.name_source = '{SOURCE_NAME}'
          AND NOT EXISTS (SELECT 1 FROM gn_synthese.synthese sy
                          WHERE sy.id_source = s.id_source)
        """
    )
    op.execute(
        f"""
        DELETE FROM gn_meta.t_acquisition_frameworks af
        WHERE af.unique_acquisition_framework_id = '{CA_UUID}'::uuid
          AND NOT EXISTS (SELECT 1 FROM gn_meta.t_datasets d
                          WHERE d.id_acquisition_framework = af.id_acquisition_framework)
        """
    )
