-- 018_tennis_matches.sql — historique des matchs de tennis joues.
--
-- Le tennis n'avait aucune source de resultats. `tennis_load.py` date les
-- apparitions d'un joueur dans un tournoi a partir de nos propres scans, mais la
-- base ne stockait **aucun resultat** : ni vainqueur, ni score, ni surface. D'ou
-- l'impossibilite de dire si deux joueurs se sont deja affrontes, ou ce qu'un
-- joueur vaut sur terre battue.
--
-- Source : tennis-data.co.uk, un classeur par saison et par circuit. Gratuit,
-- sans cle, sans quota — donc rien dans `api_usage`, qui ne compte que des
-- credits, meme regle que les classements Elo.
--
-- Ce que cette table ne porte **pas**, et ne doit jamais porter : les huit
-- colonnes de cotes de cloture du fichier source (B365, Pinnacle, Max, Avg,
-- Betfair). Ce sont les cotes de fermeture du marche, c'est-a-dire la matiere
-- premiere d'un calcul de CLV et de value — precisement l'interdit n°1 de
-- SPEC.md. Elles sont ecartees au parsing et non au rendu : ce qui n'entre pas
-- en base ne peut pas ressortir par accident.
--
-- `winner_key` et `loser_key` portent l'identite normalisee « nom|initiales »
-- (`etcheverry|tm`). Le fichier publie « Etcheverry T. M. » quand The Odds API
-- dit « Tomas Martin Etcheverry » : sans cette cle, aucun rapprochement.
-- Mesure sur 31 290 apparitions reelles : 879 cles distinctes, et **aucune
-- collision entre deux joueurs differents**.
--
-- `fetched_at` porte la peremption, comme pour `tennis_elo` : la saison en cours
-- se rafraichit une fois par semaine (le fichier est mis a jour a cette cadence),
-- une saison terminee jamais.

CREATE TABLE tennis_matches (
  tour        TEXT    NOT NULL,       -- atp | wta
  season      INTEGER NOT NULL,
  played_on   TEXT    NOT NULL,       -- AAAA-MM-JJ
  tournament  TEXT    NOT NULL,       -- nom publie, souvent celui du sponsor
  location    TEXT,                   -- ville : le seul lien fiable avec nos libelles
  series      TEXT,                   -- ATP250 | Masters 1000 | Grand Slam | ...
  court       TEXT,                   -- Outdoor | Indoor
  surface     TEXT,                   -- Hard | Clay | Grass | Carpet
  round       TEXT,                   -- 1st Round | Quarterfinals | The Final | ...
  winner      TEXT    NOT NULL,       -- tel que publie, pour l'affichage
  loser       TEXT    NOT NULL,
  winner_key  TEXT    NOT NULL,       -- identite normalisee « nom|initiales »
  loser_key   TEXT    NOT NULL,
  score       TEXT,                   -- « 6-4 3-6 7-5 », reconstruit des colonnes de sets
  comment     TEXT,                   -- Completed | Retired | Walkover
  fetched_at  TEXT    NOT NULL,
  PRIMARY KEY (tour, season, played_on, winner_key, loser_key)
);

CREATE INDEX idx_tennis_matches_winner ON tennis_matches(winner_key, played_on);
CREATE INDEX idx_tennis_matches_loser ON tennis_matches(loser_key, played_on);
