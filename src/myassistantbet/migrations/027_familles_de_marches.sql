-- 027_familles_de_marches.sql — regrouper les libelles qui disent le meme pari.
--
-- Mesure sur cent selections : **neuf regroupements de marches, dont six vus une
-- seule fois**. Chacun mesurait le hasard, et le bloc entier ne se lisait pas.
-- Or `O/U` et `O/U 2.5` sont le meme pari a une ligne pres, `Vainqueur` et
-- `1N2` designent la meme chose sur deux sports, `Handicap` et `Hand. jeux`
-- aussi. Eclates, aucun n'atteint un effectif lisible ; groupes, trois familles
-- passent le seuil de lecture — issue 49, handicap 23, total 21.
--
-- La cle est la **cle de famille** : le libelle normalise, moins sa valeur de
-- ligne finale (`family_key`). Une ligne est un parametre du marche et non un
-- autre marche ; sans cette regle, chaque seuil rencontre reclamerait sa propre
-- correspondance et la liste « a classer » ne desemplirait jamais.
--
-- Rien n'est deduit d'un libelle, comme pour le niveau d'une competition : cette
-- table est une decision humaine, cle par cle, verifiee contre le vocabulaire de
-- `render.MARKET_ORDER_BY_SPORT` — celui que le prompt met sous les yeux de
-- l'analyse — et contre les libelles reellement presents en base.
--
-- `services/market_families.py` porte la meme table en Python, pour classer ce
-- qui arrive apres cette migration. Un test compare les deux ecritures.

CREATE TABLE market_families (
  market_key TEXT PRIMARY KEY,
  family     TEXT NOT NULL   -- issue | handicap | total | equipe | autre
);

-- Issue : sur qui gagne.
INSERT INTO market_families (market_key, family) VALUES
  ('1n2', 'issue'),
  ('dc', 'issue'),
  ('double chance', 'issue'),
  ('vainqueur', 'issue'),
  ('set', 'issue'),
  ('mt ft', 'issue'),
  ('corners 1n2', 'issue'),
  ('podium', 'issue');

-- Handicap : buts au football, jeux au tennis. Meme forme de pari, et c'est ce
-- que la famille regroupe.
INSERT INTO market_families (market_key, family) VALUES
  ('handicap', 'handicap'),
  ('hand jeux', 'handicap'),
  ('hand s1', 'handicap');

-- Total : combien, toutes equipes confondues.
INSERT INTO market_families (market_key, family) VALUES
  ('o u', 'total'),
  ('jeux o u', 'total'),
  ('mt o u', 'total'),
  ('jeux s1', 'total'),
  ('total buts', 'total'),
  ('nombre total de buts t reg', 'total');

-- Par equipe. Un total d'equipe est un total par la forme, et il est ici parce
-- que son sujet change tout : « plus de 1.5 but pour Lyon » et « plus de 2.5
-- buts dans le match » ne se gagnent pas dans les memes scenarios.
INSERT INTO market_families (market_key, family) VALUES
  ('btts', 'equipe'),
  ('btts mt', 'equipe'),
  ('eq buts', 'equipe'),
  ('les 2 equipes marquent t reg', 'equipe');

-- Autre : range ici **par decision**, jamais par defaut. Corners et cartons
-- sont des totaux d'une autre grandeur, et les melanger aux buts ferait decrire
-- deux choses par un seul taux. Les props buteurs sont des marches de joueur.
-- « Cotes » est le libelle libre de la saisie manuelle : il peut recouvrir
-- n'importe quoi, ce qui est exactement la raison de ne rien en deduire.
INSERT INTO market_families (market_key, family) VALUES
  ('score exact', 'autre'),
  ('score ex mt', 'autre'),
  ('corners', 'autre'),
  ('cartons', 'autre'),
  ('buteur', 'autre'),
  ('1er buteur', 'autre'),
  ('cotes', 'autre');
