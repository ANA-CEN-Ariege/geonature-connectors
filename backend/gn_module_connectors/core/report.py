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

FIELDS = ["reason", "record_id", "label", "detail"]

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
    "hors_perimetre": "hors périmètre (spécimen de collection ou fossile)",
    "no_date": "date indéterminable",
    "metadonnees_illisibles": "métadonnées GBIF du jeu illisibles",
    "jeu_maille": "jeu de données publié à la maille",
    "taxon_exclu": "taxon exclu par la configuration",
}


class Rejects:
    """Collecte les rejets d'un import, avec leur cause.

    L'usage typique est d'instancier un collecteur par exécution, de le passer aux étages
    qui filtrent, puis d'appeler `write_csv()` et `summary_lines()` à la fin.
    """

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, reason: str, record_id: str = "", label: str = "", detail: str = "") -> None:
        self.rows.append({
            "reason": reason,
            "record_id": str(record_id or ""),
            "label": str(label or ""),
            "detail": str(detail or ""),
        })

    def add_many(self, reason: str, records: list[dict], id_key: str, label_key: str,
                 detail: str = "") -> None:
        """Enregistre en lot des dictionnaires bruts issus d'une API."""
        for r in records:
            self.add(reason, r.get(id_key, ""), r.get(label_key, ""), detail)

    def __len__(self) -> int:
        return len(self.rows)

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
