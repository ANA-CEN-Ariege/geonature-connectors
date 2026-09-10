"""Conventions des commandes du module.

Ce fichier existe pour qu'une harmonisation tienne dans le temps. Les commandes ont
divergé une première fois sans que personne ne le voie : options en anglais d'un côté et
en français de l'autre, deux noms pour lister les mêmes découpages géographiques, un
préfixe abrégé sur une source et complet sur les deux autres. Rien de tout cela ne casse
un test classique — c'est de la cohérence, pas de la correction.

Le fichier est lu par analyse statique : `commands.py` importe GeoNature, indisponible
hors instance.

    pytest tests/test_commandes.py -q
"""

import ast
import re
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

SOURCE = (RACINE / "gn_module_connectors/commands.py").read_text(encoding="utf-8")
ARBRE = ast.parse(SOURCE)

# Les deux seules options qui restent en anglais : ce sont des conventions que tout
# utilisateur de ligne de commande reconnaît, et les traduire nuirait plus qu'autre chose.
UNIVERSELLES = {"--dry-run", "--yes"}

# Sources connues. Une commande porte le nom complet de sa source, jamais une
# abréviation : `vn-` a longtemps côtoyé `gbif-` et `dbchiro-` sans raison.
SOURCES = ("gbif", "visionature", "dbchiro", "geonature")

# Toute option du module. Ajouter un drapeau ici est un geste délibéré : c'est le moment
# de vérifier qu'il est en français et qu'il ne redouble pas un nom déjà employé pour le
# même concept ailleurs.
DRAPEAUX_ADMIS = UNIVERSELLES | {
    # communes à plusieurs sources
    "--lot", "--taxon", "--tout", "--supprimer-jdd-vides", "--incertitude-max",
    "--max-resultats", "--perimetre", "--jeu", "--depuis", "--licence", "--pays",
    # GBIF
    "--max-jeux", "--forcer", "--doi", "--garder-specimens", "--ignorer-exclusions",
    "--garder-incertitude-inconnue/--ecarter-incertitude-inconnue",
    "--ecarter-jeux-maille/--garder-jeux-maille",
    "--repli-taxref/--sans-repli-taxref",
    # VisioNature
    "--groupe-taxo", "--projet", "--jours", "--fin", "--trace",
    "--via-recherche/--sans-recherche",
    # dbChiro
    "--departement", "--importer-absences/--ecarter-absences", "--nom",
    # GeoNature distant
    "--export", "--plafond",
}


def commandes():
    """(nom de commande, nom de fonction, drapeaux, dests, paramètres) pour chacune."""
    trouvees = []
    for noeud in ARBRE.body:
        if not isinstance(noeud, ast.FunctionDef):
            continue
        nom_cmd, drapeaux, dests = None, [], []
        for deco in noeud.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            attr = getattr(deco.func, "attr", "")
            chaines = [a.value for a in deco.args if isinstance(a, ast.Constant)]
            if attr == "command" and chaines:
                nom_cmd = chaines[0]
            elif attr == "option" and chaines:
                drapeau = chaines[0]
                drapeaux.append(drapeau)
                explicite = [c for c in chaines[1:] if not c.startswith("-")]
                dests.append(explicite[0] if explicite
                             else drapeau.split("/")[0].lstrip("-").replace("-", "_"))
        if nom_cmd:
            trouvees.append((nom_cmd, noeud.name, drapeaux, dests,
                             [a.arg for a in noeud.args.args]))
    return trouvees


TOUTES = commandes()


def test_il_y_a_bien_des_commandes():
    """Garde-fou du garde-fou : une extraction cassée rendrait tous les autres verts."""
    assert len(TOUTES) >= 15


@pytest.mark.parametrize("nom, fonction, drapeaux, dests, params", TOUTES,
                         ids=[c[0] for c in TOUTES])
def test_chaque_option_correspond_a_un_parametre(nom, fonction, drapeaux, dests, params):
    """Le défaut qu'un renommage introduit le plus facilement.

    Renommer `--batch-size` en `--lot` sans fixer le nom Python fait passer le paramètre
    de `batch_size` à `lot` : la fonction reçoit un argument inattendu et lève un
    TypeError — à l'exécution seulement, jamais à l'import.
    """
    assert sorted(dests) == sorted(params), f"{nom} : options {dests} / params {params}"


@pytest.mark.parametrize("nom, fonction, drapeaux, dests, params", TOUTES,
                         ids=[c[0] for c in TOUTES])
def test_le_nom_de_commande_porte_sa_source_en_entier(nom, fonction, drapeaux, dests,
                                                      params):
    assert nom == "statut" or nom.split("-")[0] in SOURCES, (
        f"« {nom} » : préfixer par {', '.join(SOURCES)}, sans abréviation.")


@pytest.mark.parametrize("nom, fonction, drapeaux, dests, params", TOUTES,
                         ids=[c[0] for c in TOUTES])
def test_les_options_sont_declarees(nom, fonction, drapeaux, dests, params):
    inconnus = [d for d in drapeaux if d not in DRAPEAUX_ADMIS]
    assert not inconnus, (
        f"{nom} : {inconnus} absent(s) de DRAPEAUX_ADMIS. Si c'est un nouveau drapeau, "
        f"vérifiez qu'il est en français et qu'aucune autre commande ne nomme déjà ce "
        f"concept autrement, puis ajoutez-le à la liste.")


def test_aucune_option_en_anglais_hors_conventions():
    """Le mélange d'origine : --drop-empty-datasets à côté d'une aide en français."""
    # Comparaison segment par segment, et non par sous-chaîne : « --forcer » contient
    # « force » sans être anglais pour autant.
    # Les termes réellement fautifs, ceux qui étaient en place avant l'harmonisation.
    # « doi » (un acronyme), « exclusions », « specimens » et « taxo » n'en sont pas :
    # ils s'écrivent pareil ou presque dans les deux langues.
    anglais = {"batch", "size", "dataset", "datasets", "key", "drop", "keep", "skip",
               "gridded", "since", "force", "license", "country", "area", "limit",
               "debug", "results", "uncertainty", "unknown", "empty", "fallback",
               "download", "ignore", "group"}
    fautifs = []
    for nom, _, drapeaux, _, _ in TOUTES:
        for drapeau in drapeaux:
            if drapeau in UNIVERSELLES:
                continue
            segments = set(re.split(r"[-/]+", drapeau)) - {""}
            if segments & anglais:
                fautifs.append(f"{nom} {drapeau} ({sorted(segments & anglais)})")
    assert not fautifs, fautifs


def test_les_purges_offrent_les_memes_garanties():
    """Elles ont divergé : l'une refusait de tout purger sans critère, l'autre non ;
    l'une diagnostiquait un taxon inconnu, l'autre non."""
    purges = {nom: set(drapeaux) for nom, _, drapeaux, _, _ in TOUTES
              if nom.endswith("-purge")}
    assert set(purges) == {f"{s}-purge" for s in SOURCES}, (
        f"une purge par source attendue, trouvé {sorted(purges)}")
    commun = {"--taxon", "--tout", "--supprimer-jdd-vides", "--yes"}
    for nom, drapeaux in purges.items():
        assert commun <= drapeaux, f"{nom} : manque {sorted(commun - drapeaux)}"


def test_les_suppressions_de_masse_simulent_par_defaut():
    """`geonature-reconcilier` supprime en masse sans s'appeler `-purge`.

    Le nom seul ne la range pas parmi les commandes destructrices, et rien dans les
    conventions ne l'y obligerait. Elle doit pourtant offrir la même garantie : `--yes`
    pour agir, et surtout **pas** de `--dry-run`, qui laisserait croire que l'exécution
    est le comportement par défaut.
    """
    destructrices = {nom: set(drapeaux) for nom, _, drapeaux, _, _ in TOUTES
                     if nom.endswith("-purge") or nom.endswith("-reconcilier")}
    assert "geonature-reconcilier" in destructrices
    for nom, drapeaux in destructrices.items():
        assert "--yes" in drapeaux, f"{nom} : une suppression de masse exige --yes"
        assert "--dry-run" not in drapeaux, (
            f"{nom} : simule par défaut, --dry-run n'aurait aucun sens")


def test_lister_les_perimetres_porte_le_meme_nom_partout():
    """`vn-territoires` et `dbchiro-zonages` faisaient la même chose sous deux noms,
    et servaient une option elle-même nommée différemment."""
    listeurs = {nom for nom, *_ in TOUTES if nom.endswith("-perimetres")}
    assert listeurs == {"visionature-perimetres", "dbchiro-perimetres"}
    for nom, _, drapeaux, _, _ in TOUTES:
        assert "--territoire" not in drapeaux and "--area" not in drapeaux


def test_les_imports_ecrivent_et_les_purges_simulent():
    """L'asymétrie est voulue : un import s'ajoute et se rejoue sans dommage, une purge
    détruit. Que la commande destructrice exige un geste explicite est une protection.
    Ce test dit que c'est un choix, pas un oubli."""
    for nom, _, drapeaux, _, _ in TOUTES:
        if nom.endswith("-import"):
            assert "--dry-run" in drapeaux, f"{nom} doit pouvoir simuler"
            assert "--yes" not in drapeaux, f"{nom} ne doit pas exiger --yes"
        if nom.endswith("-purge"):
            assert "--yes" in drapeaux, f"{nom} doit exiger --yes"
            assert "--dry-run" not in drapeaux, f"{nom} simule déjà par défaut"


def test_aucun_nom_de_commande_en_double():
    noms = [nom for nom, *_ in TOUTES]
    assert len(noms) == len(set(noms))


def test_toutes_les_commandes_sont_exposees():
    """Une commande écrite mais absente de `connectors_cli` n'existe pas pour l'utilisateur."""
    liste = SOURCE.split("connectors_cli = [")[1].split("]")[0]
    exposees = {m.strip().rstrip(",") for m in liste.replace("\n", " ").split()}
    exposees.discard("")
    manquantes = [f for _, f, *_ in TOUTES if f not in exposees]
    assert not manquantes, manquantes


def test_le_menage_des_jdd_vides_ne_demande_pas_de_critere_de_purge():
    """`--supprimer-jdd-vides` seul doit être accepté.

    Le cas se présente après un changement de découpage : les jeux de l'ancienne clé
    restent, vides, dans le module Métadonnées. Exiger en plus un critère de purge
    obligerait à supprimer des observations pour faire ce ménage.
    """
    garde = SOURCE[SOURCE.index("menage_seul = "):SOURCE.index("if menage_seul:")]
    assert "drop_empty_datasets and not" in garde, (
        "le ménage seul doit être reconnu avant le refus pour absence de critère")
    refus = SOURCE.index("Aucun critère")
    assert SOURCE.index("if menage_seul:") > refus, (
        "le refus doit précéder le traitement, pour que le ménage y échappe")


def test_le_cadre_dacquisition_nest_jamais_supprime():
    """Sa disparition casserait tout import ultérieur.

    Il est créé par la migration du module, et `get_acquisition_framework` lève sans
    lui — une migration Alembic ne se rejouant pas. Il porte de surcroît les métadonnées
    que l'exploitant a pu enrichir à la main dans le module Métadonnées.
    """
    interdits = ("supprimer_cadre", "DELETE FROM gn_meta.t_acquisition_frameworks",
                 "db.session.delete(af")
    presents = [motif for motif in interdits if motif in SOURCE]
    assert not presents, f"le cadre d'acquisition ne doit pas être supprimé : {presents}"
