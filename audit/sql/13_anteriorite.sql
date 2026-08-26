-- L'anteriorite DERIVEE, pour toute la periode, independamment du flag natif.
-- Le flag `tardive` est ecrit a l'insertion contre utcnow() et n'existe que
-- depuis le 17/08 : il ne peut pas servir de source de verite sur la fenetre.
CREATE TEMP VIEW v_ant AS
SELECT p.id, p.exploratoire, p.created_at, e.commence_time, p.tardive AS flag_natif,
       CASE WHEN p.created_at >= e.commence_time THEN 1 ELSE 0 END AS derivee_tardive,
       CASE WHEN p.created_at < '2026-08-17' THEN 'avant 17/08' ELSE 'a partir du 17/08' END AS regime,
       p.result, p.price, p.tier, p.confidence
FROM picks p JOIN events e ON e.id=p.event_id;

SELECT '=== taux de tardives DERIVEES, par regime x section ===';
SELECT regime, CASE exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END pop,
       COUNT(*) n, SUM(derivee_tardive) tardives,
       ROUND(100.0*SUM(derivee_tardive)/COUNT(*),1) pct
FROM v_ant GROUP BY 1,2 ORDER BY 1 DESC,2;

SELECT '=== flag natif vs derivee (concordance) ===';
SELECT regime, flag_natif, derivee_tardive, COUNT(*) n
FROM v_ant GROUP BY 1,2,3 ORDER BY 1 DESC,2,3;
