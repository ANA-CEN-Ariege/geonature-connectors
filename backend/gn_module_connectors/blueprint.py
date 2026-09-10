"""Blueprint du module : les commandes, et le renvoi vers la donnée source.

Le module n'a pas de frontend (`d5f1a83c7e29_pas_de_frontend`). Ce blueprint ne sert
donc qu'à deux choses : exposer les commandes au `FlaskGroup` de GeoNature, et offrir
la redirection ci-dessous.
"""

from flask import Blueprint, abort, redirect

from .commands import connectors_cli

blueprint = Blueprint("connectors", __name__)

# Les commandes déclarées sur le blueprint sont exposées par le FlaskGroup de GeoNature :
# `geonature connectors <commande>`.
for cmd in connectors_cli:
    blueprint.cli.add_command(cmd)


@blueprint.route("/visionature/<id_sighting>", methods=["GET"])
def voir_dans_visionature(id_sighting):
    """Renvoie vers la fiche d'une observation sur le portail VisioNature d'origine.

    Raison d'être : GeoNature construit le lien « voir la donnée source » en insérant
    systématiquement un séparateur — `url_source + '/' + entity_source_pk_value`. Une
    URL de retour en chaîne de requête, comme celle de Biolovision
    (`…/index.php?m_id=54&id=`), devient donc `…&id=/176983543`, illisible.

    Plutôt que de détourner `entity_source_pk_value` pour y loger un fragment d'URL, on
    donne à GeoNature ce qu'il sait produire : un chemin terminé par l'identifiant. La
    colonne garde l'identifiant brut, `entity_source_pk_field` reste exact, et le cœur
    n'a pas à être modifié.
    """
    from geonature.utils.config import config as gn_config

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    instance = str(cfg.get("url") or "").rstrip("/")
    if not instance:
        abort(404, "Connecteur VisioNature non configuré.")

    # L'identifiant vient de la Synthèse, mais il transite par une URL : on le restreint
    # à ce qu'un identifiant Biolovision peut être, plutôt que de le concaténer tel quel
    # dans une redirection.
    cle = str(id_sighting).strip()
    if not cle.isdigit():
        abort(400, "Identifiant d'observation invalide.")

    return redirect(f"{instance}/index.php?m_id=54&id={cle}", code=302)


@blueprint.route("/dbchiro/<id_sighting>", methods=["GET"])
def voir_dans_dbchiro(id_sighting):
    """Renvoie vers la fiche d'une observation sur l'instance dbChiro d'origine.

    Même mécanique que pour VisioNature, pour une raison voisine mais distincte : le
    permalien dbChiro est `/sighting/<id>/detail`, c'est-à-dire que l'identifiant est
    **au milieu** du chemin. La concaténation du cœur — `url_source + '/' +
    entity_source_pk_value` — ne peut donc rien produire de valide, quel que soit
    l'`url_source` choisi. La redirection est ici le seul moyen, pas seulement le plus
    propre.
    """
    from geonature.utils.config import config as gn_config

    cfg = (gn_config.get("CONNECTORS") or {}).get("dbchiro", {})
    instance = str(cfg.get("url") or "").rstrip("/")
    if not instance:
        abort(404, "Connecteur dbChiro non configuré.")

    cle = str(id_sighting).strip()
    if not cle.isdigit():
        abort(400, "Identifiant d'observation invalide.")

    return redirect(f"{instance}/sighting/{cle}/detail", code=302)


@blueprint.route("/geonature/<id_synthese>", methods=["GET"])
def voir_dans_geonature(id_synthese):
    """Renvoie vers la fiche d'une observation sur l'instance GeoNature d'origine.

    Troisième variante du même problème, et la plus nette : le permalien d'une observation
    GeoNature est `<url>/#/synthese/occurrence/<id_synthese>`, c'est-à-dire que tout se
    joue **après un fragment**. Loger ce fragment dans `url_source` ferait que le
    séparateur et l'identifiant ajoutés par le cœur s'y perdraient — un fragment n'est
    jamais envoyé au serveur. La redirection est donc le seul moyen, comme pour dbChiro.
    """
    from geonature.utils.config import config as gn_config

    cfg = (gn_config.get("CONNECTORS") or {}).get("geonature", {})
    instance = str(cfg.get("url") or "").rstrip("/")
    if not instance:
        abort(404, "Connecteur GeoNature non configuré.")

    cle = str(id_synthese).strip()
    if not cle.isdigit():
        abort(400, "Identifiant d'observation invalide.")

    return redirect(f"{instance}/#/synthese/occurrence/{cle}", code=302)
