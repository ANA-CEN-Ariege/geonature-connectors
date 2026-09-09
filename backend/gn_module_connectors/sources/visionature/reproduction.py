"""Statut de reproduction des taxons **non-oiseaux**.

Les codes atlas EOAC (`nomenclatures.ATLAS_REPRODUCTION_MIN`) ne concernent que les
oiseaux. Pour tout le reste — amphibiens, reptiles, mammifères, chiroptères, odonates,
orthoptères, papillons — VisioNature exprime la reproduction autrement : par la classe
d'âge (`details[].age`), le sexe (`details[].sex`) et le comportement
(`behaviours[].@id`), dont le sens dépend du **groupe taxonomique**. Une larve est un
indice certain chez les odonates ; « juvénile » ne veut rien dire chez un adulte de
papillon, qui n'a pas ce stade.

Sans ce module, `STATUT_BIO` restait au défaut « Non renseigné » pour tous les
non-oiseaux : un trou majeur, la reproduction des amphibiens et des chiroptères étant
une donnée centrale pour un gestionnaire d'espaces naturels.

La table de correspondance est transposée de `gn_vn2synthese` v1.6.0 (LPO AuRA),
`ref_nomenclatures.t_c_vn_repro_matching_values` — 107 lignes, sept groupes — dont c'est
le seul endroit où elle existe sous forme lisible.

Trois écarts assumés avec le témoin, tous documentés à leur point d'application :
le typage des valeurs, la portée par observateur, et la résolution déterministe de la
priorité.
"""

from collections import Counter
from dataclasses import dataclass, field

from .nomenclatures import valeur_simple

# ── Degrés ───────────────────────────────────────────────────────────────────
# Vocabulaire de `gn_vn2synthese`, conservé tel quel pour que la comparaison avec le
# témoin reste possible. L'ordre EST la priorité : le degré le plus fort l'emporte quand
# plusieurs indices coexistent dans un même relevé.
CERTAIN = "certain"
PROBABLE = "probable"
POSSIBLE = "possible"
INCONNU = "inconnu"

PRIORITE = {CERTAIN: 1, PROBABLE: 2, POSSIBLE: 3, INCONNU: 4}

# ⚠ Le SINP ne gradue pas la reproduction : `STATUT_BIO` n'offre que « Reproduction ».
# Les trois degrés y sont donc versés indistinctement — exactement comme les codes atlas
# des oiseaux. Le degré est conservé dans `additional_data`, sans quoi l'information
# serait perdue alors qu'elle est le cœur de la donnée pour un atlas ou un suivi.
DEGRES_REPRODUCTEURS = (CERTAIN, PROBABLE, POSSIBLE)

# ── Correspondances ──────────────────────────────────────────────────────────
# Transposition intégrale de `t_c_vn_repro_matching_values` (v1.6.0). Le libellé en
# commentaire est celui de la colonne `label` : il est la seule documentation existante
# des énumérations VisioNature, que l'API n'expose qu'instance par instance.
#
# Les codes de degré `INCONNU` ne produisent jamais rien. Ils sont conservés — et c'est
# un choix, LPO les stocke aussi — parce qu'ils disent « ce code a été examiné et ne
# prouve rien », ce qu'une absence de ligne ne dirait pas. Ils servent en outre à
# distinguer un code neutre d'un code que le module ne connaît pas : voir `Contexte`.
#
# ⚠ Contrairement au témoin, l'appariement est **typé** : un code d'âge n'est confronté
# qu'aux règles d'âge. `fct_c_get_reproduction_status` aplatit âges, sexes et
# comportements dans un seul tableau et perd la colonne `data_type` au moment du
# `&&`. Aucune collision n'existe dans les données actuelles, mais une instance qui
# ajouterait localement un code d'âge homonyme d'un code de sexe verrait la règle de
# l'un s'appliquer à l'autre, en silence.
REGLES: dict[str, dict[str, dict[str, str]]] = {
    # Chiroptères. La femelle gestante n'y vaut que « possible » alors qu'elle vaut
    # « certain » chez les autres mammifères — asymétrie du témoin, reprise telle quelle
    # puisqu'elle est sans effet sur le SINP, qui ne gradue pas.
    "TAXO_GROUP_BAT": {
        "age": {
            "YOUNGHAIRY": CERTAIN,            # jeune velu non volant
            "ADYOUNG": PROBABLE,              # adulte et jeune
            "AD": INCONNU,                    # adulte
            "SUBAD": INCONNU,                 # subadulte
            "U": INCONNU,                     # inconnu
            "YOUNGFLYING": INCONNU,           # jeune volant
        },
        "sex": {
            "FG": POSSIBLE,                   # femelle gestante
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "MF": INCONNU,                    # mâle et femelle
            "U": INCONNU,                     # inconnu
        },
        # ⚠ Trou du témoin : la règle « jeune non velu » y porte `value = NULL`, donc
        # n'apparie jamais rien — un `&&` avec un NULL ne renvoie pas vrai. Le code
        # VisioNature correspondant n'est pas déductible du script ; l'inventer
        # produirait une règle fausse. À renseigner par configuration si l'instance
        # l'emploie, ce qui est probable en colonie de mise bas.
    },
    "TAXO_GROUP_MAMMAL": {
        "age": {
            "YOUNGDEP": CERTAIN,              # jeune dépendant
            "IMM": POSSIBLE,                  # immature
            "AD": INCONNU,                    # adulte
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": CERTAIN,                    # femelle gestante
            "MF": POSSIBLE,                   # mâle et femelle
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_22": PROBABLE,               # Rut, parade
            "134_3": PROBABLE,                # Accouplement
            "134_21": POSSIBLE,               # Marquage de territoire
            "134_10": INCONNU,                # Se nourrit
            "134_15": INCONNU,                # Prédaté
            "134_23": INCONNU,                # Sous une plaque
            "134_5": INCONNU,                 # Se déplace
        },
    },
    "TAXO_GROUP_REPTILIAN": {
        "age": {
            "EGG": CERTAIN,                   # oeuf / ponte
            "JUVENILE": PROBABLE,             # juvénile
            "AD": INCONNU,                    # adulte
            "SUBAD": INCONNU,                 # subadulte
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": CERTAIN,                    # femelle gestante
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "MF": INCONNU,                    # mâle et femelle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_4": CERTAIN,                 # Pond
            "134_3": PROBABLE,                # Accouplement
            "134_15": INCONNU,                # Prédaté
            "134_20": INCONNU,                # Prend le soleil
            "134_23": INCONNU,                # Sous une plaque
        },
    },
    "TAXO_GROUP_AMPHIBIAN": {
        "age": {
            "EGG": CERTAIN,                   # oeuf / ponte
            "TETARD": CERTAIN,                # larve/tétard
            "JUVENILE": PROBABLE,             # juvénile
            "AD": INCONNU,                    # adulte
            "SUBAD": INCONNU,                 # subadulte
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": CERTAIN,                    # femelle gestante
            "MF": POSSIBLE,                   # mâle et femelle
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_4": CERTAIN,                 # Pond
            "134_3": PROBABLE,                # Accouplement
            "134_15": INCONNU,                # Prédaté
            "134_20": INCONNU,                # Prend le soleil
            "134_23": INCONNU,                # Sous une plaque
        },
    },
    "TAXO_GROUP_ODONATA": {
        "age": {
            "EMERGENT": CERTAIN,              # individu émergent
            "EXUVIE": CERTAIN,                # exuvie
            "LARVA": CERTAIN,                 # larve
            "IMM": POSSIBLE,                  # immature
            "MATURE": INCONNU,                # individu mature
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": PROBABLE,                   # femelle gestante
            "MF": POSSIBLE,                   # mâle et femelle
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_4": CERTAIN,                 # Pond
            "134_6": CERTAIN,                 # Emergence
            "134_2": PROBABLE,                # Tandem
            "134_3": PROBABLE,                # Accouplement
            "134_1": POSSIBLE,                # Territorial
            "134_15": INCONNU,                # Prédaté
            "134_9": INCONNU,                 # Migration
        },
    },
    "TAXO_GROUP_BUTTERFLY": {
        "age": {
            "CAT": CERTAIN,                   # chenille
            "CHRY": CERTAIN,                  # chrysalide
            "EGG": CERTAIN,                   # oeuf / ponte
            "AD": INCONNU,                    # adulte
            "IMAGO": INCONNU,                 # imago
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": PROBABLE,                   # femelle gestante
            "MF": POSSIBLE,                   # mâle et femelle
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_4": CERTAIN,                 # Pond
            "134_3": PROBABLE,                # Accouplement
            "134_1": POSSIBLE,                # Territorial
            "134_10": INCONNU,                # Se nourrit
            "134_15": INCONNU,                # Prédaté
            "134_9": INCONNU,                 # Migration
        },
    },
    "TAXO_GROUP_ORTHOPTERA": {
        "age": {
            "JUVENILE": CERTAIN,              # juvénile
            "AD": INCONNU,                    # adulte
            "U": INCONNU,                     # inconnu
        },
        "sex": {
            "FG": PROBABLE,                   # femelle gestante
            "MF": POSSIBLE,                   # mâle et femelle
            "F": INCONNU,                     # femelle
            "M": INCONNU,                     # mâle
            "U": INCONNU,                     # inconnu
        },
        "behaviour": {
            "134_4": CERTAIN,                 # Pond
            "134_3": PROBABLE,                # Accouplement
            "134_15": INCONNU,                # Prédaté
        },
    },
}

# Groupes couverts par les codes atlas plutôt que par la table ci-dessus. Les y faire
# figurer avec un dictionnaire vide serait ambigu ; les nommer ici est explicite.
GROUPES_ATLAS = ("TAXO_GROUP_BIRD",)

# ── Identification du groupe ─────────────────────────────────────────────────
# `gn_vn2synthese` compare directement l'entier `species.taxonomy` à `group_taxo_id`.
# On préfère le **code** du groupe (`TAXO_GROUP_BAT`…), que le contrôleur `taxo_groups`
# expose dans son champ `name` : c'est lui qui est stable d'une instance à l'autre, alors
# que rien ne garantit la stabilité des identifiants numériques — le fichier de
# configuration de `gn_vn2synthese` désigne d'ailleurs lui-même les groupes par leur nom,
# et ses unités territoriales par leur `short_name` « et non l'identifiant ».
#
# Repli quand l'index de l'instance n'a pas pu être construit. Seuls 1 et 6 sont
# vérifiés sur des données réelles (Faune-Occitanie : `taxonomy = 1` sur un Faucon
# pèlerin, `taxonomy = 6` sur Podarcis muralis, Tarentola mauritanica et Zamenis
# longissimus) ; les autres sont déduits des libellés du témoin.
GROUPES_PAR_DEFAUT = {
    1: "TAXO_GROUP_BIRD",
    2: "TAXO_GROUP_BAT",
    3: "TAXO_GROUP_MAMMAL",
    6: "TAXO_GROUP_REPTILIAN",
    7: "TAXO_GROUP_AMPHIBIAN",
    8: "TAXO_GROUP_ODONATA",
    9: "TAXO_GROUP_BUTTERFLY",
    11: "TAXO_GROUP_ORTHOPTERA",
}


def index_groupes(groupes: list[dict]) -> dict[str, str]:
    """Index identifiant -> code, depuis la réponse du contrôleur `taxo_groups`."""
    index = {}
    for g in groupes or []:
        identifiant = str(g.get("id") or g.get("@id") or "").strip()
        code = str(g.get("name") or "").strip()
        if identifiant and code:
            index[identifiant] = code
    return index


def code_groupe(identifiant, index: dict[str, str] | None = None) -> str | None:
    """Code du groupe taxonomique, depuis un identifiant d'instance ou un code déjà résolu."""
    brut = valeur_simple(identifiant)
    if not brut:
        return None
    if brut.startswith("TAXO_GROUP_"):
        return brut
    if index and brut in index:
        return index[brut]
    try:
        return GROUPES_PAR_DEFAUT.get(int(brut))
    except ValueError:
        return None


def valeurs(observation: dict) -> dict[str, list[str]]:
    """Codes d'âge, de sexe et de comportement portés par une observation.

    ⚠ Portée volontairement plus étroite que celle du témoin, qui interroge
    `$.observers[*]` — donc **tous** les observateurs du relevé. Comme notre unité est le
    couple (relevé, observateur) produit par `transform.deplier`, ratisser tout le relevé
    attribuerait à l'un le juvénile noté par l'autre. Chaque ligne de Synthèse ne répond
    ainsi que de ce que son propre observateur a saisi.
    """
    ages, sexes = [], []
    for detail in observation.get("details") or []:
        if not isinstance(detail, dict):
            continue
        age = valeur_simple(detail.get("age"))
        sexe = valeur_simple(detail.get("sex"))
        if age:
            ages.append(age)
        if sexe:
            sexes.append(sexe)
    comportements = [c for c in (valeur_simple(b)
                                 for b in observation.get("behaviours") or []) if c]
    return {"age": ages, "sex": sexes, "behaviour": comportements}


def fusionner(surcharges: dict | None, base: dict | None = None) -> dict:
    """Table de correspondance surchargée par la configuration.

    La fusion est faite par groupe et par type de valeur, pas par remplacement global :
    ajouter une règle pour les chiroptères ne doit pas effacer celles des amphibiens.
    """
    fusion = {g: {t: dict(codes) for t, codes in types.items()}
              for g, types in (base if base is not None else REGLES).items()}
    for groupe, types in (surcharges or {}).items():
        cible = fusion.setdefault(str(groupe).strip().upper(), {})
        for type_valeur, codes in (types or {}).items():
            cible.setdefault(str(type_valeur), {}).update(
                {str(code): str(degre).strip().lower() for code, degre in (codes or {}).items()}
            )
    return fusion


@dataclass
class Contexte:
    """Ce qu'il faut savoir pour interpréter un relevé, monté une fois par moissonnage.

    Regroupé en un objet unique plutôt qu'en quatre paramètres de `to_row` : la signature
    est déjà longue, et l'index des groupes comme la table de règles sont invariants pour
    tout l'import.
    """

    index: dict[str, str] = field(default_factory=dict)
    regles: dict = field(default_factory=lambda: REGLES)
    # Groupe du lot en cours de moissonnage, employé quand le relevé ne porte pas
    # lui-même `species.taxonomy`.
    groupe_courant: str | int | None = None
    active: bool = True
    # Codes rencontrés qu'aucune règle ne connaît, par « groupe/type:code ». Un code
    # inconnu n'est pas une erreur — l'énumération VisioNature est localement
    # extensible — mais c'est le seul signal disponible qu'une règle manque. Le témoin
    # ne le produit pas : chez lui, un code inconnu et un code neutre sont
    # indiscernables, et une lacune de correspondance reste invisible.
    inconnus: Counter = field(default_factory=Counter)


@dataclass
class Analyse:
    """Résultat de l'examen d'une observation."""

    degre: str | None = None
    # Ce qui a emporté la décision, pour pouvoir la justifier a posteriori.
    indice: str | None = None

    @property
    def reproduction(self) -> bool:
        return self.degre in DEGRES_REPRODUCTEURS


def analyser(sighting: dict, observation: dict,
             contexte: Contexte | None = None) -> Analyse:
    """Degré de reproduction déduit de l'âge, du sexe et du comportement.

    Le groupe taxonomique est lu sur le relevé (`species.taxonomy`) et non sur le lot
    moissonné : c'est ce que fait le témoin, et c'est plus sûr — un formulaire peut
    contenir des relevés d'un autre groupe que celui demandé.
    """
    contexte = contexte or Contexte()
    if not contexte.active:
        return Analyse()
    groupe = code_groupe((sighting.get("species") or {}).get("taxonomy"), contexte.index)
    if groupe is None:
        groupe = code_groupe(contexte.groupe_courant, contexte.index)
    regles = (contexte.regles or {}).get(groupe or "")
    if not regles:
        return Analyse()

    meilleur = None
    for type_valeur, codes in valeurs(observation).items():
        connus = regles.get(type_valeur) or {}
        for code in codes:
            degre = connus.get(code)
            if degre is None:
                contexte.inconnus[f"{groupe}/{type_valeur}:{code}"] += 1
                continue
            # ⚠ Résolution explicite du minimum, là où le témoin s'en remet à un
            # `ORDER BY` placé dans une CTE puis à un `LIMIT 1` sans tri : PostgreSQL
            # ne garantit pas qu'un tri interne à une CTE survive à la requête
            # englobante. Le témoin est donc juste par accident de plan.
            rang = PRIORITE.get(degre, PRIORITE[INCONNU])
            if meilleur is None or rang < meilleur[0]:
                meilleur = (rang, degre, f"{type_valeur}:{code}")
    if meilleur is None:
        return Analyse()
    return Analyse(degre=meilleur[1], indice=meilleur[2])
