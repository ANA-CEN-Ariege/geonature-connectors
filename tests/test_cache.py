"""Cache disque des référentiels.

Un cache est une optimisation : il n'a jamais le droit de faire échouer ce qu'il
accélère, ni de servir une donnée qui n'est pas la bonne. Ces tests portent sur ces deux
exigences, plus la protection du fichier — le référentiel des observateurs contient des
noms de personnes.

    pytest tests/ -q
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from gn_module_connectors.core import cache  # noqa: E402

INSTANCE = "https://www.faune-occitanie.org"


def test_desactive_par_defaut(tmp_path):
    """0 heure = pas de cache du tout, ni lecture ni écriture.

    C'est le défaut, et il est délibéré : activer un cache de noms de personnes ne doit
    jamais résulter d'un oubli de configuration.
    """
    assert cache.enregistrer("especes", INSTANCE, [1], 0, tmp_path) is None
    assert not list(tmp_path.glob("*"))
    assert cache.charger("especes", INSTANCE, 0, tmp_path) is None


def test_aller_retour(tmp_path):
    cache.enregistrer("especes", INSTANCE, [{"id": "1"}], 24, tmp_path)
    assert cache.charger("especes", INSTANCE, 24, tmp_path) == [{"id": "1"}]


def test_le_fichier_nest_lisible_que_par_son_proprietaire(tmp_path):
    """Le référentiel des observateurs porte des noms réels : 0600, pas davantage."""
    fichier = cache.enregistrer("observateurs", INSTANCE, [{"name": "Untel"}], 24, tmp_path)
    assert fichier.stat().st_mode & 0o777 == 0o600


def test_changer_dinstance_ne_sert_pas_lautre_referentiel(tmp_path):
    """Faune-France et Faune-Occitanie n'ont ni les mêmes espèces ni les mêmes observateurs.

    Le cas s'est présenté : l'instance a été changée en cours de mise au point. Servir
    le référentiel de l'ancienne aurait produit des correspondances fausses en silence.
    """
    cache.enregistrer("especes", INSTANCE, [{"id": "1"}], 24, tmp_path)
    assert cache.charger("especes", "https://www.faune.fr", 24, tmp_path) is None


def test_deux_referentiels_ne_se_marchent_pas_dessus(tmp_path):
    cache.enregistrer("especes", INSTANCE, ["e"], 24, tmp_path)
    cache.enregistrer("observateurs", INSTANCE, ["o"], 24, tmp_path)
    assert cache.charger("especes", INSTANCE, 24, tmp_path) == ["e"]
    assert cache.charger("observateurs", INSTANCE, 24, tmp_path) == ["o"]


def test_un_cache_perime_nest_pas_servi(tmp_path):
    fichier = cache.enregistrer("especes", INSTANCE, [{"id": "1"}], 24, tmp_path)
    paquet = json.loads(fichier.read_text(encoding="utf-8"))
    paquet["horodatage"] -= 25 * 3600
    fichier.write_text(json.dumps(paquet), encoding="utf-8")

    assert cache.charger("especes", INSTANCE, 24, tmp_path) is None
    assert cache.charger("especes", INSTANCE, 48, tmp_path) == [{"id": "1"}]


def test_un_cache_corrompu_vaut_absence_de_cache(tmp_path):
    """Une écriture interrompue ne doit pas faire échouer le moissonnage."""
    fichier = cache.enregistrer("especes", INSTANCE, [{"id": "1"}], 24, tmp_path)
    fichier.write_text("{ceci n'est pas du json", encoding="utf-8")
    assert cache.charger("especes", INSTANCE, 24, tmp_path) is None


def test_un_paquet_sans_horodatage_vaut_absence_de_cache(tmp_path):
    fichier = cache.enregistrer("especes", INSTANCE, [{"id": "1"}], 24, tmp_path)
    fichier.write_text(json.dumps({"contenu": [1]}), encoding="utf-8")
    assert cache.charger("especes", INSTANCE, 24, tmp_path) is None


def test_un_dossier_inexistant_ne_fait_pas_echouer_la_lecture(tmp_path):
    assert cache.charger("especes", INSTANCE, 24, tmp_path / "absent") is None


def test_vidage(tmp_path):
    cache.enregistrer("especes", INSTANCE, ["e"], 24, tmp_path)
    cache.enregistrer("observateurs", INSTANCE, ["o"], 24, tmp_path)
    assert cache.vider(tmp_path) == 2
    assert cache.charger("especes", INSTANCE, 24, tmp_path) is None
    assert cache.vider(tmp_path / "absent") == 0
