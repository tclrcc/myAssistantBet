-- 022_retrait_effectif.sql — l'effectif nominatif n'a plus de collecteur ni de lecteur.
--
-- `/players/squads` etait appele une fois par equipe et par mois, sa charge utile
-- reduite a une liste de noms, et **rien ne l'a jamais lue**. Le commentaire qui
-- l'accompagnait le disait sans detour : « collecte, jamais rendu... il sert a
-- rattacher un nom a un identifiant de joueur », et la phase 15 qui devait s'en
-- servir a finalement tire les identifiants de `KIND_SCORERS`.
--
-- Il annoncait lui-meme sa sortie : « si rien ne le lit a terme, il se retire en
-- supprimant son type ». C'est fait — le type, le resume, la methode du client,
-- ses simulations de test et ses fixtures sont partis avec.
--
-- Les lignes deja ecrites restent, elles, sans personne pour les relire : ni la
-- peremption ni la collecte ne repasseront dessus, puisque le type n'existe plus.
-- Elles se suppriment donc ici, une fois.

DELETE FROM team_context WHERE kind = 'squad';
