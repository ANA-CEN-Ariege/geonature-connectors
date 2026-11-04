"""Déclare la Synthèse comme emplacement de médias, pour les médias importés

`gn_commons.t_medias.id_table_location` est obligatoire, et GeoNature ne déclare pas
`gn_synthese.synthese` dans `gn_commons.bib_tables_location` : ses médias d'origine sont
rangés sous la table qui les a saisis (le dénombrement Occtax, par exemple). Un média
moissonné n'a pas de telle table — seulement une ligne de Synthèse. On déclare donc
celle-ci, avec `unique_id_sinp` comme champ d'UUID : c'est exactement la relation par
laquelle la Synthèse affiche ses médias (`Synthese.medias`).

`ON CONFLICT DO NOTHING` sur la contrainte `(schema_name, table_name)` : un autre module,
ou une instance plus récente de GeoNature, peut avoir déclaré la même table avant nous.
L'emplacement est alors partagé, et le module ne touche toujours qu'aux médias
rattachés à ses propres lignes (voir `core/medias.py`).

Le retour arrière ne retire la déclaration que si plus aucun média ne s'y rattache —
sans quoi la clé étrangère de `t_medias` l'interdirait de toute façon. Les médias
importés se retirent d'abord par une purge du connecteur.

Revision ID: c9d3e5f7a1b2
Create Date: 2026-11-04
"""

from alembic import op

revision = "c9d3e5f7a1b2"
down_revision = "f7b204e9c318"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        INSERT INTO gn_commons.bib_tables_location
            (table_desc, schema_name, table_name, pk_field, uuid_field_name)
        VALUES (
            'Synthèse — médias rattachés directement à une observation (médias moissonnés '
            'par le module CONNECTORS)',
            'gn_synthese', 'synthese', 'id_synthese', 'unique_id_sinp')
        ON CONFLICT (schema_name, table_name) DO NOTHING
        """
    )


def downgrade():
    op.execute(
        """
        DELETE FROM gn_commons.bib_tables_location b
        WHERE b.schema_name = 'gn_synthese' AND b.table_name = 'synthese'
          AND b.table_desc LIKE '%module CONNECTORS%'
          AND NOT EXISTS (SELECT 1 FROM gn_commons.t_medias m
                          WHERE m.id_table_location = b.id_table_location)
        """
    )
