-- 029_score_en_sets.sql — le score exact en sets, enfin enregistre.
--
-- La section D du prompt impose une ligne de score en sets par match de tennis,
-- a chaque session. Elle n'etait **ni enregistree ni verifiee** : ecrite dans le
-- rendu, lue une fois, puis perdue — le sort exact que le projet a deja reserve
-- a l'effectif collecte des mois sans lecteur (migration 022).
--
-- C'est pourtant la seule mesure de la lecture de la **maniere** qui soit
-- independante de tout prix : une classification a quatre issues — `2-0`, `2-1`,
-- `1-2`, `0-2` — verifiable sur n'importe quelle feuille de match. Aucune cote
-- n'existe pour ce marche chez The Odds API, donc rien ici ne se rapproche d'un
-- prix, et l'interdit de la section 9 n'a rien a mordre.
--
-- **Saisie a la main, et non parsee du rendu.** Le prompt interdit
-- explicitement de faire de ces scores une ligne du tableau C : ils arrivent
-- donc en prose libre, une phrase de justification a cote. Un parseur de prose
-- se tromperait en silence, la ou une valeur mal lue entrerait en base et
-- fausserait le taux d'exactitude — exactement ce que ce lot cherche a mesurer.
-- Le projet a deja tranche ce genre de cas : `angle` et `source_level` passent
-- par deux menus fermes, jamais par un champ libre, parce qu'une faute de frappe
-- ferait disparaitre la ligne de son regroupement sans un mot. Quatre issues,
-- quatre options.
--
-- Cle naturelle `(session_id, event_id)` : une session ne rend qu'un score par
-- match, et rejouer la saisie corrige au lieu de dupliquer.
--
-- Le score est ecrit du point de vue du **premier joueur nomme**, comme le
-- handicap jeux et comme le H2H. Deux conventions dans la meme base se liraient
-- a l'envers, et c'est l'erreur la plus couteuse que ce module puisse produire.

CREATE TABLE set_scores (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id   INTEGER NOT NULL REFERENCES events(id)   ON DELETE CASCADE,
  predicted  TEXT NOT NULL,   -- 2-0 | 2-1 | 1-2 | 0-2
  alternate  TEXT,            -- le second scenario, quand il se defend
  actual     TEXT,            -- releve apres coup
  created_at TEXT NOT NULL,
  UNIQUE (session_id, event_id)
);

CREATE INDEX idx_set_scores_event ON set_scores(event_id);
