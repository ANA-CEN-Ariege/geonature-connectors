"""Commandes CLI du module, exposées sous `geonature connectors ...`."""

import click
from sqlalchemy import func, select

from pathlib import Path

from sqlalchemy import text as db_text
from geonature.utils.env import db


@click.command("statut")
def statut():
    """Diagnostic : vérifie que le module voit bien GeoNature et son référentiel."""
    from geonature.core.gn_synthese.models import Synthese, TSources

    click.secho("Module CONNECTORS", fg="green", bold=True)

    nb_synthese = db.session.scalar(select(func.count()).select_from(Synthese))
    click.echo(f"  observations en Synthèse : {nb_synthese}")

    sources = db.session.execute(
        select(TSources.name_source, TSources.url_source)
    ).all()
    click.echo(f"  sources déclarées        : {len(sources)}")
    for name, url in sources:
        click.echo(f"    - {name} ({url or 'sans url_source'})")

    # La résolution taxonomique repose sur les liens GBIF de TAXREF : sans eux, aucune
    # occurrence n'est importable. Le vérifier ici évite un import qui rejette tout.
    nb_liens = db.session.scalar(
        db.text("select count(*) from taxonomie.taxref_liens where ct_name ilike 'GBIF'")
    )
    nb_taxref = db.session.scalar(db.text("select count(*) from taxonomie.taxref"))
    couverture = (100.0 * nb_liens / nb_taxref) if nb_taxref else 0
    couleur = "green" if couverture > 50 else "red"
    click.secho(
        f"  liens TAXREF <-> GBIF    : {nb_liens} / {nb_taxref} ({couverture:.1f} %)",
        fg=couleur,
    )
    if not nb_liens:
        click.secho(
            "    ⚠ Aucun lien GBIF dans taxonomie.taxref_liens : la résolution des\n"
            "      cd_nom échouera pour toutes les occurrences. Vérifier l'import du\n"
            "      fichier TAXREF_LIENS.txt dans TaxHub.",
            fg="red",
        )


@click.command("gbif-synchroniser-jeux")
@click.option("--perimetre", "gadm_gid", default=None, help="Périmètre GADM (défaut : configuration).")
@click.option("--pays", "country", default=None, help="Pays (défaut : configuration).")
@click.option("--jeu", "dataset_keys", multiple=True,
              help="Limiter à ces jeux ; sinon, tous ceux du périmètre.")
@click.option("--licence", "licenses", multiple=True,
              help="Licences retenues (défaut : configuration).")
@click.option("--max-jeux", "limit", default=0, help="Ne traiter que les N plus gros jeux (0 = tous).")
@click.option("--ignorer-exclusions", "ignore_exclusions", is_flag=True,
              help="Créer un JDD même pour les jeux exclus par la configuration.")
@click.option("--dry-run", is_flag=True, help="N'écrit rien, affiche ce qui serait fait.")
def gbif_synchroniser_jeux(gadm_gid, country, dataset_keys, licenses, limit,
                       ignore_exclusions, dry_run):
    """Crée ou met à jour un JDD GeoNature par jeu de données GBIF du périmètre."""
    from geonature.utils.config import config as gn_config
    from .core import datasets as ds_core
    from .sources.gbif import metadata as gbif_meta
    from .migrations.b2d7e9f31a04_cadre_acquisition_gbif import CA_UUID

    # Mêmes défauts que gbif-import : sans cela, la commande créerait un JDD pour chaque
    # jeu du périmètre — y compris ceux que la configuration exclut, et, faute de
    # gadm_gid, pour la France entière.
    cfg_gbif = (gn_config.get("CONNECTORS") or {}).get("gbif", {})
    def choisi(valeur_cli, cle, defaut=None):
        return valeur_cli if valeur_cli not in (None, (), "") else cfg_gbif.get(cle, defaut)

    gadm_gid = choisi(gadm_gid, "gadm_gid", "")
    country = choisi(country, "country", "FR")
    licenses = tuple(choisi(licenses, "licenses", ("CC0_1_0", "CC_BY_4_0")))
    statut = cfg_gbif.get("occurrence_status", "PRESENT")

    # Exclusions taxonomiques : celles sans `dataset` valent partout, les autres ne
    # s'appliquent qu'au jeu désigné — par son datasetKey GBIF ou par l'unique_dataset_id
    # du JDD GeoNature, les deux étant acceptés pour éviter d'avoir à les traduire.
    exclusions_taxons = cfg_gbif.get("exclude_taxa") or []
    taxons_globaux = {
        int(t) for e in exclusions_taxons if not e.get("dataset")
        for t in (e.get("taxon_keys") or [])
    }
    exclus_cles = set() if ignore_exclusions else set(cfg_gbif.get("exclude_dataset_keys") or [])
    exclus_termes = [] if ignore_exclusions else [
        t.lower() for t in (cfg_gbif.get("exclude_dataset_terms") or [])]
    exclus_orgs = set() if ignore_exclusions else set(cfg_gbif.get("exclude_publishing_orgs") or [])

    if not gadm_gid:
        click.secho(
            "  ⚠ Aucun gadm_gid : le périmètre est le pays entier. Renseignez "
            "`gadm_gid` dans la configuration, ou passez --gadm-gid.", fg="yellow")

    af = ds_core.get_acquisition_framework(CA_UUID)
    click.secho(f"Cadre d'acquisition : {af.acquisition_framework_name} "
                f"(id={af.id_acquisition_framework})", fg="green")

    if dataset_keys:
        cles = [{"key": k, "count": None} for k in dataset_keys]
    else:
        filtres = {"country": country, "occurrenceStatus": statut}
        if gadm_gid:
            filtres["gadmGid"] = gadm_gid
        click.echo(f"Recherche des jeux GBIF ({filtres})...")
        cles = gbif_meta.list_dataset_keys(filtres)
    if limit:
        cles = cles[:limit]
    click.echo(f"  {len(cles)} jeu(x) à examiner.")

    licences_ok = {l.upper() for l in licenses}
    cree = maj = ignore = 0
    orgs: dict[str, str] = {}

    ignore_excl = 0
    for i, item in enumerate(cles, 1):
        if item["key"] in exclus_cles:
            # Afficher l'exclusion plutôt que la sauter en silence : un trou dans la
            # numérotation ressemble à un dysfonctionnement.
            ignore_excl += 1
            click.echo(f"  [{i}/{len(cles)}] – {item['key']} — exclu par configuration "
                       f"(datasetKey, {item['count'] or '?'} occ.)")
            continue
        try:
            meta = gbif_meta.fetch_dataset(item["key"])
        except Exception as e:
            click.secho(f"  [{i}/{len(cles)}] ✗ {item['key']} — métadonnées illisibles "
                        f"({type(e).__name__})", fg="red")
            continue
        titre_bas = (meta["title"] or "").lower()
        terme = next((t for t in exclus_termes if t in titre_bas), None)
        if terme or meta["publishing_org_key"] in exclus_orgs:
            ignore_excl += 1
            motif = f"terme « {terme} »" if terme else "publicateur exclu"
            click.echo(f"  [{i}/{len(cles)}] – {meta['title'][:46]} — {motif}")
            continue
        lic = meta["license"]
        if lic not in licences_ok:
            ignore += 1
            click.echo(f"  [{i}/{len(cles)}] ✗ {meta['title'][:52]} — licence {lic or 'inconnue'}")
            continue

        if meta["publishing_org_key"] not in orgs:
            orgs[meta["publishing_org_key"]] = gbif_meta.fetch_organization(meta["publishing_org_key"])
        producteur = orgs[meta["publishing_org_key"]]

        # La citation officielle GBIF EST la chaîne d'attribution exigée par la licence :
        # la porter dans dataset_desc satisfait l'obligation par la métadonnée elle-même.
        desc = "\n\n".join(x for x in (
            meta["citation"],
            f"Producteur : {producteur}" if producteur else "",
            f"Licence : {lic}",
            f"DOI du jeu : https://doi.org/{meta['doi']}" if meta["doi"] else "",
            f"Source : {meta['url']}",
            meta["description"],
        ) if x)

        if dry_run:
            click.echo(f"  [{i}/{len(cles)}] → {meta['title'][:56]} ({lic})")
            continue

        jdd, est_nouveau = ds_core.upsert_dataset(
            source="GBIF", cle=meta["key"], licence=lic,
            nom=meta["title"], description=desc,
            id_acquisition_framework=af.id_acquisition_framework,
        )
        cree += est_nouveau
        maj += (not est_nouveau)
        click.echo(f"  [{i}/{len(cles)}] {'+' if est_nouveau else '~'} {meta['title'][:56]} ({lic})")

    if dry_run:
        click.secho(f"\nDry-run : {len(cles) - ignore - ignore_excl} JDD seraient créés ou "
                    f"mis à jour, {ignore} ignorés (licence), "
                    f"{ignore_excl} exclus par la configuration.", fg="yellow")
        return
    db.session.commit()
    click.secho(f"\nTerminé : {cree} créé(s), {maj} mis à jour, {ignore} ignoré(s) (licence), "
                f"{ignore_excl} exclu(s) par la configuration.", fg="green")



def _description_jdd(meta: dict, producteur: str) -> str:
    """Description d'un JDD à partir des métadonnées GBIF.

    La citation officielle GBIF EST la chaîne d'attribution qu'exigent CC BY et CC BY-NC :
    la porter ici satisfait l'obligation par la métadonnée elle-même, visible dans le
    module Métadonnées, et non par un champ JSON qu'aucune interface n'affiche.
    """
    lic = meta.get("license") or "licence inconnue"
    morceaux = (
        meta.get("citation") or "",
        f"Producteur : {producteur}" if producteur else "",
        f"Licence : {lic}",
        f"DOI du jeu : https://doi.org/{meta['doi']}" if meta.get("doi") else "",
        f"Source : {meta.get('url', '')}",
        meta.get("description") or "",
    )
    return "\n\n".join(x for x in morceaux if x)


def creer_jdd(meta, licence, uid, af, orgs_cache):
    """Crée le JDD d'un jeu GBIF, à la demande.

    Appelée seulement quand des occurrences ont survécu aux filtres : un jeu maillé ou
    entièrement écarté ne doit pas laisser de JDD vide dans le module Métadonnées.
    """
    from .core import datasets as ds_core
    from .sources.gbif import metadata as gbif_meta

    org = meta.get("publishing_org_key") or ""
    if org not in orgs_cache:
        orgs_cache[org] = gbif_meta.fetch_organization(org)
    jdd, cree = ds_core.upsert_dataset(
        source="GBIF", cle=meta["key"], licence=licence,
        nom=meta["title"], description=_description_jdd(meta, orgs_cache[org]),
        id_acquisition_framework=af.id_acquisition_framework,
    )
    db.session.flush()
    if cree:
        click.secho(f"  + JDD créé : {jdd.id_dataset}", fg="green")
    return jdd


@click.command("gbif-import")
@click.option("--jeu", "dataset_keys", multiple=True,
              help="Jeux GBIF à importer (répétable). Sans cette option, tous les jeux "
                   "du périmètre sont examinés, moins les exclusions de la configuration. "
                   "Le JDD doit exister : lancer gbif-synchroniser-jeux au préalable.")
@click.option("--ecarter-jeux-maille/--garder-jeux-maille", "skip_gridded", default=None,
              help="Écarter les jeux publiés à la maille (défaut : configuration).")
@click.option("--forcer", "force", is_flag=True,
              help="Moissonner même les jeux inchangés depuis le dernier passage. "
                   "Indispensable après une évolution du mapping : GBIF n'a alors rien "
                   "modifié, mais les données doivent tout de même être réécrites.")
@click.option("--perimetre", "gadm_gid", default=None, help="Périmètre GADM (défaut : configuration).")
@click.option("--pays", "country", default=None, help="Pays (défaut : configuration).")
@click.option("--licence", "licenses", multiple=True,
              help="Licences retenues (défaut : configuration).")
@click.option("--max-resultats", "max_results", default=0, help="Plafonne le nombre d'occurrences (0 = tout).")
@click.option("--lot", "batch_size", default=None, type=int,
              help="Taille des lots. Les triggers de synthese sont FOR EACH STATEMENT : "
                   "leur coût ne s'amortit qu'en insérant par paquets.")
@click.option("--doi", "download_doi", default="", help="DOI du téléchargement GBIF, s'il y en a un.")
@click.option("--incertitude-max", "max_uncertainty", default=None, type=int,
              help="Incertitude géographique maximale en mètres (0 = pas de filtre). "
                   "Repère sur l'Ariège : <=1100 m ne retient que 39,9 %% des occurrences, "
                   "<=5000 m en retient 59,7 %%.")
@click.option("--garder-incertitude-inconnue/--ecarter-incertitude-inconnue", "keep_unknown_uncertainty", default=None,
              help="Sort des occurrences sans incertitude déclarée — 34,9 %% du corpus "
                   "ariégeois. Les garder revient à accepter une précision inconnue.")
@click.option("--garder-specimens", "keep_specimens", is_flag=True,
              help="Conserver les FOSSIL_SPECIMEN et LIVING_SPECIMEN. Par défaut ils sont "
                   "écartés : ce ne sont pas des observations naturalistes et leurs "
                   "coordonnées ne désignent pas un lieu d'observation (banques de "
                   "semences, collections de muséum).")
@click.option("--repli-taxref/--sans-repli-taxref", "taxref_fallback", default=True, show_default=True,
              help="Pour les taxons absents de taxref_liens, interroger le référentiel "
                   "TAXREF publié sur GBIF. Un appel réseau par taxon inconnu, mis en cache.")
@click.option("--dry-run", is_flag=True)
def gbif_import(dataset_keys, gadm_gid, country, licenses, max_results,
                batch_size, download_doi, max_uncertainty, keep_unknown_uncertainty,
                keep_specimens, taxref_fallback, skip_gridded, force, dry_run):
    """Importe les occurrences GBIF dans la Synthèse."""
    from .core import (report as report_core, synthese as syn_core,
                       datasets as ds_core, nomenclatures as nomen_core)
    from .sources.gbif import nomenclatures as gbif_nomen
    from .sources.gbif import api as gbif_api, metadata as gbif_meta
    from .sources.gbif import taxonomy as gbif_taxo, transform as gbif_tr
    from .migrations.b2d7e9f31a04_cadre_acquisition_gbif import CA_UUID
    from .migrations.c4e8a2b95d16_source_gbif import SOURCE_NAME
    from types import SimpleNamespace
    from datetime import datetime
    from geonature.utils.config import config as gn_config
    from .sources.gbif import griddedness as gbif_grid

    # La configuration du module fournit les valeurs par défaut ; les options de la
    # ligne de commande les surchargent lorsqu'elles sont fournies.
    cfg_gbif = (gn_config.get("CONNECTORS") or {}).get("gbif", {})
    def choisi(valeur_cli, cle, defaut=None):
        return valeur_cli if valeur_cli not in (None, (), "") else cfg_gbif.get(cle, defaut)

    gadm_gid = choisi(gadm_gid, "gadm_gid", "")
    country = choisi(country, "country", "FR")
    licenses = tuple(choisi(licenses, "licenses", ("CC0_1_0", "CC_BY_4_0")))
    batch_size = choisi(batch_size, "batch_size", 1000)
    max_uncertainty = choisi(max_uncertainty, "coordinate_uncertainty_max", None)
    if keep_unknown_uncertainty is None:
        keep_unknown_uncertainty = cfg_gbif.get("keep_unknown_uncertainty", True)
    if skip_gridded is None:
        skip_gridded = cfg_gbif.get("skip_gridded_datasets", True)
    download_doi = download_doi or cfg_gbif.get("download_doi", "")
    exclus_cles = set(cfg_gbif.get("exclude_dataset_keys") or [])
    exclus_termes = [t.lower() for t in (cfg_gbif.get("exclude_dataset_terms") or [])]
    exclus_orgs = set(cfg_gbif.get("exclude_publishing_orgs") or [])
    statut = cfg_gbif.get("occurrence_status", "PRESENT")

    # Exclusions taxonomiques : celles sans `dataset` valent partout, les autres ne
    # s'appliquent qu'au jeu désigné — par son datasetKey GBIF ou par l'unique_dataset_id
    # du JDD GeoNature, les deux étant acceptés pour éviter d'avoir à les traduire.
    exclusions_taxons = cfg_gbif.get("exclude_taxa") or []
    taxons_globaux = {
        int(t) for e in exclusions_taxons if not e.get("dataset")
        for t in (e.get("taxon_keys") or [])
    }
    cfg_valid = (gn_config.get("CONNECTORS") or {}).get("validation", {})
    statut_validation = cfg_valid.get("status") if cfg_valid.get("enabled") else None
    if statut_validation:
        click.echo(f"  pré-validation : « {statut_validation} »")

    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_module = syn_core.get_module_id("CONNECTORS")
    srid = syn_core.local_srid()
    af = ds_core.get_acquisition_framework(CA_UUID)
    # Version du référentiel sous lequel `cd_nom` est résolu : la même pour les deux
    # connecteurs, puisque c'est celle de l'instance qui reçoit la donnée.
    v_taxref = syn_core.version_taxref()
    click.secho(f"source={id_source} module={id_module} srid={srid} "
                f"cadre={af.id_acquisition_framework} taxref={v_taxref or 'inconnu'}",
                fg="green")
    if max_uncertainty:
        politique = "conservées" if keep_unknown_uncertainty else "écartées"
        click.echo(f"  incertitude max : {max_uncertainty} m "
                   f"(sans incertitude déclarée : {politique})")

    click.echo("Chargement des correspondances taxonKey -> cd_nom...")
    index = gbif_taxo.load_index()
    click.echo(f"  {len(index)} correspondances.")
    if not index:
        raise click.ClickException(
            "taxonomie.taxref_liens ne contient aucun lien GBIF : toutes les occurrences "
            "seraient rejetées. Vérifier l'import de TAXREF_LIENS.txt dans TaxHub.")

    rejets = report_core.Rejects()
    resolver = nomen_core.Resolver()
    orgs_cache: dict = {}
    cache_gbif: dict = {} if taxref_fallback else None
    total_lus = total_ecrits = total_maj = 0

    # Sans --dataset-key, on examine tout le périmètre configuré.
    if not dataset_keys:
        filtres_perimetre = {"country": country, "occurrenceStatus": statut}
        if gadm_gid:
            filtres_perimetre["gadmGid"] = gadm_gid
        click.echo(f"Recherche des jeux du périmètre {filtres_perimetre}...")
        dataset_keys = [d["key"] for d in gbif_meta.list_dataset_keys(filtres_perimetre)]
        click.echo(f"  {len(dataset_keys)} jeu(x) trouvé(s).")

    for cle in dataset_keys:
        if cle in exclus_cles:
            click.secho(f"– {cle} — exclu par configuration (datasetKey)", fg="yellow")
            continue
        # Une clé invalide ou un GBIF indisponible ne doit pas interrompre l'import des
        # autres jeux ni remonter une trace brute à l'utilisateur.
        try:
            meta = gbif_meta.fetch_dataset(cle)
        except Exception as e:
            click.secho(f"✗ {cle} — métadonnées GBIF illisibles ({type(e).__name__}), ignoré",
                        fg="red")
            rejets.add("metadonnees_illisibles", cle, "", str(e)[:200])
            continue
        lic = meta["license"]
        if lic not in {l.upper() for l in licenses}:
            click.secho(f"✗ {meta['title'][:50]} — licence {lic or 'inconnue'}, ignoré", fg="yellow")
            continue
        titre_bas = (meta["title"] or "").lower()
        terme = next((t for t in exclus_termes if t in titre_bas), None)
        if terme:
            click.secho(f"– {meta['title'][:52]} — exclu par le terme « {terme} »", fg="yellow")
            continue
        if meta["publishing_org_key"] in exclus_orgs:
            click.secho(f"– {meta['title'][:52]} — publicateur exclu", fg="yellow")
            continue

        if skip_gridded:
            verdict = gbif_grid.inspect(
                cle, {"gadmGid": gadm_gid, "occurrenceStatus": statut} if gadm_gid
                else {"country": country, "occurrenceStatus": statut})
            if verdict["verdict"] == "maille":
                click.secho(f"– {meta['title'][:52]} — {gbif_grid.explique(verdict)}", fg="yellow")
                rejets.add("jeu_maille", cle, meta["title"], gbif_grid.explique(verdict))
                continue
            if verdict["verdict"] == "suspect":
                click.secho(f"? {meta['title'][:52]} — {gbif_grid.explique(verdict)}", fg="yellow")

        uid = ds_core.dataset_uuid("GBIF", cle, lic)
        from geonature.core.gn_meta.models import TDatasets
        from sqlalchemy import select as sa_select
        # Le JDD n'est pas créé ici : il le sera seulement si des occurrences survivent
        # aux filtres. Créer d'abord reviendrait à peupler le module Métadonnées de JDD
        # vides pour les jeux maillés ou entièrement écartés.
        jdd = db.session.scalar(sa_select(TDatasets).where(TDatasets.unique_dataset_id == uid))

        # Court-circuit : si GBIF n'a pas touché au jeu depuis notre dernier passage,
        # inutile d'en relire les occurrences. Mesuré sur un périmètre départemental,
        # aucun des 60 plus gros jeux n'avait bougé en sept jours — le moissonnage
        # hebdomadaire se réduit alors à une requête de métadonnées par jeu.
        if not force and jdd is not None and meta.get("modified"):
            dernier = ds_core.dernier_moissonnage(jdd.id_dataset, id_source)
            if dernier is not None:
                from datetime import timezone
                try:
                    modif = datetime.fromisoformat(meta["modified"].replace("Z", "+00:00"))
                except ValueError:
                    modif = None
                if modif is not None:
                    ref = dernier if dernier.tzinfo else dernier.replace(tzinfo=timezone.utc)
                    if modif <= ref:
                        click.secho(
                            f"= {meta['title'][:52]} — inchangé depuis le "
                            f"{ref.date()} (jeu modifié le {modif.date()})", fg="cyan")
                        continue

        etiquette = f"JDD {jdd.id_dataset}" if jdd is not None else "JDD à créer"
        click.secho(f"\n{meta['title'][:64]}  ({etiquette}, {lic})", bold=True)
        cfg = SimpleNamespace(country=country, state_province="", has_coordinate=True,
                              has_geospatial_issue=False, max_results=max_results or None,
                              extra={"datasetKey": cle, **({"gadmGid": gadm_gid} if gadm_gid else {})})
        fcfg = SimpleNamespace(exclude_dataset_terms=[], exclude_dataset_keys=[],
                               include_dataset_keys=[], include_observers=[],
                               date_min="", date_max="",
                               coordinate_uncertainty_max=max_uncertainty or None,
                               keep_unknown_uncertainty=keep_unknown_uncertainty,
                               licenses=list(licenses), exclude_taxon_keys=set())
        # Les absences GBIF (occurrenceStatus=ABSENT) deviendraient des présences fausses
        # en Synthèse : un seul jeu ariégeois en compte 116 799.
        cfg.extra["occurrenceStatus"] = statut

        taxons_exclus = set(taxons_globaux)
        for e in exclusions_taxons:
            reference = str(e.get("dataset") or "")
            if reference and reference in (cle, str(uid)):
                taxons_exclus |= {int(t) for t in (e.get("taxon_keys") or [])}
        fcfg.exclude_taxon_keys = taxons_exclus
        if taxons_exclus:
            click.echo(f"  taxons exclus : {sorted(taxons_exclus)}")

        occurrences = gbif_api.fetch_par_tranches(cfg, fcfg, journal=click.echo)
        occurrences = gbif_api.apply_local_filters(occurrences, fcfg, rejets)
        total_lus += len(occurrences)

        lot, ecrits, maj = [], 0, 0
        for occ in occurrences:
            if not keep_specimens and occ.get("basisOfRecord") in gbif_nomen.BASIS_OF_RECORD_EXCLUS:
                rejets.add("hors_perimetre", occ.get("gbifID"), occ.get("scientificName"),
                           occ.get("basisOfRecord"))
                continue
            cd_nom = gbif_taxo.resolve(occ, index, cache_gbif, journal=click.echo)
            if not cd_nom:
                rejets.add("no_cd_nom", occ.get("gbifID"), occ.get("scientificName"),
                           f"taxonKey={occ.get('taxonKey')}")
                continue
            ligne = gbif_tr.to_row(occ, cd_nom=cd_nom, id_dataset=None,
                                   id_source=id_source, id_module=id_module, srid=srid,
                                   resolver=resolver, download_doi=download_doi,
                                   statut_validation=statut_validation,
                                   version_taxref=v_taxref)
            if ligne is None:
                rejets.add("no_coordinates" if occ.get("decimalLatitude") is None else "no_date",
                           occ.get("gbifID"), occ.get("scientificName"),
                           f"eventDate={occ.get('eventDate')!r}")
                continue
            lot.append(ligne)
            if len(lot) >= batch_size and not dry_run:
                jdd = jdd or creer_jdd(meta, lic, uid, af, orgs_cache)
                for l in lot:
                    l["id_dataset"] = jdd.id_dataset
                i, u = syn_core.insert_batch(lot)
                ecrits += i; maj += u
                db.session.commit(); lot = []
                click.echo(f"  ... {ecrits} écrites, {maj} mises à jour")
        if lot and not dry_run:
            jdd = jdd or creer_jdd(meta, lic, uid, af, orgs_cache)
            for l in lot:
                l["id_dataset"] = jdd.id_dataset
            i, u = syn_core.insert_batch(lot)
            ecrits += i; maj += u
            db.session.commit()
        elif dry_run:
            ecrits = len(lot)
        total_ecrits += ecrits
        total_maj += maj
        if ecrits == 0 and maj == 0 and jdd is None:
            click.secho("  aucune occurrence retenue — aucun JDD créé", fg="cyan")
        else:
            suffixe = f", {maj} mise(s) à jour" if maj else ""
            click.echo(f"  {'(simulation) ' if dry_run else ''}{ecrits} écrite(s){suffixe}")

    if cache_gbif:
        rattrapes = sum(1 for v in cache_gbif.values() if v)
        if rattrapes:
            click.echo(f"  {rattrapes} taxon(s) rattrapé(s) via le référentiel TAXREF de GBIF "
                       f"(absents de taxref_liens)")
    click.secho(f"\n{'DRY-RUN — ' if dry_run else ''}{total_lus} occurrence(s) lue(s), "
                f"{total_ecrits} écrite(s), {total_maj} mise(s) à jour, "
                f"{len(rejets)} rejetée(s).", fg="green")
    for l in rejets.summary_lines():
        click.echo(l)


def _purger(*, id_source, id_dataset, ca_uuid, libelle_source, taxon="",
            max_uncertainty=0, cible="", tout=False, drop_empty_datasets=False,
            yes=False):
    """Corps commun des purges. La seule chose propre à chaque source est la façon de
    désigner un JDD ; tout le reste est identique, et l'était déjà à quelques
    divergences près — chacune privant une source d'un garde-fou que l'autre avait.

    Trois d'entre elles valaient d'être généralisées :

    - **le refus de purger sans critère.** `visionature-purge --yes` effaçait toute la source
      sans rien demander, là où `gbif-purge` exigeait au moins un filtre. Le refus est
      désormais la règle, et `--tout` la façon explicite de dire qu'on veut bien tout
      supprimer ;
    - **le diagnostic d'un `--taxon` sans correspondance.** « 0 observation concernée »
      alors que l'interface en montre des milliers laisse croire à une panne : les noms
      de rangs TAXREF ne sont pas ceux du langage courant ;
    - **l'affichage des JDD avant leur suppression.** GBIF les listait, VisioNature les
      supprimait en silence.
    """
    from .core import purge as purge_core, datasets as ds_core

    # `--supprimer-jdd-vides` seul est une opération à part entière : retirer les jeux
    # devenus vides sans purger la moindre observation. Le cas se présente après un
    # changement de découpage — les jeux de l'ancienne clé restent, vides, dans le
    # module Métadonnées. L'exiger accompagné d'un critère de purge obligerait à
    # supprimer des données pour faire ce ménage.
    menage_seul = drop_empty_datasets and not (taxon or max_uncertainty or id_dataset
                                               or tout)
    if not (taxon or max_uncertainty or id_dataset or tout or menage_seul):
        raise click.ClickException(
            f"Aucun critère : précisez au moins --taxon, ou ce qui désigne un jeu. "
            f"Pour vider toute la source {libelle_source}, il faut le dire avec --tout : "
            f"une suppression totale ne doit pas pouvoir arriver par omission. "
            f"Pour ne retirer que les jeux devenus vides, --supprimer-jdd-vides suffit.")

    if menage_seul:
        click.echo(f"Aucune observation ne sera supprimée — ménage des jeux vides de "
                   f"{libelle_source} uniquement.")
        _supprimer_jdd_vides(ca_uuid, libelle_source, yes)
        return

    n = purge_core.compter(id_source, id_dataset, taxon or None, max_uncertainty or None)
    criteres = " · ".join(x for x in (
        cible,
        f"taxon « {taxon} »" if taxon else "",
        f"incertitude > {max_uncertainty} m" if max_uncertainty else "",
    ) if x) or f"toute la source {libelle_source}"
    click.echo(f"{n} observation(s) concernée(s) — {criteres}")

    if not n:
        total = purge_core.compter(id_source, id_dataset)
        if taxon and total:
            click.secho(f"  ⚠ aucun taxon ne correspond à « {taxon} », alors que la "
                        f"source porte {total} observation(s). Rangs présents :",
                        fg="yellow")
            click.echo(f"    {'classe':<20}  {'ordre':<20}  {'famille':<24}  n")
            for classe, ordre, famille, combien in purge_core.rangs_presents(
                    id_source, id_dataset):
                click.echo(f"    {str(classe or '—'):<20}  {str(ordre or '—'):<20}  "
                           f"{str(famille or '—'):<24}  {combien}")
            click.echo("  Reprenez --taxon avec l'un de ces noms, ou employez --tout "
                       "pour purger toute la source.")
        else:
            click.secho("Rien à supprimer.", fg="green")
    elif not yes:
        click.secho(f"\nSimulation : {n} observation(s) seraient supprimées, ainsi que "
                    f"leurs rattachements aux zonages. Relancez avec --yes pour "
                    f"exécuter.", fg="yellow")
        return
    else:
        supprimees = purge_core.supprimer(id_source, id_dataset, taxon or None,
                                          max_uncertainty or None)
        db.session.commit()
        click.secho(f"{supprimees} observation(s) supprimée(s).", fg="green")

    if drop_empty_datasets:
        _supprimer_jdd_vides(ca_uuid, libelle_source, yes)


def _supprimer_jdd_vides(ca_uuid: str, libelle_source: str, yes: bool) -> None:
    """Retire les jeux du cadre qui ne portent plus aucune observation.

    ⚠ Le cadre d'acquisition, lui, n'est JAMAIS supprimé. Il est créé par la migration du
    module, et `get_acquisition_framework` lève sans lui — une migration Alembic ne se
    rejouant pas, sa disparition casserait tout import ultérieur. Il porte de surcroît
    les métadonnées que l'exploitant a pu enrichir à la main. Sa suppression relève de la
    désinstallation du module, pas d'une purge de données.
    """
    from .core import purge as purge_core, datasets as ds_core

    af = ds_core.get_acquisition_framework(ca_uuid)
    vides = purge_core.jdd_vides(af.id_acquisition_framework)
    if not vides:
        click.echo(f"Aucun JDD vide dans le cadre d'acquisition {libelle_source}.")
        return
    click.echo(f"\n{len(vides)} JDD vide(s) :")
    for identifiant, nom in vides[:10]:
        click.echo(f"  {identifiant} — {nom[:62]}")
    if len(vides) > 10:
        click.echo(f"  … et {len(vides) - 10} autre(s)")
    if not yes:
        click.secho("Relancez avec --yes pour les supprimer.", fg="yellow")
        return
    partis = sum(1 for identifiant, _ in vides if purge_core.supprimer_jdd(identifiant))
    db.session.commit()
    click.secho(f"{partis} JDD supprimé(s).", fg="green")


@click.command("gbif-purge")
@click.option("--jeu", "reference", default="",
              help="Jeu visé : datasetKey GBIF, unique_dataset_id du JDD, ou son "
                   "id_dataset.")
@click.option("--taxon", default="",
              help="Groupe taxonomique à retirer, par son nom TAXREF : règne, phylum, "
                   "classe, ordre, famille, ou début de nom scientifique. "
                   "Exemple : --taxon Chiroptera")
@click.option("--incertitude-max", "max_uncertainty", default=0, type=int,
              help="Retirer les observations dont l'incertitude dépasse N mètres.")
@click.option("--tout", is_flag=True,
              help="Purger toute la source GBIF, sans autre critère.")
@click.option("--supprimer-jdd-vides", "drop_empty_datasets", is_flag=True,
              help="Supprimer ensuite les JDD du cadre GBIF devenus vides.")
@click.option("--yes", is_flag=True,
              help="Exécuter réellement. Sans ce drapeau, la commande se contente "
                   "d'afficher ce qu'elle supprimerait.")
def gbif_purge(reference, taxon, max_uncertainty, tout, drop_empty_datasets, yes):
    """Supprime des observations GBIF déjà importées.

    Utile après coup : une exclusion ajoutée à la configuration ne rattrape pas ce qui
    est déjà en base. La suppression est toujours bornée à la source GBIF — jamais aux
    données saisies localement ni à un autre import.
    """
    from sqlalchemy import select as sa_select
    from geonature.core.gn_meta.models import TDatasets
    from .core import synthese as syn_core, datasets as ds_core
    from .migrations.c4e8a2b95d16_source_gbif import SOURCE_NAME
    from .migrations.b2d7e9f31a04_cadre_acquisition_gbif import CA_UUID

    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_dataset, cible = None, ""
    if reference:
        jdd = None
        if reference.isdigit():
            jdd = db.session.get(TDatasets, int(reference))
        if jdd is None:
            # Un datasetKey GBIF se traduit en unique_dataset_id ; on essaie les trois
            # licences, l'appelant n'ayant pas à savoir laquelle a servi de clé.
            # Les candidats non-UUID sont écartés : la colonne est typée, et les passer
            # à PostgreSQL ferait échouer la requête sur une erreur de cast illisible.
            import uuid as _uuid
            candidats = [
                str(ds_core.dataset_uuid("GBIF", reference, lic))
                for lic in ("CC_BY_4_0", "CC0_1_0", "CC_BY_NC_4_0")
            ]
            try:
                candidats.append(str(_uuid.UUID(reference)))
            except (ValueError, AttributeError):
                pass
            jdd = db.session.scalar(
                sa_select(TDatasets).where(TDatasets.unique_dataset_id.in_(candidats)))
        if jdd is None:
            raise click.ClickException(f"Aucun JDD ne correspond à « {reference} ».")
        id_dataset = jdd.id_dataset
        cible = f"jeu {id_dataset}"
        click.echo(f"Jeu visé : {jdd.dataset_name[:60]} (id_dataset={id_dataset})")

    _purger(id_source=id_source, id_dataset=id_dataset, ca_uuid=CA_UUID,
            libelle_source="GBIF", taxon=taxon, max_uncertainty=max_uncertainty,
            cible=cible, tout=tout, drop_empty_datasets=drop_empty_datasets, yes=yes)


@click.command("visionature-import")
@click.option("--groupe-taxo", "groupes", multiple=True,
              help="Groupes taxonomiques à moissonner (défaut : configuration, sinon tous).")
@click.option("--depuis", "since", default="",
              help="Date ISO 8601 : ne moissonner que les créations, modifications et "
                   "suppressions depuis. VisioNature sait signaler les suppressions, "
                   "ce que GBIF ne fait pas.")
@click.option("--fin", "fin", default="",
              help="Date de fin du moissonnage complet (AAAA-MM-JJ). Par défaut, "
                   "aujourd'hui. Sert à découper un gros historique en partitions "
                   "reprenables : une par département et par année.")
@click.option("--lot", "batch_size", default=None, type=int)
@click.option("--dry-run", is_flag=True)
def visionature_import(groupes, since, fin, batch_size, dry_run):
    """Importe des observations VisioNature dans la Synthèse."""
    from geonature.utils.config import config as gn_config
    from sqlalchemy import select as sa_select
    from geonature.core.gn_meta.models import TDatasets
    from .core import (report as report_core, synthese as syn_core,
                       datasets as ds_core, nomenclatures as nomen_core,
                       purge as purge_core, cache as cache_core)
    from .sources.visionature import (api as vn_api, taxonomy as vn_taxo,
                                     transform as vn_tr, confidentialite as vn_conf,
                                     reproduction as vn_repro, perimetre as vn_perim)
    from .migrations.e91b4c07a2d8_source_visionature import SOURCE_NAME, CA_UUID

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException(
            "Connecteur VisioNature désactivé. Renseignez [visionature] dans la "
            "configuration et passez `enabled = true`.")
    for cle in ("url", "user_email", "user_password", "client_key", "client_secret"):
        if not cfg.get(cle):
            raise click.ClickException(
                f"[visionature] {cle} manquant. Les identifiants OAuth1 "
                f"(client_key/client_secret) sont fournis par Biolovision, séparément "
                f"du compte utilisateur.")

    # Le diff de Biolovision ne remonte que 10 semaines. Au-delà, l'incrémental
    # perdrait en silence les créations et suppressions de l'intervalle : mieux vaut
    # refuser que produire une base incomplète sans le dire.
    if since and not vn_api.diff_possible(since):
        raise click.ClickException(
            f"--since {since} dépasse les {vn_api.DIFF_MAX_SEMAINES} semaines que l'API "
            f"Biolovision couvre en différentiel. Lancez un moissonnage complet "
            f"(sans --since), sinon les créations et suppressions de l'intervalle "
            f"seraient perdues sans avertissement.")

    batch_size = batch_size or cfg.get("batch_size", 1000)
    instance = cfg["url"].rstrip("/")
    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_module = syn_core.get_module_id("CONNECTORS")
    srid = syn_core.local_srid()
    af = ds_core.get_acquisition_framework(CA_UUID)
    v_taxref = syn_core.version_taxref()
    click.secho(f"instance={instance} source={id_source} srid={srid} "
                f"taxref={v_taxref or 'inconnu'}", fg="green")
    if not v_taxref:
        click.secho("  ⚠ paramètre `taxref_version` absent de gn_commons.t_parameters : "
                    "meta_v_taxref restera NULL.", fg="yellow")

    # `url_source` pointe sur la redirection du module, et non directement sur le
    # portail. GeoNature construit le lien en insérant toujours un séparateur —
    # `url_source + '/' + entity_source_pk_value` — ce qu'une URL de retour en chaîne de
    # requête ne supporte pas : `…/index.php?m_id=54&id=` donnerait `…&id=/176983543`.
    # Un chemin terminé par l'identifiant est en revanche exactement ce que le cœur sait
    # produire. `entity_source_pk_value` garde donc l'identifiant brut, et
    # `blueprint.voir_dans_visionature` se charge de la redirection.
    api = str(gn_config.get("API_ENDPOINT") or "").rstrip("/")
    if api:
        db.session.execute(
            db_text("UPDATE gn_synthese.t_sources SET url_source = :u "
                    "WHERE id_source = :s AND url_source IS DISTINCT FROM :u"),
            {"u": f"{api}/connectors/visionature", "s": id_source})
    else:
        click.secho("  ⚠ API_ENDPOINT absent de la configuration GeoNature : le bouton "
                    "« voir la donnée source » ne sera pas alimenté.", fg="yellow")

    # Cache des référentiels : outil de mise au point, désactivé par défaut. Voir
    # `core/cache.py` — le référentiel des observateurs contient des noms de personnes.
    heures = float(cfg.get("cache_heures", 0) or 0)
    dossier_cache = cfg.get("cache_dir") or None

    def referentiel(nom, chargeur, personnel=False):
        contenu = cache_core.charger(nom, instance, heures, dossier_cache)
        if contenu is not None:
            click.echo(f"  {nom} : depuis le cache ({heures:g} h)")
            return contenu
        contenu = chargeur()
        fichier = cache_core.enregistrer(nom, instance, contenu, heures, dossier_cache)
        if fichier and personnel:
            click.secho(f"  ⚠ {nom} mis en cache dans {fichier} — il contient des noms "
                        f"de personnes. Fichier en 0600 ; `visionature-vider-cache` l'efface.",
                        fg="yellow")
        return contenu

    click.echo("Chargement du référentiel d'espèces…")
    especes = referentiel("especes", lambda: vn_api.especes(cfg))
    index, non_resolues = vn_taxo.construire_index(especes, journal=click.echo)
    if not index:
        raise click.ClickException(
            "Aucune espèce résolue : la correspondance se fait par nom scientifique "
            "contre TAXREF, vérifiez que le référentiel est bien chargé.")

    rejets = report_core.Rejects()
    for e in non_resolues:
        rejets.add("espece_non_resolue", e["id"], e["latin_name"] or e["french_name"],
                   e["motif"])

    # Le référentiel des groupes est chargé dans tous les cas, pas seulement pour établir
    # la liste à moissonner : le statut de reproduction des non-oiseaux dépend du groupe,
    # et le désigner par son code (`TAXO_GROUP_BAT`) plutôt que par son identifiant
    # numérique est ce qui rend la table de correspondance transposable d'une instance à
    # l'autre. Sans cet index, le module retombe sur les identifiants de Faune-France.
    groupes_bruts = referentiel("groupes", lambda: vn_api.groupes_taxonomiques(cfg))
    index_groupes = vn_repro.index_groupes(groupes_bruts)
    # `access_mode` vaut « full », « limited » ou « none ». `transfer_vn` saute les
    # groupes fermés au compte ; les interroger ne peut produire qu'un refus, et sur
    # une instance qui en compte quarante-neuf ce sont autant d'appels perdus.
    fermes = {str(g.get("id") or g.get("@id") or "").strip()
              for g in groupes_bruts if str(g.get("access_mode") or "") == "none"}
    groupes = list(groupes) or cfg.get("taxo_groups") or [
        identifiant for identifiant in index_groupes if identifiant not in fermes]
    if fermes and not cfg.get("taxo_groups"):
        click.echo(f"  {len(fermes)} groupe(s) fermé(s) au compte (access_mode = none), "
                   f"écarté(s) : {', '.join(sorted(fermes))}")
    click.echo(f"{len(groupes)} groupe(s) taxonomique(s) à traiter.")

    resolver = nomen_core.Resolver()
    cfg_valid = (gn_config.get("CONNECTORS") or {}).get("validation", {})
    statut_validation = cfg_valid.get("status") if cfg_valid.get("enabled") else None
    surcharges = cfg.get("atlas") or {}
    cfg_repro = cfg.get("reproduction") or {}
    contexte_repro = vn_repro.Contexte(
        index=index_groupes,
        regles=vn_repro.fusionner(cfg_repro.get("regles")),
        active=cfg_repro.get("active", True))
    secret = cfg.get("pseudonymisation_secret", "")
    if not secret:
        raise click.ClickException(
            "[visionature] pseudonymisation_secret manquant. Il est requis même si peu "
            "d'observateurs demandent l'anonymat : une clé par défaut rendrait les "
            "pseudonymes recalculables par un tiers, donc réidentifiables.")
    forcer_anonymat = cfg.get("forcer_anonymat", False)

    # Le consentement est individuel : chaque observateur déclare dans VisioNature si son
    # nom peut être diffusé. Un interrupteur global écraserait ce choix.
    # Le référentiel des observateurs pèse 246 699 inscrits sur Faune-Occitanie, soit
    # plusieurs minutes de téléchargement. Depuis que le consentement est lu dans le
    # relevé lui-même — `anonymous` et `anonymous_in_export`, présents en forme longue —
    # il n'est plus qu'un repli pour la forme courte. On ne le charge donc qu'à la
    # première observation qui en a réellement besoin, et le plus souvent jamais.
    index_anonymat = _IndexAnonymat(
        lambda: vn_conf.index_anonymat(
            referentiel("observateurs", lambda: vn_api.observateurs(cfg),
                        personnel=True)))
    if forcer_anonymat:
        click.echo("  anonymat forcé pour tous les observateurs")
    respecter = cfg.get("respecter_confidentialite", True)
    niveau_masquees = cfg.get("niveau_diffusion_masquees", "4")
    departements = vn_perim.normaliser(cfg.get("departements"))
    filtre_api = dict(cfg.get("filtre_api") or {})
    if departements:
        click.echo(f"  périmètre : département(s) {', '.join(sorted(departements))}"
                   + (f", filtre serveur {filtre_api}" if filtre_api else ""))
    else:
        click.secho("  ⚠ aucun filtre de périmètre : toute l'étendue de l'instance "
                    "sera moissonnée.", fg="yellow")
    par_projet = cfg.get("jdd_par_code_projet", True)
    # Producteurs déclarés par l'exploitant, jamais créés depuis les données : les tirer
    # d'une API peuplerait bib_organismes de variantes d'orthographe.
    producteurs = {str(k).strip().zfill(2): v
                   for k, v in (cfg.get("producteurs_departementaux") or {}).items()}
    fournisseur = cfg.get("organisme_fournisseur") or None
    creer_organismes = cfg.get("creer_organismes_manquants", False)
    contact_principal = cfg.get("organisme_contact_principal") or fournisseur
    # Métadonnées que le jeu prendrait sinon par défaut : « Financement : Publique » et
    # « Créateur : Non renseigné », ni l'un ni l'autre choisi.
    metadonnees = {"nom_instance": cfg.get("nom_instance", ""),
                   "territoires": list(cfg.get("territoires") or []),
                   "financement": cfg.get("financement", ""),
                   "financement_par_projet": dict(cfg.get("financement_par_projet") or {}),
                   "createur": cfg.get("createur", "")}
    if not producteurs and not fournisseur:
        click.secho("  ⚠ aucun organisme déclaré : les jeux de données seront créés sans "
                    "producteur, ce que le SINP n'admet pas. Voir "
                    "[visionature] producteurs_departementaux.", fg="yellow")

    # ── Périmètre temporel et territorial du moissonnage complet ────────────
    # Inutile de les établir en incrémental : le différentiel s'en passe.
    date_debut = date_fin = None
    territoires: list[str] = []
    tranche_jours = int(cfg.get("tranche_jours", vn_api.TRANCHE_JOURS_DEFAUT))
    # `search` est la seule voie qui porte de la donnée : `api_get` et
    # `api_list(id_sightings_list=…)` sont refusés. L'incrémental passe donc lui aussi
    # par `search`, avec `entry_date` — recherche par date de SAISIE, ce qui rattrape
    # aussi les observations anciennes encodées récemment.
    if True:
        from datetime import date as _date
        brut = since or str(cfg.get("date_debut") or "").strip()
        try:
            date_debut = _date.fromisoformat(brut) if brut else _date(1900, 1, 1)
        except ValueError:
            raise click.ClickException(
                f"{'--since' if since else '[visionature] date_debut'} = {brut!r} "
                f"n'est pas une date ISO (AAAA-MM-JJ).")
        # Borner la fin permet de découper un historique volumineux en partitions
        # reprenables. Sur 14 millions d'observations à ~32/s, une seule exécution
        # durerait cinq jours : une coupure au troisième tout perdrait, faute de
        # journal de reprise. Une partition par département et par année se rejoue en
        # quelques heures, et l'ON CONFLICT rend le recouvrement gratuit.
        if fin:
            try:
                date_fin = _date.fromisoformat(fin)
            except ValueError:
                raise click.ClickException(
                    f"--fin = {fin!r} n'est pas une date ISO (AAAA-MM-JJ).")
        else:
            date_fin = _date.today()
        if date_fin <= date_debut:
            raise click.ClickException(
                f"--fin ({date_fin}) doit être postérieure au début de période "
                f"({date_debut}).")

        # L'API refuse une recherche non bornée territorialement : 403 sans périmètre,
        # 200 avec. L'identifiant attendu est `id_country` suivi du `short_name`, soit
        # « 109 » pour l'Ariège — c'est ce que compose transfer_vn.
        voulus = {str(d).strip().zfill(2) for d in (cfg.get("departements") or [])}
        if not voulus:
            raise click.ClickException(
                "Le moissonnage exige [visionature] departements : l'API refuse "
                "une recherche sans périmètre territorial, et sans lui vous "
                "moissonneriez toute l'étendue de l'instance. "
                "`visionature-perimetres` liste les valeurs disponibles.")
        unites = referentiel("territoires", lambda: vn_api.unites_territoriales(cfg))
        territoires = [t for t in (vn_api.identifiant_territoire(u) for u in unites
                                   if str(u.get("short_name") or "") in voulus) if t]
        if not territoires:
            raise click.ClickException(
                f"Aucune unité territoriale de l'instance ne correspond à "
                f"{sorted(voulus)}. Vérifiez avec `visionature-perimetres`.")
        click.echo(f"  {'incrémental (date de saisie)' if since else 'moissonnage complet'} : "
                   f"{date_debut} → {date_fin}, "
                   f"territoire(s) {', '.join(territoires)}, "
                   f"tranches de {tranche_jours} jour(s) ajustées au volume")

    total_lus = total_ecrits = total_maj = total_supprimes = hors_perimetre = 0
    groupes_refuses: list[tuple[str, str]] = []
    suppressions_manquees: list[tuple[str, str]] = []
    jdds: dict = {}

    for rang, groupe in enumerate(groupes, 1):
        contexte_repro.groupe_courant = groupe
        # Annoncer le groupe AVANT de l'interroger : ces requêtes durent parfois
        # plusieurs dizaines de secondes, et sans cette ligne le moissonnage paraît figé.
        click.echo(f"  [{rang}/{len(groupes)}] groupe {groupe}…")

        def _tranche(territoire, debut, fin_t, n):
            if n < 0:
                click.secho(f"    {territoire} {debut:%Y-%m-%d} → {fin_t:%Y-%m-%d} : "
                            f"refus, tranche rétrécie", fg="yellow")
            else:
                click.echo(f"    {territoire} {debut:%Y-%m-%d} → {fin_t:%Y-%m-%d} : "
                           f"{n} relevé(s)")

        if since:
            # Les suppressions d'abord : une observation supprimée puis recréée sous le
            # même identifiant serait sinon retirée après avoir été réécrite.
            # Une erreur sur les suppressions ne doit pas empêcher le moissonnage des
            # créations : leur instance rend des 502 et des 504 sous charge, et perdre
            # un import entier pour un incident passager serait disproportionné. Le
            # défaut est signalé — ne pas avoir répercuté des suppressions se rattrape
            # au passage suivant, l'ignorer en silence non.
            try:
                supprimes = vn_api.observations_supprimees(cfg, str(groupe), since)
            except vn_api.bio.BiolovisionApiException as erreur:
                supprimes = []
                suppressions_manquees.append((str(groupe), repr(erreur)))
                click.secho(f"    suppressions non récupérées ({erreur!r}) — "
                            f"le moissonnage continue", fg="yellow")
            if supprimes:
                if dry_run:
                    click.echo(f"    {len(supprimes)} relevé(s) supprimé(s) à la "
                               f"source (simulation)")
                else:
                    n = purge_core.supprimer_par_identifiants_source(
                        id_source, "sighting_id", supprimes)
                    db.session.commit()
                    total_supprimes += n
                    click.echo(f"    {len(supprimes)} relevé(s) supprimé(s) à la "
                               f"source -> {n} observation(s) retirée(s)")
            # Les créations et modifications passent par `search` sur la date de
            # SAISIE, comme le moissonnage complet : `diff` ne livre que des
            # identifiants, et les deux voies qui permettraient de les résoudre —
            # `api_get` et `api_list(id_sightings_list=…)` — sont refusées par l'API.
            lots = (r for _d, _f, _t, r in vn_api.moissonner_recherche(
                cfg, str(groupe), date_debut, date_fin, territoires,
                tranche_jours=tranche_jours, journal=_tranche, type_date="entry"))
        else:
            # Moissonnage complet : par `search`, découpé en tranches de dates et borné
            # par territoire. `api_list` est déprécié en amont et refusé par l'API, et
            # une recherche sans périmètre l'est aussi — mesuré sur faune-occitanie.org.
            lots = (releves for _d, _f, _t, releves in vn_api.moissonner_recherche(
                cfg, str(groupe), date_debut, date_fin, territoires,
                tranche_jours=tranche_jours, journal=_tranche))

        lot, ecrits, maj = [], 0, 0
        try:
            for releves in lots:
                couples = vn_tr.deplier(releves)
                total_lus += len(couples)
                for sighting, observation in couples:
                    # Le moissonnage complet est déjà borné par l'API
                    # (`territorial_unit_ids`) : sa garantie vaut la nôtre, et le
                    # format court ne porte pas toujours le rattachement administratif.
                    if not vn_perim.dans_perimetre(sighting, departements,
                                                   borne_serveur=not since):
                        hors_perimetre += 1
                        continue
                    if respecter:
                        motif = vn_conf.est_confidentielle(observation, sighting)
                        if motif:
                            rejets.add("confidentielle", vn_tr.identifiant_releve(sighting, observation),
                                       (sighting.get("species") or {}).get("name"), motif)
                            continue
                    cd_nom = vn_taxo.resolve(sighting, index)
                    if not cd_nom:
                        espece = (sighting.get("species") or {})
                        rejets.add("no_cd_nom", vn_tr.identifiant_releve(sighting, observation), espece.get("name"),
                                   f"species_id={espece.get('@id')}")
                        continue
                    ligne = vn_tr.to_row(sighting, observation, cd_nom=cd_nom,
                                         id_dataset=None,
                                         id_source=id_source, id_module=id_module,
                                         srid=srid,
                                         resolver=resolver, instance=instance,
                                         surcharges_atlas=surcharges,
                                         statut_validation=statut_validation,
                                         index_anonymat=index_anonymat,
                                         secret_pseudo=secret,
                                         forcer_anonymat=forcer_anonymat,
                                         code_diffusion_masquee=niveau_masquees,
                                         version_taxref=v_taxref,
                                         repro=contexte_repro)
                    if ligne is None:
                        rejets.add("no_coordinates", vn_tr.identifiant_releve(sighting, observation),
                                   (sighting.get("species") or {}).get("name"), "")
                        continue
                    ligne["_projet"] = vn_tr.code_projet(observation) if par_projet else None
                    ligne["_departement"] = vn_perim.departement(sighting)
                    lot.append(ligne)
                    if len(lot) >= batch_size and not dry_run:
                        i, u = _ecrire_lot(lot, jdds, instance, af, id_source,
                                   producteurs, fournisseur, creer_organismes,
                                   metadonnees, contact_principal)
                        ecrits += i; maj += u
                        db.session.commit(); lot = []
                        click.echo(f"    … {ecrits} écrites, {maj} mises à jour")
        except vn_api.bio.BiolovisionApiException as erreur:
            # L'exception surgit pendant l'itération, le moissonnage étant paresseux.
            # Ce qui a déjà été lu reste acquis et sera écrit ci-dessous.
            groupes_refuses.append((str(groupe), f"recherche : {erreur!r}"))
            click.secho(f"    interrompu par l'API ({erreur!r})", fg="yellow")

        if lot and not dry_run:
            i, u = _ecrire_lot(lot, jdds, instance, af, id_source,
                                   producteurs, fournisseur, creer_organismes,
                                   metadonnees, contact_principal)
            ecrits += i; maj += u
            db.session.commit()
        elif dry_run:
            # Sans cette interrogation, la simulation annonce comme « à écrire » des
            # observations déjà présentes : un moissonnage complet recoupe presque
            # toujours un incrémental antérieur.
            deja = syn_core.compter_existants(lot)
            ecrits = len(lot) - deja
            maj = deja
        total_ecrits += ecrits; total_maj += maj

    if not dry_run:
        db.session.commit()
    if suppressions_manquees:
        click.secho(f"\n  ⚠ suppressions non récupérées sur "
                    f"{len(suppressions_manquees)} groupe(s) : "
                    f"{', '.join(g for g, _ in suppressions_manquees)}. "
                    f"Les observations retirées à la source sont donc encore en "
                    f"Synthèse ; relancez le même --since pour les rattraper.",
                    fg="yellow")
    if groupes_refuses:
        click.secho(f"\n  {len(groupes_refuses)} groupe(s) refusé(s) par l'API :", fg="yellow")
        for groupe, motif in groupes_refuses:
            click.echo(f"    groupe {groupe} — {motif}")
        click.secho("  Un 403 sur `search` signale que le périmètre de la clé d'API ne "
                    "couvre pas ce groupe : la clé est valide — une clé inconnue "
                    "renverrait 401 — mais pas habilitée sur ces observations. "
                    "`visionature-diagnostic --taxo-group <id>` détaille les points d'entrée "
                    "ouverts et fermés, à porter à l'administrateur de l'instance.",
                    fg="yellow")
    if hors_perimetre:
        # Un rejet massif alors qu'un filtre serveur est configuré signale que l'API l'a
        # ignoré : le paramètre n'existe pas, ou ne porte pas ce nom sur cette instance.
        click.secho(f"  {hors_perimetre} observation(s) hors périmètre écartée(s).",
                    fg="yellow" if filtre_api else None)
        if filtre_api and hors_perimetre > total_lus / 10:
            click.secho("  ⚠ le filtre serveur semble ignoré par l'API : la quasi-totalité "
                        "de l'instance a été téléchargée avant d'être écartée ici. "
                        "Vérifiez le nom du paramètre avec `visionature-perimetres`.", fg="yellow")
    suffixe = f", {total_supprimes} supprimée(s)" if total_supprimes else ""
    verbes = ("à écrire", "déjà en base") if dry_run else ("écrite(s)", "mise(s) à jour")
    click.secho(f"\n{'DRY-RUN — ' if dry_run else ''}{total_lus} observation(s) lue(s), "
                f"{total_ecrits} {verbes[0]}, {total_maj} {verbes[1]}{suffixe}, "
                f"{rejets.nombre_observations()} rejetée(s).", fg="green")
    for ligne in rejets.summary_lines_observations():
        click.echo(ligne)
    # Les espèces du référentiel absentes de TAXREF sont journalisées mais comptées à
    # part : les mêler aux rejets d'observations laisse croire à un échec massif. Un
    # import de 472 observations a affiché « 30191 rejetée(s) », alors qu'il s'agissait
    # d'espèces dont l'immense majorité ne sera jamais observée sur le territoire.
    if rejets.nombre_referentiel():
        click.echo(f"  ({rejets.nombre_referentiel()} espèce(s) du référentiel "
                   f"VisioNature sans correspondance TAXREF — sans rapport avec les "
                   f"observations ci-dessus, voir le journal)")
    # Un code d'âge, de sexe ou de comportement absent de la table n'est pas une erreur —
    # l'énumération VisioNature est localement extensible — mais c'est le seul signal
    # qu'une règle manque, et donc que des indices de reproduction passent à la trappe.
    # `gn_vn2synthese` ne le produit pas : chez lui, un code inconnu et un code jugé non
    # significatif sont indiscernables.
    if contexte_repro.inconnus:
        click.secho(f"  {len(contexte_repro.inconnus)} code(s) de reproduction non "
                    f"reconnu(s) — à déclarer dans [visionature.reproduction.regles] :",
                    fg="yellow")
        for cle, n in contexte_repro.inconnus.most_common(15):
            click.echo(f"    {cle} ({n})")
    chemin = rejets.write_csv(Path("vn_rejets.csv"))
    if chemin:
        click.echo(f"  Journal détaillé : {chemin}")


class _IndexAnonymat:
    """Index des consentements, chargé au premier besoin — souvent jamais.

    `observateur()` n'interroge cet index que lorsque le relevé ne porte pas lui-même
    `anonymous` ni `anonymous_in_export`. En forme longue il les porte toujours, si bien
    que le référentiel — plusieurs minutes de téléchargement pour un quart de million
    d'inscrits, et des noms de personnes en mémoire — n'a plus lieu d'être payé d'avance.

    Se comporte comme le dictionnaire qu'il remplace : `in` et `[]` suffisent à
    `observateur()`, et déclenchent le chargement.
    """

    def __init__(self, chargeur):
        self._chargeur = chargeur
        self._index = None

    def _charger(self) -> dict:
        if self._index is None:
            click.echo("\n  (chargement du référentiel des observateurs : un relevé "
                       "n'exprime pas de consentement)")
            self._index = self._chargeur() or {}
            anonymes = sum(1 for v in self._index.values() if v)
            click.echo(f"  référentiel : {len(self._index)} inscrit(s), "
                       f"{anonymes} ayant demandé l'anonymat")
            if not self._index:
                click.secho("  ⚠ référentiel vide : les observateurs concernés seront "
                            "pseudonymisés, l'ignorance ne valant pas consentement.",
                            fg="yellow")
        return self._index

    def __contains__(self, cle) -> bool:
        return cle in self._charger()

    def __getitem__(self, cle):
        return self._charger()[cle]

    def __bool__(self) -> bool:
        # Ne PAS déclencher le chargement : `observateur()` fait `index or {}`, et le
        # provoquer ici annulerait tout le bénéfice.
        return True


def _ecrire_lot(lot, jdds, instance, af, id_source=None,
                producteurs=None, fournisseur=None, creer_organismes=False,
                metadonnees=None, contact_principal=None):
    """Écrit un lot en le répartissant par code projet.

    Les JDD sont créés à la demande : un projet dont toutes les observations sont
    rejetées ne laisse pas de jeu vide dans le module Métadonnées.
    """
    from .core import synthese as syn_core

    # Réalignement des identifiants avant écriture : les lignes déjà importées portent
    # l'uuid5 calculé par les versions antérieures du module, alors que le producteur
    # publie son propre UUID. Sans ce renommage, chacune serait réinsérée à côté de
    # l'ancienne — un doublon que rien ne signalerait.
    if id_source is not None:
        renommees = syn_core.realigner_uuid(lot, id_source)
        if renommees:
            click.echo(f"    … {renommees} ligne(s) réalignée(s) sur l'UUID du producteur")

    # La clé du jeu est (département, projet) : le producteur change d'un département
    # à l'autre, et un jeu de données porte un producteur unique.
    par_jdd: dict = {}
    for ligne in lot:
        cle = (ligne.pop("_departement", None), ligne.pop("_projet", None))
        par_jdd.setdefault(cle, []).append(ligne)

    inserees = maj = 0
    for cle, lignes in par_jdd.items():
        departement, projet = cle
        if cle not in jdds:
            jdds[cle] = _jdd_visionature(instance, af, projet, departement,
                                         producteurs, fournisseur, creer_organismes,
                                         metadonnees, contact_principal)
        for ligne in lignes:
            ligne["id_dataset"] = jdds[cle].id_dataset
        i, u = syn_core.insert_batch(lignes)
        inserees += i; maj += u
    return inserees, maj


def _jdd_visionature(instance: str, af, projet: str | None = None,
                     departement: str | None = None, producteurs: dict | None = None,
                     fournisseur: str | None = None, creer_organismes: bool = False,
                     metadonnees: dict | None = None,
                     contact_principal: str | None = None):
    """JDD d'un département, créé à la première écriture.

    ⚠ Le découpage suit le **département** et non la seule instance, parce que c'est là
    que change le producteur : sur Faune-Occitanie, l'Ariège est produite par l'ANA-CEN
    Ariège, les Pyrénées-Orientales par le GOR. Un jeu de données porte un producteur
    unique — c'est une métadonnée obligatoire du SINP —, donc mêler deux départements
    dans un même jeu le rendrait non conforme.

    Le code projet reste un axe secondaire, quand il est activé : il distingue des
    programmes (atlas, suivis) au sein d'un même producteur.
    """
    from .core import datasets as ds_core
    metadonnees = metadonnees or {}
    site = instance.replace("https://", "").replace("http://", "")
    # Un nom que l'exploitant reconnaît : « Faune Occitanie (Ariège) » plutôt que
    # « dép. 09 — www.faune-occitanie.org ». Le nom du département vient de `ref_geo`,
    # qui l'a déjà et dans l'orthographe de l'instance ; les données VisioNature ne
    # portent que le code.
    portail = metadonnees.get("nom_instance") or site
    lieu = ds_core.nom_departement(departement) if departement else None
    lieu = lieu or (f"dép. {departement}" if departement else None)
    morceaux = [m for m in (portail, f"({lieu})" if lieu else None, projet) if m]
    nom = " ".join(morceaux) if morceaux else f"Observations VisioNature — {site}"
    # Construit plutôt que tronqué : amputer le nom long donnait « dép. 09 — www.faune-oc »,
    # illisible dans les listes déroulantes où le nom court sert précisément à choisir.
    court = " ".join(x for x in ("VN", projet, departement) if x)[:30]
    jdd, cree = ds_core.upsert_dataset(
        source="VisioNature",
        cle=f"{instance}:{departement or ''}:{projet or ''}", licence="",
        nom=nom, shortname=court,
        description=(f"Observations moissonnées depuis {instance} via l'API Biolovision.\n\n"
                     f"Les codes atlas de nidification sont conservés dans additional_data : "
                     f"le SINP ne connaît pas leur gradation possible/probable/certaine."),
        id_acquisition_framework=af.id_acquisition_framework,
    )
    ds_core.attacher_territoires(
        jdd, metadonnees.get("territoires") or [],
        journal=lambda m: click.secho(f"    ⚠ {m}", fg="yellow"))
    ds_core.qualifier_dataset(
        # Le financement dépend du PROJET, non du département : la plupart des projets
        # VisioNature sont privés, une minorité relève d'un financement public.
        jdd, financement=(metadonnees.get("financement_par_projet", {}).get(projet or "")
                          or metadonnees.get("financement", "")),
        createur=metadonnees.get("createur", ""),
        journal=lambda m: click.secho(f"    ⚠ {m}", fg="yellow"))
    db.session.flush()
    if cree:
        click.secho(f"  + JDD créé : {jdd.id_dataset} — {nom}", fg="green")

    # Les acteurs sont posés à chaque passage, pas seulement à la création : une
    # configuration corrigée après coup doit pouvoir rattraper un jeu déjà créé.
    from .core import datasets as ds_core
    # Un même organisme peut porter plusieurs rôles — le Collectif est à la fois
    # fournisseur et contact principal — et `attacher_acteur` étant idempotent sur le
    # triplet (jeu, organisme, rôle), les deux lignes coexistent sans doublon.
    for nom_org, role in ((( producteurs or {}).get(departement or ""),
                           ds_core.ROLE_PRODUCTEUR),
                          (fournisseur, ds_core.ROLE_FOURNISSEUR),
                          (contact_principal, ds_core.ROLE_CONTACT_PRINCIPAL)):
        if not nom_org:
            continue
        libelle = {ds_core.ROLE_PRODUCTEUR: "producteur",
                   ds_core.ROLE_FOURNISSEUR: "fournisseur",
                   ds_core.ROLE_CONTACT_PRINCIPAL: "contact principal"}[role]
        id_org = ds_core.resoudre_organisme(nom_org)
        if id_org is None and creer_organismes:
            id_org = ds_core.creer_organisme(nom_org)
            if id_org is not None:
                click.secho(f"    + organisme créé : {nom_org} (id={id_org})", fg="green")
        if id_org is None:
            # Le producteur est obligatoire au SINP, le fournisseur ne l'est pas :
            # mettre les deux sur le même plan banaliserait l'avertissement qui compte.
            gravite = {
                ds_core.ROLE_PRODUCTEUR:
                    "Le jeu de données restera non conforme au SINP, qui exige un "
                    "producteur.",
                ds_core.ROLE_CONTACT_PRINCIPAL:
                    "Le formulaire de GeoNature exige un contact principal : le jeu "
                    "ne pourra pas y être enregistré.",
                ds_core.ROLE_FOURNISSEUR:
                    "Le fournisseur est facultatif ; le jeu reste conforme.",
            }[role]
            click.secho(f"    ⚠ {libelle} « {nom_org} » introuvable dans "
                        f"utilisateurs.bib_organismes. {gravite} Vérifiez "
                        f"l'orthographe — la résolution se fait sur le nom exact, aux "
                        f"espaces et à la casse près — ou activez "
                        f"[visionature] creer_organismes_manquants.", fg="yellow")
            continue
        if ds_core.attacher_acteur(jdd.id_dataset, id_org, role):
            click.echo(f"    + {libelle} : {nom_org}")
    return jdd


@click.command("visionature-reanonymiser")
@click.option("--yes", is_flag=True, help="Exécuter réellement. Sinon, simulation.")
def visionature_reanonymiser(yes):
    """Réaligne les noms d'observateurs sur leur consentement courant.

    Un observateur peut demander l'anonymat après coup, ou le lever. Ce changement ne
    se voit dans aucune empreinte de contenu — il porte sur l'observateur, pas sur
    l'observation —, donc ni le moissonnage incrémental ni le court-circuit sur la date
    de modification ne le rattrapent. D'où cette commande, à passer périodiquement.
    """
    from geonature.utils.config import config as gn_config
    from .core import reanonymisation as rea, synthese as syn_core
    from .sources.visionature import (api as vn_api, confidentialite as vn_conf)
    from .migrations.e91b4c07a2d8_source_visionature import SOURCE_NAME

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur VisioNature désactivé.")
    secret = cfg.get("pseudonymisation_secret", "")
    if not secret:
        raise click.ClickException("[visionature] pseudonymisation_secret manquant.")

    id_source = syn_core.get_source_id(SOURCE_NAME)
    click.echo("Chargement du référentiel des observateurs…")
    observateurs = vn_api.observateurs(cfg)
    forcer = cfg.get("forcer_anonymat", False)

    # Indexé par pseudonyme : c'est la seule clé présente en base, le nom réel n'y étant
    # pas conservé pour les observateurs anonymisés.
    souhaits = {}
    for o in observateurs:
        uid = str(o.get("@id") or o.get("id") or "").strip()
        if not uid:
            continue
        anonyme = forcer or str(o.get("anonymous") or "0").strip() in ("1", "true", "True")
        souhaits[vn_conf.pseudonyme(uid, secret)[:12]] = (
            anonyme, (o.get("name") or "").strip())
    click.echo(f"  {len(souhaits)} observateur(s), "
               f"{sum(1 for a, _ in souhaits.values() if a)} demandant l'anonymat")

    bilan = rea.appliquer(id_source, souhaits, dry_run=not yes)
    if yes:
        db.session.commit()

    click.secho(f"\n{'' if yes else 'SIMULATION — '}"
                f"{bilan['vers_pseudonyme']} vers pseudonyme, "
                f"{bilan['vers_nom']} vers nom réel, "
                f"{bilan['inchangees']} inchangée(s).", fg="green")
    if bilan["inconnues"]:
        click.secho(f"  {bilan['inconnues']} observation(s) dont l'observateur n'est plus "
                    f"dans le référentiel — laissées en l'état, leur pseudonyme étant "
                    f"la seule information disponible.", fg="yellow")
    if not yes and (bilan["vers_pseudonyme"] or bilan["vers_nom"]):
        click.secho("Relancez avec --yes pour appliquer.", fg="yellow")


@click.command("visionature-perimetres")
def visionature_perimetres():
    """Liste les unités territoriales de l'instance VisioNature.

    Sert à renseigner `[visionature] filtre_api`. Le `short_name` est le code employé
    par Client_API_VN pour filtrer — sur les instances régionales françaises, c'est le
    code de département, celui qu'on retrouve dans `place.county` de chaque observation.
    """
    from geonature.utils.config import config as gn_config
    from .sources.visionature import api as vn_api

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur VisioNature désactivé.")

    unites = vn_api.unites_territoriales(cfg)
    if not unites:
        click.secho("Aucune unité territoriale renvoyée par l'instance.", fg="yellow")
        return

    click.echo(f"{len(unites)} unité(s) territoriale(s) :\n")
    # L'identifiant à employer dans `search` n'est ni l'`id` ni le `short_name` : c'est
    # leur concaténation, `id_country` suivi du `short_name`. L'afficher évite d'avoir à
    # la deviner — l'Ariège est « 109 », l'Aude « 111 ».
    click.echo(f"  {'id':>4}  {'short_name':<12}  {'à employer':<12}  nom")
    for u in unites:
        click.echo(f"  {str(u.get('id') or u.get('@id') or ''):>4}  "
                   f"{str(u.get('short_name') or ''):<12}  "
                   f"{str(vn_api.identifiant_territoire(u) or '—'):<12}  "
                   f"{u.get('name') or ''}")
    click.echo("\nRestreindre le moissonnage, dans connectors_config.toml :\n"
               "  [visionature]\n"
               "  departements = [\"09\"]              # vérifié sur place.county\n"
               "  # colonne « à employer » ci-dessus pour --territoire\n"
               "\nLe second réduit le volume téléchargé, le premier garantit le "
               "périmètre : un paramètre inconnu de l'API est ignoré sans erreur.")


@click.command("visionature-groupes")
def visionature_groupes():
    """Liste les groupes taxonomiques de l'instance VisioNature.

    Sert à renseigner `--taxo-group`, et à vérifier la correspondance employée par le
    dispositif de reproduction : celui-ci s'appuie sur le **code** du groupe
    (`TAXO_GROUP_REPTILIAN`…), stable d'une instance à l'autre, et non sur l'identifiant
    numérique dont rien ne garantit la stabilité.
    """
    from geonature.utils.config import config as gn_config
    from .sources.visionature import api as vn_api, reproduction as vn_repro

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur VisioNature désactivé.")

    groupes = vn_api.groupes_taxonomiques(cfg)
    if not groupes:
        click.secho("Aucun groupe taxonomique renvoyé par l'instance.", fg="yellow")
        return

    couverts = set(vn_repro.REGLES)
    click.echo(f"{len(groupes)} groupe(s) taxonomique(s) :\n")
    click.echo(f"  {'id':>4}  {'code':<26}  {'repro':<6}  {'accès':<8}  nom")
    for g in groupes:
        identifiant = str(g.get("id") or g.get("@id") or "")
        # `name_constant` et non `name` : ce dernier est le libellé traduit.
        code = str(g.get("name_constant") or "")
        repro = "oui" if code in couverts else "—"
        click.echo(f"  {identifiant:>4}  {code:<26}  {repro:<6}  "
                   f"{str(g.get('access_mode') or ''):<8}  {g.get('name') or ''}")
    click.echo("\nColonne « repro » : le groupe dispose-t-il de règles de déduction du "
               "statut de reproduction ?\nLes oiseaux passent par les codes atlas, pas "
               "par ces règles — ils affichent donc « — » sans que ce soit un manque.")


@click.command("dbchiro-import")
@click.option("--perimetre", "area", default="", help="Identifiant de zonage dbChiro (défaut : configuration).")
@click.option("--departement", "departements", multiple=True,
              help="Codes de département à conserver (défaut : configuration).")
@click.option("--importer-absences/--ecarter-absences", default=None,
              help="Verser les codes d'absence en STATUT_OBS « Non observé » "
                   "(défaut : configuration).")
@click.option("--max-resultats", "max_results", default=0, type=int,
              help="Plafonne le nombre d'observations moissonnées (0 = tout). Les plus "
                   "récemment modifiées d'abord — pour un premier essai d'écriture, pas "
                   "pour un échantillon représentatif.")
@click.option("--lot", "batch_size", default=None, type=int)
@click.option("--dry-run", is_flag=True)
def dbchiro_import(area, departements, importer_absences, max_results, batch_size,
                   dry_run):
    """Importe des observations dbChiro dans la Synthèse."""
    from geonature.utils.config import config as gn_config
    from .core import (report as report_core, synthese as syn_core,
                       datasets as ds_core, nomenclatures as nomen_core)
    from .sources.dbchiro import (api as db_api, taxonomy as db_taxo,
                                  transform as db_tr)
    from .migrations.a3f6c81b0e52_source_dbchiro import SOURCE_NAME, CA_UUID

    cfg = dict((gn_config.get("CONNECTORS") or {}).get("dbchiro", {}))
    if not cfg.get("enabled"):
        raise click.ClickException(
            "Connecteur dbChiro désactivé. Renseignez [dbchiro] dans la configuration "
            "et passez `enabled = true`.")
    for cle in ("url", "username", "password"):
        if not cfg.get(cle):
            raise click.ClickException(
                f"[dbchiro] {cle} manquant. L'API de dbChiro n'expose aucun jeton : "
                f"le connecteur se connecte avec un compte de service, dont les droits "
                f"déterminent le périmètre visible.")
    if area:
        cfg["area"] = area
    if importer_absences is not None:
        cfg["importer_absences"] = importer_absences

    pseudonymiser = cfg.get("pseudonymiser_observateurs", False)
    secret = cfg.get("pseudonymisation_secret", "")
    if pseudonymiser and not secret:
        raise click.ClickException(
            "[dbchiro] pseudonymisation_secret manquant alors que "
            "pseudonymiser_observateurs est actif. Une clé par défaut rendrait les "
            "pseudonymes recalculables par un tiers, donc réidentifiables.")

    batch_size = batch_size or cfg.get("batch_size", 1000)
    instance = cfg["url"].rstrip("/")
    codes_dep = {c.strip().upper().zfill(2) if c.strip().isdigit() else c.strip().upper()
                 for c in (list(departements) or cfg.get("departements") or []) if c.strip()}
    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_module = syn_core.get_module_id("CONNECTORS")
    srid = syn_core.local_srid()
    af = ds_core.get_acquisition_framework(CA_UUID)
    v_taxref = syn_core.version_taxref()
    click.secho(f"instance={instance} source={id_source} srid={srid} "
                f"taxref={v_taxref or 'inconnu'}", fg="green")
    if not v_taxref:
        click.secho("  ⚠ paramètre `taxref_version` absent de gn_commons.t_parameters : "
                    "meta_v_taxref restera NULL.", fg="yellow")

    # `url_source` pointe sur la redirection du module. Le permalien dbChiro est
    # `/sighting/<id>/detail` : l'identifiant est au milieu du chemin, donc la
    # concaténation du cœur — `url_source + '/' + entity_source_pk_value` — ne peut rien
    # produire de valide. `blueprint.voir_dans_dbchiro` reconstruit l'URL complète, et
    # `entity_source_pk_value` garde l'identifiant brut.
    api = str(gn_config.get("API_ENDPOINT") or "").rstrip("/")
    if api:
        db.session.execute(
            db_text("UPDATE gn_synthese.t_sources SET url_source = :u "
                    "WHERE id_source = :s AND url_source IS DISTINCT FROM :u"),
            {"u": f"{api}/connectors/dbchiro", "s": id_source})
    else:
        click.secho("  ⚠ API_ENDPOINT absent de la configuration GeoNature : le bouton "
                    "« voir la donnée source » ne sera pas alimenté.", fg="yellow")

    # La table taxonomique est vérifiée contre TAXREF avant toute écriture : un cd_nom
    # déprécié par une montée de version satisfait la clé étrangère sans qu'aucun
    # contrôle ne le signale.
    anomalies = db_taxo.verifier_table(journal=click.echo)
    if anomalies:
        raise click.ClickException(
            "La table taxonomique dbChiro ne correspond plus au TAXREF de l'instance. "
            "Corrigez `sources/dbchiro/taxonomy.py` avant d'importer.")

    click.echo("Connexion à l'instance dbChiro…")
    try:
        session = db_api.connecter(cfg)
        features = db_api.observations(session, cfg, journal=click.echo,
                                       max_results=max_results)
    except db_api.ErreurDbChiro as exc:
        raise click.ClickException(str(exc))
    click.echo(f"  {len(features)} observation(s) reçue(s).")

    resolver = nomen_core.Resolver()
    cfg_valid = (gn_config.get("CONNECTORS") or {}).get("validation", {})
    statut_validation = cfg_valid.get("status") if cfg_valid.get("enabled") else None
    niveau_diffusion = cfg.get("niveau_diffusion", "")
    if niveau_diffusion:
        click.echo(f"  niveau de diffusion appliqué : NIV_PRECIS « {niveau_diffusion} »")
    if not pseudonymiser:
        click.secho("  observateurs publiés en clair — dbChiro ne porte aucun marqueur "
                    "de consentement individuel, ce choix engage l'accord de "
                    "l'exploitant.", fg="yellow")

    rejets = report_core.Rejects()
    jdd = None
    lot, lus, ecrits, maj, hors_perimetre = [], 0, 0, 0, 0

    def _ecrire(lignes):
        nonlocal jdd
        if not lignes:
            return (0, 0)
        if jdd is None:
            jdd = _jdd_dbchiro(instance, af)
        for ligne in lignes:
            ligne["id_dataset"] = jdd.id_dataset
        return syn_core.insert_batch(lignes)

    for feature in features:
        lus += 1
        properties = feature.get("properties") or {}
        identifiant = feature.get("id")
        if not db_tr.dans_perimetre(properties, codes_dep):
            hors_perimetre += 1
            rejets.add("hors_perimetre", identifiant, db_taxo.nom_cite(properties),
                       f"departement={db_tr.departement(properties)}")
            continue
        cd_nom, motif = db_taxo.resolve(
            properties, importer_absences=cfg.get("importer_absences", False))
        if cd_nom is None:
            rejets.add(motif, identifiant, db_taxo.nom_cite(properties),
                       f"codesp={db_taxo.codesp(properties)}")
            continue
        ligne = db_tr.to_row(
            feature, cd_nom=cd_nom, id_dataset=None, id_source=id_source,
            id_module=id_module, srid=srid, resolver=resolver, instance=instance,
            absence=db_taxo.est_absence(properties),
            statut_validation=statut_validation, pseudonymiser=pseudonymiser,
            secret_pseudo=secret, code_diffusion=niveau_diffusion,
            version_taxref=v_taxref)
        if ligne is None:
            rejets.add("no_coordinates", identifiant, db_taxo.nom_cite(properties),
                       "géométrie ou date absente")
            continue
        lot.append(ligne)
        if len(lot) >= batch_size and not dry_run:
            i, u = _ecrire(lot)
            ecrits += i; maj += u
            db.session.commit(); lot = []
            click.echo(f"    … {ecrits} écrites, {maj} mises à jour")

    if lot and not dry_run:
        i, u = _ecrire(lot)
        ecrits += i; maj += u
    if dry_run:
        # Sans cette interrogation, la simulation annonce comme « à écrire » des
        # observations déjà présentes. dbChiro relit tout le corpus à chaque passage,
        # faute d'incrémental : à partir du deuxième moissonnage, la quasi-totalité des
        # lignes est déjà en base et l'`ON CONFLICT` n'y touchera pas.
        deja = syn_core.compter_existants(lot)
        ecrits = len(lot) - deja
        maj = deja
    else:
        db.session.commit()

    if hors_perimetre:
        # Un rejet massif alors qu'un filtre serveur est configuré signale que l'API l'a
        # ignoré : un paramètre inconnu de DRF est écarté sans la moindre erreur.
        click.secho(f"  {hors_perimetre} observation(s) hors périmètre écartée(s).",
                    fg="yellow" if cfg.get("area") else None)
        if cfg.get("area") and hors_perimetre > lus / 10:
            click.secho("  ⚠ le filtre serveur `area` semble ignoré : la quasi-totalité "
                        "de l'instance a été téléchargée avant d'être écartée ici. "
                        "Vérifiez l'identifiant avec `dbchiro-perimetres`.", fg="yellow")

    verbes = ("à écrire", "déjà en base") if dry_run else ("écrite(s)", "mise(s) à jour")
    click.secho(f"\n{'DRY-RUN — ' if dry_run else ''}{lus} observation(s) lue(s), "
                f"{ecrits} {verbes[0]}, {maj} {verbes[1]}, {len(rejets)} rejetée(s).",
                fg="green")
    for ligne in rejets.summary_lines():
        click.echo(ligne)
    chemin = rejets.write_csv(Path("dbchiro_rejets.csv"))
    if chemin:
        click.echo(f"  Journal détaillé : {chemin}")


def _jdd_dbchiro(instance: str, af):
    """JDD unique de l'instance, créé à la première écriture.

    ⚠ Le découpage naturel serait l'**étude** dbChiro (`management.Study`) : ce sont des
    programmes réels, comme les codes projet de VisioNature. Le champ existe sur la
    session et figure dans le `select_related` du queryset, mais le serializer de
    `/api/v1/search` ne l'expose pas — couverture nulle sur les 8 039 observations
    mesurées. Un JDD par étude deviendra possible dès que dbChiro publiera le champ.
    """
    from .core import datasets as ds_core
    site = instance.replace("https://", "").replace("http://", "")
    jdd, cree = ds_core.upsert_dataset(
        source="dbChiro", cle=instance, licence="",
        nom=f"Observations dbChiro — {site}",
        description=(
            f"Observations de chiroptères moissonnées depuis {instance}.\n\n"
            f"La détermination d'origine est conservée dans additional_data : TAXREF ne "
            f"propose aucun agrégat pour les chiroptères, et les déterminations "
            f"partielles (« Myotis myotis / M. blythii », « Plecotus sp. ») portent donc "
            f"le cd_nom du genre, de la famille ou de l'ordre. Le champ « determination » "
            f"dit ce qui a réellement été identifié.\n\n"
            f"Les dates sont sans heure : l'API n'expose pas l'heure de début de session."),
        id_acquisition_framework=af.id_acquisition_framework,
    )
    db.session.flush()
    if cree:
        click.secho(f"  + JDD créé : {jdd.id_dataset}", fg="green")
    return jdd


@click.command("dbchiro-perimetres")
@click.option("--nom", "recherche", default="", help="Filtre sur le nom du zonage.")
def dbchiro_perimetres(recherche):
    """Liste les zonages de l'instance dbChiro, pour renseigner [dbchiro] area."""
    from geonature.utils.config import config as gn_config
    from .sources.dbchiro import api as db_api

    cfg = (gn_config.get("CONNECTORS") or {}).get("dbchiro", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur dbChiro désactivé.")
    try:
        session = db_api.connecter(cfg)
        zonages = db_api.zonages(session, cfg, recherche)
    except db_api.ErreurDbChiro as exc:
        raise click.ClickException(str(exc))

    if not zonages:
        click.secho("Aucun zonage renvoyé par l'instance.", fg="yellow")
        return
    click.echo(f"{len(zonages)} zonage(s) :\n")
    click.echo(f"  {'id':>8}  libellé")
    for zone in zonages:
        click.echo(f"  {str(zone.get('id') or ''):>8}  {zone.get('text') or ''}")
    click.echo("\nReportez l'identifiant voulu dans [dbchiro] area. Il est propre à "
               "cette instance : ne le recopiez pas d'une autre.")
@click.command("visionature-vider-cache")
def visionature_vider_cache():
    """Efface le cache disque des référentiels.

    À faire dès que la mise au point est terminée : le cache des observateurs contient
    des noms de personnes, et un référentiel périmé produit des correspondances fausses
    sans rien signaler.
    """
    from geonature.utils.config import config as gn_config
    from .core import cache as cache_core

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    n = cache_core.vider(cfg.get("cache_dir") or None)
    click.secho(f"{n} fichier(s) de cache supprimé(s).", fg="green" if n else None)


@click.command("visionature-diagnostic")
@click.option("--groupe-taxo", "groupe", default="1", help="Groupe à sonder (défaut : 1).")
@click.option("--trace", "debug", is_flag=True,
              help="Journalise la requête réelle, pour comparer avec transfer_vn.")
@click.option("--jours", default=60,
              help="Fenêtre des sondes de recherche, en jours (défaut : 60).")
@click.option("--fin", default="",
              help="Date de fin des sondes de recherche (AAAA-MM-JJ). Par défaut, "
                   "aujourd'hui. Sert à sonder une période ancienne.")
@click.option("--perimetre", "territoire", default="",
              help="Unité territoriale à sonder (id_country + short_name, ex. 109). "
                   "Par défaut, celles déduites de [visionature] departements.")
def visionature_diagnostic(groupe, debug, jours, fin, territoire):
    """Sonde les points d'entrée de l'API et rapporte ce que le compte peut faire.

    Les droits Biolovision ne sont pas uniformes : `observations/diff` peut fonctionner
    quand `observations` en liste est refusé — c'est l'appel le plus lourd de la
    plateforme, fréquemment restreint. Le code HTTP est ce qui distingue un droit
    manquant (403) d'un appel mal formé (400), et cette distinction commande la suite :
    demander une ouverture de droit, ou corriger la requête.
    """
    from datetime import datetime, timedelta, timezone
    from geonature.utils.config import config as gn_config
    from .sources.visionature import api as vn_api
    from .sources.visionature.biolovision import api as bio

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur VisioNature désactivé.")

    # Les versions comptent : une signature OAuth1 dépend de l'implémentation qui la
    # produit. Client_API_VN exige requests>=2.32 et requests-oauthlib>=2.0 ; tourner
    # avec une version antérieure peut produire une signature que l'API refuse, ce qui
    # donne un 403 indiscernable d'un droit manquant. Comparer avec le venv qui fait
    # tourner transfer_vn est le premier réflexe quand les habilitations sont identiques.
    import requests as _requests
    import requests_oauthlib as _oauthlib
    click.echo(f"requests {_requests.__version__}, "
               f"requests_oauthlib {getattr(_oauthlib, '__version__', 'inconnue')} "
               f"(Client_API_VN exige >= 2.32 et >= 2.0)\n")

    if debug:
        # `_clean_params` masque le compte et le mot de passe : la sortie est
        # communicable telle quelle.
        import logging as _logging
        _logging.basicConfig(level=_logging.DEBUG)
        _logging.getLogger("gn_module_connectors.sources.visionature.biolovision.api"
                           ).setLevel(_logging.DEBUG)
        for nom in list(_logging.root.manager.loggerDict):
            if "biolovision" in nom:
                _logging.getLogger(nom).setLevel(_logging.DEBUG)

    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    obs = vn_api._controleur(bio.ObservationsAPI, cfg)

    def sonder(intitule, appel, observations=False):
        try:
            reponse = appel()
        except bio.HTTPError as erreur:
            code = erreur.args[0] if erreur.args else "?"
            # 401 et 403 ne disent pas la même chose, et la confusion coûte cher.
            # 401 « Can't verify request, missing oauth_consumer_key » : la signature
            # OAuth n'est pas vérifiable — client_key ou client_secret absent ou faux,
            # l'API ne reconnaît pas le demandeur.
            # 403 : demandeur reconnu, mais pas autorisé sur cette ressource. La clé est
            # donc valide ; c'est son périmètre qui est en cause.
            sens = {401: "clé non reconnue — vérifier client_key et client_secret",
                    403: "clé valide, mais périmètre insuffisant",
                    404: "point d'entrée absent",
                    400: "requête refusée (l'accès, lui, existe)"}.get(code, "")
            click.secho(f"  {intitule:<34} HTTP {code}  {sens}", fg="yellow")
            return None
        except bio.BiolovisionApiException as erreur:
            click.secho(f"  {intitule:<34} échec  {erreur!r}", fg="yellow")
            return None
        entrees = vn_api._extraire(reponse)
        click.secho(f"  {intitule:<34} OK     {len(entrees)} entrée(s)", fg="green")
        if entrees:
            # La forme de l'entrée commande toute la conception : une entrée réduite à un
            # identifiant impose une requête par relevé, ce qui ne passe pas à l'échelle.
            premiere = entrees[0]
            if isinstance(premiere, dict):
                complet = vn_api.est_releve_complet(premiere)
                click.echo(f"  {'':<34}        relevé complet : "
                           f"{'oui' if complet else 'NON — un api_get par entrée'}")
                # ⚠ Compter sur TOUTES les entrées, jamais sur la première. `details`,
                # `behaviours`, `medias`, `extended_info` et `atlas_code` sont
                # optionnels : ils n'apparaissent que renseignés. Conclure de n=1 que
                # l'API ne les renvoie pas serait une erreur — un lézard isolé sans
                # comportement noté n'en porte aucun.
                from collections import Counter as _Counter
                freq_sighting, freq_obs, freq_place = _Counter(), _Counter(), _Counter()
                for e in entrees:
                    if not isinstance(e, dict):
                        continue
                    # `.keys()` et non le dict : `Counter.update(mapping)` ajoute les
                    # VALEURS comme effectifs, ce qui explose sur des valeurs textuelles.
                    freq_sighting.update(e.keys())
                    liste = e.get("observers")
                    if isinstance(liste, list) and liste and isinstance(liste[0], dict):
                        freq_obs.update(liste[0].keys())
                    lieu = e.get("place")
                    if isinstance(lieu, dict):
                        freq_place.update(lieu.keys())

                total = len(entrees)

                def _lister(intitule, freq):
                    if not freq:
                        return
                    click.echo(f"  {'':<34}        {intitule} sur {total} relevé(s) :")
                    for champ, n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
                        part = "" if n == total else f"  ({n})"
                        click.echo(f"  {'':<34}          {champ}{part}")

                # Les valeurs comptent autant que la présence : c'est en supposant le
                # sens d'un code d'énumération qu'on pseudonymise tout le monde.
                for champ in ("anonymous", "anonymous_in_export", "second_hand"):
                    valeurs = _Counter()
                    for e in entrees:
                        liste = (e or {}).get("observers")
                        if isinstance(liste, list) and liste and isinstance(liste[0], dict):
                            if champ in liste[0]:
                                valeurs[str(liste[0][champ])] += 1
                    if valeurs:
                        detail = ", ".join(f"{v!r} ({n})"
                                           for v, n in valeurs.most_common())
                        click.echo(f"  {'':<34}        {champ} : {detail}")

                _lister("champs du relevé", freq_sighting)
                _lister("champs de observers[0]", freq_obs)
                _lister("champs de place", freq_place)

                # ⚠ Tous ces champs ne sont pas attendus partout. `atlas_code` est un
                # code de nidification EOAC : il ne concerne QUE les oiseaux, et son
                # absence sur un groupe de reptiles ne signale rien. Le signaler comme
                # un manque enverrait chercher un défaut là où il n'y en a pas.
                attendus = {} if not observations else {
                    "timing": ("heure d'observation", "tous"),
                    "uuid": ("identifiant SINP du producteur", "tous"),
                    "name": ("nom de l'observateur", "forme longue"),
                    "anonymous": ("consentement d'anonymat", "forme longue"),
                    "atlas_code": ("reproduction des oiseaux", "oiseaux seulement"),
                    "details": ("âge et sexe", "quand l'observateur les ventile"),
                    "behaviours": ("comportement", "quand il est noté"),
                    "medias": ("preuve d'existence", "quand une photo est jointe"),
                    "extended_info": ("mortalité", "quand l'animal est trouvé mort"),
                    "project_code": ("jeu de données par projet", "quand un projet existe"),
                }
                manquants = [(c, u, p) for c, (u, p) in attendus.items()
                             if c not in freq_obs]
                systematiques = [f"{c} ({u})" for c, u, p in manquants if p == "tous"]
                conditionnels = [f"{c} ({u} — {p})" for c, u, p in manquants
                                 if p != "tous"]
                if systematiques:
                    click.secho(f"  {'':<34}        ⚠ absent(s) alors qu'attendu(s) "
                                f"partout : {'; '.join(systematiques)}", fg="yellow")
                if conditionnels:
                    click.echo(f"  {'':<34}        non rencontré(s), ce qui peut être "
                               f"normal : {'; '.join(conditionnels)}")
        return reponse

    click.echo(f"Instance {cfg['url']}, groupe taxonomique {groupe} :\n")
    sonder("taxo_groups (liste)", lambda: bio.TaxoGroupsAPI(
        user_email=cfg["user_email"], user_pw=cfg["user_password"],
        base_url=cfg["url"].rstrip("/") + "/", client_key=cfg["client_key"],
        client_secret=cfg["client_secret"], timeout=60).api_list())
    # Deux variantes : `transfer_vn` passe TOUJOURS short_version. Son absence est la
    # première suspecte d'un 403 sur la forme longue d'un groupe entier.
    sonder("observations (liste, forme longue)", lambda: obs.api_list(groupe))
    sonder("observations (liste, short_version)",
           lambda: obs.api_list(groupe, short_version="1"))
    modifiees = sonder("observations/diff (modifiées)",
                       lambda: obs.api_diff(groupe, recent, "only_modified"))
    sonder("observations/diff (supprimées)",
           lambda: obs.api_diff(groupe, recent, "only_deleted"))

    # Récupération par identifiant : les deux voies qui portent de la donnée à partir
    # d'un différentiel. `api_get` en demande une, `api_list(id_sightings_list=…)` en
    # demande cent — c'est cette dernière qu'emploie `_store_update` de transfer_vn.
    identifiants = [vn_api.identifiant(e) for e in vn_api._extraire(modifiees or [])]
    identifiants = [c for c in identifiants if c][:5]
    if identifiants:
        click.echo(f"\n  Récupération par identifiant, sur {identifiants[0]} :")
        sonder("observations/<id> (api_get)",
               lambda: obs.api_get(identifiants[0]), observations=True)
        sonder(f"observations?id_sightings_list ({len(identifiants)})",
               lambda: obs.api_list(groupe,
                                    id_sightings_list=",".join(identifiants),
                                    short_version=vn_api.SHORT_VERSION),
               observations=True)
    else:
        click.secho("  (aucun identifiant à sonder : le différentiel est vide)",
                    fg="yellow")
    # Paramètres relevés sur `transfer_vn`, et non devinés : `period_choice` est
    # obligatoire et les dates sont au format JJ.MM.AAAA. La sonde précédente envoyait
    # de l'ISO sans `period_choice` — son 403 ne prouvait donc rien.
    # Une fenêtre d'un seul jour peut ne rien contenir — les reptiles ariégeois n'ont
    # pas d'observation quotidienne — et une sonde vide n'apprend rien sur la forme des
    # données. Soixante jours donnent un échantillon dans presque tous les cas.
    # Le journal de la LPO montre des oiseaux téléchargés sur l'Ariège en janvier 2019,
    # avec exactement la même requête. Si 2019 passe et 2026 non, l'API restreint les
    # données récentes — ce que ni le code ni les identifiants ne peuvent expliquer.
    if fin:
        try:
            fin = datetime.fromisoformat(fin).replace(tzinfo=timezone.utc)
        except ValueError:
            raise click.ClickException(f"--fin {fin!r} n'est pas une date ISO.")
    else:
        fin = datetime.now(timezone.utc)
    debut = fin - timedelta(days=jours)
    click.echo(f"Sondes de recherche : {debut:%Y-%m-%d} → {fin:%Y-%m-%d}\n")
    sonder(f"observations/search ({jours} j, sans périmètre)", lambda: obs.api_search(
        vn_api.parametres_recherche(groupe, debut, fin), short_version="1"),
           observations=True)

    # `_store_search` de transfer_vn n'émet JAMAIS de recherche sans périmètre : sa
    # boucle `for t_u in t_us:` pose systématiquement `location_choice` et
    # `territorial_unit_ids`. Une recherche non bornée n'est donc pas ce qu'ils envoient,
    # et l'API peut légitimement la refuser — c'est un balayage de toute l'instance.
    if territoire:
        # Sonder un territoire imposé permet d'isoler la variable territoriale. Le
        # journal de transfer_vn de la LPO montre un téléchargement d'oiseaux sur
        # l'unité 11 (Aude) là où nos sondes portaient sur 09 (Ariège).
        territoires = [territoire]
    else:
        voulus = {str(d).strip().zfill(2) for d in (cfg.get("departements") or [])}
        try:
            unites = vn_api.unites_territoriales(cfg)
        except bio.BiolovisionApiException:
            unites = []
        territoires = [t for t in (vn_api.identifiant_territoire(u) for u in unites
                                   if not voulus
                                   or str(u.get("short_name") or "") in voulus) if t]
    if territoires:
        apercu = ", ".join(territoires[:3]) + ("…" if len(territoires) > 3 else "")
        # Les deux formes, côte à côte. La forme courte ampute `observers[]` de
        # `atlas_code`, `details`, `behaviours`, `timing`, `uuid`, `medias`,
        # `extended_info`, `project_code`, `second_hand` et `name` — soit l'essentiel de
        # ce que le module exploite. Le constat doit être visible, pas déduit.
        for version, etiquette in (("1", "forme courte"), ("0", "forme longue")):
            sonder(f"observations/search ({jours} j, {apercu}, {etiquette})",
                   lambda v=version: obs.api_search(
                       vn_api.parametres_recherche(groupe, debut, fin, territoires[:1]),
                       short_version=v), observations=True)
    else:
        click.secho("  observations/search (avec périmètre)  ignoré — aucune unité "
                    "territoriale exploitable", fg="yellow")

    click.echo("\nLa ligne « relevé complet » est décisive : si le différentiel ne livre\n"
               "que des identifiants, chaque entrée impose une requête supplémentaire.\n"
               "À 37 000 modifications par jour et par groupe, ce n'est pas tenable.\n")
    click.echo("\nLa méthode `list` est DÉPRÉCIÉE en amont — transfer_vn journalise\n"
               "« Download using list method is deprecated, please use search method only ».\n"
               "Son 403 est donc attendu ; c'est `search` avec périmètre qui compte.\n")
    click.echo("\n401 partout -> la clé n'est pas reconnue : client_key ou client_secret\n"
               "               absent ou erroné. Rien à voir avec les droits.\n"
               "403 partout -> la clé est reconnue mais son périmètre ne couvre pas la\n"
               "               ressource. C'est une question d'habilitation, pas de code.\n")
    click.echo("\nLecture :\n"
               "  search avec périmètre OK              -> c'était le périmètre manquant,\n"
               "                                           pas le droit.\n"
               "  forme longue 403 mais short_version OK -> c'était le volume, pas le droit.\n"
               "  search OK                             -> moissonnage initial possible par\n"
               "                                           tranches de dates.\n"
               "  tout en 403                           -> alors seulement, demander\n"
               "                                           l'ouverture du droit.\n"
               "\n`access_mode` de visionature-groupes indique par ailleurs les groupes fermés au\n"
               "compte : `transfer_vn` saute ceux dont il vaut « none ».")


@click.command("visionature-volumetrie")
@click.option("--jours", default=1, help="Fenêtre de mesure, en jours (défaut : 1).")
@click.option("--via-recherche/--sans-recherche", "recherche", default=True,
              help="Sonder aussi `search` groupe par groupe (défaut : oui).")
def visionature_volumetrie(jours, recherche):
    """Mesure le nombre de modifications quotidiennes, groupe par groupe.

    Question à laquelle elle répond : quels groupes peut-on réellement moissonner ?

    Le différentiel ne renvoie que des identifiants — `id_sighting`, `id_universal`,
    `modification_type` — donc chaque entrée impose ensuite un `api_get`. Le coût d'un
    moissonnage incrémental est ainsi d'une requête HTTP par observation modifiée.
    Mesuré sur Faune-Occitanie : 37 246 modifications en vingt-quatre heures pour les
    seuls oiseaux, ce qui est hors de portée. D'autres groupes seront très en deçà.

    Le total par groupe est donc le chiffre qui décide de ce qui est faisable, et il
    n'est connaissable que sur l'instance visée.
    """
    from datetime import datetime, timedelta, timezone
    from geonature.utils.config import config as gn_config
    from .sources.visionature import api as vn_api, reproduction as vn_repro
    from .sources.visionature.biolovision import api as bio

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    if not cfg.get("enabled"):
        raise click.ClickException("Connecteur VisioNature désactivé.")

    depuis = (datetime.now(timezone.utc) - timedelta(days=jours)).strftime("%Y-%m-%d")
    fin_rech = datetime.now(timezone.utc)
    debut_rech = fin_rech - timedelta(days=jours)
    groupes = vn_api.groupes_taxonomiques(cfg)
    obs = vn_api._controleur(bio.ObservationsAPI, cfg)
    couverts = set(vn_repro.REGLES)

    # `search` est sondé groupe par groupe parce qu'on ne sait pas ce qui le fait
    # refuser. Un jour d'oiseaux en Ariège a été refusé quand soixante jours de reptiles
    # sur le même territoire passaient : ce n'est ni le volume seul, ni le droit — la
    # carte complète est le seul moyen d'y voir clair.
    territoires = []
    if recherche:
        voulus = {str(d).strip().zfill(2) for d in (cfg.get("departements") or [])}
        try:
            unites = vn_api.unites_territoriales(cfg)
        except bio.BiolovisionApiException:
            unites = []
        territoires = [t for t in (vn_api.identifiant_territoire(u) for u in unites
                                   if not voulus
                                   or str(u.get("short_name") or "") in voulus) if t][:1]
        if not territoires:
            click.secho("  ⚠ aucun territoire exploitable : `search` ne sera pas sondé.",
                        fg="yellow")
            recherche = False

    click.echo(f"Modifications sur {jours} jour(s), depuis {depuis}.")
    click.echo("Une requête api_get sera nécessaire par entrée du différentiel.")
    if recherche:
        click.echo(f"`search` sondé sur {jours} jour(s), territoire {territoires[0]}.")
    click.echo("")
    entete = f"  {'id':>4}  {'code':<26}  {'modifiées':>10}  {'/jour':>8}  repro"
    click.echo(entete + ("   search" if recherche else ""))

    total = 0
    mesures = []
    for g in groupes:
        identifiant = str(g.get("id") or g.get("@id") or "")
        code = str(g.get("name_constant") or g.get("name") or "")
        try:
            n = len(vn_api._extraire(obs.api_diff(identifiant, depuis, "only_modified")))
        except bio.BiolovisionApiException as erreur:
            click.secho(f"  {identifiant:>4}  {code:<26}  {'refusé':>10}  {erreur!r}",
                        fg="yellow")
            continue
        total += n
        mesures.append((n, identifiant, code))

        etat = ""
        if recherche:
            try:
                trouves = vn_api._extraire(obs.api_search(
                    vn_api.parametres_recherche(identifiant, debut_rech, fin_rech,
                                                territoires),
                    short_version=vn_api.SHORT_VERSION))
                etat = f"   OK {len(trouves)}"
            except bio.HTTPError as erreur:
                etat = f"   HTTP {erreur.args[0] if erreur.args else '?'}"
            except bio.BiolovisionApiException:
                etat = "   échec"

        couleur = "red" if n > 5000 else ("yellow" if n > 500 else None)
        click.secho(f"  {identifiant:>4}  {code:<26}  {n:>10}  {n / jours:>8.0f}  "
                    f"{'oui' if code in couverts else '—':<5}{etat}", fg=couleur)

    click.echo(f"\n  Total : {total} modification(s), soit {total / jours:.0f} par jour")
    click.echo("  Donc autant de requêtes api_get par moissonnage incrémental quotidien.")
    if mesures:
        mesures.sort(reverse=True)
        gros = [c for n, _, c in mesures[:3]]
        part = sum(n for n, _, _ in mesures[:3]) / total * 100 if total else 0
        click.echo(f"  Les trois premiers groupes ({', '.join(gros)}) pèsent "
                   f"{part:.0f} % du total.")
    click.echo("\nRestreindre le moissonnage aux groupes utiles, "
               "dans connectors_config.toml :\n"
               "  [visionature]\n"
               "  taxo_groups = [\"2\", \"6\", \"7\"]   # identifiants ci-dessus")


@click.command("visionature-purge")
@click.option("--projet", default="",
              help="Code projet VisioNature dont le JDD est visé.")
@click.option("--taxon", default="",
              help="Groupe taxonomique à retirer, par son nom TAXREF : règne, phylum, "
                   "classe, ordre, famille, ou début de nom scientifique. "
                   "Exemple : --taxon Reptilia")
@click.option("--incertitude-max", "max_uncertainty", default=0, type=int,
              help="Retirer les observations dont l'incertitude dépasse N mètres. "
                   "Alimentée depuis place.loc_precision, quand l'instance la donne.")
@click.option("--tout", is_flag=True,
              help="Purger toute la source VisioNature, sans autre critère.")
@click.option("--supprimer-jdd-vides", "drop_empty_datasets", is_flag=True,
              help="Supprimer ensuite les JDD du cadre VisioNature devenus vides.")
@click.option("--yes", is_flag=True,
              help="Exécuter réellement. Sans ce drapeau, la commande se contente "
                   "d'afficher ce qu'elle supprimerait.")
def visionature_purge(projet, taxon, max_uncertainty, tout, drop_empty_datasets, yes):
    """Supprime des observations VisioNature déjà importées.

    Indispensable après une correction du connecteur : ce qui est en base a été écrit
    par le code de l'époque, et aucune réécriture ne rattrape un champ qui n'était pas
    lu — un observateur pseudonymisé faute d'avoir su lire son consentement le reste.

    La suppression est toujours bornée à la source VisioNature. Les données saisies
    localement, celles d'Occtax et celles des autres connecteurs ne sont jamais touchées.
    """
    from sqlalchemy import select as sa_select
    from geonature.core.gn_meta.models import TDatasets
    from geonature.utils.config import config as gn_config
    from .core import synthese as syn_core, datasets as ds_core
    from .migrations.e91b4c07a2d8_source_visionature import SOURCE_NAME, CA_UUID

    cfg = (gn_config.get("CONNECTORS") or {}).get("visionature", {})
    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_dataset, cible = None, ""
    if projet:
        instance = str(cfg.get("url") or "").rstrip("/")
        if not instance:
            raise click.ClickException(
                "[visionature] url est nécessaire pour retrouver le JDD d'un projet.")
        reference = str(ds_core.dataset_uuid("VisioNature", f"{instance}:{projet}", ""))
        jdd = db.session.scalar(
            sa_select(TDatasets).where(TDatasets.unique_dataset_id == reference))
        if jdd is None:
            raise click.ClickException(
                f"Aucun JDD ne correspond au projet « {projet} » sur {instance}.")
        id_dataset = jdd.id_dataset
        cible = f"projet {projet}"
        click.echo(f"Jeu visé : {jdd.dataset_name[:60]} (id_dataset={id_dataset})")

    _purger(id_source=id_source, id_dataset=id_dataset, ca_uuid=CA_UUID,
            libelle_source="VisioNature", taxon=taxon,
            max_uncertainty=max_uncertainty, cible=cible, tout=tout,
            drop_empty_datasets=drop_empty_datasets, yes=yes)


@click.command("dbchiro-purge")
@click.option("--taxon", default="",
              help="Groupe taxonomique à retirer, par son nom TAXREF. Sur une source "
                   "entièrement chiroptérologique, c'est le genre ou la famille qui a "
                   "un sens : --taxon Rhinolophus, --taxon Vespertilionidae.")
@click.option("--tout", is_flag=True,
              help="Purger toute la source dbChiro, sans autre critère.")
@click.option("--supprimer-jdd-vides", "drop_empty_datasets", is_flag=True,
              help="Supprimer ensuite les JDD du cadre dbChiro devenus vides.")
@click.option("--yes", is_flag=True,
              help="Exécuter réellement. Sans ce drapeau, la commande se contente "
                   "d'afficher ce qu'elle supprimerait.")
def dbchiro_purge(taxon, tout, drop_empty_datasets, yes):
    """Supprime des observations dbChiro déjà importées.

    Le connecteur relisant tout le corpus à chaque passage, une purge suivie d'un
    moissonnage est le moyen le plus simple de répercuter un changement de mapping :
    l'empreinte de contenu ne détecte que les modifications faites à la source, pas
    celles de notre propre code.

    Pas d'option de jeu de données : dbChiro n'en produit qu'un par instance, faute
    d'exposer `study` dans son API. Pas de `--max-uncertainty` non plus — la colonne
    `precision` reste NULL, l'API ne publiant aucune incertitude de localisation. Le
    filtre ne retiendrait jamais rien, et l'offrir laisserait croire le contraire.
    """
    from .core import synthese as syn_core
    from .migrations.a3f6c81b0e52_source_dbchiro import SOURCE_NAME, CA_UUID

    id_source = syn_core.get_source_id(SOURCE_NAME)
    _purger(id_source=id_source, id_dataset=None, ca_uuid=CA_UUID,
            libelle_source="dbChiro", taxon=taxon, tout=tout,
            drop_empty_datasets=drop_empty_datasets, yes=yes)


connectors_cli = [
    statut,
    gbif_synchroniser_jeux, gbif_import, gbif_purge,
    visionature_import, visionature_purge, visionature_reanonymiser,
    visionature_perimetres, visionature_groupes, visionature_vider_cache,
    visionature_diagnostic, visionature_volumetrie,
    dbchiro_import, dbchiro_purge, dbchiro_perimetres,
]
