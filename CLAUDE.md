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
- **Matchs commences** : `session.has_started()` porte la regle. Un evenement dont l'heure est
  passee sort du prompt, de l'enrichissement et du compteur de selection — il quitte deja le
  board — mais **reste attache a la session** : l'historique des picks s'appuie dessus. Il est
  affiche marque « commence », jamais retire tout seul.
- **Secrets** : uniquement via l'environnement / `.env`. Jamais dans le code, les logs, les
  reponses HTTP ni les fixtures de test.
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
football, 16 sur les competitions de `PLAYER_PROPS_LEAGUES` (props buteurs), 8 en tennis.
Le cout est estime **avant** l'appel et compare a `ODDS_API_CREDIT_FLOOR` : sous le plancher,
aucun appel n'est emis.

## Le rendu compact (`services/render.py`)

C'est le composant le plus important : la qualite de l'analyse depend de sa densite. Regles
non negociables, toutes couvertes par des tests :

- une ligne sans donnee est **omise**, jamais vide ni « N/A » ;
- une donnee volontairement absente devient une ligne explicite
  (« donnees non disponibles pour cette competition ») ;
- cotes a deux decimales ; scores exacts limites aux 10 cotes les plus basses, triees
  croissant ; lignes O/U limitees aux 5 plus proches de la ligne principale ;
- libelle sur 12 caracteres, indentation de 2, continuations alignees a 14 ;
- un marche paye mais non modelise est rendu brut plutot que perdu silencieusement ;
- l'en-tete ne nomme que le book principal ; **toute ligne servie par une autre source la
  porte en fin de ligne** (`[Pinnacle (ref.)]`, `[saisie manuelle]`, `[dont …]` quand une
  ligne fusionnee melange les deux). Un en-tete « Betclic + Pinnacle (ref.) » laissait
  deviner quelle cote etait jouable et laquelle ne faisait que situer le marche ;
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
regression.

## Contexte sportif et mapping

- `providers/apifootball.py` : piege du fournisseur, **les erreurs applicatives arrivent en
  HTTP 200** dans le champ `errors` de l'enveloppe. Le client les convertit en `ProviderError`.
  Corollaire non traite a ce jour : une erreur de **debit** (`rateLimit`) arrive par le meme
  chemin, donc en HTTP 200, et `RETRY_STATUSES` ne la voit pas. Le backoff ne se declenche
  pas sur une erreur pourtant transitoire.
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
  - **Des fractions, jamais des pourcentages.** Une frequence observee decrit le passe,
    ce qui reste permis ; ecrite « 56 % », elle invite a la diviser par une cote, et c'est
    le calcul d'esperance de la section 9. `9/16` porte la meme information et le meme
    compte que les moyennes du profil. Le template porte l'interdit, un test le verifie.
  - `Buts marq.` porte les buts **de l'equipe seule**, jamais le total du match : deux
    distributions d'equipes ne s'additionnent pas en un O/U de rencontre.
  - Sous `SEASON_MIN_MATCHES` (5) matchs joues, **aucune ligne**. Le fournisseur repond des
    zeros partout pour une equipe qui n'a rien joue dans la competition — le cas de toute
    equipe entrant en qualification europeenne, ou « >0.5 0/0 » ne decrirait personne.
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
- **Delocalisation** (`_relocated`) : un match hors du stade de l'equipe qui recoit change la
  lecture, et rien ne le laissait deviner. Le `venue` d'un match n'a pas d'identifiant
  exploitable : restent son nom et sa ville, et **il faut que les deux different**.
  `Veritas Stadion / Turku` contre `Veritas Stadion / Åbo` est le meme stade sous deux noms
  de ville ; `Teddy Stadium / Ploiesti` contre `Teddi Malcha Stadium / Jerusalem` est une
  vraie delocalisation sous un nom de stade proche. La ville seule inventait deux
  delocalisations sur dix, le nom seul en laissait passer. En cas de doute, aucune ligne.
- La **surface** vient du meme appel `/teams` : une pelouse naturelle ne produit rien, c'est
  le cas ordinaire. Un synthetique change le rythme et se disait jusqu'ici nulle part.
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
- **Entraineur** (`/coachs`, orthographe du fournisseur, la corriger donne un 404) : le
  fournisseur peut rendre **plusieurs** entraineurs pour une equipe, le predecesseur y
  figurant avec sa date de fin. Le poste en cours est celui dont l'etape de carriere
  **dans cette equipe** n'est pas refermee ; prendre le premier de la liste nommerait un
  entraineur parti, affirme comme un fait. L'anciennete se compte dans l'equipe du match
  et jamais depuis le premier poste de la carriere, et une prise de fonction posterieure
  au match ne rend aucune duree — un nombre negatif presente comme une anciennete.

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

## Repos et charge au tennis (`services/tennis_load.py`)

Le football recoit onze types de lignes d'API-Football ; le tennis n'avait que l'Elo, et
l'analyse allait chercher a la main, match par match, qui avait joue la veille. Or
l'information dort **deja dans la base** : les tours precedents du meme tournoi ont ete
scannes les jours d'avant. Aucun appel, aucune cle, aucun quota.

- Deux grandeurs seulement : **jours de repos** et **nombre de tours disputes**. Le compte
  accompagne le repos — deux jours apres un premier tour et deux jours apres un quart ne se
  valent pas.
- Ce qu'on ne tire **pas** et qu'il ne faut pas inventer : duree des matchs, score, maniere.
  La base ne stocke aucun resultat. Un joueur present au tour suivant a forcement passe le
  precedent, mais l'ecrire supposerait qu'aucun forfait n'existe.
- Rapprochement des noms par `labels.sort_key` — casse et accents ignores, **rien de flou** :
  deux joueurs differents ne doivent jamais partager un parcours.
- Aucun tour connu ne produit **aucune ligne**. Ecrire « 0 tour » laisserait croire a une
  entree en lice alors que le tournoi n'a peut-etre ete scanne que ce jour-la.
- Au-dela de `MAX_DAYS`, c'est une autre semaine : le repos ne dit plus rien de la fraicheur.

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
- `competitions.CATEGORIES` : le **niveau** d'un tournoi (Grand Chelem, Masters 1000,
  500, 250, Challenger, ITF). Meme regle que la surface — **rien n'est deduit d'un
  libelle a l'execution** : « Masters » vaut pour Monte-Carlo comme pour le tournoi de
  fin d'annee. Le seed de la migration 013 est en revanche une decision humaine,
  verifiee tournoi par tournoi contre les calendriers ATP et WTA, cle par cle ; le reste
  se saisit depuis `/competitions`, et seulement pour le tennis (les valeurs proposees
  sont celles des circuits ATP et WTA). `masters_1000` couvre les Masters 1000 de l'ATP
  **et** les WTA 1000 : meme etage de la hierarchie, et le circuit se lit deja dans le
  libelle — les separer diviserait par deux des echantillons deja courts. Un niveau non
  renseigne ne produit **aucune ligne** de statistiques : « non renseigne » ne dirait
  rien sur les matchs, seulement sur la saisie.

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
  « hors selection » et horodates. **Sans ce second groupe, un match commence etait
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
  - **Le garde-fou compte autant que le chiffre.** Le template interdit explicitement de
    rapprocher un taux d'une cote : ce serait calculer une esperance, et le fait que le
    chiffre vienne de l'historique de l'utilisateur n'y change rien (section 9). Un test
    verifie que le bloc porte cette interdiction, un autre qu'aucun champ financier
    n'apparait sur `Feedback` ni `FeedbackRow`.
  - Le signal le plus utile est l'ecart entre la confiance annoncee et le taux constate :
    il dit que la notation derive. C'est pour lui que `by_confidence` existe.
- `competitions.notes` : la fiche d'une competition (format, phase, enjeu, particularites).
  Rendue **une seule fois par lot**, pas par match : repeter le format d'une coupe a chaque
  affiche couterait des tokens sans rien apprendre.
- `preferences` (table cle/valeur, cle `session_notes`) : les consignes permanentes de
  l'utilisateur, recopiees en tete de prompt. Elles priment sur les preferences generales
  du template, **jamais sur les interdits** — le template le dit noir sur blanc. Seule leur
  longueur est bornee : ce texte n'est ni compile ni interprete.

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
page. Les fragments HTMX sont des templates `_*.html` autonomes, inclus par les pages
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
page : `--edge` (liseré clair en haut d'une surface) porte le relief, la lueur de `body::before`
donne une direction a la lumiere.

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
