-- 003_tiers.sql — bandes de cotes exposees au template de prompt (SPEC.md section 8).
-- Editables en base ; l'edition depuis l'UI arrive en phase 5.

CREATE TABLE tiers (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,          -- safe|fun|ultra_fun|giga_fun|giga_plus
  label TEXT NOT NULL,
  emoji TEXT NOT NULL,
  min_price REAL NOT NULL,
  max_price REAL,                    -- NULL = pas de borne haute
  quota_min INTEGER NOT NULL DEFAULT 0,
  quota_max INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL
);

INSERT INTO tiers (key, label, emoji, min_price, max_price, quota_min, quota_max, position) VALUES
  ('safe',      'SAFE',      '🟢', 1.25, 1.70, 2, 4, 1),
  ('fun',       'FUN',       '🔵', 1.70, 2.60, 3, 5, 2),
  ('ultra_fun', 'ULTRA FUN', '🟠', 2.60, 5.00, 2, 4, 3),
  ('giga_fun',  'GIGA FUN',  '🔴', 5.00, 15.0, 1, 3, 4),
  ('giga_plus', 'GIGA+',     '💥', 15.0, NULL, 0, 2, 5);
