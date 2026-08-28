# Phase 2 — proposition unique : rendre l'echec visible a l'ingestion

Un seul lot de changements, un seul point de rupture, date a l'activation.

Ce document propose. Rien n'est applique. Les requetes qui portent chaque
chiffre sont dans `audit/sql/` : `20_faisceau.sql`, `21_domaines.sql`,
`22_coherence_bloc.sql`, `23_collage.sql`. Mesures arretees au **26/08/2026**,
sur `audit/data/audit-2026-08-26.db` (`VACUUM INTO`, `integrity_check` ok).

---

## 0. Ce qui commande ce lot

**La sante mesurable de cette application se lit sur ses intrants, pas sur ses
sorties.** Ce n'est pas une preference de conception, c'est une consequence de
deux mesures qui se repondent :

| | Ce que l'instrument detecte | Delai |
| --- | --- | --- |
| **residu au prix** (sortie) | rien : a n=281, l'effet detectable vaut ~8 points, et le residu du regime actuel vaut −1,60 pt/sel, IC [−7,36 ; +3,97] | trimestres |
| **faisceau de faits** (intrant) | des ecarts de 12 a 17 points sur 237 faits, tous au seuil | **jours** |

Le residu reste la **mesure de verite** — c'est lui qui dit si la methode vaut
quelque chose. Il n'est pas un **instrument de pilotage** a cette echelle, et le
traiter comme tel a coute trois semaines de detection sur la rupture du 21/08.

Corollaire pour la suite de l'audit : toute recommandation d'instrumentation se
juge sur son delai de detection, pas sur sa proximite avec la question de fond.

### Ce que ces grandeurs ne disent pas — et il faut l'ecrire avant le premier point

**Un faisceau qui remonte ne prouve pas que les analyses s'ameliorent.** Ces
quatre grandeurs mesurent la **matiere premiere** — combien de faits, de quel
niveau, chez quel editeur — jamais le jugement qui s'exerce dessus. Elles se
degradent quand la matiere se degrade ; elles ne montent pas quand la competence
monte.

La dissymetrie est reelle et elle vaut d'etre nommee : un faisceau qui **baisse**
est une alarme, parce qu'il retire au jugement de quoi s'exercer. Un faisceau qui
**monte** dit seulement qu'il y avait plus a lire ce jour-la — un lot de Ligue 1
en pleine saison porte plus de faits publies qu'un tour preliminaire estival, et
ca ne dit rien de qui l'a lu.

Sans cette phrase, le premier point qui remonte sera lu comme une victoire. La
seule mesure de ce que vaut le jugement reste le residu au prix, avec le delai
de detection que §0 vient de decrire.

---

## 1. Ce que la mesure a renverse dans le cadrage initial

Trois premisses du chantier ont ete dementies avant qu'une ligne soit ecrite.
Elles sont ici parce qu'elles changent la forme de ce qui est propose.

| Premisse | Ce que la mesure dit |
| --- | --- |
| une table d'attribution par domaine transpose le SKILL dans l'application | **181 domaines pour 271 faits**, 1,50 fait par domaine. Une table des domaines vus au moins deux fois couvre **47,6 %** ; au moins trois fois, **29,2 %**. La queue est la ou vivent les niveaux 1 — les sites de clubs, cites une fois chacun |
| le pick 552 aurait ete signale automatiquement | il est **conforme**. Il cite deux faits de niveau 2 (`granadacf.es`, `ultimahora.es`) plus un niveau 4 accessoire, et declare 2 : le niveau d'une selection est celui du fait qui **porte l'angle**, ni le maximum ni le minimum des faits cites |
| `confidence_floor` attraperait des crans 5 non fondes | **zero declenchement sur 211 blocs**, cran declare comme cran calcule. A 1,54 fait par bloc, aucun cran 5 n'a jamais ete emis sans ses deux editeurs distincts |
| les 134 crans forces sont un mode de defaillance actif | `ligne_absente` **s'eteint le 20/08** avec le durcissement du refus (`09e4694`). Ce qui reste est un autre defaut — voir §5 |
| la serie du faisceau se calcule depuis la session 2 | `claim_raw_json` **n'existe qu'a partir de la session 17** (18/08). La serie a **six points**, pas vingt-deux |

### Question ouverte, posee des que la serie a ete mise a l'ecran

Le test prioritaire comparait **deux blocs** de part et d'autre du 21/08 12:24Z
et sortait une rupture nette. La serie, elle, montre autre chose :

| session | 17 | 19 | 20 | 21 |
| --- | ---: | ---: | ---: | ---: |
| faits par bloc | 2,24 | 1,80 | 1,64 | **1,30** |
| part de niveau 1 | 47,4 % | 25,9 % | 18,9 % | 25,0 % |

Les quatre sessions non palies decrivent une **pente continue, pas une marche** :
`faits/bloc` decroit deja entre 17 et 19, donc **avant** la frontiere. Deux
lectures restent possibles, et quatre points ne permettent pas de les separer :

- une **degradation graduelle** que le decoupage binaire a fait passer pour une
  rupture ;
- une **rupture reelle superposee a une tendance preexistante**.

**Ne pas essayer de trancher maintenant.** Ce qui trancherait est un fait a
venir : si la pente se poursuit **apres** la restauration de la conduite de
recherche, la Skill n'etait pas la cause, ou pas la seule. C'est exactement ce
que le moniteur pourra dire dans quelques semaines et que rien d'autre ne dira —
le residu, lui, n'en saura rien avant des trimestres.

Corollaire de methode, et il vaut au-dela de ce cas : **un decoupage binaire pose
sur une frontiere choisie ne peut pas distinguer une marche d'une pente.** Il
rend un `p` qui decrit la difference des deux moyennes, et cette difference
existe aussi sous une tendance reguliere. La serie est ce qui les separe, et elle
n'existait pas quand le test a ete conduit.

---

## 2. Instrument 3 — le moniteur de faisceau

**Priorite 1.** C'est le seul des quatre qui reponde au test prioritaire, et le
seul qui n'existe sous aucune forme aujourd'hui.

### Ce qu'il publie

Quatre grandeurs, par session, sur la **section C** — la population que la page
mesure. Y melanger C-bis ferait entrer une population sans exigence de fait
date. Requete : `audit/sql/20_faisceau.sql`.

| session | depuis | blocs | faits | faits/bloc | niveau 1 | niveau 4 | pages d'operateur |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 17 | 18/08 | 17 | 38 | **2,24** | **47,4 %** | 5,3 % | 1 |
| 18 | 20/08 | 4 | 7 | 1,75 | 28,6 % | 0,0 % | 0 |
| 19 | 21/08 | 15 | 27 | 1,80 | 25,9 % | 3,7 % | 0 |
| 20 | 22/08 | 58 | 95 | 1,64 | 18,9 % | **14,7 %** | 2 |
| 21 | 22/08 | 43 | 56 | **1,30** | 25,0 % | 12,5 % | 4 |
| 22 | 24/08 | 6 | 14 | 2,33 | **7,1 %** | **28,6 %** | 2 |

> **La derniere colonne a ete recalculee le 26/08** avec la liste de refus reelle
> et non avec la sonde d'instruction. Elle valait `0 0 0 1 2 1` ; elle vaut
> `1 0 0 2 4 2`. **La session 17 — la plus ancienne, anterieure a la rupture —
> en porte une**, ce qui suffit a defaire l'observation ci-dessous.

### Ce qu'il aurait montre le 21/08

Agrege de part et d'autre de la rupture, sur les memes donnees :

| Grandeur | avant | apres | ecart | p |
| --- | ---: | ---: | ---: | ---: |
| faits par bloc | 2,00 | 1,54 | −23 % | — |
| part de niveau 1 | 37,5 % | 20,0 % | −17,5 pts | **0,0058** |
| part de niveau 1-2 | 90,3 % | 78,2 % | −12,1 pts | **0,028** |
| part de niveau 4 | 4,2 % | 15,2 % | +11,0 pts | **0,016** |

**La quatrieme grandeur ne figure pas dans ce tableau, et c'est le resultat.**
Le sondage d'instruction annoncait `0 / 72` avant contre `4 / 165` apres. Recompte
avec la liste reelle : **3 / 86 contre 9 / 185**, soit 3,5 % et 4,9 %, p = 0,76.
Il n'y a pas de rupture a montrer sur cette grandeur-la — voir §4. Elle reste au
moniteur pour ce qu'elle dira **plus tard**, pas pour ce qu'elle dit du 21/08.

### Trois regles de forme, et elles ne sont pas negociables

- **Un compte, jamais un refus.** Aucune de ces grandeurs ne peut fonder le
  rejet d'un collage. Elles decrivent un materiau, elles ne jugent pas une
  ligne.
- **Aucun seuil.** Poser un seuil sur six sessions serait du surapprentissage,
  et il declencherait sur du bruit saisonnier : intersaison, Grand Chelem et
  fenetre de mercato n'ont pas la meme densite de faits disponibles. La question
  du seuil se rouvre quand la serie aura de quoi le calibrer — et elle se
  rouvrira **avec sa mesure**, pas avec un nombre choisi.
- **La serie demarre a la session 17**, et le document le dit a l'ecran plutot
  que de laisser croire a un historique complet. Les seize premieres sessions
  n'ont pas de blocs : le gabarit ne les demandait pas.

### La limite a porter avec

A l'echelle de la session, la serie est **bruyante** : les sessions 18 et 22
portent 4 et 6 blocs. Le rendu annonce l'effectif a cote de chaque valeur, meme
regle que partout ailleurs — un taux sans son compte ne se lit pas.

---

## 3. Instrument 1 — `source_drift`

**Rendement mesure : 14 domaines, 60 faits sur 271, soit 22 %.** Aucune table,
aucun classement, aucune valeur par defaut. Requete : `audit/sql/21_domaines.sql`.

### Ce qui est decidable sans rien savoir des domaines

**Le niveau est une propriete de l'editeur** — les deux documents le disent. Un
domaine ne peut donc pas etre a la fois 1 et 4. Quatorze le sont :

| domaine | niveaux declares | faits |
| --- | --- | ---: |
| `sportsmole.co.uk` | 2 ×7, **4 ×5** | 12 |
| `mlssoccer.com` | 1 ×9, 2 ×3 | 12 |
| `atptour.com` | 1 ×10, 2 ×1 | 11 |
| `whoscored.com` | 2 ×1, 3 ×3 | 4 |
| `eurosport.fr` | 2 ×1, **4 ×2** | 3 |
| `nufc.co.uk`, `atleticodemadrid.com` | 1 ou 2, et **4** | 2 chacun |
| `arsenal.com`, `lafc.com`, `grazerak.at`, `uefa.com` | 1 et 2 | 2 chacun |

Par construction, au moins une declaration par domaine est fausse — et ca se
detecte **sans savoir laquelle**. C'est l'analogue exact de `tier_drift`, qui
expose l'ecart sans arbitrer. Le detail est instructif : quatre des quatorze
sont des **sites officiels de club**, c'est-a-dire la categorie qui produit les
niveaux 1, et celle sur laquelle le modele est le plus instable.

### Le second controle, et sa formulation juste

`Claim.rung` **lit `source_level` comme une entree** et ne le confronte jamais
aux faits cites (`confidence.py:192`). Le controle ajoute demande une seule
chose : **qu'un fait existe au niveau declare**.

Il ne peut pas demander l'accord avec le maximum des niveaux cites — ce serait
accuser a tort le pick 552, qui est conforme. Un fait faible dans le faisceau ne
contamine pas la declaration.

Rendement mesure : **2 blocs sur 211**, requete `audit/sql/22_coherence_bloc.sql`.

```
pick 353  s19  « Match nul »    source_level 2, un seul fait : niveau 4, sportytrader.com
pick 393  s20  « Southampton »  source_level 2, un seul fait : niveau 4, sportsmole.co.uk
```

Les deux ont recu `confidence_computed = 3`. Declares 4, ils tombaient au cran 2.
C'est peu, c'est juste, et ca ne coute qu'une comparaison.

### Le troisieme etat

Un domaine inconnu ne recoit **aucun niveau**. `non attribue`, et c'est la seule
reponse tenable : un defaut a 3 classerait les sites de clubs, un defaut a 1
classerait les agregateurs. Regle du projet — en cas de doute, rien.

---

## 4. Instrument 2 — la liste de refus, et elle seule

**Une liste de refus est sure la ou une liste d'admission ne l'est pas.** Son
faux negatif ne coute qu'un signal manquant ; le faux positif d'une liste
d'admission **attribue un niveau faux**, ce qui est le defaut qu'on repare.

Le cadre nomme la categorie et sa raison : *« elles vendent un operateur : leur
choix de faits sert un argumentaire, et un fait retenu pour convaincre ne vaut
pas un fait rapporte »*.

### Correction : mon premier releve sous-comptait d'un facteur trois

**A rectifier au rapport, et la cause est une regle du projet appliquee a ma
propre mesure.** Le sondage d'instruction annoncait « 4 faits, tous posterieurs
au 21/08 ». Il employait une expression reguliere batie sur les noms
d'operateurs que j'avais **devines**, et elle n'a trouve que ceux-la. Une
relecture a l'oeil des 181 domaines en rend **sept** :

| domaine | faits |
| --- | ---: |
| `betfair.es` | 4 |
| `sportytrader.com` | 3 |
| `freetips.com` | 1 |
| `footballpredictions.net` | 1 |
| `etoto.pl` | 1 |
| `extra.toto.nl` | 1 |
| `betmines.com` | 1 |
| **total** | **12 sur 271, soit 4,4 %** |

`scores24.live` a ete examine et **ecarte** : c'est un agregateur de scores qui
publie aussi des pronostics, donc un niveau 3 au sens de la table, pas une page
adossee a un operateur. Le doute se tranche vers l'exclusion — un faux negatif
de liste de refus ne coute qu'un signal.

### Ce que la correction detruit, et c'est le point

| | avant le 21/08 12:24Z | apres | p |
| --- | ---: | ---: | ---: |
| releve d'instruction (regex devinee) | **0 / 72** | 4 / 165 | 0,32 |
| **releve corrige** | **3 / 86 = 3,5 %** | **9 / 185 = 4,9 %** | **0,76** |

**L'observation est retiree, pas nuancee.** « Elles n'apparaissent qu'apres la
rupture » etait un artefact de sonde, et rien n'en subsiste : la session 17, la
plus ancienne du moniteur, en porte deja une.

**Le contraste avant/apres n'existe pas.** Des pages d'operateur etaient citees
**avant** la rupture aussi ; le zero d'origine etait un artefact de la sonde, pas
une propriete des donnees. L'observation « elles n'apparaissent qu'apres » est
retiree du rapport.

C'est la troisieme fois dans ce dossier qu'un compte faible sur un rapprochement
se revele etre un defaut d'appariement, et la premiere ou il porte sur **ma
propre mesure**. La regle vaut donc pour l'auditeur : *un compte faible sur un
rapprochement est un defaut d'appariement jusqu'a preuve du contraire, et la
preuve se fait avant de le rapporter.*

### Ce que l'instrument vaut malgre tout

**Sa valeur est prospective, pas retrospective.** Il n'existe pas pour trier ce
qui est deja entre — 12 faits, sans contraste temporel, dont la plupart sont
d'ailleurs correctement etiquetes niveau 4 par le modele. Il existe pour que ces
pages **cessent d'entrer**, et pour que leur part soit lisible dans la serie du
faisceau le jour ou elle bougera.

**Un signalement, jamais un refus, et la mesure le rend plus net qu'attendu** :
**les 12 faits sont declares niveau 4, tous les douze.** Le modele ne se trompe
jamais sur ce que sont ces pages. Ce qui se signale est donc leur **entree dans
le faisceau**, jamais leur etiquetage — et c'est precisement ce qui interdit d'en
faire un refus. Repartition : 9 en section C, 3 en C-bis.

**La liste est curee, et son incompletude est assumee** — c'est ce qui la
distingue d'une liste d'admission. Elle porte des marques d'operateurs et de
pronostiqueurs, comparees au **label** du domaine et jamais en sous-chaine :
`betterrugby.com` ne doit pas se faire prendre pour un operateur parce qu'il
contient « bet ».

## 5. Instrument 4 — l'accuse d'appariement

Directive 3, reorientee sur le defaut reel. Requete : `audit/sql/23_collage.sql`.

### Le defaut vise est eteint

| jour | cause | picks |
| --- | --- | ---: |
| 14 → 20/08 | `ligne_absente`, `cause_inconnue` | 150 |
| 21 → 23/08 | — | **0** |
| 24/08 (s22) | `reperes_non_resolus` | 9 |
| 25/08 (s23) | `reperes_non_resolus` | 9 |

Le durcissement du 20/08 (`09e4694`, « Refuser un collage sans
`dossiers_ouverts`, au lieu de l'avertir une vingt-et-unieme fois ») fonctionne.
Plus un seul collage partiel depuis.

### Ce qui reste, et pourquoi un marqueur de format n'y peut rien

Les dix-huit restants sont des collages **complets** :

```
collage 68  s22  24/08 13:32  9 blocs « faits »,  dossiers_ouverts = oui
collage 69  s22  24/08 13:42  8 blocs « faits »,  dossiers_ouverts = oui
collage 70  s23  25/08 08:13  9 blocs « faits »,  dossiers_ouverts = oui
```

Deux causes, deux traitements :

- **Session 23 — aucun prompt n'existe** (0 prompt, 1 collage, 9 selections).
  `PromptBlocks` n'a rien contre quoi apparier : le referent manque, pas
  l'annonce. **Le collage est refuse**, avec son motif — c'est la seule issue,
  et elle rend le cas reparable en generant le prompt puis en recollant.
- **Session 22 — l'appariement echoue malgre cinq prompts**, dont deux a 9 blocs
  comme le collage. Le compte concorde ; c'est la somme de controle sur
  l'affiche qui ne tombe pas. **L'echec nomme sa cause** — compte de blocs
  different, ou affiche qui ne tombe pas, avec les deux libelles cote a cote.

### Ce qui n'est pas propose

**Un compte annonce en tete de rendu.** La mesure dit qu'il **n'aurait sauve
aucun des dix-huit** : les trois collages annoncent deja correctement leur
contenu. Il ajouterait une ligne a produire, donc une ligne a oublier.

---

## 6. Decisions 1 a 3

### Decision 1 — la conduite de la recherche revient, reecrite en intention

**Verification faite** : les parametres des trois formes de requete —
`includeDomains`, `tbs: "qdr:d"`, `sources: [{type:"news"}]`, `location:
"France"` — sont ceux de l'outil **Firecrawl**. En interface chat, la recherche
web n'en expose aucun. La contrainte des six mots est empirique a Firecrawl et
n'a jamais ete mesuree ailleurs.

Colle tel quel, le bloc donnerait **l'illusion d'une procedure**. Ce qui est
**regle** est garde mot pour mot ; ce qui est **syntaxe** est traduit en
intention.

**Livre le 26/08/2026.** Le texte vit desormais dans
`templates/prompts/session_default.md.j2` et **pas ici** : une seconde copie
qu'aucun mecanisme n'oblige a concorder derive, et celle-ci aurait derive au
premier ajustement de formulation. Ce qui reste ici est ce qu'un gabarit ne peut
pas porter — les trois corrections apportees avant l'insertion, et leur raison.

**1. Le cas majoritaire n'etait pas couvert.** 181 domaines pour 271 faits, neuf
editeurs sur quatorze a un fait par niveau : la queue de la distribution est le
regime ordinaire, pas l'exception. Sans regle, un domaine inconnu se range dans
la case la plus proche, et `source_drift` remonte ensuite un conflit qui n'est
qu'un rangement arbitraire. Le gabarit dit donc qu'un domaine hors des quatre
rangs **n'est pas attribue par defaut** — on declare le niveau qu'on peut
justifier, et rien de plus.

**2. Le tableau d'attribution propose contredisait l'echelle deja servie**, et
c'est le motif du §8 sous sa forme la plus banale : le gabarit classait
« agregateurs » en niveau 4, le texte propose classait « agregateur de compos a
format constant » en niveau 3. Deux tables cote a cote, la seconde ecrite sans
relire la premiere. **Une seule echelle subsiste** — celle qui existait — et la
distinction canal/fait s'y greffe : un agregateur qui relaie une composition
officielle est un niveau 3, un site de pronostics reste un niveau 4 parce qu'il
vend un operateur. Le tableau des trois objectifs, lui, ne classe rien : il dit
ou chercher, et renvoie a l'echelle unique.

**3. La regle de plafond etait fausse dans les deux sens.** « Sans recherche,
rien n'entre au tableau principal au-dessus du cran 2 » sous-estimait la
contrainte et melangeait deux cas. Verifie contre `confidence.Claim.rung` et le
controle 8 : sans recherche, `reading_only` rend **1** ; une recherche qui ne
ramene que du niveau 3-4 plafonne a **2** ; et le tableau principal **refuse le
cran 2**, qui part en C-bis. L'enonce exact est donc plus fort que celui
propose — *sans un fait de niveau 1 ou 2, rien n'entre au tableau principal*.

**Le controle « pas de selection sur H2H seul » n'avait pas de liste d'accueil.**
Les dix controles du cadre vivent dans la Skill, desactivee ; le gabarit n'en
porte pas d'enumeration. Il est donc pose en **section B**, ou l'angle se choisit,
qui est l'endroit ou il mord. La regle de conversion des dates, elle, est dans le
bloc de recherche avec le reste de ce qui gouverne une requete.

**Cout mesure** : **+1 121 tokens** sur un lot reel de onze blocs de football,
soit +4,4 %. Le paragraphe de vigilance tennis et la mention des forfaits dans la
ligne de cadrage sont gardes par le sport du lot.

### Decision 2 — la section G est supprimee

Jamais produite (`mises:` vaut 0 sur les 70 collages, table `mises` vide),
contredite par le cadre, **592 tokens payes a chaque prompt**. La mention de
bankroll en tete part avec (41 tokens).

**Reformulation de la conclusion de la phase 1**, a reporter au rapport final :
la phrase « §9 tient » est imprecise. Ce qui est etabli sans reserve est
**aucun calcul d'esperance n'existe** — pas de devig, pas de Kelly, pas d'edge,
pas de CLV, pas de probabilite implicite, et le residu est bati sur `1/cote`
brut. La lettre de §9 enumere huit termes dont le dernier, **« mise
conseillee »**, n'est pas une quantite derivee d'une probabilite mais un
livrable : la section G en produisait un. Ce qui la defendait etait la
**signature** de `stakes.plan()` — ni cote, ni palier, ni cran, ni historique —
donc l'absence de modele, qui est la raison enoncee par §9 juste apres la liste.
La suppression ferme la question sans avoir a trancher la lettre.

### Decision 3 — le « 79 % » est retire

`_selection_median` (`history.py:6490`) compte `COUNT(DISTINCT event_id)` sur
**toutes** les selections d'une session, **C-bis comprise**, rapporte a la taille
du lot. Median sur les huit sessions dotees d'un lot :

| | mediane |
| --- | ---: |
| avec C-bis — ce que le prompt transmet | **79 %** |
| section C seule | **52 %** |

Le prompt l'ecrit « Part du lot que je selectionne ». C'est un **taux de
couverture du lot**, faux de 27 points par rapport a ce qu'un lecteur comprendra,
et c'est le **seul pourcentage transmis au modele** — donc un ancrage sur la
quantite a selectionner, pose avant l'analyse.

La grandeur part. La consigne reste, telle quelle :

> Un lot ou tu selectionnes tout, ou presque rien, s'explique en une ligne.

---

## 7. Bilan de tokens

| | tokens |
| --- | ---: |
| − section G | **−592** |
| − mention bankroll en tete | −41 |
| − la grandeur « 79 % » | −44 |
| + conduite de la recherche (texte SKILL brut ; la version en intention sera plus courte) | +899 |
| + controles 3 et 11, vigilance tennis | +286 |
| **net** | **+508** |

Sur un cadre de **15 283 tokens** (prompt 217), soit +3,3 %. **Le total n'est pas
le critere** : la section G coutait 592 tokens pour une sortie jamais produite,
et c'est la densite de ce que le cadre transporte qui decide, pas son volume.

---

## 8. Pistes explicitement fermees

Ecrites ici sous la forme qui empeche de les refaire — un resultat negatif non
ecrit sera refait.

### Le motif recurrent : deux copies qu'aucun mecanisme n'oblige a concorder

**Ce n'est pas un incident isole, c'est la forme que prennent les defauts ici**,
et ce lot en a rencontre deux de plus le jour meme de sa livraison :

| Les deux copies | Ce qui les separait | Comment ca s'est vu |
| --- | --- | --- |
| le cadre publie et la table `tiers` | plafond SAFE, chevauchement, arbitrage | par une relecture, apres des semaines — 35 selections mal classees |
| le cadre publie et `FRAMEWORK_VERSION` | 1.4 contre 1.3 | par le garde `myassistantbet-cadre`, deux jours apres |
| `Claim.rung` et le niveau declare | `source_level` lu comme une entree, jamais confronte aux faits | par cet audit, §3 |
| **un second `json.loads` dans `history`** | rien encore — il venait d'etre ecrit | **avant le commit**, en relisant ce que `confidence.parse` fait deja |
| la borne du lot et le bloc qu'elle nomme | l'**orthographe** de l'issue : `No` contre `Non`, `Over 1.5` contre `O1.5`, un nom soude contre le meme espace | par une relecture du rendu, 31 % des lignes de bornes archivees |

Le chantier `source_drift` en a produit une cinquieme le meme jour, et elle n'a
rien coute pour la meme raison : compter les editeurs par un rapprochement de
domaines ecrit sur place, quand `Fact.source` tranche deja cette question pour
compter les facteurs independants. Un agregateur qui relaie un communique de club
se serait compte sous `onefootball.com` d'un cote et sous `arsenal.com` de
l'autre — **et l'ecart n'aurait jamais fait echouer un test**, les deux lectures
etant justes chacune de son cote.

### Un garde qui ne mord pas se manifeste toujours de la meme facon : rien

**Troisieme forme du motif en une semaine, et c'est ce qui en fait une famille.**
Un controle qui n'ecarte jamais rien produit exactement la meme sortie qu'un
controle qui n'a rien a ecarter : la suite est verte, la surface est normale, et
la seule trace est une absence.

| | pourquoi | ce qui l'aurait montre |
| --- | --- | --- |
| `confidence_floor` | la population ne le porte pas | son **taux de declenchement** |
| `HiddenEvent.priced` | la regle qui le produit le contredit | **rien** |
| `recap._family` | il lit la mauvaise cle | **la sortie**, ligne a ligne |

**Les trois se ressemblent a l'execution et n'ont rien en commun a la cause**,
donc il n'existe pas un seul geste qui les attrape. Ce qui les rassemble est la
question a poser : *ai-je vu ce garde ecarter quelque chose ?* Elle est plus
severe que « le test passe-t-il », et c'est la seule qui separe un garde qui
protege d'un garde qui decore.

- **Un banc qui verifie qu'une sortie existe ne verifie pas qu'un garde a
  mordu.** Ceux de la contrainte de familles montaient une proposition et la
  voyaient sortir ; elle sortait, avec deux jambes de la meme famille. Le banc
  juste monte un vivier ou le garde **doit** ecarter, et verifie l'ecart.
- **Un taux de declenchement se mesure comme un taux de couverture.** Le dossier
  en fait deja la regle pour les criteres de la fiche de recherche — « un critere
  qui se declenche sur un quart des blocs ne classe plus rien » — et le miroir
  est vrai : un critere qui ne se declenche jamais ne garde rien.

### La lecture du rendu reel est un moyen de detection, et parfois le seul

**Trois defauts du lot du 29/08/2026 ont ete trouves en regardant la sortie**, pas
en interrogeant le code : la contamination de l'enchainement, le vivier jamais
determine, et la famille de marche silencieusement inoperante. Aucun n'aurait
leve, aucun n'aurait fait rougir un banc.

La raison est structurelle : le recapitulatif n'a **aucun banc de bout en bout sur
donnees reelles**. Ses bancs montent des fixtures, et une fixture porte l'etat
qu'on lui a donne — c'est la regle du 27/08 sur le critere valide contre le
corpus, appliquee a un rendu entier plutot qu'a un seuil.

- **Ce n'est pas un appel a ecrire ce banc-la.** Un banc sur la base servie
  echouerait le jour ou la journee change, et le dossier a deja tranche : une
  assertion qui recopie la sortie du jour decrit la fixture au lieu de contraindre
  la regle.
- Ce qui en sort est une **etape**, pas un test : apres tout chantier sur une
  surface de rendu, lire un rendu reel en entier. Le cout est de deux minutes, et
  il a rapporte trois defauts dont deux structurels.

### Une fonction renommee laisse son ancien nom dans la prose, et la prose ne compile pas

**Regularite, et non incident : quatrieme occurrence relevee le 28/08/2026.** Le
code se renomme d'un geste — l'outil suit les appelants, et un appel reste
correct ou echoue tout de suite. La prose qui nomme la fonction n'a aucun de ces
deux comportements : elle reste, elle reste **plausible**, et elle designe
quelque chose qui n'existe plus.

| Ecrit | Ce qui existait |
| --- | --- |
| `coupons.attach()` est le seul ecrivain de `picks.played` — `CLAUDE.md` | `coupons.create()` |
| meme phrase — en-tete de `combos.py` | idem |
| meme phrase — chapitre du combine, `CLAUDE.md` | idem |
| `stakes.set_played` ecrit `picks.played` — deduit du **nom** | elle ecrit `mises.montant_joue` |

Les trois premieres sont une seule affirmation recopiee ; la quatrieme est pire
et vient de la meme famille : un lecteur — moi, en instruisant ce chantier — a
conclu du **nom** ce qu'il fallait lire dans le corps. Le docstring, lui, disait
juste : « l'ecriture va dans `mises` et jamais dans `picks` ».

- **Le degat n'est pas l'inexactitude, c'est la confiance.** Une prose qui nomme
  une fonction se lit comme verifiable, alors qu'elle n'est verifiee par rien. La
  quatrieme occurrence a failli faire refuser un decoupage juste — « `stakes` et
  les coupons sont plus enchevetres que ca » — sur un nom.
- **Ce qui l'attrape est le recensement de source**, pas la relecture :
  `write_paths.writing_functions(('picks',), updates=True)` rend la liste des
  ecrivains reels en une seconde, et elle ne contient pas `stakes.set_played`.
  L'outil existait deja, rendu parametrable le 27/08 pour une autre question.
- **La regle qui en sort** : une affirmation de prose qui nomme une fonction se
  verifie **par l'enumeration**, jamais par la lecture du nom ni par le souvenir
  de l'avoir ecrite. Et apres tout renommage, `grep` sur l'ancien nom — meme
  corollaire que « compter les copies avant de declarer la correction faite »,
  applique aux identifiants plutot qu'aux chiffres.

### La regle d'ecriture, et c'est une regle et non un constat

**Avant d'ecrire une seconde lecture d'une donnee deja lue ailleurs, nommer le
mecanisme qui les oblige a concorder.** S'il n'y en a pas, reutiliser le lecteur
existant.

Le motif a cinq occurrences dans ce dossier. **Les deux qui n'ont rien coute sont
les deux ou la question a ete posee a l'ecriture** — pas a la relecture, pas au
test, pas trois semaines plus tard. Les trois autres ont ete trouvees apres coup,
et l'une d'elles a mal classe 35 selections.

Le corollaire est celui de la troisieme branche des « a ne pas oublier » : il y a
trois reponses a la question, pas deux.

1. **Une seule ecriture, les autres l'appellent.** Le cas de
   `session.context_block`, de `markets.py`, et des deux corrections de ce lot.
2. **Un test qui lit les deux sources.** Quand un cycle d'import ou une frontiere
   de couche interdit la premiere.
3. **Rien ne les oblige.** Alors la copie derivera, et la seule question est
   quand — c'est le cas du cadre publie et de la configuration servie, et c'est
   pourquoi le journal d'analyse a cesse de dependre de l'emoji colle.

### Une fixture dont le vocabulaire est invente garde une propriete qui n'existe pas

**Troisieme forme du montage aveugle, et elle precede les deux autres.** Les
deux premieres portent sur ce que la fixture *atteint* : trop riche, elle
atteint l'etat garde par plusieurs chemins et survit a la disparition du bon ;
trop pauvre, elle ne l'atteint jamais et le banc est vert pour une raison qui
n'est pas la sienne. Celle-ci porte sur ce que la fixture **est** : ses valeurs
n'existent nulle part en production.

Le cas, du 29/08/2026 : les bancs du score exact nommaient leurs issues `1-1`,
`2-1`. **Aucun fournisseur ne produit cette notation** — zero ligne sur les
12 725 de la base, qui n'en connait que deux, `1:0` et `Cruzeiro:0|Mirassol:0`.
Les bancs parcouraient la bonne fonction et traversaient le
`outcome.name.replace(' ', '')` fautif ; il n'avait simplement rien a faire sur
des noms sans espaces. Le defaut a vecu du 09/08 au 29/08, sur 188 blocs.

- **Elle ne se corrige pas en enrichissant le montage**, contrairement aux deux
  autres, mais en **allant lire ce que la source ecrit vraiment** — une requete
  sur `odds`, trente secondes.
- **Le symptome est l'absence de symptome** : un banc au vocabulaire invente est
  vert, stable, et lisible. Rien ne le distingue d'un banc juste, sauf d'aller
  comparer ses valeurs a la base.
- La question a se poser sur un montage en gagne donc une, et elle passe en
  premier : *ces valeurs, la production les produit-elle ?*

### La copie textuelle est la plus facile a laisser vieillir, parce qu'elle n'echoue jamais

**Trois occurrences dans la meme journee, le 27/08/2026, et les trois sur de la
prose ou de l'affichage plutot que sur du calcul.**

| Les deux copies | Ce qui les separait | Comment ca s'est vu |
| --- | --- | --- |
| `note_price_coverage` et `scan.py` | « seul moment ou l'etat peut avoir change », dementi par un import | en corrigeant l'une des deux |
| `CLAUDE.md` et la base | « un seul cas » contre **dix-huit** sur une seule edition | en comptant les joueurs |
| le message de demarrage et les taches posees | il a omis le balayage **le jour de son arrivee** | en lisant les logs du deploiement |

**Aucune n'aurait jamais fait echouer un test**, et c'est la difference avec les
copies de code. Une valeur dupliquee finit par diverger sur un cas que quelqu'un
execute ; une phrase dupliquee ne s'execute pas — elle se lit, six mois plus
tard, avec l'air d'etre fondee, et elle sert alors a justifier un geste.
L'affirmation du docstring aurait justifie de **retirer** l'appel qu'on venait
d'ajouter ; « un seul cas » autorisait a ne pas chercher ; le message de
demarrage aurait menti pendant des mois sur ce que l'application fait toute seule.

> **La regle** : une phrase qui affirme un etat du systeme est une copie de cet
> etat. Ou elle se **derive** de lui — le message de demarrage enumere desormais
> les taches posees plutot que de les reciter — ou elle doit etre listee au meme
> titre qu'un chiffre publie a quatre endroits. Corriger le code sans relire la
> phrase laisse une affirmation fausse a l'endroit exact ou l'on vient de gagner
> en justesse.

Le corollaire de forme, quand la derivation est possible : **supprimer la copie
plutot que la corriger**. C'est ce qui separe le message de demarrage des deux
autres — les deux docstrings ont ete reecrits, celui-la a cesse d'exister.

#### Et il en existe une seconde espece : l'affirmation qui empeche de regarder

**Distinguee le 28/08/2026, sur la sixieme occurrence, parce qu'elle ne se
repare pas comme les cinq precedentes.**

Les cinq premieres sont des **copies qui divergent** : deux ecritures d'un meme
etat, justes au depart, separees par un changement qui n'a touche que l'une des
deux. Le remede est celui ci-dessus — deriver, ou lister.

La sixieme n'a pas de seconde ecriture. `db.scratch_copy()` annoncait une copie
« **jetable** » et **rien n'a jamais jete** : la phrase n'a pas vieilli, elle
etait fausse le premier jour. Ce n'est pas une copie qui derive, c'est une
affirmation qui **remplace la verification**.

- **Le degat n'est pas qu'elle soit fausse, c'est qu'elle rassure.** Sans le mot
  « jetable », quelqu'un aurait fini par se demander qui nettoie ; avec lui, on
  lit, on croit, et l'etape de verification saute entierement. Elle a tenu une
  semaine, et ce qui l'a fait tomber n'est pas une relecture mais un tmpfs
  sature.
- **C'est le defaut caracteristique du projet applique a la prose** : la phrase
  rend la meme sortie — un lecteur rassure — que le comportement soit la ou non.
- **Elle ne se derive pas**, et c'est ce qui la separe des cinq autres : il n'y a
  aucun etat dont la deriver, seulement un comportement a ecrire. Son remede est
  donc un **test**, jamais une reformulation.

> **La question a se poser en ecrivant un docstring** : *cette phrase decrit-elle
> ce que le code fait, ou ce que j'ai l'intention qu'il fasse ?* La seconde
> s'ecrit au futur ou ne s'ecrit pas. Un adjectif qui promet un comportement —
> « jetable », « idempotent », « borne », « thread-safe » — est une assertion
> deguisee, et elle se pose dans le banc avant de se poser dans la prose.

### Un controle de doublon ne prouve rien sur l'entite qu'on croit compter

**Huitieme occurrence, le 27/08/2026, et la premiere trouvee sur un controle que
je venais d'ecrire.** Apres avoir rattrape deux journees de qualification, j'ai
verifie l'absence de doublons sur deux criteres — la cle naturelle
`(competition_id, tennisapi_fixture_id)` et le couple `(jour, affiche)`. Les deux
sont sortis a **zero**. **Les deux etaient vrais, et la propriete qu'ils devaient
etablir etait fausse** : dix-huit rencontres etaient en double.

Le fournisseur publie une entree provisoire a une heure de remplissage puis la
rencontre definitive, avec son heure reelle et un **identifiant nouveau**. Les
deux criteres passaient : les identifiants different, et le jour comme le sens de
l'affiche aussi.

> **La regle** : un controle de doublon pose sur la cle qu'on **maitrise** ne
> prouve rien sur l'entite qu'on croit compter. La cle du fournisseur identifie
> une **publication** ; l'entite cherchee etait une **rencontre**, et rien dans le
> resultat du controle ne dit laquelle des deux il a comptee.

**Le corollaire est ce qui la rend executable** : le compte des rencontres passe
par le **lecteur qui les resout** — `tournament_day` puis `_resolve_duplicates` —
jamais par un `COUNT(*)` sur `events`. Un `COUNT(*)` compte des lignes ; la
question portait sur des matchs.

**Et c'est le lecteur robuste qui a rendu le defaut invisible.**
`_resolve_duplicates` faisait exactement son travail — il garde la ligne la plus
recemment creee et nomme l'autre `replaced` — donc la sortie etait juste et rien
ne depassait. Un lecteur plus faible aurait rendu quatre tours a un qualifie qui
en a joue trois, et le defaut se serait vu le jour meme. C'est la forme la plus
couteuse du motif du dossier : la correction en aval masque l'erreur en amont.

### Un controle sur la valeur ne voit pas un defaut sur l'emplacement

**Neuvieme occurrence, le 28/08/2026, et c'est la variante de C.24 :** deux
mecanismes rendent la meme sortie visible, et rien dans la sortie ne dit lequel
a joue.

`TierScope.range_line` annonce une borne **et l'endroit ou elle se lit** —
`501.00 (M3 · Score ex. MT 1:5)` — et c'est sa raison d'etre documentee : une
borne sans son emplacement oblige a relire tous les blocs, et personne ne le
fait. Elle porte donc deux affirmations, une valeur et une adresse, sous une
seule ligne.

Mesure sur les 142 lots archives : **66 lots annoncent une borne basse absente du
bloc qu'elle nomme, et 61 seulement voient leur valeur changer**. Les cinq
autres annoncaient **la bonne valeur a la mauvaise adresse** — une cote de meme
prix existait ailleurs dans le lot, tronquee ici et rendue la.

> **La regle** : quand une sortie affirme une valeur *et* un emplacement, le
> controle porte sur le couple. Verifier la seule valeur declare sains
> exactement les cas ou l'adresse ment — et l'adresse est la moitie qui coute,
> puisque c'est elle qui envoie chercher.

**Le corollaire de mesure est le meme que celui du controle de doublon** : le
critere juste n'est pas « la borne est-elle la plus haute » mais « la borne
figure-t-elle dans le bloc qu'elle designe ». Le premier est calculable sans
regarder le rendu, et c'est ce qui le rend tentant ; il ne repond pas a la
question posee.

### Le zero d'un controle de parseur vaut autant que le resultat qu'il encadre

**Corollaire du meme rejeu, et il vaut d'etre pose separement.** Le rejeu
comparait, bloc par bloc, les paliers annonces aux paliers que les cotes rendues
atteignent. Il rendait deux comptes et non un : les paliers **annonces sans etre
rendus** — le defaut cherche — et les paliers **rendus sans etre annonces**, qui
ne peuvent pas exister si la lecture est juste, le rendu etant par construction
un sous-ensemble de ce que le code lisait.

Le second est sorti a **zero sur 1 194 blocs**, et c'est lui qui autorise a lire
le premier. Sans ce controle, un parseur qui inventerait des prix rendrait les
deux comptes non nuls, et rien ne dirait si les 145 blocs designes sont un
defaut du gabarit ou un defaut de lecture.

> **La regle** : un rejeu sur corpus rend la mesure **et sa contre-mesure** —
> celle dont la valeur est connue d'avance. Un rejeu qui ne rend que son
> resultat ne peut pas distinguer un defaut du systeme d'un defaut de son
> instrument.

Il a servi une seconde fois dans le meme rejeu : la contre-mesure sortait a 43,
et non a zero, tant que les bandes de cote d'aujourd'hui etaient appliquees a
des prompts anterieurs a la migration 071. Ce sont les 43 faux positifs que la
regle des points de rupture evite — et c'est le controle, non la vigilance, qui
les a nommes.

### Le motif jumeau : une propriete qui ne peut pas etre fausse

**Deux proprietes ont ete retirees dans ce lot, et pour la meme raison.** Toutes
deux encodaient un etat que le modele ne peut pas atteindre.

| La propriete | Le cas qu'elle voulait nommer | Ce qu'il etait |
| --- | --- | --- |
| `confidence_floor` | un cran 5 declare sans ses deux editeurs distincts | **0 sur 211 blocs**, declare comme calcule |
| `HiddenEvent.priced` | une rencontre masquee qui porte deja un prix | **contradictoire avec la regle qui la produit** : elle opere par competition, donc une seule cote ramene la competition entiere, avec toutes ses rencontres |

**Les deux ne se ferment pas au meme titre, et la difference decide de ce qu'on
en retient.** Le premier est **vide a la mesure** — rien n'interdit qu'il se
remplisse un jour, et c'est un resultat sur la population. Le second est
**impossible par construction** : aucun volume de donnees ne l'aurait rempli, et
aucune mesure ne l'aurait dit.

**C'est un test qui l'a montre, pas une relecture** — en echouant sur un
`KeyError`, la liste etant vide a l'endroit exact ou le drapeau devait
s'afficher. Le code se relisait sans surprise : la propriete rendait faux sur le
cas ordinaire comme sur le cas qu'elle etait censee attraper, donc la surface
avait l'air normale et le garde l'air pose. Signature du defaut caracteristique
du projet, transposee d'une donnee a un garde-fou.

> **La regle d'ecriture, symetrique de celle qui precede** : avant d'ajouter une
> propriete, **nommer le cas ou elle est vraie et le cas ou elle est fausse**. Si
> l'un des deux ne se construit pas, elle n'a pas d'objet.

Les deux regles posent la meme question a la meme minute — a l'ecriture, pas a
la relecture ni au test. L'une demande *qu'est-ce qui oblige ces deux lectures a
concorder*, l'autre *a quoi ressemblent mes deux cas*. Et le cout d'un garde qui
ne peut pas mordre n'est pas sa maintenance : c'est l'assurance qu'il donne.

### Une sortie dit ce que le lecteur peut en faire, rien de plus

**Principe tire de trois decisions du meme lot, qui semblaient sans rapport.**

| Sortie | Ce qu'elle rend | Pourquoi |
| --- | --- | --- |
| competitions sans contexte | le **nom**, sans compte | elles attendent un geste, et le nom dit lequel saisir |
| competitions sans prix | le nom **et le compte** | elles retirent des lignes, et le compte dit ce qui disparait |
| conflits d'editeur | les **comptes bruts**, sans dominant | on ne sait pas laquelle des deux declarations est juste |

Dans les trois cas la donnee sous-jacente permettait d'en rendre plus. Ce qui a
tranche n'est pas ce qu'on **pouvait** afficher mais **l'action attendue du
lecteur** : un compte se lit comme une file d'attente, un nom comme une tache, un
niveau dominant comme un verdict. C'est le meme raisonnement qui a fait retirer
`dominant` du modele et pas seulement du libelle.

**La question a se poser devant toute sortie** : *qu'est-ce que le lecteur fera de
ceci ?* Ce qui ne sert a aucune action se retire, meme quand c'est calculable — et
surtout quand c'est calculable, parce que la disponibilite se prend facilement
pour une raison.

### La regle vaut pour les documents, et l'audit l'a apprise sur lui-meme

**Sixieme occurrence, produite par la correction d'une cinquieme.** Le releve de
la liste de refus a ete corrige dans le code, dans le docstring et dans le message
de commit — **et pas dans le document**, qui a porte le chiffre faux a deux
endroits de plus jusqu'a ce qu'une relecture le demande.

C'est la plus utile des occurrences, parce qu'elle montre ce dont la regle ne
protege pas : **sa propre version paresseuse.** Corriger « la » copie n'a de sens
que si l'on sait combien il y en a, et un chiffre publie a quatre endroits est un
chiffre a **quatre copies** — code, docstring, surface, document — qu'aucun
mecanisme n'oblige a concorder.

Corollaire operationnel : apres toute correction de mesure, **compter les copies
avant de declarer la correction faite**. `grep` sur le chiffre, pas sur le
souvenir de l'avoir ecrit.

### Deriver le niveau d'un domaine par rapprochement de noms — **ferme, mesure le 26/08/2026**

Piste evidente pour couvrir la queue des 181 domaines : les sites de clubs se
reconnaitraient en rapprochant la racine du domaine des noms d'equipes deja en
base (`events.home/away`). Mesure sur un vocabulaire de 2 432 formes :
**67 domaines sur 181, soit 33,9 % des faits** — et les faux positifs sautent
aux yeux :

```
sportsmole.co.uk     ~ « sport »        eurosport.fr        ~ « sport »
sports.orange.fr     ~ « sport »        revolutionsoccer.net ~ « revolution »
```

C'est le meme piege que le rapprochement automatique des ligues, essaye et
rejete : la Championship ecossaise pour l'anglaise, la Bundesliga pour la
2. Bundesliga, la Coupe de Malaisie pour la MLS — **le tout avec un score
maximal**. Rien ne se deduit d'un libelle. La couverture serait d'un tiers, et
elle attribuerait des niveaux faux sur les domaines de presse les plus cites.

Ce qui rouvrirait la question, et rien d'autre : une source structuree qui
associe un club a son domaine officiel, verifiable et gageable.

### Une table d'attribution curee — **fermee par la distribution**

39 domaines a tenir pour 47,6 % des faits, 14 pour 29,2 %. La maintenance croit
avec le temps, la couverture non — chaque session apporte de nouveaux domaines
de club vus une seule fois.

### `confidence_floor` — **ferme, zero declenchement**

Cran 5 declare avec moins de deux editeurs distincts : **0 sur 211**. Cran 5
calcule : **0 sur 211**. Un garde qui ne peut pas mordre donne l'apparence d'un
garde, coute de la maintenance et achete une fausse assurance.

### `source_level_computed` — **remplace par `source_drift`**

Pas de niveau calcule, faute de table possible. L'incoherence est exposee, elle
n'est pas corrigee.

### Le compte annonce en tete de rendu — **ferme, zero des dix-huit sauves**

---

## 9. Ordre de livraison, tests avant correctifs

Un seul lot, un seul point de rupture. L'ordre interne est celui du rendement.

| # | Chantier | Test ecrit **avant** |
| ---: | --- | --- |
| 1 | moniteur de faisceau, serie retrospective depuis la session 17 | un lot monte a deux sessions de densites differentes rend deux lignes distinctes ; une session sans bloc n'y figure pas et le dit |
| 2 | `source_drift` — incoherence de domaine, et niveau non porte par les faits | un domaine declare 1 puis 4 sort en incoherence ; **le pick 552 ne sort pas** — deux niveaux 2 et un niveau 4, declaration 2, conforme |
| 3 | liste de refus | un fait de `betfair.es` est signale et **n'est pas refuse** ; le bloc s'importe |
| 4 | accuse d'appariement | un collage dans une session sans prompt est refuse avec son motif ; un appariement qui echoue nomme sa cause et les deux libelles |
| 5 | retrait de la section G, de la bankroll, du « 79 % » | le prompt rendu ne porte plus `mises:` ni de pourcentage ; **la consigne « un lot ou tu selectionnes tout… » y est toujours** |
| 6 | retour de la conduite de la recherche, controles 3 et 11, vigilance tennis | les six regles gardees mot pour mot sont dans le rendu ; **aucun parametre Firecrawl n'y figure** |

### L'etat de la suite a la livraison — un echec connu, date, et volontaire

**`tests/test_sentinelles.py::test_le_numero_de_cadre_s_appuie_sur_une_lecture_et_non_sur_une_declaration`
echoue, et ce n'est pas une regression.**

| | |
| --- | --- |
| depuis | le **22/08/2026 15:23**, date de publication du cadre 1.4 |
| ce qu'il dit | cadre publie **1.4**, `FRAMEWORK_VERSION` **1.3** |
| verifie le | 26/08/2026, sur l'arbre **remise** — il echoue sans aucun changement de ce lot |
| pourquoi il reste rouge | sa resolution depend de **D3**, qui attend le lot complet ; le module ne releve pas le numero a la place de qui exploite |

**Il doit etre porte au rapport final comme tel.** Dans trois mois, un test rouge
sans provenance sera traite comme un bug ou desactive — et c'est le troisieme
chemin vers une carte fausse, apres les deux copies qui divergent et la valeur
limite lue comme une absence. Le garde fait exactement son travail : il refuse de
se taire sur une divergence qu'il ne peut pas corriger seul.

Regle de test du projet, applicable a chacun : une assertion enonce ce qui doit
etre vrai, jamais ce qui est sorti. Le test 5 se pose sur le **prompt rendu** et
non sur le service — le defaut du lot 19 vivait dans la porte, pas dans le
calcul.

---

## 9 bis. Le critere d'admission d'un champ importe

**Ecrit avant le chantier d'enrichissement, et pas pendant.** Un critere pose
apres avoir vu ce que l'API offre se calibre sur l'offre ; pose avant, il se
calibre sur le besoin.

### Le referent n'est pas la conduite de la recherche

Elle dit **ou** chercher et **a quel niveau de source** — que le site de
l'instance est un niveau 1. Elle ne dit pas **quels faits comptent** : elle ne
tranchera jamais si le style de jeu d'un joueur est exploitable.

Le referent est la **section B** du gabarit : « l'angle sportif dominant, sa
nature en un mot — issue ou maniere — puis le marche qui la traduit ».

> **Le test, et il est concret** : pour chaque champ envisage, ecrire la phrase de
> section B qu'il permettrait. **Si la phrase ne vient pas, le champ ne rentre
> pas.**

Un champ vaut l'import s'il peut **porter un angle ou l'invalider**. Sinon c'est
du contexte decoratif, qui coute des tokens dans chaque bloc et ajoute une surface
d'erreur factuelle — les erreurs de bloc de la section F viennent deja de la.

### Un champ de niveau 4 n'a pas sa place dans le bloc

**Critere que la methode ne fournit pas, et qu'il faut poser explicitement.** Les
12 faits de la liste de refus sont entres parce qu'ils ont ete **cherches** ; un
champ d'API importe systematiquement entre **sans avoir ete cherche du tout**, sur
chaque bloc et sans decision.

Si un fournisseur sert des donnees qui relevent de la categorie « ecarter » —
pronostics, cotes agregees, formes calculees par un tiers — elles ne figurent pas
au bloc, **quelle que soit leur commodite**.

### La forme admissible d'un agregat, et c'est un critere et non un verdict

**Ecrit apres le §9 ter, qui a renverse le verdict par famille.** Le premier jet
disait « un agregat de saison est un fait sans date, donc refuse ». La regle
condamnait ce qui est **deja en production** : `Service`, `Retour`, `Jeux` et
`Ecart` sont des agregats de 52 semaines, ils sont livres, et ils tiennent.

Ce qui les rend lisibles n'est pas leur nature, c'est leur **forme de rendu**.
Trois conditions, et il faut les trois :

1. **la fenetre et les denominateurs sont ecrits** — `52 sem., 2 083 pts de
   service, arretees au 16/08`. Un agregat qui porte ses bornes ne se lit pas
   comme un fait du jour, et sa peremption se voit ;
2. **il est rendu en contraste entre les deux joueurs** de la rencontre. C'est ce
   qui le rend specifique a ce match : un chiffre qui decrit un joueur decrit
   toutes ses rencontres, donc aucune ;
3. **le contraste se tait sous le bruit** — il n'est nomme que si son intervalle
   de Newcombe exclut zero. C'est cette condition qui fait le travail qu'on
   attribuait a l'invariance : un agregat rendu avec son incertitude, et tu quand
   elle englobe zero, **ne peut pas porter un angle qu'il ne soutient pas**.

> **Le critere n'est donc pas « agregat de saison, refuse » mais « agregat qui ne
> se rend pas sous ces trois conditions, refuse ».**

Formule ainsi, il decide sans qu'on ait a trancher famille par famille — et il
ferme au passage la variante qui aurait defait un verdict par famille : une
grandeur refusee comme « non datee » revient sous forme de **serie temporelle**,
recalculee chaque semaine donc datee, et l'objection tombe alors qu'aucune
information n'a ete ajoutee. Une serie temporelle d'une grandeur qui ne varie pas
entre deux matchs reste invariante ; rendue sans contraste ni intervalle, elle
tombe sous la condition 2 ou la 3.

### Le classement : niveau 1, date, verifiable, et deja dans le prix

Cas particulier tranche **d'avance**, parce qu'il se presentera. Un classement
officiel coche toutes les cases de la table des sources : l'instance le publie, il
est date, il se verifie. Et **un angle bati sur un ecart de classement ne dit rien
que le marche ne sache deja** — il est integralement dans le prix.

Il vaut donc comme **borne de contexte, jamais comme facteur**, et le bloc doit
l'ecrire avec ce statut. Sans cette mention il servira d'angle par defaut le jour
ou rien d'autre ne se presente — et le gabarit a deja une consigne pour ce cas :
c'est un PASSE.

---

## 9 ter. Les quatre familles, instruites

Le critere du §9 bis applique. Mesures du 26/08/2026 sur la copie d'audit, plus
**trois appels en direct** a `profile/{nom}/matches-played` — endpoint deja en
service, quota mensuel a 139 090 sur 150 000 apres les trois.

### Quatre premisses renversees avant la premiere ligne d'instruction

| Premisse | Ce que la mesure dit |
| --- | --- |
| le rattachement est un prerequis de l'import | **chantier autonome** : la matiere est en base, 154 evenements, et six lignes du bloc en dependent |
| la famille « matchs precedents » vient de Tennis API | les **dates** y sont deja ; ce qui manque est le **score** et la **charge** |
| le champ `qualifying` porte les qualifications | **vide sur 962 reponses archivees et sur trois sondes en direct** — elles sont servies sous `singles` |
| le style de jeu est une caracteristique permanente, donc a refuser | **il n'existe pas** : `hand`, `ht`, `age`, `best_of` sont nuls sur **106 701 matchs sur 106 701** |

### La quatrieme aurait ferme une famille sur un zero credible

**Sixieme occurrence du motif de lecture, et la premiere sur une instruction
d'admission.** Le champ `qualifying` est present dans la charge utile — 962
reponses sur 962 — et vide partout. Lu comme une reponse, il dit « le
fournisseur ne sert pas les qualifications », et la famille se ferme.

Le denominateur le dementait : les 47 qualifies de l'US Open presents dans
`player_alias` portent **131 reponses archivees, toutes relevees les 18 et
19/08** — six jours **avant** que leurs rencontres soient jouees. Le zero ne
mesurait pas la source, il mesurait notre fenetre de collecte.

Sonde du 26/08, deux profils de qualifies : `qualifying` toujours vide, et le
match de qualification du 24/08 **present sous `singles`** —
`tournamentId: 21349`, `roundId: 1`, `result: "6-1 6-3"`, statistiques de match
completes. C'est exactement notre evenement 1168, Gonzalo Bueno – Billy Harris.

Meme famille que le `score` lu a la place de `result` au lot 16 et que le segment
de chemin du lot 17 : **le champ dont le nom decrit le besoin n'est pas celui qui
porte la donnee.**

### A. Le rattachement — chantier autonome, et sa fenetre se ferme

**L'etat mesure.** Les qualifications vivent sous les competitions **116** et
**117**, le tableau principal sous **11** et **15** — presentes au catalogue,
`api_active = 0`, aucun evenement encore. **Aucune colonne ne relie les deux** :
`competitions` porte `fenetre_debut` / `fenetre_fin` et rien d'autre.

**Ce qu'il repare sans importer un champ.** `tennis_load.load_for` filtre sur
`competition_id`, et six lignes du bloc en descendent — `Repos`, `Parcours`,
`Non joue`, `Fraicheur`, `Tour`, `Ici`. Un qualifie entrant au tableau principal
les perd toutes sur ses trois tours. La matiere est deja la : 128 rencontres,
256 joueurs, avec adversaire et horaire.

**Et rien d'autre ne la porte** : `tennis_matches` compte 14 239 lignes et
**huit tours distincts, aucun de qualification** — la source hebdomadaire ignore
les tableaux de qualification, structurellement et pas par retard.

**La question posee, et elle a une reponse.** Un champ explicite sur la
competition, jamais une convention de nommage : le dossier a refuse trois fois
la deduction par libelle — Championship ecossaise contre anglaise, Bundesliga
contre 2. Bundesliga, Coupe de Malaisie contre MLS, toutes trois avec un score
maximal. « ATP US Open Qualifications » contre « ATP US Open » est le meme piege
sous une forme plus tentante, parce que le prefixe est exact.

**Qui le renseigne : la main, sur la meme ligne que la fenetre — et pas dans le
meme formulaire.** Pas le scan : il ne cree pas ces competitions,
`tennis_fixtures` le fait sur une saisie. Mais les quatre champs de `set_fenetre`
se posent **ensemble parce qu'aucun ne sert seul**, et celui-ci sert seul —
Winston-Salem a une fenetre et aucune phase. En cinquieme champ de ce groupe, il
s'effacerait avec la fenetre. Formulaire a part, meme ligne.

**Ce qu'il ne peut pas etre, et c'est ce qui en fait un chantier.** Une
extension d'etendue appliquee aux six lecteurs **casse `Tour`** : le compte des
joueurs vus deciderait sur 128 + 256 = 384, qui n'est la taille d'aucun tableau
(`PLAUSIBLE_DRAWS`), donc `is_bracket` rendrait faux et la ligne passerait en
« phase non renseignee ». Les six lecteurs ne veulent pas la meme etendue :
`Repos`, `Fraicheur` et la charge veulent l'union ; `Tour` veut le tableau seul.
Le lien est donc une **relation entre competitions**, et chaque lecteur declare
s'il la suit — jamais un `IN (…)` recopie dans six requetes.

**Deux lecteurs filtrent deja par competition, et le second n'est pas
`load_for`** : `serve_stats` lit `events` directement, pour eviter une recursion
avec `_tournament_id`. Ecrire la resolution du lien deux fois serait la
**septieme** occurrence du motif du §8 — une seule fonction rend l'ensemble des
competitions a lire, les deux l'appellent.

**La fenetre.** Le tableau principal de l'US Open entre dans les jours qui
viennent, et c'est le seul moment de l'annee ou la matiere, le besoin et des
donnees fraiches a verifier coexistent. Passe cette date, le chantier se teste
sur un cas mort jusqu'a Melbourne.

### B. Les quatre familles au test de la phrase de section B

**1. Classements — ferme, et deux fois plutot qu'une.** Le §9 bis l'avait tranche
d'avance : integralement dans le prix, donc borne de contexte et jamais facteur.
La mesure ajoute qu'il n'y a **rien a importer** — `tennis_elo.tour_rank` porte
le classement officiel sur 1 096 des 1 101 lignes, et `player1.currentRank` est
servi sur **99,6 %** des matchs de la charge utile que nous recevons deja.

**2. Matchs precedents — la famille se coupe en deux, et une moitie passe.**

- **Les dates et les adversaires sont deja en base.** Ils relevent du
  rattachement, pas de l'import. Aucun champ nouveau.
- **Le score et la charge n'y sont pas**, et aucune autre source ne les porte.
  Ils sont servis aujourd'hui, sous `singles`, par un endpoint deja appele.

> *Phrase de section B* : « X arrive du tableau de qualification avec trois tours
> en cinq jours et 39 jeux joues, dont deux en trois sets — **maniere** — total de
> jeux, ou handicap jeux. »

Elle vient, elle est specifique a ce match, et elle vise un marche que le bloc
n'eclaire pas autrement. **La famille passe** — et elle ne coute pas un
abonnement : un appel par joueur, deja budgete.

**3. Statistiques — deux raisons de refus, et elles ne portent pas sur la meme
chose.**

> **Raison 1 — un agregat de saison est un fait sans date.** Le gabarit exige des
> faits dates, et le controle 11 vient d'etre restaure pour cela. « X gagne 68 %
> des points derriere sa premiere » ne se verifie contre aucune journee, ne
> s'invalide par aucune annonce, et ne peut pas entrer dans la section A comme un
> fait.

> **Raison 2 — une grandeur qui ne varie pas entre deux matchs ne peut ni porter
> ni invalider un angle.** Elle est vraie de toutes les rencontres du joueur
> depuis un an, donc elle est dans le prix de toutes. C'est le raisonnement qui
> ferme deja le classement, applique a une grandeur continue.

**La seconde est la plus forte, et c'est elle qui ferme les variantes datees.**
Sans elle, une statistique refusee comme « non datee » revient sous forme de
serie temporelle — un agregat glissant, recalcule chaque semaine, donc date — et
l'objection tombe alors qu'aucune information n'a ete ajoutee. Une serie
temporelle d'une grandeur invariante reste invariante.

**Et la tension se porte, plutot que de se taire** : `Service`, `Retour`, `Jeux`
et `Ecart` **sont** des agregats de saison, ils sont livres, et ils passent. Ce
qui les separe n'est pas leur nature mais leur forme de rendu — fenetre et
denominateurs ecrits, contraste entre les deux joueurs, silence quand
l'intervalle englobe zero.

**C'est cette forme qui est devenue le critere**, et elle a remplace le verdict
par famille : voir « La forme admissible d'un agregat » au §9 bis. Un agregat qui
s'y conforme entre, quelle que soit la famille dont il vient ; un agregat qui ne
s'y conforme pas decrit un joueur et pas une rencontre.

**4. Style de jeu — refuse, et il n'y avait meme pas de quoi refuser.** La
raison 2 s'y applique en entier : une caracteristique permanente est dans le prix
de toutes les rencontres du joueur depuis le debut de sa carriere. Mais la mesure
va plus loin que l'argument — **la famille n'a aucun substrat** :
`winner_hand`, `winner_ht`, `winner_age` et `best_of` sont nuls sur **106 701
matchs sur 106 701**, et aucun champ de style, de tendance ou de profil ne figure
dans la charge utile. Il n'y a pas de champ a instruire.

### C. Un champ interdit entre par une porte autorisee

`FORBIDDEN` garde les **chemins**, et son commentaire annonce que « la barriere
se pose en amont du parsing, pour que la donnee ne puisse pas entrer ». Mesure :
`player1.odd` — une cote de bookmaker — est servi sur **52 533 des 106 701
matchs (49,2 %)** de `profile/matches-played`, un endpoint autorise et deja
appele quotidiennement.

**Rien ne le lit** : aucun module ne reference ce champ, et il n'atteint aucune
table de lecture. Mais il est **archive integralement** dans `api_responses`, ce
qui est la regle de l'archive et ne doit pas changer.

Ce que ca corrige est le critere, pas le code : **l'admission se prononce par
champ et non par endpoint.** Un endpoint autorise peut porter une donnee
interdite, et c'est deja le cas. Toute extension de lecture sur cette charge
utile enumere les champs qu'elle prend, comme `tennisdata.COLUMNS` le fait deja
en ecartant les huit colonnes de cotes de cloture.

### D. Le bilan, et le critere a fait son travail

| Famille | Verdict | Ce qu'il en coute |
| --- | --- | --- |
| Classements | **refuse** — borne de contexte, deja en base a 99,6 % | rien |
| Matchs precedents · dates | **hors sujet** — deja en base | le rattachement |
| Matchs precedents · score et charge | **admis** — phrase de section B obtenue | un appel par joueur, deja budgete |
| Statistiques | **admis a la forme du §9 bis**, refuse hors d'elle | rien de nouveau |
| Style de jeu | **refuse**, et sans substrat mesurable | rien |

**Trois familles sur quatre sortent, et c'est le critere qui fonctionne** — le
§9 bis a ete ecrit pour ca, et un critere qui n'exclut jamais rien n'en est pas
un. Ce que le chantier vaut se reduit a deux gestes qui n'ont pas la meme nature :
le **rattachement**, qui repare six lignes sans importer un champ, et la **charge
de qualification**, qui est le seul champ que la mesure autorise a entrer.

---

## 10. Le point de rupture

Cinquieme de la fenetre, et le premier qui porte sur les colonnes servant a
juger les autres.

| # | Date | Changement |
| ---: | --- | --- |
| 1 | session 8, 11/08 | `prompt_odds` entre en service — le marche est fige |
| 2 | 11/08 18:52 | migration 033 — `market_key` figee a l'ecriture |
| 3 | 17/08 | migration 053 — la garde d'anteriorite marque au lieu de refuser |
| 4 | **21/08 12:24Z ± 19 h** | desactivation de la Skill — `[HYPOTHESE]` sur l'attribution, `[CONFIRME]` sur la rupture |
| 5 | **a l'activation de ce lot** | faisceau, `source_drift`, liste de refus, accuse d'appariement |
| 6 | **au premier scan qui l'evalue** | filtrage des competitions sans prix |

Le sixieme se date tout seul, et par le meme mecanisme : `note_price_coverage`
ecrit une entree de journal au **premier scan qui l'evalue**. Pas a la livraison
du code, pas au deploiement — au moment ou la composition des lots devient
soumise a la regle, **meme si ce scan-la ne retire rien**.

**Cette entree de mise en service est ce qui rend la formule vraie**, et elle a
manque au premier jet : le journal n'ecrivait que des transitions, si bien qu'un
journal muet disait « aucune competition n'a bascule » et « la regle n'a jamais
tourne » du meme silence. Deux causes, une observation — la premiere se lit et
n'appelle rien, la seconde est une panne de deploiement. Les transitions, elles,
continuent de dater les **effets**, dans les deux sens.

**Ce qu'il change, et ce qu'il ne change pas.** Il modifie la composition des
lots analyses a partir de la — donc toute comparaison de residu qui le traverse
est confondue. Il ne touche **aucune** selection passee : rien n'est supprime,
les evenements restent en base, et les selections deja prises restent dans la
population de calibration.

Il se date **a l'activation, pas a la livraison** — idiome de
`changelog.note_feedback`, qui date deja la bascule des taux sur le premier
prompt qui part avec eux. Une date de commit n'est pas une date de deploiement,
et l'incident du 21/08 sur `FRAMEWORK_VERSION` a montre ce que coute la
confusion.

**Consequence a porter dans toute lecture ulterieure** : `source_level` et
`confidence` ne sont pas comparables de part et d'autre du point 4, et les
grandeurs de faisceau ne le sont pas de part et d'autre du point 5.
