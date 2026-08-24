# Phase 1 — addendum : cinq questions sur ce que 1.4 a produit

Deux defauts de **ma propre mesure** ont ete trouves en repondant, et ils
changent les chiffres de la phase 1. Ils sont corriges ici et le document
principal est a lire avec cet addendum.

---

## 1. Les 159 cotes a 1.00 n'ont pas contamine l'overround

**Elles etaient exclues.** Le filtre `price > 1.0` figure dans les trois vues de
reconstruction (`14_overround_complet.sql`, lignes 10, 18, 30). Trois verifications
supplementaires, parce que la question merite mieux qu'une relecture de code :

**Aucune n'est la cote d'une selection.** `picks.price` va de **1,25** a 11,75,
et **zero** pick est a 1,00. Les 159 sont donc toutes des **issues adverses**
dans un marche, jamais un prix retenu.

**Aucune n'est chez `betclic_fr`.** Elles viennent de `superbet` (120) et
`bet365` (39) — les books de substitution. Le book qui porte la queue n'en a pas une.

**Les deux lectures, cote a cote** (`14b_contamination.sql`) :

| Sport / book | AVEC les 1.00 | | SANS les 1.00 | |
| --- | ---: | ---: | ---: | ---: |
| | livres | moyenne | livres | moyenne |
| football `betclic_fr` | 329 | **8,21** | 329 | **8,21** |
| football `superbet` | 47 | 7,26 | 46 | 7,33 |
| football `10bet` | 47 | 7,00 | 47 | 7,00 |
| football `pinnacle` | 46 | 4,27 | 46 | 4,27 |
| tennis `betclic_fr` | 160 | 4,43 | 160 | 4,43 |

**`betclic_fr` est identique au centieme, maximum compris (13,92).** Une seule
ligne a 1,00 touche un livre `h2h` (superbet), et l'exclure fait perdre ce livre
entier au filtre de completude — d'ou 47 → 46.

Sur les familles de totaux, ou vivent les 158 autres, l'effet existe et **va dans
l'autre sens que celui redoute** :

| Marche | AVEC | SANS |
| --- | ---: | ---: |
| `team_totals` | 7,22 (1 043 livres) | **7,51** (964) |
| `totals` | 5,80 (1 065) | **5,88** (1 027) |
| `totals_h1` | 6,41 (571) | **6,61** (536) |
| `alternate_totals` | 3,91 (3 807) | 3,91 (3 807) |

Une cote a 1,00 est appariee a une cote tres longue de l'autre cote : leur somme
approche 1, donc les inclure **abaisse** la moyenne. Les maxima ne bougent
d'aucun cote.

**Conclusion : la queue Betclic football a ≥10 points est reelle.** 62 livres sur
329, sur trois cent vingt-neuf livres a trois issues chacun servis par le book
principal. Ce n'est pas un artefact de substitution.

> Ce qui reste vrai des 159 : **une cote decimale de 1,00 est un rendement nul**,
> et rien ne dit qu'elles soient ecartees du **rendu**. Le point reste ouvert
> pour la phase 4.

---

## 2. La couverture — et deux defauts de ma mesure

Le chiffre annonce en phase 1 (36,7 %) etait **faux, par ma faute deux fois**.

### Defaut 1 — je lisais la colonne stockee, pas la cle effective

Les 120 `market_key` nulles sont **entierement anterieures a la migration 033**,
appliquee le **11/08/2026 a 18:52**. La resolution passe de 0 % (sessions 2-7) a
100 % des la session 9 ; la session 8 est la bascule (1 resolue, 4 non).

L'application les resout **a la lecture** (`market_key_effective`) ; ma requete
lisait la colonne. En rejouant la meme regle (`audit/resolve_mk.py`),
**111 des 120 se resolvent** — 9 restent irreductibles, tous des libelles hors
vocabulaire (`Double chance` la ou le bloc ecrit `DC`, `Éq. buts`, `Handicap (ligne posable)`).

### Defaut 2 — j'ignorais la fusion de marches

`render.MERGED_MARKETS` fusionne `alternate_totals` avec `totals` et `spreads`
avec `alternate_spreads` **dans une seule ligne du bloc**. Une selection etiquetee
`O/U 2.5` porte donc `market_key = totals` alors que le releve fige ne contient
peut-etre que `alternate_totals`. Ma recherche sur la seule cle stockee declarait
**« marche non fige » 57 fois a tort**, et ces 57 fausses non-couvertes portaient
un residu de **+12,36 pts/selection** — un signal spectaculaire et entierement
fabrique par ma requete.

> **A retenir plus que le chiffre** : une non-couverture qui correle fortement
> avec l'issue est d'abord un defaut d'appariement. C'est exactement la regle du
> depot sur le zero de rapprochement, transposee.

### Couverture reelle, apres correction

| Population | Tranchees | `market_key` effectif | **Overround attribue** |
| --- | ---: | ---: | ---: |
| section C | 401 | 392 | **249 (62,1 %)** |
| section C-bis | 90 | 90 | **77 (85,6 %)** |

Sur les **491 tranchees**, **326 portent un overround mesure (66,4 %)**.

### Detail par marche, section C (tranchees)

| Marche | n | avec overround |
| --- | ---: | ---: |
| `h2h` | 89 | 78 |
| `alternate_spreads` | 79 | 54 |
| `totals` | 76 | 13 → **corrige** |
| `btts` | 20 | 0 → **corrige** |
| `double_chance` | 12 | 0 → **corrige** |
| `(sans market_key)` | 119 | 0 → **111 resolus** |

### Les 120 sans `market_key` ne sont **pas** concentrees sur les exotiques

C'est la question posee, et la reponse est non :

| Famille | n |
| --- | ---: |
| **h2h (issue)** | **54** |
| handicap | 29 |
| total | 24 |
| total d'equipe | 5 |
| double chance | 4 |
| btts | 4 |

**45 % sont du h2h** — le marche exactement decrit par l'overround de 2,64 a
13,92 %. Leur composition ne differe pas de celle de la population couverte : la
cause est **temporelle** (anteriorite a la migration 033), pas structurelle.

### Mais les deux groupes different quand meme, et sur ce qui compte

| Section C | n | Cote moy. | **Tardives** | Residu brut |
| --- | ---: | ---: | ---: | ---: |
| **avec** overround | 249 | 1,81 | **4,8 %** | −3,40 (**−1,36** pts/sel) |
| **sans** overround | 152 | 1,78 | **26,3 %** | −9,37 (**−6,17** pts/sel) |
| ensemble | 401 | 1,80 | 13,0 % | −12,77 (−3,18 pts/sel) |

**La reponse a la question est donc : oui, les deux groupes sont
systematiquement differents, et le deficit vit disproportionnellement dans la
partie non couverte.** Le taux de tardives y est cinq fois plus eleve — la
population non couverte est essentiellement l'ere d'avant le 11/08, celle qui
portait 19,4 % de tardives.

Consequence a tenir en phase 3 : **un residu corrige sur les 249 ne decrit pas
les 401.** Les deux chiffres se donnent separement, jamais l'un pour l'autre.

---

## 3. `price_real` n'est pas renseigne au hasard — et le mecanisme n'est pas celui craint

**Le chemin d'ecriture repond seul.** Le champ de saisie n'est **rendu** que si
`pick.price_source == 'reference'` (`_pick_row.html:102`) :

```jinja
{% if coupon_tracking and pick.price_source == 'reference' %}
```

**Il est donc structurellement impossible de saisir une cote obtenue pour une
selection cotee chez le book principal.** Ce n'est pas une habitude de saisie
qui creerait un biais vers les ecarts constates : c'est une **porte
deterministe**. Les 123 paires ne sont pas « celles ou j'ai vu un ecart », ce
sont « celles ou le champ existait ».

### Dans la population eligible, le remplissage ressemble a du hasard

`price_source = 'reference'` : 241 en section C, 35 en C-bis.

| Section C, eligibles | n | Cote | Confiance | Taux | Fenetre |
| --- | ---: | ---: | ---: | ---: | --- |
| **remplies** | 123 (51 %) | 1,80 | 3,06 | **61,8 %** | 10→24/08 |
| **non remplies** | 118 | 1,83 | 3,27 | **60,7 %** | 13→24/08 |

**Indiscernables sur toutes les dimensions, y compris l'issue** — 1,1 point de
taux d'ecart. Dans la population eligible, l'imputation est donc defendable.

### Ce que l'ecart de −6,4 % ne decrit pas

Il decrit **les prix de book de reference**, soit 241 des 428 selections de
section C (56 %). Pour les 144 cotees `betclic` et les 103 sans source declaree,
**`price` est deja la cote du book principal** : l'ecart attendu y est proche de
zero *par construction*, et lui appliquer −6,4 % serait inventer un biais.

Le bootstrap ci-dessous n'impute donc l'ecart **qu'aux eligibles**.

---

## 4. Le signe, en toutes lettres — et une correction de ce que j'ai ecrit

> **Je me suis trompe en phase 1 en ecrivant que « la correction de la cote
> obtenue domine ».** C'etait vrai sur les 37 selections portant les deux
> informations ; sur la population complete, **les deux corrections s'annulent
> presque**, et c'est la marge qui pese le plus.

### Sur la population ou l'overround est mesure (249, section C)

| | Victoires impliquees | Residu | Par selection |
| --- | ---: | ---: | ---: |
| **brut** — ce que lit l'appli | 140,40 | **−3,40** | −1,36 pts |
| **corrige** — marge retiree + cote obtenue imputee | 140,55 | **−3,55** | −1,43 pts |

**Apres correction, le residu est MOINS favorable, de 0,15 victoire — soit 0,06
point par selection.** C'est negligeable : la correction de marge le rend plus
favorable de ~3,6 victoires, celle de la cote obtenue moins favorable de ~3,8, et
les deux se compensent.

### Intervalle bootstrap, incertitude des deux corrections propagee

10 000 tirages, graine fixe. A chaque tirage : reechantillonnage des selections
**et** retirage des ecarts de cote obtenue dans leur distribution empirique.

| Section | Point | **IC 95 %** | Par selection | P(residu ≥ 0) |
| --- | ---: | :---: | :---: | ---: |
| **C** (n=249) | −3,55 | **[−18,54 ; +11,05]** | [−7,45 ; +4,44] pts | **0,314** |
| **C-bis** (n=77) | +2,12 | **[−5,44 ; +9,81]** | [−7,06 ; +12,74] pts | 0,698 |

**L'intervalle englobe zero, largement, dans les deux sections. C'est le
resultat, et il s'ecrit tel quel : sur la population ou l'overround est
reellement mesure, aucun deficit n'est etabli.**

### D'ou vient la largeur — et elle ne vient pas des corrections

| Source d'incertitude | Intervalle | Largeur |
| --- | :---: | ---: |
| variance des **issues** seule | [−18,50 ; +11,58] | **30,08** |
| incertitude des **corrections** seule | [−4,33 ; −3,00] | **1,33** |

**Les deux corrections comptent pour environ 4 % de la largeur.** L'incertitude
est ecrasee par la variance d'echantillonnage de 249 issues binaires. Autrement
dit : mieux mesurer l'overround ou completer `price_real` ne resserrera **pas**
cet intervalle — seul du volume le fera.

### Sur la population que l'appli publie (349, anteriorite etablie)

Sensibilite, **hypothese assumee** : l'overround moyen par famille de marche est
impute aux selections sans livre reconstructible.

| | Residu | Par selection |
| --- | ---: | ---: |
| publie aujourd'hui (aucune correction) | **−16,00** | −4,59 pts |
| les deux corrections, avec imputation | **−13,75** | −3,94 pts |

Sur **cette** population, le signe est inverse du precedent : la correction rend
le residu **plus favorable de 2,25 victoires (0,65 point par selection)**, et le
deficit **persiste nettement**.

**Les deux resultats ne se contredisent pas : ils portent sur deux populations.**
Les 100 selections presentes ici et absentes des 249 sont majoritairement de
l'ere d'avant le 11/08, et elles portent l'essentiel du deficit. Dire lequel des
deux chiffres decrit « la methode » demande de trancher si cette ere doit
compter — question de phase 3, pas d'integrite.

---

## 5. Lignes entieres et lignes en quart, croisees avec les sections

### Le comptage

| Famille | Ligne | Section C | Section C-bis |
| --- | --- | ---: | ---: |
| handicap | demi (`.5`) | 88 | 2 |
| handicap | **entiere (`.0`)** | **30** | **3** |
| handicap | **quart (`.25`/`.75`)** | **4** | 0 |
| total | demi | 105 | 6 |
| total | **entiere** | **6** | **2** |

**41 selections sur ligne entiere, dont 36 en section C.** Elles ne sont donc
**pas** concentrees en C-bis : l'impact porte sur la population principale.

### Combien tombent exactement sur la ligne — et comment elles sont enregistrees

| Section | `win` | `loss` | **`void`** | `pending` |
| --- | ---: | ---: | ---: | ---: |
| C | 14 | 11 | **9** | 2 |
| C-bis | 1 | 4 | 0 | 0 |

**Les 9 push sont enregistres `void`, et c'est le traitement correct** — mise
rendue, ligne hors du denominateur. Ce n'est pas un rattrapage manuel : le moteur
de reglement le fait de lui-meme.

```python
marge = (pour - contre if camp == "home" else contre - pour) + ligne
if marge > 0: return WIN
if marge < 0: return LOSS
return VOID
```

Le docstring le dit en propre : *« le remboursement sur ligne entiere touchee est
un etat a part, et c'est tout l'objet de cette famille : `Örgryte 0` sur un nul
n'est ni gagne ni perdu, et le ranger avec l'un des deux fausserait le residu au
prix. »* **Conforme, et l'intention est ecrite.**

### Les lignes en quart : population close, et documentee

Le meme moteur **refuse** de trancher une ligne en quart :

```python
if abs(ligne * 4) % 2 == 1:
    return None   # pari scinde, aucun verdict unique
```

Les quatre selections concernees datent du **07 et 08/08**, sessions 4 et 5,
soit **avant** la mise en service de la regle (commit `9180a03`, 20/08) et avant
l'interdiction du gabarit. Elles ont ete tranchees **a la main**, en binaire, et
le docstring anticipe exactement ce cas : *« celles qui restent en base sont
anterieures a cette regle et se tranchent a la main. »*

**Population close : aucune nouvelle depuis le 08/08.** Ce qui reste est que ces
quatre lignes portent un `win`/`loss` la ou la valeur juste serait un demi-gain
ou une demi-perte — 4 sur 428, soit 0,9 % de la section C, et l'effet sur le
residu est sous le bruit.

---

## Ce que cet addendum change au document principal

| Chiffre de la phase 1 | Corrige |
| --- | --- |
| couverture section C : 36,7 % | **62,1 %** |
| couverture section C-bis : 75,6 % | **85,6 %** |
| residu C brut : −10,84 sur 147 | **−3,40 sur 249** |
| residu C corrige : −6,70 | **−3,55**, IC **[−18,54 ; +11,05]** |
| « la correction de la cote obtenue domine » | **faux** — elles s'annulent presque |
| « la marge explique 38 % du deficit » | valable pour l'ancienne population ; **sans objet** sur la nouvelle, ou le deficit n'est plus etabli |

Les mesures d'overround par marche, par book et leur distribution **ne changent
pas** : elles ne dependaient pas de l'appariement des selections.
