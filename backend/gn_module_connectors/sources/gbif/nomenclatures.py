"""Correspondances entre les vocabulaires GBIF et les nomenclatures SINP.

Les valeurs sont des `cd_nomenclature`, jamais des `id_nomenclature` : ces derniers
varient d'une instance GeoNature à l'autre. `None` signifie « pas d'information
exploitable » et laisse jouer le défaut de la colonne.

Volumes indiqués : mesurés sur l'Ariège le 2026-09-08
(`gadmGid=FRA.11.1_1&occurrenceStatus=PRESENT`, 1 190 194 occurrences).

Taux de renseignement mesurés — ils relativisent l'enjeu de chaque champ :
`basisOfRecord` et `occurrenceStatus` 100 %, `coordinateUncertaintyInMeters` ~82 %,
`individualCount` ~23 %, `establishmentMeans` 3,6 %, `lifeStage` 3,3 %, `sex` 0,97 %.
"""

# ── Périmètre ────────────────────────────────────────────────────────────────
# Collections ex-situ et paléontologie : ce ne sont pas des observations
# naturalistes, et leurs coordonnées ne désignent pas un lieu d'observation.
# Vérifié en Ariège : les LIVING_SPECIMEN sont des accessions de la banque de
# semences Kokopelli, géoréférencées au siège de la banque ; parmi les
# FOSSIL_SPECIMEN, des coquillages antillais (locality « St. Croix ») placés à Foix.
BASIS_OF_RECORD_EXCLUS = frozenset({"FOSSIL_SPECIMEN", "LIVING_SPECIMEN"})


# ── basisOfRecord → METH_OBS / ETA_BIO / STATUT_SOURCE ───────────────────────
# METH_OBS reste « 21 » (Inconnu) partout : GBIF ne livre aucune technique
# d'observation exploitable. Mapper HUMAN_OBSERVATION vers « Vu » serait inventer.
# ETA_BIO reste au défaut « 1 » (Non renseigné), littéralement exact puisque GBIF
# ne renseigne pas l'état biologique.
BASIS_OF_RECORD = {
    #                        METH_OBS  ETA_BIO  STATUT_SOURCE        volume Ariège
    "HUMAN_OBSERVATION":     ("21",    None,    "Te"),             # 1 125 465
    "MACHINE_OBSERVATION":   ("21",    None,    "Te"),             #    47 785
    "OBSERVATION":           ("21",    None,    "Te"),             #         3 (4,7 M en France)
    "OCCURRENCE":            ("21",    None,    "NSP"),            #     9 639 — DwC : « valeur ambiguë »
    "PRESERVED_SPECIMEN":    ("21",    None,    "Co"),             #     5 611
    "MATERIAL_SAMPLE":       ("21",    None,    "Co"),             #       968 — iBOL, PAS de l'ADN environnemental
    "MATERIAL_CITATION":     ("21",    None,    "Li"),             #       458
    "LITERATURE":            ("21",    None,    "Li"),             #         0 (valeur GBIF héritée)
    "FOSSIL_SPECIMEN":       ("21",    "3",     "Co"),             #       247 — si l'exclusion est levée
    "LIVING_SPECIMEN":       ("21",    "2",     "Co"),             #        18 — idem
    "UNKNOWN":               ("21",    None,    "NSP"),
}
BASIS_OF_RECORD_DEFAUT = ("21", None, "NSP")


# ── occurrenceStatus → STATUT_OBS ────────────────────────────────────────────
# STATUT_OBS n'a pas de valeur « Absent » : l'équivalent SINP est « Non observé »,
# dont la définition (« recherché suivant le protocole adéquat ») correspond à la
# sémantique DwC d'ABSENT.
# ⚠ Le DEFAULT de la colonne est « Pr » (Présent). Sans ce mapping explicite, si le
# filtre occurrenceStatus=PRESENT venait à sauter, les 118 986 absences ariégeoises
# entreraient en Synthèse comme des présences.
OCCURRENCE_STATUS = {"PRESENT": "Pr", "ABSENT": "No"}


# ── lifeStage → STADE_VIE ────────────────────────────────────────────────────
# ⚠ FAUX-AMI : `Nymph` et « Nymphe » désignent des stades OPPOSÉS du cycle.
#   GBIF `Nymph`  = immature des hémimétaboles (punaise, criquet, libellule).
#   SINP « Nymphe » (cd 13) = « stade intermédiaire entre larve et imago, pendant
#   lequel l'individu ne se nourrit pas » — c'est la pupe des holométaboles.
#   Confirmé par les nomenclatures voisines : cd 12 « Chrysalide » = nymphe des
#   lépidoptères, cd 14 « Pupe » = nymphe des diptères.
#   → `Nymph` vaut « 6 » (Larve) ; c'est `Pupa` qui vaut « 13 ».
#   552 observations ariégeoises concernées, dont 551 Insecta.
#
# ⚠ `Seedling` : « 20 » (Graine) est faux — la graine précède la germination, la
#   plantule la suit. « 18 » (Germination) est l'approximation la moins mauvaise ;
#   le SINP n'a pas de valeur « plantule ». 23 observations.
LIFE_STAGE = {
    "Adult": "2", "Mature": "2",          # 16 411  Adulte
    "Imago": "15",                        #  9 619
    "Unknown": "0",                       #  9 247  Inconnu
    "Juvenile": "3",                      #  1 510
    "Larva": "6",                         #    683
    "Nymph": "6",                         #    552  ⚠ faux-ami, voir ci-dessus
    "Caterpillar": "7",                   #     80  Chenille
    "Subadult": "5",                      #     30  Sub-adulte
    "Seedling": "18",                     #     23  ⚠ approximation assumée
    "Egg": "9",                           #     21  Œuf
    "Immature": "4",                      #     13
    "Pupa": "13",                         #     11  Nymphe (mapping exact)
    # Absents d'Ariège, non mesurés.
    "Tadpole": "8", "Hatchling": "25",
    "Chick": "3", "Nestling": "3", "Fledgling": "3", "Neonate": "3", "Eft": "3",
    # Stades larvaires de crustacés et acariens : tous enfants de Larva chez GBIF.
    "Nauplius": "6", "Metanauplius": "6", "Zoea": "6", "Megalopa": "6",
    "Veliger": "6", "Cyprid": "6", "Furcilia": "6", "Calyptopsis": "6",
    "Deutonymph": "6",
    # Sans équivalent SINP, volontairement absents (→ défaut « 0 ») :
    #   Embryo, Fetus, Zygote, Medusa, Polyp, Cyst, Gametophyte
}

# Trois valeurs de `lifeStage` décrivent un état phénologique, pas un stade de vie.
# La définition SINP de STATUT_BIO « 3 » cite littéralement « floraison,
# fructification ». Pour celles-ci, STADE_VIE reste au défaut.
# Note : « Fruiting » ne va PAS vers STADE_VIE « 27 » (Fruit), qui signifie que
# l'objet observé EST un fruit, non qu'une plante est en fructification.
LIFE_STAGE_VERS_STATUT_BIO = {
    "Flowering": "3", "Fruiting": "3",    # 1 174 + 56  Reproduction
    "Vegetative": "13",                   #   129       Végétatif
}


# ── sex → SEXE ───────────────────────────────────────────────────────────────
# Le défaut « 6 » (Non renseigné) est déjà exact pour les 99 % sans `sex`.
# ⚠ `Other` n'a aucune correspondance SINP : « 4 » (Hermaphrodite) serait une
# sur-interprétation — GBIF y range aussi les castes neutres d'hyménoptères — et
# « 1 » (Indéterminé) serait faux, l'information existant. On laisse le défaut et
# la valeur brute reste dans additional_data.
SEX = {
    "Female": "2",          # 4 489
    "Indeterminate": "1",   # 4 080
    "Male": "3",            # 2 924
    "Mixed": "5",           #     6
    "Other": None,          #     0  ⚠ correspondance impossible
}


# ── establishmentMeans / degreeOfEstablishment ───────────────────────────────
# Deux axes distincts, à ne pas confondre :
#   STAT_BIOGEO = d'où vient le TAXON      → establishmentMeans (+ affinage)
#   NATURALITE  = état de l'INDIVIDU observé → degreeOfEstablishment
# Mapper establishmentMeans vers NATURALITE écrirait « Sauvage » sur les 19 918
# animaux domestiques du jeu de pièges photo pyrénéens.
#
# Seul le vocabulaire contrôlé récent est indexé, en lowerCamelCase : les valeurs
# héritées (NATIVE, INTRODUCED, NATURALISED, INVASIVE…) renvoient zéro résultat.
# La comparaison est faite insensible à la casse par sécurité.
ESTABLISHMENT_MEANS = {
    "native": "2", "nativeendemic": "2",   # 22 452  Présent (indigène ou indéterminé)
    "nativereintroduced": "2",             #      0  ⚠ ambigu
    "introduced": "3",                     # 19 927  Introduit — affiné ci-dessous
    "introducedassistedcolonisation": "3",
    "vagrant": "6",                        #      0  Occasionnel
    "uncertain": "0",                      #      0  Inconnu/cryptogène
}

# Affinage quand establishmentMeans vaut « introduced ». Sans lui, les 19 918
# domestiques seraient « Introduit » au lieu de « Introduit non établi (dont
# domestique) » — la définition SINP de « 5 » dit littéralement « (dont domestique) ».
INTRODUIT_AFFINE_PAR_DEGRE = {
    "captive": "5", "cultivated": "5", "released": "5", "managed": "5",
    "unestablished": "5", "casual": "5", "failing": "5",
    "invasive": "4", "widespreadinvasive": "4", "spreading": "4", "colonising": "4",
    "naturalized": "3", "established": "3", "reproducing": "3",
}

DEGREE_OF_ESTABLISHMENT = {
    "native": "1",       # 22 270  Sauvage
    "captive": "2",      # 19 918  Cultivé/élevé
    "cultivated": "2", "managed": "2",
    "released": "4",     # Féral — approximatif
    # invasive / spreading… relèvent de STAT_BIOGEO, pas de la naturalité.
    # unestablished / casual / failing ne disent rien de l'état de l'individu.
}
# « Subspontané » : la définition SINP précise « Qualifie un végétal ».
DEGREE_OF_ESTABLISHMENT_PLANTES = {
    "naturalized": "5", "established": "5", "reproducing": "5",
}


# ── Preuve d'existence ───────────────────────────────────────────────────────
# Mesuré en Ariège : 51 974 StillImage, 521 Sound, 20 InteractiveResource (4,4 %),
# plus 6 844 occurrences sur spécimen conservé.
# Réserve assumée : la définition SINP de « 1 » exige une preuve « toujours
# accessible », or un lien média GBIF peut être mort.
BASIS_AVEC_SPECIMEN = frozenset({
    "PRESERVED_SPECIMEN", "FOSSIL_SPECIMEN", "LIVING_SPECIMEN", "MATERIAL_SAMPLE",
})


def _bas(valeur) -> str:
    return str(valeur).strip().lower() if valeur else ""


def cd_nomenclatures(occ: dict) -> dict[str, str | None]:
    """Correspondances SINP d'une occurrence, en cd_nomenclature.

    Retourne un dict `{mnémonique: cd_nomenclature ou None}`. `None` = laisser le
    défaut de la colonne.
    """
    basis = occ.get("basisOfRecord") or ""
    meth_obs, eta_bio, statut_source = BASIS_OF_RECORD.get(basis, BASIS_OF_RECORD_DEFAUT)

    life = occ.get("lifeStage")
    stade_vie = LIFE_STAGE.get(life) if life else None
    statut_bio = LIFE_STAGE_VERS_STATUT_BIO.get(life) if life else None

    em = _bas(occ.get("establishmentMeans"))
    de = _bas(occ.get("degreeOfEstablishment"))
    biogeo = ESTABLISHMENT_MEANS.get(em)
    if em.startswith("introduced") and de in INTRODUIT_AFFINE_PAR_DEGRE:
        biogeo = INTRODUIT_AFFINE_PAR_DEGRE[de]

    naturalite = DEGREE_OF_ESTABLISHMENT.get(de)
    if naturalite is None and (occ.get("kingdom") == "Plantae"):
        naturalite = DEGREE_OF_ESTABLISHMENT_PLANTES.get(de)

    effectif = occ.get("individualCount")
    obj_denbr, typ_denbr = ("IND", "Co") if effectif is not None else (None, None)

    medias = occ.get("mediaType") or []
    preuve = "1" if (basis in BASIS_AVEC_SPECIMEN
                     or any(m in ("StillImage", "Sound") for m in medias)) else None

    return {
        "METH_OBS": meth_obs,
        "ETA_BIO": eta_bio,
        "STATUT_SOURCE": statut_source,
        "STATUT_OBS": OCCURRENCE_STATUS.get(occ.get("occurrenceStatus") or ""),
        "STADE_VIE": stade_vie,
        "STATUT_BIO": statut_bio,
        "STAT_BIOGEO": biogeo,
        "NATURALITE": naturalite,
        "OBJ_DENBR": obj_denbr,
        "TYP_DENBR": typ_denbr,
        "PREUVE_EXIST": preuve,
    }
