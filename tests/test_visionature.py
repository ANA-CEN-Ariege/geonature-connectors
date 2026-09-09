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


def test_code_atlas_lit_le_texte_et_non_la_cle_denumeration():
    """La forme majoritaire des exports réels est `{"@id": "3_13", "#text": "12"}`.

    L'`@id` y est la clé d'énumération du champ, le `#text` le code EOAC. Prendre l'`@id`
    ne donnait pas une valeur nulle — ce qui aurait été visible — mais un nombre faux :
    `int("3_13")` vaut **313** en Python, l'underscore étant un séparateur de chiffres
    accepté depuis la 3.6. Mesuré sur 165 observations réelles portant un code atlas,
    165 étaient versées en « Reproduction » contre 123 après correction, dont
    38 observations de code 1 que le seuil exclut explicitement.
    """
    assert N.code_atlas(obs(atlas_code={"@id": "3_13", "#text": "12"})) == 12
    assert N.code_atlas(obs(atlas_code={"@id": "3_3", "#text": "2"})) == 2
    # La correspondance n'est pas un décalage constant : 3_13 vaut 12, 3_16 vaut 14.
    assert N.code_atlas(obs(atlas_code={"@id": "3_16", "#text": "14"})) == 14


def test_absence_declaree_reperee_sous_la_forme_reelle():
    """Sans la lecture du `#text`, une absence déclarée entrait en présence."""
    assert N.est_absence(obs(atlas_code={"@id": "3_99", "#text": "99"}))




# ── Dénombrement ─────────────────────────────────────────────────────────────

def test_estimation_nest_pas_un_comptage():
    """Confondre les deux fausserait toute analyse quantitative."""
    assert N.denombrement(obs(count="50", estimation_code="ESTIMATION")) == ("IND", "Es")
    assert N.denombrement(obs(count="3", estimation_code="EXACT_VALUE")) == ("IND", "Co")


def test_sans_effectif_pas_de_denombrement():
    assert N.denombrement(obs()) == (None, None)


# ── Rien n'est imposé sans source ────────────────────────────────────────────

def test_methode_observation_non_imposee():
    assert N.cd_nomenclatures({}, obs())["METH_OBS"] is None


# ── Mortalité (ETA_BIO) ──────────────────────────────────────────────────────
#
# Ce bloc était le trou le plus visible du connecteur : `ETA_BIO` valait `None` en dur,
# donc toute la mortalité routière, l'électrocution et l'éolien — les données que les
# gestionnaires d'infrastructures viennent chercher — arrivaient en Synthèse
# indiscernables d'une observation ordinaire.

def test_mortalite_routiere_est_trouvee_morte():
    """Cas réel, tel qu'exporté par l'API : Faune-LR, 11 cas sur 12 mortalités."""
    o = obs(extended_info={"mortality": {"death_cause2": "ROAD_VEHICLE",
                                         "wounded": "0", "road_type2": "SECONDARY_ROAD"}})
    assert N.cd_nomenclatures({}, o)["ETA_BIO"] == "3"


def test_bloc_mortalite_vide_reste_une_mortalite():
    """`gn_vn2synthese` teste la présence de la clé, pas son contenu.

    Un `{"mortality": {}}` traité comme une absence d'information ferait passer un
    cadavre pour un animal vivant : c'est la clé qui porte le sens.
    """
    assert N.cd_nomenclatures({}, obs(extended_info={"mortality": {}}))["ETA_BIO"] == "3"


def test_animal_blesse_nest_pas_trouve_mort():
    """Divergence assumée avec `gn_vn2synthese`, qui écrit « Trouvé mort » dans les deux
    cas. `wounded = 1` décrit un animal blessé, donc vivant à l'observation."""
    o = obs(extended_info={"mortality": {"death_cause2": "PREDATION", "wounded": "1"}})
    assert N.cd_nomenclatures({}, o)["ETA_BIO"] == "2"


def test_restes_reperes_par_la_condition():
    """PEL (pelote de réjection), MUMMIE et BONESREMAINS viennent de la table de
    synonymes ETA_BIO de `gn_vn2synthese` — table qu'eux-mêmes n'interrogent jamais :
    leur v1.6.0 ne regarde que le bloc `mortality`, si bien qu'un reste osseux de
    chiroptère y ressort « Observé vivant »."""
    assert N.cd_nomenclatures({}, obs(details=[{"condition": "PEL"}]))["ETA_BIO"] == "3"


def test_absence_nest_pas_observee():
    """Correctif récent chez eux aussi (CHANGELOG 1.5.2, « Fix 'ETA_BIO' status for
    absence data ») : une espèce recherchée sans être trouvée n'a pas été vue vivante."""
    assert N.cd_nomenclatures({}, obs(atlas_code="99"))["ETA_BIO"] == "1"


def test_la_mortalite_prime_sur_labsence():
    """Un cadavre trouvé n'est pas une espèce recherchée en vain, même si l'effectif
    saisi est nul."""
    o = obs(count="0", estimation_code="EXACT_VALUE",
            extended_info={"mortality": {"death_cause2": "ELECTRIC"}})
    assert N.cd_nomenclatures({}, o)["ETA_BIO"] == "3"


def test_observation_ordinaire_est_vivante():
    """Hors mortalité déclarée, VisioNature est un outil de saisie sur faune vivante.
    Même position que `gn_vn2synthese`."""
    assert N.cd_nomenclatures({}, obs())["ETA_BIO"] == "2"


def test_cause_de_mortalite_extraite():
    """Aucune nomenclature SINP ne sait dire « collision routière » : la cause part
    dans additional_data, comme `gn_vn2synthese` la range dans sa table étendue."""
    o = obs(extended_info={"mortality": {"death_cause2": "ROAD_VEHICLE",
                                         "road_type2": "TRACK"}})
    assert N.cause_mortalite(o) == ("ROAD_VEHICLE", "TRACK")
    o2 = obs(extended_info={"mortality": {"death_cause2": "PREDATION",
                                          "predation2": "MAMMAL"}})
    assert N.cause_mortalite(o2) == ("PREDATION", "MAMMAL")
    assert N.cause_mortalite(obs()) == (None, None)


# ── Preuve d'existence et médias ─────────────────────────────────────────────

MEDIA_REEL = {"@id": "9032371", "type": "PHOTO", "media_is_hidden": "0",
              "path": "https://cdnmedia3.biolovision.net/www.faune-lr.org/2024-06",
              "filename": "462-01211429-1947.JPG"}


def test_preuve_existante_si_media():
    assert N.cd_nomenclatures({}, obs(medias=[MEDIA_REEL]))["PREUVE_EXIST"] == "1"
    assert N.cd_nomenclatures({}, obs())["PREUVE_EXIST"] == "2"


def test_url_de_media_concatenee_comme_chez_la_lpo():
    """`path` + « / » + `filename`, jointes par « , » : même construction que leur
    fct_c_get_medias_url_from_visionature_medias_array."""
    assert T.preuves_numeriques(obs(medias=[MEDIA_REEL])) == (
        "https://cdnmedia3.biolovision.net/www.faune-lr.org/2024-06/"
        "462-01211429-1947.JPG")


def test_media_masque_nest_pas_republie():
    """Divergence assumée : un média masqué à la source l'est pour protéger un nid, un
    gîte ou une station. Republier son URL contournerait cette décision — ce que fait
    `gn_vn2synthese`, qui concatène sans regarder `media_is_hidden`."""
    masque = {**MEDIA_REEL, "media_is_hidden": "1"}
    assert T.preuves_numeriques(obs(medias=[masque])) is None
    # La preuve existe malgré tout : c'est sa diffusion qui est interdite, pas son
    # existence. Effacer les deux perdrait une information vraie.
    assert N.preuve_existence(obs(medias=[masque])) == "1"


# ── Nature de l'objet géographique ───────────────────────────────────────────

@pytest.mark.parametrize("precision, attendu", [
    ("precise", "St"),          # pointage GPS : une station
    ("place", "In"),            # lieu-dit : une zone
    ("polygone", "In"),
    ("transect_precise", "In"),  # « precise » en suffixe, mais un transect reste une zone
    ("square", "In"),
])
def test_nature_objet_geo(precision, attendu):
    """Table de synonymes NAT_OBJ_GEO de `gn_vn2synthese` (onze entrées), reprise telle
    quelle. Noter `transect_precise` : le suffixe ne suffit pas à conclure."""
    assert N.nature_objet_geo(obs(precision=precision)) == attendu


def test_type_de_localisation_inconnu_retombe_sur_le_defaut():
    """Comme leur fonction de synonymes, qui rend NULL hors table : le défaut de la
    colonne est « NSP » (Ne sait pas), ce qui est exact. Étendre « In » à l'inconnu
    serait une extrapolation — rien ne dit qu'un type à venir désignera une zone."""
    assert N.nature_objet_geo(obs(precision="type_inedit")) is None
    assert N.nature_objet_geo(obs()) is None


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


# ── Date et heure d'observation ──────────────────────────────────────────────
#
# `synthese.date_min` est un `timestamp`. Le module y écrivait un `date`, donc minuit
# pour tout le monde. Mesuré sur 338 observations réelles (exports Faune-LR) :
# `timing.@notime = 0` dans 323 cas, soit 95,6 % d'heures significatives jetées.

def test_date_iso8601():
    assert str(T.parse_date({"date": {"@ISO8601": "2024-03-15"}})) == "2024-03-15"
    assert T.parse_date({}) is None


# Relevé réel, tel qu'exporté par l'API : le jour ne porte pas d'heure (@notime = 1),
# c'est `timing` qui la porte (@notime = 0).
RELEVE_HORODATE = {"date": {"@timestamp": "1717452000", "@notime": "1",
                            "@offset": "7200",
                            "@ISO8601": "2024-06-04T00:00:00+02:00"}}
TIMING_REEL = {"@timestamp": "1717491238", "@notime": "0", "@offset": "7200",
               "@ISO8601": "2024-06-04T10:53:58+02:00"}


def test_heure_reprise_quand_elle_a_un_sens():
    horodatage, connue = T.parse_datetime(RELEVE_HORODATE, obs(timing=TIMING_REEL))
    assert str(horodatage) == "2024-06-04 10:53:58"
    assert connue is True


def test_pas_dheure_inventee_quand_notime_le_dit():
    """C'est là qu'on fait mieux que `gn_vn2synthese` : quand `@notime = 1`,
    `timing.@timestamp` vaut minuit local. Eux écrivent « 00:00:00 » sans distinguer ce
    cas d'une observation réellement faite à minuit ; nous le signalons."""
    minuit = {"@timestamp": "1717452000", "@notime": "1", "@offset": "7200"}
    horodatage, connue = T.parse_datetime(RELEVE_HORODATE, obs(timing=minuit))
    assert str(horodatage) == "2024-06-04 00:00:00"
    assert connue is False


def test_heure_lue_sans_iso8601():
    """Les réponses de transit ne portent que `@timestamp` et `@offset`, sans
    `@ISO8601` : ne lire que ce dernier rendait toute la donnée sans date."""
    sans_iso = {"date": {"@timestamp": "1717452000", "@notime": "1", "@offset": "7200"}}
    timing = {"@timestamp": "1717491238", "@notime": "0", "@offset": "7200"}
    horodatage, connue = T.parse_datetime(sans_iso, obs(timing=timing))
    assert str(horodatage) == "2024-06-04 10:53:58"
    assert connue is True


def test_heure_murale_independante_du_fuseau_du_serveur():
    """`TO_TIMESTAMP` chez eux rend un `timestamptz` que PostgreSQL reconvertit selon le
    fuseau de la base : l'heure écrite dépend d'un réglage d'exploitation. Ici elle est
    calculée depuis `@offset`, que Biolovision fournit avec chaque horodatage."""
    ete = {"date": {"@timestamp": "1717452000", "@offset": "7200", "@notime": "1"}}
    hiver = {"date": {"@timestamp": "1739833200", "@offset": "3600", "@notime": "1"}}
    assert str(T.parse_datetime(ete, obs())[0]) == "2024-06-04 00:00:00"
    assert str(T.parse_datetime(hiver, obs())[0]) == "2025-02-18 00:00:00"


def test_lheure_ne_deplace_pas_le_jour_du_releve():
    """La date d'observation déclarée fait foi. Un `timing` décalé — saisie tardive,
    prospection nocturne enregistrée après minuit — ne doit pas faire glisser
    `date_min` d'un jour."""
    lendemain = {"@ISO8601": "2024-06-05T01:30:00+02:00", "@notime": "0"}
    horodatage, _ = T.parse_datetime(RELEVE_HORODATE, obs(timing=lendemain))
    assert str(horodatage) == "2024-06-04 01:30:00"


def test_notime_absent_on_se_fie_a_lheure_lue():
    """Toutes les instances n'émettent pas `@notime`. Minuit pile y est presque toujours
    un défaut de saisie ; une heure quelconque, une vraie heure."""
    assert T.heure_significative({"@ISO8601": "2024-06-04T10:53:58+02:00"}) is True
    assert T.heure_significative({"@ISO8601": "2024-06-04T00:00:00+02:00"}) is False


# ── Altitude ─────────────────────────────────────────────────────────────────

def test_altitude_lue_en_chaine():
    """L'API renvoie « 365 » et non 365 : un `int()` direct sur la valeur brute
    fonctionnerait, mais la forme `{"@id": …}` existe aussi selon les points d'entrée."""
    assert T.altitude(obs(altitude="365")) == 365
    assert T.altitude(obs(altitude=365)) == 365
    assert T.altitude(obs()) is None


# ── UUID natif ───────────────────────────────────────────────────────────────

UUID_REEL = "d689b344-2255-41ef-b297-041008b9ed95"


def test_uuid_du_producteur_prime():
    """Recalculer un uuid5 alors que le producteur publie son propre UUID crée un
    identifiant DEE divergent : si la même donnée arrive aussi par un dépôt SINP, le
    doublon est invisible. Mesuré : `uuid` présent sur 338 observations sur 338."""
    retenu, supplante = T.identifiant_sinp(
        {"@id": "1"}, obs(uuid=UUID_REEL), "faune-ariege.org")
    assert retenu == UUID_REEL
    assert supplante == T.sinp_uuid({"@id": "1"}, obs(), "faune-ariege.org")


def test_uuid5_en_repli_sans_uuid_natif():
    retenu, supplante = T.identifiant_sinp({"@id": "1"}, obs(), "faune-ariege.org")
    assert retenu == T.sinp_uuid({"@id": "1"}, obs(), "faune-ariege.org")
    assert supplante is None


def test_uuid_illisible_ne_casse_pas_limport():
    """Une valeur non conforme doit retomber sur le calcul, pas faire échouer
    l'insertion du lot entier sur un cast uuid."""
    retenu, _ = T.identifiant_sinp({"@id": "1"}, obs(uuid="pas-un-uuid"), "x")
    assert retenu == T.sinp_uuid({"@id": "1"}, obs(), "x")


def test_uuid_de_groupe_depuis_le_formulaire():
    """⚠ `gn_vn2synthese` lit `forms_json.uuid`, qui n'est **pas** un champ de l'API :
    c'est une colonne qu'ils ajoutent avec `DEFAULT uuid_generate_v4()`
    (00_init_db.sql:10), donc un UUID aléatoire stable seulement grâce à leur table de
    transit. Vérifié sur un export réel : l'objet `forms` de l'API ne porte aucun uuid.
    On dérive donc un uuid5 de `id_form_universal`, reproductible sans rien stocker."""
    a = T.uuid_groupe(obs(id_form_universal="65_3477089"), "x")
    b = T.uuid_groupe(obs(id_form_universal="65_3477089"), "y")
    assert a == b is not None
    assert T.uuid_groupe(obs(id_form_universal="65_3491340"), "x") != a
    assert T.uuid_groupe(obs(), "x") is None


def test_uuid_de_groupe_prefixe_par_linstance_sans_identifiant_universel():
    """`id_form` seul (« 3477089 ») n'est pas unique entre instances, contrairement à
    `id_form_universal` (« 65_3477089 ») qui préfixe l'identifiant du site."""
    assert T.uuid_groupe(obs(id_form="3477089"), "faune-ariege.org") != \
        T.uuid_groupe(obs(id_form="3477089"), "faune-occitanie.org")


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


# ── Robustesse du client vendorisé ───────────────────────────────────────────

def test_les_controleurs_relaient_le_timeout():
    """Sans timeout, `requests` attend indéfiniment et le moissonnage se fige.

    `BiolovisionAPI` accepte le paramètre, mais aucune de ses onze sous-classes ne le
    relayait dans l'amont : il restait à `None` quel que soit le contrôleur, et rien ne
    le signalait — le client journalise dans un logger que la CLI n'affiche pas. Un
    dry-run est resté bloqué plusieurs minutes sans la moindre sortie avant qu'on ne
    trouve la cause.

    Ce test garde le correctif : si une mise à jour du client vendorisé l'écrase, il
    rougit au lieu de laisser revenir le gel silencieux.
    """
    import logging
    logging.disable(logging.CRITICAL)
    try:
        from gn_module_connectors.sources.visionature.biolovision import api as bio
        for classe in (bio.SpeciesAPI, bio.ObservationsAPI,
                       bio.ObserversAPI, bio.TaxoGroupsAPI):
            controleur = classe(
                user_email="a@b.c", user_pw="x", base_url="https://exemple.org/",
                client_key="k", client_secret="s",
                timeout=120, unavailable_delay=60, max_retry=3, max_chunks=100)
            assert controleur._limits["timeout"] == 120, classe.__name__
            assert controleur._limits["unavailable_delay"] == 60, classe.__name__
    finally:
        logging.disable(logging.NOTSET)
