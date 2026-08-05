-- Les bookmakers interroges font partie du constat de couverture.
--
-- Un marche absent d'une reponse ne prouve rien en soi : il prouve que *ces*
-- books-la ne le servaient pas. Le 4 aout 2026, le WTA Canadian Open a ete
-- constate sans handicap jeux ni total jeux alors que seul Betclic etait
-- interroge ; les books de reference ajoutes le meme soir n'ont jamais eu
-- l'occasion de repondre : la competition etait deja condamnee, et ses blocs
-- n'ont plus jamais porte que le vainqueur.
--
-- L'ensemble de books entre donc dans la cle : chaque combinaison apprend pour
-- son compte, et elargir la liste rouvre la question au lieu de la laisser
-- tranchee par un constat plus etroit.
--
-- Les lignes existantes sont reprises sous `betclic_fr` : c'est ce qui etait
-- interroge quand la memoire a ete introduite, et c'est le plus large dont on
-- soit sur. Les marches abandonnes seront donc redemandes une fois, aux books
-- courants — le cout est visible dans l'estimation avant tout appel.

CREATE TABLE market_coverage_next (
  competition_id INTEGER NOT NULL REFERENCES competitions(id) ON DELETE CASCADE,
  market_key     TEXT    NOT NULL,
  books          TEXT    NOT NULL,
  served         INTEGER NOT NULL DEFAULT 0,
  checks         INTEGER NOT NULL DEFAULT 0,
  updated_at     TEXT    NOT NULL,
  PRIMARY KEY (competition_id, market_key, books)
);

INSERT INTO market_coverage_next
  (competition_id, market_key, books, served, checks, updated_at)
SELECT competition_id, market_key, 'betclic_fr', served, checks, updated_at
FROM market_coverage;

DROP TABLE market_coverage;

ALTER TABLE market_coverage_next RENAME TO market_coverage;
