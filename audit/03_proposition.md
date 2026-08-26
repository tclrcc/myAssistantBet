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
intention. Texte propose, a inserer apres « CE QU'IL FAUT VERIFIER » :

> ### La recherche : trois objectifs, trois niveaux atteignables
>
> La verification est la **seule** source de niveaux 1 et 2 : les blocs de ce
> prompt ne sont jamais une source, donc une selection qui n'y ajoute rien est
> une `lecture`. Sans recherche, rien n'entre au tableau principal au-dessus du
> cran 2.
>
> Trois objectifs, et ils ne sont pas interchangeables — chacun atteint un
> niveau different, et c'est le niveau qui decide de ce que le fait pourra
> porter.
>
> | Objectif | Ce que tu cherches | Ou | Fraicheur | Niveau atteignable |
> |---|---|---|---|---|
> | Cadrage du lot | compos probables, absents, incertains | agregateur a format constant | le jour meme | 3 |
> | **Fait dirigeant** | l'annonce elle-meme | **site officiel du club, de la federation, du tour** — ou le journaliste nomme qui couvre le club | la semaine | **1 ou 2** |
> | Balayage | ce qui a ete publie sur la competition | actualite, presse | le jour meme | 3 a 4 |
>
> Formule des requetes **courtes** — le nom du club ou du joueur, plus ce que tu
> cherches. Une requete qui decrit une phrase entiere ne rend rien d'utile.
>
> **La requete de cadrage ne dirige aucun angle.** Un agregateur qui publie le
> onze, les absents et les incertains dans un format constant reste un canal de
> niveau 3 — c'est la regle du canal qui transmet, appliquee au cas le plus
> tentant qui soit. Le confort de lecture n'est pas une source. Seule la
> deuxieme forme, celle qui vise le site officiel du club ou le journaliste
> nomme qui le couvre, produit ce que le tableau principal exige. Le cadrage
> sert a savoir quoi aller chercher, pas a le sourcer.
>
> **Le niveau classe le canal qui transmet, pas le fait transmis.** Un
> agregateur qui relaie une composition officielle reste un canal de niveau 3 :
> le fait sous-jacent est officiel, sa reprise ne l'est pas. La distinction
> n'est pas theorique — c'est par la que sont passees les erreurs d'entraineur.
>
> **Attribution par domaine.**
>
> | Domaine | Niveau |
> |---|---|
> | Site officiel de club, de federation, de tour ATP/WTA | 1 |
> | Presse specialisee a journaliste identifie couvrant le club, presse locale du club | 2 |
> | Agregateur de compos a format constant | 3 |
> | Site de pronostics, page adossee a un operateur | 4 — **ecarter** |
>
> Les pages de pronostics se reconnaissent a un code promotionnel ou a un
> comparateur de cotes. Elles s'ecartent non parce qu'elles seraient fausses,
> mais parce qu'elles vendent un operateur : leur choix de faits sert un
> argumentaire, et un fait retenu pour convaincre ne vaut pas un fait rapporte.
>
> **Conversion des dates.** Une recherche renvoie des dates relatives — « il y a
> 10 heures ». Convertis en horodatage absolu au moment de la collecte. Si la
> conversion echoue, le fait entre avec une date vide et son niveau reel : il
> garde sa valeur structurelle et perd le droit de porter un angle de recence.
> Sans cette regle, une date relative devient silencieusement un fait date, et
> la confiance 4 se construit sur du vide.
>
> Verifie aussi que la publication est **posterieure a la derniere conference de
> presse**. Un article paru avant elle ne dit rien des compositions, quelle que
> soit sa fraicheur apparente.
>
> **Ce que la recherche ne donne pas.** Une composition probable publiee
> quelques heures avant le coup d'envoi est **deja dans le prix**. Elle ne
> procure aucune avance et n'a pas a etre traitee comme telle. Sa fonction est
> d'**invalider** : elle confirme ou detruit un angle deja forme sur les faits
> du bloc. Un PASSE declenche par la recherche est un resultat plein, pas un
> echec de session.

S'y ajoutent, dans la liste des controles :

> · **Pas de selection sur H2H seul.** Un historique de confrontations sans
>   corroboration de forme actuelle n'est pas un fait dirigeant.
> · **Toute date relative issue de la recherche est convertie.** Un fait dont
>   l'horodatage absolu n'a pas pu etre etabli ne porte aucun angle de recence,
>   quel que soit son niveau.

Et la vigilance mesuree, gardee par le sport du lot :

> **Le tennis sous-performe.** L'ecart au taux implicite y est nettement plus
> degrade qu'au football, et le marche Vainqueur est le plus touche. Sur un
> match de tennis, exige un fait de niveau 1-2 date de moins de 48 h avant
> d'emettre — la forme et le H2H seuls ne suffisent pas.

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

Le chantier `source_drift` en a produit une cinquieme le meme jour, et elle n'a
rien coute pour la meme raison : compter les editeurs par un rapprochement de
domaines ecrit sur place, quand `Fact.source` tranche deja cette question pour
compter les facteurs independants. Un agregateur qui relaie un communique de club
se serait compte sous `onefootball.com` d'un cote et sous `arsenal.com` de
l'autre — **et l'ecart n'aurait jamais fait echouer un test**, les deux lectures
etant justes chacune de son cote.

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

## 10. Le point de rupture

Cinquieme de la fenetre, et le premier qui porte sur les colonnes servant a
juger les autres.

| # | Date | Changement |
| ---: | --- | --- |
| 1 | session 8, 11/08 | `prompt_odds` entre en service — le marche est fige |
| 2 | 11/08 18:52 | migration 033 — `market_key` figee a l'ecriture |
| 3 | 17/08 | migration 053 — la garde d'anteriorite marque au lieu de refuser |
| 4 | **21/08 12:24Z ± 19 h** | desactivation de la Skill — `[HYPOTHESE]` sur l'attribution, `[CONFIRME]` sur la rupture |
| 5 | **a l'activation de ce lot** | ce document |

Il se date **a l'activation, pas a la livraison** — idiome de
`changelog.note_feedback`, qui date deja la bascule des taux sur le premier
prompt qui part avec eux. Une date de commit n'est pas une date de deploiement,
et l'incident du 21/08 sur `FRAMEWORK_VERSION` a montre ce que coute la
confusion.

**Consequence a porter dans toute lecture ulterieure** : `source_level` et
`confidence` ne sont pas comparables de part et d'autre du point 4, et les
grandeurs de faisceau ne le sont pas de part et d'autre du point 5.
