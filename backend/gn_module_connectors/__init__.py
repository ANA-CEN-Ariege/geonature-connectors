"""Module GeoNature d'alimentation de la Synthèse depuis des sources externes.

Tourner *dans* GeoNature plutôt qu'à côté n'est pas un détail de packaging : c'est ce qui
donne accès à `db.session` sans distribuer d'identifiants PostgreSQL, ce qui permet
d'insérer par lots au lieu d'une requête HTTP par observation, et ce qui rend le module
installable par un administrateur fonctionnel — sans quoi personne ne l'adoptera.
"""

MODULE_CODE = "CONNECTORS"
MODULE_PICTO = "fa-plug"
