-- 077_fenetre_de_qualification.sql — de quoi importer un tableau de qualification.
--
-- Les qualifications d'un Grand Chelem n'ont **aucune** cle chez The Odds API
-- (176 cles au catalogue complet le 24/08/2026, dont 44 au tennis, pas une
-- seule qualification) et ne sont donc servies par personne cote cotes.
-- `tennis-api.com`, deja sous contrat, sert en revanche les rencontres.
--
-- ## Pourquoi une fenetre stockee, et pas un champ du fournisseur
--
-- Les rencontres de qualification portent l'identifiant du **tableau
-- principal** — 21349 cote ATP, 16743 cote WTA, `tier: Grand Slam`. Meme piege
-- que les qualifications europeennes de The Odds API, qui partagent la cle de
-- la phase de ligue. Deux discriminants se presentent, et les deux sont faux :
--
--   * **`roundId`** vaut 1 sur les 64 rencontres de qualification observees,
--     mais rien n'etablit que 1 signifie « qualification » plutot que
--     « 1er tour » — et la mesure est **hors de portee** : l'endpoint ne sert
--     rien au-dela de J+1, donc aucune rencontre de tableau principal n'est
--     visible pour trancher. Un discriminant qu'on ne peut pas verifier ne
--     s'ecrit pas ;
--   * **la date de la fiche du tournoi** annonce le 31/08 quand le tableau
--     principal debute le **30/08** : elle est fausse d'un jour, et une
--     comparaison `date < startDate` classerait une rencontre du tableau
--     principal en qualification.
--
-- La fenetre, elle, se lit sur le calendrier officiel et se saisit. Une
-- rencontre rattachee au tournoi et datee dedans **est** une qualification par
-- definition, pas par inference. Meme regle que partout : ce que l'application
-- peut etablir, elle ne le devine pas.
--
-- ## Le rattachement au fournisseur est une saisie, jamais une deduction
--
-- Meme forme que `apifootball_league_id` et que `matchesplayed_tournaments` :
-- l'identifiant de tournoi se verifie a la main sur `/{tour}/tournament/info/{id}`
-- et ne se rapproche jamais d'un libelle. Le circuit l'accompagne parce que
-- l'endpoint est par circuit et qu'aucune cle Odds API ne le porte ici —
-- `elo.tour_for` lit le prefixe d'une cle, et ces competitions n'en ont pas.
--
-- ## L'identifiant de rencontre est unique **par circuit**, pas globalement
--
-- Mesure du 24/08/2026 : les 28 rencontres ATP du jour portent les identifiants
-- 1277 a 1334, les 28 WTA les identifiants 844 a 902. Ce sont deux compteurs
-- separes, donc la collision n'est pas une hypothese mais une question de
-- temps. La cle d'idempotence est le couple (competition, rencontre), et
-- l'index partiel la tient dans le schema plutot que dans la discipline du
-- service.

ALTER TABLE competitions ADD COLUMN qualif_debut TEXT;
ALTER TABLE competitions ADD COLUMN qualif_fin TEXT;
ALTER TABLE competitions ADD COLUMN tennisapi_tour TEXT;
ALTER TABLE competitions ADD COLUMN tennisapi_tournament_id INTEGER;

ALTER TABLE events ADD COLUMN tennisapi_fixture_id INTEGER;

CREATE UNIQUE INDEX idx_events_tennisapi
  ON events(competition_id, tennisapi_fixture_id)
  WHERE tennisapi_fixture_id IS NOT NULL;

-- Les deux tableaux de qualification de l'US Open 2026, crees a la main la
-- veille. Le seed s'indexe sur le libelle : sur une base neuve il ne trouve
-- rien et ne fait rien, ce qui est le comportement voulu — ces competitions se
-- creent depuis /competitions, une par une.
--
-- Fenetre du 24/08 au 27/08 : trois sources concordantes donnent lundi 24 a
-- jeudi 27, le second tour et le tour final se jouant chacun en une journee.
-- Tennis Majors ecrit « jusqu'au vendredi 28 » et reste isole. La borne haute
-- ne se choisit pas large « au cas ou » : une rencontre rattachee au tournoi
-- et datee hors fenetre est **comptee et rapportee** par l'import, jamais
-- jetee en silence, donc un report de pluie se voit et se corrige d'un champ.
UPDATE competitions
   SET qualif_debut = '2026-08-24',
       qualif_fin   = '2026-08-27',
       tennisapi_tour = 'atp',
       tennisapi_tournament_id = 21349
 WHERE label = 'ATP US Open Qualifications';

UPDATE competitions
   SET qualif_debut = '2026-08-24',
       qualif_fin   = '2026-08-27',
       tennisapi_tour = 'wta',
       tennisapi_tournament_id = 16743
 WHERE label = 'WTA US Open Qualifications';
