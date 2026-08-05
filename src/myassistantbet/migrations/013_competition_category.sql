-- 013_competition_category.sql — niveau d'une competition.
--
-- Un Grand Chelem se joue au meilleur des cinq manches chez les hommes, sur
-- deux semaines, avec un tableau de 128 ; un ATP 250 se joue en deux manches
-- gagnantes sur cinq jours avec un tableau de 28. Ce ne sont pas les memes
-- matchs, et un taux de reussite qui les melange ne decrit ni l'un ni l'autre.
--
-- Le niveau est donc porte par la competition, comme la surface, et suit le
-- meme principe : **rien n'est deduit d'un libelle de tournoi a l'execution**.
-- Le seed ci-dessous est en revanche une decision humaine, verifiee tournoi par
-- tournoi contre les calendriers ATP et WTA en cours — les cles The Odds API
-- designent chacune un tournoi identifie sans ambiguite. Ce qui n'y figure pas
-- reste NULL et se renseigne depuis /competitions.
--
-- `masters_1000` couvre les Masters 1000 de l'ATP et les WTA 1000 : c'est le
-- meme etage de la hierarchie, et le circuit se lit deja dans le libelle.

ALTER TABLE competitions ADD COLUMN category TEXT;
    -- grand_slam | finals | masters_1000 | level_500 | level_250
    -- | challenger | itf | NULL

UPDATE competitions SET category = 'grand_slam' WHERE oddsapi_key IN (
  'tennis_atp_aus_open_singles', 'tennis_atp_french_open',
  'tennis_atp_wimbledon',        'tennis_atp_us_open',
  'tennis_wta_aus_open_singles', 'tennis_wta_french_open',
  'tennis_wta_wimbledon',        'tennis_wta_us_open'
);

UPDATE competitions SET category = 'masters_1000' WHERE oddsapi_key IN (
  -- ATP Masters 1000 : les neuf du calendrier.
  'tennis_atp_indian_wells',   'tennis_atp_miami_open',
  'tennis_atp_monte_carlo_masters', 'tennis_atp_madrid_open',
  'tennis_atp_italian_open',   'tennis_atp_canadian_open',
  'tennis_atp_cincinnati_open', 'tennis_atp_shanghai_masters',
  'tennis_atp_paris_masters',
  -- WTA 1000 : les dix du calendrier.
  'tennis_wta_qatar_open',     'tennis_wta_dubai',
  'tennis_wta_indian_wells',   'tennis_wta_miami_open',
  'tennis_wta_madrid_open',    'tennis_wta_italian_open',
  'tennis_wta_canadian_open',  'tennis_wta_cincinnati_open',
  'tennis_wta_china_open',     'tennis_wta_wuhan_open'
);

UPDATE competitions SET category = 'level_500' WHERE oddsapi_key IN (
  -- ATP 500. Doha et Munich ont ete montes de 250 a 500 en 2025.
  'tennis_atp_dubai',          'tennis_atp_qatar_open',
  'tennis_atp_barcelona_open', 'tennis_atp_munich',
  'tennis_atp_hamburg_open',   'tennis_atp_queens_club_champ',
  'tennis_atp_halle_open',     'tennis_atp_washington_open',
  'tennis_atp_china_open',
  -- WTA 500.
  'tennis_wta_german_open',    'tennis_wta_charleston_open',
  'tennis_wta_stuttgart_open', 'tennis_wta_strasbourg',
  'tennis_wta_bad_homburg_open', 'tennis_wta_queens_club_champ',
  'tennis_wta_washington_open'
);
