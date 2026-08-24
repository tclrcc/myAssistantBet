# Phase 1 — addendum 2 : le chiffre de tete, et quatre questions fermees

---

## 1. Une seule table, une seule unite

### Le denominateur manquant

`52 + 349 = 401` sont les **tranchees** de section C. Les 27 qui manquent pour
atteindre 428 sont **14 `void` et 13 `pending`**. Aucune selection n'est sans
evenement.

| Section | Total | Tranchees | `void` | `pending` |
| --- | ---: | ---: | ---: | ---: |
| C | **428** | **401** | 14 | 13 |
| C-bis | **95** | **90** | 1 | 4 |
| ensemble | 523 | 491 | 15 | 17 |

Et les tranchees se decoupent : C = **349 anterieures + 52 tardives** ;
C-bis = **90 anterieures, 0 tardive**.

### La table

Unite : **points de pourcentage par selection** (residu / n × 100). « Marge
retiree » est calculee **sur les seules couvertes** de chaque ligne, et la
colonne `couv.` dit sur quelle part.

| Population | | section C | | | | section C-bis | | |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| | **n** | **brut** | **marge ret.** | **couv.** | **n** | **brut** | **marge ret.** | **couv.** |
| **tranchees (toutes)** | 401 | −3,18 | +1,42 | 62,1 % | 90 | −2,12 | +3,36 | 85,6 % |
| ├ anterieures | **349** | **−4,59** | +0,35 | 67,9 % | **90** | **−2,12** | +3,36 | 85,6 % |
| └ tardives | 52 | **+6,22** | +22,58 | 23,1 % | 0 | — | — | — |
| | | | | | | | | |
| anterieures, **avec** overround | 237 | −2,43 | +0,35 | 100 % | 77 | +1,42 | +3,36 | 100 % |
| anterieures, **sans** overround | 112 | **−9,14** | — | 0 % | 13 | −23,12 | — | 0 % |
| | | | | | | | | |
| anterieures, sessions **2-7** | 68 | **−16,45** | — | 0 % | 0 | — | — | — |
| anterieures, sessions **8-22** | 281 | **−1,72** | +0,35 | 84,3 % | 90 | −2,12 | +3,36 | 85,6 % |

**Le fait dominant du tableau** : les anterieures des sessions 2-7 pesent
**−16,45 points par selection** sur 68 lignes, celles des sessions 8-22
**−1,72** sur 281. Le deficit publie n'est pas reparti — il est **concentre sur
la premiere semaine**, celle qui n'a ni marche fige, ni bloc de confiance, ni
`market_key`, et qui portait 19,4 % de tardives.

---

## 2. Le residu corrige des anterieures, incertitude propagee

**Restreindre aux couvertes serait un estimateur biaise**, et la table le montre :
couvertes −2,43 contre non couvertes −9,14 points par selection. La non-couverture
est donc traitee comme une **source d'incertitude** — l'overround manquant s'impute
par tirage dans la distribution empirique de sa famille de marche, retire a chaque
replique. Aucune selection n'est ecartee.

**Trois sources propagees**, 10 000 repliques, graine fixe
(`audit/bootstrap_anterieures.py`) :

- (a) variance d'echantillonnage des issues — reechantillonnage des selections ;
- (b) incertitude de l'ecart a la cote obtenue — retirage dans l'empirique ;
- (c) **incertitude de l'overround non observe** — imputation par famille, retiree.

| Section C, **anterieures** | n = 349 | couverture 67,9 % |
| --- | ---: | --- |
| victoires observees | 184 | |
| **residu brut** — ce que publie l'appli | **−16,00** | −4,59 pts/sel |
| **residu corrige** — point estime | **−13,50** | **−3,87 pts/sel** |
| **IC 95 %, trois sources** | **[−31,27 ; +4,20]** | [−8,96 ; +1,20] pts/sel |
| P(residu ≥ 0) | **0,062** | |
| **zero dans l'intervalle** | **OUI** | |

| Section C-bis, **anterieures** | n = 90 | couverture 85,6 % |
| --- | ---: | --- |
| residu brut | −1,91 | −2,12 pts/sel |
| residu corrige | −0,91 | −1,01 pts/sel |
| **IC 95 %** | **[−9,05 ; +7,06]** | [−10,05 ; +7,84] pts/sel |
| P(residu ≥ 0) | 0,399 | |
| **zero dans l'intervalle** | **OUI** | |

### Ce qu'il faut ecrire, et il s'ecrit tel quel

**Le zero est dans l'intervalle des deux cotes.** Le deficit de la section C est
**directionnellement net et non etabli** : `P(residu ≥ 0) = 0,062`, donc il
manque peu — mais il manque, et un seuil frole n'est pas un seuil franchi.

**Deux consequences immediates :**

1. **La correction rend le residu plus favorable**, de 2,50 victoires soit
   **+0,72 point par selection**. La marge du book explique donc environ **16 %**
   du deficit publie — pas la majorite, et pas rien.
2. **La comparaison C / C-bis annoncee en phase 1 ne survit pas.** Elle donnait
   −6,70 contre +1,22 ; sur les anterieures completes elle donne **−3,87 contre
   −1,01 points par selection**, avec deux intervalles qui se recouvrent
   massivement. **Il n'y a pas d'ecart etabli entre les deux sections.**

---

## 3. L'axe confiance est mort — `[NON TESTABLE]`, et l'information est perdue

### L'addition ferme

`143 crans reels + 134 forces a 1 + 151 nulles = 428` — toute la section C.

### Les effectifs reels

Section C, **tranchees**, defauts de collecte exclus : **130 selections** en tout.

| Cran reel | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| tranchees | **5** | **13** | **63** | **29** | **20** |

Deux crans sur cinq sont sous quinze. Le cran 3 porte a lui seul 48 % du volume.

### `type_angle × confiance` : la case mediane fait 36, les extremes font 2

| | cran 1 | cran 2 | cran 3 | cran 4 | cran 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `issue` | **2** | 5 | 27 | 14 | 8 |
| `maniere` | **3** | 8 | 36 | 15 | 12 |

**Marque `[NON TESTABLE]` des maintenant :**

- la monotonie de l'echelle de confiance ;
- tout taux ou residu **par cran** au-dela du cran 3 ;
- **l'integralite du croisement `type_angle × confiance`** ;
- par consequent, la reconfirmation demandee en phase 3.4 — *« `type_angle ×
  confiance` discrimine-t-il mieux que `palier × confiance` »* — **ne peut pas
  etre conduite.** Ce n'est pas un resultat negatif, c'est une absence de plan
  d'observation.

Ordre de grandeur du n requis : detecter un ecart de 10 points de taux entre deux
crans voisins a 80 % de puissance demande environ **390 selections par cran**,
soit **~1 950 selections reelles** contre 130. A 9,6 tranchees par session, cela
represente plusieurs annees au regime actuel.

### Les 134 forcees ne sont pas recuperables

`is_collection_fault` **type** la perte, il ne la repare pas. Le brut a ete
verifie :

| Cause | Sessions | n | `imports_raw` conserve | **Blocs `"faits"` dans le brut** |
| --- | --- | ---: | ---: | ---: |
| `cause_inconnue` | 11, 13 | 43 | **0** | — |
| `ligne_absente` | 11, 14 | 43 | **0** | — |
| `ligne_absente` | 15, 16, 17, 18 | 41 | 41 | **0** |
| `reperes_non_resolus` | 22 | 7 | 7 | **9 blocs presents** |

Les collages conserves des sessions 15 a 18 pesent **567 a 2 119 caracteres et ne
contiennent aucun bloc** — ce sont des collages du seul tableau de la section C.
**L'information n'a jamais ete collee : elle n'existe nulle part dans
l'application.** `prompts.body` ne peut pas la porter — c'est ce que l'application
a **envoye**, quand les blocs sont ce que le modele a **rendu**.

**Une seule poche est recuperable** : les **7 selections de la session 22**, dont
le collage (29 415 caracteres) porte bien **9 blocs et la ligne
`dossiers_ouverts`**. Leur cause est `reperes_non_resolus` — l'appariement a
echoue, pas le collage. Un rejeu de la resolution les recupererait.

> **Recuperation totale possible : 7 sur 134.** Les 127 autres sont perdues
> definitivement, sauf si les transcripts Claude d'origine existent encore hors
> de l'application.

---

## 4. Combien de selections n'etaient pas posables ?

### Ce qui compte vraiment, et ce qui n'en est pas

**`betclic_fr` ne sert que le `h2h`.** Verifie sur les 48 401 lignes d'`odds` :
**dix-neuf** cles de marche ont **zero** ligne Betclic — `alternate_spreads`,
`totals`, `btts`, `double_chance`, `correct_score`, `to_qualify`, toutes.

**Mais ces marches ne sont pas « non posables », et le dire serait rejouer une
erreur que ce projet a deja payee.** Le dossier est explicite : Betclic **sert**
ces marches **sur son site**, c'est notre collecte qui ne les remonte pas. Le
libelle d'origine « Non jouable » a fait renoncer a deux angles de jeux posables ;
il a ete remplace par « A relever », et le cadre dit qu'un tel marche **est
selectionnable**. Les compter ici comme des paris fictifs serait la meme faute,
retournee.

| Section | h2h ou non resolu | **marche « a relever »** |
| --- | ---: | ---: |
| C | 217 | **211** |
| C-bis | 70 | **25** |

Ces 236 selections sont **posables**, a un prix qu'il faut relever — c'est
exactement ce que mesure l'ecart `price_real` de −6,8 %, et c'est deja porte au
chiffre corrige.

### Ce qui n'etait vraiment pas posable

| Categorie | Section C | Section C-bis | Statut |
| --- | ---: | ---: | --- |
| **lignes en quart au football** | **4** | 0 | interdites par le cadre, population **close** (07-08/08) |
| cote a 1,00 retenue comme selection | **0** | **0** | `picks.price` minimum = 1,25 |
| marche jamais servi **par personne** | 0 | 0 | tout marche selectionne a au moins un book |

**Total avere : 4 selections sur 523, soit 0,8 %, toutes en section C, toutes
anterieures au 09/08.** Le residu ne mesure pas une fiction.

### Un signal a part : les cotes a 1,00 ont bien ete rendues

`render.py` **ne filtre aucune cote basse** — aucun seuil sur `price` dans le
module. Les 159 lignes a 1,00 ont donc paru dans des blocs reels : **69 blocs sur
679 (10,2 %)**, sur 7 sessions.

Aucune n'a ete retenue, ce qui est le comportement souhaite. Mais rien ne
l'empechait, et une cote a 1,00 dans un bloc est une ligne qui ne peut rien
rapporter. **P1 pour la phase 4**, pas P0 : le degat est potentiel, pas realise.

### Deux anomalies de libelle, signalees sans conclure

**2 selections** (1 en C, 1 en C-bis) portent un marche que Betclic ne sert pas
**et** `price_source = 'betclic'`. Soit le prix a ete releve a la main sur le
site — ce que le cadre demande — soit la source est mal declaree. Indiscernable
d'ici.

---

## 5. Session 18 : le cas n'est pas isole, mais il est minuscule

**Quatre matchs** portent une selection dans chaque section, sur 90 selections de
C-bis — soit **4,4 %**.

| Session | Affiche | Section C | Section C-bis | Relation |
| --- | --- | --- | --- | --- |
| 18 | Tirante - Fils | `Fils −2.5` 1.55 **win** | `Tirante` 3.95 **loss** | **mutuellement exclusives** |
| 20 | Le Mans - Brest | `Under 2.5` 1.90 **loss** | `Le Mans 1-0` 9.81 **loss** | **emboitees** — 1-0 ⊂ Under 2.5 |
| 20 | Espanyol - Real Madrid | `Espanyol +1.5` 1.74 **win** | `Nul` 4.85 **loss** | **emboitees** — nul ⊂ Espanyol +1.5 |
| 20 | Sporting - Alverca | `Over 2.5` 1.50 **win** | `Alverca` 11.75 **loss** | faiblement correlees |

**Une seule paire est mutuellement exclusive. Deux sont emboitees**, la selection
C-bis etant *strictement incluse* dans la C — donc parfaitement correlees dans un
sens : si la C-bis gagne, la C gagne aussi. Les deux ont perdu ensemble sur Le
Mans, et sur Espanyol la C a gagne sans la C-bis, ce qui est le cas normal d'un
emboitement.

### Ce que ca fait a la comparaison des residus

**Rien de mesurable.** Quatre matchs sur 90 ne peuvent pas porter un ecart de
residu entre deux populations de 349 et 90. **Mais la comparaison ne tient de
toute facon plus** — voir le point 2 : sur les anterieures completes, C et C-bis
donnent −3,87 et −1,01 points par selection avec des intervalles qui se
recouvrent massivement.

**Le vrai obstacle a cette comparaison n'est pas la dependance, c'est le prix** :
la cote moyenne vaut **1,80 en section C et 4,00 en C-bis**. Deux populations qui
ne jouent pas aux memes prix ne se comparent pas sur un taux, et se comparent mal
sur un residu — c'est la lecon que la page de statistiques a deja apprise.

---

## Acte : sessions 2 a 7, trou permanent

`prompt_odds` commence a la **session 8**. Les sessions 2 a 7 n'ont **aucun
marche fige**, et rien ne le reconstituera : `odds` ne conserve que le dernier
releve, et l'etat du marche au moment de l'analyse n'existe plus.

| Regime | Section C | Section C-bis | Total | Part du volume |
| --- | ---: | ---: | ---: | ---: |
| **sessions 2-7 — trou permanent** | **106** | 0 | **106** | **20,3 %** |
| sessions 8-22 | 322 | 95 | 417 | 79,7 % |

**Un cinquieme du volume total est definitivement sans marche fige**, et cette
part est **entierement en section C**. Elle porte par ailleurs le plus gros
deficit du jeu de donnees (−16,45 points par selection sur ses 68 anterieures),
sans qu'aucun overround puisse jamais y etre mesure.

Ce n'est pas un defaut a corriger : c'est une **borne du jeu de donnees**, a
citer chaque fois qu'un residu global est rapporte.
