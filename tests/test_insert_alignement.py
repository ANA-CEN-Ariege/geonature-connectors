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
from gn_module_connectors.sources.dbchiro import transform as db_tr  # noqa: E402


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
    "date": {"@ISO8601": "2024-06-01T00:00:00+02:00", "@notime": "1"},
    "species": {"@id": "94", "name": "Anas crecca"},
    "place": {"name": "Étang de Lers", "loc_precision": "100"},
}

# Observation réelle telle qu'exportée par l'API Biolovision (export Faune-LR) :
# `uuid`, `timing`, `altitude` et `id_form_universal` y sont toujours présents, ce qui
# n'apparaissait dans aucun cas de test tant que le connecteur les jetait.
OBSERVATION_VN = {
    "@id": "1", "@uid": "7", "name": "Untel",
    "uuid": "d689b344-2255-41ef-b297-041008b9ed95",
    "coord_lat": "42.8", "coord_lon": "1.9",
    "count": "3", "estimation_code": "EXACT_VALUE",
    "atlas_code": {"@id": "3"}, "precision": "precise",
    "altitude": "365", "id_form_universal": "65_3477089",
    "timing": {"@timestamp": "1717491238", "@notime": "0", "@offset": "7200",
               "@ISO8601": "2024-06-04T10:53:58+02:00"},
    "comment": "au bord de l'eau",
}


FEATURE_DBCHIRO = {
    "id": 62639,
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [1.2106, 43.0310]},
    "properties": {
        "codesp": 76,
        "total_count": 1,
        "breed_colo": None,
        "period": "Estivage",
        "is_doubtful": False,
        "comment": None,
        "specie_data": {"codesp": "hypsav", "sci_name": "Hypsugo savii",
                        "common_name_fr": "Vespère de Savi", "sp_true": True},
        "creator": {"id": 4, "full_name": "Thomas CUYPERS", "label": "Thomas CUYPERS"},
        "session_data": {
            "id_session": 35059,
            "name": "loc15614 2026-07-29 du tcuypers",
            "contact": {"descr": "Contact acoustique", "code": "du"},
            "date_start": "2026-07-29",
            "place_data": {
                "id_place": 15614,
                "name": "Trou souffleur - trois frères",
                "areas": [
                    {"id": 109, "area_type": {"code": "dep", "name": "Département"},
                     "code": "09", "name": "Ariège"},
                    {"id": 1037, "area_type": {"code": "mun", "name": "Commune"},
                     "code": "09204", "name": "Montesquieu-Avantès"},
                ],
            },
            "main_observer": {"id": 4, "full_name": "Thomas CUYPERS",
                              "label": "Thomas CUYPERS"},
        },
    },
}


def ligne_dbchiro():
    return db_tr.to_row(FEATURE_DBCHIRO, cd_nom=60506, id_dataset=1, id_source=1,
                        id_module=1, srid=2154, resolver=ResolverFactice(),
                        instance="https://dbchiroc.org")


def ligne_gbif():
    return gbif_tr.to_row(OCCURRENCE_GBIF, cd_nom=252, id_dataset=1, id_source=1,
                          id_module=1, srid=2154, resolver=ResolverFactice())


def ligne_vn():
    return vn_tr.to_row(RELEVE_VN, OBSERVATION_VN, cd_nom=1958, id_dataset=1,
                        id_source=1, id_module=1, srid=2154,
                        resolver=ResolverFactice(), instance="faune-ariege.org",
                        index_anonymat={"7": False}, secret_pseudo="cle-de-test")


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn),
                                           ("dbchiro", ligne_dbchiro)])
def test_to_row_fournit_tous_les_parametres_lies(nom, fabrique):
    """Sans quoi le premier `insert_batch` échoue, et rien n'est importé."""
    manquants = parametres_lies() - set(fabrique())
    assert not manquants, f"{nom} : paramètres absents de to_row -> {sorted(manquants)}"


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn),
                                           ("dbchiro", ligne_dbchiro)])
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
    for nom, module in (("gbif", gbif_tr), ("visionature", vn_tr), ("dbchiro", db_tr)):
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


# ── Colonnes ajoutées pour VisioNature, mais portées par l'INSERT commun ─────
#
# `INSERT_SQL` est partagé entre GBIF et VisioNature. Toute colonne ajoutée pour l'un
# doit être alimentée par le `to_row` de l'autre, sinon `executemany` lève
# « A value is required for bind parameter » et **le lot entier** échoue. Les deux tests
# génériques ci-dessus le vérifient déjà ; ceux-ci nomment les colonnes en question,
# pour que leur disparition soit un échec explicite et non une régression silencieuse.

COLONNES_AJOUTEES = {
    "unique_id_sinp_grp",                 # regroupement par formulaire VisioNature
    "meta_v_taxref",                      # version du référentiel de résolution
    "altitude_min", "altitude_max",       # observers[0].altitude
    "digital_proof",                      # observers[0].medias
    "id_nomenclature_geo_object_nature",  # observers[0].precision
}


def test_les_colonnes_de_completude_sont_bien_dans_linsert():
    manquantes = COLONNES_AJOUTEES - colonnes_insert()
    assert not manquantes, sorted(manquantes)


# Colonnes que l'ON CONFLICT ne réécrit délibérément pas.
#
# `unique_id_sinp` est la clé du conflit. Les quatre autres décrivent le rattachement de
# la ligne, pas son contenu : les réécrire n'apporterait rien et `last_action` doit
# valoir « U », pas la valeur insérée.
HORS_MISE_A_JOUR = {"unique_id_sinp", "id_source", "id_module", "id_dataset",
                    "entity_source_pk_value", "last_action"}


def test_toute_colonne_inseree_est_aussi_mise_a_jour():
    """Une colonne présente à l'INSERT mais absente du SET de l'ON CONFLICT n'est jamais
    corrigée sur une ligne déjà en base : elle garde éternellement la valeur du premier
    import. C'est le piège dans lequel tombent les colonnes qu'on vient d'ajouter, et
    rien ne le signalerait — l'insertion réussit, la donnée reste périmée.
    """
    # Marqueur explicite : « ON CONFLICT » apparaît aussi dans les commentaires du
    # fichier, et la clause de réécriture est celle qui suit le DO UPDATE SET.
    bloc_set = (_source_insert().split("ON CONFLICT (unique_id_sinp) DO UPDATE SET")[1]
                .split("WHERE COALESCE")[0])
    mises_a_jour = set(re.findall(r"([a-z_]+) = EXCLUDED\.", bloc_set))
    # `"precision"` est un mot réservé, donc entre guillemets dans le SET.
    if '"precision" = EXCLUDED."precision"' in bloc_set:
        mises_a_jour.add("precision")
    attendues = colonnes_insert() - HORS_MISE_A_JOUR - {"the_geom_4326", "the_geom_point",
                                                        "the_geom_local"}
    # Les géométries sont bien mises à jour, mais sous une forme construite en SQL.
    for geom in ("the_geom_4326", "the_geom_point", "the_geom_local"):
        assert f"{geom} = EXCLUDED.{geom}" in bloc_set, geom
    manquantes = attendues - mises_a_jour
    assert not manquantes, f"jamais mises à jour -> {sorted(manquantes)}"


# ── Les champs VisioNature arrivent bien jusqu'à la ligne ────────────────────

def test_la_ligne_visionature_porte_les_champs_recuperes():
    """Chacun de ces champs était perdu à l'import, alors qu'il est renseigné sur la
    quasi-totalité des observations réelles."""
    ligne = ligne_vn()
    assert str(ligne["date_min"]) == "2024-06-01 10:53:58"   # et non minuit
    assert ligne["date_max"] == ligne["date_min"]
    assert ligne["altitude_min"] == ligne["altitude_max"] == 365
    assert ligne["unique_id_sinp"] == "d689b344-2255-41ef-b297-041008b9ed95"
    assert ligne["unique_id_sinp_grp"] is not None
    assert ligne["id_nomenclature_geo_object_nature"] == "NAT_OBJ_GEO=St"
    assert ligne["id_nomenclature_bio_condition"] == "ETA_BIO=2"
    assert ligne["id_nomenclature_exist_proof"] == "PREUVE_EXIST=2"


def test_luuid_calcule_supplante_est_conserve_pour_le_realignement():
    """Sans cette trace, les lignes déjà importées sous l'uuid5 seraient réinsérées à
    côté de leur nouvel identifiant : un doublon dans notre propre base, que rien ne
    signalerait. `core.synthese.realigner_uuid` lit cette clé pour renommer l'ancienne
    ligne avant l'insertion.
    """
    import json
    provenance = json.loads(ligne_vn()["additional_data"])
    assert provenance["vn_uuid_calcule"] == vn_tr.sinp_uuid(
        RELEVE_VN, OBSERVATION_VN, "faune-ariege.org")
    assert provenance["heure_connue"] == "oui"


def test_pas_de_trace_de_realignement_sans_uuid_natif():
    """La clé ne doit apparaître que lorsqu'un renommage est réellement nécessaire :
    la présence systématique ferait tourner le UPDATE de réalignement pour rien."""
    import json
    sans_uuid = {k: v for k, v in OBSERVATION_VN.items() if k != "uuid"}
    ligne = vn_tr.to_row(RELEVE_VN, sans_uuid, cd_nom=1958, id_dataset=1, id_source=1,
                         id_module=1, srid=2154, resolver=ResolverFactice(),
                         instance="faune-ariege.org", index_anonymat={"7": False},
                         secret_pseudo="cle-de-test")
    assert "vn_uuid_calcule" not in json.loads(ligne["additional_data"])


def test_la_ligne_gbif_alimente_aussi_les_nouvelles_colonnes():
    """GBIF n'a pas d'équivalent pour la plupart, mais doit fournir le paramètre lié :
    une clé absente fait échouer le lot entier, GBIF compris."""
    ligne = ligne_gbif()
    for colonne in COLONNES_AJOUTEES:
        assert colonne in ligne, colonne
    assert ligne["unique_id_sinp_grp"] is None
    assert ligne["digital_proof"] is None


def test_altitude_gbif_depuis_elevation():
    """`elevation` est le champ interprété ; `verbatimElevation` est du texte libre
    (« 1200-1400 m ») et reste ignoré."""
    ligne = gbif_tr.to_row({**OCCURRENCE_GBIF, "elevation": 1150.0}, cd_nom=252,
                           id_dataset=1, id_source=1, id_module=1, srid=2154,
                           resolver=ResolverFactice())
    assert ligne["altitude_min"] == ligne["altitude_max"] == 1150
    assert gbif_tr.altitude({"verbatimElevation": "1200-1400 m"}) is None
