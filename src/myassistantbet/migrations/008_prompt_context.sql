-- 008_prompt_context.sql — ce qui nourrit le prompt en dehors des cotes.
--
-- Deux ajouts, relus a chaque generation de prompt et jamais preleves sur une
-- API : ils ne coutent aucun credit et ne peuvent pas tomber en panne.
--
--   * `competitions.notes` : la fiche d'une competition — format, phase,
--     enjeu, particularites. Saisie une fois, valable toute la saison, rendue
--     une seule fois par lot et non a chaque match.
--   * `preferences` : les consignes permanentes de l'utilisateur, celles qui
--     ne changent pas d'une session a l'autre et qu'il serait absurde de
--     retaper. Table cle/valeur : le contenu evolue, le schema non.

ALTER TABLE competitions ADD COLUMN notes TEXT;

CREATE TABLE preferences (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
