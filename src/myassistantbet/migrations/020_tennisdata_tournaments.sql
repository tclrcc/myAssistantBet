-- 020_tennisdata_tournaments.sql — correspondance de nos tournois avec le jeu de donnees.
--
-- Le fichier de resultats nomme les tournois par leur **sponsor**, nous par leur
-- ville ou leur nom usuel : « ABN AMRO World Tennis Tournament » pour Rotterdam,
-- « Open Sud de France » pour Montpellier, « BMW Open » pour Munich. Aucun
-- rapprochement automatique n'est possible, et la mesure le montre crument :
--
--   * la **ville** ne suffit pas — Paris heberge le BNP Paribas Masters *et*
--     Roland-Garros, deux tournois que rien ne permet de confondre ;
--   * le **nom** ne suffit pas non plus — le Canadian Open change de ville chaque
--     annee (Montreal, Toronto), et onze villes portent plusieurs noms de tournoi,
--     tantot parce que le sponsor a change, tantot parce que ce sont deux epreuves
--     differentes.
--
-- Meme regle que `APIFOOTBALL_LEAGUES` (migration 014) : rien ne se deduit d'un
-- libelle, la table est **verifiee a la main**, tournoi par tournoi, contre la
-- ville et le niveau publies par la source. Les 43 correspondances ci-dessous ont
-- ete relevees ainsi ; une competition non renseignee ne produit simplement
-- aucune ligne « ici », et rien n'est devine.
--
-- Le champ accepte **plusieurs noms** separes par `|` : un sponsor qui change
-- renomme le tournoi sans que ce soit un autre tournoi. Le cas ne se presente pas
-- dans notre catalogue actuel, mais il se presentera — la source porte deja
-- « U.S. Men's Clay Court Championships » et « U.S.Men's Clay Court Championships »
-- pour la meme epreuve de Houston.
--
-- Le circuit n'est pas dans cette table : il se lit dans la cle (`tennis_atp_…`),
-- et c'est lui qui departage Cincinnati et Stuttgart, ou les epreuves masculine et
-- feminine portent des noms differents dans la meme ville.

ALTER TABLE competitions ADD COLUMN tennisdata_tournaments TEXT;

UPDATE competitions SET tennisdata_tournaments = CASE oddsapi_key
  WHEN 'tennis_atp_aus_open_singles'    THEN 'Australian Open'
  WHEN 'tennis_atp_barcelona_open'      THEN 'Barcelona Open'
  WHEN 'tennis_atp_canadian_open'       THEN 'Canadian Open'
  WHEN 'tennis_atp_china_open'          THEN 'China Open'
  WHEN 'tennis_atp_cincinnati_open'     THEN 'Western & Southern Financial Group Masters'
  WHEN 'tennis_atp_dubai'               THEN 'Dubai Tennis Championships'
  WHEN 'tennis_atp_french_open'         THEN 'French Open'
  WHEN 'tennis_atp_halle_open'          THEN 'Halle Open'
  WHEN 'tennis_atp_hamburg_open'        THEN 'Hamburg Open'
  WHEN 'tennis_atp_indian_wells'        THEN 'BNP Paribas Open'
  WHEN 'tennis_atp_italian_open'        THEN 'Internazionali BNL d''Italia'
  WHEN 'tennis_atp_madrid_open'         THEN 'Mutua Madrid Open'
  WHEN 'tennis_atp_miami_open'          THEN 'Miami Open'
  WHEN 'tennis_atp_monte_carlo_masters' THEN 'Monte Carlo Masters'
  WHEN 'tennis_atp_munich'              THEN 'BMW Open'
  WHEN 'tennis_atp_paris_masters'       THEN 'BNP Paribas Masters'
  WHEN 'tennis_atp_qatar_open'          THEN 'Qatar Exxon Mobil Open'
  WHEN 'tennis_atp_queens_club_champ'   THEN 'Queen''s Club Championships'
  WHEN 'tennis_atp_shanghai_masters'    THEN 'Shanghai Masters'
  WHEN 'tennis_atp_us_open'             THEN 'US Open'
  WHEN 'tennis_atp_washington_open'     THEN 'Citi Open'
  WHEN 'tennis_atp_wimbledon'           THEN 'Wimbledon'
  WHEN 'tennis_wta_aus_open_singles'    THEN 'Australian Open'
  WHEN 'tennis_wta_bad_homburg_open'    THEN 'Bad Homburg Open'
  WHEN 'tennis_wta_canadian_open'       THEN 'Canadian Open'
  WHEN 'tennis_wta_charleston_open'     THEN 'Charleston Open'
  WHEN 'tennis_wta_china_open'          THEN 'China Open'
  WHEN 'tennis_wta_cincinnati_open'     THEN 'Western & Southern Financial Group Women''s Open'
  WHEN 'tennis_wta_dubai'               THEN 'Dubai Duty Free Tennis Championships'
  WHEN 'tennis_wta_french_open'         THEN 'French Open'
  WHEN 'tennis_wta_german_open'         THEN 'German Open'
  WHEN 'tennis_wta_indian_wells'        THEN 'BNP Paribas Open'
  WHEN 'tennis_wta_italian_open'        THEN 'Internazionali BNL d''Italia'
  WHEN 'tennis_wta_madrid_open'         THEN 'Mutua Madrid Open'
  WHEN 'tennis_wta_miami_open'          THEN 'Miami Open'
  WHEN 'tennis_wta_qatar_open'          THEN 'Qatar Open'
  WHEN 'tennis_wta_queens_club_champ'   THEN 'Queen''s Club Championships'
  WHEN 'tennis_wta_strasbourg'          THEN 'Internationaux de Strasbourg'
  WHEN 'tennis_wta_stuttgart_open'      THEN 'Porsche Tennis Grand Prix'
  WHEN 'tennis_wta_us_open'             THEN 'US Open'
  WHEN 'tennis_wta_washington_open'     THEN 'Citi Open'
  WHEN 'tennis_wta_wimbledon'           THEN 'Wimbledon'
  WHEN 'tennis_wta_wuhan_open'          THEN 'Wuhan Open'
END
WHERE oddsapi_key LIKE 'tennis_%';
