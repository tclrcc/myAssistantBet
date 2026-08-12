-- Retour arriere de la migration 039_marche_se_qualifie.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/039_marche_se_qualifie.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : rien. La famille se recalcule a la lecture, et une
-- selection deja enregistree sur ce marche retombera simplement dans les cles
-- non classees, ou elle sera reclamee dans les reglages.
--
-- Le retour arriere du schema ne suppose pas celui du code : `FAMILY_SEED` la
-- reposerait au prochain demarrage, ce qui est le comportement voulu.

PRAGMA foreign_keys = OFF;

BEGIN;

DELETE FROM market_families WHERE market_key = 'se qualifie';

DELETE FROM schema_migrations WHERE version = 39;

COMMIT;

PRAGMA foreign_keys = ON;
