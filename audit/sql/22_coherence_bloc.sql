-- Le niveau declare est-il porte par au moins un fait cite a ce niveau ?
--
-- **Le niveau d'une selection est celui du fait qui porte l'angle**, ni le
-- maximum ni le minimum des faits cites : un fait faible dans le faisceau ne
-- contamine pas la declaration. Le controle ne peut donc pas demander l'accord
-- avec le maximum — il demande seulement qu'un fait **existe** au niveau
-- declare. Verifie sur le pick 552, qui cite deux niveaux 2 et un niveau 4 et
-- declare 2 : conforme, et un controle plus strict l'aurait accuse a tort.
WITH bloc AS (
  SELECT p.id, p.session_id, p.selection, p.source_level, p.confidence, p.confidence_computed,
         (SELECT COUNT(*) FROM json_each(json_extract(p.claim_raw_json, '$.faits'))) AS faits,
         (SELECT COUNT(*) FROM json_each(json_extract(p.claim_raw_json, '$.faits')) f
            WHERE json_extract(f.value, '$.niveau') IN (1, 2))                       AS faits_1_2
  FROM picks p
  WHERE p.claim_raw_json IS NOT NULL
)
SELECT id, session_id, substr(selection, 1, 28) AS selection,
       source_level AS niveau_declare, faits, faits_1_2, confidence, confidence_computed
FROM bloc
WHERE source_level IN ('1', '2') AND faits_1_2 = 0
ORDER BY id;
