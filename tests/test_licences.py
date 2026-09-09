"""Tests du traitement des licences GBIF.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.gbif import api  # noqa: E402


@pytest.mark.parametrize("valeur, attendu", [
    ("http://creativecommons.org/licenses/by-nc/4.0/legalcode", "CC_BY_NC_4_0"),
    ("http://creativecommons.org/licenses/by/4.0/legalcode", "CC_BY_4_0"),
    ("http://creativecommons.org/publicdomain/zero/1.0/legalcode", "CC0_1_0"),
    ("https://creativecommons.org/licenses/by-nc/4.0/", "CC_BY_NC_4_0"),
    ("CC_BY_4_0", "CC_BY_4_0"),
    ("cc_by_nc_4_0", "CC_BY_NC_4_0"),
    ("UNSPECIFIED", ""),
    (None, ""),
    ("", ""),
])
def test_normalisation(valeur, attendu):
    """L'API est asymétrique : le paramètre `license=` attend l'énumération, mais chaque
    enregistrement renvoie une URL. Un filtre naïf ne matcherait jamais.
    """
    assert api.normalize_license(valeur) == attendu


def test_by_nc_nest_pas_lu_comme_by():
    """« /licenses/by-nc/ » contient « /licenses/by » : l'ordre des marqueurs compte."""
    assert api.normalize_license(
        "http://creativecommons.org/licenses/by-nc/4.0/legalcode") == "CC_BY_NC_4_0"


def test_filtre_local_ecarte_les_licences_inconnues():
    """Sans base d'autorisation explicite, la donnée n'est pas rediffusable."""
    occ = [{"license": "http://creativecommons.org/licenses/by/4.0/legalcode"},
           {"license": "http://creativecommons.org/licenses/by-nc/4.0/legalcode"},
           {"license": None}]
    garde = api.filter_by_license(occ, list(api.LICENSES_COMMERCIAL_OK))
    assert len(garde) == 1


def test_filtre_vide_ne_filtre_rien():
    occ = [{"license": "CC_BY_NC_4_0"}]
    assert api.filter_by_license(occ, []) == occ


def test_provenance_porte_l_attribution():
    """Le Data user agreement impose de conserver l'identifiant de propriété avec chaque
    enregistrement rediffusé ; CC BY impose l'attribution nominative.
    """
    p = api.provenance({"gbifID": "1", "datasetKey": "d", "rightsHolder": "Untel",
                        "license": "CC_BY_4_0"}, download_doi="10.15468/dl.x")
    assert p["dataset_key"] == "d"
    assert p["rights_holder"] == "Untel"
    assert p["gbif_download_doi"] == "10.15468/dl.x"


def test_provenance_sans_doi_pour_l_api_search():
    """L'API `search` ne délivre aucun DOI : ne pas en inventer."""
    assert "gbif_download_doi" not in api.provenance({"gbifID": "1"})


# ── Exclusions taxonomiques ──────────────────────────────────────────────────

def _occ(nom, **cles):
    return {"gbifID": "1", "scientificName": nom, **cles}


def test_exclusion_dun_ordre_atteint_ses_especes():
    """Exclure Chiroptera (734) doit écarter une espèce identifiée au rang de l'espèce.

    L'occurrence ne porte l'ordre que dans `orderKey` : tester le seul `taxonKey` ne
    verrait rien.
    """
    chiro = _occ("Miniopterus schreibersii", taxonKey=2432509, orderKey=734,
                 classKey=359, kingdomKey=1)
    autre = _occ("Bufo bufo", taxonKey=2422832, orderKey=952, classKey=131, kingdomKey=1)
    garde = api.filter_by_taxa([chiro, autre], {734})
    assert [o["scientificName"] for o in garde] == ["Bufo bufo"]


def test_exclusion_selective_au_genre():
    """Le filtre ne doit pas écarter tout ce qui partage un rang supérieur."""
    a = _occ("Miniopterus schreibersii", genusKey=2432501, orderKey=734)
    b = _occ("Rhinolophus hipposideros", genusKey=2432605, orderKey=734)
    garde = api.filter_by_taxa([a, b], {2432501})
    assert [o["scientificName"] for o in garde] == ["Rhinolophus hipposideros"]


def test_sans_exclusion_rien_nest_ecarte():
    occ = [_occ("Bufo bufo", taxonKey=2422832)]
    assert api.filter_by_taxa(occ, set()) == occ
