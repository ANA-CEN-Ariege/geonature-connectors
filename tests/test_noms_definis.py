"""Aucun nom indéfini dans le code du module.

Ce fichier existe à cause d'une panne qui s'est produite deux fois. Les imports du
module sont **locaux aux commandes** — `from .sources.visionature import (...)` à
l'intérieur de `visionature_import` — pour ne pas charger l'API Biolovision quand on lance une
commande GBIF. Un import oublié ne se voit donc ni à l'import du module, ni à la
compilation : il attend l'exécution de la commande, après le chargement du référentiel
d'espèces et de celui des observateurs, soit plusieurs minutes d'attente avant le
`NameError`.

La suite de tests ne l'attrape pas davantage : `commands.py` n'est pas couvert, et ne
peut guère l'être sans instance GeoNature. Une analyse statique, elle, le voit
immédiatement.

Le répertoire `biolovision/` est exclu : `gettext.install()` y définit `_` dans les
builtins, ce que pyflakes ne peut pas savoir.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))


def fichiers_du_module():
    return sorted(
        chemin for chemin in (RACINE / "gn_module_connectors").rglob("*.py")
        if "biolovision" not in chemin.parts
    )


def test_aucun_nom_indefini():
    """Un import oublié ne doit pas attendre l'exécution pour se signaler."""
    pyflakes_api = pytest.importorskip(
        "pyflakes.api", reason="pyflakes absent : pip install -r tests/requirements.txt")
    from pyflakes import reporter as pyflakes_reporter
    import io as _io

    sortie, erreurs = _io.StringIO(), _io.StringIO()
    rapporteur = pyflakes_reporter.Reporter(sortie, erreurs)
    for chemin in fichiers_du_module():
        pyflakes_api.checkPath(str(chemin), rapporteur)

    indefinis = [ligne for ligne in sortie.getvalue().splitlines()
                 if "undefined name" in ligne]
    assert not indefinis, "noms indéfinis :\n" + "\n".join(indefinis)
