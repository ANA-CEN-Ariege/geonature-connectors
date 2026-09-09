"""Crée la source Synthèse « dbChiro » et son cadre d'acquisition

Une source distincte de GBIF et de VisioNature : le filtre « Source de la donnée » de la
Synthèse doit permettre de les séparer, et l'origine des données diffère radicalement —
dbChiro est une base thématique de gestionnaire, pas un agrégateur.

⚠ `url_source` reste vide : chaque instance dbChiro a sa propre URL, inconnue à la
migration. Elle est renseignée au premier import, depuis la configuration.

Revision ID: a3f6c81b0e52
Create Date: 2026-09-09
"""

from alembic import op

revision = "a3f6c81b0e52"
down_revision = "e91b4c07a2d8"
branch_labels = None
depends_on = None

SOURCE_NAME = "dbChiro"
CA_UUID = "8c41e07d-6b93-5a28-9f14-2d7b3e6c0a95"


def upgrade():
    op.execute(
        f"""
        INSERT INTO gn_meta.t_acquisition_frameworks (
            unique_acquisition_framework_id, acquisition_framework_name,
            acquisition_framework_desc, acquisition_framework_start_date
        ) VALUES (
            '{CA_UUID}'::uuid,
            'Import de données externes depuis dbChiro',
            'Cadre d''acquisition regroupant les jeux de données moissonnés depuis une '
            'instance dbChiro (dbchiroweb) par le module CONNECTORS. La détermination '
            'd''origine est conservée dans additional_data : 14 % des observations '
            'portent sur un rang supérieur à l''espèce (couples et groupes acoustiques), '
            'que TAXREF ne sait pas exprimer — le cd_nom y est le genre, la famille ou '
            'l''ordre, et seul le champ « determination » dit ce qui a été identifié.',
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
            'Observations moissonnées depuis une instance dbChiro. Chaque observation '
            'conserve son identifiant de saisie, sa session, son gîte ou point d''écoute '
            'et sa détermination d''origine dans additional_data.',
            'id_sighting', NULL, m.id_module
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
