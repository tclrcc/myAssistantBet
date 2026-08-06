-- 019_tennis_history_state.sql — ce qui a ete telecharge, et quand.
--
-- La peremption de `tennis_matches` etait deduite du `MAX(fetched_at)` de ses
-- lignes, comme celle de `tennis_elo`. Le raisonnement tombe des qu'une saison ne
-- ramene aucun match : sans ligne, pas de date, donc « jamais telecharge », donc
-- retelechargee a chaque enrichissement — sans fin et sans que rien ne le dise.
--
-- Et ce cas n'est pas theorique : en janvier, le fichier de la saison qui
-- commence est vide ou absent. Une collecte doit pouvoir se souvenir d'un
-- resultat vide, sinon elle le redemande indefiniment.
--
-- Cette table date donc la **collecte** et non les donnees, ce qui est la seule
-- chose qui repond a « faut-il redemander ? ». `matches` sert au diagnostic : une
-- saison a zero match, c'est soit un debut d'annee, soit une source qui a change.

CREATE TABLE tennis_history_state (
  tour       TEXT    NOT NULL,        -- atp | wta
  season     INTEGER NOT NULL,
  matches    INTEGER NOT NULL,        -- 0 est un resultat, pas une absence
  fetched_at TEXT    NOT NULL,        -- ISO 8601 UTC
  PRIMARY KEY (tour, season)
);
