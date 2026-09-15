# gn_module_connectors

Module GeoNature qui **alimente la Synthèse depuis des sources externes**. Vous le
configurez, vous lancez une commande, les observations arrivent dans la Synthèse avec
leurs jeux de données, leurs nomenclatures SINP et leur provenance.

| Source | Ce qu'elle apporte |
|---|---|
| **GBIF** | l'agrégateur mondial : tout ce qui est publié sur votre territoire, INPN compris |
| **VisioNature / Biolovision** | les portails Faune-France, Faune-Occitanie et consorts |
| **dbChiro** | les chiroptères d'une instance dbchiroweb régionale |
| **GeoNature** | une autre instance GeoNature, via son module d'export |

Le module tourne **dans** GeoNature et écrit par `db.session`. Conséquences pratiques :
aucun identifiant PostgreSQL à distribuer, insertion par lots plutôt qu'une requête par
observation, et une installation à la portée d'un administrateur fonctionnel.

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Installation](#installation)
- [Configurer](#configurer)
- [Conventions des commandes](#conventions-des-commandes)
- [GBIF](#gbif) · [VisioNature](#visionature) · [dbChiro](#dbchiro) · [GeoNature](#geonature)
  — l'essentiel ici, le détail dans [`docs/connecteurs/`](docs/connecteurs/)
- [Pré-validation](#pré-validation) — faut-il valider ce qu'on moissonne ?
- [Purger](#purger) — revenir en arrière
- [Points de vigilance](#points-de-vigilance) — **à lire avant le premier import**
- [Tests](#tests) · [Documentation](#documentation) · [Développement](#développement)

Le **pourquoi** des choix — ce qui a été mesuré, essayé, écarté — est dans
[docs/decisions.md](docs/decisions.md). Ce fichier-ci s'en tient au comment, dans les
grandes lignes ; chaque source a son propre approfondissement dans `docs/connecteurs/`.

---

## Démarrage rapide

Le module est fait pour être essayé sans risque : chaque import sait simuler, et chaque
suppression exige un geste explicite.

```bash
# 1. installer, puis redémarrer le backend
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
sudo systemctl restart geonature

# 2. vérifier que le module se voit et que TAXREF est prêt
geonature connectors statut

# 3. configurer : copier l'exemple à côté de geonature_config.toml, puis l'éditer
cp connectors_config.toml.example /chemin/vers/connectors_config.toml

# 4. simuler un import — n'écrit rien
geonature connectors gbif-import --dry-run

# 5. importer pour de bon
geonature connectors gbif-import
```

`connectors_config.toml.example` est **commenté réglage par réglage** : c'est la référence
de configuration, plus complète que ce fichier.

⚠️ Avant le premier import réel, lisez les [points de vigilance](#points-de-vigilance).
Deux d'entre eux ont des conséquences juridiques, pas seulement techniques.

---

## Installation
```bash
source ~/geonature/backend/venv/bin/activate
```
```bash
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
```

⚠️ **Redémarrer le backend juste après.** L'installation enregistre le module en base,
mais les workers gunicorn déjà lancés n'en voient pas le code Python : tant qu'ils ne sont
pas relancés, `/gn_commons/modules` renvoie une erreur 500 et l'interface est inutilisable.

```bash
sudo systemctl restart geonature
```

### Mise à jour du module

⚠️ **Copier les fichiers ne suffit pas.** Si vous mettez le module à jour par `rsync`,
`scp` ou `git pull`, le code Python change mais la métadonnée du paquet installée dans le
venv reste celle de l'installation précédente. Les dépendances déclarées dans
`requirements.in` ne sont donc pas résolues, et la première commande qui les utilise
échoue par un `ModuleNotFoundError` sans rapport apparent avec la mise à jour :

```
File ".../sources/visionature/biolovision/api.py", line 31, in <module>
    from requests_oauthlib import OAuth1
ModuleNotFoundError: No module named 'requests_oauthlib'
```

Rejouer `install-gn-module` après chaque mise à jour, puis redémarrer le service :

```bash
source ~/geonature/backend/venv/bin/activate
geonature install-gn-module /chemin/vers/gn_module_connectors CONNECTORS --build false
sudo systemctl restart geonature
```

Pour débloquer une instance sans réinstaller, la dépendance manquante suffit —
c'est un venv, donc pip s'y applique sans contournement :

```bash
source ~/geonature/backend/venv/bin/activate
pip install requests_oauthlib
```

Vérifier :

```bash
geonature connectors statut
```

La commande affiche le nombre d'observations en Synthèse, les sources déclarées, et
surtout **la couverture des correspondances TAXREF ↔ GBIF**. Sans elles, aucune
occurrence n'est importable : autant le savoir avant de lancer un import plutôt qu'après.

### Ce que crée l'installation

| Objet | Détail |
|---|---|
| Module `CONNECTORS` | `gn_commons.t_modules`, avec ses permissions `R` et `C` |
| Cadre d'acquisition | « Import de données externes depuis le GBIF » (UUID fixe, rejouable) |
| Source Synthèse | `GBIF`, avec `url_source = https://www.gbif.org/occurrence` et `entity_source_pk_field = gbifID` |

La source suffit à obtenir, sans une ligne de frontend, le bouton « voir la donnée
source » de la fiche d'observation : l'interface concatène `url_source` et
`entity_source_pk_value`, où le module stocke le `gbifID`.

---

## Configurer

Copier `connectors_config.toml.example` en `connectors_config.toml`, **à côté de
`geonature_config.toml`**. Le fichier est facultatif : sans lui, les valeurs par défaut
s'appliquent. Toute clé inconnue est refusée au démarrage.

Emplacements recherchés, dans l'ordre :

1. le chemin donné par `GEONATURE_CONNECTORS_CONFIG_FILE` ;
2. `<dossier de geonature_config.toml>/connectors_config.toml` ;
3. `<racine du module>/config/conf_gn_module.toml`.

Chaque connecteur a sa section — `[gbif]`, `[visionature]`, `[dbchiro]`, `[geonature]` —
et se désactive indépendamment par `enabled = false`. Les chapitres qui suivent donnent le
minimum vital de chacun ; `connectors_config.toml.example` donne le reste, commenté.

⚠️ **Une clé inconnue empêche GeoNature de démarrer**, sur un « Unknown field » de
Marshmallow. C'est délibéré : une faute de frappe sur `gadm_gid` casse le démarrage au
lieu de moissonner silencieusement le mauvais territoire.

---

## Conventions des commandes

Les commandes se nomment `<source>-<action>`, la source portant son nom entier :
`gbif-`, `visionature-`, `dbchiro-`, `geonature-`. Une seule exception, `statut`, qui
ne dépend d'aucune source.

Les options sont en **français**, avec deux exceptions assumées : `--dry-run` et `--yes`.

Le comportement par défaut est **asymétrique, et c'est voulu** :

| | par défaut | pour agir |
|---|---|---|
| `*-import` | écrit | `--dry-run` pour simuler |
| `*-purge` | simule | `--yes` pour exécuter |
| `geonature-reconcilier` | simule | `--yes` pour exécuter |

Un import s'ajoute et se rejoue sans dommage — les identifiants sont déterministes, une
seconde exécution ne produit rien. Une purge détruit. Qu'elle exige un geste explicite
est une protection, pas une incohérence, et `tests/test_commandes.py` le vérifie pour
qu'on ne le « corrige » pas par mégarde.

`tests/test_commandes.py` ancre le reste de ces conventions : chaque drapeau doit figurer
dans une liste explicite, aucun ne peut employer un terme anglais hors des deux
exceptions, chaque source doit avoir sa purge et toutes doivent offrir les mêmes
garanties, et toute option doit
correspondre à un paramètre de sa fonction. Cette dernière vérification n'est pas
théorique : renommer un drapeau sans figer son nom Python fait échouer la commande à
l'exécution seulement, jamais à l'import.

Le détail des renommages qui ont conduit à ces conventions est dans
[docs/decisions.md](docs/decisions.md#conventions).

---

## GBIF

```toml
[gbif]
enabled = true
gadm_gid = "FRA.11.1_1"      # l'Ariège ; voir gbif-synchroniser-jeux pour le vôtre
```

Aucun compte ni jeton : l'API GBIF est publique. Le seul réglage indispensable est le
périmètre géographique — `gadm_gid` est le seul filtre fiable, une bbox déborde largement
sur les territoires voisins.

```bash
geonature connectors gbif-synchroniser-jeux --dry-run   # prévisualiser les JDD (facultatif)
geonature connectors gbif-import --dry-run
geonature connectors gbif-import
geonature connectors gbif-import --jeu <clé> --perimetre FRA.11.1_1 --incertitude-max 1000
```

L'import est **idempotent** (identifiants déterministes) et crée les JDD à la volée, un
par jeu de données GBIF, avec la citation officielle du producteur en description.

⚠️ Deux défauts à ne pas toucher sans raison : `occurrence_status = PRESENT` (sinon les
absences deviennent des présences fausses) et `licenses` sans CC BY-NC (viral — une seule
occurrence non commerciale rend l'agrégat entier non commercial). Et **aucune clé ne
permet de dédoublonner automatiquement** contre votre Synthèse existante :
`exclude_dataset_keys` est un travail de curation manuel, irréductible.

Planification par `[gbif.schedule]` : un worker Celery déjà présent dans GeoNature s'en
charge, en sautant les jeux inchangés depuis le dernier passage.

→ Table complète des réglages, volumes mesurés sur l'Ariège, détail de la planification :
[docs/connecteurs/gbif.md](docs/connecteurs/gbif.md)

---

## VisioNature

```toml
[visionature]
enabled = true
url = "https://www.faune-occitanie.org"
user_email = "…"
user_password = "…"
client_key = "…"         
client_secret = "…"
pseudonymisation_secret = "…"   # obligatoire — python3 -c "import secrets; print(secrets.token_urlsafe(48))"
departements = ["09"]           # sinon, moissonne toute l'étendue de l'instance
```

```bash
geonature connectors visionature-import --dry-run
geonature connectors visionature-import
geonature connectors visionature-import --depuis 2026-08-01   # incrémental (10 semaines max)
geonature connectors visionature-import --debut 2005-01-01 --fin 2006-01-01  # historique, sans limite
geonature connectors visionature-reanonymiser --yes           # rattrape les changements de consentement
geonature connectors visionature-purge --taxon Reptilia --yes
```

⚠️ **`--depuis` ne remonte pas au-delà de dix semaines** — la fenêtre différentielle de
l'API Biolovision. Au-delà, utiliser `--debut` : il rattrape un historique sans limite
d'ancienneté, mais date sur l'**observation** et non la **saisie**, et ne répercute pas
les suppressions. Les deux options se refusent mutuellement.

⚠️ **`pseudonymisation_secret` ne doit jamais changer, et doit être sauvegardée hors de
la machine.** Elle seule permet de retrouver les pseudonymes déjà écrits
(`visionature-reanonymiser` s'appuie dessus) et de rapprocher les observations d'un même
contributeur. La perdre ou la remplacer après un import a des conséquences
irréversibles.

Le consentement à l'anonymat est **individuel** (champ `anonymous` par observateur, lu
observation par observation) : un observateur absent du référentiel est pseudonymisé par
défaut, l'ignorance ne valant pas consentement. Les observations masquées (`hidden`, pour
protéger une espèce ou un site sensible) sont **importées**, pas écartées — avec un niveau
de diffusion « Aucune ».

Pour un gros historique, partitionner par département et par année (débit mesuré :
~32 obs/s, ~5 jours pour 14 millions d'observations d'une région) :

```bash
for dep in 09 11 12 30 31 32 34 46 48 65 66 81 82; do
  for an in $(seq 2005 2026); do
    geonature connectors visionature-import --departement "$dep" \
      --debut "${an}-01-01" --fin "$((an+1))-01-01" 2>&1 | tee -a moisson.log
  done
done
```

Un 401 signale une clé invalide ; un 403, une clé valide dont le périmètre ne couvre pas la demande —
`geonature connectors visionature-diagnostic` sonde ce que le compte peut réellement
faire.

→ Filtrage par périmètre, résolution taxonomique, heure d'observation, cache des
référentiels, lien vers la donnée source, limites connues :
[docs/connecteurs/visionature.md](docs/connecteurs/visionature.md)

---

## dbChiro

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
geonature connectors dbchiro-import
```

⚠️ **Le périmètre moissonné est celui que voit le compte de service.** dbChiro n'expose
aucun jeton d'API : le connecteur se connecte par formulaire, et hérite des droits du
compte employé. Un compte ordinaire sur une instance `SEE_ALL_NON_SENSITIVE_DATA = true`
est le bon profil — le tri de sensibilité est alors fait par le serveur, seul juge
légitime. `access_all_data` ramène aussi les sessions confidentielles et les gîtes
masqués.

**Aucun marqueur de consentement individuel** n'existe côté dbChiro : les observations
publient un nom complet en clair, sauf à activer `pseudonymiser_observateurs = true`.
L'API livre en revanche les coordonnées exactes de cavités nommées, conservées telles
quelles — la restriction se règle par `niveau_diffusion`, pas par floutage.

Les absences (`0obs`, `0du`) sont écartées par défaut, comme `occurrenceStatus = ABSENT`
sur GBIF ; `importer_absences = true` les verse avec un effectif à zéro plutôt que NULL.
La suppression n'est pas gérée, comme pour GBIF.

→ Détail du filtrage par compte, gîtes, limites connues (comptage vs estimation, filtre
anti-robot) : [docs/connecteurs/dbchiro.md](docs/connecteurs/dbchiro.md)

---

## GeoNature

Moissonne une **autre instance GeoNature**, via son module d'export. C'est la seule
source qui parle déjà le même langage que la destination — mêmes nomenclatures SINP,
même TAXREF, mêmes identifiants permanents.

```toml
[geonature]
enabled = true
url = "https://geonature.exemple.fr"
id_export = 12
jeton = "…"
territoires = ["METROP"]
organisme_contact_principal = "ANA - CEN Ariège"
```

```bash
geonature connectors geonature-couverture     # diagnostic, n'écrit rien — à lancer avant tout import
geonature connectors geonature-import --dry-run
geonature connectors geonature-import
geonature connectors geonature-import --perimetre 09 --jeu 4d331cae-65e4-4948-b0b2-a11bc5bb46c2
```

`geonature-couverture` prévient de cinq choses qui, sinon, ne se découvriraient qu'une
fois les données en base : filtres ignorés par le serveur, colonnes absentes de la vue
d'export, version TAXREF distante différente de la locale, correspondances de
nomenclature non résolues, doublons avec une source déjà présente.

⚠️ **Les suppressions ne sont pas détectées automatiquement.** L'API d'export ne publie
aucun journal de suppression ; il faut relire tout le corpus et purger ce qui n'est pas
revenu :

```bash
geonature connectors geonature-import --tout
geonature connectors geonature-reconcilier --yes    # simule sans --yes
```

Commande séparée de l'import, protégée par plusieurs garde-fous (moisson complète,
bornée au couple instance/export, plafond de 5 % du corpus) — sans eux, un filtre mal
réglé viderait la Synthèse.

→ Diagnostic détaillé, gestion incrémentale par double filtre de date, limites connues
(géométries ramenées au centroïde, nomenclatures non transposables) :
[docs/connecteurs/geonature.md](docs/connecteurs/geonature.md)

---

## Pré-validation

Une donnée moissonnée n'a pas été validée ici, et elle n'est pas non plus à valider ici :
sa validation appartient à son producteur. Sans réglage, elle prend pourtant le défaut de
la colonne — `STATUT_VALID` cd 0, « Non évalué » — et va grossir la file du module
Validation, où elle noie les observations internes qui, elles, attendent un arbitrage.

```toml
[validation]
enabled = true
status = "2"        # ou "Probable" : le code prime, le libellé sert de repli
comment = "Validation automatique — données importées depuis une source externe"
jdd_validable = false
```

La pré-validation écrit dans **deux tables** : le statut dans `gn_synthese.synthese`, et
l'historique dans `gn_commons.t_validations`. C'est l'historique qui commande — un trigger
du cœur reporte statut et commentaire dans la Synthèse. L'historique n'est écrit
**qu'une fois par observation**, jamais réécrit : un validateur qui a tranché a le dernier
mot, et rafraîchir la date éteindrait le filtre « modifiée depuis sa validation ».

**Une validation faite chez vous survit aux imports suivants.** Le statut est écrit à la
création de la ligne, puis plus jamais : un réimport qui met à jour l'observation — parce
que la source l'a corrigée — ne remet pas le statut automatique par-dessus la décision de
votre validateur. L'observation corrigée lui revient par le filtre « modifiée depuis sa
validation », qui est fait pour ça.

⚠️ **Règle à connaître avant de toucher à `insert_batch`** : l'écriture de l'historique
doit rester dans **la transaction de l'INSERT**. Le filtre « modifiée depuis sa
validation » compare `meta_update_date` à `validation_date`, et il ne survit que parce que
`NOW()` rend l'heure de la *transaction* et non celle du *statement* — les deux colonnes
reçoivent ainsi la même valeur. Sortir l'écriture de la transaction casserait ce filtre
sans que rien ne le signale.

Le détail — pourquoi une seule écriture, et en quoi nous divergeons de `gn_vn2synthese` —
est dans [docs/decisions.md](docs/decisions.md#pré-validation).

### `jdd_validable`

Le module Validation ne liste que les jeux `validable = true`. `jdd_validable = false` 
en sort les jeux créés par les connecteurs, à la création comme aux passages suivants. 
Un jeu dont les métadonnées ne sont pas rafraîchies (`rafraichir=False`, cas d'un UUID 
venu du producteur) garde en revanche son réglage : il a pu être créé par un autre canal 
et arbitré à la main.

---

## Purger

Les quatre sources ont la même commande, avec les mêmes garanties :

```bash
geonature connectors gbif-purge --taxon Chiroptera
geonature connectors visionature-purge --projet ATLAS --yes
geonature connectors dbchiro-purge --tout --yes
geonature connectors geonature-purge --jeu 4d331cae-65e4-4948-b0b2-a11bc5bb46c2 --yes
```

| | |
|---|---|
| `--taxon` | nom TAXREF : règne, phylum, classe, ordre, famille, ou début de nom scientifique |
| `--tout` | vider toute la source, sans autre critère |
| `--supprimer-jdd-vides` | supprimer ensuite les JDD du cadre devenus vides |
| `--yes` | exécuter. Sans lui, la commande simule et n'écrit rien |

Trois propriétés valent d'être connues, parce qu'elles ont manqué à l'une ou l'autre des
commandes avant leur mutualisation :

**Une purge sans critère est refusée.** Il faut `--tout` pour vider une source, et le
dire explicitement. Une suppression totale ne doit pas pouvoir arriver par omission d'un
filtre — `visionature-purge --yes` l'a permis un temps.

**Un `--taxon` sans correspondance affiche les rangs réellement présents.** « 0
observation concernée » alors que l'interface en montre des milliers laisse croire à une
panne, quand c'est le nom de rang qui n'existe pas sous cette forme dans TAXREF :

```
0 observation(s) concernée(s) — taxon « Chauvesouris »
  ⚠ aucun taxon ne correspond, alors que la source porte 25 observation(s). Rangs présents :
    classe      ordre       famille              n
    Mammalia    Chiroptera  Vespertilionidae    19
    Mammalia    Chiroptera  Rhinolophidae        5
```

**Les JDD vides sont listés avant d'être supprimés**, jamais retirés en silence.

Toute opération est bornée à une seule `id_source` : les données saisies localement,
celles d'Occtax et celles des autres connecteurs ne peuvent pas être touchées, même sur
une erreur de critère. Le trigger `tri_log_delete_synthese` consigne les suppressions
dans `gn_synthese.t_log_synthese`, elles restent donc traçables.

`dbchiro-purge` n'a ni option de jeu de données — dbChiro n'en produit qu'un par
instance, faute d'exposer `study` — ni `--incertitude-max`, la colonne `precision`
restant NULL puisque l'API ne publie aucune incertitude. L'offrir laisserait croire à un
filtre qui ne retiendrait jamais rien.

⚠ **`geonature-purge --supprimer-jdd-vides` ne nettoie que le cadre de repli du module.**
Les jeux moissonnés depuis une autre instance sont créés sous l'UUID du producteur et
rattachés à *ses* cadres d'acquisition, que le ménage ne parcourt donc pas. C'est
délibéré : ces cadres peuvent porter des jeux qu'un autre canal — un dépôt SINP, une
saisie manuelle — possède légitimement, et les retirer parce qu'ils sont vides à un
instant donné dépasserait ce que ce module a le droit de faire.

---

## Points de vigilance

**Ne pas reverser ces données au SINP.** GBIF exclut explicitement la republication de
données qui en sont extraites, et l'essentiel du corpus français en provient déjà via
l'INPN. Marquer les JDD en conséquence dans le module Métadonnées.

**Les occurrences dont le `cd_nom` n'est pas résolu sont rejetées**, jamais insérées avec
`cd_nom` NULL : une telle ligne fait planter l'évaluation des permissions
(`AttributeError` sur `taxref_tree`) dès qu'une permission avec filtre taxonomique existe.
Défaut différé, sans lien apparent avec sa cause.

**`rights_holder` contient souvent un nom de personne** (sur iNaturalist, le pseudo ou le
nom de l'observateur). Sa conservation est imposée par la licence, mais c'est une donnée
personnelle : à porter au registre de traitement.

**Penser à filtrer `gn_profiles.v_synthese_for_profiles`** sur `id_source`, sinon les
profils de taxons sont alimentés par de la donnée externe.

**La suppression n'est pas gérée côté GBIF** — elle l'est côté VisioNature via
`api_diff`, et côté GeoNature via `geonature-reconcilier`, qui relit tout le corpus et
purge ce qui n'est pas revenu. Une occurrence retirée de GBIF reste en base : la
détecter supposerait de comparer l'ensemble des identifiants du périmètre à chaque
passage, ce qui annulerait le bénéfice du court-circuit.

**Une observation sans géométrie à la source est invisible d'ici.** Quand l'export
distant déclare une géométrie — c'est le cas de l'export « Synthese SINP » livré par
GeoNature — le serveur retire de sa réponse toute ligne dont la géométrie est nulle, tout
en la comptant dans son total. Le connecteur le détecte, le dit, et **refuse alors de
considérer la moisson comme complète** : `geonature-reconcilier` s'interdit de tourner
dessus, puisque l'absence de ces observations ne prouve aucune suppression. Si le message
apparaît à chaque passage, la réconciliation ne se fera jamais — c'est à la source qu'il
faut corriger, en donnant une géométrie à ces observations.

**Le référentiel de sensibilité local peut être moins couvrant que celui d'une
instance distante.** `id_nomenclature_sensitivity` est recalculée à l'insertion par le
trigger de la Synthèse : une observation protégée chez le producteur peut donc se
retrouver **moins protégée ici qu'à la source**. Le réglage
`[geonature] niveau_diffusion_si_sensible` rétablit une restriction dès que le producteur
déclare l'observation sensible, mais c'est un choix d'exploitation, pas un automatisme.

**Un niveau de diffusion mal saisi arrête désormais l'import.** `niveau_diffusion`,
`niveau_diffusion_masquees` et `niveau_diffusion_si_sensible` acceptent indifféremment le
`cd_nomenclature` et le libellé, et refusent tout le reste — au démarrage, avant le
premier appel d'API. Auparavant, seul le connecteur GeoNature échouait : les deux autres
passaient la valeur à un résolveur qui retombe sur le défaut du type, et `NIV_PRECIS`
n'en a pas. Une coquille, ou le libellé que l'interface affiche (« Aucune ») au lieu du
code, donnait donc NULL — aucune restriction de diffusion, aucun message — sur les
observations mêmes que le réglage protège.

**Le court-circuit repose sur `dataset.modified`.** Si un producteur pousse des données
sans mettre cette date à jour, le jeu sera sauté à tort. Une exécution `--forcer`
trimestrielle est une précaution raisonnable.

---

## Tests

```bash
python3 -m pytest tests/ -q
```

Aucune dépendance à GeoNature ni à PostgreSQL : la suite tourne sur une copie du dépôt,
sans instance. C'est ce qui la rend utilisable en intégration continue et avant chaque
envoi.

Deux fichiers méritent d'être connus de qui modifie le module :

- **`tests/test_insert_alignement.py`** confronte le `to_row` de chaque source au texte de
  `INSERT_SQL`, dans les deux sens. Un paramètre lié manquant fait échouer l'insertion
  d'un **lot entier** ; une clé produite en trop est un calcul jeté en silence. C'est le
  contrôle qui manquait quand le connecteur VisioNature a été écrit avec huit colonnes de
  nomenclature là où l'INSERT en portait quatorze.
- **`tests/test_referentiel_sinp.py`** confronte chaque correspondance à un extrait
  versionné du référentiel SINP (`tests/data/referentiel_sinp.json`, tiré du SQL
  d'installation de GeoNature). Les autres tests vérifient qu'on choisit la bonne valeur ;
  celui-ci vérifie qu'elle **existe** — un `cd_nomenclature` inventé ou un libellé
  approché rend NULL, donc le défaut de la colonne, sans le moindre signe. Il a été écrit
  après un audit qui a trouvé deux libellés impossibles dans la fixture du connecteur
  GeoNature (« Vivant » pour « Observé vivant », « Alimentation » pour
  « Chasse/alimentation »), verts depuis toujours.
- **`tests/test_noms_definis.py`** passe le module à l'analyse statique. Les imports étant
  locaux aux commandes — pour ne pas charger l'API Biolovision quand on lance une commande
  GBIF —, un import oublié ne se voit ni à l'import du module ni à la compilation : il
  attend l'exécution, après plusieurs minutes de chargement des référentiels. C'est arrivé
  deux fois.

⚠️ **Un test écrit à partir du code plutôt que de la donnée ne prouve rien.** Trois défauts
de ce module ont vécu sous un test vert qui vérifiait l'hypothèse fausse du code qu'il
couvrait. Écrire les cas à partir d'un export réel, pas de la fonction testée.

⚠️ Les cas du connecteur GeoNature font exception malgré cette règle : ils sont bâtis sur
un enregistrement **reconstitué depuis la définition SQL de `gn_exports.v_synthese_sinp`**,
faute d'accès à une instance distante au moment de l'écriture. Noms et types de colonnes
sont exacts, la distribution des valeurs ne l'est pas. À confronter à un sondage réel dès
qu'une instance sera disponible.

---

## Documentation

| Fichier | Contenu |
|---|---|
| [`docs/connecteurs/gbif.md`](docs/connecteurs/gbif.md) | GBIF en détail : réglages, workflow JDD, planification, chiffres mesurés |
| [`docs/connecteurs/visionature.md`](docs/connecteurs/visionature.md) | VisioNature en détail : incrémental, anonymisation, cache, résolution taxonomique, limites |
| [`docs/connecteurs/dbchiro.md`](docs/connecteurs/dbchiro.md) | dbChiro en détail : périmètre par compte, absences, gîtes, limites |
| [`docs/connecteurs/geonature.md`](docs/connecteurs/geonature.md) | GeoNature en détail : diagnostic de couverture, incrémental, réconciliation, limites |
| [`docs/decisions.md`](docs/decisions.md) | Le **pourquoi** : ce qui a été mesuré, essayé, écarté, et les résultats négatifs qui évitent de refaire une enquête inutile |
| [`connectors_config.toml.example`](connectors_config.toml.example) | La référence de configuration, commentée réglage par réglage |
| [`docs/audit-nomenclatures.md`](docs/audit-nomenclatures.md) | L'audit des correspondances SINP : méthode, sources de vérité, constats et suites, table de couverture par connecteur |
| `docs/gbif-ariege.md` | L'analyse GBIF complète : volumes, licences, contraintes, chiffres mesurés sur l'Ariège |
| `docs/data/` | Listes de référence : jeux PatriNat de l'Ariège, jeux CC BY-NC |

Les connecteurs autonomes d'origine ont été déplacés dans le dépôt
`geonature-connecteurs-autonomes`.

---

## Développement

`sync-to-docker.sh` déploie le module vers une instance GeoNature docker de
développement, le dépôt vivant hors du volume monté.

```
backend/gn_module_connectors/
├── core/               socle générique — ne connaît aucune source
│   ├── synthese.py         insertion par lots, empreintes, conflits
│   ├── datasets.py         jeux de données, cadres d'acquisition, acteurs
│   ├── nomenclatures.py    résolution des nomenclatures SINP
│   ├── purge.py            suppressions, toujours bornées à une id_source
│   └── report.py           journal des rejets
└── sources/            un sous-paquet par source
    ├── gbif/  visionature/  dbchiro/  geonature/
    │   ├── api.py             dialogue HTTP, pagination, filtres
    │   ├── taxonomy.py        résolution du cd_nom
    │   ├── nomenclatures.py   vocabulaire de la source -> SINP
    │   └── transform.py       enregistrement source -> ligne de Synthèse
```

La frontière `core` / `sources` est ce qui permet d'ajouter une source sans réécrire
l'écriture, les métadonnées ni le journal des rejets. Il n'y a **ni classe de base ni
registre** : un connecteur est une convention, et le contrat réel est le dictionnaire que
rend `transform.to_row`, que `tests/test_insert_alignement.py` confronte à `INSERT_SQL`.

### Le client Biolovision est vendorisé

`sources/visionature/biolovision/` est une **copie** du client de
[Client_API_VN](https://github.com/dthonon/Client_API_VN) (Daniel Thonon, GPL-3.0),
révision `376c2e1b`. Copié plutôt que dépendu : le paquet complet déclare vingt
dépendances dont aucune n'est utilisée par la couche API, qui ne demande que `requests` et
`requests_oauthlib`. Ne pas éditer ces fichiers — toute adaptation va dans
`sources/visionature/api.py`.

⚠️ **Une seule divergence avec l'amont**, à réappliquer en cas de mise à jour du client.
`BiolovisionAPI` accepte un `timeout`, mais **aucune de ses onze sous-classes ne le
relayait** : il restait à `None` quel que soit le contrôleur, et `requests` attendait
indéfiniment. Un moissonnage pouvait se figer sans fin ni message, le client journalisant
dans un logger que la CLI n'affiche pas. Les onze constructeurs le transmettent désormais,
et `tests/test_visionature.py` le vérifie sur chaque contrôleur employé : si un
re-vendoring écrase le correctif, les tests le disent. À signaler en amont.
