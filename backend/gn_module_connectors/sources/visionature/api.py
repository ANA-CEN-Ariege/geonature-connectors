"""Accès à l'API Biolovision, au-dessus du client vendorisé.

Cette couche adapte le client aux besoins du module — construction depuis la
configuration, dépliage des relevés, incrémental, et tolérance aux refus de l'API.

Le client de `biolovision/` ne porte qu'une seule divergence avec l'amont, documentée
dans le README : la transmission de `timeout` par les sous-classes, sans laquelle
`requests` attendait indéfiniment. Toute autre adaptation va ici.

⚠ Les droits d'accès ne sont pas uniformes : un compte peut avoir le différentiel sans
avoir la liste complète, et un groupe taxonomique peut être refusé quand les autres
passent. Un 403 est donc une information, pas une panne — il est remonté à l'appelant,
jamais laissé interrompre le moissonnage des autres groupes.

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


def _extraire(reponse) -> list[dict]:
    """Relevés d'une réponse, formulaires compris.

    Biolovision range les observations issues d'un formulaire — une liste complète, un
    protocole — sous `forms`, séparément des `sightings` isolés. N'aller chercher que ces
    derniers perdrait l'essentiel des données protocolées.

    ⚠ Trois formes de réponse coexistent, et le client vendorisé les annonce toutes comme
    « dict or None » dans ses docstrings :
      - `{"data": {"sightings": [...], "forms": [...]}}` — `api_list`, `api_search` ;
      - `{"data": [...]}` ;
      - `[...]` — **`api_diff` renvoie une liste nue**, sans enveloppe `data`.
    La troisième faisait échouer `vn-import --since` sur un `AttributeError: 'list'
    object has no attribute 'get'`, au premier groupe traité.
    """
    if isinstance(reponse, list):
        return list(reponse)
    if not isinstance(reponse, dict):
        return []
    data = reponse.get("data") or {}
    if isinstance(data, list):
        return list(data)
    releves = list(data.get("sightings") or [])
    for formulaire in data.get("forms") or []:
        releves.extend(formulaire.get("sightings") or [])
    return releves


def unites_territoriales(cfg) -> list[dict]:
    """Unités territoriales de l'instance : identifiant, nom et `short_name`.

    Le `short_name` est le code employé pour filtrer — sur les instances régionales
    françaises, c'est le code de département. La configuration de `Client_API_VN` le dit
    explicitement : « use the territory short_name, not the territory id ».
    """
    return _extraire(_controleur(bio.TerritorialUnitsAPI, cfg).api_list())


# `short_version=1` demande la forme réduite du JSON. `transfer_vn` la passe à TOUS ses
# appels d'observations, et la configuration de `gn_vn2synthese` la retient
# (`json_format: short`). Son absence est la première suspecte des 403 obtenus sur
# `GET /api/observations` : la forme longue d'un groupe taxonomique entier représente un
# volume que l'API peut légitimement refuser de servir.
#
# La forme réduite suffit : c'est elle qui alimente la table de transit de la LPO, dont
# le SQL lit `observers[0].details`, `behaviours`, `place` et le reste.
SHORT_VERSION = "1"


def parametres_recherche(id_taxo_group: str, date_debut, date_fin,
                         territoires: list[str] | None = None,
                         type_date: str | None = None) -> dict:
    """Corps de requête de `observations/search`, au format qu'attend Biolovision.

    Relevé sur `transfer_vn` (`download_vn.py`, `_store_search`) plutôt que deviné. Deux
    pièges qui font échouer une requête bricolée :

    - les dates sont au format **`JJ.MM.AAAA`**, pas ISO ;
    - `period_choice` est obligatoire, sans quoi les dates sont ignorées ou la requête
      refusée.

    `territoires` attend des identifiants déjà composés — `id_country` suivi du
    `short_name`, soit « 109 » pour l'Ariège sur un portail français. C'est la forme que
    `transfer_vn` construit à partir du contrôleur `territorial_units`.
    """
    parametres = {
        "period_choice": "range",
        "date_from": date_debut.strftime("%d.%m.%Y"),
        "date_to": date_fin.strftime("%d.%m.%Y"),
        "species_choice": "all",
        "taxonomic_group": str(id_taxo_group),
    }
    if type_date is not None:
        parametres["entry_date"] = "1" if type_date == "entry" else "0"
    if territoires:
        parametres["location_choice"] = "territorial_unit"
        parametres["territorial_unit_ids"] = list(territoires)
    return parametres


def identifiant_territoire(unite: dict) -> str | None:
    """Identifiant de territoire pour `search` : `id_country` + `short_name`.

    Ni l'`id` ni le `short_name` seuls ne conviennent — c'est leur concaténation que
    `transfer_vn` envoie.
    """
    pays = str(unite.get("id_country") or "").strip()
    court = str(unite.get("short_name") or "").strip()
    return f"{pays}{court}" if pays and court else None


def observations_recherche(cfg, id_taxo_group: str, date_debut, date_fin,
                           territoires: list[str] | None = None) -> list[dict]:
    """Observations sur une plage de dates, via `observations/search`.

    C'est la voie du moissonnage initial : `api_list` ne sait pas borner par date, et le
    différentiel ne remonte que dix semaines. Le découpage en tranches est à la charge de
    l'appelant, comme pour le connecteur GBIF.
    """
    return _extraire(_controleur(bio.ObservationsAPI, cfg).api_search(
        parametres_recherche(id_taxo_group, date_debut, date_fin, territoires),
        short_version=SHORT_VERSION))


def observations(cfg, id_taxo_group: str, **filtres) -> list[dict]:
    """Observations d'un groupe taxonomique.

    `filtres` est transmis tel quel à l'API comme paramètres d'URL. C'est par là que
    passe une éventuelle restriction territoriale côté serveur — la seule qui évite de
    télécharger l'instance entière.

    ⚠ Un paramètre que l'API ne connaît pas est **ignoré en silence** : rien ne distingue
    un filtre appliqué d'un filtre inexistant. C'est pourquoi le filtre serveur ne fait
    jamais foi à lui seul, et que `perimetre.dans_perimetre` revérifie chaque relevé sur
    `place.county`. Le décompte des rejets « hors périmètre » dit alors si le filtre
    serveur a mordu : proche de zéro, il a fonctionné ; élevé, il a été ignoré.
    """
    filtres.setdefault("short_version", SHORT_VERSION)
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
    return [cle for cle in (identifiant(e) for e in _extraire(reponse)) if cle]


def est_releve_complet(entree: dict) -> bool:
    """L'entrée porte-t-elle la donnée, ou seulement un identifiant ?

    `observations/diff` ne renvoie pas les observations : il renvoie la liste de ce qui a
    changé, sous forme d'enregistrements réduits à un identifiant et un type de
    modification. Le confondre avec un relevé complet est silencieux et coûteux — le
    dépliage ne trouve pas de clé `observers`, ne produit aucun couple, et l'incrémental
    annonce « 0 observation » sans que rien ne signale l'erreur.
    """
    return bool(entree.get("observers") or entree.get("species"))


def identifiant(entree: dict) -> str | None:
    """Identifiant de relevé d'une entrée de diff, quel que soit le champ employé."""
    valeur = (entree.get("id_sighting") or entree.get("@id")
              or entree.get("id_universal"))
    return str(valeur) if valeur else None


def observations_modifiees(cfg, id_taxo_group: str, depuis: str,
                           type_modification: str = "only_modified"
                           ) -> tuple[list[dict], list[tuple[str, str]]]:
    """Relevés créés ou modifiés depuis `depuis`, et la liste des inaccessibles.

    Le différentiel ne livrant que des identifiants, chaque relevé signalé est ensuite
    récupéré par `api_get`. C'est une requête par relevé : acceptable pour un incrémental,
    dont c'est le propre de ne porter que sur un delta, mais c'est aussi la raison pour
    laquelle `--since` ne remplace pas un moissonnage complet.

    Le défaut est `only_modified` et non « all » : les suppressions sont traitées à part,
    par `observations_supprimees`, et les inclure ici ferait tenter la récupération de
    relevés qui n'existent plus.

    ⚠ Un relevé peut être listé par le différentiel sans être lisible individuellement :
    l'API répond alors 403, et le client vendorisé traite tout 4xx comme irrécupérable en
    levant `HTTPError`. Sans le rattrapage ci-dessous, **une seule observation protégée
    fait échouer le moissonnage entier** — mesuré sur faune-occitanie.org, où l'import est
    tombé au premier groupe sur l'observation 88724785.

    Un relevé inaccessible est donc une donnée manquante, pas une panne : il est retourné
    à l'appelant avec son motif, à charge pour lui de le journaliser. L'ignorer en silence
    serait pire que l'erreur — on ne saurait pas ce qu'on n'a pas.
    """
    controleur = _controleur(bio.ObservationsAPI, cfg)
    entrees = _extraire(controleur.api_diff(id_taxo_group, depuis, type_modification))

    releves, inaccessibles = [], []
    for entree in entrees:
        if est_releve_complet(entree):
            releves.append(entree)
            continue
        cle = identifiant(entree)
        if not cle:
            continue
        try:
            releves.extend(_extraire(controleur.api_get(cle)))
        except bio.HTTPError as erreur:
            inaccessibles.append((cle, f"HTTP {erreur}"))
        except bio.BiolovisionApiException as erreur:
            inaccessibles.append((cle, str(erreur) or type(erreur).__name__))
    return releves, inaccessibles
