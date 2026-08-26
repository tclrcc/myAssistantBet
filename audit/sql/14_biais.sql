-- Rattache chaque selection tranchee au livre qui a fourni sa cote (egalite de
-- prix dans le meme session/evenement/marche), puis chiffre le biais.
CREATE TEMP VIEW v_pick_ovr AS
SELECT pick_id, exploratoire, price, result, bookmaker, ovr FROM (
  SELECT p.id AS pick_id, p.exploratoire, p.price, p.result, l.bookmaker,
         l.somme-1.0 AS ovr,
         ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY ABS(l.somme-1.0)) AS rang
  FROM picks p
  JOIN prompt_odds po ON po.session_id=p.session_id AND po.event_id=p.event_id
       AND po.market_key=p.market_key AND ROUND(po.price,2)=ROUND(p.price,2)
  JOIN v_livre l ON l.session_id=po.session_id AND l.event_id=po.event_id
       AND l.bookmaker=po.bookmaker AND l.marche=po.market_key
       AND (l.pt IS NULL OR l.pt=ABS(po.point))
  WHERE p.result IN ('win','loss') AND p.market_key IS NOT NULL
) WHERE rang=1;

SELECT CASE exploratoire WHEN 0 THEN 'section C' ELSE 'section C-bis' END AS population,
       COUNT(*) AS picks_rattaches,
       ROUND(AVG(ovr)*100,2) AS ovr_moy_pts,
       ROUND(MIN(ovr)*100,2) AS mini, ROUND(MAX(ovr)*100,2) AS maxi,
       SUM(result='win') AS observees,
       ROUND(SUM(1.0/price),2) AS impliquees_brutes,
       ROUND(SUM((1.0/price)/(1.0+ovr)),2) AS impliquees_corrigees,
       ROUND(SUM(result='win')-SUM(1.0/price),2) AS residu_brut,
       ROUND(SUM(result='win')-SUM((1.0/price)/(1.0+ovr)),2) AS residu_corrige
FROM v_pick_ovr GROUP BY 1;
