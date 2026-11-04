"""Médias d'une autre instance GeoNature, vers `gn_commons.t_medias`.

Les cas sont bâtis sur la forme d'une ligne de `gn_commons.t_medias` telle que GeoNature
la crée (`backend/geonature/migrations/data/core/commons.sql`) et sur les codes
`TYPE_MEDIA` du référentiel livré (Nomenclature-api-module,
`nomenclatures_inpn_data.sql`) : 2 Photo, 3 Page web, 4 PDF, 5 Audio, 6 Vidéo (fichier),
7 Youtube, 8 Dailymotion, 9 Vimeo. Comme le reste du connecteur, ils n'ont pas été
confrontés à un export réel : aucune vue standard ne publie de colonne `medias`.

`core/medias.py` et `core/purge.py` importent GeoNature : ils sont lus par analyse
statique, comme `commands.py` dans `test_commandes.py`.

    pytest tests/test_medias.py -q
"""

import json
import re
import sys
import uuid
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(RACINE))

from gn_module_connectors.sources.geonature import medias as M  # noqa: E402

MODULE = RACINE / "gn_module_connectors"
OBS = "8f2b6a1e-3c4d-4e5f-9a0b-1c2d3e4f5a6b"
BASE = "https://geonature.exemple.fr/api/media/attachments"


def media(**surcharges):
    return {"uuid": "0b6f1d2e-7a8c-4b9d-8e1f-2a3b4c5d6e7f", "type": "Photo",
            "url": None, "chemin": "117/12_IMG_0042.jpg", "titre": "Desman",
            "auteur": "C. Roy", "description": "Sous la berge", "public": True,
            **surcharges}


def lire(*medias, valeur=None):
    item = {"medias": list(medias) if valeur is None else valeur}
    return M.lire(item, OBS, BASE)


# ── Présence de la colonne ──────────────────────────────────────────────────

def test_colonne_absente_et_colonne_vide_ne_se_confondent_pas():
    # Absente : on ne sait rien, on ne retire rien. Vide : le producteur dit « aucun ».
    assert not M.present({"id_synthese": 1})
    assert M.present({"medias": None})
    assert M.brut({"medias": None}) == []


def test_tableau_json_en_texte_ou_deja_decode():
    attendu = M.brut({"medias": [media()]})
    assert M.brut({"medias": json.dumps([media()])}) == attendu
    assert M.brut({"medias": "pas du json"}) == []
    assert M.brut({"medias": media()}) == [media()]


# ── URL ─────────────────────────────────────────────────────────────────────

def test_fichier_televerse_complete_par_l_url_des_pieces_jointes():
    (m,), _ = lire(media())
    assert m["media_url"] == f"{BASE}/117/12_IMG_0042.jpg"


def test_url_publiee_prioritaire_sur_le_chemin():
    (m,), _ = lire(media(url="https://www.youtube.com/watch?v=abc"))
    assert m["media_url"] == "https://www.youtube.com/watch?v=abc"


def test_url_medias_deduite_de_l_instance_ou_imposee():
    assert M.url_medias_par_defaut({"url": "https://gn.fr/"}) == \
        "https://gn.fr/api/media/attachments"
    assert M.url_medias_par_defaut({"url": "https://gn.fr",
                                    "url_medias": "https://cdn.gn.fr/m/"}) == \
        "https://cdn.gn.fr/m"


def test_url_trop_longue_refusee_plutot_que_tronquee():
    medias, ecartes = lire(media(url="https://x.fr/" + "a" * 300))
    assert medias == [] and ecartes == {"url_trop_longue": 1}


def test_sans_url_ni_chemin_ecarte():
    medias, ecartes = lire(media(chemin=None))
    assert medias == [] and ecartes == {"sans_url": 1}


# ── Diffusion ───────────────────────────────────────────────────────────────

def test_media_non_public_jamais_relaye():
    for valeur in (False, "false", "f", 0, "0"):
        medias, ecartes = lire(media(public=valeur))
        assert medias == [] and ecartes == {"non_public": 1}, valeur


def test_public_par_defaut_quand_la_vue_ne_le_dit_pas():
    medias, _ = lire(media(public=None))
    assert len(medias) == 1


# ── Identifiants ────────────────────────────────────────────────────────────

def test_uuid_du_producteur_repris_tel_quel():
    (m,), _ = lire(media())
    assert m["unique_id_media"] == "0b6f1d2e-7a8c-4b9d-8e1f-2a3b4c5d6e7f"


def test_uuid_derive_stable_d_un_passage_a_l_autre():
    (a,), _ = lire(media(uuid=None))
    (b,), _ = lire(media(uuid="pas-un-uuid"))
    assert a["unique_id_media"] == b["unique_id_media"]
    uuid.UUID(a["unique_id_media"])
    (c,), _ = lire(media(uuid=None, chemin="117/autre.jpg"))
    assert c["unique_id_media"] != a["unique_id_media"]


def test_doublon_dans_une_observation_compte_une_fois():
    medias, ecartes = lire(media(), media())
    assert len(medias) == 1 and ecartes == {"doublon": 1}


# ── Types ───────────────────────────────────────────────────────────────────

def test_type_publie_conserve_pour_le_resolveur():
    (m,), _ = lire(media(type="5"))
    assert m["type"] == "5"


def test_type_devine_de_l_url():
    cas = {
        "https://x.fr/a.JPG": M.PHOTO, "https://x.fr/a.mp3": M.AUDIO,
        "https://x.fr/a.pdf": M.PDF, "https://x.fr/a.mp4": M.VIDEO,
        "https://youtu.be/abc": M.YOUTUBE, "https://www.youtube.com/watch?v=a": M.YOUTUBE,
        "https://vimeo.com/123": M.VIMEO, "https://www.dailymotion.com/v/x": M.DAILYMOTION,
        "https://inpn.mnhn.fr/espece/cd_nom/1958": M.PAGE_WEB,
    }
    for url, code in cas.items():
        assert M.type_devine(url) == code, url


def test_codes_type_media_du_referentiel_livre():
    assert (M.PHOTO, M.PAGE_WEB, M.PDF, M.AUDIO, M.VIDEO, M.YOUTUBE, M.DAILYMOTION,
            M.VIMEO) == ("2", "3", "4", "5", "6", "7", "8", "9")


# ── Longueurs de t_medias ───────────────────────────────────────────────────

def test_titre_et_auteur_coupes_aux_longueurs_de_t_medias():
    (m,), _ = lire(media(titre="t" * 400, auteur="a" * 200))
    assert len(m["title_fr"]) == 255 and len(m["author"]) == 100


def test_titre_par_defaut_le_nom_du_fichier():
    (m,), _ = lire(media(titre=None))
    assert m["title_fr"] == "12_IMG_0042.jpg"


# ── Écriture : lue par analyse statique ─────────────────────────────────────

CORE_MEDIAS = (MODULE / "core/medias.py").read_text(encoding="utf-8")
PURGE = (MODULE / "core/purge.py").read_text(encoding="utf-8")


def test_toute_ecriture_bornee_a_la_source_et_a_l_emplacement():
    for nom in ("_RETIRER", "_AJOUTER"):
        bloc = CORE_MEDIAS.split(f"{nom} = text(")[1].split('""")')[0]
        assert "id_table_location = :loc" in bloc, nom
        assert "s.id_source = :src" in bloc, nom


def test_cles_de_synchroniser_correspondent_aux_colonnes_du_unnest():
    bloc = CORE_MEDIAS.split("_UNNEST = ")[1].split('"""', 2)[1]
    lies = set(re.findall(r":(d_\w+)", bloc))
    construits = set(re.findall(r'"(d_\w+)"', CORE_MEDIAS.split("def synchroniser")[1]))
    assert lies == construits


def test_aucune_suppression_de_synthese_ne_contourne_les_medias():
    # Toute suppression de ligne passe par `_supprimer`, qui retire les médias dans le
    # même statement : sans clé étrangère, ils resteraient sinon orphelins.
    assert PURGE.count("DELETE FROM gn_synthese.synthese") == 1
    assert "DELETE FROM gn_commons.t_medias" in PURGE.split("_SUPPRIMER = ")[1]
    for module in MODULE.rglob("*.py"):
        if module.name == "purge.py" or "migrations" in module.parts:
            continue
        assert "DELETE FROM gn_synthese.synthese" not in module.read_text(
            encoding="utf-8"), module


def test_migration_en_tete_de_chaine():
    textes = {f: f.read_text(encoding="utf-8")
              for f in (MODULE / "migrations").glob("*.py") if f.name != "__init__.py"}
    revisions = {re.search(r'^revision = "(\w+)"', t, re.M).group(1) for t in textes.values()}
    parents = {m.group(1) for t in textes.values()
               if (m := re.search(r'^down_revision = "(\w+)"', t, re.M))}
    assert revisions - parents == {"c9d3e5f7a1b2"}
