"""Déclare les permissions du module CONNECTORS

Revision ID: 8a1c4f0d2e11
Create Date: 2026-09-08
"""

from alembic import op

revision = "8a1c4f0d2e11"
down_revision = None
branch_labels = ("connectors",)
depends_on = ("f051b88a57fd",)


def upgrade():
    op.execute(
        """
        INSERT INTO gn_permissions.t_permissions_available
            (id_module, id_object, id_action, label, scope_filter)
        SELECT m.id_module, o.id_object, a.id_action, v.label, v.scope_filter
        FROM (
            VALUES
                ('CONNECTORS', 'ALL', 'R', False, 'Consulter les imports de sources externes'),
                ('CONNECTORS', 'ALL', 'C', False, 'Lancer un import depuis une source externe')
        ) AS v (module_code, object_code, action_code, scope_filter, label)
        JOIN gn_commons.t_modules m ON m.module_code = v.module_code
        JOIN gn_permissions.t_objects o ON o.code_object = v.object_code
        JOIN gn_permissions.bib_actions a ON a.code_action = v.action_code
        """
    )


def downgrade():
    op.execute(
        """
        DELETE FROM gn_permissions.t_permissions_available pa
        USING gn_commons.t_modules m
        WHERE pa.id_module = m.id_module AND m.module_code = 'CONNECTORS'
        """
    )
