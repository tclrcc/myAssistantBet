-- Retour arriere de la migration 038_ville_du_tournoi.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/038_ville_du_tournoi.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : les villes saisies sont perdues. Elles se retapent
-- a la main, une competition a la fois — c'est une saisie, pas une collecte.
--
-- Le code degrade tout seul : sans ville, aucune ligne de meteo au tennis. Le
-- retour arriere du schema ne suppose donc pas celui du code.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE competitions DROP COLUMN city;

DELETE FROM schema_migrations WHERE version = 38;

COMMIT;

PRAGMA foreign_keys = ON;
