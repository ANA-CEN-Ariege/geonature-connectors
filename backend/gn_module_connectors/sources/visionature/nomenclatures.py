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
# ⚠ Les codes atlas ne concernent que **les oiseaux**. Les autres groupes expriment la
# reproduction par l'âge, le sexe et le comportement, dont le sens dépend du groupe
# taxonomique : c'est l'objet de `reproduction.py`, qui prend le relais ici dès que le
# code atlas ne dit rien.
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

# `behaviours[]` porte la même information pour les groupes qui n'ont pas de code atlas.
# Seuls deux comportements sont versés : ceux dont le `cd_nomenclature` SINP est déjà
# employé ci-dessus, donc vérifié. Les autres — « Pond », « Tandem », « Émergence » —
# n'ont pas d'équivalent dont on soit sûr, et inventer un code produirait une valeur
# fausse mais silencieuse, le résolveur retombant sur le défaut sans rien signaler.
# `gn_vn2synthese` ne renseigne pas du tout OCC_COMPORTEMENT : mesuré sur un export réel
# de leur Synthèse, 95 982 lignes de reptiles, toutes à « Inconnu ».
COMPORTEMENT_VN = {
    "134_3": "19",   # Accouplement -> Accouplement
    "134_1": "22",   # Territorial  -> Territorial
}

# ── Origine de la donnée ─────────────────────────────────────────────────────
# VisioNature est un outil de saisie naturaliste de terrain : l'origine ne fait pas
# de doute, contrairement à GBIF qui agrège aussi des collections et de la littérature.
STATUT_SOURCE = "Te"          # Terrain

# ── État biologique (ETA_BIO) ────────────────────────────────────────────────
# VisioNature enregistre la mortalité dans un bloc dédié :
# `observers[0].extended_info.mortality`, avec `death_cause2` (ROAD_VEHICLE, ELECTRIC,
# EOLIEN, POISONING, HUNTING, PREDATION, UNKNOWN…), `wounded`, et selon la cause
# `road_type2` ou `predation2`.
#
# `gn_vn2synthese` v1.6.0 (08_upsert_observations.sql:242-254) : présence de la clé
# `mortality` -> ETA_BIO « 3 » (Trouvé mort) ; sinon absence constatée -> « 1 » (Non
# observé) ; sinon « 2 » (Observé vivant). La branche « 1 » est récente chez eux
# (CHANGELOG 1.5.2, 2025-05-06 : « Fix 'ETA_BIO' status for absence data »).
#
# Mesuré sur 338 observations réelles exportées de Faune-LR : 12 portent un bloc
# `mortality` (3,6 %), toutes avec `wounded = "0"`.
ETA_BIO_MORT = "3"        # Trouvé mort
ETA_BIO_VIVANT = "2"      # Observé vivant
ETA_BIO_NON_OBSERVE = "1"  # Non observé

# ⚠ Divergence assumée : un bloc `mortality` avec `wounded = 1` décrit un animal
# **blessé**, donc vivant au moment de l'observation. Le verser en « Trouvé mort »
# serait faux. La LPO ne fait pas cette distinction et écrit « 3 » dans les deux cas.
# Le cas n'a pas pu être vérifié sur donnée réelle : les 12 mortalités du corpus
# d'exemple ont toutes `wounded = "0"`.

# Codes de `details[].condition` qui décrivent un animal mort, repris tels quels de la
# table de synonymes ETA_BIO de `gn_vn2synthese` (09b_init_data_nomenclatures.sql).
#
# ⚠ Cette table existe chez eux mais **n'est pas utilisée** : leur
# `08_upsert_observations.sql` v1.6.0 ne consulte jamais les synonymes ETA_BIO, il ne
# regarde que le bloc `mortality`. Un reste de pelote de réjection ou une momie de
# chiroptère y ressort donc « Observé vivant ». On lit la table qu'ils ont écrite.
#
# PEL = pelote de réjection, MUMMIE = momie, BONESREMAINS = restes osseux.
# REMAINS n'est pas dans leur table mais apparaît dans les données réelles observées.
CONDITIONS_MORTES = {"BONESREMAINS", "PEL", "MUMMIE", "REMAINS"}

# ── Preuve d'existence (PREUVE_EXIST) ────────────────────────────────────────
# `observers[0].medias` : photos, vidéos et sons attachés à l'observation.
# `gn_vn2synthese` (08:257-264) : présence de la clé -> « 1 » (Oui), sinon « 2 » (Non).
PREUVE_AVEC_MEDIA = "1"
PREUVE_SANS_MEDIA = "2"

# ── Nature de l'objet géographique (NAT_OBJ_GEO) ─────────────────────────────
# `observers[0].precision` décrit le type de localisation. `gn_vn2synthese` en tire
# NAT_OBJ_GEO par une table de synonymes de onze entrées, reproduite ici telle quelle
# (09b_init_data_nomenclatures.sql) : `precise` -> « St » (Station), les dix autres ->
# « In » (Inventoriel).
#
# ⚠ Noter `transect_precise` -> « In » : le suffixe ne suffit pas à conclure, un
# transect reste une zone même relevé précisément. Une règle par suffixe serait fausse.
#
# Une valeur hors table retombe sur le défaut de la colonne, soit « NSP » (Ne sait pas)
# dans GeoNature. C'est aussi ce que produit leur fonction de synonymes, qui rend NULL
# quand elle ne trouve pas. Étendre « In » à l'inconnu serait une extrapolation : rien
# ne dit qu'un type de localisation à venir désignera une zone plutôt qu'un point.
NAT_OBJ_GEO = {
    "precise": "St",             # pointage GPS -> Station
    "transect_precise": "In",
    "transect": "In",
    "subplace_precise": "In",
    "subplace": "In",
    "square": "In",
    "polygone_precise": "In",
    "polygone": "In",
    "place": "In",
    "municipality": "In",
    "garden": "In",
}

# ⚠ `METH_OBS` reste au défaut « Inconnu » — non pas faute d'information, mais faute de
# correspondance écrite. `details[].condition` existe et porte une énumération
# (VIEW, FLY, LAID, HAND, OBSIND, MAGNIFYING, AUDIO, U…) que `gn_vn2synthese` mappe vers
# METH_OBS et TECHNIQUE_OBS par une table de synonymes d'une trentaine d'entrées.
# ⚠ Chez eux aussi cette table est inutilisée : leur v1.6.0 écrit en dur
# `get_id_nomenclature('METH_OBS', '21')`, le bloc à synonymes restant en commentaire
# (08:184-191). Notre défaut vaut donc le leur, sans le faux semblant.
METH_OBS = None


def _entier(valeur) -> int | None:
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def code_atlas(observation: dict) -> int | None:
    """Code atlas d'une observation, quelle que soit la forme renvoyée par l'API.

    Trois formes coexistent dans la nature :
      - `"2"` — valeur simple, celle que journalise le client `transfer_vn` de la LPO ;
      - `{"@id": "3", "#text": "Couple"}` — l'`@id` porte le code ;
      - `{"@id": "3_13", "#text": "12"}` — l'`@id` porte la **clé d'énumération du
        champ** (`3_<n>`, où 3 est l'identifiant du champ « code atlas ») et le `#text`
        le code EOAC réel.

    ⚠ La troisième forme est majoritaire dans les exports réels : mesurée sur
    165 observations de Faune-LR portant un code atlas, elle est la seule employée
    (`3_3`/« 2 » 89 fois, `3_2`/« 1 » 38 fois, `3_99`/« 99 » 4 fois…). Et la
    correspondance n'est pas un décalage constant — `3_14` vaut 13, mais `3_16` vaut 14 :
    seul le `#text` fait foi.

    ⚠⚠ Prendre l'`@id` tel quel ne donnait pas une valeur nulle, ce qui aurait au moins
    été visible : `int("3_13")` vaut **313** en Python, l'underscore étant un séparateur
    de chiffres accepté depuis la 3.6. Le connecteur lisait donc « 313 » là où le code
    est 12, « 32 » là où il est 1, et « 399 » là où il est 99. Conséquences mesurables
    sur le corpus d'exemple : les 38 observations de code 1 — « vu en période de
    nidification dans un milieu favorable », explicitement écarté du seuil — passaient
    en « Reproduction », et les 4 absences déclarées (code 99) entraient en présence.
    """
    brut = observation.get("atlas_code")
    if isinstance(brut, dict):
        identifiant = str(brut.get("@id") or "").strip()
        # Un `@id` de la forme `3_13` est une clé d'énumération, pas un code : le `#text`
        # porte alors la valeur. Le test sur l'underscore doit précéder la conversion,
        # qui l'avalerait silencieusement.
        brut = brut.get("#text") if "_" in identifiant else (identifiant or brut.get("#text"))
    brut = str(brut if brut is not None else "").strip()
    # Refus explicite de tout underscore résiduel, pour la raison ci-dessus.
    return None if "_" in brut else _entier(brut)


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


def valeur_simple(brut) -> str | None:
    """Valeur textuelle d'un champ Biolovision, quelle que soit sa forme.

    L'API renvoie tantôt `{"@id": "precise", "#text": "Précis"}`, tantôt `"precise"`,
    selon le point d'entrée et la version.
    """
    if isinstance(brut, dict):
        brut = brut.get("@id") or brut.get("#text")
    return str(brut or "").strip() or None


def _booleen(source: dict | None, cle: str) -> bool:
    """L'API renvoie « 1 »/« 0 » en chaîne, jamais un booléen JSON."""
    return str((source or {}).get(cle) or "").strip().lower() in ("1", "true", "yes")


def mortalite(observation: dict) -> dict | None:
    """Bloc de mortalité, ou None. La clé fait foi, pas son contenu.

    `gn_vn2synthese` teste `extended_info ? 'mortality'` : c'est la **présence** de la
    clé qui signale une donnée de mortalité, un bloc vide restant significatif. On fait
    de même — un `{"mortality": {}}` traité comme une absence d'information ferait
    passer un cadavre pour un animal vivant.
    """
    etendu = observation.get("extended_info")
    if not isinstance(etendu, dict) or "mortality" not in etendu:
        return None
    bloc = etendu.get("mortality")
    return bloc if isinstance(bloc, dict) else {}


def cause_mortalite(observation: dict) -> tuple[str | None, str | None]:
    """(cause, précision) de la mortalité, telles que VisioNature les nomme.

    Aucune nomenclature SINP ne sait exprimer « collision routière » ou
    « électrocution » : la cause part donc dans `additional_data`, comme
    `gn_vn2synthese` la range dans sa table étendue (`mortality_cause`). La perdre
    reviendrait à ne plus pouvoir isoler les données de mortalité routière, qui sont
    précisément celles que les collectivités demandent.
    """
    bloc = mortalite(observation)
    if bloc is None:
        return (None, None)
    cause = str(bloc.get("death_cause2") or "").strip() or None
    # `road_type2` (PRINCIPAL_ROAD, SECONDARY_ROAD, TRACK…) et `predation2` (MAMMAL,
    # BIRD…) qualifient la cause. Un seul des deux est présent, selon `death_cause2`.
    detail = str(bloc.get("road_type2") or bloc.get("predation2") or "").strip() or None
    return (cause, detail)


def _conditions(observation: dict) -> set[str]:
    """Codes `condition` du bloc `details`, en majuscules.

    `details` ventile un relevé par classe d'âge et de sexe : plusieurs conditions
    peuvent coexister sur une même observation.
    """
    details = observation.get("details")
    if not isinstance(details, list):
        return set()
    return {str((d or {}).get("condition") or "").strip().upper()
            for d in details if isinstance(d, dict)}


def etat_biologique(observation: dict, absence: bool) -> str | None:
    """cd_nomenclature ETA_BIO de l'observation.

    Ordre volontaire : la mortalité l'emporte sur l'absence. Un relevé qui porte à la
    fois un bloc `mortality` et un effectif nul décrit un cadavre trouvé, pas une
    espèce recherchée en vain.
    """
    bloc = mortalite(observation)
    if bloc is not None:
        return ETA_BIO_VIVANT if _booleen(bloc, "wounded") else ETA_BIO_MORT
    if _conditions(observation) & CONDITIONS_MORTES:
        return ETA_BIO_MORT
    if absence:
        return ETA_BIO_NON_OBSERVE
    # VisioNature est un outil de saisie de terrain sur faune vivante : hors mortalité
    # déclarée, l'animal a été vu ou entendu vivant. C'est aussi la position de
    # `gn_vn2synthese`. Il subsiste un angle mort assumé : un cadavre saisi sans passer
    # par le module de mortalité et sans code `condition` explicite reste « vivant ».
    return ETA_BIO_VIVANT


def medias(observation: dict) -> list[dict]:
    """Médias attachés à l'observation, tels quels."""
    valeur = observation.get("medias")
    return [m for m in valeur if isinstance(m, dict)] if isinstance(valeur, list) else []


def preuve_existence(observation: dict) -> str | None:
    """cd_nomenclature PREUVE_EXIST.

    ⚠ On répond « Oui » dès qu'un média existe, **y compris s'il est masqué**
    (`media_is_hidden = 1`). Un média masqué reste une preuve : c'est sa publication
    qui est interdite, pas son existence. L'URL, elle, n'est pas reprise (cf.
    `transform.preuves_numeriques`).
    """
    return PREUVE_AVEC_MEDIA if medias(observation) else PREUVE_SANS_MEDIA


def nature_objet_geo(observation: dict) -> str | None:
    """cd_nomenclature NAT_OBJ_GEO déduit du type de localisation VisioNature."""
    valeur = valeur_simple(observation.get("precision"))
    return NAT_OBJ_GEO.get(valeur.lower()) if valeur else None


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


def comportement_declare(observation: dict) -> str | None:
    """cd_nomenclature OCC_COMPORTEMENT tiré de `behaviours[]`, si l'un est traduisible.

    Le premier comportement reconnu l'emporte : la colonne SINP n'en accepte qu'un, et
    rien dans le modèle VisioNature ne hiérarchise les comportements d'un même relevé.
    """
    for entree in observation.get("behaviours") or []:
        code = COMPORTEMENT_VN.get(valeur_simple(entree) or "")
        if code:
            return code
    return None


def cd_nomenclatures(sighting: dict, observation: dict,
                     surcharges: dict | None = None,
                     analyse=None) -> dict[str, str | None]:
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

    `analyse` est le résultat de `reproduction.analyser` : le degré de reproduction
    déduit de l'âge, du sexe et du comportement pour les groupes sans code atlas. Passé
    par l'appelant plutôt que calculé ici, parce que `transform.to_row` en a besoin de
    son côté pour conserver le degré dans `additional_data`.
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

    absente = est_absence(observation, absence)

    # Repli sur l'âge, le sexe et le comportement quand le code atlas ne dit rien —
    # c'est-à-dire pour tous les groupes sauf les oiseaux. L'ordre importe : un code
    # atlas, saisi explicitement par l'observateur comme un indice de nidification,
    # prime sur une déduction. `gn_vn2synthese` procède de même (COALESCE).
    #
    # ⚠ Jamais sur une absence. Une espèce recherchée et non trouvée ne peut pas s'y
    # reproduire : si le formulaire porte encore un âge ou un sexe résiduel, les
    # interpréter transformerait une absence constatée en preuve de reproduction. Le
    # témoin n'a pas ce garde-fou, son repli n'étant conditionné qu'à l'absence de code
    # atlas.
    if statut_bio is None and analyse is not None and analyse.reproduction and not absente:
        statut_bio = "3"          # Reproduction
    if comportement is None:
        comportement = comportement_declare(observation)
    return {
        "STATUT_SOURCE": STATUT_SOURCE,
        "ETA_BIO": etat_biologique(observation, absente),
        "PREUVE_EXIST": preuve_existence(observation),
        "NAT_OBJ_GEO": nature_objet_geo(observation),
        "METH_OBS": METH_OBS,
        "STATUT_BIO": statut_bio,
        "OCC_COMPORTEMENT": comportement,
        "OBJ_DENBR": obj_denbr,
        "TYP_DENBR": typ_denbr,
        "STATUT_OBS": "No" if absente else "Pr",
        # NATURALITE, STADE_VIE, SEXE et STAT_BIOGEO ne sont pas produits ici : la clé
        # absente vaut None chez le résolveur, qui applique le défaut de la colonne.
        # Elles doivent tout de même figurer dans COLONNES_NOMENCLATURE, car l'INSERT
        # les porte (cf. transform.COLONNES_NOMENCLATURE).
        #
        # TODO : STADE_VIE et SEXE sont accessibles — `details[].age` (AD, SUBAD,
        # JUVENILE, IMM, PULL, EXUVIE, IMAGO, 1Y…5Y) et `details[].sex` (U, M, F, FT).
        # Un commentaire antérieur affirmait ici que « VisioNature ne livre ni sexe, ni
        # stade de vie » : c'est faux. L'obstacle du dépliage de `details[]` est levé
        # (cf. `reproduction.valeurs` et la note de `transform.deplier`) : la ligne
        # reste unique et les classes sont agrégées. Reste à écrire les deux
        # correspondances, et à trancher le cas ambigu — « 1 mâle + 2 femelles » n'a ni
        # sexe ni stade unique, et doit rester au défaut plutôt qu'en élire un.
        # `gn_vn2synthese` a la table de synonymes (STADE_VIE : 28 entrées, SEXE : 5)
        # mais ne s'en sert pas non plus : sa v1.6.0 écrit « 0 » en dur pour les deux.
    }
