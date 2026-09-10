"""Arbitrage des observations déjà présentes en Synthèse sous une autre source.

Ce connecteur reprend l'`unique_id_sinp` publié par le producteur. Il peut donc tomber
sur une observation déjà importée par un autre chemin — et le cas n'est pas théorique :

- `sources/gbif/transform.sinp_uuid` reprend l'UUID contenu dans `occurrenceID` quand
  c'en est un, ce qui est le cas de **100 % des jeux publiés par PatriNat** (mesuré sur
  l'Ariège, `docs/gbif-ariege.md`) ;
- ces jeux représentent **75,8 % du corpus GBIF départemental**, et couvrent précisément
  ce qu'une instance GeoNature partenaire détient aussi : Faune Occitanie, SICEN, ANA,
  LIFE Desman, Natura 2000.

Moissonner un GeoNature partenaire *et* le GBIF sur le même territoire produit donc des
collisions par milliers, pas par unités.

⚠ **Le conflit ne peut pas être arbitré à l'écriture.** `INSERT_SQL` ne réécrit pas
`id_source` : la colonne reste figée sur le premier connecteur qui a inséré la ligne,
tandis que tout le contenu — `additional_data` compris — est écrasé par le dernier qui
passe. Deux connecteurs qui moissonnent la même observation se réécrivent donc l'un
l'autre à chaque exécution, sans fin, et sans qu'aucun `id_source` ne dise la vérité.

Le seul remède est en amont : **une observation ne doit être moissonnée que par un seul
connecteur.** Tout le reste n'est que constat de dégât.
"""

# Politiques admises pour `[geonature] sur_conflit_autre_source`.
POLITIQUES = ("ignorer", "remplacer")


def decider(politique: str, *, gbif_actif: bool) -> tuple[str, list[str]]:
    """(politique retenue, avertissements). Fonction pure.

    `« ignorer »` est le défaut, et c'est la seule option **stable** : le connecteur
    n'écrit pas la ligne, celle de l'autre source reste intacte, et rien n'oscille. Le
    prix est de conserver la copie la moins bonne — celle du GBIF est passée par une
    republication qui a pu dégrader la précision géographique et perdre des nomenclatures
    que la vue SINP du producteur, elle, porte.

    `« remplacer »` donne la bonne copie, mais **seulement si l'autre connecteur cesse de
    moissonner ces observations**. Sans cela, il ne fait que déplacer le problème d'un
    cran : la ligne repart sous notre `id_source` avec le contenu de l'autre au passage
    suivant, et le conflit devient indétectable puisque `id_source` ne le trahit plus.
    """
    politique = str(politique or "ignorer").strip().lower()
    if politique not in POLITIQUES:
        raise ValueError(
            f"sur_conflit_autre_source doit valoir « ignorer » ou « remplacer », "
            f"pas « {politique} ».")

    avertissements = []
    if politique == "remplacer" and gbif_actif:
        avertissements.append(
            "sur_conflit_autre_source = « remplacer » alors que le connecteur GBIF est "
            "actif. Les lignes reprises seront réécrites par le prochain gbif-import, "
            "qui moissonne toujours les mêmes observations : elles porteront alors notre "
            "id_source et le contenu du GBIF, et le conflit cessera d'être détectable.\n"
            "    Écartez d'abord les jeux concernés côté GBIF, par "
            "[gbif] exclude_dataset_keys, puis purgez ce qui est déjà en base.")
    return (politique, avertissements)


def diagnostic_ecrasement(ecrasees: int, total: int) -> list[str]:
    """Ce que dire d'un décompte de lignes écrasées par un autre connecteur.

    Une ligne portant notre `id_source` mais dépourvue de `gn_empreinte` a forcément été
    réécrite par un autre connecteur depuis notre dernier passage : c'est la signature
    exacte du conflit devenu invisible, et le seul moyen de le voir.
    """
    if not ecrasees:
        return []
    part = (100 * ecrasees / total) if total else 0
    return [
        f"{ecrasees} ligne(s) de cette source ({part:.1f} %) portent notre id_source "
        f"mais le contenu d'un autre connecteur : elles ont été écrasées depuis notre "
        f"dernier passage, et le seront de nouveau à chaque exécution des deux côtés.",
        "    Cause : la même observation est moissonnée par deux connecteurs. "
        "Écartez-la de l'un des deux — [gbif] exclude_dataset_keys pour le GBIF, "
        "[geonature] jdd_uuids pour restreindre celui-ci.",
    ]
