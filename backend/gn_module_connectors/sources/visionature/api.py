"""Accès à l'API Biolovision, au-dessus du client vendorisé.

Le client de `biolovision/` n'est pas modifié : cette couche l'adapte aux besoins du
module — construction depuis la configuration, dépliage des relevés, et incrémental.

L'incrémental mérite d'être souligné : `api_diff` renvoie les créations, les
modifications **et les suppressions** depuis une date. VisioNature sait donc dire ce qui
a été effacé, ce que GBIF ne sait pas faire — le connecteur GBIF ne peut que constater
l'absence d'une occurrence, jamais sa suppression.
"""

from .biolovision import api as bio


def _controleur(classe, cfg):
    return classe(
        user_email=cfg["user_email"],
        user_pw=cfg["user_password"],
        base_url=cfg["url"].rstrip("/") + "/",
        client_key=cfg["client_key"],
        client_secret=cfg["client_secret"],
        max_retry=cfg.get("max_retry", 3),
        max_requests=cfg.get("max_requests", 0),
        max_chunks=cfg.get("max_chunks", 100),
    )


def especes(cfg) -> list[dict]:
    """Référentiel d'espèces complet de l'instance."""
    reponse = _controleur(bio.SpeciesAPI, cfg).api_list()
    return reponse.get("data") or []


def observateurs(cfg) -> list[dict]:
    """Référentiel des observateurs de l'instance.

    Porte le champ `anonymous` : le consentement individuel de chaque contributeur à
    voir son nom diffusé. Chargé une fois au démarrage, comme le référentiel d'espèces.
    """
    reponse = _controleur(bio.ObserversAPI, cfg).api_list()
    return reponse.get("data") or []


def groupes_taxonomiques(cfg) -> list[dict]:
    """Groupes taxonomiques — l'API impose de les parcourir un par un."""
    reponse = _controleur(bio.TaxoGroupsAPI, cfg).api_list()
    return reponse.get("data") or []


def _extraire(reponse: dict) -> list[dict]:
    """Relevés d'une réponse, formulaires compris.

    Biolovision range les observations issues d'un formulaire — une liste complète, un
    protocole — sous `forms`, séparément des `sightings` isolés. N'aller chercher que ces
    derniers perdrait l'essentiel des données protocolées.
    """
    data = reponse.get("data") or {}
    if isinstance(data, list):
        return list(data)
    releves = list(data.get("sightings") or [])
    for formulaire in data.get("forms") or []:
        releves.extend(formulaire.get("sightings") or [])
    return releves


def observations(cfg, id_taxo_group: str, **filtres) -> list[dict]:
    """Observations d'un groupe taxonomique."""
    return _extraire(_controleur(bio.ObservationsAPI, cfg).api_list(id_taxo_group, **filtres))


def observations_modifiees(cfg, id_taxo_group: str, depuis: str,
                           type_modification: str = "all") -> list[dict]:
    """Créations, modifications et suppressions depuis `depuis` (ISO 8601).

    `type_modification` vaut « all », « only_modified » ou « only_deleted ». Les
    suppressions se reconnaissent au champ `id_sighting` accompagné de l'absence de
    données : c'est ainsi qu'on peut retirer de la Synthèse une observation effacée à la
    source, ce qu'aucun autre connecteur du module ne sait faire.
    """
    return _extraire(
        _controleur(bio.ObservationsAPI, cfg).api_diff(id_taxo_group, depuis, type_modification)
    )
