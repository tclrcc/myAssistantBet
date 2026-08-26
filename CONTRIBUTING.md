# Contribuer — les regles qui ne se devinent pas

`SPEC.md` est la source de verite, `CLAUDE.md` le rappel operationnel. Ce fichier
ne porte que ce qui se decide **avant** d'ecrire du code, et qui a deja coute
quelque chose une fois.

## Tout nouveau format structure entre au banc de transport

**C'est la regle la plus chere du depot, et elle a ete apprise trois fois.**

Le parcours de l'application est : un prompt part, un humain le colle dans une
interface de conversation, puis recolle la reponse dans le formulaire d'import.
**Ce dernier collage abime le markdown** — clotures supprimees ou deplacees,
barres verticales consommees par le rendu, tabulations, guillemets
typographiques, lignes rejointes, espaces insecables.

Le module d'import le savait depuis longtemps pour les **tableaux** :
`picks_import._cells` lit les barres verticales *et* les tabulations, avec ce
commentaire — « ce que l'on copie depuis son interface est un tableau tabule, les
barres ayant ete consommees par le rendu ». Le gabarit d'analyse en avait tire la
lecon de son cote : il exige que `dossiers_ouverts` soit ecrit « a cote des blocs
et **hors de tout bloc de code** », et cette ligne-la n'a jamais pose de probleme.

Les blocs `conf` et `combo` ont malgre tout ete introduits dans des clotures.
Mesure du 17/08/2026 : `picks.claim_raw_json` NULL sur **235 selections sur
235**, dont 86 sur les trois sessions ou le gabarit demandait pourtant un bloc
par ligne. La lecture ne trouvait rien et ne levait rien ; le texte colle
n'ayant ete conserve nulle part, le rattrapage etait impossible.

**Donc, avant de mettre un format structure en service :**

1. **Preferer une ligne a plat a un bloc cloture.** Une ligne du genre
   `sets: M3=2-0/2-1 | M4=PASSE` n'a pas de cloture a perdre. Un JSON imbrique
   la justifie encore — une mise a plat couterait plus qu'elle ne rapporterait —
   mais c'est un arbitrage, pas un defaut.
2. **L'ajouter a `tests/test_transport.py`.** Le banc applique onze alterations
   connues a chaque format, avec **un seul resultat acceptable** : soit une
   lecture correcte, soit une ligne dans `ingestion_rejects`. Jamais un silence.
   Un format ajoute a `FORMATS` sans son entree dans `ATTENDU` fait tomber le
   banc, ce qui est le comportement voulu.
3. **Prevoir une detection de repli sur la forme.** A defaut de cloture
   exploitable, `conf` se reconnait a ses cles `match` / `faits` et `combo` a son
   tableau `jambes`. `type` ne peut pas trancher : les deux familles le portent.
4. **Compter separement ce que le filet rattrape.** Le lecteur tolerant de prose
   des scores en sets reste en place derriere la ligne structuree, et son
   compteur est affiche a l'import. S'il sert encore dans un mois, c'est que la
   consigne n'est pas suivie — et il vaut mieux le savoir que de le deviner.

## Tout chemin d'ecriture se declare au registre

**Cette section-ci ne tient plus lieu de garde-fou, et c'est le progres.** La
regle precedente disait que le controle « prouve que les chemins declares
journalisent, jamais qu'ils sont tous declares », et confiait le reste a la
bonne volonte. Mesure : `myassistantbet-replay` a ete ecrit le meme jour et par
la meme main que cette phrase, et il a laisse tomber ses echecs d'ecriture sans
les journaliser. Une regle de contribution ne se declenche pas.

Une fonction qui insere dans `picks`, `combos`, `combo_legs` ou `set_scores`
porte donc `@writes(...)` (`services/write_paths.py`), avec les familles de
blocs dont elle repond. `tests/test_write_paths.py` **lit la source** et fait
echouer la suite si une fonction insere sans etre declaree — le critere est un
`INSERT INTO` vers une table gardee, donc il ne depend d'aucun nommage.

Consequence a connaitre : declarer une famille sans donner a
`selfcheck-ingestion` un exemplaire malforme du format correspondant fait
tomber le controle. C'est voulu, et c'est ce qui a fait passer le compte de 8 a
10 — la famille `exploratoire` etait declaree nulle part et verifiee nulle part,
sans que « 8 sur 8 » puisse le dire.

**Et le decorateur se pose sur la fonction, pas a cote.** Mesure du 19/08/2026 :
un `@dataclass` glisse entre `@writes(...)` et `add_pick` a transfere la
declaration sur la classe. Le test a bien mordu — c'est par lui que le defaut a
ete trouve — mais `selfcheck-ingestion` affichait `10 sur 10` pendant ce temps,
parce que son denominateur vient de `declared_block_types()`, **un agregat de
familles** : la classe portait les memes trois familles, donc l'agregat ne
bougeait pas d'un mot.

D'ou trois vues **independantes**, ecrites une fois dans `write_paths` et lues
par le test comme par le banc : `inserting_functions` (les corps — qui ecrit),
`decorated_nodes` (les decorateurs — qui declare, et sur quel genre d'objet) et
`REGISTRY` (quelle declaration a reellement tourne). Un controle dont le
denominateur vient de ce qu'il controle ne peut pas voir un deplacement a
l'interieur.

Corollaire pour tout garde-fou de ce depot : **il se teste contre sa propre
panne**. `tests/test_write_paths.py` injecte le decorateur manquant dans les deux
positions et verifie que le desaccord ressort — et verifie aussi les deux
positions **saines**, sans quoi un controle qui crie sur tout passerait pour un
controle qui marche.

## Toute lecture d'un collage passe par `imports_raw`

Le texte recu est conserve **avant** toute tentative de lecture, y compris quand
le parsing echouera entierement — c'est precisement ce cas-la qu'on veut pouvoir
rejouer. Chaque ligne produite garde son intervalle de position dans ce texte.

Consequence pour un nouveau lecteur : il recoit le texte **tel quel**, sans
`strip()` ni normalisation de fins de ligne, sans quoi les bornes enregistrees a
cote cessent de designer quoi que ce soit.

`myassistantbet-replay` relit un collage avec le code courant, en simulation par
defaut. C'est l'outil de reprise apres un correctif de lecteur.

## Une migration ne deplace aucun indicateur

Les indicateurs de `/stats` doivent etre **identiques avant et apres** une
migration, hors changement explicitement voulu. Ca se verifie par un test —
`tests/helpers.migre_jusqu_a` applique les migrations jusqu'a une version — et
non a l'oeil : trente cartes ne se comparent pas de memoire.

Les lignes de controle s'ecrivent en SQL sous l'ancien schema, jamais par le
service : `add_pick` est le code **courant**, et l'employer testerait la fixture
au lieu de la migration.

## Les trois populations ne se melangent jamais

`principale`, `exploratoire` (section C-bis, produite sans fait date) et
`tardive` (ecrite apres le coup d'envoi) sont comptees separement de bout en
bout, temoin d'audit compris. Leur somme vaut le total historique, et un test le
garde.

Un indicateur qui les melangerait detruirait les deux comparaisons que ces
populations existent pour rendre possibles : fait date contre lecture, et prix
d'avant-match contre prix ecrit en connaissant le debut du match.

## Toute requete sur une table qui grossit passe par `EXPLAIN QUERY PLAN`

**Quatre tables grossissent** : `prompt_odds`, `odds`, `events`, `picks`. Une
requete qui en filtre une sur un axe que rien n'indexe **passe tous les tests** —
les fixtures sont petites, le cout est proportionnel au volume — et rend la page
inutilisable en production.

Mesure du 26/08/2026 : le board est passe de **0,043 s a 18,36 s** et
`/competitions` de **0,040 s a 18,98 s**, sur une requete nouvelle qui filtrait
`prompt_odds` par `event_id` seul quand le seul index disponible portait
`(session_id, event_id)`. **La suite etait verte a 2 698 tests.** Ce qui l'a
trouve est une plainte d'utilisateur, pas un garde-fou.

Le geste, avant d'ecrire la requete et pas apres :

```sql
EXPLAIN QUERY PLAN <la requete>;
```

Un `SCAN` sur l'une de ces quatre tables se justifie ou se corrige. Un `SEARCH …
USING INDEX` n'appelle rien. Et **un index ne sert que si sa colonne de tete est
celle du predicat** : c'est exactement ce qui manquait ici.

`tests/test_plan_requetes.py` garde la propriete pour `unpriced()` — le **plan**,
jamais le temps : un test chronometre est instable et finit desactive au premier
faux positif. Il lit la SQL reellement executee plutot que d'en garder une copie,
et il verifie qu'il mordrait encore si l'index disparaissait.

## Le disque est une panne d'exploitation, et rien ne la surveille

`/tmp` est un **tmpfs de 5,8 Go**, donc de la mémoire vive. Relevé du
19/08/2026 : il était à **96 %**, et les deux consommateurs sont connus.

| Ce qui remplit | Volume | Qui l'a créé |
| --- | ---: | --- |
| `/tmp/pytest-of-ubuntu` | ~1,1 Go | les exécutions de la suite |
| `/tmp/claude-1000/…` | ~2,0 Go | les copies de base des sessions de travail |

**Une exécution complète de la suite coûte 450 à 600 Mo** — 2 200 tests, chacun
avec sa base SQLite migrée — et `pytest` en **conserve trois** (son défaut,
`tmp_path_retention_count`). Le régime permanent est donc de l'ordre de 1,5 Go
pour les seuls tests.

Ce que ça casse, et ce n'est pas théorique : un `/tmp` plein fait échouer les
écritures de test avec un `ENOSPC` que la sortie de `pytest` **n'explique pas**,
et SQLite y pose ses fichiers temporaires — un `VACUUM INTO` ou un tri volumineux
échouent alors sur la base servie. Le motif habituel du projet : une panne
d'exploitation qui ne ressemble pas à ce qu'elle est.

**Les trois règles, dans l'ordre où elles évitent le problème :**

1. **Une copie de base ne va jamais dans `/tmp`.** Elle pèse 275 Mo. Le disque
   `/` en a 50 Go de libre ; le tmpfs, cinq fois moins au total. Une session de
   travail pose ses copies sous `~/`, dans un dossier à son nom.
2. **On ne supprime que ce qu'on a créé.** Les répertoires de `/tmp/claude-1000`
   appartiennent à d'autres sessions, dont certaines tournent encore — en effacer
   un lui retire son état de travail sans qu'elle puisse le savoir. Les siens se
   nettoient à la fin, y compris le dossier de travail sous `~/`.
3. **On regarde avant de lancer une suite longue.** `df -h /tmp` coûte une
   seconde ; découvrir le disque plein au milieu d'un `pytest` de quatre minutes
   coûte les quatre minutes, et le diagnostic se paie une deuxième fois parce que
   l'erreur ne nomme pas sa cause.

`pytest` fait sa part tout seul — il retire les exécutions au-delà des trois
dernières — donc ses répertoires ne se suppriment pas à la main : le faire
pendant qu'une suite tourne lui retire sa base sous les pieds.
