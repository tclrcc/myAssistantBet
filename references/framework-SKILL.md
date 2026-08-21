---
name: myassistantbet-framework
description: Framework d'analyse de paris sportifs football et tennis, structuré en sections A à F, avec paliers SAFE / FUN / ULTRA FUN / GIGA FUN / GIGA+, confiance sur 5, niveaux de source 1 à 4, et typage des angles en "issue" ou "manière". Utiliser impérativement dès qu'un bloc de données de matchs est soumis pour analyse, dès qu'il est question de pronostics, de sélections, de lot de matchs, de cotes Betclic ou Pinnacle, de combinés, de PASSE, ou dès que "MyAssistantBet" apparaît — y compris quand l'utilisateur ne mentionne explicitement ni section ni palier et se contente de coller des données de matchs. Utiliser aussi pour toute question de calibration, de relecture d'une analyse passée, ou de vérification de marchés manquants.
---

# Framework MyAssistantBet

**Version 1.4.** Ce numéro est le référent du champ `framework_version` du bloc. Le figer au premier prompt d'une session, ne jamais le remplir rétroactivement : une analyse archivée se relit contre les règles en vigueur au moment où elle a été produite, pas contre les règles actuelles.

Analyse de paris sportifs sur données pré-collectées. La sortie est toujours **en français**.

L'application MyAssistantBet collecte les matchs, les faits et les cotes, puis transmet un bloc de données. Ce document contient la méthode ; le bloc contient les faits. Ne jamais recopier la méthode dans la sortie.

## Périmètre et interdits

Le travail consiste à **hiérarchiser des sélections à partir de faits datés et sourcés**, pas à chercher de la value.

Ne jamais employer, ni dans le raisonnement ni dans la sortie : *value bet*, *espérance de gain / EV*, *edge*, *probabilité implicite*, *cote juste*. Ces notions supposent une probabilité estimée fiable ; le framework ne prétend pas en produire une meilleure que celle du marché. Ce qu'il cherche, c'est un **fait vérifié que le prix n'a pas encore intégré**.

Aucune recommandation de mise, aucun montant, aucune bankroll. Le palier est une étiquette de risque, pas une consigne de mise.

## Ce que contient le bloc de données

Chaque fait transmis porte trois attributs : **source**, **date**, **niveau**. Un fait dont l'attribution n'a pas pu être dérivée à l'assemblage est émis quand même, en niveau 4 et date nulle — jamais supprimé. L'attribution ne filtre pas l'entrée, elle **plafonne ce qu'un fait peut justifier**.

| Niveau | Nature de la source |
|---|---|
| 1 | Officiel — club, fédération, tour ATP/WTA, communiqué, compo publiée |
| 2 | Presse spécialisée identifiée, journaliste nommé couvrant le club |
| 3 | Encyclopédie, base de données généraliste, presse non spécialisée |
| 4 | Agrégateur, site de fans, réseau social non vérifié, attribution indisponible |

Le niveau classe **le canal qui transmet, pas le fait transmis**. Un agrégateur qui relaie une composition officielle reste un canal de niveau 3 : le fait sous-jacent est officiel, sa reprise ne l'est pas. La distinction n'est pas théorique — c'est par là que sont passées les erreurs d'entraîneur.

Un fait peut être **sourcé sans être daté** (`date: null` avec un niveau réel). Il reste utilisable pour un argument structurel — format de compétition, historique long, profil de surface — mais jamais pour un argument dont la force vient de la récence : forme, confirmation d'absence, changement d'entraîneur, congestion. Sans date, la récence est invérifiable, et c'est précisément la récence qui donne sa valeur à ce type d'angle.

Le niveau d'une **sélection** est celui du fait qui porte l'angle, pas le meilleur des faits cités. Un angle dirigé par une source n4, corroboré accessoirement par une n2, est une sélection n4. Prendre le meilleur reviendrait à faire endosser par la source solide un raisonnement qu'elle ne soutient pas.

**Conséquence : le bloc plafonne à 3.** Aucun fournisseur du pipeline n'est l'instance qui publie. Seule exception, une alerte d'un service météo national recopiée telle quelle. Les niveaux 1 et 2 ne s'obtiennent donc **que par la vérification** menée avant rédaction.

Le format complet du bloc est décrit dans `references/payload-contrat.md`. Le lire quand la structure du bloc reçu semble incomplète ou inhabituelle.

## Avant d'écrire quoi que ce soit

Cette étape n'est pas une précaution, c'est la seule source de niveaux 1 et 2. Sans elle, rien ne peut entrer dans le tableau principal.

Le bloc peut aussi être faux — il l'a déjà été sur des entraîneurs, deux erreurs simultanées sur un même lot. **Vérifier par recherche web** avant de rédiger, en priorité :

1. Le match a bien lieu, à l'heure indiquée, dans le lieu indiqué
2. L'entraîneur en poste de chaque équipe
3. Les absences et suspensions, avec leur date de confirmation
4. Le contexte de calendrier — match précédent, prochaine échéance, compétition continentale
5. Les conditions météo pour les sports d'extérieur

Toute divergence entre le bloc et la recherche se corrige dans la sortie **et se déclare en Section F**. Ne jamais corriger silencieusement : l'écart est un signal sur le pipeline de collecte, il a plus de valeur que la correction elle-même.

## Vocabulaire

**Typage de l'angle.** Chaque sélection est soit *issue*, soit *manière*. L'*issue* porte sur qui gagne et se traduit en 1N2, vainqueur, double chance. La *manière* porte sur la façon dont le match se joue et se traduit en totaux, handicaps, BTTS, nombre de sets. Le typage n'est pas décoratif : deux sélections *issue* sur des matchs différents peuvent partager la même cause sans que ce soit visible, et le typage aide à le repérer.

**Cause commune.** Deux sélections adossées au même mécanisme (congestion de calendrier, élimination récente, changement d'entraîneur dans la même compétition) ne sont pas deux paris indépendants. La cause commune se déclare explicitement en Section C, et interdit le combiné entre ces lignes — combiner reviendrait à miser deux fois la même thèse à un prix qui prétend le contraire.

## Paliers

Le palier découle de la cote de référence, et **d'elle seule**. Les bandes forment une partition : aucun chevauchement, aucun trou.

| Palier | Bande de cote |
|---|---|
| 🟢 SAFE | 1,25 – 1,80 |
| 🔵 FUN | 1,80 – 2,30 |
| 🟠 ULTRA FUN | 2,30 – 3,60 |
| 🔴 GIGA FUN | 3,60 – 8,00 |
| 💥 GIGA+ | 8,00 et plus |

**Convention de borne : borne basse incluse, borne haute exclue.** Une cote de 1,80 est FUN et non SAFE ; une cote de 8,00 est GIGA+ et non GIGA FUN.

**La confiance n'entre pas dans le palier.** Un arbitrage par la confiance dans une zone de chevauchement a été envisagé puis écarté sur mesure : il aurait rangé deux sélections au même prix dans deux paliers différents, donc gonflé le taux d'un palier et dégradé celui de son voisin par construction, précisément sur le segment où ils se touchent. Le palier mesure une bande de **cote** ; la confiance a son propre axe, et les croiser rendrait le croisement dégénéré — une confiance 3 à 1,72 n'aurait structurellement jamais pu être SAFE.

**Sous 1,25, hors périmètre.** Une cote inférieure à la borne basse de SAFE n'appartient à aucune bande et ne s'enregistre pas. Le signaler d'une ligne en Section C plutôt que de l'ignorer, pour que l'absence soit lisible.

GIGA+ ne s'ouvre jamais au sein d'une session unique. Il se construit sur plusieurs sessions ou pas du tout.

Un palier vide n'est pas un échec. Si aucune ligne ne relève d'ULTRA FUN ou au-delà, l'écrire et dire pourquoi : pas de retour confirmé de joueur décisif, pas d'enjeu asymétrique, pas de scénario outsider exploitable.

## Confiance

L'échelle a cinq crans et doit les utiliser. Un historique où 99 % des sélections tombent en 3 ou 4 signale une échelle sous-ancrée, pas un lot homogène.

| Cran | Ancrage | Bande de réussite visée |
|---|---|---|
| 5 | Un fait niveau 1 qui détermine mécaniquement le marché (compo officielle publiée, forfait confirmé d'un joueur décisif), et aucune variable décisive ouverte | ≥ 70 % |
| 4 | Convergence d'au moins deux faits datés de niveau 1–2 dans le même sens, aucune variable décisive ouverte | 60 – 70 % |
| 3 | Un fait de niveau 1–2 plus un contexte cohérent, mais une variable secondaire reste ouverte | 50 – 60 % |
| 2 | Lecture plausible reposant surtout sur du delta qualitatif — forme, style, matchup — sans fait dirigeant de niveau 1–2 | 35 – 50 % |
| 1 | Intuition sans fait daté | ne pas émettre |

Une sélection de confiance 2 va en **Section C-bis**, jamais dans le tableau principal.

**Le tableau principal est fermé aux sélections non vérifiées.** Le bloc plafonnant à 3, une lecture qui s'appuie uniquement sur lui ne peut pas dépasser la confiance 2 — donc pas dépasser C-bis. Ce n'est pas une contrainte administrative : c'est la traduction du fait qu'un lot arrive relayé, et que seul le travail de vérification transforme un relais en fait dirigeant. Un tableau principal bien rempli sur un lot où la recherche n'a pas été faite est un faux.

## Structure de sortie

Toujours ces sections, dans cet ordre, avec ces titres.

### Section A — Vérification

Un tableau par match, colonnes : *Match confirmé* / *Compositions* / *Absences* / *Contexte* / *Conditions* / *Ce qui reste inconnu*. Chaque case porte le niveau de source entre parenthèses. La colonne « inconnu » n'est jamais vide : s'il n'y a rien à y mettre, c'est que la vérification n'a pas été faite.

### Section B — Analyse par match

Un paragraphe par match. Nommer l'angle et son type (*issue* ou *manière*), ou prononcer un PASSE. Un PASSE nomme **la variable décisive manquante et le moment où elle sera connue** — « à revoir à la publication de la compo, une heure avant » est un PASSE utile ; « trop incertain » n'en est pas un.

### Section C — Sélections

| Match | Marché | Sélection | Cote | Palier | Conf. | Type | Niv. | Angle | Condition d'invalidation |

La condition d'invalidation est le fait qui, s'il apparaît avant le coup d'envoi, annule la sélection. Elle est obligatoire.

Déclarer sous le tableau toute cause commune entre deux lignes.

### Section C-bis — Exploratoires

Lignes de confiance 2, longues cotes, scénarios spéculatifs. Séparées visuellement, avec la même structure de tableau. Leur présence ici est la raison pour laquelle le tableau principal peut rester court.

### Section D — Combinés

Construire un combiné seulement si les jambes sont réellement disjointes. Si ce n'est pas le cas, écrire pourquoi aucun combiné n'est proposé. Un combiné de deux lignes à cause commune est une erreur, pas une prise de risque.

### Section E — Le match que je ne jouerais pas

Un match du lot, avec la raison précise. Cette section existe parce qu'un lot où tout est jouable est un lot mal lu.

### Section F — Audit

Divergences entre le bloc et la vérification. Marchés absents du bloc qui auraient changé l'analyse. Dossiers non ouverts et pourquoi. Informations qui, si elles étaient arrivées, auraient modifié une sélection ou un PASSE.

## Contrôles avant publication

À passer systématiquement, dans l'ordre :

1. **Une seule sélection par événement.** Deux lignes sur le même match, même sur des marchés différents, sont un doublon corrélé.
2. **Pas de ligne en quart.** Handicaps et totaux en .25 ou .75 sont exclus du tableau principal.
3. **Pas de sélection sur H2H seul.** Un historique de confrontations sans corroboration de forme actuelle n'est pas un fait dirigeant.
4. **Antériorité.** Refuser toute sélection sur un match dont le coup d'envoi est passé. Vérifier l'horodatage du bloc contre l'heure courante.
5. **Aucune cote inventée.** N'employer que les cotes du bloc. Une cote absente se marque « à vérifier », jamais ne s'estime.
6. **Chaque match du lot apparaît quelque part** — en C, en C-bis, ou en PASSE nommé dans B. Un match qui disparaît silencieusement fausse la mesure du taux de PASSE.
7. **Chaque sélection porte une condition d'invalidation.**
8. **Aucune conf 2 dans le tableau principal.** Sa place est en C-bis, sans exception.
9. **Chaque ligne du tableau principal est dirigée par un fait de niveau 1 ou 2.** Un angle porté par une source n3 ou n4 descend en C-bis, quelle que soit la conviction.
10. **Aucune cote sous 1,25 enregistrée.**

Les contrôles 8 et 9 sont ceux qui cèdent en premier : une lecture convaincante sur une source moyenne se présente exactement comme une lecture solide. Les vérifier explicitement, ligne par ligne, plutôt que d'en juger d'ensemble.

## Deux points de vigilance mesurés

**Le tennis sous-performe.** L'écart au taux implicite y est nettement plus dégradé qu'en football, et le marché Vainqueur est le plus touché. Sur un match de tennis, exiger un fait de niveau 1–2 daté de moins de 48 h avant d'émettre — la forme et le H2H seuls ne suffisent pas.

**Un palier haut vide n'est pas une preuve.** Une série de zéro réussite sur sept tentatives à cote moyenne 2,90 reste dans le bruit statistique. Ne pas resserrer les critères d'un palier sur un échantillon de cette taille ; noter l'observation en Section F et laisser la mesure s'accumuler.
