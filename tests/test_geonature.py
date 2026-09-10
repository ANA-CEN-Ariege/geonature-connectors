"""Connecteur GeoNature : moissonnage d'une autre instance GeoNature.

Les cas sont bâtis sur `ITEM_GEONATURE` (tests/test_insert_alignement.py), reconstitué
depuis la définition SQL de `gn_exports.v_synthese_sinp` — le connecteur a été écrit sans
accès à une instance distante. Les noms et les types de colonnes sont donc exacts, mais la
distribution des valeurs ne l'est pas : à confronter à un sondage réel dès qu'une instance
sera disponible.

Le fil directeur : `api2GN`, dont ce connecteur reprend la cible, perd **toutes** les
nomenclatures parce qu'il passe les libellés de la vue à une fonction qui attend des
codes. Une bonne moitié des cas ci-dessous existe pour que cela ne puisse pas se
reproduire ici sans qu'un test rougisse.

    pytest tests/ -q
"""

import json
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

from gn_module_connectors.sources.geonature import api as A  # noqa: E402
from gn_module_connectors.sources.geonature import conflits as C  # noqa: E402
from gn_module_connectors.sources.geonature import metadata as M  # noqa: E402
from gn_module_connectors.sources.geonature import nomenclatures as N  # noqa: E402
from gn_module_connectors.sources.geonature import taxonomy as T  # noqa: E402
from gn_module_connectors.sources.geonature import transform as X  # noqa: E402

from test_insert_alignement import ITEM_GEONATURE, ResolverFactice  # noqa: E402

CFG = {"url": "https://geonature.exemple.fr", "id_export": 12, "jeton": "secret",
       "page_size": 1000, "timeout": 30}


def item(**surcharges):
    return {**ITEM_GEONATURE, **surcharges}


def ligne(**surcharges):
    return X.to_row(item(**surcharges), cd_nom=1958, id_dataset=1, id_source=1,
                    id_module=1, srid=2154, resolver=ResolverFactice(),
                    instance="https://geonature.exemple.fr", id_export="12")


# ── Nomenclatures : le défaut d'api2GN ──────────────────────────────────────

class ResolverStrict:
    """Résolveur qui distingue formellement un code d'un libellé.

    `id()` — la méthode qu'emploient les trois autres connecteurs — n'accepte que des
    `cd_nomenclature`, et lève si on lui passe autre chose. C'est ce qui rend le test
    capable de détecter le défaut d'api2GN : là-bas, un libellé est passé à la fonction
    des codes, qui rend NULL sans se plaindre.
    """

    def __init__(self, connus=None):
        self.connus = connus if connus is not None else {}

    def id(self, mnemonique, cd):
        raise AssertionError(
            f"id() attend un cd_nomenclature ; « {cd} » est un libellé. Employez "
            f"id_souple pour une source qui parle en libellés.")

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"

    def id_souple(self, mnemonique, valeur):
        valeur = str(valeur or "").strip()
        if not valeur:
            return None
        return self.connus.get((mnemonique, valeur))

    def id_souple_ou_defaut(self, mnemonique, valeur):
        trouve = self.id_souple(mnemonique, valeur)
        return trouve if trouve is not None else self.defaut(mnemonique)


def test_un_libelle_est_resolu_comme_un_libelle_pas_comme_un_code():
    """Le cœur du sujet. La vue livre « Vivant », pas « 2 »."""
    resolver = ResolverStrict({("ETA_BIO", "Vivant"): 4242})
    resolus = N.resoudre(item(), resolver)
    assert resolus["id_nomenclature_bio_condition"] == 4242


def test_un_libelle_inconnu_est_collecte_et_non_avale():
    """api2GN rend NULL sans rien dire. Ici la perte doit remonter jusqu'au bilan."""
    manques = set()
    resolus = N.resoudre(item(), ResolverStrict(), manques=manques)
    assert ("ETA_BIO", "Vivant") in manques
    # L'insertion doit tout de même aboutir : on retombe sur le défaut de la colonne.
    assert resolus["id_nomenclature_bio_condition"] == "ETA_BIO=defaut"


def test_une_colonne_vide_prend_le_defaut_de_la_synthese():
    """L'insertion par lots interdit d'omettre une colonne pour une seule ligne : le
    défaut doit être résolu explicitement, comme l'aurait fait le DEFAULT de la colonne."""
    manques = set()
    resolus = N.resoudre(item(etat_biologique=None), ResolverStrict(), manques=manques)
    assert resolus["id_nomenclature_bio_condition"] == "ETA_BIO=defaut"
    # Une colonne vide n'est pas un manque : le producteur n'a simplement rien dit. Seuls
    # les libellés réellement présents mais inconnus du référentiel local en sont.
    assert not [m for m in manques if m[0] == "ETA_BIO"]


def test_les_colonnes_absentes_de_la_vue_prennent_le_defaut():
    """STAT_BIOGEO n'est pas exposée par v_synthese_sinp, et `validateur` est un nom de
    personne — pas un statut de validation."""
    resolus = N.resoudre(item(), ResolverStrict())
    assert resolus["id_nomenclature_biogeo_status"] == "STAT_BIOGEO=defaut"
    assert resolus["id_nomenclature_valid_status"] == "STATUT_VALID=defaut"


def test_le_statut_de_validation_vient_de_la_configuration():
    resolver = ResolverStrict({("STATUT_VALID", "2"): 77})
    resolus = N.resoudre(item(), resolver, statut_validation="2")
    assert resolus["id_nomenclature_valid_status"] == 77


def test_les_nomenclatures_hors_insert_partent_en_donnees_additionnelles():
    """Quatre nomenclatures que l'INSERT commun ne porte pas. Les jeter serait une perte
    muette ; les ajouter à l'INSERT obligerait les trois autres connecteurs à les
    fournir."""
    provenance = json.loads(ligne()["additional_data"])
    assert provenance["gn_type_regroupement"] == "Session"
    assert provenance["gn_methode_determination"] == "Autre méthode de détermination"
    assert provenance["gn_type_info_geo"] == "Géoréférencement"


# ── Diffusion et sensibilité ────────────────────────────────────────────────

def test_le_niveau_de_diffusion_du_producteur_est_repris():
    resolver = ResolverStrict({("NIV_PRECIS", "Précise"): 55})
    assert N.niveau_diffusion(item(), resolver) == 55


def test_un_niveau_de_diffusion_inconnu_ne_retombe_pas_sur_le_defaut():
    """NULL veut dire « le producteur ne se prononce pas ». Le défaut d'un type de
    nomenclature inventerait une restriction, ou en ferait disparaître une."""
    manques = set()
    assert N.niveau_diffusion(item(), ResolverStrict(), manques=manques) is None
    assert ("NIV_PRECIS", "Précise") in manques


def test_le_niveau_de_diffusion_de_la_configuration_prime():
    resolver = ResolverStrict({("NIV_PRECIS", "3"): 33, ("NIV_PRECIS", "Précise"): 55})
    assert N.niveau_diffusion(item(), resolver, force="3") == 33


def test_une_observation_sensible_peut_etre_restreinte():
    """Parade au décalage de référentiels : la sensibilité est recalculée localement, et
    un référentiel local moins couvrant rendrait la donnée moins protégée qu'à la source.
    """
    resolver = ResolverStrict({("NIV_PRECIS", "2"): 22, ("NIV_PRECIS", "Précise"): 55})
    sensible = item(niveau_sensibilite="Sensible - Maille 10 km")
    assert N.niveau_diffusion(sensible, resolver, si_sensible="2") == 22
    # « Non sensible » ne déclenche pas la restriction.
    assert N.niveau_diffusion(item(), resolver, si_sensible="2") == 55


def test_la_sensibilite_locale_reste_calculee_par_le_trigger():
    assert "id_nomenclature_sensitivity" not in ligne()


# ── Taxonomie ───────────────────────────────────────────────────────────────

def test_un_cd_nom_connu_est_retenu_directement():
    assert T.choisir(1958, 1958, {1958}) == (1958, "")


def test_un_cd_nom_inconnu_retombe_sur_le_cd_ref():
    """`cd_ref` désigne le taxon valide : il change bien moins souvent que `cd_nom` d'une
    version de TAXREF à l'autre."""
    assert T.choisir(999999, 1958, {1958}) == (1958, "repli_cd_ref")


def test_un_taxon_introuvable_est_rejete_avec_un_motif():
    assert T.choisir(999999, 888888, {1958}) == (None, "cd_nom_hors_taxref")


def test_un_cd_nom_absent_ou_illisible_est_rejete():
    """Un cd_nom NULL en base fait échouer l'évaluation des permissions bien plus tard,
    sur une AttributeError qui ne désigne plus sa cause."""
    for valeur in (None, "", "n/a", {}):
        assert T.choisir(valeur, None, {1958})[0] is None


def test_choisir_ne_rend_jamais_un_rejet_sans_motif():
    for cd_nom, cd_ref in ((1958, 1958), (999999, 1958), (999999, 888888), (None, None)):
        retenu, motif = T.choisir(cd_nom, cd_ref, {1958})
        assert (retenu is None) == (motif == "cd_nom_hors_taxref")


def test_les_codes_a_verifier_couvrent_aussi_les_cd_ref():
    """Le repli de `choisir` est inopérant si l'index ne contient que des `cd_nom` : le
    `cd_ref` n'y figurerait jamais, et tout taxon renuméroté entre deux versions de TAXREF
    serait rejeté alors que son taxon valide est présent. Le repli passerait pour du code
    mort sans qu'aucune erreur ne se produise — c'est exactement ce qui était écrit.
    """
    codes = T.codes_a_verifier([item(cd_nom=999999, cd_ref=1958)])
    assert codes == {999999, 1958}


def test_les_codes_a_verifier_ignorent_les_valeurs_illisibles():
    assert T.codes_a_verifier([item(cd_nom=None, cd_ref="n/a")]) == set()
    assert T.codes_a_verifier([]) == set()


def test_la_couverture_compte_directs_replis_et_rejets():
    items = [item(), item(cd_nom=999999, cd_ref=1958),
             item(cd_nom=777777, cd_ref=666666)]
    couv = T.couverture(items, {1958})
    assert (couv["directs"], couv["replis"], couv["rejetes"]) == (1, 1, 1)
    assert couv["manquants"][0][0][0] == 777777


# ── Identifiants ────────────────────────────────────────────────────────────

def test_lidentifiant_permanent_du_producteur_est_repris_verbatim():
    """L'instance distante est un producteur SINP : id_perm_sinp EST l'identifiant DEE."""
    identifiant, calcule = X.identifiant_sinp(item(), "https://a.fr", "12")
    assert identifiant == "3f2b8c4d-5e60-4a17-9a17-0d7e4a2f9b13"
    assert calcule is None


def test_un_uuid_est_derive_quand_le_producteur_nen_publie_pas():
    """unique_id_sinp est nullable en Synthèse. Insérer NULL ne casserait rien — et c'est
    bien le problème : NULL n'étant jamais égal à NULL, l'index unique ne dédoublonne pas
    et chaque passage recréerait tout le corpus."""
    sans = item(id_perm_sinp=None)
    identifiant, calcule = X.identifiant_sinp(sans, "https://a.fr", "12")
    assert identifiant == calcule
    assert X.identifiant_sinp(sans, "https://a.fr", "12")[0] == identifiant


def test_luuid_derive_distingue_les_instances_et_les_exports():
    sans = item(id_perm_sinp=None)
    a = X.identifiant_sinp(sans, "https://a.fr", "12")[0]
    b = X.identifiant_sinp(sans, "https://b.fr", "12")[0]
    c = X.identifiant_sinp(sans, "https://a.fr", "13")[0]
    assert len({a, b, c}) == 3


def test_luuid_derive_laisse_une_trace_pour_le_realignement():
    provenance = json.loads(ligne(id_perm_sinp=None)["additional_data"])
    assert provenance["gn_uuid_calcule"] == provenance["gn_uuid_calcule"]
    assert provenance["gn_uuid_calcule"] == ligne(id_perm_sinp=None)["unique_id_sinp"]


def test_pas_de_trace_de_realignement_quand_luuid_est_natif():
    """La présence systématique ferait tourner l'UPDATE de réalignement à chaque lot."""
    assert "gn_uuid_calcule" not in json.loads(ligne()["additional_data"])


@pytest.mark.parametrize("valeur", ["", None, "pas-un-uuid", "1234", "  "])
def test_un_uuid_de_groupe_malforme_devient_none(valeur):
    """`unique_id_sinp_grp` passe par CAST(... AS uuid) : une chaîne non conforme ferait
    échouer non pas la ligne, mais le LOT ENTIER."""
    assert X.uuid_groupe(item(id_perm_grp_sinp=valeur)) is None


def test_lidentifiant_de_source_est_lid_synthese_distant():
    """Seule clé qui fait fonctionner la redirection vers `/#/synthese/occurrence/<id>`."""
    assert ligne()["entity_source_pk_value"] == "481902"


def test_la_cle_de_la_source_amont_est_nommee_sans_ambiguite():
    """`id_source` dans la vue est l'entity_source_pk_value du logiciel qui a produit la
    donnée, deux crans en arrière. Le nom explicite évite le contresens."""
    provenance = json.loads(ligne()["additional_data"])
    assert provenance["gn_pk_source_amont"] == "obs_2024_11837"


# ── Empreinte ───────────────────────────────────────────────────────────────

def test_lempreinte_est_stable_a_contenu_egal():
    assert X.empreinte(item()) == X.empreinte(item())


@pytest.mark.parametrize("champ, valeur", [
    ("cd_nom", 60506), ("nombre_max", 12), ("observateurs", "Unetelle"),
    ("x_centroid_4326", 2.5), ("etat_biologique", "Mort"), ("precision", 5000),
])
def test_lempreinte_change_avec_le_contenu(champ, valeur):
    assert X.empreinte(item(**{champ: valeur})) != X.empreinte(item())


def test_lempreinte_ignore_la_date_de_modification():
    """Choix de conception : `meta_update_date` bouge chez le producteur pour des motifs
    que nous n'importons pas — une validation, un recalcul de sensibilité. L'y inclure
    ferait réécrire tout le corpus à chaque campagne de validation chez lui."""
    autre = item(date_modification="2026-01-01T00:00:00+01:00")
    assert X.empreinte(autre) == X.empreinte(item())
    # Elle reste consignée, pour le diagnostic.
    assert json.loads(ligne(**{"date_modification": "2026-01-01T00:00:00+01:00"})[
        "additional_data"])["gn_date_modification"] == "2026-01-01T00:00:00+01:00"


# ── Géométrie et dates ──────────────────────────────────────────────────────

def test_le_centroide_est_prefere_au_wkt():
    assert X.coordonnees(item()) == (1.9, 42.8)


def test_un_wkt_ponctuel_est_lu_a_defaut_de_centroide():
    sans = item(x_centroid_4326=None, y_centroid_4326=None)
    assert X.coordonnees(sans) == (1.9, 42.8)


def test_une_geometrie_non_ponctuelle_est_rejetee():
    """L'INSERT commun ne sait construire qu'un point. Plutôt qu'un centroïde inventé
    ici, on rejette et le motif le dit."""
    surfacique = item(x_centroid_4326=None, y_centroid_4326=None,
                      wkt_4326="POLYGON((1 42, 2 42, 2 43, 1 43, 1 42))")
    assert X.coordonnees(surfacique) == (None, None)
    assert X.to_row(surfacique, cd_nom=1958, id_dataset=1, id_source=1, id_module=1,
                    srid=2154, resolver=ResolverFactice()) is None


def test_lheure_est_conservee_et_le_fuseau_retire():
    """La vue rend un timestamp AVEC fuseau. Tronquer à dix caractères ramènerait toutes
    les observations à minuit ; garder le fuseau ferait échouer la comparaison avec les
    timestamps sans fuseau de la Synthèse."""
    moment = X.horodatage("2024-06-01T10:53:58+02:00")
    assert str(moment) == "2024-06-01 10:53:58"
    assert moment.tzinfo is None


def test_le_z_terminal_est_accepte():
    assert str(X.horodatage("2024-06-01T08:53:58Z")) == "2024-06-01 08:53:58"


def test_une_date_sans_heure_reste_lisible():
    assert str(X.horodatage("2024-06-01")) == "2024-06-01 00:00:00"


def test_une_date_de_fin_absente_reprend_la_date_de_debut():
    assert ligne(date_fin=None)["date_max"] == ligne()["date_min"]


def test_une_observation_sans_date_est_rejetee():
    assert X.to_row(item(date_debut=None), cd_nom=1958, id_dataset=1, id_source=1,
                    id_module=1, srid=2154, resolver=ResolverFactice()) is None


# ── Périmètre ───────────────────────────────────────────────────────────────

def test_sans_emprise_tout_passe():
    assert X.dans_bbox(item(), None) is True


@pytest.mark.parametrize("bbox, attendu", [
    ((1.0, 42.0, 2.0, 43.0), True),      # dedans
    ((3.0, 42.0, 4.0, 43.0), False),     # dehors
    ((1.9, 42.8, 2.0, 43.0), True),      # exactement sur le bord
])
def test_le_double_filtre_local_borne_lemprise(bbox, attendu):
    assert X.dans_bbox(item(), bbox) is attendu


def test_une_observation_sans_coordonnees_est_ecartee_quand_une_emprise_est_active():
    """Mieux vaut un rejet tracé qu'une passoire silencieuse."""
    sans = item(x_centroid_4326=None, y_centroid_4326=None, wkt_4326=None)
    assert X.dans_bbox(sans, (1.0, 42.0, 2.0, 43.0)) is False


# ── Observateurs ────────────────────────────────────────────────────────────

def test_les_observateurs_sont_repris_en_clair_par_defaut():
    """Contrairement à dbChiro : une instance GeoNature est un producteur SINP conforme
    qui a déjà arbitré ce qu'il diffuse."""
    assert ligne()["observers"] == "Untel"


def test_la_pseudonymisation_refuse_de_tourner_sans_cle():
    with pytest.raises(ValueError, match="pseudonymisation_secret"):
        X.observateurs(item(), pseudonymiser=True, secret="")


def test_le_pseudonyme_est_stable_et_ne_contient_pas_le_nom():
    a = X.observateurs(item(), pseudonymiser=True, secret="cle")
    b = X.observateurs(item(), pseudonymiser=True, secret="cle")
    assert a == b and "Untel" not in a
    assert X.observateurs(item(), pseudonymiser=True, secret="autre") != a


def test_determinateur_et_validateur_ne_vont_pas_en_colonne():
    """`determiner` et `validator` existent en Synthèse mais pas dans l'INSERT commun."""
    resultat = ligne()
    assert "determiner" not in resultat and "validator" not in resultat
    provenance = json.loads(resultat["additional_data"])
    assert provenance["gn_determinateur"] == "Unetelle"
    assert provenance["gn_validateur"] == "Untel Tiers"


# ── Données additionnelles ──────────────────────────────────────────────────

def test_les_donnees_additionnelles_distantes_restent_imbriquees():
    """Les fusionner à plat les laisserait écraser nos propres clés."""
    provenance = json.loads(ligne()["additional_data"])
    assert provenance["gn_donnees_additionnelles"] == {"protocole": "IPA", "point": "12"}


def test_des_donnees_additionnelles_illisibles_sont_conservees_telles_quelles():
    provenance = json.loads(ligne(donnees_additionnelles="pas du json")[
        "additional_data"])
    assert provenance["gn_donnees_additionnelles"] == "pas du json"


def test_les_champs_redérivables_du_cd_nom_ne_sont_pas_stockes():
    """Les conserver gonflerait le JSONB sans rien apporter : ils se relisent dans
    taxonomie.taxref."""
    provenance = json.loads(ligne()["additional_data"])
    for champ in ("regne", "classe", "ordre", "famille", "cd_ref", "nom_valide",
                  "group1_inpn", "rang_taxo"):
        assert not any(champ in cle for cle in provenance), champ


def test_la_version_taxref_ecrite_est_la_locale():
    """C'est dans ce référentiel que le cd_nom écrit s'interprète."""
    resultat = X.to_row(item(), cd_nom=1958, id_dataset=1, id_source=1, id_module=1,
                        srid=2154, resolver=ResolverFactice(), version_taxref="Taxref V16.0")
    assert resultat["meta_v_taxref"] == "Taxref V16.0"
    assert json.loads(resultat["additional_data"])[
        "gn_version_taxref_source"] == "Taxref V17.0"


# ── Vérification de l'export distant ────────────────────────────────────────

def test_un_export_conforme_ne_manque_de_rien():
    bloquantes, degradantes = A.verifier_colonnes(item())
    assert not bloquantes and not degradantes


def test_une_vue_sans_geometrie_est_bloquante():
    """Une vue floutée qui retire les coordonnées ne convient pas : mieux vaut refuser que
    d'écrire des milliers de lignes sans géométrie."""
    floutee = {k: v for k, v in ITEM_GEONATURE.items()
               if k not in A.COLONNES_GEOMETRIE}
    bloquantes, _ = A.verifier_colonnes(floutee)
    assert bloquantes == ["géométrie (x_centroid_4326, y_centroid_4326, wkt_4326)"]


def test_une_vue_maison_sans_cd_nom_est_bloquante():
    maison = {k: v for k, v in ITEM_GEONATURE.items() if k != "cd_nom"}
    bloquantes, _ = A.verifier_colonnes(maison)
    assert "cd_nom" in bloquantes


def test_une_colonne_facultative_absente_degrade_sans_bloquer():
    partiel = {k: v for k, v in ITEM_GEONATURE.items()
               if k not in ("id_perm_grp_sinp", "precision")}
    bloquantes, degradantes = A.verifier_colonnes(partiel)
    assert not bloquantes
    assert set(degradantes) == {"id_perm_grp_sinp", "precision"}


def test_une_colonne_nulle_reste_une_colonne_exposee():
    """Le contrôle porte sur la présence de la clé, pas sur sa valeur."""
    bloquantes, degradantes = A.verifier_colonnes(item(precision=None))
    assert not bloquantes and not degradantes


def test_un_filtre_ignore_par_le_serveur_est_detecte():
    """Preuve directe et gratuite : `total_filtered` égal à `total` alors qu'un filtre est
    actif signale que le serveur l'a écarté — un paramètre inconnu ne fait pas erreur."""
    charge = {"total": 48210, "total_filtered": 48210, "items": [item()],
              "license": {"name": "Licence Ouverte v2.0"}}
    messages = A.diagnostiquer_page(charge, {"geometry": "POLYGON(...)"})
    assert any("ignoré" in m for m in messages)


def test_un_filtre_qui_exclut_reellement_ne_declenche_rien():
    charge = {"total": 48210, "total_filtered": 1200, "items": [item()],
              "license": {"name": "Licence Ouverte v2.0"}}
    assert A.diagnostiquer_page(charge, {"geometry": "POLYGON(...)"}) == []


def test_labsence_de_filtre_ne_declenche_rien():
    charge = {"total": 48210, "total_filtered": 48210, "items": [item()],
              "license": {"name": "Licence Ouverte v2.0"}}
    assert A.diagnostiquer_page(charge, {}) == []


def test_une_licence_absente_est_signalee():
    charge = {"total": 1, "total_filtered": 1, "items": [item()], "license": {}}
    assert any("licence" in m for m in A.diagnostiquer_page(charge, {}))


# ── Pagination ──────────────────────────────────────────────────────────────

class ReponseFactice:
    def __init__(self, charge, status_code=200):
        self._charge = charge
        self.status_code = status_code
        self.url = "https://geonature.exemple.fr/api/exports/api/12"

    def json(self):
        if self._charge is None:
            raise ValueError("pas du json")
        return self._charge


class RequestsFactice:
    """Serveur d'export simulé, qui note les paramètres réellement reçus."""

    def __init__(self, pages, limite_serveur=None):
        self.pages = pages
        self.limite_serveur = limite_serveur
        self.appels = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.appels.append(dict(params or {}))
        numero = int((params or {}).get("offset", 0))
        items = self.pages[numero] if numero < len(self.pages) else []
        total = sum(len(p) for p in self.pages)
        charge = {"total": total, "total_filtered": total, "page": numero,
                  "limit": self.limite_serveur or (params or {}).get("limit"),
                  "items": items, "license": {"name": "Licence Ouverte v2.0"}}
        return ReponseFactice(charge)


def _lot(debut, combien):
    return [item(id_synthese=debut + i) for i in range(combien)]


def test_offset_est_un_numero_de_page_pas_un_decalage_de_lignes(monkeypatch):
    """Contre-intuitif, écrit nulle part dans la documentation du module d'export, et
    lourd de conséquences : traité comme un décalage de lignes, il ferait sauter
    999 lignes sur 1000 à chaque page."""
    faux = RequestsFactice([_lot(1, 3), _lot(4, 3), _lot(7, 1)])
    monkeypatch.setattr(A, "requests", faux)
    items, meta = A.moissonner({**CFG, "page_size": 3}, {})
    assert [a["offset"] for a in faux.appels] == [0, 1, 2]
    assert len(items) == 7 and meta["complet"]


def test_la_limite_rabotee_par_le_serveur_est_adoptee_des_la_page_zero(monkeypatch):
    """Le serveur plafonne à max_page_size_api sans le dire. Continuer à demander la
    taille voulue ferait croire la pagination terminée dès la première page."""
    faux = RequestsFactice([_lot(1, 2), _lot(3, 2), _lot(5, 1)], limite_serveur=2)
    monkeypatch.setattr(A, "requests", faux)
    items, meta = A.moissonner({**CFG, "page_size": 500}, {}, journal=lambda m: None)
    assert meta["limite"] == 2
    assert len(items) == 5


def test_une_pagination_inerte_est_interrompue(monkeypatch):
    """Un serveur qui ignore `offset` rend indéfiniment la même page : sans ce contrôle,
    la moisson gonfle en mémoire jusqu'à ce que le processus meure."""
    class Inerte(RequestsFactice):
        def get(self, url, params=None, headers=None, timeout=None):
            self.appels.append(dict(params or {}))
            return ReponseFactice({"total": 9, "total_filtered": 9, "items": _lot(1, 3),
                                   "limit": 3, "license": {}})

    monkeypatch.setattr(A, "requests", Inerte([]))
    with pytest.raises(A.ErreurGeoNature, match="offset"):
        A.moissonner({**CFG, "page_size": 3}, {}, journal=lambda m: None)


def test_le_tri_est_impose_pour_stabiliser_la_pagination(monkeypatch):
    """Sans tri stable, une base qui reçoit des saisies pendant le moissonnage réordonne
    les résultats entre deux pages : des lignes sont répétées, d'autres sautées."""
    faux = RequestsFactice([_lot(1, 2)])
    monkeypatch.setattr(A, "requests", faux)
    A.moissonner({**CFG, "page_size": 5}, {})
    assert faux.appels[0]["orderby"] == "id_synthese"
    assert faux.appels[0]["order"] == "asc"


def test_une_moisson_bornee_nest_pas_complete(monkeypatch):
    """La troncature est voulue — donc pas d'avertissement —, mais la réconciliation doit
    s'interdire de tourner là-dessus."""
    faux = RequestsFactice([_lot(1, 10)])
    monkeypatch.setattr(A, "requests", faux)
    items, meta = A.moissonner(CFG, {}, journal=lambda m: None, max_results=4)
    assert len(items) == 4
    assert meta["complet"] is False


def test_une_pagination_incomplete_est_signalee(monkeypatch):
    class Menteur(RequestsFactice):
        def get(self, url, params=None, headers=None, timeout=None):
            numero = int((params or {}).get("offset", 0))
            items = _lot(1, 2) if numero == 0 else []
            return ReponseFactice({"total": 99, "total_filtered": 99, "items": items,
                                   "limit": 2, "license": {}})

    monkeypatch.setattr(A, "requests", Menteur([]))
    messages = []
    items, meta = A.moissonner({**CFG, "page_size": 2}, {}, journal=messages.append)
    assert meta["complet"] is False
    assert any("incomplète" in m for m in messages)


def test_le_jeton_passe_par_len_tete_et_jamais_par_lurl():
    """Une chaîne de requête est journalisée par le serveur distant et par tout proxy :
    le jeton s'y retrouverait en clair dans des fichiers que personne ne surveille."""
    entetes = A._entetes(CFG)
    assert entetes["api-key"] == "secret"
    assert "token" not in A.filtres_serveur(CFG)


@pytest.mark.parametrize("code, motif", [
    (401, "jeton"), (403, "jeton"), (404, "id_export"), (500, "HTTP 500")])
def test_les_erreurs_http_nomment_leur_cause(code, motif):
    with pytest.raises(A.ErreurGeoNature, match=motif):
        A._verifier_json(ReponseFactice({}, status_code=code), "https://x/api")


def test_une_page_de_connexion_html_est_reconnue():
    """Le symptôme d'un jeton non pris en compte."""
    with pytest.raises(A.ErreurGeoNature, match="non-JSON"):
        A._verifier_json(ReponseFactice(None), "https://x/api")


def test_une_reponse_sans_items_nest_pas_une_api_dexport():
    with pytest.raises(A.ErreurGeoNature, match="items"):
        A._verifier_json(ReponseFactice({"results": []}), "https://x/api")


def test_le_filtre_incremental_porte_sur_la_bonne_colonne():
    filtres = A.filtres_serveur(CFG, depuis="2024-06-01", champ_date="date_creation")
    assert filtres["filter_d_up_date_creation"] == "2024-06-01"


# ── Métadonnées ─────────────────────────────────────────────────────────────

def test_un_jeu_absent_est_cree():
    assert M.decider_jdd(existe=False)[0] == "creer"


def test_un_jeu_qui_nest_qu_a_nous_est_rafraichi():
    assert M.decider_jdd(existe=True, lignes_autres_sources=0)[0] == "upsert"


def test_un_jeu_alimente_par_une_autre_source_est_adopte_sans_etre_reecrit():
    """Même UUID = même objet SINP : y écrire est correct. Mais le jeu local a pu être
    enrichi à la main, et le nom que l'API nous donne n'est pas forcément meilleur."""
    action, motif = M.decider_jdd(existe=True, lignes_autres_sources=1200)
    assert action == "adopter" and "1200" in motif


def test_un_jeu_desactive_est_refuse():
    """GeoNature masque un jeu inactif : y verser des observations les rendrait
    invisibles, ce qui est pire que de ne pas les importer."""
    action, motif = M.decider_jdd(existe=True, actif=False)
    assert action == "refuser" and "désactivé" in motif


def test_un_rattachement_de_cadre_divergent_est_signale_sans_etre_change():
    action, motif = M.decider_jdd(existe=True, lignes_autres_sources=0,
                                  cadre_local=3, cadre_vise=7)
    assert action == "adopter"
    assert "3" in motif and "7" in motif


def test_le_nom_du_jeu_dit_de_quelle_instance_il_vient():
    """Deux instances publiant un « Inventaire ZNIEFF » donneraient sinon deux jeux
    indiscernables dans une liste déroulante."""
    assert M.nom_jdd("Inventaire ZNIEFF", "https://geonature.exemple.fr") == (
        "Inventaire ZNIEFF (geonature.exemple.fr)")


def test_le_nom_du_jeu_ne_repete_pas_le_site_deja_present():
    assert M.nom_jdd("ZNIEFF geonature.exemple.fr", "https://geonature.exemple.fr") == (
        "ZNIEFF geonature.exemple.fr")


def test_un_jeu_sans_nom_reste_nommable():
    assert M.nom_jdd(None, "https://a.fr").startswith("Jeu de données sans nom")


def test_la_licence_figure_dans_la_description_du_jeu():
    """C'est là que l'exploitant la cherche, dans le module Métadonnées."""
    texte = M.description_jdd(item(), instance="https://a.fr", id_export=12,
                              licence="Licence Ouverte v2.0",
                              licence_url="https://exemple.fr/lo")
    assert "Licence Ouverte v2.0" in texte and "https://exemple.fr/lo" in texte
    assert "centroïde" in texte


def test_les_acteurs_sont_degages_de_leur_role():
    assert M.acteurs_jdd(
        "CEN Ariège (Producteur du jeu de données), ANA (Fournisseur)") == [
        "CEN Ariège", "ANA"]


def test_les_acteurs_sont_dedoublonnes_sans_etre_inventes():
    assert M.acteurs_jdd("ANA (Producteur), ANA (Fournisseur)") == ["ANA"]
    assert M.acteurs_jdd("") == []
    assert M.acteurs_jdd(None) == []


# ── Conflits avec une autre source ──────────────────────────────────────────
#
# Le sujet mérite ses propres cas, parce que le premier remède écrit ici était faux.
# `« remplacer »` semblait résoudre la collision : il la déplaçait. `INSERT_SQL` ne
# réécrit jamais `id_source` mais écrase tout le contenu, si bien qu'au passage suivant
# du GBIF la ligne porte notre id_source et le contenu du GBIF — et `conflits_autre_source`
# ne la voit plus.

def test_la_politique_par_defaut_est_stable():
    politique, avertissements = C.decider("ignorer", gbif_actif=True)
    assert politique == "ignorer"
    assert avertissements == []


def test_remplacer_est_averti_quand_lautre_connecteur_moissonne_encore():
    """Le piège : reprendre la ligne ne sert à rien si le GBIF la reprend au passage
    suivant. Les deux connecteurs se réécrivent alors indéfiniment, et le conflit devient
    indétectable puisque l'id_source ne les distingue plus."""
    politique, avertissements = C.decider("remplacer", gbif_actif=True)
    assert politique == "remplacer"
    assert len(avertissements) == 1
    assert "exclude_dataset_keys" in avertissements[0]


def test_remplacer_ne_pose_pas_de_probleme_si_le_gbif_est_hors_jeu():
    assert C.decider("remplacer", gbif_actif=False) == ("remplacer", [])


@pytest.mark.parametrize("valeur", ["", None, "supprimer", "REMPLACER TOUT"])
def test_une_politique_inconnue_est_refusee_sauf_le_vide(valeur):
    if not valeur:
        # Vide = défaut, pas une erreur.
        assert C.decider(valeur, gbif_actif=False)[0] == "ignorer"
    else:
        with pytest.raises(ValueError, match="ignorer"):
            C.decider(valeur, gbif_actif=False)


def test_la_politique_est_insensible_a_la_casse_et_aux_espaces():
    assert C.decider("  Remplacer  ", gbif_actif=False)[0] == "remplacer"


def test_aucun_ecrasement_ne_produit_aucun_message():
    """Un garde-fou qui parle à chaque exécution normale cesse d'être lu."""
    assert C.diagnostic_ecrasement(0, 50000) == []


def test_un_ecrasement_est_signale_avec_sa_part_et_sa_cause():
    messages = C.diagnostic_ecrasement(312, 48210)
    assert "312" in messages[0] and "0,6" in messages[0].replace(".", ",")
    assert "exclude_dataset_keys" in messages[1]


def test_un_ecrasement_sans_corpus_ne_divise_pas_par_zero():
    assert C.diagnostic_ecrasement(5, 0)
