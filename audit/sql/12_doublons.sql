SELECT '=== doublons exacts (session, event, market, selection) ===';
SELECT session_id, event_id, market, selection, COUNT(*) n,
       GROUP_CONCAT(id) ids, GROUP_CONCAT(DISTINCT exploratoire) sections
FROM picks GROUP BY 1,2,3,4 HAVING COUNT(*)>1 ORDER BY n DESC;

SELECT '=== deux selections sur le meme match dans la meme session ===';
SELECT COUNT(*) AS paires FROM (
  SELECT session_id,event_id FROM picks WHERE exploratoire=0
  GROUP BY 1,2 HAVING COUNT(*)>1);

SELECT '=== une selection en C ET en C-bis sur le meme match ===';
SELECT COUNT(*) FROM (
  SELECT session_id,event_id FROM picks
  GROUP BY 1,2 HAVING SUM(exploratoire=0)>0 AND SUM(exploratoire=1)>0);

SELECT '=== collisions : events (meme affiche, meme jour) ===';
SELECT home, away, date(commence_time) j, COUNT(*) n, GROUP_CONCAT(id) ids
FROM events GROUP BY 1,2,3 HAVING COUNT(*)>1 ORDER BY n DESC LIMIT 10;

SELECT '=== events sans oddsapi_event_id unique ===';
SELECT oddsapi_event_id, COUNT(*) n FROM events
WHERE oddsapi_event_id IS NOT NULL GROUP BY 1 HAVING COUNT(*)>1 LIMIT 5;
