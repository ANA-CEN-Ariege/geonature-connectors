"""Transformation d'une observation VisioNature en ligne de gn_synthese.synthese."""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from . import confidentialite as vn_conf
from . import nomenclatures as vn_nomen
from . import reproduction as vn_repro

# Namespace fixe pour dériver des UUID déterministes.
VN_NAMESPACE = uuid.UUID("b7d3f1a0-5c2e-5a41-9e88-4f6d2c1b7a03")

# Colonne de synthese <- mnémonique de nomenclature.
#
# Ce dictionnaire doit couvrir TOUTES les colonnes `id_nomenclature_*` portées par
# `core.synthese.INSERT_SQL` : le statement est unique pour tout le lot, un paramètre lié
# manquant fait échouer l'insertion entière. Les mnémoniques que VisioNature ne renseigne
# pas retombent sur le défaut de la colonne via le résolveur — c'est le rôle du `None`.
# `tests/test_visionature.py` vérifie cet alignement.
COLONNES_NOMENCLATURE = {
    "id_nomenclature_obs_technique": "METH_OBS",
    "id_nomenclature_bio_condition": "ETA_BIO",
    "id_nomenclature_bio_status": "STATUT_BIO",
    "id_nomenclature_naturalness": "NATURALITE",
    "id_nomenclature_observation_status": "STATUT_OBS",
    "id_nomenclature_source_status": "STATUT_SOURCE",
    "id_nomenclature_life_stage": "STADE_VIE",
    "id_nomenclature_sex": "SEXE",
    "id_nomenclature_obj_count": "OBJ_DENBR",
    "id_nomenclature_type_count": "TYP_DENBR",
    "id_nomenclature_biogeo_status": "STAT_BIOGEO",
    "id_nomenclature_exist_proof": "PREUVE_EXIST",
    "id_nomenclature_valid_status": "STATUT_VALID",
    "id_nomenclature_behaviour": "OCC_COMPORTEMENT",
    "id_nomenclature_geo_object_nature": "NAT_OBJ_GEO",
}

# Champs dont un changement justifie de réécrire l'observation.
#
# `hidden` et `admin_hidden_type` en font partie : un observateur qui masque a posteriori
# une observation déjà importée doit voir sa décision se propager. Sans eux, la donnée
# resterait en diffusion libre en Synthèse alors qu'elle est protégée à la source.
# ⚠ `uuid`, `medias`, `extended_info`, `details` et `id_form_universal` ont été ajoutés
# en même temps que l'exploitation de ces champs. L'empreinte de toutes les lignes
# VisioNature déjà en base change donc, et le premier moissonnage après cette version
# réécrit l'intégralité du corpus VisioNature. C'est voulu : c'est le seul moyen que les
# lignes existantes reçoivent l'heure, l'altitude, la preuve d'existence et le reste.
# Les lignes GBIF ne sont pas concernées — leur empreinte est inchangée.
CHAMPS_SUIVIS = ("count", "estimation_code", "atlas_code", "coord_lat", "coord_lon",
                 "precision", "comment", "timing", "altitude",
                 "hidden", "admin_hidden_type", "second_hand",
                 "uuid", "medias", "extended_info", "details", "id_form_universal",
                 "behaviours")


def deplier(sightings: list[dict]) -> list[tuple[dict, dict]]:
    """Déplie les relevés en couples (relevé, observation).

    VisioNature imbrique les observations dans `observers` : un même relevé — une espèce,
    une date — peut porter plusieurs saisies, distinctes par leur observateur, leur
    position et leur effectif. C'est cette granularité fine qui correspond à la ligne de
    Synthèse, pas le relevé.

    ⚠ Le dépliage s'arrête là, volontairement. `details[]` ventile chaque observation en
    classes d'âge et de sexe (« 1 adulte, 2 juvéniles ») ; on aurait pu descendre d'un
    cran et produire une ligne par classe. Trois raisons de ne pas le faire :

    - les entrées de `details[]` n'ont **aucun identifiant** ; il faudrait fabriquer la
      clé de `unique_id_sinp` depuis leur position dans le tableau ou depuis le couple
      (âge, sexe). L'une comme l'autre bougent dès qu'un observateur corrige son relevé,
      et le moissonnage suivant créerait des doublons au lieu de mettre à jour ;
    - `observers[].count` est l'effectif qui fait foi. `details[].count` ne le recoupe
      pas toujours — les classes sont facultatives et peuvent ne couvrir qu'une partie
      des individus. Éclater les lignes exposerait à compter deux fois, ou moins ;
    - surtout, **la reproduction est une propriété de l'ensemble, pas de la classe**.
      « 1 adulte + 2 juvéniles » prouve la reproduction ; scindé en deux lignes, le
      juvénile la porterait et l'adulte passerait pour une donnée sans indice. L'éclatement
      dégraderait l'information au lieu de l'affiner.

    Les classes sont donc agrégées (cf. `reproduction.valeurs`), pas dépliées.
    """
    couples = []
    for s in sightings:
        for o in s.get("observers") or []:
            couples.append((s, o))
    return couples


def sinp_uuid(sighting: dict, observation: dict, instance: str = "") -> str:
    """UUID SINP calculé d'une observation, employé en repli.

    L'URL de l'instance entre dans la clé : les identifiants VisioNature sont propres à
    chaque site, et deux instances régionales peuvent employer les mêmes. Sans ce
    préfixe, une observation de Faune-Ariège et une de Faune-Occitanie pourraient
    entrer en collision et s'écraser l'une l'autre.
    """
    cle = f"{instance}:{sighting.get('@id')}:{observation.get('@id')}"
    return str(uuid.uuid5(VN_NAMESPACE, cle))


def uuid_natif(observation: dict) -> str | None:
    """UUID publié par le producteur, s'il est exploitable.

    `observers[0].uuid` : mesuré présent sur 338 observations réelles sur 338 dans les
    exports Faune-LR examinés. C'est l'identifiant que le producteur emploie lui-même
    pour publier sa donnée au SINP.
    """
    brut = str(observation.get("uuid") or "").strip()
    if not brut:
        return None
    try:
        # Normalise la casse et la ponctuation, et écarte tout ce qui n'est pas un UUID.
        return str(uuid.UUID(brut))
    except (ValueError, AttributeError, TypeError):
        return None


def identifiant_sinp(sighting: dict, observation: dict,
                     instance: str = "") -> tuple[str, str | None]:
    """(unique_id_sinp retenu, uuid calculé s'il a été supplanté).

    ⚠ Changement de clé. Jusqu'ici le module recalculait systématiquement un uuid5, ce
    qui produisait un identifiant DEE **différent** de celui que le producteur publie.
    Si la même donnée arrive aussi par un dépôt SINP, le doublon est invisible : les
    deux lignes n'ont ni le même UUID ni la même source, rien ne les rapproche.
    L'UUID natif prime donc, et l'uuid5 ne sert plus que de repli.

    ⚠ Rétrocompatibilité. Les lignes déjà importées portent l'uuid5. Sans précaution,
    le moissonnage suivant les réinsérerait sous l'UUID natif : le doublon serait cette
    fois **dans notre propre base**. L'uuid5 supplanté est donc renvoyé ici, conservé
    dans `additional_data` sous `vn_uuid_calcule`, et `core.synthese.realigner_uuid`
    s'en sert pour renommer la ligne existante avant l'insertion, ce que le bilan
    d'import annonce ligne par lot plutôt que de le faire en silence.

    `gn_vn2synthese` fait le même choix de priorité (08:117-120) mais n'a pas le
    problème : leur repli lit une table de correspondance `src_vn_json.uuid_xref`
    peuplée hors ligne, pas un uuid déterministe, et leur montée de version est
    couverte par un script de migration dédié.
    """
    calcule = sinp_uuid(sighting, observation, instance)
    natif = uuid_natif(observation)
    if natif and natif != calcule:
        return (natif, calcule)
    return (calcule, None)


def uuid_groupe(observation: dict, instance: str = "") -> str | None:
    """`unique_id_sinp_grp` : identifiant du relevé collectif (formulaire VisioNature).

    Un formulaire VisioNature est une liste complète ou un protocole : toutes les
    observations qui en sont issues forment un regroupement, ce que `unique_id_sinp_grp`
    est fait pour exprimer.

    ⚠ Divergence de fond avec `gn_vn2synthese`. Ils lisent `src_vn_json.forms_json.uuid`
    (08:123-127) — or cette colonne **ne vient pas de l'API** : c'est une colonne qu'ils
    ajoutent eux-mêmes avec un `DEFAULT uuid_generate_v4()` (00_init_db.sql:10). C'est
    donc un UUID aléatoire, stable uniquement parce qu'il est stocké dans leur table de
    transit. Vérifié sur un export réel : l'objet `forms` de l'API porte `@id`,
    `id_form_universal`, `time_start`, `time_stop`, `lat`, `lon`, `trace`, `place_type`,
    `comment` — et aucun `uuid`.

    N'ayant pas de table de transit, on dérive un uuid5 de l'identifiant du formulaire.
    Il est reproductible sans rien stocker, ce que leur v4 n'est pas.

    `id_form_universal` (« 65_3477089 ») est préféré à `id_form` (« 3477089 ») : il
    préfixe l'identifiant du site, donc il ne peut pas entrer en collision entre
    instances. À défaut, l'URL de l'instance joue ce rôle.
    """
    universel = vn_nomen.valeur_simple(observation.get("id_form_universal"))
    if universel:
        return str(uuid.uuid5(VN_NAMESPACE, f"form:{universel}"))
    local = vn_nomen.valeur_simple(observation.get("id_form"))
    if local:
        return str(uuid.uuid5(VN_NAMESPACE, f"form:{instance}:{local}"))
    return None


def empreinte(sighting: dict, observation: dict) -> str:
    """Empreinte du contenu exploité, pour ne réécrire que ce qui a changé."""
    brut = "|".join(f"{c}={observation.get(c)!r}" for c in CHAMPS_SUIVIS)
    brut += f"|espece={(sighting.get('species') or {}).get('@id')!r}"
    # L'horodatage résolu plutôt que `@ISO8601` brut : les réponses de transit ne
    # portent que `@timestamp`/`@offset`, et une correction de date y serait invisible.
    horodatage, connue = parse_datetime(sighting, observation)
    brut += f"|date={horodatage!r}|heure={connue!r}"
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


# ── Date et heure ────────────────────────────────────────────────────────────
#
# `gn_synthese.synthese.date_min` et `date_max` sont des `timestamp`, pas des `date` :
# l'heure d'observation y a toute sa place. Ce module la jetait — `parse_date` tronquait
# la chaîne ISO à `[:10]` — et datait donc tout le corpus à minuit.
#
# `gn_vn2synthese` fait mieux : `TO_TIMESTAMP(observers[0].timing.@timestamp)`
# (08:140-142), avec `date_max := date_min`. Mais il ignore `@notime`, l'indicateur que
# Biolovision place dans chaque bloc de date pour dire si l'heure est significative.
# Quand `@notime = 1`, `timing.@timestamp` vaut minuit local : ils écrivent donc
# « 00:00:00 » sans le distinguer d'une observation réellement faite à minuit.
#
# Mesuré sur 338 observations réelles (exports Faune-LR) : `date.@notime = 1` dans
# 100 % des cas — la date du relevé ne porte jamais d'heure —, et `timing.@notime = 0`
# dans 323 cas sur 338 (95,6 %). L'heure est donc presque toujours significative, et
# les 4,4 % restants doivent rester à minuit **et être signalés comme tels**.
#
# ⚠ Autre écart : `TO_TIMESTAMP` rend un `timestamptz` que PostgreSQL convertit ensuite
# dans le fuseau du serveur. Chez eux, l'heure écrite dépend donc du réglage de la base.
# Ici, l'heure murale est calculée depuis `@offset`, que Biolovision fournit avec chaque
# horodatage : le résultat ne dépend d'aucune configuration.

def _horodatage(bloc) -> datetime | None:
    """Heure murale locale d'un bloc date/timing Biolovision, sans fuseau.

    Deux formes coexistent selon le point d'entrée : `@ISO8601` (« 2024-06-04T10:53:58
    +02:00 ») et le couple `@timestamp` (epoch UTC) / `@offset` (décalage en secondes).
    Les dumps de transit de `gn_vn2synthese` ne portent que la seconde ; les exports de
    l'API portent les deux.

    Le fuseau est retiré du résultat : `date_min` est un `timestamp without time zone`,
    et c'est l'heure lue par l'observateur sur sa montre qui a un sens naturaliste.
    """
    if not isinstance(bloc, dict):
        return None
    iso = str(bloc.get("@ISO8601") or "").strip()
    if iso:
        try:
            return datetime.fromisoformat(iso).replace(tzinfo=None)
        except ValueError:
            pass
    try:
        epoch = int(str(bloc.get("@timestamp")).strip())
    except (TypeError, ValueError):
        return None
    try:
        decalage = int(str(bloc.get("@offset") or 0).strip())
    except (TypeError, ValueError):
        decalage = 0
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            + timedelta(seconds=decalage)).replace(tzinfo=None)


def heure_significative(bloc) -> bool:
    """`@notime` dit si l'heure du bloc a un sens. « 1 » = pas d'heure.

    Quand l'indicateur est absent — certaines instances ou versions ne l'émettent pas —
    on se rabat sur la seule chose observable : une heure exactement à 00:00:00 est
    presque toujours un défaut de saisie, pas une observation nocturne. Le doute
    profite à l'abstention, puisque c'est ce que `heure_connue` ira dire en base.
    """
    if not isinstance(bloc, dict):
        return False
    brut = str(bloc.get("@notime") or "").strip()
    if brut:
        return brut not in ("1", "true", "yes")
    horodatage = _horodatage(bloc)
    return horodatage is not None and horodatage.time() != datetime.min.time()


def parse_datetime(sighting: dict, observation: dict | None = None
                   ) -> tuple[datetime | None, bool]:
    """(date_min de l'observation, l'heure est-elle significative).

    Le jour vient toujours de `sighting.date` : c'est la date d'observation déclarée,
    la seule qui fasse foi. L'heure vient de `observers[].timing`, à défaut du bloc
    `date` lui-même s'il en porte une.

    L'heure est **reportée sur le jour du relevé** plutôt que prise telle quelle. Les
    deux horodatages coïncident dans les données examinées, mais rien ne le garantit :
    un `timing` décalé d'un jour ferait mentir `date_min` sur la date d'observation,
    qui est l'information de référence.
    """
    observation = observation or {}
    bloc_date = sighting.get("date")
    jour = _horodatage(bloc_date)
    if jour is None:
        # Repli historique : certaines réponses portent une simple chaîne `date_start`.
        brut = str(sighting.get("date_start") or "").strip()
        if brut:
            try:
                jour = datetime.fromisoformat(brut[:10])
            except ValueError:
                jour = None
    if jour is None:
        return (None, False)
    jour = jour.replace(hour=0, minute=0, second=0, microsecond=0)

    for bloc in (observation.get("timing"), bloc_date):
        if heure_significative(bloc):
            heure = _horodatage(bloc)
            if heure is not None:
                return (jour.replace(hour=heure.hour, minute=heure.minute,
                                     second=heure.second), True)
    return (jour, False)


def parse_date(sighting: dict):
    """Jour d'observation seul. Conservé pour les appels qui n'ont pas besoin de l'heure."""
    horodatage, _ = parse_datetime(sighting)
    return horodatage.date() if horodatage else None


def altitude(observation: dict) -> int | None:
    """Altitude de saisie, en mètres.

    `gn_vn2synthese` la verse dans `altitude_min` **et** `altitude_max` (08:342-343) :
    une observation ponctuelle n'a pas d'intervalle d'altitude. Ces deux colonnes
    étaient purement absentes de notre INSERT, alors que le champ est renseigné sur
    la totalité des observations examinées.
    """
    return _entier(vn_nomen.valeur_simple(observation.get("altitude")))


def preuves_numeriques(observation: dict) -> str | None:
    """`digital_proof` : URL des médias attachés, séparées par des virgules.

    Même construction que `fct_c_get_medias_url_from_visionature_medias_array`
    (07_functions.sql:165-195) : `path` + « / » + `filename`, jointes par « , ».

    ⚠ Divergence : les médias marqués `media_is_hidden = 1` sont écartés de l'URL. Un
    média masqué à la source l'est pour une raison — cliché d'un nid, d'un gîte, d'une
    station — et republier son adresse contournerait cette décision. `gn_vn2synthese`
    les concatène sans distinction. La preuve reste déclarée existante
    (`nomenclatures.preuve_existence`), seule son adresse est retenue.
    """
    urls = []
    for media in vn_nomen.medias(observation):
        if vn_conf.vrai(media, "media_is_hidden"):
            continue
        chemin = str(media.get("path") or "").strip().rstrip("/")
        fichier = str(media.get("filename") or "").strip().lstrip("/")
        if chemin and fichier:
            urls.append(f"{chemin}/{fichier}")
        elif fichier:
            urls.append(fichier)
    return ", ".join(urls) or None


def _flottant(valeur):
    try:
        return float(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _entier(valeur):
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


# `precision` de VisioNature n'est PAS une distance : c'est une énumération textuelle
# décrivant le type de localisation (`precise`, `place`, `garden`, `polygone`, `transect`,
# `square`…) — `gn_vn2synthese` la range dans un VARCHAR(50) et s'en sert pour dériver
# NAT_OBJ_GEO. `gn_synthese.synthese.precision` est, lui, un entier en mètres.
# Y caster l'énumération rendait la colonne systématiquement NULL, et neutralisait au
# passage le filtre d'incertitude de la purge, qui compare `precision > seuil`.
def type_precision(observation: dict) -> str | None:
    """Type de localisation VisioNature, tel quel. Conservé dans `additional_data`.

    C'est aussi ce champ qui alimente NAT_OBJ_GEO (cf. `nomenclatures.nature_objet_geo`) :
    l'extraction est partagée pour que les deux lectures ne puissent pas diverger.
    """
    return vn_nomen.valeur_simple(observation.get("precision"))


def precision_metres(sighting: dict, observation: dict) -> int | None:
    """Incertitude de localisation en mètres, si l'instance la fournit.

    `place.loc_precision` est la seule distance métrique du modèle VisioNature. Toutes
    les instances ne la renseignent pas ; en son absence la colonne reste NULL, ce qui
    est exact — mieux vaut pas d'incertitude qu'une incertitude inventée.
    """
    for source in (observation, sighting.get("place") or {}):
        valeur = _entier((source or {}).get("loc_precision"))
        if valeur is not None:
            return valeur
    # Repli : certaines instances renvoient bien une distance dans `precision`. La perdre
    # au seul motif que le champ porte ailleurs une énumération serait dommage.
    return _entier(observation.get("precision"))


def identifiant_releve(sighting: dict, observation: dict) -> str:
    """Identifiant du relevé, quelle que soit la réponse qui l'a livré.

    ⚠ Selon le point d'entrée, il ne se trouve pas au même endroit :

    - `api_list` et `api_get` posent `@id` sur le relevé ;
    - **`observations/search` ne le pose PAS**. Ses relevés ne portent que `date`,
      `observers`, `place` et `species` ; l'identifiant est dans
      `observers[0].id_sighting`, à côté de `id_universal`.

    Le lire uniquement sur le relevé laissait `entity_source_pk_value` vide pour tout ce
    qui vient de `search` — c'est-à-dire pour tout le moissonnage. Le bouton « voir la
    donnée source » de la Synthèse pointait alors vers
    `…/index.php?m_id=54&id=` sans identifiant.
    """
    for valeur in (sighting.get("@id"),
                   observation.get("id_sighting"),
                   observation.get("id_universal")):
        texte = str(valeur or "").strip()
        if texte:
            return texte
    return ""


def code_projet(observation: dict) -> str | None:
    """Code projet VisioNature, s'il existe.

    C'est le découpage retenu par `gn_vn2synthese` pour construire les jeux de données :
    une observation rattachée à un projet en hérite. Plus fidèle qu'un JDD unique par
    instance, puisque les projets correspondent à des programmes réels — atlas, suivis,
    plans d'action.
    """
    valeur = observation.get("project_code")
    if isinstance(valeur, dict):
        valeur = valeur.get("@id") or valeur.get("#text")
    valeur = str(valeur or "").strip()
    return valeur or None


def to_row(sighting: dict, observation: dict, *, cd_nom: int, id_dataset: int | None,
           id_source: int, id_module: int, srid: int, resolver,
           instance: str = "", surcharges_atlas: dict | None = None,
           statut_validation: str | None = None, index_anonymat: dict | None = None,
           secret_pseudo: str = "", forcer_anonymat: bool = False,
           code_diffusion_masquee: str = vn_conf.NIV_PRECIS_MASQUEE,
           version_taxref: str | None = None,
           repro: vn_repro.Contexte | None = None) -> dict | None:
    """Ligne prête pour l'insertion, ou None si l'observation est inexploitable."""
    lon = _flottant(observation.get("coord_lon"))
    lat = _flottant(observation.get("coord_lat"))
    if lon is None or lat is None:
        return None
    horodatage, avec_heure = parse_datetime(sighting, observation)
    if horodatage is None:
        return None

    # Statut de reproduction des groupes sans code atlas (tout sauf les oiseaux).
    analyse = vn_repro.analyser(sighting, observation, repro)
    cds = vn_nomen.cd_nomenclatures(sighting, observation, surcharges_atlas, analyse)
    cds["STATUT_VALID"] = statut_validation
    nomenclatures = {
        colonne: resolver.id(mnemonique, cds.get(mnemonique))
        for colonne, mnemonique in COLONNES_NOMENCLATURE.items()
    }
    # Le niveau de diffusion se résout à part : `resolver.id(..., None)` retomberait sur
    # le défaut de la nomenclature, alors qu'ici l'absence de restriction doit rester
    # NULL — GeoNature ne calcule plus cette colonne et NULL y a un sens.
    cd_diffusion = vn_conf.niveau_diffusion(observation, sighting, code_diffusion_masquee)
    nomenclatures["id_nomenclature_diffusion_level"] = (
        resolver.id("NIV_PRECIS", cd_diffusion) if cd_diffusion else None)

    nom_observateur, motif_anonymat = vn_conf.observateur(
        observation, index_anonymat, secret_pseudo, forcer_anonymat)

    identifiant, uuid_supplante = identifiant_sinp(sighting, observation, instance)
    cause_mort, detail_mort = vn_nomen.cause_mortalite(observation)
    bloc_mortalite = vn_nomen.mortalite(observation)

    espece = sighting.get("species") or {}
    lieu = sighting.get("place") or {}
    effectif = _entier(observation.get("count"))
    # Une absence porte un effectif de zéro, non NULL : l'ambiguïté entre « aucun
    # individu » et « effectif non renseigné » fausserait toute analyse quantitative.
    if vn_nomen.est_absence(observation):
        effectif = 0

    provenance = {
        "source": "VisioNature",
        "instance": instance,
        "sighting_id": identifiant_releve(sighting, observation),
        "observation_id": str(observation.get("@id") or ""),
        "species_id": str(espece.get("@id") or ""),
        "species_name": espece.get("name") or "",
        # Identifiant d'observateur pseudonymisé, jamais le nom : deux observations du
        # même observateur restent rapprochables sans qu'il soit identifiable.
        # Identifiant pseudonymisé, quel que soit le sort du nom : il permet de
        # rapprocher les observations d'un même contributeur sans l'identifier.
        "observateur": (vn_conf.pseudonyme(observation.get("@uid"), secret_pseudo)[:12]
                        if secret_pseudo else ""),
        "anonymat": motif_anonymat,
        "place": lieu.get("name") or "",
        "atlas_code": vn_nomen.code_atlas(observation),
        # Degré de reproduction et indice qui l'a emporté. Le SINP ne gradue pas —
        # `STATUT_BIO` dit « Reproduction » ou rien — alors que la distinction entre un
        # accouplement observé et une exuvie trouvée est justement ce qui fait la valeur
        # de la donnée pour un atlas. Même raison que la conservation d'`atlas_code`.
        # Consigné seulement quand il y a reproduction : « inconnu » signifie que des
        # codes ont été lus et jugés non significatifs, ce qui n'apprend rien au
        # consommateur de la donnée et alourdirait chaque ligne.
        "repro_degre": analyse.degre if analyse.reproduction else "",
        "repro_indice": analyse.indice if analyse.reproduction else "",
        "estimation_code": observation.get("estimation_code") or "",
        # Type de localisation : ni une distance, ni exprimable en nomenclature SINP en
        # l'état, mais la seule indication disponible sur la nature du point.
        "precision_type": type_precision(observation),
        # Trace du masquage : le niveau de diffusion peut être modifié à la main en
        # Synthèse, l'information d'origine ne doit pas s'en trouver perdue.
        "masquee_source": "oui" if vn_conf.est_masquee(observation, sighting) else "",
        # `date_min` vaut minuit dans les deux cas : sans ce drapeau, rien ne distingue
        # une observation faite à minuit d'une observation dont l'heure est inconnue.
        # C'est exactement l'ambiguïté que laisse `gn_vn2synthese`.
        "heure_connue": "oui" if avec_heure else "non",
        # Cause de mortalité : aucune nomenclature SINP ne sait dire « collision
        # routière » ou « électrocution ». ETA_BIO ne retient que « Trouvé mort ».
        "mortalite_cause": cause_mort or ("declaree" if bloc_mortalite is not None else ""),
        "mortalite_detail": detail_mort or "",
        "mortalite_blesse": ("oui" if bloc_mortalite is not None
                             and vn_conf.vrai(bloc_mortalite, "wounded") else ""),
        # UUID calculé que l'UUID natif du producteur a supplanté. Sert au réalignement
        # des lignes déjà importées (cf. `core.synthese.realigner_uuid`) et garde la
        # trace de l'identifiant sous lequel la donnée a d'abord été publiée.
        "vn_uuid_calcule": uuid_supplante or "",
        "vn_empreinte": empreinte(sighting, observation),
    }

    return {
        **nomenclatures,
        "unique_id_sinp": identifiant,
        # Regroupement : toutes les observations d'un même formulaire VisioNature
        # (liste complète, protocole) partagent cet identifiant.
        "unique_id_sinp_grp": uuid_groupe(observation, instance),
        "id_source": id_source,
        "id_module": id_module,
        "id_dataset": id_dataset,
        "entity_source_pk_value": identifiant_releve(sighting, observation),
        "cd_nom": cd_nom,
        "nom_cite": (espece.get("name") or "?")[:1000],
        # `date_max = date_min` : une observation VisioNature est ponctuelle, elle ne
        # couvre pas un intervalle. Même choix que `gn_vn2synthese` (08:141-142).
        "date_min": horodatage,
        "date_max": horodatage,
        "count_min": effectif,
        "count_max": effectif,
        "observers": (nom_observateur or "")[:1000] or None,
        "comment_description": vn_conf.nettoyer_commentaire(observation),
        "precision": precision_metres(sighting, observation),
        "altitude_min": altitude(observation),
        "altitude_max": altitude(observation),
        "digital_proof": preuves_numeriques(observation),
        # Version de TAXREF sous laquelle `cd_nom` a été résolu. Sans elle, un cd_nom
        # devenu obsolète après une montée de version n'est plus interprétable.
        "meta_v_taxref": (version_taxref or None),
        "additional_data": json.dumps(
            {k: v for k, v in provenance.items() if v not in (None, "")},
            ensure_ascii=False),
        "lon": lon,
        "lat": lat,
        "local_srid": srid,
    }
