-- Retour arriere de la migration 042_confiance_calculee.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/042_confiance_calculee.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : les faits declares. Le corps du prompt est archive
-- depuis toujours, mais la **reponse** du modele ne l'est pas — ce que l'analyse
-- avait declare disparait avec la colonne, et aucune reprise ne le reconstruit.
-- Meme nature que les releves de `prompt_odds` : une saisie figee a l'instant de
-- la decision ne se refait pas apres coup.
--
-- `confidence` n'est pas touchee. Elle a toujours porte la valeur **annoncee**,
-- et elle continue : ce retour arriere retire le calcul, pas la declaration.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE picks DROP COLUMN distinct_publishers;
ALTER TABLE picks DROP COLUMN gap_touches_factor;
ALTER TABLE picks DROP COLUMN facts_json;
ALTER TABLE picks DROP COLUMN confidence_computed;

DELETE FROM schema_migrations WHERE version = 42;

COMMIT;

PRAGMA foreign_keys = ON;
