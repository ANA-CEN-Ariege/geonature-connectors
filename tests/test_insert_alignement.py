"""Alignement entre `core.synthese.INSERT_SQL` et les `to_row` de chaque source.

Ce fichier existe à cause d'une panne précise. `INSERT_SQL` est un statement unique
réutilisé pour tout un lot : si un seul paramètre lié manque dans les dictionnaires
passés à `executemany`, SQLAlchemy lève `A value is required for bind parameter` et
**l'insertion entière échoue**. Le connecteur VisioNature a été écrit avec huit colonnes
de nomenclature là où l'INSERT en porte quatorze : il n'a donc jamais pu écrire une seule
ligne, et aucun test ne le disait.

La symétrie compte autant : une clé produite par `to_row` mais absente de l'INSERT est
calculée puis jetée en silence — c'est ainsi que `id_nomenclature_behaviour`, que le
trigger de sensibilité de GeoNature consomme, se perdait.

L'INSERT est lu comme du texte plutôt qu'importé : `core.synthese` dépend de
`geonature.utils.env`, indisponible hors instance. Le test reste ainsi exécutable seul.

    pytest tests/ -q
"""

import re
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

from gn_module_connectors.sources.gbif import transform as gbif_tr  # noqa: E402
from gn_module_connectors.sources.visionature import transform as vn_tr  # noqa: E402


class ResolverFactice:
    """Résolveur de nomenclatures sans base : renvoie une valeur lisible et non nulle."""

    def id(self, mnemonique, cd):
        return f"{mnemonique}={cd}" if cd is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"


def _source_insert() -> str:
    return (RACINE / "gn_module_connectors/core/synthese.py").read_text(encoding="utf-8")


def parametres_lies() -> set[str]:
    """Paramètres `:nom` attendus par la clause VALUES de l'INSERT."""
    bloc = _source_insert().split("VALUES (")[1].split("ON CONFLICT")[0]
    return set(re.findall(r":([a-z_]+)", bloc))


def colonnes_insert() -> set[str]:
    """Colonnes énumérées par l'INSERT, hors géométries construites en SQL."""
    bloc = _source_insert().split("INSERT INTO gn_synthese.synthese (")[1].split(")")[0]
    return {c.strip().strip('"') for c in bloc.replace("\n", " ").split(",") if c.strip()}


OCCURRENCE_GBIF = {
    "gbifID": "1234567890",
    "occurrenceID": "urn:catalog:XYZ:42",
    "decimalLongitude": 1.9,
    "decimalLatitude": 42.8,
    "eventDate": "2024-06-01",
    "scientificName": "Bufo spinosus Daudin, 1803",
    "individualCount": 3,
    "coordinateUncertaintyInMeters": 50,
    "basisOfRecord": "HUMAN_OBSERVATION",
    "occurrenceStatus": "PRESENT",
    "recordedBy": "Untel",
    "datasetKey": "588cbe8c-346e-5b0c-82cc-e00fa35d8629",
}

RELEVE_VN = {
    "@id": "9001",
    "date": {"@ISO8601": "2024-06-01T00:00:00+02:00"},
    "species": {"@id": "94", "name": "Anas crecca"},
    "place": {"name": "Étang de Lers", "loc_precision": "100"},
}

OBSERVATION_VN = {
    "@id": "1", "@uid": "7", "name": "Untel",
    "coord_lat": "42.8", "coord_lon": "1.9",
    "count": "3", "estimation_code": "EXACT_VALUE",
    "atlas_code": {"@id": "3"}, "precision": "precise",
    "comment": "au bord de l'eau",
}


def ligne_gbif():
    return gbif_tr.to_row(OCCURRENCE_GBIF, cd_nom=252, id_dataset=1, id_source=1,
                          id_module=1, srid=2154, resolver=ResolverFactice())


def ligne_vn():
    return vn_tr.to_row(RELEVE_VN, OBSERVATION_VN, cd_nom=1958, id_dataset=1,
                        id_source=1, id_module=1, srid=2154,
                        resolver=ResolverFactice(), instance="faune-ariege.org",
                        index_anonymat={"7": False}, secret_pseudo="cle-de-test")


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn)])
def test_to_row_fournit_tous_les_parametres_lies(nom, fabrique):
    """Sans quoi le premier `insert_batch` échoue, et rien n'est importé."""
    manquants = parametres_lies() - set(fabrique())
    assert not manquants, f"{nom} : paramètres absents de to_row -> {sorted(manquants)}"


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn)])
def test_to_row_ne_produit_rien_dinutile(nom, fabrique):
    """Une clé que l'INSERT ne porte pas est un calcul jeté en silence."""
    inutiles = set(fabrique()) - parametres_lies()
    assert not inutiles, f"{nom} : clés calculées puis jetées -> {sorted(inutiles)}"


# `id_nomenclature_diffusion_level` est résolue à part, et non via le dictionnaire
# commun : `resolver.id(..., None)` retombe sur le défaut de la nomenclature, alors que
# l'absence de restriction doit rester NULL. Le test qui suit vérifie ce cas à part.
RESOLUES_A_PART = {"id_nomenclature_diffusion_level"}


def test_toutes_les_colonnes_de_nomenclature_sont_couvertes():
    """Le dictionnaire de chaque source doit couvrir les colonnes `id_nomenclature_*`."""
    attendues = {c for c in colonnes_insert()
                 if c.startswith("id_nomenclature_")} - RESOLUES_A_PART
    for nom, module in (("gbif", gbif_tr), ("visionature", vn_tr)):
        manquantes = attendues - set(module.COLONNES_NOMENCLATURE)
        assert not manquantes, f"{nom} : {sorted(manquantes)}"


def test_les_colonnes_insert_et_les_valeurs_se_correspondent():
    """Un décalage entre les deux listes décale silencieusement toutes les valeurs."""
    bloc = _source_insert().split("VALUES (")[1].split("ON CONFLICT")[0]
    valeurs = [v.strip() for v in re.split(r",(?![^()]*\))", bloc.strip().rstrip(")"))]
    assert len(valeurs) == len(colonnes_insert())


def test_diffusion_restreinte_pour_une_observation_masquee():
    """L'observation masquée entre en base, avec une diffusion restreinte."""
    ligne = vn_tr.to_row(RELEVE_VN, {**OBSERVATION_VN, "hidden": "1"}, cd_nom=1958,
                         id_dataset=1, id_source=1, id_module=1, srid=2154,
                         resolver=ResolverFactice(), instance="faune-ariege.org",
                         index_anonymat={"7": False}, secret_pseudo="cle-de-test")
    assert ligne["id_nomenclature_diffusion_level"] == "NIV_PRECIS=4"
    assert '"masquee_source": "oui"' in ligne["additional_data"]


def test_pas_de_niveau_de_diffusion_sans_masquage():
    """NULL et non le défaut : une valeur inventée figerait une restriction inexistante."""
    assert ligne_vn()["id_nomenclature_diffusion_level"] is None
    assert ligne_gbif()["id_nomenclature_diffusion_level"] is None


# ── Empreinte : la clause de réécriture doit connaître les deux sources ──────

def test_lempreinte_visionature_est_prise_en_compte():
    """`vn_empreinte` doit figurer dans le WHERE de l'ON CONFLICT.

    La clause ne comparait que `gbif_empreinte`. Sur des lignes VisioNature, les deux
    côtés valaient NULL : `IS DISTINCT FROM` était faux, `DO UPDATE` n'était jamais
    exécuté, et une coordonnée rectifiée ou un taxon réidentifié à la source restait
    périmé indéfiniment — sans que le bilan d'import ne signale quoi que ce soit.
    """
    # [-1] et non [1] : « ON CONFLICT » apparaît aussi dans les commentaires du
    # fichier, et la première occurrence n'est pas la clause.
    where = _source_insert().split("ON CONFLICT")[-1]
    assert "vn_empreinte" in where
    assert "gbif_empreinte" in where
