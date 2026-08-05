-- Couverture reelle des marches profonds, par competition.
--
-- The Odds API limite les marches additionnels aux sports americains et a une
-- selection de bookmakers : demander « handicap jeux » sur du tennis servi par
-- Betclic renvoie une reponse vide, facturee. Sans memoire de ce qui a deja ete
-- servi, l'etage B repaie ce constat a chaque session.
--
-- Une ligne par (competition, marche) : `served` passe a 1 des qu'une reponse a
-- contenu ce marche, et n'en redescend jamais — un marche peut manquer sur un
-- match et exister sur un autre.

CREATE TABLE market_coverage (
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  market_key     TEXT    NOT NULL,
  served         INTEGER NOT NULL DEFAULT 0,
  checks         INTEGER NOT NULL DEFAULT 0,
  updated_at     TEXT    NOT NULL,
  PRIMARY KEY (competition_id, market_key)
);
