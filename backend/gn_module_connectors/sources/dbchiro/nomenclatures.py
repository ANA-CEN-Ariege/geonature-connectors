"""Correspondances entre le vocabulaire dbChiro et les nomenclatures SINP.

dbChiro s'appuie sur `dj-sinp-nomenclatures` : ses vocabulaires sont déjà alignés sur le
standard, ce qui change radicalement la nature du travail par rapport à VisioNature. Là
où l'API Biolovision impose de déduire un statut biologique de codes atlas et de classes
d'âge, les méthodes de contact de dbChiro se versent presque telles quelles — `METH_OBS`
possède un code « Ultrasons » et un code « Fèces/Guano/Epreintes » qui correspondent
exactement aux deux modes de détection dominants en chiroptérologie.

Les correspondances sont exprimées en `cd_nomenclature`, jamais en `id_nomenclature` :
c'est `core.nomenclatures.Resolver` qui convertit, une fois, au démarrage.
"""

# ── Méthode d'observation ────────────────────────────────────────────────────
# Les 9 codes de `session.contact`, mesurés sur 8 039 observations : ils couvrent 100 %
# du corpus, sans valeur hors référentiel.
#
# `du` (contact acoustique) est le cas remarquable : METH_OBS distingue « Entendu » (1)
# d'« Ultrasons » (3), et c'est bien le second qui convient à un enregistrement de
# détecteur. `cr` (cri audible) reste « Entendu » — la distinction est réelle en
# chiroptérologie, certaines espèces émettant des cris sociaux perceptibles à l'oreille.
CONTACT_METH_OBS = {
    "vv": "0",   # Vu en visuel                          -> Vu
    "du": "3",   # Contact acoustique (détecteur)        -> Ultrasons
    "cr": "1",   # Cri audible                           -> Entendu
    "vm": "0",   # Vu en main (capture scientifique)     -> Vu
    "gu": "6",   # Guano                                 -> Fèces/Guano/Epreintes
    "ca": "0",   # Cadavre récent                        -> Vu
    "ro": "20",  # Restes osseux                         -> Autre
    "te": "20",  # Télémétrie                            -> Autre
    "nc": "21",  # Inconnu                               -> Inconnu
}

# ── État biologique ──────────────────────────────────────────────────────────
# ETA_BIO : 2 « Observé vivant », 3 « Trouvé mort ».
#
# Deux codes ne reçoivent **aucune valeur**, à dessein. Sur un guano ou des restes
# osseux, l'animal n'a été observé ni vivant ni mort : c'est un indice de présence, et
# la date d'observation est celle de l'indice, pas celle de l'animal. Déclarer « Observé
# vivant » sur un tas de guano serait faux ; déclarer « Trouvé mort » le serait tout
# autant. La colonne retombe sur son défaut, qui dit précisément qu'on ne se prononce pas.
#
# `ro` (restes osseux) fait exception dans l'autre sens : un os est la dépouille d'un
# animal mort, même ancienne. « Trouvé mort » y est exact.
CONTACT_ETA_BIO = {
    "vv": "2",
    "du": "2",
    "cr": "2",
    "vm": "2",
    "te": "2",
    "ca": "3",   # cadavre récent
    "ro": "3",   # restes osseux
    "gu": None,  # indice indirect : ni vivant, ni mort
    "nc": None,
}

# ── Statut de validation ─────────────────────────────────────────────────────
# `is_doubtful` est un drapeau posé par le saisisseur ou le validateur de dbChiro sur
# une détermination qu'il juge incertaine. STATUT_VALID « 3 » (Douteux) le transpose
# exactement. Les observations non douteuses ne reçoivent rien ici : c'est la
# configuration `[validation]` du module qui décide s'il faut pré-valider un import, et
# ce réglage ne doit pas être court-circuité observation par observation.
VALID_DOUTEUX = "3"

# Statut d'observation. Tout ce qui est importé est une présence : les deux codes
# d'absence de dbChiro sont traités en amont, dans `taxonomy`.
STATUT_OBS_PRESENT = "Pr"
STATUT_OBS_ABSENT = "No"

# Reproduction. `breed_colo` est un drapeau explicite « colonie de reproduction »,
# renseigné sur 27,3 % des observations. C'est une constatation de terrain, pas une
# déduction : il alimente STATUT_BIO « 3 » (Reproduction) sans autre condition.
STATUT_BIO_REPRODUCTION = "3"

# Nature de l'objet géographique. Toute observation dbChiro est rattachée à un `place`
# nommé — une cavité, un bâtiment, un arbre-gîte, un point d'écoute. La géométrie décrit
# donc une station, pas un périmètre d'inventaire.
NAT_OBJ_GEO_STATIONNEL = "St"

# Statut de la source. dbChiroWeb est un outil de saisie de terrain : chaque observation
# vient d'une session menée sur un gîte ou un point d'écoute. L'argument est celui de
# VisioNature, qui pose la même constante ; faute de la poser ici, la colonne prenait le
# défaut de la Synthèse — « NSP » (Ne sait pas) —, ce qui est faux et non pas prudent.
STATUT_SOURCE = "Te"          # Terrain

# Objet du dénombrement. `total_count` compte des individus : les `countdetails` (sexe,
# âge, état sexuel) ne sont pas exposés par `/api/v1/search`, mais le total, lui, porte
# bien sur des animaux. Sans cette valeur, un effectif entrait en Synthèse sans qu'on
# sache ce qu'il dénombrait.
#
# ⚠ `TYP_DENBR` reste au défaut, à dessein, et c'est une divergence assumée avec GBIF et
# VisioNature. Un comptage de gîte est souvent une estimation — 200 individus en essaim
# ne se comptent pas un à un — et l'API n'expose aucun équivalent de l'`estimation_code`
# de VisioNature. Écrire « Compté » ferait passer une estimation pour un comptage, ce
# qui fausse précisément les analyses quantitatives que la colonne sert à qualifier.
OBJ_DENBR_INDIVIDU = "IND"


def contact(properties: dict) -> str:
    """Code de méthode de contact d'une observation, normalisé."""
    session = properties.get("session_data") or {}
    bloc = session.get("contact") or {}
    return str(bloc.get("code") or "").strip().lower()


def libelle_contact(properties: dict) -> str:
    """Libellé de la méthode de contact, tel que dbChiro l'exprime."""
    session = properties.get("session_data") or {}
    bloc = session.get("contact") or {}
    return str(bloc.get("descr") or "").strip()


def periode(properties: dict) -> str:
    """Phénologie calculée par dbChiro (« Estivage », « Hibernant », « Transit »…).

    ⚠ **Volontairement non traduite en STATUT_BIO.** Deux raisons, et la seconde est
    la plus importante :

    - le champ est *généré automatiquement* par dbChiro à partir de la date. En faire un
      statut biologique reviendrait à déduire d'un calendrier ce que seul un observateur
      peut constater — exactement l'inférence silencieuse que ce module refuse ailleurs ;
    - « Estivage » n'est **pas** l'estivation du SINP. STATUT_BIO « 5 » désigne une
      dormance estivale ; chez les chiroptères d'Europe, l'été est au contraire la
      période d'activité et de mise bas. La correspondance de vocabulaire la plus
      évidente serait ici un contresens biologique.

    La valeur est conservée dans `additional_data`, où elle reste exploitable sans
    prétendre à un statut qu'elle n'exprime pas. La reproduction, elle, vient de
    `breed_colo`, qui est une observation et non un calcul.
    """
    return str(properties.get("period") or "").strip()


def est_colonie_reproduction(properties: dict) -> bool:
    """`breed_colo` : l'observation porte-t-elle sur une colonie de reproduction ?"""
    return properties.get("breed_colo") is True


def effectif(properties: dict) -> int | None:
    """`total_count` de l'observation, quand dbChiro le renseigne."""
    try:
        return int(str(properties.get("total_count")).strip())
    except (TypeError, ValueError):
        return None


def est_douteuse(properties: dict) -> bool:
    """`is_doubtful` : la détermination est-elle signalée comme incertaine ?"""
    return properties.get("is_doubtful") is True


def cd_nomenclatures(properties: dict, *, absence: bool = False,
                     statut_validation: str | None = None) -> dict[str, str | None]:
    """cd_nomenclature par mnémonique, pour une observation.

    Les mnémoniques absents du résultat retombent sur le défaut de leur colonne, ce que
    `core.nomenclatures.Resolver` fait à partir d'un `None`.
    """
    code = contact(properties)
    valeurs: dict[str, str | None] = {
        "METH_OBS": CONTACT_METH_OBS.get(code),
        "ETA_BIO": CONTACT_ETA_BIO.get(code),
        "STATUT_OBS": STATUT_OBS_ABSENT if absence else STATUT_OBS_PRESENT,
        "STATUT_SOURCE": STATUT_SOURCE,
        "NAT_OBJ_GEO": NAT_OBJ_GEO_STATIONNEL,
        # ⚠ `effectif(properties)` peut valoir 0 : `not effectif(...)` le confondrait
        # avec un effectif absent (même bug déjà corrigé dans les nomenclatures GBIF et
        # VisioNature). Seule une absence de valeur — pas un zéro déclaré — doit laisser
        # la colonne à son défaut.
        "OBJ_DENBR": (None if absence or effectif(properties) is None
                      else OBJ_DENBR_INDIVIDU),
        "STATUT_BIO": (STATUT_BIO_REPRODUCTION
                       if est_colonie_reproduction(properties) else None),
    }
    # Une détermination douteuse prime sur la pré-validation globale : annoncer
    # « Probable » sur une donnée que la source dit incertaine serait la surclasser.
    if est_douteuse(properties):
        valeurs["STATUT_VALID"] = VALID_DOUTEUX
    elif statut_validation:
        valeurs["STATUT_VALID"] = statut_validation
    return valeurs
