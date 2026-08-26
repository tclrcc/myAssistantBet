-- Les crans forces, par jour et par cause. Ce qui se lit ici : `ligne_absente`
-- s'eteint le 20/08 avec le durcissement du refus (09e4694), et ce qui reste
-- est `reperes_non_resolus` — un collage **complet** dont les reperes n'ont pas
-- pu etre apparies. Deux defauts differents, deux correctifs differents.
SELECT DATE(p.created_at) AS jour, p.session_id, p.research_override_cause AS cause,
       COUNT(*) AS picks,
       (SELECT COUNT(*) FROM prompts q WHERE q.session_id = p.session_id) AS prompts_de_la_session
FROM picks p
WHERE p.research_overridden = 1
  AND (p.research_override_cause IN ('ligne_absente', 'ligne_illisible',
                                     'reperes_non_resolus', 'cause_inconnue')
       OR p.research_override_cause IS NULL)
GROUP BY 1, 2, 3 ORDER BY 1, 2;
