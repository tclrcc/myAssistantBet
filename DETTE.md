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

- ~~**Un match rendu 2,23 fois par session est un biais de selection.**~~
  **Fermee le 14/08/2026 par la mesure**, et fermee comme une reponse et non
  comme un abandon : le biais **ne mord pas a une taille qui expliquerait ce que
  la page mesure** — tendance du residu `z = +0,435`, `p = 0,664` bilateral — et
  surtout **la question ne se repond pas par lecture**. L'exposition est presque
  collineaire a la competition : elle mesure le decoupage, pas le sport. Detail,
  chiffres de puissance et reserve dans `CLAUDE.md`, « Le biais d'exposition ».
  - Ce qui reste vrai et qui la justifiait a moitie : un combine reste rattache a
    **un** prompt, deux jambes venues de deux prompts sur le meme match etant
    deux tirages du meme match presentes comme deux selections.
  - Ce qui en sort et qui est une **autre** dette, ci-dessous : le decoupage
    lui-meme.

- **57 % des blocs rendus sont des reprises, et les trois quarts sont des
  regenerations a l'identique.** Mesure du 14/08/2026 sur les 11 sessions :
  1 028 blocs rendus pour 447 matchs distincts, soit **581 blocs repetes**
  (~436 000 tokens de prompt). Aucun credit en jeu — un rendu ne coute rien au
  quota — mais du token et de la lecture.
  - **Le motif dominant n'est ni un recoupement manuel ni la constitution des
    lots** : 420 des 581 blocs repetes (72 %) viennent de **regenerations a
    l'identique**, 39 prompts sur 108. Session 1, huit regenerations du meme lot
    de onze blocs entre 13h44 et 14h28 ; session 3, cinq regenerations de 37 puis
    27 blocs en une heure. Ecart median avec le prompt precedent : 16 minutes.
  - Le decoupage volontaire — sous-lot d'un lot anterieur, lot elargi,
    chevauchement — ne pese que **161 blocs (28 %)**, et c'est un usage
    **documente** : `build_prompt(..., competition_id=)` existe pour ca, huit a
    douze matchs par prompt etant le volume ou l'analyse reste dense.
  - **Une regeneration n'est donc pas lue deux fois** : la seconde remplace la
    premiere, l'utilisateur ne lit que la derniere. Le cout est en generation, pas
    en attention — ce qui reduit d'autant l'enjeu, sans l'annuler.
  - Conditionne un decoupage **delibere plutot que subi**, ce qui est aussi la
    seule facon de rendre le biais d'exposition mesurable un jour.

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
