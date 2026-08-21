# Contrat du bloc de données

Ce document décrit ce que MyAssistantBet transmet. Le lire quand le bloc reçu semble incomplet, mal structuré, ou quand un champ attendu manque.

## Principe

Le bloc ne contient **que des faits**. Aucune consigne de méthode, aucun rappel de format, aucune définition de palier — tout cela vit dans le SKILL.md.

Sérialisation : **JSON**, un objet par lot. C'est la seule forme où « liste vide » et « champ absent » se distinguent nativement plutôt que par convention typographique.

## Attribution

Chaque fait porte `source`, `date`, `niveau`. L'attribution se dérive **à l'assemblage**, par tranche : l'assembleur appelle ses producteurs un par un, et chaque tranche connaît sa propre source. Aucune table parallèle libellé → source, qui divergerait au premier libellé ajouté.

| Cas | Traitement |
|---|---|
| Attribution dérivable | Fait normal, `niveau` réel |
| Non dérivable | Émis, `niveau: 4`, `date: null` |

Un troisième état existe : **sourcé mais non daté** — `niveau` réel, `date: null`. Il ne se dégrade pas en niveau 4, mais il ne peut pas porter d'argument dont la force vient de la récence : forme, confirmation d'absence, changement d'entraîneur, congestion de calendrier. Ces arguments tirent leur valeur du fait que quelque chose a changé récemment ; sans date, la récence est invérifiable. Un fait non daté reste utilisable pour un argument structurel — format de compétition, historique long, profil de terrain — où la date ne change rien.

Une date manquante ne se remplace jamais par une date de repli. Une date fausse est pire qu'une date absente : elle a l'apparence d'un fait.

## Des dates, jamais des âges

Le payload transporte des **horodatages**. La fraîcheur se dérive à la lecture, elle ne s'expédie pas.

Un âge calculé au rendu — « relevé il y a 59 h » — est vrai à la seconde où il est écrit et faux pour toujours ensuite : un payload archivé relu dans six mois afficherait 59 h pour un relevé qui en aura 4 000. Aucune valeur du bloc ne porte donc de durée comptée depuis l'instant du rendu.

Les durées comptées depuis le **coup d'envoi** ne sont pas concernées : le payload porte `debut_local`, donc elles restent vraies et vérifiables.

Le bloc plafonne à **niveau 3** : aucun fournisseur du pipeline n'est l'instance qui publie. Seule exception, une alerte d'un service météo national recopiée telle quelle. Les niveaux 1 et 2 relèvent de la vérification, pas de la collecte.

## Discriminant obligatoire

`"origine": "myassistantbet"` sur la racine **et sur chaque objet-match**.

Ce champ n'est pas décoratif. Le lecteur de blocs de confiance reconnaît un bloc à sa seule forme quand la clôture manque, et sa liste de clés attendues recoupe celles du payload. Sans discriminant structurel, un objet-match recopié dans une réponse serait lu comme un bloc de confiance : échec au parse, divergence entre le compte des blocs et celui des lignes, perte des crans du lot. Panne silencieuse, détection tardive — la classe de bug déjà payée une fois.

Le discriminant s'exclut à la lecture, comme les jambes de combiné le sont déjà. Les deux chemins, clôturé et non clôturé, retombent alors sur « ignoré proprement, sans rejet compté ».

Pour la même raison, le conteneur générique ne s'appelle pas `faits` : ce libellé figure déjà dans les clés reconnues côté réponse.

## En-tête du lot

```json
{
  "origine": "myassistantbet",
  "framework_version": "1.2",
  "genere_le": "2026-08-21T09:12:00+02:00",
  "sports": ["football", "tennis"],
  "nb_matchs": 6,
  "bookmaker_principal": "betclic",
  "bookmaker_reference": "pinnacle",
  "sections_attendues": ["A", "B", "C", "C-bis", "D", "E", "F"],
  "collecte": {
    "densite": {"attendus": 25, "obtenus": 18},
    "producteurs_muets": ["tennis_history"]
  },
  "matchs": []
}
```

`sports` porte une liste : les lots mixtes existent et se rendent déjà.

`framework_version` permet de relire une analyse archivée contre les règles en vigueur au moment où elle a été produite. Sans lui, une évolution du barème de confiance rend la base de calibration inhomogène sans que rien ne le signale.

`collecte` est un bloc distinct, jamais fondu dans les attributs d'un match : la densité mesure ce que la collecte a rapporté, pas une propriété de la rencontre. Confondre les deux ferait lire « 0 sur 25 » comme un fait sur le match — exactement ce que la ligne existe pour démentir.

## Par match

```json
{
  "origine": "myassistantbet",
  "id": "M1",
  "competition": "Ligue 1",
  "tour": "J3",
  "debut_local": "2026-08-23T21:00:00+02:00",
  "debut_paris": "2026-08-23T21:00:00+02:00",
  "lieu": "Groupama Stadium",
  "statut": "programme",
  "domicile": {
    "nom": "Olympique Lyonnais",
    "classement": {"valeur": 4, "source": "...", "date": "...", "niveau": 3},
    "forme_5": {"valeur": "VVNDV", "source": "...", "date": "...", "niveau": 3},
    "entraineur": {"nom": "...", "depuis": "2026-06-14", "source": "...", "date": "...", "niveau": 3}
  },
  "exterieur": {},
  "compositions": null,
  "absences": [
    {"joueur": "...", "motif": "suspension", "confirme_le": "...", "source": "...", "niveau": 3}
  ],
  "h2h": {"resume": "...", "source": "...", "date": "...", "niveau": 3},
  "meteo": {"contenu": "...", "source": "...", "date": "...", "niveau": 1},
  "calendrier": {
    "match_precedent": {"adversaire": "...", "resultat": "...", "date": "...", "competition": "..."},
    "prochain_match": {"adversaire": "...", "date": "...", "competition": "..."}
  },
  "attributs": [
    {"cle": "xg_domicile", "valeur": 1.62, "source": "...", "date": "...", "niveau": 3},
    {"cle": "elo", "valeur": 1874, "source": "...", "date": "...", "niveau": 3}
  ],
  "cotes": {
    "releve_le": "2026-08-21T09:12:00+02:00",
    "colonnes": ["marche", "selection", "principal", "reference"],
    "lignes": [["1N2", "Dom", 1.85, 1.90], ["Total", "-2.5", 2.05, 2.10]]
  },
  "marches_absents": ["buteurs", "cartons"],
  "questions_ouvertes": ["rotation avant le déplacement européen de jeudi"]
}
```

## Le conteneur `attributs[]`

Le socle nommé couvre ce qui est référencé par une règle de décision. Tout le reste — Elo, Repos, Parcours, Profil, Marge, Usure côté tennis ; xG, corners, cartons, possession côté football — passe par `attributs[]`, chaque entrée portant `cle`, `valeur`, `source`, `date`, `niveau`.

Rien n'est perdu, tout est attribué, et un libellé ajouté demain n'exige pas de toucher au schéma.

**Règle de promotion** : un libellé monte dans le socle nommé le jour où il est référencé par une règle de décision ou sert d'axe de calibration. Pas avant. Un socle qui grossit par anticipation redevient un schéma à maintenir.

## Champs à surveiller

**`entraineur`** a produit des erreurs répétées. Sa date de prise de fonction permet de détecter un changement récent non répercuté.

**`marches_absents`** alimente la Section F. Sans lui, impossible de distinguer « ce marché n'existe pas » de « ce marché n'a pas été collecté ». La distinction se pose à l'écriture, jamais après coup.

**`questions_ouvertes`** — liste vide signifie « vérifié, rien d'ouvert » ; champ absent signifie « non instrumenté ». Ce ne sont pas la même chose et la Section A doit les différencier. Ce champ est le canal **aller**. `dossiers_ouverts` reste exclusivement le canal **retour**, celui que le modèle écrit dans sa réponse et que l'historique archivé porte déjà.

**`releve_le`** signale une cote périmée plutôt que de la traiter comme courante. Au niveau du bloc `cotes`, sauf relevé hétérogène — auquel cas il descend par ligne.

## Ce que le bloc ne contient plus

À supprimer du template : définitions des paliers et de leurs bandes, échelle de confiance et ancrages, description des sections A à F, typage issue / manière, grille des niveaux de source, consignes de langue et de format, interdits de vocabulaire, règles d'exclusion.

`sections_attendues[]` est la seule trace de cadre qui subsiste, réduite à une liste de noms. Elle existe parce que le module d'audit des sections déduit ce qui était demandé en cherchant les motifs du gabarit dans le corps du prompt ; sans gabarit, il conclut « rien n'était demandé ». La bascule se lit sur la forme du corps — un corps qui commence par `{` est un payload — pour que les prompts archivés restent lisibles sans migration.
