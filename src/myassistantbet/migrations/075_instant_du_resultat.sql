-- 075_instant_du_resultat.sql — la borne haute de la fenetre pre-resultat, et
-- le cadre sous lequel une selection a ete produite.
--
-- ## `result_at` n'est pas une colonne de provenance
--
-- **C'est le garde d'anteriorite de la boucle de relecture.** `picks.created_at`
-- borne le debut de la fenetre — la selection a ete posee avant le coup
-- d'envoi, donc son prix est un prix d'avant-match ; il manquait la borne
-- **haute**, l'instant ou l'issue est devenue connue de l'application. Sans
-- elle, aucun bilan ne peut prouver qu'un fait qu'il invoque a ete releve avant
-- que le resultat soit su, c'est-a-dire qu'il ne retrospecte pas.
--
-- Mesure du 21/08/2026 : `picks` ne portait **qu'une seule colonne de date**,
-- `created_at`. Sur 300 selections tranchees, 148 sont datees par
-- `reglements.observed_at` et **152 n'ont aucune date** — un resultat que
-- personne n'a horodate.
--
-- - **Ce qu'elle dit exactement** : quand *nous* l'avons su, jamais quand le
--   match s'est termine. Meme regle a sens unique que l'anteriorite — la base
--   peut prouver qu'un fait precede la connaissance de l'issue, jamais qu'il la
--   suit. Une borne absente ne rend pas la selection suspecte : elle la rend
--   **hors de portee** de toute relecture qui a besoin d'une borne.
-- - **Elle s'efface avec le resultat.** Une ligne remise en attente perd sa
--   date : un horodatage qui survivrait a l'effacement affirmerait une
--   connaissance qui n'existe plus.
-- - **Retro-remplie depuis `reglements.observed_at`, et de la seule.** C'est un
--   instant **releve**, pas reconstitue : le module de reglement l'ecrit au
--   moment ou il lit la source. Les autres restent nulles — deduire une date
--   d'un `created_at` de session ou d'une heure de coup d'envoi inventerait
--   precisement la borne que cette colonne existe pour ne pas inventer.
-- - **Et seulement sur un reglement `applique`**, jamais `divergent`. La
--   distinction decide du **sens de l'erreur**, qui est tout ce qui compte pour
--   une borne. Sur une ligne appliquee, c'est le reglement qui a pose le
--   resultat : `observed_at` precede l'ecriture, donc la borne est **trop tot**,
--   et un garde qui s'en sert refuse un peu trop — il se trompe du bon cote. Sur
--   une ligne divergente, le resultat vient d'une saisie humaine anterieure et
--   `observed_at` n'est que la date ou la regle a relu la source : la borne
--   serait **trop tard**, donc permissive, et laisserait passer un fait releve
--   entre les deux. Une borne qui se trompe dans le sens permissif est pire
--   qu'une borne absente — celle-la se voit et se compte.
--
-- ## `framework_version` n'a jamais rien etiquete
--
-- Mesure du 22/08/2026 : le champ est emis par `payload.build_payload`, la
-- route payload n'a **jamais servi en production**, et `ACTIVE_PRODUCER` vaut le
-- gabarit — qui ne l'ecrit pas. Sur les 180 prompts archives, **zero** porte la
-- chaine. Il n'etait ni emis, ni persiste : une valeur qui n'etiquetait rien.
--
-- - **L'application l'estampille elle-meme, a l'ecriture de chaque selection.**
--   Pas d'aller-retour par le modele pour une valeur qu'elle connait : la faire
--   declarer dans le rendu puis relire a l'import ajouterait un chemin de perte
--   a une constante locale.
-- - **Aucun retro-remplissage.** Les 352 selections d'avant n'ont pas ete
--   produites sous un cadre que la base connaisse, et leur en preter un ferait
--   exactement ce que le champ existe pour empecher — melanger deux regimes dans
--   une population. `NULL` est la verite, et le compte des nulles se rend.
ALTER TABLE picks ADD COLUMN result_at TEXT;
ALTER TABLE picks ADD COLUMN framework_version TEXT;

UPDATE picks
   SET result_at = (
         SELECT r.observed_at FROM reglements r
          WHERE r.pick_id = picks.id AND r.etat = 'applique'
       )
 WHERE result IN ('win', 'loss', 'void')
   AND EXISTS (
         SELECT 1 FROM reglements r
          WHERE r.pick_id = picks.id AND r.etat = 'applique'
       );
