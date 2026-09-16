"""Rattrapage a posteriori du consentement des observateurs.

Un observateur peut changer d'avis : demander l'anonymat après coup, ou l'inverse.
Ce changement ne se voit dans **aucune** empreinte de contenu, puisqu'il porte sur
l'observateur et non sur l'observation — le relevé n'a pas bougé. Un moissonnage
incrémental ne le rattrapera donc jamais, et le court-circuit sur la date de
modification du jeu l'ignorera aussi.

`gn_vn2synthese` traite le même problème par une fonction dédiée,
`fct_c_set_anonymous_status()`, qu'ils exécutent périodiquement. Même principe ici.

Le rapprochement se fait sur l'identifiant pseudonymisé conservé dans
`additional_data.observateur` : il est stable, présent sur toutes les lignes — y compris
celles dont le nom est publié — et ne suppose donc pas d'avoir gardé le nom réel.
"""

from sqlalchemy import text
from geonature.utils.env import db


def lignes_a_reevaluer(id_source: int) -> list[tuple[int, str, str, str]]:
    """(id_synthese, pseudonyme, observers actuel, motif enregistré)."""
    return [
        (r[0], r[1], r[2], r[3])
        for r in db.session.execute(
            text("""
                SELECT id_synthese,
                       additional_data->>'observateur',
                       observers,
                       additional_data->>'anonymat'
                FROM gn_synthese.synthese
                WHERE id_source = :s
                  AND additional_data ? 'observateur'
            """),
            {"s": id_source},
        ).all()
    ]


def appliquer(id_source: int, souhaits: dict[str, tuple[bool, str]],
              dry_run: bool = True) -> dict[str, int]:
    """Réaligne `observers` sur le consentement courant.

    `souhaits` associe un pseudonyme à `(anonymat_souhaité, nom_reel)`. Le nom réel est
    nécessaire pour le cas inverse — un observateur qui lève son anonymat — que la seule
    lecture de la base ne permettrait pas de traiter, le nom n'y étant plus.

    Ne réécrit **que** les lignes dont la valeur change : une mise à jour inutile
    déclencherait les triggers de la Synthèse et gonflerait le journal pour rien.

    Si l'anonymat est levé mais que le référentiel ne renvoie aucun nom réel, la ligne
    est laissée en l'état (comptée dans `bilan["nom_manquant"]`) plutôt que d'écraser
    `observers` par NULL, ce qui perdrait toute attribution sans rien restaurer.
    """
    bilan = {
        "vers_pseudonyme": 0, "vers_nom": 0, "inchangees": 0, "inconnues": 0,
        "nom_manquant": 0,
    }
    modifications: list[dict] = []

    for id_synthese, pseudo, actuel, _motif in lignes_a_reevaluer(id_source):
        if pseudo not in souhaits:
            bilan["inconnues"] += 1
            continue
        anonymat, nom_reel = souhaits[pseudo]
        if not anonymat and not nom_reel:
            # Anonymat levé, mais le référentiel VisioNature ne fournit aucun nom réel
            # pour cet observateur. Écrire `observers = NULL` effacerait à la fois le
            # pseudonyme traçable et le nom, sans rien restaurer : on laisse la ligne
            # telle quelle plutôt que de compter une réussite qui n'en est pas une.
            bilan["nom_manquant"] += 1
            continue
        voulu = f"obs-{pseudo[:12]}" if anonymat else nom_reel
        if voulu == actuel:
            bilan["inchangees"] += 1
            continue
        bilan["vers_pseudonyme" if anonymat else "vers_nom"] += 1
        modifications.append({
            "id": id_synthese, "obs": voulu,
            "motif": "anonymat demandé" if anonymat else "nom publié",
        })

    if modifications and not dry_run:
        db.session.execute(
            text("""
                UPDATE gn_synthese.synthese
                SET observers = :obs,
                    additional_data = jsonb_set(additional_data, '{anonymat}',
                                                to_jsonb(:motif::text))
                WHERE id_synthese = :id
            """),
            modifications,
        )
    return bilan
