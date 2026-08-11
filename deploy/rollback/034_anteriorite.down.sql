-- Retour arriere de la migration 034_anteriorite.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/034_anteriorite.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : les motifs saisis sont perdus, et rien ne les
-- reconstruit — ils distinguent une decision prise a temps mais saisie tard d'un
-- pari reellement pris en live, et cette distinction ne se retrouve dans aucune
-- autre colonne. C'est toute la raison d'etre du champ.
--
-- Retirer la colonne ne retire pas la **garde**, qui vit dans `add_pick` : la
-- base refuserait alors d'ecrire un motif qu'elle ne saurait plus stocker.
-- Le retour arriere du schema suppose donc celui du code.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE picks DROP COLUMN late_reason;

DELETE FROM schema_migrations WHERE version = 34;

COMMIT;

PRAGMA foreign_keys = ON;
