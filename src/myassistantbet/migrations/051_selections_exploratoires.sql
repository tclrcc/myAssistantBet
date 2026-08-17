-- 051_selections_exploratoires.sql — ouvrir les paliers hauts, sans melanger.
--
-- **Mesure du 17/08/2026, sur douze sessions** : 🔴 GIGA FUN et 💥 GIGA+ a zero
-- selection sur 12 sessions sur 12, 🟠 ULTRA FUN a 6 % du volume. Trois niveaux
-- sur cinq portent tout. Une echelle dont deux niveaux ne se declenchent jamais
-- ne note plus rien : ces bandes ne sont pas seulement inexploitees, elles sont
-- **non mesurables**.
--
-- La cause est connue et elle est dans le gabarit : « Une selection qui sort des
-- deux paliers les plus surs ne se prend pas sur une lecture : il lui faut un
-- fait nomme et date en section A. » Combinee a un budget de sept dossiers de
-- recherche pour quinze matchs, et au fait qu'un fait date designe le plus
-- souvent un favori, la regle a produit zero selection dans les deux bandes
-- hautes.
--
-- Cette regle a ete ecrite pour eviter de gaspiller une mise sur un outsider mal
-- etaye. **Aucune mise n'est posee** — zero coupon sur douze sessions — donc ce
-- cout n'existe pas. Le cout inverse, lui, est reel : on ne saura jamais ce que
-- vaut l'analyse sur les cotes hautes.
--
-- L'exigence de fait date **n'est pas supprimee**, et c'est tout l'objet de la
-- colonne. La retirer perdrait la comparaison qui donne son sens a la page — une
-- selection adossee a un fait date tient-elle mieux qu'une lecture — et si les
-- deux populations se melangent dans les bandes hautes, cette question devient
-- definitivement sans reponse. Un second circuit s'ajoute donc a cote, etiquete
-- et compte a part.

ALTER TABLE picks ADD COLUMN exploratoire BOOLEAN NOT NULL DEFAULT 0;

-- `NOT NULL DEFAULT 0` et non un booleen a trois etats : une selection est
-- exploratoire ou elle ne l'est pas, et les 235 lignes deja en base ne le sont
-- pas — elles ont ete produites sous la regle d'origine. Les indicateurs
-- historiques sont donc **inchanges** apres application, ce qu'un test verifie.

CREATE INDEX idx_picks_exploratoire ON picks(exploratoire);
