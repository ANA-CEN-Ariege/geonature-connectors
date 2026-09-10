"""Crée la source Synthèse « GeoNature distant » et son cadre d'acquisition de repli

⚠ `name_source` dit « GeoNature **distant** », et non « GeoNature ». Le module tourne dans
une instance GeoNature : une source nommée « GeoNature » n'apprendrait rien à qui déroule
le filtre « Source de la donnée », où toutes les lignes sont d'une façon ou d'une autre
« GeoNature ». Ce qui distingue celles-ci, c'est qu'elles viennent d'ailleurs.

⚠ Le cadre créé ici est un **repli**, pas le cadre habituel du connecteur. Contrairement
aux trois autres sources, celle-ci reprend les identifiants SINP du producteur : chaque
`ca_uuid` distant donne un cadre local portant ce même UUID, et chaque `jdd_uuid` un jeu
portant le sien. Le cadre ci-dessous n'accueille que ce qui arrive sans cadre exploitable,
et sert d'ancrage au ménage des jeux vides.

⚠ `url_source` reste vide : chaque instance a la sienne, inconnue à la migration. Elle est
renseignée au premier import, et pointe vers la redirection du blueprint — le permalien
d'une observation GeoNature contient un fragment (`/#/synthese/occurrence/<id>`), que la
concaténation du cœur ne sait pas produire.

Revision ID: f7b204e9c318
Create Date: 2026-09-10
"""

from alembic import op

revision = "f7b204e9c318"
down_revision = "a3f6c81b0e52"
branch_labels = None
depends_on = None

SOURCE_NAME = "GeoNature distant"
CA_UUID = "5b9d3f26-4c81-5e70-a2f9-1d6c8b40e73a"


def upgrade():
    op.execute(
        f"""
        INSERT INTO gn_meta.t_acquisition_frameworks (
            unique_acquisition_framework_id, acquisition_framework_name,
            acquisition_framework_desc, acquisition_framework_start_date
        ) VALUES (
            '{CA_UUID}'::uuid,
            'Import de données externes depuis une instance GeoNature',
            'Cadre de repli pour les jeux de données moissonnés depuis une autre instance '
            'GeoNature par le module CONNECTORS. Le fonctionnement normal est de recréer '
            'localement le cadre d''acquisition du producteur sous son propre UUID SINP, '
            'de sorte qu''un cadre garde son identité d''une plateforme à l''autre ; ce '
            'cadre-ci n''accueille que les jeux dont l''export distant ne livre aucun '
            'identifiant de cadre exploitable.',
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
            'Observations moissonnées depuis l''API d''export d''une autre instance '
            'GeoNature. Chaque observation conserve son identifiant permanent SINP, son '
            'jeu de données et son cadre d''acquisition d''origine ; l''instance, '
            'l''export et la licence sont consignés dans additional_data.',
            'id_synthese', NULL, m.id_module
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
