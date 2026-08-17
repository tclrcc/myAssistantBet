-- 060_agregats_de_service.sql — sommer les comptes, jamais moyenner des taux.
--
-- **C'est la regle qui fonde la table, et elle a une consequence de schema.**
-- Moyenner les pourcentages match par match donnerait le meme poids a un abandon
-- de trois jeux et a un cinq sets, et fausserait le profil des joueurs a
-- abandons — ceux que la ligne « Abandons » du bloc signale deja. L'API sert les
-- **denominateurs** precisement pour rendre la sommation possible ; ne pas s'en
-- servir reviendrait a heriter de ses choix d'agregation sans les voir.
--
-- Les numerateurs et les denominateurs sont donc stockes **separement**, et
-- aucun taux n'est ecrit en base. Un taux se calcule a la lecture, ce qui a deux
-- effets : deux fenetres se recomposent par addition, et un taux mal defini ne
-- peut pas se figer dans l'historique.
--
-- ## Un denominateur par indicateur, et ils different
--
--   % 1re balle              Σ firstServe            / Σ firstServeOf
--   % points gagnes sur 1re  Σ winningOnFirstServe   / Σ winningOnFirstServeOf
--   % points gagnes sur 2e   Σ winningOnSecondServe  / Σ winningOnSecondServeOf
--   taux d'aces              Σ aces                  / Σ firstServeOf
--   taux de doubles fautes   Σ doubleFaults          / (Σ firstServeOf − Σ firstServe)
--   % BP converties          Σ breakPointsConverted  / Σ breakPointsConvertedOf
--
-- Les aces se rapportent aux **points de service** et jamais aux matchs, sinon
-- on mesure la longueur des rencontres. Les doubles fautes se rapportent aux
-- **secondes balles** : un joueur qui rentre 75 % de premieres a mecaniquement
-- moins d'occasions d'en commettre.
--
-- ## Ce qui n'est pas stocke, et ne doit pas l'etre
--
-- Aucun ajustement au niveau d'adversaire, aucune probabilite de tenue projetee,
-- aucun total de jeux attendu, aucune projection de score. Un nombre de jeux
-- attendu pose a cote d'une ligne Jeux O/U est un calcul d'esperance a une
-- soustraction pres — exactement ce que la section 9 de SPEC.md interdit.
-- L'ecart entre deux joueurs est une soustraction entre faits observes et reste
-- autorise ; la prediction ne l'est pas.

CREATE TABLE player_serve_agg (
  id            INTEGER PRIMARY KEY,
  -- La graphie **canonique** du fournisseur, celle que `player_alias.canonical`
  -- a validee sur le contenu. Pas notre graphie locale : deux noms locaux
  -- peuvent designer le meme profil, et l'agregat appartient au profil.
  player        TEXT NOT NULL,
  circuit       TEXT NOT NULL,          -- atp | wta
  -- '' = toutes surfaces. Sinon le libelle du fournisseur, recopie tel quel
  -- (`Hard`, `Clay`, `Grass`) — jamais traduit : il vient de la source, et le
  -- reecrire serait s'en porter garant.
  surface       TEXT NOT NULL DEFAULT '',
  -- La fenetre temporelle. **Une seule est servie, et c'est un arbitrage** : le
  -- brief demandait `52w` et `12m_dur`, or 52 semaines et 12 mois designent la
  -- meme periode a cinq jours pres. En stocker deux serait stocker deux fois la
  -- meme donnee ; la seconde fenetre du brief est donc la **cellule**
  -- (window='52w', surface='Hard'), qui porte exactement ce qu'elle demandait.
  window        TEXT NOT NULL DEFAULT '52w',

  -- Les comptes, sommes. Aucun taux.
  matches       INTEGER NOT NULL DEFAULT 0,
  first_serve   INTEGER NOT NULL DEFAULT 0,
  first_serve_of INTEGER NOT NULL DEFAULT 0,
  aces          INTEGER NOT NULL DEFAULT 0,
  double_faults INTEGER NOT NULL DEFAULT 0,
  won_first     INTEGER NOT NULL DEFAULT 0,
  won_first_of  INTEGER NOT NULL DEFAULT 0,
  won_second    INTEGER NOT NULL DEFAULT 0,
  won_second_of INTEGER NOT NULL DEFAULT 0,
  bp_converted  INTEGER NOT NULL DEFAULT 0,
  bp_converted_of INTEGER NOT NULL DEFAULT 0,
  -- Le retour, derive des colonnes **adverses** de la meme reponse : un point de
  -- retour est un point de service adverse que l'adversaire n'a pas gagne. Aucun
  -- second appel — le brief demandait de l'etablir, et c'est mesure.
  return_points INTEGER NOT NULL DEFAULT 0,
  return_won    INTEGER NOT NULL DEFAULT 0,

  -- Les jeux, derives de la **timeline** et non de `matches-played`. Ils sont
  -- donc peuples separement, et souvent sur moins de matchs : la couverture de
  -- la timeline est partielle, trois rencontres sur huit restant muettes.
  games_matches INTEGER NOT NULL DEFAULT 0,
  served        INTEGER NOT NULL DEFAULT 0,
  held          INTEGER NOT NULL DEFAULT 0,
  returned      INTEGER NOT NULL DEFAULT 0,
  broke         INTEGER NOT NULL DEFAULT 0,

  -- **La date d'arret, toujours ecrite.** Sans elle une donnee vieille de six
  -- jours se lirait comme actuelle — le defaut que `Fraicheur` existe pour
  -- corriger ailleurs. C'est le dernier match **compte**, pas la date du calcul.
  as_of         TEXT NOT NULL,
  computed_at   TEXT NOT NULL,
  -- La reponse `matches-played` dont sort la part service. Les timelines en
  -- ont d'autres ; celle-ci est la principale, et c'est elle qui permet de
  -- remonter d'un agregat a une charge utile.
  response_id   INTEGER REFERENCES api_responses(id) ON DELETE SET NULL,

  UNIQUE (player, circuit, surface, window)
);

CREATE INDEX idx_serve_agg_player ON player_serve_agg (player, circuit);

-- **`as_of` n'est pas dans la cle d'unicite, contrairement a ce que le brief
-- propose.** Une cle qui le porterait ferait une ligne par jour de calcul et par
-- joueur : la table grossirait sans fin pour un historique que personne ne relit,
-- et la lecture devrait a chaque fois chercher le maximum. L'agregat est un
-- **etat courant** ; ce qui doit survivre est la charge utile, et elle vit dans
-- `api_responses`, qui elle ne se purge jamais.
