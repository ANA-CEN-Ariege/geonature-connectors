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


class ExclusionTaxonSchemaConf(Schema):
    """Exclusion de taxons, globale ou restreinte à un jeu de données."""

    # `datasetKey` GBIF ou `unique_dataset_id` GeoNature. Absent = exclusion globale.
    dataset = fields.String(load_default="")
    # taxonKey GBIF, descendants compris : 734 = ordre Chiroptera.
    # Se résolvent via https://api.gbif.org/v1/species/match?name=<nom>&rank=<rang>
    taxon_keys = fields.List(fields.Integer(), load_default=[])


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
    # Exclusions taxonomiques. Le filtre est local : GBIF n'offre pas de négation sur
    # `taxonKey`. Il teste toute la hiérarchie de l'occurrence, donc exclure un ordre
    # écarte bien toutes ses espèces.
    exclude_taxa = fields.List(fields.Nested(ExclusionTaxonSchemaConf), load_default=[])
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


class AtlasSchemaConf(Schema):
    """Interprétation des codes atlas de nidification.

    Surchargeable car ces codes, s'ils suivent le standard EOAC, peuvent être enrichis
    localement par un atlas régional. `gn_vn2synthese` stocke l'équivalent dans une table
    de synonymes administrable en SQL ; faute d'étendre le schéma, on passe par ici.
    """

    # Code à partir duquel un indice de nidification vaut « Reproduction ». Le code 1,
    # « vu en période de nidification dans un milieu favorable », n'est pas un indice.
    reproduction_min = fields.Integer(load_default=2)
    # Code signalant une absence : espèce recherchée, non trouvée.
    absence = fields.Integer(load_default=99)
    # code atlas -> cd_nomenclature OCC_COMPORTEMENT. Vide = table par défaut du module.
    comportement = fields.Dict(keys=fields.String(), values=fields.String(), load_default={})


class ReproductionSchemaConf(Schema):
    """Statut de reproduction des groupes sans code atlas (tout sauf les oiseaux).

    La table de correspondance vit dans le code (`sources/visionature/reproduction.py`)
    et non dans une table PostgreSQL comme chez `gn_vn2synthese` : le module ne crée
    aucune table hors de ses propres migrations, et une correspondance en base serait
    invisible en revue comme en test — la suite de tests tourne sans base. Cette section
    ne sert donc qu'à corriger ou compléter la table livrée, pas à la constituer.
    """

    active = fields.Boolean(load_default=True)
    # groupe -> type de valeur -> code -> degré. Le groupe se désigne par son code
    # (`TAXO_GROUP_BAT`) ou par son identifiant numérique sur l'instance ; le type vaut
    # « age », « sex » ou « behaviour » ; le degré « certain », « probable », « possible »
    # ou « inconnu ». Les entrées surchargent la table du module groupe par groupe, sans
    # effacer le reste.
    #
    #   [visionature.reproduction.regles.TAXO_GROUP_BAT.age]
    #   YOUNGNAKED = "certain"
    regles = fields.Dict(keys=fields.String(), values=fields.Raw(), load_default={})


class VisioNatureSchemaConf(Schema):
    """Connecteur VisioNature (Biolovision)."""

    enabled = fields.Boolean(load_default=False)
    # URL de l'instance, p. ex. https://www.faune-ariege.fr — chaque site VisioNature a
    # ses propres identifiants OAuth1 et son propre référentiel d'espèces.
    url = fields.String(load_default="")
    user_email = fields.String(load_default="")
    user_password = fields.String(load_default="")
    # Fournis par Biolovision, séparément du compte utilisateur.
    client_key = fields.String(load_default="")
    client_secret = fields.String(load_default="")
    # Groupes taxonomiques à moissonner. Vide = tous.
    taxo_groups = fields.List(fields.String(), load_default=[])

    # ── Confidentialité ──────────────────────────────────────────────────────
    # Le consentement à la diffusion du nom est **individuel** : VisioNature porte un
    # champ `anonymous` sur chaque observateur. Le module le respecte, plutôt que
    # d'appliquer un choix global qui écraserait celui de chacun.
    #   anonymous = 0            -> nom publié (aucune demande d'anonymat)
    #   anonymous = 1            -> pseudonyme
    #   observateur inconnu      -> pseudonyme : l'ignorance ne vaut pas consentement
    # Passer à true impose le pseudonyme à tous, sans consulter le référentiel.
    forcer_anonymat = fields.Boolean(load_default=False)
    # ⚠ Clé du HMAC. Sans elle, la pseudonymisation est refusée — et non remplacée par
    # une valeur par défaut, qui rendrait les pseudonymes recalculables par un tiers.
    # `gn_vn2synthese` a sa clé en clair dans un dépôt public : c'est à éviter.
    pseudonymisation_secret = fields.String(load_default="")
    # Écarter les observations refusées par un modérateur (`admin_hidden_type` =
    # « refused »). Les importer republierait ce qu'un modérateur a explicitement rejeté.
    # ⚠ Ne concerne PAS les observations masquées (`hidden`) : celles-ci sont importées,
    # avec le niveau de diffusion ci-dessous.
    respecter_confidentialite = fields.Boolean(load_default=True)
    # Niveau de diffusion (cd_nomenclature NIV_PRECIS) appliqué aux observations masquées
    # à la source. On masque dans VisioNature pour protéger une espèce ou un site — nid
    # de rapace, station d'orchidée, gîte à chiroptères — donc la donnée est importée,
    # mais marquée non diffusable.
    # Référentiel NIV_PRECIS :
    #   0 Standard   1 Commune   2 Maille   3 Département   4 Aucune   5 Précise
    #   « 4 » (Aucune) : aucune diffusion. Code employé par GeoNature pour
    #                    `diffusable = false` dans sa migration v1 -> v2.
    #   « 2 » (Maille) : alimente les cartes de répartition sans livrer la localisation
    #                    précise. C'est le choix de gn_vn2synthese, moins restrictif.
    # Les observations non masquées gardent un niveau NULL : GeoNature ne calcule plus
    # cette colonne, et NULL y signifie « le producteur ne se prononce pas ».
    niveau_diffusion_masquees = fields.String(load_default="4")

    # ── Jeux de données ──────────────────────────────────────────────────────
    # Un JDD par code projet VisioNature, comme le fait gn_vn2synthese : les projets
    # correspondent à des programmes réels (atlas, suivis, plans d'action). À défaut,
    # un JDD unique par instance.
    jdd_par_code_projet = fields.Boolean(load_default=True)
    # Codes de département à conserver, vérifiés sur `place.county` de chaque relevé.
    # Vide = aucun filtre, donc toute l'étendue de l'instance : sur Faune-Occitanie,
    # treize départements. « 9 » et « 09 » sont acceptés indifféremment.
    departements = fields.List(fields.String(), load_default=list)
    # Filtre appliqué côté serveur, transmis tel quel à l'API. Seul moyen d'éviter de
    # télécharger l'instance entière — mais un paramètre inconnu de l'API est ignoré sans
    # erreur, d'où la vérification systématique sur `departements` ci-dessus.
    # `geonature connectors vn-territoires` liste les valeurs de l'instance.
    filtre_api = fields.Dict(load_default=dict)
    # ── Cache des référentiels ───────────────────────────────────────────────
    # Durée de validité en heures. 0 = désactivé, et c'est le défaut.
    # Un moissonnage commence par trois téléchargements — espèces, groupes, observateurs —
    # soit plusieurs minutes avant la première observation traitée. Le cache les évite
    # pendant la mise au point.
    # ⚠ Le référentiel des observateurs contient des NOMS DE PERSONNES. L'activer les
    # écrit sur disque (fichiers en 0600), ce que le reste du module évite soigneusement.
    # ⚠ Un référentiel périmé produit des correspondances taxonomiques fausses et des
    # consentements obsolètes, sans que rien ne le signale. Outil de mise au point, pas
    # de production. `geonature connectors vn-vider-cache` efface.
    cache_heures = fields.Float(load_default=0)
    # Répertoire de cache. Vide = ~/.cache/gn_module_connectors (ou XDG_CACHE_HOME).
    cache_dir = fields.String(load_default="")
    max_retry = fields.Integer(load_default=3)
    max_chunks = fields.Integer(load_default=100)
    # ⚠ Secondes. Doit être fourni : dans le client Biolovision vendorisé, `timeout` est
    # le seul paramètre du constructeur sans valeur par défaut. Laissé à None, `requests`
    # attend indéfiniment et un incident réseau fige le moissonnage sans message.
    timeout = fields.Integer(load_default=120)
    # Attente après une réponse 503, en secondes. Le client applique 600 s par défaut,
    # soit une demi-heure de gel apparent avec max_retry = 3.
    unavailable_delay = fields.Integer(load_default=60)
    batch_size = fields.Integer(load_default=1000)
    atlas = fields.Nested(AtlasSchemaConf, load_default=lambda: AtlasSchemaConf().load({}))
    reproduction = fields.Nested(ReproductionSchemaConf,
                                 load_default=lambda: ReproductionSchemaConf().load({}))
    schedule = fields.Nested(ScheduleSchemaConf, load_default=lambda: ScheduleSchemaConf().load({}))


class GnModuleSchemaConf(Schema):
    gbif = fields.Nested(GbifSchemaConf, load_default=GbifSchemaConf().load({}))
    visionature = fields.Nested(VisioNatureSchemaConf,
                                load_default=lambda: VisioNatureSchemaConf().load({}))
    validation = fields.Nested(ValidationSchemaConf, load_default=ValidationSchemaConf().load({}))
