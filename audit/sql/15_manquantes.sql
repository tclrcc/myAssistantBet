-- Les non-tranchees sont-elles manquantes au hasard ?
-- Population : selections dont le match est passe depuis plus de 48 h.
CREATE TEMP VIEW v_echues AS
SELECT p.*, e.commence_time, s.key AS sport,
       CASE WHEN p.result IN ('win','loss') THEN 'tranchee'
            WHEN p.result='void'    THEN 'void'
            ELSE 'non tranchee' END AS etat
FROM picks p JOIN events e ON e.id=p.event_id JOIN sports s ON s.id=e.sport_id
WHERE julianday('now') - julianday(e.commence_time) > 2;

SELECT '=== etat x section ===';
SELECT etat, SUM(exploratoire=0) C, SUM(exploratoire=1) Cbis, COUNT(*) tot
FROM v_echues GROUP BY 1 ORDER BY tot DESC;

SELECT '=== caracteristiques comparees (section C) ===';
SELECT etat, COUNT(*) n,
       ROUND(AVG(price),2) cote_moy, ROUND(AVG(confidence),2) conf_moy,
       ROUND(AVG(sport='tennis')*100,1) pct_tennis
FROM v_echues WHERE exploratoire=0 GROUP BY 1;

SELECT '=== par palier (section C) ===';
SELECT tier, SUM(etat='tranchee') tranchees, SUM(etat<>'tranchee') non_tranchees
FROM v_echues WHERE exploratoire=0 GROUP BY 1 ORDER BY 2 DESC;

SELECT '=== par marche (section C) ===';
SELECT COALESCE(market_key,'(non resolu)') mk, SUM(etat='tranchee') tr, SUM(etat<>'tranchee') non_tr
FROM v_echues WHERE exploratoire=0 GROUP BY 1 HAVING non_tr>0 ORDER BY non_tr DESC;
