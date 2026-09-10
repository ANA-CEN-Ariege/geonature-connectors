"""Restriction géographique du moissonnage VisioNature.

`gn_vn2synthese` filtre par un zonage `VN_COVER` dans `ref_geo.l_areas`, testé par un
trigger `BEFORE INSERT` sur leur table de transit. Deux raisons de ne pas transposer :

- nous n'avons pas de table de transit. Le trigger devrait vivre sur
  `gn_synthese.synthese`, où il s'appliquerait à **toutes** les sources et tous les
  modules — Occtax, Import, les autres connecteurs — et jetterait silencieusement des
  données qui ne nous appartiennent pas ;
- un filtre en aval suppose d'avoir tout téléchargé. Sur une instance régionale comme
  Faune-Occitanie, c'est treize départements moissonnés pour en garder un.

Les observations portent leur rattachement administratif : `place.county` est le code de
département, `place.insee` celui de la commune (vérifié sur des exports réels —
`{"insee": "11262", "municipality": "Narbonne", "county": "11"}`). Le filtre se fait donc
sur cette donnée, sans géométrie, sans PostGIS et sans toucher au schéma.

⚠ C'est un filtre **administratif**, pas géométrique. Il ne remplace pas un zonage pour
qui veut un périmètre qui ne suit pas les limites de départements — un bassin versant, un
parc. Pour ces cas-là, le `VN_COVER` reste la bonne réponse et n'est pas implémenté ici.
"""


def _code(valeur) -> str:
    """Normalise un code de département : « 9 » et « 09 » désignent l'Ariège."""
    brut = str(valeur or "").strip().upper()
    return brut.zfill(2) if brut.isdigit() else brut


def normaliser(codes) -> set[str]:
    """Codes de configuration, normalisés. Un ensemble vide signifie « aucun filtre »."""
    return {c for c in (_code(v) for v in (codes or [])) if c}


def departement(sighting: dict) -> str | None:
    """Code de département d'un relevé, d'après son lieu.

    ⚠ Les champs disponibles dépendent du format de réponse. Un export du portail porte
    `county`, `insee` et `municipality` ; la réponse d'API au format court peut n'en
    porter aucun. D'où le repli, puis `None` — que l'appelant interprète selon qu'il a
    déjà borné le territoire côté serveur ou non.
    """
    lieu = sighting.get("place") or {}
    code = _code(lieu.get("county"))
    if code:
        return code
    # Repli sur le code INSEE, dont les deux premiers caractères sont le département.
    # La Corse (2A, 2B) impose de ne pas se contenter d'un test numérique.
    insee = str(lieu.get("insee") or "").strip().upper()
    return insee[:2] if len(insee) >= 2 else None


def dans_perimetre(sighting: dict, codes: set[str], borne_serveur: bool = False) -> bool:
    """Le relevé est-il dans le périmètre ? Sans filtre configuré, tout passe.

    `borne_serveur` dit si la source a **déjà** restreint le territoire. C'est le cas du
    moissonnage complet, qui passe par `observations/search` avec `territorial_unit_ids` :
    l'API ne renvoie alors que le territoire demandé, et sa garantie vaut la nôtre.

    Ce paramètre n'est pas une commodité, il corrige une erreur. La réponse au format
    court ne porte pas toujours le rattachement administratif dans `place` ; un relevé
    dont le département est indéterminable était donc écarté, et un moissonnage
    territorialement borné rejetait **la totalité** de ce qu'il venait de télécharger —
    mesuré : 472 relevés lus, 472 écartés.

    Hors de ce cas, l'indétermination reste un rejet : sans borne serveur, laisser passer
    ce qu'on ne sait pas situer ferait du filtre une passoire silencieuse.
    """
    if not codes:
        return True
    dep = departement(sighting)
    if dep is None:
        return borne_serveur
    return dep in codes
