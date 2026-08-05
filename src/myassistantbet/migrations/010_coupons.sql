-- 010_coupons.sql — les paris reellement joues.
--
-- Un pick est une selection ; un coupon est ce qui a ete pose chez le
-- bookmaker : une mise, une ou plusieurs jambes, un resultat global.
--
-- Sans cette distinction, un combine s'enregistrait comme un pick unique sans
-- evenement — donc sans sport — et les taux par sport l'ignoraient en silence.
-- Rattache a des jambes qui portent chacune leur match, il compte enfin.
--
-- Deux champs volontairement absents :
--
--   * le type (simple ou combine) se deduit du nombre de jambes ;
--   * le resultat se deduit de celui des jambes.
--
-- Aucun des deux n'est stocke : un champ enregistre pourrait contredire les
-- jambes, et il faudrait alors decider lequel a raison.
--
-- La mise est memorisee parce qu'elle fait partie du souvenir de ce qui a ete
-- joue. Elle n'est **jamais agregee** : ni gain, ni solde, ni ROI, ni cote
-- totale du coupon (SPEC.md section 9). La capture jointe garde tout cela.

CREATE TABLE coupons (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  bookmaker   TEXT    NOT NULL DEFAULT 'betclic',
  stake       REAL,
  placed_at   TEXT,                 -- ISO 8601 UTC, quand le pari a ete pose
  screenshot  TEXT,                 -- nom de fichier, jamais un chemin
  note        TEXT,
  created_at  TEXT    NOT NULL
);

ALTER TABLE picks ADD COLUMN coupon_id INTEGER REFERENCES coupons(id) ON DELETE SET NULL;

CREATE INDEX idx_picks_coupon ON picks(coupon_id);
