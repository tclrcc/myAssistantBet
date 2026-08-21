-- 073_prose_de_la_section_c.sql — deux colonnes du tableau rendu, jamais lues,
-- et un troisieme etat pour l'ecrasement sans cause.
--
-- ## Les deux colonnes de prose
--
-- Le gabarit ecrit un tableau a **onze** colonnes en section C ; `HEADERS` en
-- declarait **huit**. `Angle (1 ligne)` et `Ce qui la tue` etaient produites a
-- chaque session, collees a chaque import, et jetees par trois entrees
-- manquantes dans un dictionnaire.
--
-- Mesure du 21/08/2026, avant d'ecrire une ligne : les **41 collages archives
-- portent tous l'en-tete complet**, une seule variante, et sur les 76 lignes
-- rapprochables la cellule `Ce qui la tue` est **non vide 76 fois sur 76**. Le
-- modele la renseignait sans exception ; c'est la captation qui manquait.
--
-- `invalidation` porte le **controle 7** du cadre — « chaque selection porte une
-- condition d'invalidation » — donc la seule des trois colonnes qui soit
-- opposable. Elle est ecrite **avant le coup d'envoi**, ce qui la met hors de
-- portee de toute relecture retrospective : c'est ce qui en fait la matiere
-- d'un bilan et non un commentaire.
--
-- `angle_note` est la **prose** de l'angle, et reste distincte d'`angle`, qui
-- porte le vocabulaire ferme `issue` / `maniere`. Les fondre ferait entrer une
-- phrase entiere dans un champ a deux valeurs — le commentaire de `HEADERS`
-- signalait deja le piege, il n'avait simplement pas de seconde colonne ou
-- verser la phrase.
--
-- `prose_source` dit **d'ou vient la valeur** : `import` quand la colonne a ete
-- lue au collage, `reconstruit` quand elle a ete reprise apres coup depuis
-- `imports_raw.raw_text`. Les deux ne se lisent pas pareil — une reprise passe
-- par un rapprochement, donc par une regle qui peut se tromper, quand une
-- captation recopie une cellule. Meme regle que `price_source` et
-- `open_dossiers_state` : ce qui a ete deduit se declare.
--
-- **Aucune reprise ici.** Elle demande de decouper un tableau Markdown, donc du
-- Python : elle vit dans `picks_import.rebuild_prose()` et se declenche a la
-- main. Une migration qui n'en ferait qu'une moitie serait pire que rien.
ALTER TABLE picks ADD COLUMN angle_note TEXT;
ALTER TABLE picks ADD COLUMN invalidation TEXT;
ALTER TABLE picks ADD COLUMN prose_source TEXT;

-- ## Le troisieme etat de l'ecrasement
--
-- `claim_columns` ecrivait `cause = override_cause if override_cause in
-- OVERRIDE_CAUSES else None` : une cause absente ou inconnue devenait **NULL**,
-- indiscernable d'une ligne anterieure au typage. Mesure : **43 selections**
-- portent `research_overridden = 1` sans aucune cause, sessions 11 et 13.
--
-- Elles ne se re-typent pas. `imports_raw` ne commence qu'a la session 15 : le
-- texte de ces deux sessions n'existe plus, et rien ne dira jamais si la ligne
-- `dossiers_ouverts` y etait absente ou si le match etait hors dossiers. Leurs
-- voisines typees sont massivement `ligne_absente`, mais « probablement » n'est
-- pas une base de reclassement — et rejouer la 049 sur elles ecrirait un
-- constat que personne n'a fait.
--
-- **Consequence mesuree, et c'est elle qui justifie la colonne** :
-- `is_collection_fault(None)` est faux, donc ces 43 lignes comptaient comme des
-- **observations sur le modele** dans `Override.total` et `SessionRate.overridden`
-- — « elle s'est notee comme si elle avait cherche » — alors qu'on ignore
-- totalement ce qui s'est passe. Le compte surestimait de 43 lignes sur 127.
-- Typees `cause_inconnue`, elles sortent des deux comptes et se comptent a
-- cote : le total cesse de surestimer sans se mettre a sous-estimer.
--
-- ## Ce que la 049 a appris, et qui vaut au-dela d'elle
--
-- Son relevé annoncait « 16 typees » et il etait juste ; rien n'obligeait ce 16
-- a correspondre a la population reelle. Sa clause s'indexait sur
-- `sessions.open_dossiers_state`, un etat **mutable** ecrit par le dernier
-- import de la session : les lignes ecrites avant que cet etat soit pose lui
-- ont echappe en silence, et le compte affiche ne pouvait pas le dire.
--
-- **Une reprise dont la clause s'indexe sur un etat mutable laisse une part de
-- sa population derriere.** Celle-ci s'indexe sur la colonne qu'elle corrige —
-- `research_override_cause IS NULL` — donc sur un etat qu'elle rend faux, ce
-- qui la rend idempotente et complete par construction.
UPDATE picks
   SET research_override_cause = 'cause_inconnue'
 WHERE research_overridden = 1
   AND research_override_cause IS NULL;
