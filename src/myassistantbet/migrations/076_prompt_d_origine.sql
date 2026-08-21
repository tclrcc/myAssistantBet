-- 076_prompt_d_origine.sql — de quel lot une selection est sortie.
--
-- Une selection n'etait reliee qu'a une **session**, qui porte 1 a 20 prompts.
-- Mesure du 21/08/2026 sur les 312 selections de section C, par reconstruction
-- via `prompt_events` :
--
--     121 (38,8 %)  un seul prompt candidat
--      66 (21,2 %)  deux ou trois
--      14 ( 4,5 %)  quatre et plus
--     111 (35,6 %)  aucun prompt archive ne porte le match
--
-- `combos.prompt_id` est **NOT NULL** depuis la migration 047, et pour une
-- raison qui vaut ici aussi : les selections de deux prompts n'ont jamais ete
-- comparees entre elles — chaque instance a choisi dans son lot, avec son quota
-- et son budget propres.
--
-- ## A l'ecriture, le prompt qui a valide
--
-- La colonne se remplit desormais depuis `PromptBlocks`, le prompt dont les
-- en-tetes de blocs ont apparie le tableau colle. C'est **le meme objet** qui
-- donne son `prompt_id` a un combine : deux lectures paralleles de la meme
-- chose auraient fini par designer deux prompts differents. Et l'appariement
-- porte sa somme de controle — le champ `match` de chaque bloc — donc ce n'est
-- pas une deduction mais une verification.
--
-- ## La reprise ne prend que ce qui ne se discute pas
--
-- **Seules les lignes a candidat unique.** Une reconstruction sur 21 % de
-- candidats multiples serait une fausse certitude : le lien parait pose, rien
-- ne dit qu'il designe le bon lot, et il servirait ensuite a comparer des
-- selections qui n'ont pas ete produites ensemble. Meme arbitrage que partout
-- ici — en cas de doute, rien.
--
-- Les autres restent nulles et **se comptent** (`Analysis.picks_sans_prompt`).
-- Un compte se lit ; un lien invente ne se voit plus.
--
-- La clause s'indexe sur `prompt_id IS NULL`, donc sur la colonne qu'elle
-- corrige : idempotente et complete par construction, la lecon de la 049.
ALTER TABLE picks ADD COLUMN prompt_id INTEGER REFERENCES prompts(id);

UPDATE picks
   SET prompt_id = (
         SELECT MIN(pe.prompt_id)
           FROM prompt_events pe
           JOIN prompts pr ON pr.id = pe.prompt_id
          WHERE pe.event_id = picks.event_id
            AND pr.session_id = picks.session_id
       )
 WHERE prompt_id IS NULL
   AND event_id IS NOT NULL
   AND (
         SELECT COUNT(DISTINCT pe.prompt_id)
           FROM prompt_events pe
           JOIN prompts pr ON pr.id = pe.prompt_id
          WHERE pe.event_id = picks.event_id
            AND pr.session_id = picks.session_id
       ) = 1;
