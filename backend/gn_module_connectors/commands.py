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

    # L'URL de la source ne peut être connue qu'ici : elle dépend de l'instance.
    db.session.execute(
        db_text("UPDATE gn_synthese.t_sources SET url_source = :u "
                "WHERE id_source = :s AND url_source IS DISTINCT FROM :u"),
        {"u": f"{instance}/index.php?m_id=54&id=", "s": id_source})

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
                        f"de personnes. Fichier en 0600 ; `vn-vider-cache` l'efface.",
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
    index_anonymat = {}
    if not forcer_anonymat:
        obs_ref = referentiel("observateurs", lambda: vn_api.observateurs(cfg),
                              personnel=True)
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
    departements = vn_perim.normaliser(cfg.get("departements"))
    filtre_api = dict(cfg.get("filtre_api") or {})
    if departements:
        click.echo(f"  périmètre : département(s) {', '.join(sorted(departements))}"
                   + (f", filtre serveur {filtre_api}" if filtre_api else ""))
    else:
        click.secho("  ⚠ aucun filtre de périmètre : toute l'étendue de l'instance "
                    "sera moissonnée.", fg="yellow")
    par_projet = cfg.get("jdd_par_code_projet", True)

    # ── Périmètre temporel et territorial du moissonnage complet ────────────
    # Inutile de les établir en incrémental : le différentiel s'en passe.
    date_debut = date_fin = None
    territoires: list[str] = []
    tranche_jours = int(cfg.get("tranche_jours", vn_api.TRANCHE_JOURS_DEFAUT))
    if not since:
        from datetime import date as _date
        brut = str(cfg.get("date_debut") or "").strip()
        try:
            date_debut = _date.fromisoformat(brut) if brut else _date(1900, 1, 1)
        except ValueError:
            raise click.ClickException(
                f"[visionature] date_debut = {brut!r} n'est pas une date ISO (AAAA-MM-JJ).")
        date_fin = _date.today()

        # L'API refuse une recherche non bornée territorialement : 403 sans périmètre,
        # 200 avec. L'identifiant attendu est `id_country` suivi du `short_name`, soit
        # « 109 » pour l'Ariège — c'est ce que compose transfer_vn.
        voulus = {str(d).strip().zfill(2) for d in (cfg.get("departements") or [])}
        if not voulus:
            raise click.ClickException(
                "Un moissonnage complet exige [visionature] departements : l'API refuse "
                "une recherche sans périmètre territorial, et sans lui vous "
                "moissonneriez toute l'étendue de l'instance. "
                "`vn-territoires` liste les valeurs disponibles.")
        unites = referentiel("territoires", lambda: vn_api.unites_territoriales(cfg))
        territoires = [t for t in (vn_api.identifiant_territoire(u) for u in unites
                                   if str(u.get("short_name") or "") in voulus) if t]
        if not territoires:
            raise click.ClickException(
                f"Aucune unité territoriale de l'instance ne correspond à "
                f"{sorted(voulus)}. Vérifiez avec `vn-territoires`.")
        click.echo(f"  moissonnage complet : {date_debut} → {date_fin}, "
                   f"territoire(s) {', '.join(territoires)}, "
                   f"tranches de {tranche_jours} jour(s) ajustées au volume")

    total_lus = total_ecrits = total_maj = total_supprimes = hors_perimetre = 0
    groupes_refuses: list[tuple[str, str]] = []
    jdds: dict = {}

    for rang, groupe in enumerate(groupes, 1):
        contexte_repro.groupe_courant = groupe
        # Annoncer le groupe AVANT de l'interroger : ces requêtes durent parfois
        # plusieurs dizaines de secondes, et sans cette ligne le moissonnage paraît figé.
        click.echo(f"  [{rang}/{len(groupes)}] groupe {groupe}…")
        if since:
            # Les suppressions d'abord : une observation supprimée puis recréée sous le
            # même identifiant serait sinon retirée après avoir été réécrite.
            supprimes = vn_api.observations_supprimees(cfg, str(groupe), since)
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
            try:
                releves, inaccessibles = vn_api.observations_modifiees(
                    cfg, str(groupe), since)
            except vn_api.bio.BiolovisionApiException as erreur:
                groupes_refuses.append((str(groupe), f"diff : {erreur!r}"))
                click.secho(f"    refusé par l'API ({erreur!r})", fg="yellow")
                continue
            for cle, motif in inaccessibles:
                rejets.add("inaccessible", cle, "", motif)
            lots = [releves]
        else:
            # Moissonnage complet : par `search`, découpé en tranches de dates et borné
            # par territoire. `api_list` est déprécié en amont et refusé par l'API, et
            # une recherche sans périmètre l'est aussi — mesuré sur faune-occitanie.org.
            def _tranche(territoire, debut, fin_t, n):
                click.echo(f"    {territoire} {debut:%Y-%m-%d} → {fin_t:%Y-%m-%d} : "
                           f"{n} relevé(s)")

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
                            rejets.add("confidentielle", sighting.get("@id"),
                                       (sighting.get("species") or {}).get("name"), motif)
                            continue
                    cd_nom = vn_taxo.resolve(sighting, index)
                    if not cd_nom:
                        espece = (sighting.get("species") or {})
                        rejets.add("no_cd_nom", sighting.get("@id"), espece.get("name"),
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
                        rejets.add("no_coordinates", sighting.get("@id"),
                                   (sighting.get("species") or {}).get("name"), "")
                        continue
                    ligne["_projet"] = vn_tr.code_projet(observation) if par_projet else None
                    lot.append(ligne)
                    if len(lot) >= batch_size and not dry_run:
                        i, u = _ecrire_lot(lot, jdds, instance, af, id_source)
                        ecrits += i; maj += u
                        db.session.commit(); lot = []
                        click.echo(f"    … {ecrits} écrites, {maj} mises à jour")
        except vn_api.bio.BiolovisionApiException as erreur:
            # L'exception surgit pendant l'itération, le moissonnage étant paresseux.
            # Ce qui a déjà été lu reste acquis et sera écrit ci-dessous.
            groupes_refuses.append((str(groupe), f"recherche : {erreur!r}"))
            click.secho(f"    interrompu par l'API ({erreur!r})", fg="yellow")

        if lot and not dry_run:
            i, u = _ecrire_lot(lot, jdds, instance, af, id_source)
            ecrits += i; maj += u
            db.session.commit()
        elif dry_run:
            ecrits = len(lot)
        total_ecrits += ecrits; total_maj += maj

    if not dry_run:
        db.session.commit()
    if groupes_refuses:
        click.secho(f"\n  {len(groupes_refuses)} groupe(s) refusé(s) par l'API :", fg="yellow")
        for groupe, motif in groupes_refuses:
            click.echo(f"    groupe {groupe} — {motif}")
        click.secho("  Un 403 signale que le compte n'a pas ce droit sur ce groupe. Si "
                    "TOUS les groupes sont refusés en moissonnage complet alors que "
                    "--since fonctionne, c'est l'accès à la liste complète qui manque, "
                    "pas les groupes : demandez-le à l'administrateur de l'instance.",
                    fg="yellow")
    if hors_perimetre:
        # Un rejet massif alors qu'un filtre serveur est configuré signale que l'API l'a
        # ignoré : le paramètre n'existe pas, ou ne porte pas ce nom sur cette instance.
        click.secho(f"  {hors_perimetre} observation(s) hors périmètre écartée(s).",
                    fg="yellow" if filtre_api else None)
        if filtre_api and hors_perimetre > total_lus / 10:
            click.secho("  ⚠ le filtre serveur semble ignoré par l'API : la quasi-totalité "
                        "de l'instance a été téléchargée avant d'être écartée ici. "
                        "Vérifiez le nom du paramètre avec `vn-territoires`.", fg="yellow")
    suffixe = f", {total_supprimes} supprimée(s)" if total_supprimes else ""
    click.secho(f"\n{'DRY-RUN — ' if dry_run else ''}{total_lus} observation(s) lue(s), "
                f"{total_ecrits} écrite(s), {total_maj} mise(s) à jour{suffixe}, "
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


def _ecrire_lot(lot, jdds, instance, af, id_source=None):
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


@click.command("vn-territoires")
def vn_territoires():
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
    click.echo(f"  {'id':>8}  {'short_name':<12}  nom")
    for u in unites:
        click.echo(f"  {str(u.get('id') or u.get('@id') or ''):>8}  "
                   f"{str(u.get('short_name') or ''):<12}  {u.get('name') or ''}")
    click.echo("\nRestreindre le moissonnage, dans connectors_config.toml :\n"
               "  [visionature]\n"
               "  departements = [\"09\"]              # vérifié sur place.county\n"
               "  filtre_api = { id_territorial_unit = \"<id ci-dessus>\" }\n"
               "\nLe second réduit le volume téléchargé, le premier garantit le "
               "périmètre : un paramètre inconnu de l'API est ignoré sans erreur.")


@click.command("vn-groupes")
def vn_groupes():
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


@click.command("vn-vider-cache")
def vn_vider_cache():
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


@click.command("vn-diagnostic")
@click.option("--taxo-group", "groupe", default="1", help="Groupe à sonder (défaut : 1).")
@click.option("--debug", is_flag=True,
              help="Journalise la requête réelle, pour comparer avec transfer_vn.")
def vn_diagnostic(groupe, debug):
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

    def sonder(intitule, appel):
        try:
            reponse = appel()
        except bio.HTTPError as erreur:
            code = erreur.args[0] if erreur.args else "?"
            sens = {403: "droit manquant", 404: "point d'entrée absent",
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
                click.echo(f"  {'':<34}        champs : "
                           f"{', '.join(sorted(premiere)[:12])}")
                click.echo(f"  {'':<34}        relevé complet : "
                           f"{'oui' if complet else 'NON — un api_get par entrée'}")
                # Les sous-objets décident du sort de l'observateur et du périmètre :
                # `@uid` identifie l'observateur, `county` et `insee` le département.
                # Leur absence au format court a déjà produit deux défauts silencieux.
                for cle in ("observers", "place"):
                    valeur = premiere.get(cle)
                    sous = valeur[0] if isinstance(valeur, list) and valeur else valeur
                    if isinstance(sous, dict):
                        click.echo(f"  {'':<34}        {cle}[0] : "
                                   f"{', '.join(sorted(sous)[:14])}")
                        if cle == "observers" and "@uid" not in sous:
                            click.secho(f"  {'':<34}        ⚠ pas de @uid : "
                                        f"l'observateur ne pourra pas être apparié au "
                                        f"référentiel, donc pseudonymisé par défaut.",
                                        fg="yellow")
                        if cle == "place" and not {"county", "insee"} & set(sous):
                            click.secho(f"  {'':<34}        ⚠ ni county ni insee : le "
                                        f"département n'est pas déductible du relevé.",
                                        fg="yellow")
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
    sonder("observations/diff (modifiées)",
           lambda: obs.api_diff(groupe, recent, "only_modified"))
    sonder("observations/diff (supprimées)",
           lambda: obs.api_diff(groupe, recent, "only_deleted"))
    # Paramètres relevés sur `transfer_vn`, et non devinés : `period_choice` est
    # obligatoire et les dates sont au format JJ.MM.AAAA. La sonde précédente envoyait
    # de l'ISO sans `period_choice` — son 403 ne prouvait donc rien.
    hier = datetime.now(timezone.utc) - timedelta(days=1)
    sonder("observations/search (sans périmètre)", lambda: obs.api_search(
        vn_api.parametres_recherche(groupe, hier, hier), short_version="1"))

    # `_store_search` de transfer_vn n'émet JAMAIS de recherche sans périmètre : sa
    # boucle `for t_u in t_us:` pose systématiquement `location_choice` et
    # `territorial_unit_ids`. Une recherche non bornée n'est donc pas ce qu'ils envoient,
    # et l'API peut légitimement la refuser — c'est un balayage de toute l'instance.
    voulus = {str(d).strip().zfill(2) for d in (cfg.get("departements") or [])}
    try:
        unites = vn_api.unites_territoriales(cfg)
    except bio.BiolovisionApiException:
        unites = []
    territoires = [t for t in (vn_api.identifiant_territoire(u) for u in unites
                               if not voulus or str(u.get("short_name") or "") in voulus)
                   if t]
    if territoires:
        apercu = ", ".join(territoires[:3]) + ("…" if len(territoires) > 3 else "")
        sonder(f"observations/search ({apercu})", lambda: obs.api_search(
            vn_api.parametres_recherche(groupe, hier, hier, territoires[:1]),
            short_version="1"))
    else:
        click.secho("  observations/search (avec périmètre)  ignoré — aucune unité "
                    "territoriale exploitable", fg="yellow")

    click.echo("\nLa ligne « relevé complet » est décisive : si le différentiel ne livre\n"
               "que des identifiants, chaque entrée impose une requête supplémentaire.\n"
               "À 37 000 modifications par jour et par groupe, ce n'est pas tenable.\n")
    click.echo("\nLa méthode `list` est DÉPRÉCIÉE en amont — transfer_vn journalise\n"
               "« Download using list method is deprecated, please use search method only ».\n"
               "Son 403 est donc attendu ; c'est `search` avec périmètre qui compte.\n")
    click.echo("\nLecture :\n"
               "  search avec périmètre OK              -> c'était le périmètre manquant,\n"
               "                                           pas le droit.\n"
               "  forme longue 403 mais short_version OK -> c'était le volume, pas le droit.\n"
               "  search OK                             -> moissonnage initial possible par\n"
               "                                           tranches de dates.\n"
               "  tout en 403                           -> alors seulement, demander\n"
               "                                           l'ouverture du droit.\n"
               "\n`access_mode` de vn-groupes indique par ailleurs les groupes fermés au\n"
               "compte : `transfer_vn` saute ceux dont il vaut « none ».")


@click.command("vn-volumetrie")
@click.option("--jours", default=1, help="Fenêtre de mesure, en jours (défaut : 1).")
def vn_volumetrie(jours):
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
    groupes = vn_api.groupes_taxonomiques(cfg)
    obs = vn_api._controleur(bio.ObservationsAPI, cfg)
    couverts = set(vn_repro.REGLES)

    click.echo(f"Modifications sur {jours} jour(s), depuis {depuis}.")
    click.echo("Une requête api_get sera nécessaire par entrée.\n")
    click.echo(f"  {'id':>4}  {'code':<26}  {'modifiées':>10}  {'/jour':>8}  repro")

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
        couleur = "red" if n > 5000 else ("yellow" if n > 500 else None)
        click.secho(f"  {identifiant:>4}  {code:<26}  {n:>10}  {n / jours:>8.0f}  "
                    f"{'oui' if code in couverts else '—'}", fg=couleur)

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


connectors_cli = [status, gbif_sync_datasets, gbif_import, gbif_purge, vn_import,
                  vn_reanonymiser, vn_territoires,
                  vn_groupes, vn_vider_cache, vn_diagnostic,
                  vn_volumetrie]
