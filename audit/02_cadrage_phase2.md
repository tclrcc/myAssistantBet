# Phase 1 — cloture : deux P0 livres, un P1 ouvert, et un signal a trancher

---

## Ce qui a ete applique

### P0-1 — la carte du cran calcule (`92290d6`)

Test de non-regression ecrit **avant** le correctif, echec verifie pour la bonne
raison (`assert 3 == 1`), puis correctif.

**Le test a trouve un defaut dans mon propre correctif**, et il vaut d'etre dit :
la premiere version appliquait les deux gardes dans l'ordre de leur lecture, or
`is_unknown_cause(None)` vaut **vrai** — c'est ce qui ferme le trou des lignes
anterieures au typage. Appliquee sans tester `research_overridden` d'abord, elle
ecartait le **cas ordinaire** et vidait la carte entiere. `_override` teste le
drapeau avant ; le correctif reprend cet ordre, et le commentaire dit pourquoi.

Mesure sur la base servie, avant / apres :

| Cran calcule | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| avant | **140** | 16 | 69 | 30 | 22 |
| apres | **6** | 16 | 69 | 30 | 22 |

Le cran 1 passe donc **sous `minimum_rows`** : il sera pali et annonce comme
maigre, ce qui est la lecture juste. Les 134 gardent leur compte dans `Override`
et `SessionRate.override_faults`, avec leur cause — c'est la separation qui vaut,
pas le masquage.

### P0-2 — le residu de tete annonce qu'il agrege deux regimes (`1f66705`)

Ta reserve est retenue telle quelle : **aucun calcul, une reserve.** La
ventilation par « la session porte-t-elle un releve » a ete abandonnee au profit
d'un simple compte, et la regle §9 n'est pas approchee.

`Analysis.residual_unfrozen` compte les selections du residu venant de sessions
dont **aucun** releve n'a ete fige. Mesure sur la base servie : **68 sur 349,
soit 19,5 %**.

La frontiere se lit sur la **session**, jamais sur le marche : rattacher chaque
selection a son releve demanderait de rejouer `MERGED_MARKETS` ici, c'est-a-dire
d'ecrire une seconde fois une regle qui vit dans `render` — le motif que ce depot
paie le plus cher. La question se tranche bien plus haut.

La reserve part avec les chiffres dans l'export, et vit au **second niveau** du
bloc de tete, avec les autres hypotheses du chiffre.

### Etat de la suite

`ruff check` et `ruff format` verts sur `src/` et `tests/`.
**2 644 tests passent, 1 echoue** — `test_le_numero_de_cadre…`, verifie comme
**anterieur a mes changements** (rejoue sur l'arbre remise). Voir plus bas.

---

## P1 — la source de prix : le seul changement qui ameliorera la mesure future

### Le constat

| Categorie | Paires | **Ecart moyen** | Plus basses |
| --- | ---: | ---: | ---: |
| **marches « a relever »** (section C) | 98 | **−7,11 %** | **96 / 98** |
| h2h ou non resolu (section C) | 25 | −3,54 % | 21 / 25 |

**236 selections** (211 en C, 25 en C-bis) portent un marche que `betclic_fr` ne
sert **dans aucune** des 48 401 lignes d'`odds`. Leur prix vient d'un book de
reference — `pinnacle` pour 80 des paires mesurees, a **−6,58 %** en moyenne.

Tu as raison de ne pas t'arreter a la question de savoir si la difference de
marge l'explique : **au football elle colle (−3,97 observe contre −3,64 predit),
au tennis non (−3,16 contre −1,02)**, et dans les deux cas la consequence est la
meme — l'attendu oppose a ces selections est un prix qui n'etait pas obtenable.

### Ce que ca coute a la mesure

Le residu compare une issue a `1/prix`. Si le prix est structurellement plus
genereux que celui qu'on obtient, l'attendu est **sous-estime** et le residu
**flatte**. L'ampleur est connue sur les 123 paires renseignees ; elle est
**inconnue sur les 113 autres selections de la meme famille**, et elle ne se
reconstituera pas.

### Les deux corrections, dans l'ordre de ce qu'elles coutent

**1. Relever le prix chez le book ou l'on parie.** C'est la correction en amont,
et c'est la seule qui rende la mesure juste plutot que corrigee. Elle ne demande
aucun code : c'est un geste de saisie, et le champ existe deja.

**Mais il n'est offert que sur les selections de prix de reference**
(`_pick_row.html:102`), ce qui est exactement la bonne population — 241 en
section C, dont **118 ne sont pas renseignees**. Le compteur de couverture
(`Worksheet.coverage_line`) les annonce deja au moment de la saisie.

**2. A defaut, consigner que l'attendu de ces selections est structurellement
optimiste.** C'est une reserve de plus, du meme ordre que celle de P0-2 : un
compte des selections dont le prix vient d'un book de reference **et** dont la
cote obtenue manque. `Pick.awaiting_real_price` existe deja
(`history.py:355`) — il ne remonte simplement pas jusqu'a la page de mesure.

> **Ce que je ne propose pas** : corriger le residu par l'ecart moyen. L'ecart
> vaut −3,5 % sur un marche et −7,1 % sur l'autre, il n'est mesure que sur la
> moitie de la population eligible, et l'appliquer aux autres inventerait un
> chiffre. La reserve dit ce qu'on sait ; la correction affirmerait ce qu'on ignore.

**Effort** : la premiere correction est nulle en code et continue en saisie ; la
seconde est une propriete et une phrase, du meme gabarit que P0-2.

---

## Signal a trancher : le cadre 1.4 est publie, la constante dit 1.3

**Le test qui echoue n'est pas casse — il fait exactement son travail.**

```
Ecart : la constante et le cadre publie ne disent pas la meme chose.
Cadre publie   : 1.4
  fichier      : …/plugins/2872adf0bf664498/skills/myassistantbet-framework/SKILL.md
  empreinte    : fadc322020931b9d…
Constante code : 1.3
```

Le fichier date du **22/08 15:23**, sous un **nouveau hash de plugin** — la
cartographie de phase 0, qui annoncait « le cadre publie est en 1.3 », est donc
perimee depuis deux jours. `deploy/cadre-lu.json` porte encore la preuve de la
1.3 lue le 21/08.

**Je n'ai pas bumpe la constante, et c'est la regle du module** : *« il ne releve
pas le numero a la place de qui exploite »*. Le geste est
`uv run myassistantbet-cadre --relire`, suivi d'un commit qui porte la preuve
**et** la constante.

### Pourquoi ca depasse le numero

La 1.4 declare elle-meme une frontiere :

> « La 1.4 outille la conduite de la recherche de verification. C'est un
> changement de methode, donc un changement du processus qui produit le journal :
> **les lots analyses a partir de cette version ne sont pas comparables aux
> precedents** sur la qualite de verification. »

C'est donc un **troisieme regime**, apres les sessions 2-7 et 8-22. Et
`picks.framework_version` — la colonne qui devait le dater — est estampillee par
l'application depuis **sa propre constante**. Tant qu'elle dit 1.3, **toute
selection produite sous la 1.4 sera etiquetee 1.3**, et la frontiere que le cadre
demande de garder lisible sera perdue a l'ecriture.

**C'est une decision, pas un correctif** : elle t'appartient, et elle est urgente
au sens propre — chaque session analysee d'ici la est mal etiquetee.

---

## Cadrage de la phase 2, revise

**Il n'y a aucun signal a expliquer**, et la phase 1 le montre : sur le regime
actuel le residu vaut −4,49 avec un intervalle de [−20,67 ; +11,15]. La question
devient donc celle que tu poses — **le dispositif serait-il capable de detecter
qu'il gagne ou qu'il perd ?**

### Axe 1 — l'ancrage sur la cote (question centrale)

**Premier releve, structurel, deja fait.** L'ordre du gabarit
(`session_default.md.j2`, 1 412 lignes) est sans ambiguite :

| Ligne | Section |
| ---: | --- |
| 3 | `## TON RÔLE` |
| 171 | `## CE QU'IL FAUT VÉRIFIER` |
| **276** | **`## MATCHS`** — les blocs, **cotes comprises** |
| 470 | `### A. Fiche de vérification` |
| 476 | `### B. Analyse par match` |
| 502 | `### C. Tableau des sélections` |

**Le modele lit toutes les cotes du lot avant de produire la moindre ligne.**

**Mais la nuance decide de tout, et elle joue dans les deux sens** : le cadre ne
demande **jamais** d'estimation de probabilite. La section B reclame « l'angle
sportif dominant, sa nature en un mot — issue ou maniere — puis le marche qui la
traduit ». Il n'y a donc **aucune estimation a ancrer** au sens classique du
biais.

Ce qui reste a instruire, et c'est le vrai objet de l'axe :

1. **La cote decide-t-elle du choix de selectionner ?** Le gabarit l'interdit en
   toutes lettres (« tu ne renonces pas a un angle parce qu'il serait mieux paye
   ailleurs »), et la fiche de priorite de recherche ne regarde aucun prix — un
   test le garde. Reste a verifier qu'aucun **autre** chemin ne le reintroduit.
2. **Le palier est-il annonce avant la selection ?** La ligne `Paliers` ferme
   chaque bloc et dit ce que **ses** cotes rendent atteignable. C'est une
   information de prix posee avant l'analyse : a instruire.
3. **Que resterait-il a mesurer si l'ancrage etait total ?** Si le modele
   converge vers le marche, un residu proche de zero est le **resultat attendu du
   dispositif**, et non une information sur la qualite de l'analyse. La phase 1
   ne permet pas de departager les deux — c'est ce qui fait de cet axe la
   question centrale de tout l'audit.

### Axe 2 — l'etancheite palier / confiance

Verifiable independamment de la puissance statistique. Contamination dans les
deux sens, indirecte comprise.

### Axe 3 — le filtre d'anteriorite dans les requetes de statistiques

Exhaustif : une requete qui l'oublie suffit.

### 2.4 — le staking

Tombe, et s'audite comme l'absence de devig : `SPEC.md` §9.1 l'interdit, et
`stakes.py:6` porte deja la phrase. A verifier comme une porte fermee.
