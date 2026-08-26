SELECT CASE exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END AS pop,
       CASE WHEN ovr<3 THEN '[0-3)' WHEN ovr<5 THEN '[3-5)' WHEN ovr<7 THEN '[5-7)'
            WHEN ovr<9 THEN '[7-9)' WHEN ovr<11 THEN '[9-11)' ELSE '[11+)' END AS tranche_ovr,
       COUNT(*) n, ROUND(AVG(price),2) cote_moy
FROM (SELECT exploratoire, ovr*100 AS ovr, price FROM v_pick_ovr)
GROUP BY 1,2 ORDER BY 1,2;
SELECT '---- couverture ----';
SELECT CASE p.exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END AS pop,
       COUNT(*) AS tranchees,
       SUM(p.market_key IS NOT NULL) AS avec_market_key,
       SUM(p.id IN (SELECT pick_id FROM v_pick_ovr)) AS avec_overround
FROM picks p WHERE p.result IN ('win','loss') GROUP BY 1;
SELECT '---- par sport ----';
SELECT s.key AS sport, CASE p.exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END AS pop,
       COUNT(*) n, ROUND(AVG(v.ovr)*100,2) ovr_moy,
       SUM(p.result='win') obs, ROUND(SUM(1.0/p.price),2) impl_brut,
       ROUND(SUM((1.0/p.price)/(1.0+v.ovr)),2) impl_corr
FROM v_pick_ovr v JOIN picks p ON p.id=v.pick_id
JOIN events e ON e.id=p.event_id JOIN sports s ON s.id=e.sport_id
GROUP BY 1,2 ORDER BY 1,2;
