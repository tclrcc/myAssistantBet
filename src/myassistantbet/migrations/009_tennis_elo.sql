-- 009_tennis_elo.sql — classements Elo tennis et surface des competitions.
--
-- Le tennis n'avait aucune source de contexte : le bloc CONTEXTE d'un match
-- restait vide la ou le football recoit forme, classement, absents et H2H.
-- Les classements Elo publies par Tennis Abstract comblent ce trou. Ils sont
-- gratuits, sans cle, et mis a jour une fois par semaine.
--
-- La surface est portee par la competition : c'est elle qui decide quel Elo de
-- surface a du sens. Laissee NULL, seul l'Elo general est rendu — deviner la
-- surface d'apres un libelle de tournoi serait une invention.

ALTER TABLE competitions ADD COLUMN surface TEXT;   -- hard | clay | grass | NULL

CREATE TABLE tennis_elo (
  tour        TEXT    NOT NULL,       -- atp | wta
  normalized  TEXT    NOT NULL,       -- nom normalise, cle de rapprochement
  player      TEXT    NOT NULL,       -- nom tel que publie
  elo_rank    INTEGER,
  elo         REAL,
  hard_rank   INTEGER,
  hard_elo    REAL,
  clay_rank   INTEGER,
  clay_elo    REAL,
  grass_rank  INTEGER,
  grass_elo   REAL,
  peak_elo    REAL,
  peak_month  TEXT,
  tour_rank   INTEGER,                -- classement officiel ATP / WTA
  fetched_at  TEXT    NOT NULL,
  PRIMARY KEY (tour, normalized)
);
