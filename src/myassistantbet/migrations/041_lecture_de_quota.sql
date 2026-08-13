-- Distinguer une **facturation** d'une **lecture de compteur** dans `api_usage`.
--
-- Mesure du 13/08/2026 : le 06/08, le compteur API-Football est tombe de 7 497
-- a 97, soit ~7 400 appels consommes, pour **2 953 appels journalises**. Les
-- autres jours tombent au chiffre pres (347 appels, 7 328 -> 6 981). Ce n'est
-- donc pas une derive de comptage mais un chemin d'appel qui echappe
-- entierement a l'instrumentation.
--
-- Cause : `_account` n'est appele qu'au **retour** de `_envelope`. Trois chemins
-- consomment sans rien ecrire — chaque tentative de retry, un echec apres
-- MAX_ATTEMPTS, un HTTP >= 400 non retentable. Et une saturation de debit arrive
-- en HTTP 200 chez ce fournisseur, donc elle est retentee trois fois.
--
-- `outcome` NULL est le cas ordinaire et signifie « appel facture », comme
-- toutes les lignes existantes. Une valeur — `retry`, `error` — designe une
-- **lecture** du compteur prise sur une tentative qui n'a pas abouti : `cost`
-- vaut alors 0, parce que le cout d'une tentative echouee depend du fournisseur
-- et n'est pas connu ici. Ce qui fait foi est la difference entre deux lectures
-- consecutives de `remaining` ; tout ecart avec la somme des couts journalises
-- est de la consommation invisible, et devient mesurable.
ALTER TABLE api_usage ADD COLUMN outcome TEXT;

CREATE INDEX IF NOT EXISTS idx_api_usage_provider_time
  ON api_usage(provider, called_at);
