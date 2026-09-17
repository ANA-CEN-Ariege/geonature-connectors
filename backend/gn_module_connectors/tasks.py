"""Tâches planifiées du module.

L'ordonnanceur n'est pas à installer : GeoNature fait déjà tourner un worker Celery avec
`--beat`, et `geonature.celery_app` importe automatiquement l'entry point `tasks` de
chaque module. Il suffit donc de déclarer la tâche périodique — l'adoptant n'a rien à
configurer au niveau système, ce qui était la condition pour que le module soit
installable par un administrateur fonctionnel.

Cadence : hebdomadaire par défaut. Les producteurs français republient rarement — mesuré
sur l'Ariège, la dernière modification déclarée remonte à 901 jours pour Faune Occitanie
et SICEN Occitanie, 293 à 323 jours pour l'INPN flore, eBird et Pl@ntNet. Seul
iNaturalist bouge à l'échelle de la dizaine de jours. Une cadence plus fine ne
rapporterait rien et coûterait des heures de calcul pour zéro changement.
"""

from celery.schedules import crontab
from celery.utils.log import get_task_logger

from geonature.utils.celery import celery_app
from geonature.utils.config import config

logger = get_task_logger(__name__)

# Nom du verrou en base. Un import peut durer des heures : sans verrou, une exécution
# planifiée pourrait démarrer alors que la précédente tourne encore, et les deux se
# disputeraient les mêmes lignes.
VERROU = 84_101_001


def _conf() -> dict:
    return (config.get("CONNECTORS") or {}).get("gbif", {})


@celery_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    cfg = _conf().get("schedule") or {}
    if not cfg.get("enabled"):
        return
    ct = (cfg.get("crontab") or "").split()
    if len(ct) != 5:
        logger.warning(
            "CONNECTORS : crontab « %s » invalide, planification ignorée. "
            "Format attendu : minute heure jour_du_mois mois jour_de_semaine",
            cfg.get("crontab"),
        )
        return
    minute, heure, jour_mois, mois, jour_semaine = ct
    sender.add_periodic_task(
        crontab(minute=minute, hour=heure, day_of_month=jour_mois,
                month_of_year=mois, day_of_week=jour_semaine),
        moissonner_gbif.s(),
        name="connectors: moissonnage GBIF",
    )
    logger.info("CONNECTORS : moissonnage GBIF planifié (%s)", cfg.get("crontab"))


@celery_app.task(bind=True)
def moissonner_gbif(self):
    """Moissonne le périmètre configuré, jeu de données par jeu de données.

    Le découpage par jeu n'est pas cosmétique : il évite qu'un échec sur une source
    emporte les autres, et il libère le worker régulièrement plutôt que de l'occuper
    pendant des heures d'affilée.
    """
    from sqlalchemy import text
    from geonature.utils.env import db
    from click.testing import CliRunner
    from .commands import gbif_import
    from .sources.gbif import metadata as gbif_meta

    cfg = _conf()
    if not cfg.get("enabled", True):
        logger.info("CONNECTORS : connecteur GBIF désactivé, rien à faire.")
        return

    # Verrou consultatif PostgreSQL : libéré automatiquement si le worker meurt, ce
    # qu'un drapeau en table ne garantirait pas.
    obtenu = db.session.execute(
        text("SELECT pg_try_advisory_lock(:v)"), {"v": VERROU}
    ).scalar()
    if not obtenu:
        logger.warning("CONNECTORS : un moissonnage est déjà en cours, exécution ignorée.")
        return

    try:
        filtres = {"country": cfg.get("country", "FR"),
                   "occurrenceStatus": cfg.get("occurrence_status", "PRESENT")}
        if cfg.get("gadm_gid"):
            filtres["gadmGid"] = cfg["gadm_gid"]

        cles = [d["key"] for d in gbif_meta.list_dataset_keys(filtres)]
        exclus = set(cfg.get("exclude_dataset_keys") or [])
        cles = [k for k in cles if k not in exclus]
        logger.info("CONNECTORS : %s jeu(x) à traiter.", len(cles))

        runner = CliRunner()
        ok = erreurs = 0
        for cle in cles:
            resultat = runner.invoke(gbif_import, ["--jeu", cle])
            if resultat.exit_code == 0:
                ok += 1
                logger.info("CONNECTORS : %s — %s", cle, resultat.output.strip())
            else:
                erreurs += 1
                logger.error("CONNECTORS : échec sur %s — %s", cle, resultat.output[-500:])
        logger.info("CONNECTORS : moissonnage terminé, %s jeu(x) traité(s), %s en erreur.",
                    ok, erreurs)
    finally:
        db.session.execute(text("SELECT pg_advisory_unlock(:v)"), {"v": VERROU})
        db.session.commit()
