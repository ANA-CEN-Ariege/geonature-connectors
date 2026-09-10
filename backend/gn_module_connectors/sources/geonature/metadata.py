"""Métadonnées des jeux de données et cadres d'acquisition venus d'une instance distante.

Ce connecteur est le seul à recréer localement les jeux et les cadres **sous les UUID du
producteur**. C'est ce que dit le SINP : un jeu de données garde son identité d'une
plateforme à l'autre, et deux exemplaires de la même donnée reçus par deux chemins restent
rapprochables.

Conséquence directe : le jeu peut déjà exister localement, créé par un autre canal — un
dépôt SINP, une saisie manuelle, un import antérieur. Y écrire nos observations est
correct, puisque c'est le même objet. Réécrire ses métadonnées ne l'est pas.
`decider_jdd` porte cet arbitrage, isolé de toute base pour rester testable.
"""


def decider_jdd(*, existe: bool, actif: bool = True, lignes_autres_sources: int = 0,
                cadre_local: int | None = None,
                cadre_vise: int | None = None) -> tuple[str, str]:
    """(action, motif) pour un jeu de données dont l'UUID vient du producteur.

    Actions possibles :

    - `creer`   — le jeu n'existe pas localement ;
    - `upsert`  — il existe et n'appartient qu'à nous : on rafraîchit ses métadonnées ;
    - `adopter` — il existe et porte des lignes d'ailleurs : on y écrit sans rien y
      changer. Le jeu peut avoir été enrichi à la main, et le nom que l'API nous donne
      n'est pas forcément meilleur que celui qui s'y trouve ;
    - `refuser` — il est désactivé. GeoNature masque un jeu inactif : y verser des
      observations les rendrait invisibles, ce qui est pire que de ne pas les importer.

    Le `motif` est destiné à l'exploitant. Il nomme ce qui a été constaté, pas ce qui a été
    fait — le bilan d'import dit déjà l'action.
    """
    if not existe:
        return ("creer", "")
    if not actif:
        return ("refuser",
                "jeu de données local désactivé : GeoNature le masque, et les "
                "observations qu'on y verserait seraient invisibles")
    if lignes_autres_sources > 0:
        motif = (f"jeu déjà alimenté par une autre source ({lignes_autres_sources} "
                 f"ligne(s)) : métadonnées locales conservées")
        if cadre_local is not None and cadre_vise is not None and cadre_local != cadre_vise:
            motif += (f" ; il est rattaché au cadre {cadre_local}, pas au cadre "
                      f"{cadre_vise} visé — rattachement local conservé")
        return ("adopter", motif)
    if cadre_local is not None and cadre_vise is not None and cadre_local != cadre_vise:
        return ("adopter",
                f"jeu rattaché au cadre {cadre_local} et non au cadre {cadre_vise} visé : "
                f"rattachement local conservé")
    return ("upsert", "")


def nom_jdd(jdd_nom: str, instance: str) -> str:
    """Nom du jeu local, qui doit dire de quelle instance il vient.

    Sans le site, deux instances publiant chacune un « Inventaire ZNIEFF » donneraient deux
    jeux locaux impossibles à distinguer dans une liste déroulante.
    """
    site = _site(instance)
    nom = str(jdd_nom or "").strip() or "Jeu de données sans nom"
    if site and site.lower() not in nom.lower():
        nom = f"{nom} ({site})"
    return nom[:255]


def _site(instance: str) -> str:
    return (str(instance or "")
            .replace("https://", "").replace("http://", "").rstrip("/"))


def description_jdd(item: dict, *, instance: str, id_export, licence: str = "",
                    licence_url: str = "") -> str:
    """Description du jeu local : d'où il vient, sous quelle licence, avec quelles limites.

    La licence est écrite ici parce que c'est là que l'exploitant la cherche — dans le
    module Métadonnées, sur la fiche du jeu. Elle est aussi consignée par observation dans
    `additional_data`, où elle survit à toute réédition de cette description.
    """
    site = _site(instance)
    lignes = [
        f"Observations moissonnées depuis l'instance GeoNature {site} "
        f"(export {id_export}) par le module CONNECTORS.",
        "",
        "Le jeu porte l'identifiant SINP que lui donne le producteur : c'est le même "
        "objet chez lui et ici, et non une copie sous un nouvel identifiant.",
    ]
    if licence:
        lignes += ["", f"Licence : {licence}" + (f" ({licence_url})" if licence_url else "")]
    ca = str(item.get("ca_nom") or "").strip()
    if ca:
        lignes += ["", f"Cadre d'acquisition d'origine : {ca}."]
    lignes += [
        "",
        "⚠ Les géométries non ponctuelles du producteur sont ramenées à leur centroïde : "
        "la Synthèse est alimentée en points par ce module. La nature de l'objet "
        "géographique d'origine est conservée dans additional_data.",
    ]
    return "\n".join(lignes)


def acteurs_jdd(jdd_acteurs: str) -> list[str]:
    """Noms d'organismes cités par la vue, dégagés de leur rôle entre parenthèses.

    La vue construit ce champ par `string_agg(concat(nom, ' (', rôle, ')'), ', ')`. On le
    défait pour tenter un rapprochement avec `utilisateurs.bib_organismes`.

    ⚠ Sert à **rapprocher**, jamais à créer. `core/datasets.resoudre_organisme` explique
    pourquoi : des noms tirés d'une API arrivent en autant de variantes qu'il y a de
    saisies — « LPO Occitanie », « LPO-Occitanie », « Ligue pour la Protection des Oiseaux
    Occitanie » — que plus personne ne saurait rapprocher ensuite. Ce qui ne résout pas est
    signalé à l'exploitant, qui déclarera l'organisme s'il le juge bon.
    """
    noms, vus = [], set()
    for bloc in str(jdd_acteurs or "").split(","):
        nom = bloc.strip()
        if "(" in nom:
            nom = nom[:nom.rindex("(")].strip()
        if nom and nom.lower() not in vus:
            vus.add(nom.lower())
            noms.append(nom)
    return noms
