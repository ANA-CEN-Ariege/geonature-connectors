"""Statut de reproduction des taxons non-oiseaux.

Les cas sont tirés de données réelles quand il en existait sous la main — exports de
Faune-LR et de Faune-Occitanie, 338 relevés dont 56 reptiles — et, à défaut, des règles
de production de `gn_vn2synthese` v1.6.0, seule source écrite de ces correspondances.
Chaque test dit d'où vient son cas.

    pytest tests/ -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.sources.visionature import (  # noqa: E402
    nomenclatures as N,
    reproduction as R,
    transform as T,
)


def releve(taxonomy, **champs):
    """Relevé minimal : le groupe taxonomique porté par l'espèce, comme dans l'API."""
    return {"@id": "1", "species": {"@id": "7500", "taxonomy": taxonomy}, **champs}


def obs(**champs):
    return {"@id": "10", "coord_lat": "43.2", "coord_lon": "2.9", "count": "1", **champs}


def detail(age=None, sexe=None, count="1"):
    """Ligne de `details[]` dans la forme enrichie que renvoient les exports réels."""
    d = {"count": count}
    if age:
        d["age"] = {"@id": age, "#text": age.lower()}
    if sexe:
        d["sex"] = {"@id": sexe, "#text": sexe.lower()}
    return d


# ── Identification du groupe ─────────────────────────────────────────────────

def test_groupe_resolu_par_le_code_de_l_instance():
    """L'index construit depuis `taxo_groups` prime sur les identifiants de
    Faune-France : rien ne garantit qu'une instance numérote ses groupes pareil."""
    index = R.index_groupes([{"id": "6", "name": "TAXO_GROUP_AMPHIBIAN"}])
    assert R.code_groupe("6", index) == "TAXO_GROUP_AMPHIBIAN"
    assert R.code_groupe("6") == "TAXO_GROUP_REPTILIAN"   # repli Faune-France


def test_groupe_inconnu_ne_produit_rien():
    """Un groupe absent de la table — poissons, mollusques, flore — reste muet plutôt
    que de retomber sur les règles d'un autre groupe."""
    a = R.analyser(releve("29"), obs(details=[detail(age="JUVENILE")]))
    assert a.degre is None


# ── Cas réels ────────────────────────────────────────────────────────────────

def test_reptile_subadulte_nest_pas_un_indice():
    """Timon lepidus, relevé 146292873 (Faune-LR) : SUBAD / sexe inconnu.

    Le subadulte est explicitement neutre dans la table de la LPO : il atteste d'une
    reproduction passée, pas d'une reproduction sur le site ni dans l'année.
    """
    a = R.analyser(releve("6"), obs(details=[detail(age="SUBAD", sexe="U")]))
    assert not a.reproduction
    # Le code est connu et jugé neutre : c'est différent d'un code sans règle.
    assert a.degre == R.INCONNU


def test_reptile_immature_est_un_code_non_couvert():
    """Podarcis liolepis, relevé 146371918 : IMM / F, comportement « prend le soleil ».

    `IMM` apparaît 9 fois sur 56 relevés de reptiles du corpus réel, et la table de la
    LPO ne lui donne aucune règle chez les reptiles — alors qu'elle en donne une chez
    les mammifères et les odonates (« possible »). Ce n'est donc pas un silence
    délibéré mais une lacune, que le compteur de codes inconnus rend visible.
    """
    contexte = R.Contexte()
    a = R.analyser(releve("6"),
                   obs(details=[detail(age="IMM", sexe="F")],
                       behaviours=[{"@id": "134_20"}]),
                   contexte)
    assert not a.reproduction
    assert contexte.inconnus["TAXO_GROUP_REPTILIAN/age:IMM"] == 1
    # « prend le soleil » et « femelle », eux, sont bien couverts et jugés neutres :
    # ils ne doivent pas être signalés comme inconnus.
    assert len(contexte.inconnus) == 1


def test_reptile_predate_nest_pas_une_reproduction():
    """Zamenis longissimus, relevé 158631408 (Faune-Occitanie) : restes trouvés dans des
    fèces de blaireau, comportement « Prédaté ». Le seul comportement du relevé est
    neutre — un animal mangé ne prouve rien sur sa reproduction."""
    a = R.analyser(releve("6"), obs(details=[detail(age="U", sexe="U")],
                                    behaviours=[{"@id": "134_15"}]))
    assert not a.reproduction


def test_plusieurs_lignes_de_details_sont_agregees():
    """Natrix astreptophora, relevé 146678403 : deux lignes de `details[]` pour deux
    individus. Une seule ligne de Synthèse en sort, et l'analyse porte sur l'ensemble."""
    a = R.analyser(releve("6"), obs(count="2",
                                    details=[detail(age="U", sexe="U"),
                                             detail(age="EGG", sexe="U")]))
    assert a.degre == R.CERTAIN
    assert a.indice == "age:EGG"


# ── Règles de production de la LPO ───────────────────────────────────────────

@pytest.mark.parametrize("groupe, champs, degre", [
    # Amphibiens : une ponte est l'indice de reproduction par excellence, et c'est
    # exactement ce que le connecteur perdait jusqu'ici.
    ("7", {"details": [detail(age="EGG")]}, R.CERTAIN),
    ("7", {"details": [detail(age="TETARD")]}, R.CERTAIN),
    ("7", {"behaviours": [{"@id": "134_3"}]}, R.PROBABLE),   # accouplement
    # Chiroptères : le jeune non volant atteste une colonie de mise bas sur place.
    ("2", {"details": [detail(age="YOUNGHAIRY")]}, R.CERTAIN),
    ("2", {"details": [detail(sexe="FG")]}, R.POSSIBLE),     # femelle gestante
    # Odonates : l'exuvie est la preuve que le développement larvaire s'est fait là.
    ("8", {"details": [detail(age="EXUVIE")]}, R.CERTAIN),
    ("8", {"behaviours": [{"@id": "134_2"}]}, R.PROBABLE),   # tandem
    # Papillons : la chenille tient lieu de preuve, l'imago ne prouve rien.
    ("9", {"details": [detail(age="CAT")]}, R.CERTAIN),
    ("9", {"details": [detail(age="IMAGO")]}, R.INCONNU),
    # Orthoptères : le juvénile y vaut « certain », alors qu'il ne vaut que
    # « probable » chez les reptiles — asymétrie du témoin, reprise telle quelle.
    ("11", {"details": [detail(age="JUVENILE")]}, R.CERTAIN),
    ("6", {"details": [detail(age="JUVENILE")]}, R.PROBABLE),
])
def test_regles_par_groupe(groupe, champs, degre):
    assert R.analyser(releve(groupe), obs(**champs)).degre == degre


def test_le_degre_le_plus_fort_l_emporte():
    """Un même relevé peut porter plusieurs indices de force inégale.

    Le témoin s'en remet à un `ORDER BY` placé dans une CTE puis à un `LIMIT 1` sans
    tri, ce que PostgreSQL ne garantit pas ; la résolution est ici explicite.
    """
    a = R.analyser(releve("8"), obs(details=[detail(age="IMM", sexe="MF")],   # possible
                                    behaviours=[{"@id": "134_4"}]))          # certain
    assert a.degre == R.CERTAIN


def test_les_valeurs_sont_typees():
    """Un code de sexe ne doit pas être confronté aux règles d'âge.

    `fct_c_get_reproduction_status` aplatit âges, sexes et comportements dans un seul
    tableau : « FG » y vaudrait « certain » chez les amphibiens même s'il arrivait
    comme classe d'âge. Aucune collision n'existe aujourd'hui, mais la table est
    surchargeable et une instance peut en créer une.
    """
    contexte = R.Contexte()
    a = R.analyser(releve("7"), obs(details=[detail(age="FG")]), contexte)
    assert not a.reproduction
    assert contexte.inconnus["TAXO_GROUP_AMPHIBIAN/age:FG"] == 1


def test_details_en_valeur_simple():
    """Le client `transfer_vn` de la LPO journalise `"age": "AD"` là où les exports
    renvoient `{"@id": "AD", "#text": "adulte"}`. Les deux formes existent."""
    a = R.analyser(releve("7"), obs(details=[{"age": "TETARD", "sex": "U"}]))
    assert a.degre == R.CERTAIN


# ── Articulation avec le reste des nomenclatures ─────────────────────────────

def test_statut_bio_verse_en_reproduction():
    """Le SINP ne gradue pas : les trois degrés donnent le même « 3 »."""
    a = R.analyser(releve("7"), obs(details=[detail(age="EGG")]))
    cds = N.cd_nomenclatures(releve("7"), obs(details=[detail(age="EGG")]), None, a)
    assert cds["STATUT_BIO"] == "3"


def test_le_code_atlas_prime_sur_la_deduction():
    """Un code atlas est saisi par l'observateur comme un indice de nidification ; une
    déduction ne doit pas s'y substituer."""
    o = obs(atlas_code={"@id": "3_2", "#text": "1"}, details=[detail(age="JUVENILE")])
    # Code atlas 1 = « vu en période favorable », qui n'est pas un indice : le repli ne
    # doit pas pour autant être neutralisé, sinon un juvénile serait perdu.
    a = R.analyser(releve("6"), o)
    assert N.cd_nomenclatures(releve("6"), o, None, a)["STATUT_BIO"] == "3"


def test_une_absence_ne_devient_jamais_une_reproduction():
    """Espèce recherchée et non trouvée : l'âge résiduel du formulaire ne doit pas
    transformer l'absence en preuve de reproduction. Le témoin n'a pas ce garde-fou."""
    o = obs(count="0", estimation_code="EXACT_VALUE", details=[detail(age="EGG")])
    a = R.analyser(releve("7"), o)
    cds = N.cd_nomenclatures(releve("7"), o, None, a)
    assert a.degre == R.CERTAIN          # l'indice est bien vu…
    assert cds["STATUT_BIO"] is None     # … mais non versé
    assert cds["STATUT_OBS"] == "No"


def test_oiseaux_non_couverts_par_la_table():
    """Les oiseaux relèvent des codes atlas, pas de la table : leur groupe n'y figure
    pas, et un poussin ne doit pas y déclencher de règle empruntée à un autre groupe."""
    assert "TAXO_GROUP_BIRD" not in R.REGLES
    assert R.analyser(releve("1"), obs(details=[detail(age="PULL")])).degre is None


# ── Surcharge par configuration ──────────────────────────────────────────────

def test_surcharge_complete_sans_effacer():
    """Le trou de la table du témoin : la règle « jeune non velu » y porte `value = NULL`
    et n'apparie donc jamais rien. L'exploitant doit pouvoir la combler."""
    regles = R.fusionner({"TAXO_GROUP_BAT": {"age": {"YOUNGNAKED": "certain"}}})
    contexte = R.Contexte(regles=regles)
    assert R.analyser(releve("2"), obs(details=[detail(age="YOUNGNAKED")]),
                      contexte).degre == R.CERTAIN
    # Les règles livrées du même groupe survivent à la surcharge.
    assert R.analyser(releve("2"), obs(details=[detail(age="YOUNGHAIRY")]),
                      contexte).degre == R.CERTAIN
    # La table du module n'est pas modifiée en place.
    assert "YOUNGNAKED" not in R.REGLES["TAXO_GROUP_BAT"]["age"]


def test_desactivation():
    """Un exploitant qui juge la déduction trop généreuse doit pouvoir la couper sans
    perdre les codes atlas."""
    contexte = R.Contexte(active=False)
    assert R.analyser(releve("7"), obs(details=[detail(age="EGG")]), contexte).degre is None


# ── Chaîne complète ──────────────────────────────────────────────────────────

class ResolverFactice:
    def id(self, mnemonique, cd):
        return f"{mnemonique}={cd}" if cd is not None else self.defaut(mnemonique)

    def defaut(self, mnemonique):
        return f"{mnemonique}=defaut"


def test_to_row_conserve_le_degre():
    """Le SINP ne gradue pas la reproduction ; le degré est conservé dans
    `additional_data`, pour la même raison que le code atlas brut."""
    import json
    s = releve("8", date={"@ISO8601": "2024-06-01T00:00:00+02:00"})
    o = obs(details=[detail(age="EXUVIE")])
    ligne = T.to_row(s, o, cd_nom=65473, id_dataset=None, id_source=1, id_module=2,
                     srid=2154, resolver=ResolverFactice(), instance="faune-lr",
                     secret_pseudo="k", repro=R.Contexte())
    donnees = json.loads(ligne["additional_data"])
    assert donnees["repro_degre"] == R.CERTAIN
    assert donnees["repro_indice"] == "age:EXUVIE"
    assert ligne["id_nomenclature_bio_status"] == "STATUT_BIO=3"


def test_to_row_deduit_sans_contexte_fourni():
    """La déduction n'est pas conditionnée à un câblage complet : un appelant qui ne
    fournit pas de contexte retombe sur la table du module et les identifiants de
    groupe de Faune-France. Un oubli de configuration ne doit pas faire perdre en
    silence la reproduction de tous les non-oiseaux."""
    s = releve("8", date={"@ISO8601": "2024-06-01T00:00:00+02:00"})
    ligne = T.to_row(s, obs(details=[detail(age="EXUVIE")]), cd_nom=65473,
                     id_dataset=None, id_source=1, id_module=2, srid=2154,
                     resolver=ResolverFactice(), instance="faune-lr", secret_pseudo="k")
    assert ligne["id_nomenclature_bio_status"] == "STATUT_BIO=3"


# ── Index des groupes : le code est dans name_constant ───────────────────────

# Forme réelle du contrôleur `taxo_groups`, relevée sur Faune-Occitanie :
# champs id, name, name_constant, latin_name, access_mode.
GROUPES_REELS = [
    {"id": "1", "name": "Oiseaux", "name_constant": "TAXO_GROUP_BIRD",
     "latin_name": "Aves", "access_mode": "full"},
    {"id": "2", "name": "Chauves-souris", "name_constant": "TAXO_GROUP_BAT",
     "latin_name": "Chiroptera", "access_mode": "full"},
    {"id": "6", "name": "Reptiles", "name_constant": "TAXO_GROUP_REPTILIAN",
     "latin_name": "Reptilia", "access_mode": "full"},
]


def test_le_code_du_groupe_vient_de_name_constant():
    """`name` est le libellé TRADUIT, pas le code.

    Ce module a longtemps lu `name`. L'index associait alors « Reptiles » à
    l'identifiant 6, tandis que les règles sont indexées par `TAXO_GROUP_REPTILIAN` :
    aucune ne pouvait s'apparier et tout le dispositif de reproduction des non-oiseaux
    était inerte. Rien ne le signalait — un groupe sans règle est un cas normal,
    indiscernable d'un groupe dont le code n'a pas été reconnu.
    """
    index = R.index_groupes(GROUPES_REELS)
    assert index == {"1": "TAXO_GROUP_BIRD", "2": "TAXO_GROUP_BAT",
                     "6": "TAXO_GROUP_REPTILIAN"}


def test_les_codes_indexes_apparient_les_regles():
    """Garde-fou de bout en bout : sans lui, la correction pourrait se reperdre."""
    index = R.index_groupes(GROUPES_REELS)
    assert index["2"] in R.REGLES
    assert index["6"] in R.REGLES


def test_repli_sur_name_si_name_constant_absent():
    """Mieux vaut un libellé qui n'appariera rien qu'un index vide.

    Un index vide ferait retomber tout le module sur les identifiants numériques de
    Faune-France, dont seuls deux sont vérifiés.
    """
    assert R.index_groupes([{"id": "6", "name": "Reptiles"}]) == {"6": "Reptiles"}


def test_un_groupe_sans_identifiant_est_ignore():
    assert R.index_groupes([{"name_constant": "TAXO_GROUP_BAT"}]) == {}


def test_le_groupe_taxonomique_est_conserve_dans_la_provenance():
    """Sans lui, analyser ce que la déduction a produit oblige à passer par TAXREF.

    Or la classification de TAXREF ne recoupe pas celle de Biolovision : les chiroptères
    y sont des mammifères, et rien n'y distingue un groupe fermé au compte d'un groupe
    sans règle. Compter par groupe ce qui a été déduit — et ce qui ne l'a pas été —
    suppose de savoir de quel groupe VisioNature vient chaque relevé.
    """
    import json
    from gn_module_connectors.sources.visionature import transform as T

    class ResolverFactice:
        def id(self, mnemonique, cd):
            return 1

        def defaut(self, mnemonique):
            return 1

    contexte = R.Contexte(index={"6": "TAXO_GROUP_REPTILIAN"})
    ligne = T.to_row(
        {"date": {"@ISO8601": "2026-09-01"},
         "species": {"@id": "1", "name": "Podarcis muralis", "taxonomy": "6"},
         "place": {"county": "09"}},
        {"@id": "1", "@uid": "7", "coord_lat": "42.8", "coord_lon": "1.9"},
        cd_nom=1, id_dataset=1, id_source=1, id_module=1, srid=2154,
        resolver=ResolverFactice(), instance="i", repro=contexte)

    assert json.loads(ligne["additional_data"])["groupe_taxo"] == "TAXO_GROUP_REPTILIAN"
