SELECT '=== colonnes de notation, par section ===';
SELECT CASE exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END pop, COUNT(*) n,
  SUM(confidence IS NOT NULL) conf_annoncee,
  SUM(confidence_computed IS NOT NULL) conf_calculee,
  SUM(claim_raw_json IS NOT NULL) bloc_conf,
  SUM(source_level IS NOT NULL) src_niveau,
  SUM(angle IS NOT NULL) angle,
  SUM(prompt_id IS NOT NULL) prompt_lie,
  SUM(market_key IS NOT NULL) market_key,
  SUM(price_real IS NOT NULL) cote_obtenue,
  SUM(invalidation IS NOT NULL AND invalidation<>'') invalidation
FROM picks GROUP BY 1;

SELECT '=== conf_computed par session (section C) ===';
SELECT p.session_id, MIN(date(p.created_at)) jour, COUNT(*) n,
  SUM(p.claim_raw_json IS NOT NULL) blocs, SUM(p.confidence_computed IS NOT NULL) calcules
FROM picks p WHERE p.exploratoire=0 GROUP BY 1 ORDER BY 1;
