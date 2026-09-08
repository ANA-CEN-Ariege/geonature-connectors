"""Commandes CLI du module, exposées sous `geonature connectors ...`."""

import click
from sqlalchemy import func, select

from geonature.utils.env import db


@click.command("status")
def status():
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


@click.command("gbif-sync-datasets")
@click.option("--gadm-gid", default="", help="Périmètre GADM (ex. FRA.11.1_1 pour l'Ariège).")
@click.option("--country", default="FR", show_default=True)
@click.option("--dataset-key", "dataset_keys", multiple=True,
              help="Limiter à ces jeux ; sinon, tous ceux du périmètre.")
@click.option("--license", "licenses", multiple=True, default=("CC0_1_0", "CC_BY_4_0"),
              show_default=True, help="Licences retenues.")
@click.option("--limit", default=0, help="Ne traiter que les N plus gros jeux (0 = tous).")
@click.option("--dry-run", is_flag=True, help="N'écrit rien, affiche ce qui serait fait.")
def gbif_sync_datasets(gadm_gid, country, dataset_keys, licenses, limit, dry_run):
    """Crée ou met à jour un JDD GeoNature par jeu de données GBIF du périmètre."""
    from .core import datasets as ds_core
    from .sources.gbif import metadata as gbif_meta
    from .migrations.b2d7e9f31a04_cadre_acquisition_gbif import CA_UUID

    af = ds_core.get_acquisition_framework(CA_UUID)
    click.secho(f"Cadre d'acquisition : {af.acquisition_framework_name} "
                f"(id={af.id_acquisition_framework})", fg="green")

    if dataset_keys:
        cles = [{"key": k, "count": None} for k in dataset_keys]
    else:
        filtres = {"country": country}
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

    for i, item in enumerate(cles, 1):
        meta = gbif_meta.fetch_dataset(item["key"])
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
        click.secho(f"\nDry-run : {len(cles) - ignore} JDD seraient créés ou mis à jour, "
                    f"{ignore} ignorés (licence).", fg="yellow")
        return
    db.session.commit()
    click.secho(f"\nTerminé : {cree} créé(s), {maj} mis à jour, {ignore} ignoré(s) (licence).",
                fg="green")


@click.command("gbif-import")
@click.option("--dataset-key", "dataset_keys", multiple=True,
              help="Jeux GBIF à importer (répétable). Sans cette option, tous les jeux "
                   "du périmètre sont examinés, moins les exclusions de la configuration. "
                   "Le JDD doit exister : lancer gbif-sync-datasets au préalable.")
@click.option("--skip-gridded/--no-skip-gridded", default=None,
              help="Écarter les jeux publiés à la maille (défaut : configuration).")
@click.option("--force", is_flag=True,
              help="Moissonner même les jeux inchangés depuis le dernier passage. "
                   "Indispensable après une évolution du mapping : GBIF n'a alors rien "
                   "modifié, mais les données doivent tout de même être réécrites.")
@click.option("--gadm-gid", default=None, help="Périmètre GADM (défaut : configuration).")
@click.option("--country", default=None, help="Pays (défaut : configuration).")
@click.option("--license", "licenses", multiple=True,
              help="Licences retenues (défaut : configuration).")
@click.option("--max-results", default=0, help="Plafonne le nombre d'occurrences (0 = tout).")
@click.option("--batch-size", default=None, type=int,
              help="Taille des lots. Les triggers de synthese sont FOR EACH STATEMENT : "
                   "leur coût ne s'amortit qu'en insérant par paquets.")
@click.option("--download-doi", default="", help="DOI du téléchargement GBIF, s'il y en a un.")
@click.option("--max-uncertainty", default=None, type=int,
              help="Incertitude géographique maximale en mètres (0 = pas de filtre). "
                   "Repère sur l'Ariège : <=1100 m ne retient que 39,9 %% des occurrences, "
                   "<=5000 m en retient 59,7 %%.")
@click.option("--keep-unknown-uncertainty/--drop-unknown-uncertainty", default=None,
              help="Sort des occurrences sans incertitude déclarée — 34,9 %% du corpus "
                   "ariégeois. Les garder revient à accepter une précision inconnue.")
@click.option("--keep-specimens", is_flag=True,
              help="Conserver les FOSSIL_SPECIMEN et LIVING_SPECIMEN. Par défaut ils sont "
                   "écartés : ce ne sont pas des observations naturalistes et leurs "
                   "coordonnées ne désignent pas un lieu d'observation (banques de "
                   "semences, collections de muséum).")
@click.option("--taxref-fallback/--no-taxref-fallback", default=True, show_default=True,
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
    cfg_valid = (gn_config.get("CONNECTORS") or {}).get("validation", {})
    statut_validation = cfg_valid.get("status") if cfg_valid.get("enabled") else None
    if statut_validation:
        click.echo(f"  pré-validation : « {statut_validation} »")

    id_source = syn_core.get_source_id(SOURCE_NAME)
    id_module = syn_core.get_module_id("CONNECTORS")
    srid = syn_core.local_srid()
    af = ds_core.get_acquisition_framework(CA_UUID)
    click.secho(f"source={id_source} module={id_module} srid={srid} "
                f"cadre={af.id_acquisition_framework}", fg="green")
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
        jdd = db.session.scalar(sa_select(TDatasets).where(TDatasets.unique_dataset_id == uid))
        if jdd is None:
            click.secho(f"✗ {meta['title'][:50]} — JDD absent, lancer gbif-sync-datasets", fg="red")
            continue

        # Court-circuit : si GBIF n'a pas touché au jeu depuis notre dernier passage,
        # inutile d'en relire les occurrences. Mesuré sur un périmètre départemental,
        # aucun des 60 plus gros jeux n'avait bougé en sept jours — le moissonnage
        # hebdomadaire se réduit alors à une requête de métadonnées par jeu.
        if not force and meta.get("modified"):
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

        click.secho(f"\n{meta['title'][:64]}  (JDD {jdd.id_dataset}, {lic})", bold=True)
        cfg = SimpleNamespace(country=country, state_province="", has_coordinate=True,
                              has_geospatial_issue=False, max_results=max_results or None,
                              extra={"datasetKey": cle, **({"gadmGid": gadm_gid} if gadm_gid else {})})
        fcfg = SimpleNamespace(exclude_dataset_terms=[], exclude_dataset_keys=[],
                               include_dataset_keys=[], include_observers=[],
                               date_min="", date_max="",
                               coordinate_uncertainty_max=max_uncertainty or None,
                               keep_unknown_uncertainty=keep_unknown_uncertainty,
                               licenses=list(licenses))
        # Les absences GBIF (occurrenceStatus=ABSENT) deviendraient des présences fausses
        # en Synthèse : un seul jeu ariégeois en compte 116 799.
        cfg.extra["occurrenceStatus"] = statut

        occurrences = gbif_api.fetch(cfg, fcfg)
        occurrences = gbif_api.apply_local_filters(occurrences, fcfg, rejets)
        total_lus += len(occurrences)

        lot, ecrits, maj = [], 0, 0
        for occ in occurrences:
            if not keep_specimens and occ.get("basisOfRecord") in gbif_nomen.BASIS_OF_RECORD_EXCLUS:
                rejets.add("hors_perimetre", occ.get("gbifID"), occ.get("scientificName"),
                           occ.get("basisOfRecord"))
                continue
            cd_nom = gbif_taxo.resolve(occ, index, cache_gbif)
            if not cd_nom:
                rejets.add("no_cd_nom", occ.get("gbifID"), occ.get("scientificName"),
                           f"taxonKey={occ.get('taxonKey')}")
                continue
            ligne = gbif_tr.to_row(occ, cd_nom=cd_nom, id_dataset=jdd.id_dataset,
                                   id_source=id_source, id_module=id_module, srid=srid,
                                   resolver=resolver, download_doi=download_doi,
                                   statut_validation=statut_validation)
            if ligne is None:
                rejets.add("no_coordinates" if occ.get("decimalLatitude") is None else "no_date",
                           occ.get("gbifID"), occ.get("scientificName"),
                           f"eventDate={occ.get('eventDate')!r}")
                continue
            lot.append(ligne)
            if len(lot) >= batch_size and not dry_run:
                i, u = syn_core.insert_batch(lot)
                ecrits += i; maj += u
                db.session.commit(); lot = []
                click.echo(f"  ... {ecrits} écrites, {maj} mises à jour")
        if lot and not dry_run:
            i, u = syn_core.insert_batch(lot)
            ecrits += i; maj += u
            db.session.commit()
        elif dry_run:
            ecrits = len(lot)
        total_ecrits += ecrits
        total_maj += maj
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


connectors_cli = [status, gbif_sync_datasets, gbif_import]
