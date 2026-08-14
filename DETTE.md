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
