-- Retour arriere de la migration 033_marche_a_la_prise.sql.
--
-- **Ces fichiers ne vivent pas dans `migrations/`, et ce n'est pas un rangement
-- arbitraire** : `db.discover_migrations` lit tout `*.sql` du dossier et leve
-- sur une version dupliquee. Un `033_..._down.sql` pose a cote de son aller
-- empecherait l'application de demarrer.
--
-- Ils ne sont jamais joues automatiquement. Ils se passent a la main, sur une
-- base **sauvegardee au prealable** (`myassistantbet-backup`), le service
-- arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/033_marche_a_la_prise.down.sql
--   sudo systemctl start myassistantbet
--
-- La ligne de `schema_migrations` est retiree en dernier : si le script echoue
-- avant, la migration reste declaree appliquee, ce qui est l'etat vrai.
--
-- **Ce qui ne revient pas** : les releves de `prompt_odds` sont perdus, et rien
-- ne les reconstruit — `odds` ne garde que le dernier etat du marche. C'est
-- toute la raison d'etre de la table. Le retour arriere se paie donc en donnees,
-- pas seulement en schema.

PRAGMA foreign_keys = OFF;

BEGIN;

DROP INDEX IF EXISTS idx_prompt_odds_lot;
DROP TABLE IF EXISTS prompt_odds;

-- SQLite sait retirer une colonne depuis 3.35 (2021-03). La version embarquee
-- par Python 3.11 est largement au-dessus ; en cas de doute, `sqlite3 --version`.
ALTER TABLE picks    DROP COLUMN market_key;
ALTER TABLE sessions DROP COLUMN scale_version;
ALTER TABLE prompts  DROP COLUMN feedback_active;

DELETE FROM schema_migrations WHERE version = 33;

COMMIT;

PRAGMA foreign_keys = ON;
