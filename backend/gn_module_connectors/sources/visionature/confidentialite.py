"""Anonymisation des observateurs et respect des marqueurs de confidentialité.

Deux sujets distincts, souvent confondus.

**L'anonymisation** protège les personnes : le nom de l'observateur est une donnée
personnelle. `gn_vn2synthese` pseudonymise par HMAC-SHA1 de l'identifiant — bonne idée,
mais leur clé secrète est écrite en clair dans un dépôt public, ce qui rend les
pseudonymes recalculables et donc réidentifiables par quiconque énumère les identifiants.
La clé est ici **obligatoirement fournie par la configuration**, et le module refuse de
pseudonymiser sans elle plutôt que de recourir à une valeur par défaut.

**Les marqueurs de confidentialité** protègent la donnée. Les champs réels de l'API
Biolovision — vérifiés sur les scripts de production de `gn_vn2synthese` — sont :

- `hidden` : observation masquée par son auteur ou par un modérateur. Importée, mais
  avec un niveau de diffusion restreint — c'est une protection de l'espèce ou du site,
  pas une mise au rebut ;
- `admin_hidden` : observation en cours de vérification par un modérateur ;
- `admin_hidden_type` : `incomplete`, `question` ou `refused` ;
- `hidden_comment` : commentaire réservé aux modérateurs ;
- `anonymous` / `anonymous_in_export` : consentement de l'observateur sur son nom.

⚠ Ce module a longtemps testé `is_hidden` et `export_excluded`, qui **n'existent pas** :
le filtre ne rejetait donc rien, alors que le README affirmait le contraire. Toute
modification de ces noms doit être vérifiée contre l'API, pas supposée.
"""

import hashlib
import hmac


def vrai(source: dict | None, cle: str) -> bool:
    """Lecture d'un booléen de l'API, qui arrive en chaîne (« 1 », « 0 »)."""
    return str((source or {}).get(cle) or "").strip().lower() in ("1", "true", "yes")


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
    # `second_hand` : la saisie rapporte l'observation d'un tiers. Le nom porté par
    # l'enregistrement est celui du saisisseur, pas de l'observateur — l'écrire dans
    # `observers` attribuerait l'observation à quelqu'un qui ne l'a pas faite.
    # `gn_vn2synthese` met `observers` et `determiner` à NULL dans ce cas ; on fait de
    # même, et il n'y a rien à pseudonymiser puisqu'aucun nom n'est publié.
    if vrai(observation, "second_hand"):
        return (None, "donnée rapportée par un tiers")

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


# Motifs de masquage administratif justifiant un rejet. `incomplete` et `question`
# signalent une vérification en cours — la donnée reste plausible et sera importée ;
# `refused` est un rejet explicite du modérateur, qu'il serait fautif de republier.
ADMIN_HIDDEN_REJET = {"refused"}

# Niveau de diffusion appliqué aux observations masquées, en cd_nomenclature NIV_PRECIS.
# Référentiel (relevé sur instance) :
#   0 Standard   1 Commune   2 Maille   3 Département   4 Aucune   5 Précise
#
# « 4 » (Aucune) est retenu par défaut : c'est le code que GeoNature emploie pour une
# donnée non diffusable — sa migration v1 -> v2 traduit `diffusable = false` par '4' et
# `diffusable = true` par '5'. Cela correspond au comportement de VisioNature, où une
# observation masquée n'apparaît pas publiquement, même dégradée.
#
# `gn_vn2synthese` retient « 2 » (Maille) : l'observation alimente les cartes de
# répartition sans livrer la localisation précise. Défendable, et moins restrictif.
# Le choix relève de la convention passée avec le producteur, d'où la surcharge.
NIV_PRECIS_MASQUEE = "4"


def est_confidentielle(observation: dict, sighting: dict | None = None) -> str | None:
    """Motif de rejet, ou None si l'observation peut entrer en Synthèse.

    ⚠ `hidden` n'est PAS un motif de rejet. Dans VisioNature, on masque une observation
    pour protéger l'espèce ou le site — nid de rapace, station d'orchidée, gîte à
    chiroptères — pas pour la retirer du circuit. C'est précisément la donnée à enjeu,
    celle que l'accès à l'API est censé apporter. Elle est donc importée, avec un niveau
    de diffusion restreint (cf. `niveau_diffusion`), comme le fait `gn_vn2synthese`.

    Seul un refus explicite de modérateur écarte l'observation : `admin_hidden_type` vaut
    alors « refused ». Les motifs « incomplete » et « question » signalent une
    vérification en cours, pas un rejet.
    """
    for source, nom in ((observation, "observation"), (sighting, "relevé")):
        motif = str((source or {}).get("admin_hidden_type") or "").strip().lower()
        if motif in ADMIN_HIDDEN_REJET:
            return f"refusée par un modérateur ({nom})"
    return None


def est_masquee(observation: dict, sighting: dict | None = None) -> bool:
    """L'observation est-elle masquée à la source, par son auteur ou par un modérateur ?

    L'API ne distingue pas les deux gestes : `hidden` couvre l'un et l'autre.
    """
    return vrai(observation, "hidden") or vrai(sighting, "hidden")


def niveau_diffusion(observation: dict, sighting: dict | None = None,
                     code_masquee: str = NIV_PRECIS_MASQUEE) -> str | None:
    """cd_nomenclature NIV_PRECIS, ou None si la source n'exprime aucune restriction.

    None n'est pas un défaut par défaut : depuis la migration « Do not auto-compute
    diffusion_level », GeoNature a retiré le DEFAULT de cette colonne et ne la calcule
    plus. NULL y signifie « le producteur ne se prononce pas », ce qui est exact pour une
    observation que personne n'a choisi de masquer.
    """
    return code_masquee if est_masquee(observation, sighting) else None


def nettoyer_commentaire(observation: dict) -> str | None:
    """Commentaire public uniquement. `private_comment` n'a pas à sortir de l'outil."""
    return (observation.get("comment") or "").strip() or None
