# CLAUDE.md — instructions de travail sur ce depot

`SPEC.md` est la source de verite du projet. Ce fichier en est le rappel operationnel :
commandes, conventions, et surtout la liste des interdits. En cas de contradiction,
`SPEC.md` gagne.

## Trois lecons de methode, qui valent plus que n'importe quel chantier

Ecrites apres deux jours de travail (13-14/08/2026) parce qu'elles se sont verifiees a
chaque fois, et qu'elles se redecouvriraient autrement au meme prix.

### 1. Mesurer avant de coder, meme — surtout — quand le decideur affirme

**Six premisses de l'utilisateur ont ete dementies par la mesure en deux jours, dont trois
qui ont change une decision.** Aucune n'etait de la negligence : ce sont des affirmations
plausibles, ecrites de memoire par quelqu'un qui connait son projet mieux que quiconque.

| Ce qui etait affirme | Ce que la mesure a dit |
| --- | --- |
| le combine extreme consomme les places hautes | il se batit dans les deux bandes sures, sans en toucher une |
| un combine de 20 jambes exige de rouvrir les quotas | il tient au niveau session, le plafond etant par **prompt** |
| les quotas sont calibres sur un lot de 28 | sur 10, et ils saturent des 10 — 140 n'en donne pas un de plus |
| on ne demande pas assez de marches profonds | on demande tout ; **Betclic ne sert qu'un marche sur 364 matchs** |
| `cout = nb_marches x nb_regions` | la facturation porte sur les marches **servis** — surestimation de 4 a 7 |
| un match tres vu est une grosse affiche | c'est un match dont la competition a ete fractionnee |

Les trois qui ont change une decision sont les trois dernieres : sans elles, on aurait
achete des marches inexistants, garde un plancher de credits regle sur un chiffre fantome,
et cherche un biais de selection la ou il n'y a qu'un artefact de decoupage.

**La regle qui en sort** : une premisse enoncee par le decideur se mesure comme une autre,
et le dire ne coute rien quand elle tient. Le corollaire tient en une question — *cette
affirmation, sur quelle mesure repose-t-elle ?* — et il vaut aussi pour ses propres
conclusions : deux d'entre elles ont ete reprises en cours de route, un test fragile et un
« servi par personne » qui ne valait que pour un match.

### 2. Le defaut caracteristique de ce projet : une sortie identique pour l'echec et pour
le cas ordinaire

**Rencontre cinq fois en deux jours, dans cinq couches differentes.** Ce n'est plus une
serie d'accidents, c'est la forme que prennent les defauts ici.

| Ou | Le succes et l'echec rendaient |
| --- | --- |
| bloc CONTEXTE | `0/26` — « rien a dire » et « on n'a pas pose la question » |
| `picks.facts_json` | `NULL` — bloc absent et bloc vide, qui est la reponse normale |
| bloc « paris poses » de `/stats` | masque — aucun pari pose et « cette page ne mesure pas ca » |
| import des blocs `conf` | silence — zero bloc n'avertissait pas, un bloc pour trois si |
| assertions de test | vert — la propriete tenait, ou la sortie du jour n'avait pas bouge |

**Le symptome commun : rien ne casse.** L'interface a l'air normale, les tests passent, et
le defaut se decouvre des semaines plus tard en cherchant autre chose. C'est la forme la
plus couteuse qu'un defaut puisse prendre dans un outil dont tout le travail consiste a
distinguer une absence de donnee d'une absence de mesure.

**La question a se poser sur toute sortie** : *si ceci echouait, la sortie serait-elle
differente ?* Si non, l'echec est invisible et il faut le nommer — un motif, une note, un
compteur, une ligne de journal. Et le meme raisonnement vaut pour les garde-fous : deux
d'entre eux ne disaient pas quand ils se declenchaient, les causes de contexte avant la
migration 044 et le plancher de credits apres.

### 3. Une recommandation d'inaction est un livrable

Deux mesures se sont conclues par « ne code rien », et ce sont des resultats au meme titre
que les chantiers livres :

- **le sondage `regions=eu`** (14/08) : aucun book francais ne sert la profondeur de
  marche. Elargir le perimetre interroge n'ajouterait aucun prix jouable — ce n'est pas un
  arbitrage de cout, c'est une absence d'offre, et la question est close ;
- **le biais d'exposition** (14/08) : la lecture ne le tranchera **jamais**, l'exposition
  etant presque collineaire a la competition. Ce n'est pas « pas assez de donnees », c'est
  un plan d'observation inadapte.

Une porte fermee par la mesure vaut mieux qu'une porte laissee entrouverte : elle
s'accompagne de sa date et de son chiffre, et personne ne la rouvre par acquit de
conscience. **Un resultat negatif non ecrit sera refait** — et il faut l'ecrire sous la
forme qui empeche de le refaire, jamais sous la forme « pas d'effet mesure », qui invite a
reessayer avec plus de donnees.

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

**La facturation porte sur les marches SERVIS, pas sur les marches demandes.** C'est
contre-intuitif, la formule `nb_marches x nb_regions` est dans la documentation du
fournisseur, et le prochain lecteur la reecrira de bonne foi — d'ou cette mesure, datee.

Mesure du 14/08/2026 sur les **569 appels d'etage B** de la base :

- moyenne **2,32 credits** au football et **3,94** au tennis, maximum 11, **jamais 15 ni
  17**, les tailles que la formule predit. Distribution : 197 appels a 2, 169 a 3, 158 a 5,
  8 a 11, 11 a 0 ;
- au tennis, **10 marches sont demandes et 5 servis** (`h2h`, `spreads`,
  `alternate_spreads`, `totals`, `alternate_totals`) — et 158 appels ont ete factures
  exactement **5**. Les cinq marches par set, jamais servis, sont **gratuits** ;
- confirme en direct par le sondage `regions=eu` du meme jour : 15 marches demandes,
  **7 credits factures**.

Consequence, et c'etait un defaut vivant : `expected_cost()` surestimait d'un facteur
**4 a 7**, et `ODDS_API_CREDIT_FLOOR` refusait des appels sur ce chiffre-la.
`enrich.unit_cost()` calibre donc l'estimation sur le facture observe — par competition
d'abord, par prefixe de cle ensuite, la formule en dernier repli.

- Le **maximum** observe et non la moyenne, avec une marge de 25 % : une estimation sert a
  ne pas franchir un plancher, donc elle se trompe du bon cote. Mesure a la livraison :
  Ligue 2 passe de 15 a 3, Super League chinoise a 4, Cincinnati de 10 a 6.
- Sous `COST_MIN_SAMPLE` (3) appels, rien n'est calibre : un seul releve anormal
  deplacerait l'estimation de toute une competition.
- Le repli de sport se lit sur le **prefixe de la cle** (`soccer_`, `tennis_`) et non sur
  une table de correspondance, qui aurait vieilli au premier sport ajoute.
- **Le defaut est reel mais n'a jamais mordu, et rien ne l'aurait dit** : les refus du
  plancher ne sont journalises nulle part — meme angle mort que les causes de contexte
  avant la migration 044. Ce qui se mesure : le quota est passe de 500 a 120 du 04 au
  06/08, puis a 20 000 le 06/08 au soir, et n'est jamais redescendu sous **17 384** — soit
  34 fois le plancher. L'estimation fantome n'a donc eu aucune occasion de bloquer quoi
  que ce soit depuis huit jours.

**Aucun book francais ne sert la profondeur, et la question est close** (sondage
`regions=eu`, 14/08/2026, un match de Super Lig, 7 credits). Quinze books ont repondu :
**Betclic (FR) sert `h2h` et rien d'autre**, comme sur les 364 matchs de la base. Les
marches profonds existent chez Pinnacle, Codere (IT), Coolbet et Matchbook — aucun
jouable en France. Neuf des quinze marches demandes ne sont servis par **personne** en
region `eu` : `correct_score`, `correct_score_h1`, `btts_h1`, `halftime_fulltime`,
`team_totals`, `alternate_team_totals`, `alternate_totals_corners`,
`alternate_totals_cards`, `corners_1x2`.

- Elargir le perimetre interrogé n'ajouterait donc **aucun prix jouable**. Ce n'est pas un
  arbitrage de cout : c'est une absence d'offre, mesuree.
- Le sondage reste **hors du perimetre regulier** : 7 a 17 credits une fois se paient, un
  book de plus dans chaque appel non.
- La profondeur **est** collectee, en reference, et c'est le gabarit qui la sous-vendait —
  voir « Le rendu compact » et la place accordee a `A relever`.

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
- **Le releve commun au lot se dit une fois, pas vingt-quatre.** Sur le lot du 14/08,
  24 blocs sur 28 portaient mot pour mot `A relever  Handicap, O/U` : la ligne cessait
  d'informer, et les quatre exceptions — celles qu'il fallait lire — se noyaient dedans.
  Une phrase de portee generale les remplace, et **seules les exceptions gardent leur
  ligne** (`render.common_unplayable`).
  - **Derive du lot, jamais code en dur.** « Handicap et O/U en reference » est vrai un
    jour parce que le book principal ne sert que le 1N2 sur ces competitions-la ; l'ecrire
    dans le gabarit ferait mentir le prompt le jour ou la collecte change.
  - **La majorite se compte sur tous les blocs**, pas sur ceux qui portent une ligne : une
    phrase generale sur un lot dont deux blocs sur six sont concernes se lirait comme
    valant pour les six. Mesure : un prompt du 13/08 est exactement dans ce cas (2 sur 6),
    et garde sa liste plate.
  - **Seuil mesure sur les vingt derniers prompts** : douze ne portent aucune ligne, et
    parmi les huit qui en portent, le motif dominant couvre 85 %, 100 %, 100 %, 66 % et
    33 % des blocs. Remplacer n lignes par une phrase en coute deux, donc la condensation
    gagne a partir de quatre (`COMMON_UNPLAYABLE_MIN`) — trois prompts sur vingt
    condensent, dont celui de 28 blocs.
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
- **`A relever` a droit a la place que la mesure lui donne.** Le gabarit disait une fois,
  en passant, qu'un tel marche est selectionnable, et consacrait bien plus de place a
  interdire la comparaison entre books. Or `betclic_fr` sert **un seul marche** sur les
  364 matchs de la base : ecarter les lignes `A relever`, c'est ne garder que le 1N2 —
  exactement ce que la section B demande de depasser, et une analyse reelle a deja renonce
  a deux angles de jeux pour cette raison. Le chapitre le dit donc en toutes lettres, avec
  la mesure. **La regle, elle, n'a pas bouge** : la cote du bloc reste celle qu'on
  enregistre, et comparer les books reste interdit. C'est la place qui etait fausse, pas
  la regle — un test verifie que les deux moities y sont.
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
  - **Trois causes et deux notes seulement : la ligne mentait sur la deuxieme.**
    `_unserved_line` n'en connaissait que deux — substitution, ou generique — et la cause
    « demande mais non recu **pour ce match** » se rendait donc sous « aucun book
    interroge ne les sert **sur cette competition** », c'est-a-dire en affirmant bien
    au-dela de ce qui avait ete observe. Sur une ligne dont l'effet est d'interdire de
    chercher, c'est le pire endroit du bloc ou avoir tort.
  - `_unserved_for` rend donc **deux listes** et `_unserved_line` **une ligne par cause**,
    jamais fondues : les deux constats n'appellent pas le meme comportement. Sur la
    competition, on ne cherche pas — ca ne changera pas d'une affiche a l'autre ; sur un
    seul match, ce peut n'etre qu'un trou de ce releve-la, et le marche existe peut-etre
    chez le bookmaker.
  - Le chapitre COMMENT LIRE LES BLOCS definit **les trois** notes et le comportement de
    chacune, meme regle que `Arbitre` et `Absents` : un libelle sans definition dans le
    chapitre est le defaut que ce prompt evite partout.
- `services/markets.py` detient la liste des marches demandes, parce que `enrich` et
  `session` en ont besoin tous les deux. La copier des deux cotes les aurait fait diverger,
  et le prompt aurait annonce `Non servis` sur un marche que l'outil ne demande plus.

Ajouter un marche : une entree dans `MARKET_ORDER`, un rendu dedie si sa forme le merite,
et un test. Sans rendu dedie, le repli generique s'applique — c'est acceptable, pas une
regression. L'entree, elle, ne se negocie pas : les deux props buteurs n'en avaient aucune
et sortaient en **cle brute** (`player_goal_scorer_anytime`) dans la ligne « Non servis »
d'un match de Ligue 1. Meme piege que `alternate_totals` avant elles, et il se reproduira a
chaque marche ajoute a `markets.py` sans l'etre a `render.py`.

## Le signe du handicap : deux fournisseurs, deux conventions

**The Odds API donne a chaque issue son propre handicap** — « Al-Qadsiah -1 » et
« Al-Shabab +1 » sont les deux moities d'un meme palier, aux signes opposes. **API-Football
ecrit le handicap du point de vue de l'equipe qui recoit, des deux cotes** : « Home -0.5 »
et « Away -0.5 » sont la meme ligne, la seconde valeur etant le prix de la **double chance**
de l'exterieur. Le releve de substitution entrait tel quel, et le bloc annoncait un pari
pour l'autre.

Mesure qui l'a revele, sur une Supercoupe d'Europe : le bloc servait
« Aston Villa -0.5 2.12 » quand Aston Villa vainqueur valait 4.60. Le prix etait juste —
c'est celui de sa double chance — mais le libelle designait le pari inverse. Sur la base
entiere, 33 rencontres portaient la faute, **toutes** relevees par ce chemin, **aucune** par
The Odds API.

- La conversion se fait a l'ingestion (`fixtures._outcome`) : la base ne connait qu'une
  convention, celle ou chaque issue porte son signe. Convertir au rendu aurait demande de
  savoir de quel fournisseur vient chaque ligne a chaque lecture.
- **`_render_spreads` rend un seul palier, ses deux moities.** Chaque camp choisissait sa
  ligne de son cote — la plus proche de 2.00 — si bien que rien ne garantissait que les deux
  moities affichees fussent les deux faces d'un meme pari. Elles sortent du meme palier et le
  second signe est l'oppose du premier **par construction** : c'est la seule forme ou
  l'invariant ne peut pas se defaire, et c'est elle que le test verifie, pas la valeur d'une
  ligne. Sur des donnees saines la ligne rendue ne bouge pas.
- `_by_handicap` est **partage par le football et le tennis**, qui n'en tirent pas la meme
  forme — une ligne d'un cote, une echelle de l'autre. La convention d'ancrage, elle, ne peut
  pas differer : ecrite deux fois, elle aurait diverge et les deux sports ne se liraient plus
  pareil. La convention tennis a ete verifiee sur le rendu reel, elle etait juste.
- **La ligne `Alerte` confronte deux marches**, seule du bloc a le faire : au football `-0.5`
  **est** la victoire seche et `+0.5` la double chance, donc les deux prix se deduisent du
  1N2. Le controle n'a **pas de seuil de tolerance** — il demande seulement lequel des deux
  paris le prix observe decrit le mieux — donc il ne derive ni avec la marge du book ni avec
  l'ecart entre deux books. `HANDICAP_ALERT_MARGIN` n'est pas cette tolerance mais la
  condition de lisibilite de la question : a la cote 1.05 les deux paris valent 0.952 et
  0.981, l'ecart tombe sous le bruit, et on ne demande rien. Un silence vaut mieux qu'une
  accusation que la donnee ne porte pas.
  - Elle ne coute rien quand tout va bien, et **son mode d'emploi non plus** :
    `build_prompt` passe `handicap_alerts`, vrai des qu'un bloc en porte une. Meme regle que
    `context_labels`, un cran plus loin — cette ligne est faite pour ne jamais servir.
  - Elle est **rendue avec les lignes de fin de bloc**, qui toutes qualifient le releve.
    Les autres disent ce qui n'est pas la ; celle-ci dit que ce qui est la ne doit pas etre
    lu tel quel.
  - **Limite a connaitre : elle ne controle que le barreau ±0.5**, le seul dont la
    semantique se deduise exactement du 1N2. Les barreaux -1, ±1.5, ±2.5 ne sont garantis
    que par la conversion a l'ingestion, et rien ne les recoupe : un cinquieme fournisseur
    qui n'inverserait que les barreaux hauts passerait inapercu. L'etendre demanderait de
    modeliser la probabilite d'un ecart de deux buts, c'est-a-dire d'inventer un modele —
    exactement ce que la section 9 interdit. La limite est donc structurelle, pas un manque
    de soin.
- **La migration 035 reprend les lignes deja ecrites, sur un critere structurel et non sur
  une liste de books.** Un book se configure (`APIFOOTBALL_BOOKMAKERS`) et la liste aurait
  vieilli ; surtout, elle n'aurait rien prouve. Ce qu'on sait dire, c'est qu'une paire de
  prix forme un livre a deux issues ou n'en forme pas : `1/a + 1/b` vaut un peu plus de 1
  quand les deux cotes sont les deux faces d'un meme pari, et n'importe quoi sinon. Verifie
  sur la base servie : 331 groupes, 33 repris, 298 intacts, et les 18 ou les deux lectures se
  valent — echelles symetriques, ligne nulle — ne bougent pas, parce qu'il n'y a rien a y
  corriger. Le critere est idempotent : une fois la lecture retablie, il ne reconnait plus
  rien.
  - `prompt_odds` est repris avec `odds`, et c'est lui qui compte : les cotes vivantes se
    refont au prochain releve, le releve fige d'une session **ne se reconstitue pas apres
    coup**.
  - **Le retour arriere ne rejoue pas ce critere.** Une ligne reparee est indiscernable d'une
    ligne saine, et le rejouer retournerait les 298 groupes qui n'ont jamais eu de defaut. Il
    se scope donc sur les books du releve de substitution — ce que l'aller ne pouvait pas
    faire, mais qui suffit quand on defait un geste connu au lieu de diagnostiquer.
- **Le defaut a survecu parce que `render.py` n'avait aucun test de handicap football.** Le
  tennis en avait trois, dont deux sur le signe.

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
  - **Une cle absente ne se voit nulle part tant qu'un match n'est pas parti muet a
    l'analyse**, et c'est ce que repare le controle au scan — voir « Un contexte absent a une
    cause ». Le fournisseur ne nomme pas toujours la competition comme The Odds API : l'EFL
    Cup s'y appelle « League Cup », un rapprochement par libelle aurait donc echoue la aussi.
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
- **« Absente du catalogue » n'est pas « hors saison »**, et la confusion fermait le seul
  chemin d'entree de toute une classe de competitions. Hors saison, le fournisseur connait
  la competition et ne sert rien pour l'instant : elle figure dans `/sports`, la
  synchronisation la cree, `api_active` passe a 0, et l'import de matchs se propose. Absente
  du catalogue, elle n'y est **a aucun moment**. Mesure du 12/08/2026 : 175 cles servies,
  dont 67 au football, et **aucune Supercoupe d'Europe** quand API-Football la sert sous la
  ligue 531. Aucune synchronisation ne la decouvrira jamais.
  - `competitions.create_apifootball()` est cette porte. Sans elle, le seul chemin etait
    `manual.py`, qui cree une competition comme **effet de bord** d'un match saisi a la
    main : sans identifiant de ligue, donc muette — ni classement, ni forme, ni absents —
    et sans le bouton qui aurait ramene les matchs tout seuls.
  - **`api_active = 0` s'ecrit explicitement.** La colonne vaut 1 par defaut et n'est
    jamais mise a jour que par `sync_from_api`, qui s'indexe sur `oddsapi_key` : une
    competition sans cle garderait 1 pour toujours, et `import_competition` la refuserait
    comme « deja servie par The Odds API » — l'affirmation exactement inverse de la verite,
    sur le seul bouton qui pouvait lui donner des matchs.
  - **Elle est creee active**, contrairement a ce que la synchronisation decouvre. La regle
    « rien ne se met a couter sans decision » protege le quota ; sa raison ne s'applique pas
    ici, `scan.active_competitions` filtrant sur `oddsapi_key IS NOT NULL`. Et la creer
    **est** la decision : elle se tape a la main, une par une.
  - **L'identifiant de ligue est obligatoire**, la ou `set_apifootball_league` traite une
    saisie illisible comme « non rattachee ». Le contraste est juste : la-bas l'effet est
    une ligne de contexte absente, ici c'est une competition qui ne recevra jamais un seul
    match, c'est-a-dire tout ce pour quoi on la cree. Il n'est pour autant jamais devine
    d'un libelle, meme regle que partout ailleurs.
  - Un libelle deja pris est **refuse**, casse et accents ignores (`labels.sort_key`) :
    deux competitions au meme nom, l'une scannee et l'autre non, que rien ne distingue a
    l'ecran, se partageraient les matchs. Le rattachement de l'existante se corrige au
    tableau.
  - La colonne « Servie ? » porte donc **trois** etats et non deux. Le troisieme se lisait
    « manuelle », et le bouton d'import etait garde par la seule branche « hors saison » :
    il ne s'affichait jamais la ou il est le seul chemin d'entree. Sa condition reprend
    desormais mot pour mot les gardes de `import_competition` — un bouton qui propose ce
    que le service refuse est pire qu'absent.
- **Cotes de substitution** (`import_odds`) : pour un match dont aucun prix ne vient du
  book principal, un releve chez un book proche de Betclic. **Betclic n'est pas au
  catalogue d'API-Football** — il faut donc un substitut, et « proche » se mesure au lieu
  de se supposer. Sur des matchs servis par les deux fournisseurs, l'ecart moyen absolu au
  prix Betclic valait 3.0 % pour BetVictor, 3.4 % pour William Hill et 888Sport, contre
  5.4 % pour Unibet, 6.0 % pour Pinnacle et 6.8 % pour 1xBet : **l'intuition « un book
  francais sera le plus proche » etait fausse**. L'ordre se regle par
  `APIFOOTBALL_BOOKMAKERS`, l'echantillon etant court.
  - **Aucun repli sur un book hors liste** : prendre le premier venu ferait passer pour
    jouable un prix dont l'ecart n'a jamais ete mesure. Une absence constatee est dite.
  - **Parmi les books de la liste, en revanche, c'est le catalogue qui tranche.** Ils ne
    servent pas la meme chose et l'ecart n'est pas marginal : sur un tour preliminaire de
    Ligue des champions, le fournisseur sert quatorze books, et les six de la liste vont de
    6 a 11 marches modelises. Prendre le premier disponible donnait 888Sport et ses sept
    marches quand Bet365 en servait dix. La garantie de proximite tient — tous les
    candidats sont deja mesures — donc l'ordre ne departage plus que les egalites.
    - Le compte porte sur ce que le book **apporte**, deja-servi deduit : le plus fourni
      dans l'absolu peut ne rien ajouter. Et le deja-servi se lit **par book**, sans quoi
      un second passage compterait le releve precedent comme un acquis d'ailleurs, ne
      verrait plus aucun apport nulle part, et changerait de book a chaque fois.
  - Ces prix portent le suffixe `(ref.)` comme les autres books de reference : ils situent
    le marche, ils ne sont pas jouables tels quels.
  - Le releve remplace **le seul book releve** : ni Betclic, ni la saisie manuelle.
  - **Une seule source par marche**, comme `services/reference.py` : un marche deja servi
    n'est pas relu. Deux prix sur la meme issue, le bloc en afficherait un au hasard et
    l'outil inviterait a la comparaison de cotes entre bookmakers que SPEC.md interdit.
    C'est cette regle qui permet au releve d'aller sur un match qui a **deja** des cotes.
  - **Le declencheur est « aucun prix jouable », et non « aucune cote ».** La regle
    d'origine gardait le bouton sur les seuls evenements vides, au motif qu'ailleurs il
    n'ajouterait qu'un prix non jouable a cote d'un prix jouable — vrai, sauf qu'il n'y a
    parfois **aucun** prix jouable : sur la qualification de Ligue des champions, les trois
    seules cotes venaient d'un book de reference. Mesure qui l'a declenche : `coverage`
    montre que l'etage B a ete **paye** sur cette competition et que The Odds API n'y sert
    que le `h2h` — 14 marches profonds sur 15 constates absents chez `betclic_fr`,
    `pinnacle` et `unibet_nl` reunis, dix constats chacun. Aucun credit ne pouvait donc
    acheter plus, quand API-Football servait dix marches de plus chez un book mesure. Sur
    les dix matchs concernes du board, le releve est passe de 3 cotes a 83-100, pour zero
    credit Odds API.
    - La saisie manuelle **n'ouvre pas** le releve : c'est un prix pris a la main chez le
      book principal, et le substitut ne doit pas venir par-dessus. La regle se lit chez
      `labels.primary_book` / `is_reference`, ecrites une fois — la recopier en SQL
      l'aurait fait diverger au premier book ajoute.
    - Un evenement peut etre **a la fois achetable et relevable**. Il ne figure alors que
      dans `targets`, avec un drapeau `substitute` : dans les deux listes, il paierait son
      contexte deux fois. On achete d'abord, on releve ensuite — ce que The Odds API sert
      prime, le releve ne comble que le reste.
  - **Trois marches manquaient a `BET_MARKETS`** — `HT/FT Double`, `Corners 1x2`,
    `Correct Score - First Half`. `markets.py` les demande a The Odds API depuis toujours
    et `render.py` sait les ecrire : sur une competition ou le book principal ne sert rien,
    ils n'arrivaient donc par **aucun** des deux chemins. Meme piege que les props buteurs,
    et il se reproduira a chaque marche ajoute d'un cote sans l'autre.
  - **Les camps se traduisent en noms d'equipes** (`Home/Draw` → `Lyon/Draw`). Ce n'est pas
    cosmetique pour la double chance : `render` identifie 1X / 12 / X2 par **les equipes
    citees dans l'issue**, donc `Home/Away` ne s'identifiait pas, le bloc tombait dans le
    repli generique, et le prompt affichait « Home/Away 1.14 » — une ligne ou l'analyse ne
    peut meme pas dire de quel camp on parle. Le defaut dormait depuis que la double chance
    est dans la table : il ne se voyait pas, le releve n'allant que sur des matchs sans
    aucune cote.
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
  - **Trois etats, et le troisieme est le cas ordinaire.** Mesure du 14/08/2026 sur le lot
  du jour : **20 paires annoncees « divergence », dont 10 sont le meme homme** sous deux
  ecritures — « Laurent Guyot » contre « L. Guyot ». La comparaison stricte les declarait
  differents, et la ligne la plus decisive du dossier d'equipe se noyait dans son propre
  bruit : 13 blocs sur 25 la portaient, 7 la gardent.
  - `COACH_INITIAL` **ne conclut pas**, et c'est le point : deux prenoms partageant
    l'initiale et le nom sont deux hommes, et les fratries existent au football. La ligne
    dit sur quoi elle repose — « apparié sur l'initiale du prénom » — au lieu de trancher.
    Meme regle que partout : le cas indecidable se nomme.
  - Elle porte le prenom **de la feuille**, le plus complet : c'est avec lui qu'on cherche,
    la fiche l'abregeant. L'anciennete, elle, ne vient que de la fiche.
  - **Le repli des accents etait deja applique des deux cotes** (`sort_key`), et n'a jamais
    ete la cause : « N. Usaï » et « Nicolas Usai » se replient tous deux en « usai ». Ce
    qui les separait etait l'initiale seule. Le fold reste indispensable — c'est lui qui
    permet au nom de famille de se comparer — mais il ne suffisait pas.
  - Le nom de famille se compare **en suffixe** : « J. Machado Sacramento » contre « João
    Pedro Machado Sacramento » porte deux prenoms d'un cote et un seul de l'autre, et
    exiger la meme longueur y inventerait une divergence.
  - Le chapitre COMMENT LIRE LES BLOCS definit **les trois** mentions et ce que chacune
    permet de conclure : un libelle sans definition est le defaut que ce prompt evite
    partout.
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
  - **Un forfait s'y lisait comme un match joue**, et documenter le defaut ne le corrigeait
    pas. Voir la section suivante.

## Les absents : trois etats, et une fenetre qui se rend

**L'hypothese de depart etait fausse, et la mesure l'a montree avant qu'une ligne soit
ecrite.** Le defaut constate — trois joueurs de Hapoel Tel-Aviv annonces « plus vus depuis
le 23/07 » alors qu'ils etaient titulaires le 06/08 — etait attribue a une fenetre de
reconstruction limitee a une seule competition. Verifie le 12/08/2026 :

- la fenetre sort de `/fixtures?team=&last=`, qui **ne filtre sur aucune competition** —
  une coupe nationale et une coupe d'Europe figuraient dans la meme fenetre de cinq ;
- le fournisseur sert **les quatre feuilles**, et les trois joueurs figurent sur celles du
  06/08, du 30/07 et du 23/07 ;
- le chemin de reconstruction **rejoue a l'identique** sur la meme equipe ne reproduit pas
  le defaut : aucun manquant.

Conclusion : **la regle etait juste, les feuilles ne l'etaient pas encore au moment du
releve** — le fournisseur les a publiees apres coup. Contre ce genre de panne il n'existe
pas de correctif de regle, seulement de quoi la voir.

- **La ligne porte donc sa fenetre** — `(fenetre lue : 3 feuille(s), du 23/07 au 06/08,
  toutes competitions)` — et ce sont les bornes de ce qui a ete **lu**, pas demande : une
  feuille non encore publiee est sautee, si bien que la fenetre reelle est plus courte que
  `SHEETS_LAST`. Meme idiome que `Parcours` et sa fenetre de scans, et que l'en-tete des
  marches et son heure de releve.
- **`Absents` porte trois etats**, et c'est le motif de toute la serie — meteo, arbitre,
  handicap : `aucun absent signale` (on a regarde), `non interroges` (le fournisseur ne
  couvre pas cette competition, ca ne changera pas), `source injoignable` (elle n'a pas
  repondu ce jour-la, ca se retentera). `donnees non disponibles` melangeait les trois, si
  bien qu'une couverture absente se lisait comme un incident et l'inverse. Un etat manquant
  sur un releve anterieur vaut `non interroges` : le plus prudent des deux, il envoie
  chercher au lieu d'affirmer qu'on a regarde.
- **Mesure de couverture, et elle est severe** : sur 165 releves d'absents en base,
  **81.8 % sont `non interroges`**. Sur les seuls matchs reellement analyses l'echantillon
  s'inverse — 3 servis sur 4 — mais il est trop court pour conclure. C'est une information
  sur ce que l'outil peut promettre : hors des grands championnats, la liste d'absents est
  **a chercher**, et la ligne le dit maintenant au lieu de le laisser deviner.
- **Aucune source d'absences n'a ete branchee, et c'est une decision documentee.**
  Transfermarkt a un `robots.txt` permissif (`User-agent: * / Allow: /`) mais des conditions
  d'utilisation qui interdisent explicitement l'acces automatise — verifie le 12/08/2026 :
  « The User is not permitted to access or copy the Digital Content using bots, spiders,
  screen scraping or other automated processes. » C'est **l'inverse exact du cas meteo**, ou
  le `robots.txt` interdisait et les conditions autorisaient : dans les deux cas ce sont les
  **conditions d'utilisation qui gouvernent** un client automatise, et ici elles disent non.
  Les sites de clubs, eux, demanderaient un analyseur par club — licite peut-etre,
  exploitable non.

## Les absents au football : la couverture declaree est exacte, et la porte est fermee

**Resultat negatif du 19/08/2026, ecrit sous la forme qui empeche de le refaire.**
La question revient naturellement — la moitie des matchs analyses sont du
football, la ligne `Absents` dit « non interroges » sur la moitie des blocs, et
une absence est le fait date le plus decisif du sport. Elle est close.

Sonde sur **seize competitions**, avec la couverture declaree par `/leagues` d'un
cote et ce que `/injuries?league=&season=` sert reellement de l'autre :

- **treize competitions declarees `injuries: false` servent zero ligne**, reessais
  compris — Portugal, Belgique, Autriche, Liga 2, Ecosse, Suisse, Chine, Arabie
  saoudite, Conference League, Europa League, DFB-Pokal, EFL Cup, Leagues Cup, et
  **la Serie A**, qui la declare fausse en 2026 ;
- **les trois declarees `true` servent** : Eliteserien 1 300 lignes, Turquie 56,
  Eredivisie 205.

**La couverture declaree fait donc foi, verifie plutot que suppose**, et l'etat
« non interroges » est **exact** partout ou il parait : le corriger le rendrait
faux. La Norvege et la Turquie, souvent citees comme non couvertes, le sont.

- Compte : **API-Football plan Pro**, 7 500 appels/jour, 300/minute. La cle est en
  service depuis toujours — 323 releves de contexte en base.
- **Le substitut fonctionne deja.** `Effectif` reconstruit les absents depuis les
  feuilles la ou `injuries` est faux et `lineups` vrai. Rendu de 120 blocs reels :
  51 blocs eligibles, **51 feuilles collectees sur 51**, et la ligne sort sur 21.
  Les 30 autres sont le comportement documente — rien quand personne ne manque.
- **Le retard entre l'annonce et l'apparition n'est pas mesurable**, et la cause
  est structurelle : `context` est indexee par (evenement, type) et chaque
  enrichissement **ecrase** le precedent — 323 lignes pour 323 evenements. Meme
  forme que `commence_time` avant la migration 040 et `odds` avant la 048. Ce qui
  se mesure a la place est l'**accord** : sur le lot du 18/08, le bloc portait
  deja **22 des 29 absents** que la recherche est allee chercher, et trois des
  sept manquants etaient des choix de rotation qu'`/injuries` n'a aucune raison de
  porter.
- **Ce qui ameliorerait les blocs football n'est donc pas une source de plus** :
  c'est que la section A cesse de rechercher ce que le bloc dit deja. C'est une
  question de gabarit.

Ce qui rouvrirait la question, et rien d'autre : une competition qui passerait de
`false` a `true` chez le fournisseur — ce que la synchronisation constate deja
toute seule.

## Le nom de l'entraineur : le champ propre etait a cote du champ casse

**Mesure du 21/08/2026, apres un lot ou 43 % des matchs portaient une ligne
`Entraineur` douteuse.** Le fournisseur sert **trois** champs pour une meme
personne — `name`, `firstname`, `lastname` — et nous ne lisions que le premier.

- **970 des 1 631 fiches en base portent un nom abrege** (`X. Nom`), dont 876
  completables ;
- le cas emblematique vient du fournisseur : Sebastian Hoeneß y figure sous
  `name = "S. Hoeneb"`, le ß rendu par un b, quand `lastname` porte « Hoeneß »
  sans faute.

**Le correctif naif est faux, et c'est ce qui decide de la regle.** Prendre
`firstname + lastname` partout rendrait « Enrique Setién Solar » la ou `name` dit
« Quique Setién », et « Jesús Rodríguez Tato » la ou il dit « Tato » : le nom
d'usage est celui avec lequel on cherche. Il ne se remplace que lorsqu'il est
**abrege**, jamais parce qu'il est court.

- **Aucune concordance n'est exigee entre les champs**, et c'etait l'erreur du
  premier jet. Il n'y a rien a apparier : les trois decrivent la meme fiche, et
  choisir lequel afficher n'est pas un rapprochement. Exiger que le nom de
  famille abrege se retrouve dans `lastname` refusait 42 completions justes —
  noms composes, accents polonais, particules — et laissait passer le seul cas
  ou le champ ment vraiment.
- **Ce que ca repare depasse le libelle.** La feuille de match, elle, porte
  **toujours** le nom complet : 287 relevés sur 287, aucun abrege. La comparaison
  etait donc « fiche abregee contre feuille complete », ce qui ne pouvait
  conclure que sur l'initiale. Mesure sur les 281 paires : **220 « meme homme »
  contre 155**, les mentions d'incertitude tombent de **71 a 6**, et les
  divergences ne bougent pas (55).
- **Un second prenom ne fait pas deux hommes** (`_same_person`). Comparer deux
  noms complets faisait tomber trois paires en divergence qui sont le meme homme
  — « Alexander Matthias Blessin » contre « Alexander Blessin », « Desmond »
  contre « Des ». Le nom de famille doit etre identique et les prenoms
  compatibles mot a mot : deux prenoms differents partageant le nom restent deux
  hommes, les fratries existent au football.

### L'identifiant existe, et il ment

Le commentaire du code disait « sur l'identifiant, jamais sur le nom ». **Il se
trompait deux fois** : `_current_post` ne posait pas l'`id` dans le candidat, donc
ce chemin n'a **jamais** pu s'executer — et il ne le doit pas.

Mesure sur les 281 paires : **35 portent deux identifiants pour le meme homme**
(« Felip Ortiz », fiche 22636 contre feuille 26454), contre 7 ou l'identifiant
aurait mieux tranche que le nom. Le fournisseur duplique ses fiches
d'entraineur, et s'y fier retournerait 35 accords en divergences pour 7 gains.

**Meme famille que `fixture.venue.city`** : le champ existe, il est structure, et
il se trompe assez souvent pour qu'une regle batie dessus affirme a tort. La
regle « cherchez l'identifiant » dit ou regarder ; elle ne dispense pas de
verifier qu'il dit vrai. Porte fermee, mesuree — ne pas la rouvrir.

### Ce que le correctif ne repare pas

Une **nomination absente** de la charge utile reste hors de portee : c'est le cas
documente plus haut, et le nom complet n'y change rien. La ligne reste une piste
datee, pas un fait.

## L'arbitre : le nom seul, et pourquoi il n'y a rien d'autre

Un marche Cartons est servi sur une partie des blocs sans qu'aucune ligne ne permette de le
lire. La ligne `Arbitre` comble ce trou a moitie, et la moitie manquante est **mesuree**,
pas supposee.

- **`fixture.referee` est une chaine libre** — verifie le 12/08/2026 : pas d'identifiant,
  pas de pays, et pas de format stable (64 des 183 arbitres d'une saison de Conference
  League s'ecrivent « X. Nom », les autres non). C'est la **regle de revue appliquee avant
  d'ecrire une ligne** : on a cherche l'identifiant, il n'y en a pas, et la date de la
  verification est dans le commentaire.
- **Un historique de cartons n'est pas reconstructible a cout raisonnable.** Il faudrait
  agreger sur le libelle — « M. Oliver » et « Michael Oliver » seraient deux arbitres, le
  piege deja paye trois fois — puis un appel de statistiques **par match passe**.
- **Et le compte de matchs diriges serait du decor**, ce qui a tranche : sur une saison de
  Conference League, **157 arbitres sur 183 n'ont qu'un seul match**. La ligne dirait
  « premier match dans cette competition » sur 86 % des blocs, donc ne dirait plus rien —
  exactement le defaut des deux seuils egaux, corrige la veille. Un appel par competition a
  ete envisage, mesure, puis abandonne sur ce chiffre.
- **Ce qui reste vaut quand meme, et c'est mesure aussi** : sans la ligne, il fallait une
  requete pour savoir *qui* arbitre avant d'en depenser une seconde sur ses habitudes. Le
  nom en supprime une sur deux. Il ne coute **aucun appel** — il vient du match deja resolu.
- **Le garde-fou de tokens a servi, et il n'a pas ete releve.** L'ajout a fait passer la
  fixture de six matchs a 11 615 tokens pour un plafond a 11 500. La reponse a ete de
  **tailler les modes d'emploi**, pas le nombre : les entrees `Arbitre`, `Meteo` et `Lieu`
  sont passees de 957 a 580 tokens en gardant ce qui **decide** — les trois etats, ce qu'ils
  appellent comme comportement — et en renvoyant ici ce qui **explique**. Une mesure qui
  justifie une regle n'a pas a etre payee a chaque session ; sa conclusion, si.
- **Deux etats rendus, un troisieme qui n'en est pas un.** « non encore designe » appelle le
  comportement inverse d'un nom : ne pas chercher, attendre. Le troisieme —
  « aucun historique dans cette confederation » — ne se constate pas d'ici : c'est un
  **resultat de recherche**, et le preambule dit que c'en est un valable, a ecrire en
  section A comme une caracteristique du match et non en section F comme un manque. Cas
  reel : l'arbitre somalien d'une Supercoupe d'Europe dirigeait son premier match en Europe.
- **« Non servi sur cette competition » est le troisieme etat rendu, et il dit l'inverse du
  deuxieme.** « Non encore designe » et « jamais servi ici » sont deux vides qui appellent
  des comportements opposes : attendre, ou aller chercher. Ecrire le premier partout faisait
  renoncer a une recherche qui aboutit — la DFB publie ses designations, c'est notre source
  qui ne les remonte pas.
  - **Le constat porte sur la competition, jamais sur un match ni sur une journee**
    (`referee_served`). Un match seul ne dit rien, et trois matchs d'un soir non plus : ils
    feraient basculer une competition qui designe. Il s'accumule sur nos propres releves,
    deja persistes, donc sans un appel de plus.
  - **La distribution est binaire, et c'est mesure** : sur les 66 releves de la base, une
    competition sert un arbitre sur **toutes** ses rencontres ou sur aucune — 22/22 en
    Conference League, 12/12 en Europa League, 9/9 en Ligue 2, et a l'oppose 0/7 en Leagues
    Cup, 0/3 en Super League chinoise. Aucune n'est partielle. Un seul nom quelque part
    prouve donc que la competition designe ; c'est l'absence qui demande un echantillon.
  - `REFEREE_MIN_SAMPLE` vaut **8**, le plus petit entier au-dessus du plus gros releve
    tout-vide de la base (7) : rien de ce qu'elle porte aujourd'hui ne bascule sur des
    donnees qui ne le portent pas, et une competition qui ne designe vraiment pas y arrive en
    une journee de coupe. Verifie en simulation sur le lot du 22/08 — a trois matchs
    enrichis, la DFB-Pokal dit encore « non encore designe ».
  - Un identifiant de competition inconnu laisse la ligne dire « non encore designe », qui
    n'affirme rien : une affirmation qu'on ne peut pas gager ne s'ecrit pas.
  - Le chapitre COMMENT LIRE LES BLOCS definit **les trois** libelles et le comportement de
    chacun : un libelle sans definition dans le chapitre est le defaut que ce prompt evite
    partout.

## Un contexte absent a une cause, et elle se nomme

**Quatre causes se repliaient sur `0/26`**, qui se lit « pas de donnees » alors qu'elle veut
dire « on n'a pas pose la bonne question ». Mesure du 14/08/2026 : trois matchs de Saudi Pro
League sortis a zero ligne le jour de la reprise du championnat — leur competition n'etait
rattachee a aucune ligue, donc rien n'a jamais ete demande, et le bloc ressemblait trait pour
trait a celui d'une competition mal couverte. L'EFL Cup portait deja **34 evenements** dans le
meme etat, dont un parti a l'analyse avec une selection prise dessus.

- **Le vocabulaire est celui de la ligne `Absents`**, et il n'y en a pas de second :
  « non interroges » (le fournisseur ne couvre pas) et « source injoignable » (il n'a pas
  repondu) decrivent ici les memes faits. S'y ajoutent deux **defauts de collecte** qui
  n'avaient aucun mot — `competition non rattachee`, `fixture non resolue` — et qui se
  reparent d'un geste au lieu de se chercher (`COLLECTION_FAULTS`).
- **Un lexique, deux redactions.** `CAUSE_LABELS` nomme la cause une seule fois ;
  `CAUSE_BLOCK_NOTES` parle a une analyse qui va chercher — ce qui compte pour elle est de
  savoir si ce qui manque est une absence de fait ou une absence de collecte, parce que la
  reponse decide si la recherche web peut combler le trou — et `CAUSE_UI_NOTES` parle a qui
  va reparer, en nommant le geste. Le motif **prolonge la ligne `Densite`** plutot que d'en
  occuper une seconde : il qualifie la densite, il n'ajoute pas un fait sur le match.
- **La fiche de priorite s'en sert, et c'est la que le typage change une decision** : un bloc
  vide faute de rattachement ne vaut **aucun** budget de recherche — il vaut une saisie, et
  le dossier couterait une place a un match ou chercher sert — quand un bloc vide faute de
  couverture est le **meilleur dossier du lot**, la recherche y etant le seul chemin.
- **La cause se lit sur ce qui est revenu, jamais sur les drapeaux de couverture.** Une coupe
  qui annonce tout a faux porte quand meme ses agregats depuis le championnat domestique de
  ses equipes : la declarer « non couverte » serait faux.
- **Migration 044 : une ligne par tentative, reussites comprises.** Trois des quatre causes se
  relisent a tout moment dans `competitions` et `events` ; « source injoignable » n'existe
  qu'a l'instant de l'appel — resolu a la lecture seulement, il disparaitrait au releve
  suivant et son taux serait immesurable, alors que c'est le seul des quatre qui dise quelque
  chose du fournisseur plutot que de notre saisie. Sans les reussites, on compterait des
  pannes sans savoir sur combien d'essais.
- **Le seul chemin ou la cause n'atteint pas `fetch_context`** est le saut de `run_enrich` sur
  un match sans ligue : c'est precisement le cas a compter, donc il journalise lui-meme.
- **Rien n'est retro-rempli.** Les causes d'hier ne se reconstituent pas, et une table vide
  dit la verite — la mesure commence a la mise en service.

**Le controle passe en amont, au scan** (`ScanReport.unmapped`). Le symptome arrive une
journee plus tard sous la forme d'un bloc a zero ligne, qui se lit comme un match sans
histoire plutot que comme une question jamais posee ; le scan, lui, sait au moment ou les
matchs entrent en base que rien ne pourra leur etre demande. Le cas se reproduit **a chaque
reprise de championnat**, donc plusieurs fois par an.

- Le bandeau les garde sous les yeux jusqu'au rattachement, **nommees et jamais comptees** :
  un compte se lit comme une file d'attente qui se resorbe, alors qu'ici rien ne bouge tant
  qu'une ligne n'a pas ete saisie, et c'est le nom qui dit laquelle.
- Seules celles qui portent des **matchs a venir** y figurent : au 14/08/2026, 33 des 67 cles
  football n'ont aucune entree dans `APIFOOTBALL_LEAGUES`, et les lister toutes noierait
  celle qui joue ce soir.

**Date du correctif : 14/08/2026.** La composition de la population `lecture` des statistiques
change a partir de la — avant, une selection pouvait etre en lecture parce qu'aucun fait
n'etait **disponible** ; apres, un bloc muet a une cause nommee et un ordre de passage. La
selection d'EFL Cup deja tranchee n'est pas reprise : elle etait bien une lecture, aucun fait
n'etait disponible ce jour-la.

## Les agregats de saison d'un match de coupe

`/teams/statistics` et `/standings` sont **scopes a une competition**. Sur un tour de coupe
cela ne decrit rien : les participants y ont joue un ou deux matchs — donc sous
`SEASON_MIN_MATCHES` — et une coupe n'a pas de classement. Mesure du 14/08/2026 : la
DFB-Pokal annonce `standings`, `injuries`, `lineups` et `statistics_fixtures` **tous a faux**.
Rattachee seule, elle n'aurait ramene que l'affiche, le lieu et les confrontations.

C'est le meme angle mort que celui repare par l'historique de saison du dossier d'equipe —
Motherwell compte 2 matchs de Conference League quand sa saison domestique en porte 47 —
pousse jusqu'aux agregats du bloc.

- **Le championnat de chaque equipe se lit chez le fournisseur** (`/leagues?team=`), jamais
  deduit d'un libelle, et sa couverture vient de la meme reponse — celle de la coupe ne dit
  rien de lui. **Un seul `type=League` en cours, sinon rien** : mesure sur neuf equipes, huit
  en portent exactement un — Bayern a six competitions et une seule ligue — la neuvieme deux,
  le fournisseur classant une supercoupe en `League`. Trancher a sa place attribuerait a une
  equipe les statistiques d'une autre competition.
- **Chaque agregat porte sa competition d'origine**, portee par le **nom de l'equipe** plutot
  que par dix fragments — dix endroits a modifier auraient fait dix occasions de diverger.
  Sans elle, « 1er » contre « 8e » se lit comme un match equilibre alors que l'ecart de
  division **est** le fait de la rencontre. Le preambule le dit une fois pour le lot, garde
  par `domestic_aggregates`.
- **La division se nomme meme quand la table est vide** (`_division_fragment`), et sans elle
  la regle s'inversait a la reprise : deux divisions n'entrent pas dans leur saison le meme
  jour, si bien qu'au 22/08 la 3. Liga a joue une journee et la Bundesliga zero — le club de
  D3 sortait classe et celui de Bundesliga pas du tout. Le bloc opposait un rang a un
  silence, exactement l'inverse de ce que la ligne doit montrer. Le rang reste tu, il ne
  classe rien a zero match ; la division est un fait a toute date.
  - Simulation sur le lot reel des 22-23/08, trois affiches :
    `Hansa Rostock (3. Liga) 7e (3pts, 1j, +1) | VfB Stuttgart (Bundesliga) — 0j jouée`, et
    `SC St. Tönis (Oberliga - Niederrhein) — 0j jouée | Eintracht Frankfurt (Bundesliga) —
    0j jouée`. La seconde ligne n'existait pas du tout avant, et c'est la plus decisive du
    bloc — un club d'Oberliga contre Francfort.
  - **Reservee aux equipes dont le championnat est etabli** : ailleurs un nom d'equipe seul
    serait du bruit, et la ligne `Agregats` dit deja pourquoi il manque.
- **`Enjeu` ne suit pas.** Un championnat declare « Relegation » ou « Play-offs » : ce n'est
  pas l'enjeu d'un tour de coupe, et le prompt batit des scenarios de motivation sur cette
  ligne. Le format — elimination directe, tour unique — releve de la fiche de competition.
- **Le cas non tranche produit un motif nomme** (`Agregats`) et non un silence : dix lignes
  disparaissent du bloc, et sans lui elles se liraient comme une equipe sans passe. Une seule
  ligne pour les dix, comme `Stats match` pour ses trois.
- **La regle se declare par competition** (`DOMESTIC_AGGREGATES`), elle ne se deduit pas de
  `coverage.standings` : ce drapeau decrit ce que le fournisseur **sert**, pas la nature de la
  competition, et s'en servir laisserait la couverture decider de la methode. Il embarquerait
  au passage la Conference League, qui l'annonce a faux et dont les blocs fonctionnent.
  L'extension reste une decision d'une ligne, auditable.
- Cout : un appel par equipe, memorise le temps d'un enrichissement, plus un `/standings` de
  plus quand les deux equipes ne partagent pas de ligue. `Enjeu` etant retire, la densite
  d'un bloc de coupe plafonne a 24/25 — sans effet sur le seuil de « bloc pauvre », qui est
  la moitie.
- Limite a connaitre : au 14/08/2026 aucun championnat n'avait joue cinq journees, donc les
  neuf lignes de `/teams/statistics` restaient muettes de toute facon. Ce que la regle apporte
  a cette date est le **classement**, c'est-a-dire l'ecart de division ; le reste arrive en
  octobre.

## Une colonne livree qui ne se reclame nulle part attend un prompt pour se voir

**Mesure du 27/08/2026, datee d'un jour.** `competitions.phase_de` est livree la
veille (migration 080) et la saisie n'a pas ete faite : les deux qualifications
de l'US Open portent `NULL` — 111 et 112 rencontres — quand leurs tableaux
principaux existent au catalogue et n'ont aucun evenement. Le tableau principal
entre dans les jours qui suivent, et sans le rattachement **six lignes se taisent
pour chaque qualifie** — `Repos`, `Parcours`, `Non joue`, `Fraicheur`, `Tour`,
`Ici`. C'est exactement ce que la 080 a ete ecrite pour empecher.

**Le chantier livre la colonne, la surface et le service ; ce qui manquait est la
reclamation.** La regle du projet existait deja pour les cles non classees et les
fiches absentes : ce qui manque doit se voir dans l'interface, pas se decouvrir
dans le prompt. Elle n'avait pas ete appliquee a celle-ci, et la meme omission se
reproduirait a Melbourne.

- **Le detecteur evident a ete mesure et refute, avant d'ecrire une ligne.**
  « Deux competitions qui partagent des joueurs et que rien ne relie » est le
  fait qui produit le degat, et il ne discrimine rien : sur les dix paires de
  competitions de tennis de la base, **92 a 94 % de joueurs communs entre deux
  tournois consecutifs ordinaires** — Canadian Open contre Cincinnati — contre
  **23 %** sur le cas cherche. Un seuil qui attraperait le second declencherait
  sur toutes les paires.
- **L'identifiant non plus, et il faut le savoir avant de le rechercher** :
  `tennisapi_tournament_id` est porte par la qualification (21349, 16743) et
  jamais par le tableau principal, qui entre par The Odds API et n'a pas besoin
  de ce chemin. Les deux ne partagent aucune valeur a comparer.
- **Le critere est la porte d'entree** : `fenetre_debut IS NOT NULL`. Par
  definition de la migration 078, une fenetre de rattachement decoupe un tournoi
  que le fournisseur sert entier — c'est le seul chemin qui pose la question.
  Rien n'est deduit d'un libelle, et un test monte une competition nommee
  « … Qualifications » sans fenetre pour verifier qu'elle n'est pas reclamee.
- **Trois etats, jamais deux** (migration 082). `phase_de IS NULL` confondait
  « pas encore repondu » et « ce n'est pas une phase » : Winston-Salem est un
  tournoi entier entre par le meme chemin, et la reclamation l'aurait nomme tous
  les jours sans qu'aucun geste puisse l'en retirer. Un signal qui ne peut pas
  s'eteindre devient du decor — le defaut exact que cette reclamation existe pour
  ne pas etre. `set_phase` ecrit `phase_repondue` dans ses **deux** branches,
  donc un seul ecrivain et un invariant par construction.
- Une competition **sans candidat** n'y figure pas : elle porte deja une phase,
  `set_phase` refuse la chaine, et reclamer une question sans reponse possible
  serait la faute du cyclisme dans les cles a classer.
- **Corollaire gratuit, et c'est l'argument pour le faire vite** : le statut dans
  le tableau — qualifie, wild card, lucky loser — que la checklist tennis fait
  chercher devient **derivable sans un appel** des que `phase_de` est pose. Un
  joueur du tableau principal ayant des apparitions sous la competition de
  qualification *est* un qualifie.

## Le report d'un horaire, et ce qu'il dit avec l'alerte meteo

**Le fait dominant d'une soiree peut etre un report**, et l'application
l'effacait a chaque scan : `commence_time` est ecrase, donc l'heure d'avant
n'existait nulle part. Mesure du 12/08/2026 a Cincinnati — 17:30 au releve de
12:42, 22:30 a celui de 22:15, soit cinq heures que le modele a du retrouver
dans la presse alors que les deux relevés etaient passes par ici.

- Migration 040, **deux colonnes et non une** : l'heure precedente dit *de
  combien*, l'instant du constat dit *quand nous l'avons vu*. Sans la seconde,
  un report vieux de trois jours se lirait comme celui de ce matin.
- Le seuil est celui de l'age d'un releve (`LEAD_TIME_MIN_MINUTES`, 15) plutot
  qu'un nombre invente a cote : c'est la meme question — a partir de quand un
  ecart de temps veut dire quelque chose — et deux reponses a une meme question
  finissent par diverger.
- La ligne se pose **sous l'en-tete**, parce que c'est l'heure qu'elle corrige,
  et reste un **signal** : rien ne s'ecrit quand l'horaire n'a pas bouge. Un
  report deja enregistre n'est pas efface par un scan qui ne bouge plus — le
  report a eu lieu, et c'est lui qui decrit la soiree.
- **L'alerte meteo et le report ne disent ensemble ce qu'aucun ne dit seul** :
  le programme a deja cede, il peut ceder encore. La ligne est en tete de
  section MATCHS et **ne parait que si les deux tiennent** — chacune a deja la
  sienne, et une conjonction a moitie declenchee rediraient ce qui suit.
  `weather.ALERT_MARK` est une constante et non un litteral recopie, meme regle
  que `NEUTRAL_MARK`.

## L'age d'un releve se compte vers le coup d'envoi, pas depuis maintenant

L'en-tete d'un bloc donnait l'heure du releve et rien d'autre : `releve 13:27`. La
soustraction restait a faire, donc personne ne la faisait — une journee entiere a ete
analysee sur des cotes relevees huit heures avant le coup d'envoi sans que rien ne le
signale.

- **C'est l'ecart qui compte, pas l'age absolu.** Huit heures sur un tour preliminaire
  obscur ne bougent rien ; les memes huit heures sur une affiche couvrent l'annonce des
  compositions. Un age nu (« il y a 8 h ») dirait la moitie de la chose.
- **Un seul moment est nomme, et c'est le seul qui se produise a heure connue** : la
  publication des onze, `LINEUP_LEAD_MINUTES` (60) avant le coup d'envoi. Mesure deja au
  dossier du projet — l'endpoint des compositions rend zero equipe a 2h30 du coup d'envoi
  et deux a 8 minutes. Football seulement : au tennis il n'y a pas de onze a publier.
- **Les deux seuils doivent rester distincts, et ils ne l'etaient pas.** Ecrits tous deux
  a soixante minutes, la mention des compositions accompagnait *toutes* les lignes rendues
  — elle cessait d'etre un signal pour devenir un decor. `LEAD_TIME_MIN_MINUTES` vaut donc
  15 : en dessous un releve n'a rien traverse, entre 15 et 60 l'ecart s'ecrit seul, au-dela
  il s'ecrit avec ce qu'il couvre. Trouve en ecrivant le test.
- Meme famille que l'age du releve meteo, et **le seuil s'y calait deja sur la fenetre de
  fraicheur du module** plutot que sur une constante inventee. Les deux lignes qui datent
  un releve parlent le meme langage : on ne compte le temps qu'une fois qu'il commence a
  vouloir dire quelque chose.

## Un titre de section se reconnait en tete de ligne, jamais a une mention

**Regle de revue, du 19/08/2026, et elle a coute deux sessions d'analyse.** Le
lecteur d'import basculait en section C-bis des qu'il voyait « C-bis » **n'importe
ou** dans une ligne. Or la section B en parle — « il part en C-bis », « voir
C-bis » — et le gabarit lui-meme en ecrit une. La bascule se produisait donc
**avant** le tableau de la section C, dont toutes les lignes etaient alors
refusees comme « exploratoires en palier sur ».

**Le collage complet rendait moins que le collage du seul tableau**, ce qui est
l'inverse exact de ce que le correctif d'interface de la veille visait. Mesure sur
les trois collages complets de la base : 3 selections sur 5 perdues, 4 sur 5, puis
5 sur 7 — et **zero bloc de confiance rattache** dans les trois cas, le compte de
blocs ne tombant plus sur le compte de lignes.

- Le motif s'ancre en tete de ligne, et les titres `A.` a `F.` **ferment** la
  section autant que son titre l'ouvre.
- **Les lettres seules se lisent sur la ligne brute et non repliee** : repliee,
  « C'est » commence par `c` suivi d'un espace, donc exactement comme
  « C. Tableau ». C'est le separateur (`.`, `)`, `:`) qui distingue un titre d'un
  debut de phrase francaise. `C-bis` n'a pas d'homonyme et se passe du sien.
- **Le banc de transport testait chaque format isole**, jamais le rendu complet ou
  la prose de la section B cotoie les deux tableaux. C'est ce qui a laisse passer
  le defaut, et le decoupage en sections y entre donc comme **sixieme format**.
  « Lu » s'y mesure sur les lignes de la section C et non sur leur total — les
  compter toutes aurait rendu le banc vert pendant la panne.

## Un test qui mesure un service ne dit rien de la surface qui le rend

**Regle de revue, du 20/08/2026, et elle a coute cinq collages complets.** Le
lecteur d'import lisait correctement un rendu entier — 5 a 7 selections, autant
de blocs `conf`, la ligne `dossiers_ouverts` — et le formulaire d'import ne
s'affichait pas. Sur les 35 collages archives, les cinq complets sont les
**seuls** que l'application refusait ; les trente collages du seul tableau
passaient tous. Le message affiche a la place envoyait recoller la section C
seule, c'est-a-dire le geste qui coute les crans du lot entier.

- **La cause tient en un nom** : `parse_table` employait `columns` pour deux
  notions — l'entete du tableau **en cours**, remis a zero par chaque titre de
  section, et le fait global « un tableau a-t-il ete reconnu ». Un rendu complet
  finit par `F.`, donc `columns is None` y est vrai **par construction**. Le
  refus se prononce desormais sur ce qui a ete lu, jamais sur un etat de
  section.
- **Le defaut est ne du correctif de l'autre moitie de la meme fonction**
  (`a75da0d`, 19/08 18:11), celui qui empechait une mention de « C-bis » en
  prose de faire basculer la lecture. L'import qui precede de douze minutes
  passe, les deux suivants echouent. Corriger une fonction sans rejouer
  **l'ensemble de son parcours** deplace le defaut au lieu de le retirer.
- **Et le banc ecrit pour ce defaut-la est reste vert.**
  `tests/test_collage_complet.py` tournait sur le collage reel, comptait chaque
  objet exactement, et tous ses comptes etaient justes. Il n'assertait ni
  `preview.ignored`, ni le rendu de la route. **Un banc qui mesure le lecteur ne
  voit pas un defaut dans la porte.**
- La regle generale, et `CONTRIBUTING.md` n'en portait que la moitie : « le
  service et sa surface se livrent ensemble » visait un service qui **accepte**
  une valeur que rien ne permet de saisir — le motif de saisie tardive, reste
  sans surface deux jours. Ici c'est l'inverse : un service qui **produit** une
  valeur que rien ne permet de valider. Les deux se testent d'une seule facon —
  **poster le formulaire rendu et relire la base**.

**Le garde-fou qui en sort, et il vaut au-dela de ce cas** : `ImportPreview.ignored`
cache tout le formulaire, donc il n'a qu'un sens legitime — *il n'y a rien a
montrer*. `_unreadable` est desormais le seul chemin vers lui, et une remarque
posee sur un apercu qui porte des selections descend dans `notes`, qui affiche
sans empecher d'importer. La regle « un collage complet ne peut pas etre plus
difficile a importer qu'un collage partiel » tient par une fonction plutot que
par la vigilance.

**Corollaire de plage** : `SECTION_HEAD` s'arretait a `[A-F]` quand la section G
etait produite depuis un lot. Un titre `G.` ne fermait donc aucune section. La
plage suit le gabarit, et un test compare les deux — une section H ajoutee
demain se solde par un test rouge et non par un silence.

## Un champ dont le nom evoque une date peut etre un entier

**Regle de revue, meme jour, et c'est la soeur de « cherchez l'identifiant ».**
`result.startTimestamp` de `tennis-api.com` vaut `1780565400`. Il etait lu
`str(value)[:10]`, ce qui rend dix **chiffres** — une chaine qui ressemble a une
date par sa longueur et n'en est pas une.

Le degat etait total et invisible : `_store_player` rapproche les jeux de leur
surface **par cette date**, aucun rapprochement ne tombait, et les jeux
n'atteignaient donc que l'agregat toutes surfaces — seul cas ou le filtre est
court-circuite. La ligne `Jeux` etait **inatteignable sur tous les blocs**, y
compris pour les joueurs qui venaient d'atteindre le seuil de 300 jeux, et son
absence sous le seuil est son comportement normal. Sixieme occurrence du motif du
projet.

- **Le correctif qui compte n'est pas la conversion**, c'est la suppression du
  rapprochement par date : `collect_games` sait quelle ligne de service a demande
  quelle timeline, et la surface est desormais **portee** par le jeu. Rapprocher
  deux flux par une date reste faux meme apres reparation, la timeline se trouvant
  parfois a `J-1`.
- La fixture du lot 4 portait deja la valeur reelle : **un test sur la seule
  fonction de lecture aurait suffi**, et il n'existait pas.
- **Second blocage sous le premier, et structurel** : `collect_games` s'arrete a
  300 jeux **toutes surfaces confondues**, donc aucun agregat par surface ne peut
  atteindre ce seuil — zero ligne au-dessus de 300 par surface sur la base servie,
  maximum 225. Le seuil ne bouge pas ; c'est la **portee** qui se replie sur toutes
  surfaces, **en le declarant**, comme `fell_back` le fait deja pour les points de
  service.

## Un zero sur un rapprochement de joueurs est un defaut d'appariement

**Regle de revue, du 20/08/2026, et elle a ete apprise cinq fois en une
semaine.** Un taux de 0 % sur un rapprochement de noms est **parfaitement
credible** — la source ne couvre pas ce joueur, l'historique ne remonte pas
assez loin — et c'est exactement ce qui le rend dangereux : il se rapporte sans
qu'on le verifie, et il ferme un chantier.

| Ou | Ce que le zero disait | Ce qu'il etait |
| --- | --- | --- |
| Fernandez, lot 5 | profil vide chez le fournisseur | un **doublon** portait les 452 matchs |
| Andreescu, lot 9 | joueuse absente de la source | la source ecrit « Bianca Vanessa Andreescu » |
| recuperabilite tennis, lot 15 | **0 sur 127**, source inexploitable | `tennis_matches` ecrit « Mensik J. », `events` ecrit « Alex Michelsen » — corrige a **65,8 %** |
| palmares par edition, lot 16 | **0 edition sur 589 matchs** | le **champ** ne s'appelle pas pareil : `result` chez `matches-played`, `score` chez `event/get`, et les sets s'y separent par des espaces |
| tournois rattaches, lot 17 | **0 joueur sur 261** avec un passe ici | le nom du joueur est l'**avant-dernier** segment du chemin, pas le dernier |

Le troisieme aurait **ferme le tennis pour de bon** : un « 0 % » sur la
recuperabilite des resultats se serait lu comme l'absence de toute source, et le
reglement automatique n'aurait jamais ete construit.

**La regle** : un taux de 0 % sur un rapprochement de joueurs est un defaut
d'appariement **jusqu'a preuve du contraire**, et la preuve se fait avant de le
rapporter. Le repli progressif est deja en place et se parcourt en entier —
casse, accents, tirets, **ordre des noms** (prenom devant chez l'un, nom de
famille devant chez l'autre), initiales, decoupages multiples.

- **Le zero ne se rapporte qu'apres**, et il se rapporte alors avec le repli qui
  a ete tente. « 0 sur 127 » ne dit rien ; « 0 sur 127, replis casse, accents et
  ordre des noms epuises » est un resultat.
- **Chaque source a sa fonction de nom, et elles ne se partagent pas.** Le nom de
  famille est le **dernier** mot chez `events` (`Alex Michelsen`) et le
  **premier** chez `tennis_matches` (`Mensik J.`). Une fonction unique appliquee
  aux deux compare « alex » a « mensik » — c'est litteralement le defaut du
  lot 15.
- **Le corollaire vaut au-dela des joueurs** : le lot 16 a mesure 94,1 % la ou il
  y avait 99,75 %, parce que la cle de rapprochement omettait la **date**. Deux
  rencontres du meme couple s'ecrasaient. Un taux qui surprend se re-verifie sur
  sa cle avant d'etre ecrit.
- **Et le controle le moins cher qui existe : un denominateur identique sur des
  axes independants est une signature de population, jamais une propriete.**
  Mesure du 27/08/2026 — « non renseigne » sortait en tete de trois axes du cran
  3, avec le meme `12/41` et le meme residu `-11,12`. Trois axes qui ne
  partagent rien ne peuvent pas designer le meme effectif par hasard : c'etait
  **la meme population**, celle d'avant la migration 026, donc une colonne jeune
  et non un discriminant. Le controle tient en une comparaison de comptes, et il
  aurait attrape plusieurs des faux discriminants du dossier.
- **Et il vaut au-dela des noms : ce n'est pas une regle d'appariement, c'est une
  regle de lecture.** Les deux dernieres occurrences ne portent sur aucun nom.
  Le lot 16 lisait le mauvais **champ** — `score` la ou la source ecrit
  `result`, avec des espaces au lieu de virgules — et rendait un palmares vide
  sur 589 matchs. Le lot 17 lisait le mauvais **segment** d'un chemin,
  `/profile/<nom>/matches-played` : il prenait le dernier, donc
  « matches-played » pour tout le monde, et rendait 0 joueur sur 261. Les trois
  causes — nom, champ, indice — produisent le meme zero credible, et il n'existe
  aucun moyen de les distinguer **du zero lui-meme**.
- **Ce qui les distingue est le denominateur.** « 0 sur 261 » n'est pas
  suspect ; « 0 sur 261 alors qu'une seule cle a ete construite » l'est. Compter
  ce qui **entre** dans le rapprochement, et pas seulement ce qui en sort, est ce
  qui a fait tomber la derniere occurrence en dix secondes : « 261 profils
  archives » contre « 1 profil » dans le premier jet.

### Elle vaut contre son auteur, et ce qui l'attrape est le denominateur

**Trois mesures fausses dans un seul lot, le 28/08/2026, toutes de moi et non
d'une source.** Aucune n'etait une erreur de calcul :

| Ce qui etait annonce | La cle qui manquait |
| --- | --- |
| 40 % de divergence entre `Tour` et `Ici` | 16 blocs **nommaient la phase**, ou le compte est omis a dessein |
| 22 joueurs « rendus muets » par la ligne | 6 venaient de prompts **anterieurs a la livraison** de la mention |
| 18 joueurs « encore muets aujourd'hui » | l'appariement portait sur `(joueurs)` seuls, et Swiatek — Rybakina designe **deux editions** |
| 2 blocs « question manquante » | c'etaient des **entrees en lice**, donc un fait et non un trou |

Le chiffre juste etait **10 sur 57**, et il n'aurait pas suffi a justifier le
correctif : c'est son **effet** — 5 blocs sur 31 dont l'asymetrie se retourne —
qui l'a fait. Un taux ne justifie rien tant qu'on n'a pas dit ce qu'il change.

- **Ce qui les a attrapees est a chaque fois le denominateur**, jamais le
  numerateur : sur quelle population le taux porte, et cette population est-elle
  homogene. Compter ce qui **entre** dans un rapprochement, et pas seulement ce
  qui en sort.
- **Une fonctionnalite livree en cours de periode coupe le corpus en deux**, et
  la date de son commit est la borne. Le corpus archive porte plusieurs regimes,
  et un rejeu qui les melange decrit une population qui n'a jamais existe.
- **Un rejeu dit ce qu'un defaut produirait aujourd'hui, jamais ce qu'il a
  produit.** Les deux nombres se rapportent cote a cote ou pas du tout : sur le
  releve perime, 2 fragments sur le corpus archive contre 1 bloc au rejeu du
  jour, les profils ayant ete rafraichis entre-temps.

## Avant de coder une heuristique sur des libelles, cherchez l'identifiant

**Regle de revue, tiree de trois defauts de la meme famille.** Le handicap qui semblait mal
signe, le walkover compte comme un match joue, le repos calcule en dates de Paris, le
« hors de København » : a chaque fois ce n'est pas le sport qui etait mal modelise, c'est
**notre lecture de la charge utile** — un artefact de collecte pris pour une propriete du
monde.

Le cas le plus net est le lieu. Le `venue` d'un match **porte un identifiant** ; un
commentaire du code affirmait le contraire, et toute l'heuristique nom + ville en
decoulait, faux positifs compris. Personne n'avait relu la charge utile depuis.

Donc, avant d'ecrire une comparaison de chaines : ouvrir une reponse reelle et chercher
l'identifiant. S'il existe, c'est lui. S'il n'existe pas, l'ecrire dans le commentaire —
avec la date de la verification.

**Et la version la plus couteuse de cette regle : la donnee est parfois deja la, calculee
et rendue, et c'est le code qui ne s'en sert pas.** Le bloc du 12/08 se contredisait a
quatre lignes d'ecart — `Tour : 32e de finale`, puis `le debut du tableau nous echappe`.
`truncated()` produisait le signal qui demontait l'etiquette, et rien ne l'opposait a
elle. Il n'y avait aucune information a aller chercher : il y avait deux sorties du meme
module qui ne se parlaient pas. Le lecteur non plus ne l'a pas vu — un diagnostic entier
a ete bati sur la premiere ligne alors que la seconde la dementait deja.

Donc, quand une ligne parait fausse : chercher d'abord si le bloc ne porte pas **deja**
le fait qui la contredit. Deux sorties d'un meme calcul doivent etre opposees l'une a
l'autre dans le code, jamais laissees a l'oeil.

## Quand une information n'est pas servie, chercher ce que la question voulait savoir

**Troisieme fois que la mesure fait mieux que la specification, et c'est un motif.** La
demande etait : « la ligne `Tour` doit dire qu'on est en qualification, nommer la phase,
le tour dans cette phase, et ce que le vainqueur obtient ». Aucune source ne sert la
phase — verifie, six champs pour zero credit. La reponse litterale aurait donc ete un
silence, ou pire une deduction inventee.

Ce qui a marche est d'avoir repris la **question** plutot que la source : ce qu'il fallait
savoir n'etait pas « qualification, tour final », c'etait **ou en est ce joueur dans ce
tournoi ce soir**. `Fraicheur` y repond directement — `entre en lice` contre `1 non
compte` separe les entrants de ceux qui ont deja joue le jour meme — sans nommer quoi que
ce soit d'invérifiable. Le vocabulaire de la phase etait une hypothese sur le chemin, pas
le besoin.

Les deux precedents sont de la meme forme : l'historique de cartons par arbitre, remplace
par le nom seul qui epargne une requete sur deux ; le score du tour precedent au tennis,
introuvable en automatique, remplace par la demande explicite en tete de fiche de
recherche. Dans les trois cas, une source manquante a ete traitee comme une question mal
posee plutot que comme un trou a combler.

## Tout ajout au preambule budgete sa propre coupe

Le socle commun sature : le lot de reference football tient a **7 tokens** de son plafond,
et le lot mixte a franchi le sien pendant ce chantier (10 086 pour 10 000). Le plafond n'a
pas bouge — c'est la regle — mais la coupe a ete trouvee **apres coup**, trois fois de
suite : sur `Repos` ce soir, sur `Arbitre` / `Meteo` / `Lieu` avant. Autant que ce soit la
procedure.

- **La coupe se mesure avant d'ecrire**, pas quand le test casse, et elle se prend dans le
  **plus ancien mode d'emploi de la meme famille** — celui qui a eu le plus d'occasions
  d'accumuler de l'explication.
- **La frontiere est fine, et deux garde-fous l'ont prouvee** en mordant sur le premier
  resserrement : « journees de tournoi et non dates civiles » et « en parallele et non a
  la place » ressemblent a des explications et sont des **conventions de lecture**. Leur
  perte ne casse rien — elle fait lire une ligne de travers, ce qui est pire.
- D'ou la question a se poser ligne par ligne : **est-ce que ceci change ce que le lecteur
  fait de la donnee** ? Si oui, ca reste, meme long. Si ca explique seulement *pourquoi*
  la donnee est ainsi, ca descend ici.

**Le plafond ne mord plus, et le motif de cette regle a change — 27/08/2026.** Elle est
nee d'une marge de **7 tokens** ; le lot de reference mesure aujourd'hui **15 915 tokens
pour 23 000 permis**, soit sept mille de marge, parce que les deux plafonds ont ete recales
sur une fixture qui mesurait faux. Continuer a couper « parce que le plafond serre » serait
justifier une bonne pratique par une raison fausse — et une raison fausse finit par etre
testee, puis par tomber en emportant la pratique.

- **Le motif qui tient seul est la densite**, et il n'a jamais dependu du plafond : une
  ligne ajoutee se paie deux fois, la donnee dans chaque bloc et son mode d'emploi en tete,
  et un mode d'emploi qui gonfle cesse d'etre lu bien avant de couter un token de trop.
- **La procedure ne bouge pas** : la coupe se mesure avant d'ecrire, dans le plus ancien
  mode d'emploi de la meme famille, et la question ligne par ligne reste la meme. C'est sa
  **justification** qui est corrigee, pas son contenu.
- Les deux plafonds restent ce qu'ils sont — des alarmes contre une explosion
  **involontaire**, une porte de preambule cassee, un bloc duplique — et non un budget a
  arbitrer ligne a ligne. Ils l'ont ete pendant trois sessions, et c'est ce regime-la qui
  n'existe plus.

## Un critere se valide contre le corpus, jamais contre une fixture

**Regle de revue, du 27/08/2026, et les deux moities du meme lot la fondent.** Un critere
de priorite se juge sur une seule chose : **quelle part du corpus il designe**. Une fixture
ne peut pas repondre a cette question — elle porte l'etat qu'on lui a donne — et un test
vert ne dit donc rien du fait qu'un critere discrimine.

| Ce qui a ete ecrit | Ce que la fixture disait | Ce que le corpus disait |
| --- | --- | --- |
| `_dense("tennis")` comme bloc « pleinement servi » | un bloc dense | ni `Ici` ni `Service`, rendues sur **79 %** des blocs reels |
| `SHORT_REST_HOURS = 24` | le test passait | **27 %** de declenchement, sur le rythme normal d'un tournoi |

- **Le seuil de repos est le cas d'ecole.** La distribution des 48 blocs porte un pic net a
  **23 h, 9 blocs** : c'est le retour de la meme session la veille, donc l'ordinaire. Un
  seuil pose a 24 h englobe le mode et se declenche sur un quart du lot — exactement ce
  qu'on reproche aux deux criteres faibles du football. Pose **sous** le mode, a 23 h, il
  designe 8 % et l'ecart veut alors dire autre chose : le joueur a joue **plus tard hier
  qu'il ne joue aujourd'hui**.
- **Un seuil se pose sous un mode, jamais dessus ni dedans.** Un mode est le comportement
  ordinaire de la population ; un seuil qui le traverse compte du bruit de mesure, et un
  seuil qui l'englobe compte l'ordinaire.
- **Le rejeu sur le corpus est ce qui attrape ces deux-la, et il est bon marche** : les
  blocs rendus sont archives dans `prompts.body`, les fonctions de critere sont pures, et
  les passer l'une sur l'autre tient en trente lignes. C'est ce qui a montre les 13 blocs
  la ou une lecture a la main en comptait 6 — elle ne captait que le premier joueur de la
  ligne.
- **Corollaire de test** : un test de critere garde une **propriete** — celui-ci se
  declenche, celui-la non — et le taux, lui, se mesure sur le corpus et se **date** dans le
  commentaire du seuil. Ecrire le taux dans une assertion la ferait decrire la fixture.

## Un drapeau booleen ne se construit pas sur un champ dont on a mesure qu'il ment

**Regle de revue, de la meme famille que « cherchez l'identifiant » et tiree du meme
chantier.** Celle-la dit ou prendre la donnee ; celle-ci dit **quelle forme** lui donner
quand on a mesure qu'elle se trompe parfois.

Le cas qui la fonde : `fixture.venue.city` dit « Vitebsk » pour un match joue a
Mezokovesd, en Hongrie. Le champ existe, il est structure, il est juste sur 7 relevés sur
8 — et il ment sur le huitieme.

- **Un booleen calcule dessus affirme.** `TERRAIN NEUTRE` absent se lit « domicile », et
  ce serait dit avec la meme autorite sur les sept justes et sur le faux. La ou le champ
  ment, l'outil mentirait a sa place, sans laisser de trace.
- **Une mention textuelle expose la donnee brute.** `Mezokovesdi Varosi Stadion, Vitebsk
  (BLR) — pas d'identifiant de stade ici, terrain neutre non verifiable` porte le nom
  hongrois du stade, la ville belarusse et l'aveu que la comparaison manque : c'est la
  contradiction elle-meme qui fait tiquer, et l'arbitrage revient au lecteur.
- **Le taux d'erreur ne decide pas de la forme, la visibilite de l'erreur si.** Un champ
  faux une fois sur huit reste utilisable — a condition que sa sortie soit lisible. Un
  booleen ne l'est jamais : il n'a pas de place ou mettre son doute.
- Corollaire pour ce qui reste a faire : c'est **cette** regle qui bloque le drapeau de
  terrain neutre, et non le cout d'une table `teams` qui n'a jamais ete necessaire. Elle
  se levera par une source de lieu qu'on puisse gager, pas par une donnee de plus.

## Le marche « Se qualifie »

Vingt-quatre manches retour en une semaine, et le marche qui traduit le mieux un tour a
elimination directe n'existait **nulle part** : ni en cote, ni meme en « Non servis ».
C'est l'angle mort exact que le prompt reserve a sa section F — un marche qu'on ne peut ni
jouer ni declarer absent.

- Le fournisseur le sert (`to_qualify`), et c'est la seule raison pour laquelle ce chantier
  livre un marche plutot qu'une ligne « Non servis ». **La verification passait avant le
  code** : le resultat probable etait negatif, et une ligne `Non servis` correcte aurait ete
  un vrai livrable.
- **Demande sur les seules coupes**, lues sur `KNOCKOUT_CATEGORIES` — donc sur le **niveau**
  deja saisi, jamais sur un libelle. Un niveau non renseigne rend faux : un doute ne se paie
  pas. Ailleurs le marche ne serait jamais servi, et le reclamer couterait un credit par
  match pour un constat vide — que `coverage` memoriserait ensuite, mais apres l'avoir paye.
- Sur un tour **aller simple**, il ne sera pas servi non plus, et c'est tres bien : l'absence
  devient une ligne « Non servis », ce qui est precisement ce qui manquait.
- Famille `issue` : « Se qualifie » et le 1N2 d'une manche retour repondent a la meme
  question — qui gagne — sur deux perimetres, le tour et le match. Les separer aurait coupe
  en deux un echantillon deja court.
- **La table des familles se seede par une nouvelle migration** (039) et non en modifiant la
  027 : une migration deja appliquee ne se rejoue pas, et les installations existantes
  seraient restees sans l'entree. Le test de parite lit desormais **toutes** les migrations
  qui touchent `market_families`, ce qui est aussi ce qui empeche d'oublier d'en ecrire une.
- **Branche des deux cotes**, et il a fallu le verifier pour le savoir : `/odds/bets` liste
  338 paris, dont « To Qualify » (id 61). Verifie le 12/08/2026, pour une unite de quota.
  Sans cette entree, le marche etait ajoute **exactement la ou il ne pouvait pas arriver** —
  un lot de 21 manches retour servi integralement par Superbet et Bet365 de substitution
  n'en aurait vu aucune cote, et le bloc ne l'aurait pas dit : « Non servis » ne se construit
  que sur ce que l'outil demande a The Odds API. Meme piege que les props buteurs et que les
  trois marches ajoutes a la table avant elles.

## La meteo : l'alerte d'abord, les chiffres ensuite

Mesure qui fixe cet ordre, sur cinq sessions reelles : **la temperature n'a jamais rien
change**. L'alerte, si — deux fois, parce qu'elle disait que la rencontre pouvait ne pas se
jouer. Un chiffre interessant et un fait bloquant ne se lisent pas au meme rang, et le
module est construit dans cet ordre-la.

- **Trois etats d'alerte, et le troisieme est celui qui compte.** « aucune alerte NWS en
  vigueur » dit qu'on a regarde ; « alertes officielles non interrogees (Bulgaria) » dit
  que **personne n'a regarde** ; « alertes NWS injoignables » dit que la source n'a pas
  repondu. Les confondre reproduirait exactement le defaut d'`Absents : donnees non
  disponibles`, qui rendait « aucun absent » sur une competition non couverte — un silence
  qui ressemble a une information.
- **Un seul service national est branche** (`ALERT_SOURCES`), et c'est une limite assumee :
  chaque pays a le sien, et il n'existe pas d'agregateur qui soit lui-meme l'instance —
  MeteoAlarm agrege l'Europe mais n'emet rien. **L'emetteur est recopie de la charge utile**
  (« NWS Wilmington OH »), jamais devine : c'est lui qui fait la source de niveau 1.
- **Les chiffres valent a l'heure du coup d'envoi**, pas pour la journee : l'orage de
  l'apres-midi peut etre passe. Et ils portent **l'heure de leur releve**, comme l'en-tete
  des cotes — une prevision de huit heures plus tot n'engage pas grand-chose sur un orage.
- **Le pays departage les homonymes du geocodage**, et en cas de doute il n'y a pas de
  ligne : « Mason » existe en Ohio, dans le Nebraska et en Angleterre, et la meteo de la
  mauvaise ville serait une erreur **invisible** — le genre le plus couteux.
- **Deux chemins, un seul service.** Au football la ville vient du lieu du match, deja
  persiste — donc le stade reel, delocalisation comprise. Au tennis elle vient de
  `competitions.city` (migration 038), saisie a la main : aucun fournisseur ne sert le lieu
  d'un tournoi, et « ATP Cincinnati Open » se joue a **Mason**. Sans cette colonne, les deux
  cas ou la meteo a reellement change une analyse — un tournoi de tennis et une soiree de
  coupe — n'auraient ete couverts qu'a moitie.
  - Le geocodage rend **le fuseau du lieu** dans la meme reponse : une ville saisie recoupe
    gratuitement `competitions.timezone`, et c'est ce fuseau-la que la ligne emploie — le
    seul qui soit certainement celui du stade.
  - **Le pays servi au geocodage etait celui du club a defaut d'autre chose, et une soiree
    de coupe d'Europe en sortait sans meteo** : chercher « Miskolc » en Israel ne rend
    rien, donc aucune ligne, precisement la ou le lieu n'est pas celui qu'on croit. Trois
    sources desormais, dans l'ordre de ce que chacune prouve — le stade identifie chez le
    fournisseur, la ville geocodee, puis le club. Le meme appel repare les deux : la ligne
    `Lieu` y gagne son pays, la meteo sa ville. Le chemin n'avait **aucun test**.
- Peremption a `TTL_HOURS` (3) : au-dela, une prevision d'orage a eu le temps de se preciser
  ou de se dissiper, et c'est justement le cas ou elle decide. **Le geocodage, lui, ne
  perime jamais** — une ville ne bouge pas, et c'est le seul des trois appels qui se garde.
- Gratuit, sans cle, sans quota : **rien dans `api_usage`**, meme regle que l'Elo tennis et
  l'historique des matchs. La meteo passe donc **hors du garde-fou de credit**, apres le
  contexte dont elle tire les coordonnees.

**Sur les `robots.txt`, et c'est une decision a connaitre.** `api.weather.gov` et
`api.open-meteo.com` servent tous deux `User-agent: * / Disallow: /`. Ce sont des **APIs
publiques documentees**, dont les conditions autorisent explicitement l'acces
programmatique — « all of the information presented via the API is intended to be open
data, free to use for any purpose » cote NWS, acces programmatique non commercial sous
CC-BY 4.0 cote Open-Meteo. Le `Disallow: /` d'un hote d'API ecarte les robots
d'indexation ; il ne s'adresse pas a un client d'API, et **ni l'un ni l'autre ne nomme
d'agent ni ne porte de reserve de droits**. C'est ce qui les distingue d'`atptour.com`, qui
interdit `ClaudeBot` nommement et porte un `Content-Signal: ai-train=no` — refuse, et qui
doit le rester.
  - Deux obligations en decoulent, tenues dans le code : le NWS **exige** un `User-Agent`
    qui identifie l'appelant avec un contact (`WEATHER_CONTACT`, vide par defaut, jamais
    code en dur) ; Open-Meteo demande l'**attribution** CC-BY, d'ou son nom rendu dans la
    ligne et pas seulement dans les logs.

## Le lieu, et ce que « domicile » suppose

**Le faux positif a coute plus que l'absence de ligne.** La comparaison portait sur deux
chaines — nom de stade et ville — et devait voir les deux differer. Elle a produit
« Parken Stadium, Copenhagen — hors de København » : une delocalisation annoncee entre deux
orthographes de la meme ville. La ligne a fini par etre ignoree, c'est-a-dire l'inverse de
son but.

- **Sur les identifiants, jamais sur les libelles.** Le `venue` d'un match **a** un
  identifiant ; le commentaire qui affirmait le contraire datait d'une lecture trop rapide
  de la charge utile, et toute l'heuristique nom + ville en decoulait. Deux identifiants
  connus et differents sont un fait ; tout le reste est une inconnue.
- **Neutre veut dire hors du pays du club, pas hors de son stade.** Un club deplace pour
  travaux ou sanction reste chez lui — le public suit. C'est la difference entre une
  contrainte logistique et une contrainte politique ou securitaire, et seule la seconde
  change la lecture. Trois cas d'une meme semaine relevent de la seconde : Dinamo Minsk au
  Stadion Beroe (Bulgarie), ML Vitebsk a Mezokovesd (Hongrie), Hapoel Tel-Aviv a Miskolc.
- **Le pays vient d'une donnee structuree** (`/venues`), jamais d'un nom de ville :
  « Ploiesti » ne dit pas « Roumanie » a une machine. L'appel n'est emis **que sur une
  rencontre effectivement deplacee** — le stade habituel n'a pas besoin d'etre situe, on
  sait deja que le club y est chez lui. Quelques appels par lot, et un test verifie qu'un
  match a domicile n'en declenche aucun.
- **Quatre etats et non un booleen**, et les deux derniers comptent autant que les deux
  premiers : un domicile **suppose** qui n'en est pas serait pire qu'un « non renseigne »
  franc. Meme regle que le fuseau du lieu.
- **Le drapeau est structurellement muet sur les competitions UEFA, et c'est mesure.**
  `fixture.venue.id` est nul sur **210 matchs sur 210** d'une saison de Conference League,
  et servi sur **380 sur 380** d'une saison de Premier League — verifie le 12/08/2026. La
  comparaison d'identifiants est donc hors de portee exactement la ou les delocalisations
  arrivent : Minsk en Bulgarie, Vitebsk en Hongrie, Hapoel a Miskolc, Kyiv a Lublin.
  - Rendre « donnees non disponibles » y jetait le **nom du stade et sa ville**, que le
    fournisseur sert pourtant. D'ou `VENUE_UNIDENTIFIED` : le lieu est ecrit, suivi de
    « pas d'identifiant de stade ici, terrain neutre non verifiable ». C'est strictement
    plus informatif qu'un silence — un club israelien qui « recoit » a Miskolc se lit sans
    qu'aucun drapeau soit calcule, ce que l'utilisateur avait lui-meme observe sur Lublin.
  - **Le pays de la ville, lui, se geocode** (`_geocoded_country`), et c'est la moitie
    manquante : gratuit, sans cle, sans quota, et sans identifiant a exiger. La ligne
    devient `DVTK Stadion, Miskolc (HUN) — pas d'identifiant de stade ici, terrain neutre
    non verifiable`. Le pays est un **fait sur la ville**, la mention qui suit dit que la
    comparaison reste hors de portee : c'est elle, et non le pays, qui interdit de conclure.
    - **Un nom de ville nu ne suffit pas, et deux mesures le prouvent.** Sur les 128 villes
      de stade de la base, le plus peuple des homonymes donne l'**Allemagne** au Club
      Bruges — « Brügge », 1 019 habitants, quand Bruges vit sous un autre nom chez le
      geocodeur — et l'Allemagne encore au SV Ried, autrichien, dont les 87 homonymes
      exacts placent Ried im Innkreis hors des dix premiers. C'est exactement le faux
      positif qui avait fait ignorer la ligne.
    - **Deux temps, et l'ordre porte la regle.** Un homonyme dans le pays du club emporte
      la decision — cas ordinaire, et il rattrape Ried ; sinon on affirme que le match se
      joue a l'etranger, ce qui demande une ville de plus de 20 000 habitants et dix fois
      plus peuplee que son premier homonyme. Mesure : les cinq delocalisations connues
      visent des villes de 121 000 a 336 000 habitants, seules ou 660 fois plus peuplees
      que leur homonyme suivant. Deux ordres de grandeur separent les vrais cas des faux
      de chaque seuil.
    - **La preference au pays du club ne cache aucune delocalisation**, et c'est mesure
      plutot que suppose : aucune des cinq n'a d'homonyme dans le pays de son club.
    - Rejeu de la regle livree sur les releves reels : **113 des 128 villes situees**, 15
      tues — les traps, les egalites (Dundee, Barcelos, Geneva a 9,2 fois) et les villes
      trop petites. Avec le pays du club, 11 cas connus sur 13 justes, un tu, un faux.
    - **Le seul faux est un libelle du fournisseur**, et aucune regle ne le rattrape : ML
      Vitebsk recevait a Mezokovesd, en Hongrie, sous un `city` qui dit « Vitebsk ». Le
      pays rendu est donc le Belarus. Le nom du stade, hongrois, et la mention « non
      verifiable » sont ce qui reste pour le voir — et c'est ce que la ligne disait deja.
    - **Les deux vocabulaires de pays divergent** et il faut les rapprocher a la main :
      l'article des Pays-Bas, et les quatre nations britanniques qu'API-Football compte
      pour des pays quand Open-Meteo dit « United Kingdom ». Sans `HOME_NATIONS`, Dundee
      et Motherwell n'ont aucun candidat chez eux et passent par la branche des
      delocalisations.
  - **Le pays du club ne demande aucune saisie, et le drapeau reste bloque quand meme.**
    `team.country` arrive dans le `/teams` deja appele pour le stade habituel, et
    `home_country` le persiste : la table `teams` et sa passe de saisie par club ne
    conditionnaient rien. Le blocage est ailleurs, et il est **mesure** : un drapeau
    `TERRAIN NEUTRE` calcule sur `fixture.venue.city` dirait « domicile » sur ML Vitebsk
    avec autorite, la ou la mention actuelle laisse voir la contradiction. Voir la regle
    de revue plus bas — un drapeau booleen ne se construit pas sur un champ dont on a
    mesure qu'il ment. Ce qui manque n'est donc pas une donnee de plus mais une source de
    lieu qu'on puisse gager ; le jour ou elle existe, le drapeau touche quatre
    consommateurs (`Lieu`, `Aller`, `Scenario`, la meteo).
  - **Les 164 releves `venue` de la base datent d'avant `venue_id` et `home_country`**, et
    rien ne les reprend. Consequence a connaitre avant une session plutot que dedans : sur
    un lot monte a partir d'eux, le pays sort de la **seule** regle de population, sans le
    garde-fou du pays du club. Les cinq delocalisations connues passent — aucune n'a
    d'homonyme chez elle — mais le filet est absent tant que rien n'a ete reenrichi.
    Aucune migration : elle rattraperait un historique que personne ne relira, et le
    prochain enrichissement ecrit les deux champs tout seul. Meme arbitrage que partout
    ailleurs — une donnee sans lecteur ne se collecte pas.
- **La ligne est devenue systematique**, et le calcul qui la reservait a la surprise etait
  faux dans l'autre sens : son absence ne se distinguait pas d'un domicile ordinaire, si
  bien qu'un match delocalise dont le lieu n'avait pas ete recupere passait pour un match
  chez soi. Elle a donc rejoint `CONTEXT_EXPECTED` — l'exclure sous-estimait la densite
  d'un bloc complet — et le denominateur football passe de 24 a 25.
- **Trois consommateurs, un seul calcul** (`venue_state`) : la ligne `Lieu`, la mention de
  `Scenario` — `nominalement a domicile, terrain neutre`, sans quoi le mot « domicile »
  inverse le sens de la phrase — et le critere de la fiche de recherche. Le marqueur rendu
  est une constante (`NEUTRAL_MARK`) et non un litteral recopie : la fiche le relit.
- **Ce qui n'a pas ete construit** : aucune table `teams`. Le pays vient du fournisseur,
  donc P1-4 n'a besoin d'aucune saisie ; creer la table pour y poser `site_url` et un stade
  en attendant un lecteur serait exactement la faute de `/players/squads`, collecte des
  mois sans personne pour la lire et retiree par la migration 022. Elle se creera le jour
  ou P1-1a en aura besoin — avec ses deux colonnes d'un coup, ce qui donne la meme passe de
  saisie unique.
- Limite connue : les **coordonnees** demandees a l'origine ne sont servies par aucun de nos
  endpoints. Stade, ville et pays le sont ; le reste aurait ete invente.

## Le tennis n'avait pas une conduite de recherche plus faible, il n'en avait pas

**Mesure du 27/08/2026 sur les 62 prompts portant une fiche depuis le 20/08** —
471 blocs, 360 dossiers :

| | Blocs | Dossiers | Questions distinctes | Dossiers a une seule question |
| --- | ---: | ---: | ---: | ---: |
| Football | 423 | 319 | 27 | 187 (59 %) |
| Tennis | 48 | 41 | **8** | **39 (95 %)** |

**36 des 41 dossiers de tennis portaient la meme unique question.** Un seul
critere tirait (`Fraicheur`), plus un a 1 % par construction mesuree
(`_thin_player`). La checklist tennis compte huit rubriques ; sept n'avaient
aucun critere derriere elles.

**Trois etats du bloc menent a la meme question, et c'est le vrai enseignement.**
Nos sources ne portent **aucun detail de match pour le tournoi en cours** : le
fichier hebdomadaire parait apres coup, `Ici` ne couvre que ce que nos scans ont
vu, `Service` s'arrete sous son seuil de points. Trois criteres qui emettraient
trois formulations voisines se liraient comme trois recherches quand c'est la
meme — `TOURNAMENT_DETAIL` est donc **ecrite une fois**, et ce qui differe est le
**poids** : `STRONG` quand le bloc ne porte rien du tournoi, `MEDIUM` quand
l'historique accuse du retard.

Quatre criteres neufs, chacun mesure avant d'etre ecrit, sur les 48 blocs :

- **`Ici` absente** — 10 blocs (21 %) ;
- **`Service` absente** — 10 blocs (21 %) ;
- **`Repos` sous 23 h** — 4 blocs (8 %). La question porte sur ce que `Repos`
  **ne peut pas** dire : la duree du match precedent — la ligne part du coup
  d'envoi, aucune source ne publiant la duree — la session, et le **double**, que
  le fournisseur de cotes ne sert pas ;
  - **Le seuil se pose sous un mode, et le mode est le cas ordinaire.** La
    distribution porte un pic net a **23 h, 9 blocs** : c'est le retour de la
    meme session la veille, donc le rythme normal d'un tournoi. « Moins de 24 h »
    designerait **27 %** du lot — un critere qui se declenche sur un quart des
    blocs ne classe plus rien, et c'est exactement le reproche fait aux deux
    criteres faibles du football. Sous le mode, l'ecart dit autre chose : le
    joueur a joue **plus tard hier qu'il ne joue aujourd'hui**.
  - **La premiere version a ete ecrite a 24 h sur un compte faux** — 6 blocs
    releves au lieu de 13, par une lecture qui ne captait que le premier joueur
    de la ligne. Le seuil se lit sur **tous** les joueurs. Un taux qui surprend se
    re-verifie sur sa cle avant d'etre ecrit, et celui-la ne surprenait meme
    pas : c'est le rejeu des criteres sur les blocs archives qui l'a attrape ;
- **`Non joue` presente** — 2 blocs (4 %). La ligne dit le fait et s'arrete la ;
  ce que la recherche ajoute est la date du dernier match reellement dispute.

**Une absence ne se reclame que sur un bloc par ailleurs servi.** Sans cette
garde, un bloc entierement vide produirait trois criteres pour une seule cause,
et `Densite` la nomme deja — meme regle que `Stats match`. `Forme` est le
marqueur : les 48 blocs reels la portent tous.

- **Et c'est un montage de test qui l'a revele** : `_dense("tennis")` ne portait
  ni `Ici` ni `Service`, donc il decrivait un etat que la production atteint une
  fois sur cinq, et les criteres d'absence s'y declenchaient sur tout le lot.
  Meme piege que la forme canonique d'une selection posee sur un match commence.

### Le statut dans le tableau se derive du rattachement de phase

`_draw_status_reasons` : un joueur qui figure dans la competition declaree phase
de celle-ci **est** un qualifie — par definition, pas par inference — et ca ne
coute aucun appel. C'est la seule des quatre rubriques « statut dans le tableau »
que l'application puisse etablir ; tete de serie, wild card et lucky loser sont
la question emise.

**La part attendue est structurelle et non mesuree** : 16 qualifies sur 128 en
Grand Chelem, soit 12,5 % du champ, decroissant ensuite. Elle n'a pas pu se
relever sur les prompts archives — aucun tableau principal n'etait entre, le
rattachement datant du 27/08/2026 — et c'est le premier scan de tableau principal
qui la donnera. C'est la seule exception assumee a la regle « mesurer avant
d'ecrire » de ce lot, et sa raison est que le cas n'a jamais existe en base.

### Chantier identifie, non instruit : les deux criteres faibles du football

**238 des 319 questions de football viennent de deux criteres `WEAK`** qui tirent
sur la simple presence d'une ligne — rotation (130) et effectif (108) — et
**81 dossiers sur 319 (25 %) ne portent qu'eux**. Ce n'est pas de la couverture,
c'est du volume : le football n'a pas une meilleure conduite de recherche que le
tennis, il a plus de blocs.

Les affaiblir demanderait ce que les autres poids ont et qu'ils n'ont pas — **le
rendement mesure de chaque piste** — et cette mesure n'existe pas. Elle
supposerait de savoir ce qu'une recherche lancee sur ces deux questions a
reellement rapporte, donc de relier un dossier ouvert a la selection qui en est
sortie. Porte laissee **ouverte et datee**, contrairement a celles que ce dossier
ferme.

## Ou depenser un budget de recherche fini (`services/research.py`)

**Le vrai plafond d'un lot n'est pas le prompt, c'est le lecteur.** Les deux plafonds de
tokens ne voient jamais un lot reel (voir plus bas) : un lot de 21 blocs pese 21 707 tokens
et rien ne s'y oppose. Ce qui manque sur vingt-et-un matchs n'est pas de la place, c'est du
budget de requetes.

Mesure sur cinq sessions reelles, et c'est la derniere ligne qui compte :

| Lot | Matchs | Dossiers traites | Selections a confiance >= 3 |
| --- | --- | --- | --- |
| Supercoupe | 1 | 1 | 1 sur 1 |
| Canadian Open | 4 | 4 | 1 sur 1 |
| Conference League | **21** | **3** | 2 sur 8 |

Les dix-huit autres sont retombes en `lecture`, donc a confiance 1 — non par distraction,
mais parce que le lot ne donnait **aucun ordre de passage**. Les trois traites l'ont ete au
juge, sur les matchs qui paraissaient les plus lisibles, pas les plus rentables.

- **Aucun critere ne regarde les cotes, et c'est une decision.** Un match a 1.08 n'a en
  apparence pas besoin de recherche — mais trier sur le prix rend le tri **circulaire** :
  on ne chercherait jamais la ou le marche est confiant, donc jamais l'information qui le
  contredit. Le preambule limite deja les cotes a deux usages, et un troisieme affaiblirait
  les deux autres. Un test monte deux fois le meme lot, l'un nu et l'autre charge de cotes
  extremes, et verifie que la fiche ne bouge pas d'un mot.
- **Les poids ne sont pas reglables**, contrairement au nombre de dossiers. Ce ne sont pas
  des preferences mais le rendement mesure de chaque piste ; le seul nombre qui depende de
  qui lit est **combien de dossiers une session couvre**, et il vit dans `thresholds`.
- **Trois etats de tie et non deux.** Ouvert (`<= 1`), joue (`>= 3`), et **deux buts au
  milieu** : ca ne monte pas — deux buts a remonter n'est pas un tour ouvert — et ca ne
  descend pas non plus, ca se remonte. Le classer avec l'un ou l'autre aurait fait passer un
  tour jouable pour mort, ou l'inverse.
- **Un identifiant absent n'est pas une source absente.** Le malus « bloc quasi vide et
  aucune source » ne s'applique que si l'on a pu **verifier** le rattachement : punir un
  match de ce qu'on ignore de lui serait l'inverse de ce que cette fiche fait. Trouve en
  ecrivant le test du tennis.
- **Le malus demote, il ne veto pas.** Un tour a trois buts d'ecart perd trois points, mais
  un dossier qui cumule deux criteres forts reste propose. Un veto ferait disparaitre un
  match sur un seul critere, quand la fiche est un **ordre de passage** et non un filtre.
- La fiche **ne parait pas** sous le seuil : classer trois dossiers sur trois n'apprend
  rien, et la ligne de budget ferait renoncer a un match qu'il y avait tout le temps de
  traiter.
- **La question emise est ce qui fait la valeur, pas la liste de matchs.** « Cherche sur ce
  match » ne fait rien gagner ; « la composition annoncee de X sort-elle son onze
  offensif » se repond en une requete et clot un point. Chaque critere emet la sienne, et
  les doublons sont retires — deux criteres peuvent viser la meme verification.
- **Aucun lien profond n'est rendu, et la requete de recherche n'est pas un pis-aller.**
  Les liens demandent des identifiants que la base ne porte pas — id de match UEFA, adresse
  du site d'un tournoi ou d'un club. Et surtout, mesure en reel : `atptour.com` refuse nos
  agents **aussi en lecture directe**, et les scores d'une journee ont ete obtenus par des
  extraits de recherche pointant vers lui. Le domaine reste l'editeur, donc le niveau de
  source tient ; c'est le chemin d'acces qui differe. La requete formulee epargne la requete
  perdue.
- **Un bloc plein peut cacher un joueur vide**, et les deux critères ne se recouvrent
  pas : la densite mesure le remplissage du **bloc**, `_thin_player_reasons` ce qu'il y a
  derriere les lignes d'un **joueur**. Sur la soiree du 12/08, les deux blocs les plus
  vides au niveau joueur — `Forme D/1`, `Forme VD/2`, ni Profil ni Marge — avaient un bloc
  complet par ailleurs : la fiche a propose six dossiers portant tous la meme question et
  aucun sur les deux joueurs dont on ne savait rien.
  - **Seuil mesure avant d'etre ecrit**, et la mesure a contredit la crainte qui
    l'accompagnait : sur les 406 blocs de tennis archives, « moins de trois matchs »
    designe **5 blocs, soit 1 %** — exactement les cinq de cette soiree. Voisins mesures
    avec : 2 a moins de deux, 9 a moins de quatre, 15 a moins de cinq. Un critere qui se
    declencherait partout ne classerait plus rien.
- **Le motif d'un bloc vide classe, pas sa densite.** Depuis le typage du contexte, un
  `0/26` dit **pourquoi** il est vide, et les quatre causes n'appellent pas le meme budget :
  - `non interroges` — le fournisseur ne couvre pas la competition et ne la couvrira pas :
    la recherche est le seul chemin, donc **dossier fort** ;
  - `source injoignable` — **budget ordinaire**, comme un bloc pauvre. Le motif dit
    pourquoi le bloc est vide, pas qu'il sera rempli a temps. Mesure du 14/08/2026 : rien
    ne rejoue le contexte tout seul — le planificateur porte le scan, les sources
    gratuites et un balayage de compositions, lequel exige un `apifootball_fixture_id` et
    ne peut donc pas reparer le cas ou le rapprochement a echoue. Le coup d'envoi, lui, ne
    recule pas parce qu'un enrichissement rejouera demain ;
  - `competition non rattachee` et `fixture non resolue` — **aucun budget** : ca se repare
    d'un geste hors analyse, et un dossier depense la est perdu.
- **Le cinquieme cas se traite et se journalise.** Apres le typage, un bloc de football a
  0 % sans cause ne devrait plus exister : les quatre causes couvrent le sport entier. S'il
  en reste un, le ranger par defaut dans l'une des quatre lui donnerait un budget decide au
  hasard — il devient donc **dossier fort** (on ne sait pas pourquoi il est vide, donc on ne
  peut pas affirmer qu'une recherche n'y servirait a rien) et un avertissement nomme
  l'evenement. C'est le typage qu'il faut reprendre, pas la fiche.
  - **Scope au football**, et c'est indispensable : le contexte sportif n'existe pas
    ailleurs, et un bloc de tennis pauvre a d'autres sources. L'y ranger ferait journaliser
    un defaut sur un comportement normal.
- **Ce que la fiche ne sait pas encore classer**, faute des chantiers qui fournissent la
  donnee : terrain neutre (P1-4), alerte meteo (P1-5), entraineur en poste depuis moins de
  trois mois — celui-la demanderait de relire une anciennete ecrite en prose, et un
  analyseur de « 3 mois » vaut moins qu'une colonne.
- **`context.tie_state` est ecrit une fois et lu deux fois** : la ligne `Scenario` le rend,
  la fiche s'en sert pour classer. Deux calculs paralleles auraient fini par ne plus dire la
  meme chose du meme match — le piege deja paye deux fois par l'assembleur de contexte.

## Le scenario d'une manche retour se calcule (`context._scenario_line`)

Vingt-quatre manches retour en une semaine, et le meme raisonnement refait a la main a
chaque fois : cumul, qui mene, combien il faut a celui qui est mene. C'est deterministe et
ca tient en trois soustractions — donc ca ne se delegue pas au modele.

- **La detection est ecrite une seule fois** (`_return_leg`) et sert les deux lignes :
  `Aller` enonce le fait, `Scenario` en tire l'arithmetique. Deux detections paralleles
  auraient diverge, et le bloc aurait annonce un scenario sur une rencontre que l'autre
  ligne ne reconnaissait plus comme un aller — le piege deja paye deux fois par
  l'assembleur de contexte.
- **Deux seuils, et il faut les deux** : `doit gagner de 2 pour egaliser, de 3 pour
  passer`. Egaliser envoie en prolongation, passer gagne le tour dans le temps
  reglementaire. Les deux ne produisent pas la meme fin de match, et c'est le second qui
  decide si l'equipe s'ouvre encore a la 80e — un cumul seul laisse ce travail a faire.
- **Le camp oblige est nomme, et c'est le mot « doit » qui declenche l'angle.** Une
  obligation de marquer se traduit en total, en handicap ou en marche d'equipe ; un cumul
  ne se traduit en rien. La mention `(a domicile)` / `(a l'exterieur)` suit, parce que la
  meme obligation ne produit pas le meme match selon le terrain — c'est la configuration
  qui a porte les quatre selections d'un lot reel.
- **Un aller nul n'avantage personne, et c'est la lecture qui se trompe le plus souvent** :
  sans regle des buts a l'exterieur, `2-2` ne vaut pas mieux que `0-0`. La ligne le dit en
  toutes lettres plutot que de laisser deduire.
- **La ligne ne se porte pas garante d'un reglement.** Buts a l'exterieur, prolongation,
  tirs au but sont des regles de **competition**, pas de l'arithmetique. Le preambule les
  enonce **une fois pour le lot** — standard UEFA, abolition des buts a l'exterieur en
  2021 — et dit que la fiche de la competition prime sur lui : elle seule sait qu'une
  Supercoupe va aux tirs au but sans prolongation. Les affirmer par match couterait des
  tokens **et** engagerait l'outil sur un reglement qu'il n'a pas lu. Un test verifie
  qu'aucun de ces trois mots n'entre dans la ligne.
- Le mode d'emploi d'`Aller` a ete relu avec : il disait « a toi de dire s'il s'agit d'une
  double confrontation », ce qui etait juste tant que rien ne la calculait. Meme regle que
  `Serie`, `Parcours` et `Non joue` — toute condition ajoutee a une ligne se verifie contre
  la phrase du preambule qui la decrit.
- **`(a domicile)` suppose un avantage, et cette supposition attend P1-4.** Sur un lot reel
  de 21 manches retour, trois auraient rendu la mention fausse ou trompeuse : ML Vitebsk
  « recoit » a Mezokovesd en Hongrie, Dinamo Minsk au Stadion Beroe en Bulgarie, et l'aller
  de Dynamo Kyiv s'etait joue a Lublin. Le mot **inverse alors le sens de la phrase**. Ce
  n'est pas un defaut a corriger ici : c'est P1-4 qui fournira le drapeau de terrain neutre,
  et `_scenario_line` n'aura qu'a le consommer — `Vitebsk (nominalement a domicile, terrain
  neutre)`. **Dependance notee, pas bug.**
- Limite assumee, et elle vient de la source : `Scenario` ne parait que si `Aller` parait,
  donc seulement quand le rapprochement API-Football a abouti et que le releve H2H porte
  son `league_id`. Une manche retour dont le contexte n'a pas ete recupere n'a pas de
  ligne — comme le reste du bloc.

## Le repos se compte en heures ecoulees, pas en journees de tournoi

La journee de tournoi a corrige un defaut et en a laisse un autre : elle regroupe
correctement une soiree a cheval sur minuit, mais elle **ne sait pas dire un ecart**.
Deux mesures qui l'ont revele :

- sur les demi-finales du Canadian Open, le bloc donnait `2j` aux joueurs sortis de la
  session de jour et `1j` a ceux de la session du soir — une journee entiere d'ecart
  affiche, quand sept heures les separaient vraiment ;
- a Cincinnati, six joueuses recevaient `Repos 0j` : leur premier tour s'etait joue la
  veille en fin d'apres-midi local, mais 23h00 UTC tombe apres minuit a Paris.

`Rest` porte donc **les deux grandeurs a la fois**, et c'est delibere : a ecart horaire
egal, un tournoi de douze jours et un tournoi de sept ne fatiguent pas pareil. La ligne
rend un joueur par ligne — trois informations par fragment, deux joueurs bout a bout ne
se lisaient plus d'un coup d'oeil.

- **`Rest.basis` dit sur quoi l'ecart a ete mesure, et cette mention compte autant que le
  chiffre.** La bonne borne serait la **fin** du match precedent ; ni la duree ni l'heure
  de fin ne sont publiees par une source lisible — `tennis-data.co.uk` ne sert que des
  scores et retarde de dix jours, les pages de match de Tennis Abstract sont interdites
  par son `robots.txt`, les CSV de Jeff Sackmann ont disparu, `atptour.com` interdit nos
  agents. Le calcul part donc du **coup d'envoi**, et le preambule dit noir sur blanc de
  ne pas comparer deux ecarts a l'heure pres : celui qui a joue 2h33 et celui qui est
  passe en 1h05 portent la meme mention.
  - Le jour ou une duree entrera — par un chemin autorise, ou a la main — **seule
    `_elapsed` changera**, et le `~` de `Rest.estimated` distinguera un instant deduit
    d'un instant releve. Rien n'est estime aujourd'hui, donc aucun `~` ne parait : une
    machinerie qui ne peut pas se declencher aurait ete du code mort.
- **Heures entierement ecoulees, jamais `round()`.** La regle bancaire fait tomber 25h30
  et 78h30 chacune d'un cote ; un plancher est monotone et se lit comme la phrase le dit.
  Au-dela de `HOURS_MAX` (72), l'ecart passe en `3 j 6 h` — deux unites dans **un seul**
  nombre, parce que `78 h (3 j)` ferait trois chiffres sur la ligne avec la journee de
  tournoi, soit un de trop pour un coup d'oeil.
- **`competitions.timezone` date un fait la ou il se produit** (migration 037). Tout le
  reste de l'application affiche en `Europe/Paris`, et c'est juste pour une heure de coup
  d'envoi — c'est celle a laquelle on allume sa television. Ce l'est beaucoup moins pour
  **dater un fait sur place** : le forfait de Bencic, annonce le mardi 11 aout au soir a
  Toronto, s'ecrivait « le 12/08 ».
  - **Rien ne se deduit d'un libelle**, meme regle que la surface et le niveau :
    « Cincinnati Open » ne dit pas `America/New_York`, et une table de villes se
    tromperait le jour ou le tournoi demenage — le Canadian Open change de ville chaque
    annee. La saisie est manuelle, depuis `/competitions`.
  - **Non renseigne, rien n'est invente** : les instants se rendent en UTC, annonces comme
    tels. Une heure de Paris presentee comme locale serait pire qu'une heure UTC
    presentee comme distante.
  - **Un fuseau illisible est refuse a l'ecriture**, la ou la surface se contente d'etre
    ignoree. Le contraste est juste : une surface inconnue ne coute qu'une ligne d'Elo,
    un fuseau accepte sans etre reconnu ferait rendre des heures UTC sous le mot
    « local ». A la **lecture**, en revanche, il vaut « non renseigne » — un fuseau
    supprime d'une version de tzdata a l'autre ne doit pas faire tomber le bloc entier.
  - Ce qu'il **ne** change pas : le regroupement de `tournament_day`, qui se fait par trou
    horaire et vaut pour les deux hemispheres sans qu'aucun fuseau soit stocke.
- **`scan_window()` remplace une ambiguite par deux bornes.** « vu depuis le 04/08 » ne
  disait pas s'il s'agissait du premier jour du tournoi ou du premier jour ou nous avons
  regarde — il a fallu le deviner. `events.created_at` porte l'instant ou chaque rencontre
  est entree en base. La borne **haute** compte autant que la basse : elle vaut
  « maintenant » quand tout va bien, et vieille de deux jours elle dit qu'un scan ne
  tourne plus, ce que rien d'autre dans le bloc ne dirait. En UTC des deux cotes — un
  instant de collecte n'a pas de lieu.
- **Le football n'est pas touche.** Son `Repos` sort de `context._rest_days`, sur les
  dates de match du fournisseur, et compte en jours calendaires : « un match le 28 au soir
  et un match le 3 en debut d'apres-midi font 6j de repos, pas 5 tranches de 24 heures ».
  La semantique y est defendable — les deux symptomes mesures sont tennis, et une session
  du soir a 01h UTC est l'exception au football.

## Une rencontre programmee n'est pas une rencontre disputee

Le fournisseur de cotes **programme, il ne rapporte pas**. `Repos` et `Parcours` sortent de
nos propres scans, donc de rencontres programmees : un forfait, un adversaire remplace ou un
match interrompu y laissent une ligne indiscernable d'un match dispute.

Mesure qui l'a revele, sur une demi-finale du Canadian Open : Bencic s'est retiree trente
minutes avant son quart, Gauff est passee sans entrer sur le court. Le bloc a servi
« Repos Coco Gauff 1j » et a liste Bencic au `Parcours`, quand son dernier match remontait a
**quatre journees de tournoi**. Le fait le plus decisif du lot etait efface par la ligne
censee le porter.

- `events.match_outcome_type` (migration 036) dit **ce qui n'a pas eu lieu, jamais ce qui a
  eu lieu**. NULL est le cas ordinaire et signifie « rien ne s'y oppose », pas « disputee » —
  meme regle que la ligne `Statut` du football. Vocabulaire tenu par `tennis_load.OUTCOMES` :
  `walkover`, `replaced`, `suspended`.
- **`Appearance` porte la distinction, et tout en decoule.** `opponents`, `days`, `faced`,
  `days_rest` et `rounds` se calculent sur les seules rencontres disputees ; `uncontested`
  porte les autres. Un seul point de decision, donc `Repos`, `Parcours` **et** `Fraicheur`
  cessent de compter un forfait sans qu'aucun des trois ait a le savoir — ce dernier
  enverrait sinon chercher le score d'un match jamais joue.
- **Le remplacement d'adversaire se derive de nos propres scans, sans saisie** : un joueur ne
  dispute qu'une rencontre par journee de tournoi, et **c'est la plus recemment creee qui
  tient**. Une rencontre reprogrammee garde son identifiant chez le fournisseur ; un
  adversaire remplace en produit un nouveau. Premier cas mesure — JJ Wolf programme contre
  Toby Samuel a 19h00 (enregistre a 12h32) puis contre Shintaro Mochizuki a 21h45 (enregistre
  a 21h51). Le `Parcours` listait les deux, ce qui lui donnait deux tours au lieu d'un.
  - **« Un seul cas » etait faux, et cette phrase est restee la pendant que le cas devenait
    systematique.** Mesure du 27/08/2026 sur les qualifications de l'US Open : **8 affiches
    en double cote ATP, 10 cote WTA**, soit 18 sur une seule edition — `events` porte 111 et
    112 lignes pour 103 et 102 rencontres reelles. Le fournisseur publie une entree
    provisoire a une **heure de remplissage** (`18:00:00Z`, la meme partout) puis la
    rencontre definitive avec son heure reelle et un identifiant nouveau. Ce n'est donc pas
    un accident de reprogrammation : c'est **le regime normal d'un tableau de
    qualification**.
  - **Un document qui minimise un cas systematique est pire qu'un document muet** : il
    autorise a ne pas chercher. Le controle qui devait le voir cherchait des doublons sur la
    cle du fournisseur et sur `(jour, affiche)` — deux criteres vrais, tous deux sortis a
    zero, sur une propriete fausse. Le compte des rencontres passe par le lecteur qui les
    resout, jamais par un `COUNT(*)`.
  - Limite assumee : un tableau retarde par la pluie peut faire jouer deux simples dans la
    meme journee. Le cas ne s'observe pas en base, et le degat serait une ligne de parcours
    en trop plutot qu'un repos faux — l'inverse de ce que le silence coutait.
- **Le fichier de resultats ne peut pas servir cette colonne, et c'est mesure.** Il parait
  une fois par semaine et **apres coup** : le 12/08 il s'arretait au 03/08, soit dix jours de
  retard, quand `Parcours` ne couvre que `MAX_DAYS` (10) jours. Il arrive donc toujours apres
  que le tournoi a cesse d'etre rendu. Les lignes qui en sortent — `Forme`, `Usure`,
  `Profil`, `Marge` — le lisent deja en direct et n'ont besoin de rien ici.
  - Consequence : **la saisie a la main est la seule source vivante**, et c'est un constat,
    pas une preference. Elle vit sur la fiche d'un match, au tennis seulement — au football
    un forfait arrive par le fournisseur de contexte et sort deja sur `Statut`, et un second
    chemin pourrait le contredire.
  - Un marquage **se defait** : se tromper doit pouvoir s'annuler, sinon on hesite a marquer
    et la ligne ne sert plus a rien. Une valeur hors vocabulaire est **refusee** plutot
    qu'ecrite — `load_for` l'ignorerait, et le marquage paraitrait pose sans aucun effet,
    exactement le silence qu'on corrige.
- La ligne `Non joue` porte **la date et la cause**, parce que les deux se verifient en une
  recherche et qu'elles ne se valent pas : un forfait adverse offre un tour sans jouer, un
  adversaire remplace veut dire que le joueur a bien joue ce jour-la, contre quelqu'un
  d'autre. La date est celle de la **journee de tournoi**, comme partout dans ce module —
  en donner une autre ferait deux calendriers dans le meme bloc.
- **Le preambule a ete relu avec le rendu.** Il affirmait qu'« un forfait s'y lit comme un
  match joue » : vrai tant que rien ne le detectait, faux des que `Non joue` existe. Il dit
  desormais que les forfaits connus sortent du `Parcours` et que **l'absence de `Non joue` ne
  prouve pas** que tout le parcours a ete dispute. Meme regle que `Serie` et son repli de
  saison : toute condition ajoutee a une ligne se verifie contre la phrase qui explique son
  absence.
- **Ce qui n'a pas ete construit, faute de donnee.** Un match interrompu et non termine
  laisse le tour suivant sans adversaire connu ; `suspended` existe au vocabulaire, mais nos
  scans ne produisent alors **aucun evenement** — verifie sur la base entiere, aucun match
  sans second participant, aucun nom de substitution (`Qualifier`, `TBD`, `Bye`). Le bloc
  n'existe pas plutot que d'etre incomplet, et il n'y a rien a y rendre.
- `Awarded` est la quatrieme valeur du champ `comment` du fichier de resultats, **2 lignes
  sur 13 858** : un match donne sur decision a un score tronque, comme un abandon. Le traiter
  en match complet le faisait entrer dans `Usure`, `Profil` et `Marge`. L'effet est nul a
  deux lignes ; la regle, elle, ne l'est pas.
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

### Les statistiques de service : source disparue, substitut mesure et refuse (17/08/2026)

**Resultat negatif, ecrit sous la forme qui empeche de le refaire.** Le chantier a ete
propose une seconde fois — reconstruire `Service`, `Retour`, `Jeux` et `Ecart` depuis
`JeffSackmann/tennis_atp` et `tennis_wta` — et il s'arrete sur deux mesures.

**1. Les deux depots n'existent plus, et ce n'est pas un chemin errone.** 404 sur `raw`
comme sur l'API, quand `python/cpython` repond 200 et que le compte `JeffSackmann` existe.
**Un seul depot public subsiste**, `tennis_MatchChartingProject`. Les colonnes sur
lesquelles reposait tout le calcul — `w_svpt`, `1stIn`, `1stWon`, `2ndWon`, `SvGms`,
`bpSaved`, `bpFaced` — n'ont donc aucune source. Corollaire a connaitre : la tenue de
service et le taux de break **sont** derivables de `SvGms`, mais `SvGms` est dans le depot
manquant, et la seule source restante ne le sert pas non plus.

**2. Le seul substitut a ete mesure et il echoue.** Le Match Charting Project porte
exactement les bonnes colonnes, remplies a **100 %** — le remplissage n'est pas le
probleme. Ce qui manque, ce sont les **matchs** : il est cartographie par des benevoles, et
les benevoles cartographient le haut du tableau. Sur les 196 joueurs des cinq derniers lots
tennis, 52 semaines glissantes, surface dur :

| Rang officiel | Joueurs | Mediane, points de service | >= 400 points |
| --- | ---: | ---: | --- |
| 1 – 20 | 27 | **709** | 19 / 27 |
| 21 – 50 | 44 | 134 | 6 / 44 |
| 51 – 100 | 81 | 126 | 10 / 81 |
| 101 et au-dela | 36 | 21 | 1 / 36 |

Premier quartile sur dur : **0 point cote ATP, 19,5 cote WTA**, pour un seuil de 400. La
ligne serait servie sur les tetes de serie et vide exactement la ou les lots vivent —
161 des 196 joueurs sont au 21e rang ou au-dela.

**Le cas concret** : sur le lot du 16/08, Fritz porte 744 points de service et Michelsen
**65**, dans la meme affiche. Le bloc rendrait une demi-ligne, et « une ligne Service a
moitie vide est pire que pas de ligne : elle sera lue comme un fait ».

**Ce qui rouvrirait la question**, et rien d'autre : un lot portant majoritairement des
joueurs du top 20 — Masters de fin d'annee, seconde semaine de Grand Chelem. Ce n'est pas
le regime de ce projet, dont les lots sont des tableaux complets de Masters 1000. Et meme
alors, `SvGms` manquerait : `Overview` ne le sert pas, donc ni tenue ni taux de break.

**Consequence tenue dans le gabarit** : la phrase « Aces, premiere balle et balles de break
ne sont dans aucune source » **reste**, et elle est plus vraie qu'avant — elle l'etait par
choix de collecte, elle l'est maintenant par disparition de la source.

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

## Un comptage ne decrit un tour que s'il decrit un tableau

Prolongement direct du module ci-dessous, et **correction de sa regle
centrale**. Mesure du 12/08/2026, sur une soiree de qualifications a
Cincinnati : le meme match a ete rendu « 16e de finale » a 13:05 et « 32e de
finale » a 22:15. L'etiquette suivait l'avancement de nos scans.

- **La phase n'est servie par personne, et c'est verifie** : `/sports/{cle}/events`
  rend six champs — `id`, `sport_key`, `sport_title`, `commence_time`,
  `home_team`, `away_team` — pour zero credit, le point de mesure etant gratuit.
  Le fichier de resultats, lui, parait une semaine apres coup et ignore les
  qualifications. Elle ne se devine pas non plus : **un tableau de qualification
  ne finit pas par une finale mais par douze qualifies**, donc compter depuis la
  fin y produit un nombre qui ne designe rien et emprunte au passage le
  vocabulaire du tableau principal.
- **Le module avait deja le signal et ne s'en servait que pour une mention.**
  `truncated()` ecrivait « le debut du tableau nous echappe » dans le meme bloc
  ou `Tour` annoncait « 32e de finale ». `is_bracket()` est desormais lu par les
  deux : un tour ne se nomme que sur une population qui forme un tableau.
- **Cout assume, et il faut le connaitre** : un tableau unique vu en partie perd
  son tour lui aussi, alors que son comptage etait juste — c'est le cas mesure
  du Canadian Open, 79 joueurs. Les deux situations sont **indiscernables**
  d'ici, et la regle du module tranche : en cas de doute, rien.
- La ligne porte **deux etats** plutot qu'un silence : le silence se lisait
  « tournoi sans tour » quand il valait « nous ne savons pas ou en est le
  tableau ». Le compte est **dans la valeur** — `phase non renseignee (76
  joueurs vus ne forment aucun tableau)` — ce qui rend l'affirmation verifiable
  d'un coup d'oeil, meme idiome que la fenetre de `Parcours`.
- **La stabilite ne se prouve pas comme on l'attend.** Le compte entre
  parentheses **bouge** avec nos scans, et c'est juste : il decrit notre vue,
  pas le match. Ce qui ne doit pas bouger est le **tour**, et c'est ce que le
  test verifie sur les deux populations reelles de la soiree — 34 puis 76.

### Compter les tours depuis le debut : mesure, et resultat negatif

Piste evidente pour recuperer ce que le garde-fou fait perdre : `Parcours`
compte les tours deja joues, donc `5e match du joueur dans ce tournoi` se
deduirait **sans aucune taille de tableau** — et c'est la convention qu'il
faudrait en qualification, ou il n'y a pas de finale. Mesure sur les 406 blocs
de tennis archives, avant d'ecrire une ligne :

| Etat du bloc | Part | Ce qu'un compte y vaudrait |
| --- | --- | --- |
| sans `Parcours`, vue complete | 70 % | « 1er match », que `Fraicheur` dit deja |
| `Parcours`, vue complete | 18 % | sur, mais le tour s'y nomme deja |
| `Parcours`, vue tronquee | 8 % | un plancher, pas un compte |
| sans `Parcours`, vue tronquee | 4 % | rien |

- **La troncature ne tombe pas au hasard : elle tombe exactement sur les blocs
  que la ligne devait servir.** Le quart de finale du Canadian Open — le cas qui
  a fait naitre l'idee — est dans la ligne « vue tronquee » : ses joueurs
  affichent 3 adversaires chacun, ce qui est un plancher pour un non tete de
  serie. La piste **ne rattrape pas le cas pour lequel elle est proposee**.
- **Et la ou elle serait sure, le tour se nomme deja** : « vue complete » est
  exactement la condition qui autorise le comptage depuis la fin. Remplacer
  « quart de finale » par « 4e match du joueur » y perdrait de l'information.
- Le critere d'arret pose d'avance — « tronque plus d'une fois sur deux, ne rien
  faire » — n'aurait pas suffi : la troncature vaut 29 % des blocs a `Parcours`.
  C'est sa **correlation** avec le besoin, et non sa frequence, qui tranche.

## Le compte de tours prend le plus complet des deux releves

**Une prevision ecrite s'est verifiee, et c'est assez rare pour etre note.** Le
docstring de `_rounds_played` portait depuis le 20/08/2026 : « nos scans seuls,
et c'est un arbitrage mesure — 11 joueurs sur 192, soit 5,7 %, et toujours d'un
seul tour. Le jour ou ce taux monte, c'est ici que ca se reprend. » Il a monte.

Rejeu du 28/08/2026 sur les blocs archives portant a la fois `Tour` et `Ici`,
restreint aux blocs a phase inconnue — les seuls ou la mention se rend :

| | joueurs |
| --- | ---: |
| compte juste | 46 |
| **compte trop bas** | **10** |
| compte plus haut que la source | 1 |

- **Ce n'est pas le taux qui a decide, c'est son effet.** Sur les 31 blocs
  concernes, **5 voient l'asymetrie du bloc retournee** — de « 1-1 » a « 2-1 » ou
  « 3-1 ». C'est la ligne dont le gabarit dit qu'elle « dit si l'enjeu est
  asymetrique », et elle annoncait une egalite la ou le bloc portait le double
  d'un cote, quatre lignes plus bas, avec les scores.
- **Deux bornes inferieures, donc leur maximum.** Nos scans sont bornes par leur
  fenetre, la source par la date de son releve ; le maximum de deux bornes
  inferieures en est une. « Au moins » reste le mot exact, et il l'etait deja —
  la ligne n'a jamais ete fausse, elle etait inutilement basse.
- **Et surtout pas le compte de la source seul.** Cas reel du prompt 212 :
  `Parcours` nommait cinq adversaires de Tiafoe, la source quatre, et
  `_uncovered` n'en declarait aucun non couvert — son rapprochement par **nom ou
  jour** est genereux a dessein, et le cinquieme partageait sa journee avec le
  quatrieme.
  - **C'est ce qui interdit l'appariement unique**, plus pur au sens du §8 : les
    deux consommateurs ont besoin d'erreurs de **sens opposes** — la fiche de
    recherche ne doit pas sur-affirmer un manque, le compte ne doit pas
    sous-affirmer un total. Un seul appariement en servirait un et trahirait
    l'autre, et le cas Tiafoe est la demonstration.
- **Le compte voyage par l'assembleur, jamais par un second calcul.**
  `session.context_block` est le seul endroit ou les deux lignes se rencontrent ;
  il calcule `Ici` d'abord, passe le compte a `Tour`, et **pose** `Ici` a sa place
  d'origine, apres `Non joue` qu'elle complete. Seul l'ordre du calcul change.
  Mesure qui l'impose : le chemin de la source coute **52 ms par bloc** contre 23
  pour la ligne `Tour` entiere.
- **`_uncovered` rend la liste et son total**, et `_uncovered_line` formate.
  Recompter `faced` chez l'appelant aurait ete la seconde lecture que le §8
  interdit — c'est ce que le premier jet avait ecrit, et les deux comptes
  auraient fini par ne plus porter sur la meme fenetre.
- **Le mode d'emploi a ete relu avec le calcul.** Il disait « sur nos propres
  releves », vrai tant que le compte n'en sortait que ; faux des que la source y
  entre. Meme regle que `Serie` et `Non joue` — toute condition ajoutee a une
  ligne se verifie contre la phrase qui l'explique. Un test lit le gabarit.
- **Le drapeau de la ligne `Ici` garde le correctif** : sans elle le compte de la
  source n'existe pas, la borne retombe sur nos scans, et c'est le comportement
  d'avant.
- Portee : **8 blocs sur 41** changent au rejeu, 33 sont identiques.

### Mon premier compte etait faux deux fois, et c'est la meme regle qui l'attrape

Le releve initial annoncait **33 joueurs sur 82, soit 40 %**. Les deux erreurs
sont de la famille du zero d'appariement — un chiffre credible, rapporte sans que
sa cle ait ete verifiee :

- il comptait **16 joueurs dont le bloc nomme la phase**, ou le compte est omis a
  dessein : « quart de finale » situe mieux qu'« au moins 3 tours », et la ligne
  ne porte pas les deux ;
- il comptait **6 joueurs des prompts 166 et 167**, rendus le 20/08 a 10h38 et
  19h56 UTC, quand `_rounds_played` a ete livre a **20h40**. La mention ne pouvait
  pas exister.

Un rejeu intermediaire s'est trompe une troisieme fois, en appariant un bloc sur
`(joueurs)` seuls : Swiatek — Rybakina designe deux editions, et le Canadian Open
a ete pris pour Cincinnati. **La cle d'un bloc est (joueurs, competition, jour)**,
et un rejeu qui surprend se reverifie sur sa cle avant d'etre ecrit.

Corollaire pour tout rejeu sur ce corpus : **une fonctionnalite livree en cours de
periode coupe la population en deux**, et la date de son commit est la borne. La
regle vaut au-dela de ce cas — le corpus archive porte plusieurs regimes.

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

## Un remount n'est pas un raccourcissement, et le correctif n'est pas le meme

**Mesure du 23/08/2026, au navigateur, sur une session de 59 selections.** Saisir un
resultat faisait passer `window.scrollY` de 3000 a **181 px**, et il fallait remonter
avant chaque ligne suivante — bloquant sur les lots de quarante.

La cause **evidente est fausse, et c'est ce qui decide du correctif** : on lit « la ligne
quitte la liste, le document raccourcit, le navigateur clampe `scrollY` ». Or la hauteur
du document **ne bouge pas d'un pixel** — 8183 avant, 8183 apres — et le fragment rendu ne
perd que 222 caracteres sur 238 000. Ce qui clampe, c'est le **detachement transitoire**
du bloc pendant un `hx-swap="outerHTML"` sur `#worksheet` : le temps que le div sorte du
document et que le nouveau y entre, la page ne fait plus que sa hauteur residuelle, le
navigateur ramene `scrollY` a ce qu'elle permet, et la valeur ne remonte pas quand le
contenu revient.

- **Les deux diagnostics menent a deux correctifs differents.** Un raccourcissement
  s'ancre — on note la position, on la restaure. Un remount ne s'ancre pas : il se
  supprime. `scrollTo` aurait rendu la mesure verte en laissant la cause en place, et le
  premier autre swap l'aurait fait revenir.
- **La saisie ne rend donc que sa ligne** (`_pick_row.html`, `hx-target="closest tr"`), et
  la ligne tranchee **reste dans « A trancher »** jusqu'au prochain chargement ou au bouton
  « Rafraichir ». Un tri qui se refait sous la main pendant qu'on saisit quarante lignes
  coute plus qu'il n'apporte. Mesure apres correctif : **0 px d'ecart sur dix saisies**.
- **La propriete se teste, le pixel non.** La suite n'a pas de navigateur — section 9.4,
  aucun `node_modules` — donc elle verifie ce qui produit le saut : la reponse ne porte
  aucun `id="worksheet"`, et elle rend exactement une ligne. Un test qui recopierait
  « 0 px » ne tiendrait que jusqu'au prochain changement de gabarit.
- **Le compteur suit hors bande** (`hx-swap-oob` sur un `span`), sans quoi le reste a
  trancher resterait celui de l'ouverture. Un swap sur un element inline ne change aucune
  hauteur, donc ne peut pas reproduire ce qu'on vient de corriger.
- **Le refus se voit enfin.** La route journalisait un resultat refuse et re-rendait la
  feuille **inchangee** : l'echec et le cas ordinaire rendaient la meme sortie, sur le seul
  geste qu'on repete quarante fois. La ligne porte desormais le message et garde ses
  controles actifs.
- **« Annuler » restaure l'etat d'avant, jamais « en attente » en dur.** Les deux se
  confondent sur une ligne de « A trancher », jamais sur une ligne qu'on corrige depuis
  « Tranchees ».
- **Ce qui n'a pas ete touche, et il faut le savoir** : cote obtenue, montant pose,
  reglement propose, « jouer » et la suppression visent toujours `#worksheet` et font donc
  toujours sauter la page. Deux d'entre eux reorganisent vraiment la feuille, ce qui les
  justifie ; les deux champs de saisie, non — ils se remplissent en serie eux aussi, et
  `Worksheet.coverage_line` reclame justement la cote obtenue ligne apres ligne. **Dette
  nommee, non corrigee**, parce qu'elle n'etait pas dans le perimetre demande.

## Une seconde selection sur un match : ce qui bloque n'est pas le plafond

**Mesure du 27/08/2026, faite avant d'elargir quoi que ce soit.** Sur les
574 matchs portant au moins une selection, **13 en portent deux** — 9 en
section C. La permission que le gabarit accorde depuis toujours sert dans
**2,3 %** des cas : il n'y a pas de file d'attente, et relever le plafond a deux
ou trois ne debloquerait rien.

Ce qui bloque est ailleurs : **la note d'independance est renseignee sur 5 lignes
sur 18**. Le champ existe depuis toujours a l'import ; ce qui manquait est
qu'elle soit **produite** — il n'y avait rien a recopier. Le gabarit la demande
donc sous la forme qui n'a jamais rate son transport, une ligne nommee hors de
tout bloc de code, meme idiome que `dossiers_ouverts` et `sets:`.

- **Elargir avant, c'est fabriquer de la correlation non declaree.** Sur les
  9 paires tranchees de section C, **7 sont tombees entierement du meme cote** ;
  14 selections sur 393 partagent un match (4 %), et `clustered_p_value` absorbe
  deja ce cas — a 5 sur 73 l'effet valait `0,0161 → 0,0227`. Il grossit avec la
  part, et le residu du bloc de tete suppose l'independance.
- **Le lecteur de la ligne n'est pas livre, et c'est l'ordre voulu.** La surface
  de saisie existe, donc le chemin est complet ; ecrire un lecteur pour une
  valeur dont on ne sait pas encore si elle est produite serait construire le
  consommateur avant le producteur. Il se decide sur deux ou trois sessions, au
  vu du taux.

### Le combine intra-match : refuse pour une raison structurelle

« X ou nul & +1,5 buts » n'est pas deux selections mais **une seule a une cote
differente**, et le parametrage ne peut pas la representer. Mesure : `odds` porte
**20 cles de marche servies**, aucune de cette forme. Une telle selection n'aurait
donc **aucun prix dans le bloc**, le gabarit interdit d'en inventer un, et un prix
invente classerait la ligne dans le mauvais palier tout en faussant le taux par
bande de cote. Cote `combos`, deux jambes sur le meme evenement sont
structurellement possibles mais leur produit n'est pas ce qu'un book paie pour un
combine intra-match, lequel est correle.

**Ce n'est pas un arbitrage, c'est une absence d'offre**, et la question se
rouvrira sur d'autres bases le jour ou le fournisseur sert un tel marche.

### Le controle 1 compte ce que le gabarit interdit, pas une regle levee

Le SKILL disait « une seule selection par evenement, sans exception » ; le
gabarit en autorise deux si les angles sont independants. Le compteur remontait
donc le comportement voulu, et un compteur qui compte le conforme cesse d'etre
lu.

Il se lit desormais sur **la famille de marche** : deux lignes de la meme famille
ne sont pas deux angles independants, et c'est le seul cas que le collage prouve
a lui seul. Verifie sur les 9 paires reelles — huit portent deux familles
distinctes (`1N2` et `Handicap`, `Eq. buts` et `Se qualifie`), la neuvieme est
**la meme selection ecrite deux fois**, `Vainqueur` sur Peyton Stearns, notee
`c4` puis `c3`. C'est elle qu'il faut attraper, et c'est la seule.

- **La famille plutot que la cle fine** : `O/U 2.5` et `O/U 3.5` sur un meme
  match sont le meme angle a une ligne pres. `family_key` retire la valeur de
  ligne finale et fait exactement ce groupement.
- **La note manquante n'y entre pas, et ce n'est pas un oubli.** Elle n'existe
  nulle part ou ce compte se lit : elle se saisit a l'apercu et le rapport se
  calcule sur le collage conserve, qui ne porte aucune colonne d'independance —
  la condition ne peut pas voyager par le formulaire qu'elle garde. Elle est de
  toute facon deja **bloquante**, `add_pick` refusant la ligne sans elle. La
  compter ici redirait ce qu'un refus dit deja, sur une valeur illisible.

## Le budget de recherche borne les paliers hauts

Tout palier **au-dela des deux plus surs** reclame un fait nomme et date de la
section A, donc un dossier ouvert ; et une session n'en ouvre qu'un nombre fini
(`recherche_dossiers`). Le quota autorisait plus que la methode ne permet de
justifier — une invitation a remplir, l'erreur que le prompt nomme lui-meme
comme la plus couteuse.

- **La frontiere est celle du gabarit, et le code la placait deja la** :
  `QUOTA_FLOOR_TIERS` vaut 2, les paliers hauts sont donc ULTRA FUN et au-dessus.
  Verifie avant d'ecrire une ligne.
- **La contrainte porte sur le total des paliers hauts, jamais palier par
  palier.** Un `min` par palier ne mordrait jamais : le plus genereux des trois
  vaut 3 ou 4 quand le budget en ouvre 7. C'est leur **somme** qui consomme le
  budget.
- **La coupe part du bas** — le palier haut le plus sur d'abord : un fait date
  justifie plus facilement un 2.50 qu'un 9.00.
- **Les dossiers disponibles valent `min(budget, lot)`** et non le budget seul :
  sur un lot plus court que le budget, la fiche de priorite ne se rend meme pas
  et tout match peut recevoir un dossier.
- **Calcule et annonce, jamais formule en consigne** : le prompt ecrit les
  bornes du lot et dit combien de dossiers la session ouvre. Une borne qu'il
  faut recalculer soi-meme ne contraint rien — meme regle que la reduction des
  quotas a la taille du lot.

**Ou elle mord, et c'est mesure le 14/08/2026** :

| Reglage des paliers | Total hauts | Dossiers | Mord ? |
| --- | --- | --- | --- |
| seed d'origine (4 + 3 + 2) | 9 | 7 | **oui**, ramene a 7 |
| production, quotas resserres (3 + 2 + 1) | 6 | 7 | non |

Sur la base servie la contrainte **ne mord sur aucun lot** : les quotas y ont ete
resserres a la main sous le budget. C'est une **porte fermee, pas un defaut
repare** — meme forme que le rejet d'une cote hors bande, dont l'audit avait
trouve zero ligne concernee. Ce qu'elle empeche est que les deux nombres derivent
l'un de l'autre en silence : relever un quota_max ou descendre
`recherche_dossiers` a sa borne basse la fait mordre aussitot.

Corollaire pour les tests : le critere est une **propriete** — le total haut vaut
`min(quotas regles, budget, lot)` — et jamais les valeurs du jour, qui different
deja entre le seed et la base servie.

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
- **Ces deux plafonds ne voient jamais un lot reel, et il ne faut pas les lire comme des
  limites de production.** Ils vivent dans `tests/`, s'appliquent a deux fixtures de six et
  trois matchs, et **rien ne les lit a l'execution** : `token_estimate` est calcule,
  archive et affiche, jamais oppose a quoi que ce soit. Mesure sur les 92 prompts
  archives : le plus gros pese **21 707 tokens pour 21 blocs** — une soiree de Conference
  League — soit pres du double de `PROMPT_BUDGET`, sans que rien ne s'y oppose ni n'ait a
  s'y opposer.
  - **Le cadre a change de regime, et le chiffre ci-dessus decrit l'ancien : mesure du
    21/08/2026 sur les 172 prompts archives.** Un bloc enrichi coute **493 tokens** en
    moyenne sur toute la base, 665 sur les huit derniers prompts ; le cadre, lui, coute
    **8 000 tokens en moyenne et 12 257 a 18 498 sur les huit derniers**. Le chiffre
    precedent — ~6 100, releve sur 92 prompts — decrivait le regime d'avant le 10/08.
    - **Les deux nombres ne se comparent pas directement**, et c'est la seconde moitie
      du meme piege : 6 100 et 8 000 sont deux **moyennes**, soit +31 % ; 6 100 et
      15 232 sont une moyenne et un **regime courant**, soit x2,5. Dire « a triple »
      revient a comparer l'ancienne moyenne au nouveau maximum.
    - **La croissance est lineaire, environ 700 tokens par jour, et le dire ainsi
      change la conclusion.** 8 048 tokens de cadre le 10/08, 12 160 le 15/08, 15 232 le
      20/08 : deux fenetres de cinq jours, **+4 112 puis +3 072**. Les increments sont
      constants et plutot decroissants — c'est une droite, pas une exponentielle.
      - Le ratio invite a lire « double tous les dix jours », et c'est le piege : a
        trente jours le modele lineaire donne **~36k**, l'exponentiel **~120k**, et
        **seul le second justifierait une refonte a lui seul**. La refonte se justifie
        par la part du cadre — 65,4 %, 80 % sur le dernier gros lot — pas par une
        urgence de croissance qui n'existe pas.
      - Regle generale : **une suite de mesures se decrit par ses increments, jamais par
        le rapport de ses bornes.** Deux points suffisent a fabriquer un doublement ; il
        en faut trois pour voir une pente.
    - **Rien ne s'y oppose, et c'est la vraie cause** : les deux budgets vivent dans
      `tests/`, s'appliquent a des fixtures de six et trois matchs, et ne voient jamais
      un lot reel. La derive etait integralement archivee dans `prompts.fixed_tokens`,
      et personne ne la regardait. L'alarme se pose donc a `save_prompt`, sur le prompt
      reellement produit — voir `SPEC-PAYLOAD.md` §7 bis.
    - **Rapporte au volume, le cadre est desormais l'essentiel de ce qui part** : 65,4 %
      des tokens archives, et 80 % sur le prompt 171 — 18 498 de cadre pour 4 677 de
      faits. Cote gabarit, 26 lignes sur 1 411 sont factuelles.
    - C'est la mesure qui fonde le chantier du bloc de donnees (`SPEC-PAYLOAD.md`) : ce
      qui decide de la sortie part dans le `SKILL.md`, et le prompt ne porte plus que
      des faits attribues.
  - La confusion est facile et elle a ete faite : « 7 482 tokens pour un lot de six,
    contre 11 500 permis » se lit comme une marge de securite sur la production, alors que
    c'est la mesure d'une fixture contre son alarme de non-regression.
  - Consequence pour la priorisation : **la taille d'un lot n'est pas bornee par le
    prompt**. Ce qui manque sur un lot de vingt-et-un n'est pas de la place, c'est du
    budget de recherche — et c'est un tout autre probleme.
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
  - **Ce 36 % est date, et il a deja fait deriver un seuil.** Il vaut pour l'etat de la
    base a la mi-aout 2026 ; **il n'est plus le taux courant**. Re-mesure du 21/08/2026,
    sur la part du lot de **session** (`prompt_events`) effectivement retenue en
    section C, mediane par session : **42,6 %** sur les 7 sessions anterieures au 16/08
    et **51,7 %** sur les 5 posterieures. Le dénominateur n'est pas tout a fait celui de
    `_selection_median`, qui lit les `FEEDBACK_SESSIONS` dernieres et passe par `lots()`
    — donc les lots reconstruits : l'ecart de ~9 points entre les deux regimes tient,
    l'absolu est a quelques points pres.
  - **Tout seuil derive de ce chiffre se re-derive avec lui.** `combo_solo_min_lot` a ete
    releve a 11 sur les 36 %, puis ramene a 9 quand la re-mesure a montre le taux
    courant : le seuil n'est pas un nombre, c'est `lot x taux >= jambes`, et changer le
    seuil sans re-mesurer le taux revient a garder la conclusion en jetant la premisse.
    Le taux vit dans la base, pas ici — ce paragraphe ne fait que le dater.

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
  - **Les coupes n'en ont pas le monopole**, et s'en tenir a elles laissait cinq
    competitions actives muettes. Un **championnat** a lui aussi un format a dire — ce que
    sa fin de saison met en jeu, a quel moment de l'annee il se joue, si ses clubs se
    valent : une A-League sans montee ni descente n'a plus d'enjeu de maintien en avril, et
    aucune ligne du bloc CONTEXTE ne le signale. Un **tournoi de tennis** en a deux de plus,
    qui n'appartiennent qu'a lui : ce qui se dispute la **semaine d'avant** et la **semaine
    d'apres** — Cincinnati suit le Masters canadien et precede l'US Open, ou les forfaits de
    precaution sont la regle.
  - Le test de garde ne verifie donc plus le prefixe `soccer_` mais ce qu'il verifiait
    vraiment : que la cle est celle d'un sport connu, donc qu'elle sera rapprochee d'une
    competition. Une faute de frappe ne casse rien — la fiche ne se pose jamais, et la
    competition reste reclamee sans qu'on comprenne pourquoi.
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
- **`Classement` et `Enjeu` sont dates et marques « indicatif » en debut de saison.** A la
  3e journee sur 32, « Relegation Playoffs » decrit l'ordre alphabetique autant que le
  niveau — et le prompt ordonne de recopier cette ligne comme l'enjeu reel, sans recherche.
  Elles sont **datees plutot que supprimees** : l'information reste, c'est bien ce que la
  competition declare, et sa portee est dite.
  - **La reserve valait pour `Enjeu` seul, et c'etait un oubli, pas un arbitrage.** Les deux
    lignes sortent du **meme** classement, a la meme journee : un rang de 1re journee ne
    classe pas plus qu'un enjeu de 1re journee. Le lot du 14/08 le montrait a deux lignes
    d'ecart — « Eintracht Braunschweig 1er (3pts, 1j, +5) » sans reserve, « Promotion
    (après 1j — indicatif) » juste dessous. Sur un tour de coupe le degat est pire :
    « Preußen Münster (3. Liga) 5e » contre « Karlsruher SC (2. Bundesliga) 5e » sortaient a
    egalite apparente, le rang travaillant contre la division que la ligne venait de nommer.
  - `_provisional()` est **ecrite une fois** et sert les deux : deux ecritures de la meme
    reserve auraient diverge, comme le seuil qu'elles partagent.
  - **Le seuil ne divergeait pas** : les deux lignes se gardent deja au rendu par
    `_standing_played` (un match joue), et `enjeu_min_journees` ne portait que la reserve.
  - Le compte de journees vit **dans la reserve** et sort alors du detail du rang : entre
    deux parentheses voisines il paraissait deux fois, et c'est dans la reserve qu'il decide
    de quelque chose.
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
- **Le critere n'est pas « la ligne porte-t-elle un fait sur le match » mais « la ligne
  change-t-elle le comportement du lecteur ».** `Tour : phase non renseignee (83 joueurs
  vus)` envoie verifier le tableau soi-meme, exactement comme `Absents : non interroges`
  designe la recherche comme seul chemin : les deux passent le test et comptent donc.
  Distinguer les etats « non etablis » demanderait un mecanisme pour une difference qui ne
  change rien a ce qui est fait ensuite — de la mecanique sans lecteur.
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

### Un palier ne s'annonce que sur une cote que le bloc affiche

**Le mecanisme existait, il n'etait pas applique au bon niveau.** `prices_of`
lisait `event.markets`, donc toute la table `odds`, quand `render` tronque — dix
scores exacts, cinq lignes O/U, un palier de handicap, le seul Over de chaque
equipe. Les deux consommateurs annoncaient donc des cotes que personne ne peut
aller lire.

Mesure du 28/08/2026 sur les 142 lots archives portant la ligne des bornes,
**bandes de l'epoque appliquees** — la migration 071 deplace SAFE/FUN le 21/08,
et sans cette precaution 43 blocs sortaient en faux positif :

| | avant | apres |
| --- | ---: | ---: |
| lots dont la borne **haute** manque au bloc nomme | **84 (59 %)** | 0 |
| lots dont la borne **basse** manque au bloc nomme | **66 (46 %)** | 0 |
| blocs annoncant un palier qu'aucune cote rendue n'atteint | **145 / 1 194 (12,1 %)** | 0 |
| liste de paliers **du lot** en desaccord | **0 / 142** | 0 |

- **La conclusion evidente etait fausse, et c'est la mesure qui l'a dite.** Le
  prompt 230 annoncait `501.00 (M3 · Score ex. MT 1:5)` et proposait GIGA+ ; on
  en a deduit que le palier n'existait que par cette cote invisible. Faux : M1
  rend un `Score ex. MT 2:2 34.00`, donc GIGA+ est atteint par une cote
  **visible**. Le quota du lot n'a jamais ete fausse, sur 142 lots. Le defaut
  vit un cran plus bas, sur la ligne `Paliers` de chaque bloc.
- **145 sur 145 sont GIGA FUN et 145 sur 145 sont du football**, soit 17,2 % des
  blocs de football et 17,0 % depuis le 22/08 — regime stable, pas un artefact
  ancien. La bande 3.60-8.00 y est peuplee par une ligne O/U ou un score exact
  que la troncature ecarte, quand GIGA+ reste atteint par les scores rendus.
- **Aucune selection n'a pu etre prise dessus, et c'est mesure deux fois** : sur
  370 selections rattachees a leur bloc, 369 portent une cote qui figure dans
  une ligne de marche rendue, et **0** tombe dans un palier fantome de son bloc —
  y compris sur les 35 blocs fantomes qui portaient pourtant une selection. On
  ne recopie pas un prix invisible. Ce qui se paie est **une recherche envoyee
  dans une bande vide, puis une ligne d'excuse pour un palier que le bloc rendait
  impossible** : le cout exact que `TierScope` existe pour supprimer.
- **Le correctif ne replique pas la troncature**, ce serait la copie que
  `markets.py` a deja appris a ne pas faire. Chaque rendu de marche rend
  `(lignes, retenus)`, tires des **memes cles** qu'il a calculees pour ecrire ses
  lignes ; `render.rendered_outcomes` est la seule porte. Douze fonctions
  touchees, et l'invariant passe de la discipline a la **signature** — un rendu
  neuf ne compile pas sans dire ce qu'il imprime.
- **La ligne `Cote min` est gardee**, et elle ne designe rien de jouable : 114
  lots sur 142 y nomment une cote sous 1.25, donc sous toute bande. C'est
  precisement ce qu'elle dit — il n'y a rien en dessous — et elle epargne la meme
  relecture que la borne haute. Corrigee, elle nomme une cote rendue.
- **Une cote inferieure ou egale a 1.00 en est ecartee, et il faut savoir
  pourquoi elle y arrivait** : elle **est** imprimee, 52 fois sur les 51 892
  cotes rendues du corpus, dans une echelle O/U qui montre ses deux cotes. Ce
  n'est pas une cote pour autant — un taux implicite d'au moins 100 %, refuse par
  `add_pick` — et une borne ne peut pas nommer un prix qu'aucune selection ne
  pourrait porter.
- Cout : **-580 tokens sur tout le corpus**, 4 par prompt. 1 049 blocs sur 1 194
  ne changent pas d'un mot.

### Le garde-fou des cotes existait quatorze fois et n'avait pas de nom

Corollaire du meme chantier, et cas 1 de la regle des copies. « 1.00 ou moins
n'est pas une cote : ce serait un taux implicite d'au moins 100 % » etait ecrit
en commentaire au-dessus d'**un** des quatorze endroits qui recopiaient la
comparaison.

- **Verifie site par site avant d'unifier**, parce que deux gardes proches sous
  des noms differents seraient pires que deux noms : refus a la saisie d'une
  selection et d'une cote obtenue, refus d'une ligne de cotes manuelles, refus
  d'une saisie de grille, et garde devant chaque `1.0 / cote` du residu au prix.
  Aucun n'est un voisin du predicat ; tous sont le predicat.
- `render.is_price` le nomme, et les **douze exemplaires Python** l'appellent.
  Le module est le seul que les cinq appelants puissent importer sans cycle —
  `history` y arrive deja par `market_families`, `grid` l'importe. Meme
  raisonnement que `in_band` : **c'est le sens de l'import qui decide, pas
  l'affinite du sujet**.
- **Trois exemplaires restent, et ils sont en SQL.** Une clause `WHERE` ne peut
  pas appeler la fonction, et rouvrir une connexion par ligne paierait
  l'unification en performance. C'est le second traitement du chapitre des
  copies : un banc lit **les deux sources** et compare leur seuil, plutot que de
  recopier la regle.
- **Un garde sans nom du tout est pire qu'un garde mal nomme** : il ne peut meme
  pas se chercher. C'est ce qui a permis a la quatorzieme copie de s'ecrire.

### La divergence d'entraineur ouvre un dossier, la meteo extreme non

Deux candidats du meme lot, mesures separement, et **ils ne se concluent pas
pareil** — 28/08/2026.

**La divergence d'entraineur n'avait aucun critere.** M2 du lot du 28/08 portait
une double divergence — Milojevic contre Slutski, Tang contre Han — et ne
figurait pas dans la fiche, quand quatre blocs y etaient sur la seule presence
d'une ligne `Effectif`. Ce qu'elle coute depasse sa ligne : trois autres lignes
du **meme bloc** — `Forme 5`, `Formations`, `xG` — decrivent une equipe sous une
direction dont on ne sait pas si elle est la.

- Taux mesure **depuis le correctif du nom complet** (`f0e500a`, 21/08), qui est
  un point de rupture : avant lui la comparaison opposait un nom abrege a un nom
  entier. **87 blocs sur 335, 26,0 %**, dont **33 (9,9 %) sur un bloc qu'aucun
  critere actuel ne designe** — le « aucun critere » du lot tombe de 54,0 % a
  44,2 %. Divergence double : 19 (5,7 %).
- **26 % est la largeur des deux criteres faibles** — `_squad_reasons` 26,0 %,
  `_rotation_reasons` 30,7 % — donc le reproche que le dossier leur fait
  vaudrait ici. Deux choses l'en separent, et aucune n'est le taux : il tire sur
  un **conflit nomme entre deux sources** et non sur la presence d'une ligne, et
  il ouvre un dossier la ou il n'y en avait aucun.
- **Le poids ne distingue pas la simple de la double**, et le motif si. Peser la
  double plus haut serait regler un poids sur son propre exemple — le lot qui a
  souleve la question en portait justement une. Porte laissee ouverte et datee :
  le resserrement est une decision d'une ligne, et sa mesure est prise.

**La meteo extreme reste fermee, et la raison qui la fermait etait fausse.** Le
docstring de `_weather_reasons` la fermait sur la **frequence** — « un lot d'ete
monterait en entier ». Vrai des valeurs ordinaires, faux des extremes, et c'est
ce qui a fait rouvrir la porte.

| | part des 640 relevés |
| --- | ---: |
| >= 30 C | 23,4 % |
| >= 41 C | **0,9 %** (6 blocs) |
| >= 47 km/h | **4,1 %** (26 blocs) |

- **Ce qui ferme la porte est l'effet.** Sur les blocs de queue — 35 C, 44 km/h
  ou 90 % de pluie et au-dela — la prose des selections cite **deja** la meteo
  dans **10 cas sur 14 (71 %)**, contre 27 sur 213 ailleurs (13 %). Fisher exact
  `p = 2,6e-6`. Le modele lit la valeur extreme et s'en sert sans qu'on l'y
  envoie, parce qu'elle est **ecrite dans le bloc**.
- Un critere emettrait donc une question dont la reponse est sur la meme ligne —
  le defaut corrige au lot precedent. Ce qui **n'est pas** dans le bloc est
  l'etat de l'alerte a l'heure du coup d'envoi, et c'est le seul cas qui reste.
- **Un docstring faux coute plus qu'un docstring absent** : celui-ci a fait
  re-deriver la conclusion inverse. La porte est desormais tenue par un banc —
  premiere branche de la regle des « a ne pas oublier », une condition
  structurelle qu'un test peut voir.

**La lecon de methode** : une porte fermee se rouvre en verifiant **la raison
ecrite**, pas la decision. Ici la decision etait bonne et sa raison ne l'etait
pas ; les deux se corrigent separement.

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

## Le socle inferentiel (`services/inference.py`)

Couche **pure** — aucune base, aucun reglage, aucun import de service — extraite
de `history.py`, ou `wilson` et `required_sample` vivaient au milieu de trois mille
lignes de requetes. Ces fonctions decident desormais de **ce que la page affirme** :
les laisser la rendait une erreur invisible, et un intervalle faux de deux points ne
casse rien — il fait seulement lire un constat la ou il n'y en a pas. Aucune
dependance ajoutee : `scipy` ferait entrer une bibliotheque de calcul scientifique
dans un projet qui tient sur un processus et un fichier SQLite.

Trois lecons mesurees, et ce sont elles le module :

- **L'intervalle de Wilson ne remplace pas un test.** Sur la population reelle,
  « l'intervalle ecarte 50 % » retenait 6 lignes quand le test exact n'en retenait
  que 3, et les trois desaccords vont tous dans le meme sens — l'intervalle affirme
  plus que les donnees ne portent. Deux lignes a `0/4` dont l'intervalle monte a
  48,99 % quand quatre pertes d'affilee arrivent **une fois sur huit** (p = 0,125),
  et une ligne dont la borne basse vaut **50,011 %** : elle franchit le seuil d'un
  centieme de point. Une ligne qui bascule sur la troisieme decimale n'est pas un
  fait. L'intervalle reste rendu — c'est la precision, et elle se lit d'un coup
  d'oeil sur une barre — mais c'est le **test** qui decide.
  - La correction de continuite existe et est **hors par defaut** : l'intervalle
    montre une precision, il ne decide pas. Son symptome de mauvaise ecriture est
    l'**asymetrie** — les deux signes du terme sous la racine sont opposes, et les
    confondre decale la borne haute de six points sur une proportion de 0,5 sans
    toucher la basse. Un test le verifie sur `5/10`.
- **La reference n'est pas 50 %, c'est le complement.** Un taux de reussite de 50 %
  n'est un repere pour rien : sur un 1N2 la base tourne autour de 33 %, sur un
  handicap asiatique autour de 50 %, sur un total tout depend de la ligne. Comparer
  chaque tranche a pile ou face teste une hypothese que personne n'a formulee. La
  question actionnable est « cette tranche differe-t-elle de ce que je fais **par
  ailleurs** », donc un 2x2 de Fisher contre le reste de la meme population.
  - Le changement **retourne le verdict la ou il compte** : `22/34` passe de p = 0,12
    a p = 0,0013, `18/26` de p = 0,076 a p = 0,0023. La reference d'origine declarait
    non prouve ce que les donnees etablissent — l'inverse exact du defaut corrige.
  - Fisher exact plutot qu'un chi2 sur une table 2x2 : les effectifs sont petits —
    `0/4` contre `30/63` — et l'approximation normale y est fausse dans le sens qui
    trompe.
- **L'axe se teste avant la ligne.** Un axe est une **partition** : « conf 3 contre
  le reste » et « conf 4 contre le reste » sont le meme test ecrit deux fois, et les
  compter comme deux essais gonfle la multiplicite. Un omnibus par axe, puis
  decomposition en lignes seulement s'il passe.
  - `RateRow.discriminant` porte les deux conditions dans cet ordre.
    `_with_complements` remplit tout `Analysis.groups` **en un seul endroit** : axe
    par axe, il aurait ete oublie au premier axe ajoute — le piege exact de
    `RateRow.merge`, dont les deux fusions recopiees a la main n'avaient pas suivi
    les champs ajoutes apres elles.
- **Et cet omnibus doit etre exact — appris en se trompant.** La premiere version
  employait un chi2 d'homogeneite. Son hypothese — des effectifs attendus au-dessus
  de 5 — est fausse sur une page qui porte quinze lignes sous huit paris, c'est-a-dire
  fausse exactement la ou le test decide.
  - Le degat, mesure sur l'axe « niveau de competition » : chi2 **p = 0,083**, l'axe
    ne passe pas et « 1re division — Europe » (`2/13` contre `28/54`, p = 0,028) est
    demotee — cas qui figurait ici meme comme **exemple de la regle qui fonctionne**.
    Fisher-Freeman-Halton exact donne **p = 0,044** : l'axe passe, la ligne tient.
  - L'erreur ne cassait rien. Elle retirait une ligne de la page en presentant son
    retrait comme une regle — la forme la plus couteuse qu'un defaut puisse prendre
    ici, puisqu'elle se lit comme une justification.
  - **Exact par enumeration sous `EXACT_BUDGET` (200 000 tables), chi2 au-dela, et
    `Omnibus.exact` dit lequel a servi.** Comptes reels : confiance 27 tables, palier
    144, famille 1 078, niveau 3 113 — tous sous la milliseconde ; le marche en
    compte **777 437**, soit 632 ms, ce qu'une page ne peut pas payer. Le nombre de
    tables se compte d'abord, par une recurrence qui ne les construit pas. Cout final
    de `analysis()` sur 114 selections : **67 ms**.
  - Un verdict exact et une approximation ne se lisent pas au meme titre : c'est leur
    confusion qui a produit le faux negatif, et la sortie porte donc la distinction.

**Unilateral seulement sur une hypothese posee d'avance.** Le gabarit de prompt
affirme qu'une confiance 4 doit battre une confiance 3, et qu'un SAFE doit battre un
FUN, avant que la moindre donnee existe : ces deux tests sont **diriges**, donc
unilateraux, et forment un lot confirmatoire de deux. Partout ailleurs le test est
bilateral — choisir la direction apres avoir vu le resultat revient a diviser son
seuil par deux en silence.

**La multiplicite se dit, elle ne filtre pas.** `benjamini_hochberg` rend un
**compte**. Appliquee aux trente lignes de la page, la correction les retirerait
toutes et reinstallerait le defaut qu'on corrige — masquer la seule ligne qui
affirme quelque chose. Et melanger les deux lots serait pire : les deux hypotheses
d'avance passent Bonferroni a 0,025 (p = 0,0015 et 0,0033), quand rien ne survit a
BH sur les vingt-neuf lignes exploratoires. Les confondre ferait passer pour du
bruit le seul resultat que cette base ait etabli.

**Le V de Cramer mesure ce que le Jaccard mesure mal.** Le Jaccard compare deux
**lignes** de deux axes — « Tennis » et « Masters 1000 », qui portent les memes
selections. Le V compare deux **partitions** : le palier et la confiance annoncee
donnent **V = 0,54**, avec 51 selections sur 67 sur la diagonale. Leurs deux
resultats confirmatoires n'en font qu'un.

**Une valeur de reference du cahier des charges etait fausse, et l'affichage ne
pouvait pas le montrer.** La borne haute de `29/47` y valait 0,740 ; elle vaut
**0,742095**, verifie par une seconde methode independante de l'implementation — les
racines du polynome `p²(n+z²) − p(2np̂+z²) + np̂² = 0`, qui est la definition meme de
l'intervalle. La page arrondit a l'entier (« [47 – 74] »), donc l'ecart y etait
invisible : c'est exactement pourquoi ce module se teste contre des valeurs
publiees et non contre ce qu'il affiche.

## La confiance en pourcentages : porte fermee, mesuree le 27/08/2026

**Resultat negatif, ecrit sous la forme qui empeche de le refaire.** La demande
revient naturellement — « ce pari a 80 % de chances, celui-la 76 % » est ce que
tout le monde veut lire — et sa refutation tient a des chiffres que personne ne
refera. Quatre arguments, et le troisieme suffit.

**1. Qui produit le nombre : les deux reponses sont fermees.** Au modele, le
prompt interdit deja en toutes lettres et a deux endroits de convertir un Elo et
un `xG` en probabilite, « la correspondance existe et tu la connais » — produire
« 80 % » est la meme operation sur un faisceau au lieu d'un chiffre. A
l'application, il n'y a rien pour le faire, et en ecrire un est le premier
interdit du projet, mot pour mot.

**2. L'effectif de calibration, et le delai.** Pour valider **une seule** tranche
de 10 points, l'intervalle a 95 % doit tenir dedans, soit une demi-largeur de
5 points : **381 selections par tranche** a 55 %, 350 a 65 %, 246 a 80 %. Le
denominateur reel est **393** — section C, anteriorite etablie, tranchees,
cotees — soit 17,9 par jour sur 22 jours. Calees sur la distribution reelle de
`1/cote` : 1,5 mois pour la tranche 50-59 % (48 % du volume), **6,6 mois** pour
la tranche 70-79 % (11 %), 39 mois pour 30-39 %. Or `changelog_mesure` porte
**17 changements de gabarit du 15 au 26/08**, un tous les 1,5 jour : une
calibration de six mois traverserait une centaine de changements de cadre, et la
population serait inhomogene par construction — exactement ce que
`framework_version` existait pour empecher de melanger.

**3. Le prix impose, et c'est l'argument decisif.** La distribution des
probabilites implicites est **etroite** : sur 446 selections tranchees, **87 %
tombent entre 40 % et 70 %**, mediane 56,5 %, et **une seule** depasse 80 %.
L'exemple qui motive la demande decrit une zone ou la base porte un pari. La ou
elle vit, un pourcentage devrait discriminer 54 % de 58 % avec un intervalle de
±10 points. Et le dilemme n'a pas de sortie : un nombre qui colle au prix ne dit
rien de plus que le prix ; un nombre qui s'en ecarte **est** l'estimation de
probabilite que la section 9 interdit.

**4. Ce qu'on perdrait, et c'est mesure.** L'echelle 1-5 est aujourd'hui
**orthogonale au prix** : `1/cote` moyenne par cran vaut 0,577 / 0,558 / 0,593 /
0,577 des crans 2 a 5 — plate — et **V de Cramer confiance x palier = 0,142** sur
393. Les deux axes sont pratiquement independants. Un pourcentage est une
grandeur d'echelle de prix : il les fusionne, et le residu au prix — le seul
chiffre interpretable de `/stats` — perd le second axe contre lequel il se lit.

Ce qui rouvrirait la question, et rien d'autre : un regime ou les selections
vivraient hors de la bande 40-70 %, c'est-a-dire un tout autre usage de l'outil.

### Le deficit du cran 3 n'est pas une propriete du cran

**Et c'est la cinquieme fois qu'un discriminant se revele etre un artefact.** Le
cran 3 porte **-8,88 sur -12,78** du deficit global, sur 195 selections : le
chiffre est juste, et la conclusion qu'on en tire — instruire le cran 3 dans le
gabarit — ne l'est pas.

La coupe est la date d'application de la **migration 026**, `2026-08-10T11:40`,
celle qui cree `angle` et `source_level`. Elle n'est pas choisie sur les
resultats : c'est une borne de schema, deja en base, meme idiome que
`TIER_PARTITION_MIGRATION`.

| Population | Tranchees | Taux | Residu au prix | Par selection |
| --- | ---: | ---: | ---: | ---: |
| conf 3, avant le 10/08 | 41 | 29 % | **-11,12** | -0,271 |
| conf 3, a partir du 10/08 | 154 | 57 % | **+2,24** | +0,015 |
| tout, avant le 10/08 | 63 | 44 % | -10,11 | -0,160 |
| tout, a partir du 10/08 | 330 | 56 % | -2,67 | -0,008 |

**Le deficit vit entierement dans les cinq premiers jours**, et il n'est pas
propre au cran 3 : toute la population d'avant le 10/08 y est. Ce sont les
selections que le dossier declare deja non propres par construction — la garde
d'anteriorite date du 11/08, et « le 11/08/2026 est la borne a partir de laquelle
une population est propre par construction plutot que par filtrage ».

**Sur la population etiquetee, rien ne distingue les cran 3 gagnants des
perdants.** Cinq axes testes — palier, type d'angle, niveau de source, sport,
marche — omnibus exact entre 0,018 et 0,31, et **zero axe ne survit a la
correction de multiplicite entre axes**. Le residu y vaut +2,24, du bon cote.

- **Le deficit n'est donc pas actionnable, et instruire le cran 3 serait
  instruire sur un artefact de la premiere semaine.** L'option ecartee est ecrite
  ici pour qu'elle ne soit pas reprise sur le seul chiffre de tete.
- **Ce qui a fait tomber la mesure est un compte de denominateur** : les
  « — non renseigne — » sortaient en tete de trois axes avec le meme `12/41` et
  le meme residu `-11,12`. C'etait **la meme population**, et une colonne jeune
  plutot qu'une propriete. Meme regle que le zero d'appariement : compter ce qui
  **entre** dans un regroupement, pas seulement ce qui en sort.

## Un taux sans son prix ne mesure rien (le coin SAFE x confiance 4)

**Resultat negatif, et il vaut d'etre ecrit : il a economise sept sessions de
collecte.** L'enchainement qui y mene est le mode de defaillance principal de
cette page, et il se reproduira.

Le chemin : les selections dont l'anteriorite est etablie donnent une notation
qui parait fonctionner (confiance 4 a 69 %, confiance 3 a 29 %) ; le test
conditionnel montre qu'aucun des deux axes ne survit au conditionnement sur
l'autre (Mantel-Haenszel exact 0,115 et 0,119) ; le signal se concentre dans une
**cellule** — `SAFE ∩ confiance 4`, 19/23 contre 16/50 ailleurs, Fisher
p = 0,000024 ; six retraits de strate ne l'entament pas. Tout cela est vrai, et
tout cela ne prouve rien.

- **Il manquait le seul controle qui compte : le prix.** 82 % n'est pas un
  resultat, c'est un chiffre sans unite tant qu'on ignore ce qui etait paye. La
  cellule est faite de favoris courts — cote moyenne **1,456**, mediane 1,41,
  toutes entre 1,25 et 1,74 — soit **69,4 % de probabilite implicite**. Le taux
  observe de 82,6 % ne depasse le prix que de 13 points, et sur 23 selections
  c'est dans le bruit : loi de Poisson-binomiale exacte sur les `1/cote`,
  **P(X ≥ 19) = 0,119**.
- **Et le complement ne se raconte pas non plus.** Dire « hors cellule, 16
  victoires pour 28,35 payees » serait refaire la meme faute dans le miroir : la
  cellule a ete trouvee en regardant le tableau, donc son complement aussi. Un
  deficit sur un sous-ensemble defini apres coup n'a pas plus de statut que les
  19/23 qu'on vient de retirer.
  - **Le seul enonce qui tienne porte sur la population entiere** : une
    partition, aucun choix, aucune multiplicite. `35` victoires pour **44,31
    payees par les prix**, soit un residu de **-9,31**.
- **Le test de residu ne rouvre pas les interdits.** Aucun devig, aucun marche
  complet, aucune projection, aucune mise : il compare des issues **tranchees**
  a des prix **deja enregistres**, meme statut que le taux lui-meme. Et il est
  **conservateur par construction** — `1/cote` porte la marge du book, donc
  surestime la probabilite vraie : la barre est trop haute, la franchir serait
  un constat solide, ne pas la franchir n'accuse de rien.
- Effectif qu'il faudrait pour trancher la cellule : **~67 selections dedans**,
  soit une quinzaine de sessions de plus. Elle en porte 23.

### La marge s'ecarte sans reconstruire le marche

On ne peut pas devigger sans le marche complet, et on n'en a pas besoin : il
suffit de calculer **l'overround qui annulerait le constat**, c'est-a-dire le
facteur par lequel il faudrait diviser les probabilites implicites pour que
l'attendu tombe sur l'observe. C'est descriptif, sans devig, sans projection, et
ca dit exactement ce que la marge peut ou ne peut pas expliquer.

| Hypothese | Attendu | Residu | `P(X <= 35)` |
| --- | --- | --- | --- |
| marge 0 % (brut) | 44,31 | -9,31 | **0,0161** |
| marge 3 % | 43,02 | -8,02 | **0,0345** |
| marge 5 % | 42,20 | -7,20 | 0,0532 |
| marge 8 % | 41,03 | -6,03 | 0,0922 |

**Marge qui annulerait le constat : 26,6 %.** Aucun book n'en approche — on est
entre 3 et 8 % sur les marches concernes. L'ampleur du deficit n'est donc pas
explicable par la marge.

**La certitude, elle, l'est encore par l'effectif, et il ne faut pas la
durcir.** Le seuil de 5 % n'est franchi que si la marge reelle est sous ~4 % :
a 5 % on est deja a 0,053. Formulation juste, a garder telle quelle : *le
deficit est directionnellement net et n'est pas explique par la marge, mais il
n'est pas encore etabli*. Fragilite mesuree : **3 victoires de plus** suffisent
a lui faire perdre son seuil a marge nulle, **aucune** a marge 5 %.

### D'ou vient le prix, et pourquoi c'est la vraie lacune de couverture

Tout le calcul repose sur `picks.price`, **un nombre auto-declare** : la cote
recopiee du bloc a la main. Le seul controle possible est `price_real`, la cote
reellement obtenue, renseignee sur **9 lignes**.

Sur ces 9 : 6 cotes obtenues plus basses, 2 plus hautes, 1 identique, pour un
ecart moyen de **-2,19 %**. Le sens compte plus que l'amplitude — une cote de
bloc **optimiste** sous-estime l'attendu, donc **le deficit reel est pire** que
celui qu'on mesure. Corrige de 2,19 %, il passe de -9,31 a -10,30
(`P = 0,0083`). Le constat est conservateur.

- **Reserve a porter, et elle est serieuse** : les 9 paires ont **toutes**
  `price_source = 'reference'`. Elles mesurent l'ecart entre un prix de book de
  reference et le prix obtenu — exactement ce que la migration 030 a ete ecrite
  pour separer — et ne disent **rien** des selections cotees chez le book
  principal, qui sont le gros du lot. Trois des neuf sont d'ailleurs encore en
  attente.
- C'est le **vrai enjeu de couverture de cette base**, bien avant `angle` ou
  `source_level` : sans `price_real` systematique, le chiffre de tete de la page
  repose sur une saisie que rien ne verifie.

### Une analyse est datee, et un verdict qui bouge n'en est pas un

Que l'axe « niveau de competition » passe de `p = 0,0443` a `p = 0,0195` sur
**six resultats saisis** n'est pas un incident de lecture : c'est une mesure. Un
verdict qui bouge d'un facteur deux sur six saisies n'est pas un verdict.

Deux consequences, a tenir :

- toute analyse porte son `as_of`, la page dit « arrete au … », et un
  pre-enregistrement — s'il en existe un un jour — gele un instantane ;
- chaque ligne porte un **indice de fragilite** : combien de resultats il
  faudrait retourner pour que son verdict bascule. Le calcul est trivial et
  l'information est indispensable — une ligne discriminante a fragilite 1 et une
  a fragilite 12 ne se lisent pas pareil, et la page n'en fait aujourd'hui
  aucune difference.

### L'anteriorite, et le compteur qui la rend saisissable

**Ce n'est pas un filtre de proprete : c'est ce qui fait du prix un prix.** Une
selection enregistree apres le coup d'envoi porte une cote enregistree apres le
coup d'envoi, et son `1/cote` ne decrit plus le marche d'avant-match — donc plus
rien de comparable a un resultat. Tout le residu en depend, et c'est pourquoi ce
filtre passe avant lui.

- **Derive a la lecture, selection par selection** (`_antecedence`,
  `Pick.antecedence`) : `picks.created_at < events.commence_time`. Aucune
  colonne stockee — elle mentirait des qu'un rattachement est corrige.
- **Sens unique, et le vocabulaire le respecte.** `created_at` est l'heure
  d'**enregistrement dans l'application**, pas celle de la decision : une saisie
  tardive d'une analyse faite a temps y ressemble a un pari pose apres le match.
  La base peut prouver l'anteriorite, **jamais son absence** — d'ou « anteriorite
  non etablie », et jamais « enregistre apres coup », meme quand l'ecart atteint
  vingt-six heures. Un test le verifie sur le libelle.
- **Ce qui s'observe, et ce qui s'en deduit — a ne pas confondre.** Sur les
  selections **sans** anteriorite etablie, le residu au prix est **nul** :
  20 victoires pour 20,25 payees, p = 0,53, quand il vaut -9,31 sur les autres.
  C'est l'observation, et c'est ce que la page affiche.
  - **L'inference, au conditionnel** : un prix qui suit l'issue a ce point
    aurait ete releve en la connaissant. Elle est plausible et elle ne vit
    qu'ici — ni le code ni la page ne l'affirment.
  - **Deux causes se disputaient l'ajustement, et la calibration par bande de
    cote les separe.** Soit le prix suit l'issue (a), soit ces selections sont
    concentrees sur des cotes courtes ou `1/cote` colle mecaniquement au taux
    observe (b). Mesure : le residu des 37 est nul **dans chaque bande** —
    -0,65 sur les courtes, +0,14 sur les moyennes, +0,25 sur les longues — donc
    y compris la ou l'ajustement mecanique est impossible. Et la distribution
    ecarte (b) d'elle-meme : les 37 sont sur des cotes **plus longues** que les
    73 (mediane 1,87 contre 1,67), pas plus courtes.
  - **Observation sans statut, notee pour plus tard** : sur les 73, le deficit
    croit avec la cote — 1 victoire sur 12 au-dela de 2.00, contre 5,40 payees.
    C'est une tranche decoupee **apres coup**, donc exactement la faute corrigee
    deux fois plus haut. Elle se decrit, elle ne se conclut pas.
- **Deux populations, deux chiffres, jamais additionnes** (`residual` et
  `residual_late`). Leur **difference** est le diagnostic ; le bloc de tete ne
  porte que la premiere.
- **`reconstructed` ne filtre plus rien** et se dit « complétude du lot » : il
  porte sur le denominateur du taux de selection, pas sur la valeur des
  selections. Les confondre aurait ete fatal — seules deux sessions natives
  portent des resultats, et un filtre sur ce critere blanchirait la page.

**La garde a l'ecriture (migration 034, mise en service le 11/08/2026).** Le
compteur informait, la garde empeche — et l'information seule n'a pas suffi : le
couple horaire etait deja sous les yeux au moment de la saisie, et 37 des 110
selections tranchees ont ete posees apres le coup d'envoi.

- **Second controle bloquant du module**, apres la note d'independance, et le
  second seulement : ailleurs une valeur manquante vaut « non renseigne ».
- **Refusee a la main, decochee a l'import.** Meme traitement que la note
  d'independance, et pour la meme raison : une ligne qui echoue au milieu de
  vingt se remarque moins qu'une case qu'on doit cocher. `ParsedPick.started`
  porte le drapeau, `PickableEvent` l'a recu pour que le rapprochement par le
  voisinage se comporte comme celui par la shortlist.
  - Le motif se donne **la ou la ligne est refusee** : une colonne « Apres le
    coup d'envoi » dans le tableau d'import, un menu dans la saisie a la main.
    Elle voisine celle de l'independance — ce sont les deux seules colonnes qui
    disent pourquoi une ligne est decochee, et une meme ligne peut reclamer les
    deux, une seconde selection sur un match commence.
  - **Le motif est relu sur la feuille de session**, comme la note
    d'independance et pour la meme raison : une donnee que rien ne lit finit par
    se retirer. Celle-ci decide de la lecture du prix — une selection `live`
    porte une cote qui n'a jamais ete un prix d'avant-match, et aucune autre
    ligne de la feuille ne le dit. `Pick.late_label` resout le libelle depuis
    `LATE_REASONS`, jamais recopie cote gabarit.
  - **Un match fini se saisit comme un match commence** : c'est le meme etat, et
    la garde ne connait que lui. Le voisinage de `pickable_events` couvre 24 h
    avant et 48 h apres, `query` leve la fenetre au-dela — rien a ajouter.
  - La mention `(ref.)` traverse l'import intacte, et c'est ce qui rend le
    chemin utilisable : ces selections-la sont justement celles dont le prix ne
    vient pas du book principal, donc celles ou le palier repose sur une cote
    qu'on n'obtiendra pas.
- **Le refus n'est pas absolu : il reclame un motif**, sur un chemin qu'on veut
  rare. Sans lui, la garde dirait combien de selections sont tardives et jamais
  **pourquoi** — or les deux cas legitimes ne se ressemblent pas, et c'est leur
  melange qui a rendu les 37 inexploitables :
  - `differee` — decision prise a temps, saisie tardive. L'etiquette est
    **valide**, le prix douteux.
  - `live` — pari reellement pris en cours de match. Les deux sont invalides.
  - **Pas de troisieme choix, pas de texte libre** : ils feraient retomber dans
    le melange que cette colonne existe pour defaire.
  - **Et il l'a pourtant ete pendant deux jours, parce que le motif n'avait
    aucune surface pour se saisir.** `add_pick` l'acceptait, `ParsedPick` le
    portait, la ligne se decochait avec le bon message — mais ni le tableau
    d'import ni le formulaire manuel n'offraient les deux valeurs, et les deux
    routes ne transmettaient pas le champ. Un lot de six selections rendait
    « Rien d'importe » avec, en face de chaque ligne, une question a laquelle
    rien ne permettait de repondre. La garde etait donc **absolue**,
    exactement sur le chemin qu'elle devait laisser ouvert.
    - **Le service et sa surface se livrent ensemble, ou la regle qu'on croit
      poser n'est pas celle qui s'applique.** Rien n'a casse : les tests du
      service passaient — ils appellent `add_pick` directement — et ceux de
      l'import montaient tous des matchs a venir, un commentaire de fixture
      allant jusqu'a ecarter le cas comme relevant « d'un test dedie » qui
      n'existait pas. Meme forme que B1, disparu neuf lots durant.
    - Ce qui l'aurait attrape est ce qui garde maintenant les deux chemins :
      un test qui poste le formulaire et **relit la base**, jamais un test qui
      appelle le service.
- **La garde ne valide pas le passe.** Elle ne touche que l'avenir : les 37
  restent ecartees definitivement, et le **11/08/2026** est la borne a partir de
  laquelle une population est propre **par construction** plutot que par
  filtrage. Tout pre-enregistrement ulterieur — a commencer par celui du biais
  favori-outsider — se mesure sur des selections nees apres elle.
- Corollaire mesure : **la forme canonique d'une selection, dans tout le code de
  test du projet, etait un pari pose sur un match deja commence.** La convention
  de test refletait la pratique, et c'est la meme habitude qui produit les 37.
  Cent six tests ont casse quand la garde est arrivee.

**Le compteur vivant, et pourquoi il compte plus que le filtre.** Un filtre dit
ce qui a ete perdu ; un compteur evite de le perdre. `Worksheet.coverage_line`
annonce les deux couvertures sur la feuille de session — « 3 sur 8 sans
anteriorite etablie · 5 sur 8 sans cote obtenue » — au moment ou l'on saisit,
seul instant ou l'information arrive assez tot pour changer quelque chose.

- **La cote obtenue est la vraie lacune, avant `angle` et `source_level`.** Le
  chiffre de tete repose sur `price`, un nombre recopie a la main ; `price_real`
  est le seul controle possible, et il est renseigne sur **9 lignes sur 116**,
  toutes issues d'un book de reference. Le controle qui valide ou invalide le
  resultat principal du projet ne peut donc pas etre fait sur l'existant, et il
  ne le sera jamais retroactivement.
- Rien quand tout est couvert : un compteur a zero sur chaque session serait du
  bruit, et c'est le manque qui doit se voir.

### Le deficit croit-il avec la cote ? Non concluant, et c'est la reponse

Le tableau de calibration montre un deficit qui croit monotonement avec le
prix — `-1,5` sur les cotes courtes, `-3,4` sur les moyennes, `-4,4` sur les
longues, soit un tiers du deficit total dans douze selections. Le mecanisme
candidat est nommable et connu : sur les cotes longues la marge est plus elevee
et le biais favori-outsider joue contre le parieur.

**Ce n'est donc pas une tranche trouvee en fouillant, c'est une variable ordonnee
continue** — et elle se teste par une **tendance**, pas en comparant trois bacs :
une seule statistique, aucune multiplicite. Test de score de la pente dans
`logit(P) = logit(1/cote) + a + b·cote`, l'ordonnee a l'origine restant un
parametre de nuisance qui porte le deficit global deja mesure.

- **Unilateral p = 0,031, bilateral p = 0,062** — et c'est le bilateral qui fait
  foi. La direction est predite par un mecanisme connu, mais elle a aussi ete
  **vue dans le tableau avant d'etre testee** : prendre l'unilateral reviendrait
  a diviser le seuil par deux apres avoir regarde. Robuste a la transformation :
  `log(cote)` donne 0,035 / 0,070.
- Le constat **n'entre donc pas dans le bloc de tete**. Il attend, et l'en-tete
  replie dit ce qu'il faudrait : ~165 selections a anteriorite etablie pour 80 %
  de puissance, contre 73 — soit une dizaine de sessions.
- Ce qu'il vaudrait s'il tenait : ce ne serait plus « je perds contre les prix »
  mais « je perds contre les prix, et principalement la », avec une consequence
  operationnelle immediate — les douze selections a cote >= 2.00 portent 47 % du
  deficit. Aucune autre mesure du projet ne debouche sur une action.
- **Fragilite de la tranche longue : 2.** `1/12` contre 5,40 payees donne
  p = 0,0082, et deux victoires l'effacent. C'est aussi pourquoi elle ne se porte
  pas seule.

### La calibration, et la puissance qu'elle n'a pas

Deux precautions a garder attachees a ce tableau :

- **Trois bandes sont un decoupage**, donc un choix. Verifie sur un second
  decoupage — terciles d'effectif au lieu des seuils ronds — et la nullite des
  37 tient : `-1,00`, `+1,51`, `-0,76`, avec une tendance a `p = 0,86`. Le
  gradient des 73 s'y renforce au contraire (`-0,50`, `-1,49`, `-7,32`).
- **« Residu nul dans chaque bande » a une puissance tres faible** sur des bacs
  de 5, 21 et 11 selections : un ecart de deux victoires y passerait inapercu.
  La formulation juste est **rien ne s'ecarte, et l'effectif ne permettrait de
  detecter qu'un ecart important**. C'est suffisant pour preferer (a) a (b) —
  d'autant que la distribution des cotes va contre (b) — mais ce n'est pas une
  preuve d'ajustement parfait.

### La section des regroupements est devenue un compteur de progression

**Elle avait pour but de faire remonter la ligne informative ; on a etabli qu'il
n'y en a pas.** Confiance et palier sont un meme phenomene, et le seul contraste
apparent etait des favoris courts que leurs prix annoncaient deja. Elle n'est pas
supprimee pour autant — le volume s'y accumule et elle conclura peut-etre — mais
elle passe **sous le bloc de tete, repliee par defaut**, et son en-tete porte le
seul texte utile du bloc : la distance a laquelle elle conclura.

- **Trois conditions pour qu'une ligne soit portee**, dans cet ordre : l'axe
  passe son omnibus **exact**, il survit a la correction de multiplicite
  **entre axes**, et la ligne s'ecarte de son complement. **Jamais un intervalle
  de Wilson.**
  - La correction se fait entre **axes** et non entre lignes : corriger par ligne
    compterait chaque partition N fois et gonflerait le nombre d'essais —
    exactement la raison d'etre de l'omnibus.
- **Le critere d'acceptation est une propriete, jamais un nombre.** Il valait
  « 1 ligne sur 30 » a 104 selections, « 3 sur 29 » a 67, et la base bouge chaque
  jour : un nombre ecrit dans un test serait faux le jour ou on le recette. Les
  tests montent leur propre lot et verifient la regle.
- **La fragilite par ligne est bloquante.** Un chiffre sans son effectif se lit
  comme un fait, et l'effectif ne suffit pas — une ligne a quarante paris peut
  tenir a un seul resultat. Mesure qui le prouve : `ULTRA FUN 0/7` est portee, et
  sa fragilite vaut **1**. Le calcul refait **les deux tests**, l'axe pouvant
  ceder avant la ligne.
- **`Horizon` planifie, il ne conclut pas.** Une version portait un plafond de
  sessions au-dela duquel une question etait declaree tranchee : **cela
  transformait une propriete de l'agenda de saisie en verdict statistique**, et
  le defaut s'est montre tout seul — le meme calcul a annonce « rien a mesurer »
  puis « atteignable » sur les memes donnees, lues a travers deux populations.
  Le plafond est retire. Un horizon dit quand regarder a nouveau, rien d'autre.

### Faut-il deux echelles ? Une equivalence, pas un horizon

**« Quarante-neuf sessions » repond a *quand saurai-je*** — une question sur le
rythme de saisie, pas sur les donnees. La question produit est : **quel ecart
residuel justifierait le cout d'un second axe ?** Elle se decide **avant** de
regarder les donnees, une fois, et ne bouge pas avec l'echantillon.

`EQUIVALENCE_MARGIN` vaut **10 points de taux**. En dessous, deux echelles a
saisir, deux jeux de libelles a tenir et le poids de prompt associe ne se
justifient pas.

- **Un test classique ne conclut jamais « il n'y a rien »** : il echoue a
  rejeter, ce qui n'est pas la meme chose. Une equivalence, elle, se conclut par
  l'affirmative — l'ecart tient **entierement** dans la marge.
- L'intervalle est celui de **Newcombe**, bati sur les Wilson des deux
  proportions : meme famille que ce que la page affiche deja, et l'approximation
  normale sur une difference sort de `[-1, 1]` la ou elle compte.
- `TOST_Z` vaut **1,645 et non 1,96** : une equivalence se conclut par deux
  tests unilateraux, donc par l'intervalle a `1 - 2α`. Prendre celui a 95 %
  testerait a 2,5 % de chaque cote.
- **Mesure actuelle : non concluante, et c'est l'inverse de ce que le plafond
  annoncait.** A confiance fixee, le palier ecarte de **-12 points**, intervalle
  **[-37 ; +13]**. Il sort largement des dix points, donc on ne peut pas encore
  conclure qu'une seule echelle suffirait. Le verdict precedent — « le second axe
  n'apportera rien de mesurable » — etait un artefact du seuil.
- L'ecart mesure est le **residuel**, dans la strate la plus fournie de l'autre
  axe : l'ecart brut recopierait ce que l'axe dit deja tout seul.
- **Une equivalence se merite par du volume** : deux groupes identiques mais
  minuscules ne la donnent pas. C'est la propriete qui rend le test honnete.

### Le residu vit en tete, et nulle part ailleurs

**Il etait deja affiche ligne par ligne, et personne ne l'avait vu.** Le detail
chiffre portait deux colonnes — « Taux implicite » et « Écart » — depuis
plusieurs lots : un residu au prix sur trente lignes, **sans test, sans
correction de multiplicite, sans fragilite**. Il a fini par etre « decouvert »
cinq lots plus tard, par un calcul refait a la main sur la population entiere.

C'est le meilleur argument possible pour la regle qui les retire : **trente
residus non testes ne se lisent pas, ils decorent**. Les colonnes, leur note de
mode d'emploi et les champs qui les alimentaient (`priced`, `implied_sum`) sont
partis ensemble — une donnee que rien ne lit finit par se retirer, et celle-ci
etait revenue une fois de trop.

### Ce que le chiffre de tete n'est pas

- **Le taux de reussite nu a cesse d'etre un indicateur.** Il est rendu en
  **decompte** — « 35 sur 73 » — et non en pourcentage : un pourcentage en gros
  invite a etre compare a quelque chose, et on a etabli qu'un taux nu n'est
  comparable a rien. Un decompte n'invite a rien.
- `.hero-figure` est **reserve au residu**. Le seul chiffre interpretable de la
  page ne peut pas partager son poids visuel avec celui qui ne l'est pas : ils
  se sont cotoyes a la meme taille, a quinze pixels d'ecart, pendant un lot.
- **La reference a 50 % a disparu de la page**, et ce n'etait pas une
  formulation. Elle y comptait les lignes « qui n'ecartent pas 50 % » — une
  hypothese que personne n'avait formulee, la base variant avec le marche joue.
  Le socle avait change de reference sans que le rendu suive.
- **L'en-tete disait « toutes les selections »** en en ecartant trente-sept.

### Trois libelles que le socle avait rendus faux, et qui restaient

- **« SCORE EXACT 100 % » sur deux constats** — la specification d'origine
  demandait deja son intervalle, et il n'avait jamais ete fait. Il tombe
  desormais sous la meme regle que les lignes portees : **decompte, intervalle
  et fragilite dans la meme phrase** — `2 sur 2 · intervalle [34 – 100] · un
  resultat retourne le ramenerait a 50 %`. L'effectif seul ne corrigeait pas la
  lecture ; « 100 % sur 2 » reste un fait a l'oeil, dix lignes sous un
  avertissement qui dit a la page qu'elle manque de recul.
- **« cible 51 – 60 % » est devenu « bande global +3 → +12 → 51 – 60 % ».** Ce
  n'est pas une reformulation : le mot **cible** promettait un pilotage — on
  regle la bande, on resserre le cran — alors que la question de savoir si cette
  echelle doit exister n'est **pas tranchee**, l'equivalence contre le palier
  allant de -37 a +13 points. Le libelle dit maintenant ce que la bande **est**
  mecaniquement, avec sa valeur resolue, et ne promet rien de ce qu'elle sert.
  Un cran sans bande le dit toujours : une case vide par decision ne se
  distingue pas d'un oubli.
- **Le bandeau de recouvrement est descendu sous le pli**, avec les axes qu'il
  concerne : il les commentait au-dessus alors qu'ils sont replies, donc il
  parlait de lignes que le lecteur ne voyait pas.

### Ce que la page dit d'une session, et ce que ca coute

- **Un lot entierement passe est un incident, pas une journee severe.** Zero
  selection sur un lot parti a l'analyse ne se distingue pas d'un rendu jamais
  colle ni d'un import oublie — le cas s'est produit, 34 matchs le 04/08, et la
  ligne se confondait avec un tri exigeant. Passer est un resultat valable et
  attendu ; passer **tout** se signale.
- **« Sel./match » mesure une correlation, pas une densite.** Deux selections
  sur la meme rencontre ne sont pas deux observations, et le residu du bloc de
  tete suppose l'independance. Mesure : **5 des 73** partagent un match, et
  **quatre des cinq paires sont tombees du meme cote**.
  - La borne conservatrice se calcule (`clustered_p_value`) : chaque groupe
    devient un tirage unique rendant tous ses succes ou aucun. L'esperance ne
    bouge pas, la variance monte. `0,0161` devient `0,0227`, et `0,0532`
    devient `0,0656`.
  - **L'effet est modeste, donc il se mentionne** — dans le bloc de tete et non
    en note, comme la fragilite : aucun verdict ne bascule, mais le lecteur doit
    savoir que le chiffre suppose quelque chose de faux. Il grossira si les
    selections multiples se multiplient.
  - Le projet avait deja une notion d'independance — la note obligatoire sur un
    second pick du meme match — et elle n'avait jamais servi a l'analyse.
- **« Prompt / match » ne se lit qu'a regime constant.** Le cout fixe du cadre
  est passe de 106 a 1 208 unites par match en une semaine ; mais le bloc de
  retour d'experience a ete servi sur trois sessions puis suspendu, et la garde
  d'anteriorite ne vaut que pour ce qui vient apres elle. Une courbe qui melange
  trois regimes ne dit rien : chaque ligne porte donc **bloc servi** et
  **gardee**, comme les strates de la boucle.

### `angle` et `source_level` ne sont pas sous-couverts, ils sont jeunes

Le masquage sous 30 % de couverture aurait ete une erreur, et la mesure le dit :
sur les deux dernieres sessions la couverture est de **5/5 et 10/10**. Le point
de capture existe — `picks_import` lit les colonnes « Angle » et « Source » du
tableau rendu — et chaque session depuis la migration 026 les remplit
entierement.

Les `4/104` mesuraient une colonne **jeune** contre tout l'historique. La regle
juste n'est donc pas de masquer mais de **dater** : une couverture sans sa
fenetre est le meme defaut qu'un taux sans la sienne, et la page l'a deja paye
deux fois — `as_of` et la fenetre glissante du retour d'experience.

C'est aussi ce qui les distingue de `price_real` : la cote obtenue n'a **aucun**
point de capture automatique, et sa couverture ne montera que par un geste. Une
colonne qu'aucun geste ne remplit est morte ; celle-ci ne l'est pas.

### La redondance : deux faits, pas trente signalements

Le detecteur compare des **ensembles d'identifiants** et non des comptes — deux
regroupements de 37 lignes peuvent n'avoir aucune selection commune. Ce qui a
change est la grandeur et la sortie.

- **L'indice de Jaccard remplace une double inclusion.** « Partage plus de 95 %
  de chaque cote » disait la meme chose en deux conditions ; une seule grandeur,
  symetrique par construction, se compare d'une paire a l'autre.
  - **Le seuil change de nature au passage, et c'est assume** : 19 selections
    communes sur 20 et 20 font `19/21 = 0,90`, donc un recouvrement fort, la ou
    l'ancienne regle jugeait qu'une ligne de difference de chaque cote suffisait
    a distinguer deux echantillons. Le test le dit.
- **Un recouvrement fort se nomme, un partiel se compte.** Entre 0,60 et 0,90 la
  paire n'est pas enumerée : **trente avertissements faibles reproduiraient sous
  un autre nom le defaut que cette page a mis huit lots a corriger** — des
  lignes qui n'affirment rien, en nombre tel que plus personne ne les lit.
- **La matrice existe pour qui veut verifier, pas pour se lire de haut en bas.**
  Sous le pli, et seulement les paires d'axes ou quelque chose se recouvre : une
  paire disjointe n'y figure pas.
- Ce que la page dit aujourd'hui tient donc en deux faits : `Tennis` et
  `Masters 1000` portent **les memes 33 selections** (J = 1,00), et deux autres
  paires se recouvrent partiellement, sans etre nommees.

### B1 a survecu neuf lots sans etre fait

**Le defaut d'origine, conserve sous un couvercle.** La specification demandait
de supprimer le seuil « n < 8 → afficher l'effectif a la place du taux » ; ce qui
a ete livre est le **repli** des lignes non discriminantes. Les deux repondent a
la meme gene, donc l'un a remplace l'autre dans la conversation — et personne n'a
note que le premier n'avait pas ete fait. A l'interieur du pli, le critere de
lecture etait reste `n`.

- **Le taux s'affiche desormais sur toute ligne, avec son intervalle**, dans les
  barres comme dans le detail chiffre. `RateRow.thin`, `readable`, `thin_label`
  et `inconclusive` ont ete retires — les deux premiers fondes sur `n`, le
  dernier sur une reference a 50 % dont on a etabli qu'elle ne veut rien dire.
- **Le compte des lignes maigres disparait comme categorie.** « 18 lignes
  portent moins de 8 paris » regroupait des lignes qui n'ont en commun qu'un
  effectif faible — ce qui a cesse d'etre une propriete interessante depuis
  qu'on mesure la fragilite. Le compte utile est celui de l'en-tete : portees,
  repliees, ecartees.
- `ANALYSIS_MIN_ROWS` **reste reglable et lu par le prompt**, seule surface qui
  s'en sert encore : lui, il tait ses lignes courtes, et pour une raison qui n'a
  rien a voir avec la lisibilite — une categorie annoncee a 0/7 cesserait d'etre
  produite.

**La lecon de methode** : une exigence remplacee dans la conversation par une
autre qui la recouvre partiellement disparait sans laisser de trace. Elle ne
casse rien, aucun test ne tombe, et elle se retrouve neuf lots plus tard par une
relecture de la page rendue. C'est pourquoi la relecture large est une etape et
non un exercice.

### Le bloc de tete a deux niveaux, et le test des extremites

Il portait quatre qualifications **juxtaposees au meme niveau syntaxique**.
Chacune etait justifiee ; ensemble elles produisaient l'effet inverse — un
lecteur ne compte pas les reserves, il percoit une densite, et le seul chiffre
interpretable de la page se lisait comme un chiffre qui ne vaut rien.

- **Premier niveau** : le constat, son ampleur (l'overround d'annulation), sa
  solidite (la fragilite). La frontiere du verdict y est **dite sans son
  nombre** — « il n'en faudrait que 4,5 % pour qu'il cesse d'etre net » — parce
  que poser un second `P` a cote du premier ajoutait une qualification la ou une
  phrase suffit. Elle est **derivee** (`tipping_margin`), jamais ecrite en dur :
  elle bouge a chaque session, comme la fragilite.
- **Second niveau, dans le depliant** : la marge de reference et son `P`, la
  correlation entre paris, et le rappel qu'aucun interdit n'est rouvert.
- **Le test des extremites** decide, parce que c'est ce que fait toute lecture
  rapide : premiere ligne et derniere ligne doivent donner *« un ecart, et il
  tient a trois resultats »*. Avant, la derniere ligne etait la correlation —
  la plus faible des quatre reserves.

### Le seuil du Jaccard est arbitraire, et sans effet — mesure

`COLLINEAR_SHARE` vaut 0,90, pose par convention et jamais justifie autrement.
Verifie sur les 30 paires comparables : **le classement est identique a 0,85,
0,90 et 0,95** — un fort, deux partiels dans les trois cas.

Ce qui le rend inconsequent n'est pas la stabilite mais **le trou dans la
distribution** : `1,000` puis `0,697`, et rien entre les deux. Sur ces donnees
les recouvrements sont soit totaux soit moderes. **Le jour ou une paire tombe
dans le trou, ce seuil devra etre justifie pour de bon** — et ce sera le signal.

### F2a est sans objet, et une seule de ses trois questions ne l'est pas

**La resolution ne mesure rien de neuf.** « Les crans separent-ils les issues »
est exactement ce que calcule l'omnibus, deja affiche. Et sur la population
filtree l'axe confiance n'a que **deux crans au-dessus du seuil de lecture**
(27 et 44, plus 2 selections en confiance 2) : une resolution sur deux crans est
un contraste binaire, donc l'omnibus sous un autre nom. La decomposition de
Murphy prend son sens a partir de trois ou quatre crans peuples.

Le lot s'arrete la — sauf sur un point, qu'aucun autre bloc ne pose.

**L'ordre.** L'omnibus dit si les crans separent, la fragilite dit a quel point
ils tiennent ; **ni l'un ni l'autre ne dit dans quel sens**. Un axe ou le cran
superieur fait moins bien que l'inferieur separe **exactement autant** — un test
le verifie, en comparant les deux orientations de la meme table.

- `ordinal_trend` est un Cochran-Armitage **unilateral**, et l'unilateral est
  licite : la direction est declaree par le gabarit de prompt avant qu'aucune
  donnee existe. Meme argument que les deux tests confirmatoires.
- **Mesure, et elle donne au filtre d'anteriorite sa justification la plus
  directe** : sur la population filtree, `p = 0,013` pour la confiance et
  `0,0001` pour le palier — les deux echelles ordonnent. Sur les selections
  **ecartees**, `p = 0,90` et `0,93` : l'ordre y est **inverse**.
- **Piege de la queue** : la premiere version rendait la queue superieure quel
  que soit le signe, si bien qu'une echelle parfaitement inversee ressortait a
  `p = 0,0006`. Un unilateral doit rendre un **grand** `p` sur un `z` negatif —
  c'est le cas ou l'echelle est ordonnee a l'envers, et le declarer significatif
  serait exactement l'erreur que ce test existe pour attraper. Le test le tient.

### La portee d'affichage du residu

**Au niveau de la population, en chiffre de tete. Pas en colonne sur trente
lignes.** Sur trente lignes il redevient exactement le probleme corrige plus
haut : trente residus, ~1,5 « significatif » par hasard, et une nouvelle cellule
a trouver. Par ligne, il ne s'affiche que sous la machinerie complete — omnibus
d'abord, exact, BH entre axes, fragilite affichee.

Et **le chiffre de tete change de nature**. Le taux de 48 % n'etait pas
interpretable : il ne distingue pas une methode qui bat le marche d'une methode
qui prend des favoris. Le residu au prix, lui, l'est. C'est lui qui occupe le
bloc de tete, le taux devenant une description a cote.

**La lecon de methode, qui vaut au-dela de ce cas** : une hypothese formulee
comme « cette etiquette bat les autres etiquettes » a un **mode de reussite
vide**. Si l'etiquette selectionne des favoris courts, elle gagnera toujours,
et le test passera sans que l'etiquette ait rien apporte. La question juste est
toujours le **residu au prix** — l'etiquette ajoute-t-elle quelque chose a ce
que le prix disait deja.

- Corollaire sur les sous-tests de robustesse : six retraits de strate ne sont
  **pas six preuves**. Ce sont six vues du meme echantillon, toutes descendantes
  du meme p, fortement correlees. C'est une robustesse au confondant teste, pas
  une accumulation d'evidence — et le plus fin d'entre eux tombait a 6
  selections dans un bras, ou un resultat de plus deplace le p d'un facteur dix.
- **La base est vivante, et une lecture est datee.** Entre le debut et la fin de
  cette analyse, elle est passee de 114 a 116 selections et de 104 a 110
  tranchees ; l'axe « niveau de competition » de p = 0,0443 a p = 0,0195, ce qui
  lui a fait franchir un seuil de Benjamini-Hochberg qu'il ne franchissait pas.
  Toute mesure citee ici vaut pour son etat, pas pour toujours.

**Le bloc de taux reste retenu** (`FEEDBACK_SUSPENDED`). La raison ne depend
d'aucune hypothese en cours : le transmettre rend ininterpretables les mesures
qu'il contient, et ce n'est pas theorique — **9 prompts de 3 sessions l'ont
fait**, quand les seuils valaient encore 10 et 4, dont un annoncant « confiance
4 — 10/15, 67 % » juste avant que soient produites les etiquettes qu'on mesure
aujourd'hui a 82 %. Une constante et non un reglage : le garde-fou d'origine
etait un couple de seuils, et il a cede sans que personne le decide.

## Ce que la specification demandait, et ce que la mesure a impose

**Fait de methode, pas de satisfaction.** Sur trente-quatre points specifies,
sept ont ete abandonnes ou faits autrement — chacun contre une mesure, jamais
par commodite. Ce n'est pas un taux d'echec : c'est le rapport ordinaire entre
ce qu'on croit savoir en ecrivant un cahier des charges et ce que les donnees
autorisent.

Les trois qui comptent viennent de la **meme faute**, et c'est celle que la page
commettait sur ses propres donnees — prendre un chiffre pour un fait sans
regarder ce qu'il compte :

| Specifie | Etabli |
| --- | --- |
| une reference a 50 % | erreur de categorie ; le complement est la bonne reference |
| masquer les colonnes sous 30 % de couverture | colonne **jeune**, pas morte : c'est la fenetre qui manquait |
| un critere d'acceptation en valeur absolue | faux le jour de la recette ; devient une propriete |

**Et l'inverse merite d'etre note aussi** : les elements aujourd'hui centraux du
produit ne figuraient dans **aucune** specification. Le residu au prix, le test
d'equivalence, la garde a l'ecriture et son motif, la fragilite par ligne, le
compteur de couverture a la saisie — tous sont nes d'une mesure qui a rendu la
question posee insuffisante.

La consequence pratique : **une specification plus courte, qui prevoit plus de
place pour ce que la mesure fera apparaitre**, vaut mieux qu'une specification
exhaustive dont un cinquieme sera renverse. Et une exigence remplacee en cours
de route par une autre qui la recouvre partiellement disparait sans laisser de
trace — B1 a survecu neuf lots ainsi.

## Toute lecture se fait sur une copie de la base

**Aucune verification, aucun rendu, aucune execution qui demarre l'application
ne touche `data/myassistantbet.db`.** On copie, on lit la copie. Si un controle
exige vraiment la production, il se demande d'abord.

Constate en livrant E1 : un `TestClient` lance pour verifier le rendu reel du
bloc de tete a demarre l'application, donc **applique la migration 033** sur la
base servie. Non destructive et de toute facon prevue au prochain redemarrage —
mais non decidee.

Deux raisons qui vont plus loin que l'ecriture accidentelle, et ce sont elles
qui font la regle :

- **La base bouge pendant qu'on travaille.** Elle est passee de 114 a 116
  selections et de 104 a 110 tranchees au cours d'une seule analyse, l'axe
  « niveau » suivant de 0,0443 a 0,0195. Un releve sur production n'est donc pas
  reproductible, et deux mesures d'un meme rapport peuvent porter sur deux
  populations differentes sans que rien ne le signale.
- **C'est ce qui rend `as_of` honnete plutot que decoratif.** Une lecture faite
  sur une copie datee porte vraiment la date qu'elle affiche.

## L'historique des cotes (migration 048) : la table seule, et pourquoi rien ne la lit

**Ce chantier n'affiche rien, ne lit rien, n'alerte sur rien, et ne pose aucun seuil.** Il
arrete une perte : `scan.replace_odds` fait un DELETE puis un INSERT par (evenement, book,
marche), donc **seul le dernier releve survit** et l'etat d'avant n'existe nulle part une
heure apres un scan. Meme defaut que `commence_time` avant la migration 040, meme forme de
correctif — on garde la valeur precedente et l'instant du constat.

- **Deux bornes, et il faut les deux.** « Le prix a change entre 11h22 et 15h06 » n'est pas
  « il a change a 15h06 » : sans `previous_fetched_at`, tout mouvement parait instantane et
  un scan quotidien ferait passer une derive de vingt-quatre heures pour un decrochage.
  `observed_at` s'y ajoute — le fournisseur date son releve, nous datons notre lecture.
- **Le book et le marche sur chaque ligne.** Mesure du 14/08/2026 sur les seuls releves
  comparables de la base — 63 issues de `h2h`, 26 h d'ecart moyen : **1,0 % de mouvement
  moyen**, 2 au-dela de 10 %. Tous marches confondus la moyenne monte a 23 %, entierement
  a cause des cotes longues : une derive sur un score exact a 34.00 n'est pas comparable a
  une derive sur un 1N2, et les melanger rendrait toute lecture fausse.
- Seuls les prix **qui changent** sont ecrits. Un prix stable ne dit rien qu'`odds` ne dise
  deja ; un premier releve n'est pas un mouvement, et l'ecrire ferait passer chaque arrivee
  de match pour une derive.

### Aucune surface avant que le lot soit fige, et ce n'est pas une precaution technique

Le preambule limite les cotes a **deux usages**, et un troisieme a deja ete propose puis
refuse — trier les dossiers de recherche par le prix, qui rendrait le tri circulaire. Une
derive affichee serait ce troisieme usage.

**La frontiere « l'UI jamais le prompt » ne suffit pas**, et c'est le point a retenir : une
alerte sur le board oriente au moment ou le lot se constitue et ou les dossiers se
choisissent. **La contamination passe par le lecteur, pas par le texte** — le tri
circulaire se produit un cran en amont, sans qu'aucune regle du gabarit soit violee.

Donc : **rien sur le board, rien sur la shortlist, rien sur aucune surface consultee avant
que le lot soit fige.** Si quelque chose s'affiche un jour, ce sera sur une surface
**post-selection**. Un test garde la porte : il echoue des qu'un fichier autre que `scan.py`
mentionne `odds_history`, et il doit alors etre traite comme une decision a prendre, pas
comme un detail a corriger. Le premier reflexe dans six mois sera une colonne sur le board.

### L'usage cible, et la mesure qui le validerait

« Combien de cotes bougent, de combien, a quelle heure » **ne repondrait pas a la
question** : ca dirait que les cotes bougent, ce qu'on sait deja, et on aurait un detecteur
de mouvement sans savoir ce qu'il detecte — donc un seuil d'alerte choisi au juge, le
`p = 0,0148` dans une autre couche.

**La question est : un mouvement au-dela d'un seuil precede-t-il un fait date et
trouvable ?** Une composition publiee, une absence annoncee, un forfait. Sans cette
validation, la table ne doit rien produire.

- **Cette mesure demande un travail manuel sur un echantillon** : prendre quelques dizaines
  de mouvements notables, chercher ce qui les a suivis dans les heures d'apres, et compter.
  **Personne ne l'automatisera** — il n'existe aucune source qui date les faits sportifs
  assez finement pour les apparier a un horodatage de cote.
- Piste, et **piste seulement, pas une decision** : la derive ne signale pas un match, elle
  **date un fait**. Le gabarit dit deja qu'un releve « avant les compositions » decrit un
  marche que le bloc ne reflete plus ; un mouvement horodate dirait **a quelle heure
  chercher** ce qui l'a fait bouger. Ce serait un outil de recherche — il aide a trouver le
  fait, il ne remplace pas le fait, et la selection s'appuierait sur la compo trouvee et
  jamais sur le mouvement qui a fait chercher. Formule ainsi, ca ne contredit pas « les
  cotes ne sont jamais un argument en soi ». Ca reste a valider par la mesure ci-dessus.

## Le biais d'exposition : resultat negatif, et la lecture ne le tranchera jamais

**Question posee le 14/08/2026** : un match rendu plusieurs fois dans la meme session
traverse plusieurs lots analyses par des instances qui s'ignorent. La selection retenue
dessus est-elle le **maximum de N tirages** — donc un biais — ou une **convergence** —
donc un signal ? Mesure du 14/08/2026 sur les 103 selections tranchees a anteriorite
etablie, exposition reconstituee pour les 103.

- **Resultat : rien.** Tendance du residu au prix avec le nombre de rendus,
  `z = +0,435`, `p = 0,664` **bilateral**. Le signe est positif — les matchs tres vus
  feraient plutot mieux, donc convergence — mais un signe a `p = 0,66` ne se lit pas.
  Le residu par valeur exacte de rendus n'est d'ailleurs pas monotone : `-3,8` a un
  rendu, `-5,2` a deux, `+1,5` a quatre, `-1,8` a huit.
  - Bilateral, et c'est une condition : le sens n'etait predit ni dans un sens ni dans
    l'autre. Prendre l'unilateral apres avoir vu le signe reviendrait a diviser le seuil.
  - Une **tendance** et non des tranches comparees : le nombre de rendus est une variable
    ordonnee, donc une seule statistique et aucune multiplicite. Un seuil « vu une fois
    contre vu N fois » choisi apres avoir regarde aurait ete le `p = 0,0148` une seconde
    fois.
- **Les deux chiffres de puissance, et c'est eux qui concluent** : cet echantillon ne
  detecte qu'une pente `|b| >= 0,267` par rendu — environ **6,7 points de probabilite par
  rendu supplementaire**, soit 47 points entre un match vu une fois et un match vu huit
  fois. Aucune hypothese plausible ne predit un effet pareil. Et pour etablir l'effet
  **de la taille observee**, il faudrait **~2 100 selections**, vingt fois la base.
  - Les deux disent la meme chose dans deux directions : **l'echantillon ne peut voir
    qu'un effet enorme, et l'effet observe est minuscule.** Ce n'est donc pas « pas assez
    de donnees », c'est un **plan d'observation inadapte**.
- **La raison de fond, et elle est definitive : l'exposition est presque collineaire a la
  competition.** Elle ne mesure pas la taille de l'affiche mais **le decoupage** — un
  match vu huit fois est un match dont la competition a ete fractionnee en huit prompts ce
  jour-la. Conference League 5,8 rendus en moyenne, Europa League 4,2, tournois de tennis
  2,2 a 2,3, tout le reste 1,0 a 2,0 ; au-dessus de la mediane, 32 % des selections
  tiennent sur la seule Europa League. Deux des cinq competitions fournies n'ont **aucune
  variation d'exposition** — la Champions League Qualification est a 2 partout — donc
  elles n'apportent rien a l'estimation de la pente.
  - **Aucun volume de lecture ne separera les deux.** Ce qui trancherait est une variation
    d'exposition **a competition fixee**, c'est-a-dire un decoupage volontairement varie :
    une **intervention**, pas une lecture. Refaire cette mesure avec plus de donnees ne
    changera rien tant que le decoupage reste subi.
  - **Et tracer le decoupage ne suffit pas.** Un lot nomme, un registre de ce qui a deja
    ete soumis, une trace des prompts : rien de tout cela ne debloque la mesure. Un
    decoupage **trace** reste un decoupage **subi** tant qu'il n'est pas fait varier
    exprès. La collinearite est une propriete du plan d'observation, pas de son
    instrumentation — l'outillage n'en est que la condition prealable, jamais
    l'intervention. Quiconque implementerait le lot nomme en croyant rouvrir cette
    question trouverait la collinearite intacte.
  - Le palier, lui, ne confond rien : 3,0 rendus moyens en SAFE, 2,4 en FUN, 3,2 en ULTRA.
- **Reserve** : les sept premieres sessions n'ont pas de `prompt_events`, et leur
  exposition a ete reconstruite depuis les en-tetes de prompts archives, **par affiche**.
  Deux rencontres homonymes le meme jour n'en feraient qu'une. Aucune n'a ete detectee,
  mais le rattachement y est moins sur que sur les sessions 8 a 11.
- **Ce que ca implique pour le reste de la page** : la mesure n'exclut pas un biais, mais
  elle montre que s'il existe, il est trop petit pour expliquer quoi que ce soit de ce qui
  est mesure ailleurs. Le deficit de `conf 3` vaut `-11,1` sur 53 ; un biais d'exposition
  de la taille observee ne le deplacerait pas de facon perceptible.

## Une mesure d'amelioration suppose que l'outil mesure etait bien celui qu'on croit

**Regle de revue, du 21/08/2026, et elle a invalide une conclusion deja tiree.**
Trois lots ont ete analyses et compares a l'etat anterieur ; la lecture qu'on en
a faite — cran de confiance decoince, taux de PASSE remonte — attribuait
l'amelioration au gabarit revise. Elle ne lui est pas attribuable : la Skill
etait installee, et **sa description l'active des qu'un bloc de matchs est
soumis**. Ces trois lots etaient donc deja « gabarit + Skill ».

- **Les corrections qui en sortent restent bonnes** : une confiance 2 en section
  C etait bien une violation, un plancher de cote infere etait bien une regle
  manquante. Ce sont des observations sur la **sortie**, et elles ne dependent
  pas de savoir qui l'a produite.
- **C'est la mesure d'amelioration qui tombe**, et elle seule : elle compare deux
  etats en supposant qu'un seul facteur a change.
- **Le piege est invisible parce que l'outil s'active tout seul.** Un
  interrupteur qu'on n'a pas mis n'est pas un interrupteur qu'on a laisse a
  zero : une Skill se declenche sur sa description, sans etre appelee.

**La question a se poser avant d'attribuer une amelioration** : *sur quel etat de
l'outil cette mesure a-t-elle ete prise, et cet etat est-il verifiable ?* Le
transcript le dit — une invocation y est visible. La sortie, non : deux versions
qui demandent les memes sections portent les memes signes.

## Une chose a ne pas oublier se transforme en chose qui refuse de l'etre

**Regle de revue, du 21/08/2026, et son precedent est dans ce fichier depuis des
mois.** `FEEDBACK_SUSPENDED` porte une note disant que sa bascule ne se produira
pas toute seule. Elle est exacte, elle est lue, et le drapeau est toujours leve —
si bien que **plus personne ne sait s'il l'est encore volontairement ou
seulement par oubli**. Les deux etats se ressemblent trait pour trait : c'est le
defaut caracteristique du projet, applique cette fois a une decision plutot qu'a
une donnee.

Citer ce drapeau comme precedent d'un bon commentaire, c'est citer la preuve que
le commentaire ne tient pas une decision. **Un test, si.**

- **Une condition structurelle quand il en existe une.** L'alarme de cadre se
  tait parce qu'elle mord sur 20 prompts sur 20 ; cette raison disparait avec le
  gabarit. Le test se pose donc sur `ACTIVE_PRODUCER` — des que le payload
  devient ce qui part, `FRAME_ALERT_MUTED` doit etre retombe, sinon la suite est
  rouge. Rien a retenir, rien a dater.
- **Un rendez-vous date sinon**, meme idiome que `Threshold.remeasure_on` : « un
  provisoire non date devient permanent par oubli ». `FEEDBACK_SUSPENDED_REVIEW`
  porte la date a laquelle le drapeau se **re-decide**.
- **Le test ne leve jamais le drapeau a la place de qui exploite.** Il demande de
  choisir, et il nomme les deux reponses valables : lever, ou reecrire la date
  **avec la raison** de garder. Repousser une date sans ecrire la raison est
  exactement ainsi qu'un provisoire devient permanent.
- **Le rendez-vous se teste lui-meme.** Un garde-fou qui ne se declencherait
  jamais serait pire qu'absent — il donnerait l'apparence d'un garde-fou. Les
  quatre transitions sont verifiees : condition atteinte drapeau leve (rouge),
  condition atteinte drapeau retire (vert), et l'inverse.
- **Il se tait des que le drapeau tombe** : une fois la suspension retiree, il
  n'y a plus rien a re-decider.

**La question a se poser en ecrivant un commentaire qui dit « a ne pas oublier »**
est : *quelle condition rendra ceci faux, et un test peut-il la voir ?* Elle a
**trois** reponses, et la troisieme est celle qu'on saute.

1. **Une condition existe et un test la voit** — le commentaire explique, le test
   garde. C'est le cas de l'alarme de cadre.
2. **Une condition existe mais aucun test ne la voit** — il faut une date de
   re-decision, et la raison de chaque report.
3. **Aucune condition n'existe.** Le drapeau n'est pas provisoire : il l'a
   seulement ete **dans l'intention de quelqu'un**. Un « a ne pas oublier » sans
   condition de falsification est une **decision de conception permanente
   deguisee en provisoire**, et le traitement n'est ni un test ni une date : c'est
   de retirer le mot provisoire et d'assumer la decision.

**La troisieme branche est celle qui coute le plus a ne pas voir**, parce qu'elle
se confond avec la deuxieme : on reporte, la date passe, on reporte encore, et
chaque report parait raisonnable pris seul. Ce n'est pas un oubli — c'est une
question mal posee, indefiniment.

- **D'ou les raisons de report qui s'empilent au lieu de se remplacer.** Une date
  remplacee ne garde aucune trace ; trois raisons cote a cote disent ce
  qu'aucune date ne dit — le drapeau n'attend pas un evenement, il n'en a jamais
  attendu. La liste est le diagnostic.
- **Et la question du rendez-vous n'est donc pas « faut-il lever ce drapeau »**
  mais *ce drapeau a-t-il jamais eu une condition de sortie*. Poser la premiere
  fait choisir entre lever et reporter, deux reponses qui supposent toutes deux
  un provisoire ; la seconde ouvre la troisieme branche.

**C'est le diagnostic que `FEEDBACK_SUSPENDED` attend**, et le reporter en
novembre serait la mauvaise reponse s'il s'avere qu'il n'a jamais eu de condition
de sortie.

## Un rythme de saisie n'est pas un resultat

Une question du genre « dans combien de sessions saurai-je » repond a une
propriete de **l'agenda**, pas des donnees. Elle a sa place — c'est de la
planification, et savoir quand regarder a nouveau est utile — mais elle ne rend
aucun verdict, et le libelle ne doit pas la presenter comme tel.

Le defaut s'est montre tout seul : un plafond de sessions restantes a annonce
« rien a mesurer » puis « atteignable » sur les **memes donnees**, lues a travers
deux populations. Ce qui conclut est un test dont la reference se decide avant
de regarder les donnees — voir l'equivalence plus haut.

## Une garde d'entree qui double une garde de sortie est une branche morte

Trois branches inatteignables retirees en six lots, et **deux partagent cette
forme** : un controle d'entree qui rend impossible le repli place plus bas.

- `cramers_v` filtrait les lignes vides a l'entree, puis testait un total nul
  qui ne pouvait plus l'etre ;
- `difference_interval` refusait un effectif nul avant d'appeler `wilson`, qui
  rend deja `None` dans ce cas.

La troisieme est d'une autre famille — un repli en fin de boucle rendu impossible
par un **invariant mathematique** : la loi de Poisson-binomiale somme a 1, donc
la fragilite trouve toujours sa valeur. Elle ne se prevoit pas par une regle de
forme, seulement par la couverture.

**Et c'est l'argument retrospectif le plus net pour `pytest-cov`**, qui n'etait
meme pas installe au debut de ce chantier : les trois sont apparues en poussant
le module a 100 %, aucune n'aurait ete trouvee autrement — une branche morte ne
casse rien, par definition.

## Une valeur nulle ou extreme est un resultat, pas une absence de resultat

**Trois occurrences en cinq lots : c'est un motif, pas une serie d'accidents.**
Toute garde qui confond un etat limite avec un manque doit etre testee **sur ce
cas limite**, et pas seulement sur le cas ordinaire.

- `required_sample` rend **0** sur un ecart total — aucun volume supplementaire
  n'est requis, la question est deja tranchee. `if besoin:` le lisait « pas de
  question », et l'horizon disparaissait **au moment precis ou il conclut**.
  Attrape par une fixture trop parfaite, qui rendait l'axe parfaitement separe.
- `Feedback.suspended` en propriete lisait la constante **a l'acces** : deux
  releves du meme lot devenaient indiscernables des qu'elle changeait entre les
  deux, et la suspension etait intestable. Voir la section suivante.
- `_upper_gamma` a **chi2 nul** prenait le logarithme de zero. Le cas ne se
  produit que sur un axe parfaitement homogene, c'est-a-dire exactement quand le
  test doit repondre « rien ne separe ».

Le symptome commun : **rien ne casse**. La valeur limite se lit comme une
absence, la ligne disparait, et l'interface a l'air normale. C'est la forme la
plus couteuse qu'un defaut puisse prendre sur cette page, puisqu'elle se
confond avec le message qu'elle est censee porter.

## Une assertion enonce ce qui doit etre vrai, jamais ce qui est sorti

**Quatre tests casses dans un seul chantier, tous de la meme forme** : l'assertion avait
ete ecrite en recopiant la sortie du jour au lieu d'enoncer la propriete. Aucun ne
verifiait ce qu'il croyait verifier, et aucun n'a rien appris en cassant.

- **La valeur du jour.** `assert "11 jambes"` : 11 est le reglage de la base servie, le
  seed de la migration 003 en donne 9. Le test passe chez l'un et casse chez l'autre sans
  qu'une regle ait bouge. Ce qui doit etre vrai est que le nombre annonce **soit celui que
  le calcul rend** — donc `safe_legs_available(...)` dans l'assertion elle-meme. Meme regle
  que pour les paliers hauts : le critere est une propriete, jamais les valeurs du jour.
- **Le formatage.** `assert "N'ajoute\njamais une jambe"` : le retour a la ligne n'est pas
  la regle, c'est une largeur de colonne. Un mot ajoute trois phrases plus haut deplace la
  coupe et casse un test qui ne verifiait pas ca. La phrase se compare **a plat**
  (`" ".join(texte.split())`), comme le fait deja le test de la section D.
- **La formulation exacte quand seule la substance compte.** Un test qui recopie une phrase
  entiere casse a chaque reecriture, et la reaction naturelle — realigner l'assertion sur
  la nouvelle sortie — ne verifie plus rien d'autre qu'elle-meme.

La question a se poser en ecrivant l'assertion : **si elle casse, aura-t-on appris quelque
chose ?** Si la reponse est « il faudra recopier la nouvelle sortie », elle decrit au lieu
de contraindre.

**Et la reciproque garde le garde-fou, sans quoi cette regle deviendrait un permis
d'affaiblir les tests** : une assertion qui casse sur un changement **de fond** n'est pas
fragile, elle fait son travail. Deux tests du gabarit ont refuse une coupe du preambule et
ils avaient raison ; une coupe qui casse un test de contenu est une regression, pas une
coupe. La difference se lit sur ce que la casse revele — une decision changee, ou une
largeur de colonne.

## Un seuil descend dans l'objet, il ne va pas se chercher lui-meme

Regle generale, apprise trois fois — sur `RateRow.minimum`, sur le seuil
d'effectif minimum, et sur `Feedback.suspended`.

Une valeur qui decide d'un comportement se pose **en champ, a la construction**.
Ecrite en propriete qui lit une constante de module ou un reglage en base, elle
est relue **a chaque acces** : deux releves du meme lot deviennent alors
indiscernables des que la valeur change entre les deux, et la classe n'est plus
testable hors d'une base.

Le symptome est celui d'un test qui compare un avant et un apres et trouve deux
fois l'apres. Constate sur `Feedback.suspended` : le test comparait un lot avec
et sans suspension, et les deux objets voyaient la meme valeur — le defaut ne
cassait rien en production, il rendait seulement l'etat d'exploitation
invisible a la verification.

## Le marche a la prise (migration 033)

**Ce chantier n'affiche rien et ne repare rien. Il arrete une perte**, et c'est la seule
raison pour laquelle il passe avant le reste : chaque session passee sans lui est une
session definitivement non comparable.

Ce qui manquait, mesure sur les 114 selections : `odds` fait un `DELETE` puis un `INSERT`
par (match, book, marche), donc **ne conserve que le dernier releve** — une heure apres un
prompt, l'etat du marche que l'analyse a lu n'existe plus nulle part. Et `picks.market` est
du **texte libre** recopie a la main : seize libelles pour dix marches reels, sans aucune
cle vers `odds`. Pour une selection tranchee, on ne pouvait donc ni retrouver les autres
issues de son marche, ni dire qui en etait le favori — les deux seules choses qui diraient
si 48 % est bon ou mauvais.

- **`prompt_odds` fige le marche complet a l'archivage du prompt**, au meme endroit que
  `prompt_events` : c'est le seul instant ou l'on sait ce que le bloc portait. Tous les
  books, pas seulement le principal — un favori se lit sur le marche entier, et sur une
  competition que Betclic ne sert pas, un book de reference est le seul a servir la ligne.
  - **Un releve par session et par match, remplace a chaque prompt.** Ni par prompt — une
    session reelle en genere jusqu'a vingt — ni fige au premier : un match entre parfois
    dans un prompt **avant** d'etre enrichi, et le dernier prompt qui le porte est celui
    dont l'etat est le plus proche de la decision. Meme forme que `scan._store`.
  - Deux horodatages, et ils ne disent pas la meme chose : `fetched_at` est l'heure du
    releve chez le fournisseur, `captured_at` celle ou il a ete fige pour la session.
  - **Pas de cle primaire composite** : `point` est nullable, et SQLite laisse les NULL se
    dupliquer dans une PK. L'unicite est tenue par le service.
  - Cout assume : ~29 lignes par match, ~35 Mo par an a raison d'une session par jour.
    C'est la table qui grossira le plus vite, et elle porte la seule donnee du projet qui
    **ne se reconstitue pas apres coup**.
- **`picks.market_key` se resout par correspondance exacte** avec les libelles de
  `render.MARKET_ORDER_BY_SPORT`. Ce n'est pas deduire d'un texte libre : ce sont les
  libelles que le bloc met sous les yeux de l'analyse, donc reconnaitre un mot qu'on a
  soi-meme imprime. Le **sport** decide — « Vainqueur » est le `h2h` d'un match de tennis
  et l'`outright` d'une etape de cyclisme.
  - **Deux passes, et l'ordre compte.** La ligne d'un total fait partie du libelle recopie
    (« O/U 2.5 ») sans faire partie du marche : elle se retire pour la seconde tentative.
    La retirer d'emblee confondrait « Set 1 » et « Set 2 », deux marches distincts dont le
    numero **est** le nom — une selection sur le premier set se rattacherait au second.
  - Un libelle hors vocabulaire reste **NULL et se reclame** : « Double chance » la ou le
    bloc ecrit « DC ». Mesure : 110 des 114 selections se resolvent, les 4 autres sont des
    saisies libres. La selection est enregistree quand meme — une cle absente vaut « on ne
    sait pas », jamais un refus.
  - **Figee a l'ecriture, resolue a la lecture quand elle manque** (`market_key_effective`,
    meme idiome que `tier_effective`). Les deux traitements sont justes et la difference
    est le coeur du sujet : une selection ecrite depuis cette migration porte sa cle, et ce
    lien vers le releve pris le meme jour doit survivre a un libelle renomme dans `render` ;
    les selections anterieures n'en ont pas, et les resoudre a la lecture vaut mieux qu'un
    retro-remplissage — **la regle vit en Python, la recopier en SQL l'aurait fait diverger
    au premier marche ajoute**, exactement le piege des niveaux de competition.
  - `set_event()` la **recalcule** : c'est le seul endroit ou elle bouge apres coup, et
    c'est justifie — un rattachement corrige peut changer de sport, donc de vocabulaire.
    Elle etait fausse, pas perimee.
- **`sessions.scale_version` ne sert a rien aujourd'hui, et c'est delibere.** Une courbe de
  fiabilite tracee a travers un changement d'echelle de confiance ne mesure rien : il
  faudra savoir, session par session, contre quoi la confiance annoncee etait notee. Le
  champ passe maintenant pour que ces sessions-ci soient deja datees quand la question se
  posera — une echelle ne se reconstitue pas apres coup. `COALESCE` la fige au premier
  prompt : changer d'echelle en cours de session ne doit pas reetiqueter ce qui a deja ete
  rendu sous l'ancienne. Le regime actuel s'appelle `relatif-032`, du nom de ce qu'il est —
  des bandes exprimees en ecart au taux global, donc sans ancrage absolu.
- **`prompts.feedback_active`, et la boucle qu'il mesure.** Des qu'un agregat de resultats
  entre dans le prompt, les selections suivantes ne sont plus des tirages independants :
  l'analyse lit son propre tableau de bord, et une categorie annoncee a 0/7 cesse d'etre
  produite — donc cesse d'etre mesurable.
  - **La reponse a « depuis quand » n'etait pas celle qu'on attendait.** Le bloc a bien ete
    servi : **9 prompts sur 3 sessions** (06, 07 et 08/08), quand les seuils valaient encore
    10 et 4 — puis plus jamais depuis leur relevement a 40 et 10. Les 104 selections
    tranchees ne sont donc **pas** propres sur ce plan, et 54 d'entre elles viennent d'une
    session ou au moins un prompt transmettait des taux.
  - La valeur se retro-remplit, et c'est legitime la ou celle de la migration 030 ne
    l'etait pas : **le corps du prompt est la preuve**, il est archive depuis toujours, et
    la ligne « Taux de reussite de » n'apparait que lorsque le bloc publie. Un test relit le
    fichier de migration plutot que d'en recopier le critere, comme pour la migration 021.
  - Le drapeau vaut `feedback.enough` et non `not feedback.empty` : le bloc peut paraitre
    en ne portant que le taux de selection, qui ne depend d'aucun resultat et ne referme
    donc aucune boucle.
- **Les scripts de retour arriere vivent dans `deploy/rollback/`, jamais dans
  `migrations/`** : `discover_migrations` lit tout `*.sql` du dossier et leve sur une
  version dupliquee — un `033_..._down.sql` pose a cote de son aller empecherait
  l'application de demarrer. Ils ne sont jamais joues automatiquement, et le retour arriere
  se paie **en donnees** : les releves de `prompt_odds` sont perdus, et rien ne les
  reconstruit.

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

## Un combine long se batit dans les bandes sures

**Le combine extreme ne se construit pas dans les paliers hauts**, et l'hypothese inverse
avait fait ecrire une consigne de places hautes a reserver. Six SAFE a 1.45 et quatre FUN
a 1.95 donnent 135 : dix jambes, aucune place haute consommee.

D'ou la regle qui commande tout le reste : **le nombre de jambes est un parametre, pas une
consequence**. « Cote >= 100 » se satisfait par 5 jambes a 2.50 comme par 10 a 1.55, et ce
sont deux objets sans rapport. Chaque combine se regle donc par **deux** valeurs — jambes
visees et cote cible — et la cible reste une cible, jamais un plancher.

- **Le plafond de jambes ne depend plus du lot au-dela de dix matchs.** `quota_for`
  plafonne a `quota_max`, regle pour `QUOTA_REFERENCE_LOT` (10) : au reglage servi le
  14/08/2026, 6 SAFE + 5 FUN = **11 jambes des dix matchs**, et un lot de 140 n'en donne
  pas une de plus qu'un lot de 28. La shortlist de 140 evenements de ce jour-la a d'ailleurs
  rendu un prompt de **dix blocs**, donc exactement le point de saturation. `lot` est le
  nombre de **blocs du prompt**, jamais la taille de la shortlist.
  - **Le plafond vaut par prompt, jamais par session**, et l'oublier fait croire qu'un
    combine de vingt jambes exige de rouvrir les quotas. Une session genere 3 a 17 prompts,
    chacun avec son quota plein : celle du 09/08 porte **28 selections en bande sure sur 28
    matchs distincts**, celle du 06/08 22, celle du 13/08 19. Vingt jambes tiennent sans
    toucher a un seul reglage.
  - `safe_legs_available()` rend ce plafond et la section D l'annonce. Une demande qui le
    depasse se dit — meme defaut que `combo_solo_min_lot` un cran plus loin : reclamer ce
    que le lot ne porte pas fait ecrire que la demande etait insatisfiable.
  - **Le budget de recherche en fait partie, et le docstring d'origine se trompait de
    regle.** Il ecartait le budget au motif que `research_capped` ne touche pas les deux
    paliers surs, « aucun d'eux ne reclamant de dossier » : vrai de la regle de **palier**,
    et sans effet, parce que ce n'est pas elle qui contraint une jambe. La section D en
    impose une seconde — aucune jambe sous confiance 3 — et celle-la passe par le dossier :
    le cran 3 exige un fait date (`Claim.rung` rend 1 des que `reading_only`), et une
    selection hors des dossiers ouverts est ramenee en lecture. Une jambe suppose donc un
    dossier ouvert. Le plafond est `min(quotas, lot, budget)`.
    - **Un docstring faux coute plus qu'un docstring absent** : il fait re-deriver la meme
      conclusion fausse au lecteur suivant. C'est la raison du correctif, avant le `min()`.
    - **Et le nombre est lu par le modele.** Un plafond trop haut dans le prompt invite a
      chercher des jambes qui ne peuvent pas exister — exactement la pression que le reste
      du gabarit travaille a supprimer. La section D dit donc d'ou il vient.
    - Il ne mordait sur **aucun** lot au 14/08/2026 — le vivier s'epuise avant, 37 prompts
      sur 39, maximum 6 jambes produites contre 7 de budget. Mais cette mesure vaut pour un
      regime ou la ligne `dossiers_ouverts` n'etait jamais collee : porte fermee, pas defaut
      repare, et elle ne dit rien de ce que le vivier vaudra ensuite.
- **« >= 100 » est confortable, et ce n'est pas la cote qui contraint.** Sur les six
  sessions offrant dix jambes sures ou plus, le produit des dix meilleures cotes va de 302
  a 1396, mediane 565. Les quatre autres echouent faute de **selections produites** — 5 a 9
  jambes — pas faute de prix. Il faut 1.585 de moyenne geometrique sur dix jambes, quand
  les deux bandes couvrent 1.25 a 2.30.
- **Le recouvrement entre un combine court et un long est impose par le vivier.** Sur **la
  moitie des sessions**, un 4-jambes et un 10-jambes disjoints sont structurellement
  impossibles : il faudrait 14 matchs distincts et le vivier n'en offre que 5 a 11. Et si
  les deux se batissent par tri decroissant de surete a partir du meme vivier, le court est
  **inclus** dans le long par construction — J = 4/10 = 0,40, valeur a attendre par defaut
  et non accident. Il se calcule et s'affiche ; une regle de disjonction rendrait le second
  combine impossible un jour sur deux, en silence.
- **Le maillon le plus fragile ne se demande plus au-dela de `combo_maillon_jambes`** (6) :
  a dix jambes toutes decisives, designer la plus faible ne veut plus rien dire. La section
  D fait nommer **les jambes du palier le plus haut du combine**, et le libelle ne dit pas
  « fragile » — il dit que la cote vient de la.
  - **La raison est structurelle, et elle doit le rester.** Une cible longue s'atteint en
    gonflant par le haut de la bande : c'est la que s'exerce la pression, donc c'est ce
    groupe qu'on expose. Il n'est pas expose parce que les donnees le designeraient.
  - **Le nombre de jambes du long est un plafond, jamais une cible**, et c'est la meme
    raison retournee : la mesure dit que la cote n'est pas la contrainte — les six sessions
    offrant dix jambes sures depassent toutes 100 — et que c'est le compte qui l'est. Viser
    un compte fabriquerait donc exactement la pression que la section interdit. Il prend ce
    que `safe_legs_available()` autorise et s'arrete au premier des trois motifs.
  - **La mesure existe, elle a ete calculee, et elle ne fonde pas la decision.** Sur les
    selections a anteriorite etablie, les jambes SAFE gagnent 33/50 (66 %) contre 19/47
    (40 %) pour les FUN, Fisher **p = 0,0148**. Elle ne vaut pas son p nominal — le critere
    « palier » a ete retenu **apres** avoir regarde, parmi plusieurs candidats testes sur le
    meme echantillon — et elle compare deux **bandes de cote** sur des taux bruts,
    c'est-a-dire la metrique retiree de la tete de `/stats` : un taux sans son prix ne
    mesure rien, et les cotes courtes gagnent plus souvent par construction. Elle est
    ecrite ici pour qu'une relecture ne la prenne pas pour une validation.
  - **Ce qui aurait permis de trancher proprement existe desormais** : le residu au prix,
    decline par cran de confiance, par type d'angle et par marche. C'est ce qui a ete fait
    a la place du Fisher ci-dessus, et il faut le lire comme tel — la note ne dit pas
    seulement ce qu'on a refuse. Un taux brut ne compare pas ce qu'il pretend comparer ;
    le residu compare chaque selection a **son** prix, donc deux regroupements qui ne
    jouent pas aux memes cotes s'y departagent honnetement.
    - Mesure du 14/08/2026 sur les 103 selections a anteriorite etablie : le taux brut
      donnait `conf 3` a 36 % contre `conf 4` a 66 %, ce qui se lit « le cran 4 est
      meilleur ». Le residu dit autre chose et de plus utile — **`conf 4` est exactement a
      parite avec ses prix** (+0,1 sur 38) et **`conf 3` porte tout le deficit global**
      (-11,1 sur 53, seul niveau qui s'ecarte au seuil de la page). Le meme calcul sur
      l'angle rend `Issue` a -2,5 sur 21 et `Maniere` a +3,8 sur 19 : l'ecart de 31 points
      de taux brut se reduit a presque rien une fois les prix pris en compte.
    - **La page affiche et ne conclut pas** : la ligne qui s'ecarte est marquee, et
      `residual_horizon()` dit combien de selections de plus au meme regime il faudrait
      pour que les autres tiennent — meme role que `required_sample` sur la carte « ce qui
      s'ecarte ». Aucune phrase n'accompagne la marque.
    - **Un axe qui ne porte que du bruit ne s'affiche pas.** Le marche donne 13 niveaux
      dont huit sous dix selections ; le seuil est celui de la page (`minimum_rows`),
      jamais un second — sous quel compte un taux ne veut plus rien dire est une propriete
      des donnees, pas de la carte qui les montre.
  - **Le critere ecarte l'a ete par la mesure, lui.** Nommer les deux ou trois confiances
    les plus basses designerait des jambes qui ne sont pas plus fragiles : 54 % contre
    58 %, et l'omnibus exact sur les confiances 2/3/4 des bandes sures donne p = 0,19.
    Coherent avec ce qui etait deja etabli — la confiance n'ordonne pas.
  - **Le critere « jambes en lecture » attend**, meme regle que partout : 93 des 137 lignes
    n'ont aucun niveau de source et le chantier de la migration 043 n'a recu aucune entree.
    Un critere qui ne se declenche jamais est pire qu'absent, il donne l'apparence d'un
    filtre actif. Il se rouvre quand la colonne se sera remplie sur quelques sessions.
- **La pression change de forme avec la longueur**, et le garde-fou d'origine ne couvrait
  que la premiere moitie. Sur un combine court, la tentation est une jambe chere ajoutee a
  la fin ; sur un long, c'est une jambe a 1.30 — moins visible, moins couteuse a ecrire,
  tout aussi fausse. Le plancher de bande (1.25) l'autorise, donc rien ne l'arrete que la
  consigne.
- **Le taux de jambe se mesure, le taux de combine non.** Les selections en bande sure
  gagnent 78/137 (57 %), 54 % sur la seule anteriorite etablie : un combine de dix jambes
  passe de l'ordre d'**une fois sur 280**, pas une fois sur cent. Son taux ne sera jamais
  mesurable, quel que soit le temps qu'on lui laisse — le combine est un **regroupement**,
  pas une unite de mesure, et ses jambes restent des selections ordinaires comptees
  individuellement. Le produit des taux n'est qu'un ordre de grandeur : les jambes d'une
  meme session ne sont pas independantes, ce que `clustered_p_value` sait deja ailleurs.
- **Aucun combine n'existe en base au 14/08/2026** — `coupons` vide, `coupon_id` nul sur
  les 149 selections, `played` faux partout. Le recouvrement historique n'etait donc pas
  mesurable : tout ce qui precede porte sur ce que le vivier **autorise**, jamais sur ce
  qui a ete pose.

## Le combine est un objet d'analyse (`services/combos.py`, migration 047)

**Il ne passe pas par `coupons`, et la collision etait reelle.** `coupons.attach()` est le
**seul** ecrivain de `picks.played`, et il ecrit aussi `picks.coupon_id` : faire passer les
combines par la aurait fait apparaitre comme paris poses des combines que personne n'a
joues. La page pose deux questions distinctes — ce que valent les selections, ce que valent
les paris — et un combine produit par le modele appartient sans ambiguite a la premiere.
S'il devient un pari, c'est un geste fait apres, et qui ne le change pas. **Le mot « joue »
n'apparait nulle part sur ces cartes.**

- **Un combine reste rattache a un prompt** (`prompt_id NOT NULL`), et une jambe venue d'un
  autre prompt est refusee. Les selections de deux prompts n'ont jamais ete comparees entre
  elles : chaque instance a choisi dans son lot, avec son quota et son budget propres. Et
  les colonnes qui mesureraient si un cran signifie la meme chose d'un prompt a l'autre
  (migrations 042 et 043) n'ont recu leur premiere entree que le 14/08/2026.
  - **Deux mesures le justifient**, et la seconde est la vraie : sur dix sessions, deux
    seulement portent de quoi batir un combine de vingt jambes ; et un match est rendu
    **2,23 fois** en moyenne dans sa session, jusqu'a 13 fois — deux jambes venues de deux
    prompts sur le meme match seraient deux tirages du meme match presentes comme deux
    selections.
  - La contrainte est **ecrite dans le schema** plutot que laissee implicite, et un test
    garde le refus.
- **La cote se recalcule depuis les jambes, jamais depuis le produit ecrit.** Le nombre
  qu'un rendu affiche est une affirmation du modele ; celui-ci est une consequence des prix
  enregistres. Les deux se gardent (`declared_price`, et le calcul a la lecture), et
  **l'ecart se signale a l'apercu** — le seul moment ou l'information est encore
  recuperable, meme regle que le releve des blocs de confiance. Une jambe sans prix rend le
  produit **incalculable** et non partiel : un produit ampute serait plus bas que le vrai
  sans que rien ne le dise.
- **Le motif d'arret est la donnee du bloc qui ne se deduit de rien** : `cible`, `plafond`,
  `confiance`. Aucun n'est un echec, et c'est leur repartition qui dira si la cible est
  bien reglee — toujours `cible` et elle peut monter, toujours `confiance` et le lot est la
  contrainte, la cible n'y pouvant rien.
- **Aucune carte « taux de reussite par combine », et c'est definitif.** Au taux de jambe
  constate (78/137, 57 %), un combine de dix jambes se tranche favorablement une fois sur
  280 : son taux ne sera jamais mesurable, quel que soit le temps qu'on lui laisse. Le
  combine est un **regroupement** ; les jambes restent les unites de mesure, comptees
  individuellement dans les statistiques existantes. Ce qui garde un sens est **combien de
  jambes tombent** et **a quel rang la premiere tombe** — d'ou `combo_legs.position`, qui
  porte l'ordre d'ajout.
  - Le produit des taux n'est qu'un ordre de grandeur : les jambes d'une meme session ne
    sont pas independantes, ce que `clustered_p_value` sait deja ailleurs.
- **Le recouvrement s'affiche, il ne s'interdit pas.** Mesure : sur **la moitie** des
  sessions, un 4-jambes et un 10-jambes disjoints sont structurellement impossibles — il
  faudrait 14 matchs distincts et le vivier n'en offre que 5 a 11. Et si les deux se
  batissent par tri decroissant de surete a partir du meme vivier, le court est **inclus**
  dans le long par construction, J = 4/10 = 0,40. Une regle de disjonction rendrait le
  second combine impossible un jour sur deux, en silence. Le compte partage accompagne
  l'indice : un J de 0,40 ne dit pas s'il porte sur deux jambes ou sur huit.
- **La repartition par palier remplace la designation d'un maillon fragile**, et son
  libelle ne dit pas « fragile » : il dit **ou la cote a ete achetee**. Voir plus bas la
  raison, qui est structurelle et non statistique.
- Un combine dont une jambe n'a pas ete importee — ligne decochee, ligne en echec — n'est
  **pas enregistre ampute** : il porterait une cote que rien ne justifie. Il est dit.
- `PromptBlocks` porte l'identifiant du prompt **avec** ses reperes de blocs : c'est le
  prompt qui valide l'appariement des blocs de confiance qui donne aussi son `prompt_id` a
  un combine, et deux lectures paralleles de la meme chose auraient fini par designer deux
  prompts differents.

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

## Le cran se calcule (`services/confidence.py`, migration 042)

**Definir les crans n'a pas suffi, et la mesure le dit.** La table des cinq crans a ete
ecrite pour reparer un defaut mesure — 99 % du volume sur deux crans. Sept jours et
quarante-neuf selections plus tard, sur 141 tranchees : **90 % du volume sur les crans 3
et 4**, toujours **aucune en cran 1 sur 149**, et un ordre qui n'est pas monotone —
cran 2 a 77 % (10/13), cran 4 a 60 % (31/52), cran 3 a 44 % (33/75). Le palier ordonne a
p = 0,000 ; la confiance non, p = 0,131.

Definir une regle et la confier au modele ne la fait donc pas appliquer. C'est le meme
constat que pour le comptage de la section C et pour la famille d'un marche, et il a la
meme reponse : **ce que l'application peut trancher de facon deterministe ne se delegue
pas.** Le gabarit demande desormais les **entrees** — niveau de source, faits dates avec
leur editeur, et si un manque de la section A touche le facteur porteur — et
`Claim.rung` applique la table.

- **Les deux valeurs se gardent, et ce que leur ecart mesure a change de nature en cours
  de chantier.** `confidence` reste le cran **annonce**, `confidence_computed` le cran
  **calcule**. Tant que le modele notait seul, l'ecart aurait mesure son flair ; depuis
  qu'il declare ses entrees et que la table s'applique dans le code, **les deux valeurs
  sortent du meme faisceau**. L'ecart teste donc s'il applique correctement sa propre
  table : c'est un **lint sur la redaction du gabarit**, pas sur son jugement — et c'est
  plus utile ainsi, une clause ambigue se reecrivant quand un jugement ne se corrige pas.
  - D'ou `Notation.transitions`, **le seul champ actionnable du bloc** : un desaccord
    disperse est du bruit de redaction, un desaccord concentre sur un passage — 4 annonce
    que la table met a 3 — nomme la clause a reprendre.
  - **Deux conditions avant qu'une clause soit designee, et l'effectif passe en premier.**
    `Notation.minimum` **reutilise le seuil de la page** (`minimum_rows`) au lieu d'en
    inventer un : sous quel compte une repartition ne veut plus rien dire est une propriete
    des donnees, pas du bloc qui les affiche. La raison est plus forte ici qu'ailleurs — la
    sortie n'est pas un taux mais une **consigne**, reecrire une clause du gabarit, et la
    publier sur trois desaccords ferait reecrire un texte sur du bruit. Le seuil
    **descend dans l'objet**, il n'y est pas lu d'une constante.
  - La concentration ensuite, **strictement** plus de la moitie : l'inegalite large etait
    fausse, deux desaccords partages un-un les auraient declares concentres tous les deux.
    Trouve en ecrivant le test.
  - **Deux tests ont du etre releves au-dessus du seuil avec lui**, et l'un serait sinon
    passe **pour la mauvaise raison** : ecrit sur deux desaccords, il verifiait un
    `dominant is None` que le plancher d'effectif rendait vrai tout seul, et il aurait
    continue de passer si la regle de concentration disparaissait. Un test qui change de
    cause en gardant son resultat est un test mort qui en a l'air vivant.
  - **La formulation d'origine de cette section disait le contraire**, et disait faux.
    Meme regle que partout ici : une condition qui change se verifie contre la phrase qui
    l'explique — ici le module, la migration, la carte de la page et ce paragraphe.
- **Aucun renommage**, precedent de la migration 030 : `confidence` **est** deja la valeur
  declaree, et la renommer toucherait six gabarits pour un gain nul.
- **`Conf/5` reste demande au modele**, contrairement a la lettre du brief, et pour deux
  raisons qui se recoupent. La section D s'en sert (« n'ajoute jamais une jambe sous
  confiance 3 ») : sans cran declare, cette regle n'a plus de sujet. Et surtout le taux de
  desaccord — que le brief exige — a besoin des **deux** valeurs sur les **memes**
  selections : cesser de demander l'annonce le rendrait calculable seulement sur
  l'historique, qui n'a pas de cran calcule. Les deux moities du brief se contredisaient ;
  c'est celle qui porte la mesure qui a gagne.
- **Aucun repli silencieux sur l'annonce.** Un bloc illisible journalise et laisse le cran
  a `NULL`. Retomber sur la valeur declaree ferait passer pour calculee une note qui ne
  l'est pas, et le taux de desaccord annoncerait alors un accord parfait — l'inverse exact
  de ce qu'on mesure.
- **Le bloc voyage dans le meme copier-coller que le tableau.** Un second geste ferait
  perdre la colonne le jour ou on l'oublie, et c'est la seule qui rende le cran calculable.
  - La jointure se fait **par l'ordre**, et le champ `match` en est la **somme de
    controle**. Le numero de bloc (`M8`) change d'une generation a l'autre, donc il ne peut
    pas servir de cle ; mais il est coherent **a l'interieur d'un rendu**, et les en-tetes
    `### M8 · sport · competition · affiche · heure` sont archives avec le corps du prompt
    depuis toujours. L'information dormait deja en base.
  - **Le compte seul ne suffisait pas, et c'etait le point faible de la premiere version.**
    Nombre egal et ordre different donnait des crans tous decales d'un rang, **en
    silence** — et un cran faux ne se voit pas, la ou un cran inconnu se voit. Meme
    raisonnement que la garde d'anteriorite : ce qui ne peut pas se relire ne doit pas
    pouvoir s'ecrire.
  - **Un prompt valide l'ensemble ou ne le valide pas.** Retenir le meilleur des prompts
    paire par paire reviendrait a piocher la lecture qui arrange, ce qui ne demontrerait
    plus rien. Sans aucun prompt archive, rien n'est rattache.
  - Le rapprochement porte sur le **texte de la colonne Match** et non sur l'evenement
    resolu : c'est l'appariement du modele qu'on verifie, et il doit l'etre meme sur une
    ligne dont le rapprochement de nom a echoue.
  - **Normalisation deterministe, puis egalite stricte** — et c'est le seul reglage qui
    tienne les deux bouts. Une egalite sur le texte brut ferait tomber tout un lot sur un
    tiret long rendu en tiret court ; une similarite floue laisserait passer l'appariement
    decale qu'on cherche a attraper. `_fold` absorbe la **typographie** — casse, accents,
    tirets, espaces, `Győri ETO FC` contre `Gyori ETO FC` — et rien d'autre.
    - La premiere version comparait par **contenance**, ce qui etait le cote flou : « Lyon »
      se serait trouve dans n'importe quel en-tete portant Lyon, y compris celui de l'autre
      match. La comparaison porte donc sur le **segment d'affiche** de l'en-tete, extrait
      par position, et un en-tete de forme inattendue invalide la paire — une somme de
      controle qui s'accommode de ce qu'elle ne reconnait pas ne controle plus rien.
    - **Ce qu'une orthographe reellement differente coute est les crans du lot entier**, et
      c'est le bon sens du compromis : la perte est visible, elle se rattrape en recollant,
      et elle ne s'ecrit jamais en base.
- **L'unicite se teste sur l'editeur d'origine, pas sur le domaine qui publie**
  (`Fact.source`). La normalisation de domaine seule sur-comptait l'independance
  **exactement la ou le gabarit previent** : un agregateur qui reprend un blog, ou deux
  titres rapportant la meme conference de presse, sortent sur deux domaines distincts et
  auraient produit un cran 5 — alors que le gabarit dit depuis toujours que l'editeur
  d'origine est alors le club, donc **un seul facteur**. Le domaine qui publie ne peut pas
  le savoir : c'est une propriete du contenu, pas de l'URL.
  - `editeur_origine` est donc demande **des que le site qui publie n'est pas celui qui a
    produit l'information**, et vide dans le cas ordinaire ou les deux se confondent.
  - Il est normalise et **refuse** comme `editeur` s'il n'est pas un domaine : une origine
    illisible laisserait compter l'independance sur le relais.
  - `publisher_of` reste ce qui tranche le reste : `motherwellfc.co.uk` et
    `https://www.motherwellfc.co.uk/news/…` sont un seul facteur, et ce qui n'est pas un
    domaine — « Motherwell FC » — n'en est aucun, parce que ce qui n'est pas verifiable ne
    compte pas.
- **`manque_touche_facteur` n'a pas de defaut, et c'est le troisieme etat.** Les crans 3, 4
  et 5 ne se distinguent que par lui ; absent, `rung` rend `None`. Le deviner reviendrait a
  choisir un cran a la place de l'analyse — meme famille que le drapeau de terrain neutre,
  qu'un champ dont on a mesure qu'il ment interdit de calculer.
- **Rien n'est retro-rempli**, et le script de reprise n'existe pas plutot que d'etre vide :
  les 149 selections d'avant n'ont aucun bloc, et un faisceau d'information ne s'invente pas
  apres coup. `NULL` est la verite. Meme arbitrage que `price_source` a la migration 030.
- **Deux tests du gabarit ont refuse la coupe, et ils avaient raison.** La regle « tout
  ajout au preambule budgete sa propre coupe » a ete appliquee sur les paragraphes que le
  calcul semblait rendre inutiles — « deux editeurs distincts » et « exiger le vide rendrait
  le cran 5 inatteignable ». Les deux sont gardes par un test, et les deux **changent encore
  ce que le modele fait** : le premier est desormais la regle de comptage de l'application,
  enoncee a celui qui la remplit ; le second corrige un defaut mesure. Ils sont restaures
  mot pour mot, l'ajout est net de +876 caracteres, et les deux plafonds tiennent
  largement — ils valent aujourd'hui le double de leur mesure. **Une coupe qui casse un test
  de contenu n'est pas une coupe, c'est une regression** : la bonne reaction est de la
  reprendre ailleurs, jamais d'affaiblir l'assertion.

## Le niveau de source cesse d'etre pris au mot (migration 043)

**Mesure : 0 selection en `lecture` sur 149**, quand le budget de recherche vaut sept
dossiers pour des lots de 57 a 72 matchs. Le preambule presente pourtant `lecture` comme
« une reponse normale et frequente » : elle n'a jamais ete donnee une seule fois. Le niveau
de source est donc gonfle, et la table qui le regroupe ne mesure rien.

**Les deux chantiers partent ensemble, et l'ordre n'est pas negociable.** Livrer le cran
calcule seul aurait ete **pire que l'etat d'avant** : le modele declare un `source_level`
sur les matchs qu'il n'a pas ouverts, l'application en aurait deduit un cran
**deterministe** a partir d'une declaration gonflee, et le faux aurait gagne l'apparence du
calcul. Une colonne libre etait inoffensive parce qu'on la savait molle.

- **Le defaut est `lecture`, jamais « ouvert ».** Liste absente, illisible, ou portant un
  repere qui ne se resout contre aucun prompt : **tout le lot** passe en lecture, cran 1.
  Meme raisonnement que la somme de controle de l'appariement — un `lecture` de trop se
  voit et se corrige, un niveau de source gonfle qui passe pour verifie ne se voit pas.
- **Une ligne structuree, jamais la prose de la section F.** Celle-ci est redigee pour etre
  lue, change de tournure d'une session a l'autre, et ses reperes s'y trouvent au milieu de
  phrases qui expliquent pourquoi. `dossiers_ouverts: [M1, M4, M7]` se donne a cote, et la
  prose garde son travail.
  - Demandee **hors de tout bloc de code**, mais `read_blocks` ecarte quand meme un bloc
    qui la porte : comptee comme un bloc de confiance en echec, elle ferait diverger le
    compte des blocs de celui des lignes et couterait les crans du lot entier.
- **Les M-numeros passent par la machinerie du chantier 1**, jamais par une seconde
  resolution : c'est le meme prompt qui valide les blocs et qui resout la liste, sans quoi
  deux lectures paralleles finiraient par designer deux matchs sous le meme repere.
- **L'override enregistre l'ecart, pas seulement le compte** (`Override.claimed`). Un `3`
  revendique sur un dossier non ouvert est de l'**inflation** — l'analyse s'est notee comme
  si elle avait cherche ; un `5`, qui suppose deux faits dates d'editeurs distincts et une
  origine, est de la **fabrication**. Deux fautes, et le total seul les confondrait. Les
  faits restent en base : c'est la trace.
- **`Notation` exclut les ecrasees**, et c'est indispensable : sans cela la majorite des
  desaccords viendrait de l'override — le modele annonce 3, l'application force 1 — et la
  matrice des transitions ne mesurerait plus que lui, en designant toujours la meme clause
  du gabarit, qui n'y serait pour rien.
- **`opened=None` n'ecrase rien**, et c'est le cas de la saisie a la main : l'override juge
  une declaration de modele, pas un geste humain. Un champ absent du formulaire vaut
  « on ne sait pas » — l'apercu, lui, l'emet toujours.
- **Un compte, jamais un taux**, donc aucun seuil ne le garde : il est juste a tout
  effectif, meme regle que le compte des non-classees.
- **La liste declaree se memorise a l'import et jamais a l'apercu.** Un test de longue date
  garde ce contrat — « l'apercu n'ecrit rien » — et il a attrape la premiere version, qui
  ecrivait a la lecture. Elle voyage donc par un champ cache, comme le bloc de confiance.
- **Mesure sans decision** : la liste declaree se compare a l'ordre de passage que
  l'application avait propose, relu dans le **corps archive** — la fiche recalculee
  aujourd'hui ne donnerait plus le meme classement. Un dossier hors priorite est
  **legitime**, la section F demande justement de le dire ; c'est un ecart systematique qui
  dirait que le tri par « ce qu'une recherche peut y changer » ne sert a rien.

### Un format produit et jamais transmis — le quatrieme cas

**Ni un parseur muet, ni un modele defaillant, ni une persistance ratee.** Les
`confidence_computed`, `confidence_claimed` et `research_overridden` a **0 sur
149** ont fait soupconner un chantier mort ; la mesure dit autre chose, et la
chronologie tranche :

| Prompt | Session | Date | Blocs ```conf demandes | `dossiers_ouverts` |
| --- | --- | --- | --- | --- |
| 105-109 | s10 et avant | ≤ 13/08 17:09 | **non** | **non** |
| 110 | s10 | 13/08 21:26 | oui | non |
| 111-112 | s11 | 14/08 | oui | oui |

Les migrations 042 et 043 ont ete appliquees le 13/08 a 20:55 et 21:47 ; les
picks de la session 10 ont ete importes a 13:08 **le meme jour**. Les sessions 2
a 10 ne pouvaient pas porter de blocs — le gabarit ne les demandait pas encore.
**Une seule session a eu l'occasion**, et elle n'en porte pas : le « 0 sur 149 »
mesurait un import, pas deux chantiers de silence.

- **Le chemin fonctionne de bout en bout**, et c'est la trace qui le prouve :
  la session 11 porte `research_overridden` sur ses trois picks, ce qui ne
  s'ecrit que si `read_opened` a tourne, si les reperes ont ete resolus et si le
  champ cache a traverse le formulaire.
- **Ce qui manquait est l'entree.** Le rendu produisait les blocs ; le collage
  ligne par ligne depuis le tableau de la section C les a laisses derriere lui.
- **Et l'import l'acceptait en silence** : `_attach_claims` portait
  `if not reading.claims: return None`. Un bloc pour trois lignes avertissait,
  **zero bloc ne disait rien** — la seule branche muette du module, et c'est
  celle qui a servi.
- La somme de controle sur `match` n'a donc **jamais tourne** sur cette session :
  `_select` est en aval de cette garde, et il n'y avait rien a apparier.

**Le releve d'apercu repare la cause** (`ImportPreview.readout`) : selections
detectees, blocs apparies, etat de la ligne `dossiers_ouverts`, affiches avant
la validation — **le seul moment ou l'information est encore recuperable**. Un
defaut dit une semaine plus tard sur la page de statistiques se repare en
recollant ; dit apres l'import, il ne se repare plus.
  - Le compte porte sur les blocs **apparies**, jamais sur les blocs presents :
    deux blocs dans le desordre ne rattachent rien, et annoncer deux crans que
    l'import n'ecrira pas serait pire que zero.
  - Le message d'un collage sans bloc ne dit pas « complete les blocs » comme
    celui du compte : ce n'est pas le rendu qui en manque, c'est le collage qui
    les a laisses.

### Les trois selections perdues a l'import du 14/08/2026

**Elles ne sont pas reprises, et c'est une decision.** La session 11 porte trois
selections a `lecture` / cran 1 : leurs blocs ```conf existaient dans le rendu et
n'ont pas ete colles. Les recoller injecterait trois crans **reconstitues apres
coup** dans la population qui doit justement mesurer si un cran declare tient
mieux qu'une lecture — et rien n'atteste que les blocs existaient, sauf un
souvenir. C'est le meme arbitrage que partout : un faisceau d'information ne
s'invente pas apres coup.

### Une colonne muette depuis sa naissance doit se voir

**Meme defaut que la densite a zero** : un echec qui produit exactement la meme
sortie qu'un succes. La carte « par cran calcule » disait « aucun cran calcule »
en l'imputant aux selections d'avant le chantier — c'etait vrai, et ca masquait
que les nouvelles non plus n'en portaient pas.

- **Une colonne a 0 % n'est pas un signal ; une colonne a 0 % sur les lignes
  posterieures a sa propre migration en est un.** L'age d'une colonne ne demande
  ni table ni saisie : `schema_migrations.applied_at` est deja en base.
- **Le seuil se compte en sessions d'import, jamais en lignes**
  (`COLUMN_GAP_MIN_SESSIONS`, 2). Une session peut rater son collage, deux
  d'affilee est systematique — et le seuil s'echelonne tout seul avec la taille
  du lot, ce qu'un seuil en lignes ne fait pas. En dessous, la ligne se rend sans
  le style d'alerte et dit combien de sessions sont concernees.
- **Le critere d'entree est le geste qui remplit la colonne** : celles qu'un
  import alimente, jamais celles qui dependent d'une saisie a la main.
  `price_real` reste dehors, sa couverture basse etant deja dite ailleurs et
  pour une autre raison.
- **`confidence_claimed` en est absente a dessein** : elle ne s'ecrit que sur une
  selection ecrasee, donc nulle partout est son etat normal. L'auditer ferait
  crier au defaut sur une base saine — la faute exacte que cet audit attrape.
- **`facts_json` porte, malgre son nom, le bloc entier** (`Claim.raw`). Non
  nulle veut donc dire « un bloc a ete apparie », quel que soit son contenu — et
  c'est ce qu'il faut auditer. Un bloc `"faits": []` est une reponse **normale**,
  que le gabarit impose meme avec `source_level: lecture` : auditer les faits
  confondrait le cas ordinaire avec le manque. Le nom, lui, tend ce piege a tout
  lecteur suivant.
- Se tait sur une colonne dont aucune ligne n'est encore passee : un chantier
  livre ce matin n'a rien a prouver avant le premier import.

### La declaration reste l'entree de la mesure (migration 045)

**`source_level` etait ecrase** par l'override : la valeur declaree disparaissait au profit
de `lecture`, et la colonne cessait d'etre une declaration de modele pour devenir une sortie
de l'application. La carte « par niveau de source » mesurait alors sa propre correction.

- `source_level` et `confidence` sont les **entrees** ; `source_level_effective` et
  `confidence_computed` vivent a cote. L'ecart entre les deux ne se stocke pas : les deux
  colonnes sont la, leur difference est une soustraction, et la recopier l'aurait fait
  diverger — meme regle que la famille d'un marche ou le niveau d'une competition.
- **La carte lit l'effectif, la mesure d'ecart lit les deux.** Un niveau annonce sur un
  dossier non ouvert decrit ce que l'analyse croyait avoir ; la question de la page est ce
  sur quoi la selection reposait vraiment.
- **Retro-remplissage sur, et c'est mesure** : `research_overridden` etait NULL sur les 149
  selections, donc l'ecrasement n'avait **jamais** tourne et aucune valeur declaree n'a ete
  perdue. L'effectif vaut le declare partout.

**La regle est a sens unique.** L'absence de dossier force la lecture ; la presence
n'accorde rien. Un dossier ouvert dont l'analyse ne tire **aucun fait date** est une lecture
des blocs — c'est le resultat de la recherche, pas son absence, et il se note pareil. Sans
cette moitie, ouvrir un dossier suffirait a se noter au-dessus de la lecture sans avoir rien
trouve.

**`Override.researched` compte les recherches qui n'ont pas eu lieu** : une selection hors
dossiers ouverts qui cite quand meme un fait date **avec son editeur**. Ce n'est pas un cran
mal note — un editeur cite suppose une page ouverte, et c'est cette page-la qui n'existe pas.
Distinct de `fabricated`, qui compte les crans hauts : les deux recouvrent souvent les memes
lignes, mais l'un decrit une note et l'autre un geste, et un cran 3 adosse a un fait invente
compte ici et pas la-bas.

**Quatre etats pour la ligne `dossiers_ouverts`**, la ou un NULL en confondait deux :
`renseignee`, `vide`, `absente`, `illisible`. Le modele qui omet la ligne et le lecteur qui
echoue a la relire produisent le meme repli — tout le lot en lecture — mais ni la meme cause
ni le meme correctif, l'un se reprenant dans le gabarit et l'autre dans le lecteur ; leur
somme se lirait comme un seul taux. Un rendu qui ecrit `dossiers_ouverts: M1, M4` sans
crochets passait pour une ligne omise, et le gabarit se faisait accuser d'un defaut de
lecteur.

- **`vide` s'est ajoute a la migration 049, et son absence etait la moitie du defaut** :
  `dossiers_ouverts: []` rendait `lue`, donc se lisait comme une liste renseignee. C'est
  pourtant une **declaration legitime** — le modele n'a rien ouvert, le gabarit l'autorise —
  quand une ligne absente est un collage rate. Les deux forcent tout le lot en lecture.
- **Toute valeur produite par `read_opened` doit figurer dans `OPEN_STATES`**, sinon elle
  s'ecrit NULL et l'etat disparait sans un mot. Le releve d'apercu nomme les quatre un par
  un et dit « etat inconnu » pour le reste : un cinquieme etat qui retomberait dans
  « absente » reproduirait exactement le defaut que ce releve existe pour rendre visible.

**Date de bascule : 14/08/2026, et aucun recalcul retroactif n'est possible.** Les neuf
sessions de la base portent `open_dossiers` a NULL — la colonne date de la veille et aucune
session n'a ete importee depuis. `confidence_computed`, `confidence_claimed` et
`research_overridden` sont a **0 sur 149** : toute cette machinerie n'a jamais recu une seule
entree. La population « confiance annoncee » change donc de composition a partir de cette
date, comme celle de `lecture` l'a fait pour l'EFL Cup.

**Attendu, et ce n'est pas une regression** : `lecture` est le cas ordinaire selon le
preambule, et la confiance 1 — absente des 149 premieres selections — deviendra
probablement majoritaire. C'est la mesure qui commence, pas la notation qui se degrade.

### Un cran 1 force porte sa cause (migration 049)

**Quatrieme occurrence du defaut caracteristique du projet, et cette fois sur la mesure
elle-meme.** La session 11 porte 16 selections a `research_overridden = 1`, donc toutes
ecrasees en lecture. Lues telles quelles, elles disent « aucune selection ne portait sur un
dossier ouvert » — une observation sur le modele, et c'est ainsi que la page les affichait.
Elles disent en realite que la ligne `dossiers_ouverts` n'a **jamais ete collee** :
`open_dossiers_state` vaut `absente`. Six causes distinctes produisaient le meme `1`.

- **Trois sont des observations** — `hors_dossiers`, `aucun_dossier`, `sans_fait` : elles
  decrivent ce que l'analyse a fait, et ont leur place dans une statistique sur le modele.
- **Trois sont des defauts de collecte** — `ligne_absente`, `ligne_illisible`,
  `reperes_non_resolus` : elles decrivent ce que le collage a perdu, se reparent en
  recollant, et `is_collection_fault()` les tient a l'ecart. `SessionRate.overridden` les
  deduit donc, et `override_faults` les compte a cote — leur somme reste le total ecrase.
- **`sans_fait` ne passe pas par `research_overridden`**, qui ne compte que les dossiers non
  ouverts : c'est la seule des six qui dise que la recherche **a eu lieu** et n'a rien donne.
  Sans elle, ce cran 1 se confondrait avec ceux qu'aucune recherche n'a approches.
- **Le retro-remplissage est sur, et c'est la base qui le prouve** — pas une reconstitution :
  `open_dossiers_state` est persiste depuis la migration 045, et une session dont la ligne
  etait `absente` ne pouvait ecraser ses selections pour aucune autre raison, l'ensemble des
  reperes resolus y etant vide par construction. Rejoue sur une copie : 16 typees, zero
  ecrasee sans cause, idempotent.
- **Controle strict et non `_vocabulary`** : ce vocabulaire est produit par `Opened.cause` et
  non ecrit par le modele, donc il n'y a aucune orthographe a rattraper. Une valeur inconnue
  vaut « on ne sait pas », jamais un refus.

**Ce qui n'a pas ete fait, et c'est une decision datee du 14/08/2026 : recalibrer les cibles
de combine.** La mesure disait la cible longue hors d'atteinte — **0 prompt sur 39**, meilleur
produit atteignable 19,9 contre une cible a 100 — et le vivier, non le budget, comme
contrainte : mediane 2 jambes par prompt, maximum 6, quand le budget en autorise 7. Mais ce
vivier a ete mesure en **regime casse** : sans la ligne `dossiers_ouverts`, aucune selection
ne peut depasser le cran 1, donc aucune jambe n'existe. Poser une cible sur cette
distribution-la, c'est refaire le meme travail dans six semaines. Les options — recalibrer,
n'exiger qu'un seul combine, ouvrir le long aux jambes de cran 2 — se reevaluent sur deux ou
trois sessions propres, et aucune ne se decide utilement avant.

- Mesure a garder pour cette reevaluation : `m`, moyenne **geometrique** des cotes des deux
  paliers surs, vaut **1,690** sur 155 selections. C'est la seule des trois entrees du calcul
  propose qui soit mesurable aujourd'hui ; `r`, le rendement d'un dossier ouvert, a **zero
  observation**.
- **Le garde-fou `cote_max_atteignable` a ete envisage et mesure** : calcule sur les cotes du
  lot, il declare la cible de 100 atteignable sur **24 prompts sur 31**. Il ne l'aurait donc
  jamais signalee. L'ecart est entre les prix **offerts** et les selections **produites**, et
  seule la seconde contraint — ce que le dossier notait deja : « les quatre autres echouent
  faute de selections produites, pas faute de prix ».

**Defaut trouve par un test existant, et il valait pour tout le chantier precedent** :
`ImportPreview.ignored` **garde le rendu du tableau entier**. Y verser une remarque —
un bloc de confiance rejete, un appariement refuse, un dossier non ouvert — faisait
disparaitre l'import en entier, donc coutait la possibilite d'importer pour un detail.
D'ou `notes`, second canal : ce qui accompagne un apercu **lisible** n'est pas ce qui
empeche de le lire. La distinction n'existait pas, et les trois messages ajoutes depuis
deux chantiers tombaient tous du mauvais cote.

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
    manuelle. Et ce n'est pas une entree morte : `odds` porte **11 lignes** de cle
    `outright`, toutes du bookmaker `manual`. Le marche est releve, il n'a
    simplement jamais produit de selection. Rien a purger, rien a corriger cote
    parsing.
    - **Le tiret ne distinguait rien, et cette page a porte le defaut
      caracteristique du projet pendant tout ce temps.** Il etait rendu sur
      **toutes** les lignes classees : `vainqueur`, 76 selections, et `cotes`,
      aucune, s'y lisaient a l'identique — donc la page ne repondait pas a la
      seule question qu'on lui pose devant un libelle qui surprend, et c'est ce
      qui a fait soupconner un artefact une seconde fois. Le compte reel est
      desormais rendu, sur la **cle de famille** comme le classement lui-meme, et
      « aucune » s'ecrit en toutes lettres : c'est un fait sur le libelle, le
      catalogue seedant des marches que le bloc sait ecrire et qu'on n'a jamais
      joues, et non une case restee vide.
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

## La prose de la section C (`picks.angle_note`, `picks.invalidation`)

**Le gabarit ecrit onze colonnes, `picks_import.HEADERS` en declarait huit.**
`Angle (1 ligne)` et `Ce qui la tue` etaient produites a chaque session, collees
a chaque import, et jetees par trois entrees manquantes dans un dictionnaire.

Mesure du 21/08/2026, faite avant d'ecrire une ligne : les **41 collages
archives portent tous l'en-tete complet**, une seule variante sur 41, et sur les
lignes rapprochables la cellule `Ce qui la tue` est non vide **76 fois sur 76**.
Le taux de renseignement par le modele etait parfait ; c'est la captation qui
manquait. Le commentaire du champ `angle` signalait meme le piege — « Angle »
n'est pas un alias d'`angle` — sans qu'il existe nulle part ou verser la phrase.

- **`invalidation` porte le controle 7 du cadre** — « chaque selection porte une
  condition d'invalidation » — donc la seule des deux colonnes qui soit
  **opposable**. Et c'est la seule colonne de ce chantier qu'un bilan pourra
  relire sans precaution de date : elle est ecrite **avant le coup d'envoi**,
  donc rien de ce qui vient apres ne peut la contaminer. C'est ce qui la separe
  d'un commentaire.
- **`angle_note` reste distincte d'`angle`**, qui porte le vocabulaire ferme
  `issue` / `maniere`. Les fondre ferait entrer une phrase entiere dans un champ
  a deux valeurs, et la carte « par type d'angle » cesserait de regrouper quoi
  que ce soit.
- **Un tiret n'est pas une condition** (`PROSE_EMPTY`). Un rendu ecrit `—` pour
  dire « rien ici » ; le recopier ferait passer la ligne pour couverte et le
  controle 7 passerait sur une selection qui ne porte rien — le defaut
  caracteristique du projet, applique a un controle.
- **`prose_source` dit d'ou vient la valeur**, `import` ou `reconstruit`. Ce
  n'est pas « fiable » contre « moins fiable » : une captation recopie une
  cellule que le lecteur avait sous les yeux, une reprise decoupe une ligne par
  ses offsets, donc par une regle qui peut echouer. `NULL` veut dire que les
  deux colonnes sont vides — il n'y a rien a situer. Meme regle que
  `price_source` : ce qui a ete deduit se declare, ce qui n'existe pas ne se
  declare pas.
- **Les deux colonnes sont rendues sur la feuille de session.** Une donnee que
  rien ne lit finit par se retirer — c'est le sort exact de l'effectif collecte
  des mois sans lecteur, retire par la migration 022. Et il y a mieux : c'est en
  relisant la condition **apres** le resultat qu'on voit si l'angle a cede par ou
  l'analyse l'avait annonce.

### La reprise (`picks_import.rebuild_prose`, `myassistantbet-replay --prose`)

**A faire pendant que `raw_text` existe.** `imports_raw` ne commence qu'a la
session 15 (17/08/2026) : les 235 selections anterieures n'ont laisse aucun texte
et ne seront **jamais** reprises. Le solde se reprend, et il se reprend une fois.

- **Le decoupage passe par les offsets, jamais par un rapprochement de
  libelles.** `picks.offset_start` / `offset_end` ont ete ecrits par l'import qui
  a cree la ligne : ils designent *cette* ligne-la. Mesure comparee : un
  rapprochement sur `(session, selection)` rendait **76 lignes sur 77**, les
  offsets en rendent **117 sur 117** — section C et C-bis comprises — et sans
  aucun faux appariement possible.
- **L'entete en vigueur se lit a l'offset** (`_column_maps`), avec les memes
  remises a zero que `read` : un titre de section ferme l'entete en cours, et
  sans cette regle l'entete de la section C servirait a decouper une ligne de
  C-bis. Deux lectures paralleles du meme decoupage auraient fini par ne plus
  designer les memes colonnes.
- **Jamais d'ecrasement** : seules les lignes dont `prose_source` est nul sont
  reprises. Une passe rejouee ne change rien, et un test le verifie.
- Le compte des **non retrouvees** est nomme et non compte : un compte se
  resorbe, un identifiant se va voir.

### Ce qui etait deja livre, et qu'il ne fallait pas refaire

Le signalement d'une section declaree et absente du collage **existe depuis le
17/08** (`sections.for_paste`, rendu par `ImportPreview.readout`), et il a ete
**durci en refus le 20/08** : l'avertissement avait parle les 20 fois ou la ligne
`dossiers_ouverts` manquait, et les 20 imports avaient ete valides quand meme.
`main.import_picks` demande donc une confirmation explicite. **Un signal qui
n'arrete rien ne se distingue pas d'un signal absent** — et le verifier avant de
le reconstruire est la meme regle que « chercher d'abord si le bloc ne porte pas
deja le fait qui la contredit ».

## Les deux bornes d'une selection (`created_at`, `result_at`)

**`result_at` n'est pas une colonne de provenance : c'est le garde d'anteriorite
de la boucle de relecture.** `created_at` ouvre la fenetre — la selection a ete
posee avant le coup d'envoi, donc son prix est un prix d'avant-match ; il
manquait la borne **haute**, l'instant ou l'issue est devenue connue. Sans elle,
aucun bilan ne peut prouver qu'un fait qu'il invoque a ete releve avant que le
resultat soit su, c'est-a-dire qu'il ne retrospecte pas.

Mesure du 21/08/2026 : `picks` ne portait **qu'une seule colonne de date**. Sur
300 selections tranchees de section C, 148 etaient datees par
`reglements.observed_at` et 152 ne l'etaient par rien.

- **Elle dit quand nous l'avons su, jamais quand le match s'est termine.** Meme
  regle a sens unique que l'anteriorite : la base peut prouver qu'un fait precede
  la connaissance de l'issue, jamais qu'il la suit.
- **Elle s'efface avec le resultat.** Une ligne remise en attente perd sa date :
  un horodatage qui survivrait a l'effacement affirmerait une connaissance qui
  n'existe plus — le defaut caracteristique du projet, pose sur la colonne qui
  sert justement a dater ce qu'on sait.
- **Deux ecrivains et pas un** : `set_result` et `coupons.settle_all`, qui ecrit
  `result` en masse sur les jambes d'un combine. Le laisser sans date ferait de
  chaque jambe une ligne hors de portee de toute relecture.
- **La reprise ne prend que les reglements `applique`, jamais `divergent`**, et
  la distinction decide du **sens de l'erreur** — la seule chose qui compte pour
  une borne. Sur une ligne appliquee, le reglement a pose le resultat :
  `observed_at` precede l'ecriture, la borne est trop tot, un garde qui s'en sert
  refuse un peu trop et se trompe du bon cote. Sur une ligne divergente, le
  resultat vient d'une saisie humaine anterieure et `observed_at` n'est que la
  date ou la regle a relu la source : la borne serait **trop tard**, donc
  permissive. **Une borne qui se trompe dans le sens permissif est pire qu'une
  borne absente** — celle-la se voit et se compte.
- **Une tranchee sans date n'est pas suspecte, elle est hors de portee** d'une
  relecture qui a besoin d'une borne. Ce n'est pas la meme chose et ca n'appelle
  pas le meme geste. `Analysis.settled_undated` la compte, et la population est
  **close** : tout resultat pose depuis la migration 075 est date a l'ecriture.

### `framework_version` : le referent existait deja, et ce n'etait pas lui

**Trois etats en cinq jours sur la meme colonne, et c'est le troisieme qui
tient.** Le champ etait emis par une route que rien ne sert et ne persistait
nulle part ; il a ete estampille par l'application a l'ecriture (migration 075,
22/08/2026) ; il a cesse de l'etre le **27/08/2026**. La colonne reste,
historique — 205 lignes a `1.3`, ni remplies ni reecrites.

**Ce qui a fait tomber la deuxieme version est une premisse fausse du brief qui
l'avait demandee : il fallait creer un referent qui existait.** Migration 054,
et deux colonnes qui repondent deja aux deux questions :

- `sessions.gabarit_sha` — empreinte **mecanique** du gabarit rendu, calculee par
  `save_prompt`, qui bouge sur une virgule. Personne ne l'ecrit a la main, donc
  rien ne peut diverger ;
- `sessions.gabarit_version` — le libelle de la **decision**, incrementee a la
  main, qui dit *quel* changement.

**Et le hash faisait deja le travail.** Mesure du 27/08/2026 sur la base servie :
**cinq empreintes distinctes sous le seul libelle « lot-3 »**, six en comptant
`lot-4`. La granularite reclamee etait deja en base, sur la session, depuis le
17/08.

- **Troisieme copie, et du mauvais sujet.** `FRAMEWORK_VERSION` suit la
  **Skill** — publiee en 1.4 puis desactivee — quand `ACTIVE_PRODUCER` vaut le
  **gabarit**. Elle etiquetait donc ce qui ne produit pas, a cote de deux
  ecritures qui etiquettent ce qui produit, et rien n'obligeait les trois a
  concorder. Cas 3 de la regle des copies : quand on ne peut pas forcer l'accord,
  **on cesse de dependre de la copie**.
- **Elle sort de `AUDITED_COLUMNS`, et c'est le meme critere dans l'autre sens.**
  « Toute selection importee devrait la porter » est devenu faux : la laisser
  ferait crier au defaut sur le comportement voulu. Une sentinelle tient
  desormais l'equivalence **dans les deux sens** — auditee si et seulement si le
  payload produit.
- **Aucun retro-remplissage, et aucun effacement.** Les 382 selections d'avant la
  075 n'ont ete produites sous aucun cadre que la base connaisse ; les 205
  suivantes l'ont ete sous `1.3`, et le nier ferait perdre une borne reelle sur
  une population reelle. `NULL` est la verite pour ce qu'on ignore.
- **La migration 075 ne se corrige pas** : une migration deja appliquee ne se
  modifie jamais, et son commentaire decrit fidelement la decision du 22/08. La
  version en vigueur est ici.

### Le garde de cadre : ferme parce que la question est repondue ailleurs

`test_le_numero_de_cadre_s_appuie_sur_une_lecture` etait **rouge depuis le
27/08/2026** — cadre publie `1.4`, constante `1.3` — et le rouge etait
volontaire : la 1.4 a ete publiee puis la Skill desactivee, le gabarit porte
seul la methode.

**Un rouge volontaire est un rouge qui finira par se lire comme un rouge
ordinaire.** Il ne se tait pas pour autant : l'exigence est **conditionnee a
`ACTIVE_PRODUCER`**, meme forme que `FRAME_ALERT_MUTED`, premiere branche de la
regle des « a ne pas oublier » — une condition structurelle quand il en existe
une, jamais une date.

- **Ce que la comparaison garde n'existe que si le numero etiquette une sortie.**
  Tant que le gabarit produit, il n'en etiquette aucune, et un ecart entre deux
  copies dont aucune ne sert n'apprend rien. Le jour ou `ACTIVE_PRODUCER`
  bascule, `build_payload` reemet le champ dans ce qui part et la lecture
  redevient le seul moyen de savoir sous quel cadre.
- **La lecture se fait dans tous les cas, seule l'exigence est conditionnee.**
  Un garde conditionne qui cesserait de s'executer se serait tu pour deux raisons
  dont une seule est ecrite — exactement le defaut caracteristique du projet,
  pose sur le dispositif de verification.
- Le mecanisme reste entier (`services/framework.py`, `myassistantbet-cadre`) :
  c'est lui qui redeviendra exigeant, et il sert deja la CLI.

## De quel lot une selection est sortie (`picks.prompt_id`)

Une selection n'etait reliee qu'a une **session**, qui porte 1 a 20 prompts.
`combos.prompt_id` est `NOT NULL` depuis la migration 047, et pour une raison
qui vaut ici aussi : les selections de deux prompts n'ont jamais ete comparees
entre elles — chaque instance a choisi dans son lot, avec son quota et son
budget propres.

Mesure du 21/08/2026 sur les 312 selections de section C, par reconstruction :

| Candidats via `prompt_events` | Lignes | Part |
| --- | ---: | ---: |
| un seul | 121 | 38,8 % |
| deux ou trois | 66 | 21,2 % |
| quatre et plus | 14 | 4,5 % |
| aucun prompt archive | 111 | 35,6 % |

- **A l'ecriture, le prompt qui a valide.** La colonne se remplit depuis
  `PromptBlocks`, le prompt dont les en-tetes de blocs ont apparie le tableau
  colle — **le meme objet** qui donne son identifiant a un combine. Deux lectures
  paralleles auraient fini par designer deux prompts differents, et l'appariement
  porte sa somme de controle (le champ `match` de chaque bloc) : c'est une
  verification, pas une deduction.
- **Verifie contre la session, jamais pris au mot.** Un identifiant inconnu ou
  pointant sur une autre session vaut `NULL` : un lien faux serait pire que nul,
  puisqu'il servirait ensuite a comparer des selections qui n'ont pas ete
  produites ensemble.
- **Sans bloc de confiance, aucun prompt ne valide et le lien reste nul.** En cas
  de doute, rien — la regle du projet, appliquee au rattachement d'un lot.
- **La reprise ne prend que le candidat unique** (121 en section C, 32 en C-bis).
  Une reconstruction sur 21 % de candidats multiples serait une **fausse
  certitude** : le lien parait pose, rien ne dit qu'il designe le bon lot.
  `Analysis.picks_sans_prompt` compte les autres — un compte se lit, un lien
  invente ne se voit plus.
- La clause de reprise s'indexe sur `prompt_id IS NULL`, donc sur la colonne
  qu'elle corrige : idempotente et complete par construction, la lecon de la 049.

## Les controles du cadre, comptes a l'import (`services/controls.py`)

Le cadre enonce dix controles « a passer systematiquement, dans l'ordre ».
L'application sait repondre a **quatre** d'entre eux depuis toujours — elle
connait les evenements rapproches, les crans, les niveaux de source, et depuis
le lot A la condition d'invalidation — et elle ne l'avait jamais dit.

Mesure du 21/08/2026 sur les 312 selections de section C, faite **avant**
d'ecrire une ligne, et c'est elle qui leve la reserve « ne pas opposer un
controle avant d'en connaitre le taux de base » :

| Controle | Ce qu'il dit | Violations mesurees |
| --- | --- | ---: |
| 1 | une seule selection par evenement | 16 |
| 7 | chaque selection porte une condition d'invalidation | 1 sur les 117 captables |
| 8 | aucune conf 2 dans le tableau principal | 36 |
| 9 | dirigee par un fait de niveau 1 ou 2 | 39 |

- **Compter, jamais bloquer, et la raison est la remediation.** Pour les
  controles 8 et 9 elle est le **renvoi en C-bis**, qui se decide dans le rendu
  et pas a l'import : refuser la ligne la ferait disparaitre du lot sans qu'aucune
  trace ne dise pourquoi — le rejet silencieux que ce projet retire partout.
- **Mais un avertissement ne suffit pas, et c'est mesure.** Celui de la section
  manquante a parle **20 fois sur 20** et les 20 imports ont ete valides quand
  meme. Le compte passe donc par la **confirmation explicite**, meme mecanisme
  que la ligne `dossiers_ouverts` absente : rien n'est refuse, rien ne se
  franchit sans avoir ete vu.
- **Deux cases et non une.** Cocher pour une section manquante ferait passer au
  meme geste des ecarts au cadre qu'on n'aurait pas lus.
- **Trois etats, jamais deux.** Un controle dont la **colonne** manque a l'en-tete
  est `muet`, pas `tenu` : sans cette distinction, le controle 7 dirait « aucune
  condition d'invalidation » sur un collage a huit colonnes, c'est-a-dire une
  violation la ou la question n'a pas ete posee. Meme vocabulaire que `Absents`.
  Un muet **ne se confirme pas** — la case deviendrait le decor qu'elle existe
  pour ne pas etre.
- **Les recouvrements se rendent.** `16 + 36 + 39` ne fait pas 91 si les
  ensembles se croisent, et une ligne qui viole deux controles ne se repare pas
  comme deux lignes qui en violent un. Le compte de lignes **distinctes** ferme
  l'addition, et les paires sont nommees.
- **Aucun second lecteur** : les lignes viennent de `picks_import`, le seul module
  qui sache decouper le tableau. Une expression reguliere posee a cote finirait
  par ne plus designer les memes lignes, et deux comptes du meme collage se
  contrediraient sans qu'aucun ne soit faux.
- **La garde relit le collage conserve**, jamais un champ cache : ce qui retient
  l'import ne peut pas voyager par le formulaire qu'il retient. `imports_raw`
  fait foi, exactement comme pour les sections. Sans collage relisable — saisie a
  la main, rejeu — on ne retient rien.
- **Six controles ne sont pas ici, et c'est delibere.** La ligne en quart et la
  cote inventee se lisent sur le bloc du match, l'anteriorite a deja sa garde et
  son compte, le H2H seul et « chaque match apparait quelque part » demandent le
  rendu entier. Les nommer sans pouvoir les compter donnerait l'apparence d'une
  couverture complete.

**Ce qui reste a mesurer, et ce n'est pas la presence.** Le controle 7 est
renseigne a **98,7 %** en section C et **100 %** en C-bis : comme garde il rendra
peu. Ce qui a de la valeur est le **declenchement** de la condition — l'angle
a-t-il cede par ou l'analyse l'avait annonce — et sa pertinence quand il ne se
declenche pas sur une selection perdue. C'est le gisement du futur bilan.

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
  pixels et le graphique n'ajoute plus rien au pourcentage ecrit a cote. **Une carte seule
  ne se coule pas en deux colonnes** (`.charts.is-single`) : « Par session » occupait la
  moitie gauche et laissait l'autre vide, avec une barre deux fois plus courte que
  necessaire pour la seule grandeur du bloc.
- Le tableau chiffre complet (annules, en attente) reste accessible sous chaque bloc, dans
  un `<details>`. Un graphique ne remplace pas les nombres, il les ordonne.
- **Aucune colonne de barre n'a de largeur ecrite d'avance pour du texte.** La colonne du
  compte fait trois caracteres, et deux annotations en faisaient vingt-cinq : l'effectif
  independant (`24/40 · 39 ev.`) debordait hors de la carte, et la vacance d'un palier
  (`0 · absent de 6/6 session(s)`) **passait par-dessus la carte voisine** — les libelles
  de palier se lisaient sur les comptes de la colonne de gauche. Deux corrections, et
  chacune repond a une question differente :
  - ce qui **precise le compte** reste dans sa colonne, qui se dimensionne desormais sur
    son contenu (`minmax(3.1rem, max-content)`), la piste prenant ce qui reste — d'ou le
    `minmax(0, 1fr)`, sans lequel elle refuse de descendre sous sa largeur minimale ;
  - ce qui **qualifie la ligne** — un palier laisse vide six sessions de suite, une journee
    dont le lot n'a pas ete enregistre — descend sous son libelle, comme une bande cible.
    C'est une propriete du niveau, pas une precision sur un nombre, et la place existe la.

### La densite de la page : ce qui porte un nombre, et ce qui explique une methode

La page portait **huit paragraphes de methode** entre ses graphiques — l'intervalle de
Wilson, l'effectif independant, les lignes minces, la testabilite d'un ecart (celle-la
deux fois, une par carte) — et les chiffres qu'ils commentent se perdaient dedans. Le
detail chiffre arrivait apres un ecran entier de prose.

- **Rien n'est retire.** La regle de cette page n'a pas change : c'est la surface ou
  l'utilisateur regarde ses propres donnees, et un manque s'y dit — c'est justement ce qui
  la separe du prompt, qui se tait sous les memes seuils. Ce qui change est la
  **partition** : ce qui porte un **nombre ou un lien** reste lu, ce qui explique une
  **methode** attend qu'on le demande (`details.why`, macro `why()`).
  - Corollaire : le resume d'un depliant porte le nombre quand il y en a un — « il faudrait
    ~92 selections par ligne » — et **jamais** un « en savoir plus ». Un depliant dont le
    resume n'apprend rien ne se deplie pas, et l'information est perdue pour de bon.
  - `card()` a donc deux parametres distincts, `note` et `reason`, pour que le choix se
    fasse a l'ecriture de la carte et non a la relecture d'un paragraphe qui aurait
    melange les deux.
- **Trois notes de methode deviennent un bandeau de comptes** (`details.readout`) : leur
  part actionnable est un nombre, et un nombre tient sur une ligne. Le raisonnement est
  dessous, mot pour mot.
- **Un constat se pose sur ce qu'il decrit.** La concentration d'une echelle etait rendue
  en bandeau au-dessus du bloc, donc recopiee pour chaque echelle : deux paragraphes
  presque identiques dont seul le premier tiers changeait. Le tiers qui change vit sur sa
  carte, les deux tiers communs une seule fois sous le bloc.
- **Un sommaire d'ancres** en tete : cinq blocs repondent a cinq questions differentes, et
  rien ne disait ce qui venait. Il suit les blocs **reellement rendus** — annoncer une
  ancre vers une section absente serait pire que pas de sommaire. `scroll-margin-top` tient
  compte de la barre collante, sans quoi l'ancre depose le titre dessous.
- **Corollaire pour les tests, et c'est ce qui a rendu la refonte sure** : les tests de
  cette page cherchent des **phrases** dans le texte servi. Une prose deplacee dans un
  `<details>` y reste ; une prose reecrite, non. Les phrases n'ont donc pas bouge d'un mot
  la ou un test les nomme — et la ou l'une a du changer, c'est qu'elle disait deux fois le
  meme nombre que la tuile posee au-dessus d'elle.

### L'export de la page (`services/stats_export.py`)

La page n'etait consultable qu'a l'ecran : la faire relire ailleurs demandait d'enchainer
les captures. `GET /api/stats/export?format=md|json` rend le meme etat des lieux en un
fichier autoportant — Markdown pour un lecteur humain ou un modele, JSON pour une machine.

- **Une seule source de calcul, et c'est tout l'enjeu.** `report()` assemble ce que la page
  consomme, et la route HTML lit desormais son `context`. Rien n'est recalcule cote export :
  un chiffre qui differerait entre l'ecran et le fichier serait pire que pas de fichier du
  tout, puisque l'export existe justement pour faire relire ces chiffres-la. Effet de bord
  du regroupement : le releve des scores en sets n'est plus fait deux fois par rendu de page.
- **Chaque taux porte son denominateur et son intervalle.** A l'ecran l'effectif est a cote
  de la barre et l'intervalle est dessine dessus ; le fichier n'a ni l'un ni l'autre, il les
  ecrit. Un pourcentage seul, hors de la page, est exactement ce que cet export corrige.
- **Les reserves voyagent avec les chiffres** (`StatsReport.warnings`) : sous-effectif,
  population ecartee faute d'anteriorite, absence de cran calcule retroactif, recouvrement de
  deux regroupements. Assemblees **une fois** et portees par les deux ecritures — deux
  redactions cote a cote auraient fini par ne plus dire la meme chose.
- **La parite se verifie sur les deux rendus reels**, jamais sur une table de correspondance
  qui aurait vieilli de son cote : le test extrait les titres de la page servie et exige,
  pour chacun, la section correspondante dans le fichier, **sous le meme bloc** — deux blocs
  portent une carte « Par palier », et seul le couple les distingue. Il a attrape deux
  manques a sa premiere execution : la carte « Coupons », et « Par type d'angle » dont
  l'apostrophe echappee par Jinja ne s'appariait pas.
- Le registre `SECTIONS` est le contrat, et `StatsReport.sections` dit ce que **ce** releve
  rend : le fichier ne peut donc pas porter une section que la page tait, ni l'inverse.

## Mesurer l'existant avant de construire le demande rapporte plus que la demande

**Regle de methode, du 20/08/2026, et c'est la deuxieme fois en trois lots.** Le
lot 18 a livre ses dix points ; les trois defauts les plus graves qu'il corrige
n'etaient **dans aucun** d'eux, et tous trois sont sortis de la mesure faite
**avant** d'ecrire la ligne demandee :

| Trouve en mesurant | Ce qu'on mesurait | Ce qu'on cherchait |
| --- | --- | --- |
| `Ici` rendait le tournoi de la semaine passee | la couverture de `Ici` sur les blocs archives | de quoi soustraire les matchs non couverts |
| un handicap posable etait jete 94 fois sur 94 | la part des lignes en quart | de quoi choisir un signe de marquage |
| le palmares plafonnait a 12 joueurs par jour | pourquoi une ligne ne sortait pas | une cause de couverture |

Les trois ont la forme caracteristique du projet — l'echec et le cas ordinaire
rendaient la meme chose — et **aucun n'etait visible depuis le rendu**. Ils l'ont
ete depuis la **distribution** : un compte par bloc, un taux par sport, une
mediane par journee.

- **La mesure preliminaire n'est pas une verification de la premisse**, c'est
  cela qui la rend rentable. On mesure la premisse pour savoir si la demande
  tient ; on mesure l'**existant** pour savoir ce qu'on va modifier — et c'est la
  seconde qui trouve ce que personne n'a demande.
- **Elle ne coute presque rien** : les trois mesures ci-dessus sont trois
  requetes sur `prompt_odds`, `prompt_events` et `api_responses`, toutes deja
  peuplees.
- **Le corollaire pour un brief** : dix points formules depuis un rendu decrivent
  ce qui se voit. Ce qui ne se voit pas ne peut pas y figurer, et c'est
  precisement ce que ce projet paie le plus cher.

**Et il vaut pour les chiffres qu'on rapporte.** Une mesure de defaut faite par
re-rendu de l'archive dit ce qu'un defaut **aurait pu** produire, jamais ce qu'il
a produit : les « 14 fragments sur 223 » du tournoi croise portaient sur des blocs
hypothetiques, quand la ligne n'a vecu qu'une soiree et n'a servi **aucun**
fragment faux. La distinction entre surface latente et exposition reelle se porte
dans la phrase, sinon elle se perd.

## La ligne `Ici` : un tournoi se corrobore, une couverture se soustrait

Deux defauts du meme lot, tous deux invisibles, tous deux dans la ligne livree la
veille.

**`_tournament_id` prenait le mode sur la fenetre d'edition, et rendait le
tournoi de la semaine passee.** Deux tournois se chevauchent une semaine sur
deux — le Canadien finit le lundi ou Cincinnati commence — donc notre fenetre
contient la fin du precedent. Un joueur qui entre en lice ici apres un bon
parcours ailleurs y voyait **l'autre tournoi** rendu sous le titre « ici ».
Mesure du 20/08/2026 : **14 fragments sur 223**, dont un bloc de Cincinnati
servant quatre matchs du Canadien d'un cote et un Washington de l'autre.

- La fenetre est donc **corroboree par nos propres scans** : un match de la
  source ne compte pour identifier le tournoi que s'il porte un adversaire ou un
  jour que nous avons scannes ici. Sans corroboration possible — un entrant —
  l'identifiant vaut **0**, et c'est son partenaire qui le donne, ce que
  `here_lines` faisait deja. Rejeu : **223 justes, 0 faux**.
- **Le jour se compare a l'exact pour corroborer**, jamais a un jour pres : la
  tolerance parait prudente et ouvre precisement le trou qu'on ferme, un match du
  tournoi precedent tombant la veille d'un match d'ici. Un joueur ne dispute
  qu'une rencontre par jour — la premisse de `_resolve_duplicates`.

**`Ici` s'arretait avant le match qui compte**, et la soustraction etait laissee
a l'analyse. `Parcours` nommait quatre adversaires de Bejlek, `Ici` en couvrait
trois, et le quatrieme etait **Sabalenka**, jouee la veille — le fait dominant de
la rencontre. La ligne nomme desormais ce qu'elle ne couvre pas.

- **La borne « posterieur au releve » est fausse deux fois**, et c'etait la
  premisse du brief. Comparee au **jour** elle n'attrape **rien** — la journee de
  tournoi du match vaut le jour du releve alors que le coup d'envoi est a 00h30
  le lendemain ; comparee a l'**instant** elle n'attrape que **6 des 28** — un
  match commence trente minutes avant le releve n'est pas fini quand il passe, et
  il faudrait la duree, qu'aucune source ne publie. **La soustraction, elle, n'a
  aucune borne a choisir.**
- **Le nom se compare genereusement, le jour strictement, et c'est l'arbitrage
  inverse de la corroboration.** Rendu strict, le fragment nommait trois matchs
  « non couverts » dont le score figurait sur la ligne juste au-dessus — nos
  scans ecrivent « Leylah Fernandez », la source « Leylah **Annie** Fernandez ».
  Un faux positif depense une place de dossier, un faux negatif ne fait que taire
  un fragment qui n'existait pas hier : en cas de doute on declare **couvert**.
  `_same_player` applique la regle de `tennis_history.resolve` — meme nom de
  famille, prenoms en chaine de prefixes — qui separe les freres Zverev.
- **Une seule regle de nom dans le module**, et elle sert aux deux usages : la
  strictesse a ete essayee cote corroboration et elle etait inutile, les 14
  fragments fautifs portant des adversaires jamais scannes ici. C'est le jour
  exact qui les ecarte.
- **Un troisieme critere, et il se lit ailleurs que sur nos scans** : le **nom du
  tournoi** que la source porte dans chaque match, compare a
  `profile_tournament_names` — la table verifiee a la main du lot 17, qui
  existait et n'etait branchee que sur le palmares. Les deux gardes sont
  **cumulatives** : la corroboration par les scans peut tomber sur un joueur qui
  a croise le meme adversaire dans les deux tournois de la quinzaine, le nom du
  tournoi ne le peut pas. Une competition non rattachee la rend **muette et
  jamais negative** — un ensemble declare vide n'affirme rien.
- **Portee reelle du defaut : nulle, et il faut le dire.** Deux prompts archives
  sur 167 portent une ligne `Ici`, pour 5 blocs et 10 fragments ; les 22 matchs
  qu'ils listent sont tous corroborés, et les trois selections posees dessus
  reposent sur des fragments justes. Le defaut a vecu vingt-six heures en code et
  n'a servi aucun bloc faux.
- La ligne reste **rare** : 7 blocs sur 195, 12 fragments. Une ligne qui sortirait
  partout cesserait d'informer.
- **`Fraicheur` ne bouge pas, et le brief demandait qu'elle bouge.** Elle ne dit
  pas « on ignore ce qui s'est passe » : elle dit que
  `Forme/Usure/Profil/Marge/Niveau adv.` sont arretees a la date de
  `tennis-data.co.uk`, une source hebdomadaire **distincte**, et les quatre
  matchs y manquent — y compris les trois dont `Ici` donne le score. Ramener le
  compte ferait lire « Usure » comme comptant trois matchs de plus qu'elle n'en
  compte.

**Le nombre de tours, lui, s'etablit** quand la phase reste inconnue :
`au moins 4 tours disputes par X, 3 par Y`. Il decide de quelque chose — le
gabarit conditionne les paliers hauts a un enjeu asymetrique — et il se compte
sur nos propres scans. « au moins », le debut d'un tableau pouvant preceder notre
fenetre. La source de profils n'ajouterait un tour que sur **11 joueurs sur
192**, toujours un seul : la dependance ne se paie pas pour un mot qui reste vrai
des deux cotes.

## Un releve anterieur au tournoi n'affirme rien, et il l'affirmait

**Troisieme defaut de la ligne `Ici`, et le plus dangereux des trois** : les deux
premiers rendaient un fait faux ou incomplet, celui-ci rendait une **absence de
donnee sous la forme d'un fait**. Mesure du 28/08/2026 sur les 41 blocs de tennis
archives portant une ligne `Ici`.

Cas reel, prompts 227 et 229 :

    Ici    Diane Parry aucun match dans ce tournoi [releve au 19/08]
           2 matchs non couvert (tout le Parcours)

Le tournoi avait commence le 22/08. La charge utile datait de **trois jours avant
la premiere rencontre** : elle ne pouvait en contenir aucune. « Aucun match »
decrivait notre fenetre de collecte, pas la joueuse — et `Parcours`, deux lignes
plus haut, lui donnait deux adversaires.

- **Les deux etats se separent sans ambiguite, et c'est mesure** : sur les cinq
  fragments « aucun match dans ce tournoi » du corpus, **2 portent un releve
  anterieur** au premier jour connu du tournoi et ont tous deux deux rencontres
  non couvertes ; **3 portent un releve posterieur** et ont tous trois zero. Le
  libelle d'origine reste donc juste dans son cas — une entree en lice est un
  fait sur le match, souvent le fait dominant — et il ne bouge pas. Un banc le
  garde, aussi important que celui qui monte le cas corrige.
- **Le repere voyage sur le canal qui porte deja l'identifiant de tournoi**, donc
  aucune seconde lecture : le premier jour connu sort du meme appel
  `_tournament_matches` qui produit les fragments. Le recalculer ailleurs aurait
  fait diverger deux comptes du meme tournoi.
- **L'invariant rend le partage total, et il n'y a pas de troisieme etat a
  inventer.** Un joueur n'atteint la branche « sans match » qu'avec un identifiant
  **recu** — `_tournament_id` ne resout rien sans rencontre a corroborer — donc un
  partenaire a deja tourne et le repere est renseigne. Verifie sur le corpus : les
  cinq fragments en ont un.
- **La mesure de couverture prend un quatrieme etat** (`HereCoverage.stale`).
  Compter un releve perime comme `renseigne` ferait passer une fenetre de collecte
  pour un bloc servi — le defaut caracteristique du projet, pose sur l'instrument
  qui doit le mesurer. `filled` ne peut pas bouger : le nouveau libelle ne
  remplace que `HERE_NO_MATCH`, donc seuls des blocs `partiel` se reclassent.
- **Portee : deux fragments dans le corpus archive, un evenement.** Rejeu de
  `here_coverage` sur les 213 blocs de tennis soumis — 92 renseignes, 71 partiels,
  **1 perime**, 49 absents. Le rejeu dit ce que le defaut produirait **aujourd'hui**,
  jamais ce qu'il a produit : les profils ont ete rafraichis depuis, et c'est le
  corps des prompts archives qui donne l'exposition reelle.
- **Le motif ne se retrouve nulle part ailleurs, et la raison est structurelle.**
  Balayage des autres lignes datees : `Historique` et `Fraicheur` ecrivent leur
  date d'arret, `Service` / `Retour` / `Jeux` rendent « non disponible » — un
  enonce sur notre donnee, pas sur le joueur — `H2H` dit « aucun match joue depuis
  <annee> » avec sa fenetre, `palmares` ne dit « jamais joue » que sur un palmares
  effectivement lu. **`Ici` est la seule ligne dont le sujet est le tournoi en
  cours**, donc la seule ou quelques jours de retard peuvent retirer exactement ce
  qu'elle rapporte. Partout ailleurs le passe est clos et le retard ne peut pas
  inverser l'enonce.

## La fiche de priorite contredisait la fiche de verification

**Mesure du 28/08/2026, et c'est le §8 sur deux sorties qui ne se parlaient
pas.** Le critere tennis du detail de tournoi se lisait sur `Fraicheur` :

    elif "non comptes" in (lignes.get("Fraicheur") or ""):

Or les deux lignes datent **deux retards differents**. `Fraicheur` porte celui du
fichier hebdomadaire `tennis-data.co.uk`, qui alimente `Forme`, `Usure`,
`Profil`, `Marge` et `Niveau adv. ` ; `Ici` porte la couverture du **tournoi en
cours**. Seule la seconde repond a « quels tours de ce tournoi le bloc ne raconte
pas ».

Rejeu sur les 41 blocs de tennis archives portant une ligne `Ici` :

| | blocs |
| --- | ---: |
| question emise, `Ici` couvre tout | **28** |
| question emise, `Ici` laisse un trou | 11 |
| aucune question, aucun trou | 2 |
| **question ajoutee par le nouveau critere** | **0** |

- **Le gabarit disait deja l'inverse sur ces 28 blocs.** La fiche de verification
  tennis porte, mot pour mot : « ne les cherche pas, et ne les refais pas de
  zero » pour ce que `Ici` sert, puis « quand `Ici` nomme des matchs non
  couverts, ou qu'elle est absente, cherche ces scores-la ». Les deux fiches du
  meme prompt se contredisaient sur les memes blocs, et c'est la fiche de
  verification qui avait raison.
- **Un critere qui se declenche sur 95 % des blocs n'en classe aucun.** 39 des 41
  blocs portaient au moins un motif ; 13 en portent. Ce n'est pas une perte de
  couverture, c'est la fin d'un tri qui ne triait pas — meme reproche que celui
  fait aux deux criteres faibles du football et au seuil de repos pose sur un
  mode.
- **La duree ne justifie pas de garder la question.** Elle n'est servie par
  aucune source, et la fiche de verification la reclame deja une fois pour tout
  le lot — « va chercher les trois choses qu'aucune source ne sert : la duree,
  les conditions reelles du court, et le double ». Un dossier n'a pas a repayer
  ce que le cadre demande partout.
- **Trois etats de la ligne, deux seulement sont des trous.** `non couvert` et
  `HERE_NO_INFO` en sont ; `HERE_NO_MATCH` non — c'est une **entree en lice**,
  donc un fait sur le joueur. Les deux blocs que la premiere mesure comptait
  comme « question manquante » etaient exactement ceux-la, Chwalinska et
  Alexandrova, releve posterieur et zero non-couvert. La distinction est celle
  que le libelle perime venait d'introduire : les deux chantiers se rejoignent.
- **Le motif nomme le joueur, jamais l'adversaire.** Nommer le joueur est
  possible sur les 39 blocs qui portaient la question ; nommer l'adversaire
  recopierait `Parcours` huit lignes plus haut et ne vaudrait que **5 fois sur
  39**, la ligne ecrivant « (tout le Parcours) » le reste du temps.
- Les noms viennent de l'**evenement** et jamais de la prose ; seul le marqueur
  s'y lit, par ses constantes.

## Deux regles du gabarit se contredisaient sur le plafond de selections

`Quotas de ce lot : … Une seule selection par match, **donc** le total ne peut
pas depasser N` — et neuf lignes plus bas : « Deux selections sur un meme match
ne se justifient que si elles reposent sur des angles reellement independants ».
Le « donc » reposait sur une premisse que le meme chapitre dement.

- **La permission sert, elle n'est pas theorique** : sur les 574 matchs portant
  au moins une selection, **13 en portent deux**, 9 en section C.
- La regle vivait **deux fois** — une fois sans son exception et suivie du
  « donc », une fois avec. Elle ne vit plus qu'une fois, la ou elle est
  qualifiee, et le plafond s'annonce comme ce qu'il est : `N lignes au plus,
  sauf second angle independant`. Solde : **+10 tokens**.
- Le banc n'assertait plus la phrase mais la **propriete** : le nombre annonce
  est la taille du lot, et la phrase ne se donne pas pour infranchissable.
- **`au moins 1 tour disputes` s'accorde enfin.** Le pluriel etait porte par le
  seul nom et jamais par le participe. Rien ne cassait — et c'est ce qui le
  rendait genant : le lecteur se demande si le pluriel porte une information que
  le chiffre ne porte pas. Corrige dans le meme lot que le reste du texte, et non
  dans le correctif du compte, ou il aurait rendu le rejeu illisible.

## Un ecart se nomme sur ce qui se gagne, et se tait sous le bruit

`Ecart` confrontait le taux de premieres balles **mises en jeu**. Sur un bloc
reel il rendait `+0.1 pts` — les deux joueuses a 63,2 % — quand la ligne
`Service` juste au-dessus portait **6,1 points** sur les points gagnes derriere
la premiere et **7,5** sur les doubles fautes.

- **Ce n'est pas la grandeur la moins dispersee, c'est la plus**, et la nuance
  decide de l'implementation : ecart absolu median **4,3 points** contre 3,5 sur
  les points gagnes, mesure sur 174 paires. Un seuil pose sur l'etalement aurait
  garde la grandeur qu'on voulait retirer. Ce qui la disqualifie est **ce qu'elle
  mesure** — qui rentre sa premiere balle, pas qui en tire quelque chose.
- **Le seuil se lit sur les denominateurs de la ligne**, jamais sur un nombre
  choisi : un ecart n'est nomme que si son intervalle de **Newcombe exclut
  zero** (`inference.difference_interval`, deja ecrit). Il s'adapte donc au
  volume de chaque joueur. Meme famille que `HANDICAP_ALERT_MARGIN` : un silence
  vaut mieux qu'une affirmation que la donnee ne porte pas.
- Taux de declenchement : 49 % pour les points gagnes, 49 % pour les doubles
  fautes, 36 % pour le retour ; **aucune ligne sur 19 % des blocs**.
- **Sur les doubles fautes, l'avantage est au plus bas taux.** L'inversion est
  portee une seule fois, par un booleen de la table des grandeurs — deux
  ecritures auraient diverge, et un ecart lu a l'envers est l'erreur la plus
  couteuse que ce bloc puisse produire.

**Et les unites d'une meme famille de lignes doivent se comparer.** `Ici` rendait
`12 df`, un compte brut, quand `Service` rend `11.3% df` sur les secondes
balles : 12 doubles fautes sur ~81 secondes balles font 14,8 % sur ce tournoi
contre 11,3 % sur 52 semaines, degradation nette que rien ne donnait a lire. Le
compte brut n'est pas garde a cote — il faudrait un seuil de « petit
denominateur » qui s'inventerait, et la parenthese `(3 matchs, 212 pts)` borne
deja le fragment.

## Les lignes en quart, et le handicap posable qui etait jete

La regle « au football, les lignes en quart ne se posent pas » vivait dans le
preambule et se re-derivait bloc par bloc. Elles portent desormais `†` **la ou
elles sont ecrites** : 412 des 1 414 lignes d'echelle affichees, soit **29 %**.

- **Au football seulement, et c'est mesure** : zero point en quart sur les 4 944
  issues de tennis archivees. Le marquer la-bas serait du decor.
- **Aucune legende par bloc** : elle se dit une fois dans le preambule, ou la
  regle vivait deja et ou la porte de sport la garde. Vingt-quatre legendes pour
  vingt-quatre blocs, c'est le defaut que `common_unplayable` a corrige.

**Le defaut trouve sous celui-la est plus grave.** Le total rend cinq lignes,
dont une ou deux en quart ; le handicap n'en rend qu'**une**, donc un palier
d'equilibre en quart laisse le bloc sans aucun handicap posable — **94 des 268
paliers principaux, soit 35 %**. Et sur **94 cas sur 94**, un palier entier servi
des deux cotes existait dans l'echelle et etait **jete** : un tiers des blocs de
football montrait la seule ligne impossible a poser et cachait les quatre autres.

Une seconde ligne `posable` rend ce palier, choisi par `_main_handicap` sur
l'echelle restreinte — **la meme fonction**, jamais un second departage qui
aurait diverge. L'equilibre reste rendu, marque : il situe le match mieux
qu'aucun autre.

## Une retrogradation ordonne, elle n'ecarte pas (`research.merit`)

La fiche de priorite compte desormais les marches **presents** sur un bloc : un
bloc qui n'en porte qu'un plafonne toute selection au 1N2, quelle que soit la
qualite de la recherche. Sur le lot du 20/08, **trois des quatre** dossiers
proposes etaient dans ce cas.

- **Ce n'est pas regarder une cote**, et la regle « la fiche ne regarde aucun
  prix » tient entiere : aucune **valeur** n'est lue, seulement le nombre de
  familles presentes. Le test qui gardait cette regle comparait un bloc **sans**
  marche a un bloc **avec** — il testait donc la presence ; il porte maintenant
  sur deux blocs aux memes marches a des prix opposes.
- **Le seuil est « un seul », et il designe 1 % des blocs** au football comme au
  tennis, marches fusionnes. Le palier suivant est a trois — 38 % au football,
  100 % au tennis — et un critere qui se declencherait la ne classerait plus
  rien. Il ne se decline pas par sport, la norme etant de 12 marches d'un cote et
  de 3 de l'autre : « un seul » y designe la meme part infime.
- **`sheet()` ecarte tout dossier dont le score n'est pas positif**, donc un
  malus pose dans le score est un **veto** : -1 sur un dossier a un seul critere
  le faisait disparaitre. D'ou `merit` a cote de `score` — l'un decide si le
  dossier **se propose**, l'autre son **rang**. Une retrogradation ne dit pas
  « ne cherche pas », elle dit « ce que tu trouveras vaudra moins ».
- **Le poids vaut un cran, et c'est conservateur a dessein.** Deux crans feraient
  basculer l'ordre du lot qui a fait naitre le critere — regler un poids sur son
  propre exemple est la faute deja payee deux fois. Les autres poids du module
  sont « le rendement mesure de chaque piste » ; celui-ci n'en a pas encore.
- **Les motifs negatifs se rendent.** Une retrogradation que le lecteur ne voit
  pas est un garde-fou muet.

## Un exemple de format se batit sur le lot, jamais sur un litteral

Le gabarit ecrivait `dossiers_ouverts: [M1, M4, M7, M8]` sur un lot de **sept**
matchs, `sets: M3=… | M4=… | M8=PASSE` sur un lot dont M3 et M4 sont du
**football** et qui ne porte qu'un match de tennis, et `combine_court=0.25` juste
apres que la section D a ecrit « Aucun combine sur ce lot ». Aucun n'induit
vraiment en erreur, et les trois sement un doute au moment precis ou le format
doit etre sans ambiguite.

- **Deux besoins, deux regles.** `dossiers_ouverts` decrit un **sous-ensemble
  choisi** : l'echantillon est disperse du premier au dernier, jamais un prefixe
  — `M1, M2, M3` se lirait « ouvre-les tous » — et jamais tout le lot. `sets`
  reprend **chaque** match de tennis : l'exemple les prend tous, plafonne a
  quatre pour rester lisible.
- **Le critere de test est une propriete** : tout repere cite existe parmi les
  blocs rendus. Jamais la liste du jour, qui depend de la taille du lot.

## Un plafond sans son etat ne contraint rien

La ligne qui annonce ce qu'il reste de la journee n'etait rendue que si une mise
etait **deja enregistree** — donc jamais, `mises` portant zero ligne. Le
docstring de `stakes.Brief` promettait pourtant depuis le lot 17 que « chaque
prompt annonce ce qu'il **reste**, pas le plafond nu », et c'est lui qui avait
raison.

La branche disparait au profit d'une ligne unique qui porte toujours les trois
nombres, pour **27 tokens de moins** : la condition coutait plus que la ligne
qu'elle gardait. Meme regle que les quotas et les bornes de palier — une regle
qu'il faut appliquer de tete ne contraint rien.

**Et le corollaire de test** : le defaut vivait dans la porte, pas dans le
service. Un `Brief` correct n'aurait rien montre ; le test lit le **prompt
rendu**.

## Ne pas affirmer une impossibilite qui n'en est pas une

La section D ecrivait « trois jambes independantes ne peuvent pas en sortir » sur
un lot de sept matchs. **Structurellement faux** : `safe_legs_available` en
autorise sept, le plafond ne mordant qu'a partir de onze. **Empiriquement
serre** : 45 % des prompts de neuf blocs ou moins ont produit trois jambes en
bande sure a confiance >= 3 — et ce chiffre est un **plancher**, mesure dans le
regime ou l'absence de `dossiers_ouverts` forcait tout au cran 1.

Le seuil `combo_solo_min_lot` n'a pas bouge : il vaut **9** pour un defaut de 5,
c'est un reglage resserre a la main, et la mesure ne le tranche pas. C'est la
phrase qui etait fausse. Le combine est desormais « **pas demande** sur ce lot »,
avec le seuil ecrit plutot que sous-entendu.

## Une mesure qui contredit une premisse ne dispense pas d'expliquer la premisse

**Regle de revue, du 20/08/2026.** Le brief demandait de changer la grandeur de
la ligne `Ecart` au motif qu'elle comparait « la moins discriminante » ; la
mesure dit que c'est la **plus dispersee** des cinq. La conclusion etait juste et
la premisse fausse — et s'en tenir a « il a raison » aurait conduit a poser un
seuil sur l'etalement, c'est-a-dire a **garder la grandeur qu'on voulait
retirer**.

La premisse fausse et la conclusion juste coexistent, et c'est la premisse qui
decide de l'implementation. Le corollaire de la premiere lecon du projet — *une
premisse enoncee par le decideur se mesure comme une autre* — est donc qu'on la
mesure **meme quand on est d'accord avec ce qu'elle demande**.

## Le seul texte libre qui entre dans un prompt

`preferences.session_notes` est recopie en tete de chaque prompt et prime sur les
preferences du gabarit. C'est la **seule** surface de l'application qui puisse
faire entrer dans un prompt une regle que le gabarit retient volontairement, sans
qu'une ligne du gabarit soit touchee.

- **La porte derobee du dispositif de mesure.** Le gabarit retient les taux par
  palier et par confiance, et il dit pourquoi : « les selections que tu produis
  ensuite cessent d'etre independantes de ce qui les mesure, et une categorie
  annoncee faible cesse d'etre produite — donc cesse d'etre mesurable ». Une
  consigne tiree de `/stats` defait ce dispositif par le lecteur, pas par le
  texte — **meme forme que la regle qui interdit a l'historique des cotes toute
  surface avant que le lot soit fige**.
- **Un avertissement, jamais un refus**, et l'aveu en fait partie : l'application
  ne peut pas lire l'intention derriere une phrase, un controle produirait des
  faux positifs, et un garde-fou qu'on croit automatique et qui ne l'est pas est
  pire que pas de garde-fou. Il porte trois moities — l'interdit, l'irreversibilite,
  et surtout le **test** a appliquer a une consigne : *aucune ne depend d'un
  resultat*. C'est le seul des trois qu'on puisse suivre en ecrivant une phrase.
- **Ce qui a sa place la contraint le placement et la forme** : ou l'on pose, ce
  qu'une colonne doit nommer, ce qu'on ne joue jamais par principe. Les six
  consignes servies au 21/08/2026 passent toutes le test.
- **Et il ne demande aucune section.** `sections.survey()` lit `prompts.body` pour
  savoir ce que le prompt reclamait : une consigne dont une ligne commence par
  `sets:` y declarait la section demandee **sur un lot de football**, ou le
  gabarit ne la demande jamais — un faux manque, sur la surface dont le seul role
  est de separer une absence de collecte d'une absence de demande.
  `sections.gabarit_only()` retire le bloc avant de lire les demandes, sur son
  **titre** et jamais sur son texte : les consignes sont libres, et rien de ce
  qu'elles contiennent ne peut deplacer la borne.

## Un palier present ne peut pas etre interdit par son quota

`reachable()` declare present un palier qu'une cote du lot atteint ; `quota_for()`
reduisait sa borne a proportion du lot, jusqu'a zero. Les deux se contredisaient
sur **6 prompts archives**, dont le dernier rendu — `Paliers presents … GIGA FUN`
puis `0-0 🔴` sur une cote a 3.80, et le paragraphe des paliers vides ordonnant de
commenter un vide venu d'un arrondi.

- **Le plancher est celui de `QUOTA_FLOOR_TIERS`, applique un cran plus haut et
  plus strictement** : les deux paliers surs l'ont sans condition, un palier haut
  ne l'a que si un prix y tombe vraiment. Le total reste borne par le lot, et
  l'exigence de fait date ne bouge pas — le plancher rend le palier *proposable*,
  jamais *justifiable*.
- **Retirer le palier de `present` aurait contredit le prompt de l'interieur** :
  il faudrait le retirer aussi d'`absent`, dont la ligne affirme « aucune cote du
  lot n'y tombe », et la ligne `Paliers` de chaque bloc — calculee par
  `reachable()` sur les cotes du bloc — continuerait de le nommer.
- **Le budget garde son veto, et son zero est un zero explique** : le paragraphe
  qui suit les quotas dit qu'un palier haut reclame un dossier ouvert. C-bis le
  nomme alors, ce qui rend l'asymetrie **voulue** au lieu de subie — c'est
  exactement ce que la section existe pour porter.
- **Un palier absent du lot ne consomme aucun dossier.** Il ne peut recevoir
  aucune selection et affamait pourtant ceux que le lot offre : le zero se lisait
  « plus de dossier disponible » quand la cause etait « un palier hors du lot a
  pris la place ». Trouve en ecrivant le test du plancher, qui refusait de passer
  pour une raison qui n'etait pas la sienne.
- Mesure a connaitre : `research_capped` **ne mord sur aucun prompt archive**, ni
  a 10 ni a 7, les trois paliers hauts n'offrant que 6 places. La note du seuil
  affirmait le contraire — un docstring faux coute plus qu'un docstring absent, il
  fait re-deriver la meme conclusion fausse au lecteur suivant.

## La bascule du retour d'experience ne se produira pas toute seule

`FEEDBACK_SUSPENDED` est une **constante**, et `Feedback.enough` s'ecrit
`not self.suspended and …`. Franchir les deux seuils de recul ne transmet donc
rien : la rouvrir demande de modifier le code, ce que son propre commentaire dit
depuis le debut. Le dixieme jour d'analyse ne changera rien.

- **Le compte a rebours doit dire laquelle des deux conditions bloque.** Annoncer
  « il manque N journees » sans nommer la suspension serait un compte a rebours
  vers un evenement qui ne peut pas se produire. Quatre etats, une seule phrase
  (`Feedback.missing_line`), rendue a cote de ses deux seuils, sur `/stats` et
  dans l'export.
- **Le defaut latent ne pouvait paraitre qu'au jour qu'on attend** : recul atteint
  sous suspension, la liste de ce qui manque est vide et la phrase rendait
  « Il manque . Les taux ne sont pas transmis au prompt. » Sixieme forme du defaut
  caracteristique du projet, et la premiere sur une **syntaxe**.
- **La bascule se date toute seule** (`changelog.note_feedback`, appele par
  `save_prompt` quand `feedback_active`). Ni la date de livraison ni celle du
  franchissement de seuil ne decrivent le moment ou le regime change : seul le
  **premier prompt qui part** avec des taux le fait. Une fois et une seule, la
  garde se lisant sur le journal lui-meme.
- **L'instrument est pose avant, pas apres** (`history.scale_shift`) : la
  distribution des crans et des paliers session par session, avec une colonne
  « lit ses taux ». `labelling()` rend la part globale et la vacance — **une
  echelle qui glisserait d'un cran d'un mois sur l'autre y rendrait exactement la
  meme part**. Une serie ne se reconstitue pas apres coup : les crans sont en
  base, mais le fait de les avoir regardes dans l'ordre ne s'invente pas.
  - Elle montre une coupe qui existe deja : les sessions 3, 4 et 5 ont lu leurs
    propres taux. Ce qui s'y voit ne se conclut pas — trois points marques sur
    seize, et trois changements de cadre les traversent.
  - **Hypothese datee du 21/08/2026, a verifier apres la bascule** : si le cran 3
    sort sous sa bande, la consigne de resserrement poussera des selections vers
    le cran 2, qui n'a **aucune cible** — il est fixe par ce que la recherche a
    trouve. Le mouvement demande irait donc vers une categorie qu'aucune bande ne
    mesure. Plausible et non mesuree ; elle ne le sera pas avant la bascule, la
    consigne ne partant que dans la branche `feedback.enough`.
  - Etat au 21/08 : conf 1 a 33 %, conf 2 a **60 %**, conf 3 a **47 %**, conf 4 a
    59 %, conf 5 a 70 %. **Le cran 2 bat le cran 3** : la monotonie que les bandes
    supposent n'est pas etablie.

## Un parametre qui ne mord plus n'est pas un parametre inerte

Deux reglages ont ete soupconnes d'etre morts le 21/08/2026 ; **aucun ne l'est**,
et la difference tient a ce qu'on mesure.

- `combo_min_lot` = 20 : la branche des deux combines s'est declenchee **4 fois
  sur les 85 prompts** rendus depuis son reglage, la derniere le 15/08. Les lots
  n'ont jamais fait « 2 a 12 matchs » — ils vont de 0 a **37**.
- `recherche_dossiers` = 10 : il a borne **47 prompts sur 170**, et borne encore
  tout lot de 11 blocs ou plus (`safe_legs_available` passe de 11 jambes a 10). Ce
  qui l'a rendu inerte est la conjonction de son passage de 7 a 10 le 17/08 **et**
  de lots retombes a 10 blocs au plus depuis six jours. Au reglage precedent, il
  aurait borne 8 des 22 prompts recents.

**La regle** : un parametre inactif depuis quelques jours et un parametre
structurellement mort se ressemblent, et seule la mesure les separe. Le second se
retire ; le premier est un plafond dimensionne au-dessus du regime courant, et un
plafond qui ne mord pas fait son travail. Ce qu'il faut mesurer n'est pas « se
declenche-t-il aujourd'hui » mais « s'est-il declenche, et qu'est-ce qui a change
depuis ».

## Mesurer l'existant avant de construire, troisieme fois

Le lot 19 a livre ses points ; **trois des defauts qu'il corrige n'etaient dans
aucun d'eux**, et les trois sont sortis d'une mesure faite avant d'ecrire la ligne
demandee :

| Trouve en mesurant | Ce qu'on mesurait | Ce qu'on cherchait |
| --- | --- | --- |
| un palier absent du lot mangeait un dossier | le plancher de quota, en ecrivant son test | de quoi isoler prorata et budget |
| `Il manque .` une fois le recul atteint | les quatre etats du compte a rebours | ou afficher le compte restant |
| une consigne `sets:` demandait une section | le rendu complet sous consignes | un test de bout en bout |

Et **deux premisses du brief ont ete dementies**, dont une qui change tout le
sens du chantier : la bascule des taux ne se produira pas sans intervention
humaine, et le champ des consignes permanentes n'etait pas vide.

## Une affirmation sur l'etat de l'application se verifie avant d'etre posee

**Regle de revue, du 21/08/2026, a lire a cote de celle du zero d'appariement.**
Quatre affirmations sur cinq d'un brief ont ete renversees, et **deux decrivaient
l'etat actuel de l'application** :

| Pose en premisse | Verifiable en | Ce qu'il fallait lire |
| --- | --- | --- |
| « le champ des consignes est vide » | une requete | `preferences.session_notes` — 1 103 caracteres |
| « la bascule se produira sans intervention humaine » | une lecture | `FEEDBACK_SUSPENDED = True`, et `enough` s'ecrit `not self.suspended and …` |

Les deux prenaient trente secondes, et les deux venaient de quelqu'un qui avait lu
le code la veille — c'est ce qui les rend dangereuses. Une affirmation d'etat
vieillit sans prevenir, et celui qui l'enonce n'a aucune raison de la reverifier
puisqu'il se souvient de l'avoir sue. Contrairement a une hypothese sur les
donnees, **elle ne se signale pas comme incertaine**.

**Le corollaire, et c'est lui le plus utile** : une premisse d'etat fausse ne rend
pas la demande caduque, elle en **change la raison**. « Date la bascule parce
qu'elle sera automatique » est devenu « date-la parce qu'elle dependra d'un geste
dont la date d'activation n'est pas la date de livraison » — la meme livraison,
une justification plus forte. La verification passe donc **avant** le code, jamais
a sa place.

## Le deficit du projet tient dans un seul cran

Releve du 21/08/2026, population principale tranchee, 237 selections sur 15
journees. **Quatre crans sur cinq sont a parite avec leurs prix**, a moins d'une
victoire pres chacun ; le cinquieme porte tout.

| Cran | Tranchees | Taux | Ecart au prix |
| --- | ---: | ---: | ---: |
| confiance 5 | 7/10 | 70 % | +1,16 |
| confiance 4 | 52/87 | 60 % | −0,63 |
| **confiance 3** | **45/107** | **42 %** | **−14,56** |
| confiance 2 | 17/30 | 57 % | −0,57 |
| *global* | 122/237 | 51 % | **−15,39** |

C'est ce qui rend le retournement de `FEEDBACK_SUSPENDED` delicat, et ce n'est pas
une opinion : le jour ou les bandes entreront dans le prompt, `conf 3` sortira
sous la sienne et la consigne dira de resserrer — donc de pousser vers `conf 2`,
**un cran sans cible**, deja a parite avec ses prix. Le mouvement demande irait
vers la categorie qu'aucune bande ne mesure.

**La condition d'observation, a tenir des le retournement** : regarder les deux ou
trois sessions qui suivent et elles seules — au-dela, d'autres changements de cadre
s'y melent, le journal en compte treize en quinze jours. Le signe a chercher est
un **transfert de 3 vers 2**, pas une baisse du volume de 3 ; vers 4 serait le
comportement voulu. La serie des crans par session (`history.scale_shift`) est ce
qui les rendra lisibles, et la date de coupe s'ecrit toute seule.

## « Facteur » ne veut pas dire la meme chose dans la table des crans et dans le code

**Six desaccords sur six entre le cran annonce et le cran calcule sont le meme
passage** — `transitions = [(4, 5, 6)]` sur 21 paires comparables. Le lint sur la
redaction du gabarit ne peut pas produire de signal plus net.

- **Le sens est l'inverse de celui qu'on craint** : `drift = −0,29`. Le modele se
  note **en dessous** de ce que la table autorise. Ce n'est pas de l'inflation.
- **La cause evidente est exclue par les donnees.** « Aucun manque ne touche ce
  facteur » lu comme « aucun manque » produirait des **3** — le bloc declarerait
  `manque_touche_facteur: true`. Les six declarent `false` : le paragraphe qui
  ferme cette lecture est suivi, et il doit rester mot pour mot.
- **La cause reelle est le mot « facteur ».** `Claim.rung()` departage 4 et 5 sur
  `distinct_publishers >= 2`, donc sur des **editeurs** ; la table dit « 1 facteur
  dominant … le reste neutre », donc un **role**. Les six blocs portent 2 a 4
  editeurs distincts et le modele a lu « un argument dominant plus du detail ». Le
  cas pur est deux resultats du meme joueur chez deux editeurs : editorialement
  deux facteurs, sportivement un seul.
- La regle de comptage **existe** mais sous la table, et son titre l'annonce comme
  un contraste avec la section C. Rien dans les deux lignes qui se disputent ne
  dit **ou compter**. Correction proposee et datee dans `DIAGNOSTIC.md`, non
  appliquee : elle part avec le retournement du drapeau, pas seule.

## Les blocs de confiance sont produits, ils ne sont pas colles

**60 rejets `conf` / `fence_not_found` sur 4 sessions**, et `imports_raw` tranche
la question sans ambiguite : la coupure est de **taille**.

- au-dessus de 13 000 caracteres, un collage porte ses blocs — 6 collages, 30
  blocs, `claim_raw_json` ecrit sur 22 des 29 selections de la session 17 ;
- en dessous de 2 200, jamais — 30 collages du **seul tableau de la section C**.

**L'extraction fonctionne**, il n'y a pas de defaut de lecture. Piege ecarte au
passage : la chaine ` ```conf ` vaut **zero dans les 36 collages**, y compris les
six complets — les clotures sont consommees par le rendu, et les blocs se
reconnaissent sur leur forme (`"faits"`). Une sonde qui chercherait la cloture
conclurait a un defaut de production.

**Deux gestes, et un seul est une perte** :

| Sessions | Rendus complets colles | Rejets | Selections avec bloc |
| --- | ---: | ---: | --- |
| 15 et 16 | **0** | 26 | **0 sur 34** |
| 17 et 18 | 6 | 34 | 28 sur 60 |

Les 34 rejets des sessions 17 et 18 sont leves sur des collages **partiels
posterieurs** a un collage complet : ils ne decrivent aucune perte, les blocs
etant deja arrives. **Le compte de 60 sur-etat donc le probleme d'un facteur
deux**, dans le sens qui inquiete.

C'est aussi ce qui explique que le lint ci-dessus ne dispose que de 21
observations : sur 237 selections tranchees, **114 portent un cran calcule a 1** —
l'etat force quand `dossiers_ouverts` manque — et **20 seulement un cran reel**.
La cause n'est ni le modele ni l'extraction.

## Le gabarit se relit sur le disque, le code non

**Mode de panne nomme le 21/08/2026, et il appartient a la famille du projet.**
`prompt._environment()` construit un `FileSystemLoader` a chaque appel : le
gabarit est relu sur le disque a chaque generation. Le code Python, lui, est
charge au demarrage. Un `git pull` sans redemarrage laisse donc **gabarit du lot,
code d'avant**.

Ce que ca produit : une variable ajoutee au gabarit rend `Undefined`, donc une
chaine vide et un `{% if %}` faux ; un bloc entier peut ne pas se rendre. **Rien
ne leve.** L'application n'emploie pas `StrictUndefined` et ne le peut pas —
plusieurs variables sont legitimement absentes selon le lot.

Constate en reel : une heure durant, le gabarit portait le bloc C-bis du lot 19
quand le code en memoire ne passait pas `tiers_sans_dossier`. Aucun prompt n'a
ete genere dans cette fenetre — c'est ce qui a rendu l'episode inoffensif, pas
une propriete du dispositif.

**Proposition ecrite dans `DIAGNOSTIC.md`, non construite.** Deux moities qui
n'attrapent pas la meme chose : l'empreinte des gabarits figee au demarrage et
exposee par `/health` attrape le deploiement sans redemarrage ; un test comparant
les variables referencees par le gabarit aux cles passees a `.render()` attrape
le renommage.

**Le piege qui decide de la forme de la premiere** : l'ecran des reglages permet
d'editer un gabarit (`save_template`, `delete_template`), qui ecrivent sur le
disque. Une comparaison naive crierait sur le **chemin d'edition supporte**,
c'est-a-dire le cas ou le code en memoire est le bon. L'empreinte de reference
doit donc etre mise a jour par ces deux fonctions et par elles seules : un ecart
veut alors dire « le disque a change **sans passer par moi** », le seul cas a
signaler.

Ce qui n'est **pas** propose : refuser de demarrer sur un ecart — un board du
matin vaut mieux qu'un refus, meme arbitrage qu'un seuil illisible qui revient au
defaut ; recharger le code a chaud ; mettre le gabarit en cache — c'est la
relecture disque qui rend l'edition immediate, et c'est voulu. Le probleme n'est
pas qu'on relise le disque, c'est qu'on ne dise pas quand il a bouge.

## La fiche d'entraineur muette : deux causes opposees, et une seule se dit

Mesure du 21/08/2026 sur les fragments d'equipe reellement rendus : **59 % des
lignes `Entraineur` ne portent aucune mention** (45 % depuis le 17/08). Le cas
muet est donc le cas **majoritaire**, et la valeur y vient de la **fiche seule** —
`_coach_fragment` ne rend une mention que si une feuille a ete lue.

- **Les mentions ne sont pas trois mais cinq**, et deux n'etaient definies nulle
  part dans le chapitre : « non confirme » (6 % des fragments) et « (feuille du
  JJ/MM) ». C'est le defaut que ce prompt evite partout ailleurs, et il etait la
  avant qu'on le cherche.
- **Le cas muet a deux causes opposees.** Sur 257 evenements de football partis
  en prompt : 62 % portent une mention, **34 % sont muets parce qu'`/injuries`
  couvre** — les feuilles ne servent alors a rien d'autre — et **4 % parce que
  `lineups` n'est pas servi**, ou elles sont impossibles. Le docstring ne nommait
  que le premier, en le situant « sur les grands championnats » ; le second est
  l'exact contraire — DFB-Pokal, La Liga 2, Supercoupe d'Europe, ni absents ni
  compositions.
- **Seule la seconde se dit** (`— fiche seule, aucune feuille servie ici`), meme
  discipline que les trois etats d'`Absents` : une chose qu'on n'a pas verifiee et
  une chose qu'on ne **peut pas** verifier ne s'ecrivent pas pareil. Sur les 34 %
  la mention paraitrait sur un bloc sur trois et cesserait d'etre un signal.
- **Une couverture inconnue ne rend rien** : on n'affirme pas une absence qu'on
  n'a pas lue.
- **La date de fiche n'existe pas, et c'est verifie** : `/coachs` sert `age`,
  `birth`, `career`, `firstname`, `height`, `id`, `lastname`, `name`,
  `nationality`, `photo`, `team`, `weight` — et `career` n'a que `start`, `end`,
  `team`. Ni `update`, ni `updated`, ni `last_*`, ni `modified`. La seule date
  disponible est **notre** date de lecture, et l'ecrire donnerait l'assurance
  d'une fraicheur qui ne decrit que nous. Meme porte que celle deja fermee par le
  cas Utrecht.
- **Deux autres signaux mesures et ecartes comme decor** : « fiche seule » sur
  tout cas muet (45-59 %), et « choix heuristique entre etapes ouvertes » —
  **83 % des 421 fiches en base**, 68 seulement portant une etape unique. Le
  chiffre de 92 clubs sur 110 du dossier tient toujours, en proportion.
- Faux positif connu et **laisse** : `Jalel Kadri` contre `Jalal Qaderi` sont deux
  translitterations du meme nom, et `_coach_match` ne peut pas le voir. Le sens de
  l'erreur est le bon — une divergence annoncee a tort envoie verifier, une
  divergence tue laisse croire.

## La section C-bis se decrit par sa regle, jamais par son contenu

**« Produites sans fait date, par construction » etait faux la ou c'etait
verifiable.** Mesure du 21/08/2026 sur les 32 selections exploratoires : **26
declarent un `source_level` numerique**, 6 seulement `lecture` ; sept portent un
bloc `conf` apparie et **cinq de ces sept portent des faits dates**.

- Nuance a tenir : `source_level_effective` est a `lecture` sur 25 des 32, mais
  c'est l'**ecrasement** qui l'y met faute de ligne `dossiers_ouverts`. Sur les 7
  lignes ou l'ecrasement n'a pas tire — les seules ou l'on voit ce que l'analyse a
  declare — le fait date est majoritaire.
- **Refuser un fait date en C-bis a ete ecarte** : le gabarit ne l'interdit pas et
  il a raison (« `Source` y vaudra **le plus souvent** `lecture` ») ; un PASSE en
  section B sur un match portant un fait date ne produirait alors plus rien du
  tout ; et il faudrait un refus a l'import, donc jeter une ligne legitime pour
  proteger une phrase.
- **Le motif de l'absence de mise change de nature.** « Lecture seule » etait un
  motif de **qualite** et ne survit pas a la mesure ; le motif juste est de
  **role** — ces selections sont la **population temoin**, elles existent pour
  etre comparees a la section C a palier egal, et un montant en ferait un pari
  plutot qu'un point de mesure. Il est vrai quelle que soit la ligne.
- La description est donc « les selections que la section C n'a pas retenues,
  produites **sans exigence** de fait date ». Corrigee **aux six endroits a la
  fois** — gabarit, page, export, docstring, commentaire, infobulle : une
  description corrigee a un seul endroit est pire que pas de correction.

## Un quatrieme etat de « Non servis » : l'etage B a tourne et n'a rien rapporte

`session._is_enriched` deduit le passage de l'etage B de la **presence d'un
marche profond**. Sur une reponse **vide**, il n'y en a aucun : la garde rend
faux, la cause « demande pour ce match et non revenu » ne s'applique pas, et les
marches ne tombent nulle part.

- **Le repli annonce ne joue pas** : « ce constat est deja memorise par
  `coverage` » est faux, `coverage.record` ecrivant `MAX(served, ...)` — un marche
  vu servi une fois sur la competition l'est pour toujours. Sur M4 le memo dit
  `alternate_spreads` et `alternate_totals` **servis**, 31 verifications, quand le
  match n'en porte aucun.
- **La rencontre avait bien ete interrogee** : appel a 23:36:40, **cout 0**, donc
  reponse vide — les reponses vides ne sont pas facturees. Ni match saute, ni
  plancher de credit, ni panne.
- **Portee** : 29 appels vides sur 1 107 (3 %), 23 evenements, dont **3 partis en
  prompt** — et les trois sont dans le meme lot, soit la moitie de ses blocs de
  football. Rare dans l'absolu, entierement concentre.
- **Ce qui manque est un fait persiste, pas une inference.** Retirer la garde
  ferait lister tous les marches d'un evenement jamais enrichi ; le memo
  `coverage` est une union par competition ; le contexte s'ecrit aussi par le
  balayage des compositions ; `api_usage` est un registre de quota. Il faut une
  **marque d'appel par evenement**, ecrite par `enrich` au moment ou il appelle,
  independante de ce qui revient. La deuxieme cause couvre alors le cas sans
  nouveau libelle — son texte est deja juste — et le cout en tokens est nul.
- `Se qualifie` absent d'un bloc de championnat n'a **aucun rapport** :
  `markets_for` ne le demande que sur `KNOCKOUT_CATEGORIES`, donc il n'a pas a
  figurer dans une liste de marches demandes et non revenus. Les blocs de coupe du
  meme lot le portent bien.

## Une distinction qui sert deux consommateurs qui ne se connaissent pas est au bon niveau

**Le pendant positif du motif ci-dessous, et le seul test fiable qu'une
abstraction soit posee au bon endroit — il ne se voit qu'apres coup.**

`HERE_NO_MATCH` contre `HERE_NO_INFO` a ete ecrite le 28/08/2026 pour un
**libelle** : separer « le joueur entre en lice », qui est un fait sur lui, de
« le releve precede le tournoi », qui ne decrit que notre fenetre de collecte.

Elle a tranche, le meme jour et sans y avoir ete pensee, un **critere de la fiche
de recherche** : les deux blocs qu'une premiere mesure comptait comme « question
manquante » etaient des entrees en lice, donc rien a chercher. Sans la
distinction, il aurait fallu l'inventer une seconde fois, et les deux ecritures
auraient diverge — le motif documente juste en dessous.

- **Le test ne s'anticipe pas, il se constate.** Une abstraction posee « parce
  qu'elle servira » est une supposition ; une abstraction qu'un second
  consommateur reprend **sans que le premier l'ait prevu** est une mesure.
- **Le corollaire pratique** : quand un chantier reclame une distinction qui
  existe deja ailleurs, verifier d'abord si c'est la meme. Si oui, elle se
  reutilise ; si non, la nommer autrement — deux distinctions voisines sous un
  meme nom sont pires que deux noms.

## Une seconde copie qu'aucun mecanisme n'oblige a concorder derive sans bruit

**Motif du 21/08/2026, trouve trois fois dans la meme session, dans trois couches
qui n'ont rien a voir.** C'est la forme que prend ici le defaut caracteristique
du projet — une sortie identique pour l'echec et pour le cas ordinaire — quand il
porte sur une **decision** plutot que sur une donnee.

| Les deux copies | Ce qui les separait | Comment ca s'est vu |
| --- | --- | --- |
| le cadre publie et la table `tiers` | plafond SAFE, chevauchement, arbitrage | par une relecture, apres des semaines |
| le seed de la migration 003 et la base servie | 1.70/2.60/5.00/15.0 contre 1.70/2.30/3.60/8.00 | en ecrivant la migration 071 |
| le selfcheck et le refus qu'il exploitait | la garde de palier C-bis, retiree | par un test rouge, et c'est le seul des trois |
| `tier_for_price`, `Tier.covers`, `tier_offers` | rien — les trois s'accordaient | en cherchant, pas en cassant |
| `FEEDBACK_MIN_ROWS` et le seuil reglé | 8 contre 25 apres relevement | un test de parite, sur un lot vide |

**Le symptome commun : les deux copies s'accordent longtemps.** C'est ce qui rend
le motif couteux — un desaccord immediat se verrait, une convergence entretenue a
la main tient jusqu'au jour ou elle cede, et ce jour-la personne ne cherche de ce
cote. Les trois premieres lignes ci-dessus ont diverge en silence ; la quatrieme
n'avait **pas encore** diverge, et c'est exactement pourquoi elle a ete fusionnee.

**La question a se poser devant toute valeur ecrite deux fois** : *qu'est-ce qui
force ces deux-la a rester d'accord ?* Il y a trois reponses, et une seule est
bonne.

1. **Une seule ecriture, les autres l'appellent.** C'est le traitement de
   `history.in_band`, de `markets.py` et de `session.context_block`. Toujours
   preferable quand la valeur n'a qu'un sens.
2. **Un test qui compare les deux ecritures.** Quand un cycle d'import ou une
   frontiere de couche interdit la premiere : `SAFE_BANDS` contre
   `QUOTA_FLOOR_TIERS`, la table des familles de marches contre sa migration,
   `Tier.covers` contre `tier_for_price` avant leur fusion. Le test **doit lire
   les deux sources**, jamais recopier la regle.
3. **Rien.** Alors la copie derivera, et la seule question est quand. Le cadre
   publie et la configuration servie sont dans ce cas par construction — l'un
   vit chez le fournisseur de Skill, l'autre en base — et c'est pourquoi le
   journal d'analyse recalcule desormais le palier au lieu de lire l'emoji colle.
   **Quand on ne peut pas forcer l'accord, on cesse de dependre de la copie.**

**Corollaire pour un exemplaire de test** : `selfcheck` choisissait, parmi
plusieurs refus possibles, celui qu'il montrait. Un exemplaire choisi se perime
avec ce qu'il exploite, et le repointer sur un autre refus reproduit la meme
fragilite decalee d'un cran. La forme robuste est de **ne plus choisir** : que le
controle enumere les chemins de refus et exige de chacun sa ligne de journal.

### Onzieme occurrence, en version migration : une reprise indexee sur un etat mutable

**La migration 049 annoncait « 16 typees » et le chiffre etait juste.** Rien
n'obligeait pourtant ce 16 a correspondre a la population reelle : sa clause
s'indexait sur `sessions.open_dossiers_state`, un etat **ecrit par le dernier
import de la session**. Les lignes ecrites avant que cet etat soit pose lui ont
echappe, et le releve ne pouvait pas le dire — il comptait ce qu'il avait touche,
pas ce qu'il aurait du toucher.

Mesure du 21/08/2026 : **43 selections** portaient `research_overridden = 1` sans
aucune cause, sessions 11 et 13. Comme `is_collection_fault(None)` vaut faux,
elles comptaient depuis comme des **observations sur le modele** — « elle s'est
notee comme si elle avait cherche » — sur une population de 127. Un tiers du
compte affirmait ce qu'il ignorait.

- **La regle qui en sort** : une reprise s'indexe sur **la colonne qu'elle
  corrige**, jamais sur un etat qui la decrit. `research_override_cause IS NULL`
  est rendu faux par la reprise elle-meme, donc la clause est complete et
  idempotente **par construction** ; `open_dossiers_state = 'absente'` est une
  observation exterieure qui peut arriver apres.
- **Le compte d'une reprise ne se verifie pas contre lui-meme.** « 16 typees »
  se lit comme une couverture ; c'est un volume. La verification est le compte
  **restant** — combien de lignes remplissent encore la condition d'origine — et
  il vaut zero ou il ne vaut rien.
- Corollaire, et c'est la troisieme branche de la regle des « a ne pas oublier » :
  ces 43 lignes ne se re-typent **pas**. `imports_raw` ne commence qu'a la
  session 15, le texte de ces deux sessions n'existe plus, et leurs voisines
  typees `ligne_absente` ne sont qu'un « probablement ». Elles deviennent un
  **troisieme etat** (`cause_inconnue`, migration 073) : ni observation, ni
  defaut de collage identifie. Le total cesse de surestimer sans se mettre a
  sous-estimer.

### Quatorzieme occurrence : une consigne qui porte sur un champ jamais servi

**Sortie de la verification de la treizieme, et elle vit dans le cadre et non
dans le code.** La ligne 8 du SKILL instruit le modele sur `framework_version` —
« le figer au premier prompt d'une session, ne jamais le remplir
retroactivement ». Mesure du 22/08/2026 : le champ n'est emis que par
`payload.build_payload`, la route payload **n'a jamais servi en production**, et
`ACTIVE_PRODUCER` vaut le gabarit, qui ne l'ecrit pas. Sur les 180 prompts
archives, **zero** porte la chaine.

La consigne decrit donc un geste que le modele n'a jamais eu l'occasion de
faire, sur un champ qui n'a jamais rien etiquete. Deux ecritures d'une meme
notion — le cadre qui l'ordonne, le producteur qui ne le sert pas — et rien
n'obligeait les deux a concorder.

- **Le correctif est dans le cadre, pas dans le code**, et il part avec la
  publication du 1.4. L'ecrire ici serait la quinzieme occurrence : une note
  dans un depot qui ne peut pas modifier le fichier concerne.
- **Cote application, la premiere reponse etait de cesser de dependre de
  l'aller-retour** — `picks.framework_version` estampille localement
  (migration 075). **Elle a ete retiree le 27/08/2026** : le referent existait
  deja, sur la session, et une valeur locale estampillee sur la selection restait
  une troisieme copie du mauvais sujet. Voir « le referent existait deja ».

### Treizieme occurrence : un numero bumpe sur une declaration

**Le 21/08/2026, `FRAMEWORK_VERSION` est passe a `1.4` sur une annonce de
publication.** Le cadre servi disait encore `1.3` — six copies du cache de
plugin le disaient, et elles disaient vrai. Le prealable avait ete annonce au
futur, la sequence executee au present.

**Portee reelle : nulle, et il faut le dire ainsi.** Aucun prompt n'a ete
produit dans la fenetre — le dernier date de `16:35:35Z`, le redemarrage de
`21:24:36Z` — et **aucun des 180 prompts archives ne porte le champ**, le
producteur actif etant le gabarit et non le payload. Deux raisons independantes,
et aucune des deux n'est un garde-fou : la premiere est une coincidence
d'horaire, la seconde une etape de migration qui finira par tomber.

- **Ce que la faute coute n'est pas la sortie, c'est le champ.** Il n'a qu'une
  utilite — ne pas melanger deux regimes dans une population — et un numero pose
  en avance la lui retire entierement : deux sorties du meme cadre s'y liraient
  sous deux numeros, ce qui est exactement le desordre qu'il existe pour eviter.
- **Elle n'etait detectable ni par le code ni par les tests**, et c'est ce qui la
  distingue des douze precedentes. Le cadre vit chez le fournisseur de Skill, la
  constante en base : rien dans le depot ne pouvait les confronter, donc le
  troisieme cas de la regle s'appliquait — la copie derive, et la seule question
  est quand.

**Le garde qui en sort ferme le troisieme cas au lieu de s'y resigner**
(`services/framework.py`, `myassistantbet-cadre`). Le cadre publie **est**
lisible sur la machine qui l'emploie : ce qui manquait n'etait pas une source,
c'etait de la lire.

- **Le cadre lu fait foi, sans appel.** A defaut — depot frais, CI, machine sans
  le plugin — la **preuve enregistree** (`deploy/cadre-lu.json`) prend le relais.
  Elle est ecrite par `--relire`, qui lit le fichier reel : c'est ce qui la
  distingue d'une affirmation, et elle est versionnee, donc elle voyage dans le
  commit qui bouge le numero.
- **L'ordre n'est pas negociable.** Laisser la preuve primer rendrait le garde
  vert sur une machine qui a le vrai fichier sous les yeux et le contredit —
  litteralement la situation du 21/08.
- **Sans lecture ni preuve, rouge.** Un garde qui se tait quand il ne peut pas
  verifier est indiscernable d'un garde qui a verifie : le defaut caracteristique
  du projet, applique au dispositif de verification lui-meme.
- **Il ne releve pas le numero a la place de qui exploite**, et il est
  **symetrique** : un cadre publie en `1.4` sous une constante a `1.3` le rend
  rouge comme l'inverse. Dans les deux cas deux ecritures ont diverge, et c'est
  ca qu'on veut voir.
- **Le fichier de preuve se retouche a la main, et le module le dit.** Ce n'est
  pas la faille qu'on croit : ce qui a produit l'erreur n'est pas une
  falsification mais un raccourci de bonne foi. Le garde retire le raccourci ; il
  ne pretend pas resister a une intention contraire, et l'ecrire vaut mieux que
  de laisser croire l'inverse.

**La regle generale** : une valeur qui declare l'etat d'un systeme exterieur ne
se change que sur une lecture de ce systeme. Une declaration en conversation est
la meme chose qu'un commentaire « a ne pas oublier » — vraie au moment ou elle
est faite, et sans aucun mecanisme derriere.

### Douzieme occurrence : la suite de tests en portait une version

**Quatre tests sont tombes en corrigeant le typage des ecrasements, et tous les
quatre encodaient le defaut.** `_ecrasee`, le helper de `test_confidence.py`,
appelait `add_pick(opened=False)` **sans cause** — un etat que le parcours reel
ne produit jamais, `_apply_research` en calculant toujours une des cinq. Ils
passaient donc *parce que* `is_collection_fault(None)` valait faux.

**Un helper de test est une seconde description de la verite, et rien n'oblige
la seconde a suivre la premiere.** C'est la meme forme que les deux copies d'une
valeur, appliquee a une forme canonique plutot qu'a un nombre : le montage se
fige le jour ou il est ecrit, le service continue d'evoluer, et le montage finit
par decrire un etat que la production ne peut plus atteindre.

Le precedent est deja dans ce fichier, et il n'avait pas ete lu comme un motif :
*« la forme canonique d'une selection, dans tout le code de test du projet, etait
un pari pose sur un match deja commence »* — cent six tests casses quand la
garde d'anteriorite est arrivee. La convention de test refletait la pratique,
donc le defaut.

**La regle qui en sort, et elle porte sur la reaction plutot que sur le code** :
quand un correctif casse un test, la premiere question n'est pas comment le
faire repasser mais **lequel des deux decrit l'etat voulu**. Realigner
l'assertion sur la nouvelle sortie est le geste par defaut, et c'est celui qui
transforme un test en description de ce qui est.

- Le symptome qui designe le helper plutot que l'assertion : **plusieurs tests
  tombent ensemble, sur la meme ligne de montage**. Un test isole qui casse
  discute d'une regle ; quatre qui cassent au meme endroit disent que le montage
  a vieilli.
- Corollaire : un helper de test se relit contre le **chemin de production**, pas
  contre ce qui fait passer. `_ecrasee` porte desormais la cause que
  `_apply_research` pose, et un test neuf garde la nouvelle propriete — un
  ecrasement sans cause ne compte pas comme une observation.

### L'incident du 21/08/2026 : une migration partie sur la base servie

**Consigne ici parce que l'etat est le bon et que seule sa provenance est
fausse** — une provenance se repare par une note, pas par une seconde ecriture
non planifiee.

- **Ce qui s'est passe** : un script de controle a cru isoler sa base par
  `MYASSISTANTBET_DB`. Le champ s'appelle `db_path`, donc la variable est
  `DB_PATH` : l'override n'a rien fait, `get_settings()` a rendu les parametres
  servis, et `run_migrations()` a applique la migration 071 sur
  `data/myassistantbet.db` a **18:41:33Z**.
- **Perimetre exact** : les cinq lignes de `tiers`, plus la ligne 71 de
  `schema_migrations`. Les migrations 001 a 070 etaient deja appliquees. Aucune
  ligne de `picks`, d'`odds` ou de `context` n'a ete touchee — la derniere
  selection datait de 12:24Z.
- **Le deploiement a venir traite la 071 en no-op**, verifie : `run_migrations`
  saute toute version deja presente dans `schema_migrations`
  (`if version in done: continue`), et les cinq bornes en base sont deja celles
  que la migration ecrit. Rien ne sera rejoue, rien ne bougera.
- **Ce qui empeche la recidive**, en trois gardes qui n'attrapent pas la meme
  chose : `db.scratch_copy()` donne au chemin d'ecriture l'equivalent du
  `VACUUM INTO` de lecture ; `run_migrations(deliberate=)` refuse un appel non
  declare hors de la suite de tests ; et un validateur refuse toute variable
  `MYASSISTANTBET_*` sans champ correspondant — ce prefixe n'a aucun usage
  legitime, l'application ne declarant pas d'`env_prefix`, donc le refus est sans
  faux positif possible. `extra="forbid"` sur les parametres attrape une cle
  inconnue dans `.env` — **et pas** une variable d'environnement inconnue, mesure
  le meme jour : pydantic-settings ne lit l'environnement que pour les champs
  declares. C'est la troisieme garde qui porte ce cas.
- **Ce qui n'a pas ete fait, et pourquoi** : restaurer 1.70. La table injectee
  disait 1.70 pendant que le cadre disait 1.80, et le modele suit la table — 1,7 %
  d'ecart entre l'emoji colle et la cote sur 352 selections. Restaurer aurait fait
  produire une session du soir sous l'ancienne regle ; et supprimer la ligne de
  `schema_migrations` aurait fait mentir le registre en datant la 071 d'un
  deploiement qui n'a pas eu lieu.
