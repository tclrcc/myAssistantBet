# Spec — le générateur de prompt émet un bloc de données, plus un cadre

Chantier de migration. Le prompt cesse de porter sa propre méthode : le gabarit
Jinja2 se réduit aux faits, et tout ce qui décide de la sortie part dans le
`SKILL.md`, chargé automatiquement côté lecteur.

Le contrat de format vit dans [`references/payload-contrat.md`](references/payload-contrat.md).
Ce fichier-ci dit ce qui bouge dans le code, dans quel ordre, et ce que ça casse.

## §0 — Les mesures qui gouvernent ce chantier

Toutes du 21/08/2026, sur une copie de la base servie (172 prompts archivés).
**Elles se refont** : `sqlite3 copie.db` sur `prompts`, et le relevé de libellés
par expression régulière sur les corps archivés.

| Mesure | Valeur | Ce qu'elle décide |
| --- | --- | --- |
| part du cadre dans le volume archivé | **65,4 %** | ce que la migration libère |
| cadre des 8 derniers prompts | 12 257 à **18 498** tokens | le chiffre du CLAUDE.md (~6 100) était périmé d'un facteur trois |
| coût d'un bloc | **665** tokens | le prompt 171 pesait 18 498 de cadre pour 4 677 de faits, soit 80 % de cadre |
| lignes factuelles du gabarit | **26 sur 1 411** | l'ampleur de la coupe |
| lignes de contexte archivées | 17 538, dont **20 % datées, 8 % sourcées** | l'attribution ne peut pas se poser ligne par ligne |
| libellés de `CONTEXT_ICONS` | **64, tous rattachés** à un module producteur | le repli « non attribuable » est l'exception, pas la règle |
| `fetched_at` | présent sur les **8** tables de contexte | l'attribution se dérive, elle ne se saisit pas |

**La deuxième ligne du tableau est la raison d'être du chantier, et la
cinquième est celle qui a corrigé le contrat.** Appliquer littéralement « un
fait sans ses trois attributs est traité comme absent » aurait supprimé 80 %
du bloc — voir §4.

## §1 — Format : un objet JSON par lot

`build_prompt` cesse de rendre un document Markdown de 1 411 lignes de gabarit
et rend **un objet JSON unique**, précédé de rien et suivi de rien.

- **Un seul objet racine.** Jamais une suite d'objets : le scan de
  `ingestion.read_bodies` avance de `{` en `{` et un objet qui se relit fait
  sauter tout ce qu'il contient. La racine avale donc ses matchs, et c'est ce
  qui protège les objets internes — voir §6.
- **`cotes` est colonnaire** : `{colonnes: [...], lignes: [[...], ...]}`. Un
  tableau dense, un seul format, un seul parseur. La forme objet-par-cote
  répéterait sept clés par ligne pour ~29 cotes par match, soit le poids d'un
  bloc entier en noms de champs.
- **`releve_le` monte au niveau du bloc `cotes`**, pas sur chaque ligne. Il
  décrit le relevé, pas la cote : c'est déjà ce que l'en-tête actuel écrit une
  fois par bloc (`MARCHES (Betclic, relevé 09:56)`). Il **descend par ligne sur
  un relevé hétérogène**, et le cas existe : une saisie manuelle n'a pas
  d'horodatage de marché — son heure est celle de la frappe, et le projet refuse
  déjà de la présenter comme un relevé. Une colonne `releve_le` apparaît alors,
  `null` sur ces lignes-là.
- **Des dates, jamais des âges.** Le payload transporte des horodatages ; la
  fraîcheur se dérive à la lecture. Un âge calculé au rendu est vrai à la seconde
  où il est écrit et faux pour toujours ensuite : un payload archivé dirait
  « il y a 59 h » pour un relevé qui en aura 4 000.
  - **Mesure du 21/08/2026, et elle restreint le chantier** : sur 12 703 faits
    rendus, cinq libellés portent une durée — `Meteo` (174), `Entraineur` (347),
    `Calendrier` (343), `Repos` (243), `Historique` (110). **Quatre la comptent
    depuis le coup d'envoi**, que le payload porte : elles restent vraies et
    vérifiables. Seule `Meteo` compte depuis `now`, et c'est elle qui a produit
    les 14 lignes divergentes de la phase 1.
  - La crainte était générale, le défaut est unique et localisé. C'est la mesure
    qui le dit, pas l'intuition.
  - **Mais « ancré au coup d'envoi » ne veut pas dire « stable », et c'est une
    correction du 21/08/2026.** `statut: "reporte"` existe : **177 événements sur
    1 022 (17,3 %)** ont vu leur horaire bouger, jusqu'à 24 h de décalage. Un
    report rend les 1 043 durées fausses **sans qu'aucune n'en ait l'air**.
  - **Règle de construction, pas de contrat** : le payload expédie l'ancre brute
    — `depuis: "2025-09-08"`, la date du prochain match, celle du dernier match
    connu — et jamais la durée calculée. La durée devient une affaire de rendu,
    et le lecteur la recalcule sur l'ancre qu'il a sous les yeux.
- Encodage UTF-8, `ensure_ascii=False`, clés triées, indentation 1. Les accents
  coûtent moins que leurs échappements.

## §2 — Schéma

### Racine

```
origine              "myassistantbet"      — discriminant, voir §6
framework_version    "1.1"
genere_le            ISO 8601 avec fuseau  — contrôle d'antériorité
sports[]             les sports du lot
nb_matchs
bookmaker_principal
bookmaker_reference
sections_attendues[]
collecte             {densite: {attendus, obtenus}, producteurs_muets[]}
matchs[]
```

**`framework_version` est le champ dont l'absence ne se voit qu'a posteriori.**
Les ancrages de confiance viennent de changer ; sans ce champ, une analyse
archivée ne se relit plus contre les règles en vigueur au moment ou elle a ete
produite, et la base de calibration devient inhomogene **sans que rien ne le
signale**. Le projet a deja paye cette forme exacte : `sessions.scale_version`
existe pour la meme raison, et son commentaire dit qu'une echelle ne se
reconstitue pas apres coup.

- Il se fige au **premier prompt** d'une session, comme `scale_version`, et par
  le meme `COALESCE` : changer de regles en cours de session ne doit pas
  reetiqueter ce qui a deja ete rendu sous les anciennes.
- Il **ne se retro-remplit pas**. Les 172 prompts archives n'en portent aucun, et
  leur en attribuer un serait affirmer sous quel barème ils ont ete produits. La
  mesure commence a la mise en service.

`sports` porte une liste : les lots mixtes existent et se rendent aujourd'hui.

### Par match

```
origine            "myassistantbet"
id                 "M1"                — le repère de bloc, stable dans un rendu
competition
tour
debut_local        ISO 8601 avec fuseau
debut_paris        ISO 8601
lieu
statut             "programme" | "reporte" | "incertain"
domicile / exterieur  {nom, classement, forme_5, entraineur}
compositions       {contenu, publiee_le, source, niveau} | null
absences[]         {joueur, motif, confirme_le, source, niveau}
h2h                {resume, source, date, niveau}
meteo              {contenu, source, date, niveau} | null
calendrier         {match_precedent, prochain_match}
attributs[]        {cle, valeur, source, date, niveau}
cotes              {colonnes, lignes, releve_le}
marches_absents[]  ou clé absente — voir §5
questions_ouvertes[] ou clé absente — voir §5 et §6
collecte           {densite: {attendus, obtenus}, cause} | null
```

**`collecte` n'est pas un fait sur le match**, c'est une mesure de ce que notre
collecte a rapporté : `Densite` en sort donc du conteneur `attributs[]`. La
confondre avec un fait ferait lire « 0 sur 25 » comme une propriété de la
rencontre, ce que la ligne existe précisément pour démentir.

**Il existe aux deux niveaux, et il faut les deux.** Le contrat le pose sur le
lot ; il descend aussi par match, et ce n'est pas une redondance :

- au **lot**, il dit ce que la collecte a rapporté dans l'ensemble, et quels
  producteurs sont restés muets — un `tennis_history` silencieux se voit une
  fois, pas vingt ;
- au **match**, il porte la **cause** typée, et c'est elle qui décide du
  comportement. `competition non rattachée` ne vaut aucun budget de recherche —
  ça se répare d'un geste hors analyse — quand `non interrogés` fait du bloc le
  meilleur dossier du lot, la recherche y étant le seul chemin. Une densité de
  lot ne peut pas porter cette distinction, et c'est exactement celle que la
  section A doit faire.

`research.py` lit déjà la densité par match pour classer les dossiers : la
retirer du payload ne casserait rien côté application — la fiche se calcule en
amont — mais priverait le lecteur du seul élément qui distingue une absence de
fait d'une absence de collecte.

### `attributs[]` — le conteneur générique

Tout libellé hors du socle nommé y entre, attribué. Aujourd'hui ~40 des 64
libellés : tout le tennis (`Elo`, `Repos`, `Parcours`, `Profil`, `Marge`,
`Usure`, `Service`, `Ici`…) et les statistiques de match du football (`xG`,
`Corners`, `Cartons`, `Possession`, `Tirs`…).

**Règle de promotion, et elle est stricte** : un libellé monte dans le socle
nommé le jour où il est référencé par une règle de décision, ou sert d'axe de
calibration. Pas avant. Sans elle, le socle absorbe le conteneur en six mois et
chaque libellé ajouté demande une modification de schéma — le piège de
`markets.py` et `render.py`, où un marché ajouté d'un seul côté sortait en clé
brute.

## §3 — Où l'attribution se pose

**Par tranche d'assemblage, jamais par une table `label → source` posée à
côté.** `session.context_block` est le seul assembleur, et il appelle les
producteurs un par un : chaque `lines += module.lines(...)` sait de quelle
source vient sa tranche. Un libellé ajouté demain dans `tennis_history` hérite
donc de l'attribution de sa tranche, sans que personne ait à y penser.

Une table parallèle diviserait au premier libellé ajouté. C'est le défaut
documenté du projet, payé trois fois.

| Tranche | Source émise | Niveau |
| --- | --- | --- |
| `context.context_lines` | API-Football | 3 |
| `dossier.*` | API-Football | 3 |
| `elo.lines` | Tennis Abstract | 3 |
| `tennis_history.lines` | tennis-data.co.uk | 3 |
| `serve_stats.*` | tennis-api.com | 3 |
| `weather.lines` — chiffres | Open-Meteo | 3 |
| `weather.lines` — alerte | l'émetteur recopié (« NWS Wilmington OH ») | **1** |
| `tennis_load.*`, `tennis_round.lines` | nos propres scans | 3 |

**Le niveau plafonne à 3, et ce n'est pas un réglage timide.** L'échelle classe
par éditeur : aucun de nos fournisseurs n'est l'instance qui publie. La seule
exception est l'alerte officielle, dont l'émetteur *est* le service national —
et le projet recopie déjà cet émetteur de la charge utile plutôt que de le
deviner. Ce plafond décrit le régime actuel : lire un bloc vaut `lecture`, et
c'est la recherche qui monte la confiance.

**Nos propres scans sortent en 3 avec une source qui les nomme.** Ils n'ont pas
d'éditeur — ce sont des dérivés de nos relevés — mais leur donner un niveau à
part inventerait une case dans une échelle qui n'en a que quatre.

### La date

Elle vient de `fetched_at`, au grain que la source permet :

- `context` porte un `fetched_at` **par kind** — 12 kinds sont lus par
  `context_lines`, pour 29 lignes produites. Le grain fin est donc atteignable
  en annotant chaque `append` de son kind ;
- `dossier.load` rend déjà sa date, `team_context`, `league_context`,
  `player_context`, `tennis_elo`, `tennis_matches` et `tennis_history_state`
  portent toutes la colonne.

**`context.load()` est la seule pièce manquante** : il fait
`SELECT kind, payload_json` et jette `fetched_at`, présent en base sur les
3 278 relevés. Il rend désormais la date avec la charge utile.

## §4 — Correction du contrat sur l'attribution

Le contrat écrivait : *« Un fait sans ces trois attributs est traité comme
absent, quelle que soit sa plausibilité. »* La mesure l'invalide — 20 % des
lignes datées, 8 % sourcées — et la règle telle qu'écrite viderait le bloc.

Elle est remplacée par :

> **L'attribution plafonne ce qu'un fait peut justifier, elle ne filtre pas
> l'entrée.** Un fait non attribuable est émis avec `niveau: 4` et
> `date: null`, jamais supprimé. Un fait de niveau 4 ne peut pas porter seul
> une confiance ≥ 3.

Trois raisons, et la troisième est la plus forte :

- **filtrer à l'entrée détruit de l'information qui existe.** Le fait est
  collecté, il est en base, et le jeter parce que notre chaîne ne sait pas le
  dater serait punir le lecteur d'un défaut de notre code ;
- **le plafond obtient le même résultat là où il compte.** Ce que la règle
  d'origine voulait empêcher, c'est qu'un fait mal établi porte une sélection.
  Le plafond l'empêche, et il le dit au lieu de le taire ;
- **une suppression est silencieuse, un plafond est lisible.** Un fait absent
  et un fait jamais collecté rendent exactement la même chose — c'est le défaut
  caractéristique de ce projet, rencontré six fois. Un `niveau: 4` se voit.

**Un troisième état, ajouté au contrat v1.2 : sourcé mais non daté.** `niveau`
réel, `date: null`. Il ne se dégrade pas en niveau 4, mais il ne peut porter
aucun argument dont la force vient de la récence — forme, confirmation d'absence,
changement d'entraîneur, congestion de calendrier. Ces arguments tirent leur
valeur de ce que quelque chose a changé récemment ; sans date, la récence est
invérifiable. Un fait non daté reste bon pour un argument **structurel** — format
de compétition, historique long, profil de terrain — où la date ne change rien.

**Et une date manquante ne se remplace jamais par un repli.** Une date fausse est
pire qu'une date absente : elle a l'apparence d'un fait. La règle a son test, et
il a fallu une mutation pour s'apercevoir qu'il manquait — voir §8 ter.

La règle de décision (« niveau 4 ne porte pas seul une confiance ≥ 3 ») vit
dans le `SKILL.md`. Le payload l'énonce comme conséquence, il ne l'applique pas.

## §5 — Vide et absent se distinguent à l'écriture

Pour `marches_absents` et `questions_ouvertes` :

- **liste vide** — vérifié, rien à signaler ;
- **clé absente** — non instrumenté : la question n'a pas été posée.

Ce ne sont pas la même chose, et l'écriture doit pouvoir produire les deux.
Concrètement, la valeur remonte en `list | None` depuis le service, jamais en
`list` avec un défaut à `[]` — un défaut mutable écraserait la distinction au
premier appelant.

Le projet connaît déjà cette discipline **en lecture** : `confidence.OPEN_STATES`
porte quatre états dont `vide` et `absente`, ajoutés parce que les confondre
mélangeait une réponse du modèle et une panne de transmission dans le même
taux. La même distinction manquait en écriture.

Un test monte les deux cas et vérifie que le JSON les rend différemment. Un
test qui ne vérifierait que le cas peuplé laisserait passer un `[]` par défaut.

## §6 — La collision `dossiers_ouverts`, évitée par construction

`dossiers_ouverts` reste **exclusivement le canal retour** : c'est lui qui porte
l'historique archivé et le rattachement des crans. Le payload émet
`questions_ouvertes[]`.

Le renommage ne suffit pas. Le lecteur d'import reconnaît un bloc de confiance
**sur sa seule forme** quand la clôture manque :

```python
CLAIM_KEYS = ("faits", "facts", "source_level", "confiance", "confidence")
def is_claim(payload): return any(k in payload for k in CLAIM_KEYS) and not ("jambes" in payload or "legs" in payload)
```

`read_bodies` scanne tout objet `{...}` équilibré du collage, et `BLOCK` accepte
la clôture ```` ```json ````. **Un objet-match qui porterait `faits` — le nom
d'origine du conteneur générique — serait lu comme un bloc de confiance dès
qu'il est recopié dans une
réponse : échec au `parse()`, divergence entre le compte des blocs et celui des
lignes, perte des crans du lot.** C'est la classe `EXPLORATORY_HEAD` : panne
silencieuse, détection tardive.

**Deux défenses, et la seconde ne coûte rien.**

- **Le discriminant, qui est la défense principale** : la racine et chaque match
  portent `"origine": "myassistantbet"`, et `is_claim` l'ajoute à sa liste
  d'exclusion, symétriquement à `jambes`/`legs` pour les combinés. Un objet qui
  déclare son émetteur ne peut pas être une réclamation du modèle.
- **Le conteneur générique s'appelle `attributs`, pas `faits`.** Le discriminant
  suffirait. Mais laisser une clé en collision frontale avec `CLAIM_KEYS` sur un
  chemin de panne silencieuse ne vaut pas l'économie d'un renommage, et la
  défense en profondeur se justifie ici par la **classe** du défaut plutôt que
  par sa probabilité : une divergence entre le compte des blocs et celui des
  lignes ne lève rien, ne casse aucun test, et coûte les crans du lot entier.

Chemin par chemin, après correction :

- **clôturé ```` ```json ````** — `is_claim` faux : le bloc est ignoré
  proprement, sans rejet compté ;
- **non clôturé** — `is_claim` faux : l'objet n'est pas retenu, et la racine
  ayant été lue en premier, ses matchs sont déjà sautés.

Un test colle un payload complet dans un collage réel et vérifie que
`read_blocks` ne compte **ni bloc, ni rejet**. Zéro des deux : un rejet compté
serait déjà la panne.

## §7 — Ce que la coupe casse, et ce qui le rattrape

Cinq lecteurs lisent le corps du prompt. Ils ne cassent pas tous.

| Lecteur | Ce qu'il cherche | Après la coupe |
| --- | --- | --- |
| `sections.survey` | `###c-bis`, `sets:`, `dossiers_ouverts`, ```` ```conf ````, ```` ```combo ```` dans `prompts.body` | **casse en silence** — déclare toutes les sections non demandées |
| `history` (lot d'une session) | `^### M\d+ · (.+)$` | **casse** — reconstruction des lots anciens |
| `prompt.split_cost` | `^### M\d+ ` et `^## SORTIE ATTENDUE` | **casse** — plus de découpage cadre/blocs |
| `confidence` (appariement) | en-têtes `### M8 · sport · …`, somme de contrôle sur `match` | **casse** — les crans ne se rattachent plus |
| `read_research_budget` | `\*\*ce prompt\*\* en ouvre (\d+)` | **casse** — le budget n'est plus en prose |

**Le premier est le plus grave, parce qu'il ne lève rien.** `survey()` déduit ce
qui était demandé en cherchant les motifs du gabarit ; sans gabarit, il conclut
« rien n'était demandé » — donc « rien à réclamer ». C'est exactement le défaut
que ce module existe pour corriger, retourné contre lui.

Deux règles pour tous les cinq :

- **la source de vérité de « ce qui était demandé » se déplace du corps vers une
  déclaration du payload** (`sections_attendues[]`). Une liste de clés n'est pas
  une description de méthode : elle dit ce que la session réclame, pas comment
  le produire ;
- **les prompts archivés se lisent comme avant.** La bascule est datée, et le
  lecteur choisit sur la forme du corps — un corps qui commence par `{` est un
  payload, tout le reste est un prompt d'avant. Aucune migration, aucun
  retro-remplissage : les 172 corps archivés restent lisibles par le chemin qui
  les a produits.

Les en-têtes `### M\d+ ·` doivent survivre **en tant que clé de rattachement**.
Le payload porte `repere` et `affiche` par match : `history` et `confidence` s'y
branchent au lieu de la ligne Markdown, et la somme de contrôle sur le libellé
de l'affiche continue de tenir.

## §7 bis — L'alarme de budget voit un lot réel

**Les deux plafonds existants n'ont rien vu passer.** `PROMPT_BUDGET` et
`MIXED_BUDGET` vivent dans `tests/`, s'appliquent à des fixtures de six et trois
matchs, et **rien ne les lit à l'exécution** : `token_estimate` est calculé,
archivé, affiché, jamais opposé à quoi que ce soit. C'est ce qui a laissé le
cadre passer de 8 048 à 15 232 tokens en dix jours sans qu'une seule alarme se
déclenche — la dérive était intégralement archivée, et personne ne la regardait.

L'alarme se pose donc **sur le prompt réellement produit**, à `save_prompt`, où
`split_cost` tourne déjà et où `fixed_tokens` est déjà persisté. Le coût est nul.

- **Elle porte sur le cadre, pas sur le total.** Un lot de vingt-et-un blocs pèse
  légitimement 21 707 tokens ; ce qui doit alerter est ce qui se paie **une fois
  par prompt quel que soit le lot**. Une alarme sur le total se déclencherait sur
  la taille du lot, c'est-à-dire sur ce qui n'est pas un défaut.
- **C'est une alarme, pas un refus.** Un prompt long ne gêne pas l'utilisateur, et
  refuser de servir une page pour un dépassement serait hors de proportion — même
  arbitrage qu'un seuil illisible qui revient au défaut. Elle se rend dans l'UI et
  dans le journal.
- **Le seuil est un réglage** (`seuil_cadre_max`), pas une constante : « à partir
  de quel cadre je veux être prévenu » est une décision de l'utilisateur, au même
  titre que les bandes de cote.
- **Elle garde son sens après la migration.** Le cadre d'un payload se réduit à
  son en-tête de lot ; l'alarme attrape alors toute reprise de cadre — une clé
  ajoutée à la racine qui grossirait, un retour de prose dans le corps.

**Elle se livre en phase 2, avant la coupe et non après** : c'est elle qui
mesurera ce que la coupe fait gagner, et une alarme posée après n'aurait aucun
point de comparaison sur un lot réel.

**Sa ligne est coupée à l'écran jusqu'à la coupe du gabarit**
(`FRAME_ALERT_MUTED`). À 20 dépassements sur 20, elle paraîtrait à chaque
génération et deviendrait du décor — le défaut qu'elle existe pour corriger. Ce
qui se coupe est l'**affichage** et rien d'autre : `fixed_tokens` s'écrit,
`frame_history` compte, le journal avertit.

- **Le seuil ne bouge pas.** Le déplacer pour faire taire l'alarme fabriquerait
  un « avant » incomparable avec l'« après » : le nombre suivrait le confort au
  lieu de suivre la réalité.
- **Une constante et non un réglage**, même forme que `FEEDBACK_SUSPENDED` — et
  ce précédent est justement la preuve qu'un commentaire ne tient pas une
  décision : il porte depuis des mois une note disant que sa bascule ne se
  produira pas toute seule, et il est toujours levé.
- **Le rallumage ne dépend donc d'aucune mémoire.** `tests/test_sentinelles.py`
  devient rouge dès que `ACTIVE_PRODUCER` bascule sur le payload sans que
  `FRAME_ALERT_MUTED` soit retombé. La condition est **structurelle** : la raison
  de se taire — l'alarme mord partout — disparaît avec le gabarit.
- **Le test qui gardait « une alarme qui ne mord pas ne dit rien » a dû être
  repris** : avec la coupure, sa ligne était vide pour deux raisons, et il
  passait pour la mauvaise. Il construit désormais son objet sans la coupure —
  un test qui change de cause en gardant son résultat est un test mort qui en a
  l'air vivant.

## §7 ter — La taxonomie des causes ne décrivait pas le régime réel

Mesure du 21/08/2026 sur les 378 tentatives journalisées : `served` **340**,
`unresolved` **35**, `unmapped` **3** — et **zéro** `not_covered`, **zéro**
`unreachable`. Deux causes déclarées ne se produisent jamais ; la seule qui se
produise vraiment était réduite à un mot.

**Ce sont les 35 `unresolved` (9 %) qui méritaient d'être typés**, parce qu'ils
recouvrent trois situations qui n'appellent pas la même décision de budget :

| Forme | Ce qui s'est passé | Ce que ça décide |
| --- | --- | --- |
| `unresolved_team` | une équipe n'a pas été appariée | un alias, et il débloque tous ses matchs à venir |
| `unresolved_fixture` | les deux équipes reconnues, aucune rencontre ce jour-là | vérifier la date — report non répercuté |
| `unresolved_empty` | le fournisseur ne sert rien ce jour-là | rien à apparier, se retente plus tard |

- **Les trois se distinguent par construction, pas par heuristique** : ce sont
  trois points de sortie de `resolve_fixture`, chacun sur un fait connu à cet
  instant. Rien n'est deviné, et rien ne se reconstitue après coup.
- **Le flux d'écriture ne bouge pas.** Un premier jet sortait avant
  `_record_pending` sur un fournisseur muet : l'événement quittait `/mapping` et
  `failure_causes` ne le voyait plus — le bloc perdait la cause qu'on venait de
  préciser. Seule la cause se raffine.
- **`fournisseur expiré` n'est pas construit, et c'est une mesure qui le dit.**
  La notion aurait demandé de comparer `fetched_at` à un TTL ; or les **75**
  relevés de contexte des matchs à venir ont **tous moins de 24 h**. Le critère
  ne se déclencherait jamais — « pire qu'absent, il donne l'apparence d'un filtre
  actif ». Réserve : 75 relevés, et un lot monté sur des matchs enrichis trois
  jours plus tôt changerait le tableau.

## §8 — Ordre des phases

Une phase, un commit, `ruff` et `pytest` verts, puis validation.

1. **Attribution en base.** `context.load()` rend `fetched_at`. Les producteurs
   annotent leurs lignes de leur kind. Aucun changement de sortie : le rendu
   texte actuel est reconstruit à l'identique depuis les faits attribués.
   **Livrée le 21/08/2026** — voir §8 bis pour la vérification.
2. **L'alarme de budget d'abord** (§7 bis), et elle doit enregistrer deux ou
   trois lots réels **avant** la coupe. Le « avant » des archives ne suffit pas :
   sans observation par l'alarme elle-même, une alarme muette après la migration
   se lira « le cadre a fondu » aussi bien que « elle n'a jamais mordu ».
   **Livrée le 21/08/2026** — et elle mord : 20 prompts sur 20 dépassent le
   seuil de 10 000 tokens sur la fenêtre courante, 49 sur 50, contre 67 sur 172
   sur tout l'historique. Ce contraste est la mesure du changement de régime.
3. **Le payload, à côté du prompt.** `build_payload()` rend l'objet JSON. Le
   gabarit ne bouge pas encore. Les deux coexistent, ce qui rend la comparaison
   possible sur un lot réel.
4. **Le discriminant.** `is_claim` exclut `origine`, test de non-régression sur
   un collage complet.
5. **Les lecteurs.** `sections`, `history`, `confidence`, `split_cost`,
   `read_research_budget` apprennent la forme payload, sans perdre l'ancienne.
6. **La coupe.** Le gabarit se réduit aux faits ; les phrases méthodologiques
   partent dans le `SKILL.md`. Les tests qui les gardaient migrent avec elles —
   ils gardent une décision, et la décision n'a pas disparu, elle a déménagé.

**La dernière phase l'est vraiment, et l'ordre n'est pas négociable** : couper le
gabarit avant que les lecteurs sachent lire le payload produit exactement la
panne silencieuse du §7.

**Et le gabarit ne se supprime pas pour autant.** L'identité octet pour octet
prouve que le refactor est un no-op ; elle ne prouve **rien** sur ce que la
migration vaut. Le gabarit reste le repli jusqu'à ce que le protocole ait
tranché — voir [`PROTOCOLE-COMPARAISON.md`](PROTOCOLE-COMPARAISON.md), écrit
avant le premier résultat, par construction.

**« Payload + SKILL vaut gabarit » est un mauvais énoncé, et le test qui en
sortirait échouerait pour la mauvaise raison.** Il suppose une équivalence de
sortie, alors que la Skill v1.2 diffère **volontairement** : C-bis ouvert, cinq
crans ancrés, tableau principal fermé aux sélections non vérifiées. **Une sortie
identique au gabarit prouverait que la Skill ne s'applique pas.**

Ce qui se teste est donc **asymétrique** : non-régression sur ce que le gabarit
faisait bien, amélioration sur ce qu'il cassait — confiance 3 par défaut, PASSE
à zéro, doublons — et réduction du cadre. Dix critères, dont deux barrières
dures : traçabilité des faits, et aucun match disparu.

## §8 bis — Comment se vérifie une phase à sortie inchangée

**Les tests verts ne prouvent pas l'identité.** 2 449 tests passaient déjà avant
la phase 1 ; ils vérifient des propriétés, pas que les 17 538 lignes du bloc
sortent au même octet. La vérification se fait donc **contre le code d'avant, sur
la base servie**, et elle se refait :

```
git archive HEAD src | tar -x -C <tmp>/avant     # le code d'avant
cp data/myassistantbet.db <tmp>/mesure.db        # jamais la base servie
DB=<tmp>/mesure.db SRC=<tmp>/avant/src  uv run python rendu.py > avant.txt
DB=<tmp>/mesure.db SRC=./src            uv run python rendu.py > apres.txt
diff avant.txt apres.txt
```

où `rendu.py` appelle `session.context_block` sur **tous** les événements de la
base et écrit `label<TAB>valeur` par ligne.

**Résultat du 21/08/2026 : 1 022 événements, 16 749 lignes, 1,3 Mo — identiques
octet pour octet**, à l'exception de 14 lignes `Meteo` dont l'âge se compte
depuis maintenant (`relevé il y a 59 h` contre `58 h`) : les deux exécutions
sont séparées d'une minute. Neutraliser cet âge donne deux empreintes égales.

- **Les 14 lignes se vérifient une par une, jamais en bloc.** « 14 différences,
  toutes bénignes » est une phrase qu'on écrit avant d'avoir regardé ; le
  décompte par motif (`grep -c`) est ce qui la rend vraie.
- **Le garde-fou permanent n'est pas une empreinte.** Un test qui figerait le
  md5 casserait à chaque changement légitime et se ferait recopier — il
  décrirait au lieu de contraindre. `tests/test_attribution.py` énonce les
  propriétés à la place : le rendu texte est exactement la projection des faits,
  et **aucun fait du bloc ne sort sans source**.
- **Ce dernier test a été vérifié par mutation** : une tranche ajoutée à
  l'assembleur sans passer par `attribue` le fait tomber, et lui seul. Sans
  cette vérification, il aurait pu passer pour la mauvaise raison — le défaut
  que ce projet a déjà payé.

## §8 ter — Le jeu de mutations, et ce qu'il a révélé

**Un test vert ne prouve rien tant qu'on n'a pas vu ce qui le fait rougir.** Le
garde-fou « aucun fait sans source » attrape la tranche oubliée ; il ne dit rien
d'une tranche attribuée **de travers**, qui sort avec sa source, sa date et son
niveau — donc parfaitement crédible. Quatre mutations couvrent les quatre façons
dont l'attribution peut mentir en restant verte :

| Mutation | Ce qu'elle simule | Attrapée par |
| --- | --- | --- |
| tranche non attribuée | un producteur ajouté sans `attribue` | `aucun_fait_sans_source` |
| **mauvais kind** | `Classement` daté du relevé de forme | `chaque_ligne_est_datee_du_type_qui_l_a_produite` |
| **date = `now()`** | le fait paraît frais pour toujours | 4 tests, dont le dédié |
| **niveau poussé à 1** | une statistique tierce passe pour l'instance | `un_fournisseur_qui_n_est_pas_l_instance…` |

- **Le test du mauvais kind a dû être renforcé.** Sa première version vérifiait
  que la date figurait parmi les relevés de l'événement — un kind faux mais
  **présent** y passait. Il énonce désormais la correspondance libellé → type
  indépendamment du code, sur trois types seedés à trois dates distinctes. C'est
  la seconde écriture que le code s'interdit ; dans un test, sa divergence est
  précisément ce qu'on veut voir échouer.
- **Une cinquième mutation a révélé un trou** : poser l'horloge **en repli**,
  quand aucun relevé n'est connu, n'était attrapé par personne — alors que c'est
  le cas que le contrat interdit nommément. Le cas ne se provoque pas par la
  base (`fetched_at` est `NOT NULL`), donc il se teste là où la règle vit.
- **La mutation est ce qui rend l'identité octet pour octet crédible.** Sans
  elle, 2 449 verts ne prouvaient pas qu'un seul test regardait la bonne chose.

## §9 — Ce que cette spec ne fait pas

- **Elle ne réécrit pas les 70 libellés.** L'attribution se dérive ; aucun
  fragment de texte n'est retouché pour y coller une date.
- **Elle ne touche pas au canal retour.** `dossiers_ouverts`, les blocs `conf`,
  les combinés, l'import des picks : rien ne bouge. Le chantier porte sur ce qui
  part, pas sur ce qui revient.
- **Elle ne promeut aucun libellé dans le socle nommé.** La règle de promotion
  du §2 s'applique à partir de maintenant, elle ne se rattrape pas sur
  l'existant.
- **Elle ne mesure pas ce que la coupe fera gagner en qualité d'analyse.** Elle
  mesure ce qu'elle libère en tokens. Que le `SKILL.md` chargé automatiquement
  vaille mieux qu'un cadre recollé à chaque session est l'hypothèse du
  chantier — elle se vérifiera sur les sessions qui suivent, pas ici.
