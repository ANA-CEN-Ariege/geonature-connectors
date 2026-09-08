"""Le module n'expose pas d'interface

`geonature install-gn-module` déclare tout module avec `active_frontend = True`. Or
CONNECTORS est un module de ligne de commande et de tâche planifiée : il n'a pas de
`frontend/app/gnModule.module.ts`. Laisser le drapeau à vrai fait chercher à GeoNature
une entrée d'interface inexistante.

Revision ID: d5f1a83c7e29
Create Date: 2026-09-08
"""

from alembic import op

revision = "d5f1a83c7e29"
down_revision = "c4e8a2b95d16"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE gn_commons.t_modules
        SET active_frontend = false, module_target = NULL
        WHERE module_code = 'CONNECTORS'
        """
    )


def downgrade():
    op.execute(
        """
        UPDATE gn_commons.t_modules
        SET active_frontend = true, module_target = '_self'
        WHERE module_code = 'CONNECTORS'
        """
    )
