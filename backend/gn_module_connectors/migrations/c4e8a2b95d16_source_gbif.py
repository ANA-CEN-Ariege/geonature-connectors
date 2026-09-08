"""Crée la source Synthèse « GBIF »

`gn_synthese.t_sources` répond à la question « d'où vient physiquement cette ligne, et
comment revenir à la donnée d'origine ? ». Renseigner `url_source` suffit à obtenir, sans
une ligne de frontend, le bouton « voir la donnée source » de la fiche d'observation :
l'interface concatène `url_source + '/' + entity_source_pk_value`. En y stockant le
`gbifID`, le lien pointe directement sur la fiche GBIF.

C'est aussi ce qui rend la donnée filtrable dans la Synthèse via le filtre « Source de la
donnée », et distinguable de la saisie interne.

Revision ID: c4e8a2b95d16
Create Date: 2026-09-08
"""

from alembic import op

revision = "c4e8a2b95d16"
down_revision = "b2d7e9f31a04"
branch_labels = None
depends_on = None

SOURCE_NAME = "GBIF"


def upgrade():
    op.execute(
        f"""
        INSERT INTO gn_synthese.t_sources
            (name_source, desc_source, entity_source_pk_field, url_source, id_module)
        SELECT
            '{SOURCE_NAME}',
            'Occurrences moissonnées depuis le GBIF. Chaque observation conserve son '
            'jeu de données, son producteur et sa licence d''origine dans additional_data. '
            'Données non re-partageables vers le SINP.',
            'gbifID',
            'https://www.gbif.org/occurrence',
            m.id_module
        FROM gn_commons.t_modules m
        WHERE m.module_code = 'CONNECTORS'
        ON CONFLICT (name_source) DO UPDATE
            SET url_source = EXCLUDED.url_source,
                entity_source_pk_field = EXCLUDED.entity_source_pk_field,
                id_module = EXCLUDED.id_module
        """
    )


def downgrade():
    # Ne retire la source que si plus aucune observation ne s'y rattache : la contrainte
    # sur synthese.id_source est NOT NULL, supprimer une source encore utilisée ferait
    # échouer la migration — mieux vaut la laisser en place et le dire.
    op.execute(
        f"""
        DELETE FROM gn_synthese.t_sources s
        WHERE s.name_source = '{SOURCE_NAME}'
          AND NOT EXISTS (
              SELECT 1 FROM gn_synthese.synthese sy WHERE sy.id_source = s.id_source
          )
        """
    )
