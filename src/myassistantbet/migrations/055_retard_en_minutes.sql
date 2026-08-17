-- 055_retard_en_minutes.sql — de combien une selection tardive l'est.
--
-- **Le decoupage change la nature de la reponse, et c'est pour ca qu'il passe.**
-- L'ecart de residu entre population principale et tardive — +0,145 par
-- selection — est le resultat le plus interessant de la page, et il est rendu
-- comme un bloc unique. Or une selection ecrite quatre minutes apres le coup
-- d'envoi et une ecrite quatre-vingt-dix minutes apres ne decrivent pas la meme
-- chose : la premiere peut n'etre qu'un retard d'import, la seconde suppose de
-- connaitre le deroulement du match.
--
-- Les deux reponses possibles sont utiles, et c'est ce qui rend la mesure
-- interessante plutot qu'esperee :
--
--   · si le residu **croit avec le retard**, la contamination est demontree et
--     cesse d'etre une hypothese de lecture ;
--   · s'il est **plat**, la population tardive est un artefact d'import et peut
--     etre traitee plus legerement.
--
-- L'absence de stratification, elle, n'en donne aucune.

ALTER TABLE picks ADD COLUMN late_minutes INTEGER;

-- **La colonne se stocke, et il faut savoir pourquoi** : le projet derive
-- plutot que de recopier, et il y a ici une bonne raison de faire l'inverse —
-- la meme que pour `tardive`. Une population qui se deduirait a chaque lecture
-- finirait par ne plus designer les memes lignes que le compte affiche a cote,
-- et un retard derive a la lecture sortirait d'un `commence_time` **courant**,
-- donc d'un horaire qui a pu bouger depuis.
--
-- La contrepartie est tenue : `history._LATE_RULE` ecrit les **deux** colonnes
-- dans le meme UPDATE, et le scan la rejoue des qu'un coup d'envoi bouge. Deux
-- regles paralleles auraient fini par ne plus dire la meme chose du meme match,
-- et ce serait ici particulierement couteux — un match reporte a un retard qui
-- n'existe pas.
--
-- **Le retro-remplissage est sur** : la valeur se derive de `created_at` et
-- `commence_time`, deja en base. Deriver n'est pas inventer, et c'est le meme
-- argument qu'a la migration 053.
--
-- `ROUND` et non troncature : le retard sert a ranger dans des bandes de quinze
-- minutes et plus, ou une demi-minute ne decide de rien, et l'arrondi au plus
-- proche se lit comme la phrase le dit.
UPDATE picks
   SET late_minutes = (
         SELECT CAST(ROUND((julianday(picks.created_at) - julianday(e.commence_time)) * 1440)
                     AS INTEGER)
           FROM events e
          WHERE e.id = picks.event_id
       )
 WHERE tardive = 1
   AND event_id IS NOT NULL;

-- **NULL sur tout ce qui n'est pas tardif**, et c'est un etat a part entiere :
-- une selection anterieure n'a pas un retard de zero, elle n'en a pas. Ecrire 0
-- la ferait entrer dans la premiere bande et gonflerait de 178 lignes une
-- population qui en porte 52 — le genre de defaut qui ne casse rien et se lit
-- comme une mesure.
CREATE INDEX idx_picks_late_minutes ON picks(late_minutes);
