# ÉTAT AU 19/08/2026, 23h45 — à lire en premier

**En service** : schéma **64**, `SERVE_LINES_ENABLED=1`,
`CURRENT_EVENT_LINE_ENABLED=1`.

**Fin de journée. Trois points, aucun ajout de fonctionnalité.**

1. **Le bilan cartons de l'arbitre devient conditionnel** — il ne se cherche que
   sur un marché de cartons. Mesure du lot 11 : huit arbitres, huit « non
   trouvé ». **+72 tokens.**
2. **L'heure d'un bloc tennis porte « (estimée) »** — aucune source accessible ne
   sert le court ni le rang, donc elle est invérifiable. **+92 tokens**, plus ~3
   par bloc tennis. **L'ancrage de session n'est pas rendu** : `timezone` et
   `city` sont vides sur les quatre compétitions de tennis, et le déduire d'un
   libellé est interdit.
3. **La purge des artefacts temporaires tourne**, et la fuite qui les créait est
   fermée — elle était dans `tests/helpers.migre_jusqu_a`.

**Quatre changements de gabarit portent le 19/08** — lignes de service, ligne
`Ici`, variante A, et ces deux-ci. **Leurs effets ne sont pas isolables.** C'est
la limite à connaître avant de lire un écart de résidu autour de cette date.

**Le cadre pèse 66 % d'un prompt de taille médiane** (12 390 tokens de cadre,
810 par bloc, lot médian de 8 blocs). Il a été multiplié par **7,6 en quinze
jours**. C'est une lecture, et le §2 du lot 12 ne conclut rien.

**Le seul chantier restant identifié** : la table `EMMA_ID` par pays, qui
apporterait un fait de **niveau 1** aux blocs football via les alertes météo
officielles. Elle mérite une session dédiée — de la saisie manuelle sur une
quinzaine de pays, faite tard, est de la saisie à revérifier.

**La passe de timelines tourne**, et se reprend sans état :

```bash
uv run myassistantbet-timelines --joueurs 0 --reprise
```

---
# DIAGNOSTIC — lot 1 : l'ingestion, les paliers hauts, et ce qui se perdait en silence

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


---
---

# DIAGNOSTIC — lot 2 : rendre les erreurs de parsing rattrapables

Relevé du **17/08/2026**, sur une copie de la base servie. Même règle que
ci-dessus : aucune mesure n'est reprise du brief sans vérification, et deux
d'entre elles le contredisent.

---

## §A — Persister le collage brut

**Cause racine : il n'y a rien à corriger, et c'est le problème.** Le lot 1 avait
établi que le texte collé n'est conservé nulle part — aucune colonne des 31
tables, `data/uploads` ne portant que des captures de coupons. Rien n'empêchait
que ça recommence.

Et il faut être précis sur ce que la journalisation des rejets (migration 050)
n'apporte pas : **elle attrape ce qui lève, pas ce qui passe et se trompe**. La
panne d'origine ne levait rien — la lecture ne trouvait aucun bloc, faute de
clôture, et se taisait. Une table de rejets serait restée vide.

**Correctif** : migration 052, table `imports_raw`. Le collage intégral, **tel
quel**, avant toute normalisation — un `strip()` rendrait fausses les bornes
enregistrées à côté, et c'est justement le balisage abîmé qui intéresse au rejeu.

Trois décisions à connaître :

| Décision | Raison |
| --- | --- |
| écrit **à l'aperçu** | un collage dont le parsing échoue entièrement n'atteint jamais le formulaire d'import ; l'attendre le perdrait |
| dédupliqué sur l'empreinte | contrairement aux rejets : le texte est le même, ce qu'on garde est de quoi rejouer, pas un compteur d'essais |
| dégrade sans bruit sur session inconnue | garder le collage est un filet, jamais une condition |

Le contrat « l'aperçu n'écrit rien » porte sur les **sélections** ; un test le
garde désormais sous cette forme.

`myassistantbet-replay` relit un collage avec le code courant, **en simulation par
défaut**. Un test rejoue la panne d'origine : un bloc sans clôture, perdu hier,
entre aujourd'hui.

**Non-régression vérifiée sur une copie de la base servie** : `recorded` 230,
`settled` 178, `without_antecedence` 52, `consistent` vrai, ventilation par
palier identique au triplet près, avant et après.

---

## §B — Généraliser la tolérance au transport

**Cause racine constatée** : le mode de destruction était **déjà connu du code**
et n'avait pas été généralisé.

- `picks_import._cells` lit les barres verticales **et** les tabulations depuis
  toujours, avec ce commentaire : « ce que l'on copie depuis son interface est un
  tableau tabulé, les barres ayant été consommées par le rendu » ;
- le gabarit en avait tiré la même leçon : `dossiers_ouverts` s'écrit « hors de
  tout bloc de code », et cette ligne-là n'a **jamais** posé de problème ;
- les blocs `conf` et `combo` ont malgré tout été introduits dans des clôtures, et
  le score en sets était demandé en prose libre.

### B1 — la ligne `sets:`

Le gabarit demande désormais `sets: M3=2-0/2-1 | M4=PASSE`, bâtie sur le modèle de
`dossiers_ouverts` : **elle n'a pas de clôture à perdre**. Le lecteur de prose
reste en filet, et son compteur s'affiche à l'import.

Deux corrections trouvées en câblant la lecture, et aucune n'était dans le brief :

- le rapprochement des repères de la ligne `sets:` **tombait avec les blocs de
  confiance** — même cascade que les combinés avant elle. Il a le même repli ;
- un match peut arriver **deux fois**, par la ligne et par la prose. La
  déduplication se fait au rapprochement, là où l'identité devient connue.

### B2 — le banc, et sa couverture mesurée

`tests/test_transport.py` : **11 altérations × 4 formats**, un seul résultat
acceptable — lecture correcte, ou ligne dans `ingestion_rejects`.

**Compte : 38 lues, 6 rejetées, 0 muette.**

| Altération | combo | conf | sets | tableau |
| --- | --- | --- | --- | --- |
| fence ouvrante retirée | lu | lu | lu | lu |
| fence fermante retirée | lu | lu | lu | lu |
| les deux fences retirées | lu | lu | lu | lu |
| info string absente | lu | lu | lu | lu |
| info string remplacée par `json` | lu | lu | lu | lu |
| barres converties en tabulations | lu | lu | lu | lu |
| tabulations converties en espaces | lu | lu | lu | lu |
| guillemets typographiques | lu | **rejet** | lu | lu |
| lignes rejointes | lu | lu | lu | lu |
| espaces insécables | **rejet** | **rejet** | lu | lu |
| préfixe de numérotation | **rejet** | **rejet** | lu | **rejet** |

**Aucune combinaison non couverte.** Les six « rejet » ne sont pas des trous :
l'altération détruit l'information pour de bon — un JSON dont les guillemets sont
typographiques n'est plus du JSON — et prétendre la lire serait inventer. Ce que
le banc exige est que l'échec **se voie**, et il se voit dans les six cas.

`sets:` survit aux onze, ce qui est l'argument de B1.

**Le banc a failli passer pour la mauvaise raison**, et c'est noté : sa fixture
`conf` portait un bloc pour deux lignes du tableau, donc l'appariement échouait
quoi qu'il arrive au transport. Deux blocs désormais. Et l'assertion exige un
rejet **du format testé** : accepter n'importe quelle trace l'aurait fait passer
partout, un collage sans `dossiers_ouverts` produisant toujours une note.

---

## §C — Trancher l'antériorité

**Cause racine : le diagnostic d'origine était faux**, et le lot 1 l'avait déjà
établi — 0 sur 230 sans horodatage. Les 52 écartées ont réellement été écrites
après le coup d'envoi. Ce qui manquait n'était pas un correctif mais une
**décision d'usage**, et la page les présentait comme un manque.

Mesure refaite après migration 053, sur une copie de la base servie :

| Population | Tranchées | Résidu | P |
| --- | --- | --- | --- |
| principale | 178 | −14,78 pour 103,78 payées | 0,014 |
| tardive | 52 | **+3,24** pour 28,76 payées | 0,856 |

**Écart de résidu par sélection : +0,145.** C'est la meilleure estimation
disponible du biais que produit une sélection écrite en connaissant le début du
match, et elle ne s'obtient qu'en gardant les deux populations séparées.

Motifs des 52 : 15 `differee`, 0 `live`, 37 sans motif déclaré.

Le rétro-remplissage est **sûr ici et ne l'était pas ailleurs** : cette valeur se
*dérive* de `created_at` et `commence_time`, déjà en base, là où `price_source`
(030) ou le cran calculé (042) auraient demandé de reconstituer une information
jamais écrite. Dériver n'est pas inventer.

**C2** — la garde ne refuse plus : elle se laissait contourner, et refuser ferait
disparaître la population qui porte la mesure. Le compte-rendu d'import **nomme
les matchs avec leur écart en minutes**.

**Trouvé en câblant, et absent du brief : un report de match lève le retard.** Un
match reporté n'a pas commencé, donc une sélection écrite « après » l'ancien
horaire n'a rien vu. La règle vit dans `history._LATE_RULE`, écrite une fois, et
le scan la rejoue dès qu'un coup d'envoi bouge. C'est le seul cas où un report
change une mesure déjà écrite.

**Non-régression** : 183 + 0 + 52 = 235, `consistent` vrai, ventilation par
palier inchangée.

---

## §D — Vérifier que l'instrumentation instrumente

**D1 — le recalcul du cran applique bien la table.** Vérifié par trois blocs
volontairement mal notés, corrigés dans les trois sens (5 → 4, 4 → 1, 4 → 3), et
un quatrième test qui suit le parcours **jusqu'en base** : un test sur l'objet
seul laisserait passer un `add_pick` qui écrirait l'annonce dans la colonne
calculée. **Rien à corriger.**

**D2 — un chemin était muet, et le contrôle l'a trouvé dès sa première
exécution.** `replay` collectait ses échecs d'écriture dans `failures` et ne les
journalisait jamais : une ligne refusée par une garde y disparaissait exactement
comme avant le lot 1. Corrigé.

`selfcheck-ingestion` passe par les **deux vraies routes** — aperçu puis import :
appeler `build_preview` seul ne vérifiait que la moitié du chemin, les rejets
d'écriture ne naissant qu'à l'import, et c'est ce demi-contrôle qui laissait le
rejeu muet. Résultat : **8 contrôles, 0 manque**. Un second test rend un chemin
muet exprès et vérifie que le contrôle tombe.

Limite connue et notée dans le code : le contrôle prouve que les chemins
**déclarés** journalisent, jamais qu'ils sont tous déclarés. La règle de
`CONTRIBUTING.md` en tient lieu.

**D3 — le relevé des paliers ne portait que sur 6 sessions sur 14**, et rien ne
le disait. `prompt_odds` date de la migration 033 : les huit plus anciennes n'ont
aucun marché figé, et les compter ferait lire « bande jamais atteinte » sur des
sessions dont on ne sait rien. La page l'annonce désormais. Un test vérifie
qu'une session **nouvelle** alimente bien le relevé.

---

## Ce que la mesure a contredit — lot 2

| Affirmé | Mesuré |
| --- | --- |
| « la journalisation des rejets attrape désormais les blocs qui échouent » — donc le risque serait couvert | elle attrape ce qui **lève** ; la panne d'origine ne levait rien, et une table de rejets serait restée vide. C'est la persistance du brut qui couvre, pas elle |
| le banc doit couvrir « 4 formats × 10 altérations » | il en porte **11** : la numérotation de lignes est une onzième forme observée, et la retirer aurait fait passer le seul cas qui casse le tableau |
| §C ne demandait qu'à isoler la population tardive | il fallait aussi **lever le retard sur un report** — sans quoi un match reporté sortirait des indicateurs principaux pour rien. Absent du brief, trouvé en câblant |

Et une quatrième, sur la forme des tests plutôt que sur les données : la
non-régression ne peut pas se vérifier en comparant deux appels d'`analysis()` de
part et d'autre d'une migration — **le lecteur est toujours le code courant**, et
il ne tourne pas sur un schéma antérieur. Elle compare donc les indicateurs à ce
que les lignes impliquent, lues en SQL.


---
---

# DIAGNOSTIC — lot 3 : consolider la mesure, et un préalable qui ne tient pas

Relevé du **17/08/2026**, sur une copie de la base servie. Même règle que
ci-dessus : aucune mesure n'est reprise du brief sans vérification, et **cinq
affirmations le contredisent** — dont une qui arrête toute la partie II.

---

# PARTIE I — consolider la mesure

## §1 — Le contrôle prouvait ce qu'on lui avait déclaré, et rien d'autre

**Cause racine : la règle du lot 2 n'en était pas une.** `selfcheck.py` portait
la limite en toutes lettres — *« le contrôle prouve que les chemins **déclarés**
journalisent, jamais qu'ils sont tous déclarés. La règle de `CONTRIBUTING.md` en
tient lieu. »* Elle n'en tenait pas lieu, et c'est mesuré : `replay` a été écrit
**le même jour et par la même main** que cette règle, et il a laissé tomber ses
échecs d'écriture sans les journaliser. Une règle de contribution ne se
déclenche pas.

**Trois énumérations étaient possibles ; deux ont été essayées sur le dépôt et
écartées avant d'écrire une ligne.**

| Piste | Verdict |
| --- | --- |
| convention de nommage | les trois écrivains s'appellent `add_pick`, `record` et `save`. Il faudrait en inventer une — donc remplacer une règle qu'on oublie par une autre règle qu'on oublie |
| inspection du module | ne voit que ce qui est importé, et ne distingue pas une fonction qui écrit d'une qui lit. Prouverait qu'un décorateur a été posé, jamais qu'il en manque un |
| **analyse statique** | se pose sur la chose elle-même : une fonction qui porte un `INSERT INTO` vers une table gardée. Ne dépend d'aucune discipline, et reste vraie le jour où personne ne se souvient du fichier |

**Correctif** : `services/write_paths.py` porte le registre et le décorateur
`@writes`. `tests/test_write_paths.py` lit la **source** — pas les objets
importés — et fait échouer la suite dès qu'une fonction insère dans `picks`,
`combos`, `combo_legs` ou `set_scores` sans être déclarée.

**Ce que le registre a trouvé dès sa première exécution.** Le dénominateur du
contrôle se dérive désormais des familles déclarées, et il a immédiatement
contredit le numérateur : la famille `exploratoire` n'avait **aucun exemplaire
malformé**, donc n'était vérifiée nulle part. « 8 sur 8 » restait vrai parce que
les deux nombres étaient écrits à la main et ne pouvaient pas se contredire. Le
contrôle passe de **8 à 10**.

**Trois gardes sur le garde-fou lui-même**, parce qu'il porte le même défaut que
tout le reste ici : `test_toute_fonction_qui_insere_est_declaree` et sa
réciproque **passent tous les deux si l'analyse ne rend rien** — deux ensembles
vides sont égaux. Un test vérifie donc que les trois écrivains connus sont
encore vus, un autre qu'un `INSERT` concaténé sur plusieurs lignes l'est aussi,
un troisième qu'un type de bloc inconnu est refusé à la déclaration.

**Critère d'acceptation vérifié** : l'analyse rejouée sur un faux paquet portant
`enregistrer_quelque_chose` — un nom qu'aucune convention n'aurait prévu — le
détecte.

## §2 — La population exploratoire est vide pour la cause 1, et ce n'est pas la réponse utile

**Les trois pistes ont été vérifiées dans l'ordre imposé, pas la première
retenue.**

| Piste | Verdict |
| --- | --- |
| 1. aucune session importée depuis C-bis | **c'est elle**. Dernier prompt de la base : 16/08 17:50. Dernier import de sélections : 15/08 22:21. Tous les commits des lots 1 et 2 sont datés du 17/08, et la base servie est **encore en migration 049** — l'application n'a pas redémarré |
| 2. le gabarit rendu ne porte pas la section | **écartée par la mesure**. Un prompt réel rendu sur une copie porte bien `### C-bis` et la ligne `sets:`. La porte `tier_scope.high` se serait ouverte sur **6 sessions sur 6** parmi celles dont le marché est figé — ULTRA FUN atteignable 6/6, GIGA FUN et GIGA+ 5/6 |
| 3. l'extracteur ne la reconnaît pas | **écartée**. Le lecteur la reconnaît des deux côtés, et un test paramétré le vérifie section par section |

Le zéro est donc **attendu**. Mais — et c'est le vrai défaut — **cette réponse
ne se distinguait d'un extracteur muet par rien**, et c'est exactement l'état
dans lequel les blocs `conf` sont restés quatre jours.

**Le trou que ni les rejets ni le collage brut ne couvrent** : une section
absente **n'échoue pas, elle n'arrive pas**. Elle ne lève donc rien, ne produit
aucun rejet, et le zéro de la population qu'elle alimente est illisible.

**Correctif : `services/sections.py`, et aucune migration.** Les deux moitiés
dorment déjà en base — ce que le prompt émis réclamait (`prompts.body`) et ce
que le collage a rapporté (`imports_raw`). Une colonne aurait figé un constat que
le code courant sait refaire, et aurait menti au premier lecteur corrigé : même
arbitrage que la famille d'un marché et le palier calculé à la lecture.

**Trois états et non deux**, et c'est le troisième qui manquait : « jamais
demandée » n'est pas « demandée et perdue ». Un lot sans palier haut atteignable
n'a pas de section C-bis, et le lui reprocher enverrait chercher un lecteur muet
là où il n'y a qu'une porte de gabarit fermée.

Deux règles tenues : chaque section se reconnaît par **le lecteur qui
l'importe** — une seconde expression régulière posée à côté aurait fini par ne
plus désigner la même chose ; et une ligne `dossiers_ouverts` **vide ou
illisible** compte comme trouvée, la première étant une déclaration du modèle et
la seconde un défaut de lecteur.

La ligne se rend à **l'aperçu**, seul instant où elle change quelque chose, et
sur la page pour ce qui est déjà passé.

## §4 — Rien à corriger, et c'est le problème

**Cause racine : il n'y en a pas.** Trois lots ont modifié ce qui est produit et
ce qui est mesuré **en une seule journée**, un quatrième arrive, et rien ne date
ces changements. Les sélections portent leur `created_at` ; le cadre sous lequel
elles ont été produites n'existe nulle part.

Migration 054, et **deux colonnes qui répondent à deux questions différentes** :
`sessions.gabarit_sha` dit *le gabarit a-t-il changé* — elle se calcule et bouge
sur une virgule ; `sessions.gabarit_version` dit *quel* changement — elle
s'incrémente à la main, donc elle nomme une décision. Les fondre obligerait à
reparser une colonne qu'on a soi-même écrite. Les deux sont figées au **premier
prompt** par `COALESCE`, comme `scale_version`.

Le **nom** des fichiers entre dans l'empreinte : deux gabarits dont on
échangerait le contenu rendraient sinon la même somme, et ce n'est pas le même
cadre.

`changelog_mesure` porte trois portées fermées — `gabarit` déplace ce que le
modèle reçoit, `ingestion` ce qui entre en base à production constante,
`restitution` ne déplace **rien** et se journalise quand même, puisque c'est elle
qui explique qu'un chiffre ait *paru* changer.

**Le seed est rétroactif et sûr** : il se lit dans l'historique des commits, qui
existe. C'est la différence avec `price_source` (030) ou le cran calculé (042).

**Ce qu'il montre au passage, et qui n'est pas confortable** : les lots 1 et 2
sont tous deux du 17/08 et ne fournissent donc **qu'un seul point de coupe**.
C'est un fait sur le rythme de livraison, pas un défaut du journal — inventer une
seconde date ferait croire à un découpage qui ne découpe rien.

**Le découpage est un outil de lecture et jamais un test.** Aucun `p` ne
l'accompagne : la date est posée d'avance, ce qui évite la multiplicité, mais
deux moitiés d'une base de 235 sélections restent deux petits échantillons.

Mesure sur la copie, coupe du 15/08 : **−0,098 par sélection avant, −0,042
après, soit +0,056**. Elle ne conclut rien.

## §3 — Le résidu croît avec le retard, et il croît monotonement

Migration 055, `picks.late_minutes`. **Stockée et non dérivée**, contre l'usage
du projet et pour la même raison que `tardive` : un retard dérivé à la lecture
sortirait d'un `commence_time` **courant**, donc d'un horaire qui a pu bouger.
La contrepartie est tenue — `_LATE_RULE` écrit les **deux** colonnes dans le même
UPDATE, et `set_event` passe désormais par elle au lieu de recalculer à la main.
Cette seconde écriture aurait divergé au premier ajustement, et le premier
ajustement est arrivé ici même.

**Mesure sur la copie de la base servie, 52 tardives tranchées :**

| Bande | Tranchées | Gagnées | Intervalle | Écart au prix | Par sélection |
| --- | ---: | ---: | --- | ---: | ---: |
| moins de 15 min | 2 | 1 | [9 – 91] | −0,39 | **−0,193** |
| 15 à 60 min | 15 | 7 | [25 – 70] | −0,78 | **−0,052** |
| plus de 60 min | 35 | 24 | [52 – 81] | +4,40 | **+0,126** |

**Les deux bandes courtes sont *sous* leurs prix, comme la population
principale.** Tout l'excédent de la population tardive (+3,24) vient de la seule
bande au-delà d'une heure. La direction est celle qu'un mécanisme de
contamination prédit.

**Deux mesures absentes du brief et qui changent la lecture :**

- le retard **médian vaut 133 minutes**, le maximum **1557** (vingt-six heures),
  et **2 lignes sur 52** sont sous le quart d'heure. L'hypothèse du brief — « la
  première peut n'être qu'un simple retard d'import » — décrit donc un cas qui
  n'existe presque pas dans cette base ;
- aucune ligne n'a de retard inconnu : le rétro-remplissage couvre les 52.

**« Démontrée » reste plus fort que ce que trois bandes peuvent porter**, et
c'est la seule réserve à tenir. La méthode établie du projet — celle qui a fermé
le biais d'exposition — dit qu'une variable ordonnée se teste par une
**tendance** et non par des tranches comparées. Les bandes sont posées d'avance,
ce qui évite la multiplicité ; le test qui conclurait reste à faire, et la règle
de travail n°4 interdit de l'inventer ici. Ce qu'il faudrait : un test de score
de la pente dans `logit(P) = logit(1/cote) + a + b·log(minutes)`, la machinerie
existant déjà pour le gradient de cote.

## §5a — La numérotation se récupère, et elle méritait de l'être

Le banc la rapportait comme *« le seul cas qui casse le tableau »*, détectée et
non rattrapée. Contrairement aux guillemets typographiques sur du JSON, elle **ne
détruit aucune information** : un préfixe `  12  ` se retire sans perte.

**Le banc passe de 38 lues / 6 rejets à 41 lues / 3 rejets / 0 muettes.**

| Altération | combo | conf | sets | tableau |
| --- | --- | --- | --- | --- |
| fence ouvrante retirée | lu | lu | lu | lu |
| fence fermante retirée | lu | lu | lu | lu |
| les deux fences retirées | lu | lu | lu | lu |
| info string absente | lu | lu | lu | lu |
| info string remplacée par `json` | lu | lu | lu | lu |
| barres converties en tabulations | lu | lu | lu | lu |
| tabulations converties en espaces | lu | lu | lu | lu |
| guillemets typographiques | lu | **rejet** | lu | lu |
| lignes rejointes | lu | lu | lu | lu |
| espaces insécables | **rejet** | **rejet** | lu | lu |
| **préfixe de numérotation** | **lu** | **lu** | lu | **lu** |

Deux contraintes tenues, et la seconde a décidé de la règle :

- **le préfixe devient autant d'espaces, il n'est jamais supprimé.**
  `imports_raw` garde le texte tel quel et chaque ligne lue garde son intervalle
  de position dedans ; un retrait qui raccourcirait les lignes ferait cesser
  toutes ces bornes de désigner quoi que ce soit. Le brut est gardé **avant** le
  retrait ;
- **la tabulation seule n'est pas un séparateur de numérotation**, et c'est une
  exclusion mesurée. Le module d'import sait depuis toujours qu'un tableau copié
  depuis le rendu arrive **tabulé** : accepter `12\t` ferait manger la première
  colonne d'un tableau dont le numéro de ligne est une donnée. Une vue `cat -n`
  n'est donc pas rattrapée — elle échoue visiblement, ce que le banc garantit
  déjà, et c'est le sens dans lequel ce projet se trompe.

Le retrait exige en plus une séquence **complète et consécutive** : une
numérotation est une propriété du bloc, pas d'une ligne.

## §5b — Le texte est bien conservé, l'information n'y est pas

La question était : le texte des prompts antérieurs à la migration 033 est-il
conservé ? **Oui** — `prompts.body` l'est depuis toujours. Mais la mesure dit que
le rattrapage est impossible quand même :

| Sessions | Lignes « Paliers » par bloc | Blocs | Verdict |
| --- | ---: | ---: | --- |
| 1 à 6, 12 | **0** | 742 | rien à re-parser |
| 7 | 7 | 20 | partiel, et trompeur |
| 8 à 14 | toutes | 634 | déjà couvertes par `prompt_odds` |

**La ligne qui porte l'atteignabilité est née *avec* la migration 033.** Couvrir
la session 7 au tiers de ses blocs dirait « bande jamais atteinte » de treize
blocs jamais lus — le défaut exact que ce relevé existe pour supprimer.

La mention de période reste donc en place, et elle **dit désormais la vraie
cause** : elle imputait le trou au seul `prompt_odds`, ce qui invitait à tenter
le re-parsing. Un résultat négatif non écrit sera refait.

---

# PARTIE II — statistiques de service : le préalable ne tient pas

## §6 — Le dépôt Sackmann n'existe plus, et le seul substitut échoue à la règle de décision

### 6.1 — Les fichiers demandés sont introuvables, et c'est vérifié avec témoins

Les quatre fichiers du brief — `atp_matches_2025.csv`, `atp_matches_2026.csv`,
`wta_matches_2025.csv`, `wta_matches_2026.csv` — rendent **404**, sur `raw` comme
sur l'API. Et ce ne sont pas les fichiers qui manquent, ce sont les **dépôts** :

| Sonde | Réponse |
| --- | --- |
| `api.github.com/repos/JeffSackmann/tennis_atp` | **404** |
| `api.github.com/repos/JeffSackmann/tennis_wta` | **404** |
| `api.github.com/users/JeffSackmann` | 200 — le compte existe |
| `api.github.com/repos/python/cpython` (témoin) | 200 — le réseau répond |
| dépôts publics du compte | **un seul**, `tennis_MatchChartingProject` |

Ce n'est donc ni une panne de réseau, ni un chemin erroné, ni un compte
supprimé : **les deux dépôts ont disparu**. Le dossier de projet le notait déjà
au 07/08/2026 pour les CSV de statistiques de service ; la mesure du 17/08 étend
le constat aux fichiers de matchs, qui étaient encore là.

**Conséquence immédiate** : les colonnes sur lesquelles reposent les §7 et §8 —
`w_svpt`, `1stIn`, `1stWon`, `2ndWon`, `SvGms`, `bpSaved`, `bpFaced` — n'ont
aucune source. La correction que le brief apportait à un brief précédent (« la
tenue de service et le taux de break sont dérivables ») est juste sur le fond et
repose sur `SvGms`, une colonne du dépôt manquant.

### 6.2 — Le seul substitut mesuré, et il échoue à la règle de décision du brief

`tennis_MatchChartingProject` subsiste chez le même auteur, sous la même licence,
et ses fichiers `charting-*-stats-Overview.csv` portent **exactement** les
colonnes du §8 : `serve_pts`, `aces`, `dfs`, `first_in`, `first_won`,
`second_won`, `bk_pts`, `bp_saved`, `return_pts`, `return_pts_won`. La règle de
décision lui a donc été appliquée telle quelle, plutôt que de conclure d'après sa
réputation.

**Méthode** : les 196 joueurs des **cinq derniers lots tennis** en base
(sessions 9, 10, 11, 13, 14 — Canadian Open et Cincinnati, tout sur dur),
rapprochés par nom replié, sur 52 semaines glissantes au 17/08/2026.

| | ATP | WTA |
| --- | ---: | ---: |
| joueurs du lot | 99 | 97 |
| avec au moins un match charté | 74 | 78 |
| matchs chartés / joueur — médiane | 2 | 2 |
| **points de service sur dur — 1er quartile** | **0** | **19,5** |
| points de service sur dur — médiane | 111 | 133 |
| points de service toutes surfaces — 1er quartile | 0 | 53,5 |
| joueurs ≥ 400 points de service sur dur | 17 / 99 | 19 / 97 |

**Règle de décision du brief : premier quartile au-dessus de 400 points de
service sur dur → construire. Il vaut 0 et 19,5. Nulle part.**

### 6.3 — Le risque nommé par le brief est confirmé, et quantifié

Le brief prévenait : *« les colonnes peuvent être vides sur la majorité des
matchs qui comptent — les lots sont majoritairement WTA, entre le 40e et le 130e
rang. »* La mesure lui donne raison, et le mécanisme n'est pas celui qu'il
supposait.

**Le remplissage des colonnes n'est pas le problème : il est de 100 % sur les
748 lignes retenues.** Ce qui manque, ce sont les **matchs eux-mêmes** — le
Match Charting Project est cartographié par des bénévoles, et ils cartographient
le haut du tableau.

| Rang officiel | Joueurs du lot | Médiane, points sur dur | ≥ 400 points |
| --- | ---: | ---: | --- |
| 1 – 20 | 27 | **709** | 19 / 27 |
| 21 – 50 | 44 | 134 | 6 / 44 |
| 51 – 100 | 81 | 126 | 10 / 81 |
| 101 et au-delà | 36 | 21 | 1 / 36 |
| non classés | 8 | 0 | 0 / 8 |

**161 des 196 joueurs du lot sont au 21e rang ou au-delà.** La ligne serait
servie sur les têtes de série et vide exactement là où les lots vivent.

### 6.4 — Le cas concret, sur le lot du 16/08

Six joueurs tirés des matchs réellement rendus le 16/08 :

| Joueur | Matchs chartés | Points de service | Verdict au seuil |
| --- | ---: | ---: | --- |
| Taylor Fritz | 10 | 744 | **au-dessus** |
| Alex Michelsen | **1** | **65** | en dessous |
| Ekaterina Alexandrova | 9 | 709 | **au-dessus** |
| Anna Blinkova | 3 | 171 | en dessous |
| Marta Kostyuk | 13 | 1011 | **au-dessus** |
| Sofia Kenin | 5 | 375 | en dessous |

**Trois sur six.** Et les deux moitiés tombent *dans la même affiche* : le bloc
Fritz – Michelsen rendrait `Service   Taylor Fritz 61.8% 1re · 77.6% s/1re ·
14.5% aces` puis `Alex Michelsen non disponible`. Une demi-ligne, sur le match le
plus en vue du lot.

C'est mot pour mot ce contre quoi le brief prévient : *« Une ligne « Service » à
moitié vide est pire que pas de ligne : elle sera lue comme un fait. »*

### 6.5 — Décision

**§6 s'arrête, et les §7 à §10 et §12 ne sont pas construits.** C'est la branche
« nulle part » de la règle de décision, appliquée telle quelle.

**Rien du gabarit n'est modifié.** La phrase que le §10 demandait de supprimer —
*« Aces, première balle et balles de break ne sont dans aucune source »* — est
**toujours vraie**, et plus vraie qu'avant : elle l'était par choix de collecte,
elle l'est maintenant par disparition de la source. La remplacer par une phrase
annonçant des lignes qui n'existent pas serait la faute la plus coûteuse que ce
projet connaisse — une affirmation fausse à l'endroit exact où le lecteur va
chercher.

**Ce qui reste ouvert, et à quelles conditions.** Le Match Charting Project est
utilisable *pour le haut du tableau* : 100 % de remplissage, colonnes exactement
celles du §8, licence compatible. Si un lot devait un jour porter
majoritairement des joueurs du top 20 — un Masters de fin d'année, un Grand
Chelem en seconde semaine — le seuil serait franchi. Ce n'est pas le régime de
ce projet, dont les cinq derniers lots sont des tableaux complets de Masters
1000.

Deux choses manqueraient en plus, et il faut les connaître avant de rouvrir :
`SvGms` n'existe pas dans `Overview`, donc **le taux de tenue et le taux de
break du §8 ne seraient pas dérivables** de cette source ; et l'`as_of` y est
celui de la dernière contribution bénévole, pas d'une publication hebdomadaire.

## §11 — Bloqué sur une clé, et sur une décision qui n'est pas la mienne

Le test des trois fixtures WTA — Blinkova – Sawangkaew, Gibson – Tagger,
Korpatsch – Joint — **n'a pas été mené**, et il ne pouvait pas l'être : aucune
clé RapidAPI n'existe dans l'environnement ni dans `.env`, dont le vocabulaire
complet est `ODDS_API_KEY` et `APIFOOTBALL_KEY`.

Souscrire, même à un plan gratuit, engage un compte et sort de ce qu'un lot de
code décide. C'est donc laissé à l'utilisateur, avec ce qu'il faut pour le faire :

- la règle de décision reste celle du brief — souscrire **si et seulement si** la
  `timeline` est complète et cohérente avec le score **sur les trois** fixtures ;
- le bloc `stats` reste un bonus descriptif et jamais une source d'agrégat, son
  taux de conversion de balles de break étant sans dénominateur ;
- les endpoints de cotes, de prédictions, de *top matches* et de *value bets*
  restent interdits d'ingestion quel que soit le plan.

**Et la conséquence du §6 déplace l'enjeu du §11** : les profils de fond devaient
rester « entièrement sur Sackmann ». Sackmann n'existe plus. Le §11 cesserait
donc d'être une couche temps réel posée sur un socle, pour devenir la **seule**
source de statistiques de service — ce que son bloc `stats` ne peut pas porter.
La reconstruction par la `timeline` deviendrait le socle lui-même, et ce n'est
pas ce qui a été évalué.

**La consigne de recherche sur les tours du tournoi en cours reste donc entière**,
et le §10 n'y touchait déjà pas.

---

## Ce que la mesure a contredit — lot 3

| Affirmé | Mesuré |
| --- | --- |
| télécharger `atp_matches_*.csv` et `wta_matches_*.csv` depuis `JeffSackmann/tennis_atp` et `tennis_wta` | **les deux dépôts n'existent plus** : 404 sur `raw` et sur l'API, quand un dépôt témoin répond 200 et que le compte existe. Un seul dépôt public subsiste |
| « la tenue de service et le taux de break sont dérivables, contrairement à ce qui a été dit » | juste sur le fond, et sans objet : la dérivation repose sur `SvGms`, une colonne du dépôt manquant. La seule source restante ne la sert pas non plus |
| le risque est que les colonnes de statistiques soient vides | **elles sont remplies à 100 %**. Ce qui manque, ce sont les matchs : médiane de 2 matchs chartés par joueur, et une couverture qui suit le rang — 709 points au top 20, 21 au-delà du 100e |
| une sélection tardive de moins de 15 min « peut être un simple retard d'import » | **2 lignes sur 52** sont sous le quart d'heure ; la médiane vaut 133 minutes et le maximum 1557. Ce cas n'existe presque pas dans cette base |
| « si le résidu croît avec le retard, la contamination est **démontrée** » | il croît, monotonement, et la direction est celle qu'un mécanisme prédit — mais trois bandes de 2, 15 et 35 lignes ne démontrent pas. La méthode du projet exige une **tendance** sur une variable ordonnée, pas des tranches comparées |
| §5b : « vérifier si le texte du prompt est conservé ; si oui, re-parser » | le texte **est** conservé, et l'information n'y est pas : 0 ligne « Paliers » par bloc sur les 742 blocs des sessions 1 à 6 et 12. La ligne est née avec la migration 033 |

Et une septième, sur la forme : le brief demandait le §6 « sans écrire une ligne
de production ». C'est ce qui a été fait — et la mesure a coûté deux heures pour
arrêter un chantier de cinq sections. C'est le meilleur rapport du lot.


---
---

# DIAGNOSTIC — lot 4 : la source tennis n'a pas disparu, et rien ne l'aurait dit

Relevé du **17/08/2026**, sur une copie de la base servie et par sondage direct
des URL amont. **La prémisse du brief est contredite** : aucune ligne tennis ne
passait par les dépôts Sackmann supprimés.

---

## §0.1 — D'où vient chaque ligne tennis (établi en 8 minutes)

**Aucune occurrence de `github` dans tout `src/`.** Le tableau est donc
entièrement en **cas A**.

| Ligne | Module | Source concrète | État sondé | Dernier succès |
| --- | --- | --- | --- | --- |
| `Elo` | `providers/tennisabstract.py` | `https://www.tennisabstract.com/reports/atp_elo_ratings.html` | **200** · 286 174 o | 17/08 08:29 |
| `Elo` (WTA) | idem | `…/wta_elo_ratings.html` | **200** · 284 080 o | 17/08 08:29 |
| `Niveau adv.` | `services/tennis_history.py` | dérivée de `tennis_elo` | table, 1105 lignes | 17/08 08:29 |
| `Forme` | `providers/tennisdata.py` | `http://www.tennis-data.co.uk/2026/2026.xlsx` | **200** · 301 307 o | 17/08 05:15 |
| `Usure` | idem | idem | **200** | 17/08 05:15 |
| `Profil` | idem | idem | **200** | 17/08 05:15 |
| `Marge` | idem | idem | **200** | 17/08 05:15 |
| `Surface` | idem | idem | **200** | 17/08 05:15 |
| `Abandons` | idem | idem | **200** | 17/08 05:15 |
| `H2H` | idem | idem | **200** | 17/08 05:15 |
| `Palmarès` | idem | idem (+ `competitions.tennisdata_tournaments`) | **200** | 17/08 05:15 |
| `Précédent` | idem | idem | **200** | 17/08 05:15 |
| `Parcours` | `services/tennis_load.py` | **nos propres scans** (`events`) | table | permanent |

Les six URL amont ont été **appelées**, pas déduites d'une constante. Les
fichiers WTA correspondants (`/2026w/2026.xlsx`, `/2025w/…`) répondent également
200.

| Grandeur | ATP | WTA |
| --- | --- | --- |
| dernier match dans `tennis_matches` | **2026-08-14** | 2026-08-13 |
| dernier téléchargement (`tennis_history_state`) | 17/08 05:15 | 17/08 05:15 |
| lignes dans le fichier amont | 1 881 | 1 826 |

**Verdict : cas A partout, rien n'est cassé.** Le retard de trois jours est le
régime normal — le fichier est hebdomadaire et publié après coup, ce que
`HISTORY_LATE_DAYS` traduit déjà.

**Ce que la mesure corrige dans la prémisse du brief.** Le gabarit attribue ces
lignes à « Tennis Abstract », ce qui est vrai pour l'Elo seul : les onze autres
viennent de **tennis-data.co.uk**, un site sans rapport avec Sackmann ni avec
GitHub. La confusion venait du dossier de projet, qui cite les trois sources dans
la même phrase.

---

## §0.2 — La garde de péremption : la panne n'a pas eu lieu, le mécanisme manquait quand même

**Cause racine : l'instrumentation datait la tentative, jamais le contenu.**
`tennis_history_state.fetched_at` avance à chaque passage du planificateur, y
compris sur un fichier figé. Une source morte **répond encore** : un dépôt
supprimé rend 404 et se voit, un fichier hebdomadaire qui cesse d'être publié
rend 200, le même classeur, indéfiniment. Septième occurrence du défaut
caractéristique du projet — une sortie identique pour l'échec et pour le cas
ordinaire.

**Migration 056**, table `source_freshness` : `source_as_of` (le dernier fait
obtenu), `checked_at` (la tentative), `moved_at` (la dernière fois que le
contenu a avancé).

- **La stagnation se mesure entre `moved_at` et `checked_at`, jamais entre deux
  exécutions consécutives.** C'est le point qui décide : le planificateur tourne
  tous les jours, donc trois relances rapprochées feraient trois comparaisons de
  moins de 48 h et la source ne stagnerait **jamais**, quel que soit son âge
  réel. Le mécanisme aurait été entièrement inopérant. Un test le garde.
- **Un recul ne compte pas comme un mouvement.** Un fichier republié amputé —
  Sackmann le pratiquait — ferait sinon repartir le compteur en perdant des
  données.
- **Le premier relevé ne conclut rien** : on ne dit pas qu'une source ne bouge
  plus quand on ne l'a vue qu'une fois.
- **Les deux circuits vivent leur vie**, et le bloc rend le **pire** des deux :
  l'un peut geler quand l'autre vit, et rendre le meilleur tairait le cas qu'on
  veut voir.

**L'escalade dans le bloc** est une fonction pure de la date collectée —
`collected` **est** déjà `source_as_of`, et relire la table ici ferait perdre la
mention sur une base neuve, pour une raison sans rapport avec la fraîcheur.

| Âge | Rendu |
| --- | --- |
| ≤ 7 j | inchangé |
| 8 à 21 j | `source non rafraichie depuis le JJ/MM` |
| > 21 j | `SOURCE FIGEE depuis le JJ/MM — Forme, Usure, Profil, Marge et Niveau adv. decrivent un etat perime…` |

Les seuils ne sont pas arbitraires : sous huit jours, deux publications
hebdomadaires n'ont pas encore été manquées, et une ligne qui crierait chaque
jour cesserait d'informer au bout d'un lot — le défaut de `A relever` sur
vingt-quatre blocs.

**`ingestion_rejects.session_id` devient facultatif**, seul changement imposé
ailleurs : une source figée ne se perd pendant aucune session, elle se constate
à la collecte. Rattacher le rejet à la dernière session en ferait un défaut de
cette session-là ; créer une seconde table de pertes aurait divergé de celle-ci.

**Trouvé en câblant, et absent du brief** : la ligne `Fraicheur` devient
dépendante de l'horloge, ce qui rend fragiles les fixtures datées. Un test de
`tennis_history` l'a montré immédiatement — sa fixture est datée du 05/07, donc
l'escalade s'y déclenche. C'est le comportement voulu, et l'assertion portait
sur une position et non sur un fait.

---

## §0.3 — Point de contrôle

Prompt réel généré sur une copie de la base servie, six matchs de tennis du
17/08 (Cincinnati) : **6 blocs, ~14 604 tokens**, les douze lignes tennis
présentes, `Historique` à « dernier match connu le 14/08, soit 3j avant
celui-ci », `Fraicheur` sans mention d'escalade — ce qui est correct à trois
jours.

`ruff` vert, **1979 tests**, `selfcheck-ingestion` **10/10**.

---

# Lot 4, phase 1 — la source manquante existait, sous un autre nom

## §1 — La clé était là, et `extra="ignore"` la jetait

**Cause en une ligne** : `RAPIDAPI_KEY` est présente dans `/home/ubuntu/myAssistantBet/.env`
sous exactement ce nom ; `Settings` ne déclarait aucun champ correspondant, et
`model_config` porte `extra="ignore"` — pydantic-settings la supprimait
silencieusement.

Les trois vérifications du brief, dans l'ordre : le nom est **bon** (`grep -i rapid`
le trouve ligne 6) ; le fichier lu est **bien** `.env` à la racine, et il est lu
**relativement au répertoire courant** — un script lancé ailleurs ne le voit pas ;
le rechargement n'était pas en cause.

C'est la huitième occurrence du défaut caractéristique du projet, et la première
sur la configuration : **une clé non déclarée est indiscernable d'une clé
absente.** `Settings.rapidapi_key` est déclarée, et `/health` porte
`rapidapi_key_present` — un booléen, jamais la valeur.

## §2 — Les trois fixtures : timeline complète et cohérente sur les trois

**Le préfixe réel est `/tennis/v2/extend/api/`**, et non `event/get/…` seul :
six appels ont été perdus à le chercher avant de le lire dans la documentation.
Cloudflare renvoie par ailleurs une **erreur 1010** sans `User-Agent` de
navigateur — même précaution que pour Tennis Abstract.

**Graphie canonique** (`/tennis/v2/profile/search/{nom}/{tour}`) : les six noms se
résolvent, et la forme est **« Prénom Nom » avec une espace** — pas le CamelCase
`DaniilMedvedev` que le brief annonçait.

| Fixture (ordre de la base) | Code | Score annoncé | Score reconstruit | Timeline | Verdict |
| --- | --- | --- | --- | --- | --- |
| Mananchaya Sawangkaew – Anna Blinkova | 200 | 6-1, 2-6, 3-6 | 6-1, 2-6, 3-6 | 24 jeux | **OK** |
| Lilli Tagger – Talia Gibson | 200 | 6-4, 1-6, 5-7 | 6-4, 1-6, 5-7 | 29 jeux | **OK** |
| Maya Joint – Tamara Korpatsch | 200 | 6-0, 3-6, 4-6 | 6-0, 3-6, 4-6 | 25 jeux | **OK** |

La reconstruction est **automatique** et non faite à l'œil : les jeux sont
retalliés depuis la séquence, les sets fermés sur les conditions standard, et la
suite comparée à l'annonce. **Zéro ligne de vocabulaire non reconnue** sur les
trois. Le compte de jeux ferme exactement (24 = 7+7+10, etc.).

**Invariant de contrôle, plus fort que la simple cohérence** : le serveur se
déduit de `holds` / `breaks`, et les jeux de service doivent **alterner**. Ils
alternent sur les trois. Tenue et break s'en dérivent :

| Joueuse | Tenue | Break |
| --- | --- | --- |
| Sawangkaew | 58,3 % (7/12) | 33,3 % (4/12) |
| Blinkova | 66,7 % (8/12) | 41,7 % (5/12) |
| Tagger | 57,1 % (8/14) | 26,7 % (4/15) |
| Gibson | 73,3 % (11/15) | 42,9 % (6/14) |
| Joint | 41,7 % (5/12) | 61,5 % (8/13) |
| Korpatsch | 38,5 % (5/13) | 58,3 % (7/12) |

**Recommandation binaire : la règle de décision est remplie sur les trois.**

Confirmé sans être redécouvert : le bloc `stats` de cet endpoint reste pauvre —
`aces`, `double_faults`, `win_1st_serve` ambigu, `break_point_conversions` en
pourcentage **sans dénominateur** — et il n'y a **pas de durée de match**.

## §2 bis — Ce que le test a trouvé et que personne ne cherchait

Les trois fixtures sont des matchs de **tableau principal** : « bas de tableau »
y désigne des joueuses peu classées, pas un tour de qualification. La couverture
des qualifications restait donc entière, et c'est en la sondant que le résultat
utile est apparu.

Deux qualifications WTA du 11/08 rendent **200 avec un `result` vide** et
`"success": true`. Ce n'est **pas** une absence de couverture : l'API date ce
match du **12/08**, pas du 11/08. Le décalage se confond avec un trou, et
`"success": true` sur un résultat vide est le défaut caractéristique du projet
**dans la source candidate** — tout collecteur écrit dessus devra traiter le vide
comme un échec nommé, jamais comme une réponse.

En vérifiant par `/tennis/v2/profile/{nom}/matches-played`, le match apparaît —
**avec un bloc de statistiques que rien n'annonçait** :

| Indicateur du §8 (lot 3) | Colonne Sackmann | Champ `matches-played` |
| --- | --- | --- |
| % 1re balle | `1stIn` / `svpt` | `firstServe` / `firstServeOf` |
| % points gagnés sur 1re | `1stWon` / `1stIn` | `winningOnFirstServe` / `winningOnFirstServeOf` |
| % points gagnés sur 2e | `2ndWon` / (`svpt` − `1stIn`) | `winningOnSecondServe` / `winningOnSecondServeOf` |
| Taux d'aces | `ace` / `svpt` | `aces` / `firstServeOf` |
| Doubles fautes | `df` / 2es balles | `doubleFaults` / `winningOnSecondServeOf` |
| % BP converties | dérivée de l'adversaire | `breakPointsConverted` / `breakPointsConvertedOf` |

**Tous avec leur dénominateur**, ce qui est exactement ce qui manquait au bloc
`stats`. Cohérence interne vérifiée sur Charaeva – Marino : 60 + 53 points de
service pour 63 + 50 points gagnés au total, et `winningOnSecondServeOf` =
`firstServeOf` − `firstServe` sur les deux joueuses.

**Couverture mesurée**, sur un échantillon étalé sur le classement — sept joueurs
des cinq derniers lots, **une seule page de dix matchs** chacun :

| Joueur | Rang | Circuit | Matchs avec stats | Points de service | Seuil 400 |
| --- | ---: | --- | ---: | ---: | --- |
| Aryna Sabalenka | 1 | WTA | 10 | 764 | **oui** |
| Viktorija Golubic | 51 | WTA | 10 | 828 | **oui** |
| Simona Waltert | 90 | WTA | 10 | 680 | **oui** |
| McCartney Kessler | — | WTA | 10 | 667 | **oui** |
| Alexander Zverev | 3 | ATP | 10 | 991 | **oui** |
| Kamil Majchrzak | 67 | ATP | 10 | 818 | **oui** |
| Jan-Lennard Struff | — | ATP | 10 | 1125 | **oui** |

**Sept sur sept**, et ce sont des **planchers** : dix matchs sont une page, pas
une fenêtre de 52 semaines. À comparer au Match Charting Project, mesuré au lot 3
sur la même population : premier quartile à **0** point côté ATP et **19,5** côté
WTA.

**Un piège rencontré et à retenir** : l'API écrit « Mccartney Kessler » quand la
base écrit « McCartney Kessler ». Une comparaison stricte a d'abord rendu
« 0 point de service » — un faux négatif de **mon** rapprochement, pas de la
source. Tout collecteur devra replier casse et accents, comme `labels.sort_key`.

## §3a — Les feuilles officielles : citation, sans interprétation

**`atptour.com/robots.txt`**, cité mot pour mot :

```
User-agent: *
Content-Signal: search=yes,ai-train=no,use=reference
```
```
User-agent: ClaudeBot
Disallow: /
```

et, en tête de fichier : *« ANY RESTRICTIONS EXPRESSED VIA CONTENT SIGNALS ARE
EXPRESS RESERVATIONS OF RIGHTS UNDER ARTICLE 4 OF THE EUROPEAN UNION DIRECTIVE
2019/790 »*.

**`wtatennis.com/robots.txt`** :

```
User-agent: *
Disallow:

Sitemap: https://www.wtatennis.com/sitemap/index.xml
```

**Conditions d'utilisation WTA**, citées : *« use automated means to access the
WTA Sites »* et *« "harvest" (or collect) information from the WTA Sites using an
automated software tool or manually on a mass basis »*, avec cette réserve —
*« This prohibition does not apply to search engines accessing the WTA Sites
solely for web indexing purposes. »*

**Forme** : la page `wtatennis.com/scores` n'est pas rendue côté serveur ; elle
s'alimente à `https://api.wtatennis.com`, une API interne du site.

**Ce qui relève du constat et non de l'interprétation** : le dossier de projet
porte déjà deux précédents applicables — `atptour.com` est refusé nommément et
« doit le rester » ; et Transfermarkt a été écarté sur exactement la
configuration WTA — `robots.txt` permissif, conditions d'utilisation
prohibitives — au motif que « ce sont les conditions d'utilisation qui
gouvernent un client automatisé ». L'arbitrage reste à l'utilisateur.

## §3c — Tableau comparé et recommandation

| | Feuilles officielles (§3a) | `tennis-api.com` (§2/§3b) |
| --- | --- | --- |
| Couverture ATP | robots.txt nomme `ClaudeBot`, `Disallow: /` | mesurée : 3 joueurs sur 3, 818 à 1125 pts |
| Couverture WTA | robots permissif, CGU prohibitives | mesurée : 4 sur 4, 667 à 828 pts |
| Qualifications | non établie | **couvertes** (Charaeva, rang 323) |
| % 1re balle | oui | **oui** — `firstServe`/`firstServeOf` |
| Tenue / break | oui | **oui** — deux chemins, timeline et BP |
| Durée du match | oui | **non** |
| Niveau de source | 1 | 4 |
| Fragilité | refonte de page ; API interne non documentée | contrat RapidAPI, versionné `/v2/` |
| Entretien | un analyseur par site, deux sites | un client HTTP, une clé |
| Coût | nul | plan gratuit testé ; volume à vérifier |

**Recommandation unique : `tennis-api.com`, et ne rien construire aujourd'hui.**

Elle est la seule des deux à franchir le seuil de couverture sur les lots réels,
et la seule dont l'accès ne demande aucun arbitrage juridique. Son défaut est son
**niveau de source 4** — le gabarit y plafonne la confiance à 2 — mais c'est le
niveau qui convient à un **profil de fond** : ces taux ne portent pas une
sélection, ils la contextualisent, exactement comme `Profil` et `Marge`
aujourd'hui.

**Trois réserves à lever avant de construire**, et aucune ne l'a été ici :

1. la mesure porte sur **sept joueurs et une page** chacun. Il faut la refaire
   sur les 196 joueurs des cinq derniers lots, avec pagination, avant d'engager
   quoi que ce soit — c'est la règle du lot 3 et elle n'est pas satisfaite ;
2. le **volume d'appels** du plan gratuit n'est pas connu. Un profil par joueur
   et par lot, c'est de l'ordre de 40 appels par session ;
3. `"success": true` sur un `result` vide impose de traiter le vide comme un
   échec nommé — dans `ingestion_rejects` — dès la première ligne de collecteur.

**Repli partiel mentionné pour mémoire** : `Tennismylife/TML-Database` est ATP
seulement, quand les lots sont majoritairement WTA. Non évalué, conformément au
brief.

## §4 — Spécification du test de tendance sur le retard (non implémenté)

**Modèle.** Régression logistique à un paramètre libre, avec offset :

```
logit( P(gagné) )  =  offset( logit(1/cote) )  +  a  +  b · log(retard_minutes)
```

- **Variable expliquée** : `picks.result ∈ {win, loss}` recodée 0/1. Les `void`
  et `pending` sont hors population, comme partout.
- **Variable explicative** : `log(picks.late_minutes)`. Le logarithme et non les
  minutes brutes — la distribution va de 12 à 1557 minutes, médiane 133, donc
  fortement asymétrique ; une pente linéaire y serait dictée par les trois
  valeurs extrêmes. C'est aussi la forme déjà retenue pour le gradient de cote.
- **Offset** : `logit(1/price)`, coefficient **fixé à 1** et non estimé. C'est ce
  qui fait répondre à la question posée — *le retard apporte-t-il de
  l'information au-delà de ce que le prix contient déjà* — plutôt qu'à « les
  paris tardifs gagnent-ils plus », à laquelle un taux brut répond déjà et sans
  intérêt. `1/cote` porte la marge du book, donc l'offset est **conservateur**.
- **Population** : les 52 sélections tardives tranchées. Aucune sélection
  principale n'y entre — elles n'ont pas de retard, et leur en attribuer un de
  zéro créerait un point de masse à `log(0)`.
- **Effectif et puissance** : 52 observations, un paramètre libre. Pour un test
  bilatéral à 5 %, 80 % de puissance demandent un `b` d'environ **0,45 par unité
  de log-minute** — soit un écart de ~11 points de probabilité entre un retard de
  15 minutes et un de 150. L'effet suggéré par les bandes (−0,193 → +0,126 sur
  l'échelle du résidu par sélection) est **du même ordre**, ce qui place ce test
  à la limite de sa puissance : il peut conclure, il peut aussi ne rien voir sans
  que ce soit une absence d'effet.

**Ce que chaque résultat autoriserait à conclure :**

| Résultat | Conclusion autorisée |
| --- | --- |
| `b > 0`, `p < 0,05` | La contamination est **établie** : le retard porte de l'information que le prix n'a pas, et elle croît avec lui. La population tardive cesse d'être comparable et la garde d'écriture peut être resserrée. |
| `b > 0`, `p ≥ 0,05` | Rien. La direction est celle attendue, l'effectif ne tranche pas — c'est l'état actuel, et il faut le dire ainsi plutôt que « pas d'effet ». |
| `b ≈ 0`, intervalle **serré** | La population tardive est un **artefact d'import** : elle peut être traitée comme la principale, et la stratification en bandes se retire. |
| `b ≈ 0`, intervalle **large** | Non concluant, et c'est le cas le plus probable à 52 observations. À ne pas lire comme une équivalence — celle-ci se conclut par un TOST, pas par un test qui échoue à rejeter. |
| `b < 0` | Contredirait le mécanisme. À ne pas retenir sans réplication : la direction n'était pas prédite, donc ce serait un résultat exploratoire. |

**Bilatéral**, et c'est une condition : la direction a été **vue dans les bandes
avant** d'être testée. Prendre l'unilatéral reviendrait à diviser le seuil par
deux après avoir regardé — la faute que cette page a mis huit lots à corriger.

**Ce que le test ne dirait pas** : rien sur la population principale, et rien sur
la cause. Un retard corrélé au résultat peut venir d'une information acquise
pendant le match, ou d'un biais de saisie — une sélection perdue se saisit
peut-être moins vite. Le modèle ne les sépare pas.

## §5 — Dettes

**§5a — Horloge injectable : NON FAIT, et volontairement.** `freshness.note_for`
est appelée depuis `tennis_history.freshness_line`, donc depuis
`session.context_block`, donc depuis `build_prompt` : c'est **le chemin de
génération de prompt**, que la contrainte d'exploitation interdit de toucher
pendant qu'une session tourne. Le point est prêt à être fait — `note_for` et
`state` prennent déjà un `now` optionnel, il reste à le faire descendre depuis
`build_prompt` — et il doit l'être hors fenêtre de session.

**§5b — Registre : aucun chemin à ajouter, vérifié.** `freshness.record` écrit
dans `source_freshness`, qui n'est pas une table gardée : ce n'est pas une
prédiction, c'est un témoin de collecte — même statut que `ingestion_rejects` et
`imports_raw`. Le test du registre passe sans modification.

**§5c — `changelog_mesure` : fait**, migration 057. Deux lignes pour la garde de
péremption, une par portée : elle change ce que le modèle lit (`gabarit`) **et**
ce qui entre en base (`ingestion`), et les deux effets se découpent séparément.

**§5d — Population exploratoire : NON VÉRIFIABLE À CETTE HEURE.** La session est
importée à partir de 16h et il est 15h30. La vérification à faire **après**
l'import, et à ne pas supposer : `SELECT COUNT(*) FROM picks WHERE exploratoire = 1`
doit être non nul, et la page `/stats` doit porter le bloc « Sélections
exploratoires ». Si le compte reste à zéro, `sections.survey()` dira laquelle des
deux causes s'applique — section jamais demandée, ou demandée et non collée.

**§5e — Coût du gabarit : mesuré, non enregistré.** L'enregistrer demande
d'instrumenter `save_prompt`, donc le chemin de génération — même blocage que
§5a. La mesure, elle, se fait en lecture seule sur les 142 prompts archivés :

```
ajustement global : coût ≈ 8 107 fixe + 344 par bloc
```

Et la dérive, par jour d'analyse :

| Jour | Prompts | Coût fixe | Coût par bloc |
| --- | ---: | ---: | ---: |
| 04/08 | 16 | 853 | 145 |
| 06/08 | 17 | 5 512 | 174 |
| 09/08 | 16 | 7 570 | 270 |
| 12/08 | 10 | 7 477 | 665 |
| 13/08 | 10 | 6 942 | 1 019 |
| 14/08 | 16 | 9 881 | 754 |
| 15/08 | 19 | 11 934 | 698 |

**Le coût fixe a été multiplié par quatorze et le coût par bloc par cinq en onze
jours.** Le budget de recherche, lui, est resté à sept dossiers. Ce n'est pas
encore un problème — les deux plafonds de tokens sont des alarmes et ne voient
jamais un lot réel — mais c'est la grandeur à surveiller, et elle est désormais
chiffrée.

Réserve de lecture : ces ajustements sont faits **par jour**, sur 4 à 19 prompts
dont la taille de lot varie peu. Le 10/08 rend une pente négative (−127), ce qui
n'a pas de sens physique et dit seulement que ce jour-là les lots étaient trop
homogènes pour identifier une pente. Le chiffre à retenir est l'ajustement
global, pas la colonne jour par jour.

---

# DIAGNOSTIC — lot 5 : les statistiques de service, le budget, et les dettes

Relevé du **17/08/2026**, sur une copie de la base servie et par sondage direct
de `tennis-api.com`. Deux prémisses du brief sont contredites dès les préalables.

---

## §0.1 — La population exploratoire : la porte n'a pas eu l'occasion de s'ouvrir

**Réponse en une ligne, avant tout le reste : elle est encore à zéro, et ce
n'est pas un extracteur muet — aucune session n'a été importée depuis la mise en
service de C-bis.**

La condition posée par le brief — « si elle est encore à zéro *alors qu'une
session a bien été importée* » — n'est pas remplie. Le point ne passe donc pas
devant le reste, et la chronologie le dit sans ambiguïté :

| Instant | Fait |
| --- | --- |
| 08:02–08:04 | 8 sélections importées, **session 14**, sous le code de la veille |
| **13:45:20** | migrations 050 à 057 appliquées — l'application redémarre |
| 13:47:49 | prompt de la session 15 généré, **il porte bien C-bis** (2 occurrences) |
| — | **session 15 : 0 sélection**, la réponse n'a pas encore été collée |

**Le témoin décisif est `imports_raw`, et il est vide.** Cette table s'écrit
*avant* toute tentative de lecture — c'est tout son objet, livré au lot 2. Si un
collage avait eu lieu et que l'extracteur avait été muet, il y aurait une ligne
ici et zéro sélection exploratoire. Il n'y a ni l'un ni l'autre : rien n'a été
collé.

`ingestion_rejects` est vide pour la même raison. La mesure de C-bis commence au
premier import postérieur à 13:45 aujourd'hui, et pas avant.

---

## §0.2 — Le quota PRO : mesuré dans les en-têtes, et le brief le surestime d'un ordre de grandeur

**Les en-têtes font foi, et ils suffisent** — la page de tarification n'a pas été
nécessaire, ce que le brief demandait de confronter est directement servi :

```
x-ratelimit-requests-limit:     150000
x-ratelimit-requests-remaining: 149999
x-ratelimit-requests-reset:     2677884   (= 30,99 jours)
```

Le quota est donc de **150 000 appels par mois**, et il est intact — le plan
vient d'être souscrit.

**Aucun en-tête de débit n'existe**, ni `x-ratelimit-rate-limit` ni
`retry-after`. Dix appels consécutifs passent en **2,25 s (4,4 req/s)** sans un
seul 429. Le débit n'est donc pas borné par le fournisseur : `RAPIDAPI_INTERVAL`
est une politesse de notre côté sur une reprise longue, et le code le dit ainsi
plutôt que de la présenter comme une limite relevée.

**Une réponse servie par le cache de RapidAPI consomme quand même.** Les dix
appels identiques portaient `x-cached: HIT` et ont fait descendre le compteur de
dix. Il n'y a pas de repli gratuit à espérer d'une répétition, et `_account`
compte donc **un crédit par appel, quoi qu'il rende** — c'est la différence avec
The Odds API, qui facture au marché servi.

### La prémisse du dimensionnement est fausse, et d'un facteur dix

> « Une reprise complète de l'historique sur ~196 joueurs, à 52 semaines et
> **10 matchs par page**, se compte en centaines d'appels. »

`pageSize` est un paramètre accepté, et il monte à **200** :

| `pageSize` demandé | Lignes rendues | Fenêtre couverte |
| ---: | ---: | --- |
| 10 (défaut) | 10 | 2026-06-20 → 2026-08-16 |
| 100 | 100 | 2025-06-12 → 2026-08-16 |
| 200 | 200 | 2024-04-30 → 2026-08-16 |
| 500 | **200** | plafonné silencieusement |
| 1000 | **200** | plafonné silencieusement |

À `pageSize=100`, **une seule page couvre plus de 52 semaines pour tous les
joueurs sondés** — un joueur joue 60 à 81 matchs par an, cent lignes remontent à
quatorze à dix-neuf mois :

| Joueur | Total historique | Matchs sur 52 sem. | Avec stats | Points de service | Page 1 remonte à |
| --- | ---: | ---: | ---: | ---: | --- |
| Alexander Zverev | 975 | 81 | 78 | **6 129** | 2025-06-12 |
| Aryna Sabalenka | 739 | 62 | 61 | **4 233** | 2025-03-24 |
| Simona Waltert | 561 | 76 | 74 | **5 217** | 2025-04-19 |
| Alex Michelsen | 311 | 60 | 60 | **4 609** | 2025-01-16 |

`PAGE_SIZE` vaut donc 100 et non 200 : le double du volume transféré
n'ajouterait pas une ligne à la fenêtre utile.

**Michelsen est le cas qui tranche.** C'est lui qui avait fait échouer le Match
Charting Project au lot 3 — **65** points de service, dans la même affiche qu'un
Fritz à 744. Ici il en porte **4 609**. Le mode de défaillance qui a fermé le
substitut précédent — la couverture qui s'effondre hors du top 20 — n'existe pas
sur cette source.

### Le plancher dur

`RAPIDAPI_CALL_FLOOR`, **20 000**, soit 13 % du quota mensuel.

**Ce plancher ne ressemble à aucun des deux autres, et le code le dit.**
`ODDS_API_CREDIT_FLOOR` et `APIFOOTBALL_CALL_FLOOR` gardent des quotas
*journaliers* : un plancher franchi se rouvre tout seul le lendemain, et le coût
d'une erreur est une journée. Celui-ci garde un quota *mensuel*. Une reprise qui
l'épuiserait le 8 laisserait l'application sans données de service jusqu'au
renouvellement — d'où le message, qui écrit « le quota est mensuel : il se rouvre
au renouvellement, pas demain », et un test qui le vérifie.

Deuxième différence, voulue : **on s'arrête, on ne dégrade pas.** Le dossier
d'équipe se suspend en laissant passer le contexte, parce que le contexte est la
fonction première. Ici il n'y a rien à laisser passer : ces lignes sont un profil
de fond, et un profil de fond incomplet est exactement ce que le lot 3 a refusé.

### Le dimensionnement

| Poste | Appels | Détail |
| --- | ---: | --- |
| Résolution d'identité, reprise | 176 | un `profile/search` par joueur, **puis cache définitif** |
| Table de service, reprise | ~180 | un `matches-played` par joueur, +quelques 2ᵉ pages |
| **Reprise, sous-total** | **~360** | |
| Timelines 52 sem., reprise | ~6 200 | un `event/get` par match distinct (~70 matchs/joueur ÷ 2 camps) |
| **Reprise complète** | **~6 600** | soit **4,4 %** du quota mensuel |
| Entretien : joueurs d'un lot | ~35/jour | un lot tennis porte 35 joueurs en moyenne |
| Entretien : timelines du jour | ~18/jour | les matchs joués depuis la passe précédente |
| **Entretien quotidien** | **~55/jour** | soit **~1 700/mois** |

**Marge restante après une reprise complète et un mois d'entretien : environ
141 700 appels, soit 94 % du quota.** Le plancher de 20 000 laisse de quoi tenir
plus d'un an d'entretien au régime mesuré.

C'est aussi ce qui autorise la mesure de couverture du §6 sans arbitrage : elle
coûte l'équivalent d'une reprise d'identité et de table, ~360 appels, et il n'y
avait donc aucune raison de l'échantillonner.

---

## §6 — La couverture réelle : les deux circuits passent, et de loin

Mesure du **17/08/2026 sur les 176 joueurs des cinq derniers lots tennis**,
pagination complète, par le **vrai chemin de code** — `resolve`,
`parse_matches_played`, `aggregate` — donc elle mesure ce que l'application
produira, pas ce qu'une sonde ad hoc produirait. Base temporaire : rien n'a été
écrit en production.

**Coût : 355 appels**, soit deux par joueur (une recherche, un profil) plus trois
replis. Le dimensionnement du §0.2 tombe exactement juste.

### Résolution d'identité : 174 sur 176

| Niveau de repli | Joueurs |
| --- | ---: |
| `exact` | 168 |
| `casse` | 3 |
| `accents` (typographie, tirets compris) | 3 |
| **non résolus** | **2** |

`accents` reste très minoritaire, ce qui est le signal recherché : le brief
demandait de journaliser les niveaux parce que *« si le repli accents devient
majoritaire, la normalisation en amont est mauvaise »*. Il ne l'est pas.

**Les deux non résolus sont nommés, et leurs causes diffèrent** :

- **JJ Wolf** — la source le connaît sous `J J Wolf`, et **ce profil porte zéro
  match**. Aucune graphie servie n'existe : c'est une absence réelle, pas un
  défaut de rapprochement ;
- **Leylah Fernandez** — cas ambigu et assumé. La recherche sur le nom complet
  rend le seul `Leylah Fernandez`, profil vide ; la recherche sur `Fernandez`
  rend **94** candidats, dont **deux** portent tous nos mots (`Leylah Fernandez`
  et `Leylah Annie Fernandez`, 452 matchs). Départager sans mesure serait
  deviner, et il n'existe ici aucune résolution manuelle. Elle part en
  `ingestion_rejects` sous `match_ref_unresolved` — un manque nommé et visible
  vaut mieux qu'une attribution silencieuse.

### Points de service, ATP et WTA séparés

| Circuit | Fenêtre | Médiane | **Q1** | ≥ 400 pts |
| --- | --- | ---: | ---: | --- |
| ATP (88) | 52 sem., toutes surfaces | 4 648 | **3 744** | **88 / 88** (100 %) |
| ATP | 52 sem., dur | 1 870 | **1 373** | 87 / 88 (99 %) |
| WTA (86) | 52 sem., toutes surfaces | 4 056 | **3 346** | **86 / 86** (100 %) |
| WTA | 52 sem., dur | 2 305 | **1 699** | 85 / 86 (99 %) |

### Ventilation par tranche de classement

C'est le tableau qui tranche, parce que **c'est exactement là que le Match
Charting Project s'était effondré** — le lot 3 avait mesuré une médiane de 709
points dans le top 20 et de 21 au-delà du 101ᵉ rang.

| Rang | ATP n | ATP médiane | ATP Q1 | WTA n | WTA médiane | WTA Q1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 – 20 | 13 | 4 926 | 4 315 | 13 | 4 214 | 3 756 |
| 21 – 50 | 25 | 4 613 | 3 724 | 20 | 3 662 | 3 310 |
| 51 – 100 | 37 | 4 934 | 3 744 | 38 | 4 200 | 3 552 |
| 101 et au-delà | 13 | 3 768 | 1 451 | 15 | 3 980 | 2 745 |

**Toutes les tranches sont à 100 % au-dessus du seuil**, y compris au-delà du
101ᵉ rang. La couverture ne décroît pas avec le classement — c'est la propriété
exacte qui manquait au substitut précédent, et elle est ici mesurée sur
l'intégralité de la population, pas sur un échantillon.

### La branche retenue

> « Si le premier quartile est sous 400 sur un circuit, les blocs de ce circuit
> écrivent `données de service non disponibles`. »

**Elle ne se déclenche sur aucun des deux circuits.** Le Q1 le plus bas mesuré
est **1 373** (ATP, sur dur), soit **3,4 fois le seuil** ; sur la fenêtre 52
semaines toutes surfaces il vaut 3 744 et 3 346. Les lignes de service sont donc
servies sur les deux circuits.

Le seuil garde son rôle **par joueur** : il n'est pas devenu décoratif, il ne
mord simplement pas au niveau du circuit. C'est le repli surface → toutes
surfaces qui travaillera en pratique, l'écart entre les deux colonnes du tableau
ci-dessus étant d'un facteur 2 à 3.

### Deux mesures qui confirment le §0.2

- **Aucun joueur n'atteint 95 matchs sur la page 1** : une page de 100 couvre
  bien les 52 semaines pour l'intégralité des 174, et la seconde page ne sert
  jamais. La prémisse « des centaines d'appels » du brief est fausse d'un facteur
  dix, et la mesure complète le confirme après le sondage.
- **355 appels** pour la population entière, soit **0,24 %** du quota mensuel.

---

## §14 — L'écart vient de la croissance du gabarit, et la régression est le mauvais outil

Le brief demande de trancher : l'écart entre `8 107 fixe + 344 par bloc` et les
**~12 540** impliqués par la session du 17/08 vient-il de la croissance récente
du gabarit, ou d'un défaut de la régression ?

**Les deux, et dans cet ordre.** Refait sur la copie, l'ajustement global rend
`8 020 + 350` — le chiffre du lot 4 se reproduit exactement, il n'y a donc pas
d'erreur de calcul. Mais il ne décrit rien :

| Jour | Prompts | Coût fixe ajusté | Coût par bloc |
| --- | ---: | ---: | ---: |
| 04/08 | 16 | 853 | 145 |
| 06/08 | 17 | 5 512 | 174 |
| 09/08 | 16 | 7 570 | 270 |
| 12/08 | 10 | 7 477 | 665 |
| 13/08 | 10 | 6 942 | **1 019** |
| 14/08 | 17 | 8 088 | 868 |
| 15/08 | 19 | **11 934** | 698 |

L'ajustement porte sur **onze jours d'un gabarit qui grossit tous les jours**, et
la taille des lots est corrélée à la date : la pente absorbe donc la croissance
du coût fixe. Un couple de nombres unique sur cette période ne décrit **aucun**
des trois régimes qu'il mélange.

Ajusté sur les **quinze derniers prompts**, il rend `10 303 + 866`, ce qui prédit
**14 633** tokens pour la session du 17/08 — 5 blocs — contre **14 675**
observés. **0,3 % d'écart.** L'écart du brief vient donc de la croissance, et le
« défaut de la régression » est qu'elle ne devrait pas exister.

### Ce qui remplace la régression

**Il n'y a rien à ajuster.** Un prompt est un préambule suivi de N blocs, et la
frontière est un en-tête `### M1` — le découpage se **mesure**. Une régression
sur une donnée dont on tient la décomposition exacte est de la mécanique pour
rien, et elle se trompe.

Trois colonnes s'écrivent donc **à la génération**, seul moment où elles ne
coûtent rien : `blocks`, `fixed_tokens`, `block_tokens`. Le lot 4 avait mesuré en
lecture seule faute de pouvoir toucher ce chemin ; la contrainte est levée.

Les 149 prompts archivés sont **rétro-remplis, et c'est sûr ici** — contrairement
au cran calculé ou à la source d'un prix, rien n'est reconstitué : le corps est
en base depuis toujours et porte ses propres en-têtes. Le découpage d'un prompt
du 04/08 se refait exactement comme celui d'aujourd'hui.

La série vit sur `/stats` et dans l'export, **par jour d'analyse** — une session
en génère jusqu'à vingt, tous rendus par le même gabarit à quelques minutes
d'écart, et vingt points identiques ne dessinent pas une courbe. Le coût du cadre
est une **médiane** : une moyenne suivrait un prompt aberrant, et c'est justement
l'aberration qu'on veut voir apparaître comme un point et non comme une pente.

### La borne haute, trouvée sur le relevé et non en écrivant le code

Le premier jet comptait les sections de sortie et le chapitre « COMMENT LIRE LES
BLOCS » **avec les blocs**, en les jugeant « une imprécision connue et bornée ».
Le relevé réel dit qu'elle ne l'est pas : le coût par bloc du 17/08 sortait à
**2 238** tokens contre ~1 400 les jours précédents, et l'inflation venait
entièrement de ce chapitre versé dans le dernier bloc — **d'autant plus forte que
le lot est court**, puisqu'il se divise alors par moins de blocs.

Une mesure de dérive qui bouge avec la taille du lot ne mesure pas la dérive. Ce
qui se paie une fois par prompt appartient au cadre, où qu'il se trouve dans le
texte. Un test le garde : un prompt d'un bloc et un prompt de dix, même chapitre,
doivent rendre le **même** coût par bloc.

La borne est écrite **une fois** et partagée avec le relevé du §15 : deux
écritures auraient divergé au premier changement d'en-tête, et l'une serait
devenue fausse en silence.

### La série corrigée, sur les 149 prompts

| Jour | Prompts | Blocs | Cadre | / bloc |
| --- | ---: | ---: | ---: | ---: |
| 04/08 | 16 | 138 | 1 571 | 62 |
| 05/08 | 16 | 92 | 3 089 | 97 |
| 06/08 | 20 | 324 | 4 107 | 240 |
| 08/08 | 7 | 54 | 6 481 | 418 |
| 10/08 | 6 | 23 | 8 048 | 575 |
| 12/08 | 10 | 75 | 8 162 | 573 |
| 13/08 | 10 | 119 | 10 247 | 740 |
| 15/08 | 19 | 215 | 12 160 | 677 |
| 16/08 | 2 | 30 | 16 967 | 691 |
| 17/08 | 1 | 5 | **11 472** | **640** |

**Le cadre a été multiplié par 7,7 et le coût par bloc par 11 en onze jours**, et
la série est désormais **monotone** — ce que l'ajustement jour par jour n'était
pas, avec sa pente négative du 10/08. Contrôle : 11 472 + 5 × 640 = **14 672**
contre 14 675 mesurés sur la session du 17/08.

C'est la grandeur à surveiller, et ce lot y ajoute encore quatre lignes par bloc
tennis le jour où le drapeau se lèvera.

---

## §15 — Cas décrits par le gabarit et jamais rencontrés

Relevé sur les **1 405 blocs** des 149 prompts archivés — ce que les sessions ont
réellement vu, et non un rendu recalculé aujourd'hui. **Aucune suppression** :
c'est un constat pour arbitrage.

| Cas décrit | Blocs | Part | Prompts | Sessions | Poids |
| --- | ---: | ---: | ---: | ---: | --- |
| Non servis | 1 042 | 74,2 % | 115 | 13 | moyen |
| Absents — non interrogés | 292 | 20,8 % | 29 | 5 | fort |
| A relever | 253 | 18,0 % | 47 | 9 | fort |
| Lieu — terrain neutre non vérifiable | 188 | 13,4 % | 29 | 5 | moyen |
| Tour — phase non renseignée | 159 | 11,3 % | 24 | 6 | moyen |
| Absents — aucun signalé | 153 | 10,9 % | 19 | 6 | faible |
| Effectif — absents reconstruits | 132 | 9,4 % | 29 | 7 | fort |
| Entraîneur — divergence | 67 | 4,8 % | 19 | 4 | fort |
| Entraîneur — apparié sur l'initiale | 42 | 3,0 % | 18 | 3 | moyen |
| Compos — onze publié | 9 | 0,6 % | 3 | 3 | fort |
| Météo — alerte officielle | 4 | 0,3 % | 1 | 1 | **décisif** |
| Statut — reporté, annulé, forfait | 1 | 0,1 % | 1 | 1 | **décisif** |
| **Absents — source injoignable** | **0** | 0 % | 0 | 0 | moyen |
| **Lieu — TERRAIN NEUTRE** | **0** | 0 % | 0 | 0 | **décisif** |
| **Alerte — handicap suspect** | **0** | 0 % | 0 | 0 | **décisif** |

### Les trois jamais rencontrés, et ce qu'ils valent

**Aucun ne doit être supprimé sur la foi de ce tableau**, et deux d'entre eux
sont même une confirmation :

- **`Alerte` — handicap suspect, 0 sur 1 405.** Le dossier de projet l'annonçait
  en toutes lettres : *« elle ne coûte rien quand tout va bien… cette ligne est
  faite pour ne jamais servir »*. Le mode d'emploi ne se paie déjà que sur les
  lots qui en portent une (`handicap_alerts`), donc son coût fixe est **nul**.
  Rien à arbitrer ;
- **`Lieu` — TERRAIN NEUTRE, 0 sur 1 405.** Ce n'est pas un cas rare, c'est un
  cas **structurellement hors de portée**, et la cause est déjà mesurée :
  `fixture.venue.id` est nul sur 210 matchs sur 210 d'une saison de Conference
  League. Le drapeau ne peut donc pas se calculer là où les délocalisations
  arrivent, et c'est la mention « non vérifiable » qui prend le relais — **188
  blocs, 13,4 %**. Les deux lignes forment un seul mécanisme dont une moitié
  porte tout le trafic ;
- **`Absents` — source injoignable, 0 sur 1 405.** Le seul des trois qui soit un
  vrai résultat sur le fournisseur : sur cinq sessions de football, API-Football
  n'a **jamais** été injoignable au moment d'un relevé d'absents. L'état reste
  utile — il distingue une panne d'une absence de couverture, et les confondre
  reproduirait le défaut que les trois états existent pour supprimer.

### Deux cas décisifs à un chiffre, et c'est le vrai enseignement

`Statut` (1 bloc) et `Météo — alerte` (4 blocs) sont les deux plus rares du
tableau **et** les deux que le dossier de projet cite comme ayant changé une
analyse : le Rakow – Zaglebie reporté depuis neuf jours, et les deux sessions où
l'alerte disait que la rencontre pouvait ne pas se jouer.

**Un cas à 0,1 % qui retourne une lecture ne se compare pas à un cas à 0,1 % qui
n'apprend rien**, et c'est pourquoi le relevé porte une colonne « poids » que le
compte ne produit pas. Sans elle, ce tableau se lirait comme une liste de
coupes.

### Deux erreurs de mesure, trouvées et corrigées avant d'écrire

Elles valent d'être notées, parce que toutes deux **produisaient un compte
plausible** :

1. **Le découpage sans borne haute.** Les sections de sortie et le chapitre
   « COMMENT LIRE LES BLOCS » viennent *après* les blocs et tombaient donc dans
   le dernier. Trois cas sortaient à exactement 29 blocs sur 29 prompts — la
   signature d'un marqueur capté une fois par prompt, donc hors bloc ;
2. **Les marqueurs pris dans le mode d'emploi.** Le bloc écrit « aucun signale »
   **sans accent** — règle du module, *« ni apostrophe ni accent dans une valeur
   rendue »* — quand le chapitre écrit « aucun absent signalé ». Le marqueur
   accentué ne trouvait que le manuel, donc **zéro** une fois le préambule
   exclu, sur un cas qui arrive **153 fois**.

---

## §7 — Synchronisation : deux régimes, une seule fonction

- **Reprise** : tout le catalogue, une passe, **reprenable**. Un agrégat écrit
  dans les 24 h n'est pas redemandé, donc une interruption ne coûte que ce qui
  restait. Coût mesuré au §6 : **355 appels** pour 176 joueurs.
- **Entretien** : les joueurs des matchs **à venir** seulement. Un lot tennis en
  porte ~35, contre 180 pour le catalogue entier.

**Le plancher se vérifie avant chaque joueur**, jamais une fois au départ : un
contrôle unique laisserait une reprise franchir le plancher en cours de route et
le découvrir à la fin — trop tard, le quota étant mensuel. `SyncReport.stopped`
distingue une passe arrêtée d'une passe complète.

La garde de péremption du lot 4 s'applique : le dernier match obtenu date la
source, par circuit, et une stagnation de plus de 48 h écrit `source_figee`.

**Registre des chemins d'écriture : rien à déclarer, vérifié.** `player_alias`,
`player_serve_agg` et `api_responses` ne sont pas des tables gardées — ce sont
des témoins de collecte, même statut que `source_freshness` et `imports_raw`, et
non des prédictions. `tests/test_write_paths.py` passe sans modification et
`selfcheck-ingestion` rend **10 sur 10**.

---

## Ce que la mesure a contredit — lot 5

C'est la partie du rapport qui a le plus servi au lot 4, et elle est de nouveau
fournie. **Sept affirmations**, dont deux du brief lui-même et trois de mes
propres conclusions en cours de route.

| Ce qui était affirmé | Ce que la mesure dit |
| --- | --- |
| « la reprise se compte en **centaines d'appels**, à 10 matchs par page » | `pageSize` monte à 200 ; **une page de 100 couvre 52 semaines pour les 174 joueurs résolus**. La reprise coûte 355 appels, soit 2 par joueur |
| repli d'identité : « exact, casse, accents, **puis recherche via `Players`** » | la recherche **est** le mécanisme, pas le dernier recours ; et le repli d'accents doit se faire **sur l'entrée** — l'endpoint est insensible à la casse et pas aux accents |
| le seuil de 400 points pourrait fermer un circuit | Q1 à **3 744** (ATP) et **3 346** (WTA), 100 % au-dessus du seuil dans **toutes** les tranches de classement |
| l'`as_of` des lignes de service passe par l'horloge injectable | il n'est **comparé à rien** : il est rendu, et c'est le lecteur qui soustrait. Un `now` y serait du code mort |
| **(la mienne)** `event/get` rend vide sur 8 rencontres sur 8 | artefact de ma sonde : lancée hors de la racine, donc **sans `.env`, donc sans clé** — le piège que le lot 4 avait documenté. Le relevé refait donne 5 sur 8 |
| **(la mienne)** la famille d'appel se dérive du chemin | le segment variable n'est pas au même rang d'un endpoint à l'autre : « premier + dernier segment » rangeait `profile/search` sous `profile/atp` |
| **(la mienne)** un nom exact est une résolution | `Leylah Fernandez` existe chez le fournisseur avec **zéro match** ; le vrai profil est `Leylah Annie Fernandez`, 452 matchs, et la recherche rend les deux |

### Trois corrections que seul un rendu réel a trouvées

Les tests unitaires passaient dans les trois cas :

1. **Les lignes de service dépendaient de tennis-data.** Posées après le retour
   anticipé de `tennis_history.lines`, elles disparaissaient dès que
   `tennis_matches` était vide. Une source payante suspendue au téléchargement
   d'un classeur gratuit sans rapport — et le symptôme aurait été un bloc
   normal, sans quatre lignes ;
2. **Le tiret est une variation réelle et bidirectionnelle** : la source écrit
   `Pablo Carreno-Busta` quand la base écrit `Pablo Carreno Busta`, et
   `Felix Auger Aliassime` quand la base écrit `Felix Auger-Aliassime`. Le profil
   de Carreno porte 1 028 matchs ;
3. **Le repli par nom de famille se déclenchait sur « recherche vide »**, ce qui
   ne part jamais quand la recherche rend un candidat unique dont le profil est
   vide. Il se déclenche sur « aucun candidat validé ».

### Deux erreurs de mesure attrapées avant d'écrire un chiffre

Toutes deux produisaient un compte **plausible**, ce qui est la forme la plus
coûteuse : le découpage des blocs sans borne haute (§15), et les marqueurs pris
dans le mode d'emploi au lieu du rendu (§15). Le second sortait **zéro** sur un
cas qui arrive 153 fois.

### Trois assertions qui recopiaient la valeur du jour

Corrigées en propriétés, conformément à la règle du dépôt : `fiche.budget == 7`,
`count(...) == 7`, et `len(sections) == 7` dans le test de parité page/fichier.
Les trois cassaient sur un réglage changé sans qu'aucune règle ait bougé.

---

# DIAGNOSTIC — lot 6 : la ligne `Jeux`, et le seuil qu'aucun joueur n'atteint

Relevé du **18/08/2026**, session courte (08h55 – 10h00). Une seule passe a été
lancée, sur la base servie, et **arrêtée volontairement** au vu de ce qu'elle
mesurait. Toute mesure ci-dessous vient de `api_responses` et de
`player_serve_agg`, pas du brief.

---

## §1 — La cause racine, et les quatre taux

**Cause racine constatée.** `fetch_timeline` (`services/serve_stats.py:917`)
existait depuis le lot 5 et n'avait **aucun appelant** : `aggregate` était
toujours invoqué sans `games=`, donc `served` valait `0` sur les 176 lignes de
`player_serve_agg`. La ligne `Jeux` ne disait pas « non disponible » par manque
de couverture — elle le disait parce que **rien n'avait jamais été collecté**.

**Correctif livré** (commit `ef845d9`) : `collect_games`, qui remonte du match le
plus récent au plus ancien et **s'arrête dès les 300 jeux atteints** ; reprise
par l'archive (`archived_timeline` relit les six chemins possibles avant d'en
payer un) ; plancher vérifié avant chaque rencontre. `TimelineTally` porte
quatre compteurs. L'entretien quotidien n'est pas touché : `sync` ne collecte
que sur `with_games`.

### Les quatre taux demandés

Passe du 18/08, **1 604 appels `event/get`**, arrêtée à six joueurs complets
(trois par circuit). Quota consommé : **1 691 appels**, 147 288 restants.

| Mesure | Valeur |
| --- | --- |
| timelines obtenues | **94 sur 1 604 appels — 5,9 %** |
| `result` vide (HTTP 200) | **1 510 — 94,1 %** |
| ruptures d'alternance | **0** |
| échecs réseau | **0** |
| joueurs atteignant 300 jeux | **0 sur 6** |

**L'invariant d'alternance ne s'est jamais rompu**, sur 94 timelines et non sur
trois. Le brief attendait le contraire — « à l'échelle, il ne le sera pas » — et
la mesure ne le suit pas : la source, quand elle sert une timeline, la sert
entière. Ce qu'elle fait mal, c'est **servir**.

**La ventilation par tranche de classement n'a pas été produite, et il ne faut
pas l'inventer** : six joueurs. La ventilation par circuit, elle, tient — et les
deux circuits se ressemblent.

### Le verdict, et il est celui que le brief avait prévu

| Joueur | Circuit | Timelines | Jeux (servis + retournés) |
| --- | --- | ---: | ---: |
| Nuno Borges | atp | 14 | **299** |
| Madison Keys | wta | 10 | **200** |
| Andrey Rublev | atp | 7 | **155** |
| Joao Fonseca | atp | 5 | **115** |
| Janice Tjen | wta | 3 | **61** |
| Katerina Siniakova | wta | 2 | **37** |

Aucun n'atteint 300, **fenêtre de 52 semaines épuisée**. Ce n'est donc pas un
problème de fenêtre ni d'arrêt trop précoce : c'est la couverture de la source.
Borges échoue à **un jeu près**, ce qui est une coïncidence et non un signal —
les cinq autres sont entre 37 et 200.

Le brief demandait explicitement quoi faire dans ce cas — « si moins de la
moitié des joueurs atteint le seuil, ne bricole pas le seuil : dis-le, et la
ligne `Jeux` restera omise pour les autres ». **Le seuil n'a pas été touché.**
La ligne `Jeux` reste omise, et c'est le comportement correct : à 155 jeux, une
tenue de service serait lue comme un fait alors qu'elle décrit sept matchs.

### La passe se relance sans personne

Elle ne meurt pas avec la session qui l'a lancée. Deux chemins, le même code :

```bash
uv run myassistantbet-timelines --joueurs 12
```

Et **automatiquement** : `TIMELINES_JOB_ID`, 30 minutes après le scan, chaque
jour. Elle est bornée à `BATCH` (12) joueurs par passage — **une borne de temps
de mur, pas de quota**, le plancher gardant le second : sans elle un passage
tournerait quinze heures et chevaucherait le suivant.

Trois propriétés en font une reprise et non un recommencement :

- **l'archive est l'index.** Une rencontre déjà demandée n'est jamais repayée,
  y compris quand la réponse était vide — et c'est le cas de 94 % d'entre
  elles ;
- **les lots à venir passent en premier**, donc une interruption laisse
  couverts les joueurs utiles aujourd'hui ;
- **un passage manqué ne se rattrape pas** (`misfire_grace_time` d'une heure,
  `coalesce`) : celui de demain reprendra exactement où celui-ci s'est arrêté.

Elle n'est **pas** gratuite, contrairement aux trois sources du lot voisin, et
son garde-fou n'est donc pas la gratuité mais le plancher de quota, vérifié
avant chaque joueur.

### Ce que la mesure change au dessin

**L'auto-limitation ne mord pas.** Elle a été construite parce que le brief la
demandait, et elle est juste ; elle ne s'est déclenchée **aucune fois**, la
collecte s'arrêtant sur la liste épuisée et non sur le seuil. La contrainte
n'est pas le nombre de matchs à parcourir, c'est le **nombre de timelines
servies** — 5 à 14 là où il en faudrait une quinzaine.

**Le coût par timeline utile est le vrai chiffre.** 1 604 appels pour 94
timelines, soit **~17 appels par timeline obtenue** et ~270 appels par joueur. L'estimation du lot 5 — « ~6 200 appels pour 52 semaines » — vaut
pour 25 joueurs, pas pour le catalogue : à 256 joueurs en file, la passe
complète demanderait de l'ordre de **70 000 appels et une quinzaine d'heures de
temps de mur** — soit la moitié du quota mensuel, pour une ligne dont on vient
d'établir qu'elle ne sortirait pas.

**C'est ce qui a motivé l'arrêt de la passe**, et c'est une décision, pas une
interruption subie : continuer aurait dépensé du quota pour une ligne dont on
venait d'établir qu'elle ne sortirait pas. La passe est reprenable — les 764
réponses sont archivées et ne seront pas repayées.

---

## §3 — La porte C-bis s'ouvre

**Verdict : elle s'ouvre.** Prouvé le 18/08 par le vrai chemin d'import — les
routes HTTP, pas `replay` — sur une **copie** de la base servie.

Collage de test : une section C d'une ligne, puis une section C-bis de cinq,
dont trois valides et les deux cas de rejet spécifiés.

| Ligne C-bis | Palier | Résultat |
| --- | --- | --- |
| Levski Sofia | 🟠 ULTRA FUN | importée, `exploratoire = 1` |
| Viking FK | 🔴 GIGA FUN | importée, `exploratoire = 1` |
| Fenerbahce – Lyon | 💥 GIGA+ | importée, `exploratoire = 1` |
| Viking FK (doublon) | 🔵 FUN | **refusée** — `schema_invalid`, palier sûr |
| Shanghai Shenhua | 🟠 ULTRA FUN | **refusée** — `duplicate`, déjà pris en section C |

Les deux refus sont **journalisés** et non silencieux, et les lignes ne sont pas
proposées du tout — les corriger sur place inventerait une décision que le rendu
n'a pas prise.

**L'exclusion des agrégats est réelle, et il a fallu un contrôle pour le dire.**
Les trois lignes étant en attente de résultat, leur absence des axes pouvait
n'être qu'un artefact — une sélection non tranchée n'entre nulle part. Les neuf
exploratoires de la copie ont donc été **tranchées à `gagne`**, et les axes n'ont
pas bougé : `by_tier`, `by_confidence`, `by_sport` restent à **197**, `overall`
à 197 également. L'addition ferme : `Populations(main=197, exploratory=9,
late=52, total=258)` — 197 + 9 + 52 = 258.

Le bloc de la page répond (`history.exploratory().empty` est faux, l'ancre
`#exploratoires` est rendue).

### Et la porte était déjà ouverte en production

**La prémisse du §3 ne tient plus.** Le brief la donne comme « population
exploratoire à zéro depuis trois lots », et le lot 5 l'expliquait par un
`imports_raw` vide. Relevé sur la base servie ce matin :

| | |
| --- | --- |
| `imports_raw` | **7 lignes**, pas vide |
| `picks.exploratoire = 1` | **6 sélections**, session 15, datées du **17/08** |
| leur `import_id` | **3, 4 et 7** — trois imports réels distincts |

Elles sont donc arrivées par le vrai chemin, hier, sur de vrais collages :
`Magdalena Frech +4.5`, `Maja Chwalinska`, `Jiri Lehecka`, `Emma Navarro`,
`Wrexham AFC`, `Match nul` — quatre ULTRA FUN et deux GIGA FUN, toutes en palier
haut comme la règle l'exige. Cinq sont encore `pending`, une est tranchée.

**Le verdict est donc doublement établi** : par le collage de test ci-dessus, et
par six lignes que personne n'avait regardées. Ce qui manquait n'était pas une
preuve, c'était de relire la table.

---

## §2 et §5 — non traités, et pourquoi

- **§2 (rendu réel de la ligne `Jeux`)** — **sans objet en l'état** : aucun
  joueur n'atteint le seuil, donc le rendu montrerait exactement ce qu'il
  montrait avant le lot. Le bloc avant/après demandé sur Fritz – Michelsen et
  sur un match WTA hors top 50 n'a pas été produit, et le produire aurait
  affiché deux fois « non disponible ». À refaire quand un joueur passera 300.
- **§5** — `lot5-statistiques-de-service` et `main` pointent sur le **même
  commit** : la fusion demandée était déjà faite, il n'y avait rien à fusionner.
  Les deux autres points du §5 n'ont pas été faits.

---

## §4 — Le drapeau des deux côtés, et la note d'activation

**`SERVE_LINES_ENABLED` reste à `0`.** Conforme, et ce n'est pas un oubli.

Le test livré porte sur le **prompt entier** et non sur `serve_lines` seule :
drapeau bas, un rendu produit **avec** les agrégats en base est comparé octet
pour octet à un rendu produit **sans aucun agrégat** — c'est-à-dire à l'état
d'avant le lot. Les deux sont identiques, et aucune attribution
`[tennis-api.com]` n'apparaît. La référence est un rendu et non une sortie du
jour recopiée dans une assertion : si elle casse, on aura appris quelque chose.

Le côté haut du drapeau reste couvert au niveau de `serve_lines`, là où la
décision se prend.

### La note d'activation (à ne pas jouer aujourd'hui)

L'activation est **une décision de l'utilisateur**, prise après quelques
sessions au budget de 10, et elle ne doit pas être jouée ce matin : le budget de
recherche est passé à 10 hier sans qu'aucune session l'ait exercé, et allumer
les lignes de service maintenant ferait porter à la première session les deux
changements à la fois — le découpage avant/après du `changelog_mesure` ne
pourrait plus les séparer.

Commande, le jour venu :

```bash
grep -q '^SERVE_LINES_ENABLED=' .env && sed -i 's/^SERVE_LINES_ENABLED=.*/SERVE_LINES_ENABLED=1/' .env || echo 'SERVE_LINES_ENABLED=1' >> .env; sudo systemctl restart myassistantbet
```

**La clé est absente du `.env` aujourd'hui** — le drapeau vaut `False` par son
défaut Pydantic, vérifié. Un `sed` seul n'aurait donc rien substitué et serait
sorti sans erreur : la commande aurait paru fonctionner, le service aurait
redémarré, et le drapeau serait resté bas. D'où la forme ci-dessus, qui ajoute
la ligne quand elle manque. À vérifier après redémarrage :

```bash
curl -s localhost:8021/health | python3 -m json.tool | grep -i serve
```

Ce qui doit être écrit dans `changelog_mesure` **au moment où la commande est
jouée, et pas avant** : une entrée datée du jour de l'activation, portant que
les lignes de service entrent dans le bloc tennis. **La date du journal est
celle où le changement agit sur ce que le modèle lit, jamais celle où il a été
livré** — une entrée écrite d'avance daterait le changement du jour du code et
placerait du mauvais côté de la coupure les sessions qui n'en ont pas bénéficié.

**Préalable à ne pas oublier** : tant qu'aucun joueur n'atteint 300 jeux,
allumer le drapeau ne fera sortir que `Service`, `Retour` et `Ecart`. La ligne
`Jeux` restera omise.

---

## §6 — Ce que la mesure contredit dans le brief

Huit affirmations reprises et vérifiées ; **quatre ne tiennent pas**, dont deux
qui ont changé le déroulé de la session. Une cinquième, que j'avais comptée
comme fausse, ne l'était pas — voir §8, et c'est la correction la plus
importante de ce relevé.

| Affirmé | Mesuré |
| --- | --- |
| ~~« 176 joueurs sur 176 » serait un compte de lignes~~ | **retiré — c'était mon erreur, pas celle du brief.** Voir §8 : deux populations distinctes, et le nombre 176 tombe des deux côtés par coïncidence |
| « ~6 200 appels pour 52 semaines, soit ~4 % du quota » | ~270 appels par joueur : **~70 000 appels** pour les 256 joueurs en file, soit ~47 % du quota |
| « l'invariant d'alternance… à l'échelle, il ne le sera pas » | **0 rupture sur 94 timelines.** La source sert entier ou ne sert pas |
| « à ~22 jeux par match, une quinzaine de matchs suffit » | juste sur les jeux (21,4 par match mesuré), **faux sur les matchs** : on n'obtient que 2 à 14 timelines par joueur, jamais quinze |
| « le §1 est le seul point qui exige du temps de mur » | vrai, et il en exige **un ordre de grandeur de plus** que prévu |
| « la ligne `Jeux` ne sort pas [car] les timelines ne sont pas collectées en masse » | exact, et la cause est plus nette : `fetch_timeline` n'avait **aucun appelant** |
| « la population exploratoire est à zéro depuis trois lots » / « `imports_raw` est vide » | **faux** : 7 lignes dans `imports_raw`, **6 sélections exploratoires** en base, arrivées par trois imports réels le 17/08 |

**Les deux qui ont changé une décision** sont la troisième et la cinquième. Sans
elles, la passe aurait été lancée sur tout le catalogue et laissée tourner : elle
aurait consommé près de la moitié du quota mensuel pour produire une ligne que le
seuil omet de toute façon. C'est ce qui a motivé l'arrêt volontaire à six
joueurs.

**Une affirmation du brief est correcte et mérite d'être notée** : il prévoyait
le cas « moins de la moitié des joueurs atteint le seuil » et disait quoi faire.
C'est exactement ce qui s'est produit, et la consigne — ne pas bricoler le
seuil — est ce qui a été suivi.

---

## §7 — Ce qui reste, dans l'ordre

1. **La ligne `Jeux` ne sortira pas sans une autre source de jeux.** La
   couverture `event/get` est à 5,9 % et ne dépend pas de nous.

   **La voie la moins chère a été vérifiée ce matin, et elle est fermée pour la
   tenue de service.** `profile/matches-played` — déjà appelé, déjà archivé,
   aucun appel de plus — porte 23 champs par match et 18 par joueur, et
   **aucun ne compte les jeux de service** : ni `SvGms`, ni `serviceGames`, ni
   holds. Ce qu'il porte :

   | Champ | Ce qu'il vaut |
   | --- | --- |
   | `result` (« 6-3 7-5 ») | le **total** de jeux du match, exact |
   | `breakPointsConverted` | le **nombre de breaks réussis**, exact |
   | *(rien)* | les jeux **servis** par chaque joueur — le dénominateur |

   Le numérateur du taux de break est donc gratuit et exact ; **les deux
   dénominateurs manquent**. Les déduire du score supposerait que chacun sert
   la moitié des jeux à une unité près — une approximation, sur une ligne qui
   serait lue comme un fait. C'est précisément ce que le seuil de 300 refuse.

   Restent donc deux voies : accepter que la ligne reste omise, ou trouver une
   source de timelines dont la couverture ne soit pas de 6 %.
2. **§2** — le bloc avant/après, quand un joueur passera le seuil.
3. **§5** — le registre des chemins d'écriture est **à jour** : la collecte
   n'ouvre aucun chemin nouveau (elle écrit par `store_aggregate` et
   `archive_response`, tous deux déjà déclarés), et `test_write_paths` passe.
   Reste l'entrée `changelog_mesure`, et elle **n'a pas été écrite** — voir
   ci-dessous.

### L'entrée `changelog_mesure` de la passe : non écrite, et c'est raisonné

Le brief la demande, au motif que la passe « change ce que le modèle lit dès
l'activation, donc elle mérite sa date ». **Les deux moitiés de cette phrase se
contredisent**, et la règle du §4 tranche : la date du journal est celle où le
changement **agit**, pas celle où il a été livré.

`changelog_mesure` coupe la population sur `picks.created_at` — une entrée
marque le jour où ce que le modèle produit a changé. Or `SERVE_LINES_ENABLED`
vaut `0` : la collecte de ce matin ne change **rien** à ce que le modèle lit, et
le test du §4 le prouve octet pour octet. Une entrée datée du 18/08 poserait
donc une coupure là où rien n'a bougé, et placerait du mauvais côté toutes les
sessions tirées entre aujourd'hui et l'activation.

C'est exactement ce que le brief interdit par ailleurs — « ne pas écrire
d'avance la ligne de journal de l'activation ». La passe et l'activation
partagent la même date d'effet : **celle où le drapeau passe à `1`**, et une
seule entrée les couvrira toutes les deux.

Les onze entrées existantes suivent déjà cette convention : « lot 5 — budget de
recherche à 10 » est datée du 17/08, jour où le budget a pris effet.

---

## §8 — 38 contre 176 : deux populations, et une coïncidence qui m'a trompé

**Réponse : le troisième cas. Les deux comptes sont justes et ne portent pas sur
la même population.** Mon relevé précédent affirmait que « 176 » était un compte
de lignes et que le lot 5 s'était trompé. **C'est moi qui me suis trompé**, et
la correction compte plus que le constat d'origine : elle décide si la source
reste adoptée.

```sql
SELECT (SELECT COUNT(DISTINCT player) FROM player_serve_agg) AS joueurs_persistes,
       (SELECT COUNT(*)              FROM player_serve_agg) AS lignes_persistees;
```

| | Compte | Ce que c'est |
| --- | ---: | --- |
| `COUNT(DISTINCT player)` de `player_serve_agg` | **38** | les joueurs **persistés** en production |
| `COUNT(*)` de `player_serve_agg` | **176** | leurs lignes — 38 joueurs × ~4,6 surfaces |
| `couverture.py` du lot 5 | **176** | les joueurs **distincts des cinq derniers lots**, mesurés sur base temporaire |

**Le nombre 176 tombe des deux côtés par pur hasard**, et c'est toute l'origine
de la confusion : 38 × 4,63 surfaces ≈ 176 lignes persistées, contre 176 joueurs
mesurés. Deux grandeurs sans rapport, un seul nombre.

### Ce qui écarte les deux autres cas

**Le premier cas est écarté par la source de `couverture.py`** : il itère sur
`joueurs.json`, un **dictionnaire indexé par le nom du joueur**. Un dictionnaire
ne peut pas porter de doublon, donc il comptait bien des joueurs — vérifié :
176 entrées, **176 clés distinctes**, 89 ATP et 87 WTA. Cela recoupe exactement
le §6 du lot 5 (88 + 86 résolus, plus 2 non résolus nommés). **Le lot 5 ne s'est
pas trompé, et la décision d'adopter la source n'est pas invalidée.**

**Le deuxième est écarté par l'intention, écrite dans le script** : « Base
temporaire : rien n'est écrit en production. » Aucune passe n'a été perdue —
elle n'a jamais eu vocation à être persistée. C'était une mesure de couverture,
pas une collecte.

### Les deux populations, nommées

| | Population | Origine |
| --- | --- | --- |
| **176** | les joueurs des **cinq derniers lots tennis** au 17/08 | instantané du board, base temporaire |
| **38** | les joueurs vus par `sync(upcoming_players)` les jours où le planificateur a tourné | population **glissante**, écrite en production |

Recoupement mesuré : **26 joueurs communs**, 12 persistés hors des 176 (arrivés
au board depuis), **150 des 176 jamais persistés**. Le faible recouvrement est
normal — l'un est un instantané, l'autre un cumul de ce qui jouait ces jours-là.

### La notation `13/26`, levée

**13 fenêtres sur 26, et non 13 joueurs sur 26 attendus.** Chaque ligne de
`player_serve_agg` est un couple *(joueur, surface)* : les 26 sont les joueurs
qui portent **une ligne I.hard**, c'est-à-dire qui ont joué au moins un match
sur cette surface — pas un effectif attendu. Sur ces 26, 13 franchissent les 400
points **sur cette surface**. Idem pour `Grass 24/36`.

**Ce n'est donc pas une contradiction du brief**, dont les 100 % portent sur la
fenêtre *toutes surfaces* — vraie elle aussi : les 38 joueurs persistés y sont
tous au-dessus de 400. Une couverture par surface est mécaniquement plus basse,
et le lot 5 le disait déjà (« ATP dur 87/88 »). L'entrée est retirée du §6.

---

## §9 — Mise en production du 18/08/2026, 09h40

Décision de l'utilisateur, prise en connaissance de cause : il veut voir les
lignes ce matin.

| Étape | État |
| --- | --- |
| Sauvegarde préalable | `data/backups/myassistantbet-20260818-073042.db`, 45,9 Mo |
| Migrations | schéma **62**, appliquées au démarrage |
| `SERVE_LINES_ENABLED` | **`1`** — la clé était absente du `.env`, elle a été **ajoutée** |
| Redémarrage | `systemctl restart`, service `active`, `/health` ok |
| Suite avant bascule | **2 123 tests**, ruff vert |

### Le prompt réel : les lignes sortent, `Jeux` est absente partout

Lot ATP Cincinnati du jour, **12 blocs**, 21 711 tokens, aucun appel réseau.

| Ligne | Blocs |
| --- | ---: |
| `Service` | **8 / 12** |
| `Retour` | **8 / 12** |
| `Ecart` | **8 / 12** |
| **`Jeux`** | **0 / 12** |

Chaque ligne porte son `as_of`, ses dénominateurs et son attribution :

```
  Service     Andrey Rublev 60.3% 1re · 77.9% s/1re · 50.4% s/2e · 10.3% aces · 9.1% df
              Nuno Borges 66.9% 1re · 75.0% s/1re · 48.4% s/2e · 9.1% aces · 9.6% df
              (Hard, 52 sem., 2083 et 2471 pts de service, arretees au 16/08) [tennis-api.com]
```

**`Jeux : non disponible` n'est pas un défaut aujourd'hui, c'est la règle qui
fonctionne.** La ligne exige 300 jeux servis + retournés ; la passe n'a couvert
qu'une fraction du catalogue et aucun joueur mesuré n'atteint le seuil. Elle
apparaîtra joueur par joueur à mesure que la passe quotidienne avance, et pour
personne avant. Ne pas la lire comme une régression, et **ne pas abaisser le
seuil pour la faire sortir** : à 155 jeux elle serait lue comme un fait.

Les 4 blocs sans lignes de service sont des joueurs que la passe n'a pas encore
résolus — même mécanique, même correctif : elle tourne.

### Les trois entrées de `changelog_mesure`, et le défaut consigné

| id | Portée | Objet |
| ---: | --- | --- |
| 12 | gabarit | budget de recherche à 10 — **daté du 18/08**, jour d'effet, car aucune session ne l'avait exercé avant |
| 13 | gabarit | lignes de service tennis |
| 14 | restitution | **COUPE JOINTE** — les deux ci-dessus ne seront pas séparables |

L'entrée 14 est le point à retenir : les deux changements partagent la même date
d'effet, donc **tout écart de résidu mesuré autour du 18/08 mesure leur somme**.
Il n'existe aucune session au budget 10 sans lignes de service. Les séparer
demanderait de désactiver l'un des deux pendant quelques sessions. Consigné le
jour même plutôt que découvert dans trois semaines.

---

## §10 — `BEGIN IMMEDIATE` : correctif tenté, mesuré, et **rejeté**

Résultat négatif, écrit sous la forme qui empêche de le refaire.

**Le défaut est réel et caractérisé.** `connect()` ouvre un `BEGIN` *déféré* :
il lit, puis se **promeut** en écriture au premier INSERT. Sur cette promotion
SQLite rend `SQLITE_BUSY` **immédiatement**, sans honorer `busy_timeout` — qui
couvre l'attente d'un verrou, pas le conflit d'instantané. Il a dormi tant qu'il
n'y avait qu'un écrivain, puis a cassé un enrichissement réel le 18/08 quand une
passe de collecte tournait en même temps (`Rublev – Borges — OperationalError:
database is locked`).

Mesure isolée, trois écrivains, 120 transactions, `busy_timeout` à 5 s :

| Ouverture | « database is locked » |
| --- | ---: |
| `BEGIN` | **72** |
| `BEGIN IMMEDIATE` | **0** |

**Et pourtant le correctif ne passe pas.** Appliqué à `connect()` (écrivains en
`IMMEDIATE`, `query`/`query_one` laissés déférés), il fait **échouer une douzaine
de tests** répartis sur la moitié de la suite — dont
`test_le_prompt_se_tait_sur_un_regroupement_trop_maigre`, qui meurt sur le
`BEGIN IMMEDIATE` lui-même.

**Cause : `connect()` s'imbrique, et dynamiquement.** Une transaction écrivain
externe tient le verrou ; du code appelé à l'intérieur ouvre sa **propre**
connexion écrivain ; les deux s'attendent sur des connexions distinctes du même
fil, et `busy_timeout` ne fait que retarder l'échec de 5 s. En déféré la seconde
passait tant qu'elle ne faisait que lire — c'est ce qui masquait l'imbrication.

**L'imbrication n'est pas lexicale, et c'est ce qui rend le correctif coûteux** :
mesuré par AST sur tout `src/`, **0 imbrication `connect()`-dans-`connect()`
écrite littéralement**. Elle se produit à l'exécution, une fonction sous
`with connect()` en appelant une autre qui ouvre la sienne. Aucune recherche
textuelle ne les trouvera.

**Revenu en arrière.** L'application sert le code d'avant, et le contournement du
jour est opérationnel : **ne pas faire tourner la passe de collecte pendant un
enrichissement**. Le job planifié part 30 min après le scan, donc hors des heures
de travail.

**Ce qu'il faudrait, et ce n'est pas une ligne** : rendre `connect()` réentrant —
une transaction par fil, les appels internes rejoignant celle de l'extérieur au
lieu d'en ouvrir une seconde. C'est la vraie correction, elle touche le point de
passage de **toutes** les écritures, et elle se livre avec sa propre mesure de
contention. À ne pas tenter en fin de session.

**Ce qu'il ne faut PAS refaire** : passer `connect()` en `BEGIN IMMEDIATE` sans
traiter la réentrance. La mesure isolée est excellente (72 → 0) et **c'est un
piège** : elle ne teste pas l'imbrication, qui est le régime réel de ce code.

---

# DIAGNOSTIC — lot 7 : le coût réel de la passe, et le levier qui n'est pas celui prévu

Session courte (23h39 – 00h15). Tout ce qui suit est mesuré sur `api_responses`
et sur la base servie ; rien n'est repris du brief sans vérification.

## §1a — Le coût réel, sur 2 767 appels `event/get`

| Mesure | Valeur |
| --- | ---: |
| rencontres tentées | 564 |
| rencontres aboutissant à une timeline | **113 — 20,0 %** |
| appels | 2 767 |
| **appels par rencontre tentée** | **4,91** |
| **appels par timeline obtenue** | **24,5** |
| `result` vide (HTTP 200) | 2 647 |
| 404 / erreurs réseau | **0** |
| ruptures d'alternance | **0** |

L'ordre de grandeur du brief est confirmé : à 24,5 appels par timeline utile, et
~15 timelines par joueur pour atteindre 300 jeux, couvrir 176 joueurs coûterait
de l'ordre de **60 000 appels** — 40 % du quota mensuel.

## §1b — Le tâtonnement n'est PAS le coût, et le brief se trompe de levier

**L'hypothèse du brief est renversée par la mesure.** Il proposait de mémoriser
par tournoi le décalage de date et l'ordre des joueurs. Les deux branches de
cette idée tombent :

| Rang de l'essai qui aboutit | Rencontres |
| --- | ---: |
| **1er essai** | **106 / 113 (93,8 %)** |
| 5e essai | 7 |

**Quand une timeline existe, elle est trouvée du premier coup neuf fois sur
dix.** Il n'y a donc presque rien à mémoriser. Et l'ordre des joueurs est un
tirage à pile ou face — **60 fois l'ordre inverse, 53 fois l'ordre direct** :
aucun ordre systématique à apprendre.

**Où part réellement le quota :**

| | Appels | Part |
| --- | ---: | ---: |
| rencontres qui aboutissent | 170 | **6,1 %** |
| rencontres sans timeline | 2 597 | **93,9 %** |

**94 % du quota est dépensé sur des rencontres que la source ne sert sous
AUCUNE combinaison.** 407 rencontres ont brûlé les six essais pour rien. Aucune
mémorisation ne peut les aider : il n'y a rien à mémoriser d'un échec total.

### Le vrai levier : `J+1` ne paie jamais

| Décalage qui aboutit | Rencontres |
| --- | ---: |
| `J+0` | **106** |
| `J-1` | 7 |
| **`J+1`** | **0** |

`J+1` est essayé sur chaque rencontre en échec et **n'a jamais rien rapporté**,
sur 564 tentatives. Contrefactuel, en ne tentant que `J+0` (les deux ordres,
2 appels au plus) :

| | Actuel | `J+0` seul |
| --- | ---: | ---: |
| timelines obtenues | 113 | **106 (−6 %)** |
| appels | 2 767 | **1 029** |
| **coût par timeline** | 24,5 | **9,7** |

**2,7 fois moins cher pour 94 % du rendement.** C'est l'arbitrage à trancher, et
il n'est pas de même nature que celui proposé : on n'optimise pas la recherche,
on **cesse de chercher là où la mesure dit qu'on ne trouve pas**.

**Rien n'a été implémenté** — 23h45 était l'heure d'arrêt de construction, et le
choix (perdre 6 % de couverture pour 2,7× de quota) est un arbitrage de
l'utilisateur, pas une évidence technique.

## §2 — Oui, le tournoi en cours est couvert, statistiques comprises

**Répondu depuis l'archive, pour zéro appel** — et sur **243 profils** au lieu
des trois ou quatre demandés, ce qui rend la réponse bien plus solide.

### Le retard

| Mesure | Valeur |
| --- | ---: |
| profils lus | 243 |
| **retard médian** (jour d'appel − dernier match servi) | **2 jours** |
| retard minimum | **0 jour** |
| retard maximum | 20 jours |

Distribution : 24 profils à `0`, 33 à `1`, 66 à `2`, 22 à `3`, 30 à `4`, 38 à
`5`, 18 à `6`.

**Le médian de 2 jours surestime le vrai retard, et il faut le dire** : il
mesure l'écart au dernier match *servi*, or un joueur ne joue pas tous les
jours. Les **24 profils à 0 jour** sont la vraie borne — la source sait servir
un match **le jour même**.

### Le tournoi en cours, spécifiquement

Sur les 247 profils appelés le 18/08, Cincinnati étant en cours depuis le 15 :

| | |
| --- | ---: |
| profils servant un match du 15/08 ou après | **139 — 56 %** |
| matchs du tournoi en cours servis | **180** |
| **dont statistiques de service renseignées** | **173 — 96 %** |

### Ce que ça décide

**La consigne de recherche du gabarit — « ses matchs déjà joués dans ce
tournoi-ci, la recherche la plus rentable du lot » — porte sur une information
que l'application collecte déjà**, avec ses statistiques de service, à un ou
deux jours près.

C'est l'inverse de la situation de `tennis-data.co.uk`, qui accuse **dix jours**
et publie une fois par semaine — c'est lui que la ligne `Historique` date, et
c'est de lui que venait la conviction que le tournoi en cours est hors de
portée. Deux sources, deux fraîcheurs, et la conclusion tirée de l'une avait été
étendue à l'autre.

**Le gabarit n'est pas modifié** : retirer la consigne et rendre le budget de
recherche à autre chose est un arbitrage de l'utilisateur. La mesure est là pour
qu'il se prenne sur un chiffre. Réserve à garder : 56 % des profils seulement
portent le tournoi en cours, donc la consigne resterait utile sur près de la
moitié des blocs — la retirer entièrement serait plus fort que ce que la mesure
autorise.

## §3 — Le catalogue se peuple

| | Joueurs distincts dans `player_serve_agg` |
| --- | ---: |
| avant | **38** |
| après | **250** |

Passe `matches-played` seule sur les 256 joueurs du catalogue, **terminée**.
Coût : **445 appels** (3 304 − 2 859), soit **1,7 par joueur** — le
dimensionnement du lot 5 (~2) tombe juste. Quota restant : **145 673**.

Deux joueurs restent non résolus et sont **nommés** plutôt que tus — JJ Wolf et
Bianca Andreescu : la source ne sert aucun match sous les graphies rendues.
C'est une absence réelle, pas un défaut de rapprochement, et elle part en
`ingestion_rejects`.

**Effet attendu au prochain prompt** : les 4 blocs sans lignes de service de ce
matin devraient en porter. La ligne `Jeux`, elle, ne bougera pas — voir §1c.

## §4 — Les trois réponses

- **§4a — rien à rétablir.** La session 16 porte **4 matchs de tennis** et
  **15 sélections** : elle a été consommée dans la journée. Les 12 ATP que
  j'avais cochés ce matin ont été décochés par l'usage. Rétablir un état
  antérieur écraserait le travail réel.
- **§4b — la porte C-bis s'est ouverte pour de bon.** 6 imports aujourd'hui,
  15 sélections, dont **5 exploratoires** en production. Ce n'est plus un
  collage de test.
- **§4c — la question ne peut pas être répondue, et c'est le constat.**
  `open_dossiers_state` vaut **`absente`** sur la session 16 comme sur toutes
  les précédentes, et les **15 sélections du jour portent
  `research_overridden = 1`, cause `ligne_absente`**. La ligne
  `dossiers_ouverts` n'a **toujours pas** été collée. On ne sait donc pas si le
  modèle ouvre 10 dossiers ou 6 : **troisième session consécutive sans cette
  entrée**, et toute la machinerie du cran calculé reste sans mesure.

## §5 — La spécification du retard est toujours à jour

**52 sélections tardives tranchées** (32 gagnées, 20 perdues), exactement
l'effectif sur lequel la spécification du lot 4 est écrite. **Rien n'a bougé, la
spécification n'a pas à être reprise.** Non implémentée, conformément au brief.

---

## §1c — La passe longue n'a PAS été lancée, et c'est une recommandation d'inaction

**Le §1a la déconseille, et c'est exactement pourquoi il était bloquant.**

État au moment de l'arrêt :

| | |
| --- | ---: |
| joueurs porteurs de jeux | **6** |
| joueurs au seuil de 300 jeux | **0** |
| appels `tennisapi` consommés | 3 304 |
| quota restant | **145 673** |

Lancer la passe complète cette nuit aurait coûté de l'ordre de **60 000
appels** — 40 % du quota mensuel — sur un chemin dont on vient de mesurer qu'il
**gaspille 94 % de ses appels** et que `J+1`, essayé à chaque échec, n'a jamais
rien rapporté en 564 tentatives.

Dépenser 60 000 appels sur la version inefficace, la veille du jour où l'on
peut la rendre 2,7 fois moins chère, serait précisément l'erreur que la règle
« mesurer avant de lancer » existe pour empêcher. **La passe attend l'arbitrage
du §1b.**

Elle reste reprenable et rien n'est perdu — les 2 767 réponses sont archivées et
ne seront pas repayées.

```bash
uv run myassistantbet-timelines --joueurs 400
```

Le job planifié (`TIMELINES_JOB_ID`, 30 min après le scan) continue d'avancer par
lots de 12 en attendant.

**À décider avant de relancer** : garder les six combinaisons, ou ne tenter que
`J+0` — 106 timelines sur 113 pour 2,7 fois moins de quota.

---

## §1 (lot 7 bis) — `dossiers_ouverts` : ni le modèle, ni l'extracteur — le collage

**Tranché en une requête, et la dichotomie du brief rate le vrai cas.**

```sql
SELECT id, session_id, char_count, raw_text LIKE '%dossiers_ouverts%' FROM imports_raw;
```

| | |
| --- | ---: |
| collages archivés | **13** |
| portant `dossiers_ouverts` | **0** |
| portant un bloc ` ```conf ` | **0** |
| taille des collages | **567 à 1 314 caractères** |

**La taille tranche à elle seule.** Un rendu complet en fait des dizaines de
milliers ; 1 314 caractères, c'est un tableau et rien d'autre. Vérifié sur le
collage 13 : il **commence** à `C. Tableau des sélections` et **s'arrête** à sa
dernière ligne. Ni section A, ni B, ni D, ni E, ni F, ni bloc `conf`, ni ligne
`dossiers_ouverts`.

**Ce n'est donc aucun des deux cas proposés.** Ce n'est pas un défaut
d'extraction — la donnée n'arrive jamais. Et ce n'est pas « le modèle ne la
produit pas » — un modèle muet sur cette ligne aurait quand même produit les
sections A à F, absentes elles aussi. **C'est le copier-coller qui ne prend que
le tableau.**

La distinction décide du correctif : reprendre le gabarit ne changerait rien, et
c'est le geste de collage qu'il faut viser — coller la réponse entière.

**Un seul chemin est donc mesuré aujourd'hui, et il fonctionne** : la colonne
`Source` du tableau est bien lue (valeurs `1` et `2` en base). Tout ce qui vit
**hors** du tableau est perdu depuis le premier import.

### Le banc de transport passe à cinq formats

`dossiers_ouverts` était **le seul format structuré du gabarit absent du banc** —
cinquième occurrence du motif du projet, un silence que rien ne surveillait. Il y
entre, avec ses 11 altérations.

- Son échec sort sous le type `CONF`, et c'est juste : la ligne et les blocs de
  confiance sont lus par le même lecteur et se perdent **par le même geste**.
- « Lu » se mesure sur `opened.declared`, **jamais sur le nombre de repères** :
  `dossiers_ouverts: []` est une déclaration légitime, et compter ses repères la
  confondrait avec une ligne absente, qui est un défaut de collage. C'est
  exactement la distinction que la migration 049 a dû ajouter après coup.

---

## §2 (lot 7 bis) — `J+1` retiré : 33 % d'appels en moins, zéro perte

`DAY_SHIFTS` passe de `(0, 1, -1)` à `(0, -1)`. Rejeu de la mesure sur les 564
rencontres de l'archive :

| | Appels | Timelines | Par timeline |
| --- | ---: | ---: | ---: |
| avant `(0, 1, -1)` | 2 767 | 113 | 24,5 |
| **après `(0, -1)`** | **1 844** | **113** | **16,3** |

**923 appels économisés (33 %), zéro timeline perdue.** `J-1` est gardé — 7 sur
113, peu mais pas zéro.

**Une réserve, et elle est écrite dans le test plutôt que masquée.** Le lot 5
documente un cas réel où `J+1` aboutissait : Fernandez – Wang, programmé le
16/08 à **19h10 UTC**, daté du **17** par la source. Le mécanisme est causal — un
match de fin de soirée bascule au lendemain chez un fournisseur qui date plus à
l'est — et **ce match n'est pas dans l'archive**. Les deux constats sont vrais
sur deux populations différentes, et l'archive ne connaît pas les heures de coup
d'envoi : elle ne peut pas dire si elle contient des matchs tardifs.

`test_la_fenetre_d_un_jour_rattrape_un_decalage_de_date` a donc été **réécrit
pour décrire la perte** au lieu d'être supprimé : il échouera le jour où `J+1`
reviendra, et la question se rouvre si des matchs tardifs se mettent à manquer.

## §3 (lot 7 bis) — Il existe une borne nette, et elle règle la question du coût

**C'est une fenêtre de rétention côté source, exactement l'hypothèse du brief.**
Croisement des 113 rencontres qui aboutissent contre les 451 qui échouent, sur
l'âge du match au moment de l'appel :

| Âge du match | Tentées | Timelines | Taux |
| --- | ---: | ---: | ---: |
| 0 – 7 j | 20 | 16 | **80 %** |
| 8 – 30 j | 30 | 24 | **80 %** |
| 31 – 90 j | 127 | 73 | **57 %** |
| **91 – 180 j** | **154** | **0** | **0 %** |
| **181 – 365 j** | **233** | **0** | **0 %** |

**Âge maximum d'un match portant une timeline : 80 jours.** 387 rencontres
au-delà de 90 jours, **zéro timeline**.

| Filtre | Rencontres tentées | Timelines gardées |
| --- | ---: | ---: |
| aucun | 564 (100 %) | 113 / 113 |
| **≤ 90 jours** | **177 (31 %)** | **113 / 113** |

**Un filtre d'âge à 90 jours supprime 69 % des tentatives sans perdre une seule
timeline.** Combiné au retrait de `J+1`, le coût par timeline tombe de **24,5 à
~4** — la passe complète repasse de ~60 000 appels à **moins de 10 000**, et la
question du coût est réglée.

**Non implémenté** : c'est un filtre en amont de `collect_games`, et l'heure
d'arrêt était passée. C'est le premier geste du prochain lot, et il est
mécanique.

## §4 (lot 7 bis) — Le dénominateur était faux, et la conclusion se renforce

Le brief avait raison de s'en méfier. Un joueur qui n'a pas encore joué dans le
tournoi en cours n'a rien à servir, et son absence n'est pas un défaut de
couverture.

| Dénominateur | Taux |
| --- | ---: |
| tous les profils appelés (250) | 56 % |
| **profils dont un match du 15/08+ figure au board (125)** | **99 % — 124 / 125** |

**`matches-played` sert le tournoi en cours pour 99 % des joueurs qui y
jouent**, statistiques de service comprises (173 matchs sur 180). Le 56 % ne
mesurait que la proportion de joueurs engagés cette semaine-là.

La conclusion du §2 d'hier s'en trouve **beaucoup plus forte** : la consigne que
le cadre appelle « la recherche la plus rentable du lot » porte sur une
information que l'application collecte déjà, presque intégralement. **Le gabarit
n'est toujours pas modifié** — c'est l'arbitrage de l'utilisateur, et il se prend
désormais sur 99 % et non sur 56 %.

---

## §11 — La vérification avant de changer le geste : l'aperçu tient, mais **l'interface demandait le tableau**

### Ce qui a été vérifié, et qui passe

Test de bout en bout par la vraie route `POST /history/16/picks/preview`, sur une
copie de la base, avec un collage de **32 172 caractères** :

| | |
| --- | --- |
| HTTP | **200** |
| `char_count` en base | **32 172 — exact, aucune troncature** |
| sélections lues | 2 |
| **`dossiers_ouverts`** | **`renseignee`, `declared=True`, repères `{M1, M2}`** |
| `sets:` | 2 scores |

**Aucun `maxlength` sur le `<textarea>`, aucune troncature côté serveur** :
`imports_raw.record` écrit `raw_text` intégral et `char_count = len(text)`.

Les blocs `conf` et le combiné sont ressortis à **0** — et **ce n'est pas une
limite de taille**. Contrôle : le **même contenu réduit à 632 caractères**, soit
cinquante fois moins, donne **exactement les mêmes zéros**. La cause est la somme
de contrôle, qui refuse des repères `M1/M2` ne correspondant à aucun des 4
prompts archivés de la session — un refus **nommé et visible**, pas un silence :

```
conf  : Les repères de match des blocs (M1, M2…) ne correspondent à aucun
        prompt de cette session, ligne par ligne.
combo : 1 combiné(s) lu(s), aucun rattaché : le prompt d'origine n'a pas pu
        être identifié.
```

**Le geste peut donc changer sans risque de remplacer un silence par un autre.**

### Et la cause racine n'est pas le geste : c'est l'interface

`templates/picks.html:311` — le panneau d'import **demande explicitement la
section C, et rien d'autre** :

```html
<summary>Importer le tableau de Claude
  <span class="muted small">section C du rendu, en-tête compris</span></summary>
<textarea name="table" rows="5"
          placeholder="| # | Match | Marché | Sélection | Cote | Palier | …">
```

Le libellé dit « le tableau », sa précision dit « section C du rendu », le
placeholder montre un en-tête de tableau, et le champ s'appelle `table`.
**L'utilisateur a collé exactement ce que l'application lui demandait.**

Les 13 collages de 567 à 1 314 caractères ne sont donc pas une négligence : ce
sont des collages **conformes à la consigne affichée**. Le correctif n'est pas
seulement dans le geste — il est d'abord dans ces trois lignes de gabarit, sans
quoi le geste se reprendra tout seul à la session suivante.

**Sixième occurrence du motif, et la plus coûteuse** : l'interface a demandé une
donnée partielle, l'a reçue, et rien ne pouvait signaler que le reste manquait —
puisque rien ne l'attendait.

### Le correctif : le libellé, et les deux filets qui attrapent la récidive

**Le libellé corrige la cause.** `picks.html` ne demande plus « le tableau,
section C du rendu » mais **la réponse entière**, avec le placeholder « Colle ici
la réponse complète, de la section A à la section F » et `rows="12"` au lieu de
`5`. Le champ garde son nom `table` : le renommer toucherait la route et le banc
pour zéro gain fonctionnel.

**Mais un texte d'interface ne se relit pas, alors qu'un compte-rendu se lit à
chaque import.** D'où deux filets, tous deux dans `ImportPreview.readout` —
c'est-à-dire **au seul moment où l'information est encore récupérable** :

1. **Les sections demandées et absentes sont nommées.** `sections.for_paste()`
   existait depuis le lot 3, avec ses trois états, et **rien ne l'appelait
   ici** : elle ne parlait qu'à la page de statistiques, une semaine trop tard.
   `SessionSections.note` était pourtant écrite pour ce moment précis — son
   docstring le dit. Elle est branchée, par un import différé (`sections`
   importe ce module).
2. **Un collage de moins de `PASTE_SHORT` (3 000) caractères est signalé, jamais
   refusé.** Le seuil est mesuré : les treize collages tronqués pèsent 567 à
   1 314 caractères, un rendu complet plus de 30 000 — deux ordres de grandeur
   séparent les deux populations, et le seuil se pose au milieu. Un collage
   court peut être légitime, donc rien n'est bloqué.

**Rejeu du collage réel #13 (1 314 caractères) à travers le nouveau relevé :**

```
· 4 sélection(s) détectée(s)
· 0 bloc(s) de confiance apparié(s)
· 0 combiné(s) rattaché(s)
· ligne dossiers_ouverts absente
· collage de 1314 caractères — un rendu complet en fait plus de 30 000,
  celui-ci est probablement partiel
· demandée(s) par le prompt et absente(s) du collage : section C-bis,
  blocs conf, ligne sets:, ligne dossiers_ouverts
```

**Cette ligne aurait été visible au tout premier import.** Elle est le coût de
quatre jours d'enquête, écrit en une phrase.

---

# DIAGNOSTIC — lot 8 : la ligne `Jeux`, le tournoi en cours, et le collage complet qui perdait la section C

Relevé du **19/08/2026**, sur une copie de la base servie (121 Mo, 279 sélections,
17 sessions) et sur l'archive des réponses `tennisapi` — 844 rencontres et
27 242 matchs de simple. Aucune mesure n'est reprise du brief sans vérification ;
**cinq le contredisent**, et la première a déplacé tout l'ordre de la session.

---

## §4 — Le collage complet arrive, et il rendait **moins** que le collage du tableau

**Ce point vient en tête parce que la mesure a renversé la question.** Le brief le
plaçait en avant-dernier, avec la réserve « s'il n'existe aucun import postérieur
au correctif, dis-le en une ligne et passe ». Il en existe, ils portent tout ce que
la machinerie attendait depuis le lot 1 — et l'import le jetait.

### Les quatre imports du 18/08 au soir

| Import | Session | Heure | Caractères | `dossiers_ouverts` | blocs `conf` dans le texte |
| ---: | ---: | --- | ---: | --- | ---: |
| 14 | 17 | 22:48 | **16 559** | `[M1…M6]` | **5** |
| 15 | 17 | 22:49 | 1 022 | absente | 0 |
| 16 | 17 | 22:50 | **17 780** | `[M1…M7]` | **5** |
| 17 | 17 | 22:50 | 1 155 | absente | 0 |

**Le geste a changé : deux collages complets sont arrivés.** Ils portent la ligne
`dossiers_ouverts`, ils portent cinq blocs de confiance chacun — sans clôture, les
trois accents graves ayant été mangés par le rendu, exactement comme le lot 1
l'avait établi — et `confidence.read_blocks` les lit tous les cinq.

**Et pourtant `picks.claim_raw_json` est NULL sur les dix sélections de la
session 17.** Rejeu des deux collages à travers `build_preview` :

| | Import 14 | Import 16 |
| --- | ---: | ---: |
| sélections détectées | **2** | **1** |
| blocs de confiance appariés | **0** | **0** |
| lignes refusées « exploratoire en palier sûr » | 3 | 4 |

Les lignes refusées **sont la section C**. Les sélections détectées sont la seule
section C-bis.

### Cause racine

`picks_import.EXPLORATORY_HEAD` cherchait `C-bis` **n'importe où dans la ligne** :

```python
EXPLORATORY_HEAD = re.compile(r"\bc\s*bis\b|selections? exploratoires?")
```

Or la section B en parle. Sur les deux collages, la bascule s'est déclenchée
**avant** le tableau de la section C, sur ces phrases :

```
… on ferme au-dessus des deux paliers sûrs, l'angle part en exploratoire (C-bis) …
… pas de fait nommé et daté de niveau 1-2, il part en C-bis
… mais vainqueur des deux derniers H2H dont février 2026 sur dur (voir C-bis)
```

Le gabarit lui-même en écrit une, ligne 671 : *« le fait qu'il soit rempli en
section C-bis ne dispense pas de ce commentaire »*. La bascule était donc
**structurellement atteignable**, pas accidentelle.

Une fois le drapeau posé, la section C entière tombe sous les deux refus propres à
C-bis, dont « une ligne en palier sûr n'y a rien à faire ». Les trois SAFE/FUN de
l'import 14 et les quatre de l'import 16 ont donc été **journalisés et non
importés** — puis le compte de blocs (5) ne tombait plus sur le compte de lignes
(2, puis 1), et `_attach_claims` refusait tout l'appariement, ce qui est son
comportement correct.

**C'est ce qui explique l'alternance des quatre imports.** Le collage complet
perdant le tableau principal, il a fallu recoller la seule section C juste après —
et celle-ci, arrivant sans blocs, force tout le lot en `lecture` (`ligne_absente`,
7 sélections sur 10 de la session).

### Le correctif, et ce qu'il rend

Les deux motifs s'ancrent désormais en tête de ligne, et les titres `A.` à `F.`
ferment la section exploratoire autant que son titre l'ouvre. Rejeu des deux
collages réels :

| | Import 14 | Import 16 |
| --- | ---: | ---: |
| sélections détectées | **5** (3 en C, 2 en C-bis) | **5** (4 en C, 1 en C-bis) |
| blocs de confiance appariés | **5 / 5** | **5 / 5** |
| `dossiers_ouverts` | `renseignee`, 6 repères | `renseignee`, 7 repères |

**Toute la machinerie des migrations 042, 043, 045 et 049 s'allume d'un coup**, et
elle attendait depuis le lot 1.

- Le motif se lit sur la **ligne brute** pour les lettres seules, et pas sur la
  ligne repliée : repliée, `C'est` commence par `c` suivi d'un espace, donc
  exactement comme `C. Tableau`. C'est le séparateur qui distingue un titre d'un
  début de phrase française. `C-bis`, lui, n'a pas d'homonyme et se passe du sien.
- Le **découpage en sections entre au banc de transport**, sixième format. Son
  absence est ce qui a laissé passer ce défaut : le banc testait chaque format
  **isolé**, jamais le rendu complet où la prose de la section B côtoie les deux
  tableaux. « Lu » s'y mesure sur les lignes de la section C et non sur leur
  total — les compter toutes aurait rendu le banc vert pendant la panne.

### Le défaut s'est reproduit deux fois de plus **pendant la session**

Deux imports sont arrivés à **15:59 et 16:00**, avant que le correctif soit
livré — et ils rejouent le même scénario, sur des données que je n'avais pas vues
en posant le diagnostic :

| Import | Heure | Caractères | Sélections entrées | Ce que le texte portait |
| ---: | --- | ---: | ---: | --- |
| 18 | 15:59 | **21 559** | **2**, toutes deux exploratoires | 5 blocs `conf`, 1 bloc `combo`, `sets:`, `dossiers_ouverts: [M1…M9]` |
| 19 | 16:00 | 1 426 | 5, toutes en `ligne_absente` | le seul tableau |

**Même alternance, troisième fois** : le collage complet perd sa section C,
l'utilisateur recolle le tableau seul, et les cinq sélections principales entrent
sans bloc. C'est la confirmation la plus forte du diagnostic — elle est arrivée
d'elle-même, sur un lot que je n'avais pas regardé.

Rejeu de l'import 18 à travers le code corrigé : **7 sélections** (5 en C, 2 en
C-bis), **5 blocs de confiance appariés**, **1 combiné rattaché — le premier de
l'histoire de la base** — et **9 repères de dossiers ouverts**.

### Une seconde ambiguïté, révélée par le correctif lui-même

Avec la section C rétablie, le compte de blocs cessait de tomber sur le compte de
lignes : 5 blocs pour **7** lignes (5 de C, 2 de C-bis), donc refus.

**Le gabarit rend les deux lectures légitimes.** Il demande « un bloc par ligne,
dans l'ordre du tableau » (`session_default.md.j2:559`) — et cette consigne est
posée **sous la section C**, quatre-vingts lignes avant que C-bis existe. Le
modèle l'a lue des deux façons, et c'est mesuré :

| Collage | Blocs | Lignes section C | Lignes C-bis |
| ---: | ---: | ---: | ---: |
| 14 (18/08) | 5 | 3 | 2 |
| 16 (18/08) | 5 | 4 | 1 |
| **18 (19/08)** | **5** | **5** | **2** |

L'appariement essaie donc **deux populations définies d'avance** — le tableau
entier, puis la seule section C — et **la somme de contrôle décide**. Ce n'est pas
retenir la lecture qui arrange, ce que le module refuse explicitement pour les
prompts : chaque ensemble est validé ou refusé **en entier** sur les affiches de
son prompt d'origine, et un ensemble mal choisi échoue sur ses paires. Un test le
garde : un bloc dont l'affiche ne correspond à rien fait toujours tomber le lot.

**Le gabarit étant hors périmètre, la levée de l'ambiguïté n'est pas faite** — une
phrase précisant si les lignes de C-bis portent un bloc supprimerait le besoin de
cette souplesse. Elle est notée pour l'arbitrage, avec les deux variantes du §2d.

### Ce que la session 17 dit quand même

Les dix sélections importées portent une information qui n'existait nulle part
avant :

| Cause de l'écrasement en lecture | Sélections |
| --- | ---: |
| `hors_dossiers` (import 14, collage complet) | 2 |
| `ligne_absente` (imports 15 et 17, collages du seul tableau) | 7 |
| aucun écrasement (import 16, dossier ouvert) | 1 |

**`hors_dossiers` est une observation sur le modèle, pas un défaut de collecte** :
la ligne a été lue, les repères résolus, et les deux sélections portaient sur des
matchs hors des six dossiers ouverts. C'est la première fois que ce chemin
fonctionne de bout en bout.

### Les trois réponses du §4, en une ligne chacune

- **Blocs `conf`** : 10 produits, 0 appariés avant correctif, **10 sur 10** après —
  et le cran calculé vaudra donc désormais quelque chose. L'écart calculé/déclaré
  ne se mesure pas encore : il demande un import passé par le code corrigé.
- **`dossiers_ouverts`** : présente sur **les trois** collages complets, avec
  **6, 7 et 9 repères** pour un budget réglé à 10. Le modèle s'approche donc de
  son budget sans l'épuiser, et **le budget n'était pas la contrainte** —
  exactement ce que le brief voulait savoir. À lire avec la réserve du §1c du
  lot 5 : le vivier de jambes se mesurait jusqu'ici en régime cassé.
- **Combinés et scores en sets** : `jambes` absent des deux collages du 18/08,
  **présent sur celui du 19/08** — et son rejeu à travers le code corrigé rattache
  **le premier combiné de l'histoire de la base**. `sets:` est présent sur deux
  des trois ; `set_scores` est passé de 5 à 21 lignes pendant la session.
  `combos` et `combo_legs` restent vides, faute d'un import passé par le code
  corrigé.

---

## §1 — La ligne `Jeux`

### §1a — Le filtre d'âge, mesuré sur une archive deux fois plus grande

Le lot 7 avait mesuré sur 564 rencontres et 2 767 appels. L'archive en porte
désormais **844 et 3 818**, et le constat se renforce.

| Âge du match à l'appel | Rencontres tentées | Timelines | Taux |
| --- | ---: | ---: | ---: |
| 0 – 7 j | 33 | 28 | **85 %** |
| 8 – 30 j | 44 | 37 | **84 %** |
| 31 – 90 j | 169 | 91 | **54 %** |
| **91 – 180 j** | **235** | **0** | **0 %** |
| **181 j et plus** | **362** | **0** | **0 %** |

**Âge maximum d'une rencontre servie : 76 jours.** Les huit plus anciennes servies
sont à 70, 71, 71, 73, 75, 75, 76 et 76 jours. 597 rencontres au-delà de 90 jours,
**zéro timeline**.

| Filtre | Rencontres tentées | Appels | Timelines | Appels / timeline |
| --- | ---: | ---: | ---: | ---: |
| aucun | 844 | 3 818 | 157 | 24,3 |
| **≤ 90 jours** | **247 (29 %)** | **689 (−82 %)** | **157 / 157** | **4,4** |

**Le gain réel est plus grand que celui du brief** : 82 % d'appels en moins et non
69 %, parce que les rencontres hors fenêtre brûlaient plus d'appels chacune que la
moyenne — une rencontre servie coûte peu (le premier essai aboutit neuf fois sur
dix), une rencontre absente coûte tous les essais.

**Une erreur de méthode attrapée avant d'écrire un chiffre.** Le premier relevé
groupait les appels par **paire de joueurs** et annonçait « âge maximum 359 jours,
28 timelines au-delà de 90 » — l'inverse du lot 7. La faute est dans le
regroupement : deux rencontres des mêmes joueurs à un mois d'écart sont deux
rencontres, et prendre la date la plus ancienne du groupe vieillissait
artificiellement les succès. Une rencontre est une **paire plus une grappe de dates
voisines**, ce que `_event_paths` produit — et alors les deux relevés concordent.

Trois gardes, et **leur ordre porte la règle** :

- l'**archive** passe avant le filtre : une timeline déjà payée se relit
  gratuitement quel que soit l'âge de la rencontre, et l'écarter perdrait une
  donnée qu'on possède pour économiser un appel qu'on ne ferait pas ;
- le **filtre** passe avant le plancher de quota : ce qui n'est pas demandé n'a pas
  à être budgété ;
- une rencontre hors fenêtre **saute**, elle n'interrompt pas le parcours : les
  lignes sont triées par date, mais une seule date aberrante ferait sinon perdre
  tout le fond de liste.

Le compte des rencontres hors fenêtre sort **à part** (`TimelineTally.too_old`) et
se journalise. Fondu dans les vides, il ferait lire une couverture qui s'effondre
là où il n'y a qu'un filtre qui travaille — et c'est lui qui dira le jour où la
rétention de la source aura bougé.

`TIMELINE_MAX_AGE_DAYS` se règle, zéro le désactive : c'est le seul moyen de
rejouer la mesure ce jour-là.

**Un test a dû être réparé, et il avait raison de casser.** Le test du seuil de
300 jeux datait ses quarante rencontres de janvier et février : hors fenêtre dès le
mois de mai. Il recevait une horloge implicite, donc il mesurait le jour où il
tournait. Il reçoit maintenant la sienne.

### §1b — La passe longue, et la seconde péremption que personne ne regardait

**Le brief demandait d'ouvrir la passe ; il manquait une condition qu'aucune mesure
n'avait cherchée.** `sync` saute un joueur dont l'agrégat a moins de 24 heures, et
ce saut emporte la collecte de timelines avec lui. Mesure au moment du départ :
**221 couples joueur/circuit sur 250 étaient frais**. Une reprise lancée le
lendemain d'un entretien aurait donc sauté 88 % de sa file et **rendu un passage
complet indiscernable d'un catalogue déjà couvert** — le défaut caractéristique du
projet, sur le chantier lui-même.

Deux fraîcheurs sont en jeu et ce n'en est pas une seule : un agrégat écrit ce
matin ne dit rien des timelines, qui sont un second étage de collecte et vivent
bien plus longtemps. `--reprise` lève la première et laisse la seconde.

La file passe de deux étages à trois — matchs à venir, joueurs des cinq derniers
**lots analysés** (`prompt_events`, ce qui est parti à l'analyse, jamais la
shortlist qui se vide), puis fond de catalogue. Mesure au départ : **256 joueurs,
dont 28 à venir et 111 des cinq derniers lots.**

**La passe est allée au bout.** Relevé final, 250 joueurs traités :

| | Avant le lot | Après |
| --- | ---: | ---: |
| joueurs porteurs de jeux | **12** | **239** |
| joueurs au seuil de 300 jeux | **0** | **14** |
| rencontres tentées | — | 4 853 |
| **timelines obtenues** | — | **1 970** |
| rencontres vides | — | 2 838 |
| ruptures d'alternance | — | 45 |
| **rencontres écartées sur leur âge** | — | **8 677** |
| appels consommés par cette passe | — | **876** |
| quota mensuel restant | 145 673 | **139 533** |

**Le filtre d'âge a écarté 8 677 rencontres sur 13 530**, soit **64 % des
tentatives** — et la passe n'a coûté que **876 appels**, le reste venant de
l'archive. Sans le filtre, ces 8 677 rencontres auraient coûté au minimum deux
appels chacune, soit plus de 17 000 de plus, pour zéro timeline.

Elle se relance sans état :

```bash
uv run myassistantbet-timelines --joueurs 0 --reprise
```

**Rien de ce qui a déjà été payé ne se repaie** : `archived_timeline` rend chaque
timeline obtenue depuis `api_responses`, et un `result` vide archivé compte comme
vu. La passe a d'ailleurs été **arrêtée et relancée en cours de route** (voir §1c),
et le coût de ce redémarrage se limite aux appels `matches-played`, soit un par
joueur.

Le job planifié (`TIMELINES_JOB_ID`, 30 min après le scan) continue d'avancer par
lots de 12 entre deux passes longues, sans `--reprise` : l'entretien quotidien n'a
pas de raison de forcer la péremption.

### §1c — Le rendu réel, et le défaut le plus coûteux du lot

**Refaire le rendu était la consigne, et c'est ce qui a trouvé le défaut.** À
mi-passe, la base était passée de **12 joueurs porteurs de jeux à 177**, dont
**7 au seuil de 300**. Les blocs de ces sept joueurs ne rendaient **toujours pas**
la ligne `Jeux`.

L'absence d'une ligne sous son seuil est son comportement normal. **L'échec avait
donc exactement la même sortie que le cas ordinaire** — sixième occurrence du motif
du projet, et cette fois sur la ligne que trois lots consécutifs essayaient de
faire sortir.

**Cause racine.** `result.startTimestamp` est un **entier epoch** — `1780565400` —
et il était lu `str(value)[:10]`, ce qui rend les dix premiers **chiffres**. Une
chaîne qui ressemble à une date par sa longueur et n'en est pas une.
`_store_player` rapproche ensuite les jeux de leur surface **par cette date** :
aucun rapprochement ne tombait, et les jeux n'atteignaient donc que l'agrégat
**toutes surfaces**, seul cas où le filtre est court-circuité (`if not surface`).

Or `serve_lines` est appelé avec la surface du tournoi, `competitions.surface`
étant renseignée sur les tournois de tennis. C'est donc l'agrégat **par surface**
qui est lu, et il portait `served = 0` partout.

| Joueur | jeux, toutes surfaces | jeux, agrégat `Hard` |
| --- | ---: | ---: |
| Taylor Fritz | 316 | **0** |
| Alexandra Eala | 300 | **0** |
| Zeynep Sonmez | 301 | **0** |

**La ligne `Jeux` était donc inatteignable sur tous les blocs**, quel que soit le
volume collecté, et l'aurait été après une passe complète de 60 000 appels.

**Deux correctifs, et le second compte plus que le premier :**

- `_from_epoch` lit le champ pour ce qu'il est. La fixture du lot 4 porte déjà la
  valeur réelle de la source (`1786892400`) : un test sur cette seule fonction
  aurait suffi, et il n'existait pas ;
- **le rapprochement jeu / surface cesse de passer par la date.** `collect_games`
  sait quelle ligne de service a demandé quelle timeline — c'est le seul endroit où
  le couple est certain — et la surface est désormais **portée** par le jeu. Le
  rapprochement par date serait resté fragile une fois réparé : la timeline se
  trouve parfois à `J-1`, et les deux dates ne coïncident alors plus.

Le test qui manquait vérifie **la propriété et non une valeur** : au moins un
agrégat par surface porte des jeux. Vérifié en réintroduisant le défaut à
l'identique — il tombe, avec son message.

**Et la ligne sort.** Rendu réel sur la base servie, après la passe, drapeau
`SERVE_LINES_ENABLED=1` — c'est-à-dire **en production** :

```
  Service    Nuno Borges 66.8% 1re · 75.0% s/1re · 48.5% s/2e · 9.2% aces · 9.5% df
             (Hard, 52 sem., 2530 pts de service, arretees au 18/08) [tennis-api.com]
  Retour     Nuno Borges 34.7% pts · 37.8% BP converties (2495 pts recus)
  Jeux       Nuno Borges tenue 88.1% · break 25.2% (159 jeux servis)
             (toutes surfaces, arretees au 18/08 — le seuil de jeux ne s'atteint
              pas par surface)
```

Tenue, break, compte de jeux servis, portée et `as_of` : la ligne porte les quatre
choses que le §1c demandait de vérifier.

**Avant / après sur des blocs réels**, ATP et WTA hors top 50 — le bloc passe de
15 à 19 lignes :

```
ATP — Taylor Fritz – Christopher O'Connell   [ATP Cincinnati Open]
  Jeux      Taylor Fritz tenue 88.8% · break 19.9% (160 jeux servis)
            Christopher O'Connell non disponible
            (toutes surfaces, arretees au 18/08 — …)

WTA — Barbora Krejcikova – Sara Bejlek   [WTA Cincinnati Open]
  Jeux      Barbora Krejcikova tenue 77.1% · break 45.4% (157 jeux servis)
            Sara Bejlek non disponible
            (toutes surfaces, arretees au 16/08 — …)

WTA — Alexandra Eala – Amanda Anisimova   [WTA Cincinnati Open]
  Jeux      Alexandra Eala tenue 71.7% · break 32.4% (152 jeux servis)
            Amanda Anisimova non disponible
```

**Une moitié manquante se dit**, elle ne se tait pas : le bloc porte l'autre
joueur, et un silence se lirait comme un oubli de collecte. C'est le comportement
attendu à 14 joueurs au seuil sur 250 — et c'est aussi la mesure de ce qu'il reste
à couvrir.

**Et le rendu réel a trouvé un second blocage, sous le premier.** Une fois les jeux
arrivés dans les agrégats par surface, la ligne ne sortait toujours pas :

| Joueur | jeux, toutes surfaces | `Hard` | `Grass` | `Clay` |
| --- | ---: | ---: | ---: | ---: |
| Taylor Fritz | **316** | 105 | 211 | 0 |

**`collect_games` s'arrête à 300 jeux toutes surfaces confondues** — c'est sa
règle, et elle est juste. Il en découle qu'**aucun agrégat par surface ne peut
atteindre 300**. Mesure sur la base servie : **zéro ligne par surface au-dessus du
seuil**, maximum observé **225**. Et `serve_lines` est appelé avec la surface du
tournoi, `competitions.surface` étant renseignée sur tous les tournois de tennis.

Le blocage n'était donc pas de volume : il était **structurel**, et aucune passe,
si longue soit-elle, ne l'aurait levé.

**Le seuil de 300 n'est pas abaissé** — c'est un interdit du brief, et il est
juste : une ligne `Jeux` sur 155 jeux serait lue comme un fait. Ce qui change est
la **portée** : quand l'agrégat de surface n'atteint pas le seuil et que celui de
toutes surfaces l'atteint, la ligne se rend depuis le second **et le déclare**.

```
  Jeux       Nuno Borges tenue 88.1% · break 25.2% (159 jeux servis)
             (toutes surfaces, arretees au 18/08 — le seuil de jeux ne s'atteint
              pas par surface)
```

Le module avait déjà ce troisième état pour les **points de service**
(`fell_back`), avec la même raison : un repli tu serait une affirmation fausse, un
repli dit est une information de plus. Il lui manquait sur l'axe des jeux, parce
que `load_aggregate` ne connaît qu'un seuil et que les deux grandeurs n'ont pas le
même. La date est portée par la ligne `Jeux` elle-même : les deux agrégats ne sont
plus forcément le même relevé.

**Le choix n'est pas entre deux portées, il est entre une ligne repliée qui se
déclare et pas de ligne du tout.**

**Conséquence sur la passe** : elle tournait avec le code d'avant. Elle a été
arrêtée à 4 204 appels et relancée avec le correctif. **Rien n'est repayé** —
l'archive `api_responses` rend chaque timeline déjà obtenue sans un appel, ce qui
est exactement la propriété que le lot 6 avait construite pour cette raison-là.

---

## §2 — Le tournoi en cours entre dans le bloc

### §2a — Ce que `matches-played` sert par match, mesuré sur 27 242 matchs

Relevé sur les 276 réponses `matches-played` archivées, soit **27 242 matchs de
simple**, dont 217 du tournoi en cours.

| Ce que le brief demandait | Servi ? | Taux |
| --- | --- | ---: |
| **score set par set** (`result`) | **oui** | **100 %** |
| **durée** | **NON — aucun champ, sous aucun nom** | 0 % |
| aces | oui | 99,9 % |
| doubles fautes | oui | 100 % |
| 1re balle et son dénominateur (`firstServe` / `firstServeOf`) | oui | 100 % |
| points gagnés sur 1re, et dénominateur | oui | 100 % |
| points gagnés sur 2e, et dénominateur | oui | 100 % |
| balles de break converties, et dénominateur | oui | 100 % |
| total de points gagnés | oui | 100 % |
| **le tour** | **partiellement** — voir ci-dessous | 100 % |
| nom de l'adversaire | oui | 100 % |
| surface, tournoi, catégorie | oui | 100 % |

Servis mais **inégaux**, donc inutilisables comme ligne : `fastestServe` (10,9 %),
vitesses moyennes de service (10,6 %), montées au filet (14,8 %), fautes directes
et coups gagnants (15,2 %). `best_of` et `draw_size` sont dans le schéma et **nuls
sur 27 242 lignes**.

**La durée n'est servie par aucun des deux endpoints.** Le lot 4 l'avait établi
pour `event/get` ; c'est vrai aussi de `matches-played`, et c'est vérifié
exhaustivement plutôt que sur trois exemples — aucune clé contenant `dur`, `time`
ou `minut` n'existe dans la charge utile.

**Le tour est servi et ne se nomme pas.** `roundId` est présent à 100 %, mais c'est
un **entier opaque** : seize valeurs observées, aucun libellé nulle part dans la
charge utile, et `draw` est un **numéro de place dans le tour** — non une taille de
tableau, vérifié sur la distribution croisée. Il **ordonne** les tours à
l'intérieur d'un tournoi (Cincinnati 2026 : 1 → 3 → 4 → 5 → 6 par date croissante)
mais ne les nomme pas. La ligne porte donc **la date**, qui est un fait, plutôt
qu'un `Q1` qui serait une invention — même règle que partout, sauf qu'ici il n'y a
même pas de libellé à déduire.

**Vocabulaire fermé du champ `result`**, et il porte exactement ce que §2c
réclamait : les sets, plus `ret.` (789 occurrences) et `w/o` (203). Rien d'autre.

### §2b — La ligne `Ici`

Une ligne, deux fragments par joueur, insérée **après `Non joue`** et avant
`Historique` / `Fraicheur`. Le brief demandait « après `Parcours` » : `Non joue`
s'intercale, et c'est délibéré — le code dit depuis le lot précédent que ces
deux-là se complètent et doivent rester adjacents, un forfait retirant un nom du
parcours.

Rendu réel, sur la base servie, drapeau levé en test — **avant / après**, ATP :

```
AVANT
  Parcours   Taylor Fritz Alex Michelsen (1847), Daniel Merida Aguilar (1847)
             | Christopher O'Connell Dane Sweeny (1472), Alexander Shevchenko (1669),
               Kamil Majchrzak (1795), Casper Ruud (1954) [vu depuis le 11/08]
  Non joue   Christopher O'Connell — Joao Fonseca (1934) le 18/08 15:00 UTC,
             forfait adverse, non disputee

APRES — la même chose, plus :
  Ici        Taylor Fritz 16/08 bat Alex Michelsen 6-3 6-4 [releve au 18/08]
             service ici 53.5% 1re · 81.6% s/1re · 6 df (1 match, 71 pts)
             Christopher O'Connell 11/08 bat Dane Sweeny 6-7(3) 6-2 7-5
             | 13/08 bat Alexander Shevchenko 6-4 6-1 | 14/08 bat Kamil Majchrzak 6-4 7-6(5)
             | 16/08 bat Casper Ruud 7-5 1-2 (abandon) | 18/08 forfait de Joao Fonseca
             [releve au 18/08]
             service ici 58.1% 1re · 76.0% s/1re · 5 df (4 matchs, 265 pts)
```

WTA, hors top 50, avec un joueur entrant en lice — le second cas demandé :

```
  Ici        Aryna Sabalenka 16/08 bat Talia Gibson 6-2 7-6(2) [releve au 18/08]
             service ici 59.3% 1re · 70.8% s/1re · 2 df (1 match, 81 pts)
             Sara Bejlek 14/08 bat Karolina Pliskova 6-0 6-2
             | 16/08 bat Barbora Krejcikova 7-6(5) 6-4 [releve au 18/08]
             service ici 61.5% 1re · 71.2% s/1re · 7 df (2 matchs, 130 pts)
```

**Le vainqueur se lit sur la position, et c'est la mesure qui l'impose contre
l'intuition.** Le réflexe du projet — lire le fait dans la donnée plutôt que dans
une convention — désignait le score : compter les sets gagnés. Recoupement des deux
lectures contre `tennis_matches` sur **12 049 rencontres** :

| Méthode | Juste | Faux | Indécidable |
| --- | ---: | ---: | ---: |
| `player1` est le vainqueur | **12 046 — 99,98 %** | 3 | 0 |
| vainqueur déduit des sets gagnés | 11 910 — 98,85 % | 16 | 123 |

**Quinze des seize erreurs de la seconde méthode sont des abandons** : sur un
`4-6 3-6 3-1 ret.`, celui qui menait au tableau d'affichage est celui qui a perdu.
`ret.` casse le sens du score sans toucher à la position. Le réflexe reste juste,
la mesure tranche autrement — et la ligne le documente pour qu'il ne se re-dérive
pas.

Corollaire vérifié sur un cas réel : la position dit aussi **le sens d'un forfait**,
et `Non joue`, qui le tire de nos propres scans, tombe sur la même lecture
(« forfait adverse » contre « forfait de Joao Fonseca »).

Le score se rend **du point de vue du joueur nommé**, comme `H2H` et `Aller` : deux
conventions dans le même bloc se liraient à l'envers. Le test ne vérifie pas une
valeur mais l'**invariant** — le camp que le verbe annonce est celui qui mène au
score, quel que soit le côté depuis lequel la source écrit.

**L'identifiant de tournoi se lit dans la fenêtre de notre édition**
(`tennis_round.edition_for`, déjà écrit) et jamais sur le dernier match du joueur :
un entrant n'a rien joué ici, et son dernier tournoi est celui de la semaine
passée. Il se partage entre les deux joueurs — celui qui a joué le donne à celui
qui entre, sans quoi l'entrant se tairait là où sa ligne a le plus à dire. Une fois
connu, **tous** les matchs qui le portent sont pris, y compris ceux joués avant
notre premier scan : c'est précisément ce que `Parcours` ne peut pas faire.

**Deux défauts trouvés en rendant pour de vrai**, et aucun test unitaire ne les
aurait vus :

- la **date du relevé** manquait. Sur le rendu du 19/08, le match de Jaime Faria
  contre Adam Walton — joué après le dernier relevé — manquait à `Ici` alors que
  `Parcours` le portait, et rien ne le disait. La ligne porte donc
  `[releve au 18/08]`, **par joueur** : deux profils se rafraîchissent à deux
  instants différents ;
- **deux écritures du même instant ne se comparent pas comme des chaînes.** La
  source écrit `2026-08-14T12:00:00.000Z`, nos événements `2026-08-14T12:00:00Z`,
  et le point trie avant le `Z` : le **premier match d'un tournoi** tombait juste
  avant le début de sa propre fenêtre, silencieusement et seulement pour lui.
  Trouvé par un test, pas par une relecture.

Coût : **aucun appel**. La charge utile est relue dans `api_responses`, même idiome
qu'`archived_timeline`, et un test le vérifie **sans simuler la moindre route**.

`Ici` n'entre **pas** dans `CONTEXT_EXPECTED`, comme les quatre lignes de service
avant elle : le dénominateur de densité doit bouger le jour où la ligne rend
vraiment, c'est-à-dire à l'activation, et pas au commit.

### §2c — Ce que ça change ailleurs, et une contradiction mesurée

**`Parcours` : ne pas l'alléger, et surtout pas maintenant.** Il porte deux choses
qu'`Ici` n'a pas — l'**Elo des adversaires**, qui distingue un parcours facile d'un
parcours d'usure, et la **fenêtre de nos scans**. Et il s'est révélé plus **frais**
qu'`Ici` sur un cas réel (le match de Faria contre Walton). Les deux populations
sont différentes : `Ici` remonte plus loin dans le tableau, `Parcours` descend plus
près de maintenant. Proposition, non appliquée : ne rien changer tant qu'`Ici`
n'est pas activée et mesurée sur quelques lots.

**`Fraicheur` : intacte.** Elle décrit le retard de `tennis-data.co.uk`, source
hebdomadaire et distincte, qui alimente `Forme` / `Usure` / `Profil` / `Marge`.
`Ici` ne la remplace en rien — et c'est exactement la confusion qui a coûté une
conclusion au lot 7.

**`Non joue` : une contradiction réelle, et c'est `Ici` qui la révèle.** Sur
Madison Keys – Xiyu Wang, le bloc rend :

```
  Non joue   Xiyu Wang — Bianca Andreescu (1714) le 13/08 14:00 UTC,
             adversaire remplace, non disputee
  Ici        Xiyu Wang 13/08 bat Polina Kudermetova 6-3 6-2
             | 13/08 bat Bianca Vanessa Andreescu 6-0 6-4 | …
```

La source rapporte un match **joué**, avec son score et ses statistiques de
service. Notre `match_outcome_type = 'replaced'` vient de la règle « un joueur ne
dispute qu'une rencontre par journée de tournoi, et c'est la plus récemment créée
qui tient » — dont `CLAUDE.md` note la limite : *« un tableau retardé par la pluie
peut faire jouer deux simples dans la même journée. Le cas ne s'observe pas en
base. »*

**Il s'observe maintenant.** Xiyu Wang a bien joué deux simples le 13/08, et la
dérivation a produit un faux positif. Ce n'est **pas corrigé ici** : changer
`tennis_load` est une décision, et le drapeau étant bas rien ne part en production
avec la contradiction. C'est **le point à trancher avant l'activation**, et la piste
est écrite plus bas.

### §2d — Les deux variantes de gabarit, écrites et **non appliquées**

Texte actuel, `session_default.md.j2` lignes 180-189 :

> · **Ses matchs déjà joués dans ce tournoi-ci** — la recherche la plus rentable
> du lot, et la première à faire : aucune de nos sources ne les porte. Pour chaque
> joueur et chaque tour déjà disputé — score set par set, durée, et si le site du
> tournoi ou de l'ATP/WTA les publie, les statistiques de service (aces, doubles
> fautes, % de première balle, balles de break sauvées et converties) — elles
> vivent derrière l'onglet « Stats » […] Un tour non trouvé s'écrit « non trouvé ».

**La phrase « aucune de nos sources ne les porte » devient fausse le jour où `Ici`
s'active.** C'est le seul point non négociable des deux variantes : une affirmation
fausse dans la consigne qui commande la recherche la plus chère du lot est le pire
endroit du gabarit où en laisser une.

#### Variante A — « allégée » : la puce reste et ne demande que ce qui manque

> · **Ce que la ligne `Ici` ne porte pas de ses matchs dans ce tournoi.** Elle
> donne, pour chaque tour déjà disputé, l'adversaire, le score set par set et le
> service agrégé du tournoi, avec la date de son relevé — ne les cherche pas, et
> ne les refais pas de zéro. Va chercher les trois choses qu'aucune source ne
> sert : la **durée** de chaque match, les conditions réelles du court (session,
> vent, chaleur, court central ou annexe), et le **double** s'il est engagé sur
> place. Un match postérieur à la date de relevé de la ligne manque : celui-là se
> cherche. Un tour non trouvé s'écrit « non trouvé ».

- **On gagne** : la recherche cesse de re-trouver ce que le bloc porte déjà. Mesure
  sur le lot du 18/08 — le bloc `conf` de M3 citait *« Tirante : Choinski
  7-6(9) 6-7(7) 7-6(4) en 2h56 puis Djokovic 2-6 6-4 6-4 en 2h44 »*, et `Ici` rend
  aujourd'hui `13/08 bat Jan Choinski 7-6(9) 6-7(7) 7-6(4) | 15/08 bat Novak
  Djokovic 2-6 6-4 6-4`. **Les scores sont identiques ; seules les durées ne sont
  pas dans le bloc.** Le budget se reporte sur elles.
- **On garde** : la durée, qui est le seul élément décisif de la puce qu'aucune
  source ne sert, et le repli explicite quand le relevé a pris du retard.
- **On perd** : rien de mesurable. La puce reste, donc le coût en tokens aussi —
  environ le même, la liste des statistiques de service étant remplacée par la
  liste de ce qui manque.

#### Variante B — « retirée » : la puce disparaît

La puce est supprimée ; les conditions de court et le double rejoignent les puces
« Surface et conditions » et « Charge » qui existent déjà et les mentionnent ; la
durée est ajoutée à « Charge ».

- **On gagne** : **213 tokens** de préambule tennis (mesurés avec
  `prompt.estimate_tokens`), et un dossier de
  recherche libéré par bloc — le budget en ouvre 10, et la puce en consommait un
  par joueur ayant joué.
- **On perd** : la **durée**, définitivement. C'est le seul substitut d'`Usure` au
  niveau du match, et `Usure` compte les jeux et non les minutes : deux matchs de
  22 jeux à 1h05 et à 2h33 y sont identiques. Le lot 3 a établi qu'aucune source
  automatisable ne la sert ; la retirer du gabarit, c'est décider de ne plus jamais
  l'avoir. On perd aussi le repli quand le relevé a pris du retard.

**Recommandation, et elle n'engage pas** : la variante A. La mesure qui la porte
est que la recherche a effectivement ramené, sur le dernier lot réel, exactement ce
que la ligne rend — sauf les durées. Retirer la puce reviendrait à supprimer une
recherche dont on vient de mesurer qu'elle rapporte encore une chose.

**Aucune des deux n'est appliquée**, et l'arbitrage appartient à l'utilisateur.

---

## §3 — Le football : rien à construire, et c'est un résultat

### §3a — Le fournisseur, le plan, le quota

Lus dans les en-têtes et dans `/status`, qui font foi :

| | |
| --- | --- |
| fournisseur | **API-Football** (`v3.football.api-sports.io`), appelé directement — pas via RapidAPI |
| plan | **Pro**, actif jusqu'au **06/09/2026** |
| quota | **7 500 appels / jour** (`x-ratelimit-requests-limit`), **300 / minute** (`x-ratelimit-limit`) |
| consommé au moment de la sonde | 0 sur 7 500 |

**Première affirmation du brief contredite : la clé ne « dort » pas dans le
`.env`.** Elle est en service et l'a toujours été — la base porte **323 relevés de
contexte** issus de ce fournisseur, sur onze types de lignes. Le trou est ailleurs.

**Piège rencontré pendant la sonde, et il est documenté dans le dossier** : le
dépassement de débit arrive en **HTTP 200** avec un objet `errors`, donc une
réponse « vide » n'est pas une absence de couverture. La première passe l'a
rencontré sur cinq compétitions ; la sonde a été refaite avec espacement et
réessai, et aucune conclusion n'est tirée d'une réponse dont l'erreur applicative
n'a pas été écartée.

### La couverture, compétition par compétition

Deux mesures indépendantes : la **couverture déclarée** par `/leagues` et ce que
`/injuries?league=&season=2026` **sert réellement**.

| Ligue | Compétition | Déclaré | Lignes servies | Joueurs |
| ---: | --- | --- | ---: | ---: |
| 94 | Primeira Liga — Portugal | `false` | **0** | 0 |
| 144 | Belgium First Div | `false` | **0** | 0 |
| 218 | Austrian Bundesliga | `false` | **0** | 0 |
| 141 | La Liga 2 — Espagne | `false` | **0** | 0 |
| 179 | Premiership — Écosse | `false` | **0** | 0 |
| 207 | Swiss Superleague | `false` | **0** | 0 |
| 169 | Super League — Chine | `false` | **0** | 0 |
| 307 | Saudi Pro League | `false` | **0** | 0 |
| 848 | UEFA Conference League | `false` | **0** | 0 |
| 3 | UEFA Europa League | `false` | **0** | 0 |
| 81 | DFB-Pokal | `false` | **0** | 0 |
| 48 | EFL Cup | `false` | **0** | 0 |
| 772 | Leagues Cup | `false` | **0** | 0 |
| 135 | **Serie A — Italie** | `false` | **0** | 0 |
| **103** | **Eliteserien — Norvège** | **`true`** | **1 300** | **208** |
| **203** | **Turkey Super League** | **`true`** | **56** | **27** |
| **88** | Dutch Eredivisie | `true` | 205 | 64 |
| 39 | EPL *(témoin)* | — | 38 | 19 |
| 61 | Ligue 1 *(témoin)* | — | 12 | 6 |
| 140 | La Liga *(témoin)* | — | 86 | 38 |

**La couverture déclarée est exacte, sans une exception.** Là où le fournisseur
annonce `injuries: false`, l'endpoint ne sert rien ; là où il annonce `true`, il
sert. La règle du projet — *« la couverture déclarée par le fournisseur fait
foi »* — est validée sur seize compétitions au lieu d'être supposée.

**Deuxième et troisième affirmations du brief contredites.** Il nomme sept
compétitions dites « non interrogées », dont **la Norvège et la Turquie — qui sont
couvertes**, et l'étaient déjà avant ce lot (10 relevés sur 10 et 9 sur 9 dans la
base). À l'inverse, il ne cite pas la **Serie A**, qui déclare `injuries: false`
pour 2026 : un championnat du top 5 européen, et le fait n'était nulle part.

### Le retard : non mesurable, et la cause est structurelle

**On ne conserve qu'un instantané par match.** `context` porte **323 lignes
`injuries` pour 323 événements distincts** : la table est indexée par
(événement, type) et chaque enrichissement **écrase** le précédent. Il n'existe
donc aucune série temporelle d'où tirer un délai — même forme que `commence_time`
avant la migration 040 et que `odds` avant la 048, et le correctif serait de la
même famille. **Non construit** : ce n'est pas dans le périmètre du lot, et ça se
décide.

Ce que les données permettent, en revanche, c'est de **recouper la liste du
fournisseur contre ce que la recherche a trouvé le même jour**. Les deux collages
complets du 18/08 citent 28 absents nommés et datés, avec leur éditeur :

| Match | Cités par la recherche | Déjà dans le bloc |
| --- | ---: | --- |
| Atlético Madrid – Málaga | 11 | **7** — manquent Llorente, Baena, Musso, Dotor |
| Celtic – LASK | 4 | **4** |
| NEC Nijmegen – Bodø/Glimt | 7 | **5** — manquent Nuytinck, Linssen |
| Hapoel Be'er Sheva – Sabah FK | 2 | **2** |
| Slovan Bratislava – NK Celje | 5 | **4** — manque Ibrahim |
| **Total** | **29** | **22 — 76 %** |

**Le bloc portait déjà les trois quarts de ce que la recherche est allée
chercher** — Sørloth avec son « Muscle Injury », Jota, Oxlade-Chamberlain, Šporar,
Schuurs. Et trois des sept manquants (Álvarez, Llorente, Baena, Musso) sont décrits
par la recherche elle-même comme *« une semaine d'entraînement, non attendus
titulaires »* : une décision de rotation, que `/injuries` n'a aucune raison de
porter. Le désaccord réel se réduit à **Dotor, Nuytinck, Linssen et Ibrahim**.

C'est une information sur le **gabarit** et non sur la source : la recherche
football dépense son budget à re-trouver ce que le bloc dit déjà. Hors périmètre
ici, et noté.

### §3b — Ce qu'on en fait : rien, et la branche « nulle part » est la réponse

Le brief prévoyait les deux issues. C'est la seconde.

- **Il n'y a pas de couverture à récupérer.** Zéro ligne servie sur les treize
  compétitions déclarées non couvertes, réessais compris.
- **L'état « non interrogés » n'est pas factuellement faux : il est exact.** Il dit
  *« le fournisseur ne couvre pas cette compétition, ça ne changera pas »*, ce qui
  est précisément ce que la mesure établit. Le corriger le rendrait faux.
- **Et le substitut existe déjà, et il fonctionne.** `Effectif` reconstruit les
  absents depuis les feuilles de match là où `injuries` est faux et `lineups` vrai.
  Rendu réel de **120 blocs football**, sans un appel :

| | |
| --- | ---: |
| substitut possible (`injuries=false` **et** `lineups=true`) | **51 blocs** |
| feuilles de match effectivement collectées | **51 / 51 — 100 %** |
| ligne `Effectif` rendue | **21 / 51 — 41 %** |

**Le mécanisme n'est ni cassé ni sous-collecté : il tire partout où il peut.** Les
59 % restants sont le comportement documenté — *« Rien quand personne ne manque :
écrire "aucun" affirmerait un effectif au complet, ce que des feuilles ne peuvent
pas prouver. »*

**Quatrième affirmation du brief contredite : la densité football.** Elle n'est pas
« 31-38 % », elle est **médiane 42 %**, et surtout elle est **bimodale** :

| Densité | Blocs |
| --- | ---: |
| 0-9 % | 1 |
| 20-29 % | 2 |
| **30-39 %** | **43** |
| 40-49 % | 20 |
| 50-59 % | 8 |
| 60-69 % | 18 |
| 70-79 % | 3 |
| **90-100 %** | **25** |

Le « 31-38 % » du brief décrit le mode principal, pas le lot. Un cinquième des
blocs football est **complet ou presque**, et ce qui sépare les deux masses est
l'avancement de la saison, pas la couverture des absents : la ligne `Absents` sort
sur **119 blocs sur 120**, et 62 d'entre eux portent une liste réelle.

**Décision : ne rien construire.** Aucune source d'absences n'est branchée, aucun
état n'est ajouté à la ligne, et l'instrumentation demandée par le brief
(« nombre de blocs football portant une liste réelle, par compétition et par
mois ») ne se construit pas non plus — elle mesurerait une couverture qu'on vient
de constater fixe et déclarée à la source.

Ce qui **améliorerait** vraiment les blocs football n'est donc pas une source de
plus : c'est que la section A cesse de rechercher les absents que le bloc porte
déjà. C'est une modification de gabarit, hors périmètre, et c'est le pendant
football du §2d.

---

## §5 — Dettes et arbitrages

### La métrique du retard : spécification inchangée, effectif identique

**Rien n'a bougé.** Relevé du jour contre celui du lot 4 :

| | Lot 4 / lot 7 | 19/08 |
| --- | ---: | ---: |
| sélections tardives tranchées | 52 | **52** |
| gagnées / perdues | 32 / 20 | **32 / 20** |
| `late_minutes` renseignées | 52 | **52** |
| étendue (minutes) | 12 – 1 557 | **12 – 1 557** |
| médiane | 133 | **133** |

La spécification du §4 du lot 4 est **à jour telle quelle**. Toujours non
implémentée, conformément au brief.

*Note de méthode, parce qu'elle a failli entrer dans ce rapport comme une
découverte* : la première requête a rendu « 52 tardives, 0 gagnée, 0 perdue » et
ressemblait à une contradiction du brief. C'était ma requête : le vocabulaire de
`picks.result` est `win` / `loss` / `pending` / `void`, pas `gagne` / `perdu`. Une
mesure qui contredit un chiffre stable de trois lots mérite d'abord un doute sur
la mesure.

### Le registre des chemins d'écriture

**Aucun chemin ajouté par ce lot.** Il n'insère dans aucune des quatre tables
gardées (`picks`, `combos`, `combo_legs`, `set_scores`) : la ligne `Ici` et le
filtre d'âge lisent, et le correctif de section change ce qui **arrive** à
`add_pick` sans toucher à son appelant. `tests/test_write_paths.py` lit la source
et fait échouer la suite sur un `INSERT` non déclaré : il passe.

### `changelog_mesure`

Trois entrées, **à leur date d'effet** :

| # | Date | Portée | Ce qui change |
| ---: | --- | --- | --- |
| **15** | 19/08 | `ingestion` | La section C d'un collage complet est de nouveau lue. Les blocs `conf` s'apparient, `dossiers_ouverts` se résout. **Change la composition de toutes les populations à partir de cette date.** |
| **16** | 19/08 | `gabarit` | **La ligne `Jeux` sort pour la première fois.** Le bloc tennis porte une ligne de plus, sur les joueurs au seuil — et `SERVE_LINES_ENABLED` étant à 1, c'est une vraie date d'activation. |
| **17** | 19/08 | `ingestion` | Filtre d'âge à 90 jours sur les timelines : la couverture de `Jeux` monte pour cette raison, et non parce que la source aurait changé. |
| *(non écrite)* | — | `gabarit` | La ligne `Ici` **n'entre pas au journal** : son drapeau est bas, elle ne change rien à ce que le modèle lit. Elle y entrera à son activation, avec sa vraie date — pas celle du commit. Même règle que `SERVE_LINES_ENABLED`. |

**La première est la plus importante du journal depuis le lot 1**, et elle mérite
d'être lue comme telle : la population « sélections portant un cran calculé »
commence au 19/08/2026, et les 279 sélections antérieures n'en auront jamais.

### `CURRENT_EVENT_LINE_ENABLED` reste bas

Conformément au brief, et la raison tient toute seule : la coupe budget / lignes de
service est déjà jointe au 18/08. Une troisième variable à la même date rendrait
les trois effets indissociables, et le journal des mesures existe pour qu'ils se
découpent.

**Un point à trancher avant l'activation**, et c'est le seul : la contradiction du
§2c entre `Non joue` et `Ici` sur un match réellement joué deux fois dans la même
journée de tournoi. Trois issues possibles, aucune décidée ici :

1. laisser les deux lignes, la contradiction étant visible et le lecteur arbitrant
   — c'est ce que le projet fait déjà pour le terrain neutre non vérifiable ;
2. faire de `Ici` la source d'autorité sur `match_outcome_type` — la source
   rapporte, nos scans programment, et un score avec ses statistiques de service
   est une preuve qu'un match a eu lieu ;
3. lever la règle « une rencontre par journée de tournoi » quand la source en
   rapporte deux.

La 2 est la plus proche de la règle du projet (« quand une ligne paraît fausse,
chercher d'abord si le bloc ne porte pas déjà le fait qui la contredit »), et c'est
aussi la plus lourde. Elle ne se décide pas dans un lot qui n'active rien.

---

## Ce que la mesure a contredit — lot 8

**Cinq affirmations du brief**, et la première a déplacé tout l'ordre de la
session.

| Ce que le brief affirmait | Ce que la mesure dit |
| --- | --- |
| §4 est un point de vérification, « s'il n'existe aucun import postérieur, passe » | Il en existe deux, et ils révèlent que **le collage complet rendait moins que le collage du tableau** — le correctif d'hier soir était annulé par un motif de lecture. Point le plus lourd du lot. |
| §1 est « mécanique » : un filtre d'âge, puis la passe | Le filtre l'était. Mais la ligne `Jeux` était **inatteignable sur tous les blocs** pour une raison sans rapport — un horodatage lu comme une date — et aucun volume collecté ne l'aurait fait sortir. Trouvé en refaisant le rendu, comme le §1c l'exigeait. |
| une clé API-Football « dort » dans le `.env` | Elle est en service depuis toujours : **323 relevés de contexte** en base, plan Pro, 7 500 appels/jour. |
| la ligne `Absents` dit « non interrogés » sur Portugal, Belgique, Autriche, **Norvège**, Liga 2, **Turquie**, Trophée des Champions | **La Norvège et la Turquie sont couvertes**, et l'étaient déjà (10/10 et 9/9 relevés). En revanche la **Serie A** ne l'est pas, et le brief ne la cite pas. |
| les blocs football sont « à 31-38 % de densité » | Médiane **42 %**, distribution **bimodale** : 43 blocs entre 30 et 39 %, mais **25 entre 90 et 100 %**. |

**Et deux de mes propres mesures ont été reprises avant d'être écrites** :

- le premier relevé du filtre d'âge annonçait « âge maximum 359 jours, 28 timelines
  au-delà de 90 » — l'inverse du lot 7. La faute était dans mon regroupement : deux
  rencontres des mêmes joueurs à un mois d'écart ne sont pas une rencontre ;
- le premier relevé de la population tardive annonçait « 0 gagnée, 0 perdue » sur
  un chiffre stable depuis trois lots. C'était le vocabulaire de `picks.result`.

**Et une intuition de conception renversée par la mesure** : lire le vainqueur
**dans le score** plutôt que dans la position des joueurs est la règle du projet, et
elle donne ici le **moins** bon résultat — 98,85 % contre 99,98 % — parce que `ret.`
casse le sens du score sans toucher à la position. Le réflexe reste juste, la
mesure tranche autrement, et c'est écrit dans le module pour qu'il ne se re-dérive
pas.

---

# DIAGNOSTIC — lot 9 : récupérer ce que le défaut a coûté, puis mesurer

Relevé du **19/08/2026**, sur une copie de la base servie (275 Mo, 286 sélections,
17 sessions). Aucune mesure n'est reprise du brief sans vérification ; **trois le
contredisent**, et l'une contredit une conclusion du lot 8 — la mienne.

**Une session concurrente travaillait le même brief sur le même arbre.** Elle a
livré le §0b, le §3 et une première version du §0a avant d'être arrêtée ; son
travail est repris ici, vérifié et complété. Deux défauts qu'elle avait laissés
sont corrigés plus bas — une déclaration de registre perdue, et un chemin muet.

---

## §0a — Le rejeu, en écriture, sur les dix-neuf collages

### La première question, et la réponse est « simulation »

Le rapport du lot 8 annonçait « rejeu des trois collages complets à travers le
code corrigé ». Mesure sur la base servie avant toute écriture :

| | Base servie, 19/08 19h20 |
| --- | ---: |
| `picks.claim_raw_json` renseigné | **0 sur 286** |
| `combos` / `combo_legs` | **0 / 0** |
| `sessions.open_dossiers_state` | `absente` sur **toutes** |

**C'était une simulation.** Le tableau du lot 8 décrivait ce qui pourrait être,
pas ce qui est.

### L'inventaire est complet, pas un échantillon

Les dix-neuf collages archivés ont été balayés, pas les trois du rapport
précédent. Le résultat le plus important est un **zéro** :

| | Compte |
| --- | ---: |
| collages archivés | **19** |
| collages rejoués | **19** |
| collages portant des blocs `conf` | **3** (imports 14, 16, 18 — tous session 17) |
| **sélections nouvelles** | **0** |
| blocs `conf` posés | **15** |
| combinés rattachés | **1** (3 jambes) |
| `dossiers_ouverts` relues | **3** (6, 7 et 9 repères) |

**Zéro sélection nouvelle change le geste de récupération, et c'est le résultat
du §0a.** Les lignes de section C des trois collages complets sont déjà en
base — entrées par les re-collages du seul tableau qui les ont suivis, avec
`ligne_absente` donc un cran 1 forcé. Ce qui leur manquait n'était pas leur
existence, c'était leur **bloc de confiance**. `replay` ne pouvait rien y faire :
il crée, il ne complète pas.

D'où `myassistantbet-replay --rattacher`, qui pose sur des sélections **déjà
enregistrées** ce qu'un collage portait en plus. Trois règles, et c'est le dessin
entier : **on ne crée rien**, **on n'écrase rien** (le premier relevé fait foi),
et **le rapprochement exige l'unicité** — un bloc qui correspond à zéro ou à
plusieurs sélections de la session est dit, jamais posé au hasard.

### Ce que l'écriture a produit, et le contrôle de non-régression

| | Avant | Après |
| --- | ---: | ---: |
| `picks` | 286 | **286** |
| avec bloc `conf` | 0 | **15** |
| `combos` / `combo_legs` | 0 / 0 | **1 / 3** |
| principale / exploratoire / tardive | 218 / 16 / 52 | **218 / 16 / 52** |

**Aucun doublon**, vérifié en SQL sur `(session, marché, sélection)` : les seize
paires vues deux fois sont toutes antérieures (sessions 3 à 14) et portent sur
des matchs différents — c'est d'ailleurs pourquoi le rapprochement exige
l'unicité plutôt que de la supposer.

Les quinze sélections passent d'un cran 1 forcé à leur cran calculé réel, et leur
`research_override_cause` se vide.

### Deux prémisses de ma propre implémentation, mesurées et corrigées

- **la garde de doublon de l'aperçu se périme.** `parse_table` marque `duplicate`
  sur une signature qui inclut l'identifiant de match, lequel se résout par la
  shortlist et le voisinage — donc **cesse de se résoudre** dès que le match sort
  de la fenêtre. Douze sélections d'un rejeu se déclaraient neuves, douze avec
  `event_id = None`, et **les douze existaient déjà**. Un second filet ne
  dépendant d'aucune résolution — marché et libellé, dans la session — refuse
  d'écrire : refuser une ligne se rattrape en la saisissant, un doublon non ;
- **`build_preview` archive à chaque appel, et je craignais qu'un balayage
  pollue `imports_raw`. Faux** : `imports_raw.record` déduplique sur l'empreinte
  SHA-256. Dix-neuf lignes avant le balayage, dix-neuf après, sur une trentaine
  d'appels. Le code était juste, l'inquiétude non.

---

## §0b — Le test de bout en bout, sur le rendu entier

**C'est par là que le défaut du lot 8 est passé.** Le banc de transport applique
onze altérations à chaque format structuré, mais il les teste **isolés** : un
tableau seul, un bloc `conf` seul, une ligne `sets:` seule. Le défaut ne se
voyait que dans le rendu complet, où la prose de la section B côtoie les deux
tableaux. Chaque format passait son banc, et le rendu entier perdait la moitié de
sa substance.

`tests/test_collage_complet.py` prend le collage réel du 19/08 — 21 559
caractères, tel qu'il a été reçu, clôtures mangées, tabulations à la place des
barres — le passe par le **vrai** chemin d'import sur une base de test, et compte
chaque objet :

| Objet | Attendu |
| --- | ---: |
| sélections de section C | 5 |
| sélections de section C-bis | 2 |
| blocs de confiance appariés | 5 |
| combinés | 1 |
| scores en sets | 10 |
| repères `dossiers_ouverts` | 9 |

**Un compte, pas une présence** : c'est le compte qui aurait crié. Le collage
portait cinq blocs et deux sélections sont entrées ; une assertion « il y a des
sélections » serait passée pendant toute la panne.

---

## §1 — L'écart entre cran déclaré et cran recalculé

### La population, et ce qui en sort

**15 sélections portent les deux crans.** 120 autres portent
`confidence_computed = 1` **sans aucun bloc** : ce sont des écrasements forcés,
pas des calculs, et les compter ferait mesurer à la page sa propre correction —
le piège que la migration 045 a été écrite pour éviter.

### La distribution

| Cran | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| déclaré | 0 | 3 | 4 | 7 | 1 |
| recalculé | 0 | 0 | 7 | 4 | 4 |

Écart signé (recalculé − déclaré) : **0 sur 9 sélections, +1 sur 6, et aucune
correction à la baisse.**

| `manque_touche_facteur` | n | accord | écarts |
| --- | ---: | ---: | --- |
| `false` | 8 | 5 | +1 × 3 |
| `true` | 7 | 4 | +1 × 3 |

### Les corrections à la baisse : **il n'y en a aucune**

Le brief les attendait — « deux faits d'un même éditeur déclarés indépendants,
`faits: []` déclaré au-dessus de 1 » — et la mesure n'en trouve pas une. L'écart
est **entièrement unidirectionnel** : le modèle se note **sous** ce que sa propre
table autorise. Les six désaccords forment deux groupes nets :

| Sélections | Passage | Ce qu'elles portent |
| --- | --- | --- |
| #283, #287, #290 (section C) | 4 → 5 | source 1, 2 ou 3 faits, autant d'éditeurs distincts, `manque_touche_facteur: false` |
| #281, #282, #286 (C-bis) | 2 → 3 | source 2, au moins un fait daté, `manque_touche_facteur: true` |

**Aucune conclusion sur la qualité de l'analyse n'est tirée**, l'effectif étant de
quinze. Ce qui est rendu est la distribution.

### Ce que la page montrait, et la moitié qui manquait

`Notation` existait et rendait déjà **3 sur 12** avec son effectif visible. Elle
compte la population **principale seule** — et c'est juste, les trois populations
ne se mélangent jamais. Mais les trois désaccords 2 → 3 sont **exploratoires**, et
`Exploratory` n'avait aucun champ pour eux : la moitié de la mesure était
invisible, au moment précis où le §3 la rend possible.

Le bloc exploratoire porte donc son propre écart — **3 sur 3, dérive −1,0**,
transition 2 → 3, sans clause nommée (l'effectif de 3 est sous le seuil de 8 de la
page, et la garde fonctionne). Les deux ne sont **jamais fondus** : les
additionner désignerait une clause moyenne que ni l'une ni l'autre ne réclame.

### Un 500 trouvé en écrivant le test, et il valait plus que le test

`Residual.gap` rend `None` pour dire qu'un résidu sans effectif n'est pas un
résidu. **Deux des trois rendus le formataient sans garde** : une population
portant des sélections mais aucune tranchée faisait tomber `/stats` en 500 — pas
une ligne absente, la page entière. C'est l'état exact dans lequel C-bis entre
aujourd'hui, sa première volée de blocs étant toute en attente. Le troisième
rendu — les bandes de retard, trois lignes plus bas dans le même gabarit — portait
déjà la garde, et personne ne l'avait reprise.

---

## §2 — `dossiers_ouverts` : la série, et la prémisse renversée

### La maille est le lot, pas la session

La session 17 porte **trois lots** — six, sept et neuf blocs — et trois
déclarations. Rangée par session, elle rendrait « 9 repères pour 22 matchs », deux
nombres qui ne se sont jamais rencontrés. `sessions.open_dossiers` ne garde
d'ailleurs que la dernière des trois : c'est un état courant, pas un historique.

### La série

| Jour | Prompt | Lot | Budget | Déclarés | Lot entier ? |
| --- | ---: | ---: | ---: | ---: | --- |
| 18/08 | 157 | 6 | 6 | 6 | **oui** |
| 18/08 | 158 | 7 | 7 | 7 | **oui** |
| 19/08 | 159 | 9 | 9 | 9 | **oui** |

Part des sélections en `lecture`, par jour : 13/08 `0/20` · 14/08 `29/29` ·
15/08 `49/49` · 17/08 `21/21` · 18/08 `10/17` · 19/08 `0/5`.
Résidu au prix du cran 3 : **aucune sélection de ce cran n'est encore tranchée**.

**Trois points ne font pas une tendance, et rien n'en est tiré.** Un test vérifie
qu'aucun champ de pente, de moyenne ou de projection n'existe sur ce bloc.

### Ce que la mesure renverse

Le brief lit « 6, 7 et 9 repères pour un budget de 10 » et en tire « le budget
mordait au moins parfois ». Les lots correspondants comptaient **exactement 6, 7
et 9 blocs**. Le budget effectif étant `min(réglage, taille du lot)`, il valait 6,
7 et 9 : **le modèle a déclaré tout le lot les trois fois**, et ce n'est pas le
réglage qui bornait, c'est le lot.

Le réglage, lui, a bien mordu — mais **avant**, quand il valait 7 : sur **28
prompts** des 45 dont le corps porte le nombre, dont un lot de 26 blocs ramené à
7. **Depuis son passage à 10 le 18/08, aucun lot ne l'a atteint.**

**Conséquence à connaître, et elle porte sur la mesure elle-même** : quand la
déclaration couvre le lot entier, `hors_dossiers` **ne peut plus se produire**. Le
compteur d'inflation des migrations 043 et 045 est alors neutralisé — non par un
défaut de code, mais par une déclaration qui ne laisse rien dehors.

### La colonne, et pourquoi elle est stockée

Migration 063, `prompts.research_budget`. Le réglage **change** — 7 puis 10 — et
`preferences` ne garde que sa valeur courante : recalculer à la lecture ferait
décrire les sessions d'hier par le réglage d'aujourd'hui. Rétro-rempli en Python
depuis le corps archivé, qui écrit le nombre en toutes lettres — **le corps est la
preuve**, rien n'est reconstitué. 45 prompts sur 159 le portent ; les 114 autres
sont antérieurs à la phrase du gabarit et restent `NULL`, ce qui est la vérité.

---

## §3 — Les lignes de C-bis portent un bloc `conf`

Décision appliquée, phrase du gabarit ajoutée **au mot près**.

**Conséquence sur le code, et elle n'était pas dans le brief** : le compte de
blocs cesse de valoir compte de lignes de la section C, donc l'appariement par
l'ordre ne tient plus. Il se fait désormais **par le repère de match** — « une
seule sélection par match, tous tableaux confondus » étant déjà une règle de
rejet, le repère suffit. La somme de contrôle ne bouge pas : le repère doit
désigner, dans le prompt d'origine, une affiche que porte la ligne.

Deux gains au passage : deux blocs dans le désordre se rangent chacun chez lui au
lieu de coûter les crans du lot entier, et une ligne sans bloc est **nommée** au
lieu de faire tomber l'appariement en entier.

Le drapeau `exploratoire` se dérive de la sélection appariée et **jamais d'un
champ déclaré** : la ligne vient d'un tableau ou de l'autre, c'est déterministe.

---

## §4 — `Non joué` contre `Ici` : la source démonte la prémisse

### §4a — Le cas concret, et d'où vient chaque affirmation

Sur **Madison Keys – Xiyu Wang** (19/08), le bloc rendait à quatre lignes
d'écart :

```
  Non joue   Xiyu Wang — Bianca Andreescu (1714) le 13/08 14:00 UTC,
             adversaire remplace, non disputee
  Ici        Xiyu Wang 13/08 bat Polina Kudermetova 6-3 6-2
             | 13/08 bat Bianca Vanessa Andreescu 6-0 6-4 | …
```

- **`Non joué` vient de nos propres scans.** Deux événements le 13/08 — #400 vs
  Andreescu (14:00, créé le 11/08 à 21h51) et #539 vs Kudermetova (16:30, créé le
  13/08 à 08h49) — et la déduction « un joueur ne dispute qu'une rencontre par
  journée de tournoi, c'est la plus récemment créée qui tient ».
- **`Ici` vient de la charge utile `matches-played` archivée**, qui porte les deux
  matchs avec leur score et leurs statistiques de service.

Les deux matchs ont été joués. C'est la déduction qui a produit un faux positif —
exactement le cas que `CLAUDE.md` annonçait comme « ne s'observe pas en base ».

### Le principe tient sur sa première moitié ; la seconde est **sans objet**

- *« Un match qui porte un score a été joué : la source gagne, et `Non joué` doit
  cesser de le mentionner »* — **confirmé**, et appliqué.
- *« Un match que la source ignore et que `Non joué` signale : `Ici` doit alors le
  nommer plutôt que l'omettre »* — **le cas ne se produit pas.** Mesuré sur
  O'Connell — Fonseca : `Non joué` dit « forfait adverse, non disputée » et `Ici`
  rend « 18/08 forfait de Joao Fonseca ». Les deux disent la même chose, ce qui
  est une **redondance et non une contradiction**. Rien à coder de ce côté.

### Le rapprochement par nom était la fausse piste

La source écrit « Bianca **Vanessa** Andreescu » quand nos scans disent « Bianca
Andreescu » : `sort_key` ne tombe pas, et une comparaison souple serait le « en
cas de doute on devine » que le projet refuse partout.

**C'est le jour qui parle.** Vérifié : la journée de tournoi de nos deux
événements vaut `2026-08-13`, et la source date les deux matchs du même jour.
`contested_days` compte donc les matchs **disputés** par jour ; au-delà d'un, la
prémisse est fausse pour ce jour-là et toutes les apparitions scannées y sont
tenues pour réelles. Aucun nom n'est rapproché.

Trois gardes :

- **positif seulement** — un jour absent ne prouve rien, la source pouvant ne pas
  couvrir ce tournoi, ce joueur, ou n'avoir pas encore publié ;
- un **forfait n'est pas un match disputé** (même règle qu'`Usure`), donc il ne
  lève rien ;
- le **marquage à la main** n'est jamais touché : c'est un geste humain.

**Hors du drapeau `CURRENT_EVENT_LINE_ENABLED`, et c'est délibéré** : le drapeau
garde une ligne *ajoutée* au bloc ; ici on retire une affirmation *fausse* d'une
ligne déjà servie. Attendre l'activation d'`Ici` laisserait « adversaire remplacé,
non disputée » sur un match joué, sur toutes les sessions d'ici là.

### §4b — Le rendu, avant / après

**Drapeau bas — ce qui part en production dès maintenant :**

```
AVANT  Parcours   … | Xiyu Wang Polina Kudermetova (1697), Maria Timofeeva (1718),
                  Leylah Fernandez (1816), Elina Svitolina (2054) [vu depuis le 12/08]
       Non joue   Xiyu Wang — Bianca Andreescu (1714) le 13/08 14:00 UTC,
                  adversaire remplace, non disputee

APRES  Parcours   … | Xiyu Wang Bianca Andreescu (1714), Polina Kudermetova (1697),
                  Maria Timofeeva (1718), Leylah Fernandez (1816),
                  Elina Svitolina (2054) [vu depuis le 12/08]
       (la ligne Non joue disparait)
```

Le forfait, lui, ne bouge pas :

```
       Non joue   Christopher O'Connell — Joao Fonseca (1934) le 18/08 15:00 UTC,
                  forfait adverse, non disputee
```

**Drapeau haut, en test seulement.** Les trois cas demandés sont couverts et la
contradiction a disparu :

| Cas | Vérification |
| --- | --- |
| plusieurs tours disputés (Keys – Wang) | `Ici` liste 5 rencontres pour Wang, `Non joué` ne dit plus rien |
| entrant en lice (Andreescu, match #400) | `Bianca Andreescu aucun match dans ce tournoi [releve au 18/08]` — la mention explicite, jamais un silence |
| portant un `Non joué` (Fritz – O'Connell) | `Non joué` et `Ici` disent le même forfait, sans se contredire |

`as_of` est présent sur **chaque** fragment (`[releve au 19/08]`), **par joueur** —
deux profils se rafraîchissent à deux instants différents. Les dénominateurs
accompagnent chaque taux (`(2 matchs, 139 pts)`, `(4 matchs, 265 pts)`).

**Le drapeau reste bas en production.**

---

## §5 — La ligne `Jeux` sur un rendu réel

**14 agrégats au seuil de 300 jeux, sur 239 joueurs porteurs de jeux — et les 14
sont « toutes surfaces ».** Zéro par surface, ce qui confirme le blocage
structurel mesuré au lot 8.

Quatre blocs rendus, ATP et WTA :

```
ATP  Jeux   Taylor Fritz tenue 88.8% · break 19.9% (160 jeux servis)
            Christopher O'Connell non disponible
            (toutes surfaces, arretees au 18/08 — le seuil de jeux ne s'atteint pas par surface)

WTA  Jeux   Barbora Krejcikova tenue 77.1% · break 45.4% (157 jeux servis)
            Sara Bejlek non disponible
            (toutes surfaces, arretees au 16/08 — le seuil de jeux ne s'atteint pas par surface)
```

Les trois vérifications demandées passent :

- **le repli de portée est déclaré dans la ligne**, et il compte double ici : la
  ligne `Service` juste au-dessus porte `(Hard, 52 sem., …)`. Les deux lignes ont
  des portées différentes, et c'est la mention qui rend l'écart lisible ;
- **un joueur sous le seuil rend `non disponible`** ;
- **les deux sous le seuil omettent la ligne** — vérifié sur Coco Gauff – Marie
  Bouzkova, où `Service`, `Retour` et `Ecart` sortent et `Jeux` non.

La date d'arrêt diffère parfois de celle de `Service` (16/08 contre 18/08) : deux
agrégats, deux relevés, et chacun porte le sien.

**Rien à corriger.** La ligne reste rare — un ou deux blocs par lot.

---

## §6 — Dettes

### La métrique du retard : spécification inchangée, effectif identique

| | Lots 4 / 7 / 8 | 19/08, après le rejeu |
| --- | ---: | ---: |
| sélections tardives tranchées | 52 | **52** |
| gagnées / perdues | 32 / 20 | **32 / 20** |
| `late_minutes` renseignées | 52 | **52** |
| étendue (minutes) | 12 – 1 557 | **12 – 1 557** |
| médiane | 133 | **133** |

Cohérent avec le §0a : le rejeu n'a créé aucune sélection, donc la population
tardive ne pouvait pas bouger. **Toujours non implémentée**, conformément au
brief.

*Note de méthode, et c'est la deuxième fois qu'elle sert* : ma première requête a
rendu une médiane de 136 et ressemblait à un changement. C'était mon `OFFSET`, qui
prend la valeur haute des deux centrales sur un effectif pair. Une mesure qui
contredit un chiffre stable de quatre lots mérite d'abord un doute sur la mesure.

### Le registre des chemins d'écriture : deux défauts, tous deux corrigés

- **`add_pick` avait perdu sa déclaration.** Un `@dataclass` s'était glissé entre
  le décorateur `@writes(...)` et la fonction : le registre pointait sur le
  dataclass, et `add_pick` — le seul écrivain de `picks` — n'était plus déclaré.
  `tests/test_write_paths.py` l'a dit, et c'est exactement ce pour quoi il existe.
- **`--rattacher` était muet, et c'est la deuxième fois sur ce fichier.**
  `CONTRIBUTING.md` dit de la première : *« `myassistantbet-replay` a été écrit le
  même jour et par la même main que cette phrase, et il a laissé tomber ses échecs
  d'écriture sans les journaliser. »* Le rattachement l'a refait — un bloc qui ne
  trouve pas sa sélection, un combiné dont une jambe manque, se disaient à l'écran
  et nulle part ailleurs. Il journalise désormais, et il entre au banc.

**`PATHS` s'énumère à la main** — rien dans la source ne distingue une route
d'entrée d'une route quelconque — donc le banc ne peut pas prouver que la liste
est complète. Il prouve ce qui est prouvable : chaque chemin listé couvre chaque
famille du registre, **ou déclare pourquoi il ne le peut pas**. Sans cette table
d'exemptions, le premier réflexe devant un manque serait de retirer le chemin de
la liste, c'est-à-dire de le rendre muet à nouveau. Une seule exemption
aujourd'hui : le rattachement ne crée aucune sélection, donc il ne peut pas en
refuser une.

Le banc passe de **10 à 15 contrôles**, sur trois chemins, **0 manque**.

### `changelog_mesure` : quatre entrées, à leur date d'effet

| # | Date | Portée | Ce qui change |
| ---: | --- | --- | --- |
| **18** | 19/08 | `ingestion` | Les blocs `conf` des trois collages complets sont posés. 15 sélections passent d'un cran 1 forcé à leur cran calculé réel. |
| **19** | 19/08 | `gabarit` | Les lignes de C-bis portent un bloc `conf`. **Complétion de la spécification du lot 1, pas un changement de comportement attendu.** |
| **20** | 19/08 | `gabarit` | `Non joué` cesse d'annoncer un match que la source dit joué. Hors du drapeau `CURRENT_EVENT_LINE_ENABLED`, qui reste bas. |
| **21** | 19/08 | `ingestion` | Le repli de résolution des dossiers ouverts choisit le prompt du tableau, et non le premier qui porte assez de repères. |

Le §1 et le §2 n'ont **pas** d'entrée : ils ajoutent de la restitution et une
colonne de mesure, sans changer ce que le modèle lit ni ce que l'application
écrit sur une sélection.

---

## §7 — Ce que la mesure contredit dans le brief

**Trois affirmations, et la troisième porte sur une conclusion du lot 8 —
c'est-à-dire sur la mienne.**

| Ce qui était affirmé | Ce que la mesure dit |
| --- | --- |
| « 6, 7 et 9 repères pour un budget de 10, donc le budget mordait au moins parfois » | les lots comptaient **exactement** 6, 7 et 9 blocs : le budget effectif valait 6, 7 et 9, et le lot entier a été déclaré les trois fois. Le réglage a mordu **avant**, quand il valait 7 — 28 prompts — et jamais depuis |
| « `Ici` doit nommer un forfait que la source ignore plutôt que l'omettre » | `Ici` le nomme déjà (« 18/08 forfait de Joao Fonseca »). Le cas décrit ne se produit pas ; rien à coder |
| lot 8 : « `hors_dossiers` est une observation sur le modèle, pas un défaut de collecte » | **faux, et c'était mon erreur**. Le repli de résolution avait choisi le mauvais prompt parmi les trois de la session : Bodø/Glimt est `M3` du prompt 157, et `M3` figure dans la liste déclarée. Les deux sélections portaient bien sur des dossiers ouverts |

**Et une quatrième, sur ma propre inquiétude plutôt que sur le brief** : je
craignais qu'un balayage de rejeu pollue `imports_raw`, `build_preview` archivant
à chaque appel. `record` déduplique sur l'empreinte : dix-neuf lignes avant,
dix-neuf après, sur une trentaine d'appels.

### La leçon de méthode du lot

**Un défaut corrigé peut laisser sa conclusion fausse en place.** Le lot 8 a
corrigé `EXPLORATORY_HEAD`, mesuré ce que le correctif rendait, et lu les deux
`hors_dossiers` restants comme une observation sur le modèle — parce que le
chemin *avait l'air* de fonctionner de bout en bout. Il fonctionnait à moitié :
les blocs s'appariaient, et la résolution des dossiers passait par un **second**
chemin, non corrigé, qui choisissait son prompt sur un critère de comptage.

Ce qui l'aurait attrapé est la règle que le projet applique déjà ailleurs :
`_attach_combos` exigeait la bonne garde depuis toujours, et son propre docstring
annonçait « même repli que `_apply_research` ». C'était faux — un des trois
chemins avait la garde, les deux autres non. **Un commentaire qui affirme une
symétrie est un endroit où la vérifier.**

---

# DIAGNOSTIC — lot 10 : le garde-fou éteint, la ligne `Ici`, et deux dettes soldées

Relevé du **19/08/2026**, sur une copie de la base servie (275 Mo, 286 sélections,
17 sessions). **Quatre affirmations du brief sont contredites par la mesure**, dont
une qui portait sur le point le plus important du lot.

Arbre propre au démarrage, aucune modification concurrente : la règle 9 est
satisfaite.

---

## §0 — Préalables d'exploitation

**L'état servi est le bon, et c'est vérifié depuis le serveur.** Le processus a
démarré à 20:43:55, après la dernière modification de source (20:41:50) ; mais
l'inférence par horodatage ne prouve rien, donc trois marqueurs de code du lot 9
ont été cherchés dans la page servie sur `localhost:8021` — « Dossiers de
recherche déclarés », « Cran annoncé contre cran calculé, sur cette population »
et `min(réglage, taille du lot)`. Les trois y sont. Schéma **63** au démarrage du
lot.

**`/tmp` : l'inventaire, et ce qui est supprimable.**

| Ce qui remplit | Volume | Supprimable par moi ? |
| --- | ---: | --- |
| `/tmp/claude-1000/…` (sessions) | 2,0 Go | **non** — 136 Ko m'appartiennent |
| `/tmp/pytest-of-ubuntu` | 1,1 Go | **non à la main** — `pytest` en retire au-delà de trois |
| `~/lot9-travail` (mes copies) | 789 Mo | **oui**, supprimé |

Le tmpfs fait 5,8 Go. Une suite complète coûte 450 à 600 Mo, `pytest` en conserve
trois : **1,5 Go de régime permanent** pour les seuls tests. La convention est
écrite dans `CONTRIBUTING.md` — une copie de base ne va jamais dans `/tmp`, on ne
supprime que ce qu'on a créé, et `df -h /tmp` coûte une seconde avant une suite de
quatre minutes.

**La ligne `Non joué` telle qu'elle sort aujourd'hui**, sur un prompt réel de la
session 17 généré sans drapeau. Le lot avait raison de passer hors du drapeau, et
le rendu le montre — **une seule** ligne `Non joué` sur sept blocs, celle du
forfait saisi à la main :

```
### M4 · TENNIS · ATP Cincinnati Open · Taylor Fritz – Christopher O'Connell · 20/08 01:00
  Parcours    Taylor Fritz Alex Michelsen (1847), Daniel Merida Aguilar (1847)
              | Christopher O'Connell Dane Sweeny (1472), Alexander Shevchenko (1669),
              Kamil Majchrzak (1795), Casper Ruud (1954) [vu depuis le 11/08]
  Non joue    Christopher O'Connell — Joao Fonseca (1934) le 18/08 15:00 UTC,
              forfait adverse, non disputee
```

Et le bloc corrigé, où la ligne a disparu et l'adversaire a rejoint `Parcours` :

```
  Parcours    Madison Keys … | Xiyu Wang Bianca Andreescu (1714),
              Polina Kudermetova (1697), Maria Timofeeva (1718),
              Leylah Fernandez (1816), Elina Svitolina (2054) [vu depuis le 12/08]
```

---

## §1 — Pourquoi le garde-fou n'a-t-il pas mordu ? Il a mordu.

**C'est la contradiction la plus importante du lot.** Le brief dit : *« `add_pick`
a perdu son décorateur, et rien n'a échoué : le `selfcheck` affichait 10/10 parce
que le dénominateur venait du registre lui-même. »* La seconde moitié est exacte.
La première ne l'est pas.

### La reproduction, à l'identique

L'état du lot 9 a été réinjecté dans une copie du dépôt — `@writes(...)` suivi
d'un `@dataclass` interposé, `add_pick` nue en dessous :

| Instrument | Ce qu'il a fait |
| --- | --- |
| `tests/test_write_paths.py` | **2 assertions tombent**, et le message nomme `add_pick → picks` |
| `selfcheck-ingestion` | **15 sur 15, 0 manque**, code de retour 0 |

Le test a donc mordu — c'est par lui que le défaut a été trouvé au début du lot 9,
en lançant la suite sur l'état hérité.

### La cause de l'aveuglement, et elle est précise

Le registre, sous le défaut, contient :

```
myassistantbet.services.combos.record
myassistantbet.services.history._Interpose     ← la classe interposée
myassistantbet.services.set_scores.save
```

`add_pick` a disparu, `_Interpose` a pris sa place — **et
`declared_block_types()` rend exactement la même chose qu'avant** :
`('conf', 'combo', 'score_sets', 'selection', 'exploratoire')`. C'est un **agrégat
de familles** : la classe portait les mêmes trois familles que la fonction, donc
la somme ne bouge pas d'un mot.

**Un contrôle dont le dénominateur vient de ce qu'il contrôle ne peut pas voir un
déplacement à l'intérieur.** Il ne regardait pas mal ; il regardait la bonne chose
au mauvais niveau d'agrégation.

### Le correctif de forme demandé était déjà en place

Le brief demande que l'énumération vienne d'une source indépendante du registre —
l'AST, les `INSERT INTO` vers table gardée. **Elle en venait déjà** :
`_inserting_functions` walk l'AST de tous les `.py` du paquet et cherche un
`INSERT INTO` dans les **corps** de fonction, sans regarder un seul décorateur.
C'est pour cela que le test a mordu.

Ce qui manquait n'était pas l'énumération : c'était que le **contrôle
d'exploitation** s'en serve. Le recensement vivait dans le fichier de test, donc
hors de portée du banc.

### Le recensement complet, aujourd'hui

| | Compte |
| --- | ---: |
| occurrences brutes d'`INSERT INTO` vers table gardée | **4** |
| fonctions qui les portent | **3** |
| déclarées au registre | **3** |
| désaccords | **0** |

Les 4 occurrences pour 3 fonctions viennent de `combos.record`, qui insère dans
`combos` **et** `combo_legs`. **Aucun autre chemin n'est dans le cas
d'`add_pick`.**

### Trois vues indépendantes, et deux trous fermés

Le recensement quitte `tests/` pour `write_paths`, où le test et le banc le lisent
tous les deux :

- `inserting_functions` — les **corps** : qui écrit, déclaré ou non ;
- `decorated_nodes` — les **décorateurs** : une déclaration posée ailleurs que sur
  une fonction, ce qui nomme le défaut du lot 9 directement ;
- `REGISTRY` — le seul témoin d'**exécution** : une déclaration qui n'a jamais
  tourné, faute d'import.

Aucune ne se dérive d'une autre. Deux trous fermés au passage, **tous deux
latents et non vivants**, mesurés comme tels :

- `INSERT OR REPLACE INTO` et `REPLACE INTO` n'étaient pas reconnus. Zéro
  occurrence dans le dépôt ;
- le recensement s'arrêtait au **premier** `INSERT` d'une fonction, donc
  `combos.record` n'était vue que sur une de ses deux tables. Sans effet sur
  « est-elle déclarée », mais le message d'erreur nommait une table sur deux.

### Le test du test

Quatre mutations sur un faux paquet, et la réciproque :

| Mutation | Attendu | Obtenu |
| --- | --- | --- |
| `@writes` retiré **au-dessus** d'un autre décorateur | désaccord | ✔ |
| `@writes` retiré **en dessous** d'un autre décorateur | désaccord | ✔ |
| `@writes` au-dessus / en dessous, **sain** | rien | ✔ |
| le défaut du lot 9 rejoué (`@dataclass` interposé) | **deux** lignes | ✔ |

La réciproque compte autant : un contrôle qui crie sur tout crie aussi sur le code
sain, et c'est ce qui rendrait le cri des trois autres non informatif.

**Vérification finale** : le défaut réinjecté contre le banc corrigé donne
maintenant code de retour **1** et deux lignes nommées, là où il donnait 0.

---

## §2 — La ligne `Ici` en production

### Le rendu, et le coût

Six blocs sur six portent une ligne renseignée sur le lot du jour, ATP et WTA.
Deux extraits, l'un avec un abandon et un forfait :

```
  Ici   Xiyu Wang 13/08 bat Polina Kudermetova 6-3 6-2 | 13/08 bat Bianca Vanessa
        Andreescu 6-0 6-4 | 14/08 bat Maria Timofeeva 6-0 3-0 (abandon) | 17/08 bat
        Leylah Annie Fernandez 3-6 6-2 6-2 | 18/08 forfait de Elina Svitolina
        [releve au 19/08]
        service ici 68.9% 1re · 70.1% s/1re · 9 df (4 matchs, 209 pts)
```

**Coût mesuré sur le même lot**, drapeau bas puis haut : 18 003 → **18 789**
tokens pour six blocs, soit **+131 par bloc**, préambule compris.

### La vérification depuis le serveur

Lot 6 a failli rapporter une régression inexistante en interrogeant la mauvaise
route. La fiche du match 827 a donc été lue sur `localhost:8021` après
redémarrage, et elle porte la ligne, ses deux joueurs, le forfait de Svitolina et
l'horodatage. Schéma servi : **64**.

### La couverture à l'activation

| Mois | Circuit | Blocs | Renseignés | Partiels | Absents | Part servie |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-08 | atp | 97 | 46 | 32 | 19 | **80 %** |
| 2026-08 | wta | 93 | 41 | 32 | 20 | **78 %** |
| | **total** | **190** | **87** | **64** | **39** | **79 %** |

**Trois états et non deux, et la distinction n'est pas cosmétique.** Un bloc
*partiel* porte des résultats d'un côté et « aucun match dans ce tournoi » de
l'autre : c'est un **fait sur le match**, souvent le fait dominant quand l'un sort
de trois tours et l'autre entre en lice. Les fondre avec les absents ferait lire
une entrée en lice comme un trou de collecte — exactement ce que la mention
explicite existe pour éviter.

La mesure se fait **hors du drapeau** : gardée par lui, elle rendrait des zéros le
jour où il redescend, et ça se lirait comme une source tarie.

### Elle n'est pas branchée sur `/stats`, et c'est un arbitrage mesuré

Le balayage coûte **2,8 s** sur une page qui en coûte **2,3**. Le contrat de
parité page/export interdit de la mettre dans l'un sans l'autre, donc c'était
page **et** export, ou ni l'un ni l'autre. Une mesure qui justifie une décision
n'a pas à être payée à chaque consultation ; sa conclusion, si — elle est au
journal des mesures et ici.

### Migration 064 : l'index qui manquait

`archived_profile` filtre sur `(provider, path)` ; l'index portait
`(provider, endpoint, fetched_at)`. La recherche se faisait donc sur le **seul**
`provider`, puis triait dans un B-tree temporaire.

| | Avant | Après |
| --- | ---: | ---: |
| génération d'un prompt de six blocs tennis | 2,11 s | **1,43 s** (−32 %) |
| balayage de couverture, 190 blocs | 8,9 s | 3,6 s |
| idem, avec un cache de profils passé par l'appelant | — | **2,8 s** |

Le gain de 32 % porte sur le **chemin servi**, généré plusieurs fois par session :
il justifie la migration à lui seul, indépendamment de la mesure de couverture.

### Le défaut que l'activation elle-même a trouvé

Activer le drapeau a fait tomber **deux tests** qui n'avaient rien à voir avec le
changement : ceux qui décrivent ce que le bloc rend **drapeau bas**. La suite
lisait le `.env` servi.

Les deux drapeaux de gabarit sont donc épinglés à leur défaut de code dans le
`conftest`, même règle que les clés factices qui y sont depuis longtemps — *« un
test qui passe parce qu'une clé existe sur cette machine-ci échouerait
ailleurs »*. Épingler `SERVE_LINES_ENABLED` n'a **rien cassé** : aucun test ne
s'appuyait dessus sans le dire.

---

## §3 — Les deux variantes du gabarit, augmentées du rendu réel

**Aucune n'est appliquée.** Ce qui suit reprend les variantes du lot 8 et y ajoute
ce que le rendu en production a montré.

### Ce que `Ici` porte effectivement — mesuré, plus riche qu'annoncé

| Élément | Exemple réel |
| --- | --- |
| score tour par tour, nom complet de l'adversaire | `16/08 bat Alex Michelsen 6-3 6-4` |
| abandons **nommés** | `14/08 bat Maria Timofeeva 6-0 3-0 (abandon)` |
| forfaits **nommés** | `18/08 forfait de Elina Svitolina` |
| service agrégé du tournoi, **avec son dénominateur** | `service ici 68.9% 1re · 70.1% s/1re · 9 df (4 matchs, 209 pts)` |
| date du relevé, **par joueur** | `[releve au 19/08]` |
| entrée en lice, explicite | `Bianca Andreescu aucun match dans ce tournoi` |

Le lot 8 annonçait « l'adversaire, le score set par set et le service agrégé ». Il
faut y ajouter **les abandons et les forfaits nommés** : la ligne distingue un
tour gagné sur le court d'un tour obtenu sans jouer, ce qu'aucune autre ligne du
bloc ne fait.

### Ce qu'elle ne porte pas, et qui reste à chercher

- la **durée** de chaque match — aucune source automatisable ne la sert, établi au
  lot 3 et non démenti depuis. C'est le seul substitut d'`Usure` au niveau du
  match, et `Usure` compte les jeux : deux matchs de 22 jeux à 1h05 et à 2h33 y
  sont identiques ;
- les **conditions réelles du court** — session, vent, chaleur, court central ou
  annexe ;
- le **double** engagé sur place — le fournisseur de cotes ne sert pas les
  doubles, et 10 des 16 joueuses d'une journée WTA en avaient joué un la veille ;
- l'**ordre du jeu** réel, qui décide de l'heure effective.

### Le fait nouveau, et il pèse sur l'arbitrage

**La ligne est renseignée sur 79 % des blocs, pas sur tous.** Sur les 21 % restants
— 39 blocs sur 190 — la puce du gabarit est le **seul** chemin, et la variante A
comme la variante B lui retirent la demande des scores.

C'est la considération que le lot 8 ne pouvait pas avoir : il raisonnait sur un
lot où la ligne sortait partout.

### Variante A — « allégée », augmentée

> · **Ce que la ligne `Ici` ne porte pas de ses matchs dans ce tournoi.** Quand
> elle est présente, elle donne pour chaque tour l'adversaire, le score set par
> set, les abandons et forfaits nommés, et le service agrégé du tournoi avec la
> date de son relevé — ne les cherche pas, et ne les refais pas de zéro. Va
> chercher les trois choses qu'aucune source ne sert : la **durée** de chaque
> match, les conditions réelles du court (session, vent, chaleur, court central ou
> annexe), et le **double** s'il est engagé sur place. Un match postérieur à la
> date de relevé manque : celui-là se cherche. **Quand la ligne `Ici` est absente
> du bloc, cherche aussi les scores.** Un tour non trouvé s'écrit « non trouvé ».

- **On gagne** : la recherche cesse de re-trouver ce que le bloc porte déjà, sur
  les 79 % où il le porte.
- **On garde** : la durée, et le repli explicite — désormais **deux** replis, le
  relevé en retard et la ligne absente.
- **On perd** : rien de mesurable. Le coût en tokens reste du même ordre.

### Variante B — « retirée », augmentée

La puce disparaît ; conditions de court et double rejoignent les puces « Surface
et conditions » et « Charge » ; la durée est ajoutée à « Charge ».

- **On gagne** : **213 tokens** de préambule tennis, et un dossier de recherche
  libéré par bloc.
- **On perd** : la durée, définitivement — et, fait nouveau, **les scores sur les
  21 % de blocs sans ligne `Ici`**. Le lot 8 chiffrait cette perte à zéro parce
  qu'il supposait la couverture totale ; elle vaut 39 blocs sur 190.

### Recommandation, et elle n'engage pas

**La variante A**, et la mesure de couverture la renforce par rapport au lot 8 :
elle est la seule des deux qui gère les deux régimes — ligne présente et ligne
absente — dans une puce unique. Le coût de la phrase ajoutée est d'une ligne.

**L'arbitrage appartient à l'utilisateur**, et il se prend sur les 79 %.

---

## §4 — `dossiers_ouverts` a cessé de discriminer

La série du lot 9 portait déjà lot, budget et repères **sur la même ligne**. Ce
lot y ajoute la phrase, dans la partie visible de la page et non sous le pli :

> Le budget de recherche ne borne plus : sur les 11 derniers lots, de 4 à 9 blocs,
> le réglage n'a pas mordu une seule fois — c'est la taille du lot qui limite, pas
> lui. Quand la déclaration couvre le lot entier, « hors_dossiers » ne peut donc
> plus se produire, et le seul discriminant restant pour comparer un fait daté à
> une lecture est le niveau de source.

**Le brief dit « lots de 6 à 12 matchs », la mesure dit 4 à 9.** La direction est
juste, la borne non : aucun lot n'a atteint 12 depuis le changement de réglage.
Les bornes sont donc **calculées et jamais écrites en dur** — elles bougeront au
premier lot plus grand, et une phrase figée se mettrait à mentir sans que rien ne
le dise.

| Jour | Prompts | Lots | Le réglage a mordu |
| --- | ---: | --- | ---: |
| 14/08 | 13 | 4 – 26 blocs | 10 |
| 15/08 | 19 | 6 – 20 | 16 |
| 16/08 | 2 | 15 | 2 |
| 17/08 | 4 | 5 – 8 | **0** |
| 18/08 | 6 | 4 – 8 | **0** |
| 19/08 | 1 | 9 | **0** |

**Le budget n'est pas modifié.** Le remettre à 7 le ferait mordre à nouveau, donc
réintroduirait la variable qu'on vient de voir disparaître, sans bénéfice mesuré.

**Défaut trouvé en écrivant le test** : le constat était rangé sous la branche
« il existe une déclaration collée ». Il aurait donc disparu exactement quand la
ligne manque — c'est-à-dire au moment où l'on se demande si le budget y est pour
quelque chose. Il porte sur les lots **soumis**, pas sur ce que le modèle en a dit.

---

## §5a — La métrique du retard : exécutée une fois, dette close

Le modèle est celui de la spécification du lot 4, sans un écart :

```
logit( P(gagné) )  =  offset( logit(1/cote) )  +  a  +  b · log(retard_minutes)
```

Population : **52 sélections tardives tranchées**, 32 gagnées / 20 perdues,
`log(retard)` de 2,48 à 7,35.

| | Valeur |
| --- | ---: |
| `b`, pente par log-minute | **+0,122** |
| erreur type | 0,230 |
| intervalle à 95 % | **[−0,328 ; +0,573]** |
| `p`, **bilatérale** | **0,594** |
| pente détectable à 80 % de puissance | **0,643** |
| ordonnée à l'origine (nuisance) | −0,342 |

### Le verdict, et c'est celui que la spécification avait prévu

`b > 0`, `p ≥ 0,05` → *« Rien. La direction est celle attendue, l'effectif ne
tranche pas — c'est l'état actuel, et il faut le dire ainsi plutôt que "pas
d'effet". »*

**À ne pas lire comme une équivalence** : celle-ci se conclut par un TOST, pas par
un test qui échoue à rejeter.

### La spécification se trompait sur sa propre puissance

Elle annonçait qu'il faudrait `b ≈ 0,45` pour 80 % de puissance. La mesure dit
**0,643**. Le test voit donc encore moins loin qu'annoncé, et l'effet observé vaut
**un cinquième** de ce qu'il faudrait pour le voir.

### Vérifié par deux méthodes indépendantes

L'ajustement est écrit à la main — Newton-Raphson sur une matrice 2×2 — plutôt
qu'importé : `scipy` ferait entrer une bibliothèque de calcul scientifique dans un
projet qui tient sur un processus et un fichier SQLite.

| Contrôle | Résultat |
| --- | --- |
| récupération d'une pente connue (simulation, n=4000) | `b` vrai 0,800 → estimé **0,809** |
| l'offset est bien fixé à 1 | décaler l'offset de 0,5 décale l'ordonnée de −0,5 **exactement**, pente inchangée |
| balayage de vraisemblance sur les données réelles | `b = +0,120` contre +0,1224 |
| intervalle de vraisemblance | **[−0,330 ; +0,582]** contre Wald [−0,328 ; +0,573] |

Une séparation parfaite **ne converge pas et ne rend rien**, plutôt qu'une pente
infinie.

**La dette est close**, quel que soit le verdict : une spécification maintenue six
lots sans être exécutée coûte plus qu'elle ne rapporte.

---

## §5b — L'écart de cran, ventilé

**15 sélections portent les deux crans. 9 accords, 6 écarts, tous à `+1`, aucune
correction à la baisse.**

| id | Section | Écart | Faits | Éditeurs | Cause |
| ---: | --- | --- | ---: | ---: | --- |
| 281 | C-bis | 2 → 3 | 2 | 1 | manque touche le facteur — la table plafonne à 3 |
| 282 | C-bis | 2 → 3 | 2 | 1 | idem |
| 286 | C-bis | 2 → 3 | 1 | 1 | idem |
| 283 | C | 4 → 5 | 3 | 3 | faisceau complet — deux éditeurs distincts, la table dit 5 |
| 287 | C | 4 → 5 | 2 | 2 | idem |
| 290 | C | 4 → 5 | 2 | 2 | idem |

**Ventilation : 3 sur 6 / 3 sur 6**, et le partage tombe exactement sur la
frontière des sections.

### Le brief se trompe sur la première cause

Il la formule *« un `manque_touche_facteur` déclaré `true` alors qu'aucun fait
n'est touché »*, ce qui suppose une déclaration abusive. La table dit autre chose :

```python
if self.gap_touches_factor:
    return 3
```

`true` rend **exactement 3** — un **plafond**, pas un plancher. Le modèle a
déclaré **2**, donc *sous* le plafond de sa propre table. Il n'y a **aucun** cas de
déclaration de manque abusive.

### Les deux groupes sont le même phénomène

Une fois lus ainsi, les six écarts disent la même chose en deux endroits : **le
modèle déclare un cran sous ce que sa propre table implique** — une fois sous le
plafond d'un manque qui touche (2 contre 3), une fois au sommet d'un faisceau
complet (4 contre 5).

**Aucune conclusion sur la qualité de l'analyse.** Quinze observations, et
l'effectif est écrit à côté du chiffre.

---

## §6 — Dettes de forme

### Le registre couvre-t-il les lots 9 et 10 ? Par la mesure, oui

Le §1 répond par un recensement et non par une revue : **4 occurrences, 3
fonctions, 3 déclarées, 0 désaccord**. Le lot 10 n'ajoute aucune fonction
d'écriture — `here_coverage` lit, `offset_logistic` est pure. Le banc compte **15
contrôles sur 3 chemins**, dont le `rattachement` ajouté au lot 9.

### `changelog_mesure` : une entrée

| # | Date | Portée | Ce qui change |
| ---: | --- | --- | --- |
| **22** | 19/08 | `gabarit` | **La ligne `Ici` entre en production.** Coût +131 tokens/bloc, couverture 79 %. **La coupe est isolée** — le budget à 10 s'étant révélé sans effet, il n'y a pas de seconde variable à cette date. |

Le §1, le §4, le §5 et le §6 n'ont **pas** d'entrée : ils touchent au contrôle, à
la restitution et à la mesure, jamais à ce que le modèle lit ni à ce que
l'application écrit sur une sélection.

### Le test de bout en bout comptait-il les blocs de C-bis ? Non

**Et le compte le cachait.** Le collage archivé porte cinq blocs `conf` — M1, M4,
M5, M8, M9 — et ses cinq lignes de section C ; ses deux lignes exploratoires (M3,
M7) n'en portent aucune. « 5 » valait donc aussi bien « tous les blocs » que
« ceux de la section C », et un appariement qui aurait ignoré C-bis **aurait rendu
exactement le même nombre**.

Deux corrections, sans toucher à la fixture — c'est un vrai collage reçu :

- l'attribution se vérifie **ligne par ligne** et plus seulement en compte ;
- un second test compose les mêmes 21 559 caractères **plus** les deux blocs que
  le gabarit réclame du côté exploratoire depuis le lot 9. Le compte monte à
  **sept**, les sept lignes portent le leur, le drapeau exploratoire ne bouge pas,
  et les crans de C-bis valent 1.

---

## §7 — Ce que la mesure contredit dans le brief

**Quatre affirmations, et la première portait sur le point le plus important.**

| Ce qui était affirmé | Ce que la mesure dit |
| --- | --- |
| « `add_pick` a perdu son décorateur, et **rien n'a échoué** » | le test **a mordu**, sur ses deux assertions, et c'est par lui que le défaut a été trouvé. Ce qui est resté aveugle est `selfcheck`, dont le dénominateur est un **agrégat de familles** insensible à un déplacement |
| « le correctif de forme : l'énumération doit venir d'une source indépendante du registre » | **elle en venait déjà** — l'AST, sur les corps de fonction. Ce qui manquait est que le contrôle d'exploitation s'en serve |
| « le budget ne borne plus depuis les lots de **6 à 12** matchs » | depuis le changement de réglage, les lots vont de **4 à 9**. Aucun n'a atteint 12 ; la direction est juste, la borne inventée |
| « combien viennent d'un `manque_touche_facteur` déclaré `true` **alors qu'aucun fait n'est touché** » | aucun. `true` fait rendre **3** à la table — un plafond — et le modèle a déclaré **2**, donc *sous* son propre plafond. Il n'y a pas de déclaration abusive, il y a une sous-déclaration systématique |

### La leçon de méthode du lot

**Un garde-fou peut être vivant et son voyant éteint.** Le réflexe, devant
« `selfcheck` disait 10/10 », est de conclure que le contrôle est mort et de le
réécrire. La mesure dit qu'il y en avait **deux**, que l'un a fait son travail, et
que l'autre regardait la bonne chose au mauvais niveau d'agrégation.

Réécrire le premier aurait coûté du temps et n'aurait rien réparé. La distinction
ne se voit qu'en **rejouant la panne** — ce qui est aussi, exactement, ce que le
test du test institutionnalise pour la prochaine fois.

---

# DIAGNOSTIC — lot 11 : trois trous de contenu, deux portes fermées

Relevé du **19/08/2026**, sur une copie de la base servie (276 Mo, 286 sélections,
17 sessions) et sur ~90 appels de sonde à `tennis-api.com`. Arbre propre au
démarrage, aucune modification concurrente.

**Quatre affirmations du brief sont contredites par la mesure**, et l'une d'elles
a failli produire un résultat faux de ma part avant d'être vérifiée.

---

## §1 — L'heure de coup d'envoi au tennis

### §1a — Ce que la source sert, et ce qu'elle ne sert pas

**Les endpoints de calendrier existent mais ne sont pas atteignables à l'aveugle.**
`/date/fixtures` répond, valide correctement `startDate` au format `YYYY-MM-DD`,
et **rejette `tourType` sur 18 orthographes réparties en 6 noms de paramètre** —
`atp`, `wta`, `1`, `2`, `singles`, `atp_singles`… sous `tourType`, `tour`, `type`,
`tour_type`, `tourtype`, `circuit`, `tourTypes`. Le message est constant :
`"Tour type is not valid."`

`/tournament/fixtures/{id}/{n}` existe aussi et exige deux segments numériques,
mais rend `result: null` sur toutes les valeurs essayées.

**L'exploration s'arrête là, délibérément.** Le projet a déjà payé une fois ce
chemin — *« six appels ont été perdus à le chercher avant de le lire dans la
documentation »* (lot 4, sur le préfixe `/tennis/v2/extend/api/`). Continuer à
deviner reproduirait la même dépense. **Ce qu'il faut est la documentation
RapidAPI du fournisseur, pas d'autres essais.**

### Le seul champ d'heure servi est un marqueur de session

`event/get` porte `startTimestamp`. **Ce n'est pas un coup d'envoi**, et la mesure
est sans appel :

| | Mesure |
| --- | ---: |
| matchs archivés avec horodatage | 1 878 |
| **partageant leur horodatage** avec un autre match du même tournoi-jour | **1 133 (60 %)** |
| plus gros groupe simultané | **21 matchs** (Cincinnati, 16/08 15:00 UTC) |

Cincinnati, en heure locale (UTC−4) :

| Heure locale | Matchs |
| --- | ---: |
| **11:00** | **77** |
| **10:00** | **32** |
| 20:30 | 11 |
| 20:00 | 10 |

Soixante-dix-sept matchs ne commencent pas à 11:00 sur un court. **Ce sont des
heures de session** — session de jour à 10:00/11:00, session de nuit à
20:00/20:30. La source ne sert **ni le court, ni le rang dans le programme, ni de
mention « not before »** : `area`, `court`, `order` n'existent nulle part dans la
charge utile.

### Le faux résultat que j'ai failli rapporter

En confrontant `commence_time` à `startTimestamp` sur les rencontres appariées
(±48 h, même paire de joueurs) :

| | Mesure |
| --- | ---: |
| rencontres appariées | 283 |
| écart absolu médian | **1,60 h** |
| au-delà de 2 h | 118 (42 %) |
| source **plus tôt** que nous | 198 sur 245 signés |

Et la reclassification qui en découlait : **20 sélections changeaient de camp,
toutes vers « tardive »**, faisant passer la population du §5a du lot 10 de 52 à
**72** — soit +38 %.

**C'était un artefact, et il fallait le vérifier pour le voir.** Un marqueur de
session est systématiquement *antérieur* au coup d'envoi réel de tous les matchs
de la session sauf le premier ; le substituer à une estimation par match fait donc
mécaniquement paraître tardives des sélections qui ne le sont pas. Le signe
uniforme — 198 « plus tôt » contre 47 — était lui-même le symptôme.

**Conclusion : zéro sélection change de camp sur une base défendable. Le §5a du
lot 10 n'est pas à rejouer, et son verdict tient.**

### §1b — Branche « nulle part », et la formulation proposée

La source ne servant ni court ni rang, **rien n'est construit**. Ce que la mesure
autorise à dire, et qui n'est pas rien :

- **nous n'avons aucun moyen de vérifier notre propre heure de coup d'envoi.** Le
  seul autre relevé disponible est une heure de session. C'est ce qui rend une
  heure affichée au quart d'heure près dangereuse — elle est invérifiable, et se
  lit comme vérifiée ;
- le marqueur de session, lui, **est réel et mesuré** : à Cincinnati, 11:00 et
  20:00/20:30 locales.

**Formulation proposée, non appliquée** (le gabarit est hors périmètre) — elle
remplacerait l'heure dans l'en-tête de bloc tennis :

```
### M4 · TENNIS · ATP Cincinnati Open · Fritz – O'Connell · 20/08 01:00 (estimée)
    session du soir, à partir de 20:00 locales — rang sur le court inconnu
```

Trois propriétés, et chacune répond à un défaut mesuré : le mot **« estimée »**
retire la fausse précision ; l'**ancrage de session** est un fait vérifié ; et
**« rang inconnu »** dit pourquoi l'heure ne peut pas être serrée, au lieu de
laisser croire à une négligence.

---

## §2 — Le bilan de l'arbitre : branche « nulle part », et le brief se trompe deux fois

Le brief pose : *« Or l'application a probablement déjà la réponse. Elle collecte
des matchs avec leur arbitre nommé et, sur les compétitions couvertes, des comptes
de cartons. »* **Les deux moitiés sont fausses.**

### §2a — Le dénominateur interdit tout agrégat

| | Mesure |
| --- | ---: |
| relevés `referee` en base | 180 |
| dont un nom non vide | **147** |
| **arbitres distincts** | **145** |
| compétitions distinctes | 21 |
| matchs par arbitre : médiane | **1** |
| matchs par arbitre : maximum | **2** |
| **arbitres à 5 matchs ou plus** | **0** |

Les deux plus vus — J. Pinheiro et S. Gozubuyuk — ont **deux** matchs. Aucun
seuil, si bas soit-il, ne rend une fréquence lisible sur un dénominateur de 1.

**La cause est structurelle et déjà écrite dans `CLAUDE.md`** : *« sur une saison
de Conference League, 157 arbitres sur 183 n'ont qu'un seul match »*. Notre base
n'enrichit que les matchs mis en shortlist — 180 en tout, sur 21 compétitions.
Un arbitre y revient par coïncidence, pas par construction.

### Et le numérateur n'existe pas non plus

| | Mesure |
| --- | ---: |
| événements avec arbitre nommé | 147 |
| dont avec statistiques de match | 144 |
| **dont la charge utile mentionne `card`** | **0** |
| **dont elle mentionne `penalt`** | **0** |

Les champs `yellow` et `red` que nous stockons existent bien — mais dans
`KIND_PROFILE`, où ce sont les **moyennes de l'équipe sur ses derniers matchs**,
pas les cartons *de ce match-là*. Ils ne sont donc attribuables à aucun arbitre.
Les penaltys ne sont collectés nulle part.

### §2b — Rien n'est construit, et la puce reste à arbitrer

La ligne `Arbitre` ne bouge pas. Ce que la mesure établit, c'est que **la puce du
gabarit demande une recherche dont l'échec est structurel** : huit arbitres, deux
sessions, huit « bilan cartons et penaltys non trouvé ».

**Reformulation proposée, non appliquée** — c'est un arbitrage :

> · **Arbitre** — le nom seul, et c'est une économie de recherche : sans lui il
> fallait une requête pour savoir qui arbitre. **Ne cherche son bilan cartons que
> si la sélection porte sur un marché de cartons** ; ailleurs, cette requête n'a
> jamais abouti dans le budget d'une session, et elle en coûte une.

---

## §3 — Les alertes météo officielles : la source existe, le filtre non

### §3a — Ce qui répond, et sous quelle licence

**MeteoAlarm répond, sans compte, et la licence est utilisable.**

| | Constat |
| --- | --- |
| accès | `feeds.meteoalarm.org/api/v1/warnings/feeds-{pays}` — **HTTP 200, sans clé** |
| `robots.txt` | **tout est commenté** : rien n'est interdit |
| licence | *« Licensed under terms equivalent to CC BY 4.0 »* — attribution requise, comme Open-Meteo |
| format | CAP 1.2, en JSON **et** en Atom |

Et surtout, l'objection que le projet avait notée — *« MeteoAlarm agrège l'Europe
mais n'émet rien »* — **est levée par la charge utile** : elle porte
`senderName: "GeoSphere Austria"` et `sender: cap@zamg.ac.at`. L'émetteur réel est
donc **recopiable**, exactement comme la ligne NWS le fait déjà. Le niveau 1 tient.

La charge utile sert tout ce dont la ligne existante a besoin : `event`,
`severity`, `urgency`, `certainty`, `onset`, `expires`, `headline`, `senderName`.

### Le filtrage régional, et c'est lui qui ferme la porte

Le brief le dit lui-même : *« C'est le point qui décide si le résultat est
exploitable ou du bruit. »* Il l'est.

| | Constat |
| --- | ---: |
| alertes dans le flux autrichien | **930** |
| zones distinctes (Autriche) | 116 |
| alertes dans le flux français | 85 |
| zones distinctes (France) | 93 |
| **aires portant un polygone** | **0 sur 1 860** |

Les aires ne portent que `areaDesc` (un nom de district) et un `geocode` EMMA_ID.
**Sans polygone, il n'y a pas de point-dans-polygone possible**, et il reste le
rapprochement par libellé — que ce projet interdit partout.

Et il échouerait, c'est mesuré sur nos propres villes de stade :

| Ville | Ce que notre géocodeur rend | Ce que MeteoAlarm écrit |
| --- | --- | --- |
| Klagenfurt | `admin2 = Klagenfurt am Wörthersee` | `Klagenfurt (Stadt)` |
| Annecy | `admin2 = Upper Savoy` | `Haute-Savoie` |
| Lyon | `admin2 = Rhône` | `Rhône` ✔ |
| Reims | `admin2 = Marne` | `Marne` ✔ |

**Le géocodeur localise ses noms d'unités administratives** — « Upper Savoy » en
anglais — et MeteoAlarm écrit les siens dans la langue du pays, avec ses propres
conventions (« Hautes Alpes » sans trait d'union). Deux sur quatre tombent, deux
non.

**Et le mode d'échec est le pire possible pour cette ligne.** Une alerte manquée
ne produit pas un silence : elle produit `aucune alerte en vigueur`,
c'est-à-dire **l'affirmation qu'on a regardé et qu'il n'y a rien**. Le gabarit
distingue exprès ce libellé de `non interrogées` ; un rapprochement par libellé
détruirait précisément cette distinction, sur la ligne dont le projet a mesuré
qu'elle a changé une analyse deux fois.

### §3b — Branche « nulle part », et ce qui la rouvrirait

Rien n'est construit, la ligne ne bouge pas, et **le troisième libellé reste le
bon** : personne n'a regardé, et c'est vrai.

Ce qui rouvrirait la question, et rien d'autre :

- une **table EMMA_ID ↔ ville** tenue à la main — une centaine d'entrées par pays,
  sur sept pays. C'est le coût réel, et il se compare à celui de
  `APIFOOTBALL_LEAGUES` ou `TENNISDATA_TOURNAMENTS`, qui ont été jugés
  soutenables. **C'est un arbitrage, pas une impossibilité** ;
- une source servant des **polygones** ou les coordonnées de l'aire ;
- un géocodeur rendant directement le code NUTS3 / EMMA de la ville.

**Hors d'Europe, la couverture est nulle** : MeteoAlarm est européen, et le Brésil
n'y figure pas. La mention actuelle y reste inchangée, définitivement.

---

## §4 — La variante A, appliquée

La puce tennis cesse de demander ce que la ligne `Ici` porte déjà, et nomme les
trois choses qu'aucune source ne sert — **durée**, **conditions de court**,
**double**. Elle dit aussi, et c'est ce qui la distingue de l'ancienne, qu'elle
s'applique **quand `Ici` est absente ou partielle**, avec la couverture écrite
dedans (« quatre blocs sur cinq, pas tous »).

La phrase *« aucune de nos sources ne les porte »* est retirée : devenue fausse le
19/08, dans la consigne qui commande la recherche la plus chère du lot.

**Coût : 213 → 331 tokens, soit +118.** Le lot 8 annonçait « environ le même » ;
la clause des deux régimes, qu'il n'avait pas prévue, se paie. Les deux plafonds
tiennent largement.

**Troisième variable de gabarit active — et sa coupe n'est PAS isolée** de celle de
la ligne `Ici` : les deux portent le 19/08. Tout écart mesuré autour de cette date
mélange leurs deux effets, et c'est écrit dans l'entrée de journal.

---

## §5 — La durée d'un match de tennis : **non**, et la question est close

**Réponse : non.** Aucune source du projet ne sert la durée.

`profile/matches-played` — **153 clés distinctes inspectées**, aucune ne contient
`duration`, `elapsed`, `minute`, `time` ni `length`. Voici la charge utile d'un
match complet, telle quelle :

```json
{
  "date": "2026-08-17T04:35:00.000Z",
  "result": "3-6 7-6(5) 6-0",
  "roundId": 5, "draw": 8, "best_of": null, "h2h": "1-0",
  "tournamentId": 16740,
  "tournament": {"id": 16740, "name": "Cincinnati Open - Cincinnati", "court": …},
  "player1": {"id": 11371, "name": "Elina Svitolina", "stats": {…}},
  "player2": {"id": 73274, "name": "Tereza Valentova", "stats": {…}}
}
```

Les `stats` portent aces, doubles fautes, première balle, balles de break, vitesse
moyenne de service, montées au filet — **jamais de durée**.

`event/get` porte `startTimestamp` (un début, et c'est un marqueur de session, cf.
§1) et une `timeline` dont **chaque entrée n'a que `id` et `text`** :

```json
{"id": "304408980", "text": "Game 1 - Hannah Klugman - holds to 40"}
```

Aucun horodatage par jeu, donc aucune durée reconstructible.

**Consequence : la ligne `Usure` reste le seul substitut**, et le gabarit a raison
de l'annoncer comme tel. La question est close, et la variante A du §4 la range
définitivement du côté « à chercher ».

---

## §6 — Dettes de forme

### Le registre couvre-t-il ce lot ? Par la mesure, oui

**Ce lot n'ajoute aucune fonction d'écriture** — les sondes lisent, la variante A
est du texte. Les trois vues du lot 10 le confirment, et le test comme le banc
lisent le **même** recensement :

| Vue | Résultat |
| --- | --- |
| `inserting_functions` | 3 fonctions, `combos.record` sur ses deux tables |
| `decorated_nodes` | les 3, toutes `fonction` |
| `REGISTRY` | les 3 |
| `mismatches()` | **aucun** |

Banc : **15 contrôles sur 15, 0 manque**, code de retour 0.

### `changelog_mesure` : une entrée

| # | Date | Portée | Ce qui change |
| ---: | --- | --- | --- |
| **23** | 19/08 | `gabarit` | **La puce tennis passe en variante A.** +118 tokens. Troisième variable active, **coupe jointe** avec la ligne `Ici`. |

Les §1, §2, §3 et §5 n'ont **pas** d'entrée : ce sont des mesures et des portes
fermées, et rien n'y change ce que le modèle lit.

### Le test de bout en bout

**Ce lot n'ajoute aucun objet au rendu** — pas de nouvelle ligne de bloc, pas de
nouveau format structuré. Les comptes du test restent ceux du lot 10, et les deux
tests qui gardent l'attribution des blocs `conf` par section passent inchangés.

---

## §7 — Ce que la mesure contredit dans le brief

**Quatre affirmations, et la première est la mienne autant que la sienne.**

| Ce qui était affirmé | Ce que la mesure dit |
| --- | --- |
| §1 : *« si l'heure de coup d'envoi est fausse de trois heures, des sélections classées tardives ne le sont pas »* — donc les 52 sont à rejouer | la seule heure alternative disponible est un **marqueur de session** (77 matchs à 11:00). Les « 20 sélections qui changent de camp » sont un **artefact de cette substitution**. Zéro change de camp sur une base défendable, et le §5a tient |
| §2 : *« l'application a probablement déjà la réponse »* | **non, deux fois** : 145 arbitres pour 147 matchs (médiane 1, zéro à ≥5), et **zéro** carton par match ou penalty en base |
| §3 : la source d'alertes est le point à établir | la **source** est le point facile — elle répond, gratuite, CC BY, et nomme son émetteur. C'est le **filtrage régional** qui ferme : zéro polygone sur 1 860 aires, et le géocodeur rend « Upper Savoy » là où MeteoAlarm écrit « Haute-Savoie » |
| §4 : la variante A coûte « environ le même » (lot 8) | **+118 tokens**. La clause des deux régimes, exigée par ce brief-ci, n'était pas dans l'estimation du lot 8 |

### La leçon de méthode du lot

**Une mesure qui confirme spectaculairement le brief mérite le même doute qu'une
mesure qui le contredit.** Le §1 produisait un résultat net, chiffré, dans la
direction annoncée — 20 sélections, +38 % de population tardive — et il était
faux. Ce qui l'a démonté n'est pas une intuition mais une question de forme :
*ce champ est-il vraiment ce que je crois ?* Soixante secondes de comptage — 21
matchs au même instant — ont suffi.

Le projet a une règle pour ça depuis le lot 8 (« cherchez l'identifiant ») et une
autre depuis le lot 9 (« un champ dont le nom évoque une date peut être un
entier »). En voici la troisième face : **un champ dont la valeur est plausible
peut décrire un autre objet que celui qu'on croit mesurer.**

---

# DIAGNOSTIC — lot 12 : clôture de soirée

Relevé du **19/08/2026**. Lot court, trois points, aucun n'ajoute de
fonctionnalité. Arbre propre au démarrage, aucune modification concurrente.

---

## §1 — Les deux reformulations du lot 11, appliquées

### §1a — Le bilan de l'arbitre devient conditionnel

La puce demandait, sur **tous** les blocs football, de chercher le bilan cartons
et penaltys de l'arbitre. Le lot 11 a mesuré pourquoi elle n'aboutit jamais :
**145 arbitres pour 147 matchs** en base (médiane 1, maximum 2, zéro à cinq
matchs), et **zéro carton par match ou penalty** collecté. Huit arbitres sur deux
sessions réelles, huit « bilan non trouvé ».

Elle ne se cherche désormais que **si la sélection porte sur un marché de
cartons**. Ailleurs, connaître les habitudes de l'arbitre ne débouche sur aucune
sélection possible : la requête est dépensée pour rien, et une session en a peu.

**La ligne `Arbitre` ne bouge pas.** Le nom reste une économie de recherche — il
évite une requête pour savoir qui arbitre. C'est la consigne de creuser qui
devient conditionnelle, pas la donnée.

**Coût : +72 tokens.**

### §1b — L'heure d'un bloc tennis est annoncée comme estimée

Deux blocs d'une session du 16/08 affichaient une heure fausse de deux à trois
heures. Le lot 11 a établi que ce n'est **pas** un défaut de collecte : aucune
source accessible ne sert le court ni le rang dans le programme, et le seul champ
d'heure de `tennis-api` est un **marqueur de session** — 77 matchs à 11:00.

L'heure est donc **invérifiable**, et une heure au quart d'heure près se lit comme
une heure ferme. Elle porte `(estimée)`, et le préambule cesse de conditionner
l'imprécision à un tournoi perturbé : elle est structurelle, le tournoi perturbé
ne fait que l'agrandir.

**La mention ne vise que le tennis, et la différence est le point.** Un coup
d'envoi de football est fixé à l'avance, et un report s'y dit déjà par
`_shift_line`. Marquer les deux ferait de la mention un décor, et elle cesserait
d'être lue.

**Coût : +92 tokens de cadre, plus ~3 par bloc tennis.**

### L'ancrage de session n'est pas rendu, et c'est mesuré

La formulation rédigée au lot 11 proposait trois propriétés :
`(estimée)`, l'ancrage de session (`à partir de 20:00 locales`), et
`rang sur le court inconnu`. **Seule la première est appliquée.**

| Compétition tennis | `timezone` | `city` | Matchs |
| --- | --- | --- | ---: |
| `tennis_atp_cincinnati_open` | **vide** | **vide** | 122 |
| `tennis_wta_cincinnati_open` | **vide** | **vide** | 112 |
| `tennis_atp_canadian_open` | **vide** | **vide** | 78 |
| `tennis_wta_canadian_open` | **vide** | **vide** | 63 |

Écrire « 20:00 **locales** » demanderait un fuseau, et le déduire de « Cincinnati
Open » est exactement ce que `CLAUDE.md` interdit **nommément** pour cette
colonne : *« rien ne se déduit d'un libellé, même règle que la surface et le
niveau »*. Le brief prévoyait le cas — *« si l'ancrage n'est pas disponible, la
mention `(estimée)` seule est déjà la correction »*.

Quant à `rang sur le court inconnu` : c'est un **constat de lot**, vrai sur tous
les blocs tennis. Le répéter par bloc reproduirait le défaut que
`render.common_unplayable` a corrigé — *« le relevé commun au lot se dit une
fois, pas vingt-quatre »*. Il vit donc dans le préambule.

### §1c — Le coût, et le troisième écart d'estimation n'a pas eu lieu

| | Tokens |
| --- | ---: |
| gabarit avant le lot | 21 224 |
| après §1a | 21 296 (**+72**) |
| après §1b | 21 388 (**+92**) |
| **total du lot** | **+164** |

Le brief demandait de mesurer chaque reformulation **séparément**, parce que la
variante A avait coûté +118 là où le lot 8 annonçait « environ le même ». Les deux
coûts sont ici mesurés avant d'être annoncés, et il n'y a pas de troisième écart.

Une entrée `changelog_mesure` (#24) dit que **les deux arrivent le même jour** et
ne sont donc isolables ni l'une de l'autre, ni de la variante A, ni de la ligne
`Ici`.

---

## §2 — Le coût fixe du gabarit, relu

**Lecture, pas chantier.** Rien n'est conclu, aucune coupe n'est proposée.

| Jour | Prompts | Blocs | Cadre | / bloc | Changement de cadre |
| --- | ---: | ---: | ---: | ---: | --- |
| 04/08 | 16 | 138 | **1 621** | 62 | |
| 05/08 | 16 | 92 | 3 159 | 97 | |
| 06/08 | 20 | 324 | 3 605 | 240 | |
| 07/08 | 5 | 37 | 5 501 | 369 | |
| 08/08 | 7 | 54 | 6 398 | 419 | |
| 09/08 | 16 | 99 | 5 996 | 483 | |
| 10/08 | 6 | 23 | 8 140 | 576 | |
| 11/08 | 4 | 26 | 7 943 | 579 | |
| 12/08 | 10 | 75 | 8 279 | 573 | |
| 13/08 | 10 | 119 | 10 230 | 741 | |
| 14/08 | 17 | 168 | 11 620 | 556 | |
| 15/08 | 19 | 215 | 12 037 | 678 | *lot 0 — état antérieur* |
| 16/08 | 2 | 30 | **16 967** | 692 | |
| 17/08 | 4 | 24 | 12 340 | 650 | *budget de recherche à 10* |
| 18/08 | 6 | 36 | 12 426 | 840 | *coupe jointe : budget + lignes de service* |
| 19/08 | 1 | 9 | 13 808 | 818 | *ligne `Ici`, variante A, et les deux de ce lot* |

**La part du cadre.** Sur les quinze derniers prompts, le cadre pèse **12 390
tokens** et un bloc **810**. Le lot médian des 154 prompts découpés fait **8
blocs**. Un prompt médian pèse donc ~18 900 tokens, **dont 66 % de cadre**.

Le cadre a été multiplié par **7,6** en quinze jours — de 1 621 à 12 390.

Trois remarques de lecture, et aucune n'est une conclusion :

- **le pic du 16/08 (16 967) porte sur deux prompts seulement.** La médiane
  journalière suit un prompt aberrant quand la journée n'en compte que deux ;
  c'est pourquoi le module la calcule en médiane et pourquoi cette ligne-ci ne se
  lit pas comme une marche ;
- **les marches ne s'alignent pas toutes sur une entrée du journal.** La montée de
  1 621 à 8 279 court du 04 au 12/08, avant que `changelog_mesure` existe (première
  entrée : 15/08). Cette partie de la courbe **n'est pas décomposable**, et rien ne
  la rendra telle ;
- **le coût par bloc a grossi aussi**, de 62 à 810 : le cadre n'explique pas tout.

Le tableau du §15 du lot 5 — les cas décrits par le gabarit et jamais rencontrés —
existe déjà pour arbitrer, et c'est un arbitrage.

---

## §3 — La purge des artefacts temporaires

### La fuite était dans le dépôt

**Le code applicatif ne crée presque rien dans `/tmp`** — un seul `mkdtemp()`,
dans `selfcheck`, qui nettoie déjà derrière lui. Les **208 répertoires anonymes,
63 Mo** mesurés ce jour venaient tous de `tests/helpers.migre_jusqu_a`, qui copie
les migrations dans un `mkdtemp()` et **ne le retirait jamais**. Il le retire
désormais dans un `finally`. Après une suite complète : **zéro répertoire du
projet subsiste**.

### Le préfixe, et pourquoi il a fallu le créer

`tempfile.mkdtemp()` sans préfixe rend `/tmp/tmpXXXXXXXX`, **indiscernable de
celui de n'importe quel autre programme**. Les 208 dossiers n'étaient donc pas
réclamables par une règle sûre : rien dans leur nom ne les rattachait à ce dépôt.

`selfcheck` et le helper portent maintenant `TEMP_PREFIX = "myassistantbet-"`, et
la purge **ne connaît que lui**. Un `tmp*` aurait emporté `pytest`, `uv`, `ruff`
et les copies de travail — ce répertoire est partagé par toute la machine.

**Les répertoires de `pytest` ne sont pas touchés**, et ce n'est pas un oubli :
il fait sa propre rotation (trois exécutions), et les retirer pendant qu'une suite
tourne lui retirerait sa base sous les pieds.

**Vingt-quatre heures et non une** : une suite dure quatre minutes, mais une
session de travail garde ses copies ouvertes toute une journée.

### Où elle tourne

Dans le job des **sources gratuites**, une fois par jour — et non au démarrage :
un service reste allumé des jours, et une purge qui ne tourne qu'au redémarrage
ne tourne pas. Elle journalise son compte et l'espace libéré ; ce qu'elle ne sait
pas retirer est **compté et laissé**, jamais tu.

### Ce que le premier passage a libéré

| | Mesure |
| --- | ---: |
| nettoyage ponctuel des 208 dossiers antérieurs au préfixe | **78 retirés, 8,5 Mo** |
| laissés (contenu non reconnu, ou moins de 24 h) | 130 |
| premier passage de la purge automatique | **0** — plus rien ne porte le préfixe |
| `/tmp` | 78 % → **68 %** |

Le nettoyage ponctuel s'est fait sur un critère **vérifié** et non sur un motif :
un répertoire dont le contenu est *exactement* des fichiers de migration de ce
dépôt, et vieux de plus de 24 h. Les 130 autres sont laissés.

**La purge automatique a rendu 0 au premier passage, et c'est le résultat
attendu** : elle ne connaît que le préfixe, et rien ne le portait encore.

---

## §4 — Ce que la mesure contredit dans le brief

**Une seule affirmation, et deux précisions.**

| Ce qui était affirmé | Ce que la mesure dit |
| --- | --- |
| §3 : *« essentiellement des copies de base et des runs de suite retenus par pytest »* | les copies de base étaient **hors `/tmp`** depuis le lot 10, et `pytest` fait sa rotation. Le consommateur non surveillé était **une fuite du dépôt** — 208 répertoires laissés par un helper de test — que personne n'avait attribuée |
| §1b : appliquer la reformulation « telle qu'elle est écrite » | **une propriété sur trois est applicable.** L'ancrage de session demanderait un fuseau que les quatre compétitions de tennis n'ont pas, et `rang inconnu` est un constat de lot qui appartient au préambule |
| §1c : *« il ne faut pas en découvrir un troisième »* | **il n'y en a pas** : +72 et +92, mesurés avant d'être annoncés |

### La leçon de méthode du lot

**Une convention écrite n'est pas un dispositif.** Celle de `/tmp` était dans
`CONTRIBUTING.md` depuis le lot 10, exacte et complète — et la fuite qu'elle
décrivait a continué de couler pendant deux lots, parce que rien ne l'appliquait.

C'est la même leçon que celle du registre d'écriture au lot 3 : *« une règle de
contribution ne se déclenche pas ; un test si. »* Ici, ni l'un ni l'autre — il
fallait **le code qui nettoie**, et le préfixe qui rend le nettoyage possible.

---

# DIAGNOSTIC — lot 13 : ce qui reste accessible au football, et le coût du cadre

Relevé du 20/08/2026, sur une copie de la base servie (`VACUUM INTO`, 298
sélections, schéma 64). Aucune migration appliquée.

## §1 — Le coût réel de la carte régionale

### §1a — Le dimensionnement, et il renverse l'estimation du brief

Le brief annonçait *« une quinzaine de lignes de saisie »* et sept pays. **Les
deux chiffres sont faux, et pas du même facteur.**

**Ce dont on dispose côté stade est meilleur qu'annoncé.** Le lot 12 avait
constaté `timezone` et `city` vides sur les quatre compétitions tennis ; côté
football la situation est inverse :

| Champ | Couverture |
| --- | ---: |
| coordonnées (`latitude` / `longitude`) | **142 / 142 relevés météo** |
| fuseau du lieu | 142 / 142 |
| ville + pays | 142 / 142 |

Le géocodage est déjà fait et déjà persisté. **Il n'y a donc rien à saisir du
côté du stade** — la question porte entièrement sur ce que MeteoAlarm expose en
face.

**Et là, il n'y a pas un schéma d'aire mais sept.** Sondage des flux pour les
31 pays de stade présents en base, et non pour les sept du brief :

| Schéma exposé | Pays | Villes de stade | Relevés |
| --- | ---: | ---: | ---: |
| **polygone** (NO, SE, CH, UK) | 4 | 14 | 16 (11,3 %) |
| `EMMA_ID` (ES, PT, AT, PL, CY, HR, DK, NL, SK…) | 10 | — | — |
| `NUTS3` (FR, RO, BG) | 3 | — | — |
| `NUTS2` (HU), `EMMA_ID`+`NUTS2` (BE) | 2 | — | — |
| `WARNCELLID` (DE) | 1 | — | — |
| `FIPS` (IE) | 1 | — | — |
| flux vide (IS) | 1 | — | — |
| **sous-total « code »** | **17** | **76** | **79 (55,6 %)** |
| déjà couvert par le NWS (US) | 1 | 13 | 15 (10,6 %) |
| **aucun flux** (CN, TR, CA, MX, BR, SA, LI, AL, CO) | 9 | 23 | 32 (22,5 %) |

Trois corrections au brief sortent de ce tableau :

- **la granularité est départementale, pas régionale** : 96 zones en France (les
  départements), 116 en Autriche (les districts), **233 en Espagne** ;
- **la Turquie n'est pas couverte du tout** — `HTTP 404` sur quatre graphies du
  slug, alors que le brief la comptait parmi les sept pays cibles. Elle porte
  4 villes de stade ;
- **le périmètre n'est pas de sept pays mais de trente et un.** Les lots de
  football tirent sur les qualifications européennes, qui balaient le continent :
  126 villes de stade distinctes sont déjà en base, et chaque compétition
  nouvelle en ajoute.

### §1b — La branche retenue : construire là où le polygone décide, refuser ailleurs

**Le critère du brief est un nombre d'entrées ; le critère qui tranche est le
mode d'échec.** Les 76 villes du groupe « code » passeraient la règle des ~100
entrées — et il ne faut pas les construire pour autant :

- une table `ville → EMMA_ID` **ne se vérifie contre rien**. `EMMA_ID` est un
  schéma interne à MeteoAlarm, sans registre public ; une entrée mal saisie
  pointe sur un district voisin et **rend `aucune alerte en vigueur`**,
  c'est-à-dire l'affirmation qu'on a regardé. C'est exactement le mode d'échec
  que le §1c du brief interdit, et sur la seule ligne du bloc dont le projet a
  mesuré qu'elle a changé une analyse deux fois ;
- il faudrait **sept tables**, une par schéma, et non une ;
- l'ensemble **croît avec chaque compétition ajoutée**, sans que rien ne signale
  qu'une entrée manque.

**Le polygone est d'une autre nature, et c'est pourquoi il est construit.** Il
voyage dans l'alerte elle-même : aucune table, aucun rapprochement par libellé,
aucune saisie, et le résultat se vérifie tout seul — le point est dans le
polygone ou il n'y est pas. Testé sur les coordonnées réelles de la base :

| Ville | Résultat |
| --- | --- |
| Oslo, Fredrikstad, Trondheim | **3 / 3 dans une alerte en vigueur** (`Mye lyn`, `Mye regn`) |
| Sion | dans une alerte `Strong rainfall` |
| St. Gallen, Göteborg, Glasgow, Edinburgh… | aucune alerte les couvrant — et c'est la réponse juste |

Livré : `providers.weather.meteoalarm()`, `services.weather.METEOALARM_COUNTRIES`,
et la résolution point-dans-polygone (`_ring`, `_inside`, `_meteoalarm`). Aucune
dépendance ajoutée — un lancer de rayon tient en quinze lignes.

### §1c — Le mode d'échec est fermé, et il a fallu le dire trois fois

- **une aire sans polygone se compte** (`alerts_unresolved`) et fait rendre
  **`non interrogées`**, jamais `aucune alerte`. Le champ `alerts_checked` reste
  absent : sans polygone on ne sait pas si l'aire couvre le stade, et ne pas
  savoir n'est pas une absence d'alerte ;
- **elle ne se range pas non plus dans `injoignables`** — le flux a parfaitement
  répondu, et y envoyer le cas ferait réessayer une source qui n'a rien à
  réessayer. C'est le rapprochement qui a échoué, pas la source ;
- **les dix-sept pays à code ne sont pas appelés du tout**, donc leur ligne dit
  `alertes officielles non interrogées (Spain)` — ce qui est vrai ;
- chaque échec est journalisé (`logger.warning`, pays et compte d'aires).

Réserve tenue : le journal passe par le log applicatif et **non par
`ingestion_rejects`**, contrairement à la lettre du brief. Cette table porte
`session_id`, `import_id` et des bornes de position dans un collage ; un relevé
météo n'a aucun des trois, et l'y forcer aurait dénaturé la table dont
`selfcheck-ingestion` tire son dénominateur. **Écart assumé, à arbitrer.**

Cinq tests couvrent les quatre états : alerte rendue avec son émetteur recopié
(`Meteorologisk Institutt`, la source de niveau 1), polygone qui ne couvre pas,
aire non résolue, flux injoignable, pays servi sans polygone.

## §2 — `venue.id` : la cause est mesurée, et le drapeau reste hors de portée

### La cause : le fournisseur ne le sert pas, et notre collecte le demande bien

| | |
| --- | ---: |
| relevés `venue` en base | 336 |
| portant le champ `venue_id` (donc collectés depuis le lot 10) | 194 |
| **`venue_id` réellement servi** | **40 / 194 — 21 %** |
| nuls renvoyés par le fournisseur | 154 |

**Le brief et le lot 10 sous-estiment la portée.** Ce n'est pas un défaut UEFA :

| Compétition | servi / demandé |
| --- | ---: |
| MLS | **0 / 26** |
| UEFA Europa Conference League | 0 / 22 |
| UEFA Europa League | 0 / 12 |
| Primeira Liga, La Liga, Leagues Cup, Ekstraklasa | 0 partout |
| Championship | 8 / 12 |
| Bundesliga 2, Eredivisie | 5 / 9 |
| Ligue 2 | 4 / 9 |

Fait nouveau : **la distribution n'est pas binaire par compétition.** Le lot 12
avait établi que l'arbitre l'était (une compétition désigne sur toutes ses
rencontres ou sur aucune) ; ici Championship sort 8 sur 12 et Ligue 2 4 sur 9.
C'est donc **par match**, et aucun constat de compétition ne peut être mémorisé.

### Le pays est disponible autrement — et il ne suffit pas

Les deux moitiés existent déjà en base : `home_country` (le pays du club, servi
par `/teams`, 194 relevés) et `geo_country` (le pays de la ville, géocodé,
131 relevés). **113 relevés portent les deux**, et leur comparaison donne :

| | |
| --- | ---: |
| accord — même pays | 73 |
| désaccord | 40 |
| — dont **désaccords de vocabulaire** | **37** |
| — dont vraies délocalisations | **2** |
| — dont **faux positif** | **1** |

Les 37 sont le motif déjà payé par l'entraîneur : « Republic of Türkiye » contre
« Turkey », « United Kingdom » contre « Scotland » et « England », « The
Netherlands » contre « Netherlands », « Saudi Arabia » contre « Saudi-Arabia »,
« United States » contre « USA ». Ils se réduiraient par une table de synonymes
d'une dizaine d'entrées.

**Mais ce qui reste après ce nettoyage tranche la question, et dans le mauvais
sens :**

| Cas | `geo_country` | pays du club | Verdict |
| --- | --- | --- | --- |
| Dinamo Minsk au Stadion Beroe, Stara Zagora | Bulgaria | Belarus | **vraie délocalisation** |
| Hapoel Be'er Sheva à la Superbet Arena, Bucarest | Romania | Israel | **vraie délocalisation** |
| **Sevilla, à Séville** | **Colombia** | Spain | **faux positif** |

**Un faux positif sur trois, et c'est le pire des trois.** Séville reçoit chez
elle, dans sa propre ville ; un drapeau calculé là-dessus écrirait
`TERRAIN NEUTRE` avec autorité sur un match parfaitement ordinaire, et l'analyse
retirerait l'avantage du terrain sans que rien ne le contredise. C'est
littéralement la règle de revue du dossier de projet — *« un drapeau booléen ne
se construit pas sur un champ dont on a mesuré qu'il ment »* — désormais chiffrée.

**Rien n'est construit, et la mention reste mot pour mot.** `pas d'identifiant
de stade ici, terrain neutre non vérifiable` porte 188 blocs sur 1 405 et fait
tout le travail : elle expose le nom du stade, la ville et le pays, et laisse
l'arbitrage au lecteur. C'est la moitié qui marche d'un mécanisme dont l'autre
moitié n'a jamais pu se déclencher.

**Conséquence pour le §3, comme le brief le demande : `Lieu — TERRAIN NEUTRE`
sort de la liste des candidats à la coupe.** Ce n'est pas une règle inutile,
c'est une règle en attente de sa donnée — et la donnée qui manque n'est ni le
pays du club ni la ville, qui sont là, mais une source de lieu qu'on puisse
gager.

## §3 — L'arbitrage du gabarit : proposition écrite, non appliquée

### La mesure qui déplace la question

Le tableau du §15 du lot 10 comptait les déclenchements ; il manquait le coût.
Une fois les deux mis côte à côte, **la prémisse de l'arbitrage tombe** :

**Les quinze cas du tableau du §15 sont déjà gardés, tous les quinze**, chacun
par sa porte `context_labels` — vérifié ligne à ligne dans le gabarit :

| Cas | Porte | Coût sur un lot qui ne le porte pas |
| --- | --- | ---: |
| `Statut` | `'Statut' in context_labels` | **0** |
| `Météo` — les trois états | `'Meteo' in context_labels` | **0** |
| `Lieu` — non vérifiable / `TERRAIN NEUTRE` | `'Lieu' in context_labels` | **0** |
| `Absents` — les trois états | `'Absents' in context_labels` | **0** |
| `Entraîneur` — divergence / initiale | `'Entraineur' in context_labels` | **0** |
| `Arbitre` — les trois états | `'Arbitre' in context_labels` | **0** |
| `Effectif`, `Aller`, `Scénario`, `Tour`, `Elo`, `Densité` | idem | **0** |

**Couper un cas rare ne rapporterait donc rien** : il ne se paie déjà que sur
les lots qui le portent. Le brief supposait un coût fixe dû aux cas rares ; il
n'existe pas.

### Où le coût est réellement

| Section du gabarit | ~tokens | Part |
| --- | ---: | ---: |
| **COMMENT LIRE LES BLOCS** | **7 343** | **38 %** |
| ### C. Tableau des sélections | 3 429 | 18 % |
| ## TON RÔLE | 2 336 | 12 % |
| ### BUDGET DE RECHERCHE | 1 870 | 10 % |
| ## CE QU'IL FAUT VÉRIFIER | 1 577 | 8 % |
| ### D. Combinés | 1 583 | 8 % |
| B, E, F, MATCHS, A | ~1 100 | 6 % |
| **gabarit entier, toutes portes ouvertes** | **19 249** | |

Coût fixe **réellement facturé** en production, colonne `fixed_tokens` :
**12 217 à 13 808 tokens** sur les trois prompts du 19/08 — donc environ 6 000
tokens du chapitre sont payés sur un lot ordinaire, le reste étant fermé par les
portes.

**Et la part est passée la barre de la moitié** : sur le lot médian mesuré
(**8 blocs**, sur 156 prompts archivés), le cadre pèse **12 217 tokens contre
10 810 de blocs, soit 53 %**. Le gabarit coûte désormais plus que ce qu'il décrit.

### Ce que je propose de couper — trois entrées, ~740 tokens

Le critère appliqué : **couper là où le déclenchement est fréquent mais où la
description dépasse ce que le lecteur en fait**, jamais là où il est rare.

| Entrée | Coût | Blocs | Proposition | Ce qu'on perdrait |
| --- | ---: | ---: | --- | --- |
| `Scénario` | **380** | manches retour | **raccourcir à ~180** — l'arithmétique des deux seuils reste, les trois paragraphes de règlement UEFA descendent dans `CLAUDE.md` | la nuance « égaliser » / « passer » si la coupe mord trop bas ; à relire ligne à ligne |
| `Entraîneur` — `divergence` | **208** | 67 (4,8 %) | **raccourcir à ~110** — les trois mentions restent définies, la mesure des « 20 paires dont 10 le même homme » descend ici | rien, si les trois libellés gardent leur comportement |
| `Palmarès` | **295** | tennis rattaché | **raccourcir à ~150** | la distinction vainqueur / finaliste doit rester : c'est l'erreur la plus visible de la ligne |

### Ce que je propose de garder tel quel, et pourquoi

- **`Statut` (87 tokens, 1 bloc) et `Météo — ALERTE` (86 tokens, 4 blocs).** Les
  deux plus rares du relevé, et les deux qui ont changé une analyse. Ils sont
  déjà gardés : leur coût sur un lot qui ne les porte pas est **nul**. Les
  couper économiserait zéro et perdrait les deux cas décisifs — la mauvaise
  affaire que le troisième garde-fou du brief décrit exactement ;
- **`Lieu — TERRAIN NEUTRE` (49 tokens, 0 bloc)** — retiré des candidats par le
  §2 : cas empêché, pas cas inutile ;
- **`Alerte` — handicap suspect (0 bloc)** — coût fixe déjà nul (`handicap_alerts`) ;
- **`Absents — source injoignable` (99 tokens, 0 bloc)** — c'est un résultat sur
  le fournisseur, et les trois états perdent leur sens dès qu'il en manque un ;
- **les trois états de `Météo` ensemble.** La porte est sur la famille, pas sur
  l'état : un lot qui rend `Météo` paie les trois (161 tokens). Les séparer
  serait une erreur — c'est précisément leur distinction qui porte l'information,
  et le §1 vient d'en ajouter la matière.

### La cible n'est pas un pourcentage, et voilà ce que ça donne

~740 tokens sur 12 217 de coût fixe, soit **6 %**. C'est peu, et c'est le
résultat honnête : **le chapitre n'est pas gras, il est simplement grand**, et
il l'est parce que chaque ligne de bloc ajoutée depuis le lot 6 y a déposé son
mode d'emploi. La vraie économie serait ailleurs — section C (3 429 tokens) et
BUDGET DE RECHERCHE (1 870) —, et elle touche des règles qui **décident de ce
qui est rendu**, donc hors du périmètre de cet arbitrage.

**Rien n'est appliqué.** Le tableau est là pour être tranché.

## §4 — Relevé consolidé de l'état de la mesure

**Sans conclusion.** Les effectifs sont de quelques dizaines et les activations
datent de deux jours. Ce relevé dit où on en est, pas ce que ça vaut.

### §4.1 — Les trois populations

| Population | Sélections | Tranchées | Gagnées | Fenêtre |
| --- | ---: | ---: | ---: | --- |
| principale | 225 | 209 | 105 | 05/08 → 19/08 |
| tardive | 52 | 52 | 32 | 05/08 → **17/08** |
| exploratoire | 21 | 14 | 4 | 17/08 → 19/08 |
| **total** | **298** | **275** | **141** | |

La somme retombe sur le `COUNT(*)` de `picks` (298). La population **tardive
s'arrête au 17/08** — la garde d'écriture ne laisse plus rien y entrer.

Depuis les activations :

| Fenêtre | Sélections | Tranchées |
| --- | ---: | ---: |
| avant le 18/08 | 254 | 249 |
| 18/08 — lignes de service | 25 | 25 |
| 19–20/08 — ligne `Ici` | 19 | **1** |

**Les 19 sélections postérieures à `Ici` n'ont qu'un résultat saisi.** Rien ne
se lit encore sur cette fenêtre.

### §4.2 — `source_level` et angle

| `source_level_effective` | Sél. | Tranchées | Gagnées |
| --- | ---: | ---: | ---: |
| lecture | 132 | 119 | 57 |
| (non renseigné) | 101 | 100 | 48 |
| 2 | 28 | 25 | 17 |
| 1 | 25 | 20 | 11 |
| 3 | 9 | 8 | 5 |
| 4 | 3 | 3 | 3 |

| Angle | Sél. | Tranchées | Gagnées |
| --- | ---: | ---: | ---: |
| issue | 102 | 89 | 38 |
| (non renseigné) | 101 | 100 | 48 |
| manière | 95 | 86 | 55 |

Le déclaré et l'effectif divergent fortement : `source_level` déclaré donne 77
en niveau 2 et 73 en niveau 1, quand l'effectif en garde 28 et 25. **La cause
est unique et elle est nommée : `research_override_cause = ligne_absente` sur
89 sélections** — la ligne `dossiers_ouverts` n'est toujours pas collée, donc
tout le lot bascule en lecture. C'est le régime que le lot 12 décrivait, et il
n'a pas changé.

### §4.3 — Écart cran déclaré / recalculé

**147 crans calculés sur 298** ; accord **16 sur 147 — 11 %**.

| Déclaré → calculé | n |
| --- | ---: |
| 3 → 1 | 51 |
| 4 → 1 | 35 |
| 2 → 1 | 33 |
| 5 → 1 | 6 |
| 1 → 1 (accord) | 7 |
| 3 → 3, 4 → 4, 5 → 5 (accord) | 9 |
| 2 → 3, 4 → 5 | 6 |

**125 des 131 désaccords sont un écrasement vers 1**, c'est-à-dire le même
`ligne_absente` que ci-dessus. L'écart ne mesure donc pas la rédaction du
gabarit tant que la ligne n'est pas collée.

### §4.4 — Couverture des lignes ajoutées, par circuit

Seuils réels : `MIN_SERVE_POINTS` = 400, `MIN_GAMES` = 300.

| Circuit | Surface | Joueurs | `Service` | `Retour` | `Jeux` |
| --- | --- | ---: | ---: | ---: | ---: |
| ATP | Hard | 130 | 129 | 127 | 0 |
| ATP | (toutes) | 130 | 130 | 130 | **4** |
| ATP | Clay | 127 | 114 | 113 | 0 |
| ATP | Grass | 122 | 73 | 73 | 0 |
| ATP | I.hard | 117 | 80 | 78 | 0 |
| WTA | Hard | 120 | 118 | 118 | 0 |
| WTA | (toutes) | 120 | 120 | 120 | **10** |
| WTA | Clay | 118 | 107 | 108 | 0 |
| WTA | Grass | 117 | 70 | 70 | 0 |
| WTA | I.hard | 66 | 22 | 21 | 0 |

1 167 lignes, **250 joueurs**. `Service` et `Retour` sont servis sur la quasi
totalité des joueurs sur dur ; **`Jeux` sort sur 14 joueurs sur 250**, et
uniquement sur le repli toutes surfaces — maximum par surface **257** pour un
seuil à 300. C'est le blocage structurel que le lot 12 avait nommé, mesuré ici.

`Ici` : la ligne est branchée (`serve_stats`, libellé `Ici`) ; sa couverture de
production a été relevée au lot 12 à 79 % et n'est pas rejouée ici.

### §4.5 — Le coût fixe du gabarit

| Date | Prompts | `fixed_tokens` | Tokens / bloc |
| --- | ---: | ---: | ---: |
| 13/08 | 10 | 9 517 – 10 994 | 730 |
| 15/08 | 19 | 10 875 – 13 118 | 691 |
| 17/08 | 4 | 11 472 – 12 454 | 648 |
| 18/08 | 6 | 11 730 – 13 049 | 850 |
| **19/08** | 3 | **12 217 – 13 808** | **1 168** |

Lot médian sur 156 prompts : **8 blocs**. Sur ce lot, le cadre pèse
**12 217 tokens pour ~10 810 de blocs — 53 % du prompt**.

## §5 — Dettes de forme

- **Registre des chemins d'écriture** : ce lot n'ajoute aucun `INSERT` vers
  `picks`, `combos`, `combo_legs` ou `set_scores` — la météo écrit dans
  `context` par `store()`, hors périmètre du registre. `tests/test_write_paths.py`
  passe, et son critère lit la source : un chemin ajouté sans déclaration
  ferait tomber la suite. Rien à déclarer.
- **`changelog_mesure`** : une entrée, à sa date d'activation (voir ci-dessous).
- **Test de contrat d'en-tête et bout en bout** : ce lot n'ajoute aucun objet
  d'en-tête ni aucun format structuré collé — la ligne `Météo` existait déjà et
  ses quatre états sont couverts par cinq tests neufs. Le banc de transport
  n'est pas concerné : aucun nouveau format ne traverse un collage.
- **Purge du lot 12** : aucun artefact `myassistantbet-*` ne subsiste dans
  `/tmp`. Le tmpfs est à **68 %**, contre 96 % au relevé du 19/08 consigné dans
  `CONTRIBUTING.md`. Le journal systemd n'expose pas la ligne de purge sur la
  fenêtre lisible, donc **l'attribution n'est pas prouvée** : la rotation de
  `pytest` explique une part inconnue de la baisse.

## §6 — Ce que la mesure contredit dans le brief

Quatre affirmations, dont trois qui changent une décision.

| Affirmé | Mesuré |
| --- | --- |
| « une quinzaine de lignes de saisie », sept pays | 31 pays de stade, 126 villes, **sept schémas de code** — et 0 ligne à saisir pour la branche construite |
| la granularité est peut-être régionale | **départementale** : 96 zones en France, 116 en Autriche, 233 en Espagne |
| la Turquie fait partie des sept pays cibles | **aucun flux MeteoAlarm** — 404 sur quatre graphies |
| `venue.id` nul « sur 210 blocs en Conference League » | nul sur **79 % de tout ce qui est demandé**, MLS comprise (0/26), et **par match** et non par compétition |

Et une du dossier de projet, que ce lot lève : *« MeteoAlarm agrège l'Europe
mais n'émet rien »*. La charge utile porte `senderName` — « Meteorologisk
Institutt » — donc l'émetteur réel est recopiable et le niveau 1 tient.

### La leçon de méthode du lot

**Le nombre d'entrées d'une table ne dit pas si elle doit exister ; son mode de
vérification, si.** Les 76 villes du groupe « code » passaient le critère du
brief (« moins de ~100 entrées → construis ») et ne devaient pas être
construites, parce qu'aucune de leurs entrées ne se vérifie contre quoi que ce
soit. Les 14 villes du groupe « polygone » n'ont demandé **aucune** entrée.

C'est la même bascule qu'au §2 : 40 désaccords de pays dont 37 de vocabulaire,
2 vrais et **1 faux**. Ce qui décide n'est ni le compte ni le taux, c'est de
savoir si l'erreur, quand elle survient, **se voit**. Un polygone qui se trompe
n'existe pas ; un `EMMA_ID` mal saisi et un `geo_country` qui dit « Colombia »
pour Séville se taisent tous les deux — et disent, en se taisant, l'inverse de
la vérité.

---

# DIAGNOSTIC — lot 14 : la ligne `dossiers_ouverts`, collage par collage

Relevé du 20/08/2026 sur une copie de la base servie. **Un seul sujet.**

## §1 — Où en est la ligne, collage par collage

Les 24 collages archivés, passés au lecteur réel (`confidence.read_opened`) et
non à une réimplémentation :

| id | date | sess. | car. | clé présente ? | état lu | repères |
| ---: | --- | ---: | ---: | --- | --- | --- |
| 1–7 | 17/08 14:08 → 16:14 | 15 | 702 – 1 313 | non | `absente` | — |
| 8–13 | 18/08 08:01 → 08:20 | 16 | 567 – 1 314 | non | `absente` | — |
| **14** | **18/08 22:48** | 17 | **16 559** | **oui** | **`renseignee`** | M1–M6 |
| 15 | 18/08 22:49 | 17 | 1 022 | non | `absente` | — |
| **16** | **18/08 22:50** | 17 | **17 780** | **oui** | **`renseignee`** | M1–M7 |
| 17 | 18/08 22:50 | 17 | 1 155 | non | `absente` | — |
| **18** | **19/08 15:59** | 17 | **21 559** | **oui** | **`renseignee`** | M1–M9 |
| 19 | 19/08 16:00 | 17 | 1 426 | non | `absente` | — |
| **20** | **19/08 21:09** | 17 | **25 128** | **oui** | **`renseignee`** | M1, M3–M8 |
| 21–24 | 19/08 21:09 → 21:13 | 17 | 674 – 1 432 | non | `absente` | — |

**La distribution est parfaitement bimodale** : quatre collages de 16 559 à
25 128 caractères, vingt de 567 à 1 432. Aucun intermédiaire.

### Les quatre cas, comptés et non supposés

| Cas | Collages | Sélections |
| --- | ---: | ---: |
| absente d'un collage **tronqué** | **20 / 24** | 89 |
| absente d'un collage complet (le modèle ne l'a pas produite) | **0** | 0 |
| présente et **non lue** | **0** | 0 |
| lue et **sans effet sur la sélection** | **0** | 0 |

**Et la troncature n'est pas accidentelle.** Les vingt collages courts
commencent tous exactement par `C. Tableau des sélections` suivi de l'en-tête de
colonnes : ce sont des collages **du seul tableau**, pas des réponses coupées.

**Deux conclusions que ça ferme :**

- **le modèle produit bien la ligne** — les quatre collages complets la portent
  tous, avec 6 à 9 repères. Ni le gabarit ni l'extracteur ne sont en cause ;
- **le lecteur la lit bien** — quatre `renseignee` sur quatre, zéro `illisible`.
  Le correctif de libellé du lot 12 n'avait rien à rattraper ici.

### Le défaut annexe que le tableau révèle

`sessions.open_dossiers_state` vaut **`absente` pour la session 17**, alors que
quatre de ses collages portaient la ligne correctement lue. Cause :
`set_open_dossiers` **écrase à chaque import** — *« le dernier rendu collé décrit
l'analyse en cours »*, ce qui est juste pour un import ordinaire. Ici le dernier
collage de la session est un tableau seul, et il efface la déclaration des
quatre bons. Les crans déjà posés, eux, ne bougent pas : l'écrasement est décidé
par import.

## §2 — Les deux filets ont parlé, les vingt fois

`sections.for_paste()` rejoué sur les 24 collages, avec le prompt archivé de
leur session :

| | |
| --- | ---: |
| collages où le prompt **demandait** la ligne | **24 / 24** |
| collages où elle a été **trouvée** | 4 |
| **collages où l'avertissement se déclenchait** | **20 / 20** |
| imports validés malgré l'avertissement | **20** |

**Le filet n'a pas de trou** — et son `asked` se lit dans le **corps du prompt**,
pas dans le collage, donc un collage tronqué ne peut pas emporter avec lui la
question qu'on lui pose. Ce point-là était le piège possible, et il est fermé.

**Correction de datation, et elle change l'analyse.** Le brief attribue ce
branchement au lot 12 : il date du **17/08 à 14:04** (`421afa8`), et la section
`dossiers_ouverts` y figurait dès l'origine. Le premier collage archivé est de
14:08 — **quatre minutes plus tard**. Il n'existe donc aucun collage antérieur au
filet : l'avertissement était affiché sur **la totalité** des vingt.

**Le problème n'est donc plus technique.** C'est la formulation exacte du brief,
et la mesure la confirme : *un signal qui existe et que rien n'arrête*. Vingt
avertissements lus, vingt imports validés, 89 sélections en lecture.

## §3 — Le correctif : un refus, pas un avertissement de plus

La branche est celle que le brief prévoit pour le collage tronqué — **bloquer
plutôt qu'avertir**. Un vingt-et-unième avertissement aurait le même effet que
les vingt premiers.

**`sections.SessionSections.blocking`** isole la seule section dont l'absence
coûte les crans du **lot entier** et non d'une ligne : un bloc `conf` manquant
coûte son cran à sa sélection, `dossiers_ouverts` fait basculer tout l'import en
lecture, cran 1. Les quatre autres sections restent des avertissements.

**Trois propriétés du garde-fou, et chacune vient d'un défaut déjà payé :**

- **il se recalcule depuis `imports_raw`** (`sections.for_import`), jamais depuis
  un champ caché. Ce qui garde l'import ne peut pas voyager par le formulaire
  qu'il garde ;
- **il ne bloque pas ce qu'il n'a pas vu.** Sans identifiant de collage — saisie
  à la main, rejeu — il se tait. Refuser là fermerait deux chemins pour en garder
  un ;
- **le service et sa surface sont livrés ensemble.** La case `confirm_partial`
  est émise par l'aperçu avec `required`, et le serveur refuse sans elle. Un
  refus serveur sans case serait un blocage sans issue — le défaut exact du motif
  de saisie tardive, resté sans surface pendant deux jours.

Le refus **nomme d'abord le geste qui répare** — recoller la réponse entière — et
seulement ensuite celui qui passe outre : la mesure dit que le collage du seul
tableau est une habitude, et un refus qui ne proposerait que de la confirmer
l'installerait.

Cinq tests, dont deux qui gardent le garde-fou contre sa propre panne : un
collage complet passe sans rien cocher, et un import sans identifiant passe aussi.

## §4 — Le rejeu, et ce qu'il ne peut pas rattraper

Simulation puis écriture sur les **quatre** collages qui portent la ligne
(14, 16, 18, 20), sauvegarde prise avant
(`myassistantbet-20260820-091747.db`).

| | Avant | Après |
| --- | ---: | ---: |
| sélections en `ligne_absente` | 89 | **82** |
| accord cran déclaré / recalculé | 16 / 147 — **11 %** | 19 / 147 — **13 %** |
| `sessions.open_dossiers` (s17) | `NULL`, état `absente` | `M1 M3 M4 M5 M6 M7 M8`, état `renseignee` |

**Sept sélections récupèrent leur cran**, toutes venues du collage 20 ; les
trois autres collages complets avaient déjà posé leurs blocs. La déclaration de
la session 17 est réparée — et elle ne l'aurait pas été par un import ordinaire,
`attach` refusant explicitement de poser `absente`.

**Sept sur quatre-vingt-neuf, et le brief se trompe sur la raison.** Il écrit
*« sans lui, ces 89 sélections seraient perdues »* : `imports_raw` n'en sauve que
sept, et les 82 autres ne sont pas perdues faute d'outil mais **faute de texte à
relire**.

| Sélections en `ligne_absente` | Session | Collage complet disponible ? |
| ---: | --- | --- |
| 16 | s11 | **aucun collage archivé** — antérieure à `imports_raw` |
| 27 | s14 | **aucun collage archivé** — idem |
| 19 | s15 | 0 sur 7 collages : la réponse entière n'a jamais été collée |
| 15 | s16 | 0 sur 6 collages : idem |
| 12 | s17 | 4 collages complets → **7 récupérées** |

Les 43 de s11 et s14 précèdent la migration 052. Les 34 de s15 et s16 ont bien
leur collage conservé — **et il ne contient que le tableau**. Aucun rejeu ne peut
faire apparaître un texte qui n'a jamais été collé.

**C'est l'argument le plus net pour le garde-fou du §3** : ce qui se répare en
dix secondes au moment du collage ne se répare plus du tout ensuite.

## §5 — L'arbitrage du lot 13 : rendre visibles les échecs de rapprochement météo

**Proposition écrite, non construite.**

Le constat du lot 13 tient : `ingestion_rejects` porte `session_id`, `import_id`
et des bornes de position dans un collage, dont un relevé météo n'a aucun. L'y
forcer fausserait le dénominateur de `selfcheck-ingestion`.

**La forme minimale retenue : un état sur la ligne `Météo` elle-même, et rien
d'autre.** Pas de table sœur.

- **Pourquoi pas une table sœur.** Une table `source_rejects` demanderait sa
  migration, sa page, son compteur et son entretien — pour un fait qui a déjà un
  porteur naturel. Et surtout, le projet a déjà payé une fois le prix d'une
  donnée collectée sans lecteur : `/players/squads`, des mois d'appels retirés
  par la migration 022. Un compteur d'échecs météo que personne ne consulte
  serait le même piège.
- **Ce qui existe déjà et suffit.** `payload["alerts_unresolved"]` porte le
  nombre d'aires non résolues, il est persisté dans `context`, et il décide déjà
  du libellé rendu. **La donnée est là ; ce qui manque est qu'elle se voie.**
- **La forme proposée, en une ligne de rendu.** Faire dire à la ligne *combien*
  d'aires n'ont pas été résolues, au lieu de retomber sur le libellé générique :
  `alertes officielles non interrogées (Norway — 3 aires sans polygone)`. Même
  idiome que la fenêtre de `Parcours`, que le compte de `Tour — phase non
  renseignée`, et que `Effectif` et sa fenêtre lue : **le compte est dans la
  valeur**, ce qui rend l'affirmation vérifiable d'un coup d'œil.
- **Ce que ça change, et ce que ça ne change pas.** L'état rendu reste
  `non interrogées` — le comportement du lecteur ne bouge pas, et c'est
  volontaire. Ce qui change est qu'un pays servi dont les aires cessent de porter
  leurs polygones **se voit dans le bloc**, au lieu de se confondre avec les
  dix-sept pays qu'on n'interroge pas du tout.
- **Coût.** Quelques tokens sur les seuls blocs concernés, aucune migration,
  aucune table, aucun seuil. La ligne `Météo` est déjà gardée par
  `'Meteo' in context_labels`.

**À arbitrer, et non retenu d'office** : si la distinction entre « pays non
interrogé » et « pays interrogé dont l'aire n'est pas résolue » ne change aucun
comportement, alors elle n'a pas sa place dans le bloc et le log applicatif
suffit. C'est la question à trancher, et elle ne se tranche pas depuis ici.

## §6 — Ce que la mesure contredit dans ce brief

| Affirmé | Mesuré |
| --- | --- |
| « Le lot 12 a branché `sections.for_paste()` sur l'aperçu » | branché le **17/08 à 14:04** (`421afa8`), avec `dossiers_ouverts` dès l'origine — quatre minutes avant le premier collage archivé |
| « Pour chaque collage **postérieur** au correctif… » | **les 24 sont postérieurs.** Le filet a parlé les 20 fois où la ligne manquait |
| « sans lui, ces 89 sélections seraient perdues » | `imports_raw` en sauve **7**. Les 82 autres n'ont pas de texte complet à relire — 43 sont antérieures à la table, 34 n'ont vu qu'un collage du tableau |
| le lot 8 concluait « ni le modèle, ni l'extracteur : le collage » | **confirmé, et c'est la seule des trois affirmations qui tienne** : 4 collages complets sur 4 portent la ligne, 4 sur 4 se lisent |

### La leçon de méthode du lot

**On a cru le sujet réglé deux fois parce qu'on a corrigé deux fois le lecteur,
et jamais le geste.** Le lot 8 a nommé la cause — le collage — et livré un
avertissement ; le lot 9 a livré `--rattacher`, qui répare après coup ; le lot 12
a corrigé un libellé. Les trois sont justes et aucun n'arrête quoi que ce soit.

Le relevé du §2 est le vrai enseignement : **vingt avertissements affichés,
vingt imports validés**. Un signal qui n'a pas le pouvoir de refuser se
consomme comme un élément de décor — et il se lit, dans les statistiques, comme
si la mesure fonctionnait. C'est la neuvième occurrence du motif du projet, sous
sa forme la plus retorse : ici l'échec **était** signalé, et le signal n'a rien
changé.

---

# DIAGNOSTIC — lot 15 : la répartition de mise, et le plafond qui n'en est pas un

## §1a — La table proposée se contredit : le plafond est la règle, l'unité est du décor

**Mesure du 20/08/2026, avant d'écrire une ligne de code.** Le brief propose une
table de mises et un plafond de session, et demande de confirmer les valeurs.
Elles ne peuvent pas être confirmées telles quelles : appliquées à l'historique,
**16 sessions sur 16 atteignent ou dépassent le plafond**.

Table proposée : unité = 1 % de la bankroll, sélection de section C = 1 unité,
sélection de C-bis = 0,25 unité, plafond de session = 5 % de la bankroll.

| Session | C | C-bis | Unités demandées | % de bankroll | Facteur de réduction |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 17 | 0 | 17,00 | 17,0 | 0,294 |
| 3 | 28 | 0 | 28,00 | 28,0 | 0,179 |
| 4 | 9 | 0 | 9,00 | 9,0 | 0,556 |
| 5 | 18 | 0 | 18,00 | 18,0 | 0,278 |
| 6 | 29 | 0 | 29,00 | 29,0 | 0,172 |
| 7 | 5 | 0 | 5,00 | 5,0 | 1,000 |
| 8 | 11 | 0 | 11,00 | 11,0 | 0,455 |
| 9 | 12 | 0 | 12,00 | 12,0 | 0,417 |
| 10 | 20 | 0 | 20,00 | 20,0 | 0,250 |
| 11 | 29 | 0 | 29,00 | 29,0 | 0,172 |
| 13 | 30 | 0 | 30,00 | 30,0 | 0,167 |
| 14 | 27 | 0 | 27,00 | 27,0 | 0,185 |
| 15 | 13 | 6 | 14,50 | 14,5 | 0,345 |
| 16 | 10 | 5 | 11,25 | 11,25 | 0,444 |
| 17 | 19 | 10 | 21,50 | 21,5 | 0,233 |
| 18 | 19 | 10 | 21,50 | 21,5 | 0,233 |

Unités demandées : **médiane 19,0**, min 5,0, max 30,0. **15 sessions sur 16
dépassent strictement** le plafond ; la seizième (session 7, cinq sélections)
tombe exactement dessus.

**Conséquence, et c'est elle qui interdit de coder la table telle quelle** : la
mise réellement appliquée à une sélection de section C ne vaut jamais 1 % de la
bankroll. Elle vaut **0,167 % à 1,000 %, médiane 0,264 %**. Le nombre écrit dans
la configuration et le nombre appliqué par le code diffèrent d'un facteur 4 en
régime courant, et d'un facteur 6 sur le plus gros lot.

- **Le plafond ne plafonne pas : il dimensionne.** Une règle qui s'applique 16
  fois sur 16 n'est pas un garde-fou, c'est le calcul principal. La table
  au-dessus n'est plus qu'un jeu de **poids relatifs** — le rapport 1 / 0,25
  entre C et C-bis survit à la réduction, la valeur absolue non.
- **C'est le défaut caractéristique du projet, déplacé sur l'argent.** « 1 unité
  = 1 % » se lit comme un fait — *je mets un pour cent sur cette sélection* —
  pendant que le code en met un quart. Rien ne casse, l'interface a l'air
  normale, et l'écart ne se découvre qu'en relisant le journal des mises.
  Dixième occurrence, et la première où le symptôme se compte en euros.
- **Le brief l'annonce lui-même sans le mesurer** : « le lot 14 a montré ce que
  vaut un avertissement qu'on peut valider : vingt fois sur vingt ». Un message
  de réduction affiché à chaque session subirait exactement le même sort.

### Ce que la mesure ne dit pas, et qui reste à trancher

Elle dit que les quatre nombres ne sont pas mutuellement cohérents. Elle ne dit
pas lequel bouge, parce que c'est une décision de l'utilisateur — combien il
accepte d'exposer par session, et non ce que les données autorisent.

Trois ancrages possibles, mesurés :

| Ancrage | Unité | Plafond | Effet mesuré |
| --- | --- | --- | --- |
| **A — l'unité est fixe, le plafond est un vrai refus** | 0,15 % | 5 % | le plus gros lot mesuré (30 unités) pèse 4,5 % : **le plafond ne mord sur aucune session de la base**. Porte fermée, pas défaut réparé |
| **B — le plafond est fixe, l'unité est dérivée** | budget ÷ unités demandées | 5 % | exact par construction, aucune réduction à annoncer. Mais une même sélection vaut 0,17 % un jour et 1 % un autre, selon le nombre de ses voisines |
| **C — l'unité est fixe et le plafond suit le lot** | 1 % | ≥ 30 % | conserve le nombre du brief au prix d'un tiers de bankroll exposé par session. Ce n'est plus un plafond |

**Deux faits qui pèsent sur le choix, et qu'aucune des trois options ne change :**

- **le plafond est « par session », et quatre jours sur quatorze portent deux
  sessions** (10/08, 14/08, 15/08, 18/08). Un plafond de session à 5 % vaut donc
  10 % ces jours-là. Un plafond par **journée** dirait ce que le brief semble
  vouloir dire ; un plafond par session dit autre chose ;
- **le combiné pèse pour rien dans l'addition.** Deux combinés existent en base,
  tous deux `court`, trois jambes, sur la même session. À 0,5 unité pièce ils
  représentent 1 unité contre 19 à 30 pour les simples : régler leur mise ne
  déplace pas le plafond.

## §1a bis — L'unité se mesure, et elle tombe sous 0,20 %

Arbitrage rendu le 20/08/2026 : **structure A** — unité fixe, plafond comme
porte fermée — **plafond par journée** et non par session, **C-bis sans mise**.
Les trois sont repris ci-dessous avec la mesure qui les accompagne.

### Le changement de régime ne touche pas la grandeur qui compte

La consigne demandait de ne retenir que le régime actuel — « des lots de 8 à
12 matchs, soit environ 4 à 5 sélections de section C ». Mesuré, et **la prémisse
ne tient pas dans les deux sens** :

| | Lots par session | Taille des lots | Sélections C par lot |
| --- | ---: | ---: | ---: |
| Session 15 (17/08) | 4 | 5 à 8 | 3,25 |
| Session 16 (18/08) | 4 | 4 à 8 | 2,50 |
| Session 17 (18/08) | 5 | 6 à 9 | 3,80 |
| Session 18 (19/08) | 5 | 4 à 10 | 3,80 |

Les lots font **4 à 10 matchs**, pas 8 à 12, et rendent **2,50 à 3,80**
sélections, pas 4 à 5. Mais surtout : il y en a **quatre à cinq par session**, et
le plafond porte sur la journée.

| Régime | Journées | Min | Médiane | Max |
| --- | ---: | ---: | ---: | ---: |
| ancien (05 – 15/08) | 10 | 9 | **19,0** | 57 |
| nouveau (≥ 16/08) | 3 | 13 | **19,0** | 29 |

**Les deux médianes sont identiques.** Les lots ont rétréci, la journée n'a pas
bougé — le découpage a été absorbé par le nombre de prompts. Restreindre la
mesure au nouveau régime ne retire donc que du volume : trois journées, sur
lesquelles un 90e centile ne veut rien dire (il tombe entre la 2e et la 3e
valeur). L'historique complet est la base honnête **parce que** la mesure montre
que le régime n'affecte pas cette grandeur.

### Le nombre, et il est sous le seuil que tu as posé

Journées, section C seule, C-bis retirée : `9, 12, 13, 16, 17, 18, 19, 20, 28,
29, 29, 29, 57`. **P90 = 29,0.**

> Unité = 5 % ÷ 29 = **0,172 %** de la bankroll par sélection de section C.

Sur le nouveau régime seul (n = 3, P90 = 27) : **0,185 %**. **Les deux sont sous
0,20 %**, et c'est le signal que tu as demandé de faire remonter plutôt que
d'absorber.

**Ce qui marche, et qu'il faut noter avant de rediscuter le plafond** : à cette
unité, le plafond de 5 % **ne mord qu'une journée sur treize** — le 15/08, ses
deux sessions et ses 57 sélections. C'est exactement le comportement voulu d'une
porte fermée : elle se déclenche sur l'anomalie, pas tous les jours. La structure
A tient. Seule la valeur absolue est en question.

| Plafond de journée | Unité dérivée du P90 | Journée médiane pèse | Le plafond mord |
| ---: | ---: | ---: | ---: |
| 5 % | **0,172 %** | 3,28 % | 1 journée / 13 |
| 7,5 % | 0,259 % | 4,91 % | 1 journée / 13 |
| 10 % | 0,345 % | 6,55 % | 1 journée / 13 |
| 12,5 % | 0,431 % | 8,19 % | 1 journée / 13 |
| 15 % | 0,517 % | 9,83 % | 1 journée / 13 |

Le taux de déclenchement ne dépend pas du plafond : il est fixé par le choix du
P90 comme ancrage. **Le plafond ne choisit donc pas la sécurité, il choisit
l'échelle** — c'est-à-dire combien pèse une sélection, et rien d'autre.

### La cause, et elle n'est pas dans les nombres

Une unité minuscule n'est pas un défaut de calibrage : c'est l'arithmétique d'une
méthode qui **produit dix-neuf sélections par jour**. Cinq pour cent répartis sur
dix-neuf paris font 0,26 % chacun, quel que soit l'habillage. Les deux seules
sorties sont d'exposer davantage, ou de produire moins.

Et la seconde n'est pas de mon ressort : le nombre de sélections est réglé par
les quotas de palier et le budget de recherche, qui ont été calibrés sur des
critères d'analyse, jamais sur une exposition. Les coupler maintenant ferait
exactement ce que l'arbitrage du plafond par journée vient d'écarter — lier le
garde-fou d'argent à la discipline d'analyse.

## §1a ter — La journée se compte sur `picks.created_at`, et l'unité vaut 0,25 %

**La première mesure groupait sur `sessions.created_at`, et c'était faux.** Les
deux dates divergent : la session 18 est datée du 19/08 et ses cinq prompts ont
tourné le **20/08 à 09h40 – 10h38**. La session 14, datée du 15/08, a produit ses
sélections jusqu'au 17/08. Grouper sur la date de session compte donc des
sélections d'aujourd'hui dans le budget d'avant-hier.

Le projet a déjà tranché cette question ailleurs, et dans le même sens : la
fenêtre de `feedback()` se compte sur `picks.created_at`, « la journée de la
**décision** et non celle du match ». C'est la même grandeur ici — le jour où
l'argent s'engage.

**La consigne demandait d'ancrer sur la date de session « et pas sur l'heure de
coup d'envoi ». Les deux candidats la respectent** — ni l'un ni l'autre ne
regarde un coup d'envoi. Ce qui les départage est mesuré :

| | Découpage en plusieurs sessions le même jour | Session laissée ouverte |
| --- | --- | --- |
| `sessions.created_at` | couvert | **trou** : les sélections du 20/08 comptent sur le budget du 19/08 |
| `picks.created_at` | couvert | couvert |

Le seul reproche possible au second — couper une soirée à cheval sur minuit —
**ne se produit sur aucune session de la base**. Trois sessions sur seize
couvrent deux jours civils (4, 14, 17), et aucune n'est une soirée coupée : la
17 va du 18/08 à 22h48 au 19/08 à 21h14, soit une session laissée ouverte une
journée entière, que le plafond **doit** compter pour deux jours. La plus tardive
s'arrête à 23h56 sans rien après.

### Les deux distributions, côte à côte

Journée d'analyse = `picks.created_at`, section C seule, C-bis retirée.

| Journée | Sessions | C | C-bis | Régime |
| --- | ---: | ---: | ---: | --- |
| 05/08 | 1 | 17 | 0 | ancien |
| 06/08 | 1 | 28 | 0 | ancien |
| 07/08 | 1 | 7 | 0 | ancien |
| 08/08 | 2 | 20 | 0 | ancien |
| 09/08 | 1 | 29 | 0 | ancien |
| 10/08 | 1 | 5 | 0 | ancien |
| 11/08 | 1 | 11 | 0 | ancien |
| 12/08 | 1 | 12 | 0 | ancien |
| 13/08 | 1 | 20 | 0 | ancien |
| 14/08 | 1 | 29 | 0 | ancien |
| 15/08 | 2 | 49 | 0 | ancien |
| 17/08 | 2 | 21 | 6 | **nouveau** |
| 18/08 | 2 | 17 | 8 | **nouveau** |
| 19/08 | 1 | 12 | 7 | **nouveau** |
| 20/08 | 1 | 19 | 10 | **nouveau** |

| Régime | n | P50 | P75 | P90 | max | Unité à 5 % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ancien (< 16/08) | 11 | 20,0 | 28,5 | 29,0 | 49 | 0,172 % |
| **nouveau (≥ 16/08)** | **4** | **18,0** | **19,5** | **20,4** | **21** | **0,245 %** |

**Le nouveau régime est plus resserré, et c'est ce qui sauve l'unité** : sa queue
haute tombe de 29 à 20,4, et l'unité remonte **au-dessus de 0,20 %** sans
toucher au plafond. La prémisse chiffrée qui accompagnait la consigne — « si le
P90 du régime actuel est autour de 10 à 12 sélections » — n'est pas vérifiée :
il vaut **20,4**. La direction était juste, l'ampleur non.

### Le nombre retenu, et il est provisoire

Quatre journées, quand la consigne fixait le seuil de défendabilité à une
dizaine. **Le plafond reste donc à 5 %**, l'unité en découle, et le nombre est
explicitement provisoire — à re-mesurer après un mois du nouveau régime, soit
vers le **20/09/2026**.

> **Unité = 0,25 % de la bankroll. Plafond de journée = 5 %, soit exactement
> 20 unités.**

L'arrondi de 0,245 à 0,25 n'est pas cosmétique : il fait tomber le plafond sur un
**compte entier de sélections**, donc sur une règle qui se vérifie de tête —
*au-delà de vingt sélections de section C dans la journée, la réduction
s'applique*. Un plafond exprimé en pourcentage d'un pourcentage ne se vérifie pas.

Ce que ça donne sur les journées mesurées : le plafond mord **une journée sur
quatre** dans le régime actuel (le 17/08 et ses 21 sélections), et la réduction y
vaut **5 %** — 21 unités demandées, 20 accordées. À comparer aux **83 %** de
rabot qu'aurait produits la table du brief sur la même journée.

### La garde demandée, et où elle se pose

La réduction proportionnelle est **annoncée nommément** — unités demandées,
unités accordées, facteur — dans la section de répartition du rendu comme dans le
relevé d'import. Jamais absorbée en silence. C'est la règle du lot 14 appliquée
d'avance : un rabot qui ne se nomme pas se lit comme une mise choisie.

## §1b — La formulation de gabarit, écrite avant d'être appliquée

Le brief impose d'écrire ici la formulation exacte avant de toucher au gabarit.
La voici, avec ce qui la justifie ligne par ligne.

### Où elle se pose, et sous quelle forme

**Section G, après la section F**, et une ligne dans l'en-tête pour la bankroll.
Le format de sortie est une **ligne à plat**, jamais un bloc clôturé — c'est la
règle la plus chère du dépôt, apprise trois fois, et la mesure qui la fonde est
sans appel : `picks.claim_raw_json` était NULL sur 235 sélections sur 235 parce
que les blocs ```conf perdaient leur clôture au collage. Une ligne `mises:` n'a
pas de clôture à perdre, exactement comme `sets:` et `dossiers_ouverts:`, les
deux formats qui n'ont jamais posé de problème.

Elle entre au banc de transport (`tests/test_transport.py`) comme **septième
format**, avec son entrée dans `ATTENDU`.

### Le texte

> ### G. Répartition de mise
>
> Cette section n'est produite **que si une bankroll de session est donnée** en
> tête de ce prompt. Sans montant, saute-la entièrement et n'écris pas non plus
> que tu ne l'as pas produite — la question n'est pas posée.
>
> Tu **appliques une table**, tu n'exerces aucun jugement. La mise ne dépend ni
> de la cote, ni du palier, ni de ton cran de confiance, ni d'aucun résultat
> antérieur : ces quatre-là servent à classer et à mesurer, jamais à doser.
> N'écris **aucune justification de mise** — pas de « je mets davantage ici parce
> que ». Ce serait le calcul d'espérance interdit en tête de ce prompt, sous un
> autre nom.
>
> La table de ce lot :
>
>   · unité de référence : **{{ mise_unite_pct }} % de la bankroll**
>   · une sélection de section C : **1 unité**
>   · une sélection de section C-bis : **aucune mise**
>   · un combiné : **{{ mise_combine_unites }} unité**
>   · plafond de la journée : **{{ mise_plafond_pct }} % de la bankroll**, soit
>     **{{ mise_plafond_unites }} unités**
>
> Les sélections de C-bis ne reçoivent aucune mise **parce qu'elles sont
> produites sans fait daté, sur lecture seule des blocs**. Ce n'est pas un
> jugement sur leur qualité : elles sont enregistrées et tranchées comme les
> autres, et c'est ce qui les mesure. Leur mettre un montant paierait une
> information qu'on obtient sans payer.
>
> Si le total des unités dépasse le plafond, réduis **proportionnellement** et
> dis-le nommément : combien d'unités demandées, combien accordées. Ne l'absorbe
> pas en silence.
>
> Après ta prose, une ligne unique pour tout le lot, hors de tout bloc de code :
>
>     mises: bankroll=200 | M3=0.50 | M7=0.50 | combine_court=0.25
>
> Un repère de bloc par sélection de section C, puis `combine_court` et
> `combine_long` s'ils existent, chacun avec son montant dans la monnaie de la
> bankroll, deux décimales. L'application recalcule la répartition depuis la
> même table : ne l'ajuste pas pour qu'elle tombe juste, l'écart est ce qui se
> lit.

### Les quatre points que le brief exige, et où ils sont

| Exigé | Où |
| --- | --- |
| le montant vient de l'utilisateur, et sans lui la section n'est pas produite | premier paragraphe, avec l'interdiction d'écrire qu'on ne l'a pas produite |
| la répartition applique la table, sans jugement | deuxième paragraphe, avec les quatre axes nommés — cote, palier, confiance, résultat antérieur |
| aucune justification de mise attendue | même paragraphe, rattaché à l'interdit d'espérance de la tête du prompt |
| C-bis réduite parce que produite sans fait daté, non parce que moins bonne | paragraphe dédié — et la réduction va jusqu'à zéro, arbitrage du 20/08 |

### Ce que le gabarit ne dit pas, et pourquoi

- **Il n'énonce pas la règle de non-progression.** Elle ne se tient pas par une
  consigne : elle se tient parce que la fonction qui calcule une mise **ne reçoit
  aucun résultat en paramètre**. Une contrainte de signature ne se contourne pas,
  une phrase si. Le gabarit dit seulement que la mise ne dépend d'aucun résultat
  antérieur, ce qui suffit à empêcher le modèle d'en inventer une.
- **Il ne fait pas calculer le plafond au modèle.** Les deux nombres — pour-cent
  et unités — descendent résolus, comme les quotas de palier et le budget de
  recherche. Une borne qu'il faut recalculer soi-même ne contraint rien.

## §2a — Le recensement des clés : tout est là, et presque rien n'est servi

**Mesure du 20/08/2026, sans un seul appel.** Les 735 réponses
`profile/matches-played` déjà archivées portent 73 027 matchs et 146 054 fiches
de joueur — de quoi recenser exhaustivement ce que la source sert, et à quelle
couverture. Aucun crédit dépensé.

### Les dix-huit clés de `stats`, et leur couverture

| Clé | ATP | WTA |
| --- | ---: | ---: |
| `firstServe` / `firstServeOf` | 97,7 % | 97,6 % |
| `aces` | 97,7 % | 97,5 % |
| `doubleFaults` | 97,7 % | 97,6 % |
| `winningOnFirstServe` / `Of` | 97,7 % | 97,6 % |
| `winningOnSecondServe` / `Of` | 97,7 % | 97,6 % |
| `breakPointsConverted` / `Of` | 97,7 % | 97,6 % |
| `totalPointsWon` | 97,7 % | 97,6 % |
| **`winners`** | **13,8 %** | **15,4 %** |
| **`unforcedErrors`** | **13,8 %** | **15,4 %** |
| `netApproaches` / `Of` | 13,4 % | 15,1 % |
| `fastestServe` | 9,8 % | 11,2 % |
| `averageFirstServeSpeed` | 9,5 % | 10,9 % |
| `averageSecondServeSpeed` | 9,5 % | 10,9 % |

Les deux clés que le brief demande **existent**. C'est leur couverture qui
tranche, et elle n'est pas ce que 14 % laisse croire.

### La couverture n'est pas partielle, elle est binaire : Grand Chelem, ou rien

Ventilé par niveau de tournoi, sur la fenêtre de 52 semaines que les agrégats
emploient — 10 345 matchs distincts :

| Niveau | Matchs | Avec `winners` |
| --- | ---: | ---: |
| **Grand Slam** | 1 335 | **99,4 %** |
| WTA 1000 | 1 057 | 0,0 % |
| WTA 125 | 1 021 | 0,0 % |
| ATP Masters 1000 | 983 | 0,0 % |
| ATP 250 | 930 | 0,0 % |
| WTA 250 | 735 | 0,0 % |
| ITF Event | 730 | 0,0 % |
| WTA 500 | 670 | 0,0 % |
| ATP 500 | 646 | 0,0 % |
| Challenger 75 / 125 / 100 / 175 | 1 817 | 0,0 % |
| Future, Fed Cup, Finals, autres | 372 | 0,0 % |

**Pas une seule exception hors Grand Chelem**, et le même mur pour toutes les
clés riches :

| Clé | Grand Chelem | Hors Grand Chelem |
| --- | ---: | ---: |
| `winners` | 99,4 % | **0,0 %** |
| `unforcedErrors` | 99,4 % | **0,0 %** |
| `netApproaches` | 98,3 % | **0,0 %** |
| `fastestServe` | 76,6 % | **0,0 %** |
| `averageFirstServeSpeed` | 72,3 % | **0,0 %** |
| `breakPointsConverted` | 99,4 % | 96,8 % |
| `totalPointsWon` | 99,4 % | 96,8 % |

Le contrôle par surface le confirme sans le chercher : Grass 35,1 % (Wimbledon
pèse lourd dans le peu de gazon joué), Hard 12,9 % (Open d'Australie et US
Open), Clay 10,7 % (Roland-Garros), **`I.hard` 0,0 %** — la salle, où aucun
Grand Chelem ne se joue.

### Ce que ça donne sur les joueurs des cinq derniers lots

79 joueurs, dont 72 rapprochés dans l'archive, fenêtre 52 semaines :

| | ATP (36 joueurs) | WTA (36 joueurs) |
| --- | ---: | ---: |
| matchs dans la fenêtre — médiane / Q1 | 63,0 / 56,8 | 56,0 / 48,8 |
| dont stats de service | 60,5 / 54,0 | 54,0 / 47,8 |
| **dont `winners`** | **8,5 / 6,0** | **10,0 / 7,8** |
| points de service — médiane / Q1 | 4 802 / 4 370 | 3 795 / 3 368 |
| au-dessus du seuil de 400 points | **36 / 36** | **36 / 36** |

Les huit à dix matchs ne sont pas un échantillon maigre : ce sont **exactement
les matchs de Grand Chelem** du joueur. Quatre tournois par an.

### Conclusion : `Coups gagnants` et `Fautes directes` ne se construisent pas

Trois raisons qui se cumulent, et chacune suffirait :

- **Les lots de ce projet sont des tableaux de Masters 1000**, où la couverture
  est de **0,0 %**. La ligne serait vide sur la totalité des blocs qu'on analyse,
  et pleine seulement pendant les quinze jours d'un Grand Chelem ;
- **le sous-échantillon est un format, pas un tirage.** Côté ATP, ces matchs sont
  au meilleur des cinq sets — le format que `Profil` et `Marge` **écartent déjà**
  parce qu'il fait lire un joueur de trois sets comme un marathonien. Un compte
  de coups gagnants y est mécaniquement plus élevé ;
- **un joueur battu au premier tour porte un match, un finaliste sept.** La ligne
  serait la plus fournie pour ceux dont on sait déjà le plus.

C'est le **même résultat négatif que le Match Charting Project** du 17/08, sous
une forme plus nette : là-bas la couverture décroissait avec le rang, et une
mesure de médiane suffisait à trancher ; ici elle est **binaire et
structurelle** — le fournisseur ne sert ces colonnes que pour quatre tournois,
et aucun volume de collecte n'y changera rien.

**Ce qui rouvrirait la question, et rien d'autre** : un lot de Grand Chelem,
seconde semaine. Ce n'est pas le régime de ce projet.

### Ce qui, en revanche, se construit : les balles de break sauvées

Le brief demande si elles sont servies côté joueur ou seulement déductibles de
l'adversaire. **Déductibles, et c'est sans conséquence** : les deux joueurs
figurent dans la **même** fiche `matches-played`, donc

    balles de break affrontées = breakPointsConvertedOf de l'adversaire
    balles de break sauvées    = Of − Converted de l'adversaire

à **97 % de couverture**, sur tous les niveaux de tournoi, et sans un appel de
plus. Le chemin existe déjà et il est éprouvé : `return_points` et `return_won`
se dérivent exactement ainsi — « les colonnes adverses de la même réponse ».

C'est le complément naturel de `Retour`, qui rend déjà « BP converties » :
`Service` gagnerait « BP sauvées », et les deux moitiés d'une même question
seraient enfin dans le bloc. Deux colonnes sommées de plus dans
`player_serve_agg`, aucun coût de collecte.

## §4a — La porte des prix : entrouverte, sur 1,6 % du board

**La question passe avant toute donnée joueur, et c'est elle qui décide.** Deux
mesures, l'une sur la base servie, l'autre en direct.

### Ce que la base dit : jamais un seul prix

`odds` porte **32 570 lignes**, `prompt_odds` **26 041**. Lignes de props
buteurs, dans l'une comme dans l'autre : **zéro**. Sur toute l'histoire du
projet, aucun prix de buteur n'est jamais entré en base.

`market_coverage` dit pourquoi il ne s'agit pas d'un oubli de collecte — les
marchés **ont été demandés** :

| Compétition | Marché | Books interrogés | Servi | Constats |
| --- | --- | --- | ---: | ---: |
| La Liga | `player_first_goal_scorer` | betclic_fr, pinnacle, unibet_nl | **0** | 2 |
| La Liga | `player_goal_scorer_anytime` | betclic_fr, pinnacle, unibet_nl | **0** | 2 |
| MLS | `player_first_goal_scorer` | betclic_fr, pinnacle, unibet_nl | **0** | 11 |
| MLS | `player_goal_scorer_anytime` | betclic_fr, pinnacle, unibet_nl | **0** | 11 |

Treize constats, aucun prix. Les quatre autres compétitions de la liste blanche
(`PLAYER_PROPS_LEAGUES` : EPL, Ligue 1, Bundesliga, Serie A) n'ont jamais été
sondées — elles entrent tout juste en saison.

### Le sondage en direct : 18 matchs, 8 books, 6 compétitions

Sondage du 20/08/2026, trois matchs par compétition, huit bookmakers —
`betclic_fr`, `pinnacle`, `unibet_nl`, `williamhill`, `bet365`, `888sport`,
`superbet`, `10bet`. **Coût total : 18 crédits** (les réponses vides ne sont pas
facturées, donc les compétitions sans prix coûtent zéro).

| Compétition | Matchs sondés | Qui sert |
| --- | ---: | --- |
| EPL | 3 / 10 | **William Hill**, les 2 marchés |
| La Liga | 3 / 14 | **William Hill**, les 2 marchés |
| Serie A | 3 / 10 | **William Hill**, les 2 marchés |
| Ligue 1 | 3 / 9 | **aucun book** |
| Bundesliga | 3 / 9 | **aucun book** |
| MLS | 3 / 29 | **aucun book** |

Résultat parfaitement homogène — 3 matchs sur 3 dans chaque sens, jamais un cas
partiel. **Betclic ne sert ces marchés sur aucune compétition**, ce qui prolonge
exactement le constat déjà établi : un marché sur 364 matchs.

### La mesure qui tranche : 7 matchs sur 445

Un prix existe donc, sur trois compétitions. Reste à savoir ce qu'elles pèsent
dans ce qui est réellement analysé.

| | Matchs analysés | Dont EPL / La Liga / Serie A |
| --- | ---: | ---: |
| toute l'histoire (`prompt_events`) | 445 | **7 — soit 1,6 %** |
| cinq derniers lots | 163 | **4 — soit 2,5 %** |

Les lots vivent ailleurs : Cincinnati ATP 92 matchs, Cincinnati WTA 88,
Conference League 49, MLS 26, Europa League 24, qualifications de Champions
League 16, Championship 12, La Liga 2 11.

**Réserve honnête, et elle joue contre la conclusion** : ces trois championnats
ouvrent leur saison au moment de la mesure — la base ne porte qu'**1 match
d'EPL, 8 de La Liga, 0 de Serie A**. Le 1,6 % sous-estime donc le régime de
saison pleine. Borne haute raisonnable : trois championnats à une dizaine de
matchs par semaine font ~30 rencontres contre ~280 analysées sur la même période,
soit **de l'ordre de 10 %** — et seulement si les trois sont scannés en entier.

### Conclusion : la porte est entrouverte, et ce n'est pas assez

Ce qu'une section buteurs produirait, tel que mesuré :

- elle serait **vide sur 90 à 98 % des blocs**, et le gabarit paierait son mode
  d'emploi sur chaque prompt — le coût est fixe, le rendement non ;
- **chaque ligne porterait une cote de référence**, jamais un prix jouable :
  William Hill n'est pas le book principal, donc toute sélection sortirait avec
  sa mention `(ref.)` et sa consigne de relever le prix avant de miser ;
- **l'écart de ce book sur ce marché n'est pas mesuré.** Les 3,4 % d'écart moyen
  connus pour William Hill l'ont été sur les marchés principaux, via
  API-Football. Un marché de buteur porte une marge bien supérieure, et rien ne
  dit que la proximité tienne — l'affirmer serait exactement la généralisation
  déjà payée sur « le bookmaker les propose bien sur son site ».

**Recommandation : ne rien construire, et le §4b n'est pas mesuré.** Ce n'est pas
un refus de principe — le marché existe, il est servi, il est modélisable. C'est
un arbitrage de rendement, et il se rouvrira tout seul le jour où les trois
championnats pèseront vraiment dans les lots. **Ce qui le rouvrirait, et rien
d'autre** : une part d'EPL / La Liga / Serie A durablement au-dessus de 20 % du
board, ou Betclic servant ces marchés via The Odds API — les deux se constatent
sans rien coder, la première dans `prompt_events`, la seconde dans
`market_coverage`.

**Ce qui a été retiré de ce lot par cette mesure** : le §4b (recensement des
données joueur d'API-Football) et le §4c (la section dédiée). Mesurer la
couverture joueur avant de savoir s'il existe un prix aurait été l'ordre inverse
de celui que le brief impose lui-même.

## §3a — La catégorie est servie, la profondeur est bridée par notre propre pagination

**Mesure du 20/08/2026, sans un appel**, sur les 735 réponses archivées —
18 757 matchs distincts, du 17/05/2018 au 19/08/2026.

### Les trois champs que §3 demande sont servis

| Champ | Couverture | Ce qu'il porte |
| --- | ---: | --- |
| `tournament.tier` | **99,1 %** | `Grand Slam`, `WTA 1000`, `ATP Masters 1000`, `Challenger 125`… |
| `tournament.court.name` | **100,0 %** | `Hard`, `Clay`, `Grass`, `I.hard`, `Carpet` |
| `roundId` | **100,0 %** | le tour, donc le résultat atteint |

**La catégorie n'a donc pas à se déduire d'un libellé**, ce que le brief
interdisait à juste titre : elle est un champ. Trente valeurs distinctes, dont
quelques **alias historiques** à réunir — `ATP World Tour Masters 1000` (196) est
`ATP Masters 1000` (1 694), `ATP World Tour 250` (182) est `ATP 250` (1 531).
Réunir deux graphies du même niveau chez le même fournisseur est un fait de
renommage, pas une déduction ; le projet le fait déjà pour les tournois avec ses
alias séparés par `|`.

### Le rattachement manuel n'est pas la contrainte, et c'était la prémisse à vérifier

Le gabarit dit que `Palmarès` *« n'existe que si le tournoi a été rattaché à la
main au jeu de données — la source le nomme par son sponsor »*. Mesuré :

| | Compétitions tennis | Rattachées | Analysées sans rattachement |
| --- | ---: | ---: | ---: |
| base servie | 43 | **43** | **0** |

**Le rattachement est complet.** Ce n'est donc pas lui qui manque — et surtout,
il ne concerne que `tennisdata.co.uk`. `matches-played` nomme le tournoi
lui-même (`tournament.name`), donc un palmarès bâti sur cette source **n'a besoin
d'aucune table de correspondance**.

### En revanche, aucun identifiant ne porte l'identité d'un tournoi d'une année sur l'autre

C'est la réponse à « cherchez l'identifiant » pour ce cas-ci, et elle est
négative :

- **`tournamentId` est par édition.** 261 noms apparaissent sur plusieurs années,
  et **0** conserve son identifiant. « Cincinnati Open - Cincinnati » vaut
  `[15980, 20357]` en 2025 et `[16740, 21347]` en 2026 ;
- **`link` ne le porte pas non plus, et il collisionne.** 18 valeurs regroupent
  des tournois **différents** : `15649` réunit « Citi Open - Washington » et
  « UniCredit Iasi Open - Iasi », `0` réunit toutes les rencontres de Coupe
  Davis. S'en servir attribuerait à un joueur le palmarès d'un autre tournoi.

Reste le **nom servi par le fournisseur**, stable d'une année sur l'autre. Ce
n'est pas une déduction depuis un libellé — c'est une égalité sur la graphie
canonique de la source — mais le changement de sponsor reste le risque connu, et
il se traite comme ailleurs : par des alias déclarés, jamais devinés.

### La profondeur : la source en annonce cinq fois plus que ce qu'on prend

C'est le résultat qui commande le §3b.

| | Mesuré |
| --- | ---: |
| `singlesCount` annoncé — médiane | **509 matchs** |
| `singlesCount` — maximum | **1 593** |
| matchs réellement rendus — médiane et maximum | **100** |
| réponses tronquées par notre pagination | **729 / 735 = 99,2 %** |
| `(page, pageSize, limit)` observés | **(1, 100, 100)**, sans exception |

**La fenêtre 2018–2026 que l'archive montre est un artefact de notre collecte**,
pas la profondeur de la source : elle vient de ce qu'on prend les 100 matchs les
plus récents de 735 profils. À ~60 matchs par an pour un joueur actif, 509 matchs
représentent environ huit ans et demi — de quoi porter un « demi-finale 2019 ».

Le volume par année de l'archive le confirme et l'explique : 13 matchs en 2018,
35 en 2019, 25 en 2021, puis 1 592 en 2024, **9 591 en 2025**, 7 311 en 2026.
Ce n'est pas une source qui s'appauvrit en remontant, c'est une fenêtre de
100 matchs qui ne remonte loin que pour les joueurs qui jouent peu.

### Le coût, et il n'est pas l'obstacle

Le client **sait déjà paginer** — `matches_played(name, page)` — et `singlesCount`
dit quand s'arrêter **sans demander une page de plus**. `PAGE_SIZE` vaut 100
quand le plafond mesuré est **200**.

- historique complet à `pageSize=200` : **~3 pages** pour la médiane, 8 pour le
  maximum, contre 1 aujourd'hui ;
- un lot de 8 à 12 matchs de tennis porte 16 à 24 joueurs, soit **~50 à 70 appels
  de plus par lot** ;
- quota RapidAPI restant au 19/08 : **139 480 appels**, pour une consommation
  observée de 3 304 et 6 191 appels sur les deux journées mesurées.

**Le §3b est donc constructible, et rien dans la mesure ne s'y oppose.** Ce qu'il
demande est une décision de pagination — aller chercher l'historique complet d'un
joueur au lieu de sa dernière centaine de matchs — et un choix d'identité de
tournoi par le nom, avec ses alias déclarés. Il n'a pas été construit dans ce
lot : le §1 a consommé la session, et une pagination profonde change le coût de
chaque enrichissement de tennis, ce qui se décide avec sa mesure sous les yeux
plutôt qu'en fin de lot.

## §5 — Le règlement automatique : 93,3 % d'accord, et il en faut 100

**Mesure du 20/08/2026**, sur les 293 sélections tranchées de la base — 166 au
football, 127 au tennis, toutes rattachées à un match. Le brief en annonce 298 ;
la base en porte 293 au moment du relevé.

### Existe-t-il seulement une source de résultat ?

Oui, deux, et **elles dorment déjà en base** :

| Sport | Source | Ce qu'elle porte | Récupérable sur les tranchées |
| --- | --- | --- | ---: |
| football | `team_context` (`kind='season'`) | `goals`, `halftime`, `status: FT`, `at_home` | **72 / 166 = 43,4 %** |
| tennis | `api_responses` (`event/get`) | `score` set par set, `status: Ended` | **104 / 127 = 81,9 %** |
| | | **total** | **176 / 293 = 60,1 %** |

Ce sont les taux sur les **archives**, pas sur ce qu'un cron obtiendrait : les
deux endpoints se rappellent, et un règlement quotidien irait les chercher.

**Ce que `tennis-data.co.uk` ne peut pas faire, et c'est mesuré** : son fichier
s'arrête au **14/08** quand les matchs analysés vont jusqu'au **20/08**. 54 des
127 sélections tennis lui sont postérieures — 43 % du sport. Un cron passant deux
ou trois fois par jour ne réglerait rien avec cette source. `event/get`, lui, est
en direct et **déjà appelé** par l'enrichissement courant.

Réserve à connaître : sur 8 511 réponses `event/get` archivées, **6 558 sont
vides** — c'est le `SOURCE_VIDE` déjà documenté. Les 1 809 qui portent un statut
`Ended` suffisent pourtant à couvrir 81,9 % des sélections tennis.

### Le rejeu, et son taux de divergence

Règle appliquée : le **vainqueur** seul, la plus simple des trois que le brief
autorise à commencer. Un marché dont la règle n'est pas écrite part en
`non tranchable` et attend une main — c'est la consigne, et c'est ce que fait le
rejeu.

| Marché | n | Accord | Divergence | Non tranchable |
| --- | ---: | ---: | ---: | ---: |
| `h2h` | 41 | 32 | **1** | 8 |
| `Vainqueur` (sans clé) | 31 | 24 | **3** | 4 |
| `alternate_spreads` | 33 | 0 | 0 | 33 |
| `Hand. jeux` (sans clé) | 10 | 0 | 0 | 10 |
| `totals` | 7 | 0 | 0 | 7 |
| `Jeux O/U` (sans clé) | 5 | 0 | 0 | 5 |

> **60 règlements tentés, 56 d'accord, 4 divergences — 93,3 % d'accord.**

### Les quatre divergences, nommées

| Sélection | À la main | Automatique | Score lu |
| --- | --- | --- | --- |
| Collignon | perdu | **gagné** | `4-6,2-6` |
| Nuno Borges | perdu | **gagné** | `6-7,4-6` |
| Jessica Pegula | perdu | **gagné** | `7-5,6-4` |
| Maja Chwalinska | perdu | **gagné** | `"6-7,4-6"` |

**Les quatre vont dans le même sens, et c'est le plus instructif.** Trois portent
un score que le rejeu lit comme une victoire de `participant1` alors que la
sélection désignait l'autre camp : l'attribution du vainqueur repose sur un
rapprochement de **nom de famille** entre l'affiche (`Jessica Pegula`) et le
champ `participant1`, et rien ne garantit que les deux se correspondent dans le
même ordre. Le quatrième porte en plus un score **entre guillemets** dans la
charge utile, donc une seconde forme à lire.

Ce ne sont donc pas quatre cas limites du sport — pas de `0` remboursé, pas
d'abandon, pas de report. **Ce sont quatre défauts de la lecture**, et c'est
exactement ce que la validation devait attraper.

### Ce que ça décide

- **Rien ne se met en service.** Le brief pose la barre à 100 % d'accord ; le
  rejeu rend 93,3 % sur le marché le plus simple, avec les données les plus
  riches. Un règlement à 93 % appliqué à 293 sélections en corromprait une
  vingtaine, **silencieusement** — et c'est tout ce que ce projet sait produire.
- **La cause est identifiée et elle est réparable** : il faut un rapprochement
  camp par camp, sur l'ordre des participants et non sur un nom de famille isolé.
  Le projet a déjà l'outillage — `serve_stats.resolve()` refuse une identité
  ambiguë au lieu de deviner.
- **Le rejeu lui-même est le livrable de ce §**, et il doit rester : c'est lui
  qui fera la différence entre « 93,3 % » et « 100 % », et sans lui la mise en
  service se déciderait au jugé.
- **Ordre des marchés** : `Vainqueur` d'abord, une fois l'attribution réparée,
  puis O/U — dont la règle est arithmétique sur un score déjà lu. Les handicaps
  en quarts, les abandons et les reports restent manuels : leur règle n'est pas
  écrite, et un marché dont la règle n'est pas écrite ne se règle pas.

### La mesure qui a failli être fausse, et ce qu'elle rappelle

Le premier relevé de récupérabilité tennis a rendu **0 sur 127**. C'était un
**artefact de ma lecture, pas une absence de donnée** : `tennis_matches` écrit
« Mensik J. » — nom de famille en tête — quand `events` écrit « Alex
Michelsen ». Ma fonction prenait le premier mot long des deux côtés, donc
comparait « mensik » à « alex ».

Corrigé, le même relevé rend **65,8 %**. Un « 0 % » se serait lu comme une source
inexploitable et aurait fermé le tennis pour de bon — la neuvième occurrence du
motif du projet, sur la mesure censée décider du chantier.

## §2b — Le seuil par surface est structurellement hors d'atteinte, et le chiffre le dit

**Mesure sur `player_serve_agg`, 1 167 agrégats, 250 joueurs.** La prémisse du
brief — « `Jeux` ne sort que sur 14 joueurs sur 250 » — est **confirmée au
chiffre près**, et l'explication est celle déjà écrite au lot 8.

| Portée | Joueurs | Au seuil de 300 | Maximum atteint | Moyenne |
| --- | ---: | ---: | ---: | ---: |
| toutes surfaces | 250 | **14** | 322 | 172 |
| Hard | 250 | **0** | 257 | 72 |
| Clay | 245 | **0** | 222 | 29 |
| Grass | 239 | **0** | 237 | 74 |
| I.hard | 183 | **0** | 89 | 0 |

**Aucune surface n'atteint jamais 300, et ce n'est pas une question de volume :**
`collect_games` s'arrête à 300 jeux **toutes surfaces confondues**, donc un
agrégat de surface ne peut par construction pas les atteindre. Le maximum
observé, 257, est le plafond mécanique de cette règle.

### Ce qu'il faudrait, et ce que ça coûterait

Mesuré sur les mêmes agrégats : **~22 jeux par match**, **8 timelines lues** par
joueur en moyenne, et la répartition des jeux collectés par surface —
**Hard 41,9 %, Grass 41,2 %, Clay 16,6 %, I.hard 0,3 %**.

- 300 jeux sur une surface demandent **~14 matchs de cette surface** ;
- **la surface est connue avant l'appel** (`court.name`, servi à 100 % — voir
  §3a), donc la passe peut ne lire que les timelines utiles au lieu d'en scanner
  33 pour en retenir 14 ;
- au coût par timeline que le brief donne (~4 appels après le filtre d'âge et le
  retrait de J+1), cela fait **~56 appels par joueur et par surface**, contre
  ~32 aujourd'hui — soit **~1 350 appels pour un lot de 24 joueurs**, sur la
  seule surface du tournoi en cours.

Rapporté au quota RapidAPI restant — **139 480 appels**, pour 3 304 et 6 191
consommés sur les deux journées mesurées — c'est finançable, mais ce n'est pas
marginal : cela **double à peu près** le coût d'un enrichissement de tennis.

**Le seuil de 300 n'est pas touché**, comme le brief l'exige : ce qui change
serait la **portée** de la collecte, pas la barre. Le chantier n'est pas livré
ici — il change le coût de chaque enrichissement de tennis, et cela se décide
avec la mesure sous les yeux plutôt qu'en fin de lot.

## §6 — Ce que la mesure contredit dans ce brief

| Affirmé | Mesuré |
| --- | --- |
| table de mises : unité 1 %, plafond de session 5 % | **16 sessions sur 16** l'atteignent ou le dépassent. La mise réelle vaut 0,167 à 1,000 %, médiane 0,264 % : le plafond ne plafonne pas, **il dimensionne** |
| « lots de 8 à 12 matchs » (régime actuel) | **4 à 10 blocs**, moyenne 5,8 à 8,4 sur les quatre dernières sessions |
| « soit environ 4 à 5 sélections de section C » | **2,50 à 3,80** par lot |
| « si le P90 du régime actuel est autour de 10 à 12 sélections » | **20,4** par journée. La direction était juste, l'ampleur non |
| le régime actuel serait plus resserré que l'ancien | **médianes identiques : 19,0 contre 19,0.** Les lots ont rétréci, la journée n'a pas bougé — le découpage a été absorbé par le nombre de prompts |
| plafond « par session » | la date de session **n'est pas** celle des sélections : la session 18 est datée du 19/08 et a produit ses cinq prompts le 20/08. Grouper dessus compte les sélections d'aujourd'hui dans le budget d'avant-hier |
| C-bis à 0,25 unité | **contredit par l'utilisateur lui-même**, et à raison : miser sur une population produite sans fait daté paie une information qu'on obtient sans payer |
| §2 : « coups gagnants et fautes directes » à ajouter | servis **au Grand Chelem seul** — 99,4 % là, **0,0 % partout ailleurs**, sans une exception sur 9 010 matchs. Les lots de ce projet sont des Masters 1000 |
| §2 : « `Jeux` ne sort que sur 14 joueurs sur 250 » | **confirmé au chiffre près**, et la cause est structurelle : 0 sur 250 par surface, maximum mécanique 257 |
| §3 : « la source le nomme par son sponsor », d'où le rattachement manuel | **43 compétitions tennis sur 43 sont rattachées**, 0 analysée sans. Et `matches-played` nomme le tournoi lui-même : un palmarès bâti dessus n'a besoin d'aucune table |
| §3 : « faut-il déduire la catégorie d'un libellé ? » | **elle est servie**, `tournament.tier` à 99,1 % — surface à 100 %, tour à 100 % |
| §3 : « la description commerciale annonce 1930 » | invérifiable d'ici, et **sans objet** : la source annonce **509 matchs médians** par joueur et nous n'en prenons **100**. 99,2 % des profils sont tronqués par notre propre pagination |
| §4 : « ils ressortent en non servis » | **zéro ligne de props en base**, jamais, sur 58 611 cotes. Mais un prix **existe** — William Hill, sur EPL, La Liga et Serie A, les deux marchés, 3 matchs sur 3 |
| §4 : la porte serait fermée ou ouverte | **entrouverte** : le prix existe sur trois compétitions qui pèsent **7 matchs sur 445** analysés, jamais chez le book principal |
| §5 : « 298 sélections tranchées » | **293** dans la base au moment du relevé |
| §5 : un cron 2-3 fois par jour remplirait l'historique | pas avec `tennis-data`, qui s'arrête au 14/08 quand les matchs vont au 20/08 — **43 % du tennis lui est postérieur**. `event/get` le peut, il est en direct et déjà appelé |
| §5 : la validation dirait si le règlement est sûr | **93,3 % d'accord** sur le marché le plus simple. Les 4 divergences ne sont pas des cas limites du sport, ce sont **des défauts de lecture** — c'est exactement ce que la validation devait attraper |
| « le lot 14 a montré ce que vaut un avertissement qu'on peut valider » | **confirmé**, et appliqué d'avance : le plafond de mise s'applique à l'écriture, il ne s'affiche pas |

### Ma propre mesure, reprise en cours de route

**Le relevé de récupérabilité tennis a d'abord rendu 0 sur 127.** C'était un
artefact de ma lecture : `tennis_matches` écrit « Mensik J. », `events` écrit
« Alex Michelsen », et ma fonction prenait le premier mot long des deux côtés.
Corrigé, le même relevé rend **65,8 %**.

Un « 0 % » se serait lu comme une source inexploitable et aurait fermé le tennis
pour de bon. C'est le motif du projet appliqué à l'outil de mesure lui-même — et
la raison pour laquelle une mesure surprenante se re-vérifie avant d'être écrite.

### La leçon de méthode du lot

**Trois des cinq chantiers ont été tranchés par une mesure qui n'a coûté aucun
appel.** Le recensement des clés de tennis, la profondeur d'historique et la
récupérabilité des résultats dorment tous dans `api_responses`, archivé depuis la
migration 058 ; le sondage des prix de buteurs a coûté **18 crédits** sur 14 466.

Et le seul chantier construit — la répartition de mise — est celui dont la
mesure a **renversé la spécification avant la première ligne de code**. Les
quatre nombres du brief n'étaient pas mutuellement cohérents, et les coder tels
quels aurait produit un écran affichant « 1 unité » pendant que le code en
mettait un quart : le défaut caractéristique du projet, pour la première fois
libellé en euros.

## §1c — Le journal, et les trois fonctions qui n'avaient pas de lecteur

**Trouvé en relisant le module livré, pas par un test.** Trois fonctions
publiques de `stakes` n'avaient aucun appelant : `bankroll_of`, `journal` et —
en apparence — `engaged_units`. La troisième était un faux positif, appelée
depuis `brief()` dans le même module ; les deux premières ne l'étaient pas.

Conséquence concrète : **`bankroll_journee` était écrite à chaque import et
jamais relue.** C'est exactement la faute de `/players/squads`, collecté des mois
sans lecteur et retiré par la migration 022 — et son propre commentaire
annonçait déjà sa sortie.

Deux issues possibles, et la règle du projet tranche : *ce qui n'a pas de lecteur
se retire, ou reçoit sa surface.* Ici la surface manquait, et le §1c la
demandait explicitement — « un état de bankroll par session, et son évolution ».

`day_state()` la fournit, sur la feuille de session :

> **Journée 2026-08-20** — 4 mise(s) · 4 unité(s) engagée(s) sur 20 ·
> 2.00 sur 200.00 (1,00 %) · 1.50 réellement posé.

- **Un état, jamais une projection.** Ni objectif, ni solde attendu, ni courbe de
  tendance : une projection supposerait une espérance de gain, c'est-à-dire
  l'interdit qui gouverne tout ce module. Un test vérifie qu'aucun des mots
  `attendu`, `objectif`, `espérance`, `gain`, `tendance`, `prévu` n'entre dans la
  ligne rendue.
- **Rien quand rien n'est engagé**, même règle que partout.
- C'est aussi la seule surface qui dise ce que **les autres rendus du même jour**
  ont déjà engagé — le plafond portant sur la journée et non sur la session.

**Et la porte se ferme par un test plutôt que par une relecture** :
`test_aucune_fonction_du_module_de_mise_n_est_sans_lecteur` lit la source, liste
les **13** fonctions publiques du module et vérifie que chacune est appelée
quelque part. Une fonction ajoutée demain sans surface fera tomber la suite —
c'est la même forme que le registre des chemins d'écriture, et pour la même
raison : une règle de contribution ne se déclenche pas, un test si.

### Deux limites du §1 à connaître avant de s'en servir

- **Le montant réellement posé ne se saisit que sur une sélection qui a reçu une
  proposition.** Le champ vit sur la ligne de `mises`, donc une sélection
  importée sans ligne `mises:` n'en a pas. C'est cohérent — le journal compare un
  proposé à un posé, et sans proposé il n'y a rien à comparer — mais cela veut
  dire qu'une mise décidée entièrement à la main n'a pas de surface aujourd'hui.
- **La bankroll n'a pas d'écran de réglage, et c'est voulu** : le montant se tape
  en tête de prompt, au collage, et l'application le relit sur la ligne rendue.
  Un champ de réglage en ferait une seconde source, qui divergerait de la
  première au premier oubli — le piège déjà payé sur la liste des marchés
  demandés.

---

# DIAGNOSTIC — lot 16 : finir les trois chantiers ouverts

## §0 — L'état servi après redémarrage, et le coût réel de la section G

**Migration 065 appliquée le 20/08/2026 à 19h49**, sur sauvegarde fraîche prise
juste avant (`myassistantbet-20260820-174946.db`, 279 Mo). Relevé depuis le
serveur, pas depuis une copie :

| | Avant | Après |
| --- | ---: | ---: |
| `schema_version` (`/health`) | 64 | **65** |
| tables | 38 | **40** |
| `mises` | absente | **présente** |
| `bankroll_journee` | absente | **présente** |

Journal du démarrage : `Migration appliquee : 065_journal_des_mises.sql`, une
seule, sans erreur. `status: ok`, `journal_mode: wal`.

**Les réglages résolvent tous à leur défaut** — aucune ligne dans `preferences` :
`suivi_coupons` **vrai**, `mise_unite_bp` 25, `mise_plafond_bp` 500,
`mise_combine_pct` 50. Le rallumage du suivi de l'argent est donc effectif sur
l'instance servie sans qu'aucune saisie soit nécessaire.

### Le coût de la section G : 592 tokens, et c'est un coût de **cadre**

Mesuré sur trois sessions réelles, en rendant chacune deux fois :

| Session | Blocs | Avec section G | Sans | Écart |
| ---: | ---: | ---: | ---: | ---: |
| 18 | 3 | 16 368 | 15 776 | **592** |
| 17 | 0 | 6 887 | 6 296 | **591** |
| 16 | 0 | 6 887 | 6 296 | **591** |

**Le chiffre du lot 15 est confirmé, et la mesure ajoute ce qu'il ne disait
pas : le coût ne dépend pas de la taille du lot.** 591 sur un lot vide, 592 sur
trois blocs — c'est du cadre, pas du bloc.

`split_cost` le confirme et dit où il se range :

| | Cadre fixe | Par bloc |
| --- | ---: | ---: |
| suivi ouvert | **13 453** | 972 |
| suivi éteint | **12 861** | 972 |

L'écart est **entièrement dans le cadre**, le coût par bloc ne bouge pas d'un
token. La série du coût du gabarit l'enregistrera donc toute seule au prochain
prompt réel : `save_prompt` écrit `fixed_tokens` depuis `split_cost`, et rien
n'est à ajouter. Dernier point de la série avant ce lot — 20/08, 5 prompts,
cadre 13 312 ; les prompts suivants porteront ~13 900.

**Ce que l'extinction rend** : 592 tokens sur un prompt qui en pèse 16 800 à
20 800 pour un lot de 6 à 10 blocs, soit **2,8 % à 3,5 %**. C'est peu, et c'est
la raison pour laquelle la porte se justifie quand même : le cadre commun est ce
qui se paie à **chaque** prompt d'une session, et une session en génère quatre à
cinq.

## §1 — Le règlement automatique : 93,3 % était un artefact, et la cause est une clé

### §1a — La réparation, et ce qu'elle a révélé

**Le taux de 93,3 % du lot 15 ne mesurait pas le règlement, il mesurait mon
index.** Les résultats y étaient rangés par **paire de noms de famille**, sans
date : deux rencontres du même couple s'écrasaient donc l'une l'autre, et la
dernière lue gagnait. Les quatre divergences rapportées n'étaient pas quatre cas
limites du sport, c'était **quatre fois le même défaut d'appariement**.

Mesure du 20/08/2026, sur **800 matchs** recoupables entre `event/get` et
`tennis-data.co.uk` — deux sources indépendantes, la seconde nommant
explicitement son vainqueur :

| Clé de rapprochement | Matchs | Accord sur le vainqueur |
| --- | ---: | ---: |
| paire de noms seule | 710 | **94,1 %** |
| **paire de noms + jour** | 800 | **99,75 %** |

Et les **deux** désaccords restants sur 800 n'en sont pas : `7-5,3-6,2-1` et
`6-1,1-0` portent un set **inachevé**, donc un abandon. Compter les sets y
désigne celui qui menait quand le jeu s'est arrêté — le perdant.

**Deux conséquences, et la seconde valait le détour :**

- la convention du champ `score` est **établie et non supposée** : il s'écrit du
  point de vue de `participant1`, set par set. Ce n'est pas une lecture de
  documentation, c'est un recoupement contre une source qui nomme son vainqueur ;
- **l'abandon se détecte sur le score seul**, sans champ supplémentaire : un set
  qui n'atteint pas 6 avec deux jeux d'écart, ou 7, n'est pas un set — c'est
  l'instant où le jeu s'est arrêté.

### Le taux réparé, sur les 293 sélections tranchées

| Marché | n | Accord | Divergence | Hors règle | Sans résultat | Taux |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `h2h` | 80 | 39 | **0** | 0 | 41 | **100 %** |
| `(sans clé) Vainqueur` | 31 | 26 | **0** | 0 | 5 | **100 %** |
| `(sans clé) 1N2` | 22 | 14 | **0** | 0 | 8 | **100 %** |
| `totals` | 32 | 5 | **0** | 5 | 22 | **100 %** |
| `(sans clé) O/U` | 10 | 6 | **0** | 0 | 4 | **100 %** |
| `(sans clé) O/U 2.5` | 7 | 4 | **0** | 0 | 3 | **100 %** |
| `(sans clé) O/U 3.5` | 1 | 1 | **0** | 0 | 0 | **100 %** |
| `alternate_spreads` | 42 | — | — | 31 | 11 | hors règle |
| `(sans clé) Handicap` | 16 | — | — | 15 | 1 | hors règle |
| `btts`, `double_chance`, `team_totals`, `correct_score`, `halftime_fulltime`, `to_qualify`, `Hand. jeux`, `Jeux O/U` | 42 | — | — | 27 | 15 | hors règle |

Agrégé par **famille de règle** — c'est la règle qui se met en service, pas le
libellé :

| Famille | Règlements | Accord | Divergence | Taux |
| --- | ---: | ---: | ---: | ---: |
| `issue` (1N2, Vainqueur) | 79 | 79 | **0** | **100,00 %** |
| `total` (O/U football) | 16 | 16 | **0** | **100,00 %** |

> **95 règlements tentés, 95 d'accord, zéro divergence.**

**Aucune divergence ne reste à nommer** — c'est la réponse à la demande du §1a,
et elle est plus courte que prévu.

### Un cas exclu à tort, et le correctif

Le premier rejeu laissait `Los Angeles FC` hors règle sur
`Los Angeles FC – San Diego FC` : le jeton `fc` touchait les **deux** camps, donc
la règle du doute s'appliquait. Elle avait tort — **un mot présent des deux côtés
ne porte aucune information sur le camp visé**, donc il ne doit pas en fabriquer.
Les jetons communs sont retirés avant comparaison ; le doute réel (`FC` seul)
reste sans camp. Gain : un règlement, et la règle n'est pas affaiblie.

### §1b — Ce qui est mis en service, et ce qui ne l'est pas

**Deux familles, celles à 100 %.** `ENABLED = ("issue", "total")`. Tout le reste
reste manuel — handicaps, scores exacts, mi-temps/fin de match, qualification,
totaux d'équipe, deux-équipes-marquent, double chance — **et les totaux au
tennis** : `event/get` ne sert pas le compte de jeux dans son score agrégé, donc
la règle ne s'écrit pas.

Les quatre gardes demandées, et où chacune vit :

| Garde | Où |
| --- | --- |
| le cron propose, il n'écrit pas d'autorité | `etat = propose`, et un test lit la source de la tâche planifiée pour vérifier qu'elle n'appelle ni `apply`, ni `set_result`, ni `UPDATE picks` |
| cas non couvert → non tranché | un marché hors règle ne produit **aucune ligne** : il n'est pas rangé « inconnu », il est absent |
| une divergence alerte, n'écrase jamais | `etat = divergent`, `picks.result` intact, badge sur la feuille de session avec le score qui la fonde — et `apply()` **refuse** une divergence |
| journaliser source et horodatage | `reglements.source` et `observed_at` sur chaque ligne, `agreement()` rend le taux par famille |

**La promotion est un geste humain et le calcul ne la défait pas** : rejouer la
passe ne ramène pas une ligne `applique` à `propose`.

### Ce que le règlement ne peut pas régler, et ce n'est pas une règle qui manque

Sur les 293 sélections : **95 réglées, 136 hors règle, 94 sans résultat, 2
inachevées.** Les 94 ne manquent pas d'une règle — elles manquent d'un
**résultat** : la passe relit ce que l'enrichissement a déjà archivé, et
169 événements sur 293 en portaient un au 20/08. Les autres arriveront à mesure
que l'enrichissement repasse sur leurs équipes.

C'est aussi pourquoi la tâche passe **trois fois par jour** et ne coûte rien :
aucun appel réseau, uniquement une relecture.

## §2a — La pagination coûte trois fois rien, et l'historique remonte à 2009

**Sondage en direct du 20/08/2026, six joueurs du dernier lot, 21 appels.**

| Joueur | Pages | Matchs | Historique |
| --- | ---: | ---: | --- |
| Amanda Anisimova | 2 | 390 | 2015-09-07 → 2026-08-19 |
| Jessica Pegula | 5 | 824 | **2009-04-04** → 2026-08-19 |
| Tommy Paul | 4 | 788 | 2013-01-07 → 2026-08-19 |
| Flavio Cobolli | 3 | 462 | 2017-12-11 → 2026-08-19 |
| Iga Swiatek | 3 | 589 | 2016-05-29 → 2026-08-19 |
| Elena Rybakina | 4 | 644 | 2014-12-08 → 2026-08-20 |

`pageSize=200` est **honoré** — la page pleine rend bien 190 à 200 lignes — et
`singlesCount` dit quand s'arrêter sans demander une page de plus.

| | Aujourd'hui | Pagination complète (`pageSize=200`) |
| --- | ---: | ---: |
| appels par joueur | 1 | **médiane 3, max 8** |
| lot de 24 joueurs de tennis | 24 | **~84, soit +60** |
| quota RapidAPI restant | 139 480 | inchangé à l'échelle |

**Le coût n'est pas l'obstacle**, et la prémisse du brief — « à 509 matchs
médians, c'est un autre ordre » — est mesurée à la baisse : c'est +2 appels par
joueur, pas un ordre de grandeur.

### Ce que la profondeur change au `H2H`

Mesuré sur trois affiches réelles du dernier lot, en comparant l'historique
complet à la fenêtre de trois saisons de `tennis-data.co.uk` :

| Affiche | H2H complet | Dans 3 saisons | Gain |
| --- | ---: | ---: | ---: |
| Anisimova – Pegula | 5 | 4 | **+1** |
| Paul – Cobolli | 1 | 1 | 0 |
| Swiatek – Rybakina | **13** | 9 | **+4** |

Le gain n'est pas uniforme : nul sur une paire jeune, **+44 %** sur une paire
installée. C'est cohérent avec ce que la profondeur apporte — elle ne crée pas
de rencontres, elle retrouve celles d'avant 2024.

### Ce que la mesure a trouvé au passage, et qui décide de la forme du §2b

| Champ | Couverture sur 1 767 matchs d'historique profond |
| --- | ---: |
| `roundId` | **100,0 %** |
| `tournament.tier` | 85,0 % |
| `draw` | **31,2 %** |

**`roundId` est servi partout**, et son ordre se lit sans le deviner : en
comptant les matchs par `(tournoi, roundId)` sur l'archive entière, le nombre
**maximal** de matchs d'un tour dans une édition donne sa profondeur.

| `roundId` | Matchs max dans une édition | Ce que c'est |
| ---: | ---: | --- |
| 12 | **1** | finale |
| 10 | 2 | demi-finale |
| 9 | 4 | quart de finale |
| 7 | 8 | huitième |
| 6 | 16 | seizième |
| 5 | 32 | trente-deuxième |
| 4 | 64 | premier tour d'un tableau de 128 |
| 1 – 3 | 33, 23, 16 | qualifications |

Ce n'est **pas** une déduction depuis un libellé : c'est un comptage sur la
structure d'un tableau à élimination directe, la même arithmétique que
`tennis_round`. `draw`, lui, ne porte pas la taille du tableau — ses valeurs
vont de 1 à 64 sans structure — et il est absent de 69 % des lignes : il ne sert
à rien ici.

**Le `tier` tombe à 85 % sur l'historique profond**, contre 99,1 % mesuré au
lot 15 sur la fenêtre de 52 semaines. La différence est le passé : les éditions
anciennes et les petits tournois n'en portent pas. Un palmarès par catégorie doit
donc **ignorer les éditions sans catégorie plutôt que les ranger ailleurs**, et
son dénominateur ne compte que les éditions catégorisées.

## §3 — Le seuil de 300 jeux est inatteignable par surface, et ce n'est pas le budget

### Le coût par timeline, re-mesuré

Le brief demande si le chiffre de ~1 350 appels par lot précède le retrait de
`J+1`. **Il le suit** — `DAY_SHIFTS = (0, -1)` depuis le 18/08 — mais il repose
sur une estimation de 4 appels par rencontre que la mesure corrige :

> **2 936 rencontres tentées, 2,89 appels chacune en moyenne** — et **52 %
> n'en coûtent qu'un seul**.

Le coût par lot tombe donc de ~1 350 à **~970**. Ce n'est pas ce qui bloque.

### Ce qui bloque : la couverture de la timeline, et l'arithmétique

| | Mesuré |
| --- | ---: |
| réponses `event/get` archivées | 8 511 |
| **portant une timeline exploitable** | **1 762 — 20,7 %** |
| jeux par timeline (servis + retournés) | 24,6 |
| timelines nécessaires pour 300 jeux | **12,2** |
| **rencontres à tenter pour les obtenir** | **59** |

Et un joueur ne dispute pas 59 matchs sur une même surface en un an. Sur les
250 joueurs profilés, fenêtre de 52 semaines :

| Surface | Joueurs | Matchs médians | Timelines espérées | Jeux espérés | Atteindraient 300 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hard | 250 | 27 | 5,6 | **137** | **5 / 250** |
| Clay | 245 | 14 | 2,9 | **71** | **5 / 245** |
| Grass | 239 | 6 | 1,2 | **31** | **0 / 239** |
| **toutes surfaces** | 250 | **59** | 12,2 | **300** | **126 / 250** |

**Même en tentant toutes les rencontres d'une surface, le joueur médian
plafonne à 137 jeux sur dur, 71 sur terre, 31 sur gazon.** Le seuil de 300 n'est
pas hors d'atteinte faute de budget : il l'est parce que la matière n'existe pas.

**Le repli toutes surfaces est donc conservé**, et il porte déjà sa mention
explicite — *« (toutes surfaces, arrêtées au JJ/MM — le seuil de jeux ne
s'atteint pas par surface) »*. Aucun code ne change : la ligne dit déjà
exactement ce que la mesure vient d'établir.

### La prémisse du lot 15, corrigée

Le lot 15 attribuait le blocage à `collect_games`, qui s'arrête à 300 jeux
**toutes surfaces confondues** — donc à un choix d'implémentation, réparable.
**C'est faux.** Le blocage est arithmétique : à 20,7 % de couverture, il faudrait
59 rencontres d'une surface, et le calendrier n'en offre que 27 au mieux. Lever
la limite d'implémentation ne changerait rien.

### Ce que la mesure désigne à la place, et qui n'était pas demandé

La même arithmétique dit que **126 joueurs sur 250 devraient franchir 300 jeux
toutes surfaces**, alors que **14 seulement** y parviennent — parce que la passe
lit 7 timelines par joueur là où 12,2 seraient nécessaires, et s'arrête avant.

C'est un facteur **9** sur la seule portée où la ligne fonctionne, pour ~72
appels de plus par joueur — soit ~1 730 par lot de 24. **Ce n'est pas construit
ici** : le §3 demandait la passe par surface, la mesure la ferme, et ouvrir un
chantier voisin en fin de lot serait prendre une décision de coût sans qu'elle
ait été posée.

## §4 — Deux dettes de forme

### §4a — Un zéro sur un appariement de noms se vérifie avant d'être rapporté

Écrite dans `CLAUDE.md`, avec les trois occurrences qui la fondent : Fernandez au
lot 5 (un **doublon** portait les 452 matchs), Andreescu au lot 9 (la source
écrit « Bianca Vanessa Andreescu »), et la récupérabilité tennis au lot 15 —
**0 sur 127**, corrigé à **65,8 %**, parce que `tennis_matches` écrit
« Mensik J. » là où `events` écrit « Alex Michelsen ».

Deux points que la rédaction ajoute, et que la demande n'avait pas :

- **le zéro ne se rapporte qu'après**, et il se rapporte avec le repli tenté.
  « 0 sur 127 » ne dit rien ; « 0 sur 127, replis casse, accents et ordre des
  noms épuisés » est un résultat ;
- **chaque source a sa fonction de nom, et elles ne se partagent pas.** Le nom de
  famille est le **dernier** mot chez `events`, le **premier** chez
  `tennis_matches`. Une fonction unique compare « alex » à « mensik ».

Le corollaire s'est vérifié dans ce lot même : le taux de 94,1 % contre 99,75 %
du §1 vient d'une clé qui omettait la **date**. Un taux qui surprend se
re-vérifie sur sa clé avant d'être écrit.

### §4b — L'échéance de l'unité, rendue visible

L'unité de 0,25 % a été mesurée sur **quatre journées d'analyse**, quand un 90e
centile défendable en demande une dizaine. Elle porte donc désormais, **à côté du
champ et non noyé dans sa note** :

> **provisoire** — mesuré sur 4 journées d'analyse (17 – 20/08/2026) — **à
> re-mesurer le 2026-09-20**.

Et une entrée au journal des mesures, portée par la **migration 067** plutôt que
par une insertion à la main : une donnée seedée se rejoue à l'identique sur une
installation neuve, une insertion manuelle n'existe que sur une machine.

**Le plafond de 5 % n'est pas marqué provisoire, et c'est délibéré** : c'est un
arbitrage de l'utilisateur, pas une grandeur observée. Seule l'unité dépend du
volume, donc seule l'unité a une échéance. `Threshold.provisional` porte la
distinction, et un test la vérifie sur les deux.

## §2b — Le palmarès par catégorie, et la moitié qui ne peut pas être rendue

### Ce qui est livré

`Palmares` porte désormais le **meilleur résultat par catégorie de tournoi**,
avec son dénominateur et la surface de ce résultat, sur l'historique **entier**
d'un joueur — 2009 pour Pegula, 2013 pour Paul.

Rendu réel sur les joueurs du dernier lot :

| Avant (3 saisons, `tennis-data`) | Après (historique entier) |
| --- | --- |
| `Palmares : Iga Swiatek 3V-1D` | `Palmares : Iga Swiatek WTA 1000 vainqueur 2026 (43 éditions, dur)` |
| — | `Elena Rybakina WTA 1000 finaliste 2026 (45 éditions, dur)` |
| — | `Iga Swiatek Grand Slam vainqueur 2025 (30 éditions, gazon)` |

**Et l'angle demandé, sur une affiche réelle du lot** — Tirante contre Fils, ATP
Masters 1000 :

> `Thiago Agustin Tirante ATP Masters 1000 1/8 2026 (7 éditions, dur)`
> `Arthur Fils ATP Masters 1000 1/2 2026 (25 éditions, dur)`

Sept éditions contre vingt-cinq : c'est exactement ce que « un joueur en finale
de Masters 1000 qui n'y est jamais allé n'aborde pas le match comme un habitué »
demandait de rendre visible, et le seul bilan sur trois saisons ne le disait pas.

**La surface est celle du meilleur résultat, pas celle du lot** : une catégorie
s'étale sur plusieurs surfaces, et en rendre une seule pour l'ensemble serait
faux. Le `gazon` de Swiatek en Grand Chelem dit quelque chose qu'un « dur »
majoritaire aurait effacé.

### Ce qui n'est pas rendu, et pourquoi c'est un refus et non un oubli

**Le meilleur résultat *dans ce tournoi-ci* n'est pas rendu par la source
profonde**, parce que les deux sources ne nomment pas les tournois pareil :

| Source | Nom de Cincinnati |
| --- | --- |
| `tennis-data.co.uk` | `Western & Southern Financial Group Women's Open` |
| `matches-played` | `Cincinnati Open - Cincinnati` |

`TENNISDATA_TOURNAMENTS` rattache les 43 compétitions à la **première** graphie.
Elle ne dit rien de la seconde, et un rapprochement automatique par libellé est
précisément ce que ce projet a essayé puis rejeté.

Rendu tel quel, le fragment annonçait **« ici jamais joué »** pour Swiatek,
Rybakina, Anisimova et Pegula à Cincinnati — quatre joueuses qui y ont toutes
joué. **Une affirmation fausse est pire qu'une ligne absente**, donc la moitié
« ici » n'est pas produite par cette source : `Bilan ici` garde sa ligne, servie
par `tennis-data`, qui est rattachée.

**Ce que ça corrige dans mon propre §2a** : j'y ai écrit qu'« un palmarès bâti
sur cette source n'a besoin d'aucune table de correspondance ». C'est vrai pour
la **catégorie** — servie par la source — et **faux pour le tournoi**. Ce qui
rouvrira la moitié manquante est une seconde table de rattachement, vérifiée à
la main comme la première, et rien d'autre.

### La catégorie ne se déduit pas non plus, elle se lit sur la taxonomie saisie

`TIER_BY_CATEGORY` traduit la taxonomie du projet vers celle du fournisseur —
six entrées, vérifiées à la main :

| Niveau saisi | ATP | WTA |
| --- | --- | --- |
| `grand_slam` | `Grand Slam` | `Grand Slam` |
| `masters_1000` | `ATP Masters 1000` | `WTA 1000` |
| `level_500` | `ATP 500` | `WTA 500` |

Les 43 compétitions de tennis portent toutes un niveau, saisi à la main. **Rien
n'est déduit d'un libellé**, ni côté projet ni côté fournisseur.

### Le zéro qui a failli être rapporté

La première collecte a rendu **0 édition sur 589 matchs**, pour les six joueurs.
Un zéro parfaitement crédible.

La cause : `matches-played` nomme le champ **`result`** et sépare les sets par
des **espaces**, avec le détail du jeu décisif — `3-6 7-6(5) 6-0` — quand
`event/get` nomme le champ `score` et sépare par des **virgules**. Le lecteur
découpait sur la virgule : il lisait **un seul set** par match, jamais décisif,
donc aucune édition.

C'est la règle du §4a appliquée le jour même où elle est écrite, et sur une
mesure de ce lot. Corrigé — le lecteur cherche les sets où qu'ils soient au lieu
de découper — les six joueurs rendent **117 à 246 éditions**. Le règlement
automatique du §1, qui partage ce lecteur, reste à **100 %**.

## §5 — Ce que la mesure contredit dans ce brief

| Affirmé | Mesuré |
| --- | --- |
| « les 4 divergences sont des défauts de lecture » | **confirmé, et c'était la clé** : elles venaient toutes d'un index sans date. Le taux réparé est **100,00 %**, pas « réparable jusqu'à 100 % » |
| « re-mesure sur les 298 sélections » | **293** dans la base. 95 réglées, 136 hors règle, 94 sans résultat, 2 abandons |
| « toute divergence restante se nomme » | **il n'en reste aucune.** La demande était juste, la liste est vide |
| « les cas que je redoutais ne sont pas en cause » | **confirmé pour trois d'entre eux**, et l'abandon au tennis, lui, l'était : deux des 800 matchs recoupés ressortaient à l'envers, et ils portaient un set inachevé |
| §2 : « les 43 tournois sur 43 sont rattachés » | **vrai, et sans effet pour ce chantier** : ils le sont pour `tennis-data`, qui nomme Cincinnati « Western & Southern Financial Group Women's Open ». `matches-played` l'appelle « Cincinnati Open - Cincinnati », et rien ne les rapproche |
| §2a : « le lot 5 a mesuré ~2 appels par joueur ; à 509 matchs, c'est un autre ordre » | **ce n'est pas un autre ordre** : `pageSize=200` donne **médiane 3 pages, max 8**, soit +2 appels par joueur, ~60 par lot |
| §2 : la catégorie est le point dur | **elle est servie**, et c'est le **nom du tournoi** qui manque. L'inverse de ce que le brief et mon propre §2a annonçaient |
| §3 : « coût chiffré à ~1 350 appels par lot » | **~970** : le coût réel par rencontre est de **2,89 appels**, pas 4, et 52 % n'en coûtent qu'un |
| §3 : « le filtre d'âge… est-il déjà pris en compte ? » | **oui** — `DAY_SHIFTS = (0, -1)` depuis le 18/08. Mais le chiffre reposait sur une estimation de 4 appels que la mesure corrige |
| §3 : le blocage serait `collect_games` qui s'arrête à 300 toutes surfaces | **faux, et c'est le renversement du lot** : le blocage est arithmétique. À 20,7 % de couverture il faut **59 rencontres d'une surface**, et le joueur médian en dispute 27 sur dur, 14 sur terre, 6 sur gazon. Lever la limite d'implémentation ne changerait rien |
| §0 : « le lot 15 annonce 592 tokens fixes » | **confirmé, et la mesure ajoute que c'est indépendant du lot** : 591 sur un lot vide, 592 sur trois blocs — c'est du cadre, pas du bloc |

### Deux zéros dans ce lot, et la règle écrite le matin même

Le §4a demandait d'écrire qu'un zéro sur un rapprochement de joueurs est un
défaut d'appariement jusqu'à preuve du contraire. **Elle a servi deux fois le
jour de sa rédaction**, sur des mesures de ce lot :

- **0 édition de palmarès sur 589 matchs**, pour six joueurs. Cause : le champ
  s'appelle `result` et non `score`, et sépare les sets par des **espaces** avec
  le détail du jeu décisif. Corrigé : **117 à 246 éditions** ;
- **« ici jamais joué » pour quatre joueuses de Cincinnati** qui y ont toutes
  joué. Cause : deux sources qui nomment le tournoi différemment. Non
  corrigeable sans une seconde table de rattachement — donc **la moitié « ici »
  n'est pas rendue**, plutôt que rendue fausse.

Le second est le plus instructif : la vérification n'a pas produit un correctif,
elle a produit un **refus de rendre**. C'est le même résultat que si le zéro
avait été vrai, mais pour une raison qu'on connaît — et qui dit ce qui rouvrira
la question.

### La leçon de méthode du lot

**Les trois chantiers étaient annoncés comme « mesure faite, blocage nommé », et
les trois blocages étaient mal nommés.**

| Chantier | Blocage annoncé | Blocage réel |
| --- | --- | --- |
| règlement | des cas limites du sport à traiter | une **clé d'index** sans date |
| palmarès | la profondeur de pagination | la profondeur, **puis** un nom de tournoi que rien ne rapproche |
| `Jeux` | `collect_games` s'arrête trop tôt | **le calendrier** : la matière n'existe pas |

Aucun des trois n'était faux par négligence : ce sont trois diagnostics
plausibles, chacun tiré d'une mesure réelle du lot précédent. Ce qui les a
corrigés est d'avoir **re-mesuré la cause** au lieu de partir du correctif — et
dans deux cas sur trois, le correctif prévu n'aurait rien réparé.

---

# DIAGNOSTIC — lot 17 : le collage complet était refusé par l'application elle-même

## §1a — La cause, reproduite avant d'être cherchée

**Le collage complet ne « provoque pas parfois des erreurs » : il est refusé,
toujours, depuis le 19/08 à 18h11.** Aucune erreur HTTP, aucune exception,
aucun rejet — le formulaire d'import ne s'affiche simplement pas, et le message
qui le remplace envoie recoller la seule section C.

### La trace, avant le code

`imports_raw` porte 35 collages. Passés au lecteur réel :

| id | session | car. | sélections lues | blocs `conf` | `ignored` | **picks écrits** |
| ---: | ---: | ---: | ---: | ---: | --- | ---: |
| 14 | 17 | 16 559 | 5 | 5 | **oui** | 2 |
| 16 | 17 | 17 780 | 5 | 5 | **oui** | 1 |
| 18 | 17 | 21 559 | 7 | 5 | **oui** | 2 |
| **20** | 17 | **25 128** | **7** | **7** | **oui** | **0** |
| **25** | 18 | **26 567** | **6** | **6** | **oui** | **0** |
| les 30 autres | 15–18 | 567 – 2 119 | 1 – 5 | 0 | non | 1 – 5 |

**Les cinq collages complets sont les seuls dont `preview.ignored` n'est pas
vide, et ce sont exactement ceux qui portent les blocs de confiance.** Les
trente collages du seul tableau passent tous.

Le journal de l'instance servie ferme la question du « parfois » :

```
12:10:04  Collage conserve : import 25, session 18, 26567 caracteres
12:10:05  POST /history/18/picks/preview  200 OK
12:10:24  Collage conserve : import 26, session 18, 1490 caracteres
12:10:24  POST /history/18/picks/preview  200 OK
12:10:37  POST /history/18/picks/import   200 OK
```

**Vingt secondes séparent l'aperçu du collage complet du collage du seul
tableau, et il n'y a aucun `POST /picks/import` entre les deux.** Même forme au
19/08 à 23:09 pour l'import 20. Les deux rejets d'ingestion attribués à
l'import 20 datent du 20/08 à 09:18 — ils viennent du rejeu du lot 14, pas d'un
import.

### La cause exacte

`picks_import.parse_table` servait **un seul nom pour deux notions** :

- `columns` est l'en-tête du tableau **en cours de lecture**, et tout titre de
  section le remet à zéro — c'est ce qui permet à la section C-bis de porter le
  sien ;
- `if columns is None:` en fin de boucle prétend dire « aucun tableau n'a été
  reconnu », qui est un fait sur **tout le collage**.

Un rendu complet se termine par `F. Ce qui aurait changé mon analyse`. Trace sur
les cinq collages : `columns` vaut `None` à la fin des cinq, et le dernier
événement est le titre `F.` dans les cinq cas — 5, 5, 7, 7 et 6 lignes de
tableau ayant pourtant été lues juste avant.

Le gabarit `picks.html` porte `{% if preview and not preview.ignored %}` : le
formulaire entier disparaît, avec ses six lignes cochées, ses six blocs `conf`
et sa ligne `dossiers_ouverts`.

**Le défaut est né du correctif de l'autre moitié de la même fonction.**
`SECTION_HEAD` a été introduit par `a75da0d`, le 19/08 à 18:11:45, pour que la
bascule vers C-bis ne se déclenche plus sur une mention en prose. L'import 18 a
réussi à 17:59 — douze minutes avant. Les imports 20 et 25 sont postérieurs.

### Les six pistes, une par une

| Piste | Verdict |
| --- | --- |
| garde du lot 14 (`dossiers_ouverts`) | **écartée, mesurée** : `blocking=False` sur les cinq collages complets, `True` sur les trente partiels, `None` sur la saisie à la main et le rejeu. Elle fait exactement ce pour quoi elle a été écrite |
| section G | **écartée** : aucun des cinq collages n'en porte — ils vont de A à F. Elle a néanmoins révélé un défaut **latent** : `SECTION_HEAD` s'arrêtait à `[A-F]`, donc un titre `G.` ne fermait aucune section. Corrigé, avec un test qui compare la plage aux titres du gabarit |
| taille de la charge | **écartée, mesurée** : le formulaire d'import d'un collage de 26 567 caractères pèse **111 champs et ~4,9 ko urlencodés**, contre `client_max_body_size 6m` chez nginx et aucun plafond de champs sur un formulaire urlencodé dans Starlette 0.46. Trois ordres de grandeur de marge |
| doublons / indépendance | **écartée comme cause** : les deux collages qui échouent arrivent **avant** tout collage partiel de leur session. Elles mordent bien sur un repost, et c'est leur rôle |
| découpage en sections | **retenue, et c'est la cause** — voir ci-dessus |
| appariement (`_affiche_of`) | **écartée, mesurée** : 5, 5, 5, 7 et 6 blocs appariés sur les cinq collages, mention `(estimée)` comprise. Le contrat d'en-tête a déjà son test |

## §1b — Le correctif : le refus se prononce sur ce qui a été lu

Deux changements, et le second est le garde-fou.

- **`parse_table` distingue les deux notions.** Un fait global, `entete_vu`, à
  côté de l'état par section. Le refus ne se prononce que si **aucune sélection
  n'a été lue**, et il porte alors deux messages distincts : « aucun tableau
  reconnu » quand aucun en-tête n'a été vu, « tableau reconnu mais aucune ligne
  retenue » quand il l'a été. Le second nomme la section et le geste.
- **`_unreadable` devient le seul chemin vers `ignored`, et il ne peut pas
  fermer un aperçu lisible.** Une remarque posée sur un aperçu qui porte des
  sélections descend dans `notes`, le second canal ouvert au lot précédent —
  qui affiche sans empêcher d'importer. C'est la règle du brief tenue par une
  fonction plutôt que par la vigilance : *un collage complet ne peut plus être
  plus difficile à importer qu'un collage partiel*.

Mesure après correctif : `ignored` est vide sur les **35** collages, et les
comptes de sélections et de blocs ne bougent pas d'une unité.

## §1c — Le test, et pourquoi celui qui existait n'a rien vu

**`tests/test_collage_complet.py` existait déjà, tournait sur le collage réel de
21 559 caractères, comptait chaque objet exactement — et il est resté vert
pendant toute la panne.**

Il assertait `len(preview.picks)`, `preview.claims_attached`, `len(preview.combos)` :
tous justes. Il n'assertait ni `preview.ignored`, ni le rendu de la route. **Un
banc qui mesure le lecteur ne voit pas un défaut dans la porte** — c'est la
onzième occurrence du motif du projet, et la première où elle frappe le
garde-fou lui-même.

Trois tests ajoutés, et les trois tombent sur le code d'avant :

- `test_un_rendu_complet_de_a_a_g_s_importe_et_ecrit_chaque_objet` — **par la
  vraie route** : coller, relire le formulaire rendu, le renvoyer tel quel comme
  un navigateur, puis compter **en base**. Il exige 5 sélections de section C,
  2 de C-bis, 7 blocs `conf`, 7 crans calculés, 1 combiné, 3 jambes, 8 scores en
  sets et 5 mises. Un compte, pas une présence ;
- `test_un_collage_complet_n_est_jamais_refuse_a_l_apercu` — la porte, et non ce
  qu'elle laisse passer ;
- `test_une_remarque_sur_un_apercu_lisible_descend_dans_les_notes` — le
  garde-fou contre sa propre panne, dans les deux positions.

La fixture est le collage réel prolongé de ce que le gabarit produit aujourd'hui
et qu'aucun collage archivé ne porte : les deux blocs `conf` du côté
exploratoire, et **la section G**.

**Trouvé en l'écrivant** : la fixture posait un prompt sans son lot, si bien que
`combos.record` refusait toutes les jambes — « elles n'ont jamais été comparées
à celles-ci ». Aucun test d'aperçu ne pouvait le voir, la lecture ne lisant
jamais `prompt_events`.

## §1d — Le rejeu

Sauvegarde `myassistantbet-20260820-191300.db`, puis `--rattacher --ecrire` sur
les cinq collages complets.

| | Avant | Après |
| --- | ---: | ---: |
| sélections portant leur bloc `conf` | 22 | **26** |
| accord cran déclaré / recalculé | 20 / 176 — **11,4 %** | 23 / 176 — **13,1 %** |
| `research_override_cause = ligne_absente` | 111 | **107** |
| combinés enregistrés | 2 | 3 |

**Quatre sélections récupèrent leur cran**, toutes de l'import 25. Deux blocs de
plus ont été lus et **non posés** : deux sélections de la session 18 portent le
même marché et le même libellé (`BTTS Non`), et le rapprochement refuse plutôt
que de deviner. C'est le comportement voulu.

Les imports 14, 16, 18 et 20 avaient déjà été rattachés au lot 14 ; le rejeu
d'aujourd'hui ne leur ajoute que le combiné de l'import 18.

**Ce qui se répare ici est l'avenir**, comme le brief le dit : les 82 sélections
sans texte complet à relire le restent.

## §2 — Le rapprochement des tournois : dimensionné, puis construit

### Le dimensionnement, et il décide de la branche

Relevé sur les **798 réponses `profile/matches-played` archivées**, 84 266
matchs de simple — aucun appel :

| | Mesuré |
| --- | ---: |
| noms de tournoi distincts côté source | **1 179** |
| dont portant un niveau de tableau principal ATP/WTA | **143** |
| compétitions de tennis côté projet | **43** |
| dont déjà analysées | **4** |

**La table se compte par compétition, pas par catalogue.** On ne demande jamais
« quels tournois ce joueur a-t-il joués » mais « a-t-il joué celui-ci » : 43
lignes au plus, exactement la forme de `TENNISDATA_TOURNAMENTS`. C'est « une
trentaine » au sens du brief, donc la branche est de construire.

### Ce qui interdit toute heuristique, et c'est mesuré

Sondage par jetons partagés entre notre libellé et celui de la source, à niveau
égal : **32 compétitions sur 43 ont un candidat unique, 4 en ont plusieurs, et
7 n'en ont aucun**. Les sept sans candidat ne partagent **aucun mot** avec leur
nom de source :

| Notre libellé | Chez `matches-played` |
| --- | --- |
| ATP / WTA Canadian Open | `National Bank Open`, `Omnium Banque Nationale`, `Rogers Cup`, `Coupe Rogers` |
| ATP / WTA Queen's Club | `HSBC Championships`, `cinch Championships`, `Fever-Tree Championships`, `The HSBC Championships`, `LTA London Championships` |
| ATP / WTA US Open | `U.S. Open - New York` |
| WTA German Open | `Berlin Tennis Open`, `Berlin Ladies Open`, `bett1open`, `Betti Open` |

Le fournisseur **renomme au sponsor et ne rétro-corrige pas** : Cincinnati vaut
`Cincinnati Open` depuis 2025 et `Western & Southern Open` avant. D'où une
**liste** par tournoi, `|`-séparée, jusqu'à cinq noms.

### La ville n'est pas une clé, et le brief le supposait

Le brief propose « un appariement par ville et par dates de tournoi ». Trois
mesures s'y opposent :

- **`competitions.city` est vide sur les 43 compétitions de tennis** — et sur
  les 70 de football. La colonne existe depuis la migration 038 et n'a jamais
  été remplie ;
- **la ville bouge par calendrier** : le Canadian Open alterne Toronto et
  Montréal chaque année, sous quatre noms ;
- **et une fois par pandémie** : l'archive porte
  `Western & Southern Open - New York`, `Premier 5`, 2020 — l'édition de
  Cincinnati déplacée. Un contrôle par la ville l'aurait rejetée.

Ce qui **se contrôle**, en revanche, et que le fournisseur sert à 100 % : le
niveau (`tier`) et la surface (`court.name`). Ils se confrontent à la taxonomie
et à la surface déjà saisies chez nous — c'est ainsi que les 43 lignes ont été
vérifiées, et c'est ce qui a tranché `WTA German Open` (WTA 500 sur gazon en
Allemagne : Berlin, pas Hambourg).

### Aucun identifiant, et cette fois les huit champs ont été regardés

Le lot 16 l'avait établi pour `tournamentId` et `link`. Sur les **385 noms vus
sur plusieurs années** :

| Champ | Stable d'une année sur l'autre | Valeurs partagées par plusieurs tournois |
| --- | ---: | ---: |
| `id` | 0 / 385 | 52 |
| `link` | 7 / 385 | 55 |
| `reserveChar` | 262 / 385 | 27 |
| `site` | 334 / 385 | 77 |
| `url` | 237 / 385 | 55 |
| `coord` (lat/lon) | 376 / 385 | **232** |

Aucun n'est à la fois stable et sans collision. Les coordonnées échouent pour
une raison structurelle : deux tournois d'une même ville ont les mêmes.

### Le mode d'échec, et la vérification en réel

**Sans rattachement, aucune ligne** — jamais « ici jamais joué ». Avec
rattachement, « jamais joué » redevient un fait, et c'est l'angle de la ligne.
Chaque nom déclaré qu'un historique ne porte pas est journalisé des deux côtés,
pour que la table se complète par la mesure.

Rejeu sur les 261 profils archivés : **221 rendent un passé à Cincinnati**, 169
au Canadian Open, 44 à Queen's. Et les quatre joueuses que le lot 16 refusait de
faire mentir :

```
Iga Swiatek      ici vainqueur 2025 (2 éditions)
Elena Rybakina   ici 1/2 2025 (2 éditions)
Amanda Anisimova ici 3e tour 2026 (2 éditions)
Jessica Pegula   ici 3e tour 2026 (2 éditions)
```

Le journal signale déjà que `Western & Southern Open - Cincinnati` ne sert chez
personne : c'est attendu, les profils archivés étant tronqués à 100 matchs tant
que la pagination profonde du lot 16 n'a pas tourné en production. La boucle
« la table se complète par la mesure » fonctionne dès le premier jour.

**La ligne se pose dans `Palmares`, qui la promettait déjà** : le préambule dit
« le meilleur résultat atteint **ici** et l'année » depuis toujours, et c'est le
rendu qui s'en était écarté au lot 16. Aucun nouveau libellé, donc aucune entrée
de préambule — ce que ce lot s'interdit.

## §3 — Le règlement automatique : de 95 à 151 sélections, sans une divergence

Rejeu sur les **307 sélections tranchées à la main**, une famille à la fois :

| Famille | Réglées | Accord | Taux | En service |
| --- | ---: | ---: | ---: | --- |
| `issue` (1N2, Vainqueur) | 79 | 79 | **100,00 %** | oui, déjà |
| `total` (O/U buts **et jeux**) | 26 | 26 | **100,00 %** | oui, +10 |
| `handicap_jeux` (tennis) | 34 | 34 | **100,00 %** | **oui** |
| `btts` | 7 | 7 | **100,00 %** | **oui** |
| `double_chance` | 5 | 5 | **100,00 %** | **oui** |
| `handicap_buts` (football) | 14 | 13 | 92,86 % | **non** |

Passe sur l'instance servie après mise en service : **151 propositions, 0
divergence, 0 nouvelle** — les 151 réglables portaient déjà leur résultat manuel,
et le calcul retombe sur chacun.

### Le total au tennis ne demandait aucune source

Le module affirmait qu'`event/get` « ne sert pas le compte de jeux dans son
score agrégé, donc la règle ne s'écrit pas ». **C'est faux** : `4-6,7-6,0-6`
compte des jeux, pas des sets. Les additionner ouvre d'un coup les deux plus
gros marchés non réglés du projet — 34 handicaps jeux et 10 totaux de jeux,
soit 44 des 56 sélections gagnées.

**Ce n'est pas le motif du projet mais l'autre règle, celle de « cherchez
l'identifiant »** : une affirmation de commentaire prise pour une mesure, et
jamais rouverte contre la charge utile. Elle a coûté un lot entier de silence
sur les deux marchés les plus joués du tennis, et le seul geste qui l'aurait
évitée est d'ouvrir une réponse réelle — ce qui prend dix secondes.

### Le handicap de football sort sur une seule ligne, et c'est la règle

`Vålerenga +1`, perdu 1-2, fait 2-2 après handicap : un remboursement sur un
marché à deux issues. Le règlement manuel dit `win`. **L'arithmétique n'est pas
en cause** — ce qui n'est pas établi est le nombre d'issues du marché où le pari
a été posé, et un `void` mal rangé corrompt le résidu au prix. 92,86 % n'est pas
100 %.

### Le sport se lit sur la compétition, jamais sur le libellé

Les deux graphies sont pourtant propres — `Hand. jeux` au tennis, `Handicap` au
football, 45 et 37 lignes sans un mélange. Mais une saisie à la main peut écrire
l'une pour l'autre, et le seul coût de cette faute serait de **mettre en service
la famille qu'on refuse**. `enabled_for` prend donc un sport dont le défaut vide
range le handicap du côté non servi : on ne peut pas ouvrir une famille par
omission.

## §4 — Dettes de forme

- **Unité de mise** : rien à faire, vérifié. `/settings` affiche le badge
  `provisoire` et « à re-mesurer le 2026-09-20 » ; `changelog_mesure` porte
  l'entrée 27, `day = 2026-09-20`, avec sa méthode de re-mesure.
- **Registre des chemins d'écriture** : ce lot n'ajoute aucun `INSERT` vers
  `picks`, `combos`, `combo_legs` ou `set_scores`. `tests/test_write_paths.py`
  lit la source et reste vert (14 tests).
- **Règle du zéro d'appariement** : étendue, et il le fallait — voir ci-dessous.

### Le zéro d'appariement n'est pas une règle de noms

La règle du lot 16 portait sur les noms de joueurs. Les deux occurrences
suivantes n'en portent aucun :

| Lot | Le zéro disait | Ce qu'il était |
| --- | --- | --- |
| 16 | 0 édition sur 589 matchs | le **champ** : `result` chez `matches-played`, `score` chez `event/get` |
| 17 | 0 joueur sur 261 | l'**indice** : le nom est l'avant-dernier segment de `/profile/<nom>/matches-played` |

Nom, champ, indice : les trois produisent le même zéro crédible, et rien ne les
distingue **du zéro lui-même**. Ce qui les distingue est le **dénominateur** —
compter ce qui *entre* dans le rapprochement et pas seulement ce qui en sort.
C'est ce qui a fait tomber l'occurrence d'aujourd'hui en dix secondes : « 261
profils archivés » contre « 1 profil » dans le premier jet.

## §5 — Ce que la mesure contredit dans ce brief

| Affirmé | Mesuré |
| --- | --- |
| « coller la réponse entière **provoque parfois** des erreurs » | **jamais « parfois », et jamais une erreur** : les cinq collages complets sont refusés, les trente partiels passent. Le refus est un message d'aperçu, pas un code HTTP — 200 OK des deux côtés |
| « la conclusion du lot 14 est au moins partiellement fausse » | **confirmé, et pire que ça** : le lot 14 mesurait au bon endroit, mais son remède a été livré le 20/08 alors que la panne datait du 19/08 18:11. Les vingt collages du seul tableau **précèdent** la panne : c'était bien un geste jusqu'au 19/08 au soir, et c'est un contournement depuis |
| « les 89 sélections en sont la conséquence directe » | **non, et le partage se compte** : la base porte aujourd'hui **107** sélections en `ligne_absente`, dont **77 antérieures au 19/08 18:11** — donc au défaut — et 30 postérieures. Le geste et la panne ont chacun leur part, et elles ne se recouvrent pas |
| « la section G contient des montants, un lecteur qui ne l'attend pas… » | **écartée** : aucun collage archivé ne porte de section G. Elle a néanmoins révélé un défaut latent — `SECTION_HEAD` ignorait `G.` |
| « une limite de champ, de requête ou de délai » | **écartée** : 111 champs, ~4,9 ko, contre 6 Mo autorisés |
| « le banc de transport couvre six formats » | **sept** depuis le lot 15 (`mises:`), et le septième n'était pas le problème : la lacune est qu'aucun format n'est testé **par la route** |
| « un appariement par ville et par dates de tournoi se contrôle » | **non** : `competitions.city` est vide sur les 113 compétitions, la ville alterne chaque année au Canadian Open et l'archive porte une édition de Cincinnati jouée à New York. Ce qui se contrôle est le **niveau et la surface** |
| « handicap européen ±1 et handicap 0 » comme étape 3 | l'étape 3 **ne passe pas** (92,86 %) et l'étape 4, réputée la plus difficile à cause des abandons, passe à **100 %** sur 34 lignes. L'ordre de difficulté annoncé est inversé |
| « 2 352 tests verts » à la fin du lot 16 | **2 351** : un test portait une date de match au 20/08 20h45, donc vert le matin et rouge le soir. Corrigé en premier |

### La leçon de méthode du lot

**Le correctif d'un défaut de lecture en a créé un autre dans la même fonction,
et le banc écrit pour ce défaut-là est resté vert.**

`a75da0d` répare la bascule vers C-bis, introduit `SECTION_HEAD`, et lui fait
remettre `columns` à zéro — ce qui est juste. Le test de bout en bout écrit le
même jour compte les objets lus, et ils sont tous justes. Ce qu'aucun des deux
ne regarde, c'est si le résultat de cette lecture **peut être importé**.

La règle générale : **un test qui mesure la sortie d'un service ne dit rien de
la surface qui la rend.** `CONTRIBUTING.md` porte déjà la moitié de cette leçon
— « le service et sa surface se livrent ensemble » — mais elle y visait un
service qui accepte une valeur que rien ne permet de saisir. Ici c'est
l'inverse : un service qui produit une valeur que rien ne permet de valider.
Les deux se testent de la même façon, et d'une seule : **poster le formulaire et
relire la base**.

---

# LOT 18 — dix corrections tirées d'un prompt réellement rendu

Prompt de référence : **167**, session 18, `2026-08-20T19:56:58Z`, 7 blocs
(6 football, 1 tennis), 22 179 tokens. Toutes les mesures ci-dessous portent sur
une **copie** de la base servie (`VACUUM INTO`), prise au début du lot.

## §1 — `Ici` s'arrêtait avant le match qui compte

### Le fait, reproduit

Sur M6, `Parcours` nomme quatre adversaires pour Bejlek et trois pour Keys ;
`Ici` en couvre trois et deux. Le quatrième de Bejlek est **Aryna Sabalenka
(2194)**, jouée le 20/08 à 00h30 UTC — le fait le plus déterminant de la
rencontre, et le bloc ne disait pas si elle avait gagné.

La cause est nette : la charge utile `matches-played` archivée pour les deux
joueuses date du **19/08 16h40 UTC**, et le match Sabalenka a commencé après.
`[releve au 19/08]` le disait déjà ; il restait à faire la soustraction de tête,
sur trois lignes distantes de deux cents caractères.

### La borne évidente est fausse, et deux fois

Le brief demande « la liste nommée des matchs **postérieurs au relevé** ».
Mesure sur les **409 rencontres scannées** des 195 blocs tennis soumis, dont 28
ne sont couvertes par aucun résultat de la source :

| Borne essayée | Non couverts attrapés |
| --- | ---: |
| jour du match > jour du relevé | **0 sur 28** |
| instant du coup d'envoi > instant du relevé | **6 sur 28** |

Le premier échoue parce que la **journée de tournoi** du match Sabalenka vaut
`2026-08-19`, comme le jour du relevé, alors que le coup d'envoi est à 00h30 le
20. Le second échoue parce qu'un match **commencé** avant le relevé n'est pas
**fini** : Pegula – Cirstea part à 16h30, le relevé passe à 16h40, et la source
n'en dit rien. Il faudrait la durée d'un match, qu'aucune source ne publie —
question close au lot 3.

**La soustraction, elle, n'a aucune borne à choisir** : elle compare deux listes
que l'application possède déjà.

### Le rapprochement se fait sur le nom **ou** le jour

Mesure sur les mêmes 409 rencontres :

| Critère qui rapproche | Rencontres |
| --- | ---: |
| le nom **et** le jour | 258 |
| le **nom** seul | 109 |
| le **jour** seul | 14 |
| aucun des deux | 28 |

Les deux filets sont nécessaires. Le nom seul rattrape un décalage de date —
Hijikata – Monfils vaut `13/08 23h05` chez nous et `14/08 02h00` chez la
source ; le jour seul rattrape une graphie — « Bianca Vanessa Andreescu » contre
« Bianca Andreescu ». **Les deux ne se contredisent jamais.**

Le jour se compare **à l'exact**. La tolérance d'un jour paraît prudente et
ouvre exactement le trou qu'on ferme : à `±1`, la journée du 18/08 couvrait celle
du 19 et Sabalenka disparaissait. Trouvé en rendant le bloc.

**Et le nom se compare généreusement, ce qui est l'arbitrage inverse.** Rendu
tel quel, le fragment nommait trois matchs « non couverts » dont le score
figurait sur la ligne juste au-dessus : nos scans écrivent « Leylah Fernandez »
et « Bianca Andreescu », la source « Leylah **Annie** Fernandez » et « Bianca
**Vanessa** Andreescu », et le jour différait d'un cran sur le premier. C'est la
sixième occurrence du motif du lot 15 — *chaque source a sa fonction de nom*.

Le sens de l'erreur commande la tolérance : un faux positif envoie chercher un
score déjà rendu, donc dépense une place de dossier ; un faux négatif ne fait que
taire un fragment qui n'existait pas hier. La règle est celle de
`tennis_history.resolve` — **même nom de famille, prénoms en chaîne de
préfixes** — qui réunit « Leylah » et « Leylah Annie » et sépare les frères
Zverev. Mesure : **15 fragments → 12**, les trois retirés étant les trois faux.

**Une seule règle de nom dans le module**, et elle sert aussi à corroborer le
tournoi. La stricte y a été essayée et elle était inutile : les 14 fragments qui
rendaient un autre tournoi portaient des adversaires que nous n'avions **jamais**
scannés ici, donc c'est le jour exact qui les écarte, pas le nom. Rejeu après
unification : **223 justes, 0 faux**, inchangé.

### Le défaut trouvé sous celui-ci : `Ici` rendait le tournoi de la semaine passée

`_tournament_id` prenait le **mode** des matchs de la source tombant dans la
fenêtre de notre édition. Or deux tournois se chevauchent une semaine sur deux :
notre fenêtre contient la fin du précédent. Un joueur qui entre en lice ici après
un bon parcours ailleurs voyait donc **l'autre tournoi** rendu sous le titre
« ici ».

Mesure du 20/08/2026 sur les 195 blocs : **14 fragments sur 223** dans ce cas.
Le plus net est Darderi – Hijikata du 15/08 à Cincinnati, où la ligne servait :

    Ici   Luciano Darderi 05/08 bat Gabriel Diallo 6-4 2-3 (abandon)
          | 06/08 bat Juncheng Shang 4-6 6-1 6-4 | 08/08 bat Nuno Borges 4-6 6-3 7-5
          | 11/08 perd contre Brandon Nakashima 2-6 3-6 [releve au 19/08]
          Rinky Hijikata 02/08 perd contre Jaume Antoni Munar Clar 6-7(3) 3-6

— soit **quatre matchs du Canadien** et **un de Washington**, sur un bloc de
Cincinnati, sans qu'un mot le signale. Défaut caractéristique du projet : l'échec
et le cas ordinaire rendaient la même chose.

**La fenêtre est donc corroborée par nos propres scans** : un match de la source
ne compte pour identifier le tournoi que s'il porte un adversaire ou un jour que
nous avons scannés ici. Un joueur sans corroboration possible — il entre en
lice — rend `0`, et c'est son partenaire qui donne l'identifiant : `here_lines`
faisait déjà ce partage.

Rejeu sur les 195 blocs : **223 identifiants corroborés justes, 0 faux**, contre
209 / 14 avant. Un seul joueur sur 224 ne se corrobore ni par le nom ni par le
jour.

### Rendu avant / après — bloc M6 du 20/08

Avant :

    Ici         Sara Bejlek 14/08 bat Karolina Pliskova 6-0 6-2 | 16/08 bat Barbora Krejcikova 7-6(5) 6-4 | 18/08 bat Ekaterina Alexandrova 4-6 6-1 6-2 [releve au 19/08]
                service ici 61.8% 1re · 71.0% s/1re · 12 df (3 matchs, 212 pts)
                Madison Keys 16/08 bat Daria Snigur 4-6 6-3 6-3 | 18/08 bat Katerina Siniakova 6-1 6-3 [releve au 19/08]
                service ici 56.8% 1re · 74.7% s/1re · 9 df (2 matchs, 139 pts)

Après :

    Ici         Sara Bejlek 14/08 bat Karolina Pliskova 6-0 6-2 | 16/08 bat Barbora Krejcikova 7-6(5) 6-4 | 18/08 bat Ekaterina Alexandrova 4-6 6-1 6-2 [releve au 19/08]
                1 match non couvert : Aryna Sabalenka (2194)
                service ici 61.8% 1re · 71.0% s/1re · 12 df (3 matchs, 212 pts)
                Madison Keys 16/08 bat Daria Snigur 4-6 6-3 6-3 | 18/08 bat Katerina Siniakova 6-1 6-3 [releve au 19/08]
                1 match non couvert : Xiyu Wang (1741)
                service ici 56.8% 1re · 74.7% s/1re · 9 df (2 matchs, 139 pts)

L'Elo accompagne le nom, comme sur `Parcours` : il ne coûte rien, le classement
étant déjà en base, et c'est lui qui dit si le match manquant compte.

### La ligne reste rare, et c'est mesuré

Sur les 195 blocs tennis soumis : **7 blocs porteurs (4 %)**, 12 fragments, dont
un seul `(tout le Parcours)`. Une ligne qui sortirait partout cesserait
d'informer — même règle que `A relever` et les deux seuils égaux de l'arbitre.

Quand **tout** le parcours est non couvert, la ligne écrit `(tout le Parcours)`
plutôt que de recopier `Parcours` mot pour mot : c'est le vocabulaire que
`Fraicheur` emploie déjà pour le même fait, et deux formulations se liraient
comme deux faits.

### Ce que la mesure refuse dans le brief : `Fraicheur` ne bouge pas

Le brief demande de faire descendre le compte de `Fraicheur` — « trois de ces
quatre matchs ont désormais leur score ». **Ce serait écrire une affirmation
fausse.** `Fraicheur` ne dit pas « on ignore ce qui s'est passé » : elle dit,
et sa première ligne l'écrit en toutes lettres, que
`Forme/Usure/Profil/Marge/Niveau adv.` sont **arrêtées au 13/08**. Ces cinq
lignes sortent de `tennis-data.co.uk`, une source hebdomadaire distincte, et
elles ignorent bien les **quatre** matchs, y compris les trois dont `Ici` donne
le score. Ramener le compte à 1 ferait lire « Usure » comme comptant trois
matchs de plus qu'elle n'en compte.

Le besoin légitime — *lequel n'a de score nulle part* — est exactement ce que le
nouveau fragment d'`Ici` sert, et il le sert au bon endroit : sur la ligne qui
porte les résultats.

### Gabarit

Un seul passage touché, la consigne TENNIS de « CE QU'IL FAUT VÉRIFIER » :
elle demandait de repérer soi-même qu'« un match postérieur à la date de relevé
manque aussi ». **−1 token** (463 → 462 caractères).

## §2 — `Tour` reste inconnu, le nombre de tours ne l'est pas

Sur M6, `Tour` écrit `phase non renseignée (118 joueurs vus ne forment aucun
tableau)` quand `Parcours` établit quatre tours pour Bejlek et trois pour Keys.
Ça décide de quelque chose : le gabarit fait de l'enjeu asymétrique une condition
d'accès aux paliers hauts, et l'écarte en précisant que « l'enjeu asymétrique
n'existe pas en quart d'un Masters 1000 ». En demie, entre une joueuse jamais
allée au-delà du 3e tour ici et une lauréate de Grand Chelem, il existe.

**Rien n'est deviné.** La phase n'est servie par personne — vérifié au lot 11 —
et un tableau de qualification ne finit pas par une finale : compter depuis la
fin y produirait un nombre qui ne désigne rien. La mention **complète**
`phase non renseignée`, elle ne la remplace pas.

Rendu :

    Tour        phase non renseignee (118 joueurs vus ne forment aucun tableau)
                au moins 4 tours disputes par Sara Bejlek, 3 par Madison Keys

**« au moins »**, parce que le début d'un tableau peut précéder notre fenêtre de
scans — la limite que `truncated()` nomme déjà.

### Nos scans seuls, et c'est un arbitrage mesuré

La source de profils rapporte parfois un tour que nos scans n'ont pas vu.
Mesure du 20/08/2026 sur les 195 blocs : **11 joueurs sur 192 (5,7 %)**, et
toujours d'un seul tour. La borne serait donc un peu meilleure en la lisant ;
elle coûterait une dépendance de plus, une question de drapeau, et le mot
« au moins » reste vrai dans les deux cas. Le jour où ce taux monte, c'est là
que ça se reprend.

### Portée

Sur les 195 blocs tennis : 99 nomment leur tour et ne portent pas la mention —
« quart de finale » situe déjà, et une ligne qui sort partout cesse d'informer ;
**70 la portent** ; 26 gardent la phase inconnue sans aucun tour établi.

### Gabarit

Une puce ajoutée sous l'entrée « Tour » du chapitre COMMENT LIRE LES BLOCS.
**+99 tokens** (227 → 585 caractères). Marge relevée au même moment : le lot
football pèse **15 831** pour une alarme à 23 000, le lot mixte **13 506** pour
20 000 — la règle « tout ajout budgète sa propre coupe » a été écrite quand le
socle tenait à 7 tokens de son plafond ; elle ne mord plus ici, et la mesure est
écrite pour qu'on n'ait pas à la redécouvrir.

### Une assertion réalignée sur sa substance

`test_la_puce_ne_s_applique_pas_systematiquement` recopiait la formulation
exacte de la consigne TENNIS (§1). Ce qu'elle protège est que la consigne soit
**conditionnelle** — elle l'est toujours, et davantage. Les ancres ont été
reprises et **deux assertions ajoutées** sur ce que la ligne nomme désormais :
une assertion qui casse sur une reformulation décrit au lieu de contraindre,
mais l'affaiblir sans rien mettre à la place serait pire.

## §3 — Le palmarès par catégorie : la collecte n'avait jamais tourné

### Les trois hypothèses du brief sont fausses

| Supposé | Mesuré |
| --- | --- |
| la construction du lot 16 est ATP seulement | fausse — `TIER_BY_CATEGORY` porte `("masters_1000", "wta") → "WTA 1000"`, et il se résout : `tier_for('masters_1000', 'wta')` rend bien `WTA 1000` |
| la couverture WTA est vide | fausse — les deux profils sont archivés, 281 et 719 matchs annoncés |
| le rattachement de ce tournoi manque | fausse — `profile_tournament_names(99)` rend les **trois** graphies `Cincinnati Open - Cincinnati`, `Western & Southern Open - Cincinnati`, `Western & Southern Open - New York` |

### La cause

**`player_palmares` est vide : 0 ligne.** Le module est arrivé par le commit
`ee49211` du **20/08 à 18h37 UTC** ; la passe qui remplit la table
(`myassistantbet-timelines`, planifiée à `scan_hour + 30 min`, soit 07h30) n'avait
pas tourné une seule fois depuis. Le prompt 167 a été rendu à **19h56 UTC**, une
heure vingt après le commit.

Toutes les réponses `matches-played` archivées portent d'ailleurs
`{"page":1,"pageSize":100}` — la pagination d'avant le lot 16.

### Preuve : la passe a été lancée sur la copie

Deux joueuses, **6 appels** (quota 139 417 → 139 411). La table se remplit et la
ligne sort :

    Palmares    Sara Bejlek WTA 1000 1/8 2026 (6 éditions, dur) · ici 1/8 2026 (1 édition)
                | Madison Keys WTA 1000 1/2 2025 (38 éditions, dur) · ici vainqueur 2019 (11 éditions)
    Bilan ici   Madison Keys 1/8 2025, 2V-1D

C'est exactement l'angle demandé : **6 éditions contre 38**, la meilleure
performance de Bejlek en WTA 1000 est un huitième, et Keys a **gagné ici en
2019**. Aucun correctif de rendu n'était nécessaire.

### Le trou de couverture, lui, est réel — et il est structurel

Une fois la passe lancée, elle n'aurait servi que **douze joueurs**. `BATCH`
vaut 12, et c'est une borne de **temps de mur** posée pour les timelines, qui
coûtent quatre à six appels **par rencontre**. Le palmarès, lui, coûte une
médiane de **trois appels par joueur**, et il héritait de la même borne.

Mesure du 20/08/2026 sur les **18 journées de board archivées** :

| | |
| --- | ---: |
| joueurs de tennis distincts, médiane par journée | **32** |
| maximum | **99** |
| journées où `BATCH = 12` suffit | **4 sur 18** |

Sur quatorze journées sur dix-huit, la majorité des joueurs dont le bloc se rend
le lendemain n'aurait donc pas de palmarès. Le docstring d'`upcoming_players` le
disait déjà sans que personne l'oppose à `BATCH` — « un lot tennis porte
trente-cinq joueurs en moyenne » — deux sorties du même module qui ne se
parlaient pas.

**Correctif : le palmarès prend tous les joueurs à venir, le reste garde
`BATCH`.** Coût aux mêmes chiffres : ~96 appels par jour, ~3 000 par mois, contre
**139 411 restants sur 150 000** — 2 % du quota. Jamais tout le catalogue : 256
joueurs feraient 768 appels pour rafraîchir des profils qui ne jouent pas.

**Aucun test ne couvrait ce branchement** — `tests/test_palmares.py` mesure le
service, et la passe n'était testée nulle part. C'est la règle du 20/08 : un banc
qui mesure le lecteur ne voit pas un défaut dans la porte. Le test ajouté **lance
la passe et relit ce qu'elle a demandé**.

## §4 — `Ecart` comparait qui sert ainsi, pas qui en tire quelque chose

### Le fait

Sur M6, la ligne rend `service +0.1 pts sur la 1re balle pour Sara Bejlek` —
les deux joueuses sont à 63,2 % de premières balles — pendant que la ligne
`Service` juste au-dessus porte **6,1 points** d'écart sur les points gagnés
derrière la première et **7,5 points** sur les doubles fautes.

### Ce que la mesure contredit dans le brief

Le brief appelle le taux de mise en jeu « la grandeur la moins discriminante ».
**C'est la plus dispersée des cinq**, mesurée sur les 174 paires de joueurs des
blocs soumis :

| Grandeur | médiane | q90 | > 5 pts |
| --- | ---: | ---: | ---: |
| 1re balle **mise en jeu** | **4,3** | 9,2 | 48 % |
| points gagnés s/1re | 3,5 | 8,4 | 37 % |
| doubles fautes | 2,9 | 7,4 | 28 % |
| points gagnés s/2e | 2,2 | 5,5 | 19 % |
| retour | 2,4 | 5,9 | 20 % |

Le `+0,1` de M6 est donc un tirage bas d'une distribution large, pas une
propriété de la grandeur. Ce qui la disqualifie n'est pas son étalement mais
**ce qu'elle mesure** : un joueur qui rentre 76 % de premières n'en tire pas
forcément plus qu'un joueur à 55 %. Le brief a raison sur le fond et faux sur la
raison — et la raison compte, parce qu'un seuil posé sur l'étalement aurait
gardé la mauvaise grandeur.

### Le seuil ne s'invente pas, il se lit sur les dénominateurs

`+0,1 pts` sur 1 400 points de service n'est pas un petit avantage : c'est
**rien**. Le nommer en tête de ligne est une affirmation que la donnée ne porte
pas — même famille que `HANDICAP_ALERT_MARGIN`, où l'on se tait quand l'écart
tombe sous le bruit.

Un écart n'est nommé que si son **intervalle de Newcombe exclut zéro** —
`inference.difference_interval`, déjà écrit pour la différence de deux
proportions. Le seuil s'adapte donc au volume de chaque joueur au lieu d'être un
nombre choisi. Sur M6 :

| Grandeur | Écart | Intervalle | Nommée |
| --- | ---: | --- | --- |
| 1re balle en jeu | +0,1 | `[-3,5 ; +3,6]` | non |
| points s/1re | −6,1 | `[-10,4 ; -1,7]` | **oui** |
| doubles fautes | −7,5 | `[-11,8 ; -3,2]` | **oui** |
| retour | +5,3 | `[+1,7 ; +8,9]` | **oui** |

### Rendu avant / après — M6

    Ecart       service +0.1 pts sur la 1re balle pour Sara Bejlek | retour +5.3 pts pour Sara Bejlek · taux non ajustes du niveau d'adversaire

    Ecart       s/1re +6.1 pts pour Madison Keys | df +7.5 pts pour Sara Bejlek | retour +5.3 pts pour Sara Bejlek · taux non ajustes du niveau d'adversaire

**Sur les doubles fautes, l'avantage est au plus bas taux** — Bejlek 11,3 %
contre 18,8 %. L'inversion est portée une seule fois, par un booléen de la table
des grandeurs : deux écritures auraient divergé, et un écart lu à l'envers est
l'erreur la plus coûteuse que ce bloc puisse produire.

### Portée mesurée

Sur les 174 paires : la ligne sort sur **141 (81 %)**, avec un fragment sur 41 %
des blocs, deux sur 26 %, trois sur 14 %, et **aucune ligne sur 19 %**. Par
grandeur : points s/1re 49 %, doubles fautes 49 %, retour 36 %. Longueur moyenne
100 caractères, maximum 168.

### Gabarit

L'entrée « Ecart » du chapitre COMMENT LIRE LES BLOCS : **+126 tokens**
(181 → 634 caractères).

### Deux tests réalignés sur la décision

`test_l_ecart_est_calcule_par_l_application` et
`test_les_quatre_lignes_sortent_quand_le_drapeau_est_haut` construisaient deux
joueurs ne différant que par leur taux de **mise en jeu** — ils ne produisent
plus de ligne, et c'est le comportement voulu. Leurs fixtures portent désormais
un écart sur les points gagnés. Ce n'est pas une assertion affaiblie : c'est un
changement de fond, et le test suit la décision.

## §5 — Deux unités incompatibles dans la même famille de lignes

`Ici` écrivait `12 df` — un **compte brut** — à côté de `61.8% 1re` et
`71.0% s/1re`, quand `Service` écrit `11.3% df` sur les **secondes balles**. Les
deux lignes décrivent la même joueuse à deux profondeurs et ne se rapprochaient
pas sans un calcul intermédiaire : 12 doubles fautes sur ~81 secondes balles font
**14,8 %** sur ce tournoi contre 11,3 % sur 52 semaines, soit une dégradation
nette que le bloc ne donnait pas à lire.

Après :

    service ici 61.8% 1re · 71.0% s/1re · 14.8% df (3 matchs, 212 pts)
    service ici 56.8% 1re · 74.7% s/1re · 15.0% df (2 matchs, 139 pts)

**Le compte brut n'est pas gardé à côté**, contrairement à ce que le brief
propose. Il faudrait un seuil de « petit dénominateur » qui s'inventerait, et la
parenthèse borne déjà le fragment entier — `(3 matchs, 212 pts)` dit exactement
ce que le compte disait de la solidité. Rien du tout quand aucune seconde balle
n'a été servie : zéro se lirait comme « aucune double faute ».

### L'audit demandé : aucune autre ligne n'est dans ce cas

Les blocs CONTEXTE des 40 derniers matchs de tennis soumis ont été rendus et
passés au motif « un fragment en pourcentage à côté d'un entier nu ». **Aucune
autre ligne ne le porte.** Les comptes qui subsistent ailleurs sont tous des
**dénominateurs** entre parenthèses — `(1515 pts recus)`, `(317 jeux servis)` —
ou des fractions que le projet impose — `TB 3/10`, `2 sets 7/10`, `+5.2 en V/6`.

## §6 — Les lignes en quart, et le handicap posable qui était jeté

### Ce que le brief demandait, et ce que la mesure a ajouté

Marquer les lignes en quart là où elles sont écrites : fait, `†`. Mesure du
20/08/2026 sur les **271 blocs de football archivés** (`prompt_odds`) :

| | |
| --- | ---: |
| lignes d'échelle O/U affichées | 1 414 |
| dont en quart | **412 (29 %)** |
| échelles **entièrement** en quart | **0** |
| paliers de handicap principaux | 268 |
| dont en quart | **94 (35 %)** |

Le second volet du brief — « quand un marché est **entièrement** en quart, le
signaler » — ne se pose donc **jamais** pour l'O/U, qui rend cinq lignes. Il se
pose pour le handicap, qui n'en rend qu'**une**, et une fois sur trois.

### Et là, la mesure a trouvé bien pire qu'un marquage manquant

Sur les 94 blocs dont le palier d'équilibre est en quart, **un palier entier
servi des deux côtés existait dans l'échelle — 94 fois sur 94**, et il était
jeté. Sur un tiers des blocs de football, le rendu montrait la seule ligne qu'on
ne peut pas poser et cachait les quatre qu'on peut. Le brief lit le symptôme
juste — « sur le match le plus déséquilibré du lot, aucun handicap n'est
posable » — et la cause est un choix de rendu, pas une absence de prix.

`_render_spreads` rend donc **deux** paliers quand l'équilibre tombe en quart :
l'équilibre marqué, parce qu'il situe le match mieux qu'aucun autre, puis le
palier posable. Le second se choisit par `_main_handicap` sur l'échelle
restreinte — **la même fonction**, jamais un second départage qui aurait divergé.

### Rendu avant / après — M3, le bloc le plus déséquilibré du lot

    Handicap    Al-Riyadh +1.75 1.98 | Al-Nassr -1.75 1.84  [Pinnacle (ref.)]
    O/U         3.25: 1.61/2.28 | 3.5: 1.79/2.01 | 3.75: 2.00/1.81 | 4: 2.33/1.60 | 4.25: 2.62/1.48  [Pinnacle (ref.)]

    Handicap    Al-Riyadh +1.75† 1.98 | Al-Nassr -1.75† 1.84  [Pinnacle (ref.)]
                posable Al-Riyadh +2 1.74 | Al-Nassr -2 2.10
    O/U         3.25†: 1.61/2.28 | 3.5: 1.79/2.01 | 3.75†: 2.00/1.81 | 4: 2.33/1.60 | 4.25†: 2.62/1.48  [Pinnacle (ref.)]

Et M7, dont le handicap était déjà entier — aucune seconde ligne, une ligne qui
sortirait partout cesserait d'être un signal :

    Handicap    Ried -0.5 2.07 | Grazer AK +0.5 1.83  [Pinnacle (ref.)]
    O/U         2: 1.51/2.64 | 2.25†: 1.74/2.15 | 2.5: 1.98/1.89 | 2.75†: 2.24/1.69 | 3: 2.70/1.49  [Pinnacle (ref.)]

### Le marquage est scopé au football, et c'est mesuré

**Zéro point en quart sur les 4 944 issues de tennis archivées**, tous marchés
confondus. Le marquer là-bas serait du décor, et la légende du préambule est déjà
gardée par `{% if 'football' in sports %}`.

**Aucune légende par bloc** : elle se dit une fois dans le préambule, où la règle
vivait déjà. Vingt-quatre légendes pour un lot de vingt-quatre blocs, c'est
exactement le défaut que `render.common_unplayable` a corrigé.

### Gabarit

Le paragraphe « lignes en quart », déjà gardé par le sport : **+99 tokens**
(539 → 896 caractères).

## §7 — Trois exemples de format qui contredisaient le lot rendu

Les trois sont confirmés sur le prompt 167 :

| Exemple | Ce qu'il nommait | Le lot |
| --- | --- | --- |
| `dossiers_ouverts: [M1, M4, M7, M8]` | **M8** | 7 blocs |
| `sets: M3=… \| M4=… \| M8=PASSE \| …` | M3 et M4 | **football** ; un seul bloc de tennis, M6 |
| `mises: … \| combine_court=0.25` | un combiné | la section D venait d'écrire « Aucun combiné sur ce lot » |

Les trois se génèrent désormais depuis les repères réels du lot. Sur le
prompt 167 régénéré :

    dossiers_ouverts: [M1, M3, M5, M7]
    sets: M6=2-0/2-1
    mises: bankroll=200 | M1=0.50 | M3=0.50

### Deux besoins, deux règles

`dossiers_ouverts` liste un **sous-ensemble choisi** : l'échantillon est donc
**dispersé du premier au dernier**, jamais un préfixe — `M1, M2, M3` se lirait
comme « ouvre-les tous » — et jamais tout le lot. `sets` reprend **chaque** match
de tennis, donc l'exemple les prend tous, plafonné à quatre pour rester lisible.
La phrase qui l'introduit dit déjà « chaque match de tennis dans l'ordre des
blocs ».

Un lot sans tennis rend une liste vide, et la ligne s'omet avec son exemple — mais
la section entière est de toute façon fermée par sa porte de sport.

### Le critère de test est une propriété

Tout repère cité dans `dossiers_ouverts:`, `sets:` ou `mises:` doit exister parmi
les blocs rendus. Jamais la liste du jour, qui dépend de la taille du lot — même
règle que les paliers hauts et le nombre de jambes sûres.

## §8 — La fiche de priorité ignorait la surface de marché

### Le fait, confirmé — et il est pire que ce que le brief dit

M1 et M2 sont classés **2e et 3e**, sur leur densité (42 %). Ces deux blocs ne
portent que le 1N2, tout le reste étant « non servi » **sur toute la
compétition**. Le brief écrit « deux des trois premiers dossiers » : **M4 aussi**
est dans ce cas — trois des quatre dossiers proposés sont plafonnés d'avance.

### Le seuil, mesuré avant d'être écrit

Marchés **fusionnés**, c'est-à-dire ce que l'analyse voit, sur les 462 blocs
archivés :

| Sport | 1 marché | ≤ 3 marchés | distribution |
| --- | ---: | ---: | --- |
| football | **3 / 271 (1 %)** | 102 (38 %) | 1 : 3 · 3 : 99 · 11-13 : 169 |
| tennis | **1 / 191 (1 %)** | 191 (100 %) | 1 : 1 · 3 : 190 |

« Un seul marché » désigne **1 % des blocs de part et d'autre** — une minorité
stricte, ce qu'un critère de priorité doit être. Le palier suivant est à trois, et
trois marchés suffisent à traduire un angle de manière ; un critère qui se
déclencherait sur 38 % des blocs ne classerait plus rien.

**Le seuil ne se décline pas par sport, et c'est la mesure qui l'autorise** : la
norme est de 12 marchés au football et de 3 au tennis, mais « un seul » y désigne
la même part infime.

### Ce n'est pas regarder une cote

Aucune **valeur** n'est lue, seulement le nombre de familles présentes. Le tri
reste non circulaire : c'est ce que le prix vaut qui est interdit, pas le fait
qu'un marché existe. Le test qui gardait cette règle comparait un bloc **sans**
marché à un bloc **avec** — il testait donc la présence, pas le prix. Il porte
maintenant sur ce qui était vraiment en jeu : deux blocs aux mêmes marchés à des
prix opposés rendent la même fiche.

### Une rétrogradation n'est pas un veto, et le filtre en faisait un

`sheet()` écarte tout dossier dont le score n'est pas positif. Un malus posé dans
le score faisait donc **disparaître** M4 de la fiche — son unique critère de
rotation valait +1, et −1 le mettait à zéro. C'est exactement ce que le brief
interdit : *il descend au rang que sa densité lui donne, il ne descend pas en
dernier pour autant*.

D'où deux propriétés, et elles répondent à deux questions différentes :
`score` **ordonne** (rétrogradations comprises), `merit` décide si le dossier
**se propose** (tout sauf les rétrogradations). Une rétrogradation ne dit pas
« ne cherche pas », elle dit « ce que tu trouveras vaudra moins ».

### Le classement avant / après, sur le lot de référence

| | avant | après |
| --- | --- | --- |
| 1 | M6 (score 3) | M6 (3) |
| 2 | M1 (2) | M1 (1) — `1 seul marché servi` |
| 3 | M2 (2) | M2 (1) — `1 seul marché servi` |
| 4 | M4 (1) | M4 (0) — `1 seul marché servi` |

**L'ordre ne change pas, et il faut le dire plutôt que de régler le poids pour
qu'il change.** Sur ce lot rien de plus riche ne concourt : M6 est le seul bloc
de tennis et porte un critère plus fort, les trois blocs de football qui ont un
critère sont précisément les trois blocs étroits. Ce que le lot gagne est que le
plafond est **nommé** sur les trois dossiers — le fait que le brief a dû établir
à la main.

**Le poids vaut un cran, et c'est un choix conservateur assumé.** Deux crans
feraient basculer l'ordre de ce lot, et ce serait régler le poids sur l'exemple
qui l'a fait naître — la faute que ce projet a corrigée deux fois. Les autres
poids du module sont « le rendement mesuré de chaque piste » ; celui-ci n'a pas
encore de rendement mesuré, et il se relèvera sur des sessions qui l'auront
donné.

**Ce que la mesure n'a pas pu faire** : rejouer les fiches de l'historique. Une
fiche se construit sur la shortlist **courante**, et les sessions passées ont la
leur vide — une seule des 18 sessions archivées rend encore une fiche. Le taux de
réordonnancement n'est donc pas mesurable rétroactivement.

### Gabarit

La section BUDGET DE RECHERCHE, où la règle « la fiche ne regarde aucune cote »
est énoncée : **+71 tokens** (316 → 573 caractères). La distinction y est
explicite, comme le brief le demande.

## §9 — L'état de la journée manquait, et le docstring promettait le contraire

### Établi

La mention n'était pas conditionnée à un **montant saisi** : elle l'était à
`{% if mise.engagees %}`, donc à une mise **déjà enregistrée**. Sur la base
servie, `mises` porte **0 ligne** et `bankroll_journee` **0 ligne** ; `engagees`
vaut 0,0 et la branche `else` rendait le plafond nu.

Le docstring de `stakes.Brief` promet pourtant depuis le lot 17 que « chaque
prompt annonce ce qu'il **reste**, pas le plafond nu — sans cela, quatre rendus
dans la journée auraient chacun cru disposer du plafond entier, c'est-à-dire le
contournement par découpage que le plafond par journée existe pour fermer ». Le
service et son docstring se contredisaient, et **c'est le docstring qui avait
raison**.

### La branche disparaît

Une ligne unique, sans condition, qui porte toujours les trois nombres :

    · plafond de la journée : **20 unités**, soit 5 % de la bankroll
    · engagé aujourd'hui, tous rendus confondus : **0 sur 20** — il t'en reste **20**

Un plafond sans son état ne contraint rien — même règle que les bornes de palier
et les quotas du lot, qui se calculent au lieu de se faire recalculer de tête.

### Gabarit

**−27 tokens** (356 → 261 caractères) : la condition supprimée coûtait plus que
la ligne qu'elle gardait.

Le test lit le **prompt rendu** et non la propriété : le défaut vivait dans la
porte, et un `Brief` correct n'aurait rien montré.

## §10 — La suppression du combiné affirmait une impossibilité

### Le seuil, et ce qu'il est

`combo_solo_min_lot` vaut **9** sur la base servie, pour un défaut de **5** — c'est
un réglage utilisateur, resserré à la main. Le lot de référence porte 7 blocs,
donc sous le seuil.

### Ce que la mesure dit de l'impossibilité affirmée

**Structurellement, elle est fausse.** `safe_legs_available` sur les réglages
servis :

| lot | 3 | 4 | 5 | 6 | **7** | 8 | 9 | 10 | 11 | 12 |
| --- | --: | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| jambes sûres autorisées | 3 | 4 | 5 | 6 | **7** | 8 | 9 | 10 | 10 | 10 |

Un lot de sept autorise **sept** jambes, pas trois. Le plafond ne mord qu'à partir
de onze.

**Empiriquement, c'est serré et pas impossible.** Sélections en bande sûre à
confiance ≥ 3, par prompt et non par session — le plafond de jambes vaut par
prompt :

| Taille du lot | Prompts | Dont ≥ 3 jambes éligibles |
| --- | ---: | ---: |
| 7 blocs | 7 | **1** |
| 8 blocs | 6 | 2 |
| 9 blocs | 17 | 11 |
| **≤ 9 blocs** | **55** | **25 (45 %)** |

**Réserve, et elle est sérieuse** : ce vivier a été mesuré en régime cassé. Sans
la ligne `dossiers_ouverts`, aucune sélection ne peut dépasser le cran 1, donc
aucune jambe n'existe — `CLAUDE.md` l'écrit déjà à propos des cibles de combiné.
Les 45 % sont un **plancher**, pas une estimation.

### Le seuil est conservé, la formulation change

Il n'est pas à moi de bouger un réglage que l'utilisateur a resserré de 5 à 9, et
la mesure ne le tranche pas — elle dit seulement que la phrase qui l'accompagnait
était fausse. La section dit désormais que le combiné n'est **pas demandé** sur ce
lot, avec le seuil qui l'explique :

    **Aucun combiné n'est demandé sur ce lot.** Il porte 7 match(s), sous le
    seuil de 9 : à une sélection par match et une partie du lot qui passe, trois
    jambes indépendantes y seraient serrées. Ce n'est pas une impossibilité —
    c'est une demande qu'on ne pose pas ici.

**+36 tokens** (300 → 428 caractères). Le seuil est écrit plutôt que sous-entendu,
même règle que les quotas et les bornes de palier — une règle qu'il faut deviner
ne contraint rien.

Trois tests recopiaient la formulation. Leurs ancres suivent, et l'un d'eux gagne
l'assertion qui manquait : **la phrase d'impossibilité ne doit plus y être**.

## Le coût du lot, par modification de gabarit

| § | Passage | Avant | Après | Delta |
| --- | --- | ---: | ---: | ---: |
| §1 | consigne TENNIS de « CE QU'IL FAUT VÉRIFIER » | 129 | 128 | **−1** |
| §2 | puce « Tour » du chapitre COMMENT LIRE LES BLOCS | 63 | 162 | **+99** |
| §4 | entrée « Ecart » du même chapitre | 50 | 176 | **+126** |
| §6 | paragraphe « lignes en quart », gardé par le sport | 150 | 249 | **+99** |
| §8 | section BUDGET DE RECHERCHE | 88 | 159 | **+71** |
| §9 | table de mise, section G | 99 | 72 | **−27** |
| §10 | suppression du combiné, section D | 83 | 119 | **+36** |
| | **total gabarit** | | | **+403** |

§3, §5 et §7 ne touchent pas le gabarit — §7 remplace des littéraux par des
expressions, à longueur comparable.

### Sur le prompt de référence, régénéré

| | total | cadre | blocs | par bloc |
| --- | ---: | ---: | ---: | ---: |
| avant (prompt 167) | 22 179 | 17 613 | 4 566 | 652 |
| après | 22 776 | 18 085 | 4 691 | 670 |
| **delta** | **+597** | **+472** | **+125** | **+18** |

Les 472 tokens de cadre valent les 403 mesurés passage par passage, plus les
exemples de format générés (§7) et la ligne de motif ajoutée à trois dossiers de
la fiche (§8). Les 125 tokens de bloc sont les lignes de §1, §2 et §6 —
**18 tokens par bloc**, sur sept blocs dont un seul de tennis.

Les deux alarmes ne bougent pas : lot football **15 831 / 23 000**, lot mixte
**13 506 / 20 000**. La règle « tout ajout budgète sa propre coupe » a été écrite
quand le socle tenait à 7 tokens de son plafond ; elle ne mord plus ici, et la
mesure est écrite pour qu'on n'ait pas à la redécouvrir.

## Le bloc M6, avant et après

**Avant** — les cinq lignes que §1 à §5 touchent :

    Tour        phase non renseignee (118 joueurs vus ne forment aucun tableau)
    Ici         Sara Bejlek 14/08 bat Karolina Pliskova 6-0 6-2 | 16/08 bat Barbora Krejcikova 7-6(5) 6-4 | 18/08 bat Ekaterina Alexandrova 4-6 6-1 6-2 [releve au 19/08]
                service ici 61.8% 1re · 71.0% s/1re · 12 df (3 matchs, 212 pts)
                Madison Keys 16/08 bat Daria Snigur 4-6 6-3 6-3 | 18/08 bat Katerina Siniakova 6-1 6-3 [releve au 19/08]
                service ici 56.8% 1re · 74.7% s/1re · 9 df (2 matchs, 139 pts)
    Palmares    Madison Keys 1/8 2025, 2V-1D
    Ecart       service +0.1 pts sur la 1re balle pour Sara Bejlek | retour +5.3 pts pour Sara Bejlek · taux non ajustes du niveau d'adversaire

**Après** :

    Tour        phase non renseignee (118 joueurs vus ne forment aucun tableau)
                au moins 4 tours disputes par Sara Bejlek, 3 par Madison Keys
    Ici         Sara Bejlek 14/08 bat Karolina Pliskova 6-0 6-2 | 16/08 bat Barbora Krejcikova 7-6(5) 6-4 | 18/08 bat Ekaterina Alexandrova 4-6 6-1 6-2 [releve au 19/08]
                1 match non couvert : Aryna Sabalenka (2194)
                service ici 61.8% 1re · 71.0% s/1re · 14.8% df (3 matchs, 212 pts)
                Madison Keys 16/08 bat Daria Snigur 4-6 6-3 6-3 | 18/08 bat Katerina Siniakova 6-1 6-3 [releve au 19/08]
                1 match non couvert : Xiyu Wang (1741)
                service ici 56.8% 1re · 74.7% s/1re · 15.0% df (2 matchs, 139 pts)
    Palmares    Sara Bejlek WTA 1000 1/8 2026 (6 éditions, dur) · ici 1/8 2026 (1 édition) | Madison Keys WTA 1000 1/2 2025 (38 éditions, dur) · ici vainqueur 2019 (11 éditions)
    Bilan ici   Madison Keys 1/8 2025, 2V-1D
    Ecart       s/1re +6.1 pts pour Madison Keys | df +7.5 pts pour Sara Bejlek | retour +5.3 pts pour Sara Bejlek · taux non ajustes du niveau d'adversaire

Les cinq se lisent ensemble : la demi-finaliste a battu Sabalenka la veille — le
bloc le dit maintenant qu'il ne le dit **pas**, et nomme qui chercher — elle en
est à quatre tours contre trois, elle n'était jamais allée au-delà d'un huitième
en WTA 1000 quand son adversaire a gagné ici, et l'écart de service est de six
points sur les points gagnés et non de zéro sur la mise en jeu.

La ligne `Palmares` de la colonne « après » suppose la passe de collecte lancée
— c'est le §3, et elle a été jouée sur la copie pour l'établir.

## Ce que la mesure contredit dans ce brief

| Affirmé | Mesuré |
| --- | --- |
| §1 — nommer « les matchs **postérieurs au relevé** » | **la borne est fausse deux fois** : nulle au jour (la journée de tournoi de Sabalenka vaut le jour du relevé), 6 sur 28 à l'instant (un match commencé 30 min avant n'est pas fini). La soustraction n'a aucune borne à choisir |
| §1 — « corriger `Fraicheur` du même coup, le compte doit descendre » | **refusé, ce serait faux.** `Fraicheur` compte ce qui manque à `Forme/Usure/Profil/Marge/Niveau adv.`, arrêtées au 13/08 : les **quatre** matchs y manquent, y compris les trois dont `Ici` donne le score |
| §3 — « la construction du lot 16 est ATP seulement / la couverture WTA est vide / le tournoi n'est pas rattaché » | **les trois sont fausses**, vérifiées une par une. `player_palmares` était simplement **vide** : le module a été livré le 20/08 à 18h37 UTC et la passe qui la remplit tourne à 07h30 |
| §4 — « `Ecart` compare la grandeur **la moins discriminante** » | **c'est la plus dispersée des cinq** : médiane 4,3 points contre 3,5 sur les points gagnés, sur 174 paires. Le `+0,1` de M6 est un tirage bas, pas une propriété. Ce qui la disqualifie est ce qu'elle **mesure** — et la nuance compte, un seuil posé sur l'étalement aurait gardé la mauvaise grandeur |
| §5 — « conserver le compte brut à côté si le dénominateur est petit » | **non fait** : il faudrait un seuil de « petit » qui s'inventerait, et la parenthèse `(3 matchs, 212 pts)` borne déjà le fragment |
| §6 — « quand un marché est **entièrement** en quart, le signaler » | **jamais le cas pour l'O/U** — 0 échelle sur 271 blocs — et **toujours** le cas pour le handicap, qui ne rend qu'une ligne, sur 94 blocs sur 268. Et sur **94 sur 94**, un palier posable existait dans l'échelle et était jeté |
| §8 — « deux des trois premiers dossiers sont plafonnés d'avance » | **trois des quatre** : M4 aussi ne porte que le 1N2 |
| §8 — « pondérer le classement » | le classement du lot **ne change pas**, et il ne faut pas régler le poids pour qu'il change : rien de plus riche n'y concourt. Ce qui change est que le plafond est **nommé** |
| §10 — « trois jambes indépendantes ne peuvent pas en sortir » (texte du gabarit) | **faux** : un lot de sept en autorise sept, le plafond ne mord qu'à onze. Empiriquement 45 % des prompts de ≤ 9 blocs en ont produit trois — et ce chiffre est un plancher, mesuré en régime cassé |

### Trois défauts que le brief ne demandait pas et que la lecture a trouvés

1. **`Ici` rendait le tournoi de la semaine passée** — 14 fragments sur 223, dont
   un bloc de Cincinnati servant quatre matchs du Canadien et un de Washington.
   Le mode sur la fenêtre d'édition ne sait pas écarter la fin du tournoi
   précédent. **§1.**
2. **Le handicap posable était jeté** — 94 fois sur 94. Le bloc montrait la seule
   ligne impossible à poser. **§6.**
3. **Le palmarès ne dépassait jamais douze joueurs** — `BATCH` borne le temps de
   mur des timelines, qui coûtent 4 à 6 appels par rencontre, et le palmarès en
   héritait pour 3 appels par joueur. Médiane 32 joueurs de tennis par journée de
   board, maximum 99, et 12 ne couvre la journée que **4 fois sur 18**. **§3.**

### Une règle de revue que ce lot ajoute

**Une mesure qui contredit une prémisse ne dispense pas d'expliquer la
prémisse.** Le §4 en est le cas net : le brief a raison de vouloir changer de
grandeur et tort sur la raison, et si l'on s'était contenté de constater qu'il a
raison, on aurait pu poser un seuil sur l'étalement — qui aurait gardé la
grandeur qu'on voulait retirer. La prémisse fausse et la conclusion juste
coexistent, et c'est la prémisse qui décide de l'implémentation.

## Récapitulatif du lot 18

**Dix points, dix commits, schéma inchangé à 69** — aucune migration, donc aucune
sauvegarde requise. Toutes les mesures portent sur une copie de la base servie
(`VACUUM INTO`), et la base servie n'a pas été touchée : mtime inchangé,
`player_palmares` toujours à 0, `picks` à 327.

| § | Ce qui a changé | Ce que ça coûte |
| --- | --- | --- |
| 1 | `Ici` nomme les matchs dont la source ne dit pas l'issue ; le tournoi se corrobore | −1 token, +1 ligne sur 7 blocs / 195 |
| 2 | `Tour` dit le nombre de tours établis quand la phase est inconnue | +99 tokens, +1 ligne sur 70 blocs / 195 |
| 3 | Le palmarès n'hérite plus de la borne des timelines | 0 token, ~96 appels/jour (2 % du quota mensuel) |
| 4 | `Ecart` confronte l'efficacité, et se tait sous le bruit | +126 tokens |
| 5 | Les doubles fautes du tournoi passent en taux | 0 token |
| 6 | Les lignes en quart portent `†` ; le handicap posable est rendu | +99 tokens, +1 ligne sur 35 % des blocs football |
| 7 | Les exemples de format se bâtissent sur le lot | ≈ 0 |
| 8 | La fiche de priorité pèse la surface de marché | +71 tokens |
| 9 | L'état de la journée est toujours annoncé | **−27 tokens** |
| 10 | Le combiné n'est plus déclaré impossible | +36 tokens |

**Ce que ce lot corrige et que personne n'avait demandé** : trois défauts,
chacun de la forme caractéristique du projet — l'échec et le cas ordinaire
rendaient la même chose. `Ici` servait le tournoi de la semaine passée sur 14
fragments ; le rendu jetait un handicap posable 94 fois sur 94 ; le palmarès ne
dépassait jamais douze joueurs par jour quand la médiane est de trente-deux.

**Ce qui reste ouvert, et pourquoi ça ne se ferme pas ici** :

- le **poids** de la rétrogradation par surface de marché (§8) : un cran, faute
  de rendement mesuré. Il se relèvera sur des sessions qui l'auront donné ;
- le **seuil** `combo_solo_min_lot` (§10) : réglage utilisateur à 9 pour un
  défaut de 5, et le vivier qui le justifierait a été mesuré en régime cassé.
  `CLAUDE.md` a déjà daté cette décision d'attente pour les cibles de combiné —
  elle vaut aussi pour ce seuil ;
- la **borne de `BATCH`** pour les timelines (§3) : elle reste à 12, et c'est
  juste — une timeline coûte quatre à six appels par rencontre. Seul le palmarès
  en sort.

## §1 bis — Le tournoi croisé : portée réelle, et la garde qui manquait

### Trois réponses, mesurées sur les corps archivés

**Depuis quand.** La ligne `Ici` est arrivée par `319f5f6` le **19/08 à 18h30
locales**, derrière son drapeau, et a été activée par `0239664` le **19/08 à
21h20**. Le défaut est né avec elle et a été corrigé le 20/08 : **environ vingt-six
heures d'exposition en code**.

**Combien de blocs rendus le portent : zéro.** Sur les 167 prompts archivés,
**deux seulement** contiennent une ligne `Ici` — les prompts **166 et 167**, tous
deux de la session 18 du 20/08 — pour **5 blocs de tennis et 10 fragments de
joueur**. Passés au critère de corroboration, **22 matchs sur 22 sont corroborés
par nos propres scans** : aucun fragment servi ne décrit un autre tournoi.

**Aucune sélection ne repose dessus.** Trois sélections portent sur ces blocs —
`pick 337` Anisimova +1.5 Hand. jeux (gagnée), `pick 336` Over 23.5 Jeux O/U,
`pick 338` Tirante Vainqueur — et les trois reposent sur des fragments justes.
**Rien à signaler dans l'historique.**

### Je dois corriger ce que j'ai écrit hier

Les « **14 fragments sur 223** » du §1 ne sont pas un compte de blocs servis :
c'est le résultat d'un **re-rendu** des 195 blocs de tennis archivés avec le code
et les charges utiles d'aujourd'hui — donc des blocs hypothétiques, dont la
plupart datent d'avant l'activation de la ligne. C'est une mesure de **surface
latente**, pas d'exposition.

Le chiffre reste juste comme mesure du défaut ; la phrase « elle a donc été fausse
pendant plusieurs sessions » ne l'est pas. La ligne n'a vécu qu'une soirée, sur
un tournoi où tous les joueurs avaient déjà des matchs corroborables. **La
distinction entre ce qu'un défaut aurait pu produire et ce qu'il a produit doit
être portée par le chiffre**, et le mien ne l'était pas.

### La garde de corroboration, branchée sur la table du lot 17

Les deux critères existants se lisent sur **nos scans** ; celui-ci se lit sur le
**nom du tournoi** que la source porte dans chaque match — `Cincinnati Open -
Cincinnati`, `National Bank Open - Toronto` — comparé à
`profile_tournament_names`, la table vérifiée à la main du lot 17. Elle existait
et n'était branchée que sur le palmarès.

**Cumulatives et non alternatives** : la corroboration par les scans peut tomber
sur un joueur ayant croisé le même adversaire dans les deux tournois de la
quinzaine ; le nom du tournoi ne le peut pas.

**Une compétition non rattachée rend la garde muette, jamais négative** : un
ensemble déclaré vide n'affirme rien — même règle que la moitié « ici » du
palmarès, qui se tait plutôt que d'écrire « jamais joué ».

Rejeu sur les 195 blocs : **223 identifiants corroborés justes, 0 faux**, et
**146 blocs (75 %) portent toujours une ligne** — la garde ne coûte aucune
couverture.

Effet de bord sur les bancs : les fixtures nommaient le tournoi « Tournoi », un
libellé de fantaisie qui fait désormais taire la ligne. Elles portent le nom
déclaré par la migration 069 pour leur compétition — quinze tests l'ont appris en
cassant, ce qui est le comportement voulu.

---

# LOT 19 — les réglages, et le régime qui n'arrivera pas tout seul

Brief du 21/08/2026. Toutes les mesures portent sur une copie de la base servie
(`VACUUM INTO ~/lot19/copie.db`, 21/08 00:10), jamais sur `data/myassistantbet.db`.

## §1 — Les consignes permanentes : la porte dérobée, et l'état réel du champ

### Le mécanisme est réel, et il est déjà branché

`prompt.PREFERENCE_NOTES` (`session_notes`) est recopié en tête de chaque prompt,
sous `## CONSIGNES PERMANENTES`, avec cette phrase :

> Elles priment sur tes habitudes et sur les préférences générales de ce prompt.
> Elles ne priment **jamais** sur les interdits ci-dessus, ni sur les cotes des blocs.

Rien n'y interdit une règle tirée de `/stats`. Et le gabarit retient les taux
par palier et par confiance dans sa branche `feedback.suspended`, en disant
pourquoi — « les sélections que tu produis ensuite cessent d'être indépendantes
de ce qui les mesure, et une catégorie annoncée faible cesse d'être produite —
donc cesse d'être mesurable ». Une consigne permanente contourne ce dispositif
sans le violer : le texte du gabarit n'est pas touché, c'est le lecteur qui est
contaminé un cran en amont. **Même forme que la règle du lot 17 sur
`odds_history`** — « la contamination passe par le lecteur, pas par le texte ».

### Ce que la mesure contredit dans le brief : le champ n'est pas vide

> « Le champ est aujourd'hui vide, avec un exemple en filigrane. »

**Faux.** `preferences.session_notes` porte **1 103 caractères**, enregistrés le
**20/08/2026 à 22:06:52 UTC** — soit sept minutes après le dernier prompt de la
session 18. Le contenu est celui que le §1b propose, à trois différences près,
toutes de l'utilisateur :

| Le brief propose | Ce qui est en base |
| --- | --- |
| « Sur toute sélection portant « (ref.) » … » | + « C'est le cas de la quasi-totalité de mes sélections hors 1N2. » |
| « … si l'angle survit à un report de deux heures. » | + « Ce n'est pas une hypothèse : deux blocs d'une session réelle étaient faux de deux à trois heures. » |
| §1c : « L'utilisateur ajoutera **probablement** une ligne de marchés qu'il ne joue jamais » | déjà là : « Je ne joue jamais : cartons, corners » |

**Aucun prompt archivé ne les porte** : 0 sur 170. Le dernier prompt (170) date
du 20/08 à 21:59, la saisie de 22:06. Le premier prompt qui les portera est
celui de la prochaine session — et c'est aussi le premier qui pourra être relu
pour vérifier qu'elles ne rendent aucune section insatisfiable.

Conséquence pour le §1b : **il n'y a rien à appliquer**, et il ne fallait de
toute façon pas l'appliquer. La formulation reste écrite ci-dessous, telle que
proposée, pour que la décision soit datée.

### §1a — La formulation de l'avertissement

Rendue sous le champ, sur la page Réglages. **Coût en tokens de prompt : zéro** —
c'est une surface de réglage, elle n'entre dans aucun prompt.

> **Aucune règle tirée de la page Statistiques.** Ni un marché, ni un palier, ni
> un cran, ni un type d'angle écarté sur la foi d'un taux. Le prompt retient
> délibérément ces taux, et il dit pourquoi ; une consigne les y ferait entrer
> par la porte de service.
>
> Ce qui se passe alors est sans retour : une catégorie qu'on cesse de produire
> cesse d'être mesurable, et plus rien ne dira jamais si le chiffre qui l'a fait
> écarter décrivait quelque chose ou tirait sur cinquante sélections.
>
> Ce qui a sa place ici contraint le **placement et la forme** — où tu poses, ce
> qu'une colonne doit nommer, ce que tu ne joues jamais par principe. Aucune de
> ces consignes ne dépend d'un résultat, et c'est le test à leur appliquer.
>
> Rien ne le vérifie : l'application ne peut pas lire l'intention derrière une
> phrase, et un contrôle automatique refuserait des consignes légitimes.

Trois choix, à connaître :

- **le test est donné, pas seulement l'interdit** — « aucune de ces consignes ne
  dépend d'un résultat » se vérifie sur une phrase qu'on vient d'écrire, quand
  « ne tire rien de la page Statistiques » demande de se souvenir d'où vient une
  idée. Les cinq consignes en base passent le test, la sixième (« cartons,
  corners ») aussi : c'est un principe, pas un taux ;
- **l'irréversibilité est nommée**, parce que c'est elle qui distingue cette
  contamination d'une simple erreur d'analyse. Une mauvaise sélection se solde ;
  une catégorie qui cesse d'être produite ne laisse pas de trace ;
- **l'aveu qu'aucun contrôle ne garde la règle** est dans l'avertissement. Un
  garde-fou qu'on croit automatique et qui ne l'est pas est pire que pas de
  garde-fou : c'est le défaut que ce dépôt a payé sur `mise_unite_bp` avant que
  l'échéance soit rendue en clair.

### §1b — La valeur de départ, telle que proposée, non appliquée

Elle est **déjà en base**, saisie par l'utilisateur le 20/08 (voir plus haut).
Le texte proposé par le brief est recopié ici pour que la proposition soit datée
et que l'écart avec ce qui est servi se lise :

```
Betclic est le seul bookmaker où je pose. Une sélection que je ne peux pas y
placer ne me sert à rien : si tu doutes qu'un marché y existe, dis-le en
section F plutôt que de le sélectionner.

Sur toute sélection portant « (ref.) », écris sous le tableau C le libellé
exact à chercher chez Betclic — nom du marché et ligne — pour que je la
retrouve sans reconstituer ton raisonnement.

La colonne « Ce qui la tue » doit nommer une chose vérifiable AVANT le coup
d'envoi, pas une explication d'après-match. Si le facteur n'est vérifiable
qu'après, dis-le explicitement.

Je pose depuis mon téléphone, souvent moins d'une heure avant le coup d'envoi.
Une sélection qui demande trois vérifications préalables n'est pas posable.

Sur un bloc tennis dont l'heure estimée dépasse 01h00, écris en une ligne si
l'angle survit à un report de deux heures.
```

**La propriété commune, et c'est elle le livrable** : aucune des cinq ne dépend
d'un résultat. Elles contraignent le **placement** (Betclic, le téléphone,
l'heure), la **forme** (le libellé à chercher, ce que « Ce qui la tue » doit
nommer) et le **périmètre** (les marchés jamais joués). Aucune ne dit à
l'analyse quoi conclure, aucune n'écarte une catégorie sur la foi d'un taux.
C'est exactement le test que l'avertissement du §1a énonce.

Deux réserves qui ne s'annulent pas :

- « si tu doutes qu'un marché y existe, dis-le en section F » **consomme du
  budget de section F**, plafonnée à trois lignes et qui doit déjà porter les
  marchés manquants. La consigne est juste et son coût est réel ;
- la sixième ligne saisie — « Je ne joue jamais : cartons, corners » — rend
  `corners` et `cartons` inatteignables. Ce sont deux familles rangées dans
  `autre`, qui portent **0 sélection sur 327** : la consigne ne retire rien qui
  existait, elle rend explicite un fait déjà constaté.

### §1c — Une consigne qui rend une section impossible

Le gabarit porte déjà la phrase, et elle est juste :

> Si l'une d'elles rend une section impossible à remplir, dis-le en une ligne
> plutôt que de la contourner.

Elle n'avait **aucun test**. Trois en sont ajoutés (`tests/test_consignes.py`) :
la phrase est présente ; le bloc entier disparaît quand le champ est vide ; et
un prompt rendu avec des consignes non vides les porte **telles quelles**, à
l'endroit prévu, sans échappement ni troncature — ce dernier point étant le
seul qui échouerait si l'injection cassait, et il n'existait nulle part.

## §2a — Un palier présent et interdit par le quota

### Le fait, reproduit sur les prompts archivés

Le brief le décrit sur un lot de 2 matchs. Il est dans la base, et c'est le
**dernier prompt rendu** — `prompts.id = 170`, session 18, 20/08 à 21:59 :

```
Cote max du lot : 3.80 (M2 · Vainqueur Thiago Agustin Tirante).
Paliers présents dans ce lot : SAFE, FUN, ULTRA FUN, GIGA FUN.
Quotas **de ce lot** : 0-1 🟢, 0-1 🔵, 0-1 🟠, 0-0 🔴.
```

GIGA FUN est déclaré présent — 3.80 tombe bien dans `[3.60 ; 8.00)` — et son
quota vaut zéro. Le paragraphe des paliers vides ordonne alors de commenter un
vide dont la cause n'a rien à voir avec la recherche.

### Fréquence : rare, et concentrée exactement là où les lots sont courts

Balayage des **170 prompts archivés**, sur les deux lignes rendues (donc sur les
réglages du jour, pas sur ceux d'aujourd'hui). Les deux lignes existent depuis le
10/08 : **86 prompts** les portent tous les deux.

| Mesure | Valeur |
| --- | ---: |
| prompts portant les deux lignes | 86 |
| dont **un palier présent à quota nul** | **6** (7 %) |

| Prompt | Session | Lot | Palier présent à 0 |
| ---: | ---: | ---: | --- |
| 91 | 9 | 1 | GIGA+ |
| 92 | 9 | 3 | GIGA+ |
| 112 | 11 | 3 | GIGA+ |
| 153 | 16 | 4 | GIGA+ |
| 154 | 16 | 4 | GIGA+ |
| 170 | 18 | 2 | GIGA FUN |

**7 % est un chiffre trompeur, et c'est le second temps de la mesure.** Le défaut
ne peut se produire que sur un lot de **4 matchs ou moins** — au-delà, aucun
quota réglé ne tombe à zéro. Sur les 22 prompts du régime récent (depuis le
17/08), **4 lots de 4 ou moins**, dont **3 ont déclenché** le défaut. Rapporté à
sa population, il touche **trois petits lots sur quatre**.

### La cause est le prorata seul, et le budget n'y est pour rien

Table complète, au réglage servi (6-5-3-2-1, budget 10) :

| Lot | SAFE | FUN | ULTRA FUN | GIGA FUN | GIGA+ |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | **0** | **0** | **0** |
| 2 | 1 | 1 | 1 | **0** | **0** |
| 3 | 2 | 2 | 1 | 1 | **0** |
| 4 | 2 | 2 | 1 | 1 | **0** |
| 5 | 3 | 3 | 2 | 1 | 1 |
| 10 | 6 | 5 | 3 | 2 | 1 |

Les valeurs sont **identiques avant et après `research_capped`** : le budget de
recherche ne retire rien à aucune taille de lot, ce que la mesure du 14/08 disait
déjà et que ce balayage confirme sur les 170 prompts — **0 prompt sur 170** où le
budget déplace une borne. La seule cause est l'arrondi du prorata :
`2 × 2/10 = 0.4`, qui tombe à 0.

### La correction retenue, et pourquoi l'autre est fausse

Le brief propose deux corrections. **La seconde produirait une contradiction
interne au prompt, et c'est ce qui tranche.**

Retirer un palier de `present` obligerait à le retirer aussi d'`absent`, dont la
ligne affirme « aucune cote du lot n'y tombe » — faux d'une cote à 3.80. Il
n'apparaîtrait donc plus nulle part en tête de section C. Mais la ligne
`Paliers` qui ferme **chaque bloc** est calculée par `reachable()` sur les cotes
du bloc, et elle continuerait de le nommer : le bloc M2 du prompt 170
annoncerait `SAFE, FUN, ULTRA FUN, GIGA FUN` sous une section qui n'en dit rien.
Deux sorties du même calcul qui ne se parlent plus — le défaut que
`tennis_round.truncated()` avait déjà coûté.

**Retenu : un palier que les cotes du lot atteignent garde un quota d'au moins
un.** C'est exactement l'argument qui a donné son plancher à `QUOTA_FLOOR_TIERS`
— « sinon la réduction interdirait de rendre quoi que ce soit » — appliqué à un
palier haut, et il y est **plus strict** : les deux plus sûrs ont leur plancher
sans condition, celui-ci ne l'a que si une cote y tombe vraiment.

Ce que ça ne change pas :

- **le total reste borné par le lot** — « une seule sélection par match, donc le
  total ne peut pas dépasser N » est déjà écrit et n'a pas bougé. Sur un lot de
  2, les quotas passent de `0-1, 0-1, 0-1, 0-0` à `0-1, 0-1, 0-1, 0-1`, et le
  total possible reste **2** ;
- **l'exigence de fait daté ne bouge pas.** Un GIGA FUN reste soumis en section C
  à un fait nommé et daté de la section A. Le plancher rend le palier
  *proposable*, il ne rend rien *justifiable* ;
- **le budget de recherche garde son veto.** Il s'applique après le plancher :
  sur un lot de 1, un seul dossier est ouvrable, donc un seul palier haut peut
  être justifié et les autres retombent à zéro. Un zéro **causé par le budget**
  est un zéro expliqué — le paragraphe qui suit les quotas le dit déjà en toutes
  lettres — quand un zéro causé par le prorata n'avait aucune cause énonçable.

### C-bis : l'asymétrie devient voulue au lieu d'être subie

C-bis propose « au plus une sélection par palier haut, uniquement parmi les
paliers que ce lot propose », et `tier_scope.high` dérive de `present`. Avant la
correction, une cote à 3.80 était donc **interdite en C** (quota 0) et
**autorisée en C-bis** — sans qu'aucune décision ait produit cet écart.

Après, les deux cas se séparent proprement :

- **zéro par prorata** : il n'existe plus. C et C-bis proposent le même
  ensemble ;
- **zéro par budget** : C l'interdit faute de dossier ouvrable, C-bis l'autorise
  — et c'est précisément sa raison d'être, « le seul endroit où l'exigence d'un
  fait daté tombe ». L'asymétrie subsistante est celle que la section existe
  pour porter.

Une phrase l'écrit dans C-bis plutôt que de la laisser déduire, gardée par le
cas : elle ne paraît que si un palier haut est présent et sans quota.

### Le défaut trouvé sous celui-ci : un palier absent du lot mangeait le budget

Trouvé en écrivant le test du plancher, qui refusait de passer pour une raison
qui n'était pas la sienne. `research_capped` décrémentait `dossiers` pour
**tous** les paliers hauts, y compris ceux qu'aucune cote du lot n'atteint.

Or un palier absent ne peut recevoir aucune sélection : le rendu le retire de la
ligne des quotas, `TierScope` le déclare absent, et le gabarit ordonne de ne pas
le commenter. Il prenait pourtant sa part d'un budget fini, et affamait les
paliers réellement offerts.

Mesuré sur les bandes du seed, lot de 2 (donc 2 dossiers) :

| Paliers hauts offerts par le lot | Quota du plus haut |
| --- | ---: |
| lui seul | **1** |
| les trois | 0 |

Le zéro se lisait « plus de dossier disponible » alors que la cause était « un
palier hors du lot a pris la place ». **Encore une sortie identique pour deux
causes qui n'appellent pas le même comportement** — la première ne se répare
pas, la seconde était un bug.

Un palier non offert garde sa borne de prorata plutôt que zéro : elle n'est lue
nulle part, et la mettre à zéro ferait passer une absence de cote pour une
absence de dossier.

### Rendu avant / après, sur le lot du prompt 170 (réglages servis)

```
avant   Paliers présents dans ce lot : SAFE, FUN, ULTRA FUN, GIGA FUN.
        Quotas **de ce lot** : 0-1 🟢, 0-1 🔵, 0-1 🟠, 0-0 🔴.

après   Paliers présents dans ce lot : SAFE, FUN, ULTRA FUN, GIGA FUN.
        Quotas **de ce lot** : 0-1 🟢, 0-1 🔵, 0-1 🟠, 0-1 🔴.
```

### Coût en tokens

| Ce qui change | Coût |
| --- | ---: |
| plancher sur un palier offert (`research_capped`) | **0** — aucun texte |
| budget non consommé par un palier absent | **0** — aucun texte |
| phrase C-bis nommant le palier sans dossier | **+45 tokens**, et seulement sur un lot où un palier haut offert reste à zéro |

Le gabarit grossit de 418 caractères, dont **373 de garde** (`{% if %}`,
accords) qui ne sont jamais rendus. Sur les 22 prompts du régime récent, la
phrase se serait payée sur **1** — le prompt 170 n'en avait pas besoin après
correction, et seuls les lots d'un ou deux matchs offrant trois paliers hauts la
déclenchent.

### Six tests

Cinq nouveaux (`test_prompt.py`) : le plancher sur un palier offert ; le
plancher **absent** sur un palier non offert ; le veto du budget conservé ; la
phrase C-bis rendue quand elle décrit quelque chose et **tue** sinon ; un palier
absent ne consomme aucun dossier.

Un existant réaligné — `test_paliers_injectes_dans_le_prompt` recopiait
`1-1 🟢, 1-1 🔵, 0-0 🟠, 0-0 🔴, 0-0 💥`, c'est-à-dire la sortie du jour sous les
bandes du seed. Il énonce désormais la propriété : les deux paliers sûrs gardent
leur plancher, la borne réglée n'apparaît pas, et **un seul** palier haut est
ouvert sur un lot d'un match — celui que le dossier unique permet de justifier.

## §2b — Le marché « cotes » : ce que la mesure dit, et le vrai défaut de la page

### L'hypothèse du brief est fausse, et il l'avait signalée comme telle

> « C'est presque certainement un artefact de lecture : les blocs portent une
> ligne d'en-tête `MARCHES (Betclic, releve 23:59 — …)`, et un découpage trop
> large la prend pour un libellé. »

**Non.** `cotes` est le libellé du marché `outright` dans
`render.MARKET_ORDER` — « saisie manuelle : marché libre, sans forme imposée » —
présent aussi dans l'ordre du tennis, et rangé dans `autre` par `FAMILY_SEED` et
la migration 027. C'est une **entrée de catalogue**, décidée à la main.

Quatre mesures qui ferment la question :

| Question | Mesure |
| --- | --- |
| combien de sélections portent le libellé `cotes` ? | **0 sur 327**, toutes casses confondues |
| le parsing peut-il le fabriquer ? | non — les 19 libellés distincts de `picks` sont tous des noms de marché |
| un en-tête de bloc a-t-il jamais atterri dans `picks.market` ? | aucun |
| le marché existe-t-il vraiment ? | **oui : 11 lignes dans `odds`**, clé `outright`, bookmaker `manual` |

Le quatrième renverse la lecture qu'on aurait pu en tirer : ce n'est pas une
entrée morte du catalogue, c'est un marché **relevé** qui n'a jamais produit de
sélection. `outright` est ce que rend une saisie manuelle libre.

### Le vrai défaut est sur la page, et c'est le motif du projet

`_families.html` rendait `—` dans la colonne « Sélections » sur **toutes** les
lignes classées. Donc :

```
vainqueur   —    issue        (76 sélections)
cotes       —    autre        (0 sélection)
```

Les deux se lisent à l'identique. `CLAUDE.md` affirmait que « c'est ce tiret qui
distingue une entrée seedée d'une entrée vue en base » : **c'est faux**, le tiret
est rendu inconditionnellement. La page ne permettait donc pas de répondre à la
seule question qu'on lui pose devant un libellé qui surprend — *est-ce que
quelque chose est rangé là-dedans ?* — et c'est exactement ce qui a fait
soupçonner un artefact.

Sortie identique pour « jamais employé » et pour « employé soixante-seize fois ».
Le compte réel est désormais rendu, et **« aucune » s'écrit en toutes lettres**
plutôt qu'en zéro : c'est un fait sur le libellé — le catalogue seede des marchés
que le bloc sait écrire et qu'on n'a jamais joués — et non une case restée vide.

Le compte se fait sur la **clé de famille**, comme le classement lui-même :
`O/U 2.5` et `O/U 3.5` comptent tous deux sous `o u`, sans quoi la colonne dirait
zéro sur un marché que la table groupe bel et bien.

### Le recensement demandé : trois libellés hors catalogue, et un quatrième ailleurs

**Sur `picks`** — 3 libellés, 5 sélections, aucune depuis le 12/08 :

| Libellé | Sélections | Ce que le bloc écrit |
| --- | ---: | --- |
| `Double chance` | 3 | `DC` |
| `Nombre total de buts (t. rég)` | 1 | `O/U` |
| `Les 2 équipes marquent (t. rég)` | 1 | `BTTS` |

Les trois sont des libellés **de bookmaker**, tapés à la main aux sessions 2 à 9
au lieu du vocabulaire du bloc. Ils sont classés en famille — le seed les porte —
donc ils ne manquent à aucun regroupement ; c'est la clé fine qui reste NULL,
comportement documenté et voulu (« un libellé hors vocabulaire reste NULL et se
réclame »). Aucun depuis neuf jours : la consigne de recopier le libellé du bloc
est suivie.

**Sur `market_families`** — 14 clés sur 29 n'ont jamais servi, dont `cotes`. Une
seule sort du catalogue de rendu : `total buts`, seedée sans qu'aucun
`MARKET_ORDER` ne la produise. Elle ne coûte rien et ne masque rien.

**Sur `odds`, et personne ne le signale** : `2jrs 1 set`, **20 lignes**,
bookmaker `manual`, événements 2 à 11, saisies le 04/08. Ce n'est ni un marché du
catalogue ni une famille : c'est une saisie libre, rendue par le repli générique
de `render.py`. `unclassified()` ne la voit pas, et **c'est juste** — il ne lit
que `picks`, une famille étant un regroupement de *sélections*, et cette clé n'en
porte aucune. La signaler dans « à classer » ferait réclamer une décision sur un
objet qu'aucun taux ne compte.

### Ce que ça coûte

Zéro token de prompt : la page Réglages n'entre dans aucun prompt. Une requête de
plus au rendu de `/settings` — un `SELECT market, result FROM picks`, déjà fait
par `unclassified()` juste à côté.

## §2c — Deux paramètres « qui ne se déclenchent jamais » : un seul l'est

### `combo_min_lot` = 20 : la branche s'est déclenchée quatre fois

> « Les lots font 2 à 12 matchs. Cette branche ne s'est jamais déclenchée et ne
> se déclenchera pas au régime actuel. »

**Les lots ne font pas 2 à 12.** Distribution des 170 prompts archivés : de 0 à
**37 blocs**, dont 15 prompts à 20 blocs ou plus. Le réglage à 20 date du
**10/08 à 23:39** ; depuis, 85 prompts ont été rendus, et **4 ont déclenché la
branche des deux combinés** :

| Prompt | Session | Lot | Date |
| ---: | ---: | ---: | --- |
| 97 | 9 | 21 | 12/08 |
| 111 | 11 | 28 | 14/08 |
| 118 | 11 | 26 | 14/08 |
| 141 | 13 | 20 | 15/08 |

Vérifié sur les corps : les quatre portent bien « combiné solide » **et**
« combiné frisson ». Le seuil n'est donc pas inerte — il est **inactif depuis le
15/08**, ce qui n'est pas la même chose.

La seconde moitié du brief est juste : sur les 22 prompts du régime récent
(17/08 → 20/08), lots de **2 à 10**, aucun n'atteint 20. Trois sessions, six
jours.

**Proposition — ne rien changer, et surtout pas maintenant.** Le seuil vaut 20
pour un défaut de 6, c'est un réglage resserré à la main, et la mesure qui le
justifierait est celle du vivier de jambes — mesurée en **régime cassé**, sans la
ligne `dossiers_ouverts`, donc avec toutes les sélections au cran 1. `CLAUDE.md`
a déjà daté cette décision d'attente pour les cibles de combiné et pour
`combo_solo_min_lot` ; elle vaut identiquement ici. La différence entre « 4 fois
sur 85 » et « jamais » suffit à ne pas le retirer : une branche qui a servi
quatre fois en dix jours sert encore le jour où une soirée de coupe d'Europe
revient.

### `recherche_dossiers` = 10 : trois usages, et le brief se trompe de raison

> « Mesuré au lot 9 : il ne borne plus rien, les lots étant plus petits que le
> budget. Sa description lui prête deux usages secondaires — borner les paliers
> hauts et le nombre de jambes d'un combiné. Vérifie que ces deux-là tiennent
> toujours. »

Les trois usages, mesurés sur les 170 prompts, au réglage servi et à son
prédécesseur :

| Usage | budget 10 | budget 7 (le réglage d'avant le 17/08) |
| --- | ---: | ---: |
| `min(budget, lot)` annoncé dans le prompt | **47 / 170** | 92 / 170 |
| `safe_legs_available` — jambes du combiné long | **47 / 170** | 92 / 170 |
| `research_capped` — bornes des paliers hauts | **0 / 170** | 0 / 170 |

**La conclusion du brief est juste, sa raison ne l'est pas.** Le paramètre n'est
pas structurellement inerte : il a borné le prompt sur **47 prompts sur 170**, et
il le fait sur tout lot de 11 blocs ou plus — `safe_legs_available` passe alors
de 11 jambes à 10. Ce qui l'a rendu inerte est la **conjonction de deux
mouvements** : le passage de 7 à 10 le 17/08, et des lots retombés à 10 blocs au
plus depuis. Sur les 22 prompts du régime récent, il ne borne rien ; au réglage
précédent, il aurait borné **8** d'entre eux.

Le troisième usage, en revanche, est **mort au sens fort**, et c'était déjà
documenté : les trois paliers hauts totalisent 6 places quand le budget en ouvre
10, donc `research_capped` ne peut pas mordre. Il ne mordait pas davantage à 7.
La porte ouverte le 14/08 reste ouverte, et ce lot y ajoute une raison de plus de
ne pas s'y fier : depuis le §2a, un palier haut offert prend au moins une place,
donc la somme des paliers hauts **ne peut que monter**.

**Proposition — abaisser à 7, ou ne rien changer.** Deux lectures, et la seconde
l'emporte de peu :

- **abaisser** ramènerait le paramètre à un rôle actif : à 7, il bornerait 8 des
  22 prompts récents, et le nombre de dossiers annoncé cesserait d'être une
  simple recopie de la taille du lot. Mais le passage de 7 à 10 le 17/08 était
  une **décision datée et argumentée** — « effet assumé et voulu : le relever
  desserre mécaniquement ULTRA FUN, GIGA FUN et GIGA+ » — et rien de ce qui la
  fondait n'a été mesuré à nouveau ;
- **ne rien changer** : le paramètre est un **plafond**, et un plafond qui ne
  mord pas fait son travail. `min(budget, lot)` reste calculé et annoncé, donc
  aucun nombre fantôme n'entre dans le prompt.

Ce n'est donc pas un paramètre inerte : c'est un plafond dimensionné au-dessus du
régime courant, et le régime courant date de six jours. **Rien n'est retiré.**

### Ce qu'un paramètre réellement inerte coûterait, et pourquoi la question est bien posée

Le brief a raison sur le principe — « un réglage inerte est du coût fixe dans un
cadre qui pèse déjà 53 % d'un prompt médian ». Mais aucun des deux ne coûte de
token : `combo_min_lot` est un `{% if %}` qui **économise** du texte quand il ne
passe pas, et `recherche_dossiers` alimente un nombre qui serait écrit de toute
façon. Le coût d'un réglage inerte est ici un coût **d'écran** — une ligne de
plus à lire dans la table des seuils — et deux lignes ne justifient pas de
fermer une porte que la mesure dit encore utilisable.

## §3 — La bascule n'arrivera pas toute seule, et c'est le fait central du lot

### La prémisse, et ce que le code dit

> « Six journées d'analyse et le gabarit basculera sur la branche
> `feedback.enough` […] Cette bascule se produira **sans intervention humaine**,
> le jour où le dixième jour d'analyse tombe. »

**Faux.** `history.FEEDBACK_SUSPENDED = True` est une constante, et
`Feedback.enough` s'écrit :

```python
return not self.suspended and self.settled >= self.minimum and self.days >= self.minimum_days
```

La suspension prime sur les deux seuils. Le gabarit rend alors la branche
`feedback.suspended` — « Taux par palier et par confiance : **retenus
volontairement** » — celle-là même que le prompt 170 porte encore hier soir.

Ce n'est pas un oubli : son commentaire dit **une constante et non un réglage**,
« un seuil se baisse par inadvertance ; le garde-fou d'origine était justement un
couple de seuils, et il a cédé sans que personne le décide. Rouvrir le bloc
demande donc de modifier le code. »

Le dixième jour d'analyse ne changera donc **rien**. La bascule demande deux
gestes distincts : atteindre le recul, **et** retourner la constante.

### Le défaut latent que ça révèle, et il fire exactement ce jour-là

`Feedback.missing_line` compose la liste de ce qui manque puis la joint. Recul
atteint **sous suspension**, la liste est vide :

```
>>> Feedback(settled=80, days=12, minimum=40, minimum_days=10, suspended=True).missing_line
'Il manque . Les taux ne sont pas transmis au prompt.'
```

Une phrase cassée, rendue sur la page Réglages sous le titre « Recul actuel ». Et
elle ne peut apparaître qu'au moment précis où le §3 se produit : les deux seuils
franchis, la suspension encore posée. **Sixième forme du défaut caractéristique
du projet, et la première sur une phrase** — jusque-là c'était toujours une
valeur, jamais une syntaxe.

La page dit par ailleurs, au-dessus, que « les taux attendent le recul réglé plus
bas dans Seuils », ce qui est faux depuis que la suspension existe : ils
attendent le recul **et** une décision de code. Le compteur promet une
transmission qui n'aura pas lieu — exactement ce que §3c demande de rendre
visible, sauf que le nombre à afficher n'est pas celui que le brief croit.

### §3a — L'entrée de journal reste juste, et devient plus juste

Le brief la justifie par « cette bascule se produira sans intervention
humaine ». La justification tombe, **le besoin est plus fort** : la bascule
dépend maintenant de deux choses dont une est un déploiement, et la **date
d'activation** d'un changement de code n'est pas sa date de livraison. C'est
exactement ce que `changelog_mesure` existe pour porter — « une entrée par
changement livré, **à sa date d'activation** ».

Le point d'écriture est `save_prompt`, qui archive déjà `feedback_active` —
`retour.enough`, donc vrai seulement quand les taux partent vraiment. Écrire à ce
moment-là date la bascule au **premier prompt qui transmet**, jamais au jour où
la constante a été retournée ni au jour où le seuil a été franchi.

- **portée `gabarit`** : ce qui bouge est ce que le modèle reçoit ;
- **une fois et une seule**, gardée par une lecture de `changelog_mesure` sur son
  libellé. Un `INSERT` par prompt donnerait vingt lignes pour une session ;
- **la date est celle du prompt**, prise sur son horloge d'archivage et non sur
  une date de code.

### §3b — L'hypothèse sur la monotonie, datée pour être vérifiable

Mesure du 21/08/2026, population principale, `exploratoire = 0` :

| Cran | Sélections | Tranchées | Taux |
| ---: | ---: | ---: | ---: |
| 1 | 3 | 1/3 | 33 % |
| 2 | 35 | 21/35 | **60 %** |
| 3 | 141 | 64/137 | **47 %** |
| 4 | 106 | 61/104 | 59 % |
| 5 | 11 | 7/10 | 70 % |

Le brief donne 54 % et 40 % : la base a bougé — c'est la règle « une analyse est
datée » — et le fait tient dans les deux lectures. **Le cran 2 bat le cran 3**,
donc la monotonie que les bandes supposent n'est pas établie.

**L'hypothèse, écrite et datée pour ne pas être redécouverte** :

> Si le cran 3 sort sous sa bande, la consigne de resserrement fera passer des
> sélections de 3 vers 2. Or les crans 1 et 2 n'ont **aucune cible** — ils sont
> fixés par ce que la recherche a trouvé, `lecture` impose 1, une source de
> niveau 3-4 plafonne à 2 — donc le mouvement demandé pousse vers une catégorie
> qu'aucune bande ne mesure. La correction viderait le cran qu'on corrige sans
> qu'aucun indicateur ne le montre.

Elle est **plausible et non mesurée**, et elle ne le sera pas avant la bascule :
la consigne de resserrement ne part que dans la branche `feedback.enough`.

Ce qu'il faut pour la trancher : la **distribution des crans par session**, avant
et après. Elle n'existe nulle part. `labelling()` rend la distribution agrégée
sur toute la base et la vacance par session — combien de sessions n'emploient pas
un niveau — mais jamais la série. Or c'est une déformation dans le temps qu'il
faut voir, pas une part globale.

Ni les bandes ni la consigne ne sont touchées. **Ce lot pose l'instrument, pas le
verdict.**

### §3c — Le compte restant, et l'honnêteté du message

Le brief demande d'afficher « il manque N journées d'analyse avant transmission
des taux au prompt ». Écrit tel quel, ce serait un compte à rebours vers un
événement qui ne peut pas se produire. La ligne dit donc les **deux** conditions
et laquelle bloque, et elle nomme la suspension pour ce qu'elle est : une
décision de code, pas un seuil.

### La série posée par le §3b, et ce qu'elle montre déjà

`history.scale_shift()` rend la distribution des crans et des paliers **session
par session**, du plus ancien au plus récent, avec une colonne « lit ses taux » —
au moins un prompt de la session transmettait des taux de réussite. Rendue sur
`/stats` sous « Comment tu étiquettes » et dans l'export, repliée.

Sortie sur la base servie au 21/08/2026, échelle de confiance :

| Journée | Session | Sél. | Lit ses taux | conf 5 | conf 4 | conf 3 | conf 2 | conf 1 |
| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | ---: |
| 05/08 | 2 | 17 | — | 0 | 5 | 11 | 1 | 0 |
| 06/08 | 3 | 28 | **oui** | 0 | 10 | 18 | 0 | 0 |
| 07/08 | 4 | 9 | **oui** | 0 | 6 | 3 | 0 | 0 |
| 08/08 | 5 | 18 | **oui** | 0 | 7 | 11 | 0 | 0 |
| 09/08 | 6 | 29 | — | 0 | 8 | 21 | 0 | 0 |
| 10/08 | 7 | 5 | — | 0 | 4 | 1 | 0 | 0 |
| 11/08 | 8 | 11 | — | 1 | 4 | 4 | 2 | 0 |
| 12/08 | 9 | 12 | — | 0 | 5 | 2 | 5 | 0 |
| 13/08 | 10 | 20 | — | 1 | 6 | 8 | 5 | 0 |
| 14/08 | 11 | 29 | — | 1 | 6 | 16 | 5 | 1 |
| 15/08 | 13 | 30 | — | 0 | 9 | 13 | 6 | 2 |
| 15/08 | 14 | 27 | — | 2 | 6 | 12 | 7 | 0 |
| 17/08 | 15 | 13 | — | 0 | 7 | 5 | 1 | 0 |
| 18/08 | 16 | 10 | — | 1 | 4 | 4 | 1 | 0 |
| 18/08 | 17 | 19 | — | 3 | 10 | 5 | 1 | 0 |
| 20/08 | 18 | 19 | — | 2 | 9 | 7 | 1 | 0 |

**La coupe existe déjà**, et l'instrument la montre au premier rendu : les
sessions 3, 4 et 5 ont lu leurs propres taux — ce sont les 9 prompts de
3 sessions que `CLAUDE.md` documente, quand les seuils valaient encore 10 et 4.
`ScaleShift.cut` vaut donc **1**, le rang de la session 3.

Ce qui se voit et **ne se conclut pas** : sur les trois sessions concernées, les
crans 2 et 5 tombent à zéro et tout se concentre sur 3 et 4. Elles précèdent
aussi la définition des cinq crans, la garde d'antériorité et le cran calculé —
trois changements de cadre datés au journal. **Une série de seize points dont
trois sont marqués n'établit rien**, et c'est précisément pourquoi l'instrument
est posé maintenant : le jour où la suspension tombera, il y aura un avant.

Aucun seuil, aucun test statistique, aucun verdict. `labelling()` garde les
garde-fous qu'il a ; celui-ci n'en a aucun parce qu'il n'affirme rien.
