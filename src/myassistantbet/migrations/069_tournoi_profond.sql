-- 069_tournoi_profond.sql — le nom d'un tournoi chez le fournisseur de profils.
--
-- **Le lot precedent a refuse de rendre la moitie « ici » du palmares**, et le
-- refus etait juste : `tennis-data` ecrit « Western & Southern Financial Group
-- Women's Open », `matches-played` ecrit « Cincinnati Open - Cincinnati », et
-- rendre le rapprochement au juge annoncait « ici jamais joue » a quatre
-- joueuses qui y avaient toutes joue. Cette colonne porte la seconde graphie.
--
-- ## Le dimensionnement, avant d'ecrire une ligne
--
-- Releve du 20/08/2026 sur les 798 reponses `profile/matches-played` archivees,
-- 84 266 matchs de simple :
--
--   * cote fournisseur : **1 179 noms de tournoi distincts**, dont **143**
--     portent un niveau de tableau principal ATP/WTA ;
--   * cote projet : **43 competitions de tennis**, toutes rattachees a
--     `tennis-data`, dont **4** ont deja ete analysees.
--
-- La table se compte donc **par competition** et non par catalogue : on ne
-- demande jamais « quels tournois ce joueur a-t-il joues » mais « a-t-il joue
-- celui-ci ». Quarante-trois lignes, meme forme que `tennisdata_tournaments`.
--
-- ## Pourquoi une liste, et pas un nom
--
-- **Le nom seul n'est pas l'identite, et c'est mesure** : le fournisseur
-- renomme au sponsor et ne retro-corrige pas. Cincinnati vaut « Cincinnati
-- Open » depuis 2025 et « Western & Southern Open » avant — y compris
-- « Western & Southern Open - New York » pour l'edition 2020, deplacee. Le
-- Canadian Open porte **quatre** noms sur deux villes et deux langues
-- (« National Bank Open », « Omnium Banque Nationale », « Rogers Cup »,
-- « Coupe Rogers ») et alterne Toronto et Montreal chaque annee. Queen's en
-- porte cinq.
--
-- **Consequence pour qui voudrait un controle par la ville** : elle est dans le
-- nom, elle bouge d'une annee sur l'autre par calendrier et une fois par
-- pandemie. Ce n'est pas une cle.
--
-- ## Et pourquoi pas un identifiant
--
-- Cherche, mesure, absent — le lot precedent l'avait etabli pour `tournamentId`
-- et `link`, ce releve-ci l'etend aux six autres champs candidats. Sur les
-- 385 noms vus sur plusieurs annees, aucun champ n'est a la fois stable et sans
-- collision : `id` 0/385 stable, `reserveChar` 262/385 mais 27 valeurs
-- partagees par plusieurs tournois, `coord` 376/385 et **232** valeurs
-- partagees — deux tournois d'une meme ville ont les memes coordonnees.
--
-- Ce qui **se controle**, en revanche, et c'est ce que le fournisseur sert a
-- 100 % : le niveau (`tier`) et la surface (`court.name`). Ils se confrontent a
-- la taxonomie et a la surface deja saisies chez nous.

ALTER TABLE competitions ADD COLUMN matchesplayed_tournaments TEXT;

-- Les 43 lignes, verifiees une a une contre les 798 reponses archivees :
-- chaque nom y figure, avec un niveau et une surface compatibles avec ceux que
-- la competition declare. Rien n'est deduit d'un libelle.
UPDATE competitions SET matchesplayed_tournaments = CASE oddsapi_key
  WHEN 'tennis_atp_aus_open_singles'    THEN 'Australian Open - Melbourne'
  WHEN 'tennis_atp_barcelona_open'      THEN 'Barcelona Open Banc Sabadell - Barcelona'
  WHEN 'tennis_atp_canadian_open'       THEN 'National Bank Open - Toronto|National Bank Open - Montreal|Rogers Cup - Toronto|Rogers Cup - Montreal|Coupe Rogers - Montreal'
  WHEN 'tennis_atp_china_open'          THEN 'China Open - Beijing'
  WHEN 'tennis_atp_cincinnati_open'     THEN 'Cincinnati Open - Cincinnati|Western & Southern Open - Cincinnati|Western & Southern Open - New York'
  WHEN 'tennis_atp_dubai'               THEN 'Dubai Duty Free Tennis Championships - Dubai'
  WHEN 'tennis_atp_french_open'         THEN 'French Open - Paris'
  WHEN 'tennis_atp_halle_open'          THEN 'Terra Wortmann Open - Halle'
  WHEN 'tennis_atp_hamburg_open'        THEN 'Hamburg Open - Hamburg|Hamburg European Open - Hamburg'
  WHEN 'tennis_atp_indian_wells'        THEN 'BNP Paribas Open - Indian Wells'
  WHEN 'tennis_atp_italian_open'        THEN 'Internazionali BNL d''Italia - Rome'
  WHEN 'tennis_atp_madrid_open'         THEN 'Mutua Madrid Open - Madrid'
  WHEN 'tennis_atp_miami_open'          THEN 'Miami Open - Miami'
  WHEN 'tennis_atp_monte_carlo_masters' THEN 'Monte-Carlo Rolex Masters - Monte-Carlo'
  WHEN 'tennis_atp_munich'              THEN 'BMW Open - Munich'
  WHEN 'tennis_atp_paris_masters'       THEN 'Rolex Paris Masters - Paris'
  WHEN 'tennis_atp_qatar_open'          THEN 'Qatar ExxonMobil Open - Doha'
  WHEN 'tennis_atp_queens_club_champ'   THEN 'HSBC Championships - London|cinch Championships - London|Fever-Tree Championships - London'
  WHEN 'tennis_atp_shanghai_masters'    THEN 'Shanghai Rolex Masters - Shanghai'
  WHEN 'tennis_atp_us_open'             THEN 'U.S. Open - New York'
  WHEN 'tennis_atp_washington_open'     THEN 'Citi Open - Washington|Mubadala Citi DC Open - Washington|Mubadala DC Open - Washington'
  WHEN 'tennis_atp_wimbledon'           THEN 'Wimbledon - London'
  WHEN 'tennis_wta_aus_open_singles'    THEN 'Australian Open - Melbourne'
  WHEN 'tennis_wta_bad_homburg_open'    THEN 'Bad Homburg Open - Bad Homburg'
  WHEN 'tennis_wta_canadian_open'       THEN 'National Bank Open - Toronto|Omnium Banque Nationale - Montreal|Rogers Cup - Toronto|Rogers Cup - Montreal'
  WHEN 'tennis_wta_charleston_open'     THEN 'Credit One Charleston Open - Charleston|Volvo Car Open - Charleston|Family Circle Cup - Charleston'
  WHEN 'tennis_wta_china_open'          THEN 'China Open - Beijing'
  WHEN 'tennis_wta_cincinnati_open'     THEN 'Cincinnati Open - Cincinnati|Western & Southern Open - Cincinnati|Western & Southern Open - New York'
  WHEN 'tennis_wta_dubai'               THEN 'Dubai Duty Free Championships - Dubai'
  WHEN 'tennis_wta_french_open'         THEN 'French Open - Paris'
  WHEN 'tennis_wta_german_open'         THEN 'Berlin Tennis Open - Berlin|Berlin Ladies Open - Berlin|bett1open - Berlin|Betti Open - Berlin'
  WHEN 'tennis_wta_indian_wells'        THEN 'BNP Paribas Open - Indian Wells'
  WHEN 'tennis_wta_italian_open'        THEN 'Internazionali BNL d''Italia - Rome'
  WHEN 'tennis_wta_madrid_open'         THEN 'Mutua Madrid Open - Madrid'
  WHEN 'tennis_wta_miami_open'          THEN 'Miami Open - Miami'
  WHEN 'tennis_wta_qatar_open'          THEN 'Qatar TotalEnergies Open - Doha|Qatar Total Open - Doha'
  WHEN 'tennis_wta_queens_club_champ'   THEN 'The HSBC Championships - London|LTA London Championships - London'
  WHEN 'tennis_wta_strasbourg'          THEN 'Internationaux de Strasbourg - Strasbourg'
  WHEN 'tennis_wta_stuttgart_open'      THEN 'Porsche Tennis Grand Prix - Stuttgart'
  WHEN 'tennis_wta_us_open'             THEN 'U.S. Open - New York'
  WHEN 'tennis_wta_washington_open'     THEN 'Mubadala Citi DC Open - Washington|Mubadala DC Open - Washington|Citi Open - Washington'
  WHEN 'tennis_wta_wimbledon'           THEN 'Wimbledon - London'
  WHEN 'tennis_wta_wuhan_open'          THEN 'Wuhan Open - Wuhan|Wuhan Tennis Open - Wuhan'
END
WHERE oddsapi_key LIKE 'tennis\_%' ESCAPE '\';
