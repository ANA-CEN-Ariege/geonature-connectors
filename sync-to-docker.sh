#!/usr/bin/env bash
# Déploie le module vers l'instance GeoNature docker de développement.
# Le dépôt vit hors du volume monté par docker : ce script fait le pont.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="${1:-/home/cedric/dev/Geonature-Docker-services/sources/gn_module_connectors}"

rsync -a --delete \
  --exclude '.git' --exclude 'data/' --exclude 'TAXREF_v17_2024/' \
  --exclude '.backup-*' --exclude '__pycache__' --exclude 'config.toml' \
  --exclude 'gbif2geonature/' --exclude 'vn2geonature/' --exclude 'docs/data/' \
  --exclude '*.egg-info' \
  "$SRC/" "$DST/"

echo "→ synchronisé vers $DST"
echo "  puis, dans le conteneur :"
echo "    geonature upgrade-modules-db CONNECTORS"
echo "  et après une première installation ou un changement d'entry point :"
echo "    docker restart <conteneur-backend>   # sinon les workers gunicorn"
echo "                                          # ne voient pas le module -> 500"
