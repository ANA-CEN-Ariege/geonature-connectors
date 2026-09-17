"""Transformation d'un enregistrement d'export GeoNature en ligne de gn_synthese.synthese."""

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime

from . import nomenclatures as gn_nomen
from .util import _entier

# Namespace fixe pour dériver des UUID déterministes, distinct de ceux des trois autres
# connecteurs : deux sources ne doivent jamais dériver le même UUID d'identifiants qui
# se ressemblent.
GEONATURE_NAMESPACE = uuid.UUID("9d4e1c7a-3b58-5f26-8e04-6a2f9c15d3b7")

# Champs dont un changement justifie de réécrire l'observation.
#
# ⚠ `date_modification` n'y figure **pas**, et c'est délibéré. Le `meta_update_date` du
# distant bouge pour des motifs que nous n'importons pas — une validation, un recalcul de
# sensibilité, l'édition d'une colonne absente de notre INSERT. L'y inclure ferait
# réécrire tout le corpus à chaque campagne de validation chez le producteur. La date est
# conservée dans `additional_data`, pour la traçabilité, mais elle n'est pas le critère.
CHAMPS_SUIVIS = (
    # ⚠ `empreinte()` ne hache pas ce `cd_nom` brut : elle hache la valeur RÉSOLUE (voir
    # sa docstring), `choisir` pouvant retenir `cd_ref` en repli sans que le `cd_nom` brut
    # ne change.
    "cd_nom", "nom_cite", "date_debut", "date_fin", "nombre_min", "nombre_max",
    "altitude_min", "altitude_max", "observateurs", "determinateur", "precision",
    "comment_occurrence", "comment_releve", "preuve_numerique", "nom_lieu",
    "precision_diffusion", "niveau_sensibilite", "floutage_dee", "jdd_uuid",
    # ⚠ Toute colonne que `to_row` écrit doit figurer ici, ou dans COLONNES_VUE, ou être
    # couverte autrement — la géométrie l'est par `lon`/`lat`. Ce qui manque à l'empreinte
    # est figé au premier import et ne se rafraîchit jamais : la ligne ne change pas, donc
    # l'ON CONFLICT la juge identique et n'écrit rien.
    "id_perm_grp_sinp",   # -> unique_id_sinp_grp : le producteur peut regrouper après coup
    "version_taxref",     # -> meta_v_taxref : change à chaque montée de TAXREF du distant
    # Les quatre colonnes de HORS_INSERT, qui partent en provenance faute de place dans
    # l'INSERT commun. Les omettre gèlerait une provenance devenue fausse.
    "type_regroupement", "methode_regroupement", "type_info_geo", "methode_determination",
)

# Le WKT d'un point, seule géométrie que l'INSERT commun sait écrire.
_POINT = re.compile(r"^\s*POINT\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)\s*$", re.I)


def _flottant(valeur):
    try:
        return float(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _texte(valeur, longueur: int | None = None) -> str | None:
    """Chaîne nettoyée, ou None si `valeur` est vide.

    ⚠ `valeur or ""` traiterait `0` comme vide (0 est faux en Python) : `profondeur_min` /
    `profondeur_max` valent légitimement 0 pour une observation de surface, et perdraient
    silencieusement cette information — contrairement à `altitude_min`/`altitude_max`, qui
    passent par `_entier()` et n'ont pas ce défaut. D'où le test explicite sur `None`.
    """
    if valeur is None:
        return None
    brut = str(valeur).strip()
    if not brut:
        return None
    return brut[:longueur] if longueur else brut


def horodatage(valeur) -> datetime | None:
    """Date et heure d'un champ de la vue, en datetime naïf.

    La vue rend `s.date_min` sans conversion : PostgreSQL sérialise donc un
    `timestamp with time zone` en ISO 8601 **avec décalage** (`2024-06-01T10:53:58+02:00`).
    Tronquer à dix caractères, comme le fait le connecteur dbChiro faute d'heure exposée,
    perdrait ici une information réelle et ramènerait toutes les observations à minuit.

    Le fuseau est retiré après conversion, et non ignoré : la Synthèse stocke des
    `timestamp without time zone`, et laisser un datetime conscient du fuseau ferait
    échouer la comparaison avec les valeurs déjà en base.
    """
    brut = str(valeur or "").strip()
    if not brut:
        return None
    # `fromisoformat` d'avant 3.11 ne lit pas le « Z » terminal.
    if brut.endswith("Z"):
        brut = brut[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(brut)
    except ValueError:
        try:
            return datetime.fromisoformat(brut[:10])
        except ValueError:
            return None
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def coordonnees(item: dict) -> tuple[float | None, float | None]:
    """(lon, lat) en WGS84.

    Le centroïde explicite est préféré au WKT : il est déjà calculé côté serveur, et sa
    lecture ne demande aucun analyseur.

    ⚠ `core.synthese.INSERT_SQL` ne sait construire qu'un **point** (`ST_MakePoint`). Une
    géométrie distante surfacique — une placette, une maille, un polygone de prospection —
    est donc réduite à son centroïde. C'est une perte réelle, assumée ici plutôt que
    masquée : `nature_objet_geo` et `type_info_geo` du producteur sont conservés pour que
    la fiche d'observation dise de quoi ce point est le centre.
    """
    lon = _flottant(item.get("x_centroid_4326"))
    lat = _flottant(item.get("y_centroid_4326"))
    if lon is not None and lat is not None:
        return (lon, lat)

    trouve = _POINT.match(str(item.get("wkt_4326") or ""))
    if trouve:
        return (_flottant(trouve.group(1)), _flottant(trouve.group(2)))
    return (None, None)


def dans_bbox(item: dict, bbox) -> bool:
    """L'observation est-elle dans l'emprise configurée ?

    Double le filtre serveur `geometry`, pour la raison qui vaut partout dans ce module :
    un paramètre inconnu de l'API est ignoré **sans erreur**, et rien ne distingue alors un
    filtre appliqué d'un filtre inexistant. Ici le diagnostic est meilleur qu'ailleurs —
    `total_filtered` égal à `total` trahit un filtre ignoré dès la première page — mais ce
    second rideau reste utile quand l'emprise serveur est plus large que l'emprise voulue.

    Une observation sans coordonnées est écartée quand une emprise est active : mieux vaut
    un rejet tracé qu'une passoire silencieuse.
    """
    if not bbox:
        return True
    lon, lat = coordonnees(item)
    if lon is None or lat is None:
        return False
    ouest, sud, est, nord = bbox
    return ouest <= lon <= est and sud <= lat <= nord


def _uuid_ou_none(valeur) -> str | None:
    """Normalise un UUID, ou rend None.

    ⚠ Indispensable pour `unique_id_sinp_grp`, que l'INSERT écrit via
    `CAST(:unique_id_sinp_grp AS uuid)`. Une chaîne non conforme y ferait échouer non pas
    la ligne, mais **le lot entier** — mille observations perdues pour un champ mal
    renseigné chez le producteur.
    """
    brut = str(valeur or "").strip()
    if not brut:
        return None
    try:
        return str(uuid.UUID(brut))
    except (ValueError, AttributeError, TypeError):
        return None


def identifiant_sinp(item: dict, instance: str = "",
                     id_export: str = "") -> tuple[str, str | None]:
    """(`unique_id_sinp`, UUID dérivé **supplanté** par le natif, s'il y a lieu).

    L'identifiant permanent du producteur est repris **verbatim** quand il existe. C'est le
    fond du sujet : l'instance distante est un producteur SINP, et `id_perm_sinp` *est*
    l'identifiant DEE de l'observation. En dériver un autre ferait que la même donnée,
    reçue un jour par un dépôt SINP ou par le GBIF, formerait un doublon que rien ne
    permettrait de rapprocher — ni le même UUID, ni la même source.

    ⚠ `unique_id_sinp` est nullable en Synthèse : un export peut donc en livrer sans. Le
    cas est le plus dangereux de tous, parce qu'il ne casse rien — NULL n'étant jamais égal
    à NULL, l'index unique ne dédoublonne pas et **chaque passage recréerait tout le
    corpus**. On dérive donc un uuid5.

    ⚠ **Le second membre est l'UUID dérivé que le natif a supplanté**, et non celui qu'on
    vient d'employer. C'est le sens qu'attend `core.synthese.realigner_uuid`, qui ne retient
    une ligne que si la trace **diffère** de l'`unique_id_sinp` : il lui faut l'ancien nom
    pour retrouver la ligne à renommer, pendant que la clé porte déjà le nouveau.

    Le rendre dans l'autre sens — la trace égale à l'`unique_id_sinp` — rendait le
    réalignement inopérant, sans que rien ne le signale : le jour où le producteur complète
    ses `id_perm_sinp`, chaque observation concernée était insérée une seconde fois sous son
    UUID natif, l'ancienne ligne restant en base. VisioNature nomme la même valeur
    `uuid_supplante`, ce qui lève l'ambiguïté.

    L'instance et l'export entrent dans la clé : `id_synthese` est un entier propre à
    chaque base, et deux instances emploieraient les mêmes.
    """
    calcule = str(uuid.uuid5(
        GEONATURE_NAMESPACE, f"{instance}:{id_export}:{item.get('id_synthese')}"))
    natif = _uuid_ou_none(item.get("id_perm_sinp"))
    if natif:
        return (natif, calcule)
    return (calcule, None)


def uuid_groupe(item: dict) -> str | None:
    """`unique_id_sinp_grp` : le regroupement tel que le producteur le déclare.

    Repris verbatim, sans dérivation : contrairement à dbChiro, où le regroupement est
    reconstruit depuis l'identifiant de session, il s'agit ici d'un identifiant SINP que
    le producteur a lui-même établi.
    """
    return _uuid_ou_none(item.get("id_perm_grp_sinp"))


def empreinte(item: dict, cd_nom: int | None = None) -> str:
    """Empreinte du contenu exploité, pour ne réécrire que ce qui a changé.

    Sans elle, la relecture complète — imposée par la réconciliation des suppressions, le
    distant ne publiant aucun journal — repasserait tout le corpus dans l'`ON CONFLICT` à
    chaque campagne : WAL, tuples morts, et recalcul du rattachement aux zonages sur
    l'intégralité des lignes.

    ⚠ `cd_nom` doit être la valeur RÉSOLUE — celle que `to_row` écrit réellement dans la
    colonne `cd_nom` de la ligne, calculée par `taxonomy.choisir` — et non le `cd_nom` brut
    de l'enregistrement source. `choisir` peut retenir `cd_ref` en repli quand le `cd_nom`
    brut n'est pas dans le référentiel local (TAXREF pouvant différer de version entre les
    deux instances) : hacher le brut laisserait un rattachement taxonomique devenu faux se
    geler silencieusement en base si seul `cd_ref` change d'un import à l'autre, le `cd_nom`
    brut restant lui inchangé. Non fourni, on retombe sur le brut — pour rester utilisable
    hors du pipeline complet de `to_row`. La valeur ne remplace le brut que dans le calcul
    du hachage, jamais dans `item` : ainsi une ligne dont le repli ne change pas garde
    exactement la même empreinte qu'avant ce correctif, sans réécriture de masse au premier
    import qui le suit.
    """
    valeurs = {c: (cd_nom if c == "cd_nom" and cd_nom is not None else item.get(c))
              for c in CHAMPS_SUIVIS}
    brut = "|".join(f"{c}={valeurs[c]!r}" for c in CHAMPS_SUIVIS)
    lon, lat = coordonnees(item)
    brut += f"|lon={lon!r}|lat={lat!r}"
    brut += "|" + "|".join(
        f"{c}={item.get(c)!r}" for c in sorted(gn_nomen.COLONNES_VUE))
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()[:32]


def pseudonyme(valeur, secret: str) -> str:
    """Pseudonyme stable d'un observateur."""
    if not secret:
        raise ValueError(
            "Aucune clé de pseudonymisation. Renseignez [geonature] "
            "pseudonymisation_secret, ou laissez pseudonymiser_observateurs à false. "
            "Une clé par défaut rendrait les pseudonymes recalculables par un tiers.")
    return hmac.new(secret.encode("utf-8"), str(valeur or "").encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def observateurs(item: dict, *, pseudonymiser: bool = False,
                 secret: str = "") -> str | None:
    """Contenu de `synthese.observers`.

    **Repris en clair par défaut**, contrairement à dbChiro — et la raison est
    structurelle, pas une inattention. Une instance GeoNature est elle-même un producteur
    SINP conforme, qui a déjà arbitré ce qu'elle diffuse : l'export existe parce qu'un
    administrateur l'a créé, et chaque observation porte `niveau_sensibilite`,
    `floutage_dee` et `precision_diffusion`. Refaire cet arbitrage localement
    substituerait notre jugement au sien.

    La pseudonymisation reste offerte, parce qu'une convention de partage peut interdire la
    republication des noms même quand le producteur les diffuse.
    """
    brut = _texte(item.get("observateurs"))
    if brut is None:
        return None
    if not pseudonymiser:
        return brut[:1000]
    # Pseudonymiser la chaîne entière, et non chaque nom : la vue livre un champ libre
    # dont rien ne garantit le séparateur. Le découper au jugé produirait des pseudonymes
    # différents pour « Dupont, Martin » et « Martin, Dupont ».
    return pseudonyme(brut, secret)[:12]


def provenance(item: dict, *, instance: str, id_export: str, cd_nom: int | None = None,
               licence: str = "", licence_url: str = "",
               uuid_supplante: str | None = None) -> dict:
    """Ce qu'on conserve d'une observation et qui n'est pas redérivable localement.

    Règle appliquée : `regne`, `classe`, `ordre`, `famille`, `cd_ref`, `nom_valide` et les
    autres champs taxonomiques de la vue **ne sont pas conservés** — ils se relisent dans
    `taxonomie.taxref` à partir du `cd_nom`, et les stocker gonflerait le JSONB sans rien
    apporter.
    """
    donnees = {
        "source": "GeoNature",
        "gn_instance": instance,
        "gn_export": str(id_export or ""),
        "gn_id_synthese": str(item.get("id_synthese") or ""),
        # L'`id_source` de la vue est l'`entity_source_pk_value` de la source **amont** du
        # distant : la clé de l'observation dans le logiciel qui l'a produite, deux crans
        # en arrière. Le nom explicite évite le contresens que « id_source » garantirait.
        "gn_pk_source_amont": _texte(item.get("id_source")) or "",
        "gn_jdd_uuid": _texte(item.get("jdd_uuid")) or "",
        "gn_jdd_nom": _texte(item.get("jdd_nom")) or "",
        "gn_ca_uuid": _texte(item.get("ca_uuid")) or "",
        "gn_licence": licence,
        "gn_licence_url": licence_url,
        "gn_date_modification": _texte(item.get("date_modification")) or "",
        "gn_version_taxref_source": _texte(item.get("version_taxref")) or "",
        # La sensibilité reste ici : le trigger de la Synthèse la recalcule localement,
        # et l'avis du producteur serait sinon perdu. Le floutage, lui, a rejoint sa
        # colonne (`id_nomenclature_blurring`) et n'a plus à être dupliqué.
        "gn_sensibilite_source": _texte(item.get("niveau_sensibilite")) or "",
        # `determiner` et `validator` existent en Synthèse mais pas dans l'INSERT commun :
        # les y ajouter obligerait les trois autres connecteurs à fournir le paramètre lié.
        "gn_determinateur": _texte(item.get("determinateur")) or "",
        "gn_validateur": _texte(item.get("validateur")) or "",
        "gn_comment_releve": _texte(item.get("comment_releve")) or "",
        "gn_nom_lieu": _texte(item.get("nom_lieu")) or "",
        "gn_habitat": _texte(item.get("habitat")) or "",
        "gn_code_habitat": _texte(item.get("code_habitat")) or "",
        "gn_reference_biblio": _texte(item.get("reference_biblio")) or "",
        "gn_numero_preuve": _texte(item.get("numero_preuve")) or "",
        "gn_preuve_non_numerique": _texte(item.get("preuve_non_numerique")) or "",
        "gn_profondeur_min": _texte(item.get("profondeur_min")) or "",
        "gn_profondeur_max": _texte(item.get("profondeur_max")) or "",
        "gn_empreinte": empreinte(item, cd_nom),
    }
    for colonne, cle in gn_nomen.HORS_INSERT.items():
        donnees[cle] = _texte(item.get(colonne)) or ""

    # L'`additional_data` du producteur, sous une clé imbriquée. Jamais fusionné à plat :
    # il porte des clés arbitraires, qui écraseraient les nôtres.
    brut = item.get("donnees_additionnelles")
    if brut:
        try:
            donnees["gn_donnees_additionnelles"] = json.loads(brut) if isinstance(
                brut, str) else brut
        except ValueError:
            donnees["gn_donnees_additionnelles"] = str(brut)

    if uuid_supplante:
        donnees["gn_uuid_calcule"] = uuid_supplante
    return donnees


def to_row(item: dict, *, cd_nom: int, id_dataset: int | None, id_source: int,
           id_module: int, srid: int, resolver, instance: str = "",
           id_export: str = "", statut_validation: str | None = None,
           pseudonymiser: bool = False, secret_pseudo: str = "",
           code_diffusion: str = "", code_diffusion_si_sensible: str = "",
           version_taxref: str | None = None, licence: str = "",
           licence_url: str = "", manques: set | None = None) -> dict | None:
    """Ligne prête pour l'insertion, ou None si l'observation est inexploitable."""
    lon, lat = coordonnees(item)
    if lon is None or lat is None:
        return None
    debut = horodatage(item.get("date_debut"))
    if debut is None:
        return None
    fin = horodatage(item.get("date_fin")) or debut

    nomenclatures = gn_nomen.resoudre(
        item, resolver, statut_validation=statut_validation, manques=manques)
    nomenclatures["id_nomenclature_diffusion_level"] = gn_nomen.niveau_diffusion(
        item, resolver, force=code_diffusion,
        si_sensible=code_diffusion_si_sensible, manques=manques)

    identifiant, uuid_supplante = identifiant_sinp(item, instance, id_export)

    return {
        **nomenclatures,
        "unique_id_sinp": identifiant,
        "unique_id_sinp_grp": uuid_groupe(item),
        "id_source": id_source,
        "id_module": id_module,
        "id_dataset": id_dataset,
        "entity_source_pk_value": str(item.get("id_synthese") or ""),
        "cd_nom": cd_nom,
        "nom_cite": (_texte(item.get("nom_cite"), 1000)
                     or _texte(item.get("nom_valide"), 1000) or ""),
        # Version **locale** de TAXREF : c'est dans ce référentiel que le `cd_nom` écrit
        # s'interprète. Celle du producteur est conservée dans additional_data, pour que
        # l'écart reste lisible.
        "meta_v_taxref": (version_taxref or None),
        "date_min": debut,
        "date_max": fin,
        "count_min": _entier(item.get("nombre_min")),
        "count_max": _entier(item.get("nombre_max")),
        "observers": observateurs(item, pseudonymiser=pseudonymiser,
                                  secret=secret_pseudo),
        "precision": _entier(item.get("precision")),
        "altitude_min": _entier(item.get("altitude_min")),
        "altitude_max": _entier(item.get("altitude_max")),
        "digital_proof": _texte(item.get("preuve_numerique")),
        "comment_description": _texte(item.get("comment_occurrence")),
        "additional_data": json.dumps(
            {k: v for k, v in provenance(
                item, instance=instance, id_export=id_export, cd_nom=cd_nom,
                licence=licence, licence_url=licence_url,
                uuid_supplante=uuid_supplante).items()
             if v not in (None, "")},
            ensure_ascii=False),
        "lon": lon,
        "lat": lat,
        "local_srid": srid,
    }
