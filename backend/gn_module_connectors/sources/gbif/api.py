import copy
import time

import requests

GBIF_SEARCH_URL = "https://api.gbif.org/v1/occurrence/search"

# Licences GBIF. Attention à l'asymétrie de l'API :
#   - le paramètre de recherche `license=` attend l'énumération  ("CC_BY_4_0")
#   - chaque enregistrement renvoie une URL ("http://creativecommons.org/licenses/by/4.0/legalcode")
# Un filtre local comparant directement les deux ne matcherait jamais.
LICENSE_CC0 = "CC0_1_0"
LICENSE_CC_BY = "CC_BY_4_0"
LICENSE_CC_BY_NC = "CC_BY_NC_4_0"

# Licences réutilisables sans restriction d'usage commercial.
LICENSES_COMMERCIAL_OK = (LICENSE_CC0, LICENSE_CC_BY)

_LICENSE_URL_MARKERS = (
    ("/publicdomain/zero/", LICENSE_CC0),
    ("/licenses/by-nc/", LICENSE_CC_BY_NC),
    ("/licenses/by/", LICENSE_CC_BY),
)


def normalize_license(value: str | None) -> str:
    """Ramène une licence GBIF à son énumération, qu'elle arrive en URL ou déjà normalisée.

    Retourne "" si la licence est absente ou non reconnue (UNSPECIFIED, UNSUPPORTED...).
    Un appelant qui filtre doit traiter "" comme « inconnu, donc à écarter » : sans base
    d'autorisation explicite, la donnée n'est pas rediffusable.
    """
    if not value:
        return ""
    v = str(value).strip()
    if v.upper() in (LICENSE_CC0, LICENSE_CC_BY, LICENSE_CC_BY_NC):
        return v.upper()
    low = v.lower()
    # by-nc doit être testé avant by/ : "/licenses/by-nc/" contient "/licenses/by".
    for marker, enum in _LICENSE_URL_MARKERS:
        if marker in low:
            return enum
    return ""


def license_of(occ: dict) -> str:
    """Licence normalisée d'une occurrence GBIF."""
    return normalize_license(occ.get("license"))


# URL publique d'une licence, pour l'affichage et la conformité CC (la licence doit rester
# identifiable par le réutilisateur final, pas seulement par un code interne).
LICENSE_URLS = {
    LICENSE_CC0: "https://creativecommons.org/publicdomain/zero/1.0/",
    LICENSE_CC_BY: "https://creativecommons.org/licenses/by/4.0/",
    LICENSE_CC_BY_NC: "https://creativecommons.org/licenses/by-nc/4.0/",
}


def provenance(occ: dict, download_doi: str = "") -> dict:
    """Métadonnées de provenance à conserver avec chaque observation rediffusée.

    Le *Data user agreement* de GBIF impose que « l'identifiant de propriété des données
    soit conservé avec chaque enregistrement partagé en aval », et les licences CC BY /
    CC BY-NC imposent l'attribution nominative au jeu de données source. Un compteur
    global ou une mention générique « source GBIF » ne suffisent pas : il faut pouvoir
    remonter, pour une observation donnée, à son producteur et à sa licence.

    `download_doi` n'est renseigné que si les données viennent de l'API `download`.
    L'API `search` ne délivre aucun DOI — c'est l'un des arguments en faveur de `download`.
    """
    lic = license_of(occ)
    data = {
        "source": "GBIF",
        "gbif_id": str(occ.get("gbifID", "") or ""),
        "occurrence_id": occ.get("occurrenceID", "") or "",
        "dataset_key": occ.get("datasetKey", "") or "",
        "dataset_name": occ.get("datasetName", "") or "",
        "publishing_org_key": occ.get("publishingOrgKey", "") or "",
        "rights_holder": occ.get("rightsHolder", "") or "",
        "license": lic or "UNSPECIFIED",
        "license_url": LICENSE_URLS.get(lic, ""),
        "basis_of_record": occ.get("basisOfRecord", "") or "",
        # Date de modification déclarée par le PRODUCTEUR (dcterms:modified). C'est la
        # seule des quatre dates GBIF qui reflète un changement de la donnée : mesuré sur
        # l'Ariège, `modified` porte 71 valeurs distinctes sur un échantillon de 300,
        # contre 2 pour `lastInterpreted` et `lastCrawled` — ces dernières datent les
        # lots de traitement GBIF, et s'en servir réécrirait tout le corpus à chaque
        # passage du crawler.
        "gbif_modified": occ.get("modified", "") or "",
        "gbif_url": f"https://www.gbif.org/occurrence/{occ.get('gbifID')}" if occ.get("gbifID") else "",
    }
    if download_doi:
        data["gbif_download_doi"] = download_doi
    if occ.get("datasetKey"):
        data["dataset_url"] = f"https://www.gbif.org/dataset/{occ['datasetKey']}"
    return {k: v for k, v in data.items() if v}


def attribution_text(occ: dict, download_doi: str = "") -> str:
    """Mention d'attribution lisible, destinée au champ commentaire de l'observation.

    Doublon volontaire de `provenance()` : si le stockage structuré échoue ou n'est pas
    exploité par l'IHM, l'attribution reste visible par l'utilisateur final — ce que la
    licence exige.
    """
    p = provenance(occ, download_doi)
    morceaux = ["Source : GBIF"]
    if p.get("dataset_name"):
        morceaux.append(p["dataset_name"])
    if p.get("rights_holder"):
        morceaux.append(f"© {p['rights_holder']}")
    morceaux.append(p.get("license", "licence inconnue"))
    if p.get("gbif_url"):
        morceaux.append(p["gbif_url"])
    if p.get("gbif_download_doi"):
        morceaux.append(f"DOI {p['gbif_download_doi']}")
    return " | ".join(morceaux)


def _get(url: str, params: dict, timeout: int = 30, retries: int = 4, backoff: float = 3.0):
    """GET avec reprise sur erreur réseau transitoire (GBIF coupe parfois la connexion)."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            if attempt >= retries:
                raise
            wait = backoff * (attempt + 1)
            print(f"    ⚠ erreur réseau GBIF ({type(e).__name__}) — nouvelle tentative dans {wait:.0f}s "
                  f"({attempt + 1}/{retries})")
            time.sleep(wait)


def build_filters(cfg, filter_cfg=None) -> dict:
    filters = {
        "country": cfg.country,
        "hasCoordinate": cfg.has_coordinate,
        "hasGeospatialIssue": cfg.has_geospatial_issue,
        "limit": min(cfg.max_results or 300, 300),
    }
    if cfg.state_province:
        filters["stateProvince"] = cfg.state_province
    if filter_cfg and filter_cfg.coordinate_uncertainty_max:
        # ⚠ Le filtre API ne retient que les enregistrements AYANT le champ : il écarte
        # donc les occurrences sans incertitude déclarée (34,9 % du corpus ariégeois).
        # On ne le pousse à GBIF que si c'est bien ce qu'on veut ; sinon on filtre
        # localement, où l'on peut décider du sort des inconnues.
        if not getattr(filter_cfg, "keep_unknown_uncertainty", True):
            filters["coordinateUncertaintyInMeters"] = f"0,{filter_cfg.coordinate_uncertainty_max}"
    if filter_cfg and filter_cfg.include_dataset_keys:
        # Liste => params répétés (datasetKey=a&datasetKey=b), GBIF les combine en OU.
        filters["datasetKey"] = filter_cfg.include_dataset_keys
    if filter_cfg and filter_cfg.licenses:
        # Params répétés (license=a&license=b), combinés en OU par GBIF.
        filters["license"] = list(filter_cfg.licenses)
    if filter_cfg and (filter_cfg.date_min or filter_cfg.date_max):
        # Intervalle GBIF « début,fin » ; « * » laisse la borne ouverte de ce côté.
        # date_min seul redonne « AAAA-MM-JJ,* », le comportement d'avant date_max.
        debut = filter_cfg.date_min or "*"
        fin = filter_cfg.date_max or "*"
        filters["eventDate"] = f"{debut},{fin}"
    filters.update(cfg.extra)
    return filters


def count(cfg, filter_cfg=None) -> int:
    """Retourne le nombre total d'occurrences GBIF (avec filtres GBIF appliqués)."""
    filters = build_filters(cfg, filter_cfg)
    filters["limit"] = 1
    r = _get(GBIF_SEARCH_URL, filters)
    return r.json()["count"]


def count_by_dataset(cfg, dataset_key: str, filter_cfg=None) -> int:
    """Retourne le nombre d'occurrences pour un dataset spécifique."""
    filters = build_filters(cfg, filter_cfg)
    filters["limit"] = 1
    filters["datasetKey"] = dataset_key
    r = _get(GBIF_SEARCH_URL, filters)
    return r.json()["count"]


def fetch(cfg, filter_cfg=None) -> list[dict]:
    """Récupère les occurrences GBIF avec pagination."""
    filters = build_filters(cfg, filter_cfg)
    results = []
    offset = 0
    while True:
        r = _get(GBIF_SEARCH_URL, {**filters, "offset": offset})
        data = r.json()
        results.extend(data["results"])
        print(f"  GBIF : {len(results)}/{data['count']} occurrences récupérées")
        if data["endOfRecords"]:
            break
        if cfg.max_results and len(results) >= cfg.max_results:
            results = results[:cfg.max_results]
            break
        offset += filters["limit"]
        time.sleep(0.2)
    return results


# Au-delà de cet offset, GBIF bascule sur un chemin de pagination profonde : mesuré,
# 0,8 s par page en deçà, 36 s au-delà — un facteur 45. Découper la requête pour rester
# sous ce seuil n'est pas une optimisation, c'est ce qui rend un gros jeu importable.
OFFSET_LIMITE = 10_000


def facette(cfg, filter_cfg, champ: str, limite: int = 300) -> list[tuple[str, int]]:
    """Répartition des occurrences selon un champ, sans les rapatrier."""
    filtres = build_filters(cfg, filter_cfg)
    filtres.update({"limit": 0, "facet": champ, "facetLimit": limite})
    r = _get(GBIF_SEARCH_URL, filtres)
    facettes = r.json().get("facets") or []
    if not facettes:
        return []
    return [(c["name"], c["count"]) for c in facettes[0].get("counts", [])]


def _regroupe(valeurs: list[tuple[int, int]], plafond: int) -> list[tuple[int, int, int]]:
    """Regroupe des valeurs consécutives en tranches dont le cumul reste sous `plafond`.

    Retourne [(début, fin, cumul)]. Une valeur qui dépasse à elle seule le plafond forme
    sa propre tranche : c'est à l'appelant de la subdiviser au niveau inférieur.
    """
    tranches, debut, cumul, precedent = [], None, 0, None
    for valeur, n in sorted(valeurs):
        if debut is None:
            debut, cumul, precedent = valeur, n, valeur
            continue
        if cumul + n > plafond:
            tranches.append((debut, precedent, cumul))
            debut, cumul, precedent = valeur, n, valeur
        else:
            cumul += n
            precedent = valeur
    if debut is not None:
        tranches.append((debut, precedent, cumul))
    return tranches


def _facette_int(cfg, filter_cfg, champ: str, extra: dict) -> list[tuple[int, int]]:
    cfg_local = copy.copy(cfg)
    cfg_local.extra = {**(cfg.extra or {}), **extra}
    return [(int(v), n) for v, n in facette(cfg_local, filter_cfg, champ) if str(v).lstrip("-").isdigit()]


def tranches(cfg, filter_cfg, plafond: int = OFFSET_LIMITE) -> list[dict]:
    """Découpe le jeu en requêtes dont aucune ne dépasse `plafond` résultats.

    Découpage par années, puis par mois pour les années trop volumineuses, puis par jours
    pour les mois qui le seraient encore. Deux niveaux suffisent en pratique — mesuré,
    l'année la plus chargée d'un gros jeu ariégeois compte 37 440 occurrences, dont le
    mois le plus fourni n'en fait que 4 582 — mais le troisième évite d'échouer en
    silence sur un jeu atypique.

    Retourne une liste de filtres additionnels à appliquer, par exemple
    `{"year": "1973,2019"}` ou `{"year": "2022", "month": "1,6"}`.
    """
    resultat: list[dict] = []
    annees = _facette_int(cfg, filter_cfg, "year", {})
    if not annees:
        return []

    for a_debut, a_fin, cumul in _regroupe(annees, plafond):
        if cumul <= plafond:
            resultat.append({"year": f"{a_debut},{a_fin}"})
            continue

        # Une seule année dépasse le plafond : on descend au mois.
        mois = _facette_int(cfg, filter_cfg, "month", {"year": str(a_debut)})
        if not mois:
            resultat.append({"year": f"{a_debut},{a_fin}"})
            continue

        for m_debut, m_fin, cumul_mois in _regroupe(mois, plafond):
            if cumul_mois <= plafond:
                resultat.append({"year": str(a_debut), "month": f"{m_debut},{m_fin}"})
                continue

            # Un seul mois dépasse encore : on descend au jour.
            jours = _facette_int(cfg, filter_cfg, "day",
                                 {"year": str(a_debut), "month": str(m_debut)})
            if not jours:
                resultat.append({"year": str(a_debut), "month": f"{m_debut},{m_fin}"})
                continue
            for j_debut, j_fin, _ in _regroupe(jours, plafond):
                resultat.append({"year": str(a_debut), "month": str(m_debut),
                                 "day": f"{j_debut},{j_fin}"})
    return resultat


def fetch_par_tranches(cfg, filter_cfg=None, journal=None) -> list[dict]:
    """Récupère toutes les occurrences en restant sous le plafond de pagination.

    En dessous du plafond, une pagination simple suffit. Au-delà, la requête est
    découpée par années : chaque tranche se pagine alors dans la zone rapide.
    """
    total = count(cfg, filter_cfg)
    if total <= OFFSET_LIMITE:
        return fetch(cfg, filter_cfg)

    decoupe = tranches(cfg, filter_cfg)
    if not decoupe:
        # Sans année exploitable, on ne peut pas découper : on rapatrie ce qui est
        # accessible dans la zone rapide plutôt que de subir la pagination profonde.
        if journal:
            journal(f"  ⚠ {total} occurrences sans année exploitable : seules les "
                    f"{OFFSET_LIMITE} premières seront lues.")
        cfg_plafonne = copy.copy(cfg)
        cfg_plafonne.max_results = OFFSET_LIMITE
        return fetch(cfg_plafonne, filter_cfg)

    if journal:
        journal(f"  {total} occurrences — découpage en {len(decoupe)} tranche(s) "
                f"pour rester sous le plafond de pagination")

    resultats: list[dict] = []
    for filtres_tranche in decoupe:
        cfg_tranche = copy.copy(cfg)
        cfg_tranche.extra = {**(cfg.extra or {}), **filtres_tranche}
        lot = fetch(cfg_tranche, filter_cfg)
        resultats.extend(lot)
        if journal:
            libelle = " ".join(f"{k}={v}" for k, v in filtres_tranche.items())
            journal(f"    {libelle} : {len(lot)} occurrence(s)")
        if cfg.max_results and len(resultats) >= cfg.max_results:
            return resultats[: cfg.max_results]
    return resultats


def list_datasets(cfg, filter_cfg=None, with_titles: bool = False) -> list[dict]:
    """Liste les jeux de données GBIF avec leur nombre d'occurrences (facette datasetKey)."""
    filters = build_filters(cfg, filter_cfg)
    filters["limit"] = 0
    filters["facet"] = "datasetKey"
    filters["facetLimit"] = 200
    r = _get(GBIF_SEARCH_URL, filters)
    facets = r.json().get("facets", [])
    if not facets:
        return []
    results = [
        {"key": f["name"], "count": f["count"], "title": ""}
        for f in sorted(facets[0].get("counts", []), key=lambda x: -x["count"])
    ]
    if with_titles:
        print(f"  Récupération des titres ({len(results)} datasets)...")
        for ds in results:
            try:
                rd = requests.get(f"https://api.gbif.org/v1/dataset/{ds['key']}", timeout=10)
                ds["title"] = rd.json().get("title", "") if rd.status_code == 200 else ""
            except Exception:
                pass
            time.sleep(0.05)
    return results


def filter_occurrences(occurrences: list[dict], exclude_terms: list[str], rejects=None) -> list[dict]:
    """Exclut les occurrences dont le datasetName contient un terme indésirable."""
    if not exclude_terms:
        return occurrences
    filtered = []
    excluded = 0
    for occ in occurrences:
        dataset_name = (occ.get("datasetName") or "").lower()
        if any(term in dataset_name for term in exclude_terms):
            excluded += 1
            if rejects is not None:
                rejects.add("dataset_excluded", occ.get("gbifID"),
                            occ.get("scientificName"), occ.get("datasetName") or "")
        else:
            filtered.append(occ)
    if excluded:
        print(f"  Exclusion datasets : {excluded} occurrence(s) filtrée(s), reste {len(filtered)}.")
    return filtered


def filter_by_dataset_keys(occurrences: list[dict], exclude_keys: list[str], rejects=None) -> list[dict]:
    """Exclut les occurrences appartenant à un des datasetKey indésirables."""
    if not exclude_keys:
        return occurrences
    exclude = set(exclude_keys)
    filtered = [o for o in occurrences if o.get("datasetKey") not in exclude]
    excluded = len(occurrences) - len(filtered)
    if rejects is not None:
        for o in occurrences:
            if o.get("datasetKey") in exclude:
                rejects.add("dataset_excluded", o.get("gbifID"),
                            o.get("scientificName"), o.get("datasetKey"))
    if excluded:
        print(f"  Exclusion datasetKey : {excluded} occurrence(s) filtrée(s), reste {len(filtered)}.")
    return filtered


def filter_by_observers(occurrences: list[dict], include_observers: list[str], rejects=None) -> list[dict]:
    """Ne garde que les occurrences dont le recordedBy contient un des observateurs voulus
    (insensible à la casse, sous-chaîne)."""
    if not include_observers:
        return occurrences
    filtered = []
    for occ in occurrences:
        recorded_by = (occ.get("recordedBy") or "").lower()
        if any(obs in recorded_by for obs in include_observers):
            filtered.append(occ)
        elif rejects is not None:
            rejects.add("observer_excluded", occ.get("gbifID"),
                        occ.get("scientificName"), occ.get("recordedBy") or "")
    excluded = len(occurrences) - len(filtered)
    if excluded:
        print(f"  Filtre observateurs : {excluded} occurrence(s) écartée(s), reste {len(filtered)}.")
    return filtered


def filter_by_uncertainty(occurrences: list[dict], max_uncertainty: int | None, rejects=None,
                          keep_unknown: bool = True) -> list[dict]:
    """Écarte les occurrences dont l'incertitude géographique dépasse le seuil (mètres).

    `keep_unknown` décide du sort des occurrences **sans incertitude déclarée** — un tiers
    du corpus ariégeois. Les garder, c'est accepter une précision inconnue, qui peut être
    pire que le seuil rejeté ; les écarter, c'est perdre beaucoup de données par ailleurs
    exploitables. Le choix appartient à l'utilisateur, mais il doit être conscient : c'est
    pourquoi il est explicite et non implicite.
    """
    if not max_uncertainty:
        return occurrences

    def _ok(o):
        u = o.get("coordinateUncertaintyInMeters")
        if u is None:
            return keep_unknown
        return float(u or 0) < max_uncertainty

    filtered = [o for o in occurrences if _ok(o)]
    excluded = len(occurrences) - len(filtered)
    if rejects is not None:
        for o in occurrences:
            if not _ok(o):
                u = o.get("coordinateUncertaintyInMeters")
                rejects.add("uncertainty_too_high", o.get("gbifID"), o.get("scientificName"),
                            f"{u} m" if u is not None else "incertitude non déclarée")
    if excluded:
        print(f"  Filtre incertitude GPS (<{max_uncertainty}m) : {excluded} exclue(s), reste {len(filtered)}.")
    return filtered


def filter_by_license(occurrences: list[dict], allowed: list[str], rejects=None) -> list[dict]:
    """Ne garde que les occurrences dont la licence est dans `allowed`.

    Indispensable même quand le filtre API `license=` est posé : sur iNaturalist la licence
    est choisie observation par observation, et une licence non reconnue (URL exotique,
    UNSPECIFIED) doit être écartée plutôt que supposée permissive.
    """
    if not allowed:
        return occurrences
    allowed_set = {l.upper() for l in allowed}
    filtered = [o for o in occurrences if license_of(o) in allowed_set]
    excluded = len(occurrences) - len(filtered)
    if rejects is not None:
        for o in occurrences:
            if license_of(o) not in allowed_set:
                rejects.add("license_excluded", o.get("gbifID"), o.get("scientificName"),
                            license_of(o) or "<inconnue>")
    if excluded:
        print(f"  Filtre licence ({', '.join(sorted(allowed_set))}) : {excluded} écartée(s), "
              f"reste {len(filtered)}.")
    return filtered


# Champs portant la position du taxon dans la hiérarchie GBIF. Exclure un ordre suppose
# de le reconnaître aussi bien sur l'occurrence elle-même que sur ses rangs supérieurs :
# une observation identifiée à l'espèce ne porte pas l'ordre dans `taxonKey`.
CLES_TAXONOMIQUES = (
    "taxonKey", "acceptedTaxonKey", "speciesKey", "genusKey", "familyKey",
    "orderKey", "classKey", "phylumKey", "kingdomKey",
)


def filter_by_taxa(occurrences: list[dict], exclus: set, rejects=None) -> list[dict]:
    """Écarte les occurrences appartenant à l'un des taxons exclus, descendants compris.

    GBIF n'offre pas de négation sur `taxonKey` : le filtre est donc local. Il teste
    toute la hiérarchie de l'occurrence, si bien qu'exclure l'ordre Chiroptera (734)
    écarte aussi bien un *Rhinolophus ferrumequinum* identifié à l'espèce qu'une
    observation restée au rang du genre.
    """
    if not exclus:
        return occurrences
    exclus = {int(x) for x in exclus}

    def _exclu(o):
        return any(o.get(c) in exclus for c in CLES_TAXONOMIQUES)

    gardees = [o for o in occurrences if not _exclu(o)]
    ecartees = len(occurrences) - len(gardees)
    if ecartees:
        print(f"  Filtre taxonomique : {ecartees} écartée(s), reste {len(gardees)}.")
        if rejects is not None:
            for o in occurrences:
                if _exclu(o):
                    rejects.add("taxon_exclu", o.get("gbifID"), o.get("scientificName"),
                                f"taxonKey={o.get('taxonKey')}")
    return gardees


def apply_local_filters(occurrences: list[dict], filter_cfg, rejects=None) -> list[dict]:
    """Applique tous les filtres locaux (post-fetch) appliqués à l'import :
    termes de dataset exclus, datasetKey exclus, observateurs inclus, incertitude GPS,
    licence."""
    occurrences = filter_occurrences(occurrences, filter_cfg.exclude_dataset_terms, rejects)
    occurrences = filter_by_dataset_keys(occurrences, filter_cfg.exclude_dataset_keys, rejects)
    occurrences = filter_by_observers(occurrences, filter_cfg.include_observers, rejects)
    occurrences = filter_by_uncertainty(
        occurrences, filter_cfg.coordinate_uncertainty_max, rejects,
        keep_unknown=getattr(filter_cfg, "keep_unknown_uncertainty", True),
    )
    occurrences = filter_by_license(occurrences, filter_cfg.licenses, rejects)
    occurrences = filter_by_taxa(occurrences, getattr(filter_cfg, "exclude_taxon_keys", None) or set(), rejects)
    return occurrences
