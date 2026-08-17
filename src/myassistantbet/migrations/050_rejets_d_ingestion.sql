-- 050_rejets_d_ingestion.sql — plus aucune perte silencieuse a l'import.
--
-- **C'est la correction dont tout le reste decoule**, et c'est la cinquieme
-- occurrence du defaut caracteristique du projet : une sortie identique pour
-- l'echec et pour le cas ordinaire. Un bloc malforme, introuvable ou refuse par
-- la validation disparaissait sans trace — l'ecran affichait un import reussi,
-- et le manque se decouvrait des semaines plus tard sur la page de
-- statistiques, quand il ne se reparait plus.
--
-- Mesure du 17/08/2026 qui la fonde : `claim_raw_json` est NULL sur **235
-- selections sur 235**, y compris les 86 des trois sessions posterieures a la
-- mise en service du bloc de confiance. Rien, nulle part, ne disait qu'un bloc
-- avait ete cherche et pas trouve.
--
-- La table ne mesure pas le modele : elle mesure **le chemin d'ingestion**. Un
-- rejet est un bloc qui a existe et qui n'est pas entre — jamais un bloc que le
-- rendu n'a pas produit.

CREATE TABLE ingestion_rejects (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  -- conf | combo | score_sets | selection | exploratoire. Le type dit **quel
  -- chemin** a perdu quelque chose : les quatre premiers sont des blocs du
  -- rendu, le cinquieme une ligne du tableau refusee a l'ecriture.
  block_type  TEXT    NOT NULL,
  -- Le texte brut, tel qu'il a ete recu. **C'est lui qui rend le rejet
  -- reparable** : sans lui on saurait qu'un bloc a echoue et jamais lequel, ce
  -- qui est exactement le silence qu'on corrige. Vide quand il n'y avait rien a
  -- recevoir — un bloc absent n'a pas de charge utile.
  raw_payload TEXT    NOT NULL DEFAULT '',
  -- fence_not_found | json_invalid | schema_invalid | match_ref_unresolved
  -- | duplicate | other. Une enumeration et non du texte libre : c'est elle qui
  -- se compte, et deux orthographes du meme motif feraient deux lignes.
  reason      TEXT    NOT NULL,
  -- La phrase qui accompagne le motif, telle qu'elle a ete affichee. Le motif
  -- classe, le detail nomme.
  detail      TEXT    NOT NULL DEFAULT '',
  created_at  TEXT    NOT NULL
);

CREATE INDEX idx_ingestion_rejects_session ON ingestion_rejects(session_id);
CREATE INDEX idx_ingestion_rejects_created ON ingestion_rejects(created_at);

-- **Rien n'est retro-rempli, et une table vide dit la verite.** Les rejets
-- d'hier ne se reconstituent pas : le texte colle n'est conserve nulle part, ni
-- en base ni sur disque. La mesure commence a la mise en service — meme
-- arbitrage que la migration 044 pour les causes de contexte.
