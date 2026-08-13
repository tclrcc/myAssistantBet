-- 043_dossiers_ouverts.sql — le niveau de source cesse d'etre pris au mot.
--
-- **Mesure : 0 selection en `lecture` sur 149.** Le budget de recherche vaut
-- sept dossiers pour des lots de 57 a 72 matchs, donc l'immense majorite des
-- selections repose sur les blocs seuls — ce que le preambule appelle une
-- lecture, et qu'il presente comme « une reponse normale et frequente ». Elle
-- n'a jamais ete donnee une seule fois. Le niveau de source est donc gonfle, et
-- la table « par niveau de source » ne mesure rien.
--
-- **Sans ce chantier, le calcul du cran empire les choses.** Le modele declare
-- un `source_level` sur les matchs qu'il n'a pas ouverts ; l'application en
-- deduirait alors un cran **deterministe** a partir d'une declaration gonflee.
-- Le faux gagnerait l'apparence du calcul, et deviendrait invisible pour la
-- raison exacte qui rendait la colonne libre inoffensive — on savait qu'elle
-- etait molle. C'est pourquoi les deux migrations partent ensemble.
--
-- **Le defaut est `lecture`, jamais « ouvert ».** Liste absente, illisible, ou
-- portant un repere qui ne se resout pas contre le prompt archive : toutes les
-- selections de la session partent en lecture, cran 1. Meme raisonnement que la
-- somme de controle de l'appariement — un `lecture` de trop se voit et se
-- corrige, un niveau de source gonfle qui passe pour verifie ne se voit pas.

-- La selection porte sur un match que l'analyse declare **ne pas** avoir
-- ouvert. NULL sur tout l'existant, et sur toute saisie a la main : l'override
-- juge une declaration de modele, pas un geste humain.
ALTER TABLE picks ADD COLUMN research_overridden INTEGER;

-- Le cran que la declaration **aurait** donne, avant l'ecrasement. C'est lui qui
-- separe deux fautes que le compte seul confondrait : un 3 revendique sur un
-- dossier non ouvert est de l'inflation, un 5 — deux faits dates, deux editeurs
-- distincts, une origine — est de la **fabrication**, et ca ne se traite pas
-- pareil. `confidence_computed` reste le verdict final, donc 1.
ALTER TABLE picks ADD COLUMN confidence_claimed INTEGER;

-- Les dossiers que l'analyse declare avoir ouverts, tels que colles. Ranges par
-- session parce que c'est une propriete du rendu et non d'une selection — et
-- gardes meme quand aucun d'eux ne porte de selection : c'est la liste entiere
-- qui se compare a l'ordre de passage que l'application avait propose.
ALTER TABLE sessions ADD COLUMN open_dossiers TEXT;
