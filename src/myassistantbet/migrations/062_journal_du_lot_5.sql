-- 062_journal_du_lot_5.sql — dater les deux changements de cadre du lot 5.
--
-- **Ils sont deux, et ils ne s'activent pas le meme jour.** C'est le point le
-- plus important de la partie gabarit du lot : le passage du budget a dix
-- dossiers et l'ajout des quatre lignes de service modifient tous deux ce que le
-- modele produit. Livres le meme jour, leurs effets seraient indissociables et
-- ce journal ne servirait a rien — il existe precisement pour que deux
-- changements de cadre se decoupent.
--
-- Le budget est donc **actif** ; les lignes de service vivent derriere
-- `SERVE_LINES_ENABLED`, **faux par defaut**.

INSERT INTO changelog_mesure (day, label, description, scope, created_at) VALUES
  ('2026-08-17', 'lot 5 — budget de recherche à 10',
   'Le budget passe de 7 à 10 dossiers par prompt. Il borne aussi les paliers hauts et le nombre de jambes d''un combiné, qui réclament chacun un fait daté : ULTRA FUN, GIGA FUN et GIGA+ se desserrent mécaniquement. La fiche de priorité se rend désormais sur tout lot et produit min(budget, lot) entrées ; sur un lot plus court que le budget elle devient un ordre de traitement et non un tri.',
   'gabarit', '2026-08-17T17:00:00Z');

-- **La date d'activation des lignes de service n'est pas celle de ce commit**, et
-- c'est pour ca qu'elle n'est pas ecrite ici. Le drapeau se bascule apres
-- quelques sessions, et la ligne s'ajoutera alors par une migration a elle —
-- l'ecrire aujourd'hui daterait le changement du jour ou il a ete **livre**, pas
-- du jour ou il a commence a agir sur ce que le modele produit.
--
-- Une ligne posee d'avance serait pire qu'absente : elle ferait couper la
-- population a une date ou rien n'a change, et les deux moities seraient
-- indiscernables.
