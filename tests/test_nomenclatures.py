"""Tests du mapping GBIF -> nomenclatures SINP.

Les valeurs attendues sont des `cd_nomenclature`. Chaque cas correspond à une erreur
réelle évitée, documentée par sa définition en base.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.gbif import nomenclatures as N  # noqa: E402


def cd(occ, champ):
    return N.cd_nomenclatures(occ).get(champ)


# ── Le faux-ami Nymph / Nymphe ───────────────────────────────────────────────

def test_nymph_est_une_larve_pas_une_nymphe():
    """GBIF `Nymph` = immature des hémimétaboles (punaise, criquet, libellule).
    SINP « Nymphe » (cd 13) = « stade intermédiaire entre larve et imago, pendant lequel
    l'individu ne se nourrit pas » — la pupe des holométaboles. Stades opposés.
    """
    assert cd({"lifeStage": "Nymph"}, "STADE_VIE") == "6"     # Larve


def test_pupa_est_la_nymphe_sinp():
    assert cd({"lifeStage": "Pupa"}, "STADE_VIE") == "13"     # Nymphe


def test_seedling_nest_pas_une_graine():
    """La graine (cd 20) précède la germination ; une plantule la suit."""
    assert cd({"lifeStage": "Seedling"}, "STADE_VIE") == "18"  # Germination


# ── Phénologie contre stade de vie ───────────────────────────────────────────

@pytest.mark.parametrize("valeur, attendu", [("Flowering", "3"), ("Fruiting", "3"),
                                             ("Vegetative", "13")])
def test_phenologie_va_dans_statut_bio(valeur, attendu):
    occ = {"lifeStage": valeur}
    assert cd(occ, "STATUT_BIO") == attendu
    assert cd(occ, "STADE_VIE") is None  # et pas dans le stade de vie


# ── Origine du taxon contre état de l'individu ───────────────────────────────

def test_domestique_nest_pas_sauvage():
    """`establishmentMeans` décrit le TAXON, `degreeOfEstablishment` l'INDIVIDU.
    Les confondre étiquetterait « Sauvage » les chiens et bovins des pièges photo.
    """
    occ = {"establishmentMeans": "introduced", "degreeOfEstablishment": "captive"}
    assert cd(occ, "NATURALITE") == "2"    # Cultivé/élevé
    assert cd(occ, "STAT_BIOGEO") == "5"   # Introduit non établi (dont domestique)


def test_native_seul_ne_conclut_pas_sur_la_naturalite():
    """L'origine biogéographique ne dit rien de l'état de l'individu observé."""
    assert cd({"establishmentMeans": "native"}, "NATURALITE") is None
    assert cd({"establishmentMeans": "native"}, "STAT_BIOGEO") == "2"


def test_casse_du_vocabulaire_toleree():
    assert cd({"establishmentMeans": "NATIVE"}, "STAT_BIOGEO") == "2"


# ── Statut d'observation ─────────────────────────────────────────────────────

def test_absent_devient_non_observe():
    """STATUT_OBS n'a pas de valeur « Absent ». Sans ce mapping, le défaut de la
    colonne (« Présent ») transformerait les absences en présences fausses.
    """
    assert cd({"occurrenceStatus": "ABSENT"}, "STATUT_OBS") == "No"
    assert cd({"occurrenceStatus": "PRESENT"}, "STATUT_OBS") == "Pr"


# ── Origine de la donnée ─────────────────────────────────────────────────────

@pytest.mark.parametrize("basis, attendu", [
    ("HUMAN_OBSERVATION", "Te"), ("MACHINE_OBSERVATION", "Te"),
    ("PRESERVED_SPECIMEN", "Co"), ("MATERIAL_CITATION", "Li"),
    ("OCCURRENCE", "NSP"),  # DwC : « valeur ambiguë, type de ressource inconnu »
])
def test_basis_of_record_vers_statut_source(basis, attendu):
    assert cd({"basisOfRecord": basis}, "STATUT_SOURCE") == attendu


def test_methode_observation_reste_inconnue():
    """GBIF ne livre aucune technique d'observation : la déduire serait l'inventer."""
    assert cd({"basisOfRecord": "HUMAN_OBSERVATION"}, "METH_OBS") == "21"


# ── Dénombrement ─────────────────────────────────────────────────────────────

def test_effectif_renseigne_implique_denombrement_compte():
    assert cd({"individualCount": 12}, "OBJ_DENBR") == "IND"
    assert cd({"individualCount": 12}, "TYP_DENBR") == "Co"
    assert cd({"individualCount": None}, "OBJ_DENBR") is None


# ── Périmètre ────────────────────────────────────────────────────────────────

def test_collections_ex_situ_hors_perimetre():
    """Leurs coordonnées désignent la collection, pas un lieu d'observation."""
    assert "FOSSIL_SPECIMEN" in N.BASIS_OF_RECORD_EXCLUS
    assert "LIVING_SPECIMEN" in N.BASIS_OF_RECORD_EXCLUS


def test_sexe_other_sans_correspondance():
    """Ni « Neutre » ni « Autre » en SINP : mieux vaut le défaut qu'une invention."""
    assert N.SEX.get("Other") is None
