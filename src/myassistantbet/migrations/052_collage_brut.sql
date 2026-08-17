-- 052_collage_brut.sql — garder le texte colle avant d'essayer de le lire.
--
-- **La ligne la plus rentable du projet, et elle n'avait pas ete faite.** Le
-- chantier precedent a etabli que `picks.claim_raw_json` etait NULL sur 235
-- selections sur 235, que le texte colle n'etait conserve **nulle part** — ni en
-- base, ni sur disque — et que le rattrapage des 86 selections des sessions 11,
-- 13 et 14 etait donc impossible. Environ douze sessions perdues, definitivement.
--
-- La journalisation des rejets (migration 050) n'y aurait rien change, et il
-- faut le dire precisement : elle attrape ce qui **leve**, pas ce qui passe et
-- se trompe. Un bloc lu correctement puis mal interprete n'y laisse aucune
-- trace, et c'est exactement la forme qu'avait la panne d'origine — la lecture
-- ne trouvait rien et ne levait rien, faute de cloture.
--
-- Ce qui suit ne repare aucun bug. Il rend le **prochain** rattrapable.

CREATE TABLE imports_raw (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  -- Le collage **integral et tel quel**, avant toute normalisation. Pas de
  -- `strip()`, pas de conversion de fins de ligne : les offsets enregistres a
  -- cote n'auraient plus de sens sur un texte retouche, et c'est justement le
  -- balisage abime qui interesse quand on rejoue.
  raw_text    TEXT    NOT NULL,
  -- L'empreinte sert a **ne pas doubler** un meme collage. Contrairement aux
  -- rejets, ou deux tentatives identiques sont deux tentatives, deux collages du
  -- meme texte n'apportent rien : ce qu'on garde est de quoi rejouer, pas un
  -- compteur d'essais. L'apercu et l'import postent le meme texte a la suite.
  sha256      TEXT    NOT NULL,
  char_count  INTEGER NOT NULL,
  -- formulaire | api | rejeu. Un rejeu se distingue d'un collage humain : sans
  -- ca, relire un import ancien en creerait un nouveau indiscernable de la
  -- saisie d'origine, et la chaine de provenance se perdrait au premier rejeu.
  source      TEXT    NOT NULL,
  created_at  TEXT    NOT NULL,
  UNIQUE (session_id, sha256)
);

CREATE INDEX idx_imports_raw_session ON imports_raw(session_id);

-- L'INTERVALLE DE POSITION. C'est lui qui rend un rejeu **cible** possible :
-- sans lui, corriger un lecteur obligerait a re-parser tout un collage et a
-- rapprocher les resultats a la main. Avec lui, une ligne dit d'ou elle vient,
-- au caractere pres.
--
-- Les deux bornes sont celles du **texte brut**, jamais d'une version
-- normalisee : `raw_text[offset_start:offset_end]` doit redonner le fragment,
-- et un test le verifie.
ALTER TABLE picks ADD COLUMN import_id    INTEGER REFERENCES imports_raw(id) ON DELETE SET NULL;
ALTER TABLE picks ADD COLUMN offset_start INTEGER;
ALTER TABLE picks ADD COLUMN offset_end   INTEGER;
-- Le bloc de confiance vit **sur la meme ligne** de `picks` mais vient d'un
-- autre endroit du collage : une seule paire de bornes ne pourrait pas porter
-- les deux, et en choisir une ferait mentir l'autre.
ALTER TABLE picks ADD COLUMN claim_offset_start INTEGER;
ALTER TABLE picks ADD COLUMN claim_offset_end   INTEGER;

ALTER TABLE combos ADD COLUMN import_id    INTEGER REFERENCES imports_raw(id) ON DELETE SET NULL;
ALTER TABLE combos ADD COLUMN offset_start INTEGER;
ALTER TABLE combos ADD COLUMN offset_end   INTEGER;

ALTER TABLE set_scores ADD COLUMN import_id    INTEGER REFERENCES imports_raw(id) ON DELETE SET NULL;
ALTER TABLE set_scores ADD COLUMN offset_start INTEGER;
ALTER TABLE set_scores ADD COLUMN offset_end   INTEGER;

ALTER TABLE ingestion_rejects ADD COLUMN import_id
  INTEGER REFERENCES imports_raw(id) ON DELETE SET NULL;
ALTER TABLE ingestion_rejects ADD COLUMN offset_start INTEGER;
ALTER TABLE ingestion_rejects ADD COLUMN offset_end   INTEGER;

CREATE INDEX idx_picks_import ON picks(import_id);

-- **Aucune purge automatique, et aucun retro-remplissage.** Le volume est
-- negligeable — une trentaine de milliers de caracteres par session, une a trois
-- sessions par jour, soit une dizaine de megaoctets par an. Et les collages
-- d'hier n'existent nulle part : la mesure commence ici, comme pour les rejets.
--
-- `ON DELETE SET NULL` plutot que `CASCADE` : supprimer un collage ne doit
-- jamais emporter les selections qui en sont sorties. La provenance se perd, la
-- mesure reste.
