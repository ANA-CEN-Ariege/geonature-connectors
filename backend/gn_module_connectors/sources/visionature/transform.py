"""Transformation d'une observation VisioNature en ligne de gn_synthese.synthese."""

import hashlib
import json
import uuid
from datetime import date

from . import confidentialite as vn_conf
from . import nomenclatures as vn_nomen

# Namespace fixe pour dériver des UUID déterministes.
VN_NAMESPACE = uuid.UUID("b7d3f1a0-5c2e-5a41-9e88-4f6d2c1b7a03")

COLONNES_NOMENCLATURE = {
    "id_nomenclature_obs_technique": "METH_OBS",
    "id_nomenclature_bio_condition": "ETA_BIO",
    "id_nomenclature_bio_status": "STATUT_BIO",
    "id_nomenclature_observation_status": "STATUT_OBS",
    "id_nomenclature_source_status": "STATUT_SOURCE",
    "id_nomenclature_obj_count": "OBJ_DENBR",
    "id_nomenclature_type_count": "TYP_DENBR",
    "id_nomenclature_behaviour": "OCC_COMPORTEMENT",
}

# Champs dont un changement justifie de réécrire l'observation.
CHAMPS_SUIVIS = ("count", "estimation_code", "atlas_code", "coord_lat", "coord_lon",
                 "precision", "comment", "timing", "altitude")


def deplier(sightings: list[dict]) -> list[tuple[dict, dict]]:
    """Déplie les relevés en couples (relevé, observation).

    VisioNature imbrique les observations dans `observers` : un même relevé — une espèce,
    une date — peut porter plusieurs saisies, distinctes par leur observateur, leur
    position et leur effectif. C'est cette granularité fine qui correspond à la ligne de
    Synthèse, pas le relevé.
    """
    couples = []
    for s in sightings:
        for o in s.get("observers") or []:
            couples.append((s, o))
    return couples


def sinp_uuid(sighting: dict, observation: dict, instance: str = "") -> str:
    """UUID SINP déterministe d'une observation.

    L'URL de l'instance entre dans la clé : les identifiants VisioNature sont propres à
    chaque site, et deux instances régionales peuvent employer les mêmes. Sans ce
    préfixe, une observation de Faune-Ariège et une de Faune-Occitanie pourraient
    entrer en collision et s'écraser l'une l'autre.
    """
    cle = f"{instance}:{sighting.get('@id')}:{observation.get('@id')}"
    return str(uuid.uuid5(VN_NAMESPACE, cle))


def empreinte(sighting: dict, observation: dict) -> str:
    """Empreinte du contenu exploité, pour ne réécrire que ce qui a changé."""
    brut = "|".join(f"{c}={observation.get(c)!r}" for c in CHAMPS_SUIVIS)
    brut += f"|espece={(sighting.get('species') or {}).get('@id')!r}"
    brut += f"|date={(sighting.get('date') or {}).get('@ISO8601')!r}"
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


def parse_date(sighting: dict) -> date | None:
    """Date d'observation. VisioNature la fournit en ISO 8601 dans `date.@ISO8601`."""
    brut = (sighting.get("date") or {}).get("@ISO8601") or sighting.get("date_start")
    if not brut:
        return None
    try:
        return date.fromisoformat(str(brut).strip()[:10])
    except ValueError:
        return None


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


def code_projet(observation: dict) -> str | None:
    """Code projet VisioNature, s'il existe.

    C'est le découpage retenu par `gn_vn2synthese` pour construire les jeux de données :
    une observation rattachée à un projet en hérite. Plus fidèle qu'un JDD unique par
    instance, puisque les projets correspondent à des programmes réels — atlas, suivis,
    plans d'action.
    """
    valeur = observation.get("project_code")
    if isinstance(valeur, dict):
        valeur = valeur.get("@id") or valeur.get("#text")
    valeur = str(valeur or "").strip()
    return valeur or None


def to_row(sighting: dict, observation: dict, *, cd_nom: int, id_dataset: int | None,
           id_source: int, id_module: int, srid: int, resolver,
           instance: str = "", surcharges_atlas: dict | None = None,
           statut_validation: str | None = None, index_anonymat: dict | None = None,
           secret_pseudo: str = "", forcer_anonymat: bool = False) -> dict | None:
    """Ligne prête pour l'insertion, ou None si l'observation est inexploitable."""
    lon = _flottant(observation.get("coord_lon"))
    lat = _flottant(observation.get("coord_lat"))
    if lon is None or lat is None:
        return None
    jour = parse_date(sighting)
    if jour is None:
        return None

    cds = vn_nomen.cd_nomenclatures(sighting, observation, surcharges_atlas)
    cds["STATUT_VALID"] = statut_validation
    nomenclatures = {
        colonne: resolver.id(mnemonique, cds.get(mnemonique))
        for colonne, mnemonique in COLONNES_NOMENCLATURE.items()
    }
    nomenclatures["id_nomenclature_valid_status"] = resolver.id(
        "STATUT_VALID", statut_validation)

    nom_observateur, motif_anonymat = vn_conf.observateur(
        observation, index_anonymat, secret_pseudo, forcer_anonymat)

    espece = sighting.get("species") or {}
    lieu = sighting.get("place") or {}
    effectif = _entier(observation.get("count"))
    # Une absence porte un effectif de zéro, non NULL : l'ambiguïté entre « aucun
    # individu » et « effectif non renseigné » fausserait toute analyse quantitative.
    if vn_nomen.est_absence(observation):
        effectif = 0

    provenance = {
        "source": "VisioNature",
        "instance": instance,
        "sighting_id": str(sighting.get("@id") or ""),
        "observation_id": str(observation.get("@id") or ""),
        "species_id": str(espece.get("@id") or ""),
        "species_name": espece.get("name") or "",
        # Identifiant d'observateur pseudonymisé, jamais le nom : deux observations du
        # même observateur restent rapprochables sans qu'il soit identifiable.
        # Identifiant pseudonymisé, quel que soit le sort du nom : il permet de
        # rapprocher les observations d'un même contributeur sans l'identifier.
        "observateur": (vn_conf.pseudonyme(observation.get("@uid"), secret_pseudo)[:12]
                        if secret_pseudo else ""),
        "anonymat": motif_anonymat,
        "place": lieu.get("name") or "",
        "atlas_code": vn_nomen.code_atlas(observation),
        "estimation_code": observation.get("estimation_code") or "",
        "vn_empreinte": empreinte(sighting, observation),
    }

    return {
        **nomenclatures,
        "unique_id_sinp": sinp_uuid(sighting, observation, instance),
        "id_source": id_source,
        "id_module": id_module,
        "id_dataset": id_dataset,
        "entity_source_pk_value": str(sighting.get("@id") or ""),
        "cd_nom": cd_nom,
        "nom_cite": (espece.get("name") or "?")[:1000],
        "date_min": jour,
        "date_max": jour,
        "count_min": effectif,
        "count_max": effectif,
        "observers": (nom_observateur or "")[:1000] or None,
        "comment_description": vn_conf.nettoyer_commentaire(observation),
        "precision": _entier(observation.get("precision")),
        "additional_data": json.dumps(
            {k: v for k, v in provenance.items() if v not in (None, "")},
            ensure_ascii=False),
        "lon": lon,
        "lat": lat,
        "local_srid": srid,
    }
