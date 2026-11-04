"""Écriture des médias importés dans `gn_commons.t_medias`.

Générique : ne connaît aucune source externe, seulement des médias normalisés rattachés
à des `unique_id_sinp`.

La Synthèse affiche les médias d'une observation par la relation
`t_medias.uuid_attached_row = synthese.unique_id_sinp` — sans regarder la table d'origine.
Un média importé est donc rattaché **directement à la ligne de Synthèse**, sous
l'emplacement `gn_synthese.synthese` de `gn_commons.bib_tables_location`, que la migration
`c9d3e5f7a1b2` déclare. C'est cet emplacement qui délimite ce que le module gère : il
ne lit, ne modifie ni ne supprime jamais un média rangé ailleurs — ceux d'Occtax, de
Monitoring ou d'Occhab sont sous leur propre emplacement.

Seule l'URL est reprise (`media_url`) ; aucun fichier n'est téléchargé. Le média reste
hébergé par le producteur, et cesse de s'afficher s'il le retire ou le déplace.

Trois garde-fous, dans l'esprit de `core/purge.py` :

- toute écriture passe par une **jointure sur la Synthèse bornée à `id_source`** : un
  média n'est posé, modifié ou retiré que sur une ligne que le connecteur appelant
  possède ;
- la synchronisation ne compare que les observations **effectivement relues avec leur
  colonne de médias** : une observation absente du lot, ou un export sans colonne
  `medias`, ne perd rien ;
- la suppression d'une ligne de Synthèse emporte ses médias dans le même statement
  (`core/purge._supprimer`), faute de clé étrangère qui le ferait.

Pas de contrainte d'unicité sur `unique_id_media` dans GeoNature : l'appariement se fait
donc sur le triplet (emplacement, `uuid_attached_row`, `unique_id_media`), et un média
inchangé n'est jamais réécrit — ni son `id_media`, ni ses dates ne bougent.
"""

from sqlalchemy import text
from geonature.utils.env import db

SCHEMA, TABLE = "gn_synthese", "synthese"

# Colonnes passées en tableaux à `UNNEST`, dans cet ordre : un seul statement par lot,
# comme `INSERT_SQL`.
_UNNEST = """UNNEST(CAST(:d_obs AS uuid[]), CAST(:d_uid AS uuid[]),
                    CAST(:d_typ AS integer[]), CAST(:d_url AS text[]),
                    CAST(:d_titre AS text[]), CAST(:d_auteur AS text[]),
                    CAST(:d_descr AS text[]))
             AS d(obs, uid, typ, url, titre, auteur, descr)"""

# Retire ce qui n'est plus voulu : média disparu à la source, ou modifié — il sera
# réinséré juste après sous sa forme nouvelle. Un média identique à ce qui est voulu
# n'est pas touché.
_RETIRER = text(f"""
    DELETE FROM gn_commons.t_medias AS m
    USING gn_synthese.synthese AS s
    WHERE m.id_table_location = :loc
      AND m.uuid_attached_row = ANY(CAST(:obs AS uuid[]))
      AND s.unique_id_sinp = m.uuid_attached_row
      AND s.id_source = :src
      AND NOT EXISTS (
          SELECT 1 FROM {_UNNEST}
          WHERE d.obs = m.uuid_attached_row
            AND d.uid = m.unique_id_media
            AND d.typ = m.id_nomenclature_media_type
            AND d.url IS NOT DISTINCT FROM m.media_url
            AND d.titre IS NOT DISTINCT FROM m.title_fr
            AND d.auteur IS NOT DISTINCT FROM m.author
            AND d.descr IS NOT DISTINCT FROM m.description_fr
            AND m.media_path IS NULL
            AND m.is_public)
""")

_AJOUTER = text(f"""
    INSERT INTO gn_commons.t_medias (
        unique_id_media, id_nomenclature_media_type, id_table_location,
        uuid_attached_row, title_fr, media_url, author, description_fr, is_public)
    SELECT d.uid, d.typ, :loc, d.obs, d.titre, d.url, d.auteur, d.descr, true
    FROM {_UNNEST}
    JOIN gn_synthese.synthese AS s
      ON s.unique_id_sinp = d.obs AND s.id_source = :src
    WHERE NOT EXISTS (
        SELECT 1 FROM gn_commons.t_medias AS m
        WHERE m.id_table_location = :loc
          AND m.uuid_attached_row = d.obs
          AND m.unique_id_media = d.uid)
""")


def emplacement() -> int | None:
    """`id_table_location` de la Synthèse, ou None si la migration n'est pas passée."""
    return db.session.execute(
        text("""SELECT id_table_location FROM gn_commons.bib_tables_location
                WHERE schema_name = :schema AND table_name = :table"""),
        {"schema": SCHEMA, "table": TABLE},
    ).scalar()


def synchroniser(medias_par_obs: dict[str, list[dict]], id_source: int,
                 id_location: int) -> tuple[int, int]:
    """Aligne les médias de ces observations sur ce que la source publie.

    `medias_par_obs` associe chaque `unique_id_sinp` relu à la liste **complète** de ses
    médias, chacun portant `unique_id_media`, `id_nomenclature_media_type`, `media_url`,
    `title_fr`, `author` et `description_fr`. Une liste vide retire les médias que
    l'observation portait ; une observation absente du dictionnaire n'est pas touchée.

    Rend `(ajoutés, retirés)`. Un média modifié compte dans les deux.
    """
    if not medias_par_obs or id_location is None:
        return (0, 0)
    colonnes = {k: [] for k in ("d_obs", "d_uid", "d_typ", "d_url", "d_titre",
                                "d_auteur", "d_descr")}
    for obs, medias in medias_par_obs.items():
        for media in medias:
            colonnes["d_obs"].append(str(obs))
            colonnes["d_uid"].append(str(media["unique_id_media"]))
            colonnes["d_typ"].append(media["id_nomenclature_media_type"])
            colonnes["d_url"].append(media["media_url"])
            colonnes["d_titre"].append(media.get("title_fr"))
            colonnes["d_auteur"].append(media.get("author"))
            colonnes["d_descr"].append(media.get("description_fr"))
    parametres = {**colonnes, "loc": id_location, "src": id_source,
                  "obs": [str(o) for o in medias_par_obs]}
    retires = db.session.execute(_RETIRER, parametres).rowcount
    ajoutes = db.session.execute(_AJOUTER, parametres).rowcount if colonnes["d_obs"] \
        else 0
    return (ajoutes, retires)


def realigner(anciens: list[str], cibles: list[str]) -> int:
    """Suit le renommage d'une ligne de Synthèse (`synthese.realigner_uuid`).

    Sans quoi les médias resteraient attachés à l'ancien UUID, que plus aucune ligne ne
    porte : invisibles, et jamais nettoyés. Ne déplace que vers un UUID effectivement
    porté par une ligne de Synthèse, et seulement si l'ancien ne l'est plus.
    """
    if not anciens:
        return 0
    id_location = emplacement()
    if id_location is None:
        return 0
    return db.session.execute(
        text("""UPDATE gn_commons.t_medias AS m
                SET uuid_attached_row = c.cible
                FROM UNNEST(CAST(:anciens AS uuid[]),
                            CAST(:cibles AS uuid[])) AS c(ancien, cible)
                WHERE m.id_table_location = :loc
                  AND m.uuid_attached_row = c.ancien
                  AND EXISTS (SELECT 1 FROM gn_synthese.synthese s
                              WHERE s.unique_id_sinp = c.cible)
                  AND NOT EXISTS (SELECT 1 FROM gn_synthese.synthese s
                                  WHERE s.unique_id_sinp = c.ancien)"""),
        {"loc": id_location, "anciens": anciens, "cibles": cibles},
    ).rowcount
