"""Résolution du cd_nom TAXREF depuis le référentiel d'espèces dbChiro.

**L'API de dbChiro n'expose aucune correspondance vers TAXREF.** Le lien existe pourtant
en base : le modèle `SpecieDetail` porte `id_cdref_taxref`, aux côtés d'`id_inpn2008`,
`id_visionature` et `id_serena`. Mais le serializer de `/api/v1/search` s'arrête à
`{"codesp": "nycnoc", "sci_name": "Nyctalus noctula", "common_name_fr": …, "sp_true": …}`.
Vérifié sur les 8 039 observations d'une instance réelle : `id_cdref_taxref` n'y figure
jamais. Exposer ce champ en amont est une modification d'une ligne, et rendrait ce
module caduc — c'est le but.

En attendant, la correspondance est **écrite ici, en dur**. Ce n'est pas un pis-aller :
le référentiel dbChiro compte 60 codes pour une trentaine d'espèces, il est stable, et
`codesp` est un mnémonique que les producteurs ne renomment pas. Une table explicite se
relit et se corrige en revue, là où un rapprochement automatique sur `sci_name` serait
muet sur ses propres erreurs.

Le rapprochement par nom latin, retenu pour VisioNature faute de mieux, serait ici un
mauvais choix : plus de la moitié des codes ne désignent aucune espèce (« Myotis
myotis / M. blythii », « Plecotus sp. »), et aucun ne peut être rapproché de `lb_nom`.

⚠ **TAXREF ne propose aucun agrégat pour les chiroptères.** Vérifié sur TAXREF v16 : ni
rang `AGES`, ni entrée à barre oblique, ni hybride — `Myotis myotis/blythii` n'existe
pas, alors que c'est le troisième code le plus employé de l'instance (333 observations).
Le repli au rang supérieur commun est donc la seule voie, et il est explicite ci-dessous
plutôt que déduit d'une règle sur la chaîne de caractères.
"""

# ── Espèces déterminées ──────────────────────────────────────────────────────
# `sp_true = true` côté dbChiro. cd_nom vérifiés contre TAXREF v16 : tous valides
# (cd_nom = cd_ref), aucune homonymie.
ESPECES = {
    "rhihip": 60313,   # Rhinolophus hipposideros
    "rhifer": 60295,   # Rhinolophus ferrumequinum
    "rhieur": 60330,   # Rhinolophus euryale
    "minsch": 79305,   # Miniopterus schreibersii
    "pippip": 60479,   # Pipistrellus pipistrellus
    "pipkuh": 79303,   # Pipistrellus kuhlii
    "pippyg": 60489,   # Pipistrellus pygmaeus
    "pipnat": 60490,   # Pipistrellus nathusii
    "myonat": 60408,   # Myotis nattereri
    "myodau": 200118,  # Myotis daubentonii
    "myoema": 60400,   # Myotis emarginatus
    "myomys": 60383,   # Myotis mystacinus
    "myomyo": 60418,   # Myotis myotis
    "myoalc": 79299,   # Myotis alcathoe
    "myobec": 79301,   # Myotis bechsteinii
    "myobly": 60427,   # Myotis blythii
    "myocap": 60439,   # Myotis capaccinii
    "nyclei": 60461,   # Nyctalus leisleri
    "nyclas": 60457,   # Nyctalus lasiopterus
    "nycnoc": 60468,   # Nyctalus noctula
    "barbar": 60345,   # Barbastella barbastellus
    "tadten": 60557,   # Tadarida teniotis
    "hypsav": 60506,   # Hypsugo savii
    "eptser": 60360,   # Eptesicus serotinus
    "eptnil": 79302,   # Eptesicus nilssonii
    "pleaus": 60527,   # Plecotus austriacus
    "pleaur": 60518,   # Plecotus auritus
    "plemac": 163463,  # Plecotus macrobullaris
    "vesmur": 60537,   # Vespertilio murinus
}

# Rangs supérieurs employés comme repli, avec leur cd_nom TAXREF v16.
GENRE_MYOTIS = 195005
GENRE_PLECOTUS = 196414
GENRE_PIPISTRELLUS = 196296
GENRE_RHINOLOPHUS = 197139
GENRE_NYCTALUS = 195295
FAMILLE_VESPERTILIONIDAE = 186239
ORDRE_CHIROPTERA = 186233

# ── Déterminations partielles ────────────────────────────────────────────────
# Chaque entrée est un arbitrage, pas une règle mécanique : le cd_nom retenu est le
# **rang le plus bas qui contienne toutes les espèces candidates**. Descendre plus bas
# serait affirmer une détermination que l'observateur n'a pas faite ; remonter plus haut
# perdrait de l'information disponible.
#
# Le libellé d'origine est toujours conservé dans `nom_cite` et dans `additional_data`,
# de sorte que la détermination réelle reste lisible sur la fiche d'observation.
AGREGATS = {
    # « sp. » explicites — le genre est déterminé, l'espèce non.
    "myotis": GENRE_MYOTIS,             # Myotis sp.
    "petmur": GENRE_MYOTIS,             # Myotis sp. (doublon de `myotis` côté dbChiro)
    "pleind": GENRE_PLECOTUS,           # Plecotus sp.
    "pipind": GENRE_PIPISTRELLUS,       # Pipistrellus sp.
    "rhiind": GENRE_RHINOLOPHUS,        # Rhinolophus sp.
    "nycind": GENRE_NYCTALUS,           # Nyctalus sp.

    # Couples et triplets à l'intérieur d'un même genre.
    "murmur": GENRE_MYOTIS,             # Myotis myotis / M. blythii
    "myoalcmys": GENRE_MYOTIS,          # M. alcathoe / mystacinus
    "myoalcema": GENRE_MYOTIS,          # M. alcathoe / emarginatus
    "myoalcemamys": GENRE_MYOTIS,       # M. alcathoe / emarginatus / mystacinus
    "myoalcbramys": GENRE_MYOTIS,       # M. alcathoe / brandtii / mystacinus
    "myobecema": GENRE_MYOTIS,          # M. bechsteinii / emarginatus
    "myoemamys": GENRE_MYOTIS,          # M. emarginatus / mystacinus
    "myodaumys": GENRE_MYOTIS,          # M. daubentonii / mystacinus
    "myodauema": GENRE_MYOTIS,          # M. daubentonii / emarginatus
    "pleausmac": GENRE_PLECOTUS,        # P. austriacus / macrobullaris
    "pleaurmac": GENRE_PLECOTUS,        # P. auritus / macrobullaris
    "pipkuhnat": GENRE_PIPISTRELLUS,    # P. kuhlii / nathusii
    "pippippyg": GENRE_PIPISTRELLUS,    # P. pipistrellus / pygmaeus
    "pippipnat": GENRE_PIPISTRELLUS,    # P. pipistrellus / nathusii
    "rhieurhip": GENRE_RHINOLOPHUS,     # R. euryale / hipposideros
    "rhieurfer": GENRE_RHINOLOPHUS,     # R. euryale / ferrumequinum

    # Plusieurs genres, une seule famille : Eptesicus, Vespertilio et Nyctalus sont tous
    # trois des Vespertilionidae.
    "eptnyc": FAMILLE_VESPERTILIONIDAE,        # Eptesicus / Nyctalus
    "eptvesnyc": FAMILLE_VESPERTILIONIDAE,     # Eptesicus / Vespertilio / Nyctalus
    "nycleivesmur": FAMILLE_VESPERTILIONIDAE,  # N. leisleri / Vespertilio murinus

    # À cheval sur deux familles : rien de plus précis que l'ordre n'est défendable.
    # Miniopterus est un Miniopteridae depuis sa sortie des Vespertilionidae, et
    # Tadarida un Molossidae.
    "pippipminsch": ORDRE_CHIROPTERA,   # P. pipistrellus / M. schreibersii
    "pippygminsch": ORDRE_CHIROPTERA,   # P. pygmaeus / M. schreibersii
    "nyctad": ORDRE_CHIROPTERA,         # Nyctalus / Tadarida
    "chiind": ORDRE_CHIROPTERA,         # Chiroptera sp.
}

# ── Absences ─────────────────────────────────────────────────────────────────
# Ces deux codes ne désignent aucun taxon : ils disent qu'on a cherché et qu'on n'a rien
# trouvé. Les importer tels quels produirait 175 fausses présences de chiroptères, dont
# 171 sur des sites où l'espèce a précisément été recherchée en vain — c'est le même
# piège que `occurrenceStatus = ABSENT` côté GBIF, qui vaut 116 799 observations sur un
# seul jeu ariégeois.
#
# Écartées par défaut. `importer_absences` permet de les verser en `STATUT_OBS = No` sur
# le cd_nom de l'ordre, pour qui veut conserver la trace des prospections négatives.
ABSENCES = {
    "0obs": "Aucune chauve-souris ou trace",
    "0du": "Aucun contact acoustique",
}

TABLE = {**ESPECES, **AGREGATS}


def codesp(properties: dict) -> str:
    """Code espèce dbChiro d'une observation, normalisé."""
    donnees = properties.get("specie_data") or {}
    return str(donnees.get("codesp") or "").strip().lower()


def nom_cite(properties: dict) -> str:
    """Détermination telle que dbChiro l'exprime, à conserver dans `nom_cite`.

    C'est le libellé d'origine — « Myotis myotis / M. blythii » — et non le nom du taxon
    de repli. La colonne `nom_cite` est faite pour ça : dire ce que l'observateur a
    déterminé, quand `cd_nom` ne peut porter qu'un taxon du référentiel.
    """
    donnees = properties.get("specie_data") or {}
    for cle in ("sci_name", "common_name_fr"):
        valeur = str(donnees.get(cle) or "").strip()
        if valeur:
            return valeur
    return codesp(properties) or "?"


def est_absence(properties: dict) -> bool:
    """L'observation déclare-t-elle une absence plutôt qu'un taxon ?"""
    return codesp(properties) in ABSENCES


def est_agregat(properties: dict) -> bool:
    """La détermination porte-t-elle sur un rang supérieur à l'espèce ?"""
    return codesp(properties) in AGREGATS


def resolve(properties: dict, *, importer_absences: bool = False) -> tuple[int | None, str]:
    """(cd_nom, motif de rejet). Le motif est vide quand la résolution aboutit.

    Un code inconnu est **rejeté**, jamais rattaché à l'ordre par défaut : le
    référentiel dbChiro peut s'enrichir, et une nouvelle espèce silencieusement versée
    en « Chiroptera sp. » serait une perte d'information que rien ne signalerait.
    """
    code = codesp(properties)
    if not code:
        return (None, "espece_non_resolue")
    if code in ABSENCES:
        return ((ORDRE_CHIROPTERA, "") if importer_absences else (None, "absence"))
    cd_nom = TABLE.get(code)
    if cd_nom is None:
        return (None, "codesp_inconnu")
    return (cd_nom, "")


def verifier_table(journal=None) -> list[str]:
    """Contrôle que chaque cd_nom de la table existe et est valide dans TAXREF.

    Appelé au démarrage de l'import. Une montée de version de TAXREF peut déprécier un
    taxon — `Myotis daubentonii` a déjà changé de cd_nom entre deux versions — et une
    clé étrangère satisfaite ne dit rien de la justesse du rattachement. Mieux vaut
    l'apprendre avant d'écrire 8 000 lignes qu'après.

    Retourne la liste des anomalies, vide si tout va bien.
    """
    # Import différé : la table ci-dessus doit rester lisible sans GeoNature, pour que
    # la suite de tests tourne sans base — c'est ce qui permet de vérifier les
    # arbitraires taxonomiques en revue plutôt qu'en production.
    from sqlalchemy import text
    from geonature.utils.env import db

    cd_noms = sorted(set(TABLE.values()))
    connus = {
        int(cd): (nom, int(ref))
        for cd, nom, ref in db.session.execute(
            text("SELECT cd_nom, lb_nom, cd_ref FROM taxonomie.taxref "
                 "WHERE cd_nom = ANY(:cds)"),
            {"cds": cd_noms},
        ).all()
    }
    anomalies = []
    for code, cd_nom in sorted(TABLE.items()):
        if cd_nom not in connus:
            anomalies.append(f"{code} -> cd_nom {cd_nom} absent de TAXREF")
            continue
        nom, cd_ref = connus[cd_nom]
        if cd_ref != cd_nom:
            anomalies.append(
                f"{code} -> cd_nom {cd_nom} ({nom}) est un synonyme, "
                f"le taxon retenu est {cd_ref}")
    if journal:
        journal(f"  table taxonomique dbChiro : {len(TABLE)} code(s), "
                f"{len(cd_noms)} taxon(s) distinct(s), {len(anomalies)} anomalie(s)")
        for anomalie in anomalies:
            journal(f"    ⚠ {anomalie}")
    return anomalies
