-- Retour arriere de la migration 043_dossiers_ouverts.sql.
--
-- Ces fichiers ne vivent pas dans `migrations/` : `db.discover_migrations` lit
-- tout `*.sql` du dossier et leve sur une version dupliquee. Ils ne sont jamais
-- joues automatiquement — a la main, sur une base sauvegardee, service arrete :
--
--   sudo systemctl stop myassistantbet
--   sqlite3 data/myassistantbet.db < deploy/rollback/043_dossiers_ouverts.down.sql
--   sudo systemctl start myassistantbet
--
-- **Ce qui ne revient pas** : la liste des dossiers ouverts et la distribution
-- des crans revendiques. Elles ne se reconstituent pas — la reponse du modele
-- n'est pas archivee, seul le prompt l'est.
--
-- **A savoir avant de le jouer** : le retour arriere ne retire pas l'override,
-- il retire la **trace** de l'override. Les selections ecrasees gardent leur
-- `source_level = 'lecture'` et leur `confidence_computed = 1`, sans plus rien
-- qui dise que ces valeurs ont ete forcees. Elles deviennent indiscernables
-- d'une lecture declaree franchement — ce qui est le sens prudent, mais efface
-- la mesure que ce chantier existe pour produire.

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE sessions DROP COLUMN open_dossiers;
ALTER TABLE picks DROP COLUMN confidence_claimed;
ALTER TABLE picks DROP COLUMN research_overridden;

DELETE FROM schema_migrations WHERE version = 43;

COMMIT;

PRAGMA foreign_keys = ON;
