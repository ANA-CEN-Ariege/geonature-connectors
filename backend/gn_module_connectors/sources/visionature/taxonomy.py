"""Résolution du cd_nom TAXREF depuis le référentiel d'espèces VisioNature.

**L'API Biolovision n'expose aucune correspondance vers TAXREF.** Vérifié sur une
instance réelle : `/api/species/?id=94` renvoie

    {"id": "94", "latin_name": "Anas crecca", "french_name": "Sarcelle d'hiver", …}

— ni `cd_nom`, ni `taxref_id`. L'identifiant d'espèce VisioNature est purement interne :
l'espèce 94 est une Sarcelle d'hiver, quand le `cd_nom` 94 de TAXREF désigne
*Lacerta salamandra*. Confondre les deux, comme le faisait un connecteur antérieur,
importe des sarcelles en salamandres sans qu'aucun contrôle ne le signale — la clé
étrangère est satisfaite, seule la vraisemblance ne l'est pas.

La correspondance se fait donc sur `latin_name` contre `taxref.lb_nom`. C'est viable :
sur 300 377 taxons valides, TAXREF compte 299 065 `lb_nom` distincts, soit 0,4 %
d'homonymes. Le référentiel d'une instance régionale n'en compte que quelques milliers,
essentiellement des vertébrés et invertébrés communs.

L'index est construit **une fois au démarrage**, jamais par observation. Les espèces non
résolues sont rejetées et journalisées : mieux vaut un rejet tracé qu'un taxon faux.
"""

from sqlalchemy import text
from geonature.utils.env import db


def charger_referentiel(session, url_base: str) -> list[dict]:
    """Référentiel d'espèces complet de l'instance VisioNature."""
    r = session.get(f"{url_base.rstrip('/')}/api/species/", timeout=120)
    r.raise_for_status()
    return r.json().get("data") or []


def _cd_nom_par_nom(noms: list[str]) -> dict[str, list[int]]:
    """cd_nom des taxons **valides** portant ces noms scientifiques.

    Restreint à `cd_nom = cd_ref` : un synonyme renverrait un cd_nom valide mais
    déprécié, alors que la Synthèse doit porter le taxon retenu par TAXREF.
    """
    if not noms:
        return {}
    lignes = db.session.execute(
        text("""
            SELECT lb_nom, cd_nom FROM taxonomie.taxref
            WHERE cd_nom = cd_ref AND lb_nom = ANY(:noms)
        """),
        {"noms": noms},
    ).all()
    index: dict[str, list[int]] = {}
    for nom, cd in lignes:
        index.setdefault(nom, []).append(int(cd))
    return index


def construire_index(especes: list[dict], journal=None) -> tuple[dict[str, int], list[dict]]:
    """(index vn_id -> cd_nom, espèces non résolues).

    Une ambiguïté — plusieurs taxons valides pour un même nom — est traitée comme un
    échec : choisir au hasard entre deux homonymes serait pire que renoncer, puisque
    l'erreur passerait tous les contrôles.
    """
    noms = sorted({(e.get("latin_name") or "").strip() for e in especes if e.get("latin_name")})
    correspondances = _cd_nom_par_nom(noms)

    index: dict[str, int] = {}
    non_resolues: list[dict] = []
    ambigus = 0
    for e in especes:
        nom = (e.get("latin_name") or "").strip()
        cds = correspondances.get(nom) or []
        if len(cds) == 1:
            index[str(e.get("id"))] = cds[0]
        else:
            if len(cds) > 1:
                ambigus += 1
            non_resolues.append({
                "id": str(e.get("id")),
                "latin_name": nom,
                "french_name": e.get("french_name") or "",
                "motif": "homonymie dans TAXREF" if len(cds) > 1 else "absent de TAXREF",
            })

    if journal:
        journal(f"  référentiel VisioNature : {len(especes)} espèce(s), "
                f"{len(index)} résolue(s), {len(non_resolues)} non résolue(s) "
                f"(dont {ambigus} par homonymie)")
    return index, non_resolues


def resolve(sighting: dict, index: dict[str, int]) -> int | None:
    """cd_nom d'une observation, ou None si son espèce n'est pas résolue."""
    espece = sighting.get("species") or {}
    identifiant = espece.get("@id") or espece.get("id")
    if identifiant is None:
        return None
    return index.get(str(identifiant))
