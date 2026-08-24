-- 078_fenetre_de_rattachement.sql — la fenetre n'est pas propre aux qualifications.
--
-- La migration 077 l'a nommee `qualif_debut` / `qualif_fin` parce que le premier
-- cas servi etait un tableau de qualification, ou elle distingue effectivement
-- la qualification du tableau principal — les deux partageant l'identifiant de
-- tournoi chez le fournisseur.
--
-- **Le nom devient faux des le deuxieme cas.** Winston-Salem est un ATP 250 que
-- The Odds API ne sert pas du tout : il entre par le meme chemin, avec sa propre
-- fenetre du 23 au 29/08. Ecrite `qualif_debut = 2026-08-23`, la ligne se lirait
-- « les qualifications commencent le 23 » — or les qualifications de ce tournoi
-- se sont jouees les 22 et 23, et le 23 au 29 est le **tableau principal**. Le
-- prochain lecteur recopierait la lecture fausse.
--
-- Ce que la colonne porte vraiment : **les dates pendant lesquelles les
-- rencontres de ce tournoi chez le fournisseur appartiennent a cette
-- competition**. Sur un tableau de qualification c'est une partie du tournoi ;
-- sur un tournoi entier c'est le tournoi. Les deux sont des faits vrais, et
-- c'est le nom qui doit couvrir les deux.
--
-- **La garde ne bouge pas** : la fenetre reste obligatoire, y compris quand elle
-- ne discrimine rien. Elle ne coute rien a saisir, elle borne l'edition, et une
-- rencontre datee dehors reste comptee et rapportee plutot que jetee — ce qui
-- reste la seule facon de voir un tour repousse.
--
-- Un simple renommage : aucune valeur ne bouge, aucune ligne n'est reprise.

ALTER TABLE competitions RENAME COLUMN qualif_debut TO fenetre_debut;
ALTER TABLE competitions RENAME COLUMN qualif_fin   TO fenetre_fin;
