# Dette technique

Ce qui est **connu, delimite et non traite**. Une ligne par dette : ce qu'elle
est, pourquoi elle n'a pas ete reglee, et ce qu'elle conditionne. Une dette sans
consequence nommee n'en est pas une — elle se supprime.

- **`REFERENCE_BOOKMAKERS` melange deux roles** — books jouables (Betclic,
  Unibet) et ancres de devigorisation (Pinnacle, non jouable en France). A
  scinder ; conditionne la reouverture du ticket William Hill (+845 credits).

- **Le budget de recherche et les quotas hauts se bornent par prompt, donc le
  decoupage les multiplie.** `research_capped` s'applique a `len(blocks)`, et une
  session genere 3 a 20 prompts : mesure du 14/08/2026 sur les 11 sessions, les
  places hautes autorisees passent de 66 a 460 (**7,0x**) et les dossiers
  nominalement ouverts de 77 a 613 (**8,0x**) selon qu'on compte par session ou
  prompt par prompt. Le meme match est rendu 2,23 fois en moyenne dans sa
  session, jusqu'a 13 fois. Le plafond reel de la methode est donc fonction du
  decoupage, pas de la methode.
  - **N'a jamais mordu, et de loin** : 9 selections hautes consommees en tout,
    soit 2 % de l'autorise reel et **14 % de ce qu'un prompt unique par session
    autoriserait**. Aucune session ne depasse la borne stricte.
  - Ce qui fuit vraiment est **l'annonce** : le prompt ecrit « cette session
    ouvre 7 dossiers » dans chacun des 20 prompts d'une meme journee, et chaque
    instance le lit comme son propre budget.
  - Conditionne la remontee de la borne au niveau session — decision non prise,
    la mesure ne montrant aujourd'hui aucun depassement a corriger.

- **Un match rendu 2,23 fois par session est un biais de selection, et il porte
  sur toute la mesure.** Mesure du 14/08/2026 : 1 028 blocs servis pour 461
  matchs distincts, un match vu jusqu'a **13 fois** dans la meme session, 55 des
  72 matchs du 06/08 vus plusieurs fois. Treize instances qui s'ignorent
  analysent le meme match : la selection retenue dessus n'est pas la conclusion
  d'une analyse, c'est le **maximum de treize tirages independants**. Un angle
  qu'une instance ecarte, une autre le prend.
  - Ce qu'il faudrait mesurer pour savoir s'il mord : sur les selections
    tranchees, le taux de reussite de celles issues d'un match **vu une seule
    fois** contre celles issues d'un match **vu N fois**. Si les secondes sont
    moins bonnes, le biais est reel.
  - Consequence si elle l'est : il faudra **dedupliquer en amont** — ne pas
    resservir un match deja rendu dans la session — plutot que d'ecarter des
    selections apres coup.
  - C'est aussi ce qui justifie qu'un combine reste rattache a **un** prompt :
    deux jambes venues de deux prompts sur le meme match seraient deux tirages
    du meme match presentes comme deux selections.

- **Le chemin des coupons est complet, atteignable, et n'a jamais servi.** Route
  `POST /history/{session_id}/coupons`, `coupons.create` / `attach` /
  `play_single`, panneau dans `picks.html`, capture televersee : tout existe.
  Au 14/08/2026, **0 coupon** et `picks.played` faux sur les 149 selections.
  - Il n'est ni supprime ni complete : il repond a une question reelle — ce que
    valent les paris poses — qui se posera le jour ou l'habitude de saisir les
    coupons existera.
  - `attach()` est le **seul** ecrivain de `picks.played`, et il ecrit aussi
    `picks.coupon_id`. C'est pourquoi les combines d'analyse (migration 046) ont
    leur propre table et ne touchent ni l'un ni l'autre : les faire passer par
    `coupons` ferait apparaitre comme paris poses des combines que personne n'a
    joues.
