"""Résolution du cd_nom TAXREF depuis un taxon GBIF.

Dans le module, la résolution se fait en base plutôt que par le fichier `TAXREFv17.txt` :
`taxonomie.taxref_liens` contient les correspondances officielles publiées par PatriNat
(`ct_name = 'GBIF'`, `ct_sp_id` = taxonKey GBIF). C'est plus rapide, toujours à jour avec
le TAXREF de l'instance, et ça évite de demander à l'adoptant un fichier de 855 Mo.

⚠ La couverture n'est pas totale (≈ 93,7 % de TAXREF sur une instance de référence) et
certains taxonKey n'ont aucune correspondance. Les occurrences non résolues doivent être
**rejetées**, jamais insérées avec `cd_nom` NULL : une telle ligne fait planter
l'évaluation des permissions (`taxref_tree` est alors None) dès qu'une permission avec
filtre taxonomique existe — un défaut différé, sans lien apparent avec sa cause.
"""

import json
import urllib.request

from sqlalchemy import text
from geonature.utils.env import db

# TAXREF est lui-même publié comme référentiel sur GBIF. Interroger ses « related »
# rattrape des taxons absents de taxonomie.taxref_liens : mesuré sur un échantillon de
# 190 taxonKeys ariégeois, taxref_liens en résout 188 et ce repli récupère les 2 autres —
# dont Viola canina, qui n'a rien d'exotique. Le fichier TAXREF_LIENS.txt de PatriNat
# n'est pas exhaustif, et ses lacunes ne portent pas que sur des cas marginaux.
GBIF_TAXREF_DATASET = "0e61f8fe-7d25-4f81-ada7-d970bbb2c6d6"


def load_index() -> dict[str, int]:
    """Charge la table taxonKey GBIF -> cd_nom en mémoire.

    Un seul aller-retour vaut mieux que N requêtes : le volume tient largement en RAM
    (quelques centaines de milliers d'entiers) et un import massif interrogerait sinon
    la base une fois par occurrence.
    """
    lignes = db.session.execute(
        text(
            """
            SELECT ct_sp_id, cd_nom
            FROM taxonomie.taxref_liens
            WHERE ct_name ILIKE 'GBIF' AND ct_sp_id ~ '^[0-9]+$'
            """
        )
    ).all()
    return {str(sp): int(cd) for sp, cd in lignes}


def resolve_via_gbif(taxon_key, cache: dict) -> int | None:
    """Repli : cd_nom via le référentiel TAXREF publié sur GBIF.

    Un appel réseau par taxonKey inconnu, mis en cache — le nombre de taxons distincts
    est très inférieur au nombre d'occurrences, donc le surcoût reste marginal.
    """
    cle = str(taxon_key)
    if cle in cache:
        return cache[cle]
    resultat = None
    try:
        url = (f"https://api.gbif.org/v1/species/{cle}/related"
               f"?datasetKey={GBIF_TAXREF_DATASET}")
        with urllib.request.urlopen(url, timeout=20) as r:
            for item in json.load(r).get("results", []):
                taxon_id = str(item.get("taxonID", ""))
                if taxon_id.isdigit():
                    resultat = int(taxon_id)
                    break
    except Exception:
        resultat = None
    cache[cle] = resultat
    return resultat


def existe_dans_taxref(cd_nom: int) -> bool:
    """Le cd_nom est-il présent dans le TAXREF de l'instance ?

    Le référentiel GBIF peut être d'une version de TAXREF différente de celle installée :
    insérer un cd_nom absent violerait la clé étrangère de `synthese`.
    """
    return bool(db.session.execute(
        text("SELECT 1 FROM taxonomie.taxref WHERE cd_nom = :c"), {"c": cd_nom}
    ).first())


def resolve(occ: dict, index: dict[str, int], cache_gbif: dict | None = None) -> int | None:
    """cd_nom d'une occurrence GBIF, ou None si non résolue.

    `taxonKey` d'abord, puis repli sur `acceptedTaxonKey` et `speciesKey` : GBIF distingue
    le taxon cité du taxon accepté, et une occurrence identifiée à la sous-espèce n'a pas
    forcément de correspondance TAXREF alors que l'espèce en a une.

    En dernier recours, si `cache_gbif` est fourni, interrogation du référentiel TAXREF
    publié sur GBIF pour les taxons absents de `taxref_liens`.
    """
    cles = [occ.get(c) for c in ("taxonKey", "acceptedTaxonKey", "speciesKey")]
    cles = [c for c in cles if c is not None]

    for valeur in cles:
        cd_nom = index.get(str(valeur))
        if cd_nom:
            return cd_nom

    if cache_gbif is None:
        return None
    for valeur in cles:
        cd_nom = resolve_via_gbif(valeur, cache_gbif)
        if cd_nom and existe_dans_taxref(cd_nom):
            # Mémorisé dans l'index pour ne pas réinterroger le réseau sur ce lot.
            index[str(valeur)] = cd_nom
            return cd_nom
    return None
