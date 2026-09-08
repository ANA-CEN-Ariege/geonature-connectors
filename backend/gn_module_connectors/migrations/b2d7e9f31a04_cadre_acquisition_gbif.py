"""Crée le cadre d'acquisition des données GBIF

GBIF n'a aucun équivalent du cadre d'acquisition : sa hiérarchie s'arrête à
« organisation publiante → jeu de données ». L'élément EML `project` n'est qu'une
redite du jeu (même titre, ni identifiant ni financement), et le CA du SINP n'est pas
exporté vers GBIF. Il faut donc le créer nous-mêmes.

Un CA décrit un programme d'acquisition avec ses objectifs : ici, le programme est
*l'import lui-même*, pas la collecte d'origine — celle-ci reste portée par le JDD, qui
conserve le producteur, la licence et la citation de la source.

Revision ID: b2d7e9f31a04
Create Date: 2026-09-08
"""

from alembic import op

revision = "b2d7e9f31a04"
down_revision = "8a1c4f0d2e11"
branch_labels = None
depends_on = None

# UUID fixe : rend la migration rejouable et donne au code un point d'ancrage stable
# pour retrouver le cadre sans dépendre de son libellé.
CA_UUID = "0ea3a1f2-6d4b-5c8e-9a17-3f2b8c4d5e60"


def upgrade():
    op.execute(
        f"""
        INSERT INTO gn_meta.t_acquisition_frameworks (
            unique_acquisition_framework_id,
            acquisition_framework_name,
            acquisition_framework_desc,
            acquisition_framework_start_date
        )
        VALUES (
            '{CA_UUID}'::uuid,
            'Import de données externes depuis le GBIF',
            'Cadre d''acquisition regroupant les jeux de données moissonnés depuis le GBIF '
            '(Global Biodiversity Information Facility) par le module CONNECTORS. '
            'Chaque jeu de données conserve son producteur, sa licence et sa citation d''origine. '
            'Ces données ne doivent pas être reversées au SINP : le GBIF exclut explicitement '
            'la republication de données qui en sont extraites, et la majeure partie de ce corpus '
            'provient déjà du SINP via l''INPN.',
            CURRENT_DATE
        )
        ON CONFLICT (unique_acquisition_framework_id) DO NOTHING
        """
    )


def downgrade():
    # Ne supprime le cadre que s'il ne porte plus aucun jeu de données : le retirer
    # alors que des observations y sont rattachées casserait la contrainte de clé
    # étrangère et, pire, orphelinerait des données déjà en Synthèse.
    op.execute(
        f"""
        DELETE FROM gn_meta.t_acquisition_frameworks af
        WHERE af.unique_acquisition_framework_id = '{CA_UUID}'::uuid
          AND NOT EXISTS (
              SELECT 1 FROM gn_meta.t_datasets d
              WHERE d.id_acquisition_framework = af.id_acquisition_framework
          )
        """
    )
