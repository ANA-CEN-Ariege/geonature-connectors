# VisioNature

*(↑ retour au [README](../../README.md#visionature))*

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

## Restreindre le périmètre

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

## Observateurs : consentement individuel

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

### Générer la clé de pseudonymisation

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

### Observations masquées : importées, pas écartées

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

## Anonymat : le rattrapage a posteriori

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

## Moissonner un gros historique

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

## Quand l'API répond 401 ou 403

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
[docs/decisions.md](../decisions.md#visionature) pour éviter de refaire l'enquête.

## Accélérer la mise au point : le cache des référentiels

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

## Le lien « voir la donnée source »

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

## Jeux de données par code projet

VisioNature rattache les observations à des **codes projet**, qui correspondent à des
programmes réels : atlas, suivis, plans d'action. Le module en fait un JDD chacun, comme
`gn_vn2synthese`. Les observations sans code projet vont dans un JDD général par instance.

Les JDD restent créés à la première écriture : un projet dont toutes les observations
sont rejetées ne laisse pas de jeu vide.

## Résolution taxonomique

**L'API Biolovision n'expose aucune correspondance vers TAXREF.** Vérifié :
`/api/species/?id=94` renvoie `{"latin_name": "Anas crecca", …}`, sans `cd_nom`.
L'identifiant d'espèce est purement interne — l'espèce 94 est une Sarcelle d'hiver,
quand le `cd_nom` 94 de TAXREF désigne *Lacerta salamandra*.

Le rapprochement se fait donc sur `latin_name` contre `taxref.lb_nom`, restreint aux
taxons valides (`cd_nom = cd_ref`), et construit **une fois au démarrage**. C'est viable :
sur 300 377 taxons valides, TAXREF compte 299 065 noms distincts, soit 0,4 % d'homonymes.

⚠️ Une homonymie est traitée comme un **échec**, pas comme un choix par défaut : départager
au hasard deux taxons valides produirait une erreur que rien ne signalerait.

## Heure d'observation

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

## Limites connues

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
