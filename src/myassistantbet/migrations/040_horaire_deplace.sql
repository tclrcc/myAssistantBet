-- Le coup d'envoi precedent, quand il a bouge.
--
-- Le fait dominant d'une soiree d'orages a Cincinnati etait que les matchs
-- avaient ete repousses de cinq heures — 17:30 au releve de 12:42, 22:30 a
-- celui de 22:15. L'application possedait les deux relevés et n'en gardait
-- qu'un : `commence_time` est ecrase a chaque scan. L'information a du etre
-- retrouvee dans la presse.
--
-- Deux colonnes et non une : l'heure d'avant dit **de combien**, l'instant du
-- constat dit **quand nous l'avons vu**. Sans la seconde, un decalage vieux de
-- trois jours se lirait comme celui de ce matin.
ALTER TABLE events ADD COLUMN previous_commence_time TEXT;
ALTER TABLE events ADD COLUMN commence_shifted_at TEXT;
