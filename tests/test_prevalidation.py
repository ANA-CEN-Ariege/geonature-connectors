"""Pré-validation automatique : `[validation]` -> Synthèse et `gn_commons.t_validations`.

Ce fichier existe à cause d'un réglage qui n'a jamais rien fait. `[validation] status`
vaut « Probable » par défaut, et cette valeur était passée telle quelle à
`ref_nomenclatures.get_id_nomenclature`, qui attend un `cd_nomenclature` et non un
libellé. Retour NULL, repli silencieux sur le défaut de la colonne : toutes les données
importées ressortaient en « Non évalué », `enabled` ou pas. Les tests ne le voyaient pas
parce qu'ils écrivaient `statut_validation="2"`, le code — jamais ce que porte la
configuration.

Le référentiel factice reprend la forme réelle de `t_nomenclatures` (cd_nomenclature,
label_default). Le couple « 1 » / « Certain - très probable » est relevé dans le SQL de
production de la LPO (`09b_init_data_nomenclatures.sql`).

    pytest tests/test_prevalidation.py -q
"""

import sys
import types
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

# `core.nomenclatures` importe `geonature.utils.env` et `sqlalchemy`, indisponibles hors
# instance. Les modules de remplacement n'exposent qu'un `db` inutilisable : un test qui
# toucherait vraiment la base échouerait bruyamment au lieu de passer sur une base
# fantôme. Le cache de type est pré-rempli pour cette raison — voir `resolveur()`.
for nom in ("geonature", "geonature.utils"):
    sys.modules.setdefault(nom, types.ModuleType(nom))
_env = types.ModuleType("geonature.utils.env")
_env.db = None
sys.modules.setdefault("geonature.utils.env", _env)
# ⚠ Ne remplacer `sqlalchemy` que s'il est absent, et le vérifier par un import réel :
# `sys.modules.setdefault` seul vaudrait pour tout le processus pytest, et le jour où la
# bibliothèque serait installée, l'ordre de collecte des fichiers déciderait qui gagne.
try:
    import sqlalchemy  # noqa: F401
except ImportError:
    _sa = types.ModuleType("sqlalchemy")
    _sa.text = lambda requete: requete
    sys.modules["sqlalchemy"] = _sa

from gn_module_connectors.core import nomenclatures as N  # noqa: E402

STATUT_VALID = {
    "0": "Non évalué",
    "1": "Certain - très probable",
    "2": "Probable",
    "3": "Douteux",
}


def resolveur():
    """Résolveur réel, dont le cache de type est pré-rempli au lieu d'être interrogé."""
    r = N.Resolver()
    r._types["STATUT_VALID"] = N.TypeNomenclature(
        codes={cd: 1000 + int(cd) for cd in STATUT_VALID},
        libelles={lib.lower(): 1000 + int(cd) for cd, lib in STATUT_VALID.items()},
        cd_par_libelle={lib.lower(): cd for cd, lib in STATUT_VALID.items()},
        libelle_par_cd=dict(STATUT_VALID),
    )
    return r


# ── Le défaut qui rendait le réglage inopérant ───────────────────────────────

def test_le_libelle_de_configuration_est_traduit_en_code():
    """« Probable » est ce que l'interface affiche ; « 2 » est ce que la base attend."""
    assert resolveur().cd_souple("STATUT_VALID", "Probable") == "2"


def test_le_code_reste_un_code():
    """Le code prime : stable d'une instance à l'autre, là où le libellé dépend de la
    langue et de la version du référentiel."""
    assert resolveur().cd_souple("STATUT_VALID", "2") == "2"


def test_la_casse_et_les_espaces_ne_comptent_pas():
    assert resolveur().cd_souple("STATUT_VALID", "  probable ") == "2"


def test_un_libelle_inconnu_ne_devient_pas_un_code():
    """Sans quoi on retomberait sur le défaut de colonne sans le dire."""
    assert resolveur().cd_souple("STATUT_VALID", "Vraisemblable") is None


def test_les_accents_ne_sont_pas_replies():
    """Même règle que `id_souple` : rapprocher « Non evalué » et « Non évalué »
    fabriquerait des correspondances fausses au lieu d'en signaler l'absence."""
    assert resolveur().cd_souple("STATUT_VALID", "Non evalue") is None


# ── Lecture de `[validation]` ────────────────────────────────────────────────

def test_desactive_ne_prevalide_rien():
    assert N.prevalidation({"enabled": False, "status": "Probable"}, resolveur()) is None


def test_active_rend_le_code_et_lidentifiant():
    """Les deux formes du même statut : la chaîne de transformation travaille en
    `cd_nomenclature`, `gn_commons.t_validations` veut un `id_nomenclature`."""
    statut = N.prevalidation(
        {"enabled": True, "status": "Probable", "comment": "Import automatique"},
        resolveur())
    assert (statut.cd, statut.id_statut, statut.commentaire) == (
        "2", 1002, "Import automatique")
    assert statut.ecrites == 0, "aucune ligne d'historique avant la première écriture"


def test_une_valeur_introuvable_fait_echouer_limport():
    """Le cœur du défaut : activer la pré-validation est un geste explicite, son échec
    doit l'être aussi. Le message énumère ce qui était trouvable."""
    with pytest.raises(ValueError) as erreur:
        N.prevalidation({"enabled": True, "status": "Probablement"}, resolveur())
    assert "Probablement" in str(erreur.value)
    assert "Certain - très probable" in str(erreur.value)


def test_active_sans_statut_est_refuse():
    with pytest.raises(ValueError):
        N.prevalidation({"enabled": True, "status": ""}, resolveur())


# ── L'historique de validation ───────────────────────────────────────────────

SQL = (RACINE / "gn_module_connectors/core/synthese.py").read_text(encoding="utf-8")


def test_lhistorique_est_ecrit_une_seule_fois_par_observation():
    """Réécrire à chaque moissonnage annulerait la décision d'un validateur — le module
    Validation retient la validation la plus récente — et éteindrait le filtre
    « modifiée depuis sa validation », qui compare `meta_update_date` à `validation_date`.
    """
    assert "NOT EXISTS (SELECT 1 FROM gn_commons.t_validations v" in SQL


def test_lhistorique_se_declare_automatique():
    """`validation_auto` est ce sur quoi s'appuie le filtre « masquer les validations
    automatiques » du module Validation. Sans lui, un validateur ne peut pas écarter les
    données importées de sa file."""
    assert "validation_auto" in SQL
    assert "TRUE, :commentaire" in SQL


def test_lhistorique_nest_ecrit_que_pour_des_lignes_presentes_en_synthese():
    """Le trigger du cœur apparie sur `unique_id_sinp` : une validation orpheline ne
    remonterait nulle part et resterait dans la table sans objet."""
    assert "EXISTS (SELECT 1 FROM gn_synthese.synthese s WHERE s.unique_id_sinp = u)" in SQL


def test_le_lot_est_dedoublonne_avant_lecriture():
    """Deux fois le même identifiant dans un lot laisserait deux lignes d'historique.

    Le `NOT EXISTS` s'évalue contre l'état d'AVANT le statement : les deux occurrences le
    franchiraient. `insert_batch` fausse déjà son propre décompte dans ce cas
    (`inserees = len(lignes) - len(deja)`), mais un compte faux se rattrape — une
    observation portant deux validations automatiques concurrentes, non.
    """
    assert "SELECT DISTINCT u" in SQL


def test_lhistorique_partage_la_transaction_de_linsertion():
    """`tri_meta_dates_change_synthese` repose `meta_update_date = NOW()` à chaque UPDATE,
    y compris celui du trigger de validation. Si l'historique était écrit dans une autre
    transaction, `meta_update_date` dépasserait `validation_date` et toute observation
    paraîtrait « modifiée depuis sa validation » dès sa validation. `NOW()` rendant
    l'heure de la TRANSACTION, les deux colonnes reçoivent la même valeur — à condition
    que l'appel reste dans `insert_batch`, avant tout commit.
    """
    corps = SQL[SQL.index("def insert_batch("):]
    corps = corps[:corps.index("\n\ndef ")] if "\n\ndef " in corps else corps
    assert "prevalider(lignes, prevalidation)" in corps
    assert "commit" not in corps, (
        "insert_batch ne doit pas valider la transaction : l'historique doit partager "
        "celle de l'INSERT en Synthèse.")
