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
