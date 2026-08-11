-- 033_marche_a_la_prise.sql — garder le marche dont une selection est issue.
--
-- **Ce lot ne repare rien et n'affiche rien. Il arrete une perte.** Chaque
-- session passee sans lui est une session definitivement non comparable, et
-- c'est la seule raison pour laquelle il passe avant tout le reste.
--
-- Ce qui manque aujourd'hui, mesure sur les 114 selections en base :
--
--   · `odds` ne conserve **que le dernier releve**. Le scan fait un DELETE puis
--     un INSERT par (match, book, marche) : l'etat du marche au moment ou
--     l'analyse l'a lu n'existe nulle part, une heure apres.
--   · `picks.market` est du **texte libre** recopie a la main. Seize libelles
--     distincts en base pour dix marches reels — « Eq. buts » et « Éq. buts »,
--     « DC » et « Double chance » — et aucune cle etrangere vers `odds`.
--
-- Consequence : pour une selection tranchee, on ne peut ni retrouver les autres
-- issues du meme marche, ni dire qui en etait le favori. Toute comparaison a une
-- reference exterieure — la seule chose qui dirait si 48 % est bon ou mauvais —
-- est donc hors de portee, et le restera pour ces 114 quoi qu'on fasse ensuite.
--
-- Quatre ajouts, et aucun ne touche a ce qui est deja affiche.
--
-- 1. `prompt_odds` — le marche complet des matchs partis a l'analyse.
--
--    Le releve se fait a l'archivage du prompt, au meme endroit que
--    `prompt_events` : c'est le seul instant ou l'on sait ce que l'analyse a
--    eu sous les yeux. Tous les books sont gardes, pas seulement le principal :
--    un favori se lit sur le marche entier, et un book de reference est parfois
--    le seul a servir la ligne.
--
--    **Un releve par session et par match, remplace a chaque prompt.** Ni par
--    prompt — une session reelle en genere jusqu'a vingt, ce serait vingt fois
--    la meme chose — ni fige au premier : un match entre parfois dans un prompt
--    avant d'etre enrichi, et le dernier prompt qui le porte est celui dont
--    l'etat est le plus proche de la decision. Meme forme que `scan._store`,
--    dont c'est deja la regle.
--
--    Cout mesure : ~29 lignes par match, soit ~1650 pour une session de 57
--    matchs. A raison d'une session par jour, de l'ordre de 35 Mo par an. C'est
--    la table qui grossira le plus vite de la base, et c'est assume : elle porte
--    la seule donnee du projet qui ne se reconstitue pas apres coup.
--
-- 2. `picks.market_key` — la cle de marche de la selection, figee a l'ecriture.
--
--    Elle se resout par correspondance **exacte** avec les libelles de
--    `render.MARKET_ORDER_BY_SPORT` : ces libelles sont ceux que le bloc met
--    sous les yeux de l'analyse, donc les lire n'est pas deviner. Une saisie
--    hors vocabulaire — « Double chance » la ou le bloc ecrit « DC » — reste
--    NULL et se reclame, jamais rangee d'office.
--
--    **Aucun retro-remplissage ici**, et pour la raison inverse de la migration
--    030 : ce n'est pas qu'on ne saurait pas, c'est que la regle vit en Python
--    et que la recopier en SQL la ferait diverger au premier marche ajoute —
--    exactement le piege des niveaux de competition. Les selections anterieures
--    se resolvent **a la lecture**, comme la famille d'un marche ; c'est la
--    colonne qui fige, pour que le lien vers un releve historique survive a un
--    libelle renomme.
--
-- 3. `sessions.scale_version` — la version de l'echelle de confiance en vigueur.
--
--    Elle ne sert a rien aujourd'hui et c'est voulu : le champ passe maintenant
--    pour que l'ancrage des bandes n'ait pas a rouvrir cette migration. Une
--    courbe de fiabilite tracee a travers un changement d'echelle ne mesure
--    rien ; il faut donc savoir, session par session, quelle echelle etait en
--    vigueur, et le savoir **des la premiere session concernee**.
--
-- 4. `prompts.feedback_active` — le bloc de retour d'experience etait-il servi.
--
--    Des qu'un agregat de resultats entre dans le prompt, les selections
--    suivantes ne sont plus des tirages independants : l'analyse lit son propre
--    tableau de bord. La question « depuis quand » se posait, et la reponse est
--    **non triviale** : le bloc a bien ete servi, sur 9 prompts de 3 sessions
--    (06, 07 et 08/08), quand les seuils valaient encore 10 et 4 — puis plus
--    jamais depuis leur relevement a 40 et 10.
--
--    La valeur se retro-remplit ici, et c'est legitime : **le corps du prompt
--    est la preuve**, il est archive depuis toujours, et la ligne « Taux de
--    reussite de » n'apparait que lorsque le bloc publie. Meme procede que la
--    migration 021, qui rejoue son critere en SQL — un test relit ce fichier
--    plutot que d'en recopier la regle.

CREATE TABLE prompt_odds (
  session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id     INTEGER NOT NULL REFERENCES events(id)   ON DELETE CASCADE,
  bookmaker    TEXT NOT NULL,
  market_key   TEXT NOT NULL,
  outcome_name TEXT NOT NULL,
  description  TEXT,               -- nom du joueur pour les props
  point        REAL,
  price        REAL NOT NULL,
  fetched_at   TEXT NOT NULL,      -- quand le prix a ete releve chez le fournisseur
  captured_at  TEXT NOT NULL       -- quand ce releve a ete fige pour la session
);

-- Pas de cle primaire composite : `point` est nullable, et SQLite laisse les
-- NULL se dupliquer dans une PK. L'unicite est tenue par le service, qui
-- remplace le releve entier d'un (session, match) — un index suffit donc, et
-- c'est celui de la seule lecture qui existera.
CREATE INDEX idx_prompt_odds_lot ON prompt_odds(session_id, event_id);

ALTER TABLE picks ADD COLUMN market_key TEXT;        -- cle de marche figee a l'ecriture
ALTER TABLE sessions ADD COLUMN scale_version TEXT;  -- echelle de confiance en vigueur
ALTER TABLE prompts ADD COLUMN feedback_active BOOLEAN NOT NULL DEFAULT 0;

UPDATE prompts SET feedback_active = 1 WHERE body LIKE '%Taux de réussite de%';
