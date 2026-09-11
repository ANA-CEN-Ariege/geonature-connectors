"""Transformation d'une observation dbChiro en ligne de gn_synthese.synthese."""

import hashlib
import hmac
import json
import uuid
from datetime import datetime

from . import nomenclatures as db_nomen
from . import taxonomy as db_taxo

# Namespace fixe pour dériver des UUID déterministes.
DBCHIRO_NAMESPACE = uuid.UUID("2c9a6f38-1d47-5b90-8e25-7a3f0c6b4d81")

# Colonne de synthese <- mnémonique de nomenclature.
#
# Doit couvrir TOUTES les colonnes `id_nomenclature_*` de `core.synthese.INSERT_SQL` :
# le statement est unique pour tout le lot, un paramètre lié manquant fait échouer
# l'insertion entière.
#
# ⚠ Ce dictionnaire est identique à celui de `sources/visionature/transform.py`, et sa
# place serait dans `core/`. Le déplacement n'est pas fait ici à dessein : les fichiers
# VisioNature sont en cours de modification sur une autre branche, et déporter une
# constante partagée y créerait un conflit pour un gain nul. À factoriser quand les deux
# connecteurs seront stabilisés.
COLONNES_NOMENCLATURE = {
    "id_nomenclature_obs_technique": "METH_OBS",
    "id_nomenclature_bio_condition": "ETA_BIO",
    "id_nomenclature_bio_status": "STATUT_BIO",
    "id_nomenclature_naturalness": "NATURALITE",
    "id_nomenclature_observation_status": "STATUT_OBS",
    "id_nomenclature_source_status": "STATUT_SOURCE",
    "id_nomenclature_life_stage": "STADE_VIE",
    "id_nomenclature_sex": "SEXE",
    "id_nomenclature_obj_count": "OBJ_DENBR",
    "id_nomenclature_type_count": "TYP_DENBR",
    "id_nomenclature_biogeo_status": "STAT_BIOGEO",
    "id_nomenclature_exist_proof": "PREUVE_EXIST",
    "id_nomenclature_valid_status": "STATUT_VALID",
    "id_nomenclature_behaviour": "OCC_COMPORTEMENT",
    "id_nomenclature_geo_object_nature": "NAT_OBJ_GEO",
    # Quatre colonnes que la Synthèse porte et que seule une vue `v_synthese_sinp` sait
    # renseigner (cf. connecteur GeoNature). dbChiro n'en dit rien : le résolveur applique
    # le défaut de chacune, exactement ce qu'aurait fait le DEFAULT de la colonne.
    "id_nomenclature_info_geo_type": "TYP_INF_GEO",
    "id_nomenclature_blurring": "DEE_FLOU",
    "id_nomenclature_grp_typ": "TYP_GRP",
    "id_nomenclature_determination_method": "METH_DETERMIN",
}

# Champs dont un changement justifie de réécrire l'observation.
#
# La liste couvre exactement ce que le connecteur exploite. `timestamp_update` en serait
# le critère naturel, mais le serializer de dbChiro ne l'expose pas : l'empreinte de
# contenu est ici le **seul** moyen de détecter une correction à la source.
CHAMPS_SUIVIS = ("codesp", "total_count", "breed_colo", "period", "is_doubtful",
                 "comment", "updated_by")


def _flottant(valeur):
    try:
        return float(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _entier(valeur):
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def coordonnees(feature: dict) -> tuple[float | None, float | None]:
    """(lon, lat) d'une feature GeoJSON, en WGS84.

    dbChiro publie une géométrie ponctuelle par observation, renseignée sur 100 % des
    8 039 observations mesurées. Aucune reprojection : l'API rend déjà du 4326.
    """
    geometrie = feature.get("geometry") or {}
    if str(geometrie.get("type") or "").lower() != "point":
        return (None, None)
    coords = geometrie.get("coordinates") or []
    if len(coords) < 2:
        return (None, None)
    return (_flottant(coords[0]), _flottant(coords[1]))


def date_observation(properties: dict) -> datetime | None:
    """Date de la session d'observation.

    ⚠ **Sans heure, et sans date de fin.** Le modèle dbChiro porte `time_start`,
    `date_end` et `time_end` sur la session, mais le serializer de `/api/v1/search` n'en
    expose aucun : seul `date_start` remonte. Conséquence assumée, et à connaître pour
    l'acoustique — une nuit d'enregistrement qui court du 29 juillet au soir au 30 au
    matin est ramenée au seul 29. `date_max = date_min`, faute de mieux.

    C'est la seconde raison d'exposer davantage de champs en amont, après
    `timestamp_update`.
    """
    session = properties.get("session_data") or {}
    brut = str(session.get("date_start") or "").strip()
    if not brut:
        return None
    try:
        return datetime.fromisoformat(brut[:10])
    except ValueError:
        return None


def zonages(properties: dict) -> list[dict]:
    """Rattachements administratifs du lieu, tels que dbChiro les calcule."""
    session = properties.get("session_data") or {}
    lieu = session.get("place_data") or {}
    return [a for a in (lieu.get("areas") or []) if isinstance(a, dict)]


def _zonage(properties: dict, code_type: str) -> dict | None:
    for zone in zonages(properties):
        if str((zone.get("area_type") or {}).get("code") or "") == code_type:
            return zone
    return None


def departement(properties: dict) -> str | None:
    """Code de département de l'observation, d'après les zonages du lieu.

    dbChiro rattache chaque `place` à ses zonages et les publie dans la réponse :
    département, commune, maille 10 km, ZNIEFF, parc. On lit donc le département sans
    aucun calcul géographique de notre côté.
    """
    zone = _zonage(properties, "dep")
    if zone is None:
        return None
    code = str(zone.get("code") or "").strip().upper()
    return code.zfill(2) if code.isdigit() else (code or None)


def commune_insee(properties: dict) -> str | None:
    zone = _zonage(properties, "mun")
    return str((zone or {}).get("code") or "").strip() or None


def dans_perimetre(properties: dict, codes: set[str]) -> bool:
    """L'observation est-elle dans le périmètre départemental configuré ?

    Ce contrôle **double** le filtre serveur `area`, à dessein : un paramètre inconnu de
    l'API DRF est ignoré sans erreur, et rien ne distingue alors un filtre appliqué d'un
    filtre inexistant. Sur l'instance mesurée, `area = 109` ramène bien 8 007
    observations sur 8 039 — mais un identifiant de zonage erroné ramènerait les 8 039
    sans le moindre avertissement.

    Une observation dont le département est indéterminable est écartée quand un filtre
    est actif : mieux vaut un rejet tracé qu'une passoire silencieuse.
    """
    if not codes:
        return True
    dep = departement(properties)
    return dep is not None and dep in codes


def pseudonyme(identifiant, secret: str) -> str:
    """Pseudonyme stable d'un observateur.

    ⚠ Duplique `sources/visionature/confidentialite.pseudonyme`, même construction et
    mêmes garanties. Non factorisé dans `core/` pour la raison exposée plus haut à
    propos de `COLONNES_NOMENCLATURE` : les fichiers VisioNature évoluent en parallèle.
    """
    if not secret:
        raise ValueError(
            "Aucune clé de pseudonymisation. Renseignez [dbchiro] "
            "pseudonymisation_secret, ou laissez pseudonymiser_observateurs à false. "
            "Une clé par défaut rendrait les pseudonymes recalculables par un tiers.")
    return hmac.new(secret.encode("utf-8"), str(identifiant or "").encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def observateurs(properties: dict, *, pseudonymiser: bool = False,
                 secret: str = "") -> str | None:
    """Contenu de `synthese.observers`.

    dbChiro distingue le **créateur** de la saisie de l'**observateur principal** de la
    session. Les deux sont publiés en clair, avec leur nom complet, et coïncident dans la
    majorité des cas ; ils sont donc dédoublonnés en conservant l'ordre.

    ⚠ **Aucun marqueur de consentement individuel n'existe côté dbChiro**, contrairement
    au champ `anonymous` de VisioNature. Publier les noms suppose donc un accord de
    l'exploitant portant sur l'ensemble des contributeurs, et non le consentement de
    chacun. C'est un choix d'exploitation, pas un défaut technique : d'où l'option de
    pseudonymisation, qui reste disponible sans changer une ligne de code.

    Comme pour VisioNature, aucun rôle n'est créé dans `utilisateurs.t_roles` : ce
    référentiel est un annuaire de comptes, pas de personnes citées.
    """
    session = properties.get("session_data") or {}
    noms, vus = [], set()
    for bloc in (properties.get("creator"), session.get("main_observer")):
        if not isinstance(bloc, dict):
            continue
        if pseudonymiser:
            valeur = pseudonyme(bloc.get("id"), secret)[:12]
        else:
            valeur = str(bloc.get("full_name") or bloc.get("label") or "").strip()
        if valeur and valeur not in vus:
            vus.add(valeur)
            noms.append(valeur)
    return ", ".join(noms) or None


def empreinte(feature: dict) -> str:
    """Empreinte du contenu exploité, pour ne réécrire que ce qui a changé."""
    properties = feature.get("properties") or {}
    brut = "|".join(f"{c}={properties.get(c)!r}" for c in CHAMPS_SUIVIS)
    lon, lat = coordonnees(feature)
    brut += f"|lon={lon!r}|lat={lat!r}"
    brut += f"|date={date_observation(properties)!r}"
    brut += f"|contact={db_nomen.contact(properties)!r}"
    brut += f"|codesp={db_taxo.codesp(properties)!r}"
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


def identifiant_sinp(feature: dict, instance: str = "") -> str:
    """`unique_id_sinp` d'une observation.

    ⚠ dbChiro porte un `UUIDField` unique sur `Sighting` **et** sur `Session`, mais le
    serializer de recherche n'en expose aucun — couverture nulle sur 8 039 observations.
    L'identifiant est donc dérivé, comme il l'était pour VisioNature avant que l'UUID
    natif ne soit disponible.

    L'URL de l'instance entre dans la clé : `id_sighting` est un entier propre à chaque
    base dbChiro, et deux instances régionales emploieraient les mêmes.

    Le jour où `uuid` sera exposé, il devra primer — et les lignes déjà importées seront
    à réaligner via `core.synthese.realigner_uuid`, exactement comme pour VisioNature.
    """
    return str(uuid.uuid5(DBCHIRO_NAMESPACE, f"{instance}:{feature.get('id')}"))


def uuid_groupe(properties: dict, instance: str = "") -> str | None:
    """`unique_id_sinp_grp` : identifiant de la session d'observation.

    Une session dbChiro est une visite — un gîte prospecté un soir donné, une nuit
    d'enregistrement — et toutes les observations qui en sortent forment un relevé
    cohérent. C'est exactement ce qu'exprime `unique_id_sinp_grp`.
    """
    session = properties.get("session_data") or {}
    identifiant = session.get("id_session")
    if identifiant is None:
        return None
    return str(uuid.uuid5(DBCHIRO_NAMESPACE, f"session:{instance}:{identifiant}"))


def to_row(feature: dict, *, cd_nom: int, id_dataset: int | None, id_source: int,
           id_module: int, srid: int, resolver, instance: str = "",
           absence: bool = False, statut_validation: str | None = None,
           pseudonymiser: bool = False, secret_pseudo: str = "",
           code_diffusion: str = "", version_taxref: str | None = None) -> dict | None:
    """Ligne prête pour l'insertion, ou None si l'observation est inexploitable."""
    properties = feature.get("properties") or {}
    lon, lat = coordonnees(feature)
    if lon is None or lat is None:
        return None
    jour = date_observation(properties)
    if jour is None:
        return None

    cds = db_nomen.cd_nomenclatures(properties, absence=absence,
                                    statut_validation=statut_validation)
    nomenclatures = {
        colonne: resolver.id(mnemonique, cds.get(mnemonique))
        for colonne, mnemonique in COLONNES_NOMENCLATURE.items()
    }
    # Le niveau de diffusion est laissé au producteur : GeoNature a cessé de le calculer,
    # et NULL y signifie « ne se prononce pas ». On ne le renseigne que si l'exploitant
    # a exprimé une restriction en configuration.
    nomenclatures["id_nomenclature_diffusion_level"] = (
        resolver.id("NIV_PRECIS", code_diffusion) if code_diffusion else None)

    session = properties.get("session_data") or {}
    lieu = session.get("place_data") or {}
    effectif = _entier(properties.get("total_count"))
    # Une absence porte un effectif de zéro, non NULL : l'ambiguïté entre « aucun
    # individu » et « effectif non renseigné » fausserait toute analyse quantitative.
    if absence:
        effectif = 0

    provenance = {
        "source": "dbChiro",
        "instance": instance,
        "sighting_id": str(feature.get("id") or ""),
        "session_id": str(session.get("id_session") or ""),
        "session_name": str(session.get("name") or ""),
        "place_id": str(lieu.get("id_place") or ""),
        # Nom du gîte ou du point d'écoute. Donnée sensible en chiroptérologie — « Trou
        # souffleur - trois frères » désigne une cavité précise —, conservée parce
        # qu'elle est le seul moyen de rapprocher l'observation du suivi de site, et
        # parce que la géométrie exacte est de toute façon en base.
        "place_name": str(lieu.get("name") or ""),
        "codesp": db_taxo.codesp(properties),
        # Détermination d'origine, notamment pour les 14 % d'observations rattachées à
        # un rang supérieur : `cd_nom` dit « Myotis », seul ce champ dit « Myotis myotis
        # / M. blythii ».
        "determination": db_taxo.nom_cite(properties),
        "agregat": "oui" if db_taxo.est_agregat(properties) else "",
        # Phénologie calculée par dbChiro, conservée telle quelle : elle n'est pas
        # traduisible en STATUT_BIO sans contresens (cf. `nomenclatures.periode`).
        "periode": db_nomen.periode(properties),
        "contact": db_nomen.contact(properties),
        "contact_libelle": db_nomen.libelle_contact(properties),
        "colonie_reproduction": "oui" if db_nomen.est_colonie_reproduction(properties) else "",
        "determination_douteuse": "oui" if db_nomen.est_douteuse(properties) else "",
        "commune_insee": commune_insee(properties) or "",
        "departement": departement(properties) or "",
        # `date_min` porte minuit faute d'heure exposée par l'API : sans ce drapeau,
        # rien ne distinguerait une donnée horodatée d'une donnée qui ne l'est pas.
        "heure_connue": "non",
        "dbchiro_empreinte": empreinte(feature),
    }

    return {
        **nomenclatures,
        "unique_id_sinp": identifiant_sinp(feature, instance),
        "unique_id_sinp_grp": uuid_groupe(properties, instance),
        "id_source": id_source,
        "id_module": id_module,
        "id_dataset": id_dataset,
        "entity_source_pk_value": str(feature.get("id") or ""),
        "cd_nom": cd_nom,
        "nom_cite": db_taxo.nom_cite(properties)[:1000],
        "date_min": jour,
        "date_max": jour,
        "count_min": effectif,
        "count_max": effectif,
        "observers": (observateurs(properties, pseudonymiser=pseudonymiser,
                                   secret=secret_pseudo) or "")[:1000] or None,
        "comment_description": str(properties.get("comment") or "").strip() or None,
        # `precision`, `altitude_*` et `digital_proof` restent NULL : aucun de ces
        # champs n'est exposé par `/api/v1/search`. Une valeur inventée serait pire
        # qu'une absence.
        "precision": None,
        "altitude_min": None,
        "altitude_max": None,
        "digital_proof": None,
        "meta_v_taxref": (version_taxref or None),
        "additional_data": json.dumps(
            {k: v for k, v in provenance.items() if v not in (None, "")},
            ensure_ascii=False),
        "lon": lon,
        "lat": lat,
        "local_srid": srid,
    }
