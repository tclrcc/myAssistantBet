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

**Quatrieme occurrence, le 27/08/2026, et elle deplace la regle d'un cran.** Un
releve annoncait **19 collages archives portant une section G**, chiffre qui
fondait la contrainte de compatibilite de lecture de D2. Il venait d'un
`raw_text LIKE '%G.%'`, qui attrape toutes les initiales — « J. Machado »,
« G. Smith ». Repasse au **lecteur reel**, `picks_import.SECTION_HEAD`, le compte
est **zero** : les titres presents dans les 78 archives sont `A` 47, `B` 47, `C`
64, `D` 40, `E` 47, `F` 47.

**Les trois premieres occurrences etaient des comptes bas ; celle-ci est un compte
haut**, et c'est ce qui l'ajoute au motif plutot que de le repeter. La regle
n'etait pas « un chiffre bas se verifie » mais **« un chiffre produit par une sonde
ecrite pour l'occasion se verifie contre le lecteur de production »**. Un `LIKE`
n'est pas un lecteur de sections, et il n'y a aucun moyen de le savoir depuis son
resultat.

Ce que ca a change au travail : la contrainte de D2 devient **prospective et non
retrospective** — elle protege un rendu ancien qu'on recollerait, pas la relecture
de l'archive — et `imports_raw` ne commencant qu'a la session 15, l'avant reste
**inconnaissable et non vide**. L'exigence en sort durcie, pas relachee.

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

## C.15 — Un seuil majore, pas calibre, et il faut le rouvrir

`PRICE_WINDOW_DAYS = 21` — la fenetre glissante sur laquelle une competition est
jugee servie. **Sa borne basse est mesuree ; sa borne haute ne l'est pas.**

- **Borne basse, 14 jours** : la Leagues Cup porte des matchs a venir et son
  dernier prix date de **13,5 jours** — elle se joue par phases, et huit de ses
  quarante evenements sont cotes. Sous quinze jours, elle serait masquee a tort.
- **Borne haute, inconnue** : la base entiere couvre vingt-deux jours. Elle **ne
  peut pas departager trois semaines de quatre**, ni de six.

Vingt-et-un est donc **majore**, choisi sur une asymetrie de cout et non sur une
mesure : masquer une competition servie coute une soiree d'analyse, laisser
paraitre une competition morte coute quelques lignes de board — et le badge
« aucun prix » le dit deja.

**A rouvrir des qu'il y aura de quoi mesurer**, et cette entree existe pour ca :
sans elle, le nombre deviendra dans six mois une constante que personne ne saura
justifier. Le dossier porte deja quatre chiffres qui ont survecu a la disparition
de leur raison ; celui-ci ne doit pas etre le cinquieme.

## C.16 — Une propriete qui ne peut pas etre fausse ne se voit pas a la relecture

**Deux gardes retires dans le meme lot, et aucune relecture ne les avait vus.**
`confidence_floor` — un cran 5 declare sans ses deux editeurs distincts — vaut
**0 sur 211 blocs**. `HiddenEvent.priced` — une rencontre masquee portant deja un
prix — est **contradictoire avec la regle qui la produit** : elle opere par
competition, donc une seule cote ramene la competition entiere.

Les deux ne se ferment pas au meme titre. Le premier est **vide a la mesure**, et
peut se remplir un jour. Le second est **impossible par construction** : aucun
volume de donnees ne l'aurait rempli, et **aucune mesure ne l'aurait dit** — c'est
la limite de ce dossier sur ce point, qui n'est pas une limite de population.

Ce qui l'a montre est un **test**, en echouant sur un `KeyError` : la liste etait
vide a l'endroit exact ou le drapeau devait s'afficher. Le code, lui, se relisait
sans surprise — la propriete rendait faux sur le cas ordinaire comme sur le cas
qu'elle etait censee attraper. C'est la signature du defaut caracteristique du
projet, transposee d'une donnee a un garde-fou, et c'est la **troisieme famille**
de cette forme que le dossier rencontre, apres les deux copies qu'aucun mecanisme
n'oblige a concorder et les comptes faibles qui sont des defauts d'appariement.

**La regle de forme qui en sort**, ecrite en §8 de la proposition : avant
d'ajouter une propriete, nommer le cas ou elle est vraie et le cas ou elle est
fausse ; si l'un des deux ne se construit pas, elle n'a pas d'objet. Le cout d'un
garde qui ne peut pas mordre n'est pas sa maintenance, c'est l'assurance qu'il
donne — et cette assurance-la, elle, ne se mesure nulle part.

## C.17 — Le champ `qualifying` est vide, et on ne saura pas s'il le restera

**Le zero le plus credible du dossier, et il ne conclut rien.** La charge utile de
`profile/matches-played` porte une cle `qualifying` : elle est presente dans
**962 reponses archivees sur 962**, et vide dans toutes. Lue comme une reponse,
elle etablit que le fournisseur ne sert pas les tableaux de qualification — avec
un denominateur de 106 701 matchs, ce qui suffit a fermer une famille.

**Le denominateur la demolit.** Les 47 qualifies de l'US Open presents en base
portent 131 reponses, **toutes relevees les 18 et 19/08**, six jours avant que
leurs rencontres soient jouees. Le zero mesurait notre fenetre de collecte.
Sonde du 26/08 : `qualifying` toujours vide, et le match du 24/08 present sous
`singles`, avec son score et ses statistiques.

**Ce qui reste indecidable, et c'est le sujet de cette section** : personne ne
peut dire aujourd'hui si ce champ est mort ou dormant. Il n'existe aucune
observation qui distingue « le fournisseur ne le remplit jamais » de « il ne le
remplit pas encore », et aucun volume de collecte n'y changera rien — un champ
vide reste vide dans les deux cas.

**La consequence a porter est une fragilite, pas une inconnue de plus.** Notre
lecture se fait sur `singles`. Le jour ou le fournisseur y deplacerait les
qualifications, nos lignes raccourciraient **sans qu'aucune erreur soit levee** :
un `Parcours` plus court, une `Usure` plus basse, et rien qui casse. C'est la
forme de defaut la plus couteuse du projet, transposee a une source.

Et elle se reproduira a la lecture : **une prochaine session relira cette cle,
verra 962 sur 962, et conclura la meme chose** — avec le meme denominateur
invisible. C'est pour ca que l'entree existe, et c'est le troisieme compte faible
de ce dossier qui se revele etre un artefact de mesure. Les deux premiers ont
produit une observation erronee ; celui-ci aurait produit un **refus definitif**.

## C.18 — Un axe non indexe est invisible en test et catastrophique en production

**La classe, pas l'incident.** Une requete qui filtre une colonne sur un axe que
rien n'indexe **passe tous les tests** : les fixtures portent quelques dizaines de
lignes, et le cout est proportionnel au volume. Elle ne se voit qu'a l'usage, et
elle se voit alors comme une page qui ne repond plus.

Mesure du 26/08/2026, sur deux copies identiques de la base servie : le board
passe de **0,043 s a 18,36 s** (x427) et `/competitions` de **0,040 s a 18,98 s**
(x475). `/stats`, qui ne touche pas a la requete en cause, ne bouge pas — **c'est
ce temoin qui rend la mesure concluante**, sans lui deux surfaces lentes
n'auraient designe personne.

**La suite etait verte a 2 698 tests.** Ce qui a trouve le defaut est une plainte
d'utilisateur.

**C'est la meme famille que tout ce dossier** — la sortie de l'echec identique a
la sortie du cas ordinaire — a une difference pres qui la rend plus facile a
manquer : le signal n'est pas un chiffre faux mais un **temps d'attente**. Rien
n'est errone a l'ecran, la page finit par s'afficher, et le seul symptome est
qu'on cesse de l'ouvrir. Une page qu'on n'ouvre pas ne dit plus rien, ce qui est
l'etat final que ce dossier passe son temps a eviter.

**Ce que la mesure a ecarte, et il fallait l'ecarter pour trouver.** Ce n'etait
pas un N+1 applicatif : deux appels a la fonction par rendu, 28 requetes SQL en
tout. Le N+1 etait **a l'interieur d'une requete** — une sous-requette correlee
par evenement, 697 evenements x 43 751 lignes, ~30,5 millions de lignes lues par
appel. `EXPLAIN QUERY PLAN` a repondu seul : `SEARCH` sur `odds`, **`SCAN` sur
`prompt_odds`**, dont le seul index portait `(session_id, event_id)` — colonne de
tete `session_id`, donc inutilisable pour un predicat sur `event_id`.

**Le correctif n'est ni un cache ni une colonne materialisee**, et c'est ce qui le
rend acceptable : les deux auraient introduit une **seconde copie de la verite**
avec sa question — qu'est-ce qui la force a concorder, et que se passe-t-il quand
une cote arrive entre deux invalidations. Un index couvrant ramene le calcul en
direct a 30 ms, donc il n'y a plus rien a echanger contre de la fraicheur. **La
conception n'etait pas fausse, la requete l'etait.**

**Un second garde-fou s'est greffe sur le meme mecanisme, et il traite un autre
mode de panne** : `test_le_critere_surveille_decrit_ce_que_la_regle_lit` lit les
**colonnes** du SQL execute et les compare a `PRICE_STATE_SOURCES`, la
declaration de ce dont l'etat « sans prix » depend. Sans lui, une colonne ajoutee
a la requete rendrait la declaration obsolete en silence, le recensement des
ecritures cesserait de couvrir le chemin qui la modifie, et le journal cesserait
de dater sans qu'aucune erreur ne soit levee. Verifie en ajoutant `c.priority` a
la requete : rouge, avec la colonne nommee.

**Le geste preventif, a reprendre en section E** : toute nouvelle requete sur
`prompt_odds`, `odds`, `events` ou `picks` passe par `EXPLAIN QUERY PLAN` **avant**
d'etre ecrite. Ce sont les quatre tables qui grossissent. Il est porte par
`CONTRIBUTING.md`, et garde pour la requete en cause par
`tests/test_plan_requetes.py` — sur le **plan** et jamais sur le temps, un test
chronometre etant instable et desactive au premier faux positif.

## C.19 — La base n'est pas append-only sur la date d'un match

**Constat du 27/08/2026, en rattrapant deux journees de qualification.** L'import
a ajoute 95 rencontres et **mis a jour 21**, et les comptes par jour des 24 et
25/08 ont baisse d'exactement 21 : `28 → 22`, `36 → 33`, `28 → 20`, `36 → 32`.

Ce ne sont pas des rencontres perdues. **La source a deplace des dates apres
publication**, et la cle naturelle `(competition_id, tennisapi_fixture_id)` a mis
a jour la ligne au lieu d'en creer une seconde — c'est le comportement correct, et
c'est lui qui garantit qu'un rattrapage ne duplique rien. Verifie : zero doublon
sur la cle naturelle, zero sur `(jour, affiche)`, zero rencontre sans identifiant
fournisseur.

**Ce qu'il faut en retenir depasse le cas.** `events.commence_time` est **mutable
apres coup**, donc toute mesure qui decoupe une population **par date de match**
ne se rejoue pas a l'identique : la meme requete, passee avant et apres un
redatage, rend deux resultats sans qu'aucun des deux soit faux. Ca n'invalide rien
de ce rapport — les decoupages qui portent des verdicts se font par
`picks.created_at`, la date de **decision**, qui ne bouge pas — mais ca ferme une
porte : **une reproductibilite ne se demande pas a un axe mutable**.

Les axes surs sont ceux qu'aucune source exterieure ne reecrit :
`picks.created_at`, `picks.result_at`, `changelog_mesure.day`, `imports_raw`. La
date de coup d'envoi n'en fait pas partie, et `commence_time` avait deja son
precedent — la migration 040, qui a du garder l'heure precedente parce que le scan
l'ecrasait.

## C.20 — L'identifiant du fournisseur designe une publication, pas une rencontre

**Et le controle qui devait le prouver ne le prouvait pas.** Apres rattrapage des
qualifications de l'US Open, j'ai verifie l'absence de doublons sur deux criteres
— la cle naturelle `(competition_id, tennisapi_fixture_id)` et le couple
`(jour, affiche)`. Les deux sont sortis a **zero**, et les deux etaient vrais.
**La propriete qu'ils devaient etablir etait fausse.**

Le compte des joueurs l'a montre : 119 joueurs distincts cote ATP pour un tableau
qui en attend 128, et **quatre joueurs a quatre apparitions** la ou trois tours
en autorisent trois. En regardant leurs lignes :

    fixture 1289   2026-08-25T18:00:00Z   Nicolas Mejia - Liam Draxl
    fixture 1376   2026-08-26T02:30:00Z   Liam Draxl - Nicolas Mejia

**La meme rencontre, deux identifiants.** Le fournisseur publie d'abord une entree
provisoire a une **heure de remplissage** — `18:00:00Z`, la meme sur toutes — puis
la rencontre definitive avec son heure reelle et un **nouvel identifiant**. Les
deux criteres passaient : les identifiants different, et le jour comme le sens de
l'affiche different aussi.

Mesure : **8 affiches en double cote ATP, 10 cote WTA**. `events` porte donc 111 et
112 lignes pour **103 et 102 rencontres reelles**. Et le phenomene **precede le
rattrapage** — quatre des dix-huit doublons ont leurs deux lignes creees le 24/08,
avant toute intervention.

**Le lecteur, lui, les absorbe.** `tournament_day` regroupe les deux lignes dans la
meme journee de tournoi, `_resolve_duplicates` garde la plus recemment creee, et
`load_for` rend **trois tours** pour Draxl avec la ligne superseded nommee
`replaced` sur `Non joue`. La chaine fonctionne — et c'est justement pourquoi le
defaut de comptage etait invisible.

**Ce que ca corrige dans le dossier** : `CLAUDE.md` documente ce cas comme
**« un seul cas »** en base, celui de JJ Wolf. Il est **systematique sur un tableau
de qualification** — dix-huit occurrences sur une seule edition.

**Et la regle de verification qui en sort** : un controle de doublon pose sur la
cle qu'on maitrise ne prouve rien sur l'entite qu'on croit compter. `events` compte
des **publications** ; le compte des rencontres passe par le lecteur qui les
resout, jamais par un `COUNT(*)`.

**Consequence pour le controle empirique de `phase_de`** : recompter un parcours a
la main doit se faire sur la sortie de `load_for`, pas sur `events`. Un joueur a
quatre lignes n'a pas joue quatre tours.

## C.22 — Le profil de tennis vieillit exactement quand il sert, et la passe ne peut pas le rattraper

**Ouvert le 28/08/2026, non instruit.** C'est la cause amont du defaut corrige
sur la ligne `Ici` : le libelle signale desormais la fenetre, il ne la ferme pas.

`scheduler` appelle `serve_stats.upcoming_players()` a heure fixe, sur les
evenements dont le coup d'envoi est a venir. Sur un tableau de tennis, **l'affiche
du tour suivant n'existe qu'une fois le tour precedent fini**, donc apres la
passe. Cas mesure : la passe du 27/08 a tourne de 05:15 a 05:40 et a rafraichi
douze profils ; l'evenement `Clara Tauson – Diane Parry` a ete cree a 16:11:08 le
meme jour. Le profil de Parry datait du **19/08** et a servi l'analyse tel quel.

Le gradient dit que ce n'est pas un accident isole. Sur les 82 joueurs des blocs
archives portant une ligne `Ici` :

| age du releve | joueurs | dont au moins une rencontre non couverte |
| ---: | ---: | ---: |
| 0 j | 20 | **0** |
| 1 j | 43 | 5 |
| 2 j | 17 | **11** |
| 9 j | 2 | **2** |

Ce qui reste a instruire, et dans cet ordre : le cout d'un rafraichissement
declenche a **l'entree d'un evenement au board** plutot qu'a heure fixe — un
appel par joueur nouveau, donc borne par le nombre d'affiches publiees dans la
journee ; et ce que ca deplacerait, la ligne `Ici` etant la seule consommatrice
mesuree de cette fraicheur. Rien n'a ete chiffre.

**Ce n'est pas un defaut de la ligne.** Le libelle corrige dit maintenant ce qu'il
sait ; fermer la fenetre est un autre chantier, et il touche la collecte.

## C.23 — Le plafond de tokens ne peut pas mordre la ou le depassement se produit

**Ouvert le 28/08/2026, non instruit, et deja franchi.**

`PROMPT_BUDGET` (23 000) et `MIXED_BUDGET` (20 000) vivent dans `tests/` et
s'appliquent a deux fixtures de six et trois matchs. `CLAUDE.md` le dit deja —
« ces deux plafonds ne voient jamais un lot reel » — mais le disait comme une
limite de lecture. C'est un garde qui ne peut pas se declencher la ou le
depassement arrive.

Mesure sur les prompts archives : le **228** (27/08/2026) pese **23 552 tokens**,
soit au-dessus de `PROMPT_BUDGET`, et rien ne s'y est oppose. Le **229** pese
18 221 pour 15 221 de cadre.

Meme famille que `confidence_floor` et `HiddenEvent.priced` — un controle dont la
surface d'application ne recouvre pas la surface du risque — avec une difference
qui compte : **celui-ci a deja ete franchi**, donc l'ecart n'est plus theorique.

Non instruit : ou poser l'alarme. `SPEC-PAYLOAD.md` §7 bis en pose deja une a
`save_prompt`, sur le prompt reellement produit ; savoir si les deux se
recouvrent, se doublent ou se contredisent est le prealable, et il n'a pas ete
fait. **Ne pas relever les plafonds pour faire passer quoi que ce soit** : ici le
nombre n'est pas faux, c'est sa portee qui est vide.

## C.24 — « Une seule selection par match, tous tableaux confondus » : trois lectures, et aucune n'est celle qui s'applique

**Ouvert le 28/08/2026, non instruit, a prendre au prochain lot de gabarit.**

Le defaut 4 a retire une des deux ecritures de la regle « une selection par
match ». Il en reste **deux** : celle de la section C, qualifiee par son
exception, et celle de la section C-bis, en gras et sans exception :

    Contraintes qui ne tombent pas : **une seule selection par match, tous
    tableaux confondus** — un match retenu en section C ne peut pas reparaitre
    ici.

Lu seul, le gras contredit une permission situee **quatre-vingts lignes plus
haut**, celle que le defaut 4 vient justement de rendre explicite. Un lecteur qui
commence par C-bis en conclut que deux lignes ne sont jamais permises. L'em-dash
qui suit dit autre chose, plus etroit — et c'est lui qui decrit le code.

**La question a instruire n'est pas la formulation, c'est la regle.** Les deux
lectures ne sont pas la meme :

- « une seule selection par match, tous tableaux confondus » interdit deux lignes
  partout ;
- « pas de C-bis sur un match deja pris en C » autorise deux lignes en C et
  interdit le melange.

**Et le code n'applique exactement ni l'une ni l'autre.** Deux ecrivains, deux
portees :

| | ou | portee | ce qu'il applique |
| --- | --- | --- | --- |
| `history.add_pick` | service | **la session, en base** | une seconde ligne sur un match exige une note d'independance |
| `picks_import` (`ingestion.DUPLICATE`) | import | **le collage courant** | une ligne C-bis est refusee sur un match deja pris en C *dans ce collage* |

`principaux` se construit sur les lignes du collage en cours, jamais sur la base.
Deux imports d'une meme session ne se voient donc pas.

**Mesure sur la base servie : 4 matchs portent une ligne en C et une en C-bis**,
tous posterieurs a la livraison de la regle (17/08/2026), tous en deux imports
d'une meme session — 856 (session 18, 11:07 puis 22:14), 918, 935 et 989
(session 20, ~12:50 puis ~16:20). Dans les quatre, la ligne arrivee en second
porte sa note d'independance : `add_pick` a bien mordu, la regle C-bis non.

Comptes voisins pour situer : 9 matchs portent deux lignes en section C — le
comportement voulu — et **0** porte deux lignes en C-bis.

Ce qui reste a decider, et c'est une decision de regle avant d'etre une
correction de texte :

1. laquelle des deux lectures est voulue ;
2. si c'est la seconde, le gras doit cesser d'affirmer la premiere ;
3. si la regle doit valoir sur la session et non sur le collage, elle appartient
   a `add_pick` — seul ecrivain qui voie la base — et `picks_import` cesse d'en
   porter une version plus etroite. **Deux ecritures d'une meme regle, dont une
   seule voit la base**, sont la forme que ce projet documente ailleurs sous
   « une seconde copie qu'aucun mecanisme n'oblige a concorder ».

Rien n'a ete touche : elargir le perimetre en fin de lot rendrait le rejeu des
quatre defauts illisible.

## C.25 — La reserve « indicatif » d'`Enjeu` tient : porte fermee, mesuree le 28/08/2026

**Resultat negatif, ecrit sous la forme qui empeche de le refaire.** La question
revient naturellement — une ligne ecrite est lue malgre sa reserve, et le dossier
a deja paye ce trajet avec le libelle « Non jouable ». Elle est close.

Le fait qui la souleve est reel et plus large qu'un cas de reprise :

| | blocs portant la ligne | dont avec la reserve | part des 1 252 blocs football |
| --- | ---: | ---: | ---: |
| `Enjeu` | 358 (28,6 %) | **208 (58,1 %)** | 16,6 % |
| `Classement` | 885 (70,7 %) | **330 (37,3 %)** | 26,4 % |

Journees concernees : `Enjeu` 1 → 125, 2 → 49, 3 → 27, 4 → 7 ; `Classement`
1 → 193, 2 → 90, 3 → 30, 4 → 14, 5 → 3. Ce n'est pas un accident de reprise de
championnat, c'est le regime ordinaire d'une fin d'aout.

**La mesure d'effet dit que la reserve tient.** Sur les blocs qui la portent, la
prose des selections (`angle_note`, `invalidation`) cite un mot d'enjeu ou de
classement — relegation, maintien, play-off, montee, podium — dans **9 cas sur
108, soit 8 %**, contre **22 sur 207 ailleurs, soit 11 %**. Fisher exact
**p = 0,558**, et la direction est meme legerement inverse de la crainte.

**Mais l'argument decisif n'est pas ce `p`, c'est le cout structurel de
l'omission.** L'absence d'`Enjeu` **porte deja un sens**, et le preambule
l'ecrit : « Absente, la competition ne declare rien a cette place — pas une
equipe sans enjeu ». Omettre la ligne sous un seuil donnerait a cette absence une
**seconde cause, indiscernable de la premiere** — exactement ce que
`HERE_NO_MATCH` contre `HERE_NO_INFO` vient de corriger sur `Ici`, reintroduit
sur la ligne qu'on pretendait assainir.

**Et la regle ne se serait pas etendue a `Classement`.** 33 des 330 lignes
reservees nomment une **division**, qui est un fait vrai a toute date :
`_division_fragment` a ete livre le 22/08 precisement parce que « le bloc opposait
un rang a un silence ». Une regle posee sur `Enjeu` et etendue uniformement a sa
voisine aurait recasse ce correctif — meme extension uniforme que le rattachement
`phase_de` a deja refusee.

- **`enjeu_min_journees` reste a 8.** L'abaisser retirerait la reserve sur 34 des
  208 lignes (journees 3 et 4), c'est-a-dire **affirmerait un classement plus
  tot** : le sens inverse du probleme souleve.
- Cout de la reserve, pour memoire : 208 lignes, **5 866 tokens** sur le corpus,
  28 par ligne, environ 40 par prompt. Ce n'est pas ce qui decide.

Ce qui rouvrirait la question, et rien d'autre : une mesure montrant une analyse
qui **batit** sur un enjeu reserve — un scenario de motivation adosse a une ligne
de 1re journee. La colonne pour le voir existe (`angle_note`), et le releve
ci-dessus est le point de depart.

**Erreur de mesure a signaler, parce qu'elle est de moi et qu'elle est la
sixieme du dossier** : le premier releve annoncait « 277 sur 358 » et
« `Classement` 643 fois ». C'etaient des **occurrences**, une par equipe, et non
des blocs. Le dénominateur contre son auteur, une fois de plus.

## C.26 — Le garde qui protege la base servie faisait tomber le banc

**Ouvert et corrige le 28/08/2026, et trouve par accident.**

`db.scratch_copy()` est la moitie **ecriture** de la regle « toute lecture se
fait sur une copie », ajoutee le 21/08 apres qu'une migration est partie sur la
base servie. Elle cree un `mkdtemp`, y fait un `VACUUM INTO`, rend des `Settings`
derives — et **ne supprime jamais rien**, alors que son docstring annonce, depuis
le premier jour, une copie « jetable ».

**Le symptome est ce qui rend le defaut interessant.** `/tmp` est un tmpfs de
5,8 Go ; il a sature, et le banc est sorti a **884 tests en erreur** sur
`sqlite3.OperationalError: database or disk is full`. Aucun de ces tests n'avait
quoi que ce soit a se reprocher.

- **Un banc qui tombe pour une cause sans rapport avec le code est ce qui fait
  defaire un correctif juste.** Le risque etait nomme au chantier 4 du meme
  dossier ; cette fois il s'est produit.
- **Et la prochaine personne a le rencontrer n'aurait pas regarde `/tmp`** : elle
  aurait cherche dans le code, sur 884 lignes rouges qui ne designent rien.
- **Un garde ecrit pour proteger la base servie qui finit par casser le banc est
  un effet de bord qui contredit sa raison d'etre.**

### Ce qui etablit que c'est le mecanisme et non un incident

La session qui l'a trouve avait sa part — quatre copies de 413 Mo la ou une
suffisait, plus 2,3 Go de repertoires `pytest-of-ubuntu`. Ce n'est pas ce qui
tranche. Ce qui tranche est ce qui restait **avant** elle :

| | |
| --- | --- |
| repertoires `mab-copie-*` trouves | **28** |
| dont anterieurs a la session | 2 de **392 Mo**, dates du 27/08 22:04 et du 28/08 09:36 |
| les 26 autres | 452 Ko chacun, **un par execution du banc** |

Un seul appelant existe dans le depot — `tests/test_db.py` — et **aucun test ne
dependait de la persistance** : la copie est lue dans le corps du test et jamais
apres. Les 392 Mo, eux, viennent d'appels ad hoc sur l'instance servie, donc du
geste que la regle recommande.

A ce rythme, quinze copies de la base servie remplissent le tmpfs.

### Le correctif, et les quatre cas

`scratch_copy` devient un **contextmanager** :

| cas | ce qui est supprime | ce qui est dit |
| --- | --- | --- |
| sortie normale | le repertoire temporaire, entier | rien |
| exception dans le bloc | **rien** | le chemin, en `WARNING` |
| `keep=True` | **rien** | le chemin, en `INFO` |
| `into=` fourni | **rien, jamais** | le chemin, en `INFO` |

- **Conserver sur exception** parce que supprimer retirerait la piece a
  conviction au moment precis ou elle sert.
- **`keep=True` plutot qu'un defaut permissif** : relire une copie apres coup est
  un cas minoritaire, et il doit se voir **dans l'appel**.
- **`into=` n'est jamais nettoye** — on ne supprime que ce qu'on a cree, sinon le
  menage emporterait ce que l'appelant a mis a cote dans son repertoire.
- **Dans les trois cas ou la copie survit, le chemin est annonce.** Une copie
  conservee sans son adresse est un fichier perdu de plus dans `/tmp`, c'est-a-dire
  le defaut qu'on corrige sous un autre nom.
- Un echec du `VACUUM INTO` lui-meme nettoie aussi : il n'y a alors rien a
  examiner, et le repertoire ne doit pas survivre a son echec.

Mesure apres correctif : le banc complet laissait **une** copie par execution, il
n'en laisse **aucune** — verifie avant et apres sur 2 765 tests.

### La prose, sixieme occurrence

**Le docstring disait « jetable » et rien ne jetait.** C'est ce qui a empeche de
voir le defaut pendant une semaine : on lit la phrase, on la croit, on ne verifie
pas. Un docstring qui decrit un comportement absent coute plus qu'un docstring
absent — il fait re-deriver la meme conclusion fausse, et ici il a fait sauter
l'etape de verification entiere. Corrige dans le meme commit que le comportement.

## C.27 — La borne du lot ecrivait une autre orthographe que son bloc

**Ouvert et corrige le 29/08/2026. C'est C.26 d'un cran plus bas** : le chantier
de la veille a corrige **quelle** cote la borne nomme, celui-ci **comment elle
l'ecrit**.

Cas reel, prompt 232, deux lignes du meme prompt a 226 lignes d'ecart :

    ligne 577 :  ... RealRacingClubdeSantander:2|ElcheCF:2 49.74
    ligne 803 :  Cote max du lot : 49.74 (M7 · Score ex. MT Real Racing Club de
                 Santander:2|Elche CF:2)

`prompt._outcome_text` lisait `outcome.name` brut quand cinq rendus impriment une
forme a eux — et son docstring annoncait « l'issue **telle qu'elle se lit** ».

| sur les 144 lignes de bornes archivees | |
| --- | ---: |
| nomment un score exact en forme longue | **45 (31 %)** |
| nomment `No` quand le bloc ecrit `Non` | 1 |
| divergences **latentes**, jamais encore sorties | `Eq. buts … Over 1.5` / `O1.5`, `Handicap … 0.5` / `+0.5` |

### La cause, et pourquoi le rendu seul n'aurait pas suffi

`render._render_correct_score` ecrivait `outcome.name.replace(' ', '')`. Deux
vocabulaires de fournisseur en base — The Odds API `Cruzeiro:0|Mirassol:0`,
API-Football `0:0`, **4 568 contre 8 157, aucun evenement servi par les deux**.
Le `.replace` ne fait rien sur le second et **soude** le premier. Il n'y a donc
pas deux chemins de formatage : il y a un chemin ecrit pour un vocabulaire qui
n'en avait pas besoin, et le defaut n'est apparu que le jour ou Pinnacle a servi
la profondeur — premier bloc le 09/08, 188 blocs archives (27 % de ceux qui
portent un score exact), concentres sur sept competitions.

**Rendre la forme courte sans toucher au second lecteur aurait aggrave l'ecart** :
la borne aurait continue de nommer `Real Racing Club de Santander:2|Elche CF:2`
quand le bloc ecrit `2:2`, deux chaines n'ayant alors plus rien en commun.

### Aucune selection n'a ete touchee, et c'est mesure

L'hypothese d'un P0 — un nom colle recopie dans la colonne `Match`, qui sert de
somme de controle a l'appariement des blocs `conf` — est **refutee** : sur les
81 collages de `imports_raw`, **zero affiche collee**, les seuls tokens en casse
chameau etant des noms d'editeurs. Le seul pick de score exact sans bloc apparie
appartient a un import dont les cinq lignes en sont depourvues.

Ce qui a ete touche est le **libelle** de 4 des 10 selections de score exact, et
le modele en a reecrit trois de lui-meme (`Le Mans FC 1 – Brest 0`) : la base
porte trois notations pour un marche. Rien n'est retro-rempli.

### Le correctif ferme la classe, jamais un marche

`Rendered` rend des `Shown` — l'issue **et le jeton que la ligne ecrit pour
elle**. Une table « marche -> orthographe » posee a cote aurait ete la seconde
copie qu'on supprime, et elle aurait diverge au premier rendu ajoute ;
**l'invariant est porte par la signature**, comme celui des issues retenues un
cran plus haut. Un jeton vide dit que la ligne n'ecrit **aucun** nom d'issue —
une echelle imprime sa ligne et ses deux prix — et il n'y a alors rien a faire
concorder. Sur les 288 bornes archivees : 81 sur un score exact, 36 sur
`Eq. buts`, 60 sur une echelle O/U.

Deux defauts voisins partaient avec, sur la meme ligne :

- **`Score ex. MT` faisait 12 caracteres pour un `LABEL_MAX` de 11**, seul
  libelle de marche sur 26 a depasser. Le test qui garde la regle ne parcourait
  que `CONTEXT_ICONS`, alors que son propre docstring affirmait que « les cles
  de marche etaient deja tronquees ». Il les parcourt desormais.
- **Aucun banc ne pouvait voir le defaut** : les fixtures de score exact
  nommaient leurs issues `1-1`, une notation qu'aucun fournisseur ne produit.
  Voir §8 — troisieme forme du montage aveugle.

Cout : **-12 370 tokens sur les 232 prompts archives**, -72 sur le lot du 28/08.

## C.28 — La meteo qualitative est colineaire au seuil numerique : porte fermee

**Resultat negatif du 29/08/2026, ecrit sous la forme qui empeche de le
refaire.** La porte avait ete fermee la veille sur l'effet ; la piste rouvre
naturellement parce que `orage grelant` est un mot et non un nombre, donc les
seuils mesures ne le verraient pas. **Ils le voient tous.**

| sur les 407 matchs distincts portant une ligne Meteo | |
| --- | ---: |
| libelle d'orage **et** deja dans la queue 35 C / 90 % / 44 km/h | **3** |
| libelle d'orage **hors** de cette queue | **0** |

Les trois : Annecy - Rodez (rafales 61), Ried - Grazer AK (rafales 80), Austria
Lustenau (pluie 100 %). Le bloc M8 du lot qui a souleve la question est donc
**dans** la queue, par sa pluie.

Et l'autre moitie de la piste ne discrimine pas davantage : sur les 659 blocs
portant la ligne, **88,8 % annoncent « alertes non interrogees »**. Un critere
pose dessus se declencherait sur neuf blocs sur dix.

**Le chiffre qui tient la porte a ete corrige au passage**, et c'est le point de
methode. Le releve d'origine annoncait 10 sur 14 (71 %) contre 27 sur 213 ;
re-mesure sur la population large — toutes les selections a prose renseignee,
liste de mots elargie au vent, a la chaleur et au terrain — **11 sur 21 (52 %)**
contre 33 sur 231 (14 %), Fisher exact `p = 1,3e-4`. Meme direction, meme
significativite, **deux populations differentes**. Pres de la moitie des blocs de
queue ne citent pas la meteo : l'argument est affaibli, pas renverse. **Une porte
fermee sur un chiffre qui bouge se rouvre sur la decouverte de l'ecart plutot que
sur une raison** — donc elle porte le chiffre corrige.

Ce qui rouvrirait la question, et rien d'autre : l'etat de l'alerte a l'heure du
coup d'envoi, qui n'est dans aucun bloc.

## C.29 — Le silence de la fiche de recherche n'etait defini nulle part

**Ouvert et corrige le 29/08/2026.** Un lot de huit annonce huit dossiers
ouvrables et la fiche en classe trois. Le gabarit ecrit deja « ordre de
traitement, pas un tri » : les deux enonces sont **compatibles**, et la premiere
formulation du defaut — une contradiction interne — est fausse.

Ce qui manquait est ce que **l'absence** veut dire. Le lecteur ne pouvait pas
distinguer « rien a chercher ici » de « rien ne le classe ». Meme famille que
`HERE_NO_MATCH` / `HERE_NO_INFO` : nommer le cas vrai et le cas faux.

**Le nombre n'a rien a corriger, et c'est mesure** sur les 111 fiches archivees :

| | silence median | moyen | fiches pleines |
| --- | ---: | ---: | ---: |
| avant le 17/08 | 0 | 1,58 | 23 / 33 |
| 17 au 22/08 | 1 | 1,47 | 17 / 53 |
| 23/08 et apres | 1 | 1,72 | 8 / 25 |
| les 20 derniers | 1 | **1,40** | 8 / 20 |

Regime stable, pas une derive : les vingt derniers prompts sont **sous** la
moyenne du corpus, et un silence de 6 s'est deja produit sept fois les 14 et
15/08. Le 5 du lot du 28/08 est dans la queue de la distribution.

Elargir les criteres reste le chantier ouvert et date des deux criteres faibles
du football. Rapprocher le budget de la fiche a ete ecarte : le nombre est lu
ailleurs — le paragraphe des paliers hauts en fait sa borne — et le faire
dependre des criteres l'aurait fait varier avec eux.

## C.30 — Le code charge et le gabarit servi divergent sans un mot

**Ouvert et corrige le 29/08/2026, apres que le defaut s'est produit sur un lot
livre la veille.**

Le gabarit Jinja est relu **sur le disque a chaque generation** — c'est voulu.
Les modules Python sont charges **une fois**, au demarrage. Un deploiement sans
redemarrage laisse donc l'application a moitie a jour, **et rien ne le dit** : le
prompt se genere, aucune erreur ne se leve.

| | |
| --- | --- |
| processus demarre | **13:12:16** |
| `render.py` ecrit | **13:51:19** — 39 min apres le demarrage |
| commit `124755e` | **14:16:16** — 64 min apres |

Le fichier source est posterieur au processus qui aurait du le charger : il ne
pouvait pas l'avoir fait. La moitie du lot passait — les changements de gabarit —
et l'autre non. **Le defaut n'a ete trouve qu'en relisant un prompt ligne a
ligne.**

### `/health` repondait « ok », et c'est le coeur du sujet

Il expose `version`, qui est celle du **paquet** : elle ne bouge pas quand le code
bouge. Un indicateur existait, il repondait a la question **sans y repondre** —
meme famille que `UNPRICED_ARMED`, distinguer « rien n'a change » de « le
mecanisme n'a pas tourne ».

### Le garde

`services/runtime.py` compare une empreinte prise **a l'import** — donc au
demarrage, ce que Python vient de charger — a une empreinte du disque **au moment
de l'appel**. Aucun git, aucune date, aucune version de paquet : deux sommes de
la meme chose a deux instants. `changelog.fingerprint` est **appelee** et non
reecrite.

- **Toutes les sources**, pas seulement celles du rendu : une empreinte partielle
  ferait manquer exactement le changement qu'on n'avait pas prevu. **11 ms pour
  74 modules**, gratuit au demarrage et negligeable sur une generation.
- **L'instantane depend de l'ordre d'import**, seule fragilite : `main` importe
  `runtime` au niveau du module. Paresseux, il capturerait le disque **apres**
  l'edition. Un banc lit `main.py` et l'exige.
- **Trois etats.** `inconnu` couvre des sources illisibles : rendre « a jour »
  serait indiscernable d'une verification reussie, et rendre « obsolete »
  accuserait sans savoir.

### Deux surfaces, et pas la troisieme

`/health` repond a qui l'interroge ; le **bandeau** se voit quand on ne cherchait
pas, et c'est ce qui a manque. La pastille ne s'eteint qu'au redemarrage — meme
famille que les competitions non rattachees, qui restent sous les yeux jusqu'au
geste.

**Rien dans le prompt, et c'est une decision.** Le modele ne peut pas redemarrer
un service : l'information n'a rien a faire dans une sortie qui s'adresse a lui.
Un banc lit le gabarit et le verifie.

### Chantier ouvert, non fait : l'estampille de cadre est gelee sur la session

`sessions.gabarit_version` et `gabarit_sha` sont poses par `COALESCE` au premier
prompt. Le gel est **juste pour le passe** — ne pas reetiqueter ce qui a deja ete
rendu — et **faux pour la suite** : il etiquette les prompts neufs avec l'ancien
cadre, au moment meme ou la regle « un seul point de rupture » sert.

Mesure : **6 prompts sur 86 (7 %)** portent une estampille qui ne designe pas le
cadre en vigueur ; la session du 28/08 est estampillee `lot-4` apres avoir produit
sous lot-5 puis lot-6. `sessions.open_dossiers` a le meme defaut avec un symptome
mesure — **30 selections ecrasees pour `ligne_absente` sur des sessions declarees
`renseignee`**, l'etat de session ne gardant que le dernier import. Les deux se
reprennent **ensemble** : migration et colonne sur `prompts`, un seul geste.

## C.31 — La fiche classe tous les blocs, et la perte qui l'accompagne est ecrite

**Decision de l'utilisateur du 29/08/2026, appuyee sur une mesure faite avant
d'ecrire une ligne.** Le budget annoncait 8 dossiers, le texte disait « tous les
matchs sont ouvrables », et la fiche en classait 3 : cinq blocs arrivaient sans
question.

### Le silence ne decrivait pas la realite

Sur les 1 000 blocs des prompts archives portant une fiche :

| | blocs | ont produit ≥1 selection | `lecture` | faits dates / selection |
| --- | ---: | ---: | ---: | ---: |
| classes par la fiche | 627 | 281 (**45 %**) | 14 % | **0,95** |
| silence de la fiche | 373 | 61 (**16 %**) | 26 % | **0,97** |

Fisher : production `p = 4,3e-21`, part de `lecture` `p = 0,034`.

**Ce qui se conclut** : conditionnellement a une selection produite, le rendement
en faits dates est **identique**. Quand l'analyse a travaille un bloc muet, elle
y a trouve autant — le silence decrivait « rien que nos criteres detectent ».

**Ce qui ne se conclut pas** : le 45 % contre 16 % est **confondu**, la fiche
selectionnant **et** dirigeant, et il ne prouve rien sur les 84 % restants. D'ou
une consigne qui retient au lieu d'envoyer.

### La forme : une consigne, jamais une question

`aucun signal detecte — cadrage du lot seul`, et **aucune requete de recherche**
sous un bloc muet — `-> rechercher "…"` serait une question deguisee. Une
question generique le serait par construction : c'est le defaut paye deux fois
cette semaine, 36 dossiers de tennis sur 41 portant la meme phrase mot pour mot,
puis 4 de football sur 4.

**La completion ne vaut que si elle n'est pas un choix** : elle ne s'applique que
si le lot tient dans le budget. Au-dela, ajouter des blocs sans critere
reviendrait a en designer quelques-uns au hasard — pire qu'un silence, qui au
moins ne choisit pas. Mesure : **80 fiches sur 113 (71 %)** tiennent dans leur
budget, et 10 des 33 saturees ne remplissent meme pas le leur.

Cout : **+170 tokens** sur le lot du 28/08 (8 blocs dont 5 muets), +7 209 sur les
232 prompts archives.

### La perte sur `dossiers_ouverts`, acceptee et chiffree

Le garde-fou **ne tombe pas structurellement** : `picks_import` n'importe jamais
`research`, et l'ecrasement compare la liste declaree a l'evenement du pick,
jamais a la fiche. Elargir la fiche ne peut donc pas l'eteindre mecaniquement.

Et **le modele ne recopie pas la fiche** : declaration plus courte que la fiche
dans **7 sessions sur 10**, ecart median 2, part du lot declaree ouverte
**mediane 70 %**, minimum 20 % — fiche a 10 pour 2 declares, fiche a 8 pour 4.

Il **s'amincit** en revanche en proportion de ce que la fiche gagne, et **rien ne
le remplacerait** s'il s'eteignait : il ne resterait que la declaration
(`source_level`, blocs `conf`), sans recoupement. L'application ne peut pas
verifier qu'une recherche a eu lieu.

**`hors_dossiers` est la seule cause d'ecrasement qui observe le modele**, et son
rendement baissera mecaniquement. Sur les 320 selections des sessions portant la
ligne : **19 `hors_dossiers` (5,9 %)**, contre 30 `ligne_absente` et 18
`reperes_non_resolus`, qui sont des defauts de collage.

**Ce qu'il faudrait pour voir la baisse**, le groupe « avant » etant fige a 320
selections (10 sessions, ~32 par session) :

| baisse | selections par groupe | selections apres | detectable ? |
| --- | ---: | ---: | --- |
| 5,9 % → 3,0 % (moitie) | 754 | — | **non, jamais** contre ce groupe avant |
| 5,9 % → 2,0 % (un tiers) | 377 | **459** (~14 sessions) | oui |
| 5,9 % → 0 % (extinction) | 125 | **78** (~2 sessions) | oui |

**Une division par deux ne sera donc pas detectable**, quel que soit le temps
qu'on laisse : le groupe « avant » plafonne la puissance a 640 en taille
effective, sous les 754 requis. Ce qui se verra est une extinction en deux
sessions, ou une chute au tiers en quatorze. Ecrit ici pour qu'on ne demande pas
dans un mois si la baisse a eu lieu sans que personne puisse repondre.

**Reserve de mesure, et c'est un ordre entre deux chantiers** :
`sessions.open_dossiers` etant gele sur la session, la declaration ne se suit pas
import par import — on ne verra pas la liste se rigidifier dans le detail. C'est
le meme correctif que l'estampille de cadre, ouvert en C.30.

**La surveillance decrite ci-dessus ne peut donc pas commencer avant C.30.** Les
deux chantiers sont lies dans cet ordre, et l'inverse ne marche pas : mesurer la
rigidification sur un champ qui ne garde que le dernier import donnerait une
serie dont on ne saurait pas ce qu'elle decrit. Ecrit ici parce qu'une dependance
qui ne vit que dans une conversation se perd — c'est ce que B1 a coute, neuf lots
durant.

## C.32 — Un garde de frontiere decrit comme une propriete de l'objet

**Mesure du 28/08/2026, sur le corpus des dix combines enregistres.** Le combine
8 porte **une jambe**, `declared_price` 5.09, produit recalcule **2.08**. Le bloc
d'origine en declarait trois — `["M7", "M8", "M3"]`, relu dans `imports_raw`.

`CLAUDE.md` affirme qu'« un combine dont une jambe n'a pas ete importee n'est pas
enregistre ampute ». **La phrase est vraie a l'import et fausse ensuite.**

- Le garde existe, il est correct, et il n'a jamais ete contourne :
  `picks_import._attach_combos` refuse tout combine dont un repere ne se resout
  pas (`4af17cd`, 14/08, huit jours avant le combine 8), et `main._record_combos`
  refuse si une ligne n'a pas ete importee. Les imports 68 et 70 ont declare un
  combine et n'en ont produit **aucun** : le garde a tire.
- La cause est en aval, dans le schema :
  `combo_legs.pick_id ... ON DELETE CASCADE` (migration 047). **29 identifiants de
  selection manquent globalement**, dont 401, 402, 407, 408, 409 autour de
  l'import 48. Supprimer une selection ampute silencieusement tout combine qui la
  contenait, et `declared_price` reste en face d'un produit qui ne le vaut plus.

**La famille de §8 qu'elle nomme** : une phrase qui decrit un **garde de
frontiere** — vrai a l'instant ou la ligne entre — se lit comme une **propriete de
l'objet** — vraie tant qu'il existe. Les deux se distinguent par une seule
question : *qu'est-ce qui peut arriver a cette ligne apres son ecriture ?*

**Ouvert, non corrige.** Trois traitements possibles et aucun n'est evident : un
`ON DELETE RESTRICT`, qui interdirait de supprimer une selection referencee ; une
marque d'amputation sur `combos` ; ou l'aveu, en rendant `computed_price`
incalculable des que le nombre de jambes ne vaut plus celui du bloc declare — mais
le bloc n'est pas conserve, seuls ses offsets le sont.

## C.33 — L'idempotence vit une couche au-dessus et ne descend pas

**Meme mesure, meme corpus.** Les combines 1, 2 et 3 sont **le meme combine** :
memes trois jambes, meme `prompt_id` 159, meme `import_id` 18 — ecrits le 19/08 a
17:26, le 20/08 a 09:18 et le 20/08 a 19:13.

- `imports_raw` porte `UNIQUE (session_id, sha256)` : recoller le meme texte rend
  **le meme** identifiant d'import. La deduplication existe, et elle fonctionne.
- `picks` n'a pas ete triple — deux selections pour l'import 18.
- **`combos` n'a aucune cle naturelle**, et `main._record_combos` s'execute
  inconditionnellement a chaque import portant un champ de combine.

**Ce n'est pas le defaut d'appariement d'import deja connu** (C.27 et le collage
partiel posterieur a un collage complet), et la verification a ete faite avant de
l'ecrire comme neuf : celui-la porte sur des blocs `conf` et produit des **rejets**,
celui-ci produit une **ecriture dupliquee**.

La regle generale : une deduplication posee sur une couche ne protege pas celles
qui la consomment. `imports_raw` repond a « ce texte est-il deja entre » ;
`combos` ne repond a rien.

**Portee** : 3 lignes sur 10, soit 30 % du corpus de combines. Aucune consequence
sur une population mesuree — `analysis()` ne lit pas `combos` — mais toute lecture
de ce corpus doit deduire les doublons avant de compter.

## C.34 — `price_real` valait 9, il en vaut 184

**Copie de document vieillie, et ce n'est pas le nombre qui coute.** `CLAUDE.md`
et `history.Worksheet.without_real_price` portent tous deux « 9 lignes sur 116 ».
Mesure du 28/08/2026 : **184**.

Le nombre fonde **deux conclusions** qui sont a re-mesurer avec lui, et non
seulement a corriger :

- « le controle qui valide ou invalide le resultat principal du projet ne peut
  donc pas etre fait » — a 184 paires, il le peut peut-etre ;
- « les 9 paires ont **toutes** `price_source = reference` », d'ou la reserve que
  l'ecart mesure ne dit rien des selections cotees chez le book principal. La
  composition des 184 n'a pas ete relue.

Sixieme occurrence de la regle : **compter les copies avant de declarer une
correction faite**, et `grep` sur le chiffre plutot que sur le souvenir de
l'avoir ecrit. Ici il y en a au moins deux, et la mesure qui les met a jour n'a
pas ete conduite — c'est elle le chantier, pas la substitution du nombre.

## C.35 — Le zero de `stop_reason` etait une propriete de la population

**Instruit le 28/08/2026, et les deux hypotheses de depart etaient fausses.**
`combos.stop_reason` et `combos.target_price` sont nuls sur **10 combines sur
10** — deux champs dont le module dit qu'ils diront si la cible est bien reglee.
Ni `confidence_floor` (un garde qui ne se declenche jamais) ni `HiddenEvent.priced`
(une propriete inatteignable) : une troisieme forme, et c'est la regle du
denominateur.

- Les dix sont **tous `court`**, et le gabarit dit en toutes lettres que `arret`
  « se laisse vide sur le court, dont le nombre de jambes est fixe d'avance ».
  `stop_reason` nul est le **comportement documente**.
- Les dix viennent d'un prompt de lot **9 ou 10**, donc de la branche « un seul
  combine » (`combo_solo_min_lot` = 9, `combo_min_lot` = 20). Cette branche
  **n'annonce aucune cote cible**. `target_price` nul suit.
- Verifie sur les collages bruts : les dix blocs portent les deux cles, remplies
  `"cible": null, "arret": ""`. Le modele a repondu correctement a une question
  qu'on ne lui posait pas.

**Le constat qui reste, et il etait sous les deux hypotheses : aucun combine
`long` n'a jamais existe.** Zero bloc `"type": "long"` sur les 78 collages, et la
branche a deux combines a tire **4 fois sur 85 prompts** sans jamais produire. Le
denominateur des deux champs est **0 long**, pas 10 combines.

Consequence : la question « la cible est-elle bien reglee » n'a **aucune
observation**, et elle n'en aura pas tant que les lots ne franchiront pas
`combo_min_lot`. Porte ouverte, et son horizon se compte en lots de vingt matchs,
pas en jours.

## C.36 — La montante : fermee par le calendrier avant de l'etre par le principe

**Resultat negatif du 28/08/2026, ecrit sous la forme qui empeche de le refaire.**
La demande — « quelles selections peuvent servir pour une montante » — a ete
instruite en trois voies. Elle est close, et **pas pour la raison attendue**.

Le debat de principe portait sur §9 : une montante est un systeme de mise
progressif, et demander quelles selections y conviennent revient a les classer par
probabilite de gain. Ce debat n'a pas eu a etre tranche.

**Une montante enchaine**, donc elle a besoin de matchs qui ne se chevauchent pas.
Restreint a ce qui s'etablit — le football, dont la duree est le format du sport —
l'enchainement le plus long parmi les selections `SAFE` a cran >= 4 :

| | mediane | min | max | journees < 3 pas |
| --- | ---: | ---: | ---: | ---: |
| enchainement etabli, `SAFE` & cran >= 4 | **1** | 0 | 5 | **20 / 23** |
| enchainement etabli, `SAFE` seul | 2 | 0 | 10 | 15 / 23 |

**Le calendrier interdit la fonctionnalite les trois quarts du temps.** Une
montante a deux pas n'est pas une montante.

- **Correction d'un premier releve, et elle va dans le sens defavorable** : la
  premiere mesure annoncait une mediane de 2, en pretant au tennis une duree de
  150 minutes. **Aucune source ne la publie** — verifie le 07/08/2026, et les
  fichiers qui la portaient ont disparu. Un chevauchement calcule dessus est un
  booleen bati sur un nombre invente, exactement la regle du 21/08.
- Second constat, independant : parmi ces memes candidates, la mediane de celles
  cotees chez le **book principal** vaut **0**, et 18 journees sur 23 en portent
  moins de deux. Une progression calculee sur une cote qu'on n'obtiendra pas est
  une fiction arithmetique.
- Le filtre deterministe, lui, **fonctionne** : `SAFE` & cran >= 4 rend une
  mediane de 3 selections par jour et **jamais zero** sur 23 journees. Ce n'est
  pas le compte qui manque, c'est l'enchainement.

**Ce qui a ete livre a la place** : le recapitulatif rend le **chevauchement
horaire**, que rien n'affichait — le palier et le cran etaient deja a l'ecran — et
ne nomme aucun systeme de mise. La decision de mise reste entiere et hors de
l'outil.

**Ce qui rouvrirait la question, et rien d'autre** : un regime de lots ou les
coups d'envoi s'etalent assez pour qu'un enchainement de trois pas existe la
plupart des jours. Ce n'est pas une question de volume de selections — le compte
est deja la — c'est une propriete du calendrier des competitions suivies.

## C.37 — La suite de tests ecrivait dans l'instance servie

**Mesure du 28/08/2026, prouvee et non deduite.** `isolated_settings` redirigeait
`DB_PATH`, `DEV_CACHE_DIR` et `BACKUP_DIR` — et **pas `UPLOAD_DIR`**. Les bancs de
capture de coupon ecrivaient donc dans `data/uploads` de l'instance servie.

- **Preuve** : releve des dates de modification, execution de
  `pytest -k "capture or screenshot"`, nouveau releve. Les deux fichiers sont
  passes a l'instant du run. Deduit du code, le constat aurait ete une lecture ;
  mesure, il est un fait.
- **Le symptome etait masque par un detail sans rapport** : le nom d'un fichier
  de capture porte l'empreinte de son contenu (`coupon-{id}-{empreinte}.{ext}`),
  donc chaque execution **ecrasait** le meme fichier. Le compte de fichiers ne
  grossissait jamais, et c'est ce qui a rendu la fuite invisible.
- **Meme famille que l'incident du 21/08** — un garde qui isole trois chemins et
  pas le quatrieme — et sœur de `db.scratch_copy`, qui est la moitie **ecriture**
  de la regle « toute lecture se fait sur une copie ».
- **Il se serait efface tout seul.** Les bancs fautifs partent avec les coupons :
  sans ce releve, personne n'aurait jamais su que la fixture n'etait pas isolee.
  **Un defaut qui se resout par la suppression de son seul temoin est un defaut
  qu'on ne retrouve pas** — c'est la raison de le corriger dans le meme lot.

**Le correctif enonce la propriete et non le champ.** `conftest` boucle sur les
champs `Path` de `Settings` : un chemin ajoute demain est isole sans que personne
y pense, et le banc survit au retrait de `upload_dir` — ce qui est arrive le jour
meme. **Aucun cinquieme chemin**, verifie : le modele en porte exactement quatre.

**Un cas voisin existe et n'est pas le meme** : `tests/test_settings_ui.py` ecrit
de vrais gabarits dans `src/myassistantbet/templates/prompts/`, hors de toute
isolation — mais avec une fixture de nettoyage explicite et un commentaire qui le
dit. C'est un garde plus faible qu'une isolation (un plantage en cours de test
laisse un fichier, et `template_fingerprint()` balaie ce repertoire), et c'est une
decision assumee, pas un oubli. Laisse tel quel.

## C.38 — Le retrait des coupons, et ce qu'il rend visible

**Chantier du 28/08/2026.** Le suivi des paris poses est retire : module, routes,
surfaces, 46 bancs. Les colonnes et les migrations 010 et 011 restent.

- **Rien n'y a jamais ete ecrit** : `coupons` vide, `picks.coupon_id` nul sur 615,
  `picks.played` faux sur 615, `mises` et `bankroll_journee` vides.
- **Le precedent de `/players/squads` ne s'applique pas.** La migration 022
  supprimait des **lignes** collectees des mois sans lecteur ; il n'y a ici aucune
  ligne. Squads etait une **collecte sans lecteur**, les coupons une **surface
  d'ecriture sans utilisateur** — la seconde ne laisse rien a nettoyer.
- **Ce que le retrait rend visible, et c'est le resultat du lot** :
  `history.stats()` lisait `WHERE played = 1`, donc zero ligne depuis toujours ;
  `coupons.rates()` rendait une liste vide ; `Analysis.played` / `skipped`
  etaient gardes par `comparable`, qui exige `played.settled > 0`. **Les deux
  chiffres et leur phrase n'ont jamais ete rendus une seule fois.** Ce n'est pas
  une carte qui devient morte, c'est une carte qui n'a jamais vecu.
  `Analysis.overall` etait deduit d'une fusion dont une moitie etait toujours
  vide : il compte desormais directement et rend le meme nombre.

**Le vrai risque du chantier n'etait pas dans le brief.** `COUPON_TRACKING`
n'etait pas un interrupteur de coupons : il ouvrait aussi la saisie de la **cote
obtenue**, qui controle le prix enregistre et non ce qu'on en fait — le seul
controle qui existe sur `picks.price`, le nombre sur lequel repose tout le
residu. Elle est **vivante** : 184 lignes, 35 sur 39 selections le 27/08, 17 sur
28 le 28/08, 38 paliers revus. Le drapeau a donc ete **scinde avant** le retrait,
et `saisie_cote_obtenue` la porte seule, ouverte, avec ses deux surfaces liees.

**Trois copies de « ce que le gate ouvre » se contredisaient**, dont une rendue a
l'ecran des reglages et promettant encore la section G, retiree la veille. Toutes
corrigees, apres avoir ete comptees.

**Ce que le lot a trouve en passant, et qui n'etait pas demande** :

- `picks.stake` est orpheline **independamment des coupons** — son unique
  ecrivain etait nourri par un champ de formulaire qui n'a jamais existe, et elle
  n'avait aucun lecteur. La forme exacte que `stakes.py` a recue le 27/08 ;
- `prompt.build_prompt` passait encore `mise=stakes.brief(…)` au gabarit, pour une
  variable que la section G lisait et qui n'existe plus. `brief` rejoint la pause,
  **declaree** — le banc des orphelines de `stakes` compare desormais par egalite,
  pour qu'une orpheline de plus comme une orpheline de moins soient une decision.

## C.39 — Un etat qui ecrase ce qu'il sait calculer

**Mesure du 29/08/2026, et elle porte sur ce dont j'etais le plus sur.** La
colonne d'enchainement du recapitulatif faisait basculer en « indetermine » tout
ce qui suivait une ligne sans fin connue. Rejeu des 23 journees :

| | lignes | part |
| --- | ---: | ---: |
| `indetermine` | 280 | **57,3 %** |
| `chevauche` | 170 | 34,8 % |
| `libre` | 39 | **8,0 %** |

**Une seule ligne suffisait a effacer le calcul de toutes les autres** : 48 sur
80 le 22/08, 38 sur 49 le 15/08. La cause est structurelle — la ligne de tennis
tombe au rang 1 a 3 sur onze journees, les sessions de jour se jouant avant le
programme de football du soir.

- **La regle etait juste** : aucune source ne publie la duree d'un match de
  tennis, et l'inventer serait un booleen bati sur un nombre invente. C'est la
  **forme** qui etait fausse.
- Deux matchs de football se chevauchent ou non, et un match de tennis intercale
  n'y change rien. L'etat se calcule donc contre les seules lignes dont la fin
  est connue ; celle qui n'en a pas porte son propre etat. Ce qu'elle laisse
  indetermine est **nomme a part**, jamais propage — meme discipline que
  `Non servis` et ses trois causes.
- Apres correctif : 62,6 % / 27,6 % / 9,8 %. Les **145 lignes de football** que
  la contamination effacait retrouvent un etat exact.

**Ce que l'episode dit de la methode** : le premier rendu reel portait le tennis
en **derniere** ligne, donc aucun etat contamine. Nous avons tous deux conclu que
la regle ne coutait rien, et le brief suivant classait le point « mineur, a
instruire seulement si le reste est fait ». **Une mesure sur un seul rendu decrit
ce rendu** — c'est la meme faute que le seuil de repos pose sur un mode, et que
la duree de tennis enjambee la veille.

## C.40 — Un vivier surabondant et aucun critere : deux rendus, deux combines

**Mesure du 29/08/2026.** Le recapitulatif donnait le vivier et demandait de
composer, sans dire comment choisir. Sur 23 journees x 3 propositions, le vivier
est **determine 1 fois sur 69** — surabondant 64 fois.

- Le premier rendu paraissait determine par « coincidence de nombres ». C'etait
  une **coincidence d'heure** : le vivier le plus exigeant portait 4 matchs a
  l'aube du 28/08, 3 a 15:31 parce qu'un match avait commence, et **0** a 19:00.
  Les deux defauts — pas de critere, pas d'heure — sont le meme fait vu a deux
  endroits.
- **Le critere candidat du brief echoue, et il fallait le mesurer avant de le
  retenir** : « privilegier les prix maison » laisse le vivier sans **aucun**
  candidat cinq journees sur 23, dont des journees de 12 et 18 matchs. Il est
  utilisable en **departage**, jamais en cle primaire.
- Le critere retenu a pour cle primaire le **cran de confiance**, qui n'est pas
  un prix. Le choix des jambes revient donc a l'application, et le produit avec
  lui : ce qui reste au modele est le seul jugement que le document annoncait
  deja.

**Consequence sur la nature du document** : il bascule de « compose » vers
« audite », et son titre change. Ce n'est pas un appauvrissement — c'est
l'alignement du document sur ce qu'il disait faire.

## C.21 — Ce qui reste ouvert et n'a pas ete instruit

- **Phase 4** — le generateur de prompts : variables injectees non utilisees,
  erreurs factuelles de bloc remontees a leur source sur trois cas, conformite du
  rendu au gabarit, budget de tokens reellement utilise.
- **Phase 5** — technique : regles metier sans test, erreurs silencieuses, index
  manquants sur `picks(session_id | event_id | created_at | result)`, exploitation
  de la distribution des 593 rejets d'ingestion.
- **B1** — la couverture des cotes, dont la mesure est cadree et non conduite.
- **D3** — ce que `framework_version` etiquette desormais, et le sort du test
  rouge qui en depend.
- **La charge de qualification** — le seul champ que le §9 ter autorise a entrer :
  score et jeux joues des tours de qualification, servis sous `singles` par un
  endpoint deja appele. Non conduit. Le rattachement, lui, est livre (migration 080)
  et n'importait aucun champ.
- **`COUPON_TRACKING` a perdu la moitie de son objet**, et ce n'est pas une
  anomalie a diagnostiquer dans six mois. Il gardait deux choses : la section G du
  gabarit et le champ « montant pose » de la feuille de session. La section est
  retiree depuis le 27/08/2026 et le champ est garde par l'existence d'une ligne
  dans `mises`, que plus rien ne cree. Le drapeau garde encore le rattachement aux
  coupons, qui fonctionne. Le module `stakes` est **en pause et non supprime** —
  son en-tete porte la chaine complete, et deux tests portent l'etat plutot que le
  laisser se deduire.
- **`/stats` repond en 7,6 s**, et ce n'est **pas** une regression : la mesure du
  26/08 la trouve identique avant et apres le lot, et c'est ce qui en fait le
  temoin de la mesure ci-dessus. C'est un probleme en soi — sept secondes sur la
  page qui porte tous les instruments de mesure, donc la page qu'on finit par ne
  plus ouvrir. Non instruit : aucun profil n'a ete fait, et rien ne dit encore si
  la cause est une requete, leur nombre, ou le calcul exact des tables de Fisher.
  A reprendre quand le chemin critique sera degage.
- **La verification du rattachement contre le tableau reel** — il est livre avant
  que le tableau principal de l'US Open soit entre, donc contre des donnees montees
  a la main. Le controle empirique se fait a l'entree du tableau, en recomptant un
  parcours de qualifie a la main ; passe cette quinzaine, il attend Melbourne.
