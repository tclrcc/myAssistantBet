-- La declaration reste intacte : elle est l'entree de la mesure, pas sa sortie.
--
-- **`source_level` etait ecrase** quand la selection portait sur un dossier non
-- ouvert : la valeur declaree disparaissait au profit de `lecture`. La colonne
-- cessait donc d'etre une declaration de modele pour devenir une sortie de
-- l'application — et la table « par niveau de source » mesurait alors sa propre
-- correction, pas ce que l'analyse avait annonce. Meme chose que la carte « par
-- cran calcule », qui n'a de sens que si les deux valeurs sont conservees.
--
-- L'ecart entre declare et effectif ne se stocke pas : les deux colonnes sont
-- la, leur difference est une soustraction, et la recopier l'aurait fait
-- diverger — meme regle que la famille d'un marche ou le niveau d'une
-- competition, resolus a la lecture.
--
-- **Le retro-remplissage est sur, et c'est mesure** : `research_overridden` est
-- NULL sur les 149 selections de la base, donc l'ecrasement n'a **jamais**
-- tourne et aucune valeur declaree n'a ete perdue. L'effectif vaut donc le
-- declare partout, sans exception a inventer.
ALTER TABLE picks ADD COLUMN source_level_effective TEXT;

UPDATE picks SET source_level_effective = source_level WHERE source_level IS NOT NULL;

-- Ce qui est arrive a la ligne `dossiers_ouverts` du rendu : `lue`, `absente`,
-- ou `illisible`.
--
-- **Deux defauts differents se confondaient dans un NULL** : le modele qui omet
-- la ligne et le lecteur qui echoue a la relire n'ont ni la meme cause ni le
-- meme correctif — l'un se reprend dans le gabarit, l'autre dans le parseur — et
-- les deux produisent pourtant le meme repli, toutes les selections en lecture.
-- Sans les separer, leur somme se lirait comme un seul taux.
--
-- Rien n'est retro-rempli : les neuf sessions de la base ont ete importees avant
-- que la ligne existe, et NULL y dit la verite — la question ne leur a pas ete
-- posee.
ALTER TABLE sessions ADD COLUMN open_dossiers_state TEXT;
