"""
Journal des occurrences écartées pendant un import.

Module **générique** : rien ici ne dépend de GBIF. Destiné à être remonté tel quel dans le
socle commun le jour où `vn2geonature` sera porté dessus.

Raison d'être : sans trace, un import qui annonce « 40 000 importées » sur 100 000 sources
est indistinguable d'un import correct. On ne sait ni ce qui manque, ni pourquoi, ni si le
problème vient de la source, du référentiel ou du mapping. Compter ne suffit pas — il faut
pouvoir rouvrir le dossier ligne à ligne.
"""

import csv
from collections import Counter
from pathlib import Path

FIELDS = ["reason", "record_id", "label", "detail", "portee"]

# Libellés lisibles, pour le résumé de fin d'import.
REASONS = {
    "no_coordinates": "sans coordonnées",
    "no_cd_nom": "taxon non résolu dans TAXREF",
    "license_excluded": "licence exclue par la configuration",
    "license_nc_no_target": "CC BY-NC sans JDD de destination",
    "dataset_excluded": "jeu de données exclu",
    "observer_excluded": "observateur hors liste",
    "uncertainty_too_high": "incertitude géographique trop élevée",
    "write_error": "erreur à l'écriture dans GeoNature",
    # Deux notions distinctes, longtemps confondues sous la seule clé `hors_perimetre` :
    # GBIF y rangeait un rejet sur le TYPE d'enregistrement, les trois autres connecteurs
    # un rejet GÉOGRAPHIQUE. Le journal annonçait donc « spécimen de collection ou
    # fossile » pour une observation simplement située hors du territoire demandé.
    "hors_perimetre": "hors du périmètre géographique demandé",
    "type_enregistrement_exclu": "type d'enregistrement exclu "
                                 "(spécimen de collection ou fossile)",
    "no_date": "date indéterminable",
    "metadonnees_illisibles": "métadonnées GBIF du jeu illisibles",
    "echec_reseau_gbif": "échec réseau GBIF, jeu ignoré",
    "jeu_maille": "jeu de données publié à la maille",
    "taxon_exclu": "taxon exclu par la configuration",
    "confidentielle": "observation confidentielle à la source",
    "espece_non_resolue": "espèce sans correspondance TAXREF",
    "absence": "absence déclarée à la source (aucun taxon observé)",
    "codesp_inconnu": "code espèce absent de la table dbChiro",
    "cd_nom_hors_taxref": "cd_nom absent du TAXREF de cette instance",
    "deja_presente_autre_source": "déjà en Synthèse sous une autre source",
    "jdd_inactif": "jeu de données local désactivé",
    "colonne_manquante": "colonne absente de la vue d'export distante",
}


# Portées possibles d'un rejet — voir `Rejects.add()`.
PORTEE_OBSERVATION = "observation"
PORTEE_REFERENTIEL = "referentiel"

class Rejects:
    """Collecte les rejets d'un import, avec leur cause.

    L'usage typique est d'instancier un collecteur par exécution, de le passer aux étages
    qui filtrent, puis d'appeler `write_csv()` et `summary_lines()` à la fin.
    """

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, reason: str, record_id: str = "", label: str = "", detail: str = "",
            portee: str = PORTEE_OBSERVATION) -> None:
        """Enregistre un rejet.

        `portee` distingue un rejet qui porte sur UNE observation (le cas courant) d'un
        rejet qui porte sur un référentiel chargé une fois au démarrage — typiquement le
        catalogue d'espèces d'une source, dont les entrées sans correspondance TAXREF ne
        sont pas des observations écartées. Se fier au seul nom du motif (`reason`) pour
        cette distinction est un piège : deux connecteurs peuvent réutiliser le même motif
        avec des portées différentes (dbChiro range sous `espece_non_resolue` un code
        espèce vide sur UNE observation, sans rapport avec le sens que VisioNature donne
        au même motif pour son référentiel). D'où ce paramètre explicite, à la charge de
        l'appelant qui sait dans quelle boucle il se trouve.
        """
        self.rows.append({
            "reason": reason,
            "record_id": str(record_id or ""),
            "label": str(label or ""),
            "detail": str(detail or ""),
            "portee": portee,
        })

    def __len__(self) -> int:
        return len(self.rows)

    def nombre_observations(self) -> int:
        """Rejets portant réellement sur des observations."""
        return sum(1 for r in self.rows if r["portee"] != PORTEE_REFERENTIEL)

    def nombre_referentiel(self) -> int:
        """Rejets portant sur un référentiel chargé au démarrage."""
        return sum(1 for r in self.rows if r["portee"] == PORTEE_REFERENTIEL)

    def summary_lines_observations(self) -> list[str]:
        """Résumé des seuls rejets d'observations."""
        raisons = Counter(r["reason"] for r in self.rows if r["portee"] != PORTEE_REFERENTIEL)
        return [
            f"  {n:>7}  {REASONS.get(reason, reason)}"
            for reason, n in raisons.most_common()
        ]

    def counts(self) -> Counter:
        return Counter(r["reason"] for r in self.rows)

    def summary_lines(self) -> list[str]:
        """Résumé trié par volume décroissant, prêt à être affiché."""
        return [
            f"  {n:>7}  {REASONS.get(reason, reason)}"
            for reason, n in self.counts().most_common()
        ]

    def write_csv(self, path: Path) -> Path | None:
        """Écrit le journal. Retourne le chemin, ou None s'il n'y a rien à écrire.

        Écriture atomique : un fichier temporaire puis `os.replace`, pour qu'une
        interruption ne laisse pas un journal tronqué qu'on croirait complet.
        """
        if not self.rows:
            return None
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
                writer.writeheader()
                writer.writerows(self.rows)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return path
