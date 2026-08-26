-- B1/B5 — Ce que la regle « aucune cote obtenable » aurait ecarte.
--
-- Deux regles comparees, et l'ecart entre les deux est le sujet : la naive
-- (« competition sans cle Odds API ») et la mesuree (« aucune cote, par aucun
-- des deux chemins »).
SELECT 'selections sur une competition SANS cle Odds API' AS regle,
       COUNT(*) AS selections,
       SUM(p.result = 'win')  AS gagnees,
       SUM(p.result = 'loss') AS perdues,
       ROUND(AVG(p.price), 2) AS cote_moyenne
FROM picks p
JOIN events e ON e.id = p.event_id
JOIN competitions c ON c.id = e.competition_id
WHERE c.oddsapi_key IS NULL
UNION ALL
SELECT 'selections sur un evenement SANS aucune cote',
       COUNT(*), SUM(p.result = 'win'), SUM(p.result = 'loss'), ROUND(AVG(p.price), 2)
FROM picks p
JOIN events e ON e.id = p.event_id
WHERE NOT EXISTS (SELECT 1 FROM odds o WHERE o.event_id = e.id)
  AND NOT EXISTS (SELECT 1 FROM prompt_odds q WHERE q.event_id = e.id);
