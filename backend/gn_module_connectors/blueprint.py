from flask import Blueprint

from .commands import connectors_cli

blueprint = Blueprint("connectors", __name__)

# Les commandes déclarées sur le blueprint sont exposées par le FlaskGroup de GeoNature :
# `geonature connectors <commande>`.
for cmd in connectors_cli:
    blueprint.cli.add_command(cmd)
