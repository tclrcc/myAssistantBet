-- Rattachement des competitions de football a leur ligue API-Football.
--
-- Sans `apifootball_league_id`, `enrich.context_possible` est faux et aucun
-- contexte n'est jamais demande : ni classement, ni forme, ni confrontations
-- directes, ni blessures. La synchronisation depuis /sports cree les
-- competitions sans cet identifiant, donc tout ce qui a ete decouvert apres
-- le seed de la migration 002 etait muet — y compris les qualifications
-- europeennes, les seules sur lesquelles des paris ont ete pris.
--
-- Chaque identifiant a ete releve dans le catalogue /leagues du fournisseur,
-- filtre par pays, et verifie ligne a ligne. Le rapprochement automatique par
-- libelle a ete essaye et rejete : il proposait la Championship ecossaise (180)
-- pour l'anglaise (40), la Bundesliga (78) pour la 2. Bundesliga (79), et la
-- Coupe de Malaisie (499) pour la MLS (253) — le tout avec un score maximal.
-- Meme regle que la surface et le niveau : rien ne se deduit d'un libelle.
--
-- Les trois competitions UEFA couvrent leurs tours de qualification : les
-- rencontres du 3e tour preliminaire sont bien servies par les ligues 3 et 848,
-- avec `round = "3rd Qualifying Round"`. Il n'existe donc pas d'identifiant
-- distinct pour la qualification, et `soccer_uefa_champs_league_qualification`
-- pointe sur la Ligue des champions elle-meme.

UPDATE competitions SET apifootball_league_id = 218 WHERE oddsapi_key = 'soccer_austria_bundesliga'      AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 144 WHERE oddsapi_key = 'soccer_belgium_first_div'       AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 71  WHERE oddsapi_key = 'soccer_brazil_campeonato'       AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 119 WHERE oddsapi_key = 'soccer_denmark_superliga'       AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 40  WHERE oddsapi_key = 'soccer_efl_champ'               AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 244 WHERE oddsapi_key = 'soccer_finland_veikkausliiga'   AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 62  WHERE oddsapi_key = 'soccer_france_ligue_two'        AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 78  WHERE oddsapi_key = 'soccer_germany_bundesliga'      AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 79  WHERE oddsapi_key = 'soccer_germany_bundesliga2'     AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 197 WHERE oddsapi_key = 'soccer_greece_super_league'     AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 135 WHERE oddsapi_key = 'soccer_italy_serie_a'           AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 88  WHERE oddsapi_key = 'soccer_netherlands_eredivisie'  AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 106 WHERE oddsapi_key = 'soccer_poland_ekstraklasa'      AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 140 WHERE oddsapi_key = 'soccer_spain_la_liga'           AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 141 WHERE oddsapi_key = 'soccer_spain_segunda_division'  AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 179 WHERE oddsapi_key = 'soccer_spl'                     AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 207 WHERE oddsapi_key = 'soccer_switzerland_superleague' AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 253 WHERE oddsapi_key = 'soccer_usa_mls'                 AND apifootball_league_id IS NULL;

UPDATE competitions SET apifootball_league_id = 2   WHERE oddsapi_key = 'soccer_uefa_champs_league_qualification' AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 2   WHERE oddsapi_key = 'soccer_uefa_champs_league'               AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 3   WHERE oddsapi_key = 'soccer_uefa_europa_league'               AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 848 WHERE oddsapi_key = 'soccer_uefa_europa_conference_league'    AND apifootball_league_id IS NULL;
UPDATE competitions SET apifootball_league_id = 5   WHERE oddsapi_key = 'soccer_uefa_nations_league'              AND apifootball_league_id IS NULL;
