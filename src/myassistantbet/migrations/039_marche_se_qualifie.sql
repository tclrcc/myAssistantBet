-- Le marche « Se qualifie », et sa famille.
--
-- Vingt-quatre manches retour en une semaine, et le marche qui traduit le mieux
-- un tour a elimination directe n'existait **nulle part** : ni en cote, ni meme
-- en « Non servis ». C'est exactement l'angle mort que le prompt reserve a sa
-- section F — un marche qu'on ne peut ni jouer ni declarer absent.
--
-- The Odds API le sert (`to_qualify`, « team to qualify for the next round in
-- knockout events »). Il est demande sur les seules coupes : ailleurs il n'a
-- aucun sens, et le reclamer couterait un credit par match pour un constat vide.
--
-- **Une migration deja appliquee ne se modifie jamais**, d'ou ce fichier plutot
-- qu'une ligne ajoutee a la 027 : les installations qui l'ont deja jouee ne la
-- rejoueraient pas, et leur table resterait sans cette entree.
--
-- La famille est `issue` : « Se qualifie » et le 1N2 d'une manche retour
-- repondent a la meme question — qui gagne — sur deux perimetres, le tour et le
-- match. Les separer aurait coupe en deux un echantillon deja court.

INSERT OR IGNORE INTO market_families (market_key, family) VALUES
  ('se qualifie', 'issue');
