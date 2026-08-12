-- Retour arriere de la migration 037_fuseau_du_lieu.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/037_fuseau_du_lieu.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : les fuseaux saisis sont perdus. Ils se retapent a
-- la main, une competition a la fois — c'est une saisie, pas une collecte.
--
-- Le code, lui, degrade tout seul : sans fuseau, les instants se rendent en UTC
-- et sont annonces comme tels. Le retour arriere du schema ne suppose donc pas
-- celui du code, contrairement a la migration 034.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE competitions DROP COLUMN timezone;

DELETE FROM schema_migrations WHERE version = 37;

COMMIT;

PRAGMA foreign_keys = ON;
