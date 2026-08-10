-- 032_cibles_relatives.sql — la cible d'un cran est un ecart, pas un taux.
--
-- Les bandes etaient des taux absolus : conf 5 >= 70 %, conf 4 entre 60 et 70,
-- et ainsi de suite. **Rapprochees des paliers, elles recouplent la confiance et
-- la cote**, ce que tout le reste du projet s'emploie a separer.
--
-- Les bandes de cote, exprimees en taux de reussite d'equilibre (l'inverse de la
-- cote), donnent la mesure du probleme :
--
--     SAFE       1.25 – 1.70   ->  80 % a 59 %
--     FUN        1.70 – 2.30   ->  59 % a 43 %
--     ULTRA FUN  2.30 – 3.60   ->  43 % a 28 %
--     GIGA FUN   3.60 – 8.00   ->  28 % a 12,5 %
--
-- Une selection GIGA FUN a 4.00 qui gagne 30 % du temps est un bon pari, et elle
-- tire son cran quarante points sous une bande a 70 %. Pour tenir 70 %, **conf 5
-- doit devenir quasi exclusivement du SAFE** — et le mecanisme de retour
-- d'experience, qui ordonne de resserrer un cran employe trop largement, pousse
-- alors mecaniquement toute selection a cote haute vers le bas de l'echelle.
-- C'est la derive vers le SAFE deja identifiee sur la boucle de feedback,
-- reinstallee un etage plus haut, et cette fois par un reglage.
--
-- Les bornes deviennent donc des **ecarts en points par rapport au taux global**
-- des selections tranchees, sur la meme fenetre glissante que le reste du bloc.
-- Ce que le mecanisme mesure alors est la **monotonie** de la notation — un cran
-- superieur bat-il le cran inferieur — et non l'atteinte d'un chiffre qui depend
-- du melange de paliers du mois.
--
-- **On repart des defauts plutot que de convertir**, et la conversion a ete
-- essayee avant d'etre ecartee : au taux global constate de 47,1 %, la bande de
-- conf 3 devient `+2,9 -> +12,9` et celle de conf 5 `+22,9 et plus`. La
-- conversion **reproduit mecaniquement la derive** que cette migration
-- supprime, parce que l'echelle absolue de depart etait elle-meme calee sur une
-- hypothese SAFE. Les valeurs ci-dessous sont des decisions, pas un heritage.
--
-- Les crans 1 et 2 n'ont **pas de cible** (migration 031) : ils sont pines par
-- la source — `lecture` impose 1, une source de niveau 3-4 plafonne a 2 — et
-- aucun mouvement correctif n'y est un choix. Le cran 5, lui, garde sa borne
-- basse sans borne haute : la frontiere entre « un facteur dominant » et « deux
-- facteurs independants » est discretionnaire, et descendre ses marginales en
-- confiance 4 est une action reelle.

UPDATE confidence_bands SET low =  12.0, high = NULL WHERE level = 5;
UPDATE confidence_bands SET low =   3.0, high = 12.0 WHERE level = 4;
UPDATE confidence_bands SET low =  -6.0, high =  3.0 WHERE level = 3;
UPDATE confidence_bands SET low = NULL,  high = NULL WHERE level = 2;
UPDATE confidence_bands SET low = NULL,  high = NULL WHERE level = 1;
