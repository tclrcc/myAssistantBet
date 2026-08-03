-- 001_init.sql — schema initial (SPEC.md section 3).
-- Ne jamais modifier ce fichier une fois applique : creer une nouvelle migration.

CREATE TABLE sports (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,          -- football | tennis | cycling
  label TEXT NOT NULL
);

CREATE TABLE competitions (
  id INTEGER PRIMARY KEY,
  sport_id INTEGER NOT NULL REFERENCES sports(id),
  oddsapi_key TEXT,                  -- ex: soccer_sweden_allsvenskan
  apifootball_league_id INTEGER,
  label TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  sport_id INTEGER NOT NULL REFERENCES sports(id),
  competition_id INTEGER REFERENCES competitions(id),
  oddsapi_event_id TEXT UNIQUE,
  apifootball_fixture_id INTEGER,
  home TEXT NOT NULL,
  away TEXT NOT NULL,
  commence_time TEXT NOT NULL,       -- ISO 8601 UTC
  source TEXT NOT NULL DEFAULT 'api',-- api | manual
  created_at TEXT NOT NULL
);
CREATE INDEX idx_events_time ON events(commence_time);

-- Une ligne par outcome. Jamais de blob JSON de cotes.
CREATE TABLE odds (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  market_key TEXT NOT NULL,
  outcome_name TEXT NOT NULL,
  description TEXT,                  -- nom du joueur pour les props
  point REAL,
  price REAL NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX idx_odds_event_market ON odds(event_id, market_key);

CREATE TABLE context (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                -- form|injuries|h2h|standings|lineups|manual_note
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX idx_context_event ON context(event_id, kind);

CREATE TABLE sessions (
  id INTEGER PRIMARY KEY,
  label TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE session_events (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  note TEXT,
  PRIMARY KEY (session_id, event_id)
);

CREATE TABLE prompts (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  template_name TEXT NOT NULL,
  body TEXT NOT NULL,
  token_estimate INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE picks (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id INTEGER REFERENCES events(id),
  tier TEXT NOT NULL,                -- safe|fun|ultra_fun|giga_fun|giga_plus
  market TEXT NOT NULL,
  selection TEXT NOT NULL,
  price REAL,
  confidence INTEGER,
  played BOOLEAN NOT NULL DEFAULT 0,
  stake REAL,
  result TEXT,                       -- win|loss|void|pending
  created_at TEXT NOT NULL
);

CREATE TABLE api_usage (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  cost INTEGER NOT NULL,
  remaining INTEGER,
  called_at TEXT NOT NULL
);
