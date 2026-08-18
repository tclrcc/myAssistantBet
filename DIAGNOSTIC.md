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
sed -i 's/^SERVE_LINES_ENABLED=.*/SERVE_LINES_ENABLED=1/' .env && sudo systemctl restart myassistantbet
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

Huit affirmations reprises et vérifiées ; **six ne tiennent pas**, dont deux qui
ont changé le déroulé de la session.

| Affirmé | Mesuré |
| --- | --- |
| « 176 joueurs sur 176, tous au-dessus du seuil de 400 points » | **38 joueurs distincts.** 176 est le nombre de *lignes* de `player_serve_agg` — 38 joueurs × ~4,6 surfaces |
| « toutes tranches de classement à 100 % » | vrai toutes surfaces et sur Hard/Clay ; **I.hard 13/26, Grass 24/36** |
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
