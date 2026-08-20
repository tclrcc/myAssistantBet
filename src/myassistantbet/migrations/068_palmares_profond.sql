-- 068_palmares_profond.sql — le palmares, sur l'historique entier d'un joueur.
--
-- **Le blocage n'etait pas ce que le lot 15 annoncait.** Les 43 tournois sur 43
-- sont rattaches ; ce qui manquait etait la **profondeur** : on demandait une
-- page de 100 matchs la ou la source en annonce **509 en mediane**, donc 99,2 %
-- de l'historique restait derriere notre propre pagination.
--
-- Sondage du 20/08/2026, six joueurs : `pageSize=200` est honore, l'historique
-- remonte a **2009** pour Pegula, 2013 pour Paul, 2015 pour Anisimova. Le cout
-- est de deux appels de plus par joueur — mediane 3 pages, max 8.
--
-- ## Pourquoi une table plutot qu'un calcul a la lecture
--
-- Le palmares se derive de l'historique, et l'historique coute des appels. Le
-- recalculer a chaque rendu de prompt paierait la pagination a chaque
-- generation — une session en produit quatre a cinq. La table porte donc le
-- **resume**, pas la charge utile : quelques centaines de lignes de six champs,
-- la ou le brut peserait des dizaines de kilo-octets par joueur. Meme arbitrage
-- que `_summarize` pour l'historique de saison d'une equipe.
--
-- ## Ce qui n'y est pas
--
-- Aucune cote, aucun score. Un palmares est une **frequence passee** : il dit ce
-- qu'un joueur a atteint, jamais ce qu'il vaut. Le gabarit porte l'interdiction
-- de le rapprocher d'une cote, comme il le fait pour `Elo` et `xG`.

CREATE TABLE IF NOT EXISTS player_palmares (
  -- La graphie **canonique du fournisseur**, celle que `player_alias` a validee
  -- sur le contenu — jamais notre graphie locale, deux noms locaux pouvant
  -- designer le meme profil.
  player      TEXT NOT NULL,
  circuit     TEXT NOT NULL,          -- atp | wta

  -- Le resume : une entree par edition disputee, avec son tournoi, sa
  -- categorie, sa surface, son annee, son tour le plus profond et l'issue.
  payload_json TEXT NOT NULL,

  -- **La date du dernier match compte**, pas celle du calcul : sans elle, un
  -- palmares vieux d'un mois se lirait comme actuel — le defaut que `Fraicheur`
  -- corrige ailleurs.
  as_of       TEXT NOT NULL,
  fetched_at  TEXT NOT NULL,
  -- Combien de pages ont ete lues, et combien la source en annonce. **L'ecart
  -- se voit** : un historique tronque par une erreur de pagination ne doit pas
  -- se lire comme un joueur qui a peu joue.
  pages       INTEGER NOT NULL DEFAULT 0,
  announced   INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (player, circuit)
);
