# DIAGNOSTIC — l'ingestion, les paliers hauts, et ce qui se perdait en silence

Relevé du **17/08/2026**, sur une copie de la base servie (29 Mo, 235 sélections,
15 sessions). Toute mesure citée ici a été refaite sur cette copie ; aucune n'est
reprise du brief sans vérification, et deux d'entre elles le contredisent.

Une cause racine par point, avec son fichier et sa ligne, ou la mention explicite
**non établie** et ce qui manquerait pour l'établir.

---

## §0 — Exigence transversale : plus aucune perte silencieuse

**Cause racine constatée.** Trois chemins laissaient tomber sans trace, et aucun
n'écrivait nulle part :

| Où | Ce qui se perdait |
| --- | --- |
| `services/confidence.py:464` `read_blocks` | motifs rendus dans une liste de chaînes, affichée une fois puis refermée avec l'onglet |
| `services/picks_import.py` `_attach_claims` / `_attach_combos` | idem, via `preview.notes` |
| `main.py` `confirm_picks_import` | les `HistoryError` d'`add_pick` s'affichaient, rien n'en gardait la mémoire |

C'est la **cinquième occurrence** du défaut caractéristique du projet — une sortie
identique pour l'échec et pour le cas ordinaire — et cette fois sur le chemin qui
alimente tout le reste.

**Correctif.** Migration 050, table `ingestion_rejects` : session, type de bloc,
motif fermé (six valeurs), détail, et surtout le **texte brut**. Sans lui, on
saurait qu'un bloc a échoué et jamais lequel — le silence sous un autre nom.
`services/ingestion.py` porte le vocabulaire, l'écriture et le relevé. Un
compte-rendu clôt chaque import, une ligne apparaît sur `/stats`, une réserve
voyage dans l'export.

**Rien n'est rétro-rempli** : la mesure commence à la mise en service, et la
table le dit.

---

## §1 — Les blocs `conf` ne sont jamais lus (bloquant)

**Cause racine constatée** : `confidence.BLOCK` (`services/confidence.py:195`) ne
reconnaît un bloc que sous sa **clôture** — ` ```conf ` ou ` ```json `.

Or le module d'import sait depuis toujours que le rendu de Claude et ce qu'on en
copie ne sont pas le même texte. `picks_import._cells` lit les barres verticales
**et** les tabulations, avec ce commentaire : *« ce que l'on copie depuis son
interface est un tableau tabulé, les barres ayant été consommées par le rendu »*.
La même chose arrive à un bloc de code : le JSON survit, les trois accents graves
partent avec le reste du balisage. La lecture ne se posait donc que sur la forme
que le copier-coller détruit, et l'asymétrie n'était rattrapée nulle part.

**Mesure sur la base servie :**

| Colonne | Renseignée |
| --- | --- |
| `picks.claim_raw_json` | **0 sur 235** |
| `picks.confidence_claimed` | **0 sur 235** |
| `picks.confidence_computed` | 86 — **tous à 1**, posés par l'override de recherche, aucun par un bloc lu |
| `sessions.open_dossiers_state` | `absente` sur les trois sessions concernées (11, 13, 14) |

Les sessions 11, 13 et 14 sont les **seules** où le gabarit demandait un bloc par
ligne (`prompts.body` contient ` ```conf ` à partir du 14/08). Elles portent 86
sélections, toutes sans bloc.

**Les autres pistes du brief ont été vérifiées une par une, et aucune ne
mordait** — chacune a désormais son test :

| Piste | Verdict |
| --- | --- |
| JSON multi-ligne et indenté | passe (`re.DOTALL`) |
| `source_level` entier **ou** `"lecture"` | passe (`str(...).strip().lower()` contre `LEVELS`) |
| `faits: []` | réponse normale, aucun rejet |
| `editeur_origine` absent | facultatif, aucun rejet |
| `M15` confondu avec `M1` | impossible : `_pairs` compare des **clés de dictionnaire**, pas des préfixes |
| accents et apostrophes dans `enonce` | traversent JSON |
| deux chemins d'import | il n'y en a qu'un, `build_preview` |

**Correctif** : `ingestion.read_bodies` lit les deux formes — la clôture quand
elle est là, sinon un objet JSON de premier niveau reconnu **à sa forme**
(`faits`/`source_level` pour un bloc de confiance, `jambes` pour un combiné ; les
deux familles portent `type`, qui ne peut donc pas trancher). Le comptage
d'accolades se fait hors chaînes.

**Limite structurelle, notée plutôt que rattrapée** : sans clôture, un objet qui
ne se relit pas est indiscernable de la prose et se perd. Une heuristique plus
large avalerait des phrases. Le chemin cloturé reste le seul où un JSON cassé
peut se signaler — c'est aussi pour ça que le gabarit continue de le demander.

### Rejeu : **impossible**, et c'est établi plutôt que supposé

Les sorties brutes du modèle **ne sont conservées nulle part** :

- aucune colonne de `sessions`, `prompts`, `picks` ni d'aucune des 31 tables ne
  porte le texte collé — `prompts.body` est le prompt **émis**, pas la réponse ;
- `data/uploads/` ne contient que deux captures de coupons ;
- la route `POST /history/{id}/picks/preview` reçoit le texte en formulaire et ne
  l'écrit jamais.

**Aucune commande de backfill n'est donc livrée** : il n'y a rien à relire. Les
86 sélections des sessions 11, 13 et 14 garderont un cran calculé nul pour
toujours. **Information dont l'utilisateur a besoin tout de suite : il faudra une
douzaine de sessions — environ trois semaines au rythme constaté — pour retrouver
un volume mesurable de crans calculés.** Cette population-là repart de zéro.

---

## §2 — Scores en sets et combinés sous-collectés

### Scores en sets — **aucun lecteur n'a jamais existé**

**Cause racine constatée** : `set_scores.save()` (`services/set_scores.py:144`)
n'est appelé que par `main.save_set_score`, la route de saisie manuelle, **une
ligne à la fois**. Aucun chemin d'import ne touchait ce module. Les 5 scores de
la base (sessions 7 et 8, 2 tranchés) ont donc tous été tapés à la main.

**Cause seconde, et elle n'est pas dans le code** : le gabarit demande ces scores
**en prose** — *« Écris le score, une phrase de justification, et rien d'autre »*
(`session_default.md.j2:718`) — et pas dans un tableau de section D comme le
brief le suppose. Le dossier de projet assume ce choix : *« saisie à la main,
jamais parsée du rendu »*.

**Le gabarit étant hors périmètre (règle 4), ce point n'est corrigé qu'à
moitié**, et la moitié manquante est nommée dans le récapitulatif. `set_scores.read()`
reconnaît ce qu'une prose disciplinée porte quand même : un repère de bloc **ou**
une affiche, puis un score. `PASSE` est retenu sans score — c'est une réponse du
gabarit, et la compter sépare « huit fois PASSE » de « rien n'a été collé ». Une
ligne sans repère ni affiche est refusée plutôt que rattachée au hasard.

### Combinés — les **deux** hypothèses du brief étaient vraies

**(b) ils n'étaient pas lus**, mais par un **défaut en cascade** :
`_attach_combos` résolvait chaque repère par `pick.claim.match`, donc par le bloc
de confiance. Sans blocs — le cas de **toutes** les sessions de la base — aucun
combiné ne se rattachait, alors que le prompt archivé porte déjà la table
`M3 → affiche` qu'il faut. Un chemin d'ingestion qui tombe parce qu'un autre est
tombé cumule deux silences pour une seule cause.

**(a) rien ne les restituait** : `stats_export.SECTIONS` n'avait aucune entrée
combinés. Un combiné se lisait sur la feuille de sa session et nulle part
ailleurs.

Mesure : `combos` et `combo_legs` **vides**, et le bloc ` ```combo ` n'est demandé
que par le tout dernier prompt de la base (session 14, 16/08). Les deux causes
sont donc réelles mais aucune n'a encore eu l'occasion de mordre.

---

## §3 — Antériorité non établie sur 23 % des sélections

**Cause racine constatée — et elle contredit les trois pistes du brief.**

| Ce qui était supposé | Ce que la mesure dit |
| --- | --- |
| horodatage manquant | **0 sur 230** : toutes portent `created_at` et le `commence_time` de leur match |
| fuseau | sans objet, les deux valeurs sont en UTC ISO |
| sessions `reconstruit` | sans objet : aucune sélection ne manque de `commence_time` |
| horodatage côté client | déjà côté serveur — `add_pick` écrit `utcnow()` dans l'INSERT |

Les 52 sont **réellement** postérieures au coup d'envoi. Répartition :

| Motif | Compte | Ce que ça vaut |
| --- | --- | --- |
| `differee` | 15 | décision antérieure, saisie tardive : étiquette valide, **prix douteux** |
| `live` | 0 | jamais employé |
| aucun motif | 37 | antérieures à la garde d'écriture (migration 034, 11/08/2026) |

**Ce qui manquait n'est donc pas une donnée mais une distinction**, et le compte
unique la détruisait : il faisait lire un défaut de collecte là où il n'y en a
pas, et promettait un « 0 % de tardives » que la garde ne peut pas tenir —
`differee` reste un chemin ouvert, délibérément.

**Critère d'acceptation du brief, honnêtement** : « 0 % sur les sessions
postérieures » n'est atteignable que pour les lignes **sans motif**, et il l'est
déjà depuis le 11/08 — la garde refuse. Les `differee` continueront d'apparaître,
et c'est voulu.

---

## §4 — Cote de référence : 20 sélections au purgatoire

**Cause racine constatée** : `_quarantined` (`services/history.py:1879`) écartait
du regroupement par palier toute sélection dont le prix vient d'un book de
référence sans cote obtenue. La règle était juste **tant qu'on attendait cette
cote** ; elle n'arrivera jamais.

`set_real_price` est le seul écrivain de `price_real`, il se saisit après avoir
posé le pari, et **aucun pari n'est posé** : `coupons` vide, `coupon_id` nul sur
les 235, `played` faux partout, douze sessions.

Mesure : `price_source` vaut `reference` sur 94 sélections, dont 32 tranchées et
**20 encore écartées après le filtre d'antériorité**. Le book principal ne servant
qu'un marché via le fournisseur de cotes, la population ne se résorbera pas non
plus.

Et le gabarit tranchait déjà l'autorité : *« Ces cotes font autorité. Ne remplace
jamais une cote du bloc par une cote trouvée en ligne. »* Le code n'en tirait pas
la conséquence.

**Correctif** : la cote du bloc **est** la cote de calcul. Le drapeau reste et
change de nature — `by_price_source` en fait un **axe de mesure**. Lever la
quarantaine sans cet axe aurait mélangé deux populations sans plus aucun moyen de
les distinguer.

Le relevé automatique du prix affiché par le bookmaker, proposé en option par le
brief, est **refusé** : ce serait une intégration transactionnelle avec un
bookmaker, interdit n°7 de `SPEC.md`. Ce n'est pas un arbitrage de coût.

---

## §5 — Doublons sur un même match

**Cause racine : il n'y en a pas.** Les deux points demandés étaient déjà tenus,
et ils n'avaient aucun test.

- la garde existe : `add_pick` (`services/history.py:1648`) refuse depuis la
  migration 028 une seconde sélection sur une même rencontre sans justification
  d'indépendance. C'est l'un des deux seuls contrôles bloquants du module. Son
  refus est désormais journalisé, avec le reste de l'ingestion (§0) ;
- le compte affiché vient bien d'un **champ** : `clustered_selections` dérive de
  `residual_clusters`, le regroupement par match calculé dans `analysis()` — le
  même qui sert la borne conservatrice du bloc de tête.

**Marquer les paires en base a été écarté** : ce serait recopier un fait dérivable
de `picks.event_id`, exactement ce que le projet refuse pour la famille d'un
marché et le niveau d'une compétition — la recopie diverge, la lecture se corrige.

**Ce qui manquait vraiment** : la réserve disait qu'il faut élargir les
intervalles et jamais **où aller regarder**. 8 rencontres portent deux sélections,
dont `Lens – Paris Saint Germain` du 16/08 — « PSG O1.5 Eq. buts » et
« Lens +0.5 Handicap », deux lectures **opposées** du même rapport de forces,
notées l'une gagnante et l'autre perdante. Elles sont nommées.

---

## §6 — Paliers jamais employés

**Cause racine constatée** : `MixRow.absent_sessions` comptait les sessions sans
sélection dans un palier **sans jamais regarder le lot**. Les trois causes
possibles étaient donc indiscernables. Le prompt calcule pourtant
l'atteignabilité à la génération (`prompt.reachable`, ligne `Paliers` de chaque
bloc) et ne la conservait nulle part.

**Correctif sans migration** : tout se relit dans `prompt_odds`, qui fige le
marché au moment où le prompt part. Relire `odds` donnerait le marché
d'aujourd'hui.

Mesure, sur les **six** sessions dont le marché est figé (`prompt_odds` date de la
migration 033 ; les huit plus anciennes n'ont aucun relevé et sont exclues plutôt
que comptées « bande jamais atteinte ») :

| Palier | Bande atteinte | Employé | Sélections |
| --- | --- | --- | --- |
| 🟢 SAFE | 6/6 | 6 | 55 |
| 🔵 FUN | 6/6 | 6 | 68 |
| 🟠 ULTRA FUN | 6/6 | 5 | 6 |
| 🔴 GIGA FUN | **5/6** | **0** | **0** |
| 💥 GIGA+ | **5/6** | **0** | **0** |

**Les bandes hautes étaient atteignables cinq fois sur six et n'ont rien
produit.** C'est la méthode, pas le lot — et c'est ce qui fonde le §7.

---

## §7 — Ouvrir les paliers hauts

**Cause racine** : la règle du gabarit — *« une sélection qui sort des deux
paliers les plus sûrs ne se prend pas sur une lecture : il lui faut un fait nommé
et daté en section A »* — combinée à sept dossiers de recherche pour quinze
matchs, et au fait qu'un fait daté désigne le plus souvent un favori.

Cette règle a été écrite pour éviter de gaspiller une mise sur un outsider mal
étayé. **Aucune mise n'est posée** ; ce coût n'existe pas, le coût inverse est
réel.

**L'exigence n'est pas supprimée.** Un second circuit s'ajoute à côté : section
C-bis dans le gabarit (seule modification autorisée du prompt, écrite au mot
près), colonne `exploratoire` (migration 051), séparation stricte de bout en bout
— `analysis`, `feedback`, `stats` et `labelling` filtrent tous sur la population
principale, témoin d'audit compris.

**Vérification demandée, faite sur une copie de la base servie** : après
application des migrations 050 et 051 — 230 sélections tranchées avant, 230
après ; `recorded` = 230, `settled` = 178, `without_antecedence` = 52,
`consistent` vrai. **Les indicateurs historiques sont inchangés**, la colonne
valant 0 sur les 235 lignes existantes.

---

## §8 — L'absence de coupons présentée comme un manque

**Cause racine** : la formulation elle-même. *« Ce n'est pas une collecte qui
manque, c'est un geste qui n'a pas eu lieu »* est exact et décrit un usage assumé
comme une lacune. Deux surfaces réclamaient en outre un geste qui n'aura pas
lieu : la colonne « cote obtenue » et le bouton « jouer ».

**Correctif** : interrupteur `suivi_coupons`, désactivé par défaut, qui ferme le
bloc, les deux surfaces et le compteur de couverture correspondant. Rien n'est
supprimé.

**Ce qui a été préservé et qu'il fallait regarder à deux fois** : le bloc se
rendait **vide plutôt que masqué** pour distinguer « aucun pari posé » de « cette
page ne mesure pas les paris posés ». Cette distinction vaut toujours suivi
ouvert, et son test est conservé sous cette condition.

---

## Ce que la mesure a contredit

Trois affirmations du brief ne tiennent pas, et deux d'entre elles auraient
envoyé chercher au mauvais endroit :

| Affirmé | Mesuré |
| --- | --- |
| l'antériorité manque par défaut de collecte, peut-être sur les sessions `reconstruit` | 0 sélection sur 230 sans horodatage : les 52 sont réellement tardives, et 15 sont déclarées |
| la section D demande un tableau de score en sets | elle demande de la **prose**, explicitement — un parseur ne peut pas s'y adosser |
| les combinés sont « lus mais non restitués » **ou** « pas lus du tout » | **les deux** : non rattachés par cascade du §1, et non restitués |

Et une quatrième, plus discrète : les blocs `conf` ne sont pas « non alimentés »
faute de production — le gabarit les demandait bien et le modèle les produisait
probablement. C'est le **transport** qui les perdait, et rien ne le disait.
