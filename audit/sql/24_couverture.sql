-- B1 — La couverture des cotes, par competition.
--
-- **Le discriminant n'est pas « pas de cle Odds API ».** Les competitions creees
-- par `create_apifootball` n'en ont pas et portent pourtant des prix, par le
-- releve de substitution : Community Shield, Supercoupe d'Europe et Trophee des
-- Champions ont produit quatre selections tranchees. Ce qui compte est **aucune
-- cote obtenable par aucun des deux chemins**.
SELECT s.key                                             AS sport,
       c.label,
       c.oddsapi_key IS NOT NULL                         AS a_cle,
       c.active,
       c.api_active,
       COUNT(e.id)                                       AS evenements,
       SUM(EXISTS (SELECT 1 FROM odds o WHERE o.event_id = e.id))        AS avec_cotes_vivantes,
       SUM(EXISTS (SELECT 1 FROM prompt_odds q WHERE q.event_id = e.id)) AS avec_cotes_figees,
       SUM(EXISTS (SELECT 1 FROM odds o
                    WHERE o.event_id = e.id AND o.bookmaker = 'manual'))  AS avec_saisie_manuelle,
       GROUP_CONCAT(DISTINCT e.source)                   AS sources
FROM competitions c
JOIN sports s ON s.id = c.sport_id
LEFT JOIN events e ON e.competition_id = c.id
GROUP BY c.id
HAVING evenements > 0
ORDER BY avec_cotes_vivantes = 0 DESC, s.key, c.label;
