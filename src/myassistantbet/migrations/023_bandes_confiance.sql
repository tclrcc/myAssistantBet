-- Bande cible par niveau de confiance.
--
-- « Confiance 4 » n'est pas un pourcentage : sans referentiel, l'ecart entre la
-- confiance annoncee et le taux constate ne se mesurait contre rien, et la note
-- de la page affirmait pourtant que cet ecart « dit que la notation derive ».
-- La bande donne ce referentiel, et elle se regle **sans toucher au code** :
-- c'est une decision de l'utilisateur, pas une constante du projet.
--
-- Les bornes sont en **points de pourcentage** (0 a 100), c'est a dire dans
-- l'unite ou elles se saisissent. Les convertir a la lecture coute une seule
-- division, la ou stocker des fractions en aurait demande une a l'affichage et
-- une a la saisie — donc deux occasions de diverger.
--
-- `high` vaut NULL pour le dernier cran : « 70 % et plus » n'a pas de borne
-- haute, comme `tiers.max_price`.
CREATE TABLE confidence_bands (
  level INTEGER PRIMARY KEY,
  low REAL NOT NULL,
  high REAL
);

-- Valeurs de depart. Les crans 2 a 5 sont ceux du cahier des charges ; le cran
-- 1 en est la continuation naturelle vers le bas, aucune valeur n'ayant ete
-- donnee pour lui — il se corrige depuis les reglages comme les autres.
INSERT INTO confidence_bands (level, low, high) VALUES
  (1, 30.0, 40.0),
  (2, 40.0, 50.0),
  (3, 50.0, 60.0),
  (4, 60.0, 70.0),
  (5, 70.0, NULL);
