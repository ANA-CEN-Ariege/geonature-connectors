"""Petits convertisseurs partagés entre les modules du connecteur GeoNature.

Propre à ce connecteur, et non à `core/` : la forme des valeurs converties (chaînes
issues d'une vue SQL exposée en JSON par le module d'export) n'a de sens que pour ce
connecteur-ci.
"""


def _entier(valeur):
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None
