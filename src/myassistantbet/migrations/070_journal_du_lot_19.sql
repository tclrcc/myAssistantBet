-- 070_journal_du_lot_19.sql — dater les changements de cadre du lot 19,
-- et combler le trou que le lot precedent a laisse.
--
-- **Deux dates, et elles ne sont pas les memes.** Le lot 18 a modifie le gabarit
-- sur six de ses dix points et n'a laisse aucune ligne au journal : la derniere
-- entree de portee `gabarit` est du 20/08 a 09:05, quand ses commits vont de
-- 22:43 a 23:52 le meme soir. Tout decoupage qui traverserait cette soiree
-- serait donc aveugle a un changement de +405 tokens par prompt.
--
-- **Dater ce qui est date n'est pas inventer** — meme argument que le seed de la
-- migration 054 : la date d'activation de ces six changements se lit dans
-- l'historique des commits, qui existe. C'est ce qui la separe de `price_source`
-- ou du cran calcule, qui auraient demande de reconstituer une information
-- jamais ecrite.
INSERT INTO changelog_mesure (day, label, description, scope, created_at) VALUES
  ('2026-08-20', 'lot 18 — dix corrections tirées d''un prompt rendu',
   'Six changements de gabarit livrés le même soir, +405 tokens nets par prompt : « Ici » nomme les matchs dont la source ne dit pas l''issue et corrobore le tournoi ; « Tour » annonce le nombre de tours établis quand la phase est inconnue ; « Ecart » confronte l''efficacité au lieu du taux de premières balles ; les lignes en quart portent un marqueur et le handicap posable est rendu ; les exemples de format se bâtissent sur les repères du lot ; la fiche de priorité pèse la surface de marché.',
   'gabarit', '2026-08-20T21:52:00Z'),

-- Le lot 19 lui-meme. **Une ligne par portee touchee**, et la portee decide de
-- ce qu'un decoupage verra : `gabarit` deplace ce que le modele recoit,
-- `restitution` ne deplace rien et se journalise quand meme — c'est elle qui
-- explique qu'un chiffre ait *paru* changer un jour ou aucune donnee n'a bouge.
  ('2026-08-21', 'lot 19 — un palier présent garde son quota',
   'Un palier qu''une cote du lot atteint garde une borne d''au moins un : le prorata le déclarait présent et interdit sur les lots de quatre matchs ou moins — 6 prompts archivés, dont le dernier rendu. Un palier absent du lot cesse par ailleurs de consommer un dossier de recherche. La section C-bis nomme le palier haut que le budget interdit, +45 tokens sur les seuls lots concernés.',
   'gabarit', '2026-08-21T00:00:00Z'),
  ('2026-08-21', 'lot 19 — les réglages disent ce qu''ils décident',
   'Avertissement sous les consignes permanentes : aucune règle tirée de la page Statistiques. Les marchés déjà classés annoncent ce qu''ils portent au lieu d''un tiret. Le recul du gate est affiché à côté de ses deux seuils et sur la page Statistiques, et sa phrase dit laquelle des deux conditions bloque — elle rendait « Il manque . » une fois le recul atteint sous suspension. Distribution des crans et des paliers rendue session par session.',
   'restitution', '2026-08-21T00:00:00Z');

-- **Aucune ligne pour la bascule du retour d'experience**, et c'est le point de
-- ce lot. Elle ne se pose pas d'avance : elle s'ecrit toute seule au **premier
-- prompt qui transmet** (`changelog.note_feedback`), parce que ni la date de
-- livraison ni celle du franchissement de seuil ne decrivent le moment ou le
-- regime change. Une ligne posee ici daterait une bascule qui n'a pas eu lieu,
-- et ferait couper la population a une date ou rien n'a bouge — exactement ce
-- que la migration 062 refusait deja pour les lignes de service.
