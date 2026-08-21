# Protocole de comparaison — gabarit contre payload + SKILL

Ce document fixe la règle de décision **avant** de produire le premier résultat. Il est écrit à ce moment précisément parce qu'après quatre phases de migration, la tentation de lire un résultat ambigu comme un succès est maximale.

Version de la Skill au moment de l'écriture : **1.3** — plancher de cote 1,25 explicite, règle d'agrégation du niveau de source, deux contrôles remontés dans la checklist finale.

## La question

Le gabarit peut-il être retiré ?

## Ce qui n'est pas testé

**L'équivalence des sorties.** La Skill v1.3 diffère volontairement du gabarit : elle ouvre C-bis, ancre les cinq crans de confiance, et ferme le tableau principal aux sélections non vérifiées. Une sortie identique au gabarit prouverait que la Skill ne s'applique pas.

**Le taux de réussite.** Deux lots ne disent rien sur la performance. Tout critère fondé sur des résultats de matchs est hors périmètre ici, et le rester est une condition de validité du test.

## Conditions de tir

1. **Deux lots réels**, désignés par règle et non par choix : le **premier lot football** et le **premier lot tennis** qui se présentent après la livraison de `build_payload`. Aucune sélection discrétionnaire — « ce lot-là était particulier » est le raisonnement qui se tient toujours après une sortie décevante. Le tennis étant le poste faible mesuré, l'exclure biaiserait le test.
2. **Taille minimale : quatre matchs.** Sous ce seuil, un lot ne peut ni faire varier les crans de confiance, ni remplir C-bis, ni produire une Section F lisible. Un lot plus court n'est pas rejeté par choix : il ne compte pas comme lot, et la désignation passe au suivant. Cette clause est écrite avant de connaître le calendrier, précisément pour qu'elle ne puisse pas servir à écarter un lot décevant.
3. **Les deux versions tournent avant le coup d'envoi du premier match du lot.** L'antériorité s'applique au test comme à une session normale.
4. **Sessions séparées.** Générer les deux sorties dans une même conversation contamine la seconde.
5. **Aucune retouche manuelle** d'aucun des deux prompts.
6. `framework_version` consigné avec chaque sortie.

## Critères mesurables

Vérifiables sans jugement, à relever pour chaque sortie.

| # | Critère | Seuil |
|---|---|---|
| 1 | Faits cités traçables à une entrée du bloc ou à une source nommée | 100 % — barrière dure |
| 2 | Matchs du lot présents en C, C-bis ou PASSE nommé | 100 % — barrière dure |
| 3 | Sections attendues produites | Toutes |
| 4 | Sélections par événement | ≤ 1 |
| 5 | Lignes en quart dans le tableau principal | 0 |
| 6 | Sélections du tableau principal adossées à un fait de niveau 1–2 | 100 % |
| 7 | Répartition des crans de confiance | Au moins trois crans distincts employés sur les deux lots |
| 8 | Marchés absents signalés en Section F | Tous ceux listés dans `marches_absents` |
| 9 | Tokens **totaux**, chargement de la Skill inclus, à la taille réelle du lot | Payload strictement inférieur au prompt, et point d'équilibre déclaré |
| 10 | Conditions d'invalidation présentes | Une par sélection |

Les critères 1 et 2 sont des barrières : un manquement sur l'un des deux arrête le test, quel que soit le reste.

Le critère 9 porte sur le **coût total**, pas sur le cadre. Le payload n'a pas de cadre par construction : mesurer celui-ci reviendrait à vérifier une tautologie. Le cadre n'a pas disparu, il a changé de porteur — la Skill se charge à chaque session et son poids appartient au total. Le point d'équilibre en nombre de matchs se déclare avec la mesure : un gain qui s'inverse au-delà de la taille de lot habituelle n'est pas un gain.

**Mesure du 21/08/2026, avant le premier tir** — lot réel de 4 matchs, `SKILL.md` à 3 159 tokens :

| taille du lot | prompt | payload + Skill | écart |
| ---: | ---: | ---: | ---: |
| 4 | 18 610 | 9 927 | −47 % |
| 7 | 21 784 | 15 003 | −32 % |
| 8 | 22 842 | 16 695 | −27 % |
| 10 | 24 958 | 20 079 | −20 % |
| 18 | 33 422 | 33 615 | 0 % |
| 21 | 36 596 | 38 691 | +5 % |

**Point d'équilibre : ~17,7 matchs.** Il valait 7,6 avant deux corrections de format appliquées le même jour — les attributs passés en colonnaire (−7 %) et surtout la suppression de l'indentation (−28 %, le poste le plus cher après les faits eux-mêmes).

**Plus aucun changement de format à partir du premier lot tiré.** Ces deux-ci sont antérieurs, et déclarés ici.

## Comparaison de qualité

Le jugement humain porte sur **les arguments, pas sur la structure**.

L'aveuglement complet est impossible : la structure de sortie trahit immédiatement quelle version a produit quoi. La procédure contourne le problème plutôt que de prétendre l'ignorer.

1. Extraire de chaque sortie la liste des angles — un angle par ligne, sans étiquette de section, sans palier, sans cran de confiance.
2. Mélanger les deux listes, ordre aléatoire.
3. Juger chaque angle isolément sur une seule question : **cet argument aurait-il tenu si le match avait mal tourné pour une raison prévisible ?**
4. Ne défaire l'anonymat qu'après le classement complet.

## Règle de décision

**Le gabarit est retiré** si, sur les deux lots :
- les barrières 1 et 2 tiennent ;
- les critères 3 à 10 sont satisfaits ;
- le classement à l'aveugle ne place pas les angles du gabarit nettement devant.

**Le gabarit est conservé** si l'une de ces conditions apparaît :
- un fait non traçable dans une sortie payload ;
- un match du lot disparu silencieusement ;
- le classement à l'aveugle favorise nettement le gabarit.

**Deux lots supplémentaires** dans tous les autres cas. Un résultat ambigu n'est pas un résultat favorable.

## Ce qui invaliderait le test

- Un lot choisi parce qu'il « marche bien »
- Une sortie régénérée après une première tentative jugée décevante
- Un ajustement de la Skill entre les deux lots
- Une comparaison faite après connaissance des résultats sportifs
- Un jugement de qualité rendu avant la levée de l'anonymat

Consigner toute déviation. Un protocole dévié et déclaré reste lisible ; un protocole dévié en silence ne prouve rien.
