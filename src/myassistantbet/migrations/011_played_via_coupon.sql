-- 011_played_via_coupon.sql — « joue » veut desormais dire « pose chez le book ».
--
-- Jusqu'ici un pick etait marque joue des sa saisie, y compris quand il venait
-- de l'import du tableau de Claude et n'avait jamais ete pose. Les taux de
-- reussite melangeaient donc deux questions distinctes : ce que vaut l'analyse,
-- et ce que valent mes paris.
--
-- Depuis les coupons (migration 010), la reponse est nette : `played` ne passe
-- a vrai qu'au rattachement a un coupon. Cette migration aligne les lignes
-- existantes sur la nouvelle regle — un pick qu'aucun coupon ne reclame n'a
-- pas ete joue.
--
-- Les picks eux-memes ne sont pas touches : ils ont ete proposes et analyses,
-- et leur resultat reste enregistre. Seule leur qualite de « joue » change.

UPDATE picks SET played = 0 WHERE coupon_id IS NULL;
