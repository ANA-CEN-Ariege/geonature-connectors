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


class _FauxDB:
    """`db.session` est branché juste avant chaque test qui en a besoin : les tests de
    nomenclatures n'y touchent jamais (résolveur au cache pré-rempli), et le laisser à
    `None` par défaut fait échouer bruyamment tout accès non anticipé plutôt que de
    heurter une vraie base."""

    session = None


_env.db = _FauxDB()
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
from gn_module_connectors.core import synthese as S  # noqa: E402

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
    franchiraient. `insert_batch` déduplique désormais son propre lot par `unique_id_sinp`
    avant d'écrire (voir `test_insert_batch_deduplique_son_lot_par_unique_id_sinp`) ; ce
    `DISTINCT` reste néanmoins la seule garantie côté `t_validations` si `prevalider`
    était un jour appelée sur un lot non passé par `insert_batch`.
    """
    assert "SELECT DISTINCT u" in SQL


class _FauxResultat:
    def __init__(self, valeurs):
        self._valeurs = list(valeurs)

    def scalars(self):
        return iter(self._valeurs)


class _FauxSession:
    """Un doublon franchi jusqu'à l'INSERT ferait échouer PostgreSQL (`ON CONFLICT DO
    UPDATE command cannot affect row a second time`) : ce faux n'a donc besoin de savoir
    répondre qu'aux quatre requêtes d'`insert_batch` sans prévalidation (les deux verrous
    consultatifs, la lecture des lignes déjà présentes, puis l'INSERT) — le compte
    d'appels suffit à les distinguer, dans l'ordre où `insert_batch` les émet."""

    def __init__(self):
        self.appels = []

    def execute(self, requete, parametres=None):
        self.appels.append(parametres)
        if len(self.appels) <= 3:
            return _FauxResultat([])  # deux verrous consultatifs, puis rien de déjà présent
        return _FauxResultat(parametres["unique_id_sinp"])  # ce que l'INSERT écrit


def test_insert_batch_deduplique_son_lot_par_unique_id_sinp():
    """Un lot contenant deux fois le même `unique_id_sinp` ne doit plus faire échouer
    tout le statement UNNEST — et la première occurrence doit l'emporter, comme la
    déduplication déjà faite par `sources/geonature/api.py` avant d'atteindre
    `insert_batch`."""
    _env.db.session = _FauxSession()
    lignes = [
        {"unique_id_sinp": "11111111-1111-1111-1111-111111111111", "observers": "A",
         "id_source": 1},
        {"unique_id_sinp": "11111111-1111-1111-1111-111111111111", "observers": "B",
         "id_source": 1},
        {"unique_id_sinp": "22222222-2222-2222-2222-222222222222", "observers": "C",
         "id_source": 1},
    ]

    inserees, maj = S.insert_batch(lignes)

    assert (inserees, maj) == (2, 0)
    parametres_insert = _env.db.session.appels[3]
    assert parametres_insert["unique_id_sinp"] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    assert parametres_insert["observers"] == ["A", "C"], (
        "la première occurrence du doublon doit l'emporter, pas la dernière")


# ── Verrou consultatif contre les exécutions concurrentes ────────────────────

def test_insert_batch_pose_un_verrou_borne_a_la_source():
    """La clé du verrou doit porter `id_source` : deux sources différentes tournant en
    parallèle ne doivent pas se bloquer l'une l'autre, seules deux exécutions sur la
    MÊME source doivent se sérialiser."""
    _env.db.session = _FauxSession()
    lignes = [{"unique_id_sinp": "33333333-3333-3333-3333-333333333333",
               "observers": "D", "id_source": 42}]

    S.insert_batch(lignes)

    verrou = _env.db.session.appels[0]
    assert verrou == {"ns": S.VERROU_LOT_NAMESPACE, "id_source": 42}


def test_le_verrou_est_transactionnel_et_pose_avant_toute_lecture():
    """`pg_advisory_xact_lock`, pas `pg_advisory_lock` : il se libère tout seul à la fin
    de la transaction — succès, erreur, ou perte de connexion — sans verrou de session
    qu'il faudrait explicitement relâcher sur un chemin d'exception.

    Il doit aussi être posé avant la lecture des lignes déjà présentes (`deja`), qui fait
    elle-même partie de la section critique — voir la docstring d'`insert_batch`.
    """
    corps = SQL[SQL.index("def insert_batch("):]
    corps = corps[:corps.index("\n\ndef ")] if "\n\ndef " in corps else corps
    assert "pg_advisory_xact_lock(:ns, :id_source)" in corps
    assert corps.index("pg_advisory_xact_lock") < corps.index("gn_synthese.synthese")


# ── Second verrou : contre un AUTRE connecteur sur le même unique_id_sinp ────

def test_insert_batch_pose_aussi_un_verrou_par_unique_id_sinp():
    """Le verrou borné à `id_source` ne protège pas du cas où deux connecteurs
    DIFFÉRENTS (donc deux `id_source` différents) écrivent la même ligne : la clé de ce
    second verrou doit porter l'UUID, pas la source."""
    _env.db.session = _FauxSession()
    lignes = [{"unique_id_sinp": "44444444-4444-4444-4444-444444444444",
               "observers": "E", "id_source": 42}]

    S.insert_batch(lignes)

    verrou = _env.db.session.appels[1]
    assert verrou == {"ns": S.VERROU_CONFLIT_NAMESPACE,
                       "u": ["44444444-4444-4444-4444-444444444444"]}


def test_le_verrou_par_uuid_ignore_id_source():
    """Deux lots de sources différentes mais portant le même `unique_id_sinp` doivent
    demander exactement le même verrou : c'est ce qui les sérialise l'un contre l'autre,
    là où le premier verrou (borné à `id_source`) ne les distinguerait pas."""
    _env.db.session = _FauxSession()
    S.insert_batch([{"unique_id_sinp": "55555555-5555-5555-5555-555555555555",
                     "observers": "F", "id_source": 1}])
    verrou_source_1 = _env.db.session.appels[1]

    _env.db.session = _FauxSession()
    S.insert_batch([{"unique_id_sinp": "55555555-5555-5555-5555-555555555555",
                     "observers": "G", "id_source": 2}])
    verrou_source_2 = _env.db.session.appels[1]

    assert verrou_source_1 == verrou_source_2


def test_les_deux_verrous_sont_poses_avant_toute_lecture_de_synthese():
    corps = SQL[SQL.index("def insert_batch("):]
    corps = corps[:corps.index("\n\ndef ")] if "\n\ndef " in corps else corps
    assert "verrouiller_conflits_potentiels(lignes)" in corps
    assert (corps.index("verrouiller_conflits_potentiels")
            < corps.index("gn_synthese.synthese"))


def test_verrouiller_conflits_potentiels_utilise_hashtext_et_un_ordre_stable():
    """`hashtext` réduit l'UUID à l'entier attendu par `pg_advisory_xact_lock(int, int)` ;
    `ORDER BY` impose le même ordre d'acquisition à toute exécution concurrente, pour
    éviter l'interblocage entre deux lots qui ne partagent qu'une partie de leurs UUID."""
    corps = SQL[SQL.index("def verrouiller_conflits_potentiels("):]
    corps = corps[:corps.index("\n\ndef ")] if "\n\ndef " in corps else corps
    assert "pg_advisory_xact_lock(:ns, hashtext(u::uuid::text))" in corps
    assert "ORDER BY u::uuid" in corps


def test_verrouiller_conflits_potentiels_deduplique_les_uuid():
    """Deux lignes du même UUID ne doivent demander qu'un seul verrou : appeler
    `pg_advisory_xact_lock` deux fois pour la même clé, dans la même transaction, n'a
    rien à apporter."""
    _env.db.session = _FauxSession()
    lignes = [
        {"unique_id_sinp": "66666666-6666-6666-6666-666666666666", "id_source": 1},
        {"unique_id_sinp": "66666666-6666-6666-6666-666666666666", "id_source": 1},
    ]

    S.verrouiller_conflits_potentiels(lignes)

    assert _env.db.session.appels[0]["u"] == [
        "66666666-6666-6666-6666-666666666666"]


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
