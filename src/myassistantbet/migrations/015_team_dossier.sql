-- 015_team_dossier.sql — dossier d'equipe, memorise par equipe et non par match.
--
-- La table `context` est indexee par evenement, ce qui convient a ce qui ne vaut
-- que pour une rencontre : les absents d'un match, une confrontation directe.
-- Le dossier d'une equipe ne change pas d'un match a l'autre : son entraineur
-- est le meme dans les deux affiches ou elle apparait cette semaine, et le meme
-- demain. Le stocker par evenement le ferait repayer autant de fois qu'elle joue.
--
-- `scope` distingue les releves d'un meme type qui portent sur des perimetres
-- differents : rien pour l'entraineur, une saison pour un historique de matchs.
-- Il fait partie de la cle naturelle, donc de la cle primaire — l'y ajouter plus
-- tard aurait demande de recreer la table.
--
-- `fetched_at` n'est pas decoratif : c'est lui qui porte la peremption. Une
-- donnee fraiche est relue sans un appel, une donnee perimee est rafraichie.
-- Sans lui, chaque enrichissement repaierait tout, et le cache par equipe
-- n'existerait que de nom.

CREATE TABLE team_context (
  team_id      INTEGER NOT NULL,      -- identifiant API-Football de l'equipe
  kind         TEXT    NOT NULL,      -- coach | ... (etendu par les phases suivantes)
  scope        TEXT    NOT NULL DEFAULT '',
  payload_json TEXT    NOT NULL,      -- charge utile brute, jamais interpretee ici
  fetched_at   TEXT    NOT NULL,      -- ISO 8601 UTC
  PRIMARY KEY (team_id, kind, scope)
);
