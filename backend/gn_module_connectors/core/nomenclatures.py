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

from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import text
from geonature.utils.env import db


class TypeNomenclature(NamedTuple):
    """Un type de nomenclature entier, indexé dans les deux sens.

    Quatre index plutôt qu'un, parce que les appelants n'arrivent pas avec le même
    vocabulaire : une source parle en codes, une autre en libellés, un fichier de
    configuration en libellés aussi, et un message d'erreur doit rendre le libellé
    d'origine — non normalisé, celui que l'interface affiche.
    """

    codes: dict[str, int]           # cd_nomenclature -> id_nomenclature
    libelles: dict[str, int]        # libellé normalisé -> id_nomenclature
    cd_par_libelle: dict[str, str]  # libellé normalisé -> cd_nomenclature
    libelle_par_cd: dict[str, str]  # cd_nomenclature -> libellé d'origine


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

    S'y ajoute `cd_souple()`, qui rend un **code** et non un identifiant : la
    configuration se lit en libellés, les tables de correspondance s'écrivent en codes,
    et la traduction se fait une fois au démarrage plutôt qu'en propageant deux
    vocabulaires dans toute la chaîne. `valeurs()` sert les messages d'erreur.

    Enfin `manques` : les valeurs qu'aucune des deux familles n'a su résoudre. Le
    connecteur GeoNature tient déjà le sien, parce qu'il doit distinguer le libellé d'un
    producteur du niveau de diffusion ; celui-ci sert aux trois autres, qui retombaient
    sur le défaut sans rien dire.
    """

    def __init__(self):
        self._defauts: dict[str, int | None] = {}
        # Un type de nomenclature entier par entrée, chargé en une requête.
        self._types: dict[str, "TypeNomenclature"] = {}
        # Valeurs qu'on n'a pas su résoudre, collectées plutôt que taries : la commande
        # les affiche en fin d'import. Sans ce registre, une correspondance devenue
        # fausse — une valeur que l'instance a retirée de son référentiel — se solderait
        # par une colonne au défaut, sans un mot. C'est le silence qu'on reproche à
        # api2GN ; il n'a pas de raison d'être toléré pour les autres sources.
        self.manques: set[tuple[str, str]] = set()

    def id(self, mnemonique: str, cd: str | None) -> int | None:
        """id_nomenclature, ou le défaut de la colonne si `cd` est None ou inconnu.

        Résolu par le type déjà chargé, et non par `get_id_nomenclature` : une requête
        par type plutôt qu'une par valeur, et surtout **le même filtre `active` que
        `id_souple`**. La fonction SQL, elle, n'en pose aucun (cf. `_charger_type`) :
        les deux familles de méthodes écrivaient donc des valeurs différentes sur une
        instance ayant retiré une valeur de son référentiel — l'une l'écrivait, l'autre
        la refusait.
        """
        if cd is None:
            return self.defaut(mnemonique)
        trouve = self._charger_type(mnemonique).codes.get(str(cd))
        if trouve is not None:
            return trouve
        # Une valeur absente du référentiel de l'instance ne doit pas faire échouer
        # l'insertion : on retombe sur le défaut, qui est toujours valide. Mais on la
        # consigne, sans quoi la perte serait invisible.
        self.manques.add((mnemonique, str(cd)))
        return self.defaut(mnemonique)

    def defaut(self, mnemonique: str) -> int | None:
        if mnemonique not in self._defauts:
            self._defauts[mnemonique] = db.session.execute(
                text("SELECT gn_synthese.get_default_nomenclature_value(:m)"),
                {"m": mnemonique},
            ).scalar()
        return self._defauts[mnemonique]

    # ── Résolution par libellé ───────────────────────────────────────────────

    def _charger_type(self, mnemonique: str) -> "TypeNomenclature":
        """Charge un type de nomenclature entier, en une requête.

        Une requête par **type** — une vingtaine pour tout un import — au lieu d'une par
        **valeur**. `core/datasets.resoudre_nomenclature` fait l'inverse : c'est acceptable
        pour la poignée de valeurs d'un fichier de configuration, pas dans la boucle qui
        transforme quinze colonnes de chaque observation.

        ⚠ Le filtre `active` ne reproduit **pas** `ref_nomenclatures.get_id_nomenclature`,
        contrairement à ce qui a longtemps été écrit ici : cette fonction ne filtre pas
        (`Nomenclature-api-module`, `migrations/data/nomenclatures.sql`, définition
        unique). Le filtre est un choix du module — ne pas écrire une valeur que
        l'instance a délibérément retirée de son référentiel —, et `id()` l'applique
        désormais aussi, faute de quoi deux connecteurs traitaient différemment la même
        valeur désactivée.
        """
        if mnemonique not in self._types:
            codes: dict[str, int] = {}
            libelles: dict[str, int] = {}
            # Libellé -> code, pour `cd_souple` : la configuration se lit en libellés,
            # le reste du module travaille en `cd_nomenclature`. La traduction se fait
            # ici, une fois, au lieu de propager deux vocabulaires dans la chaîne.
            codes_par_libelle: dict[str, str] = {}
            # Et le retour, non normalisé : un message d'erreur doit citer le libellé
            # tel que le référentiel l'écrit, accents et majuscules compris.
            libelles_par_code: dict[str, str] = {}
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
                    if cd is not None:
                        codes_par_libelle.setdefault(label.strip().lower(), str(cd))
                        libelles_par_code.setdefault(str(cd), label.strip())
            self._types[mnemonique] = TypeNomenclature(
                codes, libelles, codes_par_libelle, libelles_par_code)
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
        type_nomenclature = self._charger_type(mnemonique)
        return (type_nomenclature.codes.get(valeur)
                or type_nomenclature.libelles.get(valeur.lower()))

    def id_souple_ou_defaut(self, mnemonique: str, valeur: str | None) -> int | None:
        """`id_souple`, avec repli sur le défaut de la colonne.

        Pour les colonnes dont la Synthèse porte un DEFAULT : une valeur absente ou
        inconnue du référentiel local doit donner ce que le DEFAULT aurait donné, sans
        quoi l'insertion par lots écrirait NULL là où l'insertion ligne à ligne aurait
        écrit une valeur.
        """
        trouve = self.id_souple(mnemonique, valeur)
        return trouve if trouve is not None else self.defaut(mnemonique)

    def cd_souple(self, mnemonique: str, valeur: str | None) -> str | None:
        """`cd_nomenclature` d'après un code **ou** un libellé.

        Pendant de `id_souple`, mais du côté du vocabulaire et non de l'identifiant.
        Les tables de correspondance des connecteurs sont écrites en `cd_nomenclature` ;
        un fichier de configuration, lui, se relit d'autant mieux qu'il porte le libellé
        que l'interface affiche. Traduire ici évite de faire circuler les deux.
        """
        valeur = str(valeur or "").strip()
        if not valeur:
            return None
        type_nomenclature = self._charger_type(mnemonique)
        if valeur in type_nomenclature.codes:
            return valeur
        return type_nomenclature.cd_par_libelle.get(valeur.lower())

    def valeurs(self, mnemonique: str) -> list[str]:
        """`cd_nomenclature` et libellé de chaque valeur active, pour un message d'erreur.

        Une valeur de configuration introuvable doit dire ce qui *était* trouvable :
        sans cela, l'exploitant n'a qu'un refus et aucune piste.
        """
        type_nomenclature = self._charger_type(mnemonique)
        par_code = type_nomenclature.libelle_par_cd
        return [f"{cd} ({par_code[cd]})" if cd in par_code else cd
                for cd in sorted(type_nomenclature.codes, key=lambda c: (len(c), c))]


def exiger_cd(resolver: Resolver, mnemonique: str, valeur: str, reglage: str) -> str:
    """`cd_nomenclature` d'une valeur **de configuration**, ou `ValueError`.

    Une valeur venue du producteur qu'on ne sait pas résoudre est un fait à consigner :
    elle part dans `manques` et l'import continue, des dizaines de milliers
    d'observations ne devant pas s'arrêter sur le vocabulaire d'un tiers.

    Une valeur venue de la **configuration** est autre chose : quelqu'un l'a posée
    délibérément. `Resolver.id` retombe sur le défaut du type, et `NIV_PRECIS` n'en a
    aucun — une coquille dans `niveau_diffusion` donnait donc NULL, c'est-à-dire la
    **suppression silencieuse de la restriction** que l'exploitant croyait avoir mise, au
    moment précis où elle devait jouer. D'où l'échec franc.

    Accepte indifféremment un code ou un libellé : l'interface de GeoNature affiche
    « Aucune », pas « 4 », et exiger le code sans le dire était la moitié du piège.

    Pendant de `geonature.nomenclatures._exige`, qui rend un `id_nomenclature` et vit
    dans le connecteur pour le garder exempt de dépendance à la base.
    """
    cd = resolver.cd_souple(mnemonique, valeur)
    if cd is None:
        connues = ", ".join(resolver.valeurs(mnemonique)[:8])
        raise ValueError(
            f"{reglage} = « {valeur} » est introuvable dans le référentiel "
            f"{mnemonique} de cette instance. Employez un cd_nomenclature ou un libellé "
            f"exact." + (f" Valeurs connues : {connues}…" if connues else ""))
    return cd


@dataclass
class Prevalidation:
    """Statut de validation appliqué d'office aux données importées.

    Porte les deux formes du même statut parce que la Synthèse et l'historique ne
    parlent pas la même langue : la chaîne de transformation travaille en
    `cd_nomenclature`, `gn_commons.t_validations` veut un `id_nomenclature`.

    `ecrites` compte les lignes d'historique réellement écrites, lot après lot. Muté
    plutôt que retourné : l'objet traverse déjà toute la chaîne d'écriture, et faire
    remonter un troisième nombre par les cinq chemins d'appel de `insert_batch` aurait
    coûté plus cher que le renseignement ne vaut. Le compte importe surtout au premier
    passage — il dit si le réglage a pris — et aux suivants, où il doit tomber à zéro.
    """

    cd: str
    id_statut: int
    commentaire: str
    ecrites: int = 0


def prevalidation(cfg_validation: dict, resolver: Resolver) -> Prevalidation | None:
    """Lit `[validation]` et rend le statut à appliquer, ou None si désactivé.

    ⚠ Lève `ValueError` plutôt que de retomber sur le défaut de la colonne. Le repli
    silencieux est exactement ce qui rendait ce réglage inopérant : `status = "Probable"`
    était passé à `get_id_nomenclature`, qui attend un code et non un libellé, rendait
    NULL, et toutes les données ressortaient en « Non évalué » — sans un mot. Activer la
    pré-validation est un geste explicite ; son échec doit l'être aussi.
    """
    if not cfg_validation.get("enabled"):
        return None
    brut = str(cfg_validation.get("status") or "").strip()
    if not brut:
        raise ValueError("[validation] enabled = true mais status est vide.")
    cd = resolver.cd_souple("STATUT_VALID", brut)
    if cd is None:
        raise ValueError(
            f"[validation] status = {brut!r} est introuvable dans STATUT_VALID. "
            f"Valeurs de l'instance : {', '.join(resolver.valeurs('STATUT_VALID'))}.")
    id_statut = resolver.id_souple("STATUT_VALID", cd)
    if id_statut is None:                      # inatteignable, mais la suite en dépend
        raise ValueError(f"STATUT_VALID {cd!r} sans id_nomenclature.")
    return Prevalidation(cd=cd, id_statut=id_statut,
                         commentaire=str(cfg_validation.get("comment") or "").strip())
