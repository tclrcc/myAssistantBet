-- Serie du faisceau, par session. Section C seule : c'est la population que la
-- page mesure, et melanger C-bis y ferait entrer une population sans exigence
-- de fait date.
--
-- Quatre grandeurs, et aucun seuil : ce fichier publie une serie, il ne juge
-- rien. Le seuil se posera quand il y aura de quoi le calibrer.
WITH faits AS (
  SELECT p.session_id,
         DATE(p.created_at)                       AS jour,
         p.id                                     AS pick_id,
         json_extract(f.value, '$.niveau')        AS niveau,
         lower(COALESCE(json_extract(f.value, '$.editeur_origine'),
                        json_extract(f.value, '$.editeur'), '')) AS editeur
  FROM picks p
  JOIN json_each(json_extract(p.claim_raw_json, '$.faits')) f
  WHERE p.claim_raw_json IS NOT NULL AND p.exploratoire = 0
),
blocs AS (
  SELECT session_id, COUNT(*) AS blocs
  FROM picks
  WHERE claim_raw_json IS NOT NULL AND exploratoire = 0
  GROUP BY session_id
)
SELECT b.session_id,
       MIN(f.jour)                                            AS depuis,
       b.blocs,
       COUNT(f.pick_id)                                       AS faits,
       ROUND(1.0 * COUNT(f.pick_id) / b.blocs, 2)             AS faits_par_bloc,
       ROUND(100.0 * SUM(f.niveau = 1) / COUNT(f.pick_id), 1) AS pct_niveau_1,
       ROUND(100.0 * SUM(f.niveau = 4) / COUNT(f.pick_id), 1) AS pct_niveau_4,
       SUM(f.editeur LIKE '%betfair%'   OR f.editeur LIKE '%sportytrader%'
        OR f.editeur LIKE '%pronostic%' OR f.editeur LIKE '%bookmaker%'
        OR f.editeur LIKE '%oddschecker%' OR f.editeur LIKE '%forebet%'
        OR f.editeur LIKE '%coteur%')                         AS domaines_refuses
FROM blocs b
LEFT JOIN faits f ON f.session_id = b.session_id
GROUP BY b.session_id
ORDER BY b.session_id;
