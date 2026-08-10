-- 024_niveaux_football.sql — niveau des competitions de football.
--
-- La colonne `category` existe depuis la migration 013, mais elle n'etait
-- peuplee que sur le tennis. Consequence mesuree sur cent selections
-- tranchees : le regroupement « par niveau » portait exactement l'effectif du
-- tennis (41 lignes, toutes `masters_1000`), et les 59 selections football se
-- repartissaient sur douze championnats de une a six lignes chacun — donc sous
-- le seuil de lecture a **tous** les etages : trop fines par competition,
-- noyees ensemble sous « Football ».
--
-- Meme regle que le tennis et que la surface : **rien n'est deduit d'un libelle
-- a l'execution**. Ce seed est en revanche une decision humaine, cle par cle.
-- Ce qui n'y figure pas reste NULL et se renseigne depuis /competitions, ou la
-- liste « a classer » le reclame plutot que de le laisser disparaitre.
--
-- `AND category IS NULL` sur chaque ordre : une migration comble un manque,
-- elle n'ecrase jamais une saisie manuelle — meme regle que la table des ligues
-- API-Football.

-- Le top 5 europeen. Ce qui les separe des autres premieres divisions n'est pas
-- le niveau de jeu mais la densite de donnees publiques et l'etroitesse du
-- marche : ce sont les matchs sur lesquels une recherche exterieure rapporte le
-- plus, et ceux ou un angle sportif a le moins de chances d'etre inedit.
UPDATE competitions SET category = 'd1_top5'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_epl',
  'soccer_spain_la_liga',
  'soccer_italy_serie_a',
  'soccer_germany_bundesliga',
  'soccer_france_ligue_one'
);

-- Les autres premieres divisions europeennes.
UPDATE competitions SET category = 'd1_europe'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_austria_bundesliga',
  'soccer_belgium_first_div',
  'soccer_denmark_superliga',
  'soccer_finland_veikkausliiga',
  'soccer_germany_bundesliga_women',
  'soccer_greece_super_league',
  'soccer_league_of_ireland',
  'soccer_netherlands_eredivisie',
  'soccer_norway_eliteserien',
  'soccer_poland_ekstraklasa',
  'soccer_portugal_primeira_liga',
  'soccer_russia_premier_league',
  'soccer_spl',
  'soccer_sweden_allsvenskan',
  'soccer_switzerland_superleague',
  'soccer_turkey_super_league'
);

-- Premieres divisions hors d'Europe. Elles se jouent en annee civile pour
-- plusieurs d'entre elles, ce que la saison lue chez le fournisseur gere deja ;
-- ici, ce qui les rassemble est qu'aucune n'entre dans les reperes europeens.
UPDATE competitions SET category = 'd1_hors_europe'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_argentina_primera_division',
  'soccer_australia_aleague',
  'soccer_brazil_campeonato',
  'soccer_chile_campeonato',
  'soccer_china_superleague',
  'soccer_japan_j_league',
  'soccer_korea_kleague1',
  'soccer_mexico_ligamx',
  'soccer_saudi_arabia_pro_league',
  'soccer_usa_mls'
);

-- Deuxieme division **et en dessous** : League 1 et League 2 anglaises sont les
-- troisieme et quatrieme etages, la 3. Liga le troisieme. Leur donner chacun sa
-- cle creerait des niveaux qu'aucune selection ne peuple, et le libelle du
-- niveau annonce l'amalgame plutot que de laisser croire a un pur echelon 2.
UPDATE competitions SET category = 'd2'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_brazil_serie_b',
  'soccer_efl_champ',
  'soccer_england_league1',
  'soccer_england_league2',
  'soccer_france_ligue_two',
  'soccer_germany_bundesliga2',
  'soccer_germany_liga3',
  'soccer_italy_serie_b',
  'soccer_spain_segunda_division',
  'soccer_sweden_superettan'
);

-- Coupes nationales : tour a elimination directe, ecart de niveau frequent
-- entre les deux equipes, rotation d'effectif sur les premiers tours.
UPDATE competitions SET category = 'coupe_nationale'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_england_efl_cup',
  'soccer_fa_cup',
  'soccer_france_coupe_de_france',
  'soccer_germany_dfb_pokal',
  'soccer_italy_coppa_italia',
  'soccer_spain_copa_del_rey'
);

-- Coupes continentales. Les qualifications europeennes ne recoivent pas de
-- niveau distinct, et ce n'est pas un arbitrage mais une contrainte : The Odds
-- API sert les tours preliminaires et la phase de ligue **sous la meme cle**
-- pour l'Europa League comme pour la Conference League. Un niveau se pose sur
-- une cle, donc les separer est hors de portee ici.
UPDATE competitions SET category = 'coupe_continentale'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_concacaf_leagues_cup',
  'soccer_conmebol_copa_libertadores',
  'soccer_conmebol_copa_sudamericana',
  'soccer_fifa_club_world_cup',
  'soccer_uefa_champs_league',
  'soccer_uefa_champs_league_qualification',
  'soccer_uefa_champs_league_women',
  'soccer_uefa_europa_conference_league',
  'soccer_uefa_europa_league'
);

-- Selections nationales : ni club, ni continuite d'effectif, ni forme de
-- championnat. Tout ce que le bloc CONTEXTE sait dire d'une equipe de club y
-- vaut moins, et c'est ce qui justifie de ne pas les melanger au reste.
UPDATE competitions SET category = 'selection'
WHERE category IS NULL AND oddsapi_key IN (
  'soccer_africa_cup_of_nations',
  'soccer_concacaf_gold_cup',
  'soccer_conmebol_copa_america',
  'soccer_fifa_world_cup',
  'soccer_fifa_world_cup_qualifiers_europe',
  'soccer_fifa_world_cup_qualifiers_south_america',
  'soccer_fifa_world_cup_winner',
  'soccer_fifa_world_cup_womens',
  'soccer_uefa_euro_qualification',
  'soccer_uefa_european_championship',
  'soccer_uefa_nations_league'
);
