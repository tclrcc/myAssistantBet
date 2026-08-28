# 05 — Le rendement d'une ligne : mesurable ou non

> **Question posee d'emblee, parce qu'elle decide de la faisabilite** : un
> `angle_note` qui mentionne une grandeur prouve-t-il que la ligne l'a **portee**,
> ou seulement qu'elle etait **lisible** ? Si les deux ne se distinguent pas, le
> rendement n'est pas mesurable, et il faut le dire avant d'y passer un chantier.

**Mesures du 28/08/2026**, sur une copie `VACUUM INTO` de la base servie. Aucun
appel externe : tout ce qui suit se lit dans ce qui est deja archive.

**Pourquoi ce chantier existe** : deux lots ont bute sur ce trou a trois jours
d'intervalle — l'affaiblissement des deux criteres faibles du football (04/08 du
dossier) et l'admission des lignes du bloc (audit 04). Les deux se sont conclus
par « il faudrait relier une ligne rendue a la selection qui en est sortie ».
Une mesure reportee deux fois se pose pour elle-meme.

**Ce qui est en jeu** : le plancher d'admission etabli au chapitre 04 dit qu'une
ligne **separe les equipes**. Il ne dit pas qu'un analyste s'en est servi, ni que
la decision en a ete meilleure. Sans rendement, toute admission future se juge
sur une discrimination statistique et jamais sur un effet.

---

## Ce que la base permet de relier

| | |
| --- | ---: |
| selections en base | 615 |
| avec `angle_note` renseigne | 380 |
| avec `prompt_id` | 398 |
| **football, prose + prompt** | **289** |
| dont le bloc est retrouve dans son prompt | **289 — 100 %** |
| football, population propre du residu | **201** |

La population propre est celle du bloc de tete de `/stats` : section C,
anteriorite etablie, tranchee, cotee. C'est la seule comparable au chiffre publie.

Le rattachement est **complet** : 289 sur 289. `picks.prompt_id` designe le
prompt qui a valide le collage, et l'affiche de l'evenement retrouve le bloc dans
son corps archive. Rien n'a ete perdu a cette etape, ce qui est rare et merite
d'etre dit — le chantier ne bute pas sur la matiere.

---

## Voie 1 — la prose prouve-t-elle la lecture ?

Le test est un **contraste**, et il n'y en a pas d'autre :

    p1 = P(la prose nomme la grandeur | la ligne est rendue dans ce bloc)
    p0 = P(la prose nomme la grandeur | la ligne est absente de ce bloc)

`p1 ~ p0` veut dire que la mention vient d'ailleurs — de la recherche, ou d'une
connaissance — et qu'elle ne prouve rien sur la ligne.

Sur les 289 selections, dix-sept lignes testees, motifs de prose volontairement
etroits — un motif large mesurerait le vocabulaire du francais :

| ligne | rendue | absente | p1 | p0 | ecart | IC Newcombe | Fisher |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| **`Pelouse`** | 27 | 262 | **0,41** | 0,01 | **+0,40** | **[+0,24 ; +0,59]** | **0,0000** |
| **`Buts pris`** | 82 | 207 | **0,32** | 0,17 | **+0,15** | **[+0,04 ; +0,26]** | **0,0068** |
| `Meteo` | 242 | 47 | 0,08 | 0,00 | +0,08 | [−0,00 ; +0,12] | 0,0511 |
| `H2H` | 251 | 38 | 0,07 | 0,00 | +0,07 | [−0,02 ; +0,11] | 0,1435 |
| `xG` | 159 | 130 | 0,06 | 0,02 | +0,04 | [−0,01 ; +0,09] | 0,1190 |
| `Buts marq.` | 82 | 207 | 0,06 | 0,03 | +0,03 | [−0,02 ; +0,10] | 0,3307 |
| `Tirs` | 169 | 120 | 0,02 | 0,01 | +0,02 | [−0,02 ; +0,05] | 0,4065 |
| `Enjeu` | 120 | 169 | 0,04 | 0,04 | +0,01 | [−0,04 ; +0,06] | 0,7668 |
| `Possession` | 170 | 119 | 0,02 | 0,01 | +0,01 | [−0,03 ; +0,04] | 0,6457 |
| `Effectif` | 78 | 211 | **0,00** | **0,00** | +0,00 | [−0,02 ; +0,05] | 1,0000 |
| `Corners` | 170 | 119 | **0,00** | **0,00** | +0,00 | [−0,03 ; +0,02] | 1,0000 |
| `Cartons` | 170 | 119 | **0,00** | **0,00** | +0,00 | [−0,03 ; +0,02] | 1,0000 |
| `Formations` | 80 | 209 | 0,00 | 0,02 | **−0,02** | [−0,05 ; +0,02] | 0,3271 |
| `Serie` | 75 | 214 | 0,00 | 0,02 | **−0,02** | [−0,05 ; +0,03] | 0,3319 |
| `Classement` | 247 | 42 | 0,00 | 0,02 | **−0,02** | [−0,12 ; +0,00] | 0,1453 |
| `Repos` | 289 | 0 | — | — | — | *contraste impossible* | — |
| `Calendrier` | 287 | 2 | — | — | — | *contraste impossible* | — |

**La reponse a la question posee est : parfois, et rarement.** Deux lignes sur
dix-sept produisent un ecart etabli, donc la mention n'est pas systematiquement
du bruit — mais quinze n'en produisent aucun, et trois ont un ecart **negatif**.

Quatre lectures, dans l'ordre de ce qu'elles coutent :

- **`Pelouse` et `Buts pris` partagent une propriete** : elles nomment un fait que
  **rien d'autre dans le bloc ne dit** et que la recherche ne ramene pas
  spontanement. « Synthetique » est un mot rare et exclusif. Partout ailleurs
  l'information existe en double — `Forme 5` porte deja des buts, `Tirs` et `xG`
  disent la meme production sous deux angles ;
- **`Buts pris` a pourtant `p0 = 0,17`** : une mention sur deux vient d'ailleurs
  meme quand l'ecart est etabli. L'ecart existe, l'attribution reste partielle ;
- **trois lignes ne sont jamais mentionnees**, rendues ou non — `Corners`,
  `Cartons`, `Effectif` a 0,00 des deux cotes. Ce n'est pas qu'elles ne servent
  pas : c'est qu'un `angle_note` fait **une ligne** et que vingt-six lignes du
  bloc se disputent une phrase. **C'est un plafond structurel, pas un effectif** ;
- **les trois ecarts negatifs disent le mecanisme** : quand `Serie` est absente,
  la prose parle quand meme de series — depuis `Forme 5`, ou depuis la recherche.
  La mention ne peut donc pas etre attribuee a la ligne.

> **Verdict voie 1 : la prose ne peut pas porter la mesure.** Elle le peut pour
> une ligne qui nomme un fait rare et exclusif, ce qui est le contraire du cas
> general.

---

## Voie 2 — la ligne change-t-elle la probabilite qu'une selection sorte ?

Contraste sur les **1 327 blocs** et non sur les 289 selections, donc bien plus
puissant. Et il vise quelque chose de plus proche du rendement : la ligne
a-t-elle contribue a ce qu'un angle se forme.

**Elle porte un confondant massif, et il fallait le controler** : un bloc dense
produit plus de selections, et les lignes arrivent par paquets — les memes appels
les servent toutes. Le controle est donc **stratifie sur la densite du bloc**,
en quartiles du nombre de lignes rendues.

| strate | blocs | taux de selection |
| ---: | ---: | ---: |
| 0 (< 9 lignes) | 294 | **0,0 %** |
| 1 (9-13) | 335 | 28,1 % |
| 2 (14-18) | 305 | 28,2 % |
| 3 (>= 19) | 393 | 37,4 % |

| ligne | ecart **brut** | ecart **stratifie** |
| --- | ---: | ---: |
| `H2H` | **+0,18** | **−0,117** |
| `Effectif` | +0,11 | −0,002 |
| `Serie` | +0,10 | −0,028 |
| `Classement` | +0,09 | −0,028 |
| `Enjeu` | +0,09 | −0,027 |
| `Tirs` | +0,06 | −0,050 |
| `Corners` | +0,05 | −0,065 |
| `Meteo` | +0,19 | +0,179 |
| `Buts pris` | +0,16 | +0,154 |
| `Dom/Ext` | +0,09 | +0,104 |

> **Paradoxe de Simpson, et il est massif.** L'ecart **change de signe** pour la
> moitie des lignes une fois la densite tenue. `H2H` passe de +0,18 a −0,117.
> L'effet brut est produit par la densite : une ligne rendue signale un bloc mieux
> servi, et rien d'autre.

Trois lignes gardent un ecart positif apres stratification — `Meteo`, `Buts pris`,
`Dom/Ext`. **Le confondant n'est pas epuise pour autant** : la densite en
quartiles est grossiere, la strate 0 est degeneree (aucune selection sur 294
blocs), et la couverture d'une ligne suit la competition, qui a ses propres taux.

> **Verdict voie 2 : l'effet mesurable est celui de la densite, pas celui d'une
> ligne.** Et la densite est deja un critere de la fiche de recherche, donc on
> mesurerait un dispositif par lui-meme.

---

## Voie 3 — le residu au prix, et ce qu'il revele

Les deux premieres voies mesurent qu'une ligne a ete **citee** ou qu'une selection
est **sortie**. Ni l'une ni l'autre ne dit qu'une decision a ete **meilleure**. Le
residu au prix le dit, et c'est le seul chiffre interpretable du projet.

Population propre : **201 selections**, 110 gagnees pour 113,85 payees, residu
**−3,85**.

| ligne | avec | residu/sel | sans | residu/sel | ecart |
| --- | ---: | ---: | ---: | ---: | ---: |
| `Pelouse` | 16 | +0,167 | 185 | −0,035 | +0,202 |
| `Dom/Ext` | 132 | +0,028 | 69 | −0,110 | +0,138 |
| `Classement` | 167 | −0,002 | 34 | −0,102 | +0,100 |
| **`Buts pris`** | **62** | **+0,023** | **139** | **−0,038** | **+0,061** |
| **`Buts marq.`** | **62** | **+0,023** | **139** | **−0,038** | **+0,061** |
| **`Formations`** | **62** | **+0,023** | **139** | **−0,038** | **+0,061** |
| **`1re MT`** | **62** | **+0,023** | **139** | **−0,038** | **+0,061** |
| **`Buts tard.`** | **62** | **+0,023** | **139** | **−0,038** | **+0,061** |
| `Serie` | 57 | +0,019 | 144 | −0,034 | +0,053 |
| `Meteo` | 162 | −0,012 | 39 | −0,049 | +0,038 |
| `xG` | 106 | −0,002 | 95 | −0,038 | +0,036 |
| `Tirs` | 110 | −0,005 | 91 | −0,036 | +0,030 |
| `H2H` | 176 | −0,023 | 25 | +0,010 | −0,033 |

**Cinq lignes rendent exactement le meme chiffre.** Ce n'est pas une coincidence,
et c'est le resultat qui ferme le chantier.

---

## Ce qui ferme : les lignes ne sont pas separables

Balayage des 1 327 blocs, a la recherche des lignes rendues sur **exactement** les
memes blocs :

| blocs | lignes indissociables |
| ---: | --- |
| 231 | `Buts marq.`, `Clean sheet`, `1re MT` |
| 228 | `Cartons tps`, `Formations` |
| 701 | `Corners`, `Cartons` |
| 572 | `Fautes`, `Possession` |
| 1 274 | `Repos`, `Absents` |

Elles viennent du **meme appel** et passent la **meme garde** — `SEASON_MIN_MATCHES`
pour les premieres, `PROFILE_MIN_MATCHES` pour les suivantes. Aucun volume de
donnees ne les separera : ce n'est pas un manque d'effectif, c'est une propriete
du plan de collecte.

> **Le rendement d'une ligne n'est donc pas identifiable.** Ce qui pourrait
> l'etre est le rendement d'un **appel** — un paquet de lignes qui arrivent
> ensemble.

### Et le rendement d'un appel n'est pas etabli non plus

| appel | groupe | n | gagnees | payees | residu/sel | P unilaterale |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `/teams/statistics` | avec | 62 | 38 | 36,58 | **+0,023** | 0,407 |
| | sans | 139 | 72 | 77,26 | **−0,038** | 0,205 |
| `/fixtures/statistics` | avec | 110 | 63 | 63,60 | −0,005 | 0,490 |
| | sans | 91 | 47 | 50,25 | −0,036 | 0,278 |

**Aucun des quatre groupes ne s'ecarte de ses prix.** L'ecart entre les deux
groupes vaut 0,061 par selection dans le meilleur cas, et il n'est etabli ni d'un
cote ni de l'autre.

**Reserve a porter, et c'est la faute que le dossier a deja payee deux fois** :
ce 0,061 est le plus grand ecart **parmi ceux que j'ai regardes**, sur une
partition choisie apres avoir vu le tableau. Il n'a pas le statut d'une hypothese
posee d'avance, et le `n` ci-dessous le suppose vrai — ce qui est deja
optimiste.

---

## L'effectif qu'il faudrait

Test bilateral a 95 %, puissance 80 %, sur une probabilite implicite moyenne de
0,55 :

| pour etablir | `n` par groupe | selections propres |
| --- | ---: | ---: |
| l'ecart observe, d = 0,061 | 1 043 | **2 086** |
| la moitie, d = 0,030 | 4 312 | 8 624 |

Disponible aujourd'hui : **201**. Au rythme mesure au dossier — 17,9 selections
propres par jour, tous sports — l'ecart d'un appel demanderait de l'ordre de
**quatre a huit mois** de collecte au football seul.

Et cette borne est **trop optimiste sur deux points** : le regime de cadre change
tous les 1,5 jour d'apres `changelog_mesure`, donc la population ne serait pas
homogene sur une telle duree ; et la couverture d'une ligne monte avec la saison,
si bien que le groupe « sans » se videra en octobre — le contraste disparaitra
avant que l'effectif soit atteint.

---

## Verdict

**Le rendement d'une ligne n'est pas mesurable, et il ne le deviendra pas.**
Trois raisons independantes, dans l'ordre de leur solidite :

1. **Les lignes ne sont pas separables.** Cinq groupes sont strictement
   indissociables sur les 1 327 blocs. C'est structurel, aucun volume n'y change
   rien, et c'est la raison qui suffit a elle seule ;
2. **la prose ne porte pas l'attribution** : deux lignes sur dix-sept, un plafond
   structurel — un `angle_note` fait une ligne, vingt-six lignes se la disputent —
   et trois ecarts negatifs qui montrent que la mention vient d'ailleurs ;
3. **l'effet mesurable est celui de la densite**, qui inverse le signe de la
   moitie des ecarts une fois tenue, et qui est deja un critere de la fiche de
   recherche.

**Ce qui reste possible, et il faut le nommer pour ne pas le confondre avec ce
qui est ferme** : le rendement d'un **appel** est identifiable en principe — le
contraste existe, 62 contre 139 — mais il demande 2 086 selections propres quand
la base en porte 201, sur une population que le changement de cadre rend
inhomogene et dont le groupe temoin se videra avec la saison.

> **Consequence pour l'admission des lignes.** Le plancher etabli au chapitre 04
> reste le seul critere disponible, et il faut le savoir pour ce qu'il est : une
> ligne admise parce qu'elle **separe les equipes** au niveau de ce que la
> production porte deja. Ce n'est pas une mesure d'effet, et la remplacer par une
> mesure d'effet n'est pas au programme — pas faute de volonte, faute de plan
> d'observation.

**Meme forme que le biais d'exposition** (dossier, 14/08) : ce n'est pas « pas
assez de donnees », c'est un plan d'observation inadapte. La difference est que
la collinearite y etait entre l'exposition et la competition ; ici elle est entre
les lignes d'un meme appel, et entre la presence d'une ligne et la densite du
bloc.

### Ce qui rouvrirait, et ce qui ne rouvrirait pas

**Ne rouvre rien** : plus de selections. La raison 1 est structurelle, et le
groupe temoin se vide a mesure que la couverture monte.

**Rouvrirait, et c'est une intervention et non une lecture** : rendre une ligne
sur un **sous-ensemble tire au sort** des blocs d'un lot, pendant quelques
semaines. Cela romprait a la fois la collinearite entre lignes d'un meme appel et
la correlation avec la densite — les deux verrous a la fois. C'est le seul plan
qui repondrait, il est realisable, et il coute de rendre volontairement des blocs
moins complets.

**Ce n'est pas propose ici.** Degrader une partie des blocs analyses pour mesurer
l'effet d'une ligne est un arbitrage qui ne se prend pas dans un audit, et son
cout — des analyses reelles faites sur moins d'information — se paie en decisions
et non en tokens.
