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
