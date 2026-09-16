"""Tests du connecteur GBIF : filtres purs, puis pagination réseau.

Avant cet ajout, toute la couche réseau/pagination d'`api.py` (build_filters, fetch,
fetch_par_tranches, les filtres locaux) n'avait aucun test, ni dédié ni indirect. On
couvre d'abord les fonctions pures — sans mock, elles ne demandent qu'à être appelées —
puis la pagination de `fetch()` / `fetch_par_tranches()` avec un remplaçant de `requests`,
même convention que tests/test_geonature.py (`monkeypatch.setattr(A, "requests", faux)`).

`existe_dans_taxref` (taxonomy.py) ne fait pas de réseau mais interroge la base : même
repli que tests/test_prevalidation.py pour importer le module hors instance GeoNature.

    pytest tests/test_gbif_api.py -q
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests as _requests_reel

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

from gn_module_connectors.sources.gbif import api as A  # noqa: E402
from gn_module_connectors.core import report as report_core  # noqa: E402

# `taxonomy` importe `geonature.utils.env` et `sqlalchemy`, indisponibles hors instance.
# Modules de remplacement minimalistes, posés seulement s'ils sont absents — même repli
# que tests/test_prevalidation.py — pour ne jamais masquer une vraie instance GeoNature.
for nom in ("geonature", "geonature.utils"):
    sys.modules.setdefault(nom, types.ModuleType(nom))
_env = types.ModuleType("geonature.utils.env")


class _FauxDB:
    session = None


_env.db = _FauxDB()
sys.modules.setdefault("geonature.utils.env", _env)
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    _sa = types.ModuleType("sqlalchemy")
    _sa.text = lambda requete: requete
    sys.modules["sqlalchemy"] = _sa

from gn_module_connectors.sources.gbif import taxonomy as TX  # noqa: E402


def cfg(**over):
    base = dict(country="FR", state_province="", has_coordinate=True,
                has_geospatial_issue=False, max_results=None, extra={})
    base.update(over)
    return SimpleNamespace(**base)


def fcfg(**over):
    base = dict(exclude_dataset_terms=[], exclude_dataset_keys=[],
                include_dataset_keys=[], include_observers=[],
                date_min="", date_max="", coordinate_uncertainty_max=None,
                keep_unknown_uncertainty=True, licenses=[], exclude_taxon_keys=set())
    base.update(over)
    return SimpleNamespace(**base)


def _occ(gbif_id, **kw):
    return {"gbifID": gbif_id, "scientificName": "Bufo bufo", **kw}


# ── build_filters ─────────────────────────────────────────────────────────────

def test_build_filters_base():
    f = A.build_filters(cfg())
    assert f == {"country": "FR", "hasCoordinate": True, "hasGeospatialIssue": False,
                 "limit": 300}


def test_build_filters_state_province_seulement_si_renseignee():
    assert "stateProvince" not in A.build_filters(cfg())
    assert A.build_filters(cfg(state_province="Ariège"))["stateProvince"] == "Ariège"


def test_build_filters_limit_plafonnee_a_300():
    assert A.build_filters(cfg(max_results=10_000))["limit"] == 300
    assert A.build_filters(cfg(max_results=50))["limit"] == 50


def test_build_filters_incertitude_reste_locale_par_defaut():
    """keep_unknown_uncertainty=True (défaut) : le filtre API écarterait aussi les
    occurrences sans incertitude déclarée, donc il n'est jamais poussé à GBIF."""
    f = A.build_filters(cfg(), fcfg(coordinate_uncertainty_max=1000))
    assert "coordinateUncertaintyInMeters" not in f


def test_build_filters_incertitude_poussee_si_les_inconnues_sont_ecartees():
    f = A.build_filters(cfg(), fcfg(coordinate_uncertainty_max=1000,
                                    keep_unknown_uncertainty=False))
    assert f["coordinateUncertaintyInMeters"] == "0,1000"


def test_build_filters_datasets_et_licences_en_listes():
    f = A.build_filters(cfg(), fcfg(include_dataset_keys=["a", "b"],
                                    licenses=["CC0_1_0", "CC_BY_4_0"]))
    assert f["datasetKey"] == ["a", "b"]
    assert f["license"] == ["CC0_1_0", "CC_BY_4_0"]


@pytest.mark.parametrize("date_min, date_max, attendu", [
    ("2020-01-01", "", "2020-01-01,*"),
    ("", "2021-12-31", "*,2021-12-31"),
    ("2020-01-01", "2021-12-31", "2020-01-01,2021-12-31"),
])
def test_build_filters_intervalle_de_dates(date_min, date_max, attendu):
    f = A.build_filters(cfg(), fcfg(date_min=date_min, date_max=date_max))
    assert f["eventDate"] == attendu


def test_build_filters_sans_dates_naucun_eventdate():
    assert "eventDate" not in A.build_filters(cfg(), fcfg())


def test_build_filters_extra_ecrase_les_valeurs_calculees():
    """`extra` est appliqué en dernier : c'est ce qui permet à `fetch_par_tranches` de
    poser `year=...` sans que `build_filters` ne l'écrase au tour suivant."""
    f = A.build_filters(cfg(extra={"country": "ES", "year": "2020"}))
    assert f["country"] == "ES"
    assert f["year"] == "2020"


# ── Filtres locaux ────────────────────────────────────────────────────────────

def test_filter_occurrences_exclut_par_terme_insensible_a_la_casse():
    occs = [_occ(1, datasetName="iNaturalist research-grade"),
            _occ(2, datasetName="Faune-Ariège")]
    garde = A.filter_occurrences(occs, ["inaturalist"])
    assert [o["gbifID"] for o in garde] == [2]


def test_filter_occurrences_sans_terme_ne_filtre_rien():
    occs = [_occ(1, datasetName="x")]
    assert A.filter_occurrences(occs, []) == occs


def test_filter_occurrences_journalise_les_rejets():
    rejects = report_core.Rejects()
    A.filter_occurrences([_occ(1, datasetName="iNaturalist")], ["inaturalist"], rejects)
    assert len(rejects) == 1
    assert rejects.rows[0]["reason"] == "dataset_excluded"


def test_filter_by_dataset_keys_exclut_les_cles_indesirables():
    occs = [_occ(1, datasetKey="a"), _occ(2, datasetKey="b")]
    garde = A.filter_by_dataset_keys(occs, ["a"])
    assert [o["gbifID"] for o in garde] == [2]


def test_filter_by_dataset_keys_sans_exclusion_ne_filtre_rien():
    occs = [_occ(1, datasetKey="a")]
    assert A.filter_by_dataset_keys(occs, []) == occs


def test_filter_by_dataset_keys_journalise_les_rejets():
    rejects = report_core.Rejects()
    A.filter_by_dataset_keys([_occ(1, datasetKey="a")], ["a"], rejects)
    assert rejects.rows[0]["reason"] == "dataset_excluded"


def test_filter_by_observers_ne_garde_que_les_observateurs_voulus():
    occs = [_occ(1, recordedBy="Jean Dupont"), _occ(2, recordedBy="Quelqu'un d'autre")]
    garde = A.filter_by_observers(occs, ["dupont"])
    assert [o["gbifID"] for o in garde] == [1]


def test_filter_by_observers_sans_filtre_garde_tout():
    occs = [_occ(1, recordedBy="Jean")]
    assert A.filter_by_observers(occs, []) == occs


def test_filter_by_observers_journalise_les_ecartees():
    rejects = report_core.Rejects()
    A.filter_by_observers([_occ(1, recordedBy="Autre")], ["dupont"], rejects)
    assert rejects.rows[0]["reason"] == "observer_excluded"


def test_filter_by_uncertainty_ecarte_au_dela_du_seuil():
    occs = [_occ(1, coordinateUncertaintyInMeters=50),
            _occ(2, coordinateUncertaintyInMeters=5000)]
    garde = A.filter_by_uncertainty(occs, 1000)
    assert [o["gbifID"] for o in garde] == [1]


def test_filter_by_uncertainty_garde_les_inconnues_par_defaut():
    """Un tiers du corpus ariégeois n'a pas d'incertitude déclarée : les écarter par
    défaut perdrait beaucoup de données par ailleurs exploitables."""
    occs = [_occ(1, coordinateUncertaintyInMeters=None)]
    assert A.filter_by_uncertainty(occs, 1000) == occs


def test_filter_by_uncertainty_peut_ecarter_les_inconnues_explicitement():
    occs = [_occ(1, coordinateUncertaintyInMeters=None)]
    assert A.filter_by_uncertainty(occs, 1000, keep_unknown=False) == []


def test_filter_by_uncertainty_sans_seuil_ne_filtre_rien():
    occs = [_occ(1, coordinateUncertaintyInMeters=9999)]
    assert A.filter_by_uncertainty(occs, None) == occs


def test_apply_local_filters_compose_les_filtres_locaux():
    occs = [
        _occ(1, datasetName="iNaturalist", datasetKey="ok", recordedBy="Dupont",
             coordinateUncertaintyInMeters=10, license="CC_BY_4_0"),
        _occ(2, datasetName="Faune-Ariège", datasetKey="exclu", recordedBy="Dupont",
             coordinateUncertaintyInMeters=10, license="CC_BY_4_0"),
        _occ(3, datasetName="Faune-Ariège", datasetKey="ok", recordedBy="Dupont",
             coordinateUncertaintyInMeters=10_000, license="CC_BY_4_0"),
        _occ(4, datasetName="Faune-Ariège", datasetKey="ok", recordedBy="Dupont",
             coordinateUncertaintyInMeters=10, license="CC_BY_4_0"),
    ]
    filtre = fcfg(exclude_dataset_terms=["inaturalist"], exclude_dataset_keys=["exclu"],
                  include_observers=["dupont"], coordinate_uncertainty_max=1000,
                  licenses=["CC_BY_4_0"])
    rejects = report_core.Rejects()
    garde = A.apply_local_filters(occs, filtre, rejects)
    assert [o["gbifID"] for o in garde] == [4]
    assert len(rejects) == 3


# ── Pagination réseau de fetch() ──────────────────────────────────────────────

class FauxReponseGBIF:
    def __init__(self, charge, status_code=200):
        self._charge = charge
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _requests_reel.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._charge


class FauxRequestsGBIF:
    """Remplace le module `requests` utilisé par `api._get` : sert les pages dans l'ordre
    des appels, et expose `.exceptions` pour que la reprise sur erreur réseau fonctionne
    normalement."""

    exceptions = _requests_reel.exceptions

    def __init__(self, pages):
        self.pages = list(pages)
        self.appels = []

    def get(self, url, params=None, timeout=None):
        self.appels.append(dict(params or {}))
        return FauxReponseGBIF(self.pages[len(self.appels) - 1])


def test_fetch_une_page_pleine_puis_une_page_courte_arrete_la_pagination(monkeypatch):
    faux = FauxRequestsGBIF([
        {"results": [1, 2, 3], "count": 4, "endOfRecords": False},
        {"results": [4], "count": 4, "endOfRecords": True},
    ])
    monkeypatch.setattr(A, "requests", faux)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    resultats = A.fetch(cfg())
    assert resultats == [1, 2, 3, 4]
    assert len(faux.appels) == 2
    assert faux.appels[1]["offset"] == faux.appels[0]["limit"]


def test_fetch_sarrete_a_max_results_avant_la_fin_du_corpus(monkeypatch):
    """`endOfRecords` reste faux : seule la borne locale doit interrompre la boucle."""
    faux = FauxRequestsGBIF([{"results": [1, 2, 3, 4, 5], "count": 1000,
                             "endOfRecords": False}])
    monkeypatch.setattr(A, "requests", faux)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    resultats = A.fetch(cfg(max_results=3))
    assert resultats == [1, 2, 3]
    assert len(faux.appels) == 1


def test_fetch_propage_lerreur_reseau_apres_epuisement_des_reprises(monkeypatch):
    """`_get` reprend sur erreur transitoire, mais ne doit jamais avaler l'échec au bout
    du compte : `fetch()` doit le laisser remonter tel quel à l'appelant."""
    class RequetesEnPanne:
        exceptions = _requests_reel.exceptions

        def __init__(self):
            self.appels = 0

        def get(self, url, params=None, timeout=None):
            self.appels += 1
            raise _requests_reel.exceptions.ConnectionError("panne réseau")

    faux = RequetesEnPanne()
    monkeypatch.setattr(A, "requests", faux)
    monkeypatch.setattr(A.time, "sleep", lambda *_a, **_k: None)
    with pytest.raises(_requests_reel.exceptions.ConnectionError):
        A.fetch(cfg())
    assert faux.appels == 5  # 1 essai + 4 reprises, valeurs par défaut de `_get`


# ── fetch_par_tranches() : délégation et découpage ────────────────────────────

def test_fetch_par_tranches_delegue_directement_sous_le_plafond(monkeypatch):
    appels = []
    monkeypatch.setattr(A, "count", lambda c, f=None: 42)
    monkeypatch.setattr(A, "fetch", lambda c, f=None: appels.append((c, f)) or ["occ"])
    resultat = A.fetch_par_tranches(cfg(), fcfg())
    assert resultat == ["occ"]
    assert len(appels) == 1


def test_fetch_par_tranches_decoupe_et_agrege_au_dela_du_plafond(monkeypatch):
    monkeypatch.setattr(A, "count", lambda c, f=None: A.OFFSET_LIMITE + 1)
    monkeypatch.setattr(A, "tranches",
                        lambda c, f, plafond=A.OFFSET_LIMITE: [{"year": "2020"},
                                                               {"year": "2021"}])
    lots = iter([["a", "b"], ["c"]])
    monkeypatch.setattr(A, "fetch", lambda c, f=None: next(lots))
    messages = []
    resultat = A.fetch_par_tranches(cfg(), fcfg(), journal=messages.append)
    assert resultat == ["a", "b", "c"]
    assert any("découpage" in m for m in messages)


def test_fetch_par_tranches_respecte_max_results_entre_deux_tranches(monkeypatch):
    monkeypatch.setattr(A, "count", lambda c, f=None: A.OFFSET_LIMITE + 1)
    monkeypatch.setattr(A, "tranches",
                        lambda c, f, plafond=A.OFFSET_LIMITE: [{"year": "2020"},
                                                               {"year": "2021"}])
    lots = iter([["a", "b", "c"], ["d", "e"]])
    monkeypatch.setattr(A, "fetch", lambda c, f=None: next(lots))
    resultat = A.fetch_par_tranches(cfg(max_results=2), fcfg())
    assert resultat == ["a", "b"]


# ── taxonomy.existe_dans_taxref ────────────────────────────────────────────────

class _FauxResultatSQL:
    def __init__(self, ligne):
        self._ligne = ligne

    def first(self):
        return self._ligne


class _FauxSessionTaxref:
    def __init__(self, ligne):
        self._ligne = ligne

    def execute(self, stmt, params=None):
        return _FauxResultatSQL(self._ligne)


def test_existe_dans_taxref_vrai_si_une_ligne_est_trouvee(monkeypatch):
    monkeypatch.setattr(TX.db, "session", _FauxSessionTaxref((1,)))
    assert TX.existe_dans_taxref(1234) is True


def test_existe_dans_taxref_faux_si_absent_du_referentiel(monkeypatch):
    monkeypatch.setattr(TX.db, "session", _FauxSessionTaxref(None))
    assert TX.existe_dans_taxref(999_999) is False
