-- Overround sur les trois familles de marches ou le livre se reconstruit
-- sans ambiguite :
--   h2h              : toutes les issues du meme (session,event,book)
--   totals / *_totals: Over et Under partagent LE MEME point
--   spreads          : les deux moities portent des points OPPOSES
CREATE TEMP VIEW v_h2h AS
SELECT po.session_id,po.event_id,po.bookmaker,'h2h' AS marche,NULL AS pt,
       COUNT(*) AS issues, SUM(1.0/po.price) AS somme
FROM prompt_odds po JOIN events e ON e.id=po.event_id JOIN sports s ON s.id=e.sport_id
WHERE po.market_key='h2h' AND po.price>1.0
GROUP BY 1,2,3
HAVING (s.key='football' AND COUNT(*)=3) OR (s.key='tennis' AND COUNT(*)=2);

CREATE TEMP VIEW v_tot AS
SELECT session_id,event_id,bookmaker,market_key AS marche,point AS pt,
       COUNT(*) AS issues, SUM(1.0/price) AS somme
FROM prompt_odds
WHERE market_key IN ('totals','alternate_totals','totals_h1','team_totals') AND price>1.0
-- `description` porte l'equipe sur team_totals : l'omettre fusionnait les
-- Over/Under de DEUX equipes et rendait -13,97 pts d'overround.
GROUP BY session_id,event_id,bookmaker,market_key,point,COALESCE(description,'')
HAVING COUNT(*)=2;

CREATE TEMP VIEW v_spr AS
SELECT a.session_id,a.event_id,a.bookmaker,a.market_key AS marche,ABS(a.point) AS pt,
       2 AS issues, (1.0/a.price + 1.0/b.price) AS somme
FROM prompt_odds a JOIN prompt_odds b
  ON b.session_id=a.session_id AND b.event_id=a.event_id AND b.bookmaker=a.bookmaker
 AND b.market_key=a.market_key AND b.point = -a.point AND b.outcome_name <> a.outcome_name
WHERE a.market_key IN ('spreads','alternate_spreads') AND a.point>0 AND a.price>1.0 AND b.price>1.0;

CREATE TEMP VIEW v_livre AS
SELECT * FROM v_h2h UNION ALL SELECT * FROM v_tot UNION ALL SELECT * FROM v_spr;

SELECT marche, COUNT(*) AS livres,
       ROUND(AVG(somme-1.0)*100,2) AS ovr_moy,
       ROUND(MIN(somme-1.0)*100,2) AS mini,
       ROUND(MAX(somme-1.0)*100,2) AS maxi
FROM v_livre GROUP BY 1 ORDER BY livres DESC;
