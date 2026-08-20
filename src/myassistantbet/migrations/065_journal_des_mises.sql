-- 065_journal_des_mises.sql — l'argent, dans ses propres tables.
--
-- **La regle qui commande ce fichier** : la mesure d'analyse et le suivi
-- d'argent sont deux journaux separes. Le residu au prix mesure la qualite de
-- l'analyse en comparant des issues tranchees a des prix enregistres ; y meler
-- des montants le rendrait ininterpretable, et c'est la seule mesure que ce
-- projet sache produire.
--
-- D'ou deux tables neuves plutot qu'une colonne de plus sur `picks`. Une
-- colonne aurait ete lue par tout ce qui lit `picks` — `history.analysis()`,
-- `inference`, les trois populations — et rien n'aurait empeche un montant
-- d'entrer dans un agregat par megarde. Une table a part se garde par un test
-- qui lit la source : `tests/test_mises.py` echoue des qu'un module de mesure
-- mentionne `mises` ou `bankroll_journee`.
--
-- ## Ce qui n'est PAS recopie ici, et c'est le point
--
-- Le brief demande un journal portant « selection, montant propose, montant
-- reellement joue, cote obtenue, resultat ». Les deux derniers vivent deja sur
-- `picks` (`price_real`, `result`) et **ne sont pas recopies** : une valeur
-- dupliquee diverge, le projet l'a paye sur le niveau d'une competition, sur la
-- famille d'un marche et sur le palier d'une cote. La vue les lit par jointure.
-- Ce journal ne porte donc que ce qui n'existe nulle part ailleurs : l'argent.
--
-- ## La journee, et pourquoi ce n'est pas la session
--
-- `journee` est la date de `picks.created_at`, la « journee d'analyse » que
-- `feedback()` emploie deja — le jour ou la decision se prend, jamais celui du
-- coup d'envoi. Mesure du 20/08/2026 : la session 18 est datee du 19/08 et ses
-- cinq prompts ont tourne le 20/08 ; grouper sur la date de session ferait
-- compter les selections d'aujourd'hui dans le budget d'avant-hier.
--
-- Un plafond par session se contournerait en decoupant, et le decoupage doit
-- rester gratuit : c'est une bonne pratique d'analyse — quatre prompts, quatre
-- budgets de dossiers — et la coupler au garde-fou d'argent en ferait un
-- multiplicateur d'exposition.

-- Le montant de depart d'une journee. **Saisi a la main, jamais deduit** :
-- aucune source ne connait la bankroll, et la deduire d'un historique de mises
-- supposerait que tout ce qui a ete propose a ete joue.
CREATE TABLE IF NOT EXISTS bankroll_journee (
  journee    TEXT PRIMARY KEY,          -- AAAA-MM-JJ, date de picks.created_at
  montant    REAL NOT NULL,
  devise     TEXT NOT NULL DEFAULT 'EUR',
  note       TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Une ligne par selection ou par combine dote d'une mise.
--
-- `pick_id` et `combo_id` sont exclusifs : une ligne porte l'un ou l'autre. La
-- contrainte est ecrite dans le schema plutot que laissee au service, meme
-- regle que `combos.prompt_id NOT NULL`.
CREATE TABLE IF NOT EXISTS mises (
  id          INTEGER PRIMARY KEY,
  journee     TEXT NOT NULL,
  session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
  pick_id     INTEGER REFERENCES picks(id)    ON DELETE CASCADE,
  combo_id    INTEGER REFERENCES combos(id)   ON DELETE CASCADE,

  -- Ce que la table de mises accorde, recalcule par l'application.
  unites      REAL NOT NULL,
  montant     REAL,

  -- Ce que le rendu a ecrit sur sa ligne `mises:`. **Garde a cote et jamais a
  -- la place** : l'ecart entre les deux est ce qui se lit, exactement comme la
  -- cote declaree d'un combine et le cran annonce d'une selection.
  montant_declare REAL,

  -- Ce qui a reellement ete pose chez le bookmaker. Se saisit a la main : le
  -- relever tout seul serait une integration transactionnelle, interdit n 7.
  montant_joue    REAL,

  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,

  CHECK ((pick_id IS NULL) <> (combo_id IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mises_pick  ON mises (pick_id)  WHERE pick_id  IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_mises_combo ON mises (combo_id) WHERE combo_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mises_journee ON mises (journee);
