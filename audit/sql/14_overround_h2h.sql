-- Overround par (session, evenement, book) sur h2h, marches COMPLETS seulement.
-- Complet = 3 issues au football (1N2), 2 au tennis. Un marche incomplet ne
-- forme pas un livre et son "overround" ne veut rien dire.
WITH livre AS (
  SELECT po.session_id, po.event_id, po.bookmaker,
         s.key AS sport, c.label AS ligue,
         COUNT(*) AS issues,
         SUM(1.0/po.price) AS somme
  FROM prompt_odds po
  JOIN events e ON e.id = po.event_id
  JOIN sports s ON s.id = e.sport_id
  LEFT JOIN competitions c ON c.id = e.competition_id
  WHERE po.market_key = 'h2h' AND po.price > 1.0
  GROUP BY 1,2,3
)
SELECT sport, bookmaker,
       COUNT(*) AS livres,
       ROUND(AVG(somme-1.0)*100, 2) AS overround_moy_pts,
       ROUND(MIN(somme-1.0)*100, 2) AS mini,
       ROUND(MAX(somme-1.0)*100, 2) AS maxi
FROM livre
WHERE (sport='football' AND issues=3) OR (sport='tennis' AND issues=2)
GROUP BY 1,2 ORDER BY sport, livres DESC;
