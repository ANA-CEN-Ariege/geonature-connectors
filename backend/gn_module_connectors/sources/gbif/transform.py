"""Transformation d'une occurrence GBIF en ligne de gn_synthese.synthese."""

import hashlib
import json
import re
import uuid
from datetime import date

from . import api as gbif_api
from . import nomenclatures as gbif_nomen

# Namespace fixe : le même gbifID redonne toujours le même UUID, donc un moissonnage
# rejoué met à jour au lieu de dupliquer.
GBIF_NAMESPACE = uuid.UUID("4b1d37db-9eb3-4875-9a32-2a5b7b3c3a1e")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def sinp_uuid(occ: dict) -> str:
    """UUID SINP déterministe, par ordre de préférence décroissante.

    1. **L'UUID contenu dans `occurrenceID`.** Pour les données republiées par l'INPN,
       c'est l'identifiant permanent DEE : l'observation importée conserve son identité
       SINP d'origine et se réconciliera d'elle-même avec toute reprise ultérieure.
       Mesuré sur l'Ariège : 100 % des jeux publiés par PatriNat.

    2. **Un uuid5 dérivé de `occurrenceID`**, quand celui-ci n'est pas un UUID.
       C'est le cas d'iNaturalist (`https://www.inaturalist.org/observations/333015081`)
       et d'eBird (`URN:catalog:CLO:EBIRD:OBS1929924431`) — 0 % d'UUID sur ces jeux.
       `occurrenceID` est l'identifiant que Darwin Core impose au producteur de rendre
       stable ; c'est donc la meilleure clé disponible après un vrai UUID.

    3. **Un uuid5 dérivé du `gbifID`**, en dernier recours seulement. Le `gbifID` est une
       clé interne à GBIF, pas un identifiant du producteur : rien ne garantit qu'il
       survive à une republication du jeu de données. S'en servir comme clé primaire
       exposerait à des doublons lors d'un moissonnage ultérieur.

    L'`occurrenceID` est préfixé du `datasetKey` avant hachage : deux producteurs peuvent
    employer le même identifiant local (« 1 », « OBS42 ») sans qu'il s'agisse de la même
    observation.
    """
    occurrence_id = (occ.get("occurrenceID") or "").strip()

    m = _UUID_RE.search(occurrence_id)
    if m:
        return m.group(0).lower()

    if occurrence_id:
        cle = f"{occ.get('datasetKey', '')}:{occurrence_id}"
        return str(uuid.uuid5(GBIF_NAMESPACE, cle))

    return str(uuid.uuid5(GBIF_NAMESPACE, str(occ.get("gbifID", ""))))


def _parse_une_date(valeur: str, fin: bool) -> date | None:
    """Parse une date GBIF partielle. `fin=True` complète vers la borne haute.

    GBIF renvoie aussi bien « 2024-06-01T10:00:00 » que « 2024-06 » ou « 2024 ».
    Une année seule vaut du 1er janvier au 31 décembre : compléter les deux bornes
    différemment évite de réduire une donnée annuelle à un seul jour.
    """
    if not valeur:
        return None
    v = valeur.strip().split("T")[0]
    morceaux = v.split("-")
    try:
        an = int(morceaux[0])
        if len(morceaux) >= 3:
            return date(an, int(morceaux[1]), int(morceaux[2]))
        if len(morceaux) == 2:
            mois = int(morceaux[1])
            if not fin:
                return date(an, mois, 1)
            # dernier jour du mois, sans dépendance externe
            return date(an + (mois == 12), (mois % 12) + 1, 1) - __import__("datetime").timedelta(days=1)
        return date(an, 12, 31) if fin else date(an, 1, 1)
    except (ValueError, IndexError):
        return None


def parse_dates(occ: dict) -> tuple[date, date] | None:
    """(date_min, date_max), ou None si indéterminable.

    `eventDate` peut être un **intervalle** ISO « début/fin » — c'est courant sur les
    spécimens de collection. Ne pas le gérer fait échouer l'insertion (`date_min` est
    NOT NULL), et dans api2GN cela fait perdre l'intégralité du lot puisqu'un seul
    commit couvre tout l'import.
    """
    brut = (occ.get("eventDate") or "").strip()
    if brut:
        if "/" in brut:
            debut, _, fin = brut.partition("/")
            d_min, d_max = _parse_une_date(debut, False), _parse_une_date(fin, True)
            if d_min and d_max:
                return (d_min, d_max) if d_min <= d_max else (d_max, d_min)
            if d_min:
                return d_min, d_min
        else:
            d = _parse_une_date(brut, False)
            if d:
                return d, _parse_une_date(brut, True) or d

    # Repli sur les champs éclatés, souvent renseignés quand eventDate ne l'est pas.
    an = occ.get("year")
    if not an:
        return None
    mois, jour = occ.get("month"), occ.get("day")
    try:
        if mois and jour:
            d = date(int(an), int(mois), int(jour))
            return d, d
        if mois:
            return (_parse_une_date(f"{an}-{mois}", False), _parse_une_date(f"{an}-{mois}", True))
        return date(int(an), 1, 1), date(int(an), 12, 31)
    except (ValueError, TypeError):
        return None


# Champs dont un changement justifie de réécrire l'observation. Volontairement limité à
# ce que le module exploite : inutile de déclencher une mise à jour parce que GBIF a
# réinterprété un champ qu'on n'importe pas.
CHAMPS_SUIVIS = (
    "taxonKey", "acceptedTaxonKey", "scientificName",
    "eventDate", "year", "month", "day",
    "decimalLatitude", "decimalLongitude", "coordinateUncertaintyInMeters",
    "individualCount", "recordedBy", "occurrenceStatus", "basisOfRecord",
    "lifeStage", "sex", "establishmentMeans", "degreeOfEstablishment",
    "license", "rightsHolder", "datasetKey",
)


def empreinte(occ: dict) -> str:
    """Empreinte du contenu exploité d'une occurrence.

    Raison d'être : `modified`, la date de modification déclarée par le producteur, est
    la seule date GBIF qui reflète un changement réel de la donnée — mais elle est
    **absente de tous les jeux publiés par l'INPN** (mesuré : 0 % sur SICEN Occitanie,
    Faune Occitanie et INPN flore CBN, contre 100 % sur iNaturalist). Or PatriNat
    représente 76 % du corpus ariégeois. Sans empreinte, une correction apportée à la
    source resterait invisible sur l'essentiel des données.

    L'empreinte ne porte que sur les champs réellement importés : une réinterprétation
    GBIF d'un champ inexploité ne doit pas provoquer de réécriture inutile.
    """
    brut = "|".join(f"{c}={occ.get(c)!r}" for c in CHAMPS_SUIVIS)
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


# Colonne de synthese <- mnémonique de nomenclature.
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
    # GBIF expose `behavior` en Darwin Core, mais en texte libre et sans vocabulaire
    # contrôlé : aucune correspondance fiable vers OCC_COMPORTEMENT. La colonne reste au
    # défaut, et doit tout de même être fournie puisque l'INSERT la porte.
    "id_nomenclature_behaviour": "OCC_COMPORTEMENT",
}


def to_row(occ: dict, *, cd_nom: int, id_dataset: int, id_source: int,
           id_module: int, srid: int, resolver, download_doi: str = "",
           statut_validation: str | None = None) -> dict | None:
    """Ligne prête pour l'insertion, ou None si l'occurrence est inexploitable."""
    lon, lat = occ.get("decimalLongitude"), occ.get("decimalLatitude")
    if lon is None or lat is None:
        return None
    dates = parse_dates(occ)
    if not dates:
        return None
    d_min, d_max = dates

    effectif = occ.get("individualCount")
    incertitude = occ.get("coordinateUncertaintyInMeters")

    cds = gbif_nomen.cd_nomenclatures(occ)
    # `SEXE` n'est pas produit par cd_nomenclatures : il vient directement du mapping,
    # une valeur inconnue devant retomber sur le défaut plutôt qu'être inventée.
    cds["SEXE"] = gbif_nomen.SEX.get(occ.get("sex") or "")
    # Pré-validation : la donnée GBIF a déjà été filtrée et contrôlée par le producteur
    # puis par GBIF. La laisser « En attente de validation » (défaut) noierait le module
    # Validation sous des centaines de milliers d'observations que personne ne validera.
    cds["STATUT_VALID"] = statut_validation
    nomenclatures = {
        colonne: resolver.id(mnemonique, cds.get(mnemonique))
        for colonne, mnemonique in COLONNES_NOMENCLATURE.items()
    }

    return {
        **nomenclatures,
        "unique_id_sinp": sinp_uuid(occ),
        "id_source": id_source,
        "id_module": id_module,
        "id_dataset": id_dataset,
        "entity_source_pk_value": str(occ.get("gbifID", "")),
        "cd_nom": cd_nom,
        # nom_cite est NOT NULL : c'est le nom tel que cité par l'observateur.
        "nom_cite": (occ.get("scientificName") or occ.get("verbatimScientificName") or "?")[:1000],
        "date_min": d_min,
        "date_max": d_max,
        "count_min": int(effectif) if effectif else None,
        "count_max": int(effectif) if effectif else None,
        "observers": (occ.get("recordedBy") or "")[:1000] or None,
        "comment_description": (occ.get("occurrenceRemarks") or "").strip() or None,
        # GBIF ne publie que ce qui est déjà diffusable : une occurrence sensible est
        # floutée ou retenue en amont par le producteur. Rien à restreindre ici, et NULL
        # est la bonne façon de ne pas se prononcer.
        "id_nomenclature_diffusion_level": None,
        "precision": int(incertitude) if incertitude else None,
        "additional_data": json.dumps(
            {**gbif_api.provenance(occ, download_doi), "gbif_empreinte": empreinte(occ)},
            ensure_ascii=False,
        ),
        "lon": float(lon),
        "lat": float(lat),
        "local_srid": srid,
    }
