"""Métadonnées des jeux de données GBIF.

GBIF fournit tout ce qu'il faut pour construire un JDD GeoNature conforme : titre,
citation officielle, DOI propre au jeu, licence et organisation publiante. La citation
est la pièce maîtresse — c'est la chaîne d'attribution exacte qu'exigent CC BY et
CC BY-NC, et la porter dans `dataset_desc` satisfait l'obligation par la métadonnée
elle-même, sans dépendre d'un champ JSON qu'aucune interface n'affiche.
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)

API = "https://api.gbif.org/v1"

# Licences que GBIF accepte pour les jeux d'occurrences (l'énumération est figée depuis 2017).
_LICENCE_MARQUEURS = (
    ("/publicdomain/zero/", "CC0_1_0"),
    ("/licenses/by-nc/", "CC_BY_NC_4_0"),  # avant by/ : "/licenses/by-nc/" contient "/licenses/by"
    ("/licenses/by/", "CC_BY_4_0"),
)


def _get(chemin: str, params: dict | None = None, timeout: int = 25, retries: int = 3):
    derniere = None
    for tentative in range(retries + 1):
        try:
            r = requests.get(f"{API}/{chemin.lstrip('/')}", params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            derniere = e
            if tentative < retries:
                time.sleep(2 * (tentative + 1))
    raise derniere


def normalize_license(valeur: str | None) -> str:
    if not valeur:
        return ""
    v = str(valeur).strip()
    if v.upper() in ("CC0_1_0", "CC_BY_4_0", "CC_BY_NC_4_0"):
        return v.upper()
    bas = v.lower()
    for marqueur, enum in _LICENCE_MARQUEURS:
        if marqueur in bas:
            return enum
    return ""


def fetch_dataset(dataset_key: str) -> dict:
    """Métadonnées normalisées d'un jeu de données GBIF."""
    d = _get(f"dataset/{dataset_key}")
    org_key = d.get("publishingOrganizationKey") or ""
    return {
        "key": dataset_key,
        "title": (d.get("title") or "").strip(),
        "description": (d.get("description") or "").strip(),
        "citation": ((d.get("citation") or {}).get("text") or "").strip(),
        "doi": d.get("doi") or "",
        # Date de dernière modification du jeu dans le registre GBIF. C'est le signal
        # qui permet de sauter un jeu inchangé sans lire une seule occurrence.
        "modified": d.get("modified") or "",
        "license": normalize_license(d.get("license")),
        "publishing_org_key": org_key,
        "url": f"https://www.gbif.org/dataset/{dataset_key}",
    }


def fetch_organization(org_key: str) -> str:
    """Nom de l'organisation publiante, ou chaîne vide si introuvable."""
    if not org_key:
        return ""
    try:
        return (_get(f"organization/{org_key}").get("title") or "").strip()
    except (requests.exceptions.RequestException, AttributeError) as e:
        # Résilience voulue : une organisation introuvable ne doit pas interrompre
        # l'import — mais jusqu'ici l'échec ne laissait aucune trace.
        logger.warning("Organisation GBIF %s introuvable : %s", org_key, e)
        return ""


def list_dataset_keys(filtres: dict, facet_limit: int = 200) -> list[dict]:
    """Jeux de données présents dans un périmètre, par la facette `datasetKey`.

    Pagine au-delà de `facet_limit` via `facetOffset` : sans quoi les producteurs les
    moins volumineux (GBIF trie la facette par effectif décroissant) sortiraient de la
    liste dès que le périmètre compte plus de `facet_limit` jeux distincts — silencieux,
    et durable puisque ces jeux ne seraient alors ni importés, ni synchronisés.

    Retourne [{"key": ..., "count": ...}] trié par volume décroissant.
    """
    resultats: list[dict] = []
    offset = 0
    while True:
        params = {**filtres, "limit": 0, "facet": "datasetKey", "facetLimit": facet_limit,
                  "facetOffset": offset}
        data = _get("occurrence/search", params=params, timeout=60)
        facettes = data.get("facets") or []
        if not facettes:
            break
        counts = facettes[0].get("counts", [])
        if not counts:
            break
        resultats.extend({"key": f["name"], "count": f["count"]} for f in counts)
        if len(counts) < facet_limit:
            break
        offset += facet_limit
    return sorted(resultats, key=lambda x: -x["count"])
