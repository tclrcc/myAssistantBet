-- 012_competition_api_state.sql — savoir si l'API sert encore une competition.
--
-- `GET /sports` ne liste par defaut que les competitions dont le fournisseur a
-- des cotes en ce moment. La synchronisation ne decouvrait donc que celles-la,
-- et une competition qu'on voulait activer d'avance — une phase de
-- qualification europeenne, un tournoi qui commence dans trois jours — restait
-- introuvable jusqu'a ce que l'API la serve, c'est a dire souvent trop tard.
--
-- Le catalogue complet se demande avec `all=true`, gratuitement. Reste a
-- distinguer les deux etats, sinon une competition active qui ne ramene rien
-- devient un mystere : `api_active` dit si le fournisseur la sert aujourd'hui.
--
-- Valeur par defaut a 1 : l'existant a ete decouvert par une synchronisation
-- qui ne listait que l'actif. La prochaine synchronisation remettra chacune a
-- sa vraie valeur.

ALTER TABLE competitions ADD COLUMN api_active INTEGER NOT NULL DEFAULT 1;
