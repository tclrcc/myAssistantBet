-- 057_journal_du_lot_4.sql — dater les changements du lot 4.
--
-- La regle posee a la migration 054 : **une ligne par lot et par portee**, parce
-- que la question posee au journal n'est jamais « qu'est-ce qui a change ce
-- jour-la » mais « qu'est-ce qui a change dans ce que je regarde ».
--
-- La garde de peremption est une ligne de portee `gabarit` **et** une ligne de
-- portee `ingestion`, et ce n'est pas une commodite : elle change ce que le
-- modele lit — un bloc peut desormais porter « SOURCE FIGEE » — et elle change
-- ce qui entre en base — une source qui stagne y ecrit une ligne. Les deux
-- effets se decoupent separement.
INSERT INTO changelog_mesure (day, label, description, scope, created_at) VALUES
  ('2026-08-17', 'lot 4 — garde de péremption',
   'La ligne « Fraicheur » porte une escalade calculée : rien sous 8 jours, « source non rafraichie » jusqu''à 21, « SOURCE FIGEE » au-delà.',
   'gabarit', '2026-08-17T15:00:00Z'),
  ('2026-08-17', 'lot 4 — garde de péremption',
   'Le contenu des sources est daté et plus seulement la tentative ; une stagnation de plus de 48 h écrit une ligne « source_figee ».',
   'ingestion', '2026-08-17T15:00:00Z');
