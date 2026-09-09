"""Anonymisation des observateurs et respect des marqueurs de confidentialité.

Deux sujets distincts, souvent confondus.

**L'anonymisation** protège les personnes : le nom de l'observateur est une donnée
personnelle. `gn_vn2synthese` pseudonymise par HMAC-SHA1 de l'identifiant — bonne idée,
mais leur clé secrète est écrite en clair dans un dépôt public, ce qui rend les
pseudonymes recalculables et donc réidentifiables par quiconque énumère les identifiants.
La clé est ici **obligatoirement fournie par la configuration**, et le module refuse de
pseudonymiser sans elle plutôt que de recourir à une valeur par défaut.

**Les marqueurs de confidentialité** protègent la donnée : VisioNature distingue
`is_hidden` (observation masquée par son auteur ou par un modérateur),
`export_excluded` (explicitement exclue des exports) et `private_comment`. Les ignorer
reviendrait à publier ce que le producteur a choisi de retenir.
"""

import hashlib
import hmac


def pseudonyme(identifiant, secret: str) -> str:
    """Pseudonyme stable d'un observateur.

    HMAC-SHA256 plutôt que SHA1, et clé fournie par l'exploitant. Le pseudonyme reste
    constant d'un import à l'autre — deux observations du même observateur restent
    rapprochables — sans que le nom réel n'apparaisse jamais en base.
    """
    if not secret:
        raise ValueError(
            "Aucune clé de pseudonymisation. Renseignez "
            "[visionature] pseudonymisation_secret, ou désactivez l'anonymisation. "
            "Une clé par défaut rendrait les pseudonymes recalculables par un tiers.")
    return hmac.new(secret.encode("utf-8"), str(identifiant or "").encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def index_anonymat(observateurs: list[dict]) -> dict[str, bool]:
    """uid d'observateur -> souhaite l'anonymat.

    Le champ `anonymous` du contrôleur `observers` porte le consentement **individuel** :
    chaque contributeur décide si son nom peut sortir. Un interrupteur global serait trop
    grossier — il écraserait le choix de chacun dans un sens ou dans l'autre.
    """
    index = {}
    for o in observateurs:
        uid = str(o.get("@id") or o.get("id") or "").strip()
        if uid:
            index[uid] = str(o.get("anonymous") or "0").strip() in ("1", "true", "True")
    return index


def observateur(observation: dict, index_anonymat: dict[str, bool] | None = None,
                secret: str = "", forcer_anonymat: bool = False) -> tuple[str | None, str]:
    """(valeur pour `synthese.observers`, motif).

    Trois cas, et le troisième n'est pas le second :

    - `anonymous = 0` : pas de demande d'anonymat, le nom est écrit. Dans VisioNature,
      `anonymous` est une démarche positive ; son absence n'exprime aucun souhait, et la
      paternité d'une observation a de la valeur pour un naturaliste.
    - `anonymous = 1` : pseudonyme.
    - **observateur absent du référentiel** : pseudonyme également, mais pour une autre
      raison — c'est de l'ignorance, pas un consentement. Publier par défaut ferait d'une
      panne de chargement du référentiel une divulgation.
    """
    uid = str(observation.get("@uid") or observation.get("@id") or "").strip()
    nom = (observation.get("name") or "").strip() or None

    if forcer_anonymat:
        return (f"obs-{pseudonyme(uid, secret)[:12]}" if uid else None, "anonymat forcé")

    index_anonymat = index_anonymat or {}
    if uid not in index_anonymat:
        return (f"obs-{pseudonyme(uid, secret)[:12]}" if uid else None,
                "observateur inconnu du référentiel")
    if index_anonymat[uid]:
        return (f"obs-{pseudonyme(uid, secret)[:12]}", "anonymat demandé")
    return (nom, "nom publié")


def est_confidentielle(observation: dict, sighting: dict | None = None) -> str | None:
    """Motif de confidentialité, ou None si l'observation est diffusable.

    Les valeurs booléennes de l'API arrivent en chaînes (« 1 », « 0 ») : comparer à
    `True` ne fonctionnerait pas.
    """
    def vrai(source, cle):
        return str((source or {}).get(cle) or "").strip().lower() in ("1", "true", "yes")

    for source, nom in ((observation, "observation"), (sighting, "relevé")):
        if vrai(source, "is_hidden"):
            return f"masquée à la source ({nom})"
        if vrai(source, "export_excluded"):
            return f"exclue des exports par le producteur ({nom})"
    return None


def nettoyer_commentaire(observation: dict) -> str | None:
    """Commentaire public uniquement. `private_comment` n'a pas à sortir de l'outil."""
    return (observation.get("comment") or "").strip() or None
