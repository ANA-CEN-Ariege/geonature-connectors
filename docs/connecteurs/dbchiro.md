# dbChiro

*(↑ retour au [README](../../README.md#dbchiro))*

```toml
[dbchiro]
enabled = true
url = "https://dbchiroc.org"
username = "…"          # compte de service, voir plus bas
password = "…"
area = "109"            # zonage Ariège de l'instance
departements = ["09"]
```

```bash
geonature connectors dbchiro-perimetres --nom ariege   # trouver l'identifiant de zonage
geonature connectors dbchiro-import --dry-run
geonature connectors dbchiro-import --max-resultats 25   # premier essai d'écriture
geonature connectors dbchiro-import
```

⚠ `--max-resultats` ramène les observations **les plus récemment modifiées**, l'API triant
sur `-timestamp_update`. C'est fait pour éprouver une écriture sur une instance de
travail, pas pour prélever un échantillon représentatif.

## Le compte de service décide du périmètre

dbChiro n'expose aucun jeton d'API : les vues sont protégées par le `LoginRequiredMixin`
de Django et le connecteur se connecte par le formulaire, en conservant le cookie de
session. Conséquence directe : **le périmètre moissonné est celui que voit le compte
employé**, `SightingListPermissionsMixin` filtrant le queryset selon ses droits.

| compte | ce qu'il ramène |
|---|---|
| `access_all_data` | tout, y compris sessions confidentielles, gîtes masqués, études fermées |
| ordinaire, sur une instance `SEE_ALL_NON_SENSITIVE_DATA = true` | toute la donnée non sensible, gîtes masqués exclus |

**Le second profil est le bon.** Le tri de sensibilité est alors fait par le serveur, qui
en est le seul juge légitime, plutôt que par nous après coup.

## Les absences

`0obs` (« Aucune chauve-souris ou trace », 171 obs) et `0du` (« Aucun contact
acoustique », 4 obs) ne désignent aucun taxon. C'est le même piège que
`occurrenceStatus = ABSENT` du GBIF. Écartées par défaut ; `importer_absences = true` les
verse en `STATUT_OBS = « Non observé »` sur le `cd_nom` de l'ordre, avec un effectif de
**zéro** et non NULL — l'ambiguïté entre « aucun individu » et « effectif non renseigné »
fausserait toute analyse quantitative.

## Observateurs et gîtes : deux points de convention

**Aucun marqueur de consentement individuel n'existe côté dbChiro**, contrairement au
champ `anonymous` de VisioNature. Les 8 039 observations publient un nom complet en clair.
Les diffuser suppose donc un accord de l'exploitant portant sur *l'ensemble* des
contributeurs, et non le consentement de chacun. Le connecteur suit ce choix par défaut ;
`pseudonymiser_observateurs = true` bascule sur le HMAC sans changer une ligne de code.
Comme pour VisioNature, aucun rôle n'est créé dans `utilisateurs.t_roles`.

**L'API livre les coordonnées exactes de cavités nommées** — « Trou souffleur - trois
frères ». La géométrie est conservée telle quelle en base : la flouter serait irréversible
et ruinerait tout suivi de gîte. La restriction se règle par `niveau_diffusion`
(`NIV_PRECIS`), qui n'engage que la diffusion. Le référentiel de sensibilité de GeoNature
s'applique de surcroît au déclenchement du trigger d'insertion.

## Limites connues

Le bouton « voir la donnée source » passe par la redirection
`/connectors/dbchiro/<id>` du module : le permalien dbChiro portant l'identifiant au
milieu du chemin (`/sighting/<id>/detail`), la concaténation du cœur ne pouvait rien
produire de valide.

La suppression n'est pas gérée, comme pour GBIF. Les `countdetails` (sexe, âge, état
sexuel) ne sont pas exposés par `/api/v1/search` : seul `total_count` remonte. Il alimente
`OBJ_DENBR = « Individu »` — sans quoi l'effectif entrait en Synthèse sans dire ce qu'il
dénombrait —, mais **pas** `TYP_DENBR` : un comptage de gîte est souvent une estimation,
et l'API n'expose aucun équivalent de l'`estimation_code` de VisioNature. Écrire
« Compté » ferait passer une estimation pour un comptage. C'est une divergence assumée
avec GBIF et VisioNature, qui écrivent les deux.

Enfin, une instance protégée par un filtre anti-robot bloquera le connecteur —
`demo.dbchiro.org` l'est. Le cas est détecté et signalé explicitement plutôt que de
finir en erreur de décodage JSON.
