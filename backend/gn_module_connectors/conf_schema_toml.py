from marshmallow import Schema, fields


class ScheduleSchemaConf(Schema):
    """Planification du moissonnage, via le Celery Beat déjà présent dans GeoNature."""

    # Désactivé par défaut : c'est à l'adoptant de décider qu'un import automatique
    # écrive dans sa Synthèse, pas au module de le supposer.
    enabled = fields.Boolean(load_default=False)
    # « minute heure jour_du_mois mois jour_de_semaine ».
    # Hebdomadaire, lundi 3 h : les producteurs français republient rarement — mesuré sur
    # l'Ariège, 901 jours depuis la dernière modification de Faune Occitanie et de SICEN
    # Occitanie, 293 à 323 jours pour l'INPN flore, eBird et Pl@ntNet.
    crontab = fields.String(load_default="0 3 * * 1")


class GbifSchemaConf(Schema):
    """Paramètres du connecteur GBIF."""

    enabled = fields.Boolean(load_default=True)
    # Filtre géographique. `gadmGid` est le seul fiable pour un département français :
    # `stateProvince` est un champ verbatim rarement renseigné (1 215 occurrences en
    # Ariège contre 1 309 180 par gadmGid), et une bbox déborde massivement.
    gadm_gid = fields.String(load_default="")
    country = fields.String(load_default="FR")
    # Licences conservées. Le CC BY-NC est viral : GBIF applique « la plus restrictive
    # gagne », donc un agrégat qui en contient une seule occurrence devient non commercial.
    licenses = fields.List(fields.String(), load_default=["CC0_1_0", "CC_BY_4_0"])
    # Filtres GBIF de base (repris de config.toml du connecteur autonome).
    has_coordinate = fields.Boolean(load_default=True)
    has_geospatial_issue = fields.Boolean(load_default=False)
    # Les absences (occurrenceStatus=ABSENT) deviendraient des présences fausses en
    # Synthèse : un seul jeu ariégeois en compte 116 799.
    occurrence_status = fields.String(load_default="PRESENT")

    exclude_dataset_keys = fields.List(fields.String(), load_default=[])
    # Exclusion par sous-chaîne du datasetName, insensible à la casse. Moins sûr que
    # exclude_dataset_keys (un titre peut changer) mais commode pour écarter d'un coup
    # une famille de jeux.
    exclude_dataset_terms = fields.List(fields.String(), load_default=[])
    exclude_publishing_orgs = fields.List(fields.String(), load_default=[])
    include_observers = fields.List(fields.String(), load_default=[])
    date_min = fields.String(load_default="")
    date_max = fields.String(load_default="")
    # Incertitude géographique maximale, en mètres. None ou 0 = pas de filtre.
    # Repères mesurés sur l'Ariège : <=1100 m ne retient que 39,9 % des occurrences,
    # <=5000 m en retient 59,7 %. Un seuil trop bas peut tout écarter : le jeu
    # « INPN - flore des CBN » publie intégralement à 5 km.
    # 1000 m sépare correctement, sur l'Ariège, la donnée de terrain de la donnée
    # maillée : les jeux publiés au centroïde y déclarent 5 000 m, tandis que la
    # saisie naturaliste opportuniste se tient entre 100 et 1 000 m.
    coordinate_uncertainty_max = fields.Integer(load_default=1000, allow_none=True)
    # Sort des occurrences sans incertitude déclarée (34,9 % du corpus ariégeois).
    # Conservées par défaut : l'angle mort est couvert par skip_gridded_datasets, qui
    # écarte en bloc les jeux publiés à la maille, déclaration ou non.
    keep_unknown_uncertainty = fields.Boolean(load_default=True)
    # DOI du téléchargement, à conserver avec chaque observation (obligation de citation).
    # L'API `search` n'en délivre aucun : ne vaut que pour l'API `download`.
    download_doi = fields.String(load_default="")
    batch_size = fields.Integer(load_default=1000)
    # Écarte les jeux publiés à la maille ou au centroïde. GBIF ne fournit aucun
    # indicateur : la détection est heuristique, sur échantillon (voir griddedness.py).
    skip_gridded_datasets = fields.Boolean(load_default=True)
    gridded_threshold_m = fields.Integer(load_default=1000)
    schedule = fields.Nested(ScheduleSchemaConf, load_default=lambda: ScheduleSchemaConf().load({}))


class ValidationSchemaConf(Schema):
    """Pré-validation automatique des données importées."""

    enabled = fields.Boolean(load_default=False)
    status = fields.String(load_default="Probable")
    comment = fields.String(
        load_default="Validation automatique — données importées depuis une source externe"
    )


class GnModuleSchemaConf(Schema):
    gbif = fields.Nested(GbifSchemaConf, load_default=GbifSchemaConf().load({}))
    validation = fields.Nested(ValidationSchemaConf, load_default=ValidationSchemaConf().load({}))
