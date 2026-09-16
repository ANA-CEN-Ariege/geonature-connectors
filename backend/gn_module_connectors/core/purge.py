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


def rangs_presents(id_source: int, id_dataset: int | None = None) -> list[tuple]:
    """Rangs TAXREF réellement portés par les observations d'une source.

    Un `--taxon` qui ne correspond à rien laisse l'utilisateur sans indice : « 0
    observation concernée » alors qu'il en voit dans l'interface. Les noms de rangs de
    TAXREF ne sont pas ceux du langage courant, et ils changent d'une version à l'autre —
    autant montrer ce que la base contient plutôt que de faire deviner.
    """
    return [
        tuple(r) for r in db.session.execute(
            text("""SELECT t.classe, t.ordre, t.famille, count(*)
                    FROM gn_synthese.synthese s
                    JOIN taxonomie.taxref t ON t.cd_nom = s.cd_nom
                    WHERE s.id_source = :src
                      AND (:jdd IS NULL OR s.id_dataset = :jdd)
                    GROUP BY 1, 2, 3
                    ORDER BY 4 DESC
                    LIMIT 15"""),
            {"src": id_source, "jdd": id_dataset},
        ).all()
    ]


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


def supprimer_par_identifiants_source(id_source: int, champ: str,
                                      identifiants: list[str]) -> int:
    """Supprime les observations dont `additional_data->>champ` figure dans la liste.

    Sert à répercuter une suppression faite à la source. Le rapprochement passe par
    `additional_data` plutôt que par `entity_source_pk_value` : un relevé VisioNature
    peut avoir donné plusieurs lignes de Synthèse — une par observateur —, et toutes
    doivent partir ensemble.

    `champ` nomme une entrée de JSONB et ne peut donc pas être un paramètre lié ; il doit
    venir du code du connecteur, jamais de la configuration ni des données. `identifiants`,
    qui vient des données, est lié.
    """
    if not identifiants:
        return 0
    return db.session.execute(
        text(f"""
            DELETE FROM gn_synthese.synthese
            WHERE id_source = :s
              AND additional_data->>'{champ}' = ANY(:ids)
        """),
        {"s": id_source, "ids": [str(i) for i in identifiants]},
    ).rowcount


def supprimer_par_uuid(uuids: list[str], sauf_id_source: int) -> int:
    """Supprime les lignes portant ces UUID, sauf celles de `sauf_id_source`.

    Sert au cas où une observation moissonnée arrive avec un `unique_id_sinp` déjà en base
    sous une autre source — typiquement la même donnée reçue par le GBIF et directement de
    son producteur. L'`ON CONFLICT` ne peut pas arbitrer : il ne réécrit jamais
    `id_source`, et laisserait une ligne dont la provenance affichée contredirait le
    contenu (cf. `core/synthese.conflits_autre_source`). Il faut donc supprimer, puis
    réinsérer dans la même transaction.

    ⚠ Opération destructrice, et la seule du module qui touche des lignes d'une source
    autre que celle du connecteur appelant. Elle reste bornée aux UUID explicitement
    fournis — ceux du lot en cours —, jamais à un critère large.
    """
    if not uuids:
        return 0
    return db.session.execute(
        text("""DELETE FROM gn_synthese.synthese
                WHERE unique_id_sinp = ANY(CAST(:u AS uuid[]))
                  AND id_source IS DISTINCT FROM :src"""),
        {"u": [str(u) for u in uuids], "src": sauf_id_source},
    ).rowcount


def compter_absents(id_source: int, id_datasets: list[int], uuids_vus: list[str],
                    marqueurs: dict[str, str]) -> int:
    """Combien de lignes de cette source ne figurent plus dans le corpus distant."""
    requete, params = _absents(id_source, id_datasets, uuids_vus, marqueurs)
    if requete is None:
        return 0
    return db.session.execute(
        text(f"SELECT count(*) FROM gn_synthese.synthese WHERE {requete}"), params
    ).scalar() or 0


def supprimer_absents(id_source: int, id_datasets: list[int], uuids_vus: list[str],
                      marqueurs: dict[str, str]) -> int:
    """Supprime les lignes de cette source absentes du corpus distant.

    C'est ainsi qu'une suppression faite à la source se répercute quand l'API ne publie
    aucun journal de suppression : on relit tout, et ce qui n'est pas revenu a disparu.

    ⚠ Le raisonnement n'est valide **que** si la relecture est complète. Un filtre de
    date, un plafond de résultats ou une pagination interrompue rendraient absentes des
    lignes bien vivantes : c'est à l'appelant de refuser d'exécuter dans ce cas, et
    `geonature-reconcilier` le fait avant même d'appeler ici.

    Triple bornage, dont aucun n'est superflu :

    - `id_source`, la garantie de base du module ;
    - `id_datasets`, ceux réellement rencontrés dans cette moisson : un jeu hors du
      périmètre courant ne doit pas être vidé sous prétexte qu'on ne l'a pas relu ;
    - `marqueurs`, des couples clé/valeur d'`additional_data` (instance et export) : deux
      exports alimentant la même source ne peuvent pas se supprimer l'un l'autre.
    """
    requete, params = _absents(id_source, id_datasets, uuids_vus, marqueurs)
    if requete is None:
        return 0
    return db.session.execute(
        text(f"DELETE FROM gn_synthese.synthese WHERE {requete}"), params
    ).rowcount


def _absents(id_source: int, id_datasets: list[int], uuids_vus: list[str],
             marqueurs: dict[str, str]) -> tuple[str | None, dict]:
    """Clause WHERE désignant les lignes absentes du corpus relu, et ses paramètres.

    Retourne `(None, {})` — donc « ne touche à rien » — quand le bornage serait vide :
    sans jeu de données rencontré, ou sans aucun UUID relu, la clause ne désignerait plus
    un écart mais la totalité de ce que la source a écrit.
    """
    if not id_datasets or not uuids_vus:
        return (None, {})
    conditions = [
        "id_source = :src",
        "id_dataset = ANY(:jdds)",
        # `NOT (… = ANY(…))` rend NULL sur un `unique_id_sinp` NULL, donc la ligne n'est
        # pas retenue. C'est le bon sens de l'erreur : on ne supprime pas ce qu'on ne
        # sait pas rapprocher.
        "NOT (unique_id_sinp = ANY(CAST(:u AS uuid[])))",
    ]
    params = {
        "src": id_source,
        "jdds": list(id_datasets),
        "u": [str(u) for u in uuids_vus],
    }
    for rang, (cle, valeur) in enumerate(sorted(marqueurs.items())):
        # La clé nomme une entrée de JSONB et ne peut donc pas être un paramètre lié ;
        # elle vient du code du connecteur, jamais des données. La valeur, qui vient de
        # la configuration, est liée.
        conditions.append(f"additional_data->>'{cle}' = :m{rang}")
        params[f"m{rang}"] = str(valeur)
    return (" AND ".join(conditions), params)
