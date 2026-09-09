"""Suppression ciblée d'observations importées.

Générique : ne connaît aucune source externe.

Toute opération est **bornée à une seule `id_source`**. C'est la garantie centrale : le
module ne peut pas toucher aux données saisies localement ni à celles venues d'un autre
import, même sur une erreur de critère.

La suppression d'une ligne de `gn_synthese.synthese` entraîne en cascade celle de ses
rattachements dans `cor_area_synthese`, et le trigger `tri_log_delete_synthese` en
consigne la trace dans `gn_synthese.t_log_synthese` — la suppression reste donc traçable.
"""

from sqlalchemy import text
from geonature.utils.env import db


def _conditions(taxon: str | None, max_uncertainty: int | None) -> tuple[str, dict]:
    """Construit les critères additionnels et leurs paramètres."""
    clauses, params = [], {}
    if taxon:
        # Recherche sur les rangs supérieurs de TAXREF plutôt que sur un identifiant
        # GBIF : c'est ainsi qu'un naturaliste désigne un groupe (« les chiroptères »),
        # et cela reste vrai quelle que soit la source de la donnée.
        clauses.append("""
            s.cd_nom IN (
                SELECT cd_nom FROM taxonomie.taxref
                WHERE regne = :taxon OR phylum = :taxon OR classe = :taxon
                   OR ordre = :taxon OR famille = :taxon
                   OR nom_valide ILIKE :taxon_like OR nom_complet ILIKE :taxon_like
            )""")
        params["taxon"] = taxon
        params["taxon_like"] = f"{taxon}%"
    if max_uncertainty:
        clauses.append('(s."precision" IS NOT NULL AND s."precision" > :incertitude)')
        params["incertitude"] = max_uncertainty
    return ("".join(f" AND {c}" for c in clauses), params)


def compter(id_source: int, id_dataset: int | None = None, taxon: str | None = None,
            max_uncertainty: int | None = None) -> int:
    cond, params = _conditions(taxon, max_uncertainty)
    params.update({"src": id_source, "jdd": id_dataset})
    return db.session.execute(
        text(f"""SELECT count(*) FROM gn_synthese.synthese s
                 WHERE s.id_source = :src
                   AND (:jdd IS NULL OR s.id_dataset = :jdd){cond}"""),
        params,
    ).scalar()


def supprimer(id_source: int, id_dataset: int | None = None, taxon: str | None = None,
              max_uncertainty: int | None = None) -> int:
    cond, params = _conditions(taxon, max_uncertainty)
    params.update({"src": id_source, "jdd": id_dataset})
    n = db.session.execute(
        text(f"""DELETE FROM gn_synthese.synthese s
                 WHERE s.id_source = :src
                   AND (:jdd IS NULL OR s.id_dataset = :jdd){cond}"""),
        params,
    ).rowcount
    return n


def jdd_vides(id_acquisition_framework: int) -> list[tuple[int, str]]:
    """JDD du cadre d'acquisition ne portant plus aucune observation, toutes sources."""
    return [
        (r[0], r[1])
        for r in db.session.execute(
            text("""
                SELECT d.id_dataset, d.dataset_name
                FROM gn_meta.t_datasets d
                WHERE d.id_acquisition_framework = :af
                  AND NOT EXISTS (SELECT 1 FROM gn_synthese.synthese s
                                  WHERE s.id_dataset = d.id_dataset)
                ORDER BY d.id_dataset
            """),
            {"af": id_acquisition_framework},
        ).all()
    ]


def supprimer_jdd(id_dataset: int) -> bool:
    """Supprime un JDD, seulement s'il ne porte plus aucune observation.

    Le garde-fou n'est pas redondant avec la clé étrangère : celle-ci ferait échouer la
    transaction, ici on préfère signaler proprement quel JDD ne peut pas partir.
    """
    reste = db.session.execute(
        text("SELECT count(*) FROM gn_synthese.synthese WHERE id_dataset = :d"),
        {"d": id_dataset},
    ).scalar()
    if reste:
        return False
    db.session.execute(
        text("DELETE FROM gn_meta.t_datasets WHERE id_dataset = :d"), {"d": id_dataset}
    )
    return True
