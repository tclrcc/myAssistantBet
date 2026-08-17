-- 059_identite_des_joueurs.sql — la graphie de la source, resolue une fois.
--
-- **C'est le piege numero un de cette source, et le lot 4 l'a paye deux fois.**
-- L'API ecrit « Mccartney Kessler » quand la base ecrit « McCartney Kessler » :
-- une comparaison stricte a rendu « 0 point de service », un faux negatif de
-- notre rapprochement et non de la source. Et la graphie canonique est
-- « Prenom Nom » avec une espace, pas le CamelCase `DaniilMedvedev` que le
-- brief du lot 4 annoncait.
--
-- ## Ce que la sonde du 17/08/2026 ajoute, et qui change la regle
--
-- L'endpoint de recherche est **insensible a la casse en entree** — `mccartney
-- kessler` trouve `Mccartney Kessler` — mais il n'est **pas** tolerant aux
-- accents : `Karolína Muchová` rend une liste **vide** quand `Karolina Muchova`
-- repond. Le repli d'accents se fait donc **sur l'entree**, avant l'appel, et
-- non seulement sur les candidats rendus.
--
-- Deux joueuses de la base sont concernees aujourd'hui — « Anna Bondár » et
-- « Iva Jović » — et sans ce repli elles seraient restees introuvables sans
-- qu'aucune ligne ne dise pourquoi.
--
-- Le trait d'union, lui, est tolere en entree et **absent en sortie** : `Felix
-- Auger-Aliassime` trouve `Felix Auger Aliassime`. La graphie canonique se
-- **recopie de la reponse**, jamais ne se reconstruit — meme regle que le
-- libelle d'enjeu recopie tel quel d'API-Football.

CREATE TABLE player_alias (
  id            INTEGER PRIMARY KEY,
  -- La graphie **de notre base**, telle que The Odds API la sert. C'est la cle
  -- d'entree : on part toujours de ce qu'on a, jamais d'un nom reconstruit.
  local_name    TEXT NOT NULL,
  tour          TEXT NOT NULL,           -- atp | wta | itf
  -- La graphie **de la source**, recopiee de sa reponse. NULL = cherchee et non
  -- trouvee, ce qui n'est pas la meme chose que « jamais cherchee » : une ligne
  -- absente veut dire qu'aucune resolution n'a ete tentee.
  canonical     TEXT,
  -- L'identifiant numerique du joueur chez le fournisseur. **C'est le vrai
  -- identifiant**, et il ne se connait qu'a la premiere reponse `matches-played`
  -- — la recherche ne rend que des noms. Il est donc rempli apres coup, et sa
  -- presence dit qu'un releve a abouti.
  provider_id   INTEGER,
  -- Le niveau de repli qui a tranche : `exact`, `casse`, `accents`, ou
  -- `introuvable`. **Il se compte** : si `accents` devient majoritaire, la
  -- normalisation en amont est mauvaise et il faut le savoir plutot que de le
  -- deviner.
  fallback      TEXT NOT NULL,
  -- La reponse dont cette resolution sort. Sans elle on saurait qu'un nom a ete
  -- resolu et jamais sur quoi.
  response_id   INTEGER REFERENCES api_responses(id) ON DELETE SET NULL,
  resolved_at   TEXT NOT NULL,
  -- Une resolution vaut pour un couple (nom local, circuit) : le meme patronyme
  -- peut designer deux joueurs sur deux circuits.
  UNIQUE (local_name, tour)
);

CREATE INDEX idx_player_alias_canonical ON player_alias (canonical);

-- **Une non-resolution est enregistree, elle aussi.** C'est ce qui evite de
-- redemander tous les jours un nom que la source ne connait pas — un appel par
-- joueur et par passe, pour un constat qui ne bougera pas. Elle se distingue
-- d'une resolution reussie par `canonical IS NULL`, et le rejet correspondant
-- vit dans `ingestion_rejects` sous `match_ref_unresolved`.
