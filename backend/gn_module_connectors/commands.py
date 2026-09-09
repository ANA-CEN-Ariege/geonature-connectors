"""Commandes CLI du module, exposées sous `geonature connectors ...`."""

import click
from sqlalchemy import func, select

from pathlib import Path

from sqlalchemy import text as db_text
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
@click.option("--gadm-gid", default=None, help="Périmètre GADM (défaut : configuration).")
@click.option("--country", default=None, help="Pays (défaut : configuration).")
@click.option("--dataset-key", "dataset_keys", multiple=True,
              help="Limiter à ces jeux ; sinon, tous ceux du périmètre.")
@click.option("--license", "licenses", multiple=True,
              help="Licences retenues (défaut : configuration).")
@click.option("--limit", default=0, help="Ne traiter que les N plus gros jeux (0 = tous).")
@click.option("--ignore-exclusions", is_flag=True,
              help="Créer un JDD même pour les jeux exclus par la configuration.")
@click.option("--dry-run", is_flag=True, help="N'écrit rien, affiche ce qui serait fait.")
def gbif_sync_datasets(gadm_gid, country, dataset_keys, licenses, limit,
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
                                   statut_validation=statut_validation)
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


@click.command("gbif-purge")
@click.option("--dataset", "reference", default="",
              help="Jeu visé : datasetKey GBIF, unique_dataset_id du JDD, ou son "
                   "id_dataset. Sans cette option, la purge porte sur toutes les "
                   "observations GBIF.")
@click.option("--taxon", default="",
              help="Groupe taxonomique à retirer, par son nom TAXREF : règne, phylum, "
                   "classe, ordre, famille, ou début de nom scientifique. "
                   "Exemple : --taxon Chiroptera")
@click.option("--max-uncertainty", default=0, type=int,
              help="Retirer les observations dont l'incertitude dépasse N mètres.")
@click.option("--drop-empty-datasets", is_flag=True,
              help="Supprimer ensuite les JDD du cadre GBIF devenus vides.")
@click.option("--yes", is_flag=True,
              help="Exécuter réellement. Sans ce drapeau, la commande se contente "
                   "d'afficher ce qu'elle supprimerait.")
def gbif_purge(reference, taxon, max_uncertainty, drop_empty_datasets, yes):
    """Supprime des observations GBIF déjà importées.

    Utile après coup : une exclusion ajoutée à la configuration ne rattrape pas ce qui
    est déjà en base. La suppression est toujours bornée à la source GBIF — jamais aux
    données saisies localement ni à un autre import.
    """
    from sqlalchemy import select as sa_select
    from geonature.core.gn_meta.models import TDatasets
    from .core import purge as purge_core, synthese as syn_core, datasets as ds_core
    from .migrations.c4e8a2b95d16_source_gbif import SOURCE_NAME
    from .migrations.b2d7e9f31a04_cadre_acquisition_gbif import CA_UUID

    id_source = syn_core.get_source_id(SOURCE_NAME)

    id_dataset = None
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
        click.echo(f"Jeu visé : {jdd.dataset_name[:60]} (id_dataset={id_dataset})")

    if not (taxon or max_uncertainty or reference):
        raise click.ClickException(
            "Aucun critère : précisez au moins --dataset, --taxon ou --max-uncertainty. "
            "Purger toute la source GBIF sans le dire explicitement serait trop facile.")

    n = purge_core.compter(id_source, id_dataset, taxon or None, max_uncertainty or None)
    criteres = " · ".join(x for x in (
        f"jeu {id_dataset}" if id_dataset else "",
        f"taxon « {taxon} »" if taxon else "",
        f"incertitude > {max_uncertainty} m" if max_uncertainty else "",
    ) if x) or "toute la source GBIF"
    click.echo(f"{n} observation(s) concernée(s) — {criteres}")

    if not n:
        click.secho("Rien à supprimer.", fg="green")
    elif not yes:
        click.secho(f"\nSimulation : {n} observation(s) seraient supprimées, ainsi que "
                    f"leurs rattachements aux zonages. Relancez avec --yes pour exécuter.",
                    fg="yellow")
        return
    else:
        supprimees = purge_core.supprimer(id_source, id_dataset, taxon or None,
                                          max_uncertainty or None)
        db.session.commit()
        click.secho(f"{supprimees} observation(s) supprimée(s).", fg="green")

    if drop_empty_datasets:
        af = ds_core.get_acquisition_framework(CA_UUID)
        vides = purge_core.jdd_vides(af.id_acquisition_framework)
        if not vides:
            click.echo("Aucun JDD vide dans le cadre d'acquisition GBIF.")
            return
        click.echo(f"\n{len(vides)} JDD vide(s) :")
        for i, nom in vides[:10]:
            click.echo(f"  {i} — {nom[:62]}")
        if len(vides) > 10:
            click.echo(f"  … et {len(vides) - 10} autre(s)")
        if not yes:
            click.secho("Relancez avec --yes pour les supprimer.", fg="yellow")
            return
        for i, _ in vides:
            purge_core.supprimer_jdd(i)
        db.session.commit()
        click.secho(f"{len(vides)} JDD supprimé(s).", fg="green")


@click.command("vn-import")
@click.option("--taxo-group", "groupes", multiple=True,
              help="Groupes taxonomiques à moissonner (défaut : configuration, sinon tous).")
@click.option("--since", default="",
              help="Date ISO 8601 : ne moissonner que les créations, modifications et "
                   "suppressions depuis. VisioNature sait signaler les suppressions, "
                   "ce que GBIF ne fait pas.")
@click.option("--batch-size", default=None, type=int)
@click.option("--dry-run", is_flag=True)
def vn_import(groupes, since, batch_size, dry_run):
    """Importe des observations VisioNature dans la Synthèse."""
    from geonature.utils.config import config as gn_config
    from sqlalchemy import select as sa_select
    from geonature.core.gn_meta.models import TDatasets
    from .core import (report as report_core, synthese as syn_core,
                       datasets as ds_core, nomenclatures as nomen_core,
                       purge as purge_core)
    from .sources.visionature import (api as vn_api, taxonomy as vn_taxo,
                                     transform as vn_tr, confidentialite as vn_conf)
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
    click.secho(f"instance={instance} source={id_source} srid={srid}", fg="green")

    # L'URL de la source ne peut être connue qu'ici : elle dépend de l'instance.
    db.session.execute(
        db_text("UPDATE gn_synthese.t_sources SET url_source = :u "
                "WHERE id_source = :s AND url_source IS DISTINCT FROM :u"),
        {"u": f"{instance}/index.php?m_id=54&id=", "s": id_source})

    click.echo("Chargement du référentiel d'espèces…")
    especes = vn_api.especes(cfg)
    index, non_resolues = vn_taxo.construire_index(especes, journal=click.echo)
    if not index:
        raise click.ClickException(
            "Aucune espèce résolue : la correspondance se fait par nom scientifique "
            "contre TAXREF, vérifiez que le référentiel est bien chargé.")

    rejets = report_core.Rejects()
    for e in non_resolues:
        rejets.add("espece_non_resolue", e["id"], e["latin_name"] or e["french_name"],
                   e["motif"])

    groupes = list(groupes) or cfg.get("taxo_groups") or [
        g.get("id") for g in vn_api.groupes_taxonomiques(cfg)]
    click.echo(f"{len(groupes)} groupe(s) taxonomique(s) à traiter.")

    resolver = nomen_core.Resolver()
    cfg_valid = (gn_config.get("CONNECTORS") or {}).get("validation", {})
    statut_validation = cfg_valid.get("status") if cfg_valid.get("enabled") else None
    surcharges = cfg.get("atlas") or {}
    secret = cfg.get("pseudonymisation_secret", "")
    if not secret:
        raise click.ClickException(
            "[visionature] pseudonymisation_secret manquant. Il est requis même si peu "
            "d'observateurs demandent l'anonymat : une clé par défaut rendrait les "
            "pseudonymes recalculables par un tiers, donc réidentifiables.")
    forcer_anonymat = cfg.get("forcer_anonymat", False)

    # Le consentement est individuel : chaque observateur déclare dans VisioNature si son
    # nom peut être diffusé. Un interrupteur global écraserait ce choix.
    index_anonymat = {}
    if not forcer_anonymat:
        obs_ref = vn_api.observateurs(cfg)
        index_anonymat = vn_conf.index_anonymat(obs_ref)
        anonymes = sum(1 for v in index_anonymat.values() if v)
        click.echo(f"  référentiel des observateurs : {len(index_anonymat)} inscrit(s), "
                   f"{anonymes} ayant demandé l'anonymat")
        if not index_anonymat:
            click.secho("  ⚠ référentiel vide : tous les observateurs seront "
                        "pseudonymisés, l'ignorance ne valant pas consentement.", fg="yellow")
    else:
        click.echo("  anonymat forcé pour tous les observateurs")
    respecter = cfg.get("respecter_confidentialite", True)
    niveau_masquees = cfg.get("niveau_diffusion_masquees", "4")
    par_projet = cfg.get("jdd_par_code_projet", True)

    total_lus = total_ecrits = total_maj = total_supprimes = 0
    jdds: dict = {}

    for groupe in groupes:
        if since:
            # Les suppressions d'abord : une observation supprimée puis recréée sous le
            # même identifiant serait sinon retirée après avoir été réécrite.
            supprimes = vn_api.observations_supprimees(cfg, str(groupe), since)
            if supprimes:
                if dry_run:
                    click.echo(f"  groupe {groupe} : {len(supprimes)} relevé(s) supprimé(s) "
                               f"à la source (simulation)")
                else:
                    n = purge_core.supprimer_par_identifiants_source(
                        id_source, "sighting_id", supprimes)
                    db.session.commit()
                    total_supprimes += n
                    click.echo(f"  groupe {groupe} : {len(supprimes)} relevé(s) supprimé(s) "
                               f"à la source -> {n} observation(s) retirée(s)")
            releves = vn_api.observations_modifiees(cfg, str(groupe), since)
        else:
            releves = vn_api.observations(cfg, str(groupe))
        couples = vn_tr.deplier(releves)
        total_lus += len(couples)
        click.echo(f"  groupe {groupe} : {len(releves)} relevé(s), {len(couples)} observation(s)")

        lot, ecrits, maj = [], 0, 0
        for sighting, observation in couples:
            if respecter:
                motif = vn_conf.est_confidentielle(observation, sighting)
                if motif:
                    rejets.add("confidentielle", sighting.get("@id"),
                               (sighting.get("species") or {}).get("name"), motif)
                    continue
            cd_nom = vn_taxo.resolve(sighting, index)
            if not cd_nom:
                espece = (sighting.get("species") or {})
                rejets.add("no_cd_nom", sighting.get("@id"), espece.get("name"),
                           f"species_id={espece.get('@id')}")
                continue
            ligne = vn_tr.to_row(sighting, observation, cd_nom=cd_nom, id_dataset=None,
                                 id_source=id_source, id_module=id_module, srid=srid,
                                 resolver=resolver, instance=instance,
                                 surcharges_atlas=surcharges,
                                 statut_validation=statut_validation,
                                 index_anonymat=index_anonymat, secret_pseudo=secret,
                                 forcer_anonymat=forcer_anonymat,
                                 code_diffusion_masquee=niveau_masquees)
            if ligne is None:
                rejets.add("no_coordinates", sighting.get("@id"),
                           (sighting.get("species") or {}).get("name"), "")
                continue
            ligne["_projet"] = vn_tr.code_projet(observation) if par_projet else None
            lot.append(ligne)
            if len(lot) >= batch_size and not dry_run:
                i, u = _ecrire_lot(lot, jdds, instance, af)
                ecrits += i; maj += u
                db.session.commit(); lot = []
                click.echo(f"    … {ecrits} écrites, {maj} mises à jour")
        if lot and not dry_run:
            i, u = _ecrire_lot(lot, jdds, instance, af)
            ecrits += i; maj += u
            db.session.commit()
        elif dry_run:
            ecrits = len(lot)
        total_ecrits += ecrits; total_maj += maj

    if not dry_run:
        db.session.commit()
    suffixe = f", {total_supprimes} supprimée(s)" if total_supprimes else ""
    click.secho(f"\n{'DRY-RUN — ' if dry_run else ''}{total_lus} observation(s) lue(s), "
                f"{total_ecrits} écrite(s), {total_maj} mise(s) à jour{suffixe}, "
                f"{len(rejets)} rejetée(s).", fg="green")
    for ligne in rejets.summary_lines():
        click.echo(ligne)
    chemin = rejets.write_csv(Path("vn_rejets.csv"))
    if chemin:
        click.echo(f"  Journal détaillé : {chemin}")


def _ecrire_lot(lot, jdds, instance, af):
    """Écrit un lot en le répartissant par code projet.

    Les JDD sont créés à la demande : un projet dont toutes les observations sont
    rejetées ne laisse pas de jeu vide dans le module Métadonnées.
    """
    from .core import synthese as syn_core

    par_jdd: dict = {}
    for ligne in lot:
        par_jdd.setdefault(ligne.pop("_projet", None), []).append(ligne)

    inserees = maj = 0
    for projet, lignes in par_jdd.items():
        if projet not in jdds:
            jdds[projet] = _jdd_visionature(instance, af, projet)
        for ligne in lignes:
            ligne["id_dataset"] = jdds[projet].id_dataset
        i, u = syn_core.insert_batch(lignes)
        inserees += i; maj += u
    return inserees, maj


def _jdd_visionature(instance: str, af, projet: str | None = None):
    """JDD unique de l'instance, créé à la première écriture.

    Contrairement à GBIF, VisioNature n'agrège pas plusieurs producteurs : une instance
    est un jeu de données. Le découpage par producteur n'aurait donc pas de sens ici.
    """
    from .core import datasets as ds_core
    site = instance.replace("https://", "").replace("http://", "")
    nom = (f"{projet} — {site}" if projet
           else f"Observations VisioNature — {site}")
    jdd, cree = ds_core.upsert_dataset(
        source="VisioNature", cle=f"{instance}:{projet or ''}", licence="",
        nom=nom,
        description=(f"Observations moissonnées depuis {instance} via l'API Biolovision.\n\n"
                     f"Les codes atlas de nidification sont conservés dans additional_data : "
                     f"le SINP ne connaît pas leur gradation possible/probable/certaine."),
        id_acquisition_framework=af.id_acquisition_framework,
    )
    db.session.flush()
    if cree:
        click.secho(f"  + JDD créé : {jdd.id_dataset}", fg="green")
    return jdd


@click.command("vn-reanonymiser")
@click.option("--yes", is_flag=True, help="Exécuter réellement. Sinon, simulation.")
def vn_reanonymiser(yes):
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


connectors_cli = [status, gbif_sync_datasets, gbif_import, gbif_purge, vn_import,
                  vn_reanonymiser]
