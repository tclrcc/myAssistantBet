-- Distribution de l'overround, par tranche de 2 points. La moyenne masque la queue.
WITH livre AS (
  SELECT po.session_id, po.event_id, po.bookmaker, s.key AS sport,
         COUNT(*) AS issues, SUM(1.0/po.price) AS somme
  FROM prompt_odds po JOIN events e ON e.id=po.event_id JOIN sports s ON s.id=e.sport_id
  WHERE po.market_key='h2h' AND po.price>1.0 GROUP BY 1,2,3
), ok AS (
  SELECT sport, bookmaker, (somme-1.0)*100 AS ovr FROM livre
  WHERE (sport='football' AND issues=3) OR (sport='tennis' AND issues=2)
)
SELECT sport, bookmaker,
       CASE WHEN ovr<4 THEN '[0-4)' WHEN ovr<6 THEN '[4-6)' WHEN ovr<8 THEN '[6-8)'
            WHEN ovr<10 THEN '[8-10)' WHEN ovr<12 THEN '[10-12)' ELSE '[12+)' END AS tranche,
       COUNT(*) AS n
FROM ok GROUP BY 1,2,3 ORDER BY sport, bookmaker, tranche;
