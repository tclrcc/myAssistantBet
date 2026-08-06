-- 016_league_context.sql — dossier de competition, mutualise entre ses equipes.
--
-- Meme raisonnement que `team_context`, un cran au-dessus. Les meilleurs buteurs
-- ne sont pas une donnee d'equipe : `/players/topscorers` rend les vingt
-- meilleurs de toute la ligue en **un seul appel**. Les ranger par equipe
-- stockerait la meme liste vingt fois et paierait un appel par equipe la ou un
-- seul suffit — ce qui annulerait tout l'interet de l'endpoint.
--
-- La table n'a pas ete creee avec `team_context` a la migration 015 : rien ne
-- l'aurait lue, et une table sans lecteur est une avance prise sur un besoin
-- qu'on ne connait pas encore. Elle arrive avec son premier usage.
--
-- `scope` porte la saison : les buteurs de 2025 et ceux de 2026 sont deux
-- releves distincts, et le second ne doit pas ecraser le premier.

CREATE TABLE league_context (
  league_id    INTEGER NOT NULL,      -- identifiant API-Football de la competition
  kind         TEXT    NOT NULL,      -- scorers | ...
  scope        TEXT    NOT NULL DEFAULT '',
  payload_json TEXT    NOT NULL,
  fetched_at   TEXT    NOT NULL,      -- ISO 8601 UTC, porte la peremption
  PRIMARY KEY (league_id, kind, scope)
);
