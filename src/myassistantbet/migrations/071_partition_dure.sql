-- 071_partition_dure.sql — les bandes de cote reprennent la partition du cadre.
--
-- La configuration servie avait derive du cadre d'analyse, et deux incoherences
-- arithmetiques disaient dans quel sens : le combine solide vise une cote de 9
-- sur 4 jambes, or `1.70^4 = 8.35 < 9` — un combine entierement bati en bande
-- sure etait **impossible**, ce qu'aucune surface ne signalait. A 1.80,
-- `1.80^4 = 10.50 >= 9`, et la demande redevient satisfiable. Un controle de
-- coherence garde desormais ce couple (`prompt.solid_combo_reachable`).
--
-- **Seule la frontiere SAFE/FUN bouge, et c'est mesure** : la base servie
-- portait deja 2.30, 3.60 et 8.00. Les series ULTRA FUN, GIGA FUN et GIGA+
-- restent donc integralement comparables d'un cote a l'autre de cette
-- migration ; seule celle de SAFE est coupee, et celle de FUN avec elle. Le
-- seed de la migration 003 (1.70 / 2.60 / 5.00 / 15.0) est lui plus ancien
-- encore : les cinq lignes sont reecrites pour qu'une installation neuve et
-- l'installation servie tombent sur la meme partition, ce qui n'etait plus vrai.
--
-- **La partition est dure, sans chevauchement, et c'est une decision reprise
-- apres coup.** Des bandes chevauchantes arbitrees par la confiance ont ete
-- proposees, puis ecartees sur la mesure : 70 des 352 selections en base — 20 %
-- — tombent dans les zones qu'elles auraient ouvertes. Router les confiances
-- hautes vers le palier bas et les basses vers le palier haut aurait gonfle le
-- taux de SAFE et degrade celui de FUN **par construction**, exactement sur le
-- segment ou les deux se touchent, et rendu le croisement palier x confiance
-- degenere — une confiance 3 a 1.72 n'aurait structurellement jamais pu etre
-- SAFE. Le palier sert a mesurer une bande de **cote** : la confiance a son
-- propre axe, et le gabarit continue de dire que la cote decide seule.
--
-- **Convention de borne, ecrite ici parce qu'elle n'existait nulle part** :
-- borne basse **incluse**, borne haute **exclue**. Une cote de 1.80 est FUN,
-- une cote de 8.00 est GIGA+. Elle etait deja le comportement du code — a trois
-- endroits, dont un jamais compare aux deux autres — et n'etait enoncee que
-- dans un docstring. Elle vit desormais dans `history.in_band`, lue par les
-- trois.
--
-- **Le plancher reste a 1.25**, et la question a ete posee : la case 1.25-1.40
-- porte 28 selections, 8 % du livre, encore alimentee dans le regime courant.
-- Le remonter n'aurait pas redecoupe SAFE — il aurait rendu ces cotes
-- **inenregistrables**, `_reject_out_of_band` refusant toute cote hors de
-- toutes les bandes. C'est un changement de perimetre d'emission, pas de
-- classement.
--
-- Les quotas ne sont pas touches : ils sont regles a la main et cette migration
-- ne corrige que les bornes.

UPDATE tiers SET min_price = 1.25, max_price = 1.80 WHERE key = 'safe';
UPDATE tiers SET min_price = 1.80, max_price = 2.30 WHERE key = 'fun';
UPDATE tiers SET min_price = 2.30, max_price = 3.60 WHERE key = 'ultra_fun';
UPDATE tiers SET min_price = 3.60, max_price = 8.00 WHERE key = 'giga_fun';
UPDATE tiers SET min_price = 8.00, max_price = NULL WHERE key = 'giga_plus';
