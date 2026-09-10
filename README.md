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
- [Pré-validation](#pré-validation) — faut-il valider ce qu'on moissonne ?
- [Purger](#purger) — revenir en arrière
- [Points de vigilance](#points-de-vigilance) — **à lire avant le premier import**
- [Tests](#tests) · [Documentation](#documentation) · [Développement](#développement)

Le **pourquoi** des choix — ce qui a été mesuré, essayé, écarté — est dans
[docs/decisions.md](docs/decisions.md). Ce fichier-ci s'en tient au comment.

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

Les options sont en **français**, avec deux exceptions assumées : `--dry-run` et `--yes`,
que tout utilisateur de ligne de commande reconnaît et que traduire desservirait.

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
périmètre géographique.

### Les réglages qui comptent

| Clé | Défaut | Pourquoi y regarder |
|---|---|---|
| `gadm_gid` | `""` | Seul filtre géographique fiable. Sur l'Ariège : `gadmGid` → 1 309 180 occurrences, `stateProvince` → 1 215, une bbox → 2 582 085 (elle déborde sur l'Aude et la Catalogne) |
| `occurrence_status` | `PRESENT` | Ne pas toucher sans raison : les absences deviendraient des présences fausses. Un seul jeu ariégeois en compte 116 799 |
| `licenses` | `CC0_1_0`, `CC_BY_4_0` | Le CC BY-NC est viral — une seule occurrence non commerciale rend l'agrégat entier non commercial |
| `exclude_dataset_keys` | `[]` | **Aucune clé ne permet de dédoublonner automatiquement contre votre Synthèse existante.** Cette liste est un travail de curation manuel, irréductible |
| `coordinate_uncertainty_max` | *aucun filtre* | Sur l'Ariège, ≤ 1100 m ne retient que 39,9 % des occurrences, et certains jeux publient **tout** à 5 km : un seuil trop bas n'écarte pas les mauvaises données, il annule l'import |
| `keep_unknown_uncertainty` | `true` | 34,9 % des occurrences ne déclarent aucune incertitude. Les garder revient à accepter une précision inconnue |
| `skip_gridded_datasets` | `true` | Écarte les jeux publiés au centroïde de maille |
| `download_doi` | `""` | Obligation de citation. L'API `search` n'en délivre aucun — ne vaut que pour l'API `download` |
| `batch_size` | `1000` | Les triggers de `synthese` sont `FOR EACH STATEMENT` : leur coût ne s'amortit qu'en lots |

---

### 1. Prévisualiser (facultatif)

```bash
geonature connectors gbif-synchroniser-jeux --dry-run
```

Crée ou met à jour **un JDD GeoNature par jeu de données GBIF**, rattaché au cadre
d'acquisition du module. C'est le découpage fidèle : GBIF ne produit rien, il agrège —
le producteur est l'organisation qui publie chaque jeu. Chaque JDD porte donc son
producteur, sa licence et sa **citation officielle GBIF** en description, ce qui satisfait
l'obligation d'attribution par la métadonnée elle-même.

L'opération est idempotente : l'`unique_dataset_id` est dérivé en `uuid5` du triplet
`(GBIF, datasetKey, licence)`. La licence entre dans la clé à dessein — sur iNaturalist
elle varie observation par observation, et un JDD doit rester homogène.

⚠️ **Cette commande n'est pas un préalable.** `gbif-import` crée le JDD lui-même, au
moment de la première écriture. C'est délibéré : un jeu publié à la maille, écarté pour
sa licence, ou dont toutes les occurrences tombent au filtre de précision ne doit pas
laisser un JDD vide dans le module Métadonnées. Sur un périmètre départemental, 43 des
200 jeux sont taggés « grillés » par GBIF et n'auraient jamais reçu la moindre
observation.

`gbif-synchroniser-jeux` reste utile pour prévisualiser le périmètre, et pour rafraîchir les
métadonnées — titre, citation, DOI — quand un producteur les corrige.

### 2. Importer les occurrences

```bash
geonature connectors gbif-import \
    --jeu <clé> --perimetre FRA.11.1_1 \
    --incertitude-max 1000 --dry-run
```

Options utiles : `--max-resultats` pour plafonner, `--lot`, `--doi`,
`--garder-incertitude-inconnue` / `--ecarter-incertitude-inconnue`.

Le JDD est créé à la volée si des occurrences survivent aux filtres. L'import est
**idempotent** : `unique_id_sinp` est déterministe, et réutilise l'UUID
contenu dans l'`occurrenceID` quand il y en a un. Pour les données republiées par l'INPN,
c'est l'identifiant permanent DEE — les observations importées portent donc leur identité
SINP d'origine. Une seconde exécution n'écrit rien.

Chaque observation conserve dans `additional_data` sa provenance complète :
`dataset_key`, `dataset_name`, `rights_holder`, `license`, `license_url`, `gbif_url`,
et le DOI du téléchargement s'il est configuré.

### 3. Automatiser

```toml
[gbif.schedule]
enabled = true
crontab = "0 3 * * 1"     # lundi 3 h
```

Rien à installer : GeoNature fait déjà tourner un worker Celery avec `--beat`. La tâche
traite les jeux un par un — un échec sur une source n'emporte pas les autres — et pose un
verrou consultatif PostgreSQL, libéré automatiquement si le worker meurt.

**Le connecteur saute les jeux inchangés** depuis son dernier passage, en comparant la
date de modification du jeu côté GBIF à celle des observations déjà en base
(`meta_create_date` / `meta_update_date`, entretenues par un trigger de la Synthèse —
aucune table de suivi n'est nécessaire).

| situation | ordre de grandeur |
|---|---|
| semaine sans republication | **3-5 min** |
| semaine où un gros jeu republie | quelques dizaines de minutes |
| premier import d'un périmètre départemental | plusieurs heures |

---

## VisioNature

```toml
[visionature]
enabled = true
url = "https://www.faune-ariege.fr"
user_email = "…"
user_password = "…"
client_key = "…"          # fournis par Biolovision, séparément du compte utilisateur
client_secret = "…"
pseudonymisation_secret = "…"   # obligatoire, voir plus bas
```

```bash
geonature connectors visionature-import --dry-run
geonature connectors visionature-import
geonature connectors visionature-import --depuis 2026-08-01   # incrémental (10 semaines)
geonature connectors visionature-import --debut 2005-01-01 --fin 2006-01-01  # historique
geonature connectors visionature-import --departement 09      # périmètre ponctuel
geonature connectors visionature-reanonymiser                # simulation
geonature connectors visionature-reanonymiser --yes
geonature connectors visionature-purge --taxon Reptilia       # simulation
geonature connectors visionature-purge --taxon Reptilia --yes
```

⚠️ **`--depuis` ne remonte pas au-delà de dix semaines.** C'est la fenêtre que l'API
Biolovision couvre en différentiel (`api_diff`). Au-delà, les créations et les
suppressions de l'intervalle seraient perdues sans le moindre message : la commande
refuse plutôt que de produire une base incomplète en silence, et renvoie vers `--debut`,
qui moissonne sur la date d'observation sans limite d'ancienneté. Le contrôle a lieu
avant tout appel réseau, faute de quoi une erreur de connexion masquerait le vrai
problème.

| | `--depuis` | `--debut` |
|---|---|---|
| intention | synchroniser | rattraper un historique |
| date cherchée | saisie (`entry_date`) | observation |
| ancienneté | dix semaines au plus | sans limite |
| suppressions | répercutées | non traitées |

L'incrémental traite les **suppressions avant les modifications** : une observation
supprimée puis recréée sous le même identifiant serait sinon retirée après avoir été
réécrite.

**`observations/diff` ne livre pas les observations**, seulement la liste de ce qui a
changé : `id_sighting`, `id_universal`, `modification_type`. Il sert donc à **répercuter
les suppressions**, et à cela seulement.

⚠️ Les deux voies qui permettraient d'en résoudre les identifiants — `api_get`, une
observation à la fois, et `api_list(id_sightings_list=…)`, cent à la fois comme le fait
`_store_update` de `transfer_vn` — peuvent être **refusées par l'API alors même que
`diff` répond**. Mesuré sur faune-occitanie.org : 403 sur les deux, y compris pour un
groupe dont `search` accepte les requêtes.

Les créations et modifications passent donc par `search` avec **`entry_date`**, qui fait
porter la recherche sur la date de **saisie** et non sur celle de l'observation. C'est
plus juste de toute façon : chercher par date d'observation manquerait les relevés
anciens encodés récemment, qui sont précisément ce qu'un moissonnage antérieur n'a pas pu
voir.

Un relevé peut être listé par le différentiel sans être lisible individuellement — l'API
répond alors 403. Le client vendorisé traitant tout 4xx comme irrécupérable, une seule
observation protégée faisait échouer le moissonnage entier. Ces relevés sont désormais
journalisés sous le motif `inaccessible` dans `vn_rejets.csv` et le traitement continue.
C'est une donnée manquante, pas une panne — mais elle est signalée, car ne pas savoir ce
qu'on n'a pas serait pire que l'erreur.

### Restreindre le périmètre

Sans filtre, `visionature-import` moissonne **toute l'étendue de l'instance** : treize départements
sur Faune-Occitanie, la France entière sur Faune-France. Un seul réglage, qui agit des
deux côtés :

```toml
[visionature]
departements = ["09"]
```

`departements` est vérifié sur `place.county` de chaque relevé — le code de département
que porte chaque observation, à côté de `insee` et `municipality`. C'est le filtre qui
**garantit** le périmètre. Les relevés écartés sont journalisés sous le motif
`hors_perimetre`, et un lieu dont le département est indéterminable est écarté aussi :
le laisser passer ferait du filtre une passoire silencieuse.

`departements` sert donc deux fois : il borne le téléchargement côté serveur, et il
vérifie chaque relevé côté client. Une clé `filtre_api` a existé ici jusqu'à la version
0.1.0 ; elle datait d'avant le passage à `observations/search` et n'était plus transmise
nulle part. Elle a été retirée plutôt que documentée comme inactive — un réglage qui
donne à croire qu'on a borné son moissonnage alors qu'on ramène tout est pire que pas de
réglage. Elle reste offerte sur dbChiro et GeoNature, où elle est bien appliquée.

Découvrir les valeurs de l'instance :

```bash
geonature connectors visionature-perimetres
geonature connectors visionature-groupes        # groupes taxonomiques et couverture reproduction
```

Le `short_name` qu'affiche cette commande est le code employé par `Client_API_VN` — sa
configuration le précise : « use the territory short_name, not the territory id ».

⚠️ **Un paramètre inconnu de l'API est ignoré sans erreur** : rien ne distingue un filtre
appliqué d'un filtre inexistant. C'est pourquoi le filtre serveur ne fait jamais foi
seul, ici comme sur les trois autres connecteurs. Si les rejets `hors_perimetre`
dépassent un dixième du volume lu alors qu'un périmètre territorial est posé, le
moissonnage le signale — le filtre a été ignoré et toute l'instance a été téléchargée
avant d'être écartée localement.

### Observateurs : consentement individuel

VisioNature porte un champ `anonymous` sur **chaque observateur**. Le module le respecte
plutôt que d'appliquer un réglage global :

| cas | résultat |
|---|---|
| `anonymous = 0` | nom publié — aucune demande d'anonymat n'a été exprimée |
| `anonymous = 1` | pseudonyme |
| observateur absent du référentiel | pseudonyme — l'ignorance ne vaut pas consentement |

Le pseudonyme est un HMAC-SHA256 stable : les observations d'un même contributeur restent
rapprochables sans qu'il soit identifiable. **La clé est obligatoire et vient de la
configuration** — jamais une valeur par défaut, qui rendrait les pseudonymes recalculables
par un tiers, donc réidentifiables.

Elle est exigée même si aucun observateur ne demande l'anonymat : le module écrit
systématiquement un identifiant pseudonymisé dans `additional_data.observateur`, quel que
soit le sort du nom. Sans elle, `visionature-import` refuse de démarrer.

Le consentement est lu **dans le relevé lui-même** : la forme longue de l'API porte
`anonymous` et `anonymous_in_export` sur chaque observation. C'est la source la plus
sûre — elle vaut au moment de l'observation, et non au moment où l'on consulte un
référentiel.

Le référentiel des observateurs n'est donc plus qu'un repli, pour les réponses qui ne
portent pas ces champs. Il n'est **chargé qu'au premier relevé qui en a besoin**, et le
plus souvent jamais : sur Faune-Occitanie il pèse 246 699 inscrits, soit plusieurs
minutes de téléchargement et autant de noms de personnes en mémoire, qu'il serait absurde
de payer d'avance pour un cas devenu rare.

⚠️ `visionature-reanonymiser`, lui, le charge toujours : c'est sa raison d'être, puisqu'il sert
précisément à rattraper les changements d'avis exprimés après l'import.

#### Générer la clé de pseudonymisation

Sur la machine qui héberge GeoNature :

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

48 octets d'aléa cryptographique, soit environ 64 caractères — largement suffisant pour
une clé HMAC-SHA256. `openssl rand -base64 48` fait aussi bien.

Reporter la valeur dans `connectors_config.toml`, puis redémarrer le service :

```toml
[visionature]
pseudonymisation_secret = "la-chaîne-obtenue"
```

⚠️ **Cette clé ne doit jamais changer, et doit être sauvegardée hors de la machine.**
Les pseudonymes n'en dérivent que d'elle et de l'identifiant d'observateur ; le nom réel
n'est stocké nulle part pour les observateurs anonymisés. La perdre ou la remplacer après
un import a deux conséquences irréversibles :

- `visionature-reanonymiser` ne retrouve plus aucune ligne — il apparie sur le pseudonyme conservé
  dans `additional_data.observateur`, et rien d'autre ;
- le moissonnage suivant produit des pseudonymes différents pour les mêmes observateurs,
  qui cessent donc d'être rapprochables entre eux.

La consigner dans un gestionnaire de mots de passe **au moment où on la génère**, pas
après. Et restreindre le fichier : `chmod 600 connectors_config.toml`, qui porte aussi le
mot de passe Biolovision et le `client_secret`. Il est dans le `.gitignore` du dépôt —
seul `connectors_config.toml.example`, aux valeurs vides, est suivi.

#### Observations masquées : importées, pas écartées

Dans VisioNature, on masque une observation (`hidden`) pour protéger **l'espèce ou le
site** — nid de rapace, station d'orchidée, gîte à chiroptères. Ce n'est pas une donnée
personnelle, et ce n'est pas une mise au rebut : c'est précisément la donnée à enjeu,
celle que l'accès à l'API est censé apporter. L'écarter reviendrait à ne moissonner que
le banal.

Ces observations sont donc importées, avec `id_nomenclature_diffusion_level` positionné à
`NIV_PRECIS 4` — « Aucune ». Le référentiel complet :

| cd | libellé | |
|----|---------|---|
| 0 | Standard | |
| 1 | Commune | |
| 2 | Maille | choix de `gn_vn2synthese` |
| 3 | Département | |
| 4 | Aucune | **défaut de ce module** |
| 5 | Précise | |

« Aucune » est retenu parce que c'est le code que GeoNature emploie pour traduire
`diffusable = false`, et parce qu'il correspond au comportement de VisioNature, où une
observation masquée n'apparaît pas publiquement — même dégradée. `gn_vn2synthese` préfère
« Maille », qui laisse l'observation alimenter les cartes de répartition sans livrer la
localisation précise : c'est défendable, et le réglage `niveau_diffusion_masquees` y donne
accès. Le bon choix dépend de la convention passée avec le producteur. Le fait qu'une observation était masquée
à la source est en outre conservé dans `additional_data.masquee_source`, pour que
l'information survive à une modification manuelle du niveau de diffusion.

Les observations **non** masquées gardent un niveau de diffusion NULL. Depuis la migration
« Do not auto-compute diffusion_level », GeoNature a retiré le DEFAULT de cette colonne et
ne la calcule plus : NULL y signifie « le producteur ne se prononce pas », ce qui est
exact. Y inscrire une valeur serait une affirmation que la source ne fait pas.

Un seul motif écarte réellement une observation : `admin_hidden_type = refused`, le rejet
explicite d'un modérateur. Les motifs `incomplete` et `question` signalent une vérification
en cours, pas un refus — la donnée est importée. Les commentaires réservés aux modérateurs
(`hidden_comment`) ne sortent jamais de l'outil.

Une observation portant `second_hand` — saisie rapportant l'observation d'un tiers — est
importée sans observateur : le nom enregistré est celui du saisisseur, et le porter dans
`observers` attribuerait l'observation à quelqu'un qui ne l'a pas faite.

### Anonymat : le rattrapage a posteriori

Un observateur peut demander l'anonymat après coup, ou le lever. Ce changement porte sur
l'**observateur** et non sur l'observation : il n'entre dans aucune empreinte de contenu,
donc ni le moissonnage incrémental ni le court-circuit sur la date de modification ne le
rattrapent. Les observations déjà en Synthèse resteraient figées sur le consentement en
vigueur au moment de l'import.

`visionature-reanonymiser` réaligne `synthese.observers` sur le référentiel courant, dans les deux
sens. L'appariement se fait sur l'identifiant pseudonymisé conservé dans
`additional_data.observateur` — seule clé disponible, le nom réel n'étant pas stocké pour
les observateurs anonymisés. Les lignes dont l'observateur a disparu du référentiel sont
laissées en l'état et signalées : leur pseudonyme est la seule information dont on
dispose. Seules les lignes dont la valeur change sont réécrites.

À passer périodiquement — le rattrapage n'a pas de déclencheur naturel.

### Moissonner un gros historique

Débit mesuré sur Faune-Occitanie : **5 763 observations écrites en 3 minutes**, soit
environ **32 par seconde** — et c'est une borne basse, ces trois minutes incluant le
chargement du référentiel de 63 616 espèces, qui est un coût fixe.

| volume | durée |
|---|---|
| un mois d'un département | 3 min |
| un an d'un département | ~40 min |
| vingt ans d'un département | ~13 h |
| toute une région, quatorze millions | **~5 jours** |

Jusqu'à quelques millions, un moissonnage d'un seul tenant convient : en cas d'erreur de
correspondance, on purge et on recommence. Au-delà, deux limites deviennent bloquantes —
il n'y a **pas de reprise sur incident**, et le relevé brut n'est pas conservé, donc
corriger une correspondance impose de tout retélécharger.

La parade est de **partitionner par département et par année**, chaque partition se
rejouant en quelques heures :

```bash
for dep in 09 11 12 30 31 32 34 46 48 65 66 81 82; do
  for an in $(seq 2005 2026); do
    geonature connectors visionature-import --departement "$dep" \
      --debut "${an}-01-01" --fin "$((an+1))-01-01" 2>&1 | tee -a moisson.log
  done
done
```

⚠️ **`--debut`, pas `--depuis`.** Les deux bornent une période, mais ne désignent pas le
même geste : `--depuis` *synchronise* — recherche sur la date de **saisie**, suppressions
comprises, dix semaines au plus — quand `--debut` *rattrape un historique* : date
d'**observation**, sans limite d'ancienneté, sans suppressions. Employer `--depuis` pour
partitionner vingt ans butait sur le plafond de dix semaines à chaque itération, et la
boucle entière ne moissonnait rien. Les deux options se refusent désormais mutuellement.

`--departement` prime sur `[visionature] departements` : une même configuration sert
ainsi les treize partitions, sans réécriture entre deux départements. Le recouvrement est
gratuit — l'`ON CONFLICT` ne réécrit que sur changement d'empreinte, et une partition
rejouée ne coûte que son téléchargement.

⚠️ **Le coût réel n'est pas dans le téléchargement mais dans les zonages.** Chaque
observation engendre environ **9 lignes de `cor_area_synthese`** — quatorze millions
d'observations en produisent donc cent vingt-six millions, avec la maintenance d'index
correspondante. Les 32 observations par seconde mesurées l'ont été sur une Synthèse
quasi vide ; le débit se dégrade à mesure qu'elle se remplit.

### Quand l'API répond 401 ou 403

Les deux codes ne disent pas la même chose, et c'est le seul diagnostic vraiment utile :

| code | ce que cela veut dire | ce qu'il faut faire |
|---|---|---|
| **401** | la clé est inconnue, la signature n'a pas pu être vérifiée | vérifier `client_key` et `client_secret` |
| **403** | la clé est valide, mais son **périmètre** ne couvre pas ce que vous demandez | demander une extension à Biolovision |

Pour savoir ce que votre compte peut réellement faire, plutôt que de le deviner :

```bash
geonature connectors visionature-diagnostic
```

La commande sonde les points d'entrée et rapporte, groupe par groupe, ce qui est servi et
ce qui est refusé. C'est cela qu'il faut porter à l'administrateur de l'instance.

⚠️ **Sur un 403, le code du module n'est pas en cause.** Le périmètre d'export d'une clé
Biolovision se décide par groupe taxonomique, indépendamment de l'`access_mode` du portail,
et rien dans la configuration ne le contourne. Neuf hypothèses ont été éliminées une à une
avant d'en arriver là ; elles sont consignées dans
[docs/decisions.md](docs/decisions.md#visionature) pour éviter de refaire l'enquête.

### Accélérer la mise au point : le cache des référentiels

Un moissonnage commence par trois téléchargements — espèces (63 616 sur
Faune-Occitanie), groupes taxonomiques, observateurs (246 699) — soit plusieurs minutes
avant que la première observation ne soit traitée. Pénible quand on règle un import.

```toml
[visionature]
cache_heures = 24        # 0 = désactivé, et c'est le défaut
# cache_dir = "…"        # défaut : ~/.cache/gn_module_connectors
```

```bash
geonature connectors visionature-vider-cache
```

⚠️ **Désactivé par défaut, et à laisser désactivé en production**, pour deux raisons
distinctes :

- le référentiel des observateurs contient des **noms de personnes**. L'activer les écrit
  sur disque — en 0600, mais en clair — alors que tout le dispositif d'anonymisation vise
  précisément à ne pas les conserver. Le module l'avertit explicitement à l'écriture ;
- un référentiel périmé produit des correspondances taxonomiques fausses et des
  consentements obsolètes, **sans que rien ne le signale**.

La clé de cache inclut l'URL de l'instance : passer de Faune-France à Faune-Occitanie ne
sert jamais le référentiel de l'autre. Un fichier illisible, corrompu ou sans horodatage
vaut absence de cache — une optimisation n'a pas le droit de faire échouer ce qu'elle
accélère.

### Le lien « voir la donnée source »

Le bouton de la Synthèse ouvre la fiche de l'observation sur le portail VisioNature
d'origine. Il passe par une **redirection du module** plutôt que d'y pointer directement.

⚠️ GeoNature construit ce lien en insérant systématiquement un séparateur :

```typescript
link.href = url_source + '/' + id_pk_source;   // synthese-list.component.ts:181
```

Une URL de retour en chaîne de requête — celle de Biolovision est
`…/index.php?m_id=54&id=` — devient donc `…&id=/176983543`, que le portail ne sait pas
lire. Aucune valeur d'`url_source` ne peut produire `&id=176983543` à travers ce
constructeur.

Plutôt que de détourner `entity_source_pk_value` pour y loger un fragment d'URL, le
module donne au cœur ce qu'il sait produire — **un chemin terminé par l'identifiant** :

```
url_source              <API_ENDPOINT>/connectors/visionature
entity_source_pk_value  176983543
lien produit            <API_ENDPOINT>/connectors/visionature/176983543
                        → 302 vers …/index.php?m_id=54&id=176983543
```

La colonne garde l'identifiant brut, `entity_source_pk_field` reste exact, et le cœur
n'est pas modifié. La route (`blueprint.voir_dans_visionature`) refuse tout identifiant
qui ne soit pas numérique, plutôt que de concaténer dans une redirection ce qui vient
d'une URL.

### Jeux de données par code projet

VisioNature rattache les observations à des **codes projet**, qui correspondent à des
programmes réels : atlas, suivis, plans d'action. Le module en fait un JDD chacun, comme
`gn_vn2synthese`. Les observations sans code projet vont dans un JDD général par instance.

Les JDD restent créés à la première écriture : un projet dont toutes les observations
sont rejetées ne laisse pas de jeu vide.

### Résolution taxonomique

**L'API Biolovision n'expose aucune correspondance vers TAXREF.** Vérifié :
`/api/species/?id=94` renvoie `{"latin_name": "Anas crecca", …}`, sans `cd_nom`.
L'identifiant d'espèce est purement interne — l'espèce 94 est une Sarcelle d'hiver,
quand le `cd_nom` 94 de TAXREF désigne *Lacerta salamandra*.

Le rapprochement se fait donc sur `latin_name` contre `taxref.lb_nom`, restreint aux
taxons valides (`cd_nom = cd_ref`), et construit **une fois au démarrage**. C'est viable :
sur 300 377 taxons valides, TAXREF compte 299 065 noms distincts, soit 0,4 % d'homonymes.

⚠️ Une homonymie est traitée comme un **échec**, pas comme un choix par défaut : départager
au hasard deux taxons valides produirait une erreur que rien ne signalerait.

### Heure d'observation

`gn_synthese.synthese.date_min` et `date_max` sont des `timestamp`. Le module y écrivait
un `date`, donc **minuit pour tout le monde**. L'heure vient de
`observers[].timing.@timestamp`, avec `@offset` pour l'heure murale locale.

Elle n'est écrite **que si elle a un sens** : chaque bloc de date Biolovision porte un
indicateur `@notime`, que `gn_vn2synthese` ignore — il écrit donc « 00:00:00 » sans
distinguer une observation réellement faite à minuit d'une heure inconnue. Mesuré sur 338
observations réelles : `timing.@notime = 0` dans 95,6 % des cas. `additional_data.heure_connue`
(`oui` / `non`) tranche l'ambiguïté sur les 4,4 % restants.

L'heure est reportée sur le **jour du relevé** : la date d'observation déclarée fait foi,
un `timing` décalé ne doit pas faire glisser `date_min` d'un jour.

### Limites connues

La table de reproduction ne couvre que sept groupes taxonomiques : ceux que couvre le
témoin. Les papillons de nuit, les hyménoptères, les araignées, les poissons et les
mollusques n'en ont aucune règle et restent au défaut. Rien n'est déduit non plus de
`details[].condition`, dont l'énumération (VIEW, FLY, LAID, HAND, AUDIO…) alimenterait
plutôt `METH_OBS`.

`OCC_COMPORTEMENT` n'est déduit de `behaviours[]` que pour « Accouplement » et
« Territorial » : ce sont les seuls dont le `cd_nomenclature` SINP soit déjà vérifié
ailleurs dans le module. « Pond », « Tandem » ou « Émergence » n'ont pas d'équivalent
certain, et un code inventé produirait une valeur fausse mais silencieuse.

`STADE_VIE` et `SEXE` restent au défaut. L'information existe (`details[].age`,
`details[].sex`) et la décision d'agrégation est prise, mais la règle du cas ambigu reste
à écrire : « 1 mâle et 2 femelles » n'a pas de sexe unique et doit rester au défaut.
`gn_vn2synthese` ne les alimente pas davantage — mesuré sur un export de leur production,
« Inconnu » sur 95 982 lignes sur 95 982.

Le périmètre se restreint par **code de département**, pas par géométrie. Pour un
territoire qui ne suit pas les limites administratives — un bassin versant, un parc —
le zonage `VN_COVER` de la LPO reste la bonne réponse, et n'est pas implémenté ici.

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
geonature connectors dbchiro-import --max-resultats 25   # premier essai d'écriture
geonature connectors dbchiro-import
```

⚠ `--max-resultats` ramène les observations **les plus récemment modifiées**, l'API triant
sur `-timestamp_update`. C'est fait pour éprouver une écriture sur une instance de
travail, pas pour prélever un échantillon représentatif.

### Le compte de service décide du périmètre

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

### Les absences

`0obs` (« Aucune chauve-souris ou trace », 171 obs) et `0du` (« Aucun contact
acoustique », 4 obs) ne désignent aucun taxon. C'est le même piège que
`occurrenceStatus = ABSENT` du GBIF. Écartées par défaut ; `importer_absences = true` les
verse en `STATUT_OBS = « Non observé »` sur le `cd_nom` de l'ordre, avec un effectif de
**zéro** et non NULL — l'ambiguïté entre « aucun individu » et « effectif non renseigné »
fausserait toute analyse quantitative.

### Observateurs et gîtes : deux points de convention

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

### Limites connues

Le bouton « voir la donnée source » passe par la redirection
`/connectors/dbchiro/<id>` du module : le permalien dbChiro portant l'identifiant au
milieu du chemin (`/sighting/<id>/detail`), la concaténation du cœur ne pouvait rien
produire de valide.

La suppression n'est pas gérée, comme pour GBIF. Les `countdetails` (sexe, âge, état
sexuel) ne sont pas exposés par `/api/v1/search` : seul `total_count` remonte.

Enfin, une instance protégée par un filtre anti-robot bloquera le connecteur —
`demo.dbchiro.org` l'est. Le cas est détecté et signalé explicitement plutôt que de
finir en erreur de décodage JSON.

---

## GeoNature

Moissonne une **autre instance GeoNature**. C'est la seule source qui parle déjà le même
langage que la destination : mêmes nomenclatures SINP, même TAXREF, mêmes identifiants
permanents. Cela rend le connecteur plus simple sur bien des points — et lui pose deux
problèmes que les autres n'ont pas.

### Configuration

Minimum vital, dans `connectors_config.toml` :

```toml
[geonature]
enabled = true
url = "https://geonature.exemple.fr"
id_export = 12
jeton = "…"
territoires = ["METROP"]
organisme_contact_principal = "Association des Naturalistes de l'Ariège"
```

Le fichier `connectors_config.toml.example` commente chaque réglage.

### Diagnostiquer avant d'importer

```bash
geonature connectors geonature-couverture
```

N'écrit rien, et montre cinq choses qui ne se découvriraient sinon qu'une fois les
données en base :

```
export 12 — licence « Licence Ouverte v2.0 »
  48 210 enregistrement(s) annoncé(s)
  ⚠ le serveur semble avoir ignoré les filtres ['geometry'] : total_filtered égale total
  colonne(s) absente(s) de la vue :
    id_perm_grp_sinp — identifiant de regroupement
  ⚠ TAXREF distant Taxref V17.0 / local Taxref V16.0
  cd_nom : 1 284 distinct(s), 1 279 résolu(s) (99,6 %), 3 par cd_ref, 2 hors TAXREF
      cd_nom 452301 (cd_ref 60295) Rhinolophus ferrumequinum — 84 observation(s)
  id_perm_sinp : 48 210 / 48 210 renseigné(s)
  ⚠ 312 UUID déjà en Synthèse sous une autre source : GBIF (312)
  jeux de données : 7 distinct(s), 2 déjà présent(s) localement
      4d331cae… « Inventaire ZNIEFF de l'Ariège » — actif
  cadres d'acquisition : 3 distinct(s), 0 présent(s) localement
  ⚠ 2 libellé(s) de nomenclature non résolu(s) :
      STATUT_BIO « Reproducteur probable » — 412 observation(s)
```

Ne lancez l'import qu'une fois chaque ligne comprise.

### Importer

```bash
geonature connectors geonature-import --dry-run
geonature connectors geonature-import
geonature connectors geonature-import --perimetre 09 --jeu 4d331cae-65e4-4948-b0b2-a11bc5bb46c2
```

| | |
|---|---|
| `--export` | identifiant d'export distant (défaut : configuration) |
| `--jeu` | restreindre à un ou plusieurs `jdd_uuid` distants, répétable |
| `--depuis` | date ISO 8601 ; sans elle, le filigrane du dernier passage est calculé |
| `--tout` | relire tout le corpus, sans filtre de date — requis avant `geonature-reconcilier` |
| `--perimetre` | code d'un zonage local (`09`) ou WKT en 4326 |
| `--max-resultats` | plafonner la moisson, pour un premier essai d'écriture |

**L'incrémental fait deux passes de date, et ce n'est pas une précaution excessive.**
`date_modification` est le `meta_update_date` de la Synthèse distante, **NULL tant que la
ligne n'a jamais été modifiée**. Filtrer sur ce seul champ manquerait toutes les
*créations* — et définitivement, puisque le passage suivant remonte encore le filigrane
sans jamais revenir les chercher. Le connecteur interroge donc aussi `date_creation` et
fusionne les deux sur `id_synthese`. Un filigrane reculé de `marge_heures` (24 par
défaut) couvre l'écart d'horloge entre les deux instances.

### Répercuter les suppressions

L'API d'export ne publie aucun journal de suppression : une observation retirée là-bas
cesse simplement d'apparaître. On relit donc tout, et ce qui n'est pas revenu a disparu.

```bash
geonature connectors geonature-import --tout
geonature connectors geonature-reconcilier          # simule
geonature connectors geonature-reconcilier --yes    # exécute
```

Commande **séparée de l'import**, et qui simule par défaut : un import écrit, et y loger
une suppression de masse violerait l'asymétrie sur laquelle repose tout le module.

Quatre garde-fous, dont aucun n'est de trop — sans eux, un filtre mal réglé vide la
Synthèse :

1. **la moisson doit être complète.** Un plafond de résultats, un filtre de date ou une
   pagination interrompue rendraient absentes des lignes bien vivantes ;
2. **bornée aux jeux réellement relus.** Un jeu hors du périmètre courant n'est pas vidé
   sous prétexte qu'on ne l'a pas lu ;
3. **bornée au couple instance/export**, par `additional_data`. Deux exports alimentant la
   même source ne peuvent pas se supprimer l'un l'autre ;
4. **un plafond** (`plafond_suppressions`, 5 % du corpus, minimum 100 lignes). Un
   producteur qui republierait sous de nouveaux identifiants ferait sinon tout disparaître
   d'un coup.

### Limites connues

**Les géométries non ponctuelles sont ramenées à leur centroïde.** `core/synthese.py`
n'insère que des points (`ST_MakePoint`). Une placette, une maille ou un polygone de
prospection perd donc sa forme. `nature_objet_geo` et `type_info_geo` du producteur sont
conservés dans `additional_data` pour que la fiche dise de quoi ce point est le centre,
mais l'information géométrique, elle, est perdue.

**`determiner` et `validator` ne vont pas en colonne.** Elles existent en Synthèse mais
pas dans `INSERT_SQL`, et les y ajouter obligerait les trois autres `to_row` à fournir le
paramètre lié — `tests/test_insert_alignement.py` l'impose dans les deux sens. Elles
partent en `additional_data` sous `gn_determinateur` et `gn_validateur`. Les ajouter à
l'INSERT commun est un suivi identifié.

**Deux nomenclatures ne sont pas transposables** faute de figurer dans la vue :
`id_nomenclature_biogeo_status` (`STAT_BIOGEO`, absente de `v_synthese_sinp`) et
`id_nomenclature_valid_status` (la vue publie `validateur`, un nom de personne, pas un
statut — il vient donc de `[validation]`). Les deux prennent le défaut de leur colonne.

**`[geonature.schedule]` n'est pas câblé**, comme `[visionature.schedule]` et
`[dbchiro.schedule]` : `tasks.py` n'ordonnance que GBIF. Planifier l'import passe par
cron.

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

⚠️ **Règle à connaître avant de toucher à `insert_batch`** : l'écriture de l'historique
doit rester dans **la transaction de l'INSERT**. Le filtre « modifiée depuis sa
validation » compare `meta_update_date` à `validation_date`, et il ne survit que parce que
`NOW()` rend l'heure de la *transaction* et non celle du *statement* — les deux colonnes
reçoivent ainsi la même valeur. Sortir l'écriture de la transaction casserait ce filtre
sans que rien ne le signale.

Le détail — pourquoi une seule écriture, et en quoi nous divergeons de `gn_vn2synthese` —
est dans [docs/decisions.md](docs/decisions.md#pré-validation).

### `jdd_validable`

Le module Validation ne liste que les jeux `validable = true`, et c'est le défaut de
GeoNature. `jdd_validable = false` en sort les jeux créés par les connecteurs, à la
création comme aux passages suivants. Un jeu dont les métadonnées ne sont pas rafraîchies
(`rafraichir=False`, cas d'un UUID venu du producteur) garde en revanche son réglage :
il a pu être créé par un autre canal et arbitré à la main.

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

**Le référentiel de sensibilité local peut être moins couvrant que celui d'une
instance distante.** `id_nomenclature_sensitivity` est recalculée à l'insertion par le
trigger de la Synthèse : une observation protégée chez le producteur peut donc se
retrouver **moins protégée ici qu'à la source**. Le réglage
`[geonature] niveau_diffusion_si_sensible` rétablit une restriction dès que le producteur
déclare l'observation sensible, mais c'est un choix d'exploitation, pas un automatisme.

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
| [`docs/decisions.md`](docs/decisions.md) | Le **pourquoi** : ce qui a été mesuré, essayé, écarté, et les résultats négatifs qui évitent de refaire une enquête inutile |
| [`connectors_config.toml.example`](connectors_config.toml.example) | La référence de configuration, commentée réglage par réglage |
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
