# MyAssistantBet

Application web mono-utilisateur, auto-hebergee, qui remplace deux heures de collecte
manuelle par dix minutes de tri.

Elle recupere les matchs du jour et leurs cotes, laisse selectionner ceux qui interessent,
va chercher les marches profonds et le contexte sportif de cette selection, puis produit
**un bloc de texte compact a coller dans Claude**. C'est tout : elle ne parie pas, ne predit
rien et ne calcule aucune value.

La specification complete et faisant autorite est dans [`SPEC.md`](./SPEC.md).

## Etat d'avancement

| Phase | Contenu | Statut |
|---|---|---|
| 0 | Fondations : projet, config, base, migrations, `/health`, CI | fait |
| 1 | The Odds API, etage A (scan large), ecran Board | fait |
| 2 | Etage B (marches profonds), rendu compact, generation du prompt | fait |
| 3 | Contexte sportif via API-Football, mapping des equipes | fait |
| 4 | Tennis, cyclisme, evenements manuels | fait |
| 5 | Historique des picks, personnalisation des templates | a venir |
| 6 | Deploiement VPS (systemd, nginx, sauvegardes) | a venir |

## Prerequis

- Python 3.11 ou superieur
- [`uv`](https://docs.astral.sh/uv/) — installation : `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Installation

```bash
git clone https://github.com/tclrcc/myAssistantBet.git
cd myAssistantBet

uv sync                 # cree .venv et installe les dependances verrouillees
cp .env.example .env    # puis renseigner les cles d'API
```

Aucune cle n'est necessaire pour demarrer l'application, mais sans `ODDS_API_KEY`
aucun scan ne ramenera de donnees.

## Lancement

```bash
uv run uvicorn myassistantbet.main:app --reload
```

L'application ecoute sur <http://127.0.0.1:8000>. Les migrations de base sont appliquees
automatiquement au demarrage.

Verification :

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

La reponse expose l'etat de la base (chemin, version de schema, tables, mode journal) et la
configuration chargee. **Aucune cle d'API n'y figure**, seulement un booleen de presence.

## Developpement

```bash
uv run ruff check .        # lint
uv run ruff format .       # formatage
uv run pytest              # tests (aucun acces reseau)
```

Les trois commandes sont rejouees par la CI GitHub Actions sur Python 3.11 et 3.13.

## Utilisation

Le Board (`/`) liste les evenements de la fenetre glissante courante (par defaut J+0 et
J+1, via `SCAN_WINDOW_DAYS`), avec leurs cotes 1N2 et leur ligne O/U principale.

- **Scan** — un scan automatique tourne chaque jour a `SCAN_HOUR:SCAN_MINUTE` (heure locale
  `TZ`). Le bouton « Relancer le scan » du bandeau declenche le meme traitement a la demande.
- **Cout** — le scan interroge chaque competition active sur `h2h` + `totals` chez le seul
  bookmaker `betclic_fr`, soit **2 credits par competition**. Avec les 7 competitions seedees,
  un scan coute 14 credits.
- **Bandeau** — credits restants (rouge sous `ODDS_API_CREDIT_FLOOR`), date du dernier scan
  payant, et nombre de matchs coches.
- **Selection** — cocher un match l'ajoute a la session du jour, visible sur la Shortlist.
- **Competitions** — les 7 competitions initiales sont posees par la migration `002`. Pour en
  ajouter, en retirer ou en desactiver une, editer la table `competitions` directement.

Une competition dont l'API ne repond pas n'interrompt jamais le scan : l'echec est affiche
dans le rapport et les autres competitions sont traitees normalement.

### Shortlist (`/session/{id}`)

Les matchs coches, regroupes par sport. Le bandeau du board mene a la session du jour.

- **Cout affiche avant le clic** : 14 credits par match de football (1 par marche profond),
  16 sur les competitions de la liste blanche des props buteurs, 8 par match de tennis.
- **Garde-fou** : si `restant - cout` passe sous `ODDS_API_CREDIT_FLOOR`, le bouton
  « Enrichir la selection » est desactive et la raison est affichee. Aucun appel n'est tente.
- **Progression** : l'enrichissement tourne en tache de fond, la barre est rafraichie par
  polling HTMX. Un match en echec n'interrompt pas les autres.
- **Note perso** : la zone de texte de chaque match est injectee telle quelle dans le bloc
  `CONTEXTE`, sous `NOTE PERSO`.

### Contexte sportif

L'enrichissement recupere aussi, via API-Football, le classement, la forme sur 5 matchs, la
repartition domicile/exterieur, les absents, les confrontations directes et les jours de
repos. Ces donnees alimentent le bloc `CONTEXTE` de chaque match.

Cout : environ **7 appels par match** au premier passage (matchs du jour, classement,
statistiques des deux equipes, derniers matchs des deux equipes, blessures, H2H). Classement
et statistiques sont partages entre les matchs d'une meme ligue au sein d'un enrichissement,
donc le second match d'une meme competition coute nettement moins.

Les charges utiles brutes sont persistees dans la table `context` : **regenerer un prompt ne
declenche aucun appel reseau**.

Ce qui manque est toujours dit. Une ligue non couverte pour les blessures produit
`Absents     donnees non disponibles pour cette competition`, jamais un silence.

### Compétitions (`/competitions`)

Seules les competitions **actives** sont scannees. Le bouton « Synchroniser depuis The Odds
API » aligne le catalogue local sur `GET /sports` — **endpoint gratuit** : la synchronisation
ne consomme aucun credit. Elle n'active jamais rien d'elle-meme et ne desactive jamais
l'existant.

C'est le moyen fiable d'obtenir les cles de competition tennis, qui changent d'une saison a
l'autre. Les 8 Grands Chelems seedes par la migration `005` sont **inactifs** : un tournoi ne
dure que deux semaines, on l'active au moment voulu.

### Evenements manuels (`/manual`)

Pour ce qu'aucune API ne couvre : le cyclisme entierement, et le tennis en dehors des Grands
Chelems et des Masters.

- Sport, competition, participants, date et heure locale.
- **Cotes saisies a la main**, une par ligne : `Pogacar 2.50`, `Pogacar = 2.50` ou
  `Pogacar; 2,50`. Une ligne illisible est signalee, jamais avalee en silence.
- **URLs de reference** (ProCyclingStats, etc.), une par ligne.
- **Profil d'etape** et **startlist / favoris**, qui alimentent le bloc CONTEXTE cyclisme.

L'evenement apparait ensuite sur le board comme les autres : il se coche, entre dans une
session et se rend avec le meme moteur. Ses cotes portent le bookmaker `manual` et son
enrichissement ne declenche aucun appel d'API.

### Mapping des equipes (`/mapping`)

The Odds API et API-Football n'utilisent ni les memes identifiants ni les memes noms. La
resolution se fait par alias memorise, puis par normalisation et distance de Levenshtein.

**En cas de doute, l'application ne devine pas** : l'evenement est marque `mapping_pending`,
le bandeau du board affiche le nombre de correspondances a resoudre, et la page `/mapping`
propose les candidats vus lors de la tentative — sans nouvel appel d'API. Un choix manuel est
memorise definitivement et prime sur toute deduction ulterieure.

### Prompt (`/session/{id}/prompt`)

Le prompt assemble, avec selecteur de template, estimation de tokens, bouton « Copier » et
telechargement `.md`. Chaque generation est archivee dans la table `prompts`.

Les templates sont des fichiers `.md.j2` dans `src/myassistantbet/templates/prompts/`, relus
a chaque requete : **editer un fichier suffit, sans redemarrage**. Les bandes de cotes
exposees au template viennent de la table `tiers`.

### Economiser le quota en developpement

```bash
DEV_CACHE=1 uv run uvicorn myassistantbet.main:app --reload
```

Les reponses sont alors mises en cache dans `DEV_CACHE_DIR` et rejouees depuis le disque
(cout 0). Les cles d'API ne sont jamais ecrites dans ce cache.

## Base de donnees

SQLite en fichier unique, en mode WAL, avec les cles etrangeres activees. Chemin par defaut :
`./data/myassistantbet.db` (dossier ignore par git), configurable via `DB_PATH`.

Les migrations sont des fichiers SQL numerotes dans `src/myassistantbet/migrations/`, nommes
`NNN_description.sql`, appliques dans l'ordre au demarrage et traces dans la table
`schema_migrations`. **Une migration deja appliquee ne doit jamais etre modifiee** : creer un
nouveau fichier.

## Structure

```
src/myassistantbet/
├── main.py         # app FastAPI, routes, cycle de vie — aucune logique metier
├── config.py       # parametres (pydantic-settings)
├── db.py           # connexion, migrations, helpers
├── scheduler.py    # scan quotidien (APScheduler)
├── migrations/     # fichiers .sql numerotes
├── providers/      # clients d'APIs externes — ne connaissent rien du metier
│   ├── base.py         # timeouts, retry, cache dev, comptabilite du quota
│   ├── oddsapi.py      # The Odds API v4
│   └── apifootball.py  # API-Football v3
├── services/       # logique metier — aucun appel HTTP direct
│   ├── scan.py     # etage A : scan large, upserts
│   ├── board.py    # lecture du board, selection
│   ├── enrich.py   # etage B : marches profonds, estimation et garde-fou
│   ├── render.py   # compression d'un evenement en bloc texte compact
│   ├── session.py  # shortlist, notes, assemblage des blocs
│   ├── context.py  # contexte sportif : recuperation, persistance, rendu
│   ├── matching.py # correspondance des equipes entre les deux APIs
│   ├── mapping_ui.py # resolution manuelle des correspondances
│   ├── manual.py   # evenements saisis a la main
│   ├── competitions.py # catalogue des competitions, synchronisation gratuite
│   └── prompt.py   # assemblage du prompt final
├── templates/      # Jinja2 (HTML et templates de prompt .j2)
└── static/         # CSS et htmx, servis en local (aucun CDN)
tests/
├── fixtures/       # reponses d'API en JSON (voir l'avertissement ci-dessous)
└── test_*.py
```

## Fixtures de test

`tests/fixtures/oddsapi_allsvenskan_scan.json` reproduit la forme d'une reponse d'etage A.
`tests/fixtures/oddsapi_event_odds_football.json` et les fixtures `apifootball_*.json`
reproduisent la forme documentee des reponses : elles sont
**construites d'apres le schema documente, pas capturees sur l'API reelle**. Les noms d'issues
de certains marches (notamment `double_chance` et `halftime_fulltime`) restent donc a
confirmer par un appel reel. Le rendu est concu pour cela : un marche dont le nommage n'est
pas reconnu est affiche brut plutot que mal interprete.

## Sports couverts

| Sport | Cotes | Contexte | Remarque |
|---|---|---|---|
| Football | The Odds API, 14 marches profonds | API-Football | 16 marches sur les ligues a props buteurs |
| Tennis | The Odds API, 8 marches profonds | aucun | Grands Chelems et Masters seulement ; le reste en saisie manuelle |
| Cyclisme | saisie manuelle | saisie manuelle | aucune API ne le couvre |

## Fuseau horaire

Tout est stocke en UTC (chaines ISO 8601). L'affichage se fait en `Europe/Paris`, valeur
configurable via `TZ`.
