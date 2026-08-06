-- 017_player_context.sql — dossier de joueur, un cran sous l'equipe.
--
-- Troisieme et derniere echelle du dossier, apres `team_context` (015) et
-- `league_context` (016). Meme forme, meme cle naturelle, meme peremption lue
-- sur `fetched_at` : ce qui change est seulement le sujet du releve.
--
-- `/sidelined` est le seul endpoint dont le sujet soit un joueur, et il coute un
-- appel **par joueur**. C'est pourquoi il n'est demande que pour les buteurs deja
-- identifies, et pas pour un effectif entier — trente-six joueurs par equipe
-- feraient soixante-douze appels pour une affiche.
--
-- Verifie avant d'ecrire cette table : l'endpoint repond pour n'importe quel
-- joueur, mais ne rend **aucune entree** hors des competitions dont le
-- fournisseur couvre les blessures. Sa portee est donc celle de `/injuries`, pas
-- celle du catalogue.

CREATE TABLE player_context (
  player_id    INTEGER NOT NULL,      -- identifiant API-Football du joueur
  kind         TEXT    NOT NULL,      -- sidelined | ...
  scope        TEXT    NOT NULL DEFAULT '',
  payload_json TEXT    NOT NULL,
  fetched_at   TEXT    NOT NULL,      -- ISO 8601 UTC
  PRIMARY KEY (player_id, kind, scope)
);
