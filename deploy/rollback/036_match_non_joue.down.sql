-- Retour arriere de la migration 036_match_non_joue.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/036_match_non_joue.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : les marquages poses a la main sont perdus, et rien
-- ne les reconstruit. Un forfait annonce trente minutes avant le coup d'envoi
-- n'existe dans aucune source que l'application sache lire — c'est toute la
-- raison d'etre de la saisie.
--
-- Le marquage **derive** — deux rencontres d'un meme joueur dans la meme journee
-- de tournoi — se recalcule seul, lui, et ne dependait pas de la colonne.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE events DROP COLUMN match_outcome_type;

DELETE FROM schema_migrations WHERE version = 36;

COMMIT;

PRAGMA foreign_keys = ON;
