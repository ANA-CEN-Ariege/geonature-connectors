"""Tests de la transformation GBIF -> Synthèse.

Sans dépendance à GeoNature ni à la base : ces fonctions sont volontairement pures, ce
qui permet de les éprouver seules. Les cas retenus sont ceux qui ont réellement mordu
pendant le développement, pas des cas d'école.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.gbif import transform, nomenclatures as nomen  # noqa: E402


# ── Dates ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("occ, attendu", [
    ({"eventDate": "2024-06-01T10:00:00"}, ("2024-06-01", "2024-06-01")),
    ({"eventDate": "2024-06-01"},          ("2024-06-01", "2024-06-01")),
    ({"eventDate": "2024-06"},             ("2024-06-01", "2024-06-30")),
    ({"eventDate": "2024-02"},             ("2024-02-01", "2024-02-29")),  # bissextile
    ({"eventDate": "2023-02"},             ("2023-02-01", "2023-02-28")),
    ({"eventDate": "2024-12"},             ("2024-12-01", "2024-12-31")),  # bascule d'année
    ({"eventDate": "2024"},                ("2024-01-01", "2024-12-31")),
    # Intervalle ISO : courant sur les spécimens de collection. Ne pas le gérer fait
    # échouer l'insertion (date_min est NOT NULL) — c'est ce qui fait perdre des lots
    # entiers dans api2GN.
    ({"eventDate": "2026-06-01T00:00Z/2026-06-30T00:00Z"}, ("2026-06-01", "2026-06-30")),
    ({"eventDate": "2020-05-10/2020-05-01"}, ("2020-05-01", "2020-05-10")),  # bornes inversées
    # Repli sur les champs éclatés quand eventDate manque.
    ({"eventDate": "", "year": 1998, "month": 7, "day": 4}, ("1998-07-04", "1998-07-04")),
    ({"year": 1998, "month": 7},           ("1998-07-01", "1998-07-31")),
    ({"year": 1856},                       ("1856-01-01", "1856-12-31")),
])
def test_dates_valides(occ, attendu):
    d_min, d_max = transform.parse_dates(occ)
    assert (str(d_min), str(d_max)) == attendu


@pytest.mark.parametrize("occ", [{"eventDate": "n'importe quoi"}, {}, {"eventDate": None}])
def test_dates_indeterminables(occ):
    assert transform.parse_dates(occ) is None


# ── Identifiant unique ───────────────────────────────────────────────────────

def test_uuid_reutilise_identifiant_dee():
    """Les données republiées par l'INPN portent leur identifiant permanent DEE."""
    occ = {"occurrenceID": "F4BBDF27-3B84-2F30-E053-0514A8C06E0C", "gbifID": "1"}
    assert transform.sinp_uuid(occ) == "f4bbdf27-3b84-2f30-e053-0514a8c06e0c"


def test_uuid_derive_de_occurrence_id_pas_du_gbif_id():
    """occurrenceID est l'identifiant stable au sens Darwin Core ; gbifID est interne.

    iNaturalist et eBird n'exposent aucun UUID : dériver du gbifID exposerait à des
    doublons si GBIF le réattribuait lors d'une republication.
    """
    base = {"occurrenceID": "https://www.inaturalist.org/observations/333015081",
            "datasetKey": "inat"}
    assert transform.sinp_uuid({**base, "gbifID": "999"}) == \
           transform.sinp_uuid({**base, "gbifID": "888"})


def test_uuid_distingue_deux_producteurs():
    """Deux producteurs peuvent employer le même identifiant local."""
    a = transform.sinp_uuid({"occurrenceID": "OBS42", "datasetKey": "A", "gbifID": "1"})
    b = transform.sinp_uuid({"occurrenceID": "OBS42", "datasetKey": "B", "gbifID": "2"})
    assert a != b


def test_uuid_deterministe_sans_occurrence_id():
    occ = {"gbifID": "5"}
    assert transform.sinp_uuid(occ) == transform.sinp_uuid(dict(occ))


# ── Empreinte de contenu ─────────────────────────────────────────────────────

def test_empreinte_change_avec_la_donnee():
    a = {"scientificName": "Bufo bufo", "decimalLatitude": 42.0}
    b = {"scientificName": "Bufo bufo", "decimalLatitude": 43.0}
    assert transform.empreinte(a) != transform.empreinte(b)


def test_empreinte_ignore_les_champs_non_importes():
    """Une réinterprétation GBIF d'un champ inexploité ne doit rien réécrire."""
    a = {"scientificName": "Bufo bufo", "lastInterpreted": "2026-01-01"}
    b = {"scientificName": "Bufo bufo", "lastInterpreted": "2026-09-01"}
    assert transform.empreinte(a) == transform.empreinte(b)


# ── Ligne de synthèse : effectif et incertitude déclarés à zéro ────────────────

class ResolverFactice:
    def id(self, mnemonique, cd):
        return f"{mnemonique}={cd}" if cd is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"


def test_effectif_et_incertitude_nuls_ne_sont_pas_des_absences():
    """`individualCount`/`coordinateUncertaintyInMeters` à 0 sont des valeurs déclarées
    par le producteur (ex. occurrence ABSENT, position jugée exacte) : elles doivent
    rester 0 en base, pas devenir NULL comme une donnée non renseignée."""
    occ = {"occurrenceID": "OBS0", "datasetKey": "test", "gbifID": "1",
           "decimalLongitude": 1.5, "decimalLatitude": 43.0,
           "eventDate": "2026-01-01",
           "individualCount": 0, "coordinateUncertaintyInMeters": 0}
    ligne = transform.to_row(occ, cd_nom=1, id_dataset=1, id_source=1, id_module=1,
                             srid=2154, resolver=ResolverFactice())
    assert ligne["count_min"] == 0 and ligne["count_max"] == 0
    assert ligne["precision"] == 0


def test_effectif_et_incertitude_absents_restent_null():
    occ = {"occurrenceID": "OBS1", "datasetKey": "test", "gbifID": "2",
           "decimalLongitude": 1.5, "decimalLatitude": 43.0,
           "eventDate": "2026-01-01"}
    ligne = transform.to_row(occ, cd_nom=1, id_dataset=1, id_source=1, id_module=1,
                             srid=2154, resolver=ResolverFactice())
    assert ligne["count_min"] is None and ligne["count_max"] is None
    assert ligne["precision"] is None
