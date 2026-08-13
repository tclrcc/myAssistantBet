-- 042_confiance_calculee.sql — le cran cesse d'etre devine, il se calcule.
--
-- **Ce que la mesure a montre.** Sur les 141 selections tranchees du 13/08/2026 :
-- 90 % du volume sur deux crans (3 et 4), **aucune en cran 1 sur 149**, et un
-- ordre qui n'est pas monotone — cran 2 a 77 % (10/13), cran 4 a 60 % (31/52),
-- cran 3 a 44 % (33/75). L'echelle, telle que le modele la declare, ne trie
-- rien : p = 0,131 sur la population a anteriorite etablie, quand le palier
-- ordonne a p = 0,000.
--
-- Le cran est pourtant defini dans le gabarit comme une fonction de trois
-- choses **verifiables** : le niveau des sources, le nombre d'editeurs
-- distincts, et si un manque de la section A touche le facteur porteur. Le
-- modele appliquait la table lui-meme. C'est exactement le cas que le projet a
-- deja tranche pour la famille d'un marche et pour le comptage de la section C
-- — une regle deterministe laissee au modele coute des tokens, se refait a
-- chaque session, et ne se mesure jamais.
--
-- **Aucun renommage, et c'est le precedent de la migration 030.** `confidence`
-- **est** deja la valeur declaree ; la renommer `confidence_declared` toucherait
-- six gabarits, quatre services et la moitie des tests pour un gain nul. Elle
-- reste ecrite comme avant et devient une lecture seule de plus.
--
-- **Les deux valeurs se gardent.** L'ecart entre le cran annonce et le cran
-- calcule est la seule mesure possible de savoir si le modele notait au hasard.
-- Le jeter serait perdre la reponse a la question qui fait naitre ce chantier.

-- Le cran calcule. NULL est le cas ordinaire sur tout l'existant : les champs
-- dont il se deduit n'ont jamais ete collectes, et les retro-remplir
-- reviendrait a inventer un faisceau d'information a posteriori.
ALTER TABLE picks ADD COLUMN confidence_computed INTEGER;

-- Les faits declares, tels qu'ils ont ete lus. Stockes **entiers** plutot que
-- resumes : le cran se recalcule alors sans le modele le jour ou la table
-- change, exactement comme la famille d'un marche se resout a la lecture.
ALTER TABLE picks ADD COLUMN facts_json TEXT;

-- Un manque de la section A touche-t-il le facteur porteur. **Trois etats.**
-- NULL n'est pas un defaut : les crans 3, 4 et 5 ne se distinguent que par lui,
-- et le deviner reviendrait a choisir un cran a la place de l'analyse.
ALTER TABLE picks ADD COLUMN gap_touches_factor INTEGER;

-- Editeurs distincts **parmi les faits de niveau 1-2** : le compte que la regle
-- du cran 5 a reellement employe. Un compte plus large s'afficherait a cote
-- d'un cran qu'il n'expliquerait pas.
ALTER TABLE picks ADD COLUMN distinct_publishers INTEGER;
