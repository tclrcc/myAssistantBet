# CLAUDE.md — instructions de travail sur ce depot

`SPEC.md` est la source de verite du projet. Ce fichier en est le rappel operationnel :
commandes, conventions, et surtout la liste des interdits. En cas de contradiction,
`SPEC.md` gagne.

## Commandes

```bash
uv sync                                              # installer / mettre a jour l'env
uv run uvicorn myassistantbet.main:app --reload      # lancer l'app (port 8000)
uv run pytest                                        # tests
uv run pytest tests/test_db.py -k migrations         # un sous-ensemble
uv run ruff check . --fix                            # lint (avec corrections sures)
uv run ruff format .                                 # formatage
curl -s localhost:8000/health | python3 -m json.tool # etat de l'app
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
  n'existe pas.

Ajouter un marche : une entree dans `MARKET_ORDER`, un rendu dedie si sa forme le merite,
et un test. Sans rendu dedie, le repli generique s'applique — c'est acceptable, pas une
regression.

## Contexte sportif et mapping

- `providers/apifootball.py` : piege du fournisseur, **les erreurs applicatives arrivent en
  HTTP 200** dans le champ `errors` de l'enveloppe. Le client les convertit en `ProviderError`.
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

## Historique et personnalisation

- `services/history.py` : **aucun calcul financier**, jamais. Le seul indicateur est
  `gagnes / (gagnes + perdus)`. La mise est enregistree mais **jamais agregee** — un test
  verifie qu'aucun champ `roi`, `profit` ou `stake` n'apparait sur les agregats.
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
  palier, par confiance annoncee, par sport et par marche, sur les `FEEDBACK_WINDOW`
  derniers paris **tranches** — annules et en attente hors denominateur.
  - Sous `FEEDBACK_MIN_TOTAL`, **aucun detail n'est publie** : le prompt dit qu'il manque
    du recul. Un 2/3 lu « 67 % » ferait plus de degats que le silence.
  - Sous `FEEDBACK_MIN_ROWS`, un regroupement est tu pour la meme raison.
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
- `backup.py` utilise **`VACUUM INTO`**, jamais une copie de fichier : en mode WAL, copier
  le `.db` seul livrerait une base incomplete.
- La rotation ne supprime **jamais la sauvegarde la plus recente**, meme expiree.
- L'application ecoute uniquement sur `127.0.0.1`. Elle n'a aucune authentification par
  choix : c'est nginx qui la protege. Toute modification du deploiement doit conserver ces
  deux proprietes.

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

Les deux vivent sur `/stats`, jamais melangees, et aucune ne produit d'indicateur financier.
