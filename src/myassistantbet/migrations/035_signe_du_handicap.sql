-- Le signe du handicap, du cote de l'equipe qui se deplace.
--
-- **Deux fournisseurs, deux conventions, et une seule etait lue.** The Odds API
-- donne a chaque issue **son propre** handicap : « Al-Qadsiah -1 » et
-- « Al-Shabab +1 » sont les deux moities d'un meme palier. API-Football, lui,
-- ecrit le handicap **du point de vue de l'equipe qui recoit, des deux cotes** :
-- « Home -0.5 » et « Away -0.5 » sont la meme ligne, la seconde valeur etant le
-- prix de la **double chance** de l'exterieur. Le releve de substitution entrait
-- donc tel quel, et le bloc annoncait un pari pour l'autre.
--
-- Mesure qui l'a revele, sur une Supercoupe d'Europe : le bloc servait
-- « Aston Villa -0.5 2.12 » quand Aston Villa vainqueur valait 4.60. Le prix
-- etait juste — c'est celui de sa double chance — mais le libelle designait le
-- pari inverse. Recoupement sur la base entiere : 33 rencontres portaient la
-- faute, **toutes** relevees par ce chemin, **aucune** par The Odds API.
--
-- La conversion est faite a l'ingestion (`fixtures._outcome`), et le rendu leve
-- desormais une alerte quand un handicap ±0.5 contredit le 1N2. Restent les
-- lignes deja ecrites, que cette migration reprend une fois.
--
-- **Le critere est structurel, pas une liste de books.** Un book se configure
-- (`APIFOOTBALL_BOOKMAKERS`) et la liste aurait vieilli ; surtout, elle n'aurait
-- rien prouve. Ce qu'on sait dire, c'est qu'une paire de prix forme un livre a
-- deux issues ou n'en forme pas : `1/a + 1/b` vaut un peu plus de 1 quand les
-- deux cotes sont les deux faces d'un meme pari, et n'importe quoi sinon. Un
-- groupe est donc repris lorsque la lecture « meme signe » tient et que la
-- lecture « miroir » ne tient pas. Verifie sur la base servie : 331 groupes,
-- 33 repris, 298 laisses intacts, et les 18 groupes ou les deux lectures se
-- valent — echelles symetriques, ligne nulle — ne bougent pas, parce qu'il n'y
-- a rien a y corriger.
--
-- Ce que la migration **ne** fait pas : toucher `alternate_spreads`. Le releve
-- de substitution n'ecrit que `spreads` ; l'echelle complete vient de The Odds
-- API, dont la convention a toujours ete la bonne.

WITH fautifs AS (
  SELECT DISTINCT dom.event_id, dom.bookmaker
  FROM odds AS dom
  JOIN events AS e ON e.id = dom.event_id
  JOIN odds AS ext
    ON  ext.event_id     = dom.event_id
    AND ext.bookmaker    = dom.bookmaker
    AND ext.market_key   = dom.market_key
    AND ext.point        = dom.point
    AND ext.outcome_name = e.away
  LEFT JOIN odds AS miroir
    ON  miroir.event_id     = dom.event_id
    AND miroir.bookmaker    = dom.bookmaker
    AND miroir.market_key   = dom.market_key
    AND miroir.point        = -dom.point
    AND miroir.outcome_name = e.away
  WHERE dom.market_key   = 'spreads'
    AND dom.outcome_name = e.home
    AND dom.point IS NOT NULL
    AND dom.point <> 0
    AND 1.0 / dom.price + 1.0 / ext.price >  1.0
    AND 1.0 / dom.price + 1.0 / ext.price <= 1.30
    AND (miroir.price IS NULL
         OR 1.0 / dom.price + 1.0 / miroir.price <= 1.0
         OR 1.0 / dom.price + 1.0 / miroir.price >  1.30)
)
UPDATE odds SET point = -point
WHERE market_key = 'spreads'
  AND point IS NOT NULL
  AND point <> 0
  AND outcome_name = (SELECT e.away FROM events AS e WHERE e.id = odds.event_id)
  AND EXISTS (SELECT 1 FROM fautifs AS f
              WHERE f.event_id = odds.event_id AND f.bookmaker = odds.bookmaker);

-- Le meme critere sur le releve fige des sessions. C'est celui qui compte : les
-- cotes vivantes se refont au prochain releve, `prompt_odds` **ne se reconstitue
-- pas apres coup** — c'est toute sa raison d'etre. Le groupe y porte en plus sa
-- session : deux captures du meme match sont deux instantanes distincts.

WITH fautifs AS (
  SELECT DISTINCT dom.session_id, dom.event_id, dom.bookmaker
  FROM prompt_odds AS dom
  JOIN events AS e ON e.id = dom.event_id
  JOIN prompt_odds AS ext
    ON  ext.session_id   = dom.session_id
    AND ext.event_id     = dom.event_id
    AND ext.bookmaker    = dom.bookmaker
    AND ext.market_key   = dom.market_key
    AND ext.point        = dom.point
    AND ext.outcome_name = e.away
  LEFT JOIN prompt_odds AS miroir
    ON  miroir.session_id   = dom.session_id
    AND miroir.event_id     = dom.event_id
    AND miroir.bookmaker    = dom.bookmaker
    AND miroir.market_key   = dom.market_key
    AND miroir.point        = -dom.point
    AND miroir.outcome_name = e.away
  WHERE dom.market_key   = 'spreads'
    AND dom.outcome_name = e.home
    AND dom.point IS NOT NULL
    AND dom.point <> 0
    AND 1.0 / dom.price + 1.0 / ext.price >  1.0
    AND 1.0 / dom.price + 1.0 / ext.price <= 1.30
    AND (miroir.price IS NULL
         OR 1.0 / dom.price + 1.0 / miroir.price <= 1.0
         OR 1.0 / dom.price + 1.0 / miroir.price >  1.30)
)
UPDATE prompt_odds SET point = -point
WHERE market_key = 'spreads'
  AND point IS NOT NULL
  AND point <> 0
  AND outcome_name = (SELECT e.away FROM events AS e WHERE e.id = prompt_odds.event_id)
  AND EXISTS (SELECT 1 FROM fautifs AS f
              WHERE f.session_id = prompt_odds.session_id
                AND f.event_id   = prompt_odds.event_id
                AND f.bookmaker  = prompt_odds.bookmaker);
