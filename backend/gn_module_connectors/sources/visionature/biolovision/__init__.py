"""Client de l'API Biolovision — copie du projet Client_API_VN.

Origine  : https://github.com/dthonon/Client_API_VN — src/biolovision/
Auteur   : Daniel Thonon
Licence  : GNU General Public License v3.0
Révision : 376c2e1b6f2707fa8abd60ee210be3e7e125ef2b (2025-01-15)

Pourquoi une copie plutôt qu'une dépendance : `Client_API_VN` déclare vingt
dépendances — alembic, apscheduler, pandas, psycopg2, pyproj, dynaconf, beautifulsoup4… —
dont aucune n'est utilisée par cette couche d'API, qui ne demande que `requests` et
`requests_oauthlib`. Les imposer à chaque instance GeoNature qui installerait ce module
serait disproportionné, d'autant que ses bornes de version sur SQLAlchemy et pandas
coïncident tout juste avec celles de GeoNature.

La contrepartie est assumée : les correctifs amont ne parviennent pas automatiquement.
La révision ci-dessus permet de suivre l'écart.

GPL-3.0 est compatible avec l'AGPL-3.0 de GeoNature.
"""

import gettext

__version__ = "vendorisé depuis Client_API_VN 376c2e1"

# `api.py` appelle `_()` une trentaine de fois pour ses messages de journal. Sans
# catalogue de traduction, `gettext.install` retombe sur une traduction neutre où `_`
# est l'identité : les messages restent en anglais, ce qui est sans conséquence pour du
# journal technique et évite d'embarquer les fichiers de locale.
gettext.install("biolovision")
