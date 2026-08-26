# Audit MyAssistantBet — rapport

Population arretee au **26/08/2026** : 532 selections, 24 sessions, 05 → 25/08.
Toutes les mesures sont produites par les requetes de `audit/sql/`, sur une copie
obtenue par `VACUUM INTO`.

> **Ordre de redaction.** La section C a ete ecrite **en premier**, et
> deliberement : ecrite en dernier, elle recueille ce qui reste apres que les
> affirmations ont ete posees ; ecrite en premier, elle **borne ce que les autres
> sections ont le droit d'affirmer**. C'est la section qu'un rapport d'audit rate
> le plus facilement.

*Sections B, D et E a suivre.*

---

# A. Verdict

**Cette base ne peut pas trancher ce qu'on lui demande, et ce n'est pas une
question de volume.** Vingt-deux jours, cinq points de rupture, treize changements
de gabarit : le cadre a ete modifie pendant que les sessions se deroulaient, en
reaction a ce qu'elles montraient. Il n'existe donc **aucun hors-echantillon** —
et *un deficit mesure sur une population dont les regles ont ete ajustees en cours
de route n'est pas plus generalisable qu'un excedent*. Cela condamne les deux
lectures faciles : « le regime actuel est a zero, donc tout va bien » ne vaut pas
mieux que « le cadre perd ».

Sous cette reserve, et alors seulement : le residu au prix du regime actuel vaut
**−1,60 point par selection sur 281**, intervalle [−7,36 ; +3,97], quand l'effet
detectable a cette taille est de l'ordre de huit points — **indiscernable de
zero**. Le deficit publie de −16 points etait concentre a 70 % sur la premiere
semaine, seule population dont le marche n'a jamais ete fige et ne le sera jamais.
Les interdits de la section 9 tiennent : **aucun calcul d'esperance n'existe.**

Ce qui a ete livre ne mesure pas mieux la performance — cela rend l'echec visible
plus tot. Le residu ne bougera pas avant des trimestres ; le faisceau de faits a
montre **en jours** qu'une procedure avait cesse d'etre transmise au modele. **La
sante mesurable de cet outil se lit sur ses intrants, pas sur ses sorties.**

L'urgence tient en une phrase : figer le gabarit assez longtemps pour qu'un regime
soit mesurable, ou versionner chaque selection avec l'empreinte du gabarit qui l'a
produite. Sans l'une des deux, la meme question se heurtera au meme mur dans six
mois avec quatre fois plus de donnees.

---

# C. Ce que les donnees ne permettent pas de conclure

## C.0 — La borne qui les contient toutes : il n'existe aucun hors-echantillon

**Le cadre a ete modifie pendant que les sessions se deroulaient, en reaction a
ce qu'elles montraient.** Treize changements de gabarit en quinze jours, cinq
points de rupture identifies, et aucune periode ou le dispositif est reste fixe
assez longtemps pour qu'un lot y soit produit sans que ses predecesseurs l'aient
deja influence.

**Tout ce que ce rapport affirme sur la performance est donc in-sample par
construction — y compris ce qui a l'air d'un resultat negatif.** Un deficit
mesure sur une population dont les regles ont ete ajustees en cours de route
n'est pas plus generalisable qu'un excedent : les deux decrivent l'ajustement
autant que la methode.

Ce n'est pas une reserve de forme. C'est la raison pour laquelle aucun chiffre de
ce rapport ne peut etre lu comme une prediction, et pourquoi la recommandation
d'instrumentation qui compte est celle qui rend un regime **stable et datable**,
pas celle qui ajoute une mesure de plus.

## C.1 — Le residu ne tranchera pas a cette echelle, et ce n'est pas une question de patience

| Population | n | corrige (pts/sel) | IC 95 % | P(≥0) |
| --- | ---: | ---: | --- | ---: |
| sessions 8-22, section C, anterieures | 281 | **−1,60** | [−7,36 ; +3,97] | 0,285 |
| sessions 8-22, section C-bis | 90 | −0,91 | [−9,80 ; +8,04] | 0,404 |
| sessions 2-7, section C, anterieures | 68 | −13,27 | [−24,22 ; −2,32] | 0,009 |

A n = 281, **l'effet detectable est de l'ordre de huit points par selection**. Le
point estime en vaut −1,60. La lecture juste est **« indiscernable de zero »**, et
jamais « le cadre bat le marche » ni « le cadre perd ».

**Ce que cela interdit d'ecrire** : toute phrase qui compare deux regroupements
sur leur residu et conclut. Toute phrase qui presente −1,60 comme une performance.
Toute projection.

## C.2 — Le residu ne peut pas separer deux hypotheses opposees

Le gabarit expose **toutes les cotes du lot avant que le modele produise la
moindre ligne** — section MATCHS a la ligne 207, section SORTIE ATTENDUE a la
667. Il ne demande aucune estimation de probabilite, donc il n'y a pas d'ancrage
au sens classique ; mais il **filtre le jouable par le prix** : ce qui n'a pas de
cote ne peut pas etre selectionne, et la ligne `Paliers` dit d'avance quelles
bandes chaque match autorise.

Un residu proche de zero est donc compatible avec deux lectures opposees :

- l'analyse n'apporte rien au-dela de ce que le prix disait deja ;
- elle apporte quelque chose, invisible a cette taille d'echantillon.

**Aucune mesure de cette base ne les separe**, parce qu'il n'existe **aucune
sortie produite sans exposition prealable aux cotes**. Ce qui trancherait est une
experience — un lot analyse sans les prix, cote apres coup — donc un plan
d'observation a construire, pas une lecture a faire.

## C.3 — Un cinquieme du volume n'a pas de marche, et ne l'aura jamais

`prompt_odds` ne fige le marche que depuis la session 8. Les sessions 2 a 7
portent **106 selections, 20,3 % du volume, entierement en section C**, et
l'etat du marche a l'instant de l'analyse n'existe plus nulle part — `odds` ne
conserve que le dernier releve.

Sur ces sessions : couverture d'overround **0 %**, cote obtenue renseignee **2
fois sur 68**. Leur attendu repose sur le seul prix du bloc, que rien ne recoupe.

**C'est aussi la population qui porte le plus gros deficit du jeu de donnees**
(−13,27 points par selection, seul intervalle qui exclut zero). La conjonction
n'est pas un hasard exploitable : une part inconnue de ce deficit est une **erreur
de prix** et non de selection, la direction est connue — un prix de bloc optimiste
creuse le deficit — et **l'ampleur n'est pas decomposable**.

Ce chiffre ne caracterise pas le cadre. Il se rapporte en historique ventile, avec
sa couverture a 0 %, et jamais autrement.

## C.4 — L'amelioration entre les deux regimes n'est attribuable a aucune cause

Le residu passe de −16,45 a −1,72 points par selection. Quatre changements se
chevauchent sur la meme fenetre, **aucun isole d'un autre** :

| Date | Changement |
| --- | --- |
| session 8 (11/08) | `prompt_odds` entre en service |
| 11/08 18:52 | migration 033 — `market_key` figee a l'ecriture |
| 17/08 | migration 053 — la garde d'anteriorite marque au lieu de refuser |
| 11 → 24/08 | le taux de tardives passe de 19,4 % a 4,0 % |

S'y ajoutent treize changements de gabarit qui n'ont laisse aucune borne
exploitable. **Le plus tentant est le plus faux** : attribuer l'amelioration a la
garde d'anteriorite. Elle est arrivee six jours **apres** le debut de la periode
amelioree, et elle ne refuse rien.

Ce qui se dit sans se tromper : deux regimes coexistent, ils different fortement,
et le premier n'est pas mesurable. Rien de plus.

## C.5 — L'echelle de confiance est morte comme axe de mesure

Section C, tranchees, defauts de collecte exclus : **130 selections** en tout.

| Cran reel | 1 | 2 | 3 | 4 | 5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| tranchees | 5 | 13 | 63 | 29 | 20 |

Deux crans sur cinq sont sous quinze. Le croisement `type_angle × confiance` a une
case mediane de 36 et des extremes a 2.

**Marque `[NON TESTABLE]`** : la monotonie de l'echelle, tout residu par cran
au-dela du cran 3, et **l'integralite du croisement `type_angle × confiance`**.

Ordre de grandeur du volume requis : detecter un ecart de 10 points entre deux
crans voisins a 80 % de puissance demande environ **390 selections par cran**, soit
**~1 950 selections reelles** contre 130. Plusieurs annees au regime actuel.

Ce n'est pas un resultat negatif : c'est une **absence de plan d'observation**.

## C.6 — Cent vingt-sept crans sont perdus definitivement

`143 crans reels + 134 forces + 151 nulles = 428`, toute la section C. Les 134
forces le sont par defaut de collage, et le brut a ete verifie :

| Cause | Sessions | n | `imports_raw` | Blocs dans le brut |
| --- | --- | ---: | ---: | ---: |
| `cause_inconnue` | 11, 13 | 43 | **0** | — |
| `ligne_absente` | 11, 14 | 43 | **0** | — |
| `ligne_absente` | 15 → 18 | 41 | 41 | **0** |
| `reperes_non_resolus` | 22 | 7 | 7 | 9 presents |

**L'information n'a jamais ete collee : elle n'existe nulle part dans
l'application.** `prompts.body` ne peut pas la porter — c'est ce que l'application
a **envoye**, quand les blocs sont ce que le modele a **rendu**.

Recuperation possible : **7 sur 134**, par rejeu de la resolution. Les 127 autres
sont perdues sauf si les transcripts d'origine existent hors de l'application.

## C.7 — Le controle du prix repose sur une saisie que rien ne verifie

Tout le calcul de residu repose sur `picks.price`, **un nombre auto-declare** —
la cote recopiee du bloc a la main. Le seul controle possible est `price_real`.

Sur les 123 paires renseignees : 117 cotes obtenues plus basses, ecart moyen
**−6,8 %**. Le sens compte plus que l'amplitude — une cote de bloc optimiste
**sous-estime** l'attendu, donc le deficit reel est **pire** que mesure.

**Reserve non reparable** : les 123 paires ont **toutes** `price_source =
'reference'`. Elles mesurent l'ecart entre un prix de book de reference et le prix
obtenu, et ne disent **rien** des selections cotees chez le book principal, qui
sont le gros du lot. La couverture est montee de 9 a 123 ; le biais de composition
est intact, et il ne se reparera pas retroactivement.

## C.8 — La date de desactivation du cadre est bornee, son attribution non

Deux signaux independants convergent sur une fenetre de **19 h 13**, entre le
**21/08 12:24:39Z** et le **22/08 07:37:55Z** :

- le vocabulaire des paliers — 59,3 % des cotes de la zone `[1,65 ; 1,80)`
  classees FUN avant, **0 %** apres, p = 9,9 × 10⁻¹¹ ;
- la conformite des collages — intermittente avant, **28 sur 28** apres.

**`[HYPOTHESE]` sur l'attribution.** Le second signal peut refleter un changement
d'habitude de collage plutot que d'outil. Le premier y echappe — un collage
partiel porte quand meme la colonne Palier — mais il ne distingue pas « Skill
desactivee » de « Skill active et le modele a cesse d'appliquer sa regle ». Ce qui
trancherait est le transcript claude.ai ; aucune trace locale n'existe.

## C.9 — Le moniteur de faisceau ne dira jamais rien sur la periode qui porte le deficit

**`claim_raw_json` n'existe qu'a partir de la session 17** (18/08) : le gabarit ne
demandait pas les blocs avant. La serie a **six points**, du 18 au 24/08.

Consequence a tenir : l'instrument livre par ce lot — celui qui detecte en jours
ce que le residu ne detecte pas en trimestres — **est structurellement aveugle sur
les sessions 2 a 16**, c'est-a-dire sur toute la periode ou le deficit se
concentre. Il ne servira que pour l'avenir.

## C.10 — Une pente de quatre points ne se distingue pas d'une marche

| session | 17 | 19 | 20 | 21 |
| --- | ---: | ---: | ---: | ---: |
| faits par bloc | 2,24 | 1,80 | 1,64 | **1,30** |
| part de niveau 1 | 47,4 % | 25,9 % | 18,9 % | 25,0 % |

`faits/bloc` **decroit deja entre 17 et 19**, donc avant la frontiere du 21/08.
Deux lectures restent possibles :

- une degradation graduelle que le decoupage binaire a fait passer pour une
  rupture ;
- une rupture reelle superposee a une tendance preexistante.

**Quatre points ne les separent pas, et il ne faut pas essayer.** Ce qui trancherait
est un fait a venir : si la pente se poursuit **apres** la restauration de la
conduite de recherche, le cadre desactive n'etait pas la cause, ou pas la seule.

**Regle de methode qui en sort** : *un decoupage binaire pose sur une frontiere
choisie ne peut pas distinguer une marche d'une pente.* Il rend un `p` qui decrit
la difference de deux moyennes, et cette difference existe aussi sous une tendance
reguliere.

## C.11 — Le gabarit n'est pas prouve etre le canal qui a porte la methode

**9 selections sur 532 viennent d'une session sans aucun prompt genere** (session
23 : 1 collage de 31 128 caracteres, 0 prompt, cinq minutes apres l'ouverture de
la session). L'analyse a ete produite ailleurs et importee ici.

L'ampleur est faible — 1,7 % — mais la reserve est de nature, pas de degre : tout
raisonnement en « le gabarit porte la methode, donc la methode a ete recue »
suppose un canal que la base ne peut pas verifier.

## C.12 — Ce que le retour d'experience ne pourra jamais mesurer retroactivement

Le retard entre l'annonce d'une absence et son apparition dans le bloc **n'est pas
mesurable** : `context` est indexee par (evenement, type) et chaque enrichissement
**ecrase** le precedent — 323 lignes pour 323 evenements. Meme forme que
`commence_time` avant la migration 040 et `odds` avant la 048.

Ce qui se mesure a la place est l'**accord** a un instant donne, pas la latence.

## C.13 — Une observation retiree en cours d'audit, et pourquoi elle figure ici

Le sondage d'instruction de la liste de refus annoncait **« 4 faits, tous
posterieurs au 21/08, aucun avant »**. Il employait une expression reguliere batie
sur les noms d'operateurs **devines**. Une relecture des 181 domaines en rend
sept, pour 12 faits :

| | avant 21/08 12:24Z | apres | p |
| --- | ---: | ---: | ---: |
| sonde d'instruction | 0 / 72 | 4 / 165 | 0,32 |
| **releve corrige** | **3 / 86 = 3,5 %** | **9 / 185 = 4,9 %** | **0,76** |

**L'observation est retiree, pas nuancee.** Des pages d'operateur etaient citees
avant la rupture aussi ; la session 17, la plus ancienne du moniteur, en porte
deja une.

Elle figure dans cette section parce que la lecon vaut au-dela du cas : c'est la
**troisieme fois** dans ce dossier qu'un compte faible sur un rapprochement se
revele etre un defaut d'appariement, et la premiere ou il porte sur la mesure de
l'audit lui-meme. Trois occurrences font une **regularite**, pas trois incidents,
et elle vaut avertissement prospectif : *le prochain chiffre bas de ce dossier se
verifie pour son appariement avant d'etre interprete comme un signal.*

**Et sa correction a produit un defaut de plus, qui appartient a la meme
famille** : le chiffre a ete rectifie dans le code, le docstring et le message de
commit — **pas dans le document**, qui l'a porte faux a deux endroits de plus.
Un chiffre publie a quatre endroits est un chiffre a **quatre copies**. Apres
toute correction de mesure, compter les copies avant de declarer la correction
faite.

## C.14 — Ce que les instruments livres par ce lot ne mesurent pas

**Le faisceau mesure la matiere premiere, jamais le jugement.** Ces grandeurs se
degradent quand la matiere se degrade ; elles ne montent pas quand la competence
monte. La dissymetrie est le sujet : une baisse retire au jugement de quoi
s'exercer, donc c'est une alarme ; **une hausse dit seulement qu'il y avait plus a
lire ce jour-la** — un lot de championnat en pleine saison porte plus de faits
publies qu'un tour preliminaire estival.

**`source_drift` ne dit pas qui a tort.** Un domaine porte deux niveaux : au moins
une declaration est fausse, et l'instrument ne sait pas laquelle. Il expose ; il
ne corrige pas.

**La liste de refus est prospective.** 12 faits, sans contraste temporel, **tous
declares niveau 4** par le modele — il ne s'est jamais trompe sur ce que sont ces
pages. Elle n'existe pas pour trier ce qui est deja entre.

**Aucun seuil d'alarme n'est pose sur le faisceau**, et ce n'est pas un oubli : six
sessions ne suffisent pas a en calibrer un, et il declencherait sur du bruit
saisonnier — intersaison, Grand Chelem et fenetre de mercato n'ont pas la meme
densite de faits publies.

## C.15 — Ce qui reste ouvert et n'a pas ete instruit

- **Phase 4** — le generateur de prompts : variables injectees non utilisees,
  erreurs factuelles de bloc remontees a leur source sur trois cas, conformite du
  rendu au gabarit, budget de tokens reellement utilise.
- **Phase 5** — technique : regles metier sans test, erreurs silencieuses, index
  manquants sur `picks(session_id | event_id | created_at | result)`, exploitation
  de la distribution des 593 rejets d'ingestion.
- **B1** — la couverture des cotes, dont la mesure est cadree et non conduite.
- **D3** — ce que `framework_version` etiquette desormais, et le sort du test
  rouge qui en depend.
