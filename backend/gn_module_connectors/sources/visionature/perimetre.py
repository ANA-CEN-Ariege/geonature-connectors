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
    """Code de département d'un relevé, d'après son lieu."""
    lieu = sighting.get("place") or {}
    code = _code(lieu.get("county"))
    if code:
        return code
    # Repli sur le code INSEE, dont les deux premiers caractères sont le département.
    # La Corse (2A, 2B) impose de ne pas se contenter d'un test numérique.
    insee = str(lieu.get("insee") or "").strip().upper()
    return insee[:2] if len(insee) >= 2 else None


def dans_perimetre(sighting: dict, codes: set[str]) -> bool:
    """Le relevé est-il dans le périmètre ? Sans filtre configuré, tout passe.

    Un relevé dont le département est indéterminable est **écarté** quand un filtre est
    actif. Le laisser entrer ferait du filtre une passoire silencieuse : mieux vaut le
    voir dans le journal des rejets et décider en connaissance de cause.
    """
    if not codes:
        return True
    dep = departement(sighting)
    return dep is not None and dep in codes
