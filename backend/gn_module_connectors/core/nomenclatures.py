"""Résolution des nomenclatures SINP.

Générique : ne connaît aucune source externe.

Les correspondances sont exprimées en `cd_nomenclature` (le code SINP, stable d'une
instance à l'autre) et jamais en `id_nomenclature` (une clé technique propre à chaque
base). La conversion se fait ici, une fois, au démarrage de l'import.

Chaque colonne `id_nomenclature_*` de `gn_synthese.synthese` porte un DEFAULT
`gn_synthese.get_default_nomenclature_value(...)`. Comme l'INSERT est un statement unique
réutilisé pour tout un lot, on ne peut pas omettre une colonne pour une ligne seulement :
on résout donc le défaut nous-mêmes et on le passe explicitement. Le résultat est
identique à celui qu'aurait produit le DEFAULT, sans renoncer à l'insertion par lots.
"""

from sqlalchemy import text
from geonature.utils.env import db


class Resolver:
    """Convertit (mnémonique, cd_nomenclature) en id_nomenclature, avec cache."""

    def __init__(self):
        self._ids: dict[tuple[str, str], int | None] = {}
        self._defauts: dict[str, int | None] = {}

    def id(self, mnemonique: str, cd: str | None) -> int | None:
        """id_nomenclature, ou le défaut de la colonne si `cd` est None ou inconnu."""
        if cd is None:
            return self.defaut(mnemonique)
        cle = (mnemonique, str(cd))
        if cle not in self._ids:
            self._ids[cle] = db.session.execute(
                text("SELECT ref_nomenclatures.get_id_nomenclature(:m, :c)"),
                {"m": mnemonique, "c": str(cd)},
            ).scalar()
        # Une valeur absente du référentiel de l'instance ne doit pas faire échouer
        # l'insertion : on retombe sur le défaut, qui est toujours valide.
        return self._ids[cle] if self._ids[cle] is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique: str) -> int | None:
        if mnemonique not in self._defauts:
            self._defauts[mnemonique] = db.session.execute(
                text("SELECT gn_synthese.get_default_nomenclature_value(:m)"),
                {"m": mnemonique},
            ).scalar()
        return self._defauts[mnemonique]
