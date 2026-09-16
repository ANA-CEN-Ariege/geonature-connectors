"""Cache disque des référentiels distants, pour l'itération et la mise au point.

Un moissonnage VisioNature commence par trois téléchargements : le référentiel
d'espèces (63 616 entrées sur Faune-Occitanie), les groupes taxonomiques, et le
référentiel des observateurs (246 699 inscrits). Plusieurs minutes avant qu'une seule
observation ne soit traitée — insupportable quand on met un import au point.

**Désactivé par défaut**, et ce n'est pas de la prudence de principe :

- le référentiel des observateurs contient des **noms de personnes**. Tout le dispositif
  d'anonymisation du module vise à ne pas les conserver — le nom réel n'est jamais écrit
  en base pour qui a demandé l'anonymat. Les déposer en clair dans un fichier serait
  contradictoire si c'était fait sans le dire ;
- un référentiel périmé se traduit par des correspondances taxonomiques fausses ou des
  consentements obsolètes, sans que rien ne le signale. Le cache est un outil de mise au
  point, pas un mécanisme de production.

Les fichiers sont donc écrits en 0600 dans un répertoire en 0700, la durée de validité
est explicite, et l'appelant est averti quand il met en cache de la donnée personnelle.

La clé inclut l'URL de l'instance : passer de faune.fr à faune-occitanie.org ne doit
jamais servir le référentiel de l'autre.
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

# Répertoire par défaut, si la configuration n'en impose pas.
DOSSIER_DEFAUT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) \
    / "gn_module_connectors"


def _chemin(dossier: Path, instance: str, nom: str) -> Path:
    cle = hashlib.sha256(f"{instance}|{nom}".encode("utf-8")).hexdigest()[:16]
    return dossier / f"{nom}-{cle}.json"


def charger(nom: str, instance: str, heures: float, dossier=None):
    """Contenu en cache s'il est encore valide, sinon None.

    Toute anomalie — fichier illisible, JSON corrompu, horodatage absent — vaut absence
    de cache : on retéléchargera. Un cache est une optimisation, il n'a jamais le droit
    de faire échouer ce qu'il accélère.
    """
    if not heures:
        return None
    fichier = _chemin(Path(dossier or DOSSIER_DEFAUT), instance, nom)
    try:
        with open(fichier, encoding="utf-8") as flux:
            paquet = json.load(flux)
        if time.time() - float(paquet["horodatage"]) > heures * 3600:
            return None
        return paquet["contenu"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def enregistrer(nom: str, instance: str, contenu, heures: float, dossier=None) -> Path | None:
    """Écrit le cache. Retourne le chemin, ou None si le cache est désactivé.

    L'écriture passe par un fichier temporaire renommé : une interruption en cours
    d'écriture laisserait sinon un cache tronqué que `charger` accepterait comme valide
    si le JSON se trouvait rester analysable. Ce fichier temporaire porte un nom unique
    par écriture (pid + suffixe aléatoire) : deux processus moissonnant la même instance
    en parallèle écrivent chacun dans leur propre fichier, sans jamais entrelacer leurs
    écritures avant le remplacement atomique final.
    """
    if not heures:
        return None
    racine = Path(dossier or DOSSIER_DEFAUT)
    try:
        racine.mkdir(parents=True, exist_ok=True)
        os.chmod(racine, 0o700)
        fichier = _chemin(racine, instance, nom)
        descripteur, nom_temporaire = tempfile.mkstemp(
            prefix=f"{fichier.stem}.{os.getpid()}.", suffix=".tmp", dir=racine)
        temporaire = Path(nom_temporaire)
        with os.fdopen(descripteur, "w", encoding="utf-8") as flux:
            json.dump({"horodatage": time.time(), "instance": instance,
                       "contenu": contenu}, flux, ensure_ascii=False)
        os.chmod(temporaire, 0o600)
        temporaire.replace(fichier)
        return fichier
    except OSError:
        return None


def vider(dossier=None) -> int:
    """Supprime tous les fichiers de cache. Retourne le nombre supprimé."""
    racine = Path(dossier or DOSSIER_DEFAUT)
    if not racine.is_dir():
        return 0
    n = 0
    for fichier in list(racine.glob("*.json")) + list(racine.glob("*.tmp")):
        try:
            fichier.unlink()
            n += 1
        except OSError:
            pass
    return n
