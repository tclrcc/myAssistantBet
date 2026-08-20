-- 067_echeance_unite_de_mise.sql — dater un provisoire, pour qu'il le reste.
--
-- **Un « provisoire » non date devient permanent par oubli.** La valeur reste,
-- la raison de la revoir s'efface, et six mois plus tard personne ne sait plus
-- qu'elle attendait du volume.
--
-- L'unite de mise vaut 0,25 % de la bankroll depuis le 20/08/2026. Elle n'a pas
-- ete choisie : elle a ete mesuree sur le 90e centile des journees d'analyse,
-- section C seule, C-bis exclue. Mais sur **quatre journees** (17 au 20/08),
-- quand un centile defendable en demande une dizaine.
--
-- L'echeance entre donc au journal des mesures, la ou se lisent les changements
-- de cadre — c'est le seul endroit du produit qui porte des dates plutot que des
-- etats, donc le seul ou une echeance ne se perd pas.
--
-- **Le plafond, lui, ne se re-mesure pas** : 5 % par journee est un arbitrage de
-- l'utilisateur, pas une grandeur observee. Seule l'unite depend du volume.
--
-- Ecrite en migration et non a la main sur la base servie : une donnee seedee se
-- rejoue a l'identique sur une installation neuve, et une insertion manuelle
-- n'existerait que sur une machine. Meme regle que les seeds de niveau de
-- competition et de famille de marche.

INSERT INTO changelog_mesure (day, label, description, scope, created_at)
SELECT
  '2026-09-20',
  'échéance — re-mesurer l''unité de mise',
  'L''unité de répartition a été calibrée le 20/08/2026 sur le 90e centile de '
  || 'quatre journées d''analyse seulement (17 au 20/08), quand un centile '
  || 'défendable en demande une dizaine. Re-mesurer sur le régime accumulé depuis, '
  || 'section C seule, C-bis exclue. Si le P90 a bougé, l''unité bouge avec lui — '
  || 'le plafond de journée, lui, est un arbitrage et ne se re-mesure pas.',
  'restitution',
  '2026-08-20T18:00:00Z'
WHERE NOT EXISTS (
  SELECT 1 FROM changelog_mesure
   WHERE day = '2026-09-20' AND label = 'échéance — re-mesurer l''unité de mise'
);
