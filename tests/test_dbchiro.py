"""Tests du connecteur dbChiro.

Sans dépendance à GeoNature ni à la base. Les cas retenus sont ceux où une erreur
passerait tous les contrôles : le cd_nom résout, la clé étrangère est satisfaite, et
seule la vraisemblance biologique est en défaut.

Les données de référence viennent d'un sondage réel de dbchiroc.org (8 039 observations,
9 septembre 2026), pas d'exemples inventés.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.dbchiro import (  # noqa: E402
    nomenclatures as N,
    taxonomy as T,
    transform as X,
)


class ResolverFactice:
    def id(self, mnemonique, cd):
        return f"{mnemonique}={cd}" if cd is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"


def props(codesp="nycnoc", sci_name="Nyctalus noctula", **champs):
    """Propriétés d'une feature, réduites à ce que le test manipule."""
    base = {
        "specie_data": {"codesp": codesp, "sci_name": sci_name, "sp_true": True},
        "session_data": {
            "id_session": 1,
            "contact": {"code": "vv", "descr": "Vu en visuel"},
            "date_start": "2026-07-29",
            "place_data": {"id_place": 1, "name": "Gîte",
                           "areas": [{"area_type": {"code": "dep"}, "code": "09"}]},
        },
    }
    base.update(champs)
    return base


def feature(**champs):
    return {"id": 1, "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1.21, 43.03]},
            "properties": props(**champs)}


# ── Absences ─────────────────────────────────────────────────────────────────
# 175 observations sur 8 039. Le même piège que `occurrenceStatus = ABSENT` du GBIF.

@pytest.mark.parametrize("code", ["0obs", "0du"])
def test_les_codes_dabsence_sont_ecartes_par_defaut(code):
    """Sans ce filtre, une prospection négative entre en Synthèse comme une présence."""
    cd_nom, motif = T.resolve(props(codesp=code))
    assert cd_nom is None
    assert motif == "absence"


@pytest.mark.parametrize("code", ["0obs", "0du"])
def test_les_absences_activees_portent_lordre_et_non_observe(code):
    cd_nom, motif = T.resolve(props(codesp=code), importer_absences=True)
    assert cd_nom == T.ORDRE_CHIROPTERA
    assert motif == ""
    assert N.cd_nomenclatures(props(codesp=code), absence=True)["STATUT_OBS"] == "No"


def test_une_absence_porte_un_effectif_nul_et_non_inconnu():
    """L'ambiguïté entre « aucun individu » et « effectif non renseigné » fausserait
    toute analyse quantitative."""
    ligne = X.to_row(feature(codesp="0obs", sci_name="Aucune chauve-souris ou trace"),
                     cd_nom=T.ORDRE_CHIROPTERA, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice(), absence=True)
    assert ligne["count_min"] == 0 and ligne["count_max"] == 0


# ── Déterminations partielles ────────────────────────────────────────────────
# TAXREF v16 ne propose aucun agrégat pour les chiroptères : ni rang AGES, ni entrée à
# barre oblique. Le repli au rang supérieur est la seule voie, et chaque cas est un
# arbitrage explicite.

@pytest.mark.parametrize("code, cd_nom, pourquoi", [
    ("murmur", T.GENRE_MYOTIS, "Myotis myotis / blythii : même genre"),
    ("pleind", T.GENRE_PLECOTUS, "Plecotus sp."),
    ("pipkuhnat", T.GENRE_PIPISTRELLUS, "P. kuhlii / nathusii"),
    ("eptvesnyc", T.FAMILLE_VESPERTILIONIDAE, "trois genres, une famille"),
    ("nyctad", T.ORDRE_CHIROPTERA, "Nyctalus (Vespertilionidae) / Tadarida (Molossidae)"),
    ("pippipminsch", T.ORDRE_CHIROPTERA, "Pipistrellus / Miniopterus : deux familles"),
    ("chiind", T.ORDRE_CHIROPTERA, "Chiroptera sp."),
])
def test_les_agregats_remontent_au_bon_rang(code, cd_nom, pourquoi):
    assert T.resolve(props(codesp=code))[0] == cd_nom, pourquoi


def test_un_couple_a_cheval_sur_deux_familles_ne_descend_pas_a_la_famille():
    """Le piège : « Nyctalus / Tadarida » ressemble à un couple de Vespertilionidae.
    Tadarida est un Molossidae — seul l'ordre les contient tous les deux."""
    assert T.resolve(props(codesp="nyctad"))[0] != T.FAMILLE_VESPERTILIONIDAE


def test_un_codesp_inconnu_est_rejete_et_non_verse_dans_lordre():
    """Une espèce nouvellement ajoutée au référentiel dbChiro doit se voir, pas se
    dissoudre silencieusement en « Chiroptera sp. »."""
    cd_nom, motif = T.resolve(props(codesp="myoznv"))
    assert cd_nom is None
    assert motif == "codesp_inconnu"


def test_nom_cite_conserve_la_determination_dorigine():
    """`cd_nom` dit « Myotis » ; seul `nom_cite` dit ce qui a été déterminé."""
    ligne = X.to_row(feature(codesp="murmur", sci_name="Myotis myotis / M. blythii"),
                     cd_nom=T.GENRE_MYOTIS, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice())
    assert ligne["nom_cite"] == "Myotis myotis / M. blythii"
    import json
    assert json.loads(ligne["additional_data"])["agregat"] == "oui"


def test_les_deux_codes_myotis_sp_donnent_le_meme_taxon():
    """`petmur` et `myotis` portent tous deux « Myotis sp. » côté dbChiro."""
    assert T.resolve(props(codesp="petmur"))[0] == T.resolve(props(codesp="myotis"))[0]


# ── Méthodes de contact ──────────────────────────────────────────────────────

def test_un_contact_acoustique_est_ultrasons_et_non_entendu():
    """METH_OBS distingue « Entendu » (1) d'« Ultrasons » (3) : un détecteur relève du
    second. C'est la moitié du corpus."""
    p = props(session_data={"contact": {"code": "du", "descr": "Contact acoustique"},
                            "date_start": "2026-07-29"})
    assert N.cd_nomenclatures(p)["METH_OBS"] == "3"


def test_un_cri_audible_reste_entendu():
    p = props(session_data={"contact": {"code": "cr", "descr": "Cri audible"},
                            "date_start": "2026-07-29"})
    assert N.cd_nomenclatures(p)["METH_OBS"] == "1"


def test_le_guano_ne_dit_ni_vivant_ni_mort():
    """Sur un indice indirect, l'animal n'a été observé ni vivant ni mort. Déclarer
    « Observé vivant » sur un tas de guano serait faux."""
    p = props(session_data={"contact": {"code": "gu", "descr": "Guano"},
                            "date_start": "2026-07-29"})
    valeurs = N.cd_nomenclatures(p)
    assert valeurs["METH_OBS"] == "6"      # Fèces/Guano/Epreintes
    assert valeurs["ETA_BIO"] is None


@pytest.mark.parametrize("code", ["ca", "ro"])
def test_un_cadavre_ou_des_restes_sont_trouves_morts(code):
    p = props(session_data={"contact": {"code": code, "descr": ""},
                            "date_start": "2026-07-29"})
    assert N.cd_nomenclatures(p)["ETA_BIO"] == "3"


def test_toutes_les_methodes_mesurees_sont_couvertes():
    """Les 9 codes relevés sur l'instance couvrent 100 % des observations : aucun ne
    doit retomber sur le défaut."""
    for code in ("vv", "du", "vm", "nc", "gu", "ca", "te", "cr", "ro"):
        assert N.CONTACT_METH_OBS.get(code) is not None, code


# ── Phénologie ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("periode", ["Estivage", "Estivant", "Hivernant", "Hibernant"])
def test_la_periode_nalimente_jamais_le_statut_biologique(periode):
    """Deux raisons, et la seconde est un contresens franc : `period` est calculé par
    dbChiro depuis la date, et « Estivage » n'est pas l'estivation du SINP — l'été est
    la saison d'activité et de mise bas des chiroptères, pas une dormance."""
    assert N.cd_nomenclatures(props(period=periode))["STATUT_BIO"] is None


def test_la_reproduction_vient_de_breed_colo_qui_est_une_observation():
    assert N.cd_nomenclatures(props(breed_colo=True))["STATUT_BIO"] == "3"
    assert N.cd_nomenclatures(props(breed_colo=None))["STATUT_BIO"] is None


def test_la_periode_reste_lisible_dans_additional_data():
    import json
    ligne = X.to_row(feature(period="Transit automnal"), cd_nom=60468, id_dataset=1,
                     id_source=1, id_module=1, srid=2154, resolver=ResolverFactice())
    assert json.loads(ligne["additional_data"])["periode"] == "Transit automnal"


# ── Validation ───────────────────────────────────────────────────────────────

def test_une_determination_douteuse_prime_sur_la_prevalidation():
    """Annoncer « Probable » sur une donnée que la source dit incertaine la
    surclasserait."""
    valeurs = N.cd_nomenclatures(props(is_doubtful=True), statut_validation="2")
    assert valeurs["STATUT_VALID"] == "3"      # Douteux


def test_la_prevalidation_sapplique_aux_donnees_non_douteuses():
    valeurs = N.cd_nomenclatures(props(is_doubtful=False), statut_validation="2")
    assert valeurs["STATUT_VALID"] == "2"


# ── Identifiants ─────────────────────────────────────────────────────────────

def test_lidentifiant_sinp_est_deterministe():
    assert (X.identifiant_sinp(feature(), "https://dbchiroc.org")
            == X.identifiant_sinp(feature(), "https://dbchiroc.org"))


def test_lidentifiant_sinp_depend_de_linstance():
    """`id_sighting` est un entier propre à chaque base : deux instances emploieraient
    les mêmes, et les observations s'écraseraient l'une l'autre."""
    assert (X.identifiant_sinp(feature(), "https://dbchiroc.org")
            != X.identifiant_sinp(feature(), "https://autre.dbchiro.org"))


def test_les_observations_dune_meme_session_partagent_le_groupe():
    a = props(); b = props(codesp="rhihip")
    assert (X.uuid_groupe(a, "x") == X.uuid_groupe(b, "x"))


# ── Périmètre ────────────────────────────────────────────────────────────────

def test_le_departement_est_lu_dans_les_zonages():
    assert X.departement(props()) == "09"


def test_hors_perimetre_quand_le_departement_ne_correspond_pas():
    p = props(session_data={"date_start": "2026-07-29", "contact": {"code": "vv"},
                            "place_data": {"areas": [{"area_type": {"code": "dep"},
                                                      "code": "11"}]}})
    assert X.dans_perimetre(p, {"09"}) is False


def test_un_departement_indeterminable_est_ecarte_quand_un_filtre_est_actif():
    """Le laisser passer ferait du filtre une passoire silencieuse."""
    p = props(session_data={"date_start": "2026-07-29", "contact": {"code": "vv"},
                            "place_data": {"areas": []}})
    assert X.dans_perimetre(p, {"09"}) is False
    assert X.dans_perimetre(p, set()) is True


# ── Observateurs ─────────────────────────────────────────────────────────────

def test_le_createur_et_lobservateur_principal_sont_dedoublonnes():
    p = props(creator={"id": 4, "full_name": "Thomas CUYPERS"},
              session_data={"date_start": "2026-07-29", "contact": {"code": "vv"},
                            "main_observer": {"id": 4, "full_name": "Thomas CUYPERS"}})
    assert X.observateurs(p) == "Thomas CUYPERS"


def test_deux_personnes_distinctes_sont_conservees():
    p = props(creator={"id": 4, "full_name": "Thomas CUYPERS"},
              session_data={"date_start": "2026-07-29", "contact": {"code": "vv"},
                            "main_observer": {"id": 9, "full_name": "Lilian HACQUIN"}})
    assert X.observateurs(p) == "Thomas CUYPERS, Lilian HACQUIN"


def test_la_pseudonymisation_refuse_de_travailler_sans_cle():
    """Une clé par défaut rendrait les pseudonymes recalculables par un tiers."""
    with pytest.raises(ValueError):
        X.observateurs(props(creator={"id": 4, "full_name": "X"}),
                       pseudonymiser=True, secret="")


def test_le_pseudonyme_est_stable_et_ne_contient_pas_le_nom():
    p = props(creator={"id": 4, "full_name": "Thomas CUYPERS"})
    a = X.observateurs(p, pseudonymiser=True, secret="cle")
    assert a == X.observateurs(p, pseudonymiser=True, secret="cle")
    assert "CUYPERS" not in a


# ── Dates et géométrie ───────────────────────────────────────────────────────

def test_la_date_est_sans_heure_et_le_signale():
    """L'API n'expose ni `time_start` ni `date_end` : une nuit d'enregistrement à cheval
    sur minuit est ramenée au seul jour de début."""
    import json
    ligne = X.to_row(feature(), cd_nom=60468, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice())
    assert ligne["date_min"] == ligne["date_max"]
    assert ligne["date_min"].hour == 0
    assert json.loads(ligne["additional_data"])["heure_connue"] == "non"


def test_une_observation_sans_date_est_inexploitable():
    f = feature()
    f["properties"]["session_data"]["date_start"] = ""
    assert X.to_row(f, cd_nom=60468, id_dataset=1, id_source=1, id_module=1, srid=2154,
                    resolver=ResolverFactice()) is None


def test_une_geometrie_absente_est_inexploitable():
    f = feature()
    f["geometry"] = None
    assert X.to_row(f, cd_nom=60468, id_dataset=1, id_source=1, id_module=1, srid=2154,
                    resolver=ResolverFactice()) is None


# ── Diffusion ────────────────────────────────────────────────────────────────

def test_pas_de_niveau_de_diffusion_par_defaut():
    """NULL signifie « le producteur ne se prononce pas », ce que GeoNature interprète
    correctement depuis qu'il ne calcule plus cette colonne."""
    ligne = X.to_row(feature(), cd_nom=60468, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice())
    assert ligne["id_nomenclature_diffusion_level"] is None


def test_le_niveau_de_diffusion_configure_est_applique():
    ligne = X.to_row(feature(), cd_nom=60468, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice(), code_diffusion="2")
    assert ligne["id_nomenclature_diffusion_level"] == "NIV_PRECIS=2"


def test_la_geometrie_reste_exacte_meme_avec_une_diffusion_restreinte():
    """Flouter en base serait irréversible et ruinerait tout suivi de gîte : la
    restriction porte sur la diffusion, pas sur la donnée."""
    ligne = X.to_row(feature(), cd_nom=60468, id_dataset=1, id_source=1, id_module=1,
                     srid=2154, resolver=ResolverFactice(), code_diffusion="4")
    assert (ligne["lon"], ligne["lat"]) == (1.21, 43.03)


# ── Empreinte ────────────────────────────────────────────────────────────────

def test_lempreinte_change_avec_le_contenu():
    """`timestamp_update` n'étant pas exposé, l'empreinte est le seul moyen de détecter
    une correction à la source."""
    assert X.empreinte(feature()) != X.empreinte(feature(total_count=99))


def test_lempreinte_est_stable_a_contenu_egal():
    assert X.empreinte(feature()) == X.empreinte(feature())


def test_lempreinte_suit_la_determination():
    assert (X.empreinte(feature(codesp="nycnoc"))
            != X.empreinte(feature(codesp="nyclei")))


# ── Cohérence de la table ────────────────────────────────────────────────────

def test_aucun_code_nest_a_la_fois_taxon_et_absence():
    assert not (set(T.TABLE) & set(T.ABSENCES))


def test_la_table_couvre_les_soixante_codes_mesures():
    """29 espèces, 29 déterminations partielles, 2 absences."""
    assert len(T.ESPECES) == 29
    assert len(T.AGREGATS) == 29
    assert len(T.ABSENCES) == 2


def test_tous_les_cd_nom_sont_des_entiers_positifs():
    assert all(isinstance(cd, int) and cd > 0 for cd in T.TABLE.values())


# ── Diagnostic des échecs de connexion ───────────────────────────────────────
# Écrit après coup : lors du premier essai en conditions réelles, dbchiroc.org a renvoyé
# un 500 sur le POST de connexion alors que le GET répondait normalement. Le connecteur
# annonçait « identifiants invalides », ce qui envoyait vérifier un mot de passe
# parfaitement valide. Un diagnostic faux coûte plus cher qu'une absence de diagnostic.

from gn_module_connectors.sources.dbchiro import api as A  # noqa: E402


class ReponseFactice:
    def __init__(self, status_code, url="https://dbchiroc.org/", text="", json_=None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = {"content-type": "application/json"}
        self._json = json_

    def json(self):
        if self._json is None:
            raise ValueError("pas du JSON")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.mark.parametrize("code", [500, 502, 503])
def test_une_panne_serveur_nest_pas_un_refus_didentifiants(code):
    """Le cas réellement rencontré : 500 sur /accounts/login/, GET normal."""
    with pytest.raises(A.ErreurDbChiro) as exc:
        A.verifier_reponse_login(
            ReponseFactice(code, "https://dbchiroc.org/accounts/login/"),
            "https://dbchiroc.org")
    message = str(exc.value)
    assert "panne côté serveur" in message
    assert "identifiants" not in message.replace("problème d'identifiants", "")


def test_un_429_parle_de_debit_et_non_de_mot_de_passe():
    with pytest.raises(A.ErreurDbChiro, match="débit"):
        A.verifier_reponse_login(
            ReponseFactice(429, "https://dbchiroc.org/accounts/login/"),
            "https://dbchiroc.org")


def test_un_403_evoque_le_csrf_ou_le_blocage_pas_le_mot_de_passe():
    with pytest.raises(A.ErreurDbChiro, match="CSRF"):
        A.verifier_reponse_login(
            ReponseFactice(403, "https://dbchiroc.org/accounts/login/"),
            "https://dbchiroc.org")


def test_un_retour_au_formulaire_en_200_est_bien_un_refus():
    with pytest.raises(A.ErreurDbChiro, match="identifiants invalides"):
        A.verifier_reponse_login(
            ReponseFactice(200, "https://dbchiroc.org/accounts/login/?next=/"),
            "https://dbchiroc.org")


def test_une_connexion_reussie_ne_leve_rien():
    assert A.verifier_reponse_login(
        ReponseFactice(200, "https://dbchiroc.org/"), "https://dbchiroc.org") is None


def test_une_erreur_serveur_interrompt_le_moissonnage():
    """Rendre un corpus partiel qu'un bilan présenterait comme complet serait pire
    qu'échouer."""
    with pytest.raises(A.ErreurDbChiro, match="panne côté"):
        A._verifier_json(ReponseFactice(503, "https://dbchiroc.org/api/v1/search"),
                         "https://dbchiroc.org/api/v1/search")


def test_un_filtre_anti_robot_est_nomme_explicitement():
    """demo.dbchiro.org est derrière Anubis : le cas doit se diagnostiquer seul."""
    with pytest.raises(A.ErreurDbChiro, match="anti-robot"):
        A._verifier_json(
            ReponseFactice(200, "https://demo.dbchiro.org/api/v1/search",
                           text="<html>within.website challenge</html>"),
            "https://demo.dbchiro.org/api/v1/search")


# ── Moisson bornée ───────────────────────────────────────────────────────────

class SessionFactice:
    """Rend des pages canoniques, en enregistrant les paramètres demandés."""

    def __init__(self, total, taille_reelle=None):
        self.total = total
        self.taille_reelle = taille_reelle
        self.appels = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.appels.append(params)
        taille = self.taille_reelle or int(params.get("page_size", 100))
        page = int(params.get("page", 1))
        debut = (page - 1) * taille
        lot = [{"id": i, "type": "Feature"}
               for i in range(debut, min(debut + taille, self.total))]
        suivant = "http://exemple/?page=%d" % (page + 1) if debut + taille < self.total else None
        return ReponseFactice(200, url,
                              json_={"count": self.total, "next": suivant,
                                     "results": {"features": lot}})


def test_la_moisson_bornee_sarrete_au_plafond():
    session = SessionFactice(8008)
    features = A.observations(session, {"url": "https://x", "page_size": 5000},
                              max_results=25)
    assert len(features) == 25


def test_la_moisson_bornee_ne_demande_pas_plus_que_necessaire():
    """Inutile de réclamer 5 000 observations pour en garder 25."""
    session = SessionFactice(8008)
    A.observations(session, {"url": "https://x", "page_size": 5000}, max_results=25)
    assert session.appels[0]["page_size"] == 25
    assert len(session.appels) == 1


def test_une_troncature_voulue_ne_declenche_pas_lavertissement():
    """Un garde-fou qui crie à chaque usage normal cesse d'être lu."""
    session = SessionFactice(8008)
    messages = []
    features = A.observations(session, {"url": "https://x"}, journal=messages.append,
                              max_results=10)
    assert len(features) == 10
    assert not any("pagination incomplète" in m for m in messages)
    assert any("bornée" in m for m in messages)


def test_une_moisson_complete_verifie_toujours_le_total():
    session = SessionFactice(50)
    messages = []
    features = A.observations(session, {"url": "https://x", "page_size": 5000},
                              journal=messages.append)
    assert len(features) == 50
    assert not any("pagination incomplète" in m for m in messages)


def test_une_pagination_qui_sarrete_trop_tot_est_signalee():
    """L'instance annonce 8 008 mais n'en rend que 50 : le bilan ne doit pas le taire."""
    session = SessionFactice(8008, taille_reelle=50)

    class Menteuse(SessionFactice):
        def get(self, url, params=None, timeout=None):
            self.appels.append(params or {})
            return ReponseFactice(200, url,
                                  json_={"count": 8008, "next": None,
                                         "results": {"features": [{"id": 1}] * 50}})

    messages = []
    A.observations(Menteuse(8008), {"url": "https://x"}, journal=messages.append)
    assert any("pagination incomplète" in m for m in messages)
