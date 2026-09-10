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
    """Convertit (mnémonique, cd_nomenclature) en id_nomenclature, avec cache.

    Deux familles de méthodes, à ne pas confondre :

    - `id()` / `defaut()` : la source parle en `cd_nomenclature`. C'est le cas de GBIF,
      VisioNature et dbChiro, dont les tables de correspondance sont écrites en codes.
    - `id_souple()` / `id_souple_ou_defaut()` : la source parle en **libellés**. C'est le
      cas d'une autre instance GeoNature, dont la vue d'export `v_synthese_sinp` livre des
      `label_default` (« Reproducteur », « Vivant », « Sauvage ») et non des codes. Passer
      un libellé à `get_id_nomenclature`, qui attend un code, rend NULL sans rien
      signaler — c'est le défaut du `GeoNatureParser` d'api2GN, où toutes les
      nomenclatures se perdent en silence.
    """

    def __init__(self):
        self._ids: dict[tuple[str, str], int | None] = {}
        self._defauts: dict[str, int | None] = {}
        # Un type entier par entrée, chargé en une requête : {code: id}, {libellé: id}.
        self._types: dict[str, tuple[dict[str, int], dict[str, int]]] = {}

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

    # ── Résolution par libellé ───────────────────────────────────────────────

    def _charger_type(self, mnemonique: str) -> tuple[dict[str, int], dict[str, int]]:
        """Charge un type de nomenclature entier, en une requête.

        Une requête par **type** — une vingtaine pour tout un import — au lieu d'une par
        **valeur**. `core/datasets.resoudre_nomenclature` fait l'inverse : c'est acceptable
        pour la poignée de valeurs d'un fichier de configuration, pas dans la boucle qui
        transforme quinze colonnes de chaque observation.

        Le filtre `active` reproduit ce que fait `get_id_nomenclature` : sans lui, on
        résoudrait vers des valeurs que l'instance a retirées de son référentiel.
        """
        if mnemonique not in self._types:
            codes: dict[str, int] = {}
            libelles: dict[str, int] = {}
            for id_nomenclature, cd, label in db.session.execute(
                text("""SELECT t.id_nomenclature, t.cd_nomenclature, t.label_default
                        FROM ref_nomenclatures.t_nomenclatures t
                        JOIN ref_nomenclatures.bib_nomenclatures_types b
                          ON b.id_type = t.id_type
                        WHERE b.mnemonique = :m AND t.active"""),
                {"m": mnemonique},
            ).all():
                if cd is not None:
                    codes.setdefault(str(cd), id_nomenclature)
                if label:
                    # Même normalisation qu'en SQL — `lower(trim(...))`, sans pliage des
                    # accents : rapprocher « Determine » et « Déterminé » créerait des
                    # correspondances fausses au lieu d'en signaler l'absence.
                    libelles.setdefault(label.strip().lower(), id_nomenclature)
            self._types[mnemonique] = (codes, libelles)
        return self._types[mnemonique]

    def id_souple(self, mnemonique: str, valeur: str | None) -> int | None:
        """id_nomenclature d'après un `cd_nomenclature` **ou** un `label_default`.

        Le code est essayé d'abord — il est stable d'une instance à l'autre — et le
        libellé ne sert que de repli.

        ⚠ Ne retombe **jamais** sur le défaut de la colonne. C'est à l'appelant de
        décider : le défaut convient aux quinze colonnes de nomenclature ordinaires, et
        surtout pas à `id_nomenclature_diffusion_level`, où NULL veut dire « le producteur
        ne se prononce pas » et où inventer une valeur reviendrait à inventer une
        restriction de diffusion — ou à en perdre une.
        """
        valeur = str(valeur or "").strip()
        if not valeur:
            return None
        codes, libelles = self._charger_type(mnemonique)
        return codes.get(valeur) or libelles.get(valeur.lower())

    def id_souple_ou_defaut(self, mnemonique: str, valeur: str | None) -> int | None:
        """`id_souple`, avec repli sur le défaut de la colonne.

        Pour les colonnes dont la Synthèse porte un DEFAULT : une valeur absente ou
        inconnue du référentiel local doit donner ce que le DEFAULT aurait donné, sans
        quoi l'insertion par lots écrirait NULL là où l'insertion ligne à ligne aurait
        écrit une valeur.
        """
        trouve = self.id_souple(mnemonique, valeur)
        return trouve if trouve is not None else self.defaut(mnemonique)
