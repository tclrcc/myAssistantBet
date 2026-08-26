-- Les domaines cites comme editeur, et leur coherence de niveau.
--
-- Deux resultats, et c'est le second qui fonde `source_drift` : le niveau est
-- une propriete de l'editeur, donc un domaine ne peut pas etre 1 et 4. Au moins
-- une declaration par domaine incoherent est fausse, et ca se detecte **sans
-- savoir laquelle** — meme forme que `tier_drift`, qui expose sans arbitrer.
WITH faits AS (
  SELECT p.id AS pick_id,
         replace(lower(COALESCE(json_extract(f.value, '$.editeur_origine'),
                                json_extract(f.value, '$.editeur'), '')), 'www.', '') AS domaine,
         json_extract(f.value, '$.niveau') AS niveau
  FROM picks p
  JOIN json_each(json_extract(p.claim_raw_json, '$.faits')) f
  WHERE p.claim_raw_json IS NOT NULL
)
SELECT 'faits cites'                       AS mesure, COUNT(*)                    AS valeur FROM faits
UNION ALL
SELECT 'domaines distincts',               COUNT(DISTINCT domaine)                FROM faits
UNION ALL
SELECT 'faits par domaine',                ROUND(1.0*COUNT(*)/COUNT(DISTINCT domaine), 2) FROM faits
UNION ALL
SELECT 'domaines vus >= 2 fois',           COUNT(*) FROM (SELECT domaine FROM faits GROUP BY 1 HAVING COUNT(*) >= 2)
UNION ALL
SELECT 'faits couverts par ces domaines',  COALESCE(SUM(n),0) FROM (SELECT COUNT(*) n FROM faits GROUP BY domaine HAVING COUNT(*) >= 2)
UNION ALL
SELECT 'domaines a niveau incoherent',     COUNT(*) FROM (SELECT domaine FROM faits GROUP BY 1 HAVING COUNT(DISTINCT niveau) > 1)
UNION ALL
SELECT 'faits portes par ces domaines',    COALESCE(SUM(n),0) FROM (SELECT COUNT(*) n FROM faits GROUP BY domaine HAVING COUNT(DISTINCT niveau) > 1);

-- Le detail des incoherences, tel que `source_drift` le rendra.
WITH faits AS (
  SELECT replace(lower(COALESCE(json_extract(f.value, '$.editeur_origine'),
                                json_extract(f.value, '$.editeur'), '')), 'www.', '') AS domaine,
         json_extract(f.value, '$.niveau') AS niveau
  FROM picks p
  JOIN json_each(json_extract(p.claim_raw_json, '$.faits')) f
  WHERE p.claim_raw_json IS NOT NULL
)
SELECT domaine, GROUP_CONCAT(niveau || ' x' || n, ' | ') AS niveaux_declares, SUM(n) AS faits
FROM (SELECT domaine, niveau, COUNT(*) n FROM faits GROUP BY 1, 2)
GROUP BY domaine HAVING COUNT(*) > 1
ORDER BY faits DESC;
