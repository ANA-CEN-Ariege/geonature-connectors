"""Médias rattachés aux observations d'une autre instance GeoNature.

Les médias de GeoNature — photos, sons, vidéos, PDF saisis dans Occtax ou dans un autre
module — ne sont **pas** dans la Synthèse. Ils vivent dans `gn_commons.t_medias`, rattachés
par `uuid_attached_row` à l'UUID de l'objet saisi (le dénombrement Occtax), qui est aussi
l'`unique_id_sinp` de la ligne de Synthèse. La Synthèse ne les voit qu'au travers de cette
relation ; rien ne les recopie dans `digital_proof`.

Conséquence : la vue d'export standard (`gn_exports.v_synthese_sinp`) n'en porte rien, et
un export ne peut les publier que si l'administrateur distant y ajoute une colonne. Ce
module lit cette colonne — `medias`, un tableau JSON — et n'invente rien en son absence.
La requête à proposer au producteur est dans `docs/connecteurs/geonature.md`.

Format attendu, un objet par média :

    {"uuid": "…", "type": "Photo", "url": "https://…", "chemin": "…",
     "titre": "…", "auteur": "…", "description": "…", "public": true}

- `url` **ou** `chemin` : `url` est le `media_url` distant, ou une URL absolue déjà
  construite par la vue ; `chemin` est le `media_path` d'un fichier téléversé, que l'on
  complète par l'URL publique des médias du distant (`url_medias`, déduite de `url`) ;
- `type` : `cd_nomenclature` ou libellé de `TYPE_MEDIA`. Absent, il est déduit de
  l'extension de l'URL ;
- `public` : un média que le producteur a marqué non public n'est **pas** relayé. Le
  republier ici annulerait son choix ;
- `uuid` : le `unique_id_media` distant. Réutilisé tel quel, il rend la synchronisation
  stable d'un passage à l'autre ; absent, on en dérive un de l'observation et de l'URL.

Ce module ne touche pas à la base : il rend des dictionnaires prêts pour `core/medias.py`.
"""

import json
import uuid as _uuid
from urllib.parse import urlsplit

# Espace de noms des UUID dérivés, quand le producteur ne publie pas `unique_id_media`.
# Fixe : le changer ferait passer chaque média pour nouveau au passage suivant.
NAMESPACE_MEDIA = _uuid.UUID("6f1c2a54-0d1b-4c64-9b8e-2f0a4d6b9e31")

# Longueurs de `gn_commons.t_medias`. Une URL tronquée serait une URL fausse : elle est
# refusée, et le reste est coupé.
LONGUEUR_URL = 255
LONGUEUR_TITRE = 255
LONGUEUR_AUTEUR = 100

# Codes `TYPE_MEDIA` du référentiel livré avec GeoNature (Nomenclature-api-module).
PHOTO, PAGE_WEB, PDF, AUDIO, VIDEO = "2", "3", "4", "5", "6"
YOUTUBE, DAILYMOTION, VIMEO = "7", "8", "9"

_EXTENSIONS = {
    PHOTO: (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"),
    AUDIO: (".mp3", ".wav", ".ogg", ".oga", ".flac", ".m4a", ".aac"),
    VIDEO: (".mp4", ".webm", ".ogv", ".mov", ".avi", ".mkv"),
    PDF: (".pdf",),
}
_HEBERGEURS = {
    "youtube.com": YOUTUBE, "youtu.be": YOUTUBE,
    "dailymotion.com": DAILYMOTION, "dai.ly": DAILYMOTION,
    "vimeo.com": VIMEO,
}


def url_medias_par_defaut(cfg) -> str:
    """URL publique des fichiers téléversés du distant.

    GeoNature sert `MEDIA_FOLDER` sous `MEDIA_URL` (défaut `/media`) à la racine de son
    API, et range les pièces jointes dans `attachments/`. L'API étant à `<url>/api` — c'est
    l'hypothèse sur laquelle repose déjà `api.CHEMIN_EXPORT` —, un fichier téléversé se
    trouve à `<url>/api/media/attachments/<media_path>`. Une instance qui aurait changé
    `MEDIA_URL` se règle par `[geonature] url_medias`.
    """
    explicite = str(cfg.get("url_medias") or "").strip().rstrip("/")
    if explicite:
        return explicite
    return str(cfg.get("url") or "").rstrip("/") + "/api/media/attachments"


def _texte(valeur, longueur: int | None = None) -> str | None:
    if valeur is None:
        return None
    texte = str(valeur).strip()
    if not texte:
        return None
    return texte[:longueur] if longueur else texte


def _vrai(valeur, defaut: bool = True) -> bool:
    if valeur is None or valeur == "":
        return defaut
    if isinstance(valeur, bool):
        return valeur
    return str(valeur).strip().lower() not in ("false", "f", "0", "non", "no")


def present(item: dict) -> bool:
    """L'export porte-t-il la colonne `medias` ?

    À distinguer d'une colonne présente mais vide : seule la seconde autorise à retirer
    les médias déjà importés. Sans la colonne, on ne sait rien — et on ne touche à rien.
    """
    return "medias" in item


def brut(item: dict) -> list:
    """Le tableau `medias` de l'enregistrement, quelle que soit sa forme reçue.

    Une vue peut rendre du `json` (déjà une liste une fois décodée par l'API) ou du
    `text` (une chaîne JSON) ; une agrégation sans ligne rend NULL.
    """
    valeur = item.get("medias")
    if valeur is None or valeur == "":
        return []
    if isinstance(valeur, str):
        try:
            valeur = json.loads(valeur)
        except ValueError:
            return []
    if isinstance(valeur, dict):
        valeur = [valeur]
    if not isinstance(valeur, list):
        return []
    return [m for m in valeur if isinstance(m, dict)]


def type_devine(url: str) -> str:
    """`cd_nomenclature` TYPE_MEDIA déduit de l'URL, quand le producteur n'en dit rien."""
    morceaux = urlsplit(url)
    hote = (morceaux.hostname or "").lower()
    for domaine, code in _HEBERGEURS.items():
        if hote == domaine or hote.endswith("." + domaine):
            return code
    chemin = morceaux.path.lower()
    for code, extensions in _EXTENSIONS.items():
        if chemin.endswith(extensions):
            return code
    return PAGE_WEB


def lire(item: dict, uuid_observation: str, url_medias: str) -> tuple[list[dict], dict]:
    """Médias exploitables d'un enregistrement, et le décompte de ceux qui sont écartés.

    Rend `(medias, ecartes)` : `ecartes` compte par motif (`non_public`, `sans_url`,
    `url_trop_longue`, `doublon`), pour que la commande le dise au lieu de perdre ces
    médias en silence.

    Chaque média rendu porte `type` sous sa forme d'origine (code ou libellé) et
    `type_devine`, le code déduit de l'URL : la résolution en `id_nomenclature` se fait
    côté base, par le `Resolver`, comme pour les autres nomenclatures.
    """
    medias: list[dict] = []
    ecartes: dict[str, int] = {}
    vus: set[str] = set()

    def ecarter(motif):
        ecartes[motif] = ecartes.get(motif, 0) + 1

    for media in brut(item):
        if not _vrai(media.get("public"), defaut=True):
            ecarter("non_public")
            continue
        url = _texte(media.get("url"))
        if not url:
            chemin = _texte(media.get("chemin"))
            if chemin and url_medias:
                url = f"{url_medias.rstrip('/')}/{chemin.lstrip('/')}"
        if not url:
            ecarter("sans_url")
            continue
        if len(url) > LONGUEUR_URL:
            ecarter("url_trop_longue")
            continue

        identifiant = _texte(media.get("uuid"))
        try:
            identifiant = str(_uuid.UUID(identifiant)) if identifiant else None
        except ValueError:
            identifiant = None
        if identifiant is None:
            identifiant = str(_uuid.uuid5(NAMESPACE_MEDIA, f"{uuid_observation}|{url}"))
        if identifiant in vus:
            ecarter("doublon")
            continue
        vus.add(identifiant)

        medias.append({
            "unique_id_media": identifiant,
            "type": _texte(media.get("type")),
            "type_devine": type_devine(url),
            "media_url": url,
            # Le formulaire de GeoNature exige un titre, et la fiche d'observation
            # l'affiche en légende : à défaut, le nom du fichier vaut mieux qu'un blanc.
            "title_fr": (_texte(media.get("titre"), LONGUEUR_TITRE)
                         or _texte(urlsplit(url).path.rsplit("/", 1)[-1], LONGUEUR_TITRE)
                         or "Média"),
            "author": _texte(media.get("auteur"), LONGUEUR_AUTEUR),
            "description_fr": _texte(media.get("description")),
        })
    return (medias, ecartes)
