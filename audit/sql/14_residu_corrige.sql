-- Les deux corrections cumulees, sur les selections qui portent les deux
-- informations : l'overround du livre, et la cote reellement obtenue.
SELECT CASE p.exploratoire WHEN 0 THEN 'C' ELSE 'C-bis' END AS pop,
  COUNT(*) n,
  SUM(p.result='win') AS observees,
  ROUND(SUM(1.0/p.price),2)                          AS impl_cote_bloc,
  ROUND(SUM((1.0/p.price)/(1.0+v.ovr)),2)            AS impl_marge_retiree,
  ROUND(SUM(1.0/p.price_real),2)                     AS impl_cote_obtenue,
  ROUND(SUM((1.0/p.price_real)/(1.0+v.ovr)),2)       AS impl_les_deux,
  ROUND(SUM(p.result='win')-SUM(1.0/p.price),2)                    AS residu_brut,
  ROUND(SUM(p.result='win')-SUM((1.0/p.price_real)/(1.0+v.ovr)),2) AS residu_corrige
FROM picks p JOIN v_pick_ovr v ON v.pick_id=p.id
WHERE p.price_real IS NOT NULL AND p.result IN ('win','loss')
GROUP BY 1;
