SELECT '=== couverture globale sur les 491 tranchees ===';
SELECT CASE p.exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END pop,
  COUNT(*) tranchees,
  SUM(p.market_key IS NOT NULL) avec_mk,
  SUM(p.id IN (SELECT pick_id FROM v_pick_ovr)) avec_ovr,
  ROUND(100.0*SUM(p.id IN (SELECT pick_id FROM v_pick_ovr))/COUNT(*),1) pct
FROM picks p WHERE p.result IN ('win','loss') GROUP BY 1;

SELECT '=== detail par marche (tranchees) ===';
SELECT COALESCE(p.market_key,'(sans market_key)') mk,
  CASE p.exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END pop,
  COUNT(*) n, SUM(p.id IN (SELECT pick_id FROM v_pick_ovr)) avec_ovr
FROM picks p WHERE p.result IN ('win','loss') GROUP BY 1,2 ORDER BY n DESC;
