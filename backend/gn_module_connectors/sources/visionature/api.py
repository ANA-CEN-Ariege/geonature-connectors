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


class GroupeInaccessible(Exception):
    """Un groupe taxonomique dont l'API refuse systématiquement les données.

    Distincte d'une erreur ponctuelle : elle signale qu'insister est inutile, et porte
    les identifiants qui n'ont pas même été demandés.
    """


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
        # Leur instance rend des 502 et 504 sous charge. Le client attend `retry_delay`
        # entre deux tentatives et n'abandonne qu'au-delà de `max_retry` ; cinq essais
        # coûtent quelques secondes et évitent de perdre un moissonnage sur un incident.
        max_retry=cfg.get("max_retry", 5),
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
# ⚠ « 1 » demande la forme RÉDUITE, et elle est bien plus réduite qu'il n'y paraît.
# Relevé sur faune-occitanie.org, `observers[]` n'y porte que :
#   @id, @uid, altitude, comment, coord_lat, coord_lon, count, estimation_code,
#   flight_number, gps_lat, gps_lon, hidden, id_sighting, id_universal
# Manquent donc `atlas_code`, `details`, `behaviours`, `timing`, `uuid`, `medias`,
# `extended_info`, `project_code`, `second_hand` — et `name`, le nom de l'observateur.
# Autrement dit : ni statut de reproduction, ni heure, ni identifiant SINP natif, ni
# mortalité, ni preuve d'existence, ni jeu de données par projet, ni observateur nommé.
#
# Le module retient donc la forme LONGUE par défaut. `transfer_vn` recommande la courte
# parce que sa table de transit alimente ensuite un SQL qui lit... `details` et
# `behaviours` : la recommandation vaut pour leur volumétrie, pas pour nos besoins.
SHORT_VERSION = "0"


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
                           territoires: list[str] | None = None,
                           type_date: str | None = None) -> list[dict]:
    """Observations sur une plage de dates, via `observations/search`.

    C'est la voie du moissonnage initial : `api_list` ne sait pas borner par date, et le
    différentiel ne remonte que dix semaines. Le découpage en tranches est à la charge de
    l'appelant, comme pour le connecteur GBIF.
    """
    return _extraire(_controleur(bio.ObservationsAPI, cfg).api_search(
        parametres_recherche(id_taxo_group, date_debut, date_fin, territoires,
                             type_date),
        short_version=SHORT_VERSION))


# Cible de volume par requête, et taille de tranche initiale. `transfer_vn` régule la
# tranche par un PID visant 10 000 observations ; on se contente d'un ajustement
# proportionnel, plus simple à lire et suffisant pour l'usage.
CIBLE_PAR_TRANCHE = 10_000
TRANCHE_JOURS_DEFAUT = 15
TRANCHE_JOURS_MIN = 1
TRANCHE_JOURS_MAX = 365

# Nombre de rétrécissements consentis face à un 403 avant d'abandonner.
#
# ⚠ Deux 403 distincts coexistent, et ils ne veulent pas dire la même chose. Relevé sur
# faune-occitanie.org :
#   - corps « you are not authorized to access this taxonomic group » : refus de droit
#     explicite, obtenu exactement sur les groupes dont `access_mode` vaut « none » ;
#   - corps VIDE : autre chose. Obtenu sur des groupes en `access_mode = full`, y compris
#     des groupes minuscules — 74 modifications quotidiennes pour les chiroptères sur
#     TOUTE l'Occitanie, donc une poignée en Ariège. Le volume ne l'explique donc pas.
#
# Rétrécir la tranche n'aide que si le refus tient au volume. Comme rien ne l'établit, on
# s'y essaie deux fois — c'est peu coûteux — puis on remonte l'erreur au lieu de diviser
# indéfiniment une plage qui ne sera jamais servie.
ESSAIS_403 = 2


def _ajuster(tranche: int, obtenus: int) -> int:
    """Nouvelle taille de tranche, d'après le volume qu'a rendu la précédente.

    Une tranche trop large risque de heurter le plafond de pagination du client et de
    tronquer en silence ; une tranche trop étroite multiplie les allers-retours.
    """
    if obtenus > CIBLE_PAR_TRANCHE * 1.5:
        tranche = max(TRANCHE_JOURS_MIN, tranche // 2)
    elif obtenus < CIBLE_PAR_TRANCHE // 4:
        tranche = min(TRANCHE_JOURS_MAX, max(tranche * 2, tranche + 1))
    return tranche


def moissonner_recherche(cfg, id_taxo_group: str, date_debut, date_fin,
                         territoires: list[str], tranche_jours: int = TRANCHE_JOURS_DEFAUT,
                         journal=None, type_date: str | None = None):
    """Parcourt une période par tranches décroissantes, et livre les relevés.

    Générateur de `(debut, fin, territoire, releves)`, pour que l'appelant écrive au fil
    de l'eau plutôt que d'accumuler des centaines de milliers d'observations en mémoire.

    ⚠ `territoires` ne peut pas être vide. Une recherche non bornée territorialement est
    **refusée par l'API** — mesuré sur faune-occitanie.org, 403 sans périmètre et 200
    avec. `transfer_vn` n'en émet d'ailleurs jamais : sa boucle pose systématiquement
    `location_choice` et `territorial_unit_ids`.

    Le parcours va de la fin vers le début, comme `transfer_vn` : les données récentes,
    les plus utiles, arrivent en premier, et une interruption laisse un corpus utilisable.

    `type_date="entry"` fait porter la recherche sur la date de **saisie** et non sur
    celle de l'observation. C'est ce qui permet un moissonnage incrémental par `search` :
    on récupère ce qui a été saisi depuis la dernière exécution, y compris des
    observations anciennes encodées récemment — que la date d'observation ferait manquer.
    """
    if not territoires:
        raise ValueError(
            "Un moissonnage par recherche exige un périmètre territorial : l'API refuse "
            "une recherche non bornée. Renseignez [visionature] departements.")

    from datetime import timedelta

    for territoire in territoires:
        fin = date_fin
        tranche = tranche_jours
        essais = 0
        while fin > date_debut:
            debut = max(date_debut, fin - timedelta(days=tranche))
            try:
                releves = observations_recherche(cfg, id_taxo_group, debut, fin,
                                                 [territoire], type_date)
            except bio.HTTPError as erreur:
                # ⚠ Un 403 sur `search` n'est PAS un défaut de droit : c'est un refus de
                # VOLUME. Mesuré sur faune-occitanie.org avec les mêmes identifiants et
                # le même territoire : 223 reptiles sur soixante jours passent, sept
                # jours d'oiseaux — quelque 260 000 observations — sont refusés, et une
                # recherche sans périmètre l'est toujours.
                #
                # C'est ce que régule le PID de `transfer_vn` : il ne cherche pas
                # l'efficacité, il évite ce refus. On rétrécit donc et on réessaie, au
                # lieu d'abandonner le groupe comme s'il était interdit.
                code = erreur.args[0] if erreur.args else None
                # Rétrécir ne sert que si la FENÊTRE en dépend. `debut` étant plafonné à
                # `date_debut`, une tranche plus courte peut rejouer exactement la même
                # requête — mesuré : trois requêtes identiques sur une plage de deux
                # jours. On compare donc la fenêtre qui RÉSULTERAIT du rétrécissement,
                # et non la tranche ni la position de `debut`.
                reduite = max(TRANCHE_JOURS_MIN, tranche // 4)
                nouveau_debut = max(date_debut, fin - timedelta(days=reduite))
                if (code == 403 and nouveau_debut != debut
                        and tranche > TRANCHE_JOURS_MIN and essais < ESSAIS_403):
                    essais += 1
                    tranche = reduite
                    if journal:
                        journal(territoire, debut, fin, -1)
                    continue
                raise
            if journal:
                journal(territoire, debut, fin, len(releves))
            yield (debut, fin, territoire, releves)
            essais = 0
            tranche = _ajuster(tranche, len(releves))
            fin = debut - timedelta(days=1)


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


# Nombre d'identifiants demandés par requête. `transfer_vn` emploie 100
# (`max_list_length`), et découpe la liste en tranches de cette taille.
LOT_IDENTIFIANTS = 100

# Lots consécutifs en échec avant d'abandonner le groupe.
#
# Un refus n'est pas toujours ponctuel : quand un groupe taxonomique est hors du périmètre
# de la clé d'API, TOUS les lots échouent. S'obstiner revient alors à émettre des
# centaines de requêtes vouées au refus — 260 pour un jour d'oiseaux — et à faire passer
# pour lent ce qui est simplement fermé. Trois échecs d'affilée suffisent à conclure.
ECHECS_AVANT_ABANDON = 3


def observations_par_identifiants(cfg, id_taxo_group: str, identifiants: list[str],
                                  journal=None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Relevés complets correspondant à une liste d'identifiants.

    C'est la voie qu'emploie `transfer_vn` dans `_store_update` — et celle qui manquait
    ici. `api_list` accepte un paramètre `id_sightings_list` : des identifiants séparés
    par des virgules, cent à la fois. On obtient donc cent relevés complets par requête,
    là où `api_get` en demandait un par observation.

    L'écart n'est pas théorique : le différentiel des oiseaux d'Occitanie rend quelque
    26 000 identifiants par jour, soit 26 000 requêtes en `api_get` contre 260 ici.

    Un `api_list` ainsi borné n'est de surcroît pas le dump intégral d'un groupe
    taxonomique, que l'API refuse — c'est une demande ciblée.

    Retourne `(relevés, inaccessibles)`. Un lot refusé n'interrompt pas les suivants :
    ses identifiants sont remontés avec leur motif, à charge pour l'appelant de les
    journaliser. Perdre cent relevés est regrettable, en perdre 26 000 le serait plus.
    """
    controleur = _controleur(bio.ObservationsAPI, cfg)
    releves, inaccessibles = [], []
    echecs = 0
    for debut in range(0, len(identifiants), LOT_IDENTIFIANTS):
        lot = identifiants[debut:debut + LOT_IDENTIFIANTS]
        try:
            reponse = controleur.api_list(id_taxo_group,
                                          id_sightings_list=",".join(lot),
                                          short_version=SHORT_VERSION)
            releves.extend(_extraire(reponse))
            echecs = 0
        except bio.BiolovisionApiException as erreur:
            echecs += 1
            inaccessibles.extend((cle, f"lot : {erreur!r}") for cle in lot)
            if echecs >= ECHECS_AVANT_ABANDON:
                restants = identifiants[debut + len(lot):]
                inaccessibles.extend(
                    (cle, f"groupe abandonné après {echecs} lots refusés d'affilée")
                    for cle in restants)
                raise GroupeInaccessible(
                    f"{echecs} lots consécutifs refusés ({erreur!r}) : le groupe "
                    f"{id_taxo_group} paraît hors du périmètre de la clé d'API. "
                    f"{len(restants)} identifiant(s) non demandé(s).") from erreur
        if journal:
            journal(debut + len(lot), len(identifiants))
    return releves, inaccessibles


def observations_modifiees(cfg, id_taxo_group: str, depuis: str,
                           type_modification: str = "only_modified", journal=None
                           ) -> tuple[list[dict], list[tuple[str, str]]]:
    """Relevés créés ou modifiés depuis `depuis`, complets, et les inaccessibles.

    Le différentiel ne livre que des identifiants — `id_sighting`, `id_universal`,
    `modification_type`. Les relevés sont ensuite récupérés **par lots de cent** via
    `api_list(id_sightings_list=…)`, comme le fait `_store_update` de `transfer_vn`.

    Une version antérieure appelait `api_get` par identifiant. C'était tenable sur un
    petit delta et intenable au-delà : le différentiel des oiseaux d'Occitanie rend
    quelque 26 000 identifiants par jour. Le passage par lots divise le nombre de
    requêtes par cent.

    Le défaut est `only_modified` et non « all » : les suppressions sont traitées à part,
    par `observations_supprimees`, et les inclure ici ferait demander des relevés qui
    n'existent plus.
    """
    controleur = _controleur(bio.ObservationsAPI, cfg)
    entrees = _extraire(controleur.api_diff(id_taxo_group, depuis, type_modification))

    releves, a_chercher = [], []
    for entree in entrees:
        # Le différentiel peut livrer un relevé complet ; inutile de le redemander.
        if est_releve_complet(entree):
            releves.append(entree)
            continue
        cle = identifiant(entree)
        if cle:
            a_chercher.append(cle)

    complementaires, inaccessibles = observations_par_identifiants(
        cfg, id_taxo_group, a_chercher, journal=journal)
    return releves + complementaires, inaccessibles
