-- 056_fraicheur_des_sources.sql — detecter qu'une source repond encore et ne bouge plus.
--
-- **La panne redoutee n'a pas eu lieu, et le mecanisme qui l'aurait vue manquait
-- quand meme.** Le lot 3 a etabli que les depots Sackmann sont supprimes ; la
-- mesure du 17/08/2026 dit qu'aucune ligne tennis n'en dependait — les six URL
-- amont repondent 200 et les collectes ont tourne ce matin. Rien n'est casse.
--
-- Mais rien ne l'aurait dit non plus. C'est cela, le defaut, et c'est le
-- caracteristique du projet une fois de plus : une source morte **repond
-- encore**. Un depot supprime rend 404 et se voit ; un fichier hebdomadaire qui
-- cesse d'etre publie rend 200, le meme classeur, indefiniment.
--
-- Et l'instrumentation existante ne pouvait pas l'attraper : `tennis_history_state`
-- date la **tentative** (`fetched_at`, avance a chaque passage du planificateur)
-- et compte les lignes, mais ne garde nulle part la date du **dernier match
-- obtenu**. La tentative avancerait tous les matins sur un contenu fige, et le
-- compteur de lignes ne bougerait pas d'un cheveu sans que personne le regarde.
--
-- **Ce qui se date ici est le contenu, pas l'appel.**

CREATE TABLE source_freshness (
  source       TEXT NOT NULL,  -- 'tennisdata', 'tennisabstract'
  scope        TEXT NOT NULL,  -- 'atp', 'wta' — les deux fichiers vivent leur vie
  -- La date du dernier fait obtenu : dernier match pour un fichier de
  -- resultats, date de releve pour un classement. **C'est elle qui doit
  -- avancer**, et c'est la seule grandeur que ce chantier ajoute.
  source_as_of TEXT,
  -- L'horodatage de la tentative. Il avance meme quand rien ne bouge, et c'est
  -- precisement pour ca qu'il ne peut pas servir de temoin tout seul.
  checked_at   TEXT NOT NULL,
  -- La derniere fois que `source_as_of` a **avance**. La stagnation se mesure
  -- entre lui et `checked_at`, jamais entre deux executions consecutives : une
  -- source relancee trois fois dans la journee ferait sinon trois comparaisons
  -- de moins de 48 h et ne stagnerait jamais, quel que soit son age reel.
  moved_at     TEXT,
  PRIMARY KEY (source, scope)
);

-- **Rien n'est retro-rempli**, et la raison est la meme que partout : l'etat
-- d'hier ne se reconstitue pas. `tennis_history_state.fetched_at` date des
-- tentatives, pas des contenus ; en deduire un `moved_at` reviendrait a affirmer
-- qu'une source a bouge un jour ou l'on sait seulement qu'on l'a appelee.
--
-- La premiere execution ecrit donc une ligne sans `moved_at` connu, et aucune
-- stagnation ne peut etre annoncee avant qu'une seconde execution ait eu lieu.
-- C'est juste : on ne peut pas dire qu'une source ne bouge plus quand on ne l'a
-- vue qu'une fois.

-- **`ingestion_rejects.session_id` devient facultatif**, et c'est le seul
-- changement de schema que ce chantier impose ailleurs.
--
-- La colonne portait `NOT NULL REFERENCES sessions(id)`, ce qui etait juste tant
-- que tout ce qui se perdait se perdait **pendant un import**. Une source amont
-- qui se fige ne se perd pendant aucune session : elle se constate a la
-- collecte, quand le planificateur tourne et qu'aucun humain ne regarde.
--
-- Deux fausses solutions ont ete ecartees : rattacher le rejet a la derniere
-- session en date en ferait un defaut de cette session-la, ce qu'il n'est pas ;
-- et creer une seconde table de pertes aurait diverge de celle-ci au premier
-- motif ajoute — c'est deja la raison pour laquelle `ingestion_rejects` existe
-- seule.
--
-- SQLite ne sait pas lever un `NOT NULL` : la table se recree, et les lignes
-- existantes se recopient telles quelles.
CREATE TABLE ingestion_rejects_new (
  id           INTEGER PRIMARY KEY,
  -- NULL = la perte n'appartient a aucune session. C'est le cas d'une source
  -- amont figee, constatee par le planificateur.
  session_id   INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  block_type   TEXT    NOT NULL,
  raw_payload  TEXT    NOT NULL DEFAULT '',
  reason       TEXT    NOT NULL,
  detail       TEXT    NOT NULL DEFAULT '',
  created_at   TEXT    NOT NULL,
  import_id    INTEGER REFERENCES imports_raw(id) ON DELETE SET NULL,
  offset_start INTEGER,
  offset_end   INTEGER
);

INSERT INTO ingestion_rejects_new
  (id, session_id, block_type, raw_payload, reason, detail, created_at,
   import_id, offset_start, offset_end)
SELECT id, session_id, block_type, raw_payload, reason, detail, created_at,
       import_id, offset_start, offset_end
  FROM ingestion_rejects;

DROP TABLE ingestion_rejects;
ALTER TABLE ingestion_rejects_new RENAME TO ingestion_rejects;
