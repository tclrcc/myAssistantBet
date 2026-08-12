-- Retour arriere de la migration 035_signe_du_handicap.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/035_signe_du_handicap.down.sql
--   sudo systemctl start myassistantbet
--
-- **Le critere de l'aller ne se rejoue pas a l'envers, et il ne faut pas
-- essayer.** Une fois reparee, une ligne de substitution est indiscernable
-- d'une ligne de The Odds API : la lecture « miroir » y tient des deux cotes,
-- et rejouer le critere structurel retournerait aussi les 298 groupes qui n'ont
-- jamais eu de defaut. Le retour arriere se scope donc sur les **books du
-- releve de substitution**, seuls concernes — ce que l'aller ne pouvait pas
-- faire, une liste ne prouvant rien, mais qui suffit ici parce qu'on cherche a
-- defaire un geste connu et non a diagnostiquer.
--
-- Il remet donc **toutes** les cotes de ces books a la convention du
-- fournisseur, y compris celles ecrites apres la migration : revenir en arriere
-- sur la donnee suppose de revenir en arriere sur le code, qui reconvertirait
-- de toute facon les releves suivants.
--
-- **A verifier avant de le jouer** : la liste ci-dessous doit refleter
-- `APIFOOTBALL_BOOKMAKERS`. Un book ajoute depuis n'y figure pas, et ses lignes
-- resteraient converties. Elle exclut a dessein `pinnacle` et `unibet_nl`, qui
-- servent le meme marche par The Odds API et dont la convention est l'autre.

PRAGMA foreign_keys = OFF;

BEGIN;

UPDATE odds SET point = -point
WHERE market_key = 'spreads'
  AND point IS NOT NULL
  AND point <> 0
  AND bookmaker IN ('888sport', 'william_hill', 'betvictor', '10bet', 'bet365', 'superbet')
  AND outcome_name = (SELECT e.away FROM events AS e WHERE e.id = odds.event_id);

UPDATE prompt_odds SET point = -point
WHERE market_key = 'spreads'
  AND point IS NOT NULL
  AND point <> 0
  AND bookmaker IN ('888sport', 'william_hill', 'betvictor', '10bet', 'bet365', 'superbet')
  AND outcome_name = (SELECT e.away FROM events AS e WHERE e.id = prompt_odds.event_id);

DELETE FROM schema_migrations WHERE version = 35;

COMMIT;

PRAGMA foreign_keys = ON;
