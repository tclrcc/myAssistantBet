# CLAUDE.md — instructions de travail sur ce depot

`SPEC.md` est la source de verite du projet. Ce fichier en est le rappel operationnel :
commandes, conventions, et surtout la liste des interdits. En cas de contradiction,
`SPEC.md` gagne.

## Commandes

```bash
uv sync                                                    # installer / mettre a jour l'env
uv run uvicorn myassistantbet.main:app --reload --port 8022 # lancer l'app en dev
uv run pytest                                              # tests
uv run pytest tests/test_db.py -k migrations               # un sous-ensemble
uv run ruff check . --fix                                  # lint (avec corrections sures)
uv run ruff format .                                       # formatage
curl -s localhost:8022/health | python3 -m json.tool       # etat de l'app en dev
```

Trois ports, a ne pas confondre : **8022** pour le developpement, **8021** pour l'instance
servie par systemd, **8000** pour un autre service de la machine — c'est pour lui que le
defaut d'uvicorn ne convient pas ici.

```bash
sudo systemctl status myassistantbet          # etat du service
sudo systemctl restart myassistantbet         # apres un git pull ou un changement de .env
sudo journalctl -u myassistantbet -f          # logs en direct
curl -s localhost:8021/health | python3 -m json.tool
```

`uv` s'installe avec `curl -LsSf https://astral.sh/uv/install.sh | sh` (binaire dans `~/.local/bin`).

## Interdits — section 9 de SPEC.md

Ne code **jamais** ces choses, meme si elles semblent utiles :

1. Calcul de value, EV, edge, CLV, probabilites implicites, devigging, Kelly, mise conseillee.
   L'app est un outil de collecte et de mise en forme, pas un modele.
2. Authentification, gestion d'utilisateurs, roles.
3. Scraping de Betclic ou de tout autre bookmaker. Les cotes viennent d'APIs sous contrat.
4. Framework JS front (React, Vue, Next), bundler, TypeScript.
5. PostgreSQL, Redis, Docker Compose multi-services, Celery. Le projet tient sur un processus
   et un fichier SQLite.
6. Appel automatique a l'API Anthropic. L'app produit un prompt, l'humain le colle. Point.
7. Placement automatique de paris, ou toute integration transactionnelle avec un bookmaker.

Le front est en HTMX + CSS vanilla : pas de build step, pas de `node_modules`.

## Architecture — separation stricte

- `providers/` : clients d'APIs externes. Ne connaissent rien du metier. Timeouts explicites,
  retry avec backoff sur 429/5xx, comptabilisation du quota.
- `services/` : logique metier. **Aucun appel HTTP direct.** Recoit ses donnees des providers.
- `main.py` : app FastAPI, routes, cycle de vie. **Aucune logique metier.**

## Conventions

- **Dates** : stockage en UTC, chaines ISO 8601 (`db.utcnow()`). Affichage en `Europe/Paris`.
- **Migrations** : `NNN_description.sql` dans `src/myassistantbet/migrations/`, appliquees au
  demarrage, tracees dans `schema_migrations`. Une migration deja appliquee ne se modifie
  **jamais** : creer un nouveau fichier.
- **Idempotence** : relancer un scan ne duplique aucune ligne. Upserts sur cles naturelles.
- **Cotes** : une ligne par outcome dans `odds`. Jamais de blob JSON de cotes.
- **Matchs reportes** : `dossier.status_lines()` rend une ligne `Statut` en **tete** du
  bloc — `reporte`, `annule`, `forfait`, `horaire non fixe`. **L'information dormait
  deja en base** : `_summarize` garde le statut de chaque match de la saison, et le
  match analyse figure forcement dans l'historique de sa propre equipe. Personne ne le
  lisait, si bien que le bloc a servi Rakow - Zaglebie avec ses cotes le jour ou il
  etait reporte **depuis neuf jours** — seule une recherche exterieure l'a rattrape.
  Aucun appel n'est ajoute.
  - Le rapprochement se fait sur la **journee**, jamais sur l'heure exacte : un report
    s'accompagne souvent d'un changement d'horaire, et exiger la minute ferait manquer
    le cas cherche. Une equipe ne joue pas deux fois le meme jour.
  - Le match **reste dans le prompt**, contrairement a un match commence : le statut
    est un releve de fournisseur, pas l'horloge. Le taire ou retirer l'evenement sur
    un flag qui peut etre perime serait pire que l'afficher marque.
  - **Une absence de ligne ne prouve pas qu'un match aura lieu** — elle dit que rien
    ne s'y oppose dans ce que nous savons. Le preambule le dit, et il est garde par
    `{% if 'Statut' in context_labels %}` : il ne se paie que sur les lots concernes.
- **Matchs commences** : `session.has_started()` porte la regle. Un evenement dont l'heure est
  passee sort du prompt, de l'enrichissement et du compteur de selection — il quitte deja le
  board — mais **reste attache a la session** : l'historique des picks s'appuie dessus. Il est
  affiche marque « commence », jamais retire tout seul.
- **Secrets** : uniquement via l'environnement / `.env`. Jamais dans le code, les logs, les
  reponses HTTP ni les fixtures de test. Corollaire non evident : **`httpx` journalise en
  INFO l'URL complete de chaque appel**, cle d'API comprise — `apiKey=…` se retrouvait en
  clair dans `journalctl`. Son logger est donc remonte a `WARNING` dans `main.py`. Nos
  propres lignes disent deja l'endpoint, le cout, les credits restants et la duree : les
  taire ne fait perdre que le secret.
- **Erreurs** : une API indisponible n'empeche jamais de servir la page. Les donnees partielles
  sont affichees avec une mention visible de ce qui manque — jamais de silence.
- **Logs** : logging structure, un log par appel externe avec endpoint, cout, credits restants
  et duree.
- **Tests** : aucun acces reseau, jamais. Les APIs se mockent avec `respx`, sur des fixtures
  JSON reelles anonymisees dans `tests/fixtures/`. Couverture utile visee sur `render.py`,
  `matching.py` et le calcul de cout quota — pas un pourcentage global.
- **Style** : `ruff` (lint + format), ligne a 100 caracteres. Docstrings et commentaires en
  francais, sans accents dans le code Python.

## Methode de travail

Travailler **phase par phase** (section 10 de `SPEC.md`). A la fin de chaque phase :
`ruff` et `pytest` verts, un commit atomique, un resume, puis **attendre la validation avant
de demarrer la phase suivante**. Ne pas prendre d'avance.

Messages de commit : descriptifs, en francais, sans mention d'outil ni de co-auteur.

## Cout du quota The Odds API

`cout = nb_marches x nb_regions`. Le parametre `bookmakers` prime sur `regions`, et chaque
groupe de 10 bookmakers compte pour 1 region : interroger `bookmakers=betclic_fr` seul coute
donc 1 par marche. `/sports` et `/sports/{sport}/events` sont gratuits, les reponses vides ne
sont pas facturees. Les headers `x-requests-remaining` et `x-requests-used` doivent etre
persistes dans `api_usage` a chaque appel et exposes dans l'UI.

Le header `x-requests-last` donne le cout reellement facture : il fait foi quand il est
present, `expected_cost()` ne servant que de repli et d'estimation avant appel. Une reponse
servie par le cache de developpement (`DEV_CACHE=1`) ne consomme rien et n'ecrit rien dans
`api_usage`.

Etage A (`services/scan.py`) : `h2h,totals` sur `betclic_fr` seul, soit 2 credits par
competition active.

`services/coverage.py` memorise ce qu'une competition sert vraiment, pour ne pas repayer
deux fois le meme constat vide. **Les books interroges font partie de la cle** : un marche
constate absent chez Betclic seul ne prouve rien sur un book de reference, et le
`REFERENCE_BOOKMAKERS` ajoute apres coup rouvre la question. Un constat vaut pour
l'ensemble qui l'a produit et pour tout ensemble plus etroit, jamais plus large.

Etage B (`services/enrich.py`) : 1 credit par marche profond, match par match. 14 marches
football, 16 sur les competitions de `PLAYER_PROPS_LEAGUES` (props buteurs), 10 en tennis.
Le cout est estime **avant** l'appel et compare a `ODDS_API_CREDIT_FLOOR` : sous le plancher,
aucun appel n'est emis.

**Un credit de plus quand l'etage A n'a rien ramene** (`FOOTBALL_BASE_MARKETS`). Le 1N2
vient du scan, chez Betclic seul, et l'etage B ne le rachete pas — sauf sur une competition
que **Betclic ne sert pas du tout** (Super League chinoise, Veikkausliiga), ou il n'arrivait
alors jamais. Pire, il ne pouvait pas non plus etre declare manquant : la ligne
« Non servis » se calcule sur `markets_for()`, qui l'excluait. Le marche disparaissait du
bloc sans laisser de trace, et une analyse reelle s'est rabattue sur le handicap sans savoir
pourquoi. `enrich` et `session._unserved_for` lisent donc tous deux la meme chose — le match
porte-t-il une cote `h2h` ? — et passent `base_served` a `markets_for()`.
  - `totals` n'est **pas** dans cette liste : `alternate_totals` est deja demande et le rendu
    les fusionne dans la meme ligne O/U. Le reclamer paierait une ligne deja affichee.
  - `coverage.useful(..., anchor_alone=)` leve pour ce cas la regle « un reliquat reduit au
    seul `h2h` ne vaut pas l'appel ». Elle est juste quand l'etage A possede deja la cote ;
    elle est fausse quand il n'a rien ramene, ou ce `h2h` est le seul 1N2 obtenable.

## Le rendu compact (`services/render.py`)

C'est le composant le plus important : la qualite de l'analyse depend de sa densite. Regles
non negociables, toutes couvertes par des tests :

- une ligne sans donnee est **omise**, jamais vide ni « N/A » ;
- une donnee volontairement absente devient une ligne explicite
  (« donnees non disponibles pour cette competition ») ;
- cotes a deux decimales ; scores exacts limites aux 10 cotes les plus basses, triees
  croissant ; lignes O/U limitees aux 5 plus proches de la ligne principale ;
- libelle sur 12 caracteres, indentation de 2, continuations alignees a 14. **Le
  separateur entre le libelle et sa valeur n'existe pas en propre** : c'est le
  remplissage du champ qui le fabrique, donc un libelle de 12 caracteres ne laisse
  rien et sort colle a sa valeur. Constate sur un prompt reel :
  `Buts encais.Lillestrom >0.5 10/15`. Les cles de marche etaient deja tronquees a
  `LABEL_MAX` ; les libelles de contexte, eux, ne passaient par aucune troncature.
  Un test parcourt donc `CONTEXT_ICONS` entier — **11 caracteres utiles**, et
  `Buts encais.` est devenu `Buts pris`, qui dit la meme chose et rappelle le
  « pris » de la ligne `Corners`. `line()` degrade en plus proprement : un libelle
  trop long decale sa ligne d'une colonne, ce qui se voit, au lieu de souder deux
  mots, ce qui se lit de travers ;
- un marche paye mais non modelise est rendu brut plutot que perdu silencieusement ;
- l'en-tete ne nomme que le book principal ; **toute ligne servie par une autre source la
  porte en fin de ligne** (`[Pinnacle (ref.)]`, `[saisie manuelle]`, `[dont …]` quand une
  ligne fusionnee melange les deux). Un en-tete « Betclic + Pinnacle (ref.) » laissait
  deviner quelle cote etait jouable et laquelle ne faisait que situer le marche ;
- **Les lignes en quart ne se posent pas au football** (`-0.25`, `+0.75`, `1.25`,
  `2.75`). Ce sont des paris asiatiques **scindes** — une demi-mise sur chacune des
  deux lignes voisines — et aucun book français ne les propose. Elles restent
  **affichees**, parce qu'elles situent le match mieux qu'aucune autre ligne ; c'est
  la **selection** qui est interdite, et sa traduction se donne sous le tableau,
  sans cote, comme le score exact en sets au tennis. Mesure qui l'a declenche : sur
  une analyse reelle, les **deux** selections rendues portaient une ligne en quart
  (`Over 2.75`, `Slask -0.25`), donc deux paris impossibles a poser.
  - Le prompt donne la traduction (`+1.25` sur X = « X ne perd pas, ou perd d'un
    but exactement ») **et** l'equivalent des autres lignes, qui restent
    selectionnables : `0` = rembourse si nul, `±0.5` = victoire seche ou double
    chance, `±1` = handicap europeen, totaux en `.5` tels quels. Sans cette
    seconde moitie, la regle rejouerait le degat du libelle « Non jouable » —
    faire renoncer a des paris posables.
  - **`A relever` affirmait « le bookmaker les propose bien sur son site », et
    c'etait une generalisation.** Le fait a ete verifie **au tennis**, ou il est
    vrai ; au football il est faux pour les lignes asiatiques. La phrase est donc
    gardee par sport, ce qui fait au passage economiser au football un
    paragraphe qui ne le concernait pas.
  - La regle ne peut pas se poser sur `A relever` : un evenement servi par un book
    de substitution n'en produit aucune et porte pourtant les memes lignes
    (`Kilmarnock +1.25` chez Pinnacle). Elle se lit sur la **valeur de la ligne**.
- **`A relever` est un troisieme etat** : le marche est affiche, mais aucun de ses prix ne
  vient du book principal. Sur 127 matchs de tennis a venir, `betclic_fr` ne sert **que**
  le `h2h` via The Odds API — tout le handicap jeux et tout le total de jeux viennent de
  Pinnacle. Chaque ligne le disait par son `[Pinnacle (ref.)]`, mais il fallait les lire
  toutes pour le voir.
  - **Le premier libelle, « Non jouable », se trompait de mot, et l'erreur a coute
    exactement ce qu'elle devait epargner.** Betclic sert bien ces marches **sur son
    site** : c'est notre collecte qui ne les remonte pas, pas son offre qui manque. Une
    analyse reelle a renonce a deux angles de jeux pour se rabattre sur le vainqueur,
    alors que les paris etaient posables. La ligne dit donc ce qu'il y a **a faire** —
    relever le prix avant de miser — et le preambule ajoute noir sur blanc qu'un marche
    « A relever » est un marche **selectionnable**, avec sa cote de reference et sa
    mention `(ref.)`.
  - La ligne est **seche, sans note** — `Non servis` porte la sienne parce qu'elle a trois
    causes, celle-ci n'en a qu'une et le preambule l'explique une fois pour tout le lot.
  - Le rapprochement se fait sur le marche **fusionne**, comme pour `Non servis` : un
    `spreads` servi par Betclic et un `alternate_spreads` servi par Pinnacle partagent une
    ligne, et la declarer non jouable ferait chercher un prix affiche juste au-dessus.
  - Un evenement servi par un **book de substitution** n'en produit aucune : tous ses prix
    sont de reference par construction, et le bloc le dit deja en entier.
- les marches demandes a l'API et jamais servis deviennent une ligne `Non servis` : une
  absence constatee est une information, et la taire fait chercher un handicap jeux qui
  n'existe pas. **Trois causes distinctes**, portees par `session._unserved_for` :
  `coverage` pour ce que le book a refuse sur la competition ; la difference entre demande
  et recu **pour ce match** quand l'etage B a tourne — `coverage` raisonne par competition
  quand le service se fait par match, et un handicap jeux servi sur une affiche et pas sur
  l'autre produisait un silence sur la seconde ; et l'offre du book de substitution quand
  The Odds API ne connait pas la rencontre. Un evenement d'etage A n'annonce rien de
  profond : ces marches n'ont pas ete reclames, et les lister ferait chercher une panne la
  ou il n'y a qu'un enrichissement jamais lance.
- `services/markets.py` detient la liste des marches demandes, parce que `enrich` et
  `session` en ont besoin tous les deux. La copier des deux cotes les aurait fait diverger,
  et le prompt aurait annonce `Non servis` sur un marche que l'outil ne demande plus.

Ajouter un marche : une entree dans `MARKET_ORDER`, un rendu dedie si sa forme le merite,
et un test. Sans rendu dedie, le repli generique s'applique — c'est acceptable, pas une
regression. L'entree, elle, ne se negocie pas : les deux props buteurs n'en avaient aucune
et sortaient en **cle brute** (`player_goal_scorer_anytime`) dans la ligne « Non servis »
d'un match de Ligue 1. Meme piege que `alternate_totals` avant elles, et il se reproduira a
chaque marche ajoute a `markets.py` sans l'etre a `render.py`.

## Contexte sportif et mapping

- `providers/apifootball.py` : piege du fournisseur, **les erreurs applicatives arrivent en
  HTTP 200** dans le champ `errors` de l'enveloppe. Le client les convertit en `ProviderError`.
  Corollaire **traite** : une erreur de **debit** arrive par le meme chemin, donc en HTTP
  200, et `RETRY_STATUSES` ne la voit pas. `_transient_payload_error()` la distingue d'une
  cle invalide — celle-ci doit echouer tout de suite, un debit depasse doit etre retente —
  et declenche le backoff. Verifie en direct : « nouvelle tentative 2/3 dans 5.0s » sur un
  enrichissement en rafale, la ou l'appel laissait auparavant un trou definitif.
- **`/fixtures` exige `season` des qu'on passe `league`**, sinon l'API repond
  « season: The Season field is required ». L'appel echouait donc toujours, et l'absence de
  contexte qui en resultait se lisait comme un probleme de rapprochement de noms.
- La saison **se lit chez le fournisseur** (`current_season()`, champ `current` de
  `/leagues`), elle ne se deduit pas de la date : un championnat joue en annee civile (MLS,
  Bresil, Norvege) et un match de fevrier donneraient tous deux un resultat faux. Elle est
  memorisee par ligue le temps d'un enrichissement.
- `competitions.APIFOOTBALL_LEAGUES` : la correspondance entre une cle The Odds API et une
  ligue API-Football. **Sans identifiant de ligue, `enrich.context_possible` est faux et
  aucun contexte n'est jamais demande** — la competition reste muette sans que rien ne le
  signale. La synchronisation applique la table aux competitions creees et comble un manque
  sur les existantes, mais **n'ecrase jamais une saisie manuelle**. Une cle absente de la
  table se rattache depuis `/competitions`.
  Meme regle que la surface et le niveau : **rien ne se deduit d'un libelle**. Le
  rapprochement automatique a ete essaye et rejete — il donnait la Championship ecossaise
  (180) pour l'anglaise (40), la Bundesliga (78) pour la 2. Bundesliga (79) et la Coupe de
  Malaisie (499) pour la MLS (253), le tout avec un score maximal. Trois tests gardent ces
  trois pieges.
- **Statistiques de saison** (`KIND_FORM`, cinq lignes) : `/teams/statistics` est appele
  pour la forme, et sa charge utile est persistee **entiere**. Longtemps seuls `form` et
  `Dom/Ext` en etaient tires ; le reste dormait en base alors que les marches
  correspondants etaient achetes. Ces lignes ne coutent **aucun appel** : `Buts marq.`
  (`goals.for.under_over` → `team_totals`), `Clean sheet` (→ `btts`), `1re MT`
  (`goals.*.minute` → `totals_h1`, `halftime_fulltime`), `Cartons tps` (→
  `alternate_totals_cards`), `Formations`.
  - `Buts pris` est le **miroir** de `Buts marq.`, et dormait dans la meme charge
    utile : `goals.for.under_over` etait lu, `goals.against.under_over` jamais. On savait
    dans combien de matchs une equipe avait marque deux buts, pas dans combien elle en
    avait encaisse deux — la seule des deux qui decrive une defense. `_under_over_fragment`
    sert les deux cotes, pour qu'un seuil ajoute ne le soit pas d'un cote seulement.
  - **Des fractions, jamais des pourcentages.** Une frequence observee decrit le passe,
    ce qui reste permis ; ecrite « 56 % », elle invite a la diviser par une cote, et c'est
    le calcul d'esperance de la section 9. `9/16` porte la meme information et le meme
    compte que les moyennes du profil. Le template porte l'interdit, un test le verifie.
  - `Buts marq.` porte les buts **de l'equipe seule**, jamais le total du match : deux
    distributions d'equipes ne s'additionnent pas en un O/U de rencontre.
  - Sous `SEASON_MIN_MATCHES` (5) matchs joues, **aucune ligne**. Le fournisseur repond des
    zeros partout pour une equipe qui n'a rien joue dans la competition — le cas de toute
    equipe entrant en qualification europeenne, ou « >0.5 0/0 » ne decrirait personne.
  - **`Buts tard.` est le miroir de `1re MT`**, dans la meme charge utile et pour aucun
    appel : `_half_fragment` lisait `goals.*.minute` et n'en prenait que les trois
    premieres tranches. C'est le **seul signal de maniere** du bloc football — `xG` et
    `Tirs` disent le volume produit, jamais le moment ou il tombe — donc le seul qui
    reponde a ce que la section B reclame pour sortir du 1N2.
    - **La fenetre est `76-90` + `91-105`, pas celle des cartons, et c'est mesure.**
      Sur les equipes ayant marque ou encaisse au moins vingt buts, les deux fenetres
      ont le meme ecart absolu entre premier et dernier decile (25 points) mais pas la
      meme base : mediane de 39 % apres la 60e contre 24 % apres la 75e. Rapporte a sa
      base, le quart d'heure final discrimine donc deux fois plus. Chaque ligne ecrit
      sa fenetre dans sa valeur, pour qu'aucune ne se lise a la place de l'autre.
    - Mesure : KFUM Oslo n'a rien marque apres la 75e en dix-neuf buts, SJK en met
      huit sur vingt-trois, Sichuan Jiuniu encaisse seize de ses trente-neuf buts
      dans ce quart d'heure.
  - `cards.yellow` porte une tranche de libelle **vide** : un carton dont la minute est
    inconnue. Elle compte au total mais a aucune mi-temps — l'omettre du denominateur
    surestimerait la part des cartons tardifs.
  - `biggest.streak` est le **record de la saison**, pas la serie en cours : il n'est pas
    rendu, ce serait l'inverse de ce qu'on croit lire.
- **Profil corners / cartons / tirs** (`KIND_PROFILE`) : moyennes sur les `PROFILE_LAST`
  derniers matchs, via `/fixtures/statistics`. Un appel rend **les deux equipes**, donc le
  « concede » vient de l'adversaire du meme match sans appel supplementaire. La
  memorisation est **par rencontre et non par equipe** : deux adversaires qui se sont
  croises recemment la partagent. Rapprochement **par libelle** (`PROFILE_STATS`), jamais
  par position : l'ordre de la liste `statistics` varie d'un match a l'autre.
  - **Le drapeau de couverture vit dans un sous-objet** : `coverage.fixtures.
    statistics_fixtures`, la ou `standings` et `injuries` sont a la racine. Le lire comme
    les autres renvoyait toujours l'absence, et sans lui jusqu'a dix appels par match
    etaient payes pour rien — la Primeira Liga 2026 l'annonce a `false`, chaque appel
    revient vide et les trois lignes disparaissaient en silence. Non couvert, une seule
    ligne `Stats match` le dit : trois absences separees se feraient expliquer une par une.
  - Sous `PROFILE_MIN_MATCHES` (3) matchs effectivement renseignes, **aucune ligne**. La
    couverture est irreguliere : en debut de saison, un seul des cinq derniers matchs
    revient rempli, et « 2.0 corners pris 9.0 » sur une rencontre se lit comme une
    tendance alors que c'est une soiree. La donnee est quand meme persistee — c'est la
    publication qui attend, pas la collecte.
  - Le compte accompagne toujours la moyenne (`5.2 pris 6.4/5`), meme regle que le nombre
    de paris a cote d'un taux.
- `services/fixtures.py` : **les matchs que The Odds API ne sert pas entrent par
  API-Football**. Les tours preliminaires d'Europa League et de Conference League n'ont
  chez le fournisseur de cotes *aucun evenement* ; le fournisseur de contexte les connait,
  les date et les nomme. Ils arrivent donc sans cotes, qui se saisissent a la main.
  - Garde-fou qui evite les doublons a la racine : **on n'importe que ce qui n'est pas
    servi** (`api_active = 0`). Une competition servie par les deux produirait deux fois le
    meme match, sous deux orthographes que rien ne saurait rapprocher — « KFUM » et
    « KFUM Oslo » sont deja au-dessus du seuil de `matching.py`.
  - Cle naturelle : `apifootball_fixture_id`. Relancer un import ne duplique rien, et un
    report d'horaire met la ligne a jour au lieu d'en creer une seconde.
  - `source = 'apifootball'`, distincte de `api` et de `manual` : savoir d'ou vient un
    match explique pourquoi il n'a pas de cotes, et evite de chercher une panne de scan.
- **Cotes de substitution** (`import_odds`) : pour un match sans aucune cote, un releve
  chez un book proche de Betclic. **Betclic n'est pas au catalogue d'API-Football** — il
  faut donc un substitut, et « proche » se mesure au lieu de se supposer. Sur des matchs
  servis par les deux fournisseurs, l'ecart moyen absolu au prix Betclic valait 3.0 % pour
  BetVictor, 3.4 % pour William Hill et 888Sport, contre 5.4 % pour Unibet, 6.0 % pour
  Pinnacle et 6.8 % pour 1xBet : **l'intuition « un book francais sera le plus proche »
  etait fausse**. L'ordre se regle par `APIFOOTBALL_BOOKMAKERS`, l'echantillon etant court.
  - **Aucun repli sur un book hors liste** : prendre le premier venu ferait passer pour
    jouable un prix dont l'ecart n'a jamais ete mesure. Une absence constatee est dite.
  - Ces prix portent le suffixe `(ref.)` comme les autres books de reference : ils situent
    le marche, ils ne sont pas jouables tels quels.
  - Le releve remplace **le seul book releve** : ni Betclic, ni la saisie manuelle.
  - Le bouton n'apparait que sur un evenement sans aucune cote. Ailleurs il n'ajouterait
    qu'un prix non jouable a cote d'un prix jouable.
  - **`run_enrich` les prend en charge** (`Estimate.substitutes`) : un match que The Odds
    API ne connait pas etait auparavant classe « aucun appel possible » et ressortait sans
    cotes **ni contexte**, si bien qu'une shortlist entiere de qualifs Europa produisait un
    prompt vide. Il recoit desormais son releve de substitution et son contexte dans le
    parcours normal — cocher, enrichir, generer. Le garde-fou de credit ne s'y applique
    pas : rien ne s'achete, et bloquer un releve gratuit parce que le quota est bas serait
    un refus sans objet.
- La **fiche d'un match** affiche le bloc CONTEXTE (`_event_context.html`), relu en base et
  sans aucun appel reseau. Sans lui, tout ce qui etait recupere n'existait que dans le
  prompt genere — donc invisible tant qu'une session n'avait pas ete montee.
- **La couverture declaree par le fournisseur fait foi** (`season_coverage`) : `/leagues`
  publie, par competition, ce qu'il sert. La Conference League annonce
  `injuries: false` et `standings: false`. Sans le lire, une liste d'absents vide se rendait
  « aucun signale » — **l'affirmation inverse de la verite** : sur vingt-sept qualifications
  europeennes, la ligne niait six absents a Motherwell et sept a l'Ajax, tous annonces par
  la presse. Une donnee non couverte devient une ligne « donnees non disponibles », et son
  appel n'est plus emis.
- **`Dom/Ext` n'est pas rendu sur zero match joue** : le fournisseur repond alors
  `0V-0N-0D` et une moyenne de `0.0`, indiscernables d'une equipe qui ne gagne ni ne marque.
  Quand la ligne existe, elle porte son effectif (`1.4 bpm/8j`) — la statistique vaut pour
  **cette competition**, pas pour toute la saison de l'equipe.
- **`Classement` et `Enjeu` non plus** (`_standing_played`), et ils etaient les deux
  seules lignes a passer au travers. A zero match, le fournisseur classe quand meme
  tout le monde : l'Eredivisie ouvrait sa saison avec « FC Zwolle 7e (0pts, 0j, +0) »
  et « Ajax 8e (0pts, 0j, +0) » — un rang herite de la saison passee, qui ne classe
  rien — et l'enjeu qui s'en deduit annoncait « Conference League - Play Offs » avant
  le premier coup d'envoi du championnat. Toutes les statistiques de saison se
  taisaient deja sur ces matchs-la.
  - Le seuil est **un** match et non `SEASON_MIN_MATCHES` : des la premiere journee
    le rang decrit un resultat reel, et la ligne porte deja son compte (`0pts, 1j`).
    Limite assumee que ce seuil ne couvre pas : sur une Premiership ecossaise, le
    fournisseur nomme des la 1re journee un « Relegation Group » qui ne se decide
    qu'apres la 33e. La fiche de la competition le dit dans le meme prompt, et un
    second seuil, invente pour un cas, se tromperait ailleurs.
- **`Forme 5` melange deux fenetres, et son compte le rend visible.** Les lettres
  viennent de `/teams/statistics`, donc de la **seule competition** ; les buts entre
  parentheses des `RECENT_LAST` (5) derniers matchs **toutes competitions**. Les deux
  coincident des qu'une equipe a joue cinq matchs dans la competition — soit partout,
  sauf en debut de saison, ou l'ecart devient absurde : « Celtic V (6-8) » se lisait
  « une victoire, six buts marques, huit encaisses », et « Slask Wroclaw DV (12-4) »
  douze buts en deux matchs. Le compte suit donc les buts (`V (6-8/5)`), meme idiome
  que `1.4 bpm/8j` — une lettre en face de `/5` se voit, et c'est tout ce qu'il faut
  pour ne pas lire les deux moities sur la meme periode.
- **Delocalisation** (`_relocated`) : un match hors du stade de l'equipe qui recoit change la
  lecture, et rien ne le laissait deviner. Le `venue` d'un match n'a pas d'identifiant
  exploitable : restent son nom et sa ville, et **il faut que les deux different**.
  `Veritas Stadion / Turku` contre `Veritas Stadion / Åbo` est le meme stade sous deux noms
  de ville ; `Teddy Stadium / Ploiesti` contre `Teddi Malcha Stadium / Jerusalem` est une
  vraie delocalisation sous un nom de stade proche. La ville seule inventait deux
  delocalisations sur dix, le nom seul en laissait passer. En cas de doute, aucune ligne.
- La **surface** vient du meme appel `/teams` : une pelouse naturelle ne produit rien, c'est
  le cas ordinaire. Un synthetique change le rythme et se disait jusqu'ici nulle part.
- **Compositions** (`/fixtures/lineups`, `KIND_LINEUPS`) : la seule donnee dont la
  disponibilite depend de **l'heure**, et la seule facon de savoir qui joue la ou
  `injuries` est faux — sur la Super League chinoise, `lineups: true` et
  `injuries: false`. Le drapeau vit dans le meme sous-objet que les statistiques de match
  (`coverage.fixtures.lineups`), meme piege.
  - **Fenetre `LINEUP_WINDOW_MINUTES` (90), bornee des deux cotes.** Mesure en reel : a
    2h30, 3h30 et 5h45 du coup d'envoi, l'endpoint rend **zero equipe** ; a 8 minutes, les
    deux compositions completes. Les clubs publient environ une heure avant. Appeler plus
    tot paierait un appel par match et par enrichissement pour du vide. Ouverte vers le
    passe, elle rendait « imminent » un match joue il y a quatre jours — la borne basse
    reprend la regle du projet : un match commence quitte le prompt.
  - **Hors fenetre, aucune ligne et aucune mention.** Contrairement aux absents, une compo
    qui manque cinq heures avant ne dit rien de l'equipe. « non disponible » ferait chercher
    un trou de collecte la ou il n'y a qu'une heure trop tot.
  - **Une reponse vide n'est pas persistee** : les compositions sortent au compte-gouttes, et
    figer « rien » empecherait un second essai dix minutes plus tard de rapporter quelque
    chose.
  - **Le banc est collecte et jamais rendu** : vingt-quatre noms de plus couteraient au
    prompt plus qu'ils n'apprennent. Il ne coute aucun appel de plus, et l'ecran, lui, n'a
    pas de budget de tokens — meme arbitrage que `recent_matches` au tennis.
  - La formation accompagne le onze : `Formations` donne deja l'habitude de la saison, et
    c'est l'ecart avec elle qui se lit ici.
- **`Enjeu`** (`standings.description`) : « Play-offs », « Relegation Playoffs »,
  « Promotion - Champions League ». Il arrivait dans l'appel de classement et partait a la
  poubelle, alors que la fiche de verification du prompt reclame l'enjeu reel a chaque
  match — la recherche web devait sinon le deviner du rang. Le libelle est **recopie tel
  quel**, jamais traduit : il vient de la competition, et le reecrire serait s'en porter
  garant. Verifie en reel : SonderjyskE « Relegation Playoffs » contre Viborg « Play-offs »,
  deux enjeux opposes que le classement seul ne disait pas. `goalsDiff` rejoint la ligne
  `Classement` au passage : il separe deux equipes a egalite de points.
- **`Fautes` et `Possession`** : un appel `/fixtures/statistics` rend **dix-huit**
  statistiques, `PROFILE_STATS` en gardait cinq. En garder deux de plus ne coute **aucun
  appel** — seulement de la place. `Fouls` accompagne `Cartons` (un arbitre ne sort un
  carton que sur une faute) et `Ball Possession` dit qui subit, ce qu'aucune autre ligne ne
  donne. Restent jetees : passes, hors-jeu, arrets, tirs par zone, et `expected_goals` —
  `goals_prevented`.
- **`xG`** (`expected_goals`, meme appel) : **la seule ligne du bloc qui ne soit pas un
  fait observe mais une sortie de modele**, et elle est rendue en le sachant. Elle separe
  ce qu'aucun compte de tirs ne separe — des buts nes d'occasions repetees, et des buts
  nes d'une frappe heureuse.
  - **Interdit, et c'est le coeur du sujet** : la convertir en probabilite puis la
    rapprocher d'une cote. Meme garde-fou que l'Elo tennis, meme raison — ce serait le
    calcul d'esperance de la section 9, et le fait que le chiffre vienne du fournisseur
    n'y change rien. Le template porte l'interdiction, un test verifie qu'elle y est.
  - **Couverture inegale, verifiee en reel** : la Super League chinoise rend
    `expected_goals: null`, la Superliga danoise sert 0.9 produit contre 1.5 concede.
    `_stat_value` ecarte le `null` comme toute valeur absente, donc aucune ligne — et
    surtout pas un zero, qui se lirait comme une equipe sans occasion.
  - **Le budget de tokens du prompt est un vrai garde-fou** : documenter ces lignes l'a
    fait passer a 8012 pour six matchs, contre 8000 permis alors. Toute ligne ajoutee se
    paie deux fois — la donnee dans chaque bloc, et son mode d'emploi en tete de prompt.
    Les plafonds actuels et leur mesure sont plus bas, « Deux plafonds de tokens ».
  - **`Possession` est le seul pourcentage du bloc, et il ne contredit pas la regle.**
    L'interdit vise les *frequences d'issues* : « BTTS 56 % » invite a diviser par une
    cote, ce qui est le calcul d'esperance de la section 9. Une part de ballon ne se
    rapporte a aucun marche, rien ne se divise par elle, et son unite naturelle est le
    pourcentage. Le template le dit, un test le verifie.
- **`/players/squads` a ete retire** (migration 022). Collecte des mois — un appel par
  equipe et par mois — sans **aucun** lecteur : son propre commentaire annoncait sa sortie,
  « si rien ne le lit a terme, il se retire en supprimant son type ». Le type, le resume, la
  methode du client, ses simulations et ses fixtures sont partis ensemble. Un test garde la
  porte fermee : la rouvrir demande d'abord un lecteur, sans quoi vingt-six noms restent du
  bruit dans un prompt.
- **`Effectif` reconstruit les absents la ou `/injuries` ne couvre pas**, et c'est le
  **seul ajout du projet qui coute des appels par equipe** — un par feuille de match.
  Mesure qui le justifie : `coverage.injuries` est faux sur **52 des 73** evenements
  rapproches, quand les compositions sont servies sur 44 d'entre eux. La ligne la plus
  decisive du bloc etait morte sur trois quarts du board avec, sous la main, de quoi la
  reconstruire.
  - **Substitut, jamais doublon** : le bloc ne part que si `injuries` est faux **et**
    `fixtures.lineups` vrai. La ou `/injuries` repond, il dit mieux et gratuitement ;
    la ou les compositions manquent aussi, rien n'est appele — 8 evenements sur 52.
  - Les identifiants des derniers matchs ne coutent rien : `recent:{team_id}` est deja
    memorise pour la forme. Seules les feuilles se paient, `SHEETS_LAST` (4) par
    equipe, memorisees par rencontre — soit 24 appels sur un lot de six matchs dont
    trois non couverts.
  - La regle est severe a dessein : vu sur au moins `SHEETS_MIN` (2) feuilles de la
    fenetre, absent des `SHEETS_MISSED` (2) plus recentes. Une seule absence est une
    rotation. Le banc compte comme le onze — etre sur la feuille, c'est etre
    disponible.
  - **Ce n'est pas une liste d'absents, et la nuance decide de l'usage** : une
    blessure, une suspension, un repos et une mise a l'ecart produisent le meme
    signal, et rien ici ne les distingue. La ligne est une **piste datee** — « plus vu
    depuis le 26/07 » se verifie en une recherche — meme idiome que `Buteur abs.`.
  - **Rien quand personne ne manque** : ecrire « aucun » affirmerait un effectif au
    complet, ce que des feuilles ne peuvent pas prouver — un joueur ecarte avant la
    fenetre lue n'y figure pas du tout. Le preambule le dit.
- **Les absents arrivent en double** : `/injuries` rend chaque joueur deux fois — constate
  en reel, 14 lignes pour 7 absents. Le dedoublonnage se fait a la collecte, sur
  (cote, nom, type, raison). Sans lui la ligne liste tout le monde deux fois, ce qui fait
  douter de la donnee entiere.
- Les competitions UEFA **couvrent leurs tours preliminaires** (`round =
  "3rd Qualifying Round"`) : il n'existe pas d'identifiant de qualification distinct, la ou
  The Odds API en a une cle separee. `soccer_uefa_champs_league_qualification` pointe donc
  sur la Ligue des champions elle-meme.
- `services/context.py` : `fetch_context()` appelle et **persiste les charges utiles brutes**
  dans la table `context` ; `context_lines()` relit la base. Regenerer un prompt ne declenche
  donc aucun appel reseau.
- Lettres de forme : `W -> V`, **`D -> N` (Draw = nul)**, **`L -> D` (Loss = defaite)**. C'est
  le piege classique, il a son test.
- H2H : toujours rendu du point de vue de l'equipe a domicile du match courant, avec un
  marqueur `V`/`D` quand ce n'est pas un nul.
- **`Aller`** : la fiche de verification appelle la double confrontation « le premier
  determinant du scenario » et rien ne la servait. La cause etait un champ jete a la
  collecte — le resume H2H gardait les scores et **pas la competition**, donc un aller
  de coupe d'Europe ne se distinguait pas d'un match de championnat d'il y a deux ans.
  Garder `league_id` ne coute **aucun appel**, c'est le meme `/fixtures/headtohead`.
  - Trois conditions, et il faut les trois : meme competition, **terrain inverse** et
    moins de `RETURN_LEG_DAYS` (21) jours. Le terrain inverse est le discriminant
    fort — sans lui, deux journees de championnat rapprochees passeraient pour une
    double confrontation.
  - La ligne **enonce un fait et s'arrete la** : ces deux equipes se sont rencontrees
    tel jour, chez l'autre, dans cette competition. Qu'il s'agisse d'une double
    confrontation est une deduction, tres sure sur un tour europeen et moins ailleurs ;
    c'est le preambule qui la fait faire, pas la ligne.
  - Le score se lit du point de vue de l'equipe qui **recoit aujourd'hui**, comme
    `H2H` : deux conventions dans le meme bloc se liraient a l'envers.
  - Un releve anterieur a ce champ n'a pas de `league_id` : aucune ligne jusqu'au
    prochain enrichissement, et surtout aucune erreur.
- `services/matching.py` : alias memorise, puis normalisation + Levenshtein. Seuils
  `MIN_SCORE` et `MIN_GAP`. **En cas de doute on ne devine pas** : `mapping_pending` et
  resolution manuelle. Un alias manuel prime pour toujours.

Un contexte manquant n'empeche jamais les cotes d'etre recuperees, et n'est jamais tu : il
devient une ligne explicite dans le bloc, ou une mention dans le rapport d'enrichissement.

## Le dossier d'equipe (`services/dossier.py`)

Ce qui vaut pour une **equipe** et non pour une rencontre. La table `context` est
indexee par evenement, ce qui convient aux absents d'un match ou a une confrontation
directe ; l'entraineur d'une equipe est le meme dans les deux affiches ou elle apparait
cette semaine, et le meme la semaine prochaine. Stocke par match, il se paierait autant
de fois qu'elle joue.

- `team_context(team_id, kind, scope, payload_json, fetched_at)`, cle naturelle **et**
  primaire. `scope` distingue les releves d'un meme type portant sur des perimetres
  differents — rien pour l'entraineur, une saison pour un historique. L'ajouter plus tard
  aurait demande de recreer la table.
- **Deux temps separes, comme `context.py`** : `refresh_event()` appelle et persiste,
  `dossier_lines()` relit. Regenerer un prompt ne declenche aucun appel, et le test le
  verifie **sans simuler la moindre route** — le moindre appel le ferait echouer.
- **Peremption par type** (`TTL_HOURS`), reglee sur la vitesse a laquelle la donnee change
  et bornee par ce qu'elle coute. Une date de releve illisible vaut perimee : mieux vaut un
  appel de trop qu'une donnee dont on ne sait plus quand elle a ete prise.
- `now` va **jusqu'a l'ecriture** (`store(..., now=)`), comme dans `elo.store`. La
  peremption compare une date de releve a une date de lecture : les prendre sur deux
  horloges differentes rend le calcul faux, donc intestable — constate en ecrivant le test.
- **Le plancher `APIFOOTBALL_CALL_FLOOR` ne bloque que le dossier.** Le contexte d'un match
  reste la fonction premiere de l'outil ; l'interrompre faute de credits pour un bonus
  serait le mauvais arbitrage. Un quota inconnu laisse partir — c'est l'etat d'une
  installation qui n'a jamais appele le fournisseur.
- **Deux causes distinctes, deux mentions distinctes** : `EnrichResult.dossier_note` est
  tenu a part de `context_note`. Un plancher franchi ne rend pas le contexte partiel — il
  est complet — et l'annoncer sous ce nom enverrait chercher un probleme de rapprochement
  la ou il n'y a qu'un compteur bas. L'UI les liste au meme endroit (`result.notes`) :
  pour l'oeil, ce qui compte est qu'il manque quelque chose sur ce match.
- Les identifiants d'equipe viennent de `context.KIND_TEAMS`, memorise au rapprochement.
  Sans lui, le dossier devrait refaire la resolution de noms, donc repayer `/fixtures` a
  chaque lecture. Volontairement **absent de `report.kinds`** : ce n'est pas un contexte
  recupere, c'est le moyen d'en chercher d'autres. Un evenement dont le rapprochement est
  reste incertain n'a rien ici, et le dossier **ne devine pas**.
- **Buteurs** (`/players/topscorers`, `KIND_SCORERS`, table `league_context`) : ranges par
  **competition** et non par equipe. Un appel rend les vingt meilleurs de toute la ligue,
  donc le cout ne croit pas avec la taille du lot ; les ranger par equipe stockerait la
  meme liste vingt fois et la paierait vingt fois. `LEAGUE_KINDS` porte la distinction, et
  c'est **le type qui dit ou le releve se range** — la mettre dans la liste des taches
  l'aurait fait oublier au premier appelant suivant.
  - **Ni paye ni rendu hors de `PLAYER_PROPS_LEAGUES`** (`_props_league()`) : ailleurs
    aucun book ne sert de props, et la ligne couterait des tokens sans marche en face.
    Second garde-fou gratuit : `coverage.top_scorers`, memorise au rapprochement.
  - **La part de penaltys est dite** : 23 buts dont 10 sur penalty ne se parient pas comme
    23 buts dans le jeu.
  - **Sous `SCORERS_MIN_GOALS` (3) buts, aucun joueur.** Verifie en reel : en aout
    l'endpoint rend une liste **vide**, puis des septembre vingt joueurs a un ou deux buts.
    Les lister ferait passer un classement de coincidences pour une hierarchie. La reponse
    vide est memorisee — c'est une reponse, et la repayer chaque jour n'apprend rien.
  - Le drapeau `injured` du fournisseur est **ignore** : sa fraicheur est inconnue, alors
    que `/injuries` fait autorite sur les absents. Deux sources qui se contredisent dans le
    meme bloc valent moins qu'une seule.
  - Limite assumee : une equipe dont aucun joueur n'est dans les vingt premiers n'a pas de
    ligne. Le template le dit — une equipe absente n'est pas une equipe sans buteur.
- **Effectif** (`/players/squads`, `KIND_SQUAD`) : **collecte, jamais rendu**. Sans une
  statistique, vingt-six noms sont du bruit dans un prompt. Il sert a rattacher un nom a un
  identifiant de joueur. Le garder sans lecteur est assume et delimite : si rien ne le lit
  a terme, il se retire en supprimant son type. La phase 15 **ne s'en sert pas** — les
  identifiants des buteurs viennent de `KIND_SCORERS`.
- **Absences longue duree** (`/sidelined`, `KIND_SIDELINED`, table `player_context`) : seule
  echelle **par joueur**, et un appel par joueur.
  - **Demande pour les seuls buteurs rendus**, au plus trois par equipe. Payer l'absence
    d'un joueur que le bloc ne nomme pas serait acheter une donnee que rien ne lira, et un
    effectif entier ferait soixante-douze appels par affiche. `_ranked_scorers()` est ecrit
    **une seule fois** et sert au rendu comme a la recherche : deux classements paralleles
    auraient fini par diverger.
  - **La date accompagne toujours l'absence, et jamais l'inverse.** Le fournisseur publie un
    historique de **carriere** : une periode sans date de fin dit qu'il ne l'a pas refermee,
    ce qui n'est pas tout a fait une absence en cours. Datee, la ligne se verifie en une
    recherche ; seche, « absent » serait une affirmation qu'on ne peut pas gager. Le template
    la presente comme une piste a confirmer et dit que la recherche gagne.
  - Refermee avant le match : rien, le joueur est revenu. Commencant apres : rien. La plus
    recente prime — une vieille periode jamais refermee masquerait la blessure du mois.
  - **Portee reelle, verifiee avant d'ecrire la table** : l'endpoint repond pour n'importe
    quel joueur mais ne rend **aucune entree** hors des competitions dont les blessures sont
    couvertes. Sur un board de 41 matchs, 38 n'avaient aucune couverture d'absents. Le
    template dit donc que l'absence de la ligne ne prouve rien.
- **Historique de saison** (`/fixtures?team=&season=`, `KIND_SEASON`, scope = l'annee) : un
  seul appel rend toute la saison d'une equipe, **toutes competitions**, avec les scores a
  la pause et les matchs a venir. C'est ce qui repare l'angle mort de `/teams/statistics`,
  scope a une seule competition : Motherwell y compte 2 matchs de Conference League quand
  sa saison domestique en porte 47. Trois lignes en sortent, calculees localement :
  `Total buts`, `Serie`, `Calendrier`.
  - **Score a 90 minutes** (`score.fulltime`), jamais `goals` : sur un match decide en
    prolongation, `goals` porte le total prolongation comprise, alors qu'un marche O/U se
    regle sur le temps reglementaire. Les deux champs sont identiques ailleurs, donc le
    choix ne coute rien.
  - **Amicaux exclus** (`FRIENDLY_LEAGUES`, 667 releve en reel), et reports, annulations et
    forfaits avec eux (`PLAYED_STATUSES`). En juillet les amicaux sont les seuls matchs
    joues : les compter donnerait « >2.5 dans 4/4 » a une equipe qui n'a rien joue.
    L'identifiant est la regle, le libelle un filet — il ne classe rien, il rattrape une
    ligue amicale non listee.
  - **La saison N-1 ne se demande que si N ne dit rien encore**, et l'annee est alors
    **ecrite** (`23/36 (2025)`). En debut de saison c'est la regle. La taire laisserait lire
    la saison passee comme la forme du moment. Sa peremption est longue : elle ne changera
    plus, et `ttl_for()` la lit sur le **perimetre** du releve, pas sur son type.
  - **Le match analyse n'est pas son propre prochain match** : il figure dans l'historique
    de sa propre equipe, et l'heure du fournisseur peut etre posterieure de peu a celle de
    l'evenement. Sans le garde `days >= 1`, la ligne annoncait « dans 0j » — constate en
    reel. Seul un match `NS` est une echeance : un report n'en est pas une.
  - `Total buts` compte les buts **du match**, `Buts marq.` (phase 11) ceux de **l'equipe**.
    Deux lignes voisines et deux grandeurs differentes : le template les separe
    explicitement, et un test le verifie.
  - `Serie` est la serie **en cours**. `biggest.streak` de `/teams/statistics` donne le
    record de la saison, ce qui se lit comme la serie en cours et dit l'inverse.
  - **Et elle ne se rend pas du tout sur un repli de saison**, contrairement a
    `Total buts` qui se contente d'ecrire son annee. Les deux traitements sont
    justes parce que les deux grandeurs n'ont pas la meme nature : une frequence
    sur trente-six matchs decrit encore un profil d'equipe, une serie « en cours »
    est une affirmation sur **maintenant**. Datee de la saison passee, elle n'est
    pas seulement perimee — elle est **fausse**, parce que le repli se declenche
    justement quand la nouvelle saison compte moins de `SEASON_MIN_MATCHES`
    matchs, donc en ignorant ceux qui l'ont deja rompue. Constate en reel sur un
    prompt de six matchs : le bloc donnait « Cracovia Krakow 5N » quand la ligne
    `Forme 5` juste au-dessus montrait un nul puis une **defaite** dans la
    nouvelle saison, et quatre blocs sur six portaient la meme contradiction.
    `_streak_fragment` recoit donc la saison comme `_goals_fragment` — l'oubli
    venait de la : `matches, _ = history` jetait l'information que
    `_history` rendait exprès.
  - **Corollaire immediat, et il a failli passer inapercu : l'absence de la ligne
    a desormais deux causes, donc le preambule doit les nommer toutes les deux.**
    Il disait « une equipe absente de la ligne sort **donc** d'un resultat
    isole » — vrai tant que c'etait la seule cause, faux des le correctif
    ci-dessus. Sur le prompt reel qui l'a revele, quatre blocs sur six perdaient
    la ligne par repli de saison, dont un Celtic sur **six victoires de rang** :
    le preambule en faisait une equipe sans serie, l'inverse exact. Meme regle
    que `Non servis` et ses trois causes. Le lecteur les distingue sans effort,
    le `(2025)` de `Total buts` etant visible dans le meme bloc.
    - Corriger un rendu sans relire son mode d'emploi laisse une **affirmation
      fausse** a l'endroit precis ou l'on vient de gagner en justesse. La regle
      generale : toute condition ajoutee a une ligne se verifie contre la phrase
      du preambule qui explique son absence.
  - **La charge utile n'est pas stockee brute** — seule exception du module : 43 ko pour 41
    matchs, soit une base dix fois plus grosse pour des logos. `_summarize()` garde de quoi
    tout recalculer.
- **Entraineur** (`/coachs`, orthographe du fournisseur, la corriger donne un 404) : le
  fournisseur peut rendre **plusieurs** entraineurs pour une equipe, le predecesseur y
  figurant avec sa date de fin. Le poste en cours est celui dont l'etape de carriere
  **dans cette equipe** n'est pas refermee ; prendre le premier de la liste nommerait un
  entraineur parti, affirme comme un fait. L'anciennete se compte dans l'equipe du match
  et jamais depuis le premier poste de la carriere, et une prise de fonction posterieure
  au match ne rend aucune duree — un nombre negatif presente comme une anciennete.
  - **Cette regle ne tranche que dans 15 % des cas, et c'est mesure** : sur les 110
    clubs en base, **92 ont plusieurs etapes ouvertes** chez eux — le fournisseur ne
    referme pas ses fiches. Le reste du temps c'est le **depart le plus recent** qui
    decide : heuristique juste plus souvent qu'aucune autre, mais heuristique.
  - **Aucune regle ne rattrape une nomination absente.** Le bloc nommait R. Jans a
    Utrecht, parti depuis, alors que son successeur ne figurait **nulle part** dans la
    reponse. Il n'existe aucun signal decisif dans la charge utile : le champ `team`
    de tete est un simple echo de l'equipe interrogee, pas le club courant de
    l'entraineur — verifie.
  - **La fausse piste evidente est la peremption, et elle est fausse.** Le releve
    d'Utrecht datait du matin meme : raccourcir `TTL_HOURS[KIND_COACH]` paierait des
    appels sans rien corriger.
  - Ce qui reste, c'est de ne plus presenter la ligne comme un fait. Le preambule dit
    qu'une anciennete longue **ne prouve pas la continuite** — elle peut n'etre que la
    derniere fiche restee ouverte — et que la conference de presse tranche. Il est
    garde par `{% if 'Entraineur' in context_labels %}`, ce qui le rend **moins cher
    qu'avant** sur un lot qui n'a pas de dossier d'equipe.

## Elo tennis (`providers/tennisabstract.py`, `services/elo.py`)

Le football recoit son contexte d'API-Football ; le tennis n'avait rien. Les classements
Elo de Tennis Abstract comblent ce trou, **gratuitement et sans cle**.

- Ce ne sont pas des APIs mais deux pages HTML statiques, une par circuit. Trois pieges,
  tous verifies : le domaine apex ne repond pas (**`www.` obligatoire**), l'absence de
  `User-Agent` de navigateur donne un **403**, et le `robots.txt` interdit `/jsfrags/`,
  `/jsmatches/` et `/jsplayers/`. Seul `/reports/` est autorise : ne rien ajouter d'autre.
- Aucun quota, donc **rien n'est ecrit dans `api_usage`**, qui ne compte que des credits.
  Le rafraichissement passe avant le garde-fou de credit dans `run_enrich` : c'est
  justement quand il n'y a plus un marche a acheter que l'Elo compte.
- Les colonnes sont rapprochees **par libelle**, jamais par position — la colonne du
  classement officiel s'appelle `ATP Rank` ou `WTA Rank` selon la page.
- Rapprochement des noms : seuils plus severes que pour les clubs (`MIN_SCORE = 0.88`).
  **En cas de doute on ne devine pas**, et ici il n'existe aucune resolution manuelle pour
  rattraper : attribuer a un joueur le rating d'un autre serait pire qu'une ligne absente.
- La surface vient de `competitions.surface`, saisie a la main. La deduire d'un libelle de
  tournoi serait une invention ; non renseignee, seul l'Elo general est rendu.
- Base vierge = aucune ligne. Ecrire deux fois « non trouve » ferait chercher un probleme
  de rapprochement la ou il n'y a qu'un rafraichissement jamais lance. En revanche, un
  joueur absent d'un classement existant est dit : c'est une information sur lui.
- **Interdit, et c'est le coeur du sujet** : convertir un ecart d'Elo en probabilite. La
  page source publie la table de correspondance ; s'en servir, puis rapprocher le resultat
  d'une cote, est exactement le calcul d'esperance qu'interdit la section 9. Le template
  de prompt porte cette interdiction, et un test verifie qu'elle y est.

## Historique des matchs de tennis (`providers/tennisdata.py`, `services/tennis_history.py`)

Le tennis n'avait aucune source de resultats : `tennis_load.py` date les apparitions,
mais ni vainqueur, ni score, ni surface n'etaient stockes. D'ou l'impossibilite de dire
si deux joueurs se sont deja affrontes, ou ce qu'un joueur vaut sur terre.

- Source : **tennis-data.co.uk**, un classeur `.xlsx` par saison et par circuit,
  `SEASONS_KEPT` (3) saisons, ATP et WTA. Gratuit, sans cle, **sans quota** : rien dans
  `api_usage`, meme regle que l'Elo. Trois faits verifies : `robots.txt` n'interdit que
  `/stuff/` et 2000-2005 ; **HTTPS ne repond pas**, seul `http://` sert le fichier ; le
  circuit feminin ajoute un `w` a l'annee (`/2026w/2026.xlsx`).
- **Les huit colonnes de cotes de cloture sont ecartees a la lecture** (`COLUMNS` ne les
  contient pas), jamais au rendu : ce sont les prix de fermeture du marche, donc la matiere
  premiere d'un calcul de CLV et de value. `ODDS_COLUMNS` existe pour etre citee par le
  test qui verifie qu'aucune n'atteint la base.
- Les colonnes sont rapprochees **par libelle d'en-tete**, jamais par position : la source a
  deja ajoute des colonnes de books au fil des ans.
- **Le rapprochement des noms est le coeur du risque.** Le fichier publie
  « Etcheverry T. M. », The Odds API dit « Tomas Martin Etcheverry » : ni le prenom entier
  ni le decoupage prenom/nom ne sont donnes. `resolve()` essaie **tous** les decoupages et
  n'accepte qu'une identite unique.
  - Deux orthographes du meme joueur sont **reunies** (`Etcheverry T.` et
    `Etcheverry T. M.`, neuf paires de ce genre en reel) : nom identique **et** initiales en
    chaine de prefixes. Les separer couperait son historique en deux.
  - Des initiales qui **divergent** font refuser — les freres Zverev. Aucune resolution
    manuelle n'existe ici : attribuer a un joueur l'historique d'un autre serait pire
    qu'une ligne absente, meme regle que l'Elo.
  - Mesure sur 31 290 apparitions reelles : 879 cles, **aucune collision**, 141 des 143
    joueurs de la base rapproches. Les 2 refus sont de vraies absences du fichier.
- **Un tapis vert n'est pas un match joue** : ni forme, ni bilan de surface, ni H2H. Il
  reste une information sur la disponibilite, portee par la ligne `Abandons`.
- **La collecte est datee meme quand elle ne ramene rien** (`tennis_history_state`).
  Deduire la peremption du `MAX(fetched_at)` des matchs — comme le fait `tennis_elo` —
  tombe des qu'une saison est vide : sans ligne, pas de date, donc « jamais telecharge »,
  donc redemandee a chaque enrichissement, sans fin. En janvier, le fichier de la saison
  qui commence est justement vide. Trouve en ecrivant le test.
- Une saison **terminee** n'est jamais retelechargee ; la saison en cours l'est **une fois
  par jour**. Une semaine etait la mauvaise cadence, et pas d'un peu : le fichier parait
  chaque semaine mais **aucun jour connu** — il se remplit a mesure que les tournois se
  terminent — si bien que caler la relance sur notre propre derniere collecte manquait une
  publication entiere. Releve en reel le 8 aout : l'historique s'arretait au 3 et n'aurait
  ete redemande que le 13. Une tentative par jour colle a `FREE_JOB_ID` et ne coute rien —
  400 Ko par circuit, sans cle et sans quota.
- **Une date qui ne peut pas etre celle de sa saison n'entre pas en base** (`in_season()`).
  La source se trompe parfois : le fichier 2026 datait une finale de l'Iasi Open du
  20 juillet **2029**. Le degat est invisible, et c'est ce qui le rend genant — posterieure
  a tout match analyse, la ligne sort de **chaque** fenetre de lecture (forme, surface, H2H
  filtrent toutes sur `played_on < debut du match`), si bien que le match disparait de
  l'historique des deux joueuses sans qu'aucune ligne ne signale le trou.
  - **Le garde-fou evident est le mauvais.** Exiger que l'annee de la date egale la saison
    jetterait des matchs bien reels : la saison ouvre dans les tout derniers jours de
    decembre, et le fichier 2025 porte 69 matchs joues du 29 au 31 decembre 2024, celui de
    2024 onze matchs du 31 decembre 2023. Une date vaut donc pour sa saison si elle tombe
    dans son annee, **ou en decembre de l'annee precedente**.
  - Les lignes ecartees sont **dites** (`HistoryReport.rejected`, un log a part des
    erreurs) : le telechargement a reussi, c'est la source qui s'est trompee, et confondre
    les deux ferait chercher une panne de reseau.
  - La migration 021 nettoie ce qui avait ete ecrit avant — une saison terminee n'etant
    jamais retelechargee, rien d'autre ne repasserait dessus. Elle **rejoue le meme
    critere en SQL** : un test relit le fichier de migration plutot que d'en recopier la
    regle, les deux ecritures n'ayant sinon rien qui les empeche de diverger.
- Les dates n'ont pas le meme format selon la ligne, et c'est voulu : `%m/%y` pour une
  confrontation directe — sur trois saisons, « 12/04 » ne situe rien — et `%d/%m` pour un
  abandon recent, ou le jour compte.
- **Les deux variantes « alternate » sont demandees** (`markets.TENNIS_MARKETS`) : `spreads`
  et `totals` ne servent que la ligne principale, leurs variantes toute l'echelle. Verifie
  en reel — Pinnacle rend dix cotes de chaque, la ou le bloc n'en affichait que deux et
  deux. Les marches par **set** ne sont servis par aucun book europeen (verifie avec
  `regions=eu`) mais restent demandes : la ligne « Non servis » tire son sens de la
  difference entre demande et recu.
- Le **handicap jeux se rend en echelle** (`render._render_spread_ladder`), pas en une ligne
  par joueur comme au football : au tennis c'est un continuum, comme un total. Et son signe
  est celui du **premier joueur nomme**, jamais celui du favori — groupe sur la valeur
  absolue, « -2.5 » designait le second joueur quand il etait favori et le premier sinon,
  d'un bloc a l'autre. Les prix restaient justes, mais une selection lue a l'envers est
  l'erreur la plus couteuse que ce bloc puisse produire. Le template porte la convention.
- Tout marche demande doit avoir une entree dans `MARKET_ORDER_BY_SPORT`, **meme fusionne** :
  sans elle `ordered_labels` rend la **cle brute**, et `alternate_totals` s'est affiche tel
  quel dans la liste des marches en tete de prompt.
- `H2H` dit « aucun match joue depuis <annee> » quand les deux joueurs sont rapproches sans
  passe commun. Omettre la ligne rendait l'absence indiscernable d'un rapprochement rate.
  « aucun match joue » et non « jamais rencontres » : le second serait faux quand leur seule
  rencontre a ete un forfait.
- `Precedent` donne le tournoi joue **avant celui-ci** et le resultat atteint. C'est ce que
  la ligne `Forme` detruit : Blockx affiche « dur 2V-3D/12m », ce qui se lit comme un joueur
  faible, alors qu'il sort d'une finale sur terre. Le tournoi en cours est exclu — sinon la
  ligne repeterait « 1er tour » — ce qui la rend dependante du rattachement, comme « ici ».
- Le **detail des derniers matchs** (`recent_matches()`, `_event_matches.html`) vit sur la
  fiche et **pas dans le prompt** : dix rencontres par joueur avec adversaire, score, tournoi
  et tour couteraient cinq cents caracteres par bloc. L'ecran n'a pas de budget de tokens,
  et c'est la que dix lettres V/D montrent leur limite.
- **La forme d'un match** — `Profil`, `Marge`, `Niveau adv.` — repond au fait mesure sur les
  soixante-trois premieres selections : **25 des 30 selections tennis portaient sur un
  Vainqueur**, pour 11 gagnees. Les onze lignes du bloc repondaient toutes a la meme question,
  « qui est le meilleur des deux », c'est a dire ce que la cote 1N2 resume deja et ce qu'un
  book fait le mieux. Rien n'eclairait les marches de jeux, ou le prix est plus grossier — et
  l'analyse ne pouvait donc pas les jouer. Ces trois lignes sortent des **scores deja en
  base** : aucun appel, aucune cle, aucun quota.
  - `Profil` — mediane des jeux, etendue, part de matchs avec tie-break, part de matchs en
    deux sets. Le taux de tie-breaks est **le plus proche du style** que les donnees
    permettent : un joueur qui tient son engagement produit des 7-6.
  - `Marge` — l'ecart de jeux **en victoire et en defaite separement**, la grandeur du
    handicap jeux. Une moyenne unique les annule : un joueur qui gagne de huit et perd de
    huit y ressemblerait a un joueur qui joue serre.
  - `Niveau adv.` — Elo moyen des adversaires des dix derniers matchs, et le meilleur
    battu. C'est lui qui rend `Forme` lisible : la suite de lettres traite une victoire sur
    le 150e comme une victoire sur le 5e. `ratings_by_key()` fait l'inverse d'`elo.lookup()`
    — le fichier de resultats ne nomme qu'« Fritz T. » — et **retire toute cle que deux
    joueurs du classement se disputent**, meme regle qu'ailleurs : en cas de doute, rien.
  - **Les Grands Chelems masculins sortent de `Profil` et de `Marge`, et restent dans
    `Usure`.** Quarante jeux sont ordinaires au meilleur des cinq sets ; les melanger fait
    lire un joueur de trois sets comme un marathonien. `Usure`, elle, mesure le temps passe
    sur le court, et cinq sets fatiguent vraiment. Cote WTA la colonne `series` est vide et
    tout se joue en trois sets : le filtre ne vaut donc que pour l'ATP.
  - La fenetre est **celle de `Forme` et d'`Usure`** — les dix derniers matchs joues — puis
    les formats longs en sont retires. Filtrer avant de couper irait chercher plus loin dans
    le passe et donnerait trois lignes portant sur trois periodes differentes ; le compte
    ecrit a cote dit combien ont ete gardes.
  - Sous `SHAPE_MIN_MATCHES` (5), **aucune des trois lignes**. Cout mesure : +1 279 tokens
    sur un prompt de huit matchs de tennis, dont environ 510 pour le preambule.
- **La recherche des matchs deja joues dans ce tournoi est la premiere tache de la liste
  de verification tennis.** Elle est la contrepartie directe de `Historique` : nos sources
  ne portent ni le score, ni la duree, ni les statistiques de service des tours precedents,
  et `Parcours` ne nomme que les adversaires. Le prompt demande donc explicitement le score
  set par set, la duree et — quand le site du tournoi ou de l'ATP/WTA les publie — aces,
  doubles fautes, premiere balle et balles de break. C'est la seule facon d'obtenir ce que
  la section « ce qu'aucune source ne donne » declare introuvable en automatique.
- **`Historique` dit jusqu'ou va le jeu de donnees**, et donc jusqu'ou vont toutes les
  lignes qui en sortent. Le fichier source est hebdomadaire et publie **apres coup** : le
  8 aout il s'arretait au 3, si bien qu'aucun match du Canadian Open — commence le 4 —
  n'existait en base. `Forme` ignorait deux victoires de Lehecka acquises sur place et
  `Precedent` nommait Los Cabos comme son dernier tournoi alors qu'il jouait un huitieme
  ici. **Rien ne le disait**, et le trou se lisait comme un rapprochement rate.
  - La ligne **enonce un fait et s'arrete la** : « dernier match connu le 03/08, soit 5j
    avant celui-ci ». Elle n'ecrit pas « ce tournoi n'y figure pas » — ce serait faux d'un
    tournoi commence avant la date de collecte, et une affirmation fausse dans la ligne qui
    sert justement a douter est le pire endroit ou en mettre une. C'est le preambule qui en
    tire la consequence, et qui renvoie a `Tour`, `Repos` et `Parcours` — les seules lignes
    qui viennent de nos propres releves et portent donc le tournoi en cours.
  - `HISTORY_LATE_DAYS` vaut 2 : un fichier frais accuse deja trois a quatre jours de
    retard, et rendre la ligne en dessous ferait douter de donnees completes.
  - **Le retard se compte par circuit** (`horizon()`) : l'ATP et la WTA sont deux fichiers,
    et lire le plus recent des deux tairait le retard du bon.
  - **Ni apostrophe ni accent dans une valeur rendue**, comme partout dans ce module. Elles
    traversent Jinja pour la fiche d'un match, qui les echappe, et le test de parite
    fiche/prompt comparait deux textes bruts — `jusqu'au` echouait sur `&#39;`. Le test
    passe desormais par `markupsafe.escape`, pour qu'une future valeur echoue pour la bonne
    raison au lieu de se faire « corriger » en affaiblissant l'assertion.
- `as_bytes` a ete ajoute au client de base pour ce fichier. Il n'est **jamais** mis en
  cache disque : le cache de developpement est un cache JSON, y ecrire des octets bruts le
  corromprait.
- **Le rapprochement des tournois est une table verifiee a la main**
  (`competitions.TENNISDATA_TOURNAMENTS`, seed migration 020), exactement comme
  `APIFOOTBALL_LEAGUES`, et pour une raison mesuree : ni la **ville** ni le **nom** ne
  suffisent. Paris heberge le BNP Paribas Masters *et* Roland-Garros ; le Canadian Open
  change de ville chaque annee ; onze villes portent plusieurs noms de tournoi. Le circuit
  se lit dans la cle (`tennis_atp_…`) et departage Cincinnati et Stuttgart, ou les epreuves
  masculine et feminine ont des noms differents dans la meme ville.
  - Le champ accepte **plusieurs noms** separes par `|` : un sponsor qui change renomme le
    tournoi sans que ce soit un autre tournoi. La source porte deja
    « U.S. Men's Clay Court Championships » et « U.S.Men's Clay Court Championships ».
  - Non renseigne, `H2H ici` et `Palmares` **n'existent pas**, et le template dit que leur
    absence ne parle pas du passe des joueurs mais du rattachement manquant.
  - Une finale **gagnee** vaut « vainqueur », **perdue** « finaliste ». Le rang du tour ne
    le dit pas, et les confondre serait l'erreur la plus visible de la ligne.

## Un seul assembleur de bloc CONTEXTE (`session.context_block`)

Le prompt et la fiche d'un match portent le **meme** bloc, par le meme appel. Deux
assemblages paralleles ont diverge **deux fois** : la fiche d'un match de football est
restee sans dossier d'equipe apres la phase 12, et celle d'un match de tennis affichait un
bloc entierement **vide** — ni Elo, ni repos, ni historique — alors que le prompt les
portait depuis des mois. Ajouter une source a un seul des deux endroits est une erreur
invisible : elle ne casse rien, elle fait disparaitre.

`main._event_context` et `session._context_for` sont deux adaptateurs de la meme fonction.
Un test compare les deux rendus ligne par ligne. Toute nouvelle source de contexte se
branche **la**, et nulle part ailleurs.

## Repos et charge au tennis (`services/tennis_load.py`)

Le football recoit onze types de lignes d'API-Football ; le tennis n'avait que l'Elo, et
l'analyse allait chercher a la main, match par match, qui avait joue la veille. Or
l'information dort **deja dans la base** : les tours precedents du meme tournoi ont ete
scannes les jours d'avant. Aucun appel, aucune cle, aucun quota.

- Une seule grandeur : les **jours de repos**, comptes en **journees de tournoi** et jamais
  en dates civiles. A Montreal, un match de la session du soir part a 01h du matin a Paris :
  sa date civile est celle du lendemain, et le repos calcule dessus perdait un jour d'un
  cote et en gagnait un de l'autre. Constate en reel — le bloc donnait van de Zandschulp a
  1j et Paul a 3j la ou l'ATP date leurs deux matchs precedents du **meme mercredi**.
  Regroupes par `tournament_day`, les deux tombent sur 2j.
  - Le match analyse entre dans le regroupement sous un **identifiant sentinelle** plutot
    que d'y etre cherche : rien ne garantit qu'il figure en base — une rencontre saisie a la
    main, ou pas encore scannee — et l'y supposer faisait disparaitre la ligne entiere.
- **Le nombre de tours a ete retire.** Il comptait les apparitions *scannees*, pas les
  matchs joues : sur un tournoi dont les premiers jours precedent notre fenetre, il en
  manque. Le bloc creditait Michelsen d'un tour la ou l'ATP lui en donne deux. La ligne
  `Tour` dit desormais ou en est le tournoi, et elle le dit juste.
- **Le double n'est pas vu.** Sur un tournoi ou le tableau de double a commence, un joueur
  peut avoir la meme ligne `Repos` et une charge tout autre — releve en reel, 10 des 16
  joueuses d'une journee WTA avaient joue le double la veille. Le fournisseur de cotes ne
  sert pas les doubles : le template dit que c'est a la recherche.
- Ce qu'on ne tire **pas** et qu'il ne faut pas inventer : duree des matchs, score, maniere.
  La base ne stocke aucun resultat. Un joueur present au tour suivant a forcement passe le
  precedent, mais l'ecrire supposerait qu'aucun forfait n'existe.
- Rapprochement des noms par `labels.sort_key` — casse et accents ignores, **rien de flou** :
  deux joueurs differents ne doivent jamais partager un parcours.
- Aucun tour connu ne produit **aucune ligne**. Ecrire « 0 tour » laisserait croire a une
  entree en lice alors que le tournoi n'a peut-etre ete scanne que ce jour-la.
- Au-dela de `MAX_DAYS`, c'est une autre semaine : le repos ne dit plus rien de la fraicheur.
- **`Parcours`** (`path_lines`) : les adversaires deja rencontres **dans ce tournoi-ci**, du
  premier tour au dernier, avec leur Elo quand il est connu — c'est lui qui distingue un
  parcours facile d'un parcours d'usure, et il ne coute rien, le classement etant deja en
  base. Les adversaires et **jamais les resultats** : un joueur present au tour suivant a
  forcement passe le precedent, mais l'ecrire « il a battu X » supposerait qu'aucun forfait
  n'existe.
- **`Parcours` porte la fenetre de nos scans** (`[vu depuis le 04/08]`), et ce n'est pas
  celle du tournoi. La liste se lisait comme un parcours complet : constate en reel, celui
  de Norrie omettait son premier tour contre Ugo Carabelli, joue la veille du premier jour
  scanne, et seule une recherche exterieure l'a rattrape. Comparee a `Tour`, la date dit
  tout de suite si le debut du tableau manque — compter les tours absents demanderait la
  taille du tableau, que rien ne donne.
  - **Un forfait s'y lit comme un match joue.** L'adversaire y figure parce que la
    rencontre etait *programmee* : Anisimova avait deux noms au `Parcours` pour un seul
    match dispute, et `Repos` comptait ce jour-la de la meme facon. Nos donnees ne peuvent
    pas le savoir — le fournisseur de cotes programme, le fichier de resultats retarde — et
    c'est donc le template qui le dit.
- **`Usure`** (`tennis_history._games_fragment`) : jeux par match sur les dix derniers, le
  **temps passe sur le court par procuration**. Abandons et tapis verts exclus — leur score
  est tronque a l'instant ou le match s'arrete, et les compter ferait passer un joueur qui a
  abandonne pour un joueur aux matchs courts.
  - **Les matchs au meilleur des cinq sets restent comptes, et leur nombre est dit**
    (`32.3 jeux/match sur 10 (4 en 5 sets)`). C'est l'arbitrage **inverse** de `Profil` et
    `Marge`, qui les ecartent, et les deux sont justes : trente-neuf jeux fatiguent autant
    quel que soit le format, donc la moyenne decrit bien une charge ; mais elle ne decrit
    pas la forme d'un match en trois sets. Ce que le silence rendait faux, c'est la
    **comparaison** — Lehecka affichait 32.3 contre 30.5 a Jodar sans que rien ne dise que
    quatre de ses dix matchs etaient un Grand Chelem. Les retirer aurait efface une vraie
    fatigue, les taire faisait passer un joueur ordinaire pour un marathonien.

**Ce qu'aucune source ne donne, verifie le 7 aout 2026** — a ne pas rechercher a nouveau
sans raison nouvelle :

- **la duree d'un match** et **les statistiques de service** (aces, doubles fautes, premiere
  balle, balles de break). `tennis-data.co.uk` ne sert que les scores ; les pages match de
  Tennis Abstract sont interdites par son `robots.txt` ; et les CSV de Jeff Sackmann, qui
  les portaient, **ont disparu de GitHub** — 404 jusque sur l'API du depot. `Usure` est le
  meilleur substitut disponible ;
- **les resultats du tournoi en cours.** Le fichier tennis-data est hebdomadaire : le
  7 aout, quatre jours apres le debut du Canadian Open, il s'arretait toujours au 3 aout.
  Les resultats arrivent apres la fin du tournoi. C'est pourquoi `Parcours` sort de nos
  propres scans et non de l'historique.

## Journees de tournoi (`services/tournament_day.py`)

Une journee civile ne decrit pas une journee de tournoi. A Montreal la session du soir
commence vers 19h locales : le dernier match part a 23h a Paris et le suivant a 01h le
lendemain, si bien que « les matchs d'aujourd'hui » en perdait la moitie.

- **Une heure de bascule fixe reglerait le Canada en cassant l'Australie.** A Melbourne,
  un match a 01h a Paris *ouvre* la journee du jour meme : le ranger la veille serait
  l'erreur exactement inverse. Les matchs se regroupent donc par **trou horaire**
  (`GAP_HOURS`, 6), et la journee prend la date locale de son **premier** match. Aucun
  fuseau a stocker, aucune table a tenir a jour, et la regle vaut pour les deux hemispheres.
- Mesure qui fixe le seuil : sur le Canadian Open, le dernier match d'une journee part a
  23h10 UTC et le premier de la session de nuit a 00h10 — une heure. Le trou suivant, entre
  la fin de nuit et la reprise de l'apres-midi, depasse dix heures. Le plus grand ecart
  *a l'interieur* d'une journee tient en trois heures.
- Le regroupement est **par competition** : deux tournois joues sur deux continents n'ont
  aucune raison de decouper leurs journees ensemble.
- Le filtre du board (`Filters.date`) porte cette journee, **pas la date du coup d'envoi**.
  Les journees proposees se comptent **avant** que le filtre ne s'applique, sans quoi
  choisir une date effacerait les autres du menu et on ne pourrait plus en changer. Une
  journee sortie de la fenetre est oubliee, comme une competition qui n'appartient pas au
  sport choisi.
- Le regroupement se fait sur **toute** la competition et non sur la fenetre : une soiree
  coupee par le bord de la fenetre serait datee de son second match.

## Le tour d'un match de tennis (`services/tennis_round.py`)

Aucune source ne le donne. The Odds API ne transmet pas le tour, et `tennisdata.co.uk`
publie son fichier une fois par semaine : verifie en reel, un fichier rafraichi le 6 aout
ne portait aucun match posterieur au 3 alors que le tournoi avait commence le 4. Un tour
se deduit donc, ou ne se dit pas.

- **L'invariant** : dans un tableau a elimination directe, chaque match elimine exactement
  un joueur. Donc `joueurs en lice = joueurs vus dans le tournoi - matchs deja joues`, et
  le tour se lit dans ce seul nombre — 2 joueurs restants sont une finale, 16 des huitiemes.
- **Ce comptage reste juste sur une vue partielle**, et c'est ce qui le rend utilisable :
  un match jamais scanne elimine un joueur jamais vu, il ne compte ni au numerateur ni au
  denominateur. Constate en reel : le tableau ATP du Canadian Open n'a montre que 79
  joueurs — une vue tronquee — et son compte tombait malgre tout sur les memes 16 que le
  tableau WTA, vu entier, pour la meme journee.
- **Ce que la vue partielle interdit, c'est de compter depuis le debut.** « 2e tour »
  suppose de connaitre la taille du tableau, « quart de finale » non. Les tours de la fin
  sont donc nommes sans condition ; les premiers seulement quand le total des joueurs est
  une taille de tableau qui existe (`PLAUSIBLE_DRAWS`). 79 n'en est pas une : ce tournoi
  s'est tu sur ses premieres journees et a retrouve la parole a l'approche de la fin.
- **Le compte s'arrondit a la puissance de deux superieure** : quatre joueurs moins une
  demi-finale deja jouee en laissent trois, et c'est le compte du *debut* du tour qui le
  nomme. Sans cet arrondi, le second match d'une soiree prendrait le nom du tour suivant.
  Meme raison pour les matchs **simultanes**, qui ne se comptent pas les uns les autres.
- `draw_sequence` tient compte des **exemptions** : de 96 joueurs on passe a 64, jamais a
  48. Sans cela le premier tour d'un Masters ne serait pas le premier element de la suite.
- Les editions se separent par un trou de plus de `EDITION_GAP_DAYS` : la competition garde
  son identifiant d'une annee sur l'autre, et les joueurs de l'edition precedente
  gonfleraient le compte toute la semaine.
- En cas de doute, **aucune ligne**. Un « demi-finale » affiche sur un quart serait l'erreur
  la plus visible que ce module puisse produire.

## Tennis, cyclisme et saisie manuelle

- `render.py` a un ordre de marches **par sport** (`MARKET_ORDER_BY_SPORT`). Le tennis parle
  en sets et en jeux, jamais en « 1N2 ». Le cyclisme n'a qu'un marche `outright`.
- Un evenement sans second participant (une etape) ne doit jamais afficher de tiret orphelin :
  `affiche` et `_header` le gerent, avec leurs tests.
- `services/manual.py` : cotes libres, une par ligne. Une ligne illisible part dans
  `ParsedOdds.rejected` et **est affichee a l'utilisateur** — jamais ignoree en silence.
- Les cotes manuelles portent le bookmaker `manual` et **aucun horodatage de releve** : ce
  serait l'heure de la frappe, pas celle d'un releve de marche.
- `services/competitions.py` : synchronisation depuis `GET /sports`, **gratuit**. Une
  competition decouverte est creee **inactive** — rien ne se met a couter sans decision.
  L'appel passe `all=true` : sans lui, seules les competitions que le fournisseur sert a
  l'instant sont decouvertes, et une phase de qualification europeenne reste introuvable
  jusqu'a ce que les cotes arrivent — donc trop tard pour l'activer avant les premiers
  matchs. `api_active` distingue les deux etats, sinon une competition active qui ne
  ramene rien devient un mystere. Activer une competition hors saison ne coute rien :
  une reponse vide n'est pas facturee.
- `board.filter_options()` : le menu des competitions est **groupe par sport puis par
  niveau**, trie par nom a l'interieur, accents ignores (`labels.sort_key`). L'ordre par
  priorite decroissante sert le scan, pas la lecture : sur un catalogue complet il
  melangeait les sports et ne laissait aucun moyen de deviner ou chercher. La priorite
  continue de trier les lignes du board, ou elle a un sens. Le groupement est fait en
  Python et non par le filtre `groupby` de Jinja, qui retrierait les groupes par ordre
  alphabetique et remettrait « ATP/WTA 500 » devant « Grand Chelem ».
- `competitions.CATEGORIES` : le **niveau** d'une competition. Meme regle que la
  surface — **rien n'est deduit d'un libelle a l'execution** : « Masters » vaut pour
  Monte-Carlo comme pour le tournoi de fin d'annee, et « Super League » pour la Chine
  comme pour la Grece. Les seeds des migrations 013 et 024 sont en revanche des
  decisions humaines, verifiees cle par cle ; le reste se saisit depuis
  `/competitions`. Un niveau non renseigne ne produit **aucune ligne** de statistiques :
  « non renseigne » ne dirait rien sur les matchs, seulement sur la saisie.
  - **Une taxonomie par sport** (`CATEGORIES_BY_SPORT`), et la saisie valide contre
    celle du sport et non contre la liste a plat : depuis que le football a la sienne,
    `grand_slam` est une cle connue, et l'accepter sur une Ligue 1 produirait un
    regroupement que plus rien ne distinguerait d'un vrai tournoi. Un sport sans
    taxonomie — le cyclisme — n'affiche aucun menu.
  - `masters_1000` couvre les Masters 1000 de l'ATP **et** les WTA 1000 : meme etage de
    la hierarchie, et le circuit se lit deja dans le libelle — les separer diviserait
    par deux des echantillons deja courts. Meme arbitrage au football pour `d2`, qui
    couvre la deuxieme division **et en dessous** : League 2 anglaise et 3. Liga
    n'auraient chacune aucune selection, et le libelle annonce l'amalgame.
  - **Le football n'en avait aucun, et c'est ce qui a rendu 59 selections invisibles.**
    Le regroupement « par niveau » portait exactement l'effectif du tennis, et les
    selections football se repartissaient sur douze championnats de une a six lignes —
    donc sous le seuil de lecture par competition, et noyees ensemble sous « Football ».
    Le seul etage intermediaire manquait. Mesure apres le seed : 5 lignes football,
    dont `1re division — Europe` 13/29 et `Coupe continentale` 11/21.
  - **Les qualifications europeennes ne recoivent pas de niveau distinct**, et ce n'est
    pas un arbitrage : The Odds API sert les tours preliminaires et la phase de ligue
    **sous la meme cle** pour l'Europa League comme pour la Conference League. Un niveau
    se pose sur une cle, donc les separer est hors de portee.
  - **`COMPETITION_CATEGORIES` double les migrations, comme `APIFOOTBALL_LEAGUES`.**
    Une migration ne classe que ce qui est **deja en base quand elle tourne**, et la
    synchronisation decouvre en permanence : sans cette table, chaque competition
    apparue apres le seed arriverait sans niveau. Elle comble un manque, n'ecrase jamais
    une saisie. Un test relit les deux fichiers de migration et les compare a la table
    plutot que d'en recopier la regle — trois ecritures de la meme decision divergeraient
    sans un mot, un niveau ne se voyant nulle part sur le board.
  - **Le niveau se resout a la lecture**, il n'est jamais recopie sur la selection. C'est
    ce qui rend la taxonomie corrigeable : reclasser une competition reclasse tout son
    historique, sans migration ni reprise de donnees.
  - **Une cle non classee ne disparait pas en silence** : `unclassified()` la reclame
    dans `/competitions`, avec ce qu'elle porte deja de selections — classer une
    competition a onze paris repare onze lignes, classer une competition vierge n'en
    repare aucune. Les sports sans taxonomie n'y figurent jamais.
  - **`Analysis.uncategorised` ferme l'addition** : la somme des niveaux plus les non
    classees vaut le total tranche. C'est un **compte, jamais une barre** — un taux
    moyen de tout ce qui n'a pas ete saisi n'a aucune coherence sportive et ne dirait
    rien des matchs, alors que le compte, lui, est juste. Sans lui, des selections
    quittaient le regroupement sans qu'une seule ligne ne le signale.

## Le preambule ne documente que les sports du lot

Le mode d'emploi des lignes — quarante lignes pour le tennis, autant pour le football —
etait rendu en entier sur chaque prompt : une session de football payait l'explication de
l'Elo et des handicaps jeux, une session de tennis celle des buteurs et des formations.

`build_prompt` passe donc `sports`, l'ensemble des sports presents, et le preambule se
garde par `{% if 'tennis' in sports %}`. Mesure : **6 555 tokens de preambule pour les deux
sports, 5 126 pour le football seul, 4 457 pour le tennis seul** — de 22 a 32 % de
gagnes, sur un budget que surveillent les deux plafonds decrits plus bas.

C'est la meme regle que pour les blocs : **ce qui n'a pas de donnee est omis, jamais rendu
vide.** Corollaire pour les tests : un prompt construit sur une session **vide** ne porte
aucun garde-fou de sport, et pour cause. Six tests l'ont appris en cassant — ils
verifiaient le template a travers un rendu sans match. Ils portent desormais sur un lot du
bon sport, ce qui teste aussi le conditionnel.

## Generer un prompt par competition

`build_prompt(..., competition_id=...)` restreint le lot **sans toucher a la shortlist**.
Sur une soiree a trente matchs, la recherche par match s'etiole : constate en comparant deux
analyses reelles — la colonne « effectif / absences » etait renseignee sur 8 matchs sur 8
dans un lot de tennis, et sur 7 sur 27 dans un lot de qualifications europeennes. Huit a
douze matchs par prompt est le volume ou l'analyse reste dense.

Decocher pour scinder aurait marche, mais ferait perdre le rattachement des picks : la
shortlist reste entiere, seul le rendu est filtre. Le selecteur n'apparait qu'a partir de
deux competitions, et affiche le compte de chacune — de quoi juger d'un coup d'oeil si un
lot merite d'etre coupe.

## Historique et personnalisation

- `services/history.py` : **aucun calcul financier**, jamais. Le seul indicateur est
  `gagnes / (gagnes + perdus)`. La mise est enregistree mais **jamais agregee** — un test
  verifie qu'aucun champ `roi`, `profit` ou `stake` n'apparait sur les agregats.
- `pickable_events()` : les matchs proposes au rattachement d'une selection. La shortlist
  d'abord — c'est ce qui a ete analyse — puis les matchs voisins de la session
  (`PICKABLE_BEFORE_H` / `PICKABLE_AFTER_H` autour de son `created_at`), marques
  « hors selection » et horodates.
  - **`query` leve la fenetre de temps, et elle seule** (`SEARCH_LIMIT`, 50). Le voisinage
    couvre la journee de travail, pas un pari pose trois jours plus tot ni un match
    reporte : quand le match cherche n'etait nulle part dans le menu, il n'y avait plus
    aucun recours et la selection restait sans evenement — donc sans sport, donc muette
    dans les statistiques. La recherche porte sur les deux equipes **et** la competition :
    on se souvient parfois du tournoi et pas des noms.
  - **L'heure precede l'affiche sur tous les matchs**, plus seulement hors shortlist. Une
    session porte trente affiches sur deux jours et le rattachement se fait de memoire
    (« le match de 20h30 ») : sans l'heure, il fallait reconnaitre l'affiche pour
    retrouver le match, ce dont on n'est justement pas sur.
  - **Les groupes sont ranges par heure du premier match**, shortlist d'abord. Ils
    l'etaient par identifiant de sport puis par nom : « Bundesliga 2 » passait devant
    « Premier League » pour des raisons alphabetiques. Une session se relit dans l'ordre
    ou elle s'est jouee.
  - `_pick_options.html` est la **seule** source de cette liste d'options. Elle etait
    recopiee a trois endroits — apercu d'import, ajout a la main, rattachement — donc
    trois occasions de diverger : la recherche n'aurait ete branchee que sur l'une
    d'elles. La recherche ne remplace que les `<option>` du menu qui la suit ; rerendre
    le formulaire ferait perdre le focus a chaque frappe. **Sans ce second groupe, un match commence etait
  irrattachable** : il a quitte le board, il n'a donc jamais pu etre coche, et la
  selection qui le visait restait « — hors match — » — donc sans sport ni competition,
  donc muette dans les statistiques. `set_event()` corrige apres coup, et refuse un
  identifiant inconnu plutot que de laisser un pick pointer sur du vide. L'import des
  picks essaie la shortlist **avant** le voisinage : l'elargissement ne doit pas rendre
  ambigu un match qu'elle designait seule.
- `worksheet()` : la feuille de session en **deux blocs** — ce qui reste a trancher, puis
  ce qui l'est deja (un pari annule est tranche : il n'y a plus rien a saisir dessus).
  Melangees, il fallait relire quinze lignes pour trouver les trois qui attendaient un
  resultat. Chaque bloc est groupe par competition puis range par heure de coup d'envoi :
  on relit une journee tournoi par tournoi, pas dans l'ordre ou Claude a rendu son
  tableau. **Le titre de groupe est un `td`, jamais un `th`** — `table.board th` est
  `position: sticky`, et un en-tete par competition viendrait recouvrir les lignes en
  defilant. Un test le verifie.
- Edition des templates : le corps est **compile avant ecriture**. Un template casse
  briserait toute generation de prompt, donc on refuse plutot que d'ecrire.
- Nom de template : `^[a-z0-9][a-z0-9_-]*\.md\.j2$`. Pas de traversee de repertoire.
  Le template par defaut n'est pas supprimable.
- Bandes de cotes : bornes controlees avant ecriture (haute > basse, quota max >= min).

## Coupons joues (`services/coupons.py`)

Un **pick** est une selection ; un **coupon** est ce qui a ete pose chez le bookmaker :
une mise, une ou plusieurs jambes, un resultat global. Un coupon se compose de picks
deja saisis — rien n'est retape.

- **`played` ne passe a vrai qu'au rattachement a un coupon**, et repasse a faux si le
  coupon est supprime. C'est la definition : joue = pose chez le book, pas propose par
  l'analyse. Une selection ecartee ne pese donc ni sur les taux (`stats()`) ni sur le
  retour d'experience du prompt (`feedback()`) — sans quoi les indicateurs melangeraient
  deux questions differentes : ce que vaut l'analyse, et ce que valent mes paris.
  `add_pick()` a donc `played=False` par defaut, et la migration 011 a aligne l'existant.
- Corollaire a ne pas oublier en test : marquer `played` a la main ferait passer un test
  sans que le parcours reel fonctionne. Les tests d'agregats passent par un coupon.

- Le type et le resultat ne sont **jamais stockes** : ils se deduisent des jambes. Un
  champ enregistre pourrait contredire les jambes, et il faudrait alors arbitrer.
- Regle du resultat : une jambe perdue fait tomber le coupon, meme si d'autres sont en
  attente ; une jambe annulee est neutre (le book recalcule la cote sans elle) ; tout
  annule vaut annule. Chaque cas a son test.
- Ce que ca repare : un combine s'enregistrait comme un pick sans evenement, donc sans
  sport, et les taux par sport l'ignoraient en silence. Ses jambes portent desormais
  chacune leur match.
- Les taux de coupons sont **separes par type** : un combine tombe des qu'une jambe cede,
  il ne se compare pas a un pari simple. Les melanger produirait un taux qui ne decrit
  ni l'un ni l'autre.
- **Aucun calcul financier** : la mise est memorisee, jamais agregee, jamais multipliee
  par une cote — et la **cote totale du coupon n'est meme pas calculee**, la capture la
  porte deja. Un test verifie qu'aucun champ financier n'existe sur `Coupon`.
- La capture est une **piece jointe, jamais une source de donnees** : la machine ne la lit
  pas. La lire supposerait un modele de vision (interdit n°6) ou un OCR local peu fiable.
- Securite du televersement : liste blanche de types **confirmee par les octets de tete**,
  taille bornee, et **le nom fourni par le navigateur n'est jamais utilise** — il est
  refabrique (`coupon-{id}-{empreinte}.{ext}`). Le nom relu en base est revalide contre ce
  motif avant d'ouvrir le fichier : une base modifiee a la main ne doit pas faire servir
  `../../.env`.
- Les captures vivent sous `data/`, donc deja couvertes par le `ReadWritePaths` de l'unite
  systemd et le `.gitignore`. Elles ne sont **pas** dans la sauvegarde, qui ne porte que
  sur la base.

## Le preambule ne documente que ce que le lot porte vraiment

Prolongement d'un cran de la regle des sports. `build_prompt` passe
`context_labels` — l'ensemble des libelles de contexte reellement rendus dans le lot — et
le mode d'emploi d'une ligne se garde par `{% if 'Buteurs' in context_labels %}`. Le
preambule expliquait les buteurs a un lot sans props et l'Elo a un lot dont aucun joueur
n'est classe. Mesure : le socle par sport passe de 2 267 a 1 814 tokens au football, de
2 696 a 2 283 au tennis.

- **Une porte ne se pose que sur un mode d'emploi, jamais sur l'explication d'une
  absence.** La porte essayee sur `Palmares` / `H2H ici` etait fausse : ce paragraphe dit
  precisement que *l'absence* de ces lignes signale un tournoi non rattache et non un passe
  vierge. La cacher quand elles manquent, c'est la cacher au seul moment ou elle sert. Un
  test existant l'a attrapee — c'est pour ca qu'il existe.
- **Une faute de frappe dans un libelle ne casse rien**, et c'est le danger : la condition
  est toujours fausse, le mode d'emploi disparait sans un mot, et la donnee reste affichee
  pour se lire de travers. Un test verifie que chaque libelle vise par une porte existe
  dans `labels.CONTEXT_ICONS`, le registre des libelles que le code sait produire — meme
  garde-fou que les identifiants du sprite.
- Les listes **« CE QU'IL FAUT VERIFIER »** sont gardees par sport de la meme facon. Un lot
  de tennis payait l'arbitre, la pelouse et le risque de bordures.

**Corollaire pour les tests** : un garde-fou ne se teste plus sur un evenement vide. Il
faut un lot qui porte la ligne — donc de vrais buteurs recuperes, ou de vrais matchs en
base. Trois tests l'ont appris en cassant, exactement comme les six du garde-fou de sport.

## Huit libelles sortaient sans pictogramme

Releve en rendant le bloc CONTEXTE de 250 evenements reels et en le passant a
`context_icon()` : `Buteurs`, `Buteur abs.`, `Total buts`, `Serie`, `Calendrier`,
`Precedent`, `Lieu`, `Pelouse` — plus `Niveau adv.` et `Marge`, ajoutes le meme jour.
C'est exactement le defaut que le sprite devait supprimer : la colonne se vide sans rien
dire. **Toute ligne ajoutee a un bloc doit entrer dans `CONTEXT_ICONS` le meme jour**, et
le script d'audit tient en dix lignes — le refaire coute moins que de le regretter.

## Le score exact en sets, au tennis, se propose sans prix

The Odds API ne sert **aucun** marche de score en sets au tennis ; le bookmaker, lui, le
propose. La demande est donc portee par le template et **hors du tableau des selections** :
un score en sets par match, fonde sur la maniere (`Profil`, `Marge`, H2H set par set) et
non sur l'issue, avec l'interdiction explicite d'inventer une cote, d'en faire une ligne de
la section C ou une jambe de combine. Une cote inventee entrerait en base, classerait la
selection dans le mauvais palier et fausserait le taux de reussite — c'est la meme raison
qui interdit de remplacer une cote du bloc par une cote trouvee en ligne.

## Le vainqueur n'est pas le debouche par defaut (section B du template)

Sur les trente premieres selections de tennis, **vingt-cinq portaient sur un
« Vainqueur »**, pour onze gagnees — et une seule sur un total de jeux. La section B
demandait « le marche qui traduit le mieux l'angle » sans jamais dire que ce marche-la ne
retient d'un raisonnement que le nom d'un camp. Elle le dit maintenant, et nomme les
lignes faites pour les marches derives.

La section C porte deux exigences de plus, mesurees elles aussi : **un palier au-dela des
deux plus surs reclame un fait nomme et date** de la section A — le palier ULTRA FUN etait
a 0/6 et les selections a plus de 2.00 a 1/7, quand les favoris tenaient ; et **deux
selections qui reposent sur la meme cause doivent le dire**, regle qui n'existait que pour
les combines alors qu'un tableau entier peut tenir sur une seule soiree.

**Le rappel reste sportif, et c'est la seule facon de l'ecrire.** « Le book est plus fort
sur le 1N2 » serait vrai et interdit : chercher ou le prix est tendre est exactement la
recherche de value de la section 9. Le template dit donc qu'un angle decrivant une
**maniere** se traduit mieux en handicap ou en total, et ajoute noir sur blanc que
preferer un marche plus genereux serait raisonner sur le prix. Un test verifie que les
deux phrases y sont.

### Le rappel ne suffisait pas : la forme du tableau se compte

Le paragraphe ci-dessus expliquait, sans que rien ne verifie jamais **la sortie**.
Mesure a 71 selections tranchees : `Vainqueur` reste le plus gros regroupement de la
base (28 lignes, 13/28) et le plus faible, et **28 des 35 selections tennis** y sont —
il ne restait que six handicaps jeux et un total de jeux. Deux morceaux, qui n'en font
qu'un :

- **section B, la nature de l'angle en un mot** — « issue » ou « maniere ». C'est ce
  mot qui choisit le marche, et l'ecrire empeche un raisonnement sur un rythme de
  finir sur un nom de camp ;
- **section C, le comptage** : si plus de la moitie du tableau porte sur le vainqueur,
  ces selections se relisent avec ce mot. Si elles decrivent **toutes** une issue,
  elles restent et le tableau le dit — c'est une information sur le lot, pas une faute.

Le controle porte sur le **lot** et jamais sur une selection prise seule : la
contrainte inverse — « varie tes marches » — ferait choisir un marche pour ne pas
ressembler au precedent, ce qui n'est pas un angle. Et rien n'y invite a un marche
mieux paye : ce serait raisonner sur le prix.

**Les deux tombent ensemble sous quatre matchs.** Une proportion sur deux lignes ne
decrit rien — meme regle que `FEEDBACK_MIN_ROWS` — et la derive mesuree s'est produite
sur des lots de huit et plus. Garder le mot de la section B sans le comptage aurait
coute des tokens sans rien mettre en face.

**Le budget de tokens a tranche la forme**, et le conditionnement reste juste sur le
fond : une proportion ne se lit pas sur deux lignes. Mais l'arbitrage a ete rendu sous
une contrainte fausse — voir la section suivante.

## Deux plafonds de tokens, et ce qu'ils mesurent vraiment

Le garde-fou historique annonçait 8000 tokens pour six matchs de football. Il en
mesurait **6572**, quand un vrai lot de six matchs en pesait **8304** : sa fixture ne
clonait que les **cotes**, jamais le bloc CONTEXTE. Tout ce que les phases 11 a 15 ont
ajoute — statistiques de saison, profil de maniere, dossier d'equipe — n'a donc jamais
ete mesure. Le plafond paraissait garder 1400 tokens de marge la ou la production
l'avait franchi depuis des mois sans que rien ne bronche.

- La fixture **enrichit desormais pour de vrai** : `fetch_context` et
  `dossier.refresh_event` passent une fois par leur vrai parcours, puis les lignes de
  `context` se recopient sur les clones **comme les cotes**. `KIND_TEAMS` voyage avec
  elles, donc chaque clone retrouve les memes identifiants d'equipe et le meme dossier.
  Mesure : **8957**, un peu au-dessus de la production, ses six blocs etant tous
  complets quand un vrai lot en porte de plus pauvres. C'est ce qu'un plafond doit
  mesurer.
- **Ce sont des alarmes, pas des budgets, et c'est une decision de l'utilisateur** :
  un prompt long ne le gene pas, quitte a ce que l'analyse prenne dix minutes de plus.
  Les plafonds valent donc la mesure **plus environ 2000 tokens** — `PROMPT_BUDGET`
  11500, `MIXED_BUDGET` 10000 — et ne servent plus qu'a rattraper une explosion
  **involontaire** : une porte de preambule cassee qui rendrait tout le mode d'emploi
  sur chaque lot, un bloc duplique. A ~500 tokens de marge, ils arbitraient chaque
  ligne ajoutee, et trois sessions de suite s'y sont usees.
  - Ce qui n'a **pas** change : la densite reste un objectif de qualite. Une ligne sans
    donnee est omise, un mode d'emploi se garde sur son libelle. Le plafond ne
    remplacait deja pas ces regles.
  - Le vrai cout residuel n'est plus le token mais l'**appel** : reconstruire les
    absents la ou `coverage.injuries` est faux vaut 24 a 36 appels par lot, et ce
    plafond-la n'a pas bouge.
- **Les deux lots mesurent des choses opposees, et il faut les deux.** Six matchs de
  football enrichis pesent par leurs **blocs** ; trois sports pour trois matchs pesent
  par leur **en-tete**, les trois modes d'emploi etant ouverts en meme temps sur trois
  blocs montes a la main. Une ligne ajoutee a un bloc se voit sur le premier, une ligne
  ajoutee a une section non gardee sur le second.
- **Un plafond ne se releve pas pour faire passer un ajout.** Ici il a ete recale parce
  que la mesure etait fausse, ce qui est autre chose : le nombre a suivi la realite, il
  ne l'a pas autorisee. Regenerer un prompt reel — `build_prompt(session_id)`, aucun
  appel reseau — reste la seule facon de verifier qu'une fixture n'a pas divergé.
- **Aucune ligne vide double dans un prompt** (`_collapse_blank_lines`). Chaque porte
  du preambule laisse la sienne quand elle ne rend rien : un lot de tennis en portait
  onze coupures de deux lignes ou plus, dont une de quatre. Regler les blancs porte par
  porte avec `{%- if -%}` marche une fois puis se defait a la porte suivante, et il
  s'en ajoute a chaque ligne de contexte documentee. Une regle de rendu tient toute
  seule.
- Les deux jeux de routes API-Football vivent dans `tests/helpers.py`, parce que trois
  fichiers en dependent maintenant. **Piege non evident** : le plancher du dossier lit
  `last_known_quota`, donc le **dernier** releve tous endpoints confondus. Un contexte
  simule a 82 appels restants suffit a suspendre le dossier qui le suit, et le bloc sort
  sans entraineur ni historique de saison **sans qu'aucune erreur ne soit levee** — la
  panne exacte qui a fait echouer la premiere version de cette fixture. Un
  enrichissement complet se simule donc de bout en bout avec `DOSSIER_RATE_HEADERS`.

## Trois regles du gabarit s'annulaient l'une l'autre

Toutes trois nees d'ajouts faits a des endroits differents sans relecture croisee.
Chacune annulait une consigne voisine, et aucune ne cassait quoi que ce soit — c'est ce
qui les rend chercheuses.

- **Le cran de confiance 5 etait inatteignable.** Il exigeait « Ce qui manque » vide,
  quand la section A demande de nommer **tout** ce qu'on n'a pas trouve : la colonne
  n'est pratiquement jamais vide. Le cran s'aligne donc sur le 4, qui avait deja la bonne
  formulation — le trou doit etre **sans rapport** avec ce qui porte la selection, pas
  absent. C'est exactement le defaut que la table des crans devait corriger.
- **La clause de silence fermait tout le chapitre.** « N'en tire aucune tendance, et
  n'ecris rien a ce sujet » couvrait, seize lignes plus haut, la demande de commenter un
  lot dont le taux de selection sort de l'ordinaire. Elle est **portee sur les seuls
  taux**, et la meme clause de perimetre figure dans la branche « assez de recul » —
  sans quoi le probleme reapparaitrait le jour ou le seuil est franchi. Un test lit
  cette branche-la directement dans le gabarit.
- **La section D etait insatisfiable sur un petit lot.** Elle reclamait un combine de
  3-4 jambes **et** un second de 4-5, une seule selection par match — sur cinq matchs et
  un taux de selection median de 36 %, la sortie attendue tourne autour de deux
  selections. Sous `combo_min_lot`, le prompt n'en demande qu'un, et les deux
  paragraphes qui supposent deux combines se gardent avec.

## Les fiches de competition, et ce qui manque doit se voir

Un lot de cinq matchs portait trois fiches — Allsvenskan, Superliga danoise, Primeira
Liga — et **aucune pour l'EFL Cup**, qui etait le match le plus atypique du lot : un tour
de coupe anglaise est le format ou la rotation d'effectif est la regle et non l'exception,
exactement le fait de format et de calendrier que ces fiches ont pour role de porter.

- `COMPETITION_NOTES` seede les coupes nationales et continentales. Ce qui y est ecrit est
  **structurel et durable** — nombre de manches, tour d'entree des grands clubs, terrain du
  match, ecart de niveau attendu. Rien qui change d'une saison a l'autre : un fait perime
  dans le prompt coute plus qu'une fiche absente, et la phase en cours se lit deja sur le
  match.
- **Aucune migration ne les rejoue**, contrairement aux niveaux, et c'est un arbitrage :
  c'est de la prose de plusieurs lignes, et la tenir a jour des deux cotes la ferait
  diverger au premier ajustement. La synchronisation comble le manque — elle tourne tous
  les jours avec le lot gratuit — et **n'ecrase jamais une fiche ecrite a la main** : celle
  de l'utilisateur vaut toujours mieux que la notre.
- **`without_notes()` reclame ce qui manque, avec ce que ca a deja coute.** Le compte vient
  de `prompt_events` : ce sont des matchs **reellement partis a l'analyse** sans que le
  format de leur competition soit dit. Une competition active mais jamais analysee est
  signalee sans compte — il n'y a rien a rattraper, seulement quelque chose a preparer ;
  une competition inactive et jamais analysee ne figure pas du tout, elle serait du bruit
  sur tout le catalogue. Meme logique que les cles non classees : ce qui manque doit se
  voir dans l'interface, pas se decouvrir dans le prompt.

## Deux lignes disaient plus que ce qu'elles savaient

- **`Forme 5` melange deux fenetres, et chaque moitie porte desormais son propre
  denominateur** : `Silkeborg ND (2j) 10-6/5` — deux matchs dans la competition, cinq
  derniers toutes competitions pour les buts. Le compte unique laissait lire seize buts en
  deux matchs. Les deux s'ecrivent **meme quand ils coincident**, ce qui est le cas
  ordinaire : ne les ecrire qu'en cas d'ecart rendrait une ligne sans annotation ambigue —
  coincidence, ou verification jamais faite ? La longueur des lettres **est** la fenetre,
  `_form_letters` gardant les `FORM_LENGTH` dernieres.
- **`Enjeu` est date et marque « indicatif » en debut de saison.** A la 3e journee sur 32,
  « Relegation Playoffs » decrit l'ordre alphabetique autant que le niveau — et le prompt
  ordonne de recopier cette ligne comme l'enjeu reel, sans recherche. Elle est **datee
  plutot que supprimee** : l'information reste, c'est bien ce que la competition declare,
  et sa portee est dite.
  - **Le nombre de journees jouees suffit, le total de la saison n'est pas calculable** :
    il ne se deduit pas du nombre d'equipes — la Superliga danoise joue 32 journees a
    douze equipes, quand un double round-robin en donnerait 22. Ecrire « 2j/32 » aurait
    demande une donnee qu'aucune source ne fournit.
  - Le seuil se regle (`enjeu_min_journees`, 8 par defaut, environ un quart d'une saison
    ordinaire).

## La densite du bloc CONTEXTE (`session.context_density`)

Sur un lot de cinq matchs, la shortlist affichait le **meme badge « 3 marches »** pour
tous, quand deux blocs etaient complets, deux tres pauvres — championnats a leur deuxieme
journee — et un **entierement vide**. Ce dernier avait consomme son credit
d'enrichissement pour ne rien rapporter, sans que rien ne le signale avant le prompt.

- La densite compte les lignes **reellement peuplees** sur celles qu'un match pleinement
  servi porte dans ce sport (`labels.CONTEXT_EXPECTED`). Mesure sur le lot du 10/08 :
  24/24, 24/24, 10/24, **0/24**, 6/24 — elle separe mieux que n'importe quelle taxonomie
  de niveau, et sans aucun arbitrage manuel : c'est l'avancee dans la saison et la
  couverture du fournisseur, mesurees ensemble.
- **Le referentiel exclut les lignes structurellement conditionnelles** — `Aller` (manche
  retour), `Statut` (report), `Lieu` (delocalisation), `Pelouse` (synthetique), `Compos`
  (fenetre horaire), `Effectif` (substitut la ou `/injuries` ne couvre pas), `Abandons` et
  `H2H ici` au tennis. Sans quoi chaque match paraitrait pauvre pour de mauvaises raisons.
  `Stats match` en est exclue pour une raison de plus : c'est une ligne **negative**, et la
  compter recompenserait une absence.
- **Une ligne hors referentiel ne fait pas monter la densite** au-dessus de son plafond :
  `Aller` est un bonus, pas un du.
- Le rapprochement passe par `context_family` : « H2H (5) » et « H2H (1) » sont la meme
  ligne, seul le nombre de confrontations change.
- **Un sport sans referentiel n'a pas de densite** : le cyclisme est saisi a la main, une
  densite y mesurerait la saisie. Il n'est donc jamais « pauvre », et le filtre ne
  l'ecarte pas — ce serait un jugement plutot qu'une mesure.
- **Le bloc du match porte sa densite quand elle est basse.** Un match a 0 sur 24 se lisait
  comme un match sur lequel il n'y avait rien a dire, alors que c'est notre collecte qui
  n'a rien rapporte. La distinction decide de la suite : ce qui manque la est justement ce
  que la recherche web a le plus a apporter.
- **Un enrichissement vide est annonce sur la shortlist**, avec un bouton pour sortir le
  match du lot. Il reste selectionnable — mais par choix explicite, pas par defaut.
- **Cout, et ce qu'il a fallu corriger** : la densite appelle `context_block` par match,
  le seul assembleur — un second chemin aurait diverge, comme deja deux fois. Le premier
  jet tenait 377 ms pour quatre matchs de tennis, `ratings_by_key` resolvant le classement
  entier **par evenement**. Un cache passe par l'appelant et vivant le temps d'un lot le
  ramene a 245 ms, et sert aussi la generation du prompt. Pas de memo global : son
  invalidation apres un rafraichissement d'Elo serait a inventer. Mesure finale : 14 ms
  par match de football, 37 a 77 en tennis.

## Les quotas de palier se calculent, ils ne s'expliquent plus

Le prompt affichait les bornes d'un lot de dix — `0-6 🟢, 0-5 🔵, 0-3 🟠…` — sur un lot de
cinq, puis expliquait en prose qu'elles « se reduisent a proportion du lot » et laissait
le calcul a faire. **Une borne qu'il faut recalculer soi-meme ne contraint rien.**

- `Tier.quota_for(lot)` rend la borne reelle : `min(quota_max, arrondi(quota_max × lot /
  QUOTA_REFERENCE_LOT))`. Le paragraphe explicatif a disparu avec le calcul — c'est
  autant de texte gagne, et la ligne « le total ne peut pas depasser N », qui etait deja
  calculee, a servi de modele.
- **Les deux paliers les plus surs gardent un plancher a 1** (`QUOTA_FLOOR_TIERS`) : un
  petit lot doit pouvoir porter une selection sure, sinon la reduction interdirait de
  rendre quoi que ce soit. Au-dela le plancher est 0 — un palier haut vide est un
  resultat, et la section C demande de le commenter.
- **La borne basse ne depasse jamais la haute** : la valeur reglee peut survivre a la
  reduction, et « 2-1 » ne se lit pas.
- **L'arrondi est au plus proche, moities vers le haut**, et pas celui de `round()` : la
  regle bancaire de Python rend 2 pour 2.5 et 2 pour 1.5, soit deux comportements
  differents sur deux paliers voisins.
- `Tier.quota_label` reste la borne **reglee**, et ne sert plus qu'a l'ecran des reglages.
  Corollaire pour les tests : une session vide rend desormais `0-0` partout, ce qui est
  juste mais ne dit rien d'une saisie — celui qui verifie qu'une bande modifiee atteint le
  prompt monte donc un lot de la taille de reference.

## Ne proposer que les paliers atteignables

Sur un lot de quatre quarts de finale, la cote la plus haute valait **3.40** : 🔴 GIGA FUN
et 💥 GIGA+ etaient hors d'atteinte avant que l'analyse commence. Le prompt injectait
pourtant `0-1 🔴, 0-0 💥`, puis exigeait qu'un palier vide soit commente « en nommant ce
qu'il aurait fallu trouver » — une ligne d'excuse pour une case impossible. Symetriquement,
un bloc dont la cote la plus basse valait 1.71 ne pouvait porter aucun 🟢 SAFE, et rien ne
le disait.

- **L'atteignabilite se mesure sur les cotes reellement offertes, pas sur l'intervalle
  qu'elles couvrent.** Un lot a 1.50 et 3.00 ne porte aucun prix entre les deux : declarer
  FUN atteignable parce qu'il « tombe entre » ferait chercher un prix qui n'existe nulle
  part. Une selection recopie **une** cote d'**un** bloc, jamais un intervalle.
- La section C annonce les bornes du lot **avec leur emplacement** (`3.40 (M2 · Vainqueur
  Diana Shnaider)`) : une borne sans l'endroit ou elle se lit oblige a relire tous les
  blocs, et personne ne le fait.
- Chaque bloc ferme sur une ligne `Paliers`. Sa **parenthese ne parait que s'il restreint
  au-dela du lot** — ce que le lot exclut partout est deja dit une fois.
- Un palier dont le quota **regle** vaut zero n'est jamais annonce : proposer une case
  qu'on s'est deja interdit de remplir n'a pas de sens.
- `Tier.covers()` porte la convention « la borne haute appartient au palier suivant ».
  Elle est ecrite **deux fois** — ici et dans `history.tier_for_price`, le module du prompt
  important celui de l'historique — et un test compare les deux implementations.

## La ligne `Fraicheur`, ou ce que le retard de l'historique coute en matchs

`Historique` disait jusqu'ou allait le jeu de donnees et `Parcours` nommait les adversaires
du tournoi en cours : il fallait croiser les deux, de tete et a deux cent cinquante lignes
d'ecart, pour comprendre que trois matchs d'un quart de finaliste n'entraient dans aucune
des cinq lignes qui le decrivent. L'« Usure 30.5 jeux/match » de Jodar ignorait le tournoi
qu'il venait de disputer.

- Le compte dort dans **nos propres scans** : les tours precedents ont ete vus les jours
  d'avant. Aucun appel, aucune cle.
- Le rapprochement se fait sur la **journee de tournoi**, comme le repos : le fichier de
  resultats date un match du jour ou il se joue sur place, et une session du soir a
  Montreal part apres minuit a Paris.
- Elle est le corollaire d'`Historique` : sans retard, aucune ligne — dire « rien ne
  manque » ferait douter de donnees completes. A compte nul, elle ecrit
  « toutes les lignes a jour ». Elle n'entre donc **pas** dans `CONTEXT_EXPECTED`, qui
  compte deja `Historique`.
- **Elle nomme les adversaires, et c'est la seule chose que le bloc ne dit nulle part
  ailleurs.** Le compte est sur cette ligne, la liste complete du tournoi sur `Parcours`
  deux lignes plus haut : savoir *lesquels* manquent demandait de croiser les deux de tete,
  exactement le travail que ce projet retire a l'analyse. Chaque nom est un match
  identifiable, donc une recherche a mener — celle que le prompt designe comme la plus
  rentable du lot. Quand ils manquent **tous**, la ligne ecrit « (tout le Parcours) »
  plutot que de recopier `Parcours` mot pour mot.
  - Le rapprochement passe par `Load.faced`, une paire `(journee, adversaire)` : `opponents`
    et `days` sont tries chacun de son cote et ne se remettent pas en face l'un de l'autre.
    Sans la paire, le premier tri divergent attribuerait le mauvais nom.
- **Aspirer `atptour.com` a ete envisage et refuse.** Son `robots.txt` porte
  `User-agent: ClaudeBot` / `Disallow: /` — avec GPTBot, CCBot, Google-Extended et les
  autres agents d'IA — et un `Content-Signal: ai-train=no, use=reference` qui est une
  reserve de droits explicite au titre de l'article 4 de la directive UE 2019/790.
  Collecter ces pages pour les injecter dans un prompt Claude sous un autre nom d'agent
  serait un contournement, pas une lecture de la regle ; le projet respecte les `robots.txt`
  a la lettre, comme sur Tennis Abstract. Le chemin autorise est celui qui existe deja :
  **la recherche de Claude en session** — debloquee par l'echelle des sources, ces pages
  etant desormais de niveau 1 — et la `NOTE PERSO` pour ce qui est releve a la main.
- **`tennis_round.truncated()` ne dit qu'un booleen, et c'est deliberе.** Le nombre de
  tours manquants demanderait la taille du tableau, exactement ce qui empeche ce module de
  nommer un tour par son ordinal. Le seul signal sur est qu'un nombre de joueurs vus ne
  soit **la taille d'aucun tableau** : 79 au Canadian Open masculin. Faux negatif assume
  cote feminin — 64 joueuses vues sur un tableau de 96 forment un compte plausible, et un
  silence vaut mieux qu'une affirmation fausse.

## L'echelle des sources classe par editeur

Elle placait « ATP/WTA, site du tournoi » en niveau 1 et « feuilles de match, ordre du
jeu » en niveau 3. Or la recherche que le prompt designe lui-meme comme **la plus rentable
du lot** — les statistiques de service derriere l'onglet Stats d'`atptour.com` — est les
deux a la fois : selon la lecture, elle valait 1, donc confiance 4-5 accessible, ou 3, donc
plafonnee a 2 et jamais de palier haut au tennis.

Le critere est donc **qui publie**, jamais ce que la page contient. Corollaire non evident :
la consigne de la colonne `Source`, deux sections plus bas, portait la meme contradiction —
elle definissait le niveau 3 comme « une feuille de match, un ordre du jeu, un releve
officiel ». Corriger l'echelle sans elle aurait deplace l'ambiguite au lieu de la lever.

## Les rappels `(ref.)` se calculent, la section F ne les reclame plus

F est plafonnee a **trois lignes** et doit porter les marches manquants ; avec deux ou
trois selections de reference, elle etait pleine avant d'avoir rien dit d'utile — les
quatre blocs du 10/08 portaient tous « A relever : Hand. jeux, Jeux O/U ».

`prompt.reference_notes()` couvre **deux cas**, et le second n'a aucune ligne « A relever »
pour le signaler : un bloc ordinaire dont certains marches n'ont pas de prix maison, et un
bloc dont la **source principale** est elle-meme un book de reference — releve de
substitution, ou competition que Betclic ne sert pas. Tous ses prix etant de reference par
construction, aucun ne se detache. `labels.is_reference()` lit le suffixe « (ref.) » des
libelles plutot que de retaper la liste des books.

## Le conflit entre l'angle declare et le marche rendu

Le prompt demandait a l'analyse de s'auto-auditer : « compte tes lignes avant de rendre, si
plus de la moitie du tableau porte sur le vainqueur, relis-les avec leur colonne Type ». Or
les deux colonnes sont **en base** — `angle` depuis la migration 026, la famille du marche
depuis la 027 — et `maniere` contre famille `Issue` se detecte en une requete. Une regle
deterministe laissee au modele coute des tokens, se refait a chaque session et ne se mesure
jamais.

- **Calcule a la lecture, jamais recopie sur la selection** : c'est ce qui rend la
  taxonomie corrigeable — reclasser un marche reclasse tout l'historique, sans migration.
- **Une mesure de la qualite du rendu, jamais un blocage.** Une telle selection reste
  valable, simplement moins fidele a son propre raisonnement.
- Ce qui reste dans le prompt est la consigne de fond : le mot qui choisit le marche, et le
  rappel qu'un angle sur une maniere se traduit mieux en handicap ou en total.

## Le score exact en sets (`services/set_scores.py`)

La section D l'impose a chaque session de tennis ; il n'etait **ni enregistre ni verifie** —
ecrit dans le rendu, lu une fois, puis perdu, le sort exact de l'effectif collecte des mois
sans lecteur. C'est pourtant la **seule mesure de la lecture de la maniere qui soit
independante de tout prix** : quatre issues, verifiables sur n'importe quelle feuille de
match, et aucune cote n'existe pour ce marche chez le fournisseur.

- **Saisie a la main, jamais parsee du rendu**, et le choix est assume : le prompt interdit
  d'en faire une ligne du tableau C, ces scores arrivent donc en prose libre, et un parseur
  se tromperait en silence sur le taux qu'on cherche justement a mesurer. Meme regle que
  `angle` et `source_level` — quatre issues, quatre options d'un menu ferme.
- La liste porte sur le **lot** (`prompt_events`) et non sur la shortlist, qui se vide a
  mesure qu'on decoche.
- Un score annonce vide **retire la ligne** : `PASSE` est une reponse attendue, et
  l'enregistrer ferait compter au denominateur un match sans annonce.
- **L'issue juste avec la maniere fausse est le chiffre le plus interessant du bloc** : le
  bon vainqueur, le mauvais nombre de sets. Il dit qu'un raisonnement sur le rythme n'a pas
  porte, et il le dit meme quand la selection a gagne.
- Le second scenario est compte **a part** : deux scores proposes ne valent pas une lecture
  deux fois plus juste.

## Cote de reference contre cote obtenue (migration 030)

Sur les quatre blocs du 10/08, « A relever : Hand. jeux, Jeux O/U » apparaissait 4 fois sur
4. Ce n'est pas un accident de collecte : sur 127 matchs de tennis a venir, Betclic ne sert
que le vainqueur via The Odds API. **Toute selection de maniere au tennis etait donc
enregistree a un prix Pinnacle** quand le football l'etait a un prix Betclic — et le prompt
affirme que le palier « sert a calculer un taux de reussite par bande de cote dans le
temps ». Un 1.92 Pinnacle et un 1.92 Betclic ne decrivent pas le meme marche, et pres des
bornes (1.70, 2.30) l'ecart de marge fait basculer de palier.

- **Trois colonnes, aucun renommage.** `price` **est** deja la cote de reference — le prompt
  impose de la recopier du bloc au centime pres. S'y ajoutent `price_source`, `price_real`
  et `tier_real`. Renommer `price` et `tier` aurait touche six templates, quatre services et
  la moitie des tests pour un gain nul, et un `NOT NULL` aurait ete faux sur l'existant.
- **Le palier de la cote obtenue est fige a l'ecriture**, contrairement a la famille d'un
  marche qui se resout a la lecture. Les deux traitements sont justes : une famille est un
  classement corrigeable, un palier decrit une **decision datee** — le pari a ete pose a ce
  prix-la, ce jour-la, et un reglage de bande change plus tard ne doit pas le reclasser.
- **L'exclusion est ciblee, et c'est un arbitrage.** Sortent des taux par bande de cote les
  seules selections dont le prix vient d'un book de **reference** et dont la cote obtenue
  manque. Une cote du book principal est sa propre cote reelle ; exclure tout ce qui n'a pas
  de `price_real` aurait vide la page et le bloc de retour d'experience d'un coup, en
  quarantainant surtout du football. Le compte des exclues est affiche des deux cotes et
  ferme `_audit`.
- **Rien n'est retro-rempli** : deduire la source d'une selection ancienne demanderait de
  rapprocher un libelle ecrit a la main d'une ligne de `odds`, des mois apres le releve.
  `NULL` veut dire « on ne sait pas », et c'est la verite.
- La cote obtenue **ne se releve jamais toute seule** : ce serait une integration
  transactionnelle avec un bookmaker, interdit n°7. Elle se saisit sur la feuille de
  session ; la mention `(ref.)` que le prompt impose deja alimente `price_source` a
  l'import.
- **Piste ecartee, et ce n'est pas une question de cout** : le module d'extraction vision
  d'`edgelab` instancie un client `anthropic`. Le reutiliser ferait de cette application un
  appelant de l'API Anthropic — interdit n°6, sans exception possible.

## Les cibles de confiance : un ecart, et parfois rien du tout

**Une cible absolue rapprochee des paliers recouple la confiance et la cote.** Les bandes
de cote traduites en taux d'equilibre vont de 80 % pour SAFE a 12,5 % pour GIGA FUN : une
selection a 4.00 qui gagne 30 % du temps est un bon pari, et elle tire son cran quarante
points sous une bande a 70 %. Pour tenir cette bande, conf 5 devait devenir quasi
exclusivement du SAFE — et le mecanisme qui ordonne de resserrer un cran employe trop
largement poussait alors toute selection a cote haute vers le bas de l'echelle. C'etait la
derive vers le SAFE, reinstallee un etage plus haut et cette fois par un reglage.

- Les bornes sont donc des **ecarts en points au taux global** (migration 032). Ce qui se
  mesure est la **monotonie** de la notation — un cran superieur bat-il le cran
  inferieur — et non l'atteinte d'un chiffre qui depend du melange de paliers du mois.
  Mesure sur un rendu a gate ouvert : conf 3 a 38 % et conf 4 a 52 % pour un global de
  43 % etaient annonces a **-12 et -8 points** de leurs bandes absolues, donc tous deux
  « a resserrer », alors que la notation est parfaitement monotone. En relatif, les deux
  tombent **dans** leur bande et rien ne se declenche : c'est la reponse juste.
- **La reference est le taux global de la population affichee**, sur la meme fenetre que
  les taux compares. Si les deux divergeaient, on rapporterait un taux des soixante
  dernieres a une moyenne de tout l'historique. Elle est nommee une fois dans le bloc.
- **L'ecart est ce qui se regle, la valeur resolue ce a quoi un taux se compare.** L'ecran
  affiche les deux, les surfaces de lecture la seconde : donner un ecart a comparer a un
  taux ferait refaire l'addition a chaque ligne.
- **On est reparti des defauts plutot que de convertir l'existant.** Au taux global
  constate de 47,1 %, la bande de conf 3 devenait `+2,9 → +12,9` et celle de conf 5
  `+22,9 et plus` : la conversion reproduit la derive qu'on supprime, l'echelle de depart
  etant elle-meme calee sur une hypothese SAFE.

**Un cran peut n'avoir aucune cible**, et c'est un etat a part entiere (migration 031).
La partition n'est pas « les bords de l'echelle » mais **discretionnaire contre determine
par la source** : une bande sert a declencher un mouvement — resserrer un cran employe
trop largement, relacher un cran trop etroit — encore faut-il que le mouvement existe.

- Les crans **1 et 2** sont pines par ce que la recherche a trouve : `lecture` impose 1,
  une source de niveau 3-4 plafonne a 2. Descendre supposerait de nier un fait date,
  monter exige une meilleure source et non une meilleure notation. Aucune direction n'est
  un choix, donc aucune cible n'y mesure rien.
- Les crans **3, 4 et 5** se distinguent par des criteres appreciables — un facteur ou
  deux, un manque de la section A qui touche ou non le facteur. Le cran 5 **garde donc sa
  borne basse** : ce qui n'allait pas chez lui n'etait pas d'avoir une cible, mais que
  cette cible soit absolue.
- `low` et `high` tous deux NULL = pas de cible. **`high` seul reste refuse** : c'est une
  saisie incomplete, et le dernier cas d'erreur que ce validateur sache attraper.
- La page ecrit « pas de cible » plutot que de ne rien afficher — une case vide par
  decision ne se distingue pas d'un oubli — et **jamais un zero**, qui se lirait comme une
  cible de 0 %. Le paragraphe de resserrement ne s'ecrit pas si aucun cran n'est cible.

## Le gate de recul se regle et se mesure

Les deux nombres qui decident si le bloc se transmet vivaient dans le code, et l'ecran les
citait en dur. Ils entrent dans `thresholds` — c'est exactement ce qu'elle heberge — et
`reach()` les lit **une seule fois pour les deux surfaces**.

- **C'est le seul reglage dont l'effet est differe**, donc le seul dont on ne pouvait pas
  mesurer la distance a l'activation. L'ecran affiche l'avancement (`60 / 40 selections ·
  5 / 10 journees`) et ce qu'il manque.
- Corollaire de validation : **le gate etant ferme en production**, rien de ce qui touche
  aux bandes ne se voit dans un rendu reel. Tout s'y valide sur `helpers.lot_avec_recul`,
  qui ouvre les deux seuils a la fois — l'etalement est celui qu'une fixture naive oublie.

## L'effectif minimum : un seuil, deux presentations

Un mecanisme existait deja **en double** : le prompt retirait les regroupements sous huit
selections tranchees, la page les gardait et palissait leur barre. Le seuil est desormais
unique et reglable, avec le meme defaut — 8 est deja en service, et le remplacer par un
nombre rond couterait trois regroupements pour aucun gain de principe.

**Les presentations, elles, restent distinctes, et leurs lecteurs ne sont pas les memes :**

- la **page** affiche l'effectif a la place du taux (`3 selection(s), effectif
  insuffisant`). « 100 % » sur trois paris occupe la meme colonne qu'un taux calcule sur
  quarante, et un humain doit savoir qu'une case est vide parce qu'elle est maigre et non
  parce qu'elle est nulle ;
- le **prompt** se tait. Son bloc sert, selon son propre texte, a deux choses et a rien
  d'autre — dire ou chercher en premier, et ou relever l'exigence — et une ligne
  « effectif insuffisant » ne sert ni l'une ni l'autre.

Le seuil descend **dans chaque ligne** au lieu d'etre lu d'une constante par une
propriete : une classe qui irait chercher son propre reglage serait intestable hors d'une
base.

## Une cote hors de toute bande de palier

Comportement d'avant, mesure avant de corriger : **ni rejet, ni exception, ni palier nul
visible** — pire que les trois. `add_pick` acceptait 1.18 sans controle et la rangeait sous
le palier choisi au formulaire ; `set_real_price` ecrivait `tier_real` a NULL,
indiscernable de « jamais saisi », si bien que `tier_effective` retombait en silence sur le
palier provisoire. La selection sortait alors de la quarantaine des cotes de reference
comme si son prix avait ete releve, **et entrait dans un taux par bande de cote sans tomber
dans aucune bande**.

- Audit avant correction : **zero ligne concernee**, les cotes en base allant de 1.25 a
  3.50. Le defaut etait latent — rien a reparer, seulement a fermer.
- Le message nomme le palier le plus proche et la borne franchie. « Le plus proche » se
  mesure en **distance a la borne**, jamais en ordre de position : les bandes se reglent,
  rien n'empeche d'en laisser un trou au milieu, et nommer le premier de la liste enverrait
  corriger la mauvaise borne.
- **Une borne haute vide veut toujours dire « pas de limite »** : 999.00 reste accepte sur
  `GIGA+`, et un test de non-regression le garde.

## Les seuils reglables (`services/thresholds.py`)

Des nombres qui decident d'une regle sans etre ni une constante du projet ni une donnee :
« a partir de combien de matchs un lot porte-t-il deux combines ». Ce sont des decisions
de l'utilisateur, au meme titre que les bandes de cote, et les coder en dur obligerait a
redeployer pour changer d'avis.

- Ils vivent dans `preferences`, la table cle/valeur qui porte deja les consignes
  permanentes, sous le prefixe `seuil_` — sans lui, un seuil et une consigne de texte se
  disputeraient un nom.
- **Un registre les declare** — libelle, defaut, bornes, et *ce que le seuil decide*.
  L'ecran des reglages les rend sans les connaitre un par un : un seuil ajoute demain n'a
  pas a toucher au gabarit. La note n'est pas decorative : un nombre sans son effet ne se
  regle pas, il se subit.
- **Une valeur illisible ou hors bornes vaut le defaut**, jamais une erreur : refuser de
  servir une page parce qu'un nombre a ete mal saisi serait hors de proportion. Le retour
  au defaut est **ecrit en base** et pas seulement applique a la lecture, sans quoi le
  champ afficherait le defaut quand la table porte encore la saisie refusee.

## Le denominateur ne peut pas baisser, et il le prouve

**Une fenetre glissante s'est lue comme un total, et a fait croire a une perte de
donnees.** Le bloc du prompt annoncait « 60 selection(s) tranchee(s) **enregistree(s)** »
sur une base qui en portait cent — `feedback()` lit les `FEEDBACK_WINDOW` dernieres, pas
tout l'historique. Verifie : rien n'avait disparu, `analysis()` comptait bien 100 sur
5 journees et chaque axe retombait juste.

- Le bloc **nomme sa fenetre** et ecrit le total a cote (`scope_line`) des qu'elle mord.
  Deux nombres cote a cote rendent le plafond visible ; un seul se lit comme un total.
- **`missing_note` nomme celle des deux conditions qui manque.** Le texte annoncait les
  deux seuils quel que soit le bloquant : « il en faudrait au moins 40 » sur un bloc qui
  en comptait 60 est une phrase qui se contredit, et fait chercher une panne la ou il n'y
  a qu'un etalement trop court.
- Le taux de selection **dit qu'il compte autre chose** : des sessions ayant produit un
  prompt, resultat saisi ou non, quand les taux ne comptent que du tranche. Deux nombres
  differents a quatre paragraphes d'ecart se lisaient comme un seul qui se contredit.

**Le chapitre du prompt ne transmet plus la pedagogie de son propre silence.** Il
consommait vingt-cinq lignes pour expliquer *pourquoi* il manque du recul — les deux
conditions, ce qu'un lot concentre mesure vraiment, pourquoi deux nombres different —
puis concluait par « n'ecris rien sur ces taux ». Des tokens payes pour transmettre une
information qu'il interdit ensuite d'employer. Sous le seuil, il annonce donc le fait en
trois lignes et s'arrete. Le raisonnement, lui, vit ici :

- **les deux conditions comptent, et l'etalement n'est pas une formalite** : un lot
  nombreux mais concentre sur quelques jours mesure ces jours-la — un tournoi, une
  soiree de coupe, une meteo — et non une facon d'analyser. Ecrit sous un nom large
  comme « Masters 1000 » ou « Tennis », il ferait passer une semaine pour une tendance ;
- **les deux populations different**, et cette phrase-la reste dans le prompt — mais
  **seulement dans la branche « assez de recul »**, la seule ou les deux nombres
  coexistent. Sous le seuil il n'y a qu'un nombre, donc rien a confondre, et lever une
  ambiguite absente coute des tokens pour rien.

**Le controle d'integrite** (`Analysis.recorded`, `gaps`, `consistent`) rend la vraie
panne impossible a taire :

- `recorded` est un `COUNT(*)` **sans jointure ni filtre** sur `picks`. C'est le seul
  chiffre de la page qu'aucun regroupement ne peut faire baisser, donc le temoin de tous
  les autres — une jointure devenue stricte le laisserait intact et ferait bouger le reste.
- Chaque axe **declare ce qu'il laisse dehors** (`uncategorised`, `unlabelled_angle`,
  `unlabelled_source`, `unlabelled_confidence`, `unlabelled_market`,
  `unclassified_markets`), et `_audit` verifie que somme + declares retombe sur
  `recorded`. Un ecart s'affiche en clair, avec le nombre de lignes et la cause.
- `by_market` s'audite sur le comptage **complet** et non sur les lignes affichees : la
  carte ecarte volontairement les marches vus une seule fois, et les compter comme perdus
  ferait crier a la panne sur une regle.
- Toutes les jointures d'agregation sont des `LEFT JOIN` avec repli explicite. Un test de
  non-regression monte une selection tranchee **sans aucune dimension** — pas de match,
  donc ni sport ni competition, ni type, ni source, ni confiance — et verifie qu'elle
  compte au total et se retrouve dans le compte des non classes de chaque axe.
- Corollaire teste : reclasser une competition **deplace des lignes entre regroupements,
  jamais le total**.

## L'ordre du prompt : ce qui decide avant ce qui explique

**Trois cents lignes de mode d'emploi se lisaient avant la methode.** Le lecteur
apprenait ce que veut dire « Buts marq. », comment se lit un Elo et comment se pose un
handicap jeux **avant** de savoir ce qu'il devait produire : les consignes qui decident
de la sortie etaient noyees au milieu de celles qui expliquent.

- Le mode d'emploi des lignes de contexte est devenu un chapitre
  **`## COMMENT LIRE LES BLOCS`, place apres la section F**. Deplacer ne change pas un
  token — c'est l'ordre qui etait faux. Le plan rendu est desormais : role et interdits,
  methode, ce qu'il faut verifier, matchs, historique, sortie attendue, puis glossaire.
- **Le partage n'est pas « tout ce qui est long descend ».** Ce qui **decide de ce qu'on
  rend** reste en tete : les interdits, la cote du bloc qui fait autorite, « A relever »
  qui rend un marche selectionnable, les lignes en quart qui ne le sont pas. Ce qui
  **explique une ligne** descend. Un marche qu'on croit interdit faute d'avoir lu jusqu'au
  bout est exactement l'erreur que ce prompt a deja payee une fois.
- **La porte du chapitre est celle de son contenu — `sports`, et non
  `context_labels`.** Ses paragraphes de football et de tennis se gardent sur le sport du
  lot, pas sur les lignes recuperees : un lot de football sans contexte a bien un mode
  d'emploi a lire, celui de ses marches. Le renvoi place en tete porte **la meme porte** —
  un renvoi garde autrement que le chapitre qu'il annonce promet une section absente, ou
  tait une section presente.

## Une seconde selection sur le meme match (`picks.independence_note`)

Cent selections pour **97 evenements distincts** : trois matchs en portent deux. Le prompt
l'autorise et l'encadre depuis toujours — « deux selections sur un meme match ne se
justifient que si elles reposent sur des angles reellement independants, et tu le dis
alors explicitement » — mais rien de cette justification n'arrivait en base. Elle etait
ecrite dans le rendu, lue une fois, puis perdue.

- **Seul controle bloquant du module, et c'est delibere.** Ailleurs une valeur manquante
  vaut « non renseigne » ; ici elle vaudrait « je ne me suis pas pose la question », et le
  prompt nomme precisement ce cas — multiplier les lignes d'un meme match pour atteindre
  un quota est une facon deguisee de remplir un palier avec du vide.
- **Le controle porte sur la session, pas sur l'historique.** Deux analyses successives du
  meme match sont deux decisions distinctes ; faire porter la regle sur toute la base
  bloquerait un match rejoue la semaine suivante.
- **Une selection sans match y echappe** : rien ne permet de les rapprocher, et un pari
  sur un vainqueur de tournoi n'est pas « le meme match » qu'un autre.
- **La note est rendue sur la feuille de session**, et pas seulement stockee. Une donnee
  que rien ne lit finit par se retirer — c'est le sort exact de l'effectif collecte des
  mois sans lecteur (migration 022). C'est en la relisant qu'on voit si deux angles
  etaient vraiment independants ou deux facons de dire la meme chose ; `RateRow.clustered`
  sait deja qu'un regroupement porte moins d'evenements que de selections, elle seule dit
  si c'est grave.
- **Rien n'est retrobackfille** : les trois cas deja en base gardent une note vide. On ne
  peut pas inventer une justification apres coup, et l'exiger a la lecture rendrait
  l'historique invalide.
- **Le controle ne s'applique pas a `set_event()`**, qui repare un rattachement. Refuser
  une correction laisserait un pick attache au mauvais match, ce qui est pire ; la regle
  vise le moment ou l'on **cree** une seconde ligne, pas celui ou l'on corrige.
- A l'import, la ligne concernee reste **proposee mais decochee** tant que sa
  justification manque : `add_pick` la refuserait, et une ligne qui echoue se remarque
  moins qu'une case qu'on doit cocher.

## Les crans de confiance, et les paliers qu'on n'atteint pas

- **Les crans 2 et 4 n'avaient aucune definition**, et tout tombait donc en 3 : le prompt
  n'ancrait que 5, 3 et 1. Mesure sur cent selections — **99 % du volume sur deux crans**,
  et les crans 1 et 5 jamais employes. Une echelle dont deux crans sur cinq portent tout
  ne note plus rien. Les cinq crans sont desormais definis par ce que portent les
  sections A et B, donc **verifiables** au lieu d'etre laisses a l'appreciation.
  - **Une seule echelle de sources dans tout le prompt** : celle du preambule nourrit la
    colonne `Source` du tableau, qui nourrit le cran. `lecture` va avec 1, une source de
    niveau 3-4 plafonne a 2. Trois ecritures de la meme notion se seraient contredites.
  - **Les deux plafonds en doublon ont ete retires** — « un manque important ne depasse
    pas 2 » et « niveau 4 plafonne a 2 » : la table les porte tous les deux, et les
    laisser a cote aurait donne deux regles pour un meme cas sans dire laquelle gagne. Ce
    que la phrase sur les sources de niveau 4 disait **en propre** — ne jamais la
    presenter comme un fait — reste ecrit.
- **Les bandes de cote ne se chevauchent pas, et le paragraphe d'arbitrage a ete
  supprime.** Il faisait trancher la confiance « dans une zone commune a deux paliers »,
  sauf que les bandes ne se touchent qu'en un **point exact** (1.70, 2.30, 3.60, 8.00) :
  sur cent selections, aucune cote n'y est jamais tombee. Deux cents mots pour un cas qui
  ne se produit pas.
  - Et s'il s'etait produit, la regle aurait ete nuisible : le palier sert a calculer un
    taux par **bande de cote**, et l'y faire dependre de la confiance aurait mis deux
    selections au meme prix dans deux paliers differents. Le prompt le disait lui-meme
    deux phrases plus bas — « une classification variable rendrait ce taux
    ininterpretable » — et la confiance a deja son propre axe.
  - Reste une convention, ecrite une fois : **la borne haute appartient au palier
    suivant**. Une cote a 1.70 est FUN, pas SAFE.
- **Un palier vide se commente.** ULTRA FUN est a 0/7, GIGA FUN et GIGA+ n'ont jamais
  servi en cent selections, et le prompt les annonce pourtant a chaque session. On ne
  force pas leur remplissage — un quota rempli avec du vide est l'erreur que le prompt
  nomme lui-meme comme la plus couteuse — on rend la **vacance sortante** : un palier vide
  est un resultat, un palier vide non commente est un oubli.
- **La vacance se mesure en sessions, et sans rien parser.** L'application ne lit pas la
  prose du rendu, seulement le tableau des selections ; mais un palier qu'aucune selection
  de la session ne porte **est** un palier laisse vide ce jour-la. `MixRow.absent_sessions`
  le compte. Une part de volume a zero dit qu'un niveau ne sert jamais, ce compte-ci dit
  **a quel rythme** — et c'est cette difference qui decidera un jour de raccourcir
  l'echelle plutot que d'y pousser des selections. Mesure actuelle : GIGA FUN et GIGA+
  absents de 5 sessions sur 5, confiance 5 et 1 aussi, confiance 2 de 4 sur 5.

## Familles de marches (`services/market_families.py`)

Neuf regroupements de marches sur cent selections, **dont six vus une seule fois** :
chacun mesurait le hasard, et le bloc entier ne se lisait pas. Or `O/U` et `O/U 2.5`
sont le meme pari a une ligne pres, `Vainqueur` et `1N2` la meme chose sur deux sports,
`Handicap` et `Hand. jeux` aussi. Mesure apres groupement : **trois familles passent le
seuil** — Issue 24/49, Handicap 11/23, Total 10/21 — la ou aucun libelle ne l'atteignait.

- **Deux niveaux de cle, et il faut les deux.** La cle fine (`market_key`) distingue
  `O/U 2.5` de `O/U 3.5` et titre une ligne du detail ; la cle de famille (`family_key`)
  retire en plus la **valeur de ligne finale**, qui est un parametre du marche et non un
  autre marche. Sans elle, chaque seuil rencontre reclamerait sa propre correspondance et
  la liste « a classer » ne desemplirait jamais.
  - **Seuls les nombres de fin sont retires.** « Les 2 équipes marquent » garde son 2 :
    ce n'est pas une ligne, c'est une partie du nom. Retirer tout nombre ou qu'il soit
    aurait produit une cle que personne ne reconnait dans les reglages.
- **Rien n'est deduit d'un libelle**, meme regle que le niveau d'une competition. Le
  vocabulaire est pourtant connu — c'est celui de `render.MARKET_ORDER_BY_SPORT`, le
  prompt imposant de choisir un marche present dans le bloc — mais une saisie a la main
  reste libre. La migration 027 et `FAMILY_SEED` portent la meme table, et un test compare
  les deux ecritures.
- **`autre` est une decision, jamais un depotoir.** Une cle inconnue n'y tombe pas
  d'office : le regroupement se lirait comme un choix alors que ce serait un oubli, et le
  marche nouveau qu'on essaie serait le premier a disparaitre dans le fourre-tout. Il est
  reclame dans les reglages, avec son compte, et `unclassified_markets` ferme l'addition.
  - Y sont ranges **par decision** : corners et cartons, qui sont des totaux d'une autre
    grandeur — les melanger aux buts ferait decrire deux choses par un seul taux ; les
    props buteurs, qui sont des marches de joueur ; et `Cotes`, libelle libre de la saisie
    manuelle, qui peut recouvrir n'importe quoi.
  - **`cotes` n'est pas un artefact d'ingestion, et la question a ete tranchee.** La ligne
    intrigue parce qu'elle ressemble a un en-tete de bloc capture comme un marche.
    Verification faite : **aucune selection de la base ne porte ce libelle** — les seize
    libelles enregistres sont `Vainqueur`, `1N2`, `Handicap`, `O/U`… Il vient de
    `FAMILY_SEED` et de la migration 027, et le libelle lui-meme de
    `render.MARKET_ORDER`, ou `("outright", "Cotes")` nomme le marche libre de la saisie
    manuelle. L'ecran des reglages liste **toutes** les cles classees, y compris celles
    que rien n'a encore employees : il affiche « — » dans la colonne des selections, et
    c'est ce tiret qui distingue une entree seedee d'une entree vue en base. Rien a
    purger, rien a corriger cote parsing.
  - `equipe` est la seule famille rangee par **sujet** et non par forme : un total
    d'equipe est un total, mais « plus de 1.5 but pour Lyon » et « plus de 2.5 buts dans
    le match » ne se gagnent pas dans les memes scenarios.
- **Un seul comptage, deux vues.** La carte « Par marché » applique son seuil
  (`ANALYSIS_MIN_MARKET`), le deplie d'une famille non — c'est tout l'interet du
  groupement, un libelle vu deux fois disant quelque chose sous sa famille. Les deux
  sortent du meme `_rate_tally` : les recalculer separement les aurait fait diverger, et
  la somme du deplie n'aurait plus tombe juste sur sa ligne.
- **La famille se resout a la lecture**, jamais recopiee sur la selection : reclasser un
  marche reclasse tout l'historique, sans migration. Meme regle que le niveau d'une
  competition.
- **Regrouper ne fabrique pas d'effectif** : une famille sous le seuil est palie et
  comptee dans `thin_rows` comme les autres lignes.

## Sur quoi la selection reposait (`picks.angle`, `picks.source_level`)

Palier, confiance, marche, sport, niveau de competition : **toutes les dimensions
enregistrees jusqu'ici sont des etiquettes de forme.** Aucune ne dit sur quoi la
selection tenait. Le prompt reclamait pourtant les deux elements qui en feraient de
bonnes dimensions — la nature de l'angle en section B, le niveau de la source en
preambule — et les jetait une fois l'analyse rendue.

- `angle` vaut `issue` ou `maniere`. **C'est ce mot qui choisit le marche**, et l'ecrire
  dans le tableau permet de verifier apres coup qu'il l'a vraiment fait : un angle sur une
  maniere rendu en vainqueur se voit alors d'un coup d'oeil. Le comptage de la section C
  lit desormais la colonne au lieu de renvoyer a la section B.
- `source_level` vaut `1` a `4` sur l'echelle du preambule, ou `lecture`.
  - **`lecture` n'est pas une absence de valeur mais une valeur de l'echelle** :
    l'analyse declare qu'aucun fait date ne porte la selection. La distinguer de « non
    renseigne » est **tout l'objet de la mesure** — c'est precisement la comparaison qui
    pourrait changer la methode. D'ou une colonne **TEXTE** : un entier nullable aurait
    ecrase la premiere sur la seconde, silencieusement.
  - Le template la presente comme une reponse **normale et frequente**. Le contraire
    ferait promouvoir un bloc de contexte au rang de source citee, et detruirait la
    comparaison au moment meme ou on l'installe.
- **Les deux colonnes restent facultatives.** Les cent selections deja en base n'en
  portent aucune, et une valeur hors vocabulaire vaut « non renseigne » plutot qu'un
  refus : casser un import de vingt lignes pour un mot inattendu couterait plus que la
  ligne manquante. `unlabelled_angle` et `unlabelled_source` ferment l'addition, comme
  `uncategorised`.
- **Le mot de la section B ne se garde plus par la taille du lot.** Il tombait avec le
  comptage — « les deux morceaux n'en font qu'un » — et c'etait juste tant qu'il n'existait
  que pour etre relu au moment du comptage. Depuis qu'il est une **colonne**, il decrit une
  selection prise seule, donc il vaut des la premiere ; la proportion, elle, a toujours
  besoin de volume et reste gardee a quatre matchs.
- **`by_angle` et `by_source` entrent dans le detecteur de recouvrement**, et ils en ont
  besoin plus que les autres : un lot ou toutes les manieres se traduisent en totaux ferait
  de « Manière » et « O/U » deux noms du meme echantillon, presentes comme deux constats.
- L'ordre des lignes suit **l'echelle et non l'effectif** : « Lecture seule » ferme la
  marche parce que c'est sa place, pas parce qu'elle serait la plus nombreuse. C'est elle
  qu'on veut comparer au reste.
- La saisie passe par **deux menus fermes** et jamais par un champ libre : une faute de
  frappe ferait disparaitre la ligne de son regroupement sans un mot.

## Le lot d'une session, et ce qu'elle en a ecarte (`prompt_events`)

L'application enregistrait ce qui avait ete **selectionne**, jamais ce qui avait ete
**ecarte**. Le prompt annonce pourtant que passer est un resultat valable et attendu sur
une partie du lot : sans denominateur, cette phrase n'etait ni verifiable ni suivie.

- **`session_events` ne peut pas tenir ce role, et ce n'est pas un oubli** : c'est la
  shortlist **courante**, elle se vide a mesure qu'on decoche. Mesure sur les donnees
  reelles — la session du 09/08 porte 4 lignes de shortlist pour 29 selections sur 29
  matchs distincts, et son premier prompt en servait 12. La shortlist decrit ou en est le
  board, pas ce qui a ete analyse.
- Le lot est donc l'**union des matchs entres dans un prompt**, enregistree par
  `prompt_events` au moment de l'archivage. **Compter des matchs et non des prompts** est
  ce qui la rend juste : regenerer vingt fois le meme lot ne l'agrandit pas d'une ligne,
  il ne grossit que lorsqu'un match nouveau apparait — ce que le scan fait plusieurs fois
  par jour.
  - Un maximum par prompt ne suffirait pas : un prompt restreint a une competition n'en
    verrait que le plus gros morceau, et l'union reconstitue le lot entier. Mesure : sur
    la session du 09/08, seize prompts de 3 a 12 blocs, pour 57 matchs distincts.
  - Limite assumee : un prompt genere puis jamais colle dans Claude compte quand meme ses
    matchs comme passes. Aucune donnee ne permet de le savoir, et un bouton « celui-ci
    part » ferait dependre toute la mesure d'un geste qu'on oublie.
- **Les sessions anterieures a cette table reconstruisent leur lot a la lecture**, depuis
  les corps de prompts archives (`_BLOCK_HEADER`). L'information dormait deja en base :
  les corps sont stockes depuis toujours, personne ne les lisait. Elles sont **marquees
  « reconstruit »** — un match n'y figure que par son libelle, donc deux rencontres
  homonymes le meme jour n'en feraient qu'une. Ce chemin s'eteint de lui-meme, la requete
  ne rendant plus aucune ligne des que chaque session a son enregistrement.
- **Le taux de selection se compte en matchs, jamais en lignes** : deux selections sur la
  meme rencontre sont un match retenu, pas deux. Sans cette regle il depasserait cent
  pour cent des qu'un match porte un vainqueur et un total.
  - Une selection rattachee a un match **hors du lot** — le voisinage de
    `pickable_events` en offre — est comptee a part (`outside`) et jamais rabotee : elle
    signale soit un rattachement au voisinage, soit un lot sous-enregistre.
  - Une session **sans aucune selection garde sa ligne** (0 sur 34 dans l'historique
    reel) : c'est la seule ou le tri a vraiment trie, et la retirer la ferait disparaitre.
  - Une session **sans prompt n'a pas de lot du tout**, et surtout pas un lot de zero :
    rien n'a ete soumis a l'analyse, et un taux de selection y serait invente.
- **Le bloc du prompt echappe aux trois garde-fous de `feedback()`**, et c'est delibere :
  eux protegent des **taux de reussite**, qui mesurent des issues. Une part de lot decrit
  un **comportement** et ne devient pas trompeuse parce que les resultats manquent — meme
  exemption que `labelling()` sur la page. Seul `FEEDBACK_MIN_SESSIONS` (3) le garde : en
  dessous, « mediane » ne decrit pas autre chose que l'echantillon entier.
  - **Mediane et non moyenne** : une session ou l'on n'a rien retenu tirerait la moyenne
    vers une prudence qui n'existe pas le reste du temps.
  - Le template le presente comme un **constat, jamais un quota**. Se fixer une part de
    passes ferait ecarter un match pour remplir un compte — l'erreur que le prompt nomme
    ailleurs comme la plus couteuse. Un test verifie que la phrase y est.

## Ce qui nourrit le prompt en dehors des cotes

Trois sources locales, relues a chaque generation : aucun appel reseau, aucun credit,
donc rien qui puisse manquer un matin.

- `history.feedback()` ferme la boucle du parcours : le prompt part, les picks reviennent,
  leurs resultats sont saisis, et la session suivante sait enfin ce qui a tenu. Taux par
  palier, par confiance annoncee, par sport, **par competition** et par marche, sur les
  `FEEDBACK_WINDOW` dernieres selections **tranchees** — annulees et en attente hors
  denominateur.
  - **Toutes les selections comptent, jouees ou non** (`played_only=False` par defaut) : ce
    bloc juge l'analyse, pas la discipline de mise. Une selection ecartee dont on connait le
    resultat dit tout autant si l'angle etait bon. C'est `stats()` qui mesure les paris poses.
  - Le bloc **oriente autant qu'il freine** : il dit ou chercher en premier et ou relever
    l'exigence. Le template precise que c'est un **ordre de passage, pas un argument** — une
    selection se prend parce qu'un angle sportif la porte, jamais parce que la categorie a
    bien marche — et qu'il ne remplace pas un angle manquant : sans angle, la reponse reste
    PASSE.
  - Sous `FEEDBACK_MIN_TOTAL` (**40**), **aucun detail n'est publie** : le prompt dit qu'il
    manque du recul. Un 2/3 lu « 67 % » ferait plus de degats que le silence.
  - Sous `FEEDBACK_MIN_ROWS` (**8**), un regroupement est tu pour la meme raison.
  - Ces deux seuils ont ete releves de 10 et 4 apres observation : a 17 selections
    tranchees, le bloc publiait « ATP 2/6 contre WTA 5/7 » — treize matchs d'un seul
    tournoi, joues la meme nuit. Ce n'est pas « je lis mieux la WTA », c'est « une soiree
    s'est mal passee ». Presente comme un ordre de passage, **un chiffre faux oriente plus
    surement que pas de chiffre du tout** : c'est pourquoi le seuil se regle haut, et
    `by_competition` est le regroupement qui en souffre le plus.
  - **`FEEDBACK_MIN_DAYS` (10) garde l'etalement, la ou les deux autres gardent le
    volume**, et il faut les deux. A 63 selections tranchees le bloc publiait tout son
    detail — sauf que la fenetre entiere tenait **du 5 au 8 aout** : un seul tournoi de
    tennis en deux tableaux, une seule soiree de coupes d'Europe. « Masters 1000 13/30 »
    et « Tennis 13/30 » y etaient les **memes matchs sous deux noms**, presentes comme
    deux observations independantes.
    - **Une concentration ne se mesure pas par competition.** Les deux tableaux du Canadian
      Open sont deux competitions distinctes en base, et un compte de competitions aurait
      declare l'echantillon varie. C'est le calendrier qui la dit.
    - C'est la journee d'**analyse** (`picks.created_at`) et non celle du match : deux
      paris pris dans la meme seance restent une seule decision, meme a cheval sur minuit.
  - **Le garde-fou compte autant que le chiffre.** Le template interdit explicitement de
    rapprocher un taux d'une cote : ce serait calculer une esperance, et le fait que le
    chiffre vienne de l'historique de l'utilisateur n'y change rien (section 9). Un test
    verifie que le bloc porte cette interdiction, un autre qu'aucun champ financier
    n'apparait sur `Feedback` ni `FeedbackRow`.
  - Le signal le plus utile est l'ecart entre la confiance annoncee et le taux constate :
    il dit que la notation derive. C'est pour lui que `by_confidence` existe.
  - **La bande cible descend dans le prompt, et sans elle l'ecart ne se mesurait contre
    rien.** « Confiance 4 » n'est pas un pourcentage : le bloc affirmait qu'un ecart
    disait la derive de la notation, alors qu'aucun referentiel n'y figurait. La ligne
    porte donc `cible 50 – 60 %, écart -10 pts`, et c'est **le seul chiffre du bloc qui
    parle de la notation plutot que des matchs** — donc le seul sur lequel la section C
    puisse agir tout de suite.
    - **L'ecart s'ecrit toujours, la mention `hors bande` seulement quand l'intervalle
      le confirme.** Meme regle que la page, et elle compte plus encore ici : au volume
      courant presque chaque intervalle couvre plusieurs bandes, et faire resserrer une
      notation sur du bruit orienterait plus surement qu'aucun chiffre. Le template dit
      lequel des deux declenche l'action.
    - Rien quand le taux tombe **dans** la bande : « écart 0 pt » ferait chercher un
      probleme absent.
    - La bande ne se rattache qu'a `by_confidence`, comme sur la page : un sport ou un
      marche ne se fixe pas d'objectif de taux, une confiance annoncee si — c'est meme
      sa definition.
    - Le seuil de ligne (`FEEDBACK_MIN_ROWS`) garde la bande comme il gardait le taux :
      une cible affichee a cote d'un 3/4 ferait resserrer une notation sur quatre paris.
    - **L'ecran des reglages a ete relu avec.** Il decrivait le comportement de la seule
      page ; ces bandes decident maintenant aussi de ce que l'analyse lit, et le taire
      aurait laisse une explication perimee a l'endroit ou l'on vient de gagner en
      justesse. Meme regle que le preambule et ses portes.
  - **L'ecart au taux implicite, lui, ne remonte jamais.** Il est calcule a partir des
    cotes : l'injecter reviendrait a rapprocher un taux de reussite d'un prix, c'est a
    dire a calculer une esperance. `FeedbackRow` ne porte donc **aucun champ de prix**,
    et deux tests le verifient — l'un sur la classe, l'autre sur le corps du prompt.
- `competitions.notes` : la fiche d'une competition (format, phase, enjeu, particularites).
  Rendue **une seule fois par lot**, pas par match : repeter le format d'une coupe a chaque
  affiche couterait des tokens sans rien apprendre.
- `preferences` (table cle/valeur, cle `session_notes`) : les consignes permanentes de
  l'utilisateur, recopiees en tete de prompt. Elles priment sur les preferences generales
  du template, **jamais sur les interdits** — le template le dit noir sur blanc. Seule leur
  longueur est bornee : ce texte n'est ni compile ni interprete.

## Ce que l'application fait toute seule (`scheduler.py`)

Une regle separe le planifie du manuel : **rien de ce qui coute des credits The Odds API
ne part sans decision humaine.** Le scan quotidien est la seule exception, et il precede
la regle — c'est lui qui remplit le board du matin, sans quoi il n'y aurait rien a cocher.

- `SCAN_JOB_ID` — le scan, a `SCAN_AT`.
- `FREE_JOB_ID` — Elo tennis, historique tennis, synchronisation des competitions, groupes
  `FREE_JOB_DELAY_MIN` apres le scan. Tous gratuits. Ils ne se declenchaient qu'a
  l'enrichissement, ce qui laissait une installation sans session avec des classements
  figes. Chaque source est isolee : celle qui echoue ne prive pas les autres.
- `LINEUPS_JOB_ID` — les compositions, toutes les `LINEUPS_EVERY_MIN` (10). Elles sortent
  environ une heure avant le coup d'envoi **sans horaire fixe** : une passe quotidienne les
  manquerait toutes. Un passage manque ne se rattrape pas (`misfire_grace_time` court) —
  la fenetre a bouge, et rejouer un balayage en retard appellerait pour des matchs deja
  commences.

**L'etage B n'est pas planifie, et ce n'est pas un oubli.** Il depense de vrais credits,
une shortlist de trente matchs en vaut quelques centaines, et `ODDS_API_CREDIT_FLOOR`
protege le fond du quota mais pas le gaspillage. Un test le verifie, pour qu'il ne soit
pas ajoute par megarde.

`context.refresh_due_lineups()` est **cible** : un appel par match, jamais un contexte
complet. Tout ce dont il a besoin est deja en base — `apifootball_fixture_id` sur
l'evenement, couverture memorisee dans `KIND_TEAMS`. Trois filtres avant l'appel : la
shortlist (un match jamais coche n'ira dans aucun prompt), la couverture, et la
composition deja connue — elle ne change plus une fois publiee, et sans ce dernier filtre
chaque match serait redemande toutes les dix minutes jusqu'a son coup d'envoi.
`_lineup_payload()` est ecrit une seule fois : `fetch_context` et le balayage le
partagent, sans quoi le banc serait collecte d'un cote et oublie de l'autre.

## Deploiement et sauvegardes

- Les fichiers de deploiement sont dans `deploy/` : unite systemd durcie, unite et minuteur
  de sauvegarde, exemple nginx. Ils sont valides par `systemd-analyze verify` et `nginx -t`.
  Ils decrivent le **deploiement reel**, pas un ideal : chemins dans le home de `ubuntu`,
  uvicorn sur `127.0.0.1:8021`, nginx sur 443. Un fichier de deploiement qui ne correspond
  pas au deploiement est pire qu'absent — on le recopie en croyant reparer.
- `ProtectHome=read-only` et non `true` : l'application est installee dans le home, qui doit
  rester lisible et executable — le binaire `uv` y vit aussi. Les `ReadWritePaths` percent
  ce verrou pour `data/`, `templates/prompts/` et `.venv/`, et pour eux seuls.
- `backup.py` utilise **`VACUUM INTO`**, jamais une copie de fichier : en mode WAL, copier
  le `.db` seul livrerait une base incomplete.
- La rotation ne supprime **jamais la sauvegarde la plus recente**, meme expiree.
- L'application ecoute uniquement sur `127.0.0.1`. Elle n'a aucune authentification par
  choix : c'est nginx qui la protege. Toute modification du deploiement doit conserver ces
  deux proprietes. Le mot de passe `auth_basic` n'est pas une precaution de principe : un
  `/scan` depense de vrais credits, et la base porte tout l'historique de paris.
- Le certificat couvre le **nom d'hote du VPS**, qui resout deja publiquement — aucun domaine
  n'a ete achete. En mode `--webroot`, certbot ne recharge pas nginx : le crochet
  `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` s'en charge. Sans lui le
  renouvellement reussit et le certificat expire quand meme, ce qui ne se voit que le jour J.
- L'emplacement `/.well-known/acme-challenge/` du bloc port 80 doit rester **avant** la
  redirection vers HTTPS, sinon le renouvellement echoue.

## Front

HTMX est **vendorise** dans `static/htmx.min.js` — aucun CDN, aucun appel reseau depuis la
page. **Inter l'est aussi** (`static/fonts/InterVariable.woff2`, 344 Ko, licence SIL OFL
copiee a cote). Un seul fichier variable couvre les graisses de 100 a 900 ; l'italique
n'est pas vendorise, 378 Ko de plus pour un usage que l'application n'a pas. Un test
verifie qu'aucune URL externe n'entre dans la feuille de style.

`SPEC.md` section 9.4 interdit **framework JS front, bundler et TypeScript**. L'esthetique
se travaille donc en CSS vanilla, et ce n'est pas une limitation subie : une police
vendorisee, une echelle typographique et un jeu d'icones font l'essentiel du chemin sans
un octet de `node_modules`.

- **Echelle typographique** : six tailles (`--text-xs` a `--text-2xl`), pas une de plus.
  Les titres de section vivaient a `0.95`, `1` et `1.05rem` — trois valeurs pour un seul
  niveau, donc aucune hierarchie lisible. Le crenage suit la taille (`--track-tight` /
  `--track-snug`) : Inter se resserre en grandissant, et une valeur unique sur trois
  niveaux resserre trop le petit et pas assez le grand.
- **Tous les pictogrammes sont des SVG**, plus des emoji : les sports
  (`labels.SPORT_ICONS`) comme les lignes du bloc CONTEXTE (`labels.CONTEXT_ICONS`, dont
  la valeur est l'identifiant d'un `<symbol>` du sprite de `base.html`, sans son prefixe
  `i-`). Un emoji est rendu par la police de l'appareil : different d'une machine a
  l'autre, **absent de certaines** — la colonne se vidait alors sans rien dire — et il ne
  prend jamais la couleur de son entourage, ce qui l'aurait rendu criard sur le theme
  clair. Le sprite est instancie par `<use>` autant de fois qu'il y a de lignes sans etre
  redecrit.
  - **Un test verifie que chaque identifiant vise un symbole existant.** Une faute de
    frappe ne casse rien : le `<use>` pointe dans le vide et la carte perd son pictogramme
    sans un mot. Constate en reel, sur une feuille servie perimee.
  - Les emoji de **palier** (`🟢 SAFE`…) restent, et c'est le seul cas : ils sont stockes
    en base et `picks_import` doit pouvoir les reconnaitre dans un tableau colle a la main.

### Theme clair (`prefers-color-scheme`)

Il suit le reglage du systeme plutot qu'un bouton : sans JS ni stockage, et surtout parce
qu'un utilisateur qui a regle son systeme en clair a deja repondu a la question.

**Le bloc ne redeclare que des tokens, jamais un selecteur de composant**, et un test le
verifie. C'est la preuve que le systeme tient : le jour ou ce test casse, il faut sortir
dans `:root` la couleur ecrite en dur qui l'a fait casser — pas ajouter la regle au bloc.
Sortir les couleurs en dur a d'ailleurs revele une declaration invalide : `.round` prenait
`--edge`, une valeur d'**ombre**, comme couleur de bordure — donc pas de bordure du tout,
invisible sur fond sombre et franchement plat sur fond clair.

Trois inversions qui ne vont pas de soi :

- `--edge` est un lisere **clair** sur fond sombre ; sur fond clair il disparaitrait, et
  c'est une ombre sombre qui prend le relief a sa place ;
- `--surface` eclaircissait le haut d'une surface sombre ; sur du blanc il n'y a plus rien
  a eclaircir, c'est le bas qui s'assombrit ;
- les teintes de sport doivent **foncer**, pas s'eclaircir : un vert pastel lisible sur du
  noir devient illisible sur du blanc. Le token porte donc la couleur **du texte**, et le
  fond comme la bordure s'en deduisent par `color-mix`.

**Piege de verification** : `--force-prefers-color-scheme` n'existe pas dans le Chromium
utilise pour les captures, et le headless rend en **clair** par defaut. Deux captures
« light » et « dark » peuvent donc etre identiques sans que rien ne le signale — verifie
par `md5sum`. Pour voir le theme sombre, neutraliser temporairement la media query.
- **Les cartes de statistiques coulent en colonnes** (`column-count`), pas en grille. En
  grille, la plus courte de deux cartes laisse un vide sous elle jusqu'a la rangee
  suivante : trois trous sur quatre rangees. `break-inside: avoid` empeche qu'une carte
  soit coupee au passage d'une colonne. Les fragments HTMX sont des templates `_*.html` autonomes, inclus par les pages
completes : `_board.html` porte l'id `#board`, `_banner.html` porte l'id `#banner`,
`_worksheet.html` porte l'id `#worksheet` (selections + coupons d'une session, qui
changent ensemble : le resultat d'une jambe modifie celui de son coupon).

**Une route ciblee par HTMX rend le fragment, jamais la page.** Rendre `picks.html` dans
un `hx-swap="outerHTML"` imbriquerait un `<html>` complet dans le `<div>` remplace. Les
routes qui recoivent un fichier font exception : le televersement passe par un POST
classique, qui rend la page entiere. Des tests verifient la forme de la reponse.

Ce qui ne sert qu'une fois par session — coller un tableau, ajouter une ligne a la main —
vit dans un `<details class="panel">` replie. La page s'ouvre sur le travail du jour.

`static/app.js` est le **seul** JavaScript maison : du vanilla, quelques lignes, aucun
framework ni build step. Il ne porte que ce qu'un aller-retour serveur rendrait absurde —
aujourd'hui la case « tout cocher ». Tout le reste passe par HTMX.

Les tokens de style vivent dans `:root`. Changer l'apparence se fait la, jamais page par
page : `--edge` (liseré clair en haut d'une surface) porte le relief, `--surface` (degrade
de deux pour cent) lui donne son epaisseur, et la lueur de `body::before` donne une
direction a la lumiere. Les trois vont dans le meme sens — lumiere en haut — et s'annulent
des qu'on les contrarie.

- `--surface` est applique **en un seul endroit**, par une liste de selecteurs groupee en
  fin de feuille. Sept declarations dispersees auraient diverge au premier ajout. Les
  surfaces qui portent deja leur propre degrade (`.tile`, `button.primary`) n'y figurent
  pas — le leur est plus marque, et c'est voulu ; `pre#prompt-body` non plus, un bloc a
  chasse fixe se lisant mieux sur un fond uni.
- **Le bandeau est fait de pastilles, pas d'une phrase.** Separees par des blancs,
  « credits 19540 plancher 500 appels contexte 7388 » obligeait l'oeil a redecouper les
  paires a chaque passage. `.banner-item.actions` fait sortir les boutons de ce traitement :
  ils portent deja leur fond.
- `table.board` porte `font-variant-numeric: tabular-nums` **en entier**, pas seulement sur
  `.num` : l'heure et les comptes y echappaient, et « 07/08 13:35 » ne s'alignait pas avec
  « 07/08 17:00 ». Sur soixante-seize lignes, ce tremblement se voit.
- `th[colspan]` se centre. Aligne a gauche, « 1N2 » se posait au-dessus de sa premiere
  colonne : rien ne disait qu'il en coiffait trois.
- La densite du board est un choix mesure : a `0.45rem` de rembourrage vertical il tient
  seize lignes a l'ecran contre treize, et l'usage est de les balayer.
- **La figure de tete des statistiques se couche quand elle est seule** (≥ 900 px). La
  borner a 26 rem restait juste — un chiffre perdu au milieu de 1400 px se lit moins bien —
  mais ouvrait un vide sur toute la droite. En grille, le chiffre garde son ancrage a
  gauche et sa taille, la barre et son detail passent a cote.
- `prefers-reduced-motion` coupe toutes les transitions : le mouvement est un ornement.

**Piege verifie, a ne pas reintroduire** : `.filters label` empile ses enfants en colonne
(c'est ce qui met le libelle au-dessus de son champ). Toute pastille placee dans la barre
de filtres doit donc reposer son `flex-direction: row` — sans quoi le pictogramme du
selecteur de sport passait au-dessus du nom, et « Tous », qui n'en a pas, paraissait
desaligne.

**Piege verifie, a ne pas reintroduire** : jamais d'`overflow` sur `table.board`. Il en fait
un conteneur de defilement, l'en-tete `position: sticky` s'y ancre au lieu de la fenetre,
glisse vers le bas et **recouvre la premiere ligne** — une session entiere disparaissait de
l'historique sans qu'aucune requete soit en cause. Les coins arrondis sont deja obtenus par
les regles de rayon cellule par cellule. Et `--topbar-h` doit rester la hauteur reelle de la
barre : elle est verrouillee par un `min-height` sur `.topbar`, sinon les deux valeurs
divergent et l'en-tete colle trop haut ou trop bas.

## Les deux mesures, a ne jamais confondre

- `history.stats()` et `coupons.rates()` : **ce que valent mes paris**. Filtrent sur
  `played`, donc sur ce qui a ete pose chez le bookmaker.
- `history.analysis()` : **ce que vaut l'analyse**. Aucun filtre sur `played` — une
  selection ecartee dont le resultat est connu compte autant qu'une jouee.
  - `played` et `skipped` s'y lisent **ensemble** : si l'ecarte gagne aussi souvent que le
    joue, le tri n'apporte rien. C'est la seule mesure de ce que vaut le geste de trier,
    et elle ne coute qu'un resultat saisi sur une ligne qu'on n'a pas jouee.
  - `hidden_markets` annonce les marches ecartes faute d'echantillon. Un plafond silencieux
    se lirait « tout est couvert » alors que non.
  - `Analysis.overall` est **deduit** de `played` et `skipped`, jamais compte a part :
    deux comptages du meme ensemble finiraient par diverger.

Les deux vivent sur `/stats`, jamais melangees, et aucune ne produit d'indicateur financier.

### Les seuils de lecture sont communs aux deux surfaces, la reaction ne l'est pas

`feedback()` avait trois garde-fous — volume, ligne, etalement — et `analysis()`
aucun : la page publiait donc exactement ce que le prompt refuse. Constate sur les
donnees reelles a 71 selections tranchees : « Tennis 46 % » et « Masters 1000 46 % »
etaient **les memes 35 lignes** — un seul tournoi en deux tableaux — affichees comme
deux observations independantes, et toute la mesure tenait sur **quatre jours**
(5 au 8 aout), le football n'etant qu'une soiree de coupes d'Europe plus des miettes.

- **Sous quel compte un taux ne veut plus rien dire est une propriete des donnees,
  pas de la surface qui les affiche.** Les seuils sont donc ecrits **une seule
  fois** (`ANALYSIS_MIN_* = FEEDBACK_MIN_*`, un test le verifie) : les copier des
  deux cotes les aurait fait diverger, exactement comme la liste de marches de
  `markets.py`.
- **Ce qui differe, c'est la reaction, et les deux sont justes.** Le prompt **se
  tait** : Claude n'a aucun moyen de savoir qu'il lit une semaine de paris, et un
  chiffre faux oriente plus surement que pas de chiffre. La page **le dit** : c'est
  la surface ou l'utilisateur vient regarder ses propres donnees, les lui cacher
  repondrait a cote de la question posee. Blanchir `/stats` aurait ete la
  traduction litterale du garde-fou et le mauvais choix.
- L'etalement (`Analysis.days`) se compte sur les seules selections **tranchees**,
  comme le total : le compter sur toutes crediterait d'un etalement que le taux
  affiche n'a pas. Et c'est la journee de la **decision** (`picks.created_at`), pas
  celle du match — meme regle que `feedback()`.
- Une ligne sous `ANALYSIS_MIN_ROWS` est **palie et annoncee, jamais retiree**.
  La retirer aurait supprime `ULTRA FUN 0/6`, la ligne la plus actionnable de la
  page — celle qui dit que le garde-fou « fait nomme et date » a arrete la
  production de ce palier. Le seuil visuel valait 4 en dur dans le template : il
  y en a maintenant un seul, et c'est celui du prompt.
- `ANALYSIS_MIN_MARKET` (2) reste le **seul** cas ou la page ecarte vraiment une
  ligne, et ne contredit pas la regle : un libelle vu une fois n'est pas un taux
  fragile, c'est du bruit d'orthographe. Le compte des ecartes est annonce.
- Le detail chiffre sous le graphique reste **complet en toutes circonstances** :
  c'est ce qui rend l'ecartement du graphique acceptable. Le graphique interprete,
  le tableau compte.

### Les graphiques (`templates/_charts.html`)

Du HTML et du CSS, aucun SVG a calculer, aucune bibliotheque : une barre est une boite
dont la largeur vaut le taux. Un taux est une part de 100 %, donc l'echelle est fixe —
rien a normaliser, aucun maximum a chercher.

- **Une seule teinte porte les donnees.** Les taux mesurent tous la meme chose : les
  distinguer par la couleur inventerait des categories la ou il n'y a qu'une grandeur.
  Le degrade du remplissage est **vertical** ; horizontal, il ferait varier la couleur
  avec la longueur et encoderait deux fois la meme valeur.
- **Aucun seuil n'est trace sur la piste.** Les graduations (25 / 50 / 75 %) sont une
  echelle. Marquer une « rentabilite » serait rapprocher un taux d'une cote, c'est a dire
  calculer une esperance — interdit n°1. Un test verifie que la page porte cette
  interdiction en toutes lettres.
- **Le compte accompagne toujours le taux** : « 100 % » sur un pari et « 100 % » sur
  quarante ne disent pas la meme chose. Il reste affiche jusque sur petit ecran — c'est le
  libelle qui cede la place. Sous quatre paris tranches la barre est visiblement moins
  affirmee (`.fill.is-thin`), affichee mais pas assenee.
- Deux cartes par rangee au plus : sur trois colonnes, il ne reste a la barre que quelques
  pixels et le graphique n'ajoute plus rien au pourcentage ecrit a cote.
- Le tableau chiffre complet (annules, en attente) reste accessible sous chaque bloc, dans
  un `<details>`. Un graphique ne remplace pas les nombres, il les ordonne.
