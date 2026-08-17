-- 054_dater_les_changements.sql — savoir quel changement a produit quel effet.
--
-- **Trois lots ont modifie ce qui est produit et ce qui est mesure en une
-- journee, et un quatrieme arrive.** Dans trois semaines il sera impossible de
-- dire lequel a deplace quoi : les selections portent leur date, les
-- changements de cadre non.
--
-- C'est le point de ce lot qui rapportera le plus dans un mois, et le moins
-- visible aujourd'hui — meme forme que la migration 033, qui n'affichait rien
-- et arretait une perte.
--
-- Ce que ca rend possible et qui ne l'est pas aujourd'hui : decouper n'importe
-- quel indicateur avant / apres une ligne du journal. Sans ce decoupage, l'effet
-- d'un ajout au gabarit se melange a celui d'un correctif d'ingestion livre le
-- meme jour, et les deux se lisent comme un seul mouvement.

-- 1. `sessions.gabarit_version` / `sessions.gabarit_sha` — sous quel cadre une
--    session a ete rendue.
--
--    **Deux colonnes et non une, parce qu'elles repondent a deux questions.**
--    L'empreinte dit *le gabarit a-t-il change* — elle se calcule, ne se
--    trompe pas, et bouge sur une virgule ; le libelle dit *quel changement* —
--    il s'incremente a la main, donc il nomme une decision. Les fondre en une
--    seule valeur obligerait a la reparser pour en tirer l'une des deux, et
--    reparser une colonne qu'on a soi-meme ecrite est exactement ce que ce
--    projet refuse partout ailleurs.
--
--    `COALESCE` les fige au **premier prompt**, comme `scale_version` : changer
--    de gabarit en cours de session ne doit pas reetiqueter ce qui a deja ete
--    rendu sous l'ancien.
--
--    **Rien n'est retro-rempli**, et c'est la meme regle que partout : le
--    gabarit d'hier n'existe nulle part, seul son rendu est archive. Une
--    empreinte reconstituee depuis le fichier courant dirait que les quinze
--    sessions passees ont ete rendues sous le gabarit d'aujourd'hui, ce qui est
--    faux et se lirait comme une mesure.
ALTER TABLE sessions ADD COLUMN gabarit_version TEXT;  -- libelle lisible, a la main
ALTER TABLE sessions ADD COLUMN gabarit_sha     TEXT;  -- empreinte du gabarit rendu

-- 2. `changelog_mesure` — les changements de cadre, dates.
--
--    **Plusieurs lignes par lot sont permises, une par portee touchee**, et ce
--    n'est pas une commodite. La question qu'on pose au journal n'est jamais
--    « qu'est-ce qui a change ce jour-la » mais « qu'est-ce qui a change *dans
--    ce que je regarde* » : un lot qui touche a la fois le gabarit et
--    l'ingestion produit deux reponses differentes selon l'indicateur decoupe,
--    et une ligne unique forcerait le lecteur a re-deriver laquelle.
--
--    Les trois portees se distinguent par ce qu'elles deplacent :
--
--      · `gabarit`     — ce que le modele recoit, donc ce qu'il produit ;
--      · `ingestion`   — ce qui entre en base a production constante ;
--      · `restitution` — ce que la page montre, sans qu'aucune donnee bouge.
--
--    La derniere ne peut pas deplacer un indicateur et se journalise quand
--    meme : c'est elle qui explique qu'un chiffre ait *paru* changer.
CREATE TABLE changelog_mesure (
  id          INTEGER PRIMARY KEY,
  day         TEXT NOT NULL,     -- date du changement, ISO, jour seul
  label       TEXT NOT NULL,     -- « lot 2 — rendre les erreurs rattrapables »
  description TEXT NOT NULL DEFAULT '',
  scope       TEXT NOT NULL,     -- gabarit | ingestion | restitution
  created_at  TEXT NOT NULL
);

CREATE INDEX idx_changelog_day ON changelog_mesure(day);

-- **Le seed est retroactif, et il est sur** : il se lit dans l'historique des
-- commits, qui existe. C'est la difference avec `price_source` (030) ou le cran
-- calcule (042), qui auraient demande de reconstituer une information jamais
-- ecrite. Dater ce qui est date n'est pas inventer.
--
-- Les lots 1 et 2 tombent le meme jour — tous leurs commits sont du 17/08/2026 —
-- donc ils ne fournissent qu'un seul point de coupe. C'est un fait sur le
-- rythme de livraison, pas un defaut du journal : deux changements livres dans
-- la meme journee ne se separeront jamais par la date, et le journal doit le
-- montrer plutot que de le masquer par des dates inventees.
INSERT INTO changelog_mesure (day, label, description, scope, created_at) VALUES
  ('2026-08-15', 'lot 0 — état antérieur',
   'Dernier prompt rendu avant les trois lots : sessions 1 à 14. Rien de ce qui suit ne s''y applique.',
   'gabarit', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 1 — l''ingestion et les paliers hauts',
   'Section C-bis ajoutée au gabarit : un second circuit pour les paliers hauts, sans exigence de fait daté.',
   'gabarit', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 1 — l''ingestion et les paliers hauts',
   'Journalisation des rejets, lecture des blocs conf sans clôture, lecture des scores en sets.',
   'ingestion', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 1 — l''ingestion et les paliers hauts',
   'Quarantaine des cotes de référence levée, paliers instrumentés, doublons nommés, interrupteur coupons.',
   'restitution', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 2 — rendre les erreurs rattrapables',
   'Ligne « sets: » demandée à plat plutôt qu''en bloc clôturé.',
   'gabarit', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 2 — rendre les erreurs rattrapables',
   'Collage brut persisté avec ses bornes de position, commande de rejeu, banc de transport, selfcheck.',
   'ingestion', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 2 — rendre les erreurs rattrapables',
   'Population tardive isolée et mesurée à part : écart de résidu de +0,145 par sélection.',
   'restitution', '2026-08-17T00:00:00Z'),
  ('2026-08-17', 'lot 3 — consolider la mesure',
   'Registre des chemins d''écriture, sections attendues et non trouvées, journal des changements de cadre.',
   'ingestion', '2026-08-17T00:00:00Z');
