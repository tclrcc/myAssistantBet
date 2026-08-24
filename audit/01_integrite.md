# Phase 1 — Integrite des donnees

Lecture seule, sur `audit/data/work.db`. Chaque chiffre est produit par une
requete de `audit/sql/`. **Section C et section C-bis ne sont jamais agregees.**

Population : 523 selections (428 C, 95 C-bis), 491 tranchees, 05→24/08/2026.

---

## 1.4 — L'overround, et ce qu'il retire au residu

*Traite en premier : tant qu'il n'est pas chiffre, aucun residu n'est lisible.*

### Ce que le livre permet de mesurer

`prompt_odds` fige le marche complet a l'archivage — 41 520 lignes, 6 books.
L'overround est donc **mesure**, pas suppose. Trois familles se reconstruisent
sans ambiguite ; une quatrieme a failli passer et donnait des valeurs impossibles :

- `h2h` : toutes les issues du meme (session, evenement, book) — 3 au football, 2 au tennis ;
- `totals` et derives : Over et Under partagent **le meme** point ;
- `spreads` : les deux moities portent des points **opposes** (Shelton −2.5 ↔ Mensik +2.5).

> **Piege ecarte, et il vaut d'etre nomme.** Grouper les handicaps par `point`
> apparie deux moities de paliers differents et rend des overrounds **negatifs**
> (mesure : −24,05 pts). Meme piege sur `team_totals`, dont la cle inclut
> l'equipe (`description`) : sans elle, les Over/Under de deux equipes
> fusionnaient et rendaient **−13,97 pts**. Les deux sont corriges ; un overround
> negatif est le signal qu'un livre est mal reconstruit, jamais une donnee.

### Overround par marche

| Marche | Livres | Moyenne | Mini | Maxi |
| --- | ---: | ---: | ---: | ---: |
| `alternate_spreads` | 3 876 | **3,62** | 1,74 | 7,67 |
| `alternate_totals` | 3 807 | **3,91** | 2,26 | 7,73 |
| `totals` | 1 027 | 5,88 | 1,51 | 9,29 |
| `spreads` | 795 | 6,07 | 2,69 | 8,45 |
| `h2h` | 690 | 6,55 | 2,64 | **13,92** |
| `totals_h1` | 536 | 6,61 | 2,58 | 8,99 |
| `team_totals` | 964 | 7,51 | 2,46 | 8,99 |

### Par sport et par book, sur `h2h`

| Sport | Book | Livres | Moyenne | Mini | Maxi |
| --- | --- | ---: | ---: | ---: | ---: |
| football | `betclic_fr` | 329 | **8,21** | 3,77 | **13,92** |
| football | `bet365` | 7 | 8,44 | 6,44 | 9,80 |
| football | `superbet` | 46 | 7,33 | 4,95 | 8,24 |
| football | `10bet` | 47 | 7,00 | 6,51 | 7,32 |
| football | `pinnacle` | 46 | 4,27 | 3,19 | 6,29 |
| tennis | `betclic_fr` | 160 | 4,43 | 3,11 | 10,13 |
| tennis | `pinnacle` | 55 | 3,36 | 2,64 | 4,48 |

### La distribution, et pourquoi la moyenne ne suffit pas

C'est exactement le cas redoute. `betclic_fr` football affiche **8,21 en
moyenne**, et sa distribution s'etale de `[0-4)` a `[12+)` :

| Tranche | `[0-4)` | `[4-6)` | `[6-8)` | `[8-10)` | `[10-12)` | `[12+)` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Livres | 2 | 46 | 108 | 111 | **57** | **5** |

**62 livres sur 329, soit 19 %, sont a 10 points ou plus.** Un residu lu contre
une marge supposee de 5 % serait donc faux d'un facteur deux sur un cinquieme des
matchs de football. Le tennis est bien plus resserre — 150 livres sur 160 sous 6 points.

### Le chiffre de tete : le biais, en victoires et en points

L'overround retenu est celui du **livre qui a reellement fourni la cote** de
chaque selection, identifie par egalite de prix dans le meme (session, evenement,
marche).

| | Section C | Section C-bis |
| --- | ---: | ---: |
| Selections rattachees | **147** | **68** |
| Overround moyen | 5,28 pts | 6,73 pts |
| Etendue | 1,81 → 13,92 | 3,19 → 11,96 |
| Victoires observees | 72 | 21 |
| Victoires impliquees, **cote brute** | 82,84 | 21,10 |
| Victoires impliquees, **marge retiree** | 78,70 | 19,78 |
| **Residu brut** | **−10,84** | −0,10 |
| **Residu corrige de la marge** | **−6,70** | **+1,22** |

**En points de pourcentage** : sur la section C, la cote brute surestime les
victoires impliquees de **4,14 sur 147 selections, soit 2,8 points**. Le residu
passe de −7,4 pts a −4,6 pts.

Trois lectures qui en decoulent :

1. **La marge explique 38 % du deficit de la section C, pas davantage.** Les
   6,70 victoires restantes ne sont pas imputables au book.
2. **La section C-bis est a parite avec ses prix, et passe au-dessus une fois la
   marge retiree.** C'est la population sans exigence de fait date. Le contraste
   avec la section C est le fait le plus interessant de cette phase et il devra
   etre teste en phase 3 — pas conclu ici.
3. **Le deficit de la section C est concentre au tennis** : football 43 observees
   pour 44,86 corrigees (−1,86 sur 86), tennis 29 pour 33,84 (−4,84 sur 61).

### La seconde correction va dans l'autre sens, et elle domine

`price_real` — la cote reellement obtenue — est renseignee sur **123 selections
de section C** (contre 9 lors du dernier releve du dossier). Le sens est net :

| Sens | n | Ecart moyen |
| --- | ---: | ---: |
| obtenue **plus basse** | **117** | −6,80 % |
| obtenue plus haute | 4 | +2,50 % |
| identique | 2 | 0,00 % |

Une cote de bloc optimiste **sous-estime** l'attendu : le deficit reel est donc
**pire** que mesure. Sur les 37 selections portant les deux informations, le
residu passe de **−0,85 (brut) a −1,61 (les deux corrections)**.

> **Reserve serieuse, et elle n'a pas bouge depuis le dossier** : les 123 paires
> ont **toutes** `price_source = 'reference'`. Elles mesurent l'ecart entre un
> prix de book de reference et le prix obtenu, et ne disent **rien** des
> selections cotees chez le book principal. La couverture a monte de 9 a 123 ;
> le biais de composition, lui, est intact.

### Cotes hors bornes

- `picks.price` : **aucune anomalie**. 1,25 → 11,75, aucune nulle, aucune ≤ 1.
- **`odds` porte 146 lignes a `price = 1.00`, `prompt_odds` 159.** Une cote
  decimale de 1.00 est un rendement nul. Toutes viennent de books de
  substitution (`superbet` 120, `bet365` 39) sur des lignes extremes — `Under 8.5`
  cartons, `Over 2.5` corners, `Under 5.5` buts d'equipe. Elles sont **exclues**
  de tout calcul d'overround ici ; rien ne garantit qu'elles le soient du rendu.
  **A instruire en phase 4** : une cote a 1.00 affichee dans un bloc est une
  selection qui ne peut rien rapporter.

### L'interdit §9 tient

Aucun code executable ne calcule de devig, de Kelly, d'EV, d'edge ou de CLV. Les
quatre occurrences lexicales du depot sont **toutes des commentaires qui
enoncent l'interdit** (`inference.py:702`, `inference.py:754`, `stakes.py:6`,
`changelog.py:360`). Le residu de `inference.Residual` est bati sur `1/cote`
**brut**, avec une `margin` par defaut a zero. **Conforme.**

---

## 1.1 — Completude

| Colonne | Section C (428) | Section C-bis (95) |
| --- | ---: | ---: |
| `confidence` (annoncee) | 428 | 95 |
| `confidence_computed` | **277** | 93 |
| `claim_raw_json` (bloc) | **143** | 68 |
| `source_level` | 327 | 95 |
| `angle` | 327 | 95 |
| `prompt_id` | 230 | 85 |
| `market_key` | **308** | 95 |
| `price_real` | **123** | 9 |
| `invalidation` | 192 | 95 |

### P0 — `confidence_computed` porte trois regimes sous un seul nom

C'est le defaut le plus couteux de la phase, et il n'est pas celui qui etait
suspecte : la colonne ne cesse pas d'etre peuplee — **elle se peuple avec une
valeur qui ne veut pas dire la meme chose.**

| Provenance | n | Cran | Cause |
| --- | ---: | --- | --- |
| bloc `conf` apparie | **143** | 1 a 5, distribues | — (6 en cran 1, cause `sans_fait`) |
| **aucun bloc** | **134** | **1, force** | `ligne_absente`, `reperes_non_resolus`, `cause_inconnue` |
| colonne nulle | 151 | — | sessions anterieures au chantier |

Le peuplement suit exactement la chronologie du cadre : sessions 2 a 10 (05→13/08)
n'ont **ni bloc ni cran** — le gabarit ne les demandait pas ; sessions 11 a 16
ont **des crans sans aucun bloc** — tous forces ; sessions 17 a 22 portent les blocs.

**L'etape du pipeline est donc identifiee, et ce n'est ni le modele ni
l'extraction** : c'est le collage. Un rendu partiel — le seul tableau de la
section C — laisse les blocs ```conf derriere lui, la ligne `dossiers_ouverts`
avec, et l'override force tout le lot au cran 1.

**L'impact se mesure**, et il touche une carte publiee :

| Cran calcule | n affiche | dont **reels** | dont defauts de collecte | Taux affiche | Taux reel |
| ---: | ---: | ---: | ---: | ---: | ---: |
| **1** | **140** | **6** | **134** | 53,5 % | 60,0 % |
| 2 | 16 | 16 | 0 | 46,2 % | 46,2 % |
| 3 | 69 | 69 | 0 | 57,1 % | 57,1 % |
| 4 | 30 | 30 | 0 | 51,7 % | 51,7 % |
| 5 | 22 | 22 | 0 | 55,0 % | 55,0 % |

Le cran 1 est **le plus gros groupe de la carte** (140 sur 277) et il est a
**96 % un artefact de collage**. La carte « par cran calcule » se lit comme une
observation sur le modele ; elle mesure en fait ce qui n'a pas ete colle.

**Le mecanisme de distinction existe deja et n'est pas applique ici.**
`is_collection_fault()` et `is_unknown_cause()` sont ecrits dans
`services/confidence.py:433` et appliques dans `_override`
(`history.py:4874-4887`) comme dans `changelog` (`history.py:5213`). Mais
`by_confidence_computed` (`history.py:5644`) ne filtre que sur
`confidence_computed IS NOT NULL`. **Deux lectures de la meme colonne, dont une
seule connait la distinction** — le motif que ce depot documente comme le plus
couteux.

### `market_key` : 120 non resolues, toutes en section C

Aucune en C-bis. Les libelles concernes sont **dans le vocabulaire** (`Vainqueur`
32, `1N2` 22, `Handicap` 16) : ils se resolvent ailleurs. Ce sont les selections
anterieures a la migration 033, qui n'ont pas de cle figee et se resolvent a la
lecture par `market_key_effective`. **Comportement documente, pas un defaut** —
mais il retire 120 selections de tout rattachement direct a `prompt_odds`.

### Couverture de la mesure d'overround

C'est la limite a porter en phase 3 : **147 des 401 tranchees de section C
(37 %)** portent un overround mesure, contre **68 sur 90 (76 %)** en C-bis.
L'ecart tient aux 120 `market_key` nulles et aux marches sans livre reconstructible.

---

## 1.2 — Doublons, collages, collisions

**Rien de ce qui etait suspecte ne se materialise en doublon.**

- **Doublons exacts** (session, evenement, marche, selection) : **0**.
- **Collisions d'identifiants** : `oddsapi_event_id` **unique partout** ; aucune
  affiche dupliquee le meme jour. La cle reelle des evenements coincide avec la
  cle supposee.
- **8 paires** de deux selections de section C sur le meme match dans la meme
  session — c'est le cas encadre par `independence_note`, pas un doublon.
- **4 matchs portent une selection en C et une en C-bis.** Trois sont sur des
  marches compatibles ; **une est directement contradictoire** — session 18,
  Tirante - Fils : la section C prend `Arthur Fils −2.5` (handicap jeux) et la
  C-bis prend `Thiago Agustin Tirante` (vainqueur). Les deux ne peuvent pas
  gagner. A instruire en phase 4 : le cadre l'autorise-t-il ?

### Les 593 rejets d'ingestion, tries

| Type de bloc | Motif | n |
| --- | --- | ---: |
| `source` | `source_vide` | 350 |
| `score_sets` | `match_ref_unresolved` | 75 |
| **`conf`** | **`fence_not_found`** | **60** |
| `source` | `schema_invalid` | 57 |
| `conf` | `match_ref_unresolved` | 15 |
| `exploratoire` | `schema_invalid` | 12 |
| autres | | 24 |

Les 350 `source_vide` **ne concernent pas les selections** : ce sont des trous de
couverture du fournisseur de statistiques de service tennis, avec leur detail
(« la source repond et ne sert aucune timeline »). Ils n'ont rien a voir avec le
collage et il ne faut pas les compter dedans.

Les **60 `conf/fence_not_found`** sont le seul motif qui touche la mesure, et ils
confirment le diagnostic de 1.1 par un second chemin.

### Un seul champ porte une trace de collage

`picks.selection` = `Seattle Sounders FC:1 | Austin FC:0`, quand le meme objet
s'ecrit ailleurs `San Jose Earthquakes 1 – Minnesota United FC 1`. Deux formats
de score exact, un seul cas. A noter, pas a corriger seul.

Signale au passage : `Inter Milan −2` emploie un **signe moins Unicode** (U+2212)
la ou d'autres lignes portent un tiret ASCII. Aucun defaut constate, mais toute
comparaison de libelle qui l'ignorerait echouerait en silence.

---

## 1.3 — Anteriorite

### Le regime a change au milieu de la fenetre — confirme

Commit `8390111`, **17/08/2026**, « Faire des selections tardives une population,
pas une reserve de lecture ». La garde ne refuse plus ; elle marque.

### Le flag natif n'est pas une seconde mesure

Statut derive retroactivement sur toute la periode
(`picks.created_at >= events.commence_time`), puis confronte au flag :

| Regime | flag=0 / derivee=0 | flag=1 / derivee=1 | **desaccords** |
| --- | ---: | ---: | ---: |
| avant 17/08 | 183 | 44 | **0** |
| a partir du 17/08 | 288 | 8 | **0** |

**Concordance parfaite, 523 sur 523.** La migration 053 a retro-rempli la colonne
avec la meme regle, et `set_event` la recalcule au rattachement
(`history.py:2400`). Le flag est donc **la meme derivation stockee**, pas une
observation independante — il ne peut ni confirmer ni infirmer, et il ne doit pas
etre lu comme source de verite. Mais il ne ment pas.

### La chute du taux est un changement de comportement, pas de detection

C'est la question posee, et elle se tranche : **la detection est uniforme sur
toute la fenetre** (retro-remplissage par la meme regle), donc la variation est reelle.

| Regime | n (section C) | Tardives | Taux | Avance moyenne | Retard maximal |
| --- | ---: | ---: | ---: | ---: | ---: |
| avant 17/08 | 227 | 44 | **19,4 %** | 4,10 h | −25,95 h |
| a partir du 17/08 | 201 | 8 | **4,0 %** | 6,08 h | −15,07 h |
| C-bis (toute posterieure) | 95 | 0 | **0,0 %** | — | — |

L'avance moyenne monte de 4,1 h a 6,1 h et le retard maximal se reduit de 26 h a
15 h : les trois indicateurs bougent dans le meme sens. **Le comportement de
saisie a change le jour ou la garde a cesse de refuser** — resultat contre-intuitif
et net.

### Ce que la population tardive pese

| Population (section C, tranchees) | n | Observees | Impliquees | Residu |
| --- | ---: | ---: | ---: | ---: |
| anterieure | 349 | 184 | 200,00 | **−16,00** |
| **tardive** | **52** | 32 | 28,76 | **+3,24** |

Les tardives sont **au-dessus** de leurs prix, les anterieures nettement en
dessous. C'est la signature attendue d'un prix releve en connaissant le debut du
match — et c'est ce qui justifie de les tenir a part plutot que de les refuser.

### Chemins d'ecriture

**Un seul `INSERT INTO picks`** dans tout le depot (`history.py:2196`), appele de
trois endroits — saisie a la main, import d'un collage, rejeu. Le marquage
`tardive` est calcule **dans** `add_pick`, donc **aucun des trois ne le
contourne**, par construction et non par vigilance. `write_paths.py` le verifie
par analyse statique de la source. **Conforme.**

---

## 1.5 — Reglement

### Le mecanisme

**Partiellement automatique.** `services/settlement.py` porte un moteur de regles
par famille de marche, alimente par `tennisapi/event` et l'index football ; les
familles hors regle **restent manuelles** et le module le dit
(`settlement.py:456`). 226 reglements enregistres, avec `verdict`, `etat`
(`applique` / `divergent`), `source` et `observed_at`.

### Les non-reglees ne sont pas manquantes au hasard — elles n'existent pas

Sur les selections dont le match est passe **depuis plus de 48 h** :

| Etat | Section C | Section C-bis |
| --- | ---: | ---: |
| tranchee | 325 | 51 |
| `void` | 7 | 1 |
| **non tranchee** | **0** | **0** |

**Aucune selection echue n'est en attente.** Les 17 `pending` portent tous sur des
matchs **du jour meme, non encore commences** (`commence_time` a +1 h a +4 h). Le
risque de biais de reglement est donc **nul sur cette base** — la comparaison de
caracteristiques demandee n'a pas de population sur laquelle porter.

C'est un resultat negatif, et il ferme la question : le P0 potentiel n'existe pas.

### Vocabulaire et denominateur

Quatre statuts : `win` 242, `loss` 249, `pending` 17, `void` 15. **Ni `push` ni
`cashout`.** Les taux se calculent sur `result in ("win","loss")`
(`history.py:2621`, `4169-4170`) : **`void` est correctement hors denominateur**.
`SETTLED_RESULTS` l'inclut, mais sert a la feuille de session — « il n'y a plus
rien a saisir » — jamais a un taux. **Conforme.**

### Handicap asiatique — la question posee, en deux moities

**Ligne entiere : traitee, et bien.** 25 selections de handicap portent une ligne
entiere (`0`, `+1`, `+2`, `−1`). **9 sont enregistrees `void`** — `Rapid Vienna 0`,
`Gornik Zabrze +1`, `Bournemouth +1`, `Pogoń Szczecin 0`… : le pari est tombe
pile, la mise est rendue, et `void` **est** le statut push. Exclu du denominateur,
c'est le traitement correct. Le mot manque, le comportement est juste.

**Ligne en quart : non traitee, et c'est un vrai defaut.** Quatre selections de
**section C** portent une ligne en quart, donc un pari **scinde** dont l'issue peut
etre un demi-gain ou une demi-perte :

| Selection | Resultat enregistre |
| --- | --- |
| `KV Kortrijk +1.75` | `loss` |
| `Hartberg +0.75 (ref.)` | `loss` |
| `Westerlo +0.75 (ref.)` | `loss` |
| `Telstar +1.25` | `win` |

Le vocabulaire n'a **aucun statut de remboursement partiel** : ces quatre sont
enregistrees en binaire. Si l'une d'elles a fini en demi-gain, elle compte pour
une victoire pleine ; en demi-perte, pour une defaite pleine.

Deux consequences, et la seconde est plus large que le comptage :

1. **Ampleur : 4 sur 428, soit 0,9 %.** L'effet sur le residu est negligeable a
   cet effectif — mais il est **silencieux**, et il grandira si de telles lignes
   reviennent.
2. **Ces quatre selections n'etaient pas posables.** Le dossier etablit qu'aucun
   book francais ne sert les lignes en quart au football, et que la **selection**
   en est interdite. Elles sont pourtant en section C, avec un resultat.
   **A instruire en phase 2** : le garde-fou existe-t-il ailleurs qu'au rendu ?

---

## Ce que la phase 1 laisse ouvert

- L'ecart **section C / section C-bis** sur le residu corrige (−6,70 contre
  +1,22) est le fait le plus frappant de la phase. Il est **decrit, pas conclu** :
  les deux populations ne jouent pas aux memes cotes — 1,80 de moyenne en C,
  3,90 en C-bis — et un taux sans son prix ne compare rien. Phase 3.
- La concentration du deficit **au tennis** (−4,84 sur 61 contre −1,86 sur 86 au
  football) : meme reserve, meme renvoi.
- Le biais de composition de `price_real` — 123 paires, **toutes** de book de
  reference — n'est pas reparable retroactivement.
