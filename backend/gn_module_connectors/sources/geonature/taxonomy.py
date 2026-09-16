"""Résolution taxonomique d'une observation venue d'une autre instance GeoNature.

Cas le plus simple des quatre connecteurs, et le seul où la source parle déjà TAXREF :
elle publie un `cd_nom`, qu'il n'y a pas à rapprocher d'un nom scientifique comme pour
VisioNature, ni à lire dans une table écrite à la main comme pour dbChiro.

⚠ Simple ne veut pas dire acquis. **Les deux instances peuvent tourner sur des versions
différentes de TAXREF.** Un `cd_nom` retiré ou renuméroté entre deux versions satisfait
tout de même la clé étrangère si le code existe encore par ailleurs — le rattachement
serait alors faux, et rien ne le signalerait. D'où la résolution contre le référentiel
**local**, et non la confiance faite au code distant.
"""

from .util import _entier


def codes_a_verifier(items: list[dict]) -> set[int]:
    """Tous les codes qu'il faudra confronter au TAXREF local.

    ⚠ `cd_nom` **et** `cd_ref`. N'interroger que les `cd_nom` rendrait le repli de
    `choisir` inopérant : le `cd_ref` ne figurerait jamais dans l'index, donc jamais parmi
    les codes connus, et tout taxon dont le `cd_nom` a changé de version serait rejeté
    alors que son taxon valide est parfaitement présent. Le repli passerait pour du code
    mort sans qu'aucune erreur ne se produise.
    """
    codes = set()
    for item in items or []:
        codes.add(_entier(item.get("cd_nom")))
        codes.add(_entier(item.get("cd_ref")))
    codes.discard(None)
    return codes


def charger_index(codes: set[int]) -> set[int]:
    """Ceux de ces codes qui existent réellement dans le TAXREF local.

    Une requête pour tout l'import, plutôt qu'une par observation : le corpus d'une
    instance départementale porte quelques milliers de taxons distincts pour des centaines
    de milliers d'observations.

    Import de `db` différé : ce module doit rester importable hors instance GeoNature, ce
    qui est la condition pour que la suite de tests tourne sans base.
    """
    valides = {_entier(c) for c in (codes or set())}
    valides.discard(None)
    if not valides:
        return set()
    from sqlalchemy import text
    from geonature.utils.env import db

    return {
        r[0] for r in db.session.execute(
            text("SELECT cd_nom FROM taxonomie.taxref WHERE cd_nom = ANY(:c)"),
            {"c": sorted(valides)},
        ).all()
    }


def choisir(cd_nom, cd_ref, connus: set[int]) -> tuple[int | None, str]:
    """(cd_nom retenu, motif). Motif vide quand la résolution est directe.

    Ordre de résolution, et ce qu'il exclut :

    1. le `cd_nom` distant, s'il existe localement ;
    2. sinon le `cd_ref` — le taxon *valide* du distant. C'est le repli légitime : `cd_ref`
       change bien moins souvent que `cd_nom` d'une version de TAXREF à l'autre, puisqu'il
       désigne le nom retenu et non l'un de ses synonymes. Le motif est rendu pour que le
       bilan compte ces cas au lieu de les fondre dans les succès ;
    3. sinon un rejet.

    ⚠ **Aucun repli par le nom scientifique.** VisioNature y recourt faute de `cd_nom`, et
    c'est un pis-aller assumé là-bas. Ici il n'aurait pas lieu d'être : les deux instances
    partagent le référentiel, et un taxon qui ne résout ni par `cd_nom` ni par `cd_ref`
    signale un écart de version qu'il faut corriger — pas contourner. Le rapprochement par
    nom produirait, sur les homonymies de TAXREF, un rattachement faux et muet.

    ⚠ Ne renvoie **jamais** un `cd_nom` nul sans motif : une ligne écrite avec `cd_nom`
    NULL fait échouer l'évaluation des permissions de GeoNature bien plus tard, sur une
    `AttributeError` qui ne désigne plus sa cause.
    """
    direct = _entier(cd_nom)
    if direct is not None and direct in connus:
        return (direct, "")
    repli = _entier(cd_ref)
    if repli is not None and repli in connus:
        return (repli, "repli_cd_ref")
    return (None, "cd_nom_hors_taxref")


def couverture(items: list[dict], connus: set[int]) -> dict:
    """Ce que donnerait la résolution taxonomique, sans rien écrire.

    Alimente `geonature-couverture`, dont le rôle est de rendre lisible avant l'import ce
    qui, sinon, ne se découvre qu'après — c'est le service que `statut` rend pour les
    correspondances GBIF.
    """
    distincts: dict[int, int] = {}
    directs = replis = 0
    manquants: dict[tuple, int] = {}
    for item in items or []:
        brut = _entier(item.get("cd_nom"))
        if brut is not None:
            distincts[brut] = distincts.get(brut, 0) + 1
        retenu, motif = choisir(item.get("cd_nom"), item.get("cd_ref"), connus)
        if retenu is None:
            cle = (brut, _entier(item.get("cd_ref")),
                   str(item.get("nom_valide") or item.get("nom_cite") or ""))
            manquants[cle] = manquants.get(cle, 0) + 1
        elif motif == "repli_cd_ref":
            replis += 1
        else:
            directs += 1
    return {
        "distincts": len(distincts),
        "directs": directs,
        "replis": replis,
        "rejetes": sum(manquants.values()),
        # Triés par volume : ce qui coûte le plus se corrige en premier.
        "manquants": sorted(manquants.items(), key=lambda kv: -kv[1]),
    }
