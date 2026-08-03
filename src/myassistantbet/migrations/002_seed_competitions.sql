-- 002_seed_competitions.sql — sports de base et competitions football initiales.
-- Ces lignes sont modifiables ensuite directement en base : la migration ne fait
-- que poser un etat de depart.

-- Cle naturelle d'une competition cote The Odds API. Les competitions sans cle
-- (cyclisme, ajouts manuels) restent possibles : SQLite considere les NULL comme
-- distincts dans un index unique.
CREATE UNIQUE INDEX idx_competitions_oddsapi_key ON competitions(oddsapi_key);

INSERT INTO sports (key, label) VALUES
  ('football', 'Football'),
  ('tennis', 'Tennis'),
  ('cycling', 'Cyclisme');

INSERT INTO competitions (sport_id, oddsapi_key, apifootball_league_id, label, priority, active)
SELECT s.id, v.oddsapi_key, v.apifootball_league_id, v.label, v.priority, 1
FROM sports s
JOIN (
  SELECT 'soccer_france_ligue_one'     AS oddsapi_key, 61  AS apifootball_league_id, 'Ligue 1'         AS label, 100 AS priority
  UNION ALL SELECT 'soccer_epl',                        39,  'Premier League',   90
  UNION ALL SELECT 'soccer_sweden_allsvenskan',         113, 'Allsvenskan',      80
  UNION ALL SELECT 'soccer_norway_eliteserien',         103, 'Eliteserien',      70
  UNION ALL SELECT 'soccer_china_superleague',          169, 'Chinese Super League', 60
  UNION ALL SELECT 'soccer_portugal_primeira_liga',     94,  'Liga Portugal',    50
  UNION ALL SELECT 'soccer_turkey_super_league',        203, 'Super Lig',        40
) v
WHERE s.key = 'football';
