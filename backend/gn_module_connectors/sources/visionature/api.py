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
    """Instancie un contrôleur Biolovision.

    ⚠ `timeout` et `unavailable_delay` doivent être fournis explicitement. Dans le client
    vendorisé, `timeout` est le seul paramètre du constructeur qui ne reçoive aucune
    valeur par défaut : il reste à `None`, et `requests` attend alors **indéfiniment**.
    Un incident réseau ou une API qui ne répond pas fige le moissonnage sans le moindre
    message, puisque le client journalise dans un logger que la CLI n'affiche pas.

    `unavailable_delay` vaut 600 s en amont : sur une réponse 503, le client dort dix
    minutes avant de réessayer, jusqu'à `max_retry` fois. Une demi-heure de gel apparent
    pour un service momentanément indisponible est disproportionné en usage interactif.
    """
    return classe(
        user_email=cfg["user_email"],
        user_pw=cfg["user_password"],
        base_url=cfg["url"].rstrip("/") + "/",
        client_key=cfg["client_key"],
        client_secret=cfg["client_secret"],
        max_retry=cfg.get("max_retry", 3),
        max_requests=cfg.get("max_requests", 0),
        max_chunks=cfg.get("max_chunks", 100),
        timeout=cfg.get("timeout", 120),
        unavailable_delay=cfg.get("unavailable_delay", 60),
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


# L'API refuse un diff au-delà de cette ancienneté. Passé ce délai, l'incrémental n'est
# plus possible : il faut un moissonnage complet, sans quoi les créations et suppressions
# de l'intervalle seraient perdues en silence.
DIFF_MAX_SEMAINES = 10


def diff_possible(depuis: str) -> bool:
    """Le diff couvre-t-il encore cette date ?"""
    from datetime import datetime, timedelta, timezone
    try:
        d = datetime.fromisoformat(str(depuis).replace("Z", "+00:00"))
    except ValueError:
        return False
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d >= datetime.now(timezone.utc) - timedelta(weeks=DIFF_MAX_SEMAINES)


def observations_supprimees(cfg, id_taxo_group: str, depuis: str) -> list[str]:
    """Identifiants des relevés supprimés à la source depuis `depuis`.

    C'est ce que GBIF ne sait pas faire : une occurrence retirée y devient simplement
    absente des résultats, indiscernable d'une occurrence hors périmètre. VisioNature
    signale explicitement la suppression, ce qui permet de la répercuter.
    """
    reponse = _controleur(bio.ObservationsAPI, cfg).api_diff(
        id_taxo_group, depuis, "only_deleted")
    identifiants = []
    for entree in _extraire(reponse):
        valeur = (entree.get("id_sighting") or entree.get("@id")
                  or entree.get("id_universal"))
        if valeur:
            identifiants.append(str(valeur))
    return identifiants


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
