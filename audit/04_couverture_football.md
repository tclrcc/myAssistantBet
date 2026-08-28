# 04 — Audit de couverture du bloc football

> Ce n'est pas un audit de defauts, c'est un audit de **manque**. La question
> posee est : le bloc transmet-il ce qui decide reellement d'un match de
> football, et ce qui lui manque est-il atteignable.

**Mesures du 28/08/2026**, sur une copie `VACUUM INTO` de la base servie :
1 327 blocs football archives, 683 matchs distincts, 241 prompts. Plus
**374 appels en direct** a API-Football, comptes dans la copie : 197
`/fixtures/statistics`, 170 `/fixtures/lineups`, 7 `/fixtures` — soit 5,0 % du
quota du jour.

Le quota **reel** du fournisseur a ete consomme ; la **trace** est allee dans la
copie, jamais dans la base servie, par des `Settings` derives. Verifie apres
coup : la base servie ne porte **aucun** `/fixtures/statistics` sur la periode,
et ses 19 `/fixtures/lineups` sont le balayage planifie. Consequence a connaitre
et sans gravite : son compteur `remaining` est en avance sur la realite jusqu'au
prochain appel de production, qui relit le header et se recale seul.

---

## Trois corrections de methode, faites en route

Elles precedent le rapport parce qu'elles ont chacune change un chiffre que
j'avais deja ecrit.

### 1. Mon parseur rendait quatre lignes a zero, et le piege est deja documente

Le premier releve decoupait les lignes de contexte sur « deux espaces ou plus ».
`Clean sheet`, `Cartons tps`, `Stats match` et `Buteur abs.` sortaient a **zero**
— quatre facteurs couverts que j'allais rapporter comme absents.

La cause est ecrite dans `CLAUDE.md` depuis le chantier de `Buts encais.` : le
separateur entre un libelle et sa valeur **n'existe pas en propre**, c'est le
remplissage du champ de 12 caracteres qui le fabrique. Un libelle de 11 n'en
laisse qu'un seul. La decoupe est donc **positionnelle** (`l[2:14]`), comme
`render.line` l'ecrit.

> **Un defaut de formatage documente attrape aussi les outils de mesure ecrits
> ensuite.** Le dossier connaissait le piege cote rendu ; personne n'avait note
> qu'il attend aussi le lecteur. Note ajoutee dans `CLAUDE.md` la ou le cas est
> ecrit.

### 2. Le ratio D9/D1 m'a fait classer deux grandeurs a l'envers

L'etape 1 annoncait « `BTTS MT` discrimine le plus, ratio 2,7 — meme methode que
`Buts tard.` ». **Faux, et la methode invoquee ne s'applique pas ici.**

Un ratio decile monte **mecaniquement** quand `p` est petit, parce que l'erreur
d'echantillonnage relative vaut `1/sqrt(n·p)`. Pour `Buts tard.` le ratio etait
legitime — les deux fenetres avaient le **meme ecart absolu** et des bases
differentes, donc le ratio departageait a ecart egal. Ici les bases *et* les
ecarts different, sur des denominateurs de 35 matchs.

Ce qui repond a la question est la **decomposition de variance** : la dispersion
observee entre equipes moins la variance d'echantillonnage attendue. Elle inverse
le classement — voir §B.1.

### 3. Six absences chinoises qui n'existaient pas

En mesurant le remplacant, mon appariement portait sur `(identifiant, nom)`. Le
fournisseur ecrit `Yue Tze-Nam` puis `Tze-Nam Yue` d'une feuille a l'autre, si
bien que **six titulaires ressortaient « absents »** — tous presents sous
l'identifiant `#70513`, `#41799`, `#13110`… **L'identifiant, lui, est stable :
zero nom replie porte deux identifiants sur les 21 feuilles.**

C'est la regle du dossier appliquee contre son auteur — *un zero sur un
rapprochement de joueurs est un defaut d'appariement jusqu'a preuve du
contraire*. Et le contraste avec l'entraineur merite d'etre note : la-bas
l'identifiant **ment** (35 fiches dupliquees pour le meme homme, mesure du
21/08), ici il dit vrai. La regle « cherchez l'identifiant » dit ou regarder,
jamais qu'il a raison.

Second chiffre faux du meme releve : « 0 titulaire habituel » en Primeira Liga
venait de **3 feuilles vides sur 6** — comportement documente, une reponse vide
n'est pas persistee.

---

## A. Inventaire — ce que le bloc porte deja

Taux mesures sur les blocs football archives. Trois fenetres, parce qu'une ligne
livree en cours de periode coupe le corpus en deux : **tout** (1 327 blocs),
**depuis le 22/08** (410) et **depuis le 26/08** (150), qui est le regime courant.

| Facteur | Ligne du bloc | Couverture | tout | 22/08 | 26/08 | Source |
| --- | --- | --- | ---: | ---: | ---: | --- |
| forme recente des equipes | `Forme 5` | complete | 77,2 % | 83,7 % | 99,3 % | `/teams/statistics` + `/fixtures?last=` |
| forme selon le niveau des adversaires | — | **absente** | — | — | — | (cat. 5, §B.2) |
| forme des joueurs cles — buteurs | `Buteurs` | partielle | 2,6 % | 3,4 % | 0 % | `/players/topscorers` |
| *(dont, dans le seul perimetre `PLAYER_PROPS_LEAGUES`)* | | | *24,3 %* | *14,6 %* | | *144 blocs sur 1 327* |
| forme des joueurs cles — gardien | — | **absente** | — | — | — | (cat. 5, §B.2) |
| domicile / exterieur | `Dom/Ext` | **partielle** — offensif seul | 60,4 % | 69,0 % | 91,3 % | `/teams/statistics` |
| dynamique de serie | `Serie` + `Classement` | complete | 20,6 / 72,3 % | 18,8 / 84,1 % | 25,3 / 99,3 % | `/fixtures?season=`, `/standings` |
| profil offensif — volume | `Tirs`, `xG` | complete | 52,1 / 39,9 % | 56,6 / 53,4 % | 80,7 / 75,3 % | `/fixtures/statistics` |
| profil offensif — efficacite | `xG` vs `Buts marq.` | partielle — fenetres differentes | — | — | — | idem |
| profil offensif — dependance a un joueur | `Buteurs` | partielle — total du joueur, pas sa part | 2,6 % | 3,4 % | 0 % | `/players/topscorers` |
| profil offensif — sources de buts | — | **absente** | — | — | — | (cat. 4) |
| profil defensif — solidite | `Buts pris`, `Clean sheet` | complete | 15,8 / 17,4 % | 23,2 % | 32,7 % | `/teams/statistics` |
| profil defensif — tirs concedes | `xG` concede seul | **partielle** | 39,9 % | 53,4 % | 75,3 % | `/fixtures/statistics` |
| profil defensif — types de buts encaisses | — | **absente** | — | — | — | (cat. 4) |
| rythme, possession | `Possession`, `Fautes`, `Corners` | complete | 43,1 / 43,1 / 52,8 % | 57,6 % | 83,3 % | `/fixtures/statistics` |
| pressing haut / bloc bas | — | procuration seule | — | — | — | (cat. 4) |
| moments — buts par tranche | `1re MT`, `Buts tard.` | partielle — 2 fenetres sur 6 | 17,4 / 15,2 % | 23,2 % | 32,7 % | `/teams/statistics` |
| moments — score a la pause | — | **absente, donnee en base** | — | — | — | (cat. 2, §B.1) |
| entraineur — systeme habituel | `Formations` | complete | 17,2 % | 22,4 % | 32,7 % | `/teams/statistics` |
| entraineur — arrivee recente | `Entraineur` (anciennete) | complete | 78,5 % | 99,3 % | 100 % | `/coachs` + feuilles |
| entraineur — philosophie | — | **ecartee** (§C) | — | — | — | — |
| entraineur — pression sur le poste | — | **ecartee** (§C) | — | — | — | — |
| entraineur — experience, historique | — | **ecartee** (§C) | — | — | — | — |
| entraineur — tendance a la rotation | `Calendrier` + critere de recherche | cat. 3 | 78,0 % | 98,8 % | 99,3 % | `/fixtures?season=` |
| effectif — absents | `Absents`, `Effectif` | complete | 96,0 / 20,6 % | 99,3 / 27,1 % | 100 / 44,7 % | `/injuries`, feuilles |
| effectif — **qui les remplace** | — | **fermee, mesuree** (§B.2, §C) | — | — | — | 290 feuilles, 60 equipes |
| effectif — retours de blessure | — | cat. 3 | — | — | — | — |
| effectif — profondeur de banc | `bench` collecte, non rendu | **ecartee** (§C) | 0,8 % | 0,2 % | 0,7 % | `/fixtures/lineups` |
| confrontations directes | `H2H`, `Aller`, `Scenario` | complete | 72,7 / 13,1 / 9,8 % | 93,7 % | 95,3 / 33,3 % | `/fixtures/headtohead` |
| pertinence du H2H | — | cat. 3 | — | — | — | — |
| enjeu reel | `Enjeu` + fiche de competition | complete | 31,5 % | 45,9 % | 46,7 % | `/standings` |
| motivation asymetrique | `Scenario` (manches retour) | **ecartee** hors tour (§C) | 9,8 % | 12,9 % | 33,3 % | — |
| rivalite, derby | — | **ecartee** (§C) | — | — | — | — |
| calendrier, voyage | `Calendrier`, `Repos` | complete | 78,0 / 96,0 % | 98,8 / 99,3 % | 99,3 / 100 % | `/fixtures?season=` |
| conditions — meteo | `Meteo` | complete | 52,9 % | 80,0 % | 84,0 % | Open-Meteo + NWS |
| conditions — pelouse | `Pelouse` | conditionnelle | 11,0 % | 9,0 % | 8,7 % | `/teams` |
| conditions — terrain neutre | `Lieu` (4 etats) | complete | 68,6 % | 99,3 % | 100 % | `/venues` + geocodage |
| conditions — altitude | — | **fermee, zero cas** (§C) | — | — | — | — |
| conditions — affluence, huis clos | — | cat. 3 | — | — | — | — |
| arbitre | `Arbitre` (nom, 3 etats) | complete pour ce qu'elle promet | 65,0 % | 99,3 % | 100 % | `/fixtures` |
| arbitre — tendances cartons | — | **porte deja fermee** (14/08) | — | — | — | — |

**Reserve a porter sur tout ce tableau** : les neuf lignes de saison plafonnent a
32,7 % parce que `SEASON_MIN_MATCHES` (5) les retient, et **le corpus est un mois
d'aout**. Ce taux decrit une reprise de championnat, pas un regime de croisiere.

**Deux verifications qui rayent un manque suppose.** « Equipe qui n'a pas
marque » est deja rendu — `failed_to_score` alimente `Clean sheet`
(`5 CS, 2 sans marquer/16`) ; et les **cartons rouges** sont dans `Cartons`
quand ils sont non nuls. Les deux paraissaient absents et ne le sont pas.

---

## B. Les cinq categories

La grille du brief en comptait quatre. La mesure en impose une **cinquieme**, et
c'est celle qui porte le plus : une donnee **telechargee, lue, puis jetee a la
reduction**. Elle n'est pas « disponible et non collectee » — l'appel est deja
paye — ni « collectee et non rendue » — elle n'atteint jamais la base. C'est un
troisieme regime de cout : **zero appel, une reduction a elargir**.

### B.1 — Categorie 2 : en base, jamais lu

#### a. Le score a la pause — 24 740 lignes ecrites et jamais relues

`dossier._summarize` garde `halftime` depuis l'origine du module. `grep` sur le
depot : le champ est **ecrit et n'a aucun lecteur**.

| | |
| --- | ---: |
| matchs avec score final en base | 24 968 |
| dont score a la pause | **24 740 (99,1 %)** |
| releves d'equipe a >= 5 matchs officiels | 606 |
| dont score a la pause sur **tous** leurs matchs | **605 (99,8 %)** |

La ligne sortirait donc exactement la ou `Total buts` sort, dont elle partage la
source, la fenetre et le seuil : **99,3 % des blocs du regime courant**.

**Ce qu'il y a en face.** Quatre marches de mi-temps sont **achetes et rendus** :

| marche | blocs recents |
| --- | ---: |
| `MT O/U` | 52,0 % |
| `BTTS MT` | 51,7 % |
| `MT/FT` | 50,7 % |
| `Score ex. MT` | 49,0 % |

**136 blocs sur 213 (63,8 %) portent un marche de mi-temps sans aucune ligne de
contexte de mi-temps.** Et 6 selections football sur 462 (1,3 %) visent un de ces
marches — probablement la consequence et non la cause.

**Ce n'est pas un doublon de `1re MT`, et c'est mesure.** `1re MT` rend la *part
des buts* tombes avant la pause — denominateur **buts**. Une ligne derivee de
`halftime` rendrait une *frequence de match* — denominateur **matchs**. La
correlation entre les deux grandeurs vaut **r = 0,16** pour « mene a la pause » :
elles ne disent pas la meme chose. Meme distinction que `Total buts` (buts du
match) et `Buts marq.` (buts de l'equipe), que le gabarit separe deja
explicitement.

**Laquelle des grandeurs entre — chacune gagne sa place separement.**

La premiere version de cette section calibrait les cinq candidates contre les
**deux** grandeurs de `Total buts` seules. L'instrument a ensuite ete retourne sur
**toutes** les lignes de saison deja livrees, et le resultat a change le verdict :
voir « Le plancher n'etait pas celui que je croyais » juste apres.

Deux precisions de methode, et la seconde a deplace tous les chiffres :

1. **La variance d'echantillonnage se calcule par unite**, `p_i(1-p_i)/n_i`, et
   non avec la moyenne globale `p(1-p)/n`. `p(1-p)` etant concave, la seconde
   surestime le bruit et sous-estime le signal — de 3 points sur `>2.5`, de
   3 sur `BTTS`. Les chiffres ci-dessous sont ceux de la premiere forme, et ce
   sont les seuls que ce document porte ;
2. **la part de signal depend de la fenetre, l'ecart-type vrai non.** Une ligne
   sur 39 matchs et une ligne sur 10 ne se comparent donc pas sur la part — c'est
   `sd vrai`, propriete de la population et non de l'echantillon, qui les
   departage. Les deux sont rendus.

| grandeur | statut | obs/unite | signal | **sd vrai** |
| --- | --- | ---: | ---: | ---: |
| `Profil` part 2 sets | *livree, tennis* | 10 | 40 % | *0,119* |
| `Profil` part tie-break | *livree, tennis* | 10 | 38 % | *0,107* |
| `Buts marq. >1.5` | *livree* | 39 | 58 % | *0,097* |
| `Buts pris >1.5` | *livree* | 39 | 53 % | *0,085* |
| `Buts pris >0.5` | *livree* | 39 | 50 % | *0,075* |
| `Clean sheet` | *livree* | 39 | 50 % | *0,075* |
| **`mene MT`** | **candidat** | 39 | **47 %** | **0,075** |
| `Total buts >2.5` | *livree* | 39 | 41 % | *0,070* |
| `Buts marq. >0.5` | *livree* | 39 | 47 % | *0,066* |
| `sans marquer` | *livree* | 39 | 47 % | *0,066* |
| **`>1.5 MT`** | **candidat** | 39 | **30 %** | **0,053** |
| `Total buts BTTS` | *livree* | 39 | 24 % | ***0,048 — le plancher*** |
| `>0.5 MT` | candidat | 39 | 20 % | 0,037 |
| `BTTS MT` | candidat | 39 | 21 % | 0,036 |
| `fige MT` | candidat | 39 | 19 % | 0,033 |

#### Le plancher n'etait pas celui que je croyais, et il inverse un arbitrage

**La decomposition n'avait jamais ete appliquee aux lignes deja livrees.** Je
m'en etais servi pour ecarter `BTTS MT` et `fige MT` — 85 % de leur dispersion
n'est que du bruit — sans verifier ce que portent les lignes que le bloc rend
depuis des mois.

Trois resultats, et le troisieme decide :

- **le plancher de la production est `Total buts BTTS`**, a 24 % et
  `sd = 0,048`. Toutes les autres lignes de saison sont entre 41 et 58 % ;
- **les lignes de profil et de tennis en portent plus, pas moins.** `Usure`
  atteint 67 % de signal, `xG` produit 70 % et `xG` concede 63 %. Le plancher
  n'est donc pas abaisse par elles — c'etait l'hypothese a verifier, et elle est
  fausse. *(Reserve : les lignes de profil sont mesurees sur 3 observations par
  equipe, ou l'estimation de la variance intra est instable. A ne pas conclure
  fermement.)* ;
- **`>1.5 MT` passe le plancher et `BTTS MT` ne le passe plus.** 0,053 contre
  0,048 pour l'un, 0,036 pour l'autre.

**L'arbitrage entre les deux s'inverse donc.** Il tenait sur deux points : leur
correlation de 0,75 — c'est le meme angle, un seul entre — et un marche dedie
pour `BTTS MT`. Le premier est intact, le second ne departage plus rien
(`MT O/U` 52,0 % des blocs contre `btts_h1` 51,7 %, et `totals_h1` a 1.5 est la
ligne la plus servie, 394 relevés). Ce qui reste est la discrimination, et elle
designe `>1.5 MT`.

> **Entrent : `mene MT` et `>1.5 MT`.** Le premier se place entre `Clean sheet`
> et `Total buts >2.5`, le second entre `Total buts BTTS` et le plancher. Ils
> sont **orthogonaux** (r = 0,20), et `>1.5` reprend l'idiome de `Total buts`
> (`>2.5 29/56`) sans fabriquer un second vocabulaire.
>
> **Sortent : `BTTS MT`, `>0.5 MT`, `fige MT`** — les trois sous le plancher que
> la production elle-meme etablit.

**Cout mesure**, sur des noms d'equipe reels :

| variante | caracteres | tokens | part du bloc de contexte (423 tokens) |
| --- | ---: | ---: | ---: |
| `mene` seul | 61 | 16,9 | +4,0 % |
| **`mene` + `>1.5 MT`** | **83** | **23,1** | **+5,5 %** |
| les trois | 106 | 29,6 | +7,0 % |

`Legia Warszawa mene 13/27, >1.5 7/27 | Śląsk Wrocław mene 6/26, >1.5 12/26`

Plus le mode d'emploi au preambule, paye une fois par lot et garde par
`context_labels`.

**Contrainte de forme a porter** : `LABEL_MAX` vaut 11 caracteres et `1re MT` est
pris. Le libelle doit distinguer les deux lignes sans fabriquer un second
vocabulaire — c'est le §8, et c'est la seule difficulte de ce chantier.

#### b. Le miroir defensif de `Dom/Ext` — 100 % de couverture, 5 tokens

`_side_record` lit `goals.for.average[side]` et **jamais**
`goals.against.average[side]`. On sait ce qu'une equipe marque a domicile, jamais
ce qu'elle y encaisse.

**Couverture du champ manquant : 749 sur 749 — 100 %**, exactement la ou la ligne
sort deja.

C'est le motif de `Buts pris`, miroir de `Buts marq.`, **deja corrige une fois
pour cette raison** et dont le dossier ecrit : « `_under_over_fragment` sert les
deux cotes, pour qu'un seuil ajoute ne le soit pas d'un cote seulement ». Une
asymetrie reparee a un endroit et pas a l'autre est du §8.

#### c. Les tirs concedes — la quatrieme ligne de profil qui ne rend qu'une face

Trois lignes de profil rendent les deux faces, une seule n'en rend qu'une :

```
  Corners      Rio Ave FC 3.7 pris 5.7/3 | Sporting Lisbon 4.0 pris 3.3/3
  Fautes       Rio Ave FC 8.0 subies 11.7/3 | Sporting Lisbon 13.0 subies 17.0/3
  xG           Rio Ave FC 0.5 concédé 1.6/3 | Sporting Lisbon 2.0 concédé 0.9/3
  Tirs         Rio Ave FC 7.3 dont 2.0 cadres/3 | Sporting Lisbon 11.7 dont 6.0 cadres/3
```

`shots_against` et `shots_on_against` sont **en base**, a la meme couverture que
leurs jumeaux rendus :

| champ | couverture | rendu ? |
| --- | ---: | --- |
| `shots` / `shots_on` | 93,5 % / 98,6 % | oui |
| `shots_against` / `shots_on_against` | **93,5 % / 98,6 %** | **non** |

Ce que ca ajoute a `xG concede`, deja rendu : le **volume** face a la
**dangerosite**. « 6 tirs cadres concedes pour 0,9 xG » et « 2 tirs cadres pour
0,9 xG » ne decrivent pas la meme defense, et le bloc ne permet pas aujourd'hui
de les distinguer. Cout : ~8 tokens, aucun appel, aucun champ nouveau.

#### d. Trois candidats mineurs, mesures et ecartes

`penalty` de `/teams/statistics` (49,8 % non nul, et c'est un mois d'aout) ;
`cards.red` par tranche ; les tranches 46-75 des buts. Aucun n'a de marche que le
bloc n'eclaire deja.

### B.2 — Categorie 5 : telecharge, lu, puis jete a la reduction

**Zero appel dans les quatre cas.** C'est ce qui les distingue de la categorie 1.

| # | Ce qui est jete | Par | Ce que ca ouvrirait |
| --- | --- | --- | --- |
| e | le **classement complet** de la ligue | `context._standings_entry`, qui parcourt tout et garde 2 lignes | le niveau des adversaires |
| f | l'**adversaire** de chaque match de saison | `dossier._summarize` | le niveau des adversaires |
| g | `pos` et `grid` des feuilles | `context._sheet_names`, qui fusionne titulaires et banc | le remplacant |
| h | 10 des 18 statistiques de match | `context.PROFILE_STATS` | le gardien, entre autres |

#### Le niveau des adversaires — les deux moities sont gratuites

C'est le pendant football de `Niveau adv.` au tennis, dont le dossier ecrit qu'il
« rend `Forme` lisible : la suite de lettres traite une victoire sur le 150e
comme une victoire sur le 5e ». **Au football, `Forme 5` a exactement ce
defaut et rien ne le corrige.**

Le brief demandait de chiffrer les deux moities separement. Elles sont **toutes
les deux en categorie 5** :

- **l'adversaire** : `/fixtures?team=&season=` le sert, `_summarize` garde
  `date`, `status`, `league_id`, `at_home`, `goals`, `halftime` — et jette
  l'identite de l'adversaire. Zero appel ;
- **son niveau** : `_standings_entry` **parcourt le classement entier** pour n'en
  garder que la ligne de l'equipe interrogee. Le rang de tous les autres est
  telecharge et jete. Zero appel.

**Limite mesuree, et elle est la vraie contrainte** : sur 606 equipes, **82,4 %
des cinq derniers matchs sont dans la competition principale** — 60 % des equipes
a 5/5, mais **29 % a 3 sur 5 ou moins**. Une ligne devrait donc porter son
denominateur reel (`rang moyen 7,4 sur 4 des 5 derniers`), comme `Forme 5` porte
deja le sien.

#### Le gardien — mesure, et le resultat n'est pas celui que j'annoncais

Le brief demande d'instruire sans conclure d'avance. **Collecte de 183 matchs sur
7 competitions**, soit 366 cotes d'equipe.

| | |
| --- | ---: |
| `Goalkeeper Saves` renseigne | **366 / 366 — 100 %** |
| taux d'arret moyen | 0,650 |
| part de signal dans la dispersion | **30 %** |
| ecart-type vrai | 0,088 |
| Newcombe exclut zero (518 paires intra-competition) | **11,0 %** |

Ma reserve de l'etape 1 — « un agregat sur cinq matchs est court, la condition 3
va mordre » — **est fausse sur l'ampleur, et c'etait a mesurer** : le taux
d'arret se place **entre les deux grandeurs de production**, plus pres de `>2.5`
(41 %) que du plancher `BTTS` (24 %). Le denominateur n'est pas le match
mais le **tir cadre** — 14 tirs medians sur 3 matchs dans l'echantillon, ~22 en
regime de production a `PROFILE_LAST` = 5.

**Ce qu'il mesure, verifie plutot que suppose** :

| correlation | valeur | lecture |
| --- | ---: | --- |
| taux d'arret x tirs cadres subis par match | **+0,04** | il ne mesure **pas** la domination adverse |
| taux d'arret x xG concede par tir cadre | −0,27 | il n'est pas la seule qualite des tirs |
| taux d'arret x buts encaisses par match | **−0,68** | **il redit largement `Buts pris` et `Clean sheet`** |

**Le −0,68 est ce qui le retrograde, et il faut lire exactement ce qu'il dit.**

La lecture tentante est « le taux d'arret mesure ce que la defense concede avant
le gardien ». **Elle est mesuree et elle ne tient pas** : c'est le −0,27 avec la
**qualite des tirs subis** qui porterait cette lecture, et il explique **7 %** de
la variance du taux. Le taux d'arret n'est donc pas majoritairement la defense.

Le −0,68, lui, est **arithmetique**. `buts encaisses = volume × (1 − taux)`, et
les deux facteurs sont independants (r = +0,04). Simulation de controle : en
tirant des taux d'arret **au hasard**, sans aucun lien avec quoi que ce soit, et
en calculant les buts par cette identite, le `r` median vaut **−0,75**,
intervalle a 95 % **[−0,82 ; −0,68]**. Le `−0,68` observe est **a la borne la
plus faible de ce que le pur hasard produit**.

> Ce que le −0,68 etablit n'est donc pas une erreur d'attribution mais un
> **doublon partiel** : le taux d'arret explique 46 % de la variance d'une
> grandeur que `Buts pris` et `Clean sheet` rendent deja.

**Consequence pour l'ordre de livraison, et elle n'est pas celle qu'on attend.**
Les tirs concedes passent **avant** le taux d'arret : deja en base, ils reparent
une asymetrie au lieu d'ajouter une grandeur. Mais une fois le volume rendu, le
taux d'arret **cesse d'etre un doublon** — il devient le second facteur, exact et
independant (r = +0,04), de la decomposition `buts = volume × (1 − taux)`. Le
n° 3 le rend donc **plus** utile et non moins, et c'est l'inverse de ce qu'une
lecture par l'attribution aurait conclu.

Limite de couverture a connaitre : `Goalkeeper Saves` est servi sur **100 % des
7 competitions domestiques sondees** et **nul en Conference League** — les coupes
UEFA font 38 des 232 blocs profiles du regime courant (16 %).

#### Le remplacant — mesure sur 60 equipes, et ferme

Le brief formule la seule version honnete : non pas un remplacant probable — ce
serait une inference, et le dossier n'infere pas — mais **qui a occupe cette
place lors des dernieres feuilles quand le titulaire manquait**. Un fait date.

**La matiere existe et se reunit sans multiplier les appels.** Un appel
`/fixtures/lineups` rend les **deux** equipes : en balayant trois ligues a saison
civile plutot qu'equipe par equipe, chaque appel sert deux equipes de
l'echantillon — **153 appels pour 290 feuilles et 66 equipes**, la ou une
collecte equipe par equipe en aurait coute 396. La mesure se fait ensuite en
**fenetres glissantes** (3 feuilles d'historique, 1 cible), ce qui donne 92
points de mesure sur 60 equipes sans un appel de plus.

Les seuils rejouent ceux de `context` : titulaire sur >= `SHEETS_MIN` (2) des
trois feuilles d'historique, absent de la feuille cible.

**1. Le taux de declenchement est eleve** — et mon echantillon de quatre equipes
le sous-estimait d'un facteur trois :

| | |
| --- | ---: |
| fenetres glissantes | 92 |
| dont au moins un titulaire habituel absent | **59 (64,1 %)** |
| absences au total | 97 (1,05 par fenetre) |

**2. La decidabilite tombe a la moitie**, et pour une seule raison :

| sur les 97 absences | | |
| --- | ---: | ---: |
| formation de la feuille cible **differente** | 48 | 49,5 % |
| comparables | 49 | 50,5 % |
| dont un occupant nommable au meme grid | **49** | **100 % des comparables** |

Le `grid` n'a de sens qu'a formation constante — le `2:3` d'un 4-4-2 et le `2:3`
d'un 3-1-4-2 ne designent pas le meme poste — et la formation change une fois sur
deux.

**3. Et voici ce qui ferme : dans 71,5 % des cas, « l'occupant » jouait deja.**

| d'ou vient l'occupant du grid libere | cas | part |
| --- | ---: | ---: |
| **un autre titulaire habituel, decale** | 19 | **38,8 %** |
| un titulaire occasionnel de la fenetre | 16 | 32,7 % |
| **vu sur le banc** — le seul cas ou « remplacant » est le mot juste | 13 | **26,5 %** |
| absent des trois feuilles | 1 | 2,0 % |

> **Le fait constructible n'est pas le fait cherche.** « Y a joue a la place de
> X » est vrai au sens du grid et **faux au sens ou le lecteur l'entend** : dans
> 38,8 % des cas Y etait deja titulaire ailleurs, et le vrai entrant est a la
> place que Y vient de liberer. Dire le vrai demanderait de suivre la **chaine
> des decalages**, c'est-a-dire de modeliser une reorganisation d'equipe — une
> inference, et le dossier n'infere pas.
>
> C'est la meme erreur d'attribution que `goals_prevented`, sous une autre
> forme : le champ repond exactement a la question **par son nom**, et pas par
> son contenu. Celle-ci passerait meme la condition 2, puisqu'elle differe bien
> entre les deux equipes.

**4. La repetabilite, seule chose qui rendrait le fait utile pour le match a
venir, n'est pas mesurable** — et c'est structurel : sur 4 a 7 feuilles par
equipe, une absence **repetee et a formation constante** est rare.

| | |
| --- | ---: |
| joueurs absents sur >= 2 fenetres comparables | **3** (sur 46) |
| dont le meme occupant a chaque fois | 2 |

*(Verification faite : `Marllon` #10232 et `Marlon` #10309 sont deux joueurs
distincts, donc le troisieme cas est bien un occupant different — et non le piege
d'orthographe qui avait fabrique six fausses absences au premier releve.)*

**5. Et la construction attrape ce que le dossier a decide de ne pas nommer.**
Seules **25,8 %** des 97 absences seraient deja nommees par `Effectif`, dont la
regle exige l'absence des **deux** feuilles les plus recentes. Les 74 % restants
sont donc des **rotations d'un seul match** — precisement ce que
`SHEETS_MISSED = 2` ecarte a dessein : « une seule absence est une rotation ».

**6. La version restreinte a ete testee avant de fermer.** Le seul cas ou tout
tient — occupant **venu du banc** *et* absent deja nomme par `Effectif`, donc ni
decalage ni rotation :

> **4 cas sur 92 fenetres — 4,3 %.**

Et ces quatre-la nomment un joueur qu'`Effectif` rend **deja**, sur une ligne
existante. Une ligne de plus, son mode d'emploi au preambule et la resolution
d'une chaine de postes, pour ajouter un nom a cote d'un nom, quatre fois sur
cent, sans repetabilite mesurable.

**Verdict : ferme.** Pas sur le taux de declenchement, qui est eleve ; sur la
**nature de ce qui serait nomme**.

#### Les autres statistiques de match jetees

Sur les 18 servies par `/fixtures/statistics`, `PROFILE_STATS` en garde 8.
Couverture mesuree sur 12 sondes :

| champ | servi | phrase de section B | verdict |
| --- | ---: | --- | --- |
| `Goalkeeper Saves` | 11/12 puis **366/366** | oui | instruit ci-dessus |
| `Passes %` | 12/12 | proche de `Possession`, deja rendue | doublon partiel — ecarte |
| `Offsides` | 12/12 | « X est pris 4 fois au hors-jeu » | **aucun marche en face** — ecarte |
| `Shots insidebox` / `outsidebox` | 12/12 | qualite des occasions | `xG` le dit mieux et il est rendu — ecarte |
| `Blocked Shots` | 12/12 | — | ecarte |
| `Total passes` / `Passes accurate` | 12/12 | — | ecarte |
| `goals_prevented` | 5/12 | **ferme, §C** | — |

### B.3 — Categorie 1 : disponible, non collecte, avec un cout d'appels

**Elle est vide pour ce chantier**, et c'est un resultat. Tout ce que la mesure
designe comme atteignable est deja telecharge : `/fixtures/statistics`,
`/standings`, `/fixtures?season=` et `/fixtures/lineups` sont appeles
quotidiennement et portent les champs manquants. Aucun endpoint neuf n'est
necessaire.

La seule extension qui couterait des appels est la **typologie des buts**
(`/fixtures/events`, un appel par match passe, soit `PROFILE_LAST` x 2 par
affiche) — et elle ne sert que penaltys et csc, jamais « sur corner ». Voir §C.

### B.4 — Categorie 3 : la recherche, et la fiche la designe deja

Sept facteurs. **Six sont deja nommes dans « CE QU'IL FAUT VERIFIER »** :
retours de blessure et selections (« Effectif »), conference de presse et
rotation (« Calendrier », plus le critere `_rotation_reasons`), enjeu reel,
affluence et altitude (« Conditions »), pertinence du H2H.

Le septieme — **la pression sur le poste d'un entraineur** — n'y est pas
nommement, et il est couvert de fait par « Conference de presse et declarations
de l'entraineur ».

Critere de recherche qui existe deja et vise ces facteurs : `_squad_reasons`
(`WEAK`), `_rotation_reasons` (`WEAK`), `_coach_reasons` (`MEDIUM`),
`_venue_reasons` (`MEDIUM`).

### B.5 — Categorie 4 : hors de portee, fermees avec leur mesure

| Facteur | Ce qui ferme |
| --- | --- |
| sources de buts (jeu place, CPA, transitions) | `/fixtures/events` sert `Normal Goal`, `Penalty`, `Own Goal` — **jamais la phase de jeu**. Aucun endpoint ne la porte |
| vulnerabilite aux centres, a la profondeur | aucune source dans le catalogue du fournisseur |
| types de buts encaisses | idem |
| pressing haut / bloc bas | pas de position moyenne ni de PPDA servis ; `Possession` + `Fautes` sont la seule procuration, et elles sont deja rendues |
| altitude | **zero cas dans le corpus** : sur 439 relevés meteo, aucune competition andine (Bresil 13 matchs, rien d'autre en Amerique du Sud). Deja dans la fiche de verification, a sa place |
| profondeur de banc | le banc est collecte (`bench`), mais « profondeur » suppose une **valeur** des remplacants qu'aucune source ne sert. Le nombre seul est toujours 9 |

---

## C. Ce que le §9 bis ecarte

Cette section existe parce qu'un critere qui n'exclut jamais rien n'en est pas un.

### `goals_prevented` — ferme par la mesure, et il faut l'ecrire pour qu'il ne soit pas rouvert

**C'est le champ dont le nom repond exactement a la question posee** — « le
gardien est-il en reussite » — et c'est pour ca qu'il sera rouvert si la mesure
n'est pas ecrite.

Il est servi. Il ne peut pas entrer :

| | |
| --- | ---: |
| matchs ou il est servi | 158 / 183 (86 %) |
| dont **valeur identique pour les deux equipes** | **158 / 158 — 100 %** |
| concordance servi/nul avec `expected_goals` | 178 / 183 (97 %) |
| Super League chinoise | **0 / 25** |

**Ce n'est pas une valeur d'equipe.** La condition 2 du §9 bis — « il est rendu en
contraste entre les deux equipes de la rencontre » — ne peut pas etre satisfaite :
il n'y a rien a contraster, le fournisseur sert le meme nombre des deux cotes.
Premiere sonde : 5 cas sur 5. Verification : **158 sur 158**.

Corollaire : sa couverture suit `expected_goals` a 97 %, donc la ligne se serait
tue exactement la ou `xG` se tait deja. Meme si la condition 2 tombait un jour, il
n'ajouterait aucune population.

### Le remplacant — ferme par la repartition de l'occupant

Detail et mesures en §B.2. Ce qui ferme, en une ligne : **38,8 % des
« occupants » sont un autre titulaire habituel qui s'est decale**, et 71,5 %
jouaient deja. Le fait est vrai au sens du grid et faux au sens ou le lecteur
l'entend ; le dire juste demanderait de suivre la chaine des decalages, donc
d'inferer.

La version ou le mot est juste — occupant venu du banc, absent deja nomme par
`Effectif` — a ete testee **avant** de fermer : **4 cas sur 92 fenetres**, et
chacun nomme un joueur qu'`Effectif` rend deja.

**Ce n'est ni le taux de declenchement (64,1 %) ni la couverture (100 % des
comparables) qui ferment.** Une fermeture prononcee sur l'un des deux aurait ete
commode ; celle-ci porte sur la nature de ce qui serait nomme, et elle ne bouge
pas avec l'echantillon.

### Cinq facteurs invariants — aucune phrase de section B possible

La regle du §9 bis : « une caracteristique qui ne varie pas entre deux matchs ne
peut ni porter ni invalider un angle ». Elle a ferme le style de jeu au tennis ;
elle ferme ici :

- **philosophie de jeu et systeme habituel** — `Formations` porte deja le fait
  (`3-1-4-2 (5)/5`), et la philosophie est ce fait plus un jugement ;
- **experience de l'entraineur** — invariante a l'echelle d'une saison ;
- **historique de l'entraineur face a cet adversaire** — c'est le `H2H` sous un
  autre nom, avec un echantillon plus court ;
- **rivalite, derby, revanche** — une rivalite ne change pas d'un match a
  l'autre ; elle decrit le decor ;
- **profondeur de banc** — invariante sur une fenetre de mercato.

### Deux jugements sans procuration mesurable

**La pression sur le poste** et **la motivation asymetrique**. J'ai cherche la
procuration avant de conclure : serie de resultats (`Serie`), ecart au classement
(`Classement`), date de prise de fonction (`Entraineur`), echeance
(`Calendrier`). **Chacune est deja rendue separement ; les agreger en « pression »
serait produire un jugement que rien ne gage.**

### Ce que le critere **n'ecarte pas**, et pourquoi c'est un resultat

`mene MT` et `>1.5 MT` passent les trois conditions de la forme admissible :
fenetre et denominateurs ecrits (`6/20`), contraste entre les deux equipes,
et — pour la forme **juxtaposee** — rien n'est affirme qui doive etre gage.

> **Une clarification du §9 bis que ce chantier impose.** La condition 3 (« le
> contraste se tait quand son intervalle englobe zero ») ne s'applique qu'a une
> ligne qui **affirme** un ecart, comme `Ecart` au tennis. Une ligne qui
> **juxtapose** deux fractions n'affirme rien : `Total buts` rend `29/56` et
> `23/50` cote a cote sans dire laquelle est plus grande, et le lecteur juge.
>
> Appliquee sans cette nuance, la condition 3 condamnerait **cinq lignes en
> production** — `Total buts`, `Buts marq.`, `Buts pris`, `Clean sheet`,
> `1re MT` — dont aucune ne passerait : leurs intervalles de Newcombe excluent
> zero sur 7 a 10 % des paires reelles.
>
> Ce qui remplace la condition 3 sur la forme juxtaposee est la **part de
> signal**, et c'est la mesure de §B.1. Deux fractions juxtaposees dont 85 % de
> l'ecart est du bruit invitent quand meme a lire une difference qui n'existe
> pas.

---

## D. Recommandation, ordonnee par ce que le facteur change sur ce qu'il coute

La categorie 5 partage la premiere place avec la categorie 2 : les deux coutent
zero appel, et la seule difference est qu'il faut elargir une reduction au lieu
de lire une colonne.

| # | Chantier | Cat. | Cout | Population | Ce qui le porte |
| ---: | --- | :---: | --- | ---: | --- |
| 1 | **Le score a la pause** — `mene MT` + `>1.5 MT` | 2 | 0 appel, **23 tokens/bloc** | **99,3 %** | 24 740 lignes en base ; 4 marches sur ~50 % des blocs, **64 % sans aucune ligne MT** ; `mene MT` se place entre `Clean sheet` et `>2.5`, `>1.5 MT` au-dessus du plancher |
| 2 | **Le miroir defensif de `Dom/Ext`** | 2 | 0 appel, **5 tokens** | **91,3 %** | 100 % de couverture ; asymetrie deja reparee sur `Buts marq.`/`Buts pris` et pas ici — §8 |
| 3 | **Les tirs concedes** sur `Tirs` | 2 | 0 appel, **8 tokens** | **80,7 %** | 3 lignes de profil sur 4 rendent les deux faces ; `shots_on_against` a 98,6 % ; c'est le facteur **independant** du taux d'arret |
| 4 | **Le niveau des adversaires** | 5 | 0 appel, 2 reductions a elargir | ~82 % de la fenetre | pendant de `Niveau adv.` au tennis ; `Forme 5` traite une victoire sur le dernier comme une victoire sur le premier |
| 5 | **Le taux d'arret du gardien** | 5 | 0 appel, 1 entree dans `PROFILE_STATS` | 100 % hors coupes UEFA | 30 % de signal, entre les deux references de production. Doublon partiel de `Buts pris` **tant que le volume n'est pas rendu** — le n° 3 le rend plus utile, jamais moins |
| — | *Le remplacant* | — | — | — | **ferme** — 60 equipes, 92 fenetres : le fait constructible n'est pas le fait cherche (§B.2) |

**Les trois premiers se livrent ensemble** : meme nature — une donnee deja acquise
que le rendu ne lit pas — meme absence de risque, et un total de **36 tokens par
bloc**, soit +8,5 % du bloc de contexte.

Le n° 2 est un correctif de §8 plutot qu'un ajout : livrer le n° 1 sans lui
laisserait la meme asymetrie a l'endroit ou l'on vient de gagner en justesse.

---

## E. Ce que la mesure ne tranche pas, et le `n` qu'il faudrait

### 1. Ce qui rouvrirait le remplacant, et rien d'autre

Le taux de declenchement **a ete mesure** et il ne ferme rien : 64,1 % des
fenetres. Ce qui ferme est la nature de l'occupant, et **cet argument ne depend
pas de l'echantillon** — un titulaire habituel qui se decale reste un titulaire
qui se decale, quel que soit le `n`.

Ce qui rouvrirait, et il faut les deux ensemble :

1. **une repetabilite elevee**, mesuree sur un corpus assez long pour la porter.
   Il faut des absences repetees **a formation constante** : mon corpus en donne
   3 sur 46. Sur une saison entiere de trois ligues — ~700 feuilles, donc
   ~350 appels — l'ordre de grandeur attendu est 30 a 40 cas, ce qui suffirait a
   une proportion a +/- 15 points. C'est peu, et c'est ce que la structure du
   probleme permet ;
2. **une facon de nommer le decalage sans l'inferer.** Elle n'existe pas
   aujourd'hui : suivre la chaine des postes liberes est un modele de
   reorganisation d'equipe, pas une lecture.

**Le second est le verrou.** Sans lui, une repetabilite meme parfaite ne
donnerait qu'un fait vrai sur le grid et trompeur sur le sens — et c'est
exactement le genre de ligne que ce projet retire ailleurs.

### 2. Ce que le volume de tirs concedes apporte au-dela des buts encaisses

Le n° 3 est recommande sur une asymetrie de rendu (§8) et sur son independance
mesuree au taux d'arret (r = +0,04). Ce qui **n'est pas** mesure est sa
correlation propre a `Buts pris`, faute de rapprochement entre `context:profile`
(indexe par evenement, sans identifiant d'equipe exploitable dans ma copie) et
`team_context:season`. Il faudrait ce rapprochement sur ~200 equipes.

Consequence : si cette correlation s'averait aussi forte que celle du taux
d'arret (−0,68), le n° 3 deviendrait lui aussi un doublon partiel — mais il
resterait justifie par l'asymetrie, qui ne depend d'aucune mesure.

### 3. Les neuf lignes de saison hors mois d'aout

Toutes les mesures de couverture des lignes gardees par `SEASON_MIN_MATCHES`
portent sur une **reprise de championnat**. `Buts marq.`, `Buts pris`,
`Clean sheet`, `1re MT`, `Buts tard.`, `Cartons tps` et `Formations` plafonnent a
32,7 % **pour cette raison**, et le taux de croisiere n'est pas mesurable avant
octobre. La ligne du n° 1, elle, echappe a ce plafond : elle sort de
`_history`, qui replie sur la saison precedente.

### 4. Ce que la decomposition des lignes de profil ne tranche pas encore

**La mesure a ete faite** — voir « Le plancher n'etait pas celui que je
croyais » — et elle a inverse un arbitrage. Ce qui reste ouvert est sa partie
la plus fragile.

Les lignes de **profil** (`Corners`, `Tirs`, `xG`, `Possession`, `Fautes`) sont
des moyennes sur cinq matchs, et leur charge utile est **deja agregee en base** :
`context:profile` ne porte plus les valeurs match par match. Leur decomposition
n'a donc pu se faire que sur les 183 matchs collectes ici, soit **3 observations
par equipe** — un effectif ou l'estimation de la variance intra est instable.

Les chiffres obtenus (`xG` produit 70 %, `xG` concede 63 %, tirs cadres concedes
65 %) vont tous dans le meme sens, celui d'un signal eleve, et ils **ne
descendent donc pas le plancher**. Mais ils ne sont pas assez surs pour servir de
reference a un candidat futur.

Ce qu'il faudrait : ~10 observations par equipe sur ~100 equipes, soit
~500 appels `/fixtures/statistics` — ou, sans un appel, **elargir la reduction de
`context:profile` pour garder les valeurs par match** plutot que leur seule
moyenne. La seconde voie est un chantier de categorie 5, et elle rendrait la
mesure disponible en permanence au lieu d'une fois.

### 5. Ce qu'un facteur ajoute change reellement

Aucune des mesures de ce document ne dit qu'une ligne de plus **ameliore une
selection**. Elles disent qu'une grandeur discrimine entre equipes et qu'un
marche existe en face. Le lien entre les deux — le rendement mesure d'une ligne —
est la meme mesure manquante que celle qui bloque l'affaiblissement des deux
criteres faibles du football, et elle suppose de relier une ligne rendue a la
selection qui en est sortie.

**C'est la porte ouverte et datee de ce chantier comme du precedent.**
