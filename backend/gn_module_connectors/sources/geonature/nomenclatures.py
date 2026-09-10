"""Correspondance des nomenclatures d'une vue d'export GeoNature vers le SINP local.

⚠ **C'est ici que se joue l'essentiel du connecteur, et c'est ici qu'api2GN échoue.**

`gn_exports.v_synthese_sinp` joint `ref_nomenclatures.t_nomenclatures` et sélectionne
`label_default` : ses colonnes de nomenclature contiennent donc des **libellés français**
— « Reproducteur », « Vivant », « Sauvage » —, pas des `cd_nomenclature`. Le
`GeoNatureParser` d'api2GN les passe pourtant à `ref_nomenclatures.get_id_nomenclature()`,
qui attend un code : la fonction rend NULL, l'insertion réussit, et les quinze colonnes de
nomenclature de la Synthèse se remplissent de rien. Aucune erreur, aucun message.

D'où deux règles ici :

- la résolution passe par `Resolver.id_souple`, qui essaie le code puis le libellé ;
- un libellé non résolu est **collecté** dans l'ensemble `manques` que la commande affiche
  en fin d'import. Un connecteur qui perd une nomenclature doit le dire ; c'est
  exactement ce qui distingue les deux implémentations.
"""

# Colonne de la vue → mnémonique du type de nomenclature SINP.
#
# Ne couvre que les colonnes que `core.synthese.INSERT_SQL` sait écrire. Les autres sont
# dans HORS_INSERT et partent en additional_data.
COLONNES_VUE = {
    "technique_obs": "METH_OBS",
    "etat_biologique": "ETA_BIO",
    "statut_biologique": "STATUT_BIO",
    "naturalite": "NATURALITE",
    "statut_observation": "STATUT_OBS",
    "statut_source": "STATUT_SOURCE",
    "stade_vie": "STADE_VIE",
    "sexe": "SEXE",
    "objet_denombrement": "OBJ_DENBR",
    "type_denombrement": "TYP_DENBR",
    "preuve_existante": "PREUVE_EXIST",
    "comportement": "OCC_COMPORTEMENT",
    "nature_objet_geo": "NAT_OBJ_GEO",
}

# Colonne de synthese ← mnémonique. Doit couvrir TOUTES les colonnes `id_nomenclature_*`
# de `core.synthese.INSERT_SQL` : le statement étant unique pour tout le lot, un paramètre
# lié manquant fait échouer l'insertion entière.
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

# Colonne de synthese → colonne de la vue, pour les seules colonnes que la vue alimente.
# Les deux absentes de ce dictionnaire prendront le défaut de leur colonne :
#
# - `id_nomenclature_biogeo_status` (STAT_BIOGEO) : la vue ne l'expose pas. Elle a été
#   ajoutée à la Synthèse après l'écriture de `v_synthese_sinp`, qui n'a pas suivi ;
# - `id_nomenclature_valid_status` (STATUT_VALID) : la vue publie `validateur`, qui est un
#   nom de personne, et non le statut de validation. Il vient donc de `[validation]`,
#   comme pour dbChiro.
SOURCE_DE = {
    colonne: {v: k for k, v in COLONNES_VUE.items()}.get(mnemonique)
    for colonne, mnemonique in COLONNES_NOMENCLATURE.items()
}

# Nomenclatures que la vue livre mais que l'INSERT commun ne sait pas écrire. Les ajouter
# à `INSERT_SQL` obligerait les trois autres `to_row` à fournir le paramètre lié —
# `tests/test_insert_alignement.py` l'impose dans les deux sens. Elles partent donc en
# `additional_data`, où elles restent lisibles sur la fiche d'observation.
HORS_INSERT = {
    "type_regroupement": "gn_type_regroupement",
    "methode_regroupement": "gn_methode_regroupement",
    "type_info_geo": "gn_type_info_geo",
    "methode_determination": "gn_methode_determination",
}


def valeurs(item: dict) -> dict[str, str]:
    """Libellés de nomenclature portés par un enregistrement, colonne de vue par colonne.

    Fonction pure, sans base : c'est ce qui rend la correspondance testable hors instance.
    """
    return {
        colonne: str(item.get(colonne) or "").strip()
        for colonne in COLONNES_VUE
        if str(item.get(colonne) or "").strip()
    }


def resoudre(item: dict, resolver, *, statut_validation: str | None = None,
             manques: set | None = None) -> dict[str, int | None]:
    """Les quinze colonnes `id_nomenclature_*` de l'INSERT, résolues.

    Chaque colonne prend le défaut de la Synthèse quand la vue ne dit rien : c'est
    exactement ce qu'aurait fait le DEFAULT de la colonne si l'on insérait ligne à ligne.
    L'insertion par lots interdit d'omettre une colonne pour une seule ligne, d'où la
    résolution explicite.
    """
    resolus: dict[str, int | None] = {}
    for colonne, mnemonique in COLONNES_NOMENCLATURE.items():
        source = SOURCE_DE.get(colonne)
        brut = str(item.get(source) or "").strip() if source else ""
        if colonne == "id_nomenclature_valid_status" and statut_validation:
            brut = statut_validation
        if not brut:
            resolus[colonne] = resolver.defaut(mnemonique)
            continue
        trouve = resolver.id_souple(mnemonique, brut)
        if trouve is None:
            # Le référentiel local ne connaît pas cette valeur. On retombe sur le défaut
            # — l'insertion doit aboutir — mais on le consigne, sans quoi la perte serait
            # invisible. C'est précisément le silence d'api2GN qu'on refuse ici.
            if manques is not None:
                manques.add((mnemonique, brut))
            resolus[colonne] = resolver.defaut(mnemonique)
        else:
            resolus[colonne] = trouve
    return resolus


def niveau_diffusion(item: dict, resolver, *, force: str = "",
                     si_sensible: str = "", manques: set | None = None) -> int | None:
    """`id_nomenclature_diffusion_level`, ou None si personne ne se prononce.

    Cette colonne se traite à part des quinze autres, et pour une raison de fond :
    GeoNature a cessé de la calculer et lui a retiré son DEFAULT. NULL y signifie « le
    producteur ne se prononce pas », ce qui est une valeur légitime. Y mettre le défaut
    d'un type de nomenclature reviendrait à **inventer une restriction de diffusion**, ou
    à en faire disparaître une. On ne retombe donc jamais sur le défaut.

    Trois sources, dans cet ordre :

    1. `force` — une valeur imposée en configuration, qui prime sur tout ;
    2. `si_sensible` — une restriction appliquée dès que le producteur déclare
       l'observation sensible. C'est la parade au décalage de référentiels : la sensibilité
       elle-même est recalculée localement par le trigger de la Synthèse, et si notre
       référentiel couvre moins d'espèces que celui du distant, la donnée serait **moins**
       protégée chez nous qu'à la source ;
    3. `precision_diffusion` — ce que le producteur a effectivement exprimé. C'est
       strictement mieux que la valeur globale de configuration qu'emploient dbChiro et
       VisioNature : ici l'arbitrage est porté par la donnée, observation par observation.
    """
    if force:
        return resolver.id_souple("NIV_PRECIS", force)

    if si_sensible:
        sensibilite = str(item.get("niveau_sensibilite") or "").strip()
        # Le libellé exact du « non sensible » varie (« Non sensible », « Aucune »…) : on
        # traite comme sensible tout ce qui est renseigné sans commencer par « non ».
        if sensibilite and not sensibilite.lower().startswith("non"):
            return resolver.id_souple("NIV_PRECIS", si_sensible)

    brut = str(item.get("precision_diffusion") or "").strip()
    if not brut:
        return None
    trouve = resolver.id_souple("NIV_PRECIS", brut)
    if trouve is None and manques is not None:
        manques.add(("NIV_PRECIS", brut))
    return trouve
