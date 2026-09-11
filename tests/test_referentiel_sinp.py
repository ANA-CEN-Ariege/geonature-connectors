"""Les correspondances des connecteurs visent-elles des valeurs qui existent ?

Les autres fichiers de test vérifient que la bonne correspondance est *choisie* —
`Nymph` vers Larve, `ABSENT` vers Non observé. Aucun ne vérifiait que le
`cd_nomenclature` d'arrivée **existe** : un code inventé ou tombé en désuétude donne un
`get_id_nomenclature` nul, donc le défaut de la colonne, sans le moindre signe. Le même
angle mort vaut pour les libellés du connecteur GeoNature, dont la fixture était écrite
de mémoire : « Vivant » y figurait là où le référentiel écrit « Observé vivant ».

Le référentiel est versionné dans `tests/data/referentiel_sinp.json`, extrait du SQL
d'installation de GeoNature (cf. la clé `_source`). Une instance peut l'enrichir — d'où
un test qui vérifie l'existence, jamais l'inverse.

    pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

from gn_module_connectors.sources.gbif import nomenclatures as gbif  # noqa: E402
from gn_module_connectors.sources.gbif import transform as gbif_tr  # noqa: E402
from gn_module_connectors.sources.dbchiro import nomenclatures as dbc  # noqa: E402
from gn_module_connectors.sources.dbchiro import transform as db_tr  # noqa: E402
from gn_module_connectors.sources.visionature import nomenclatures as vn  # noqa: E402
from gn_module_connectors.sources.visionature import confidentialite as vnc  # noqa: E402
from gn_module_connectors.sources.visionature import transform as vn_tr  # noqa: E402
from gn_module_connectors.sources.geonature import nomenclatures as gn  # noqa: E402

from test_insert_alignement import ITEM_GEONATURE  # noqa: E402

REFERENTIEL = json.loads(
    (Path(__file__).parent / "data/referentiel_sinp.json").read_text(encoding="utf-8")
)["types"]


def _codes(source: str, mnemonique: str, table: dict) -> list:
    """(source, mnémonique, clé d'origine, cd) pour chaque correspondance non nulle."""
    return [(source, mnemonique, cle, str(cd))
            for cle, cd in table.items() if cd is not None]


# Toutes les correspondances écrites en dur, avec le type qu'elles visent. Le tableau
# est explicite plutôt que déduit : c'est lui qui dit quel type chaque table alimente,
# et une erreur de rattachement est précisément ce qu'on cherche à empêcher.
CORRESPONDANCES = [
    *_codes("gbif", "METH_OBS", {k: v[0] for k, v in gbif.BASIS_OF_RECORD.items()}),
    *_codes("gbif", "ETA_BIO", {k: v[1] for k, v in gbif.BASIS_OF_RECORD.items()}),
    *_codes("gbif", "STATUT_SOURCE", {k: v[2] for k, v in gbif.BASIS_OF_RECORD.items()}),
    *_codes("gbif", "METH_OBS", {"défaut": gbif.BASIS_OF_RECORD_DEFAUT[0]}),
    *_codes("gbif", "ETA_BIO", {"défaut": gbif.BASIS_OF_RECORD_DEFAUT[1]}),
    *_codes("gbif", "STATUT_SOURCE", {"défaut": gbif.BASIS_OF_RECORD_DEFAUT[2]}),
    *_codes("gbif", "STATUT_OBS", gbif.OCCURRENCE_STATUS),
    *_codes("gbif", "STADE_VIE", gbif.LIFE_STAGE),
    *_codes("gbif", "STATUT_BIO", gbif.LIFE_STAGE_VERS_STATUT_BIO),
    *_codes("gbif", "SEXE", gbif.SEX),
    *_codes("gbif", "STAT_BIOGEO", gbif.ESTABLISHMENT_MEANS),
    *_codes("gbif", "STAT_BIOGEO", gbif.INTRODUIT_AFFINE_PAR_DEGRE),
    *_codes("gbif", "NATURALITE", gbif.DEGREE_OF_ESTABLISHMENT),
    *_codes("gbif", "NATURALITE", gbif.DEGREE_OF_ESTABLISHMENT_PLANTES),
    *_codes("visionature", "OCC_COMPORTEMENT",
            {str(k): v for k, v in vn.ATLAS_VERS_COMPORTEMENT.items()}),
    *_codes("visionature", "OCC_COMPORTEMENT", vn.COMPORTEMENT_VN),
    *_codes("visionature", "NAT_OBJ_GEO", vn.NAT_OBJ_GEO),
    *_codes("visionature", "STATUT_SOURCE", {"constante": vn.STATUT_SOURCE}),
    *_codes("visionature", "ETA_BIO", {"mort": vn.ETA_BIO_MORT,
                                       "vivant": vn.ETA_BIO_VIVANT,
                                       "absence": vn.ETA_BIO_ABSENCE}),
    *_codes("visionature", "PREUVE_EXIST", {"média": vn.PREUVE_AVEC_MEDIA,
                                            "sans média": vn.PREUVE_SANS_MEDIA}),
    *_codes("visionature", "NIV_PRECIS", {"masquée": vnc.NIV_PRECIS_MASQUEE}),
    *_codes("dbchiro", "METH_OBS", dbc.CONTACT_METH_OBS),
    *_codes("dbchiro", "ETA_BIO", dbc.CONTACT_ETA_BIO),
    *_codes("dbchiro", "STATUT_VALID", {"is_doubtful": dbc.VALID_DOUTEUX}),
    *_codes("dbchiro", "STATUT_OBS", {"présence": dbc.STATUT_OBS_PRESENT,
                                      "absence": dbc.STATUT_OBS_ABSENT}),
    *_codes("dbchiro", "STATUT_BIO", {"breed_colo": dbc.STATUT_BIO_REPRODUCTION}),
    *_codes("dbchiro", "NAT_OBJ_GEO", {"place": dbc.NAT_OBJ_GEO_STATIONNEL}),
    *_codes("dbchiro", "STATUT_SOURCE", {"constante": dbc.STATUT_SOURCE}),
    *_codes("dbchiro", "OBJ_DENBR", {"total_count": dbc.OBJ_DENBR_INDIVIDU}),
]


@pytest.mark.parametrize("source, mnemonique, cle, cd", CORRESPONDANCES,
                         ids=lambda v: str(v))
def test_chaque_correspondance_vise_une_valeur_existante(source, mnemonique, cle, cd):
    valeurs = REFERENTIEL[mnemonique]
    assert cd in valeurs, (
        f"{source} : {mnemonique} « {cle} » vise le cd_nomenclature {cd!r}, absent du "
        f"référentiel SINP. La colonne prendrait le défaut, en silence.")


def test_le_tableau_couvre_les_quatre_types_de_chaque_source():
    """Garde-fou du test précédent : un tableau vidé par mégarde passerait sans bruit."""
    par_source = {}
    for source, mnemonique, _, _ in CORRESPONDANCES:
        par_source.setdefault(source, set()).add(mnemonique)
    assert len(CORRESPONDANCES) > 100
    for source in ("gbif", "visionature", "dbchiro"):
        assert len(par_source[source]) >= 4, source


# ── Libellés du connecteur GeoNature ────────────────────────────────────────

@pytest.mark.parametrize("colonne, mnemonique", sorted(gn.COLONNES_VUE.items()))
def test_les_libelles_de_la_fixture_existent_dans_le_referentiel(colonne, mnemonique):
    """La vue livre des `label_default` : une fixture écrite de mémoire ne prouve rien.

    Le résolveur factice des tests accepte n'importe quel libellé ; seule une
    confrontation au référentiel dit si la valeur pourrait sortir d'une instance réelle.
    """
    libelle = str(ITEM_GEONATURE.get(colonne) or "").strip()
    assert libelle, f"la fixture ne renseigne pas {colonne}"
    connus = {v.strip().lower() for v in REFERENTIEL[mnemonique].values()}
    assert libelle.lower() in connus, (
        f"« {libelle} » n'est pas un label_default de {mnemonique} — la vue ne peut pas "
        f"le produire. Valeurs : {', '.join(sorted(REFERENTIEL[mnemonique].values()))}")


@pytest.mark.parametrize("colonne, mnemonique",
                         [("precision_diffusion", "NIV_PRECIS"),
                          ("niveau_sensibilite", "SENSIBILITE")])
def test_les_libelles_traites_a_part_existent_aussi(colonne, mnemonique):
    """`precision_diffusion` et `niveau_sensibilite` ne passent pas par COLONNES_VUE."""
    libelle = str(ITEM_GEONATURE.get(colonne) or "").strip()
    connus = {v.strip().lower() for v in REFERENTIEL[mnemonique].values()}
    assert libelle.lower() in connus, f"« {libelle} » n'est pas un label_default de {mnemonique}"


# ── Mnémoniques ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nom, module", [("gbif", gbif_tr), ("visionature", vn_tr),
                                         ("dbchiro", db_tr), ("geonature", gn)])
def test_les_mnemoniques_des_colonnes_existent(nom, module):
    """Un mnémonique mal orthographié rend un type vide, donc un défaut nul partout."""
    inconnus = set(module.COLONNES_NOMENCLATURE.values()) - set(REFERENTIEL)
    assert not inconnus, f"{nom} : {sorted(inconnus)}"
