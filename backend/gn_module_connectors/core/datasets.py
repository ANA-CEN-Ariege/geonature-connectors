"""Création et mise à jour des jeux de données GeoNature depuis une source externe.

Générique : ne connaît ni GBIF ni VisioNature, seulement un descripteur normalisé.
Destiné au socle commun lors du portage de VisioNature.
"""

import uuid

from sqlalchemy import select, text
from geonature.utils.env import db
from geonature.core.gn_meta.models import TDatasets, TAcquisitionFramework

# Namespace fixe pour dériver des UUID déterministes. Le même couple (source, clé)
# redonne toujours le même `unique_dataset_id`, ce qui rend la synchronisation
# rejouable : on met à jour au lieu de créer un doublon à chaque exécution.
NAMESPACE = uuid.UUID("6f5c1b1e-8a2d-5f47-b3c9-0d7e4a2f9b13")


def dataset_uuid(source: str, cle: str, licence: str = "") -> uuid.UUID:
    """UUID déterministe d'un jeu de données externe.

    La licence entre dans la clé parce qu'un même jeu source peut en mélanger plusieurs :
    sur iNaturalist elle est choisie par l'observateur, observation par observation. Un
    JDD doit rester homogène en licence, sous peine de rendre tout export ambigu.
    """
    return uuid.uuid5(NAMESPACE, f"{source}:{cle}:{licence}")


def get_acquisition_framework(ca_uuid: str) -> TAcquisitionFramework:
    af = db.session.scalar(
        select(TAcquisitionFramework).where(
            TAcquisitionFramework.unique_acquisition_framework_id == ca_uuid
        )
    )
    if af is None:
        raise RuntimeError(
            f"Cadre d'acquisition {ca_uuid} introuvable — la migration du module "
            f"a-t-elle bien été jouée ? (geonature upgrade-modules-db)"
        )
    return af


def upsert_acquisition_framework(
    *,
    uid: str,
    nom: str,
    description: str,
    date_debut=None,
) -> tuple[TAcquisitionFramework, bool]:
    """Crée ou récupère un cadre d'acquisition à UUID imposé. Retourne (cadre, cree).

    Sert au moissonnage d'une autre instance GeoNature, où le cadre distant doit être
    recréé **sous son propre UUID** : c'est ce que dit le SINP, un cadre garde son
    identité d'une plateforme à l'autre. Les autres connecteurs n'en ont pas besoin —
    leur cadre unique est créé par leur migration.

    Trois colonnes seulement sont NOT NULL (`acquisition_framework_name`, `_desc`,
    `_start_date`) ; tout le reste porte un DEFAULT. Les métadonnées que le formulaire de
    GeoNature exige en plus — territoire, contact principal, objectifs — relèvent de
    `qualifier_cadre`, qui ne peut être appelée qu'**après** un `db.session.flush()`.

    ⚠ Un cadre existant n'est jamais modifié. Il peut avoir été créé par un dépôt SINP ou
    saisi à la main, avec des métadonnées plus riches que celles que l'API nous livre :
    les écraser à chaque import détruirait un travail que nous ne savons pas refaire.
    """
    import datetime as _dt

    uid = str(uuid.UUID(str(uid)))
    af = db.session.scalar(
        select(TAcquisitionFramework).where(
            TAcquisitionFramework.unique_acquisition_framework_id == uid
        )
    )
    if af is not None:
        return af, False

    af = TAcquisitionFramework(
        unique_acquisition_framework_id=uid,
        acquisition_framework_name=(nom or "Cadre importé")[:255],
        acquisition_framework_desc=description or nom or "",
        acquisition_framework_start_date=date_debut or _dt.date.today(),
    )
    db.session.add(af)
    return af, True


def upsert_dataset(
    *,
    source: str,
    cle: str,
    licence: str,
    nom: str,
    description: str,
    id_acquisition_framework: int,
    shortname: str = "",
    terrestre: bool = True,
    marin: bool = False,
    uid: str | uuid.UUID | None = None,
    rafraichir: bool = True,
) -> tuple[TDatasets, bool]:
    """Crée ou met à jour un JDD. Retourne (jdd, cree).

    `uid` impose l'`unique_dataset_id` au lieu de le dériver de `(source, cle, licence)`.
    Réservé aux sources qui publient elles-mêmes un identifiant SINP de jeu de données —
    une autre instance GeoNature — pour que le jeu garde son identité d'une plateforme à
    l'autre. La conversion par `uuid.UUID` rejette tout de suite un identifiant distant
    malformé : sans elle, l'erreur ne surgirait qu'au cast PostgreSQL, sous une forme qui
    ne désigne plus la cause.

    `rafraichir=False` écrit dans un jeu existant sans toucher à ses métadonnées
    éditoriales. Utile quand l'UUID vient du producteur : le jeu local a pu être créé par
    un autre canal et enrichi à la main, et le nom que l'API nous donne n'est pas
    forcément meilleur que celui qui s'y trouve.
    """
    uid = uuid.UUID(str(uid)) if uid is not None else dataset_uuid(source, cle, licence)
    jdd = db.session.scalar(select(TDatasets).where(TDatasets.unique_dataset_id == uid))
    cree = jdd is None

    # `dataset_shortname` est NOT NULL et affiché dans les listes déroulantes : un titre
    # GBIF complet y est illisible, on le tronque proprement.
    # ⚠ 30 caractères, pas 60 : c'est ce que valide le formulaire de GeoNature
    # (« Le nom court du JDD doit être inférieur ou égal à 30 caractères »). Tronquer à
    # 60 produisait des jeux que l'interface refusait d'enregistrer — invisibles tant
    # qu'on ne les ouvre pas.
    shortname = (shortname or nom)[:30]

    if cree:
        jdd = TDatasets(
            unique_dataset_id=uid,
            id_acquisition_framework=id_acquisition_framework,
            dataset_name=nom[:255],
            dataset_shortname=shortname,
            dataset_desc=description,
            marine_domain=marin,
            terrestrial_domain=terrestre,
            active=True,
        )
        db.session.add(jdd)
    elif rafraichir:
        # On rafraîchit les métadonnées éditoriales (le producteur peut corriger son
        # titre ou sa citation), mais jamais le rattachement ni l'UUID.
        jdd.dataset_name = nom[:255]
        jdd.dataset_shortname = shortname
        jdd.dataset_desc = description
    return jdd, cree


def qualifier_dataset(jdd, financement: str = "", createur: str = "",
                      journal=None) -> None:
    """Renseigne les métadonnées qu'un JDD prend sinon par défaut.

    Deux colonnes se remplissent toutes seules et rarement à propos :

    - `id_nomenclature_data_origin` porte un DEFAULT sur `DS_PUBLIQUE`, d'où le
      « Financement : Publique » qu'affiche l'interface. Pour des données associatives
      issues de bénévoles, c'est vraisemblablement faux — mais seul l'exploitant peut
      le dire, d'où le réglage ;
    - `id_digitiser` reste NULL, d'où « Créateur : Non renseigné ». En ligne de commande
      il n'y a pas d'utilisateur courant ; il faut donc le désigner.

    Une valeur inconnue du référentiel est signalée et ignorée, jamais devinée : écrire
    une nomenclature fausse serait pire que de laisser le défaut.
    """
    if financement:
        id_nomenclature = db.session.execute(
            text("SELECT ref_nomenclatures.get_id_nomenclature('DS_PUBLIQUE', :c)"),
            {"c": financement},
        ).scalar()
        if id_nomenclature is None:
            if journal:
                journal(f"financement « {financement} » inconnu de la nomenclature "
                        f"DS_PUBLIQUE : valeur par défaut conservée")
        else:
            jdd.id_nomenclature_data_origin = id_nomenclature

    if createur:
        id_role = db.session.execute(
            text("""SELECT id_role FROM utilisateurs.t_roles
                    WHERE identifiant = :c OR CAST(id_role AS TEXT) = :c"""),
            {"c": str(createur)},
        ).scalar()
        if id_role is None:
            if journal:
                journal(f"créateur « {createur} » introuvable dans "
                        f"utilisateurs.t_roles : jeu laissé sans créateur")
        else:
            jdd.id_digitiser = id_role


def dernier_moissonnage(id_dataset: int, id_source: int):
    """Date du dernier écrit du connecteur sur ce jeu, ou None.

    Dérivée des colonnes `meta_create_date` / `meta_update_date` de la Synthèse, que le
    trigger `tri_meta_dates_change_synthese` entretient : l'information existe déjà, il
    serait inutile d'ajouter une table de suivi pour la dupliquer.

    Sert à sauter un jeu dont la date de modification côté GBIF est antérieure à notre
    dernier passage. Le gain est décisif : sur les 60 plus gros jeux d'un périmètre
    départemental, aucun n'avait été modifié dans les sept derniers jours — un
    moissonnage hebdomadaire n'a donc, la plupart du temps, rien à lire.
    """
    return db.session.execute(
        text(
            """
            SELECT max(GREATEST(meta_create_date,
                                COALESCE(meta_update_date, meta_create_date)))
            FROM gn_synthese.synthese
            WHERE id_source = :s AND id_dataset = :d
            """
        ),
        {"s": id_source, "d": id_dataset},
    ).scalar()


# ── Acteurs des jeux de données ──────────────────────────────────────────────
# Rôles SINP (`ROLE_ACTEUR`), relevés sur instance :
#   1 Contact principal          5 Fournisseur du jeu de données
#   2 Financeur                  6 Producteur du jeu de données
#   3 Maître d'ouvrage           7 Point de contact base de données de production
#   4 Maître d'œuvre             8 Point de contact pour les métadonnées
ROLE_CONTACT_PRINCIPAL = "1"
ROLE_FOURNISSEUR = "5"
ROLE_PRODUCTEUR = "6"


def resoudre_organisme(nom: str) -> int | None:
    """id_organisme d'après son nom, ou None s'il n'existe pas.

    ⚠ Le module ne CRÉE jamais d'organisme. Les tirer des données d'une API peuplerait
    `utilisateurs.bib_organismes` de variantes d'orthographe — « LPO Occitanie »,
    « LPO-Occitanie », « Ligue pour la Protection des Oiseaux Occitanie » — que plus
    personne ne saurait rapprocher ensuite. L'exploitant les déclare, le module les
    résout, et signale ceux qu'il ne trouve pas.
    """
    if not nom:
        return None
    return db.session.execute(
        text("""SELECT id_organisme FROM utilisateurs.bib_organismes
                WHERE lower(trim(nom_organisme)) = lower(trim(:n))"""),
        {"n": nom},
    ).scalar()


def creer_organisme(nom: str) -> int | None:
    """Crée un organisme et retourne son identifiant, ou None en cas d'échec.

    ⚠ À n'appeler que sur un nom venu de la CONFIGURATION, jamais des données d'une API.
    La distinction n'est pas formelle : un nom déclaré par l'exploitant exprime une
    intention, et le créer ne fait que l'exécuter. Un nom tiré de l'API arriverait en
    autant de variantes qu'il y a de saisies — « LPO Occitanie », « LPO-Occitanie »,
    « Ligue pour la Protection des Oiseaux Occitanie » — que plus personne ne saurait
    rapprocher ensuite.
    """
    nom = (nom or "").strip()
    if not nom:
        return None
    existant = resoudre_organisme(nom)
    if existant is not None:
        return existant
    return db.session.execute(
        text("""INSERT INTO utilisateurs.bib_organismes (nom_organisme)
                VALUES (:n) RETURNING id_organisme"""),
        {"n": nom},
    ).scalar()


def attacher_acteur(id_dataset: int, id_organisme: int, cd_role: str) -> bool:
    """Déclare un organisme comme acteur d'un JDD. Idempotent.

    Un jeu de données sans acteur n'est pas conforme au SINP : le producteur est une
    métadonnée obligatoire du standard. Rien dans GeoNature ne l'impose techniquement,
    d'où la facilité avec laquelle on l'oublie.
    """
    if id_dataset is None:
        raise RuntimeError(
            "Le jeu de données n'a pas encore d'identifiant : appelez db.session.flush() "
            "avant d'écrire dans une table de liaison.")
    id_role_nomenclature = db.session.execute(
        text("SELECT ref_nomenclatures.get_id_nomenclature('ROLE_ACTEUR', :c)"),
        {"c": cd_role},
    ).scalar()
    if id_role_nomenclature is None:
        return False
    return bool(db.session.execute(
        text("""INSERT INTO gn_meta.cor_dataset_actor
                    (id_dataset, id_organism, id_nomenclature_actor_role)
                SELECT :jdd, :org, :role
                WHERE NOT EXISTS (
                    SELECT 1 FROM gn_meta.cor_dataset_actor
                    WHERE id_dataset = :jdd AND id_organism = :org
                      AND id_nomenclature_actor_role = :role)"""),
        {"jdd": id_dataset, "org": id_organisme, "role": id_role_nomenclature},
    ).rowcount)


def nom_departement(code: str) -> str | None:
    """Nom d'un département d'après son code, via `ref_geo.l_areas`.

    Les données VisioNature ne portent que le code (`place.county`). Nommer un jeu
    « Faune Occitanie (Ariège) » plutôt que « dép. 09 » demande donc d'interroger le
    référentiel géographique de l'instance — qui l'a déjà, et dans l'orthographe que
    l'exploitant reconnaîtra.
    """
    if not code:
        return None
    return db.session.execute(
        text("""SELECT a.area_name FROM ref_geo.l_areas a
                JOIN ref_geo.bib_areas_types t ON t.id_type = a.id_type
                WHERE t.type_code = 'DEP' AND a.area_code = :c
                LIMIT 1"""),
        {"c": str(code)},
    ).scalar()


def attacher_territoires(jdd, cds: list[str], journal=None) -> None:
    """Rattache le jeu à des territoires (`TERRITOIRE`). Idempotent.

    Le formulaire de GeoNature l'exige — sans territoire, le jeu ne peut pas être
    enregistré. « METROP » convient à la France métropolitaine ; une instance
    ultramarine emploiera GLP, MTQ, REU, MYT, GUF…
    """
    if getattr(jdd, "id_dataset", None) is None:
        # Sans ce contrôle, PostgreSQL rejette sur une contrainte NOT NULL et la trace
        # ne dit pas la cause : le jeu n'a pas encore été « flushé », donc il n'a pas
        # d'identifiant. Une table de liaison ne peut s'écrire qu'après.
        raise RuntimeError(
            "Le jeu de données n'a pas encore d'identifiant : appelez db.session.flush() "
            "avant d'écrire dans une table de liaison.")

    for cd in cds or []:
        id_nomenclature = db.session.execute(
            text("SELECT ref_nomenclatures.get_id_nomenclature('TERRITOIRE', :c)"),
            {"c": cd},
        ).scalar()
        if id_nomenclature is None:
            if journal:
                journal(f"territoire « {cd} » inconnu de la nomenclature TERRITOIRE")
            continue
        db.session.execute(
            text("""INSERT INTO gn_meta.cor_dataset_territory
                        (id_dataset, id_nomenclature_territory)
                    SELECT :jdd, :terr
                    WHERE NOT EXISTS (
                        SELECT 1 FROM gn_meta.cor_dataset_territory
                        WHERE id_dataset = :jdd AND id_nomenclature_territory = :terr)"""),
            {"jdd": jdd.id_dataset, "terr": id_nomenclature})


def resoudre_nomenclature(mnemonique: str, valeur: str) -> int | None:
    """id_nomenclature d'après un `cd_nomenclature` ou, à défaut, un libellé.

    Accepter les deux évite d'imposer à l'exploitant de relever des codes qu'il ne voit
    nulle part dans l'interface : elle lui montre « Mixte », pas « 3 ». Le code est
    essayé d'abord — il est stable — et le libellé ne sert que de repli.

    ⚠ Un libellé est propre à une langue et peut changer d'une version de référentiel à
    l'autre. Préférer le `cd_nomenclature` quand on le connaît.

    ⚠ **Utilitaire de configuration** : une requête SQL par appel, ce qui convient à la
    poignée de valeurs d'un fichier TOML. Pour résoudre des libellés dans la boucle de
    transformation d'un import, employer `core/nomenclatures.Resolver.id_souple`, qui
    charge un type entier en une requête et met le résultat en cache.
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return None
    par_code = db.session.execute(
        text("SELECT ref_nomenclatures.get_id_nomenclature(:m, :c)"),
        {"m": mnemonique, "c": valeur},
    ).scalar()
    if par_code is not None:
        return par_code
    return db.session.execute(
        text("""SELECT t.id_nomenclature FROM ref_nomenclatures.t_nomenclatures t
                JOIN ref_nomenclatures.bib_nomenclatures_types b ON b.id_type = t.id_type
                WHERE b.mnemonique = :m
                  AND lower(trim(t.label_default)) = lower(trim(:c))
                LIMIT 1"""),
        {"m": mnemonique, "c": valeur},
    ).scalar()


def qualifier_cadre(af, territoires: list[str] | None = None,
                    contact_principal: str = "", objectifs: list[str] | None = None,
                    financement: str = "", niveau_territorial: str = "",
                    journal=None) -> None:
    """Renseigne les champs que le formulaire du cadre d'acquisition exige.

    Le cadre est créé par la migration du module, qui ne peut pas connaître ces valeurs :
    elles dépendent de l'instance et de la structure qui l'exploite. Il en sort donc sans
    territoire ni contact principal, et le formulaire de GeoNature refuse de
    l'enregistrer — exactement comme pour les jeux de données.

    Appelé à chaque import plutôt qu'à la migration : une configuration renseignée après
    coup rattrape ainsi un cadre déjà créé, et une migration Alembic ne se rejoue pas.
    """
    if af is None or getattr(af, "id_acquisition_framework", None) is None:
        return
    id_af = af.id_acquisition_framework

    for cd in territoires or []:
        id_terr = db.session.execute(
            text("SELECT ref_nomenclatures.get_id_nomenclature('TERRITOIRE', :c)"),
            {"c": cd},
        ).scalar()
        if id_terr is None:
            if journal:
                journal(f"territoire « {cd} » inconnu de la nomenclature TERRITOIRE")
            continue
        db.session.execute(
            text("""INSERT INTO gn_meta.cor_acquisition_framework_territory
                        (id_acquisition_framework, id_nomenclature_territory)
                    SELECT :af, :terr
                    WHERE NOT EXISTS (
                        SELECT 1 FROM gn_meta.cor_acquisition_framework_territory
                        WHERE id_acquisition_framework = :af
                          AND id_nomenclature_territory = :terr)"""),
            {"af": id_af, "terr": id_terr})

    # Objectifs : table de liaison, comme les territoires.
    for valeur in objectifs or []:
        id_obj = resoudre_nomenclature("CA_OBJECTIFS", valeur)
        if id_obj is None:
            if journal:
                journal(f"objectif « {valeur} » inconnu de la nomenclature CA_OBJECTIFS")
            continue
        db.session.execute(
            text("""INSERT INTO gn_meta.cor_acquisition_framework_objectif
                        (id_acquisition_framework, id_nomenclature_objectif)
                    SELECT :af, :obj
                    WHERE NOT EXISTS (
                        SELECT 1 FROM gn_meta.cor_acquisition_framework_objectif
                        WHERE id_acquisition_framework = :af
                          AND id_nomenclature_objectif = :obj)"""),
            {"af": id_af, "obj": id_obj})

    # Financement et niveau territorial : colonnes, prises par DEFAULT sinon.
    for attribut, mnemonique, valeur in (
            ("id_nomenclature_financing_type", "TYPE_FINANCEMENT", financement),
            ("id_nomenclature_territorial_level", "NIVEAU_TERRITORIAL",
             niveau_territorial)):
        if not valeur:
            continue
        id_nom = resoudre_nomenclature(mnemonique, valeur)
        if id_nom is None:
            if journal:
                journal(f"« {valeur} » inconnu de la nomenclature {mnemonique} : "
                        f"valeur par défaut conservée")
            continue
        setattr(af, attribut, id_nom)

    if not contact_principal:
        return
    id_org = resoudre_organisme(contact_principal)
    if id_org is None:
        if journal:
            journal(f"contact principal « {contact_principal} » introuvable dans "
                    f"utilisateurs.bib_organismes : cadre laissé sans contact")
        return
    id_role = db.session.execute(
        text("SELECT ref_nomenclatures.get_id_nomenclature('ROLE_ACTEUR', :c)"),
        {"c": ROLE_CONTACT_PRINCIPAL},
    ).scalar()
    if id_role is None:
        return
    db.session.execute(
        text("""INSERT INTO gn_meta.cor_acquisition_framework_actor
                    (id_acquisition_framework, id_organism, id_nomenclature_actor_role)
                SELECT :af, :org, :role
                WHERE NOT EXISTS (
                    SELECT 1 FROM gn_meta.cor_acquisition_framework_actor
                    WHERE id_acquisition_framework = :af AND id_organism = :org
                      AND id_nomenclature_actor_role = :role)"""),
        {"af": id_af, "org": id_org, "role": id_role})
