-- Journaliser **pourquoi** un contexte n'a pas ete recupere.
--
-- Quatre causes se repliaient toutes sur une densite a zero, qui se lit « pas de
-- donnees » alors qu'elle veut dire « on n'a pas pose la bonne question » : une
-- competition non rattachee, une fixture non resolue, une competition que le
-- fournisseur ne couvre pas, et le fournisseur injoignable ce jour-la. Les deux
-- premieres sont des defauts de collecte et se reparent ; les deux dernieres
-- sont des faits sur la source, et l'une des deux se retente.
--
-- **Trois des quatre se resolvent a la lecture, la quatrieme non.** Une
-- competition non rattachee et une fixture non resolue se relisent a tout
-- moment dans `competitions` et dans `events` ; « source injoignable », lui,
-- n'existe qu'a l'instant de l'appel. Resolu a la lecture seulement, il
-- disparaitrait au releve suivant et son taux serait immesurable — or c'est le
-- seul des quatre dont le taux dise quelque chose sur le fournisseur plutot que
-- sur notre saisie.
--
-- Une ligne par **tentative**, jamais un etat courant : c'est ce qui donne le
-- denominateur. `served` y figure comme les echecs, sans quoi on compterait des
-- pannes sans savoir sur combien d'essais. Volume attendu : quelques dizaines de
-- lignes par enrichissement, soit l'ordre de grandeur de `api_usage`.
--
-- Rien n'est retro-rempli : les causes d'hier ne se reconstituent pas, et une
-- table vide dit la verite — la mesure commence a la mise en service.
CREATE TABLE IF NOT EXISTS context_outcomes (
  id       INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  cause    TEXT NOT NULL,
  seen_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_context_outcomes_event
  ON context_outcomes(event_id, seen_at);
