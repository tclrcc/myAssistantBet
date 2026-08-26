-- Les deux lectures cote a cote : livres h2h AVEC et SANS les cotes a 1.00.
CREATE TEMP VIEW h2h_avec AS
SELECT po.session_id,po.event_id,po.bookmaker,s.key AS sport,
       COUNT(*) issues, SUM(1.0/po.price) somme
FROM prompt_odds po JOIN events e ON e.id=po.event_id JOIN sports s ON s.id=e.sport_id
WHERE po.market_key='h2h' GROUP BY 1,2,3;

CREATE TEMP VIEW h2h_sans AS
SELECT po.session_id,po.event_id,po.bookmaker,s.key AS sport,
       COUNT(*) issues, SUM(1.0/po.price) somme
FROM prompt_odds po JOIN events e ON e.id=po.event_id JOIN sports s ON s.id=e.sport_id
WHERE po.market_key='h2h' AND po.price>1.0 GROUP BY 1,2,3;

SELECT '=== AVEC les 1.00 (lecture contaminee) ===';
SELECT sport,bookmaker,COUNT(*) livres,ROUND(AVG(somme-1)*100,2) ovr,ROUND(MAX(somme-1)*100,2) maxi
FROM h2h_avec WHERE (sport='football' AND issues=3) OR (sport='tennis' AND issues=2)
GROUP BY 1,2 ORDER BY 1,livres DESC;

SELECT '=== SANS les 1.00 (lecture retenue) ===';
SELECT sport,bookmaker,COUNT(*) livres,ROUND(AVG(somme-1)*100,2) ovr,ROUND(MAX(somme-1)*100,2) maxi
FROM h2h_sans WHERE (sport='football' AND issues=3) OR (sport='tennis' AND issues=2)
GROUP BY 1,2 ORDER BY 1,livres DESC;

SELECT '=== livres PERDUS par lexclusion (issue retiree => groupe incomplet) ===';
SELECT a.sport,a.bookmaker,COUNT(*) livres_perdus
FROM h2h_avec a LEFT JOIN h2h_sans b
  ON b.session_id=a.session_id AND b.event_id=a.event_id AND b.bookmaker=a.bookmaker
WHERE ((a.sport='football' AND a.issues=3) OR (a.sport='tennis' AND a.issues=2))
  AND (b.session_id IS NULL OR NOT ((b.sport='football' AND b.issues=3) OR (b.sport='tennis' AND b.issues=2)))
GROUP BY 1,2;
