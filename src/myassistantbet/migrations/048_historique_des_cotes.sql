-- L'historique des cotes : arreter la perte, et rien d'autre.
--
-- `replace_odds` fait un DELETE puis un INSERT par (evenement, book, marche) :
-- **seul le dernier releve survit**. Une heure apres un scan, l'etat d'avant
-- n'existe plus nulle part. C'est le meme defaut que `commence_time` avant la
-- migration 040 — l'horaire d'un match reporte etait ecrase a chaque scan, et
-- cinq heures de decalage a Cincinnati ont du etre retrouvees dans la presse
-- alors que les deux releves etaient passes par ici.
--
-- **Ce chantier n'affiche rien, ne lit rien, n'alerte sur rien.** Il ne
-- construit aucun seuil et ne touche aucune surface. Chaque scan passe sans lui
-- est un mouvement definitivement perdu ; c'est la seule raison pour laquelle il
-- passe avant la mesure qui dira s'il y a quelque chose a en faire.

CREATE TABLE odds_history (
  id           INTEGER PRIMARY KEY,
  event_id     INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,

  -- **Le book et le marche sur chaque ligne**, jamais deduits d'ailleurs : un
  -- mouvement Pinnacle et un mouvement Betclic ne disent pas la meme chose, et
  -- une derive sur un score exact a 34.00 n'est pas comparable a une derive sur
  -- un 1N2. Mesure du 14/08/2026 sur les seuls releves comparables de la base :
  -- 1,0 % de mouvement moyen sur `h2h`, 23 % tous marches confondus — l'ecart
  -- vient entierement des cotes longues, et les melanger rendrait toute lecture
  -- fausse.
  bookmaker    TEXT NOT NULL,
  market_key   TEXT NOT NULL,
  outcome_name TEXT NOT NULL,
  description  TEXT,               -- nom du joueur pour les props
  point        REAL,

  previous_price REAL NOT NULL,
  price          REAL NOT NULL,

  -- **Deux bornes, et il faut les deux.** « Le prix a change entre 11h22 et
  -- 15h06 » n'est pas « il a change a 15h06 » : sans la borne basse, tout
  -- mouvement parait instantane, et un scan quotidien ferait passer une derive
  -- de vingt-quatre heures pour un decrochage. `previous_fetched_at` est
  -- l'horodatage du releve **precedent** chez le fournisseur, `fetched_at`
  -- celui du nouveau.
  previous_fetched_at TEXT NOT NULL,
  fetched_at          TEXT NOT NULL,
  -- Quand **nous** l'avons constate. Distinct de `fetched_at` comme dans
  -- `prompt_odds` : le fournisseur date son releve, nous datons notre lecture,
  -- et un scan en retard ecarte les deux.
  observed_at         TEXT NOT NULL
);

-- La lecture qui viendra un jour se fera par evenement et par instant : un
-- mouvement se lit sur une fenetre, jamais ligne par ligne.
CREATE INDEX idx_odds_history_event ON odds_history(event_id, observed_at);
