"""Accès à l'API de dbChiro (dbchiroweb, Django/DRF).

Trois particularités commandent tout ce fichier.

**L'authentification passe par le formulaire de connexion Django**, pas par un jeton.
Les vues d'API sont protégées par `LoginRequiredMixin` et le bloc `REST_FRAMEWORK` de
dbchiroweb ne déclare aucune `DEFAULT_AUTHENTICATION_CLASSES`. Il faut donc récupérer un
jeton CSRF, poster `username`/`password`, et conserver le cookie de session. Un jeton
DRF côté dbChiro rendrait cette gymnastique inutile : c'est la première demande à porter
en amont.

**Le périmètre visible dépend du compte employé.** `SightingListPermissionsMixin` filtre
le queryset selon les droits : un compte `access_all_data` voit tout, y compris les
sessions confidentielles, les gîtes masqués et les études fermées. Un compte ordinaire
sur une instance réglée `SEE_ALL_NON_SENSITIVE_DATA = True` voit exactement la donnée
non sensible. **C'est ce second profil qu'il faut donner au connecteur** : le tri de
sensibilité est alors fait par le serveur, qui en est le seul juge légitime, et non par
nous après coup.

**Il n'y a pas de moissonnage incrémental possible.** Le queryset est trié par
`-timestamp_update`, mais le serializer n'expose pas ce champ — vérifié sur 8 039
observations, couverture nulle. Impossible de savoir où s'arrêter. La question est
heureusement sans objet à cette échelle : 8 039 observations tiennent en deux pages de
5 000, et une relecture complète coûte quelques secondes. Si l'instance venait à grossir
d'un ordre de grandeur, exposer `timestamp_update` en amont deviendrait la priorité.
"""

import re

import requests

# Chemin du formulaire de connexion Django (django-registration-redux).
CHEMIN_LOGIN = "/accounts/login/"
# `sights.urls` est inclus à la racine, et `observation/urls.py` sans préfixe : la
# recherche vit donc à /api/v1/search, et non sous /sights/.
CHEMIN_RECHERCHE = "/api/v1/search"

# `LargeGeoJsonPageNumberPagination` plafonne `page_size` à 5000.
TAILLE_PAGE_MAX = 5000

MOTIF_CSRF = re.compile(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"')


class ErreurDbChiro(RuntimeError):
    """Échec d'accès à l'API dbChiro, avec un message exploitable."""


def _base(cfg) -> str:
    url = str(cfg.get("url") or "").strip().rstrip("/")
    if not url:
        raise ErreurDbChiro(
            "Aucune URL d'instance dbChiro configurée ([dbchiro] url dans "
            "connectors_config.toml)."
        )
    return url


def _verifier_json(reponse, url: str):
    """Réponse JSON, ou un message qui dit ce qui s'est réellement passé.

    Trois échecs se ressemblent et se diagnostiquent très différemment : une session
    expirée (redirection vers le formulaire de connexion), un filtre anti-robot qui
    intercale une page de défi, et une vraie erreur serveur. Sans ce tri, les trois
    remontent en `JSONDecodeError` sur une page HTML, ce qui n'apprend rien.
    """
    if reponse.status_code >= 500:
        raise ErreurDbChiro(
            f"L'instance a répondu {reponse.status_code} sur {url} : panne côté "
            f"serveur. Le moissonnage est interrompu plutôt que de rendre un corpus "
            f"partiel qu'un bilan présenterait comme complet."
        )
    if CHEMIN_LOGIN in reponse.url:
        raise ErreurDbChiro(
            f"Redirigé vers le formulaire de connexion en appelant {url} : la session "
            f"a expiré, ou le compte n'a pas accès à l'API."
        )
    corps = reponse.text or ""
    if "within.website" in corps.lower() or "anubis" in reponse.url.lower():
        raise ErreurDbChiro(
            f"L'instance est protégée par un filtre anti-robot (Anubis) sur {url}. "
            f"Un connecteur ne peut pas le franchir : il faut demander à l'exploitant "
            f"une exemption sur le chemin {CHEMIN_RECHERCHE}."
        )
    reponse.raise_for_status()
    try:
        return reponse.json()
    except ValueError as exc:
        raise ErreurDbChiro(
            f"Réponse non JSON de {url} (content-type "
            f"{reponse.headers.get('content-type')!r})."
        ) from exc


def verifier_reponse_login(reponse, base: str) -> None:
    """Lève une `ErreurDbChiro` explicite si la connexion n'a pas abouti.

    Isolée de `connecter` pour être vérifiable sans réseau : c'est la
    classification qui a été fausse en production, pas le transport.
    """
    # ⚠ L'ordre des tests compte. Un 500 rendu par l'application sur le chemin de
    # connexion satisfait *aussi* le test d'URL ci-dessous : conclure « identifiants
    # invalides » sur une panne serveur envoie l'exploitant vérifier un mot de passe
    # parfaitement valide. Mesuré en conditions réelles — dbchiroc.org a renvoyé un 500
    # sur le POST de connexion alors que le GET répondait normalement.
    if reponse.status_code >= 500:
        raise ErreurDbChiro(
            f"L'instance {base} a répondu {reponse.status_code} au formulaire de "
            f"connexion. C'est une panne côté serveur, pas un problème "
            f"d'identifiants : réessayez plus tard, et signalez-la à l'exploitant si "
            f"elle persiste."
        )
    if reponse.status_code == 429:
        raise ErreurDbChiro(
            f"L'instance {base} limite le débit des connexions (429). Espacez les "
            f"exécutions du connecteur."
        )
    if reponse.status_code == 403:
        raise ErreurDbChiro(
            f"Connexion refusée sur {base} avec un 403 : jeton CSRF rejeté, ou compte "
            f"bloqué après des tentatives répétées. Ce n'est pas un mot de passe erroné."
        )
    if CHEMIN_LOGIN in reponse.url:
        raise ErreurDbChiro(
            f"Connexion refusée sur {base} : identifiants invalides, ou compte inactif."
        )


def connecter(cfg) -> requests.Session:
    """Session authentifiée sur l'instance dbChiro.

    Le `Referer` est obligatoire : Django rejette un POST de formulaire sans en-tête
    d'origine cohérente sur une connexion HTTPS, avec une 403 dont le message ne
    mentionne pas le CSRF.
    """
    base = _base(cfg)
    identifiant = str(cfg.get("username") or "").strip()
    motdepasse = str(cfg.get("password") or "")
    if not identifiant or not motdepasse:
        raise ErreurDbChiro(
            "Identifiants dbChiro absents de la configuration ([dbchiro] username / "
            "password)."
        )

    session = requests.Session()
    session.headers["User-Agent"] = "gn_module_connectors (GeoNature)"
    timeout = int(cfg.get("timeout", 120))

    url_login = base + CHEMIN_LOGIN
    page = session.get(url_login, timeout=timeout)
    if "within.website" in (page.text or "").lower():
        raise ErreurDbChiro(
            f"{base} est protégée par un filtre anti-robot (Anubis) : la connexion "
            f"automatisée est impossible sans exemption côté serveur."
        )
    jeton = MOTIF_CSRF.search(page.text or "")
    if not jeton:
        raise ErreurDbChiro(
            f"Jeton CSRF introuvable sur {url_login} — la page de connexion n'a pas la "
            f"forme attendue (instance dbChiro trop ancienne ou URL erronée)."
        )

    reponse = session.post(
        url_login,
        data={
            "csrfmiddlewaretoken": jeton.group(1),
            "username": identifiant,
            "password": motdepasse,
            "next": "/",
        },
        headers={"Referer": url_login},
        timeout=timeout,
        allow_redirects=True,
    )
    verifier_reponse_login(reponse, base)
    return session


def _page(session, cfg, numero: int, taille: int, filtres: dict) -> dict:
    """Une page de résultats, brute.

    L'URL est reconstruite à chaque appel plutôt que de suivre le champ `next` de la
    réponse : sur l'instance mesurée, `next` est émis en **http://** alors que le site
    répond en https. Le suivre exposerait les identifiants de session à une redirection
    en clair.
    """
    url = _base(cfg) + CHEMIN_RECHERCHE
    parametres = {**filtres, "page": numero, "page_size": taille}
    reponse = session.get(url, params=parametres,
                          timeout=int(cfg.get("timeout", 120)))
    return _verifier_json(reponse, url)


def filtres_serveur(cfg) -> dict:
    """Filtres transmis à l'API, construits depuis la configuration.

    `area` est le seul filtre géographique fiable : c'est l'identifiant d'un zonage de
    l'instance dbChiro, et il est appliqué en base. Sur l'instance mesurée, l'Ariège
    (`area = 109`) ramène 8 007 observations sur 8 039 — le reste débordant sur l'Aude,
    les Pyrénées-Orientales et la Haute-Garonne.

    ⚠ L'identifiant de zonage est **propre à chaque instance**. `geonature connectors
    dbchiro-zonages` le liste, plutôt que de le faire deviner.
    """
    filtres = {}
    area = str(cfg.get("area") or "").strip()
    if area:
        filtres["area"] = area
    for cle_cfg, cle_api in (("date_min", "date_min"), ("date_max", "date_max")):
        valeur = str(cfg.get(cle_cfg) or "").strip()
        if valeur:
            filtres[cle_api] = valeur
    filtres.update(cfg.get("filtre_api") or {})
    return filtres


def observations(session, cfg, journal=None) -> list[dict]:
    """Toutes les observations du périmètre configuré, en features GeoJSON.

    Le nombre total annoncé par la première page sert de garde-fou : si la pagination
    s'arrête avant de l'atteindre, on le signale plutôt que de rendre un corpus tronqué
    qu'un bilan présenterait comme complet.
    """
    taille = min(int(cfg.get("page_size", TAILLE_PAGE_MAX)), TAILLE_PAGE_MAX)
    filtres = filtres_serveur(cfg)
    features: list[dict] = []
    annonce = None
    numero = 1
    while True:
        charge = _page(session, cfg, numero, taille, filtres)
        if annonce is None:
            annonce = charge.get("count")
            if journal:
                journal(f"  {annonce} observation(s) annoncée(s) par l'instance"
                        + (f" (filtres {filtres})" if filtres else ""))
        lot = ((charge.get("results") or {}).get("features")) or []
        features.extend(lot)
        if not charge.get("next") or not lot:
            break
        numero += 1

    if annonce is not None and len(features) != annonce:
        message = (f"pagination incomplète : {len(features)} observation(s) reçues "
                   f"pour {annonce} annoncée(s)")
        if journal:
            journal(f"  ⚠ {message}")
        else:
            raise ErreurDbChiro(message)
    return features


def zonages(session, cfg, recherche: str = "") -> list[dict]:
    """Zonages de l'instance, pour renseigner `area` en configuration.

    S'appuie sur l'autocomplétion de dbChiro (`/api/areas-autocomplete/`), qui rend
    l'identifiant et le libellé de chaque zonage — départements, communes, mailles,
    ZNIEFF, parcs.
    """
    url = _base(cfg) + "/api/areas-autocomplete/"
    reponse = session.get(url, params={"q": recherche} if recherche else {},
                          timeout=int(cfg.get("timeout", 120)))
    charge = _verifier_json(reponse, url)
    return charge.get("results") or []
