-- B1 — Les evenements sans **aucune** cote, par les deux chemins reunis.
--
-- La question n'est pas « en a-t-il aujourd'hui » mais « le vide s'est-il
-- referme ». `odds` ne garde que le dernier releve ; `prompt_odds` garde ce qui a
-- ete fige. Un evenement absent des deux n'a jamais porte de prix.
SELECT s.key AS sport,
       c.label,
       e.source,
       COUNT(*) AS evenements,
       ROUND(AVG((julianday(e.commence_time) - julianday(e.created_at)) * 24), 1) AS h_avant_coup_envoi,
       SUM(e.commence_time < (SELECT MAX(created_at) FROM events)) AS deja_passes
FROM events e
JOIN competitions c ON c.id = e.competition_id
JOIN sports s ON s.id = e.sport_id
WHERE NOT EXISTS (SELECT 1 FROM odds o WHERE o.event_id = e.id)
  AND NOT EXISTS (SELECT 1 FROM prompt_odds q WHERE q.event_id = e.id)
GROUP BY c.id, e.source
ORDER BY evenements DESC;
