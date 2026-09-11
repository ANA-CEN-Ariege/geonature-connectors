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
from gn_module_connectors.sources.geonature import transform as gn_tr  # noqa: E402
from gn_module_connectors.sources.geonature import nomenclatures as gn_nomen  # noqa: E402


class ResolverFactice:
    """Résolveur de nomenclatures sans base : renvoie une valeur lisible et non nulle.

    Les deux familles de méthodes du vrai résolveur sont distinguées, et c'est essentiel
    pour le connecteur GeoNature : `id` attend un `cd_nomenclature`, `id_souple` accepte
    aussi un libellé. Un factice qui les confondrait laisserait passer précisément le
    défaut qu'on cherche à empêcher.
    """

    def id(self, mnemonique, cd):
        return f"{mnemonique}={cd}" if cd is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"

    def id_souple(self, mnemonique, valeur):
        valeur = str(valeur or "").strip()
        return f"{mnemonique}~{valeur}" if valeur else None

    def id_souple_ou_defaut(self, mnemonique, valeur):
        trouve = self.id_souple(mnemonique, valeur)
        return trouve if trouve is not None else self.defaut(mnemonique)


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


# Enregistrement tel que le rend `GET /api/exports/api/<id>` sur la vue par défaut du
# module d'export, `gn_exports.v_synthese_sinp`.
#
# ⚠ **Reconstitué depuis la définition SQL de la vue** (gn_module_export,
# `migrations/data/exports.sql`), et non relevé sur une instance : le connecteur a été
# écrit sans accès à un GeoNature distant. Les noms et les types de colonnes sont donc
# exacts — ils sont lus dans le SQL —, mais la distribution réelle des valeurs ne l'est
# pas. À remplacer par un sondage réel dès qu'une instance sera disponible : le README
# rappelle que trois défauts du module ont vécu sous un test vert écrit à partir du code
# plutôt que des données.
#
# Deux traits de la vue à ne jamais perdre de vue, tous deux vérifiés plus bas :
#   - les colonnes de nomenclature portent des LIBELLÉS (`label_default`), pas des codes ;
#   - `date_debut` est un timestamp avec fuseau, pas une date.
ITEM_GEONATURE = {
    "id_synthese": 481902,
    "id_source": "obs_2024_11837",
    "id_perm_sinp": "3f2b8c4d-5e60-4a17-9a17-0d7e4a2f9b13",
    "id_perm_grp_sinp": "7c1e9d02-4b83-4f56-8a29-6d0b1e3f5c74",
    "date_debut": "2024-06-01T10:53:58+02:00",
    "date_fin": "2024-06-01T11:30:00+02:00",
    "cd_nom": 1958,
    "cd_ref": 1958,
    "version_taxref": "Taxref V17.0",
    "nom_cite": "Anas crecca",
    "nom_valide": "Anas crecca Linnaeus, 1758",
    "regne": "Animalia", "group1_inpn": "Chordés", "group2_inpn": "Oiseaux",
    "classe": "Aves", "ordre": "Anseriformes", "famille": "Anatidae", "rang_taxo": 84,
    "nombre_min": 3, "nombre_max": 3,
    "altitude_min": 365, "altitude_max": 365,
    "profondeur_min": None, "profondeur_max": None,
    "observateurs": "Untel",
    "determinateur": "Unetelle",
    "validateur": "Untel Tiers",
    "numero_preuve": None,
    "preuve_numerique": "https://exemple.fr/photo/42.jpg",
    "preuve_non_numerique": None,
    "comment_releve": "prospection matinale",
    "comment_occurrence": "au bord de l'eau",
    "date_creation": "2024-06-02T09:12:00+02:00",
    "date_modification": "2024-06-11T14:02:31+02:00",
    "derniere_action": "2024-06-11T14:02:31+02:00",
    "jdd_uuid": "4d331cae-65e4-4948-b0b2-a11bc5bb46c2",
    "jdd_nom": "Inventaire ZNIEFF de l'Ariège",
    "jdd_acteurs": "CEN Ariège (Producteur du jeu de données), ANA (Fournisseur)",
    "ca_uuid": "1b6f0a54-9c27-4e81-b3d5-2a8e7f04c916",
    "ca_nom": "Inventaires naturalistes départementaux",
    "reference_biblio": None,
    "code_habitat": None, "habitat": None,
    "nom_lieu": "Étang de Lers",
    "precision": 100,
    "donnees_additionnelles": '{"protocole": "IPA", "point": "12"}',
    "wkt_4326": "POINT(1.9 42.8)",
    "x_centroid_4326": 1.9,
    "y_centroid_4326": 42.8,
    # ⚠ Libellés, pas de cd_nomenclature — c'est tout l'enjeu du connecteur.
    "nature_objet_geo": "Stationnel",
    "type_regroupement": "REL",          # TYP_GRP : les libellés SINP sont les codes
    "methode_regroupement": "Relevé de terrain",
    # ⚠ Les libellés sont ceux de `t_nomenclatures.label_default`, au caractère près :
    # « Alimentation » et « Vivant » figuraient ici alors que le référentiel écrit
    # « Chasse/alimentation » et « Observé vivant ». Le résolveur factice acceptant
    # n'importe quel libellé, les tests restaient verts sur deux valeurs qu'aucune
    # instance n'aurait produites — `tests/test_nomenclatures.py` vérifie désormais
    # chaque libellé de cette fixture contre le référentiel.
    "comportement": "Chasse/alimentation",
    "technique_obs": "Vu",
    "statut_biologique": "Non renseigné",
    "etat_biologique": "Observé vivant",
    "naturalite": "Sauvage",
    "preuve_existante": "Oui",
    "precision_diffusion": "Précise",
    "stade_vie": "Adulte",
    "sexe": "Femelle",
    "objet_denombrement": "Individu",
    "type_denombrement": "Compté",
    "niveau_sensibilite": "Non sensible - Diffusion précise",
    "statut_observation": "Présent",
    "floutage_dee": "Non",
    "statut_source": "Terrain",
    "type_info_geo": "Géoréférencement",
    "methode_determination": "Autre méthode de détermination",
}


def ligne_dbchiro():
    return db_tr.to_row(FEATURE_DBCHIRO, cd_nom=60506, id_dataset=1, id_source=1,
                        id_module=1, srid=2154, resolver=ResolverFactice(),
                        instance="https://dbchiroc.org")


def ligne_geonature(**surcharges):
    return gn_tr.to_row({**ITEM_GEONATURE, **surcharges}, cd_nom=1958, id_dataset=1,
                        id_source=1, id_module=1, srid=2154,
                        resolver=ResolverFactice(),
                        instance="https://geonature.exemple.fr", id_export="12")


def ligne_gbif():
    return gbif_tr.to_row(OCCURRENCE_GBIF, cd_nom=252, id_dataset=1, id_source=1,
                          id_module=1, srid=2154, resolver=ResolverFactice())


def ligne_vn():
    return vn_tr.to_row(RELEVE_VN, OBSERVATION_VN, cd_nom=1958, id_dataset=1,
                        id_source=1, id_module=1, srid=2154,
                        resolver=ResolverFactice(), instance="faune-ariege.org",
                        index_anonymat={"7": False}, secret_pseudo="cle-de-test")


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn),
                                           ("dbchiro", ligne_dbchiro),
                                           ("geonature", ligne_geonature)])
def test_to_row_fournit_tous_les_parametres_lies(nom, fabrique):
    """Sans quoi le premier `insert_batch` échoue, et rien n'est importé."""
    manquants = parametres_lies() - set(fabrique())
    assert not manquants, f"{nom} : paramètres absents de to_row -> {sorted(manquants)}"


@pytest.mark.parametrize("nom, fabrique", [("gbif", ligne_gbif), ("visionature", ligne_vn),
                                           ("dbchiro", ligne_dbchiro),
                                           ("geonature", ligne_geonature)])
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
    for nom, module in (("gbif", gbif_tr), ("visionature", vn_tr), ("dbchiro", db_tr),
                        ("geonature", gn_nomen)):
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


def test_lempreinte_geonature_est_prise_en_compte():
    """Même exigence pour la source GeoNature distante.

    Le cas est même plus aigu qu'ailleurs : la réconciliation des suppressions impose une
    relecture complète du corpus, donc un passage de toutes les lignes dans l'ON CONFLICT.
    Sans `gn_empreinte` dans la clause, les deux côtés du COALESCE valent NULL sur ces
    lignes — `IS DISTINCT FROM` est faux — et une coordonnée rectifiée à la source ne
    serait jamais reprise.
    """
    where = _source_insert().split("ON CONFLICT")[-1]
    assert "gn_empreinte" in where
    assert "gn_empreinte" in ligne_geonature()["additional_data"]


def test_les_cles_dempreinte_suivent_lordre_du_coalesce():
    """`CLES_EMPREINTE` doit énumérer exactement les clés du COALESCE, dans le même ordre.

    Un désaccord ne casse aucune insertion, et c'est précisément ce qui le rend
    dangereux : `empreinte_de` — qui sert au décompte du bilan — retiendrait une clé que
    la base n'a pas retenue, et l'import annoncerait des mises à jour qui n'ont pas eu
    lieu, ou l'inverse. Le genre de défaut qui vit longtemps sous un test vert.
    """
    source = _source_insert()
    where = source.split("ON CONFLICT (unique_id_sinp) DO UPDATE SET")[1]
    # Premier COALESCE de la clause WHERE : celui qui lit la ligne déjà en base.
    premier = where.split("IS DISTINCT FROM")[0]
    dans_sql = re.findall(r"additional_data->>'([a-z_]+_empreinte)'", premier)

    declarees = re.search(r"CLES_EMPREINTE = \(([^)]*)\)", source).group(1)
    dans_python = re.findall(r'"([a-z_]+_empreinte)"', declarees)

    assert dans_python == dans_sql, (
        f"ordre divergent — Python {dans_python} / SQL {dans_sql}")


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
                    "entity_source_pk_value", "last_action",
                    # Écrite à la création, jamais réécrite : voir le commentaire dans
                    # INSERT_SQL et `test_une_validation_locale_survit_a_un_reimport`.
                    "id_nomenclature_valid_status"}


def test_une_validation_locale_survit_a_un_reimport():
    """`id_nomenclature_valid_status` doit rester hors du SET de l'ON CONFLICT.

    Le scénario que cela protège : une observation entre avec le statut de pré-validation,
    un validateur la reprend et tranche, puis la source la modifie. Si la colonne était
    réécrite, l'import remettrait le statut automatique par-dessus la décision du
    validateur — dans la Synthèse seulement, car `prevalider` s'interdit de réécrire
    `gn_commons.t_validations`. Les deux tables se contrediraient, et c'est la Synthèse que
    lisent les exports, les filtres et la carte : la version fausse serait la visible.

    Une observation corrigée à la source revient au validateur par le filtre « modifiée
    depuis sa validation », qui compare `meta_update_date` à `validation_date`. Écraser le
    statut court-circuiterait ce mécanisme au lieu de s'en servir.

    Le test est écrit à l'envers des autres — il exige une ABSENCE — parce que la ligne
    retirée l'avait été par inadvertance dès le premier commit du module, et qu'elle est
    exactement le genre de chose qu'on réintroduit en complétant une clause.
    """
    bloc_set = (_source_insert().split("ON CONFLICT (unique_id_sinp) DO UPDATE SET")[1]
                .split("WHERE COALESCE")[0])
    assert "id_nomenclature_valid_status = EXCLUDED" not in bloc_set, (
        "la décision d'un validateur local serait écrasée au prochain import")
    # Mais la colonne doit bien être écrite à la création, sans quoi la pré-validation
    # n'aurait aucun effet.
    assert "id_nomenclature_valid_status" in colonnes_insert()


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
