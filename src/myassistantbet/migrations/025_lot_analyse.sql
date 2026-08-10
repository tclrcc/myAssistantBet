-- 025_lot_analyse.sql — les matchs qu'un prompt a vraiment portes.
--
-- L'application enregistrait ce qui avait ete **selectionne**, jamais ce qui
-- avait ete **ecarte**. Le prompt annonce pourtant que passer est un resultat
-- valable et attendu sur une partie du lot : sans denominateur, cette phrase
-- n'etait ni verifiable ni suivie.
--
-- `session_events` ne peut pas tenir ce role, et ce n'est pas un oubli : c'est
-- la shortlist **courante**, elle se vide a mesure qu'on decoche. Mesure sur
-- les donnees reelles — la session du 09/08 porte 4 lignes de shortlist pour
-- 29 selections sur 29 matchs distincts, et son premier prompt en servait 12.
-- La shortlist decrit ou en est le board, pas ce qui a ete analyse.
--
-- Le lot d'une session est donc l'**union des matchs entres dans un prompt**,
-- et cette table l'enregistre au moment ou le prompt est archive. Compter des
-- matchs et non des prompts est ce qui la rend juste : regenerer vingt fois le
-- meme lot ne le gonfle pas d'une ligne, il ne grossit que lorsqu'un match
-- nouveau apparait — ce que le scan fait plusieurs fois par jour.
--
-- Un prompt restreint a une competition n'y verse que ses matchs : l'union
-- reconstitue le lot entier, la ou un maximum par prompt ne verrait que le
-- plus gros morceau.
--
-- Les prompts anterieurs a cette table n'ont aucune ligne ici. Leur lot se
-- **reconstruit a la lecture** depuis les corps archives, et se marque comme
-- tel : c'est une donnee qui dormait deja en base, pas une invention. Ce
-- chemin s'eteint de lui-meme, chaque session nouvelle enregistrant le sien.

CREATE TABLE prompt_events (
  prompt_id INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  event_id  INTEGER NOT NULL REFERENCES events(id)  ON DELETE CASCADE,
  PRIMARY KEY (prompt_id, event_id)
);

CREATE INDEX idx_prompt_events_event ON prompt_events(event_id);
