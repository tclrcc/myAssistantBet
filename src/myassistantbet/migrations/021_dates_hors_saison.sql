-- 021_dates_hors_saison.sql — purge des matchs dont la date ne peut pas etre celle
-- de leur saison.
--
-- La source publie de temps a autre une date fausse. Releve en reel : le fichier
-- de la saison 2026 datait la finale de l'Iasi Open du **20 juillet 2029**, ce
-- qui ne peut etre qu'une coquille de frappe pour 2026.
--
-- Le degat n'est pas visible, et c'est ce qui le rend genant. Une date posterieure
-- a tout match analyse sort de **chaque** fenetre de lecture — la forme, le bilan
-- de surface, les confrontations directes filtrent toutes sur `played_on < debut
-- du match`. Le match ne s'affiche donc jamais nulle part : il disparait de
-- l'historique des deux joueuses sans qu'aucune ligne ne signale un trou.
--
-- La regle de validite n'est **pas** « l'annee de la date egale la saison ». La
-- saison de tennis ouvre dans les tout derniers jours de decembre : le fichier
-- 2025 porte 69 matchs joues du 29 au 31 decembre 2024, et celui de 2024 onze
-- matchs du 31 decembre 2023. Le garde-fou evident les jetterait tous. Une date
-- appartient donc a sa saison si elle tombe dans l'annee de la saison, ou en
-- decembre de l'annee precedente.
--
-- `tennis_history.store()` applique desormais la meme regle a la collecte : cette
-- migration ne nettoie que ce qui a ete ecrit avant elle.

DELETE FROM tennis_matches
WHERE CAST(substr(played_on, 1, 4) AS INTEGER) <> season
  AND NOT (
    CAST(substr(played_on, 1, 4) AS INTEGER) = season - 1
    AND substr(played_on, 6, 2) = '12'
  );
