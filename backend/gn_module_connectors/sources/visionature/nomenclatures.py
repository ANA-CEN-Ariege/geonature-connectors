"""Correspondances entre les champs VisioNature et les nomenclatures SINP.

Les valeurs sont des `cd_nomenclature`, jamais des `id_nomenclature` : ces derniers
varient d'une instance GeoNature à l'autre. Le connecteur autonome antérieur les codait
en dur (`73`, `154`, `58`), ce qui le rendait faux sur toute autre base.

`None` signifie « pas d'information exploitable » et laisse jouer le défaut de la colonne.
"""

# ── Codes atlas de nidification ──────────────────────────────────────────────
# Codes EOAC, communs aux instances VisioNature. Trois degrés :
#   1        nidification possible   (présence en période et milieu favorables)
#   2 à 9    nidification probable   (chant, couple, territoire, parade, nid en cours)
#   10 à 19  nidification certaine   (nid occupé, jeunes, transport de nourriture)
#
# ⚠ Le SINP ne connaît pas cette gradation : `STATUT_BIO` n'offre que « Reproduction »
# sans degré. Les codes 2 et au-delà y sont donc versés, et le code atlas brut est
# conservé dans `additional_data` — sans quoi l'information de degré serait perdue,
# alors qu'elle est le cœur de la donnée pour un atlas ornithologique.
#
# ⚠ Le code 1 n'est PAS versé en « Reproduction » : « vu en période de nidification dans
# un milieu favorable » ne constitue aucun indice de reproduction, seulement une présence.
#
# ⚠ Les codes atlas ne concernent que **les oiseaux**. Pour les autres groupes,
# VisioNature exprime la reproduction autrement — `gn_vn2synthese` interroge à cet effet
# le groupe taxonomique et le champ `details[].condition`. Ce repli n'est pas encore
# implémenté ici : les non-oiseaux restent donc au défaut « Non renseigné ».
#
# À vérifier sur votre instance : le contrôleur `fields` de l'API expose la liste réelle
# des codes (`FieldsAPI`), qui peut être localement enrichie.
ATLAS_REPRODUCTION_MIN = 2

# Code d'absence. Repris de `gn_vn2synthese` (LPO AuRA), qui le traite depuis des années
# en production : `atlas_code = 99` signale que l'espèce a été **recherchée sans être
# trouvée**. Sans ce cas, une absence avérée serait versée en présence — la même erreur
# que les 118 986 absences du corpus GBIF, mais en silence puisque rien ne la distingue.
ATLAS_ABSENCE = 99

# Quelques codes décrivent un comportement que le SINP sait nommer. Les autres n'ont pas
# d'équivalent : mieux vaut laisser le défaut que forcer une approximation.
ATLAS_VERS_COMPORTEMENT = {
    2: "18",    # mâle chanteur, cris nuptiaux            -> Chant
    5: "19",    # parades, copulation                     -> Accouplement
    4: "22",    # comportement territorial                -> Territorial
    15: "14",   # transport de nourriture pour les jeunes -> Nourrissage des jeunes
    16: "14",   # transport de sacs fécaux                -> Nourrissage des jeunes
}

# ── Origine de la donnée ─────────────────────────────────────────────────────
# VisioNature est un outil de saisie naturaliste de terrain : l'origine ne fait pas
# de doute, contrairement à GBIF qui agrège aussi des collections et de la littérature.
STATUT_SOURCE = "Te"          # Terrain

# ⚠ `ETA_BIO` reste au défaut « Non renseigné ». Le connecteur antérieur imposait
# « Observé vivant », ce qui est faux pour les données de mortalité — collisions
# routières, prédation — que VisioNature sait justement enregistrer.
ETA_BIO = None

# ⚠ `METH_OBS` reste au défaut « Inconnu » : l'API n'expose pas la technique
# d'observation. La déduire serait l'inventer.
METH_OBS = None


def _entier(valeur) -> int | None:
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def code_atlas(observation: dict) -> int | None:
    """Code atlas d'une observation, quel que soit la forme renvoyée par l'API.

    Biolovision renvoie tantôt `{"@id": "3", "#text": "Couple"}`, tantôt une valeur
    simple selon le point d'entrée et la version.
    """
    brut = observation.get("atlas_code")
    if isinstance(brut, dict):
        brut = brut.get("@id") or brut.get("#text")
    return _entier(brut)


def est_absence(observation: dict, code_absence: int = None) -> bool:
    """L'observation constate-t-elle une absence ?

    Deux formes, l'une et l'autre issues de la pratique de `gn_vn2synthese` :
    le code atlas 99, et un effectif nul explicitement déclaré exact. Un effectif nul
    **sans** `estimation_code = EXACT_VALUE` ne prouve rien — c'est une donnée
    incomplète, pas une absence constatée.
    """
    if code_atlas(observation) == (ATLAS_ABSENCE if code_absence is None else code_absence):
        return True
    effectif = str(observation.get("count") or "").strip()
    estimation = str(observation.get("estimation_code") or "").upper()
    return effectif == "0" and "EXACT_VALUE" in estimation


def denombrement(observation: dict) -> tuple[str | None, str | None]:
    """(OBJ_DENBR, TYP_DENBR) d'après l'effectif et le mode d'estimation.

    VisioNature distingue le comptage exact de l'estimation via `estimation_code` :
    « EXACT_VALUE », « ESTIMATION », « MINIMUM ». Confondre les deux ferait passer une
    estimation pour un dénombrement, ce qui fausse toute analyse quantitative.
    """
    effectif = _entier(observation.get("count"))
    if not effectif:
        return (None, None)
    estimation = str(observation.get("estimation_code") or "").upper()
    if "ESTIM" in estimation or "MIN" in estimation:
        return ("IND", "Es")      # Individu, Estimé
    return ("IND", "Co")          # Individu, Compté


def cd_nomenclatures(sighting: dict, observation: dict,
                     surcharges: dict | None = None) -> dict[str, str | None]:
    """Correspondances SINP d'une observation, en cd_nomenclature.

    `sighting` porte l'espèce et la date, `observation` l'observateur, la position et
    les effectifs — c'est le couple produit par le dépliage des relevés VisioNature.

    `surcharges` permet d'adapter la correspondance sans toucher au code. `gn_vn2synthese`
    stocke la sienne dans une table de synonymes, administrable en SQL ; faute de vouloir
    étendre le schéma, on passe ici par la configuration du module. Moins souple, mais
    l'essentiel est préservé : un atlas régional qui emploie ses propres codes n'a pas à
    attendre une nouvelle version du connecteur.

        [visionature.atlas]
        reproduction_min = 2
        absence = 99
        comportement = { 2 = "18", 5 = "19" }
    """
    surcharges = surcharges or {}
    seuil = surcharges.get("reproduction_min", ATLAS_REPRODUCTION_MIN)
    absence = surcharges.get("absence", ATLAS_ABSENCE)
    comportements = {
        int(k): str(v) for k, v in (surcharges.get("comportement")
                                    or ATLAS_VERS_COMPORTEMENT).items()
    }
    atlas = code_atlas(observation)
    obj_denbr, typ_denbr = denombrement(observation)

    statut_bio = None
    comportement = None
    # 99 est un code d'absence, pas un degré de nidification : l'exclure de la
    # comparaison numérique, sans quoi il vaudrait « Reproduction certaine ».
    if atlas is not None and atlas != absence:
        if atlas >= seuil:
            statut_bio = "3"      # Reproduction
        comportement = comportements.get(atlas)

    return {
        "STATUT_SOURCE": STATUT_SOURCE,
        "ETA_BIO": ETA_BIO,
        "METH_OBS": METH_OBS,
        "STATUT_BIO": statut_bio,
        "OCC_COMPORTEMENT": comportement,
        "OBJ_DENBR": obj_denbr,
        "TYP_DENBR": typ_denbr,
        "STATUT_OBS": "No" if est_absence(observation, absence) else "Pr",
        # VisioNature ne livre ni sexe, ni stade de vie, ni preuve d'existence dans le
        # noyau de l'API : ces colonnes restent au défaut.
    }
