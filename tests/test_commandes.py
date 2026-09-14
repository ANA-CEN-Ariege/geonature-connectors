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
    "--groupe-taxo", "--projet", "--jours", "--debut", "--fin", "--trace",
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


def _fonction(nom):
    return next(n for n in ARBRE.body
                if isinstance(n, ast.FunctionDef) and n.name == nom)


def test_rattraper_un_historique_ne_passe_pas_par_lincremental():
    """`--depuis` et `--debut` sont deux gestes, pas deux orthographes du même.

    `--depuis` synchronise : recherche sur la date de SAISIE, suppressions comprises,
    et dix semaines au plus — c'est la fenêtre que couvre `api_diff`. `--debut` rattrape
    un historique : date d'OBSERVATION, sans limite d'ancienneté, sans suppressions.

    Le README a recommandé pendant un temps `--depuis "${an}-01-01" --fin …` pour
    partitionner vingt ans par année. Chaque itération se heurtait au plafond de dix
    semaines : la boucle entière ne moissonnait rien. D'où ce test — la limite doit
    porter sur le mode qui en dépend, et sur lui seul.
    """
    fonction = _fonction("visionature_import")
    params = {a.arg for a in fonction.args.args}
    assert {"since", "debut", "fin"} <= params

    gardes = [n for n in ast.walk(fonction)
              if isinstance(n, ast.If)
              and any(getattr(getattr(a, "func", None), "attr", "") == "diff_possible"
                      for a in ast.walk(n.test))]
    assert len(gardes) == 1, "un seul contrôle des dix semaines attendu"
    noms = {n.id for n in ast.walk(gardes[0].test) if isinstance(n, ast.Name)}
    assert "since" in noms and "debut" not in noms, (
        f"le plafond de dix semaines ne concerne que l'incrémental, or il lit {noms}")


def test_les_deux_modes_de_moissonnage_sexcluent():
    """Les combiner donnerait un moissonnage dont personne ne saurait dire ce qu'il a
    couvert : la date de saisie et la date d'observation ne délimitent pas le même
    ensemble."""
    fonction = _fonction("visionature_import")
    exclusions = [n for n in ast.walk(fonction)
                  if isinstance(n, ast.If) and isinstance(n.test, ast.BoolOp)
                  and isinstance(n.test.op, ast.And)
                  and {getattr(v, "id", "") for v in n.test.values} == {"since", "debut"}
                  and any(isinstance(c, ast.Raise) for c in n.body)]
    assert exclusions, "--depuis et --debut doivent se refuser mutuellement"


def test_toute_ecriture_en_synthese_porte_sa_prevalidation():
    """Cinq appels à `insert_batch`, un par chemin d'écriture.

    Le statut de validation est résolu une fois par commande, puis relayé jusqu'à
    l'écriture. Un appel qui l'oublierait n'échouerait pas : il écrirait simplement des
    observations sans historique de validation, invisibles dans le module Validation et
    impossibles à distinguer des données saisies sur place. C'est le genre d'oubli qu'un
    ajout de source réintroduit facilement.
    """
    appels = [n for n in ast.walk(ARBRE)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", "") == "insert_batch"]
    assert len(appels) >= 5, f"{len(appels)} appel(s) à insert_batch"
    sans = [a.lineno for a in appels if len(a.args) + len(a.keywords) < 2]
    assert not sans, (
        f"insert_batch sans pré-validation aux lignes {sans} : le second argument "
        f"est le statut résolu par `_prevalidation`.")


def test_les_jeux_du_connecteur_sortent_de_la_file_de_validation():
    """`upsert_dataset` reçoit `validable` partout, ou nulle part le réglage ne compte.

    Le module Validation ne liste que les jeux `validable = true`, défaut de GeoNature.
    Un connecteur qui omettrait le paramètre remonterait ses observations dans la file
    des validateurs, et le réglage `[validation] jdd_validable` ne serait vrai qu'à
    moitié — l'incohérence la plus difficile à voir depuis l'interface.
    """
    appels = [n for n in ast.walk(ARBRE)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", "") == "upsert_dataset"]
    assert len(appels) >= 5, f"{len(appels)} appel(s) à upsert_dataset"
    sans = [a.lineno for a in appels
            if not any(k.arg == "validable" for k in a.keywords)]
    assert not sans, f"upsert_dataset sans `validable` aux lignes {sans}"


def test_lenregistrement_de_la_source_est_commite_avant_la_moisson():
    """Un seul endroit écrit `url_source`, et il commite aussitôt.

    Sans ce commit, l'`UPDATE` garde un verrou de ligne sur `t_sources` pendant toute la
    suite de la commande — donc pendant la moisson, qui dure des heures sur un corpus
    entier. Deux exécutions du module ne peuvent alors plus se croiser : la seconde
    attend sur un verrou que rien ne nomme et paraît figée au démarrage. Constaté le
    14 septembre 2026 : trois commandes empilées derrière une moisson de 411 318
    observations, plus d'une heure d'attente, et `pg_stat_activity` pour seul moyen de
    comprendre.

    Le cas est statique faute de base : c'est le prix à payer, et il vaut mieux que rien.
    """
    ecritures = [ligne for ligne in SOURCE.splitlines()
                 if "UPDATE gn_synthese.t_sources" in ligne]
    assert len(ecritures) == 1, (
        f"{len(ecritures)} écritures de t_sources : elles doivent toutes passer par "
        f"`_enregistrer_url_source`, qui commite.")

    fonction = _fonction("_enregistrer_url_source")
    commits = [n for n in ast.walk(fonction)
               if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "commit"]
    assert commits, "`_enregistrer_url_source` doit commiter l'écriture qu'elle fait"


def test_la_commande_de_diagnostic_ne_prend_aucun_verrou():
    """`geonature-couverture` annonce « sans rien écrire » dès sa première ligne d'aide.

    Renseigner `url_source` est une écriture, si brève soit-elle — et c'est la commande
    qu'on lance en premier sur un export inconnu, celle où une attente sur un verrou
    serait la plus déroutante.
    """
    fonction = _fonction("geonature_couverture")
    appels = [n for n in ast.walk(fonction)
              if isinstance(n, ast.Call)
              and getattr(n.func, "id", "") == "_contexte_geonature"]
    assert appels, "geonature-couverture doit construire son contexte"
    for appel in appels:
        desactive = [k for k in appel.keywords
                     if k.arg == "enregistrer_url" and k.value.value is False]
        assert desactive, (
            f"ligne {appel.lineno} : _contexte_geonature doit être appelée avec "
            f"enregistrer_url=False depuis la commande de diagnostic")
