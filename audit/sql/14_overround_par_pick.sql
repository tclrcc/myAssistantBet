-- L'overround du livre qui a REELLEMENT fourni la cote de chaque selection.
--
-- RESTREINT AU h2h, et c'est une decision : sur les handicaps et les totaux,
-- The Odds API donne a chaque issue SON PROPRE point, de signe oppose. Grouper
-- par `point` apparie donc deux moities de paliers differents et produit des
-- overrounds negatifs (mesure : -24 pts). Le h2h est le seul marche ou le livre
-- se lit sans convention a demeler.
WITH livre AS (
  SELECT po.session_id, po.event_id, po.bookmaker, s.key AS sport,
         COUNT(*) AS issues, SUM(1.0/po.price) AS somme
  FROM prompt_odds po JOIN events e ON e.id=po.event_id JOIN sports s ON s.id=e.sport_id
  WHERE po.market_key='h2h' AND po.price>1.0 GROUP BY 1,2,3
),
complet AS (
  SELECT * FROM livre
  WHERE (sport='football' AND issues=3) OR (sport='tennis' AND issues=2)
),
appari AS (
  SELECT p.id AS pick_id, p.exploratoire, p.price, p.result,
         c.bookmaker, c.somme-1.0 AS ovr,
         ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY c.somme) AS rang
  FROM picks p
  JOIN prompt_odds po ON po.session_id=p.session_id AND po.event_id=p.event_id
       AND po.market_key='h2h' AND ROUND(po.price,2)=ROUND(p.price,2)
  JOIN complet c ON c.session_id=po.session_id AND c.event_id=po.event_id
       AND c.bookmaker=po.bookmaker
  WHERE p.result IN ('win','loss') AND p.market_key='h2h'
)
SELECT CASE exploratoire WHEN 0 THEN 'section C' ELSE 'section C-bis' END AS population,
       COUNT(*) AS picks,
       ROUND(AVG(ovr)*100,2) AS ovr_moy_pts,
       ROUND(MIN(ovr)*100,2) AS mini, ROUND(MAX(ovr)*100,2) AS maxi,
       SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) AS observees,
       ROUND(SUM(1.0/price),2) AS impliquees_brutes,
       ROUND(SUM((1.0/price)/(1.0+ovr)),2) AS impliquees_corrigees,
       ROUND(SUM(1.0/price)-SUM((1.0/price)/(1.0+ovr)),2) AS biais_victoires
FROM appari WHERE rang=1 GROUP BY 1;
