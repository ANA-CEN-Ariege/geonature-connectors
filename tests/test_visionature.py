"""Tests du connecteur VisioNature.

Sans dépendance à GeoNature ni à la base. Les cas retenus sont ceux où une erreur
passerait tous les contrôles — la clé étrangère satisfaite, seule la vraisemblance
en défaut.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.visionature import (  # noqa: E402
    nomenclatures as N,
    transform as T,
)


def obs(**champs):
    return {"@id": "1", "coord_lat": "42.8", "coord_lon": "1.9", **champs}


# ── Absences ─────────────────────────────────────────────────────────────────

def test_code_atlas_99_est_une_absence():
    """Repris de gn_vn2synthese : 99 = espèce recherchée, non trouvée.

    Sans ce cas, une absence constatée entrerait en Synthèse comme une présence.
    """
    assert N.cd_nomenclatures({}, obs(atlas_code={"@id": "99"}))["STATUT_OBS"] == "No"


def test_effectif_nul_exact_est_une_absence():
    assert N.cd_nomenclatures(
        {}, obs(count="0", estimation_code="EXACT_VALUE"))["STATUT_OBS"] == "No"


def test_effectif_nul_estime_ne_prouve_rien():
    """Un zéro non déclaré exact est une donnée incomplète, pas une absence."""
    assert N.cd_nomenclatures(
        {}, obs(count="0", estimation_code="ESTIMATION"))["STATUT_OBS"] == "Pr"


def test_code_99_nest_pas_une_reproduction_certaine():
    """99 dépasse le seuil numérique : l'exclure explicitement, sinon une absence
    vaudrait le plus haut degré de nidification."""
    assert N.cd_nomenclatures({}, obs(atlas_code={"@id": "99"}))["STATUT_BIO"] is None


# ── Codes atlas ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code, statut", [
    ("1", None),   # vu en période favorable : présence, pas indice de reproduction
    ("2", "3"),    # mâle chanteur
    ("17", "3"),   # nid occupé
])
def test_seuil_de_reproduction(code, statut):
    assert N.cd_nomenclatures({}, obs(atlas_code={"@id": code}))["STATUT_BIO"] == statut


def test_comportement_deduit_du_code_atlas():
    assert N.cd_nomenclatures({}, obs(atlas_code={"@id": "2"}))["OCC_COMPORTEMENT"] == "18"


def test_seuil_surchargeable():
    r = N.cd_nomenclatures({}, obs(atlas_code={"@id": "2"}), {"reproduction_min": 4})
    assert r["STATUT_BIO"] is None


def test_code_atlas_accepte_les_deux_formes():
    """Biolovision renvoie tantôt un dict, tantôt une valeur simple."""
    assert N.code_atlas(obs(atlas_code={"@id": "3"})) == 3
    assert N.code_atlas(obs(atlas_code="3")) == 3


# ── Dénombrement ─────────────────────────────────────────────────────────────

def test_estimation_nest_pas_un_comptage():
    """Confondre les deux fausserait toute analyse quantitative."""
    assert N.denombrement(obs(count="50", estimation_code="ESTIMATION")) == ("IND", "Es")
    assert N.denombrement(obs(count="3", estimation_code="EXACT_VALUE")) == ("IND", "Co")


def test_sans_effectif_pas_de_denombrement():
    assert N.denombrement(obs()) == (None, None)


# ── Rien n'est imposé sans source ────────────────────────────────────────────

def test_etat_biologique_non_impose():
    """VisioNature enregistre aussi de la mortalité : affirmer « observé vivant »
    sur toute donnée serait faux."""
    assert N.cd_nomenclatures({}, obs())["ETA_BIO"] is None


def test_methode_observation_non_imposee():
    assert N.cd_nomenclatures({}, obs())["METH_OBS"] is None


# ── Dépliage et identifiants ─────────────────────────────────────────────────

def test_depliage_une_ligne_par_observation():
    """Un relevé peut porter plusieurs saisies, distinctes par observateur et effectif."""
    s = {"@id": "1", "observers": [{"@id": "10"}, {"@id": "11"}]}
    assert len(T.deplier([s])) == 2


def test_uuid_distingue_les_instances():
    """Les identifiants VisioNature sont propres à chaque site : sans préfixe
    d'instance, deux observations sans rapport pourraient s'écraser."""
    s, o = {"@id": "1"}, {"@id": "10"}
    assert T.sinp_uuid(s, o, "faune-ariege") != T.sinp_uuid(s, o, "faune-occitanie")


def test_uuid_deterministe():
    s, o = {"@id": "1"}, {"@id": "10"}
    assert T.sinp_uuid(s, o, "x") == T.sinp_uuid(s, o, "x")


def test_empreinte_suit_leffectif():
    s = {"@id": "1"}
    assert T.empreinte(s, obs(count="2")) != T.empreinte(s, obs(count="3"))


def test_date_iso8601():
    assert str(T.parse_date({"date": {"@ISO8601": "2024-03-15"}})) == "2024-03-15"
    assert T.parse_date({}) is None


# ── Anonymisation ────────────────────────────────────────────────────────────

from gn_module_connectors.sources.visionature import confidentialite as C  # noqa: E402


def test_pseudonyme_stable_et_distinct():
    """Deux observations du même observateur doivent rester rapprochables."""
    assert C.pseudonyme("42", "secret") == C.pseudonyme("42", "secret")
    assert C.pseudonyme("42", "secret") != C.pseudonyme("43", "secret")


def test_pseudonyme_depend_de_la_cle():
    """Une clé publique rendrait les pseudonymes recalculables donc réidentifiables."""
    assert C.pseudonyme("42", "cle-a") != C.pseudonyme("42", "cle-b")


def test_pseudonymisation_refusee_sans_cle():
    with pytest.raises(ValueError, match="clé de pseudonymisation"):
        C.pseudonyme("42", "")


def test_consentement_individuel_respecte():
    """`anonymous` est une démarche positive de l'observateur : son absence n'exprime
    aucun souhait d'anonymat, et la paternité d'une observation a de la valeur."""
    index = C.index_anonymat([{"@id": "42", "anonymous": "0"},
                              {"@id": "43", "anonymous": "1"}])
    nom, _ = C.observateur({"@uid": "42", "name": "Jean Dupont"}, index, "s")
    assert nom == "Jean Dupont"
    pseudo, motif = C.observateur({"@uid": "43", "name": "Marie Martin"}, index, "s")
    assert "Martin" not in pseudo and motif == "anonymat demandé"


def test_observateur_inconnu_est_pseudonymise():
    """Ne pas connaître un observateur n'est pas la même chose que savoir qu'il accepte
    d'être nommé : publier par défaut ferait d'une panne de chargement du référentiel
    une divulgation."""
    nom, motif = C.observateur({"@uid": "999", "name": "Inconnu"}, {"42": False}, "s")
    assert "Inconnu" not in nom
    assert motif == "observateur inconnu du référentiel"


def test_anonymat_forcable():
    index = C.index_anonymat([{"@id": "42", "anonymous": "0"}])
    nom, _ = C.observateur({"@uid": "42", "name": "Jean"}, index, "s", forcer_anonymat=True)
    assert "Jean" not in nom


def test_index_anonymat_lit_les_chaines():
    index = C.index_anonymat([{"@id": "1", "anonymous": "1"}, {"@id": "2", "anonymous": "0"}])
    assert index == {"1": True, "2": False}


# ── Confidentialité à la source ──────────────────────────────────────────────

def test_observation_masquee_nest_pas_ecartee():
    """`hidden` protège l'espèce ou le site, il ne met pas la donnée au rebut.

    Nid de rapace, station d'orchidée, gîte à chiroptères : c'est la donnée à enjeu,
    celle que l'accès à l'API est censé apporter. Elle est importée, avec un niveau de
    diffusion restreint.
    """
    assert C.est_confidentielle({"hidden": "1"}) is None


def test_observation_masquee_est_reperee():
    """Le champ s'appelle `hidden`, et non `is_hidden`.

    Ce module a lu pendant un temps `is_hidden` et `export_excluded`, qui n'existent dans
    aucune réponse de l'API : le filtre annoncé au README ne voyait donc rien, et les
    tests d'alors reprenaient les mêmes noms inventés et passaient au vert.
    """
    assert C.est_masquee({"hidden": "1"}) is True
    assert C.est_masquee({"hidden": "0"}) is False
    assert C.est_masquee({}, {"hidden": "1"}) is True


def test_champs_inexistants_ne_masquent_rien():
    """Garde-fou contre le retour des noms inventés."""
    assert C.est_masquee({"is_hidden": "1", "export_excluded": "1"}) is False


def test_niveau_de_diffusion_restreint_si_masquee():
    assert C.niveau_diffusion({"hidden": "1"}) == C.NIV_PRECIS_MASQUEE
    assert C.niveau_diffusion({"hidden": "1"}, code_masquee="2") == "2"


def test_niveau_de_diffusion_nul_par_defaut():
    """NULL signifie « le producteur ne se prononce pas ».

    GeoNature a retiré le DEFAULT de `id_nomenclature_diffusion_level` et ne la calcule
    plus : y inscrire une valeur sans que la source l'exprime serait une affirmation.
    """
    assert C.niveau_diffusion({"hidden": "0"}) is None
    assert C.niveau_diffusion({}) is None


def test_refus_du_moderateur_ecarte_lobservation():
    assert C.est_confidentielle({"admin_hidden_type": "refused"}) is not None


@pytest.mark.parametrize("motif", ["incomplete", "question"])
def test_verification_en_cours_nest_pas_un_refus(motif):
    """`incomplete` et `question` signalent une vérification, pas un rejet : écarter ces
    observations amputerait l'import de tout ce qu'un modérateur a simplement ouvert."""
    assert C.est_confidentielle({"admin_hidden": "1", "admin_hidden_type": motif}) is None


def test_refus_lu_aussi_sur_le_releve():
    assert C.est_confidentielle({}, {"admin_hidden_type": "refused"}) is not None


def test_booleens_en_chaines():
    """L'API renvoie « 1 »/« 0 », pas des booléens : comparer à True échouerait."""
    assert C.est_masquee({"hidden": "1"}) is True
    assert C.est_masquee({}) is False


# ── Donnée rapportée par un tiers ────────────────────────────────────────────

def test_second_hand_nattribue_pas_lobservation():
    """Le nom porté par une saisie `second_hand` est celui du saisisseur.

    L'écrire dans `observers` désignerait comme observateur quelqu'un qui ne l'est pas.
    `gn_vn2synthese` met le champ à NULL ; on fait de même.
    """
    valeur, motif = C.observateur(
        {"@uid": "7", "name": "Untel", "second_hand": "1"}, {"7": False}, "cle")
    assert valeur is None
    assert "tiers" in motif


def test_sans_second_hand_le_nom_est_publie():
    valeur, _ = C.observateur({"@uid": "7", "name": "Untel"}, {"7": False}, "cle")
    assert valeur == "Untel"


def test_commentaire_prive_non_repris():
    o = {"comment": "public", "private_comment": "confidentiel"}
    assert C.nettoyer_commentaire(o) == "public"


# ── Codes projet ─────────────────────────────────────────────────────────────

def test_code_projet_deux_formes():
    assert T.code_projet({"project_code": "ATLAS09"}) == "ATLAS09"
    assert T.code_projet({"project_code": {"@id": "ATLAS09"}}) == "ATLAS09"
    assert T.code_projet({}) is None


# ── Fenêtre du différentiel ──────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from gn_module_connectors.sources.visionature import api as A  # noqa: E402


def test_diff_refuse_au_dela_de_dix_semaines():
    """L'API ne couvre que 10 semaines. Au-delà, un incrémental perdrait en silence
    les créations et suppressions de l'intervalle."""
    vieux = (datetime.now(timezone.utc) - timedelta(weeks=12)).isoformat()
    assert A.diff_possible(vieux) is False


def test_diff_accepte_une_date_recente():
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert A.diff_possible(recent) is True


def test_diff_refuse_une_date_illisible():
    assert A.diff_possible("pas une date") is False


def test_diff_tolere_une_date_sans_fuseau():
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    assert A.diff_possible(recent) is True
