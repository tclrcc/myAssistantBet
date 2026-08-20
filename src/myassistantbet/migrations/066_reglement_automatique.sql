-- 066_reglement_automatique.sql — le reglement propose, jamais impose.
--
-- **Ce fichier n'automatise pas le reglement : il lui donne un endroit ou
-- proposer.** 293 selections tranchees portent tout ce que ce projet sait
-- produire — le residu au prix, les crans, les intervalles. Un reglement
-- automatique errone les corromprait **en silence**, et rien ne le dirait :
-- c'est la forme la plus couteuse qu'un defaut puisse prendre ici.
--
-- ## Trois etats, et le troisieme est celui qui compte
--
--   * `propose`   — la selection n'avait pas de resultat, le reglage attend une
--                   main. C'est le cas ordinaire.
--   * `applique`  — un humain l'a promu dans `picks.result`.
--   * `divergent` — la selection portait **deja** un resultat saisi a la main, et
--                   le calcul ne dit pas la meme chose. **Rien n'est ecrase.**
--
-- Le troisieme est la lecon du lot 14 transposee : `set_open_dossiers` ecrasait
-- un bon etat par un mauvais sans laisser de trace, et il a fallu un rejeu pour
-- s'en apercevoir. Ici la divergence se voit, et c'est tout ce qu'elle fait.
--
-- ## Ce qui n'est PAS ici
--
-- Aucun montant. Le reglement dit qui gagne, jamais combien : la mesure
-- d'analyse et le suivi d'argent restent deux journaux separes, et le test du
-- lot 15 lit la source pour le garantir.
--
-- Aucun marche dont la regle n'est pas ecrite. Un marche non couvert ne produit
-- **aucune ligne** — il n'est pas range dans un etat « inconnu », il est absent,
-- et c'est ce qui le distingue d'un marche couvert dont le resultat manque.

CREATE TABLE IF NOT EXISTS reglements (
  id          INTEGER PRIMARY KEY,
  pick_id     INTEGER NOT NULL REFERENCES picks(id) ON DELETE CASCADE,

  -- Ce que la regle conclut. Meme vocabulaire que `picks.result`, sans
  -- `pending` : une proposition sans verdict ne s'ecrit pas.
  verdict     TEXT NOT NULL,          -- win | loss | void
  etat        TEXT NOT NULL,          -- propose | applique | divergent

  -- **D'ou vient le resultat, et quand il a ete releve.** Sans ces deux-la, le
  -- taux d'accord cesse d'etre mesurable dans le temps : on ne saurait plus si
  -- une divergence vient de la regle ou d'une source qui a change d'avis.
  source      TEXT NOT NULL,
  observed_at TEXT NOT NULL,

  -- La cle de marche sur laquelle la regle a ete appliquee, et le releve brut
  -- qui a servi — un score, deux buts. C'est ce qui rend une divergence
  -- verifiable sans rejouer toute la chaine.
  market_key  TEXT,
  detail      TEXT,

  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,

  UNIQUE (pick_id)
);

CREATE INDEX IF NOT EXISTS idx_reglements_etat ON reglements (etat);
