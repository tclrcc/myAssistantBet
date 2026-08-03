-- 004_team_aliases.sql — correspondance persistante entre les noms d'equipes
-- de The Odds API et les identifiants d'API-Football (SPEC.md section 5).

CREATE TABLE team_aliases (
  id INTEGER PRIMARY KEY,
  oddsapi_name TEXT NOT NULL,
  apifootball_id INTEGER NOT NULL,
  apifootball_name TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'auto',  -- auto | manual
  created_at TEXT NOT NULL
);

-- Un nom cote The Odds API ne se resout qu'a une seule equipe : la resolution
-- manuelle est memorisee pour toujours et prime sur toute nouvelle deduction.
CREATE UNIQUE INDEX idx_team_aliases_name ON team_aliases(oddsapi_name);

-- Un evenement dont le mapping n'a pas pu etre etabli avec certitude. On ne
-- devine pas : l'UI propose une resolution manuelle.
ALTER TABLE events ADD COLUMN mapping_pending BOOLEAN NOT NULL DEFAULT 0;
