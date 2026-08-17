-- 053_population_tardive.sql — une troisieme population, nommee comme un choix.
--
-- **Le diagnostic d'origine etait faux, et sa correction change la
-- conclusion.** On tenait les 52 selections ecartees pour un defaut de
-- collecte ; la mesure du 17/08/2026 dit **0 sur 230 sans horodatage**. Elles
-- ont reellement ete ecrites apres le coup d'envoi, et 37 d'entre elles sans
-- meme la declaration `differee`.
--
-- Ce n'est donc plus un bug a reparer, c'est un choix d'usage a trancher — et la
-- page les presentait comme une reserve de lecture, c'est-a-dire comme un
-- manque. Ce n'en est pas un.
--
-- **Ce que ce chiffre mesure vaut mieux que d'etre jete** : les 52 tardives sont
-- **au-dessus** de leur prix (32 victoires pour 28,8 payees, P = 0,856), les 178
-- anterieures **en dessous** (89 pour 103,8, P = 0,014). L'ecart entre les deux
-- populations est la meilleure estimation disponible du biais que produit une
-- selection ecrite en connaissant le debut du match. C'est une donnee a isoler
-- proprement, pas a supprimer.

ALTER TABLE picks ADD COLUMN tardive BOOLEAN NOT NULL DEFAULT 0;

-- **Le retro-remplissage est sur ici, et il ne l'etait pas ailleurs.** La
-- difference tient en une phrase : cette valeur se **derive** de donnees deja en
-- base — `picks.created_at` et `events.commence_time` — alors que `price_source`
-- (migration 030) ou le cran calcule (042) auraient demande de reconstituer une
-- information qui n'a jamais ete ecrite. Deriver n'est pas inventer.
--
-- Le critere est **exactement** celui de `history._late` : les deux heures sont
-- connues, et l'ecriture ne precede pas le coup d'envoi. Une selection sans
-- match rattache n'est pas tardive — son retard n'est pas plus demontre que son
-- anteriorite, et la regle du projet est a sens unique.
UPDATE picks
   SET tardive = 1
 WHERE event_id IS NOT NULL
   AND EXISTS (
         SELECT 1 FROM events e
          WHERE e.id = picks.event_id
            AND e.commence_time IS NOT NULL
            AND picks.created_at >= e.commence_time
       );

CREATE INDEX idx_picks_tardive ON picks(tardive);

-- **La somme des trois populations vaut le total**, et un test le garde :
-- principale, exploratoire (section C-bis, produite sans fait date) et tardive
-- ne se recouvrent pas. Melanger deux d'entre elles detruirait la comparaison
-- que chacune existe pour rendre possible — fait date contre lecture d'un cote,
-- prix d'avant-match contre prix ecrit en connaissant le debut du match de
-- l'autre.
