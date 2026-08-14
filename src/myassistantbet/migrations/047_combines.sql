-- Le combine comme objet d'analyse, et non comme pari pose.
--
-- Il ne passe **pas** par `coupons` : `coupons.attach()` ecrit
-- `picks.played = 1`, et un combine produit par le modele ferait alors
-- apparaitre comme paris poses des combines que personne n'a joues. Les deux
-- notions repondent a deux questions differentes — ce que valent les
-- selections, ce que valent les paris — et la page les separe deja.
--
-- `prompt_id` est **NOT NULL**, et c'est la contrainte centrale : un combine
-- reste rattache au prompt qui l'a produit. Les jambes de deux prompts
-- differents n'ont jamais ete comparees entre elles — chaque instance a
-- selectionne dans son lot, avec son quota et son budget propres — et deux
-- jambes venues de deux prompts sur le meme match seraient deux tirages du
-- meme match presentes comme deux selections. Mesure du 14/08/2026 : un match
-- est rendu 2,23 fois en moyenne dans sa session, jusqu'a 13 fois.

CREATE TABLE combos (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  -- La contrainte, portee par le schema plutot que laissee implicite.
  prompt_id     INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  kind          TEXT    NOT NULL,   -- court | long
  -- La cible visee, recopiee du prompt : elle se regle, donc la relire
  -- aujourd'hui ne dirait pas ce qui etait demande ce jour-la.
  target_price  REAL,
  -- La cote **ecrite par le modele**. Gardee a cote de la recalculee : c'est
  -- leur ecart qui se lit, pas l'une des deux.
  declared_price REAL,
  -- Le motif d'arret, seule donnee du bloc qui ne se deduise de rien :
  -- cible | plafond | confiance. C'est lui qui dira si la cible est bien
  -- reglee — toujours « cible » et elle peut monter, toujours « confiance » et
  -- le lot est la contrainte.
  stop_reason   TEXT,
  created_at    TEXT    NOT NULL
);

CREATE INDEX idx_combos_session ON combos(session_id);

-- `position` porte l'ordre d'ajout des jambes, et il n'est pas decoratif :
-- c'est lui qui permet de dire **a quel rang la premiere jambe tombe**, la
-- seule mesure qui garde un sens sur un combine long. Son taux de reussite,
-- lui, ne sera jamais mesurable : au taux de jambe constate (57 %), un combine
-- de dix jambes passe une fois sur 280.
CREATE TABLE combo_legs (
  combo_id  INTEGER NOT NULL REFERENCES combos(id) ON DELETE CASCADE,
  pick_id   INTEGER NOT NULL REFERENCES picks(id)  ON DELETE CASCADE,
  position  INTEGER NOT NULL,
  PRIMARY KEY (combo_id, pick_id)
);

CREATE INDEX idx_combo_legs_pick ON combo_legs(pick_id);
