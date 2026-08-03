-- 005_tennis_competitions.sql — competitions tennis couvertes par The Odds API.
--
-- Elles sont creees INACTIVES : un Grand Chelem ne dure que deux semaines, et un
-- scan de competition inactive ne coute rien. On les active depuis /competitions
-- au moment voulu.
--
-- Les cles ci-dessous suivent le nommage documente de The Odds API mais n'ont pas
-- ete verifiees contre l'API reelle. La synchronisation depuis `GET /sports`
-- (gratuite, bouton sur /competitions) fait autorite et corrigera l'ecart.

INSERT INTO competitions (sport_id, oddsapi_key, apifootball_league_id, label, priority, active)
SELECT s.id, v.oddsapi_key, NULL, v.label, v.priority, 0
FROM sports s
JOIN (
  SELECT 'tennis_atp_aus_open_singles' AS oddsapi_key, 'ATP — Open d''Australie' AS label, 90 AS priority
  UNION ALL SELECT 'tennis_atp_french_open',       'ATP — Roland-Garros',     90
  UNION ALL SELECT 'tennis_atp_wimbledon',         'ATP — Wimbledon',         90
  UNION ALL SELECT 'tennis_atp_us_open',           'ATP — US Open',           90
  UNION ALL SELECT 'tennis_wta_aus_open_singles',  'WTA — Open d''Australie', 80
  UNION ALL SELECT 'tennis_wta_french_open',       'WTA — Roland-Garros',     80
  UNION ALL SELECT 'tennis_wta_wimbledon',         'WTA — Wimbledon',         80
  UNION ALL SELECT 'tennis_wta_us_open',           'WTA — US Open',           80
) v
WHERE s.key = 'tennis';
