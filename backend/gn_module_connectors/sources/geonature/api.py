"""Accès à l'API d'export d'une autre instance GeoNature.

La source est le **module d'export** du distant (`gn_module_export`), et non l'API de la
Synthèse du cœur. Le choix n'est pas de commodité :

- `POST /synthese/export_observations` rend un **fichier**, sans `limit` ni `offset`, et
  sa requête est plafonnée en dur par `NB_MAX_OBS_EXPORT` (défaut 50 000). Au-delà, la
  donnée est tronquée sans que rien ne le dise — un moissonnage ne peut pas être bâti
  là-dessus ;
- `/synthese/for_web` n'a qu'un `limit`, sur une vue d'affichage cartographique qui ne
  porte ni nomenclatures ni identifiants de jeu de données ;
- `GET /api/exports/api/<id_export>` pagine réellement, filtre côté serveur, et
  s'authentifie par un jeton d'export — sans compte ni session.

⚠ **`offset` est un numéro de page**, pas un décalage de lignes. C'est contre-intuitif et
ce n'est écrit nulle part dans la documentation du module ; cela se lit dans
`get_one_export_api`, qui passe `offset` tel quel à `GenericQuery`.

⚠ La vue exposée est **choisie par l'administrateur distant**. Rien ne garantit que ce
soit `gn_exports.v_synthese_sinp` : ce peut être une vue floutée, ou une vue maison. D'où
`verifier_colonnes`, qui refuse un export incompatible sur la première page plutôt que
d'écrire des milliers de lignes vides.
"""

import logging

import requests

logger = logging.getLogger(__name__)

CHEMIN_EXPORT = "/api/exports/api/{id_export}"

# `max_page_size_api` vaut 1000 par défaut côté serveur, et rabote silencieusement toute
# demande supérieure. On aligne le défaut dessus, et `moissonner` adopte de toute façon la
# valeur que la première page annonce.
LIMITE_DEFAUT = 1000

# Colonnes sans lesquelles l'import n'a pas de sens : leur absence signale que l'export
# n'est pas une vue de type `v_synthese_sinp`, et le connecteur refuse de continuer.
COLONNES_REQUISES = ("id_synthese", "cd_nom", "date_debut", "date_fin", "jdd_uuid")

# Au moins l'une d'elles doit être exploitable, sans quoi aucune géométrie n'est
# reconstructible. Une vue floutée qui retire les coordonnées tombe ici.
COLONNES_GEOMETRIE = ("x_centroid_4326", "y_centroid_4326", "wkt_4326")

# Colonnes dont l'absence dégrade l'import sans l'empêcher. Chacune est nommée dans
# l'avertissement, avec ce qui sera perdu — un « export incomplet » sans détail
# n'apprendrait rien.
COLONNES_ATTENDUES = {
    "id_perm_sinp": "identifiant permanent SINP (un UUID sera dérivé, moins fidèle)",
    "id_perm_grp_sinp": "identifiant de regroupement",
    "date_modification": "date de modification (l'import ne pourra pas être incrémental)",
    "date_creation": "date de création (l'import ne pourra pas être incrémental)",
    "ca_uuid": "cadre d'acquisition (les jeux iront dans le cadre de repli du module)",
    "nom_cite": "nom cité par l'observateur",
    "observateurs": "observateurs",
    "nombre_min": "effectif minimal",
    "nombre_max": "effectif maximal",
    "altitude_min": "altitude minimale",
    "altitude_max": "altitude maximale",
    "precision": "précision géographique",
    "version_taxref": "version de TAXREF du producteur",
    "comment_occurrence": "commentaire de l'occurrence",
    "preuve_numerique": "preuve numérique",
    "precision_diffusion": "niveau de diffusion voulu par le producteur",
    "niveau_sensibilite": "niveau de sensibilité à la source",
}


class ErreurGeoNature(RuntimeError):
    """Échec d'accès à l'instance distante, ou export inexploitable."""


def _base(cfg) -> str:
    return str(cfg.get("url") or "").rstrip("/")


def _entetes(cfg) -> dict:
    """En-têtes de la requête, jeton compris.

    ⚠ Le jeton passe par l'en-tête `api-key`, **jamais** par `?token=`. Les deux sont
    acceptés par le module d'export, mais une chaîne de requête est journalisée par le
    serveur distant et par tout proxy intermédiaire : le jeton s'y retrouverait en clair,
    dans des fichiers que personne ne surveille.
    """
    entetes = {"Accept": "application/json"}
    jeton = str(cfg.get("jeton") or "").strip()
    if jeton:
        entetes["api-key"] = jeton
    return entetes


def _verifier_json(reponse, url: str) -> dict:
    """Charge JSON d'une réponse, ou une erreur qui nomme la cause."""
    if reponse.status_code in (401, 403):
        raise ErreurGeoNature(
            f"Accès refusé par {url} (HTTP {reponse.status_code}). Le jeton est-il celui "
            f"de cet export ? Un jeton n'ouvre qu'un export : il ne donne accès ni aux "
            f"autres, ni à la liste des exports.")
    if reponse.status_code == 404:
        raise ErreurGeoNature(
            f"Export introuvable ({url}). Vérifiez `id_export` : il est propre à "
            f"l'instance distante, et le module d'export n'expose aucune API permettant "
            f"de le découvrir sans compte.")
    if reponse.status_code >= 400:
        raise ErreurGeoNature(f"HTTP {reponse.status_code} sur {url}.")
    try:
        charge = reponse.json()
    except ValueError:
        raise ErreurGeoNature(
            f"Réponse non-JSON de {url}. Une page de connexion HTML à cet endroit "
            f"signale que le jeton n'a pas été pris en compte.")
    if not isinstance(charge, dict) or "items" not in charge:
        raise ErreurGeoNature(
            f"Réponse inattendue de {url} : aucun champ « items ». Ce n'est pas l'API "
            f"d'export d'un GeoNature.")
    brut = charge.get("items")
    charge["geojson"] = isinstance(brut, dict)
    charge["items"] = _aplatir(brut)
    return charge


def _aplatir(items):
    """`items` en liste d'enregistrements plats, quelle que soit la forme reçue.

    ⚠ **L'API rend deux formes selon l'export, et c'est l'export qui choisit, pas la
    vue.** `get_one_export_api` appelle `as_geofeature()` dès que l'export déclare un
    `geometry_field`, et `return_query()` sinon. Dans le premier cas `items` n'est pas une
    liste mais une FeatureCollection GeoJSON, dont chaque entrée range les colonnes sous
    `properties`.

    Ce n'est pas un cas de bord : l'export « Synthese SINP » livré par GeoNature déclare
    `geometry_field = geom`. La forme GeoJSON est donc celle que rend l'export de
    référence de n'importe quelle instance — la supposer absente faisait échouer le
    connecteur sur sa cible la plus ordinaire, par un `KeyError: 0` en cours de
    pagination.

    ⚠ **Cette forme perd des lignes en chemin, et l'appelant doit le savoir** — d'où le
    drapeau `geojson` posé sur la charge. `as_geofeature` ne retient que les
    enregistrements dont la géométrie n'est pas nulle, alors que le `LIMIT` SQL, lui, les
    a bien consommés. Une page peut donc rendre moins d'enregistrements que la limite sans
    être la dernière ; `moissonner` en tient compte.

    La géométrie de la Feature est écartée sciemment : `verifier_colonnes` exige déjà
    l'une des colonnes `x_centroid_4326` / `y_centroid_4326` / `wkt_4326`, et c'est
    d'elles que `transform` tire le point. Reprendre en plus le GeoJSON ajouterait une
    seconde source de vérité géographique sans rien résoudre.

    ⚠ Un dict sans `features` exploitable (absente, ou pas une liste) rend `[]` comme une
    page vide légitime — la pagination doit continuer, `moissonner` n'a pas à en faire un
    cas particulier. Mais les deux n'ont rien à voir : l'un est la fin normale du corpus,
    l'autre une forme de réponse que ce module ne sait pas lire. Le second est donc
    journalisé distinctement, pour qu'un corpus qui s'arrête bien plus tôt que prévu ne se
    confonde pas silencieusement avec une fin de pagination ordinaire.
    """
    if isinstance(items, dict):
        traits = items.get("features")
        if isinstance(traits, list):
            return [(t or {}).get("properties") or {} for t in traits]
        logger.warning(
            "forme GeoJSON inattendue reçue de l'export : un dict sans « features » "
            "exploitable (clés reçues : %s, « features » est %s). Traité comme une page "
            "vide pour ne pas interrompre la moisson, mais ce n'est probablement pas une "
            "page vide légitime.",
            sorted(items), "absente" if traits is None else f"de type {type(traits).__name__}")
        return []
    return items or []


def filtres_serveur(cfg, depuis: str = "", champ_date: str = "date_modification",
                    perimetre_wkt: str = "") -> dict:
    """Filtres transmis à l'API, construits depuis la configuration.

    Le module d'export applique des conventions de nommage génériques sur les colonnes de
    la vue : `filter_d_up_<col>` pour « ≥ » sur une date, `geometry` pour une intersection.

    ⚠ Comme partout ailleurs dans ce module, **un paramètre inconnu de l'API est ignoré
    sans erreur**. Un filtre mal orthographié ne fait donc pas échouer la requête : il la
    laisse ramener tout le corpus. `diagnostiquer_page` sert précisément à le détecter.
    """
    filtres: dict[str, str] = {}
    if depuis:
        filtres[f"filter_d_up_{champ_date}"] = depuis
    wkt = str(perimetre_wkt or cfg.get("perimetre_wkt") or "").strip()
    if wkt:
        filtres["geometry"] = wkt
    filtres.update(cfg.get("filtre_api") or {})
    return filtres


def verifier_colonnes(item: dict) -> tuple[list[str], list[str]]:
    """(manquantes bloquantes, manquantes dégradantes) d'après un enregistrement réel.

    Le contrôle porte sur la **présence de la clé**, non sur sa valeur : une colonne
    renseignée à NULL sur la première ligne reste une colonne exposée par la vue.
    """
    presentes = set(item or {})
    bloquantes = [c for c in COLONNES_REQUISES if c not in presentes]
    if not any(c in presentes for c in COLONNES_GEOMETRIE):
        bloquantes.append("géométrie (" + ", ".join(COLONNES_GEOMETRIE) + ")")
    degradantes = [c for c in COLONNES_ATTENDUES if c not in presentes]
    return (bloquantes, degradantes)


def diagnostiquer_page(charge: dict, filtres: dict) -> list[str]:
    """Avertissements tirés de la première page, avant tout traitement.

    Le plus utile est gratuit : la réponse porte `total` **et** `total_filtered`. Si un
    filtre est actif et que les deux sont égaux, le serveur ne l'a pas appliqué. C'est une
    preuve directe, obtenue avant d'avoir téléchargé quoi que ce soit — bien meilleure que
    le comptage a posteriori des rejets auquel les autres connecteurs sont réduits.

    Un filtre peut légitimement ne rien exclure : c'est donc un avertissement, pas une
    erreur.
    """
    messages = []
    total = charge.get("total")
    filtre = charge.get("total_filtered")
    if filtres and total is not None and total == filtre:
        messages.append(
            f"le serveur semble avoir ignoré les filtres {sorted(filtres)} : "
            f"total_filtered ({filtre}) égale total ({total}). Un paramètre inconnu de "
            f"l'API d'export est écarté sans erreur.")
    if not charge.get("items") and filtre:
        messages.append(
            f"{filtre} enregistrement(s) annoncé(s) mais aucun rendu sur la première "
            f"page : pagination suspecte.")
    if not charge.get("license", {}).get("name"):
        messages.append(
            "l'export ne déclare aucune licence. Les jeux créés n'en porteront pas, ce "
            "qui rendra toute rediffusion ambiguë.")
    return messages


def page(cfg, numero: int, limite: int, filtres: dict) -> dict:
    """Une page de résultats, brute."""
    base = _base(cfg)
    if not base:
        raise ErreurGeoNature("[geonature] url manquante.")
    url = base + CHEMIN_EXPORT.format(id_export=cfg.get("id_export"))
    parametres = {**filtres, "limit": limite, "offset": numero}
    reponse = requests.get(url, params=parametres, headers=_entetes(cfg),
                           timeout=int(cfg.get("timeout", 120)))
    return _verifier_json(reponse, url)


def _premier_identifiant(items: list[dict]) -> str | None:
    return str(items[0].get("id_synthese")) if items else None


def moissonner(cfg, filtres: dict, journal=None,
               max_results: int = 0) -> tuple[list[dict], dict]:
    """Tout le corpus correspondant aux filtres. Retourne (items, méta).

    `méta` porte `total`, `total_filtered`, `license`, la limite effectivement appliquée,
    `premiere_page` (les enregistrements de la page 0, seuls — pour `diagnostiquer_page`,
    qui a besoin de la page telle que le serveur l'a rendue et non du corpus complet) et
    surtout `complet` : la réconciliation des suppressions n'a le droit de s'exécuter que
    sur une moisson complète, et c'est ce drapeau qui l'autorise.

    Quatre pièges de pagination, tous rencontrés sur des API de ce genre :

    - **la limite est rabotée sans le dire.** Le serveur plafonne à `max_page_size_api`,
      et rend simplement moins de lignes. On adopte donc la valeur annoncée par la page 0
      avant de demander la page 1 : changer de taille en cours de pagination ferait sauter
      ou répéter des lignes.
    - **sans tri stable, la pagination ment.** Une base qui reçoit des saisies pendant le
      moissonnage réordonne les résultats entre deux pages. `orderby=id_synthese` fige
      l'ordre — la colonne est de toute façon exigée par `verifier_colonnes`, un export
      qui ne la porte pas étant refusé.
    - **un `offset` ignoré boucle indéfiniment.** Si la page N commence par la même ligne
      que la page N-1, on s'arrête net : sans ce contrôle, la moisson gonfle en mémoire
      jusqu'à ce que le processus meure, sans qu'aucun message ne désigne la cause.
    - **une page courte ne prouve la fin du corpus que sur la forme plate.** Quand l'export
      déclare une géométrie, `as_geofeature` retire de sa FeatureCollection les lignes dont
      la géométrie est nulle — que le `LIMIT` SQL avait pourtant consommées, et que
      `total_filtered` compte toujours. Or `the_geom_4326` est *nullable* dans
      `gn_synthese`. Une seule observation sans géométrie rendait donc une page plus courte
      que la limite en plein milieu du corpus, et la moisson s'arrêtait là en abandonnant
      tout le reste. Sur cette forme, la seule fin lisible est une page vide.

    ⚠ S'y ajoute un dédoublonnage sur `id_synthese`, qui n'est pas une précaution de
    confort. Le raisonnement « le tri ascendant interdit les répétitions » suppose que le
    serveur honore `orderby` ; le contrôle ci-dessus ne détecte qu'un `offset` totalement
    ignoré, pas un chevauchement partiel. Sans dédoublonnage, un même `unique_id_sinp`
    deux fois dans un lot fausse le décompte d'`insert_batch`, dont les « insérées » se
    calculent par `len(lignes) - len(deja)`.

    Et surtout : **un doublon prouve que l'ordre n'est pas stable, donc que des lignes ont
    pu être sautées.** La moisson est alors marquée incomplète, ce qui interdit à
    `geonature-reconcilier` de tourner dessus — une absence n'y serait plus la preuve
    d'une suppression à la source.
    """
    limite = min(int(cfg.get("page_size", LIMITE_DEFAUT)), LIMITE_DEFAUT)
    if max_results:
        limite = min(limite, max_results)
    # ⚠ Une limite nulle ou négative — `page_size = 0`, ou un `--max-resultats` négatif —
    # rend la seule sortie de boucle (`len(lot) < limite`) impossible à atteindre : la
    # moisson tournerait indéfiniment en gonflant en mémoire, sans un message. C'est la
    # défaillance que le contrôle d'inertie ci-dessous existe pour empêcher, entrée par une
    # autre porte ; on la ferme ici, où la cause est encore nommable.
    if limite <= 0:
        raise ErreurGeoNature(
            f"Taille de page nulle ou négative ({limite}). Vérifiez [geonature] page_size "
            f"et --max-resultats : une limite non positive ferait boucler la moisson sans "
            f"fin.")

    tri = dict(filtres)
    tri.setdefault("orderby", "id_synthese")
    tri.setdefault("order", "asc")

    items: list[dict] = []
    meta: dict = {"complet": True, "limite": limite, "total": None,
                  "total_filtered": None, "license": {}, "doublons": 0,
                  "geojson": False, "premiere_page": []}
    vus: set[str] = set()
    numero, precedent = 0, None
    while True:
        charge = page(cfg, numero, limite, tri)
        lot = charge.get("items") or []
        meta["geojson"] = meta["geojson"] or bool(charge.get("geojson"))
        if numero == 0:
            meta["total"] = charge.get("total")
            meta["total_filtered"] = charge.get("total_filtered")
            meta["license"] = charge.get("license") or {}
            # Conservée telle quelle pour `diagnostiquer_page` : sur un corpus
            # multi-pages, `items` finit par porter tout le corpus moissonné, et un
            # diagnostic qui s'y fierait perdrait son sens de « avant tout traitement ».
            meta["premiere_page"] = list(lot)
            # La limite effective, telle que le serveur la rend — pas celle demandée.
            echue = charge.get("limit")
            if isinstance(echue, int) and 0 < echue < limite:
                if journal:
                    journal(f"  limite ramenée à {echue} par le serveur (demandé "
                            f"{limite}) : pagination alignée dessus.")
                limite = echue
                meta["limite"] = echue
            if journal:
                journal(f"  {meta['total_filtered']} enregistrement(s) annoncé(s)"
                        + (f" sur {meta['total']} au total" if meta["total"] else ""))
        elif lot and _premier_identifiant(lot) == precedent:
            raise ErreurGeoNature(
                f"La page {numero} recommence à l'enregistrement {precedent} : le "
                f"serveur ignore le paramètre `offset`. Rappel : pour l'API d'export, "
                f"`offset` est un numéro de page, pas un décalage de lignes.")

        precedent = _premier_identifiant(lot)
        for enregistrement in lot:
            cle = str(enregistrement.get("id_synthese"))
            if cle in vus:
                meta["doublons"] += 1
                continue
            vus.add(cle)
            items.append(enregistrement)

        if max_results and len(items) >= max_results:
            items = items[:max_results]
            if journal:
                journal(f"  moisson bornée à {max_results} enregistrement(s).")
            # Troncature **voulue** : ce n'est pas une pagination incomplète, mais la
            # réconciliation doit tout de même s'interdire de tourner là-dessus.
            meta["complet"] = False
            return (items, meta)

        # ⚠ Une page plus courte que la limite ne signe la dernière page que sur la forme
        # plate. Sur la forme GeoJSON, le serveur a pu retirer les lignes sans géométrie
        # d'une page par ailleurs pleine : s'arrêter là abandonnerait le reste du corpus.
        # On y paie une requête de plus — celle qui rend la page vide — contre la garantie
        # de ne pas tronquer un import sur une donnée que le serveur a filtrée lui-même.
        if not lot or (not meta["geojson"] and len(lot) < limite):
            break
        numero += 1

    if meta["doublons"]:
        meta["complet"] = False
        message = (f"{meta['doublons']} enregistrement(s) reçus plusieurs fois : le "
                   f"serveur n'a pas honoré le tri. Des lignes ont donc pu être sautées "
                   f"aussi — la réconciliation des suppressions s'interdira de tourner "
                   f"sur cette moisson.")
        if journal:
            journal(f"  ⚠ {message}")
        else:
            raise ErreurGeoNature(message)

    annonce = meta["total_filtered"]
    if annonce is not None and len(items) + meta["doublons"] != annonce:
        meta["complet"] = False
        manquants = annonce - len(items) - meta["doublons"]
        if meta["geojson"] and manquants > 0:
            # La moisson a bien été menée jusqu'à la page vide : ces lignes ne sont pas
            # des lignes sautées, ce sont des lignes que le serveur refuse de rendre. La
            # distinction change le message, pas le verdict — `complet` reste faux, car
            # ces observations existent à la source et leur absence ici ne prouve donc
            # aucune suppression.
            message = (f"{manquants} enregistrement(s) annoncé(s) que le serveur n'a pas "
                       f"rendus. L'export est géographique, et `as_geofeature` retire de "
                       f"sa FeatureCollection toute ligne dont la géométrie est nulle — "
                       f"`the_geom_4326` est nullable dans `gn_synthese`. Ces "
                       f"observations existent à la source et resteront invisibles ici : "
                       f"la réconciliation des suppressions s'interdit de tourner sur "
                       f"cette moisson, où leur absence ne prouverait aucune suppression.")
        else:
            message = (f"pagination incomplète : {len(items)} enregistrement(s) reçus "
                       f"pour {annonce} annoncé(s)")
        if journal:
            journal(f"  ⚠ {message}")
        else:
            raise ErreurGeoNature(message)
    return (items, meta)
