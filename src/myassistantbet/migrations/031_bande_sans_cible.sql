-- 031_bande_sans_cible.sql — un cran peut n'avoir aucune cible.
--
-- Le validateur exigeait une borne basse sur chaque cran : vider les deux
-- bornes rendait « Confiance 5 : borne basse manquante ». Il n'existait donc
-- **aucune facon d'exprimer « ce cran n'a pas de cible »**, alors que cet etat
-- est le bon sur une partie de l'echelle.
--
-- **La partition n'est pas « les bords », c'est « discretionnaire contre
-- determine par la source ».** Une bande sert a declencher un mouvement : un
-- cran qui sous-performe se resserre — on descend ses marginales d'un cran — et
-- un cran qui surperforme se relache. Encore faut-il que le mouvement existe.
--
--   · les crans **1 et 2** sont pines par ce que la recherche a trouve :
--     `lecture` impose 1, une source de niveau 3-4 plafonne a 2. Descendre de 2
--     a 1 supposerait de nier un fait date ; monter de 2 a 3 exige une
--     meilleure source, pas une meilleure notation. Aucune des deux directions
--     n'est un choix, donc aucune bande ne peut y avoir prise ;
--   · les crans **3, 4 et 5** se distinguent par des criteres appreciables — un
--     facteur ou deux, un manque de la section A qui touche ou non le facteur.
--     C'est la, et seulement la, que le resserrement agit.
--
-- Le cran 5 **garde donc sa borne basse** : la frontiere entre « un facteur
-- dominant » et « deux facteurs independants » est discretionnaire, et le
-- descendre est une action reelle. Ce qui n'allait pas chez lui n'a jamais ete
-- d'avoir une cible, mais que cette cible soit absolue — ce que corrige la
-- migration suivante.
--
-- SQLite n'a pas d'`ALTER COLUMN` : la table se reconstruit. Cinq lignes, une
-- transaction, aucune donnee derivee — c'est le seul chemin et il est sur.
--
-- `low` et `high` tous deux NULL = pas de cible. `high` seul reste une **saisie
-- incomplete** et continue d'etre refuse : c'est le dernier cas d'erreur que le
-- validateur sache encore attraper, et une faute de frappe qui viderait une
-- borne doit se voir.

CREATE TABLE confidence_bands_new (
  level INTEGER PRIMARY KEY,
  low  REAL,   -- NULL avec `high` NULL : ce cran n'a pas de cible
  high REAL
);

INSERT INTO confidence_bands_new (level, low, high)
  SELECT level, low, high FROM confidence_bands;

DROP TABLE confidence_bands;

ALTER TABLE confidence_bands_new RENAME TO confidence_bands;
