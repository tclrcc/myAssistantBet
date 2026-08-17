-- 058_reponses_brutes.sql — garder ce que la source a rendu, avant de le lire.
--
-- **C'est la premiere chose ecrite de ce lot, et c'est la lecon directe de
-- Sackmann.** Une source gratuite a disparu du jour au lendemain — 404 sur `raw`
-- comme sur l'API, quand `python/cpython` repond 200 — et avec elle les colonnes
-- sur lesquelles reposait tout le calcul de service. Celle-ci est **payante,
-- proprietaire et unique** : si l'abonnement s'interrompt ou si le schema
-- change, seule une archive locale conservera l'historique deja constitue.
--
-- La table `imports_raw` porte deja ce raisonnement pour les collages : « le
-- texte recu est conserve **avant** toute tentative de lecture, y compris quand
-- le parsing echouera entierement — c'est precisement ce cas-la qu'on veut
-- pouvoir rejouer ». Celle-ci est la meme regle appliquee a l'amont.
--
-- ## Pourquoi ce n'est pas `api_usage`
--
-- `api_usage` compte des credits : provider, endpoint, cout, restant. Elle
-- repond a « combien ai-je depense », et elle continue de le faire. Elle ne
-- porte pas une seule reponse, et lui ajouter une colonne de charge utile ferait
-- d'une table de comptage une table d'archive — deux questions dans une table.

CREATE TABLE api_responses (
  id            INTEGER PRIMARY KEY,
  provider      TEXT NOT NULL,
  -- La **famille** declaree par l'appelant (`profile/matches-played`), qui est
  -- ce qui se compte, et le **chemin complet**, qui est ce qui se rejoue. Les
  -- deux, parce qu'ils ne repondent pas a la meme question : un premier jet
  -- redecoupait la famille depuis le chemin et un test l'a attrape — le segment
  -- variable n'est pas au meme rang d'un endpoint a l'autre.
  endpoint      TEXT NOT NULL,
  path          TEXT NOT NULL,
  -- Parametres normalises : cles triees, secrets exclus. Ils font partie de
  -- l'identite de la reponse — la page 2 d'un joueur n'est pas sa page 1.
  params        TEXT NOT NULL DEFAULT '',
  -- Le corps **integral et tel quel**. Pas de reduction aux champs qu'on lit
  -- aujourd'hui : ce qui est jete ici ne se recupere plus, et le schema d'un
  -- fournisseur bouge. C'est exactement ce que `dossier._summarize` a le droit
  -- de faire — la source y est vivante et se redemande — et que ceci n'a pas.
  raw_json      TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  -- **Les reponses en erreur et les reponses vides sont archivees aussi.** Un
  -- `result` vide sur `"success": true` est le defaut caracteristique du projet
  -- dans la source elle-meme : ne garder que ce qui se lit reproduirait ici le
  -- silence qu'on passe son temps a supprimer ailleurs.
  http_status   INTEGER,
  fetched_at    TEXT NOT NULL,
  -- Le compteur de quota au moment de l'appel. Il date l'archive dans la vie de
  -- l'abonnement, ce que `fetched_at` seul ne dit pas.
  quota_remaining INTEGER
);

-- Rejouer part d'un chemin et de ses parametres : c'est l'index de `replay-api`.
CREATE INDEX idx_api_responses_lookup ON api_responses (provider, endpoint, fetched_at);
-- Deux appels identiques a deux instants differents sont deux archives — on ne
-- deduplique pas sur l'empreinte, contrairement a `imports_raw`. La raison est
-- inverse : deux collages du meme texte n'apportent rien, quand deux relevés du
-- meme endpoint a deux dates disent **que la source n'a pas bouge**, ce qui est
-- precisement la question du lot 4.
CREATE INDEX idx_api_responses_sha ON api_responses (sha256);

-- **Aucune purge automatique**, et c'est ecrit ici pour que personne n'en ajoute
-- une en croyant faire de la place. Le cout est mesure : une reponse
-- `matches-played` pese ~29 ko, une reprise de 180 joueurs ~5 Mo, l'entretien
-- quotidien ~1,6 Mo par mois. La base fait 29 Mo. Ce qui se perdrait en purgeant
-- ne se rachete a aucun prix.
