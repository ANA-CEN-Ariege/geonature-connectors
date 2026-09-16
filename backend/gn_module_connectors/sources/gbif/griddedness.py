"""Détection des jeux de données GBIF publiés à la maille ou au centroïde.

GBIF **dispose** d'un dispositif officiel, mais ce n'est pas un `issue` : c'est un
machine tag du registre, `griddedDataSet.jwaller.gbif.org / griddedDataset`, issu des
travaux de John Waller (https://data-blog.gbif.org/post/finding-gridded-datasets/).
Sa valeur est un JSON `{"percentNN": …, "countNN": …, "distanceNN": …}` où :

- `distanceNN` est le pas de la grille détectée, en **degrés** (0,09° ≈ 10 km) ;
- `percentNN` est la part des points uniques partageant cette distance au plus proche
  voisin — c'est l'indice de « grillage » ;
- `countNN` est le nombre de points uniques analysés.

⚠ **Ce tag est figé depuis 2020** : les 1 833 tags existants (981 jeux) ont tous été
créés cette année-là, alors que les jeux continuent d'être recrawlés. Il produit donc des
**faux négatifs garantis** pour tout jeu publié ou modifié depuis. D'où l'heuristique de
repli ci-dessous.

Ce qui ne marche **pas**, vérifié :

- les `issues` GBIF — aucune des 105 constantes de `OccurrenceIssue` ne concerne la maille ;
- `COORDINATE_ROUNDED` — signifie « coordonnée source arrondie à 5 décimales », donc
  jamais levé sur une donnée maillée, qui a peu de décimales ;
- `hasGeospatialIssue` ;
- `distanceFromCentroidInMeters` — GBIF ne calcule les centroïdes qu'au niveau **pays**,
  ni commune ni maille.

Aucune convention Darwin Core ne permet de déclarer une donnée agrégée : le champ
sémantiquement correct serait `footprintWKT`, mais il n'est jamais renseigné par les
agrégateurs nationaux. Le seul signal déclaratif est `coordinateUncertaintyInMeters`,
et il est ambigu — 5 000 m ne dit pas si c'est un rayon autour d'un point réel ou la
demi-largeur d'une cellule.
"""

import json
import logging
import urllib.parse
import urllib.request
from collections import Counter

logger = logging.getLogger(__name__)

API = "https://api.gbif.org/v1"

TAG_NAMESPACE = "griddedDataSet.jwaller.gbif.org"
TAG_NAME = "griddedDataset"

# Seuils de `percentNN`. Le blog GBIF indique que > 0,30 « retire quasi tous les jeux
# grillés », > 0,50 « la plupart », > 0,75 « uniquement les évidents ». On rejette au-delà
# de 0,50 et on signale la zone grise pour arbitrage humain.
PERCENT_NN_REJET = 0.50
PERCENT_NN_SUSPECT = 0.30

# Au-delà, une incertitude uniforme trahit une publication à la maille ou au centroïde.
SEUIL_MAILLE_M = 1000
PART_UNIFORME = 0.80
PART_DECLAREE_MIN = 0.50
# Valeurs par défaut connues des géocodeurs, à traiter comme suspectes (guide GBIF).
INCERTITUDES_SUSPECTES = {301.0, 999.0, 3036.0, 9999.0}


def machine_tag(dataset_key: str) -> dict | None:
    """Tag « gridded » du registre GBIF, ou None. Les doublons sont dédupliqués."""
    try:
        with urllib.request.urlopen(f"{API}/dataset/{dataset_key}", timeout=30) as r:
            tags = json.load(r).get("machineTags", [])
    except (OSError, ValueError, AttributeError) as e:
        # OSError : réseau (timeout, DNS, HTTPError...) ; ValueError : JSON malformé ;
        # AttributeError : réponse JSON qui n'est pas l'objet attendu (pas de .get).
        logger.warning("Impossible de lire les machine tags du dataset GBIF %s : %s",
                        dataset_key, e)
        return None
    for t in tags:
        if t.get("namespace") == TAG_NAMESPACE and t.get("name") == TAG_NAME:
            try:
                v = json.loads(t.get("value") or "{}")
            except json.JSONDecodeError:
                continue
            return {
                "percent_nn": float(v.get("percentNN", 0)),
                "count_nn": int(v.get("countNN", 0)),
                "distance_nn": float(v.get("distanceNN", 0)),
                "created": (t.get("created") or "")[:10],
            }
    return None


def _echantillon(dataset_key: str, filtres: dict, taille: int, tranches: int) -> list[dict]:
    """Échantillon réparti sur plusieurs offsets.

    Prendre les N premiers enregistrements ne convient pas : GBIF les renvoie groupés,
    si bien qu'un jeu maillé peut sortir 300 fois la même cellule et donner l'illusion
    d'un point unique. Mesuré sur le jeu FCBN : 300 consécutifs → 1 coordonnée distincte,
    1 000 répartis sur 5 offsets → 21.
    """
    # Offsets volontairement PEU profonds : la pagination GBIF s'effondre au-delà de
    # ~5 000. Mesuré : 0,6 s à l'offset 5 000, 24 s à 10 000, 27 s à 20 000. Un
    # échantillonnage 0..20 000 coûtait 75 s par jeu — soit 90 % du temps d'un import.
    # En 0..4 000 il tombe à 2,3 s pour la même capacité à repérer un jeu maillé.
    par_tranche = max(1, taille // max(1, tranches))
    pas = 1000
    resultats: list[dict] = []
    for i in range(tranches):
        params = {**filtres, "datasetKey": dataset_key,
                  "limit": par_tranche, "offset": i * pas}
        url = f"{API}/occurrence/search?" + urllib.parse.urlencode(params, doseq=True)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                lot = json.load(r).get("results", [])
        except (OSError, ValueError, AttributeError) as e:
            # Résilience voulue : un échantillon partiel vaut mieux qu'un import qui
            # échoue sur un simple aléa réseau — mais l'échec ne doit plus être muet.
            logger.warning("Échantillonnage du dataset GBIF %s interrompu à l'offset %s : %s",
                            dataset_key, i * pas, e)
            break
        if not lot:
            break
        resultats.extend(lot)
    return resultats


_CACHE: dict[tuple, dict] = {}


def inspect(dataset_key: str, filtres: dict, taille: int = 1000,
            tranches: int = 5) -> dict:
    """Verdict de précision d'un jeu de données.

    `verdict` vaut :
      - "maille"  : grillé — tag GBIF au-delà du seuil, ou incertitude élevée et uniforme
      - "suspect" : indices concordants sans certitude, à arbitrer
      - "inconnu" : trop peu d'incertitudes déclarées pour trancher
      - "ok"      : précision compatible avec de l'observation ponctuelle
      - "vide"    : aucune occurrence dans le périmètre
    """
    cle_cache = (dataset_key, tuple(sorted(filtres.items())), taille, tranches)
    if cle_cache in _CACHE:
        return _CACHE[cle_cache]

    res: dict = {"dataset_key": dataset_key, "tag": machine_tag(dataset_key)}
    tag = res["tag"]

    # On échantillonne TOUJOURS, même quand le tag est formel : il peut se tromper.
    # Mesuré sur « Données piscicoles et astacicoles de l'OFB » — tag à 88 % de grillage,
    # alors que le jeu déclare 15 m d'incertitude sur 100 % de ses valeurs et que ses
    # points ariégeois sont espacés irrégulièrement. Ce sont des stations de pêche
    # revisitées, pas une maille. Rejeter sur le seul tag écarterait de la donnée précise.
    echantillon = _echantillon(dataset_key, filtres, taille, tranches)
    res["n"] = len(echantillon)
    if not echantillon:
        res.update(verdict="vide", origine="echantillon")
        _CACHE[cle_cache] = res
        return res

    points = Counter((o.get("decimalLongitude"), o.get("decimalLatitude"))
                     for o in echantillon)
    res["points_distincts"] = len(points)
    res["redondance"] = round(len(echantillon) / len(points), 1)

    # Ne raisonner que sur les incertitudes renseignées : prendre le mode brut ferait
    # ressortir `None` dès qu'il est la valeur la plus fréquente, même minoritaire.
    # iNaturalist n'a que 18 % de valeurs absentes, mais elles suffisaient à masquer
    # les 82 % de valeurs variées qui prouvent une précision par observation.
    declarees = [float(o["coordinateUncertaintyInMeters"]) for o in echantillon
                 if o.get("coordinateUncertaintyInMeters") is not None]
    res["part_declaree"] = round(len(declarees) / len(echantillon), 3)

    dominante = part = None
    if declarees:
        dominante, effectif = Counter(declarees).most_common(1)[0]
        part = effectif / len(declarees)
    res["incertitude_dominante"] = dominante
    res["part_dominante"] = round(part, 3) if part is not None else None

    # Une incertitude déclarée à la fois uniforme et fine contredit le tag : soit les
    # coordonnées sont réellement précises, soit le producteur ment. Dans le doute on
    # ne tranche pas à sa place — on signale.
    precision_fine_uniforme = (
        res["part_declaree"] >= PART_DECLAREE_MIN
        and part is not None and part >= PART_UNIFORME
        and dominante is not None and dominante < SEUIL_MAILLE_M
        and dominante not in INCERTITUDES_SUSPECTES
    )
    tag_formel = bool(tag) and tag["percent_nn"] >= PERCENT_NN_REJET
    tag_suspect = bool(tag) and tag["percent_nn"] >= PERCENT_NN_SUSPECT

    # Un échantillon superficiel n'est pas un tirage aléatoire : GBIF renvoie les
    # enregistrements groupés, si bien qu'un jeu MIXTE peut paraître uniformément maillé
    # selon la fenêtre observée. Mesuré sur SICEN Occitanie : « 5 000 m sur 93 % » en
    # offsets 0..4 000, mais « 10 m sur 51 % » en 0..20 000.
    # On n'écarte donc un jeu que si le tag GBIF ET l'échantillon concordent ; sinon on
    # signale et l'on s'en remet au filtre par occurrence, qui lui ne se trompe pas.
    echantillon_maille = (
        res["part_declaree"] >= PART_DECLAREE_MIN
        and part is not None and part >= PART_UNIFORME
        and dominante is not None
        and (dominante >= SEUIL_MAILLE_M or dominante in INCERTITUDES_SUSPECTES)
    )

    if tag_formel and precision_fine_uniforme:
        verdict, origine = "suspect", "tag_contredit"
    elif tag_formel and echantillon_maille:
        verdict, origine = "maille", "machine_tag"
    elif tag_formel:
        verdict, origine = "suspect", "tag_seul"
    elif echantillon_maille:
        verdict, origine = "suspect", "echantillon_seul"
    elif res["part_declaree"] < PART_DECLAREE_MIN:
        verdict = "suspect" if tag_suspect else "inconnu"
        origine = "heuristique"
    elif tag_suspect:
        verdict, origine = "suspect", "heuristique"
    else:
        verdict, origine = "ok", "heuristique"

    res.update(verdict=verdict, origine=origine)
    _CACHE[cle_cache] = res
    return res


def explique(res: dict) -> str:
    """Phrase lisible résumant le verdict."""
    tag = res.get("tag")
    v = res["verdict"]

    if v == "vide":
        return "aucune occurrence dans le périmètre"

    if res.get("origine") == "tag_contredit" and tag:
        return (f"tag GBIF « grillé » ({tag['percent_nn']:.0%}, {tag['created']}) CONTREDIT "
                f"par une incertitude fine et uniforme "
                f"({res['incertitude_dominante']:.0f} m sur {res['part_dominante']:.0%} "
                f"des valeurs) — à arbitrer")

    if res.get("origine") == "machine_tag" and tag:
        km = tag["distance_nn"] * 111  # 1° de latitude ≈ 111 km
        return (f"grillé selon GBIF — {tag['percent_nn']:.0%} des {tag['count_nn']} points "
                f"uniques sur un pas de {tag['distance_nn']}° (≈ {km:.0f} km), "
                f"tag de {tag['created']}")

    suffixe = f" ; tag GBIF percentNN={tag['percent_nn']:.0%} ({tag['created']})" if tag else ""

    if v == "maille":
        return (f"incertitude uniforme — {res['part_dominante']:.0%} des valeurs déclarées "
                f"à {res['incertitude_dominante']:.0f} m{suffixe}")
    if v == "inconnu":
        return (f"incertitude déclarée sur {res['part_declaree']:.0%} de l'échantillon "
                f"seulement — précision invérifiable "
                f"(redondance {res['redondance']} obs/point){suffixe}")
    if v == "suspect":
        if res.get("origine") == "tag_seul":
            return (f"tag GBIF « grillé » ({tag['percent_nn']:.0%}) mais l'échantillon ne "
                    f"le confirme pas — conservé, le filtre par occurrence tranchera")
        if res.get("origine") == "echantillon_seul":
            return (f"échantillon uniforme à {res['incertitude_dominante']:.0f} m "
                    f"({res['part_dominante']:.0%}) sans tag GBIF — conservé, le filtre "
                    f"par occurrence tranchera")
        return f"indices concordants sans certitude — à arbitrer{suffixe}"
    return (f"précision ponctuelle — incertitude dominante "
            f"{res['incertitude_dominante']:.0f} m sur {res['part_dominante']:.0%} "
            f"des valeurs déclarées{suffixe}")
