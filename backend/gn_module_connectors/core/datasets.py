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
) -> tuple[TDatasets, bool]:
    """Crée ou met à jour un JDD. Retourne (jdd, cree)."""
    uid = dataset_uuid(source, cle, licence)
    jdd = db.session.scalar(select(TDatasets).where(TDatasets.unique_dataset_id == uid))
    cree = jdd is None

    # `dataset_shortname` est NOT NULL et affiché dans les listes déroulantes : un titre
    # GBIF complet y est illisible, on le tronque proprement.
    shortname = (shortname or nom)[:60]

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
    else:
        # On rafraîchit les métadonnées éditoriales (le producteur peut corriger son
        # titre ou sa citation), mais jamais le rattachement ni l'UUID.
        jdd.dataset_name = nom[:255]
        jdd.dataset_shortname = shortname
        jdd.dataset_desc = description
    return jdd, cree


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
ROLE_PRODUCTEUR = "6"
ROLE_FOURNISSEUR = "5"


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


def attacher_acteur(id_dataset: int, id_organisme: int, cd_role: str) -> bool:
    """Déclare un organisme comme acteur d'un JDD. Idempotent.

    Un jeu de données sans acteur n'est pas conforme au SINP : le producteur est une
    métadonnée obligatoire du standard. Rien dans GeoNature ne l'impose techniquement,
    d'où la facilité avec laquelle on l'oublie.
    """
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
