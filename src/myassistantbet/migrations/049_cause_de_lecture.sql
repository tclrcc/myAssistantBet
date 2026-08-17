-- 049_cause_de_lecture.sql — un cran 1 force porte sa cause.
--
-- **Quatrieme occurrence du meme defaut : une sortie identique pour l'echec et
-- pour le cas ordinaire.** La session 11 porte 16 selections a
-- `research_overridden = 1`, donc toutes ecrasees en lecture, cran 1. Lues
-- telles quelles, elles disent « aucune selection ne portait sur un dossier
-- ouvert » — une observation sur le modele. Elles disent en realite que la
-- ligne `dossiers_ouverts` n'a jamais ete collee : `open_dossiers_state` vaut
-- `absente`. Ce n'est pas la meme chose et ca n'appelle pas le meme geste.
--
-- Trois causes de collecte et trois observations reelles produisent aujourd'hui
-- le meme `1`, donc alimentent la meme statistique. Le corriger au cas par cas
-- une quatrieme fois ne l'empecherait pas de revenir une cinquieme : c'est la
-- cause qui se persiste, pas le symptome qui se rattrape.
--
--   · `hors_dossiers`       — la liste est renseignee, ce match n'y est pas
--   · `aucun_dossier`       — la liste est lue et vide : rien n'a ete ouvert
--   · `sans_fait`           — dossier ouvert, aucun fait date n'en est tire
--   · `ligne_absente`       — la ligne n'a pas ete collee
--   · `ligne_illisible`     — la cle est la, sa valeur ne se relit pas
--   · `reperes_non_resolus` — la liste ne se resout contre aucun prompt
--
-- Les trois premieres sont des observations : elles decrivent ce que l'analyse
-- a fait. Les trois dernieres sont des defauts de collecte : elles decrivent ce
-- que le collage a perdu, et se reparent en recollant.
ALTER TABLE picks ADD COLUMN research_override_cause TEXT;

-- **Le retro-remplissage est sur, et c'est la base qui le prouve** — pas une
-- reconstitution. `sessions.open_dossiers_state` est persiste depuis la
-- migration 045 : une session dont la ligne etait `absente` ne pouvait ecraser
-- ses selections pour aucune autre raison, l'ensemble des reperes resolus y
-- etant vide par construction. La regle vaut pour la seule cause qui se prouve
-- ainsi ; les autres ne se devinent pas et restent NULL.
UPDATE picks
   SET research_override_cause = 'ligne_absente'
 WHERE research_overridden = 1
   AND session_id IN (SELECT id FROM sessions WHERE open_dossiers_state = 'absente');

UPDATE picks
   SET research_override_cause = 'ligne_illisible'
 WHERE research_overridden = 1
   AND session_id IN (SELECT id FROM sessions WHERE open_dossiers_state = 'illisible');

-- **`lue` confondait la ligne renseignee et la ligne vide**, et c'est la moitie
-- du defaut. `dossiers_ouverts: []` est une declaration legitime — le modele
-- n'a rien ouvert, et le gabarit l'autorise ; une ligne absente est un collage
-- rate. Les deux forcaient tout le lot en lecture sous le meme etat, donc le
-- taux de « rien ouvert » melangeait une reponse du modele et une panne de
-- transmission.
--
-- L'etat se lit sur `open_dossiers`, qui porte les reperes tels que colles :
-- vide veut dire vide. Idempotent — une fois separes, plus rien ne vaut `lue`.
UPDATE sessions
   SET open_dossiers_state = 'vide'
 WHERE open_dossiers_state = 'lue'
   AND COALESCE(TRIM(open_dossiers), '') = '';

UPDATE sessions
   SET open_dossiers_state = 'renseignee'
 WHERE open_dossiers_state = 'lue';
