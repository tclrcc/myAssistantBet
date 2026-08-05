# MyAssistantBet — Spécification de développement

> Ce fichier est la source de vérité du projet. Claude Code doit le lire intégralement
> avant d'écrire la moindre ligne de code, et s'y référer à chaque phase.

---

## 0. Mission

Construire **MyAssistantBet**, une application web mono-utilisateur, auto-hébergée sur un VPS OVH (Debian/Ubuntu), dont l'unique but est de **remplacer 2 heures de collecte manuelle par 10 minutes de tri**.

L'application ne parie pas, ne prédit pas, ne calcule aucune value. Elle fait une seule chose, bien :

1. Elle récupère automatiquement les matchs du jour (football, tennis, cyclisme) et leurs cotes.
2. Elle me laisse sélectionner ceux qui m'intéressent.
3. Elle récupère alors **tous les marchés profonds** de ces matchs (scores exacts, over/under buts, mi-temps, buteurs, corners…) plus le contexte sportif (forme, blessés, H2H, classement).
4. Elle génère un **prompt texte compact, prêt à coller dans Claude**, contenant tout ce qu'il faut pour que Claude produise une analyse sportive exploitable.

Le livrable final de l'app est un bloc de texte dans mon presse-papiers. Rien de plus.

---

## 1. Contraintes techniques (non négociables)

| Sujet | Choix |
|---|---|
| Langage | Python 3.11+ |
| Framework | FastAPI |
| Base de données | SQLite (fichier unique, WAL activé) |
| Templates | Jinja2 |
| Interactivité front | HTMX + CSS vanilla. **Aucun framework JS**, pas de build step, pas de node_modules |
| Client HTTP | `httpx` (client async réutilisé, timeouts explicites) |
| Planification | APScheduler (in-process) |
| Gestion de projet | `uv` (pyproject.toml, uv.lock) |
| Lint / format | `ruff` (lint + format) |
| Tests | `pytest` + `respx` pour mocker les APIs. **Aucun appel réseau dans les tests** |
| Auth | Aucune. Mono-utilisateur, protégé par le reverse proxy |
| Déploiement | systemd + uvicorn, derrière nginx |
| Fuseau | Stockage en UTC, affichage en `Europe/Paris` |

Secrets : uniquement via variables d'environnement, chargées depuis `.env` (non versionné). Fournir un `.env.example` complet.

```
ODDS_API_KEY=
APIFOOTBALL_KEY=
DB_PATH=./data/myassistantbet.db
TZ=Europe/Paris
ODDS_API_CREDIT_FLOOR=500      # blocage de l'étage B sous ce seuil
```

---

## 2. Architecture cible

```
myassistantbet/
├── pyproject.toml
├── .env.example
├── README.md
├── CLAUDE.md
├── data/                       # gitignored (base SQLite)
├── src/myassistantbet/
│   ├── main.py                 # app FastAPI, routes, startup/shutdown
│   ├── config.py               # settings (pydantic-settings)
│   ├── db.py                   # connexion, migrations, helpers
│   ├── migrations/             # fichiers .sql numérotés, appliqués au démarrage
│   ├── providers/
│   │   ├── base.py             # protocole commun, gestion quota, retry, cache
│   │   ├── oddsapi.py          # client The Odds API
│   │   └── apifootball.py      # client API-Football
│   ├── services/
│   │   ├── scan.py             # étage A — scan large
│   │   ├── enrich.py           # étage B — marchés profonds + contexte
│   │   ├── render.py           # compression d'un événement en bloc texte
│   │   └── prompt.py           # assemblage du prompt final
│   ├── templates/              # Jinja2 (HTML + templates de prompt .j2)
│   └── static/
└── tests/
    ├── fixtures/               # réponses API réelles capturées, en JSON
    └── test_*.py
```

Séparation stricte : les modules `providers/` ne connaissent rien du métier, `services/` ne fait aucun appel HTTP direct, `main.py` ne contient aucune logique métier.

---

## 3. Modèle de données

Créer ces tables via des migrations SQL numérotées (`001_init.sql`, etc.), appliquées automatiquement au démarrage.

```sql
CREATE TABLE sports (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,          -- football | tennis | cycling
  label TEXT NOT NULL
);

CREATE TABLE competitions (
  id INTEGER PRIMARY KEY,
  sport_id INTEGER NOT NULL REFERENCES sports(id),
  oddsapi_key TEXT,                  -- ex: soccer_sweden_allsvenskan
  apifootball_league_id INTEGER,
  label TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  active BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  sport_id INTEGER NOT NULL REFERENCES sports(id),
  competition_id INTEGER REFERENCES competitions(id),
  oddsapi_event_id TEXT UNIQUE,
  apifootball_fixture_id INTEGER,
  home TEXT NOT NULL,
  away TEXT NOT NULL,
  commence_time TEXT NOT NULL,       -- ISO 8601 UTC
  source TEXT NOT NULL DEFAULT 'api',-- api | manual
  created_at TEXT NOT NULL
);
CREATE INDEX idx_events_time ON events(commence_time);

-- Une ligne par outcome. Jamais de blob JSON de cotes.
CREATE TABLE odds (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  bookmaker TEXT NOT NULL,
  market_key TEXT NOT NULL,
  outcome_name TEXT NOT NULL,
  description TEXT,                  -- nom du joueur pour les props
  point REAL,
  price REAL NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX idx_odds_event_market ON odds(event_id, market_key);

CREATE TABLE context (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                -- form|injuries|h2h|standings|lineups|manual_note
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE INDEX idx_context_event ON context(event_id, kind);

CREATE TABLE sessions (
  id INTEGER PRIMARY KEY,
  label TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE session_events (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  note TEXT,
  PRIMARY KEY (session_id, event_id)
);

CREATE TABLE prompts (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  template_name TEXT NOT NULL,
  body TEXT NOT NULL,
  token_estimate INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE picks (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  event_id INTEGER REFERENCES events(id),
  tier TEXT NOT NULL,                -- safe|fun|ultra_fun|giga_fun|giga_plus
  market TEXT NOT NULL,
  selection TEXT NOT NULL,
  price REAL,
  confidence INTEGER,
  played BOOLEAN NOT NULL DEFAULT 0,
  stake REAL,
  result TEXT,                       -- win|loss|void|pending
  created_at TEXT NOT NULL
);

CREATE TABLE api_usage (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  cost INTEGER NOT NULL,
  remaining INTEGER,
  called_at TEXT NOT NULL
);
```

---

## 4. Intégration — The Odds API

Base URL : `https://api.the-odds-api.com/v4`

### Faits vérifiés à respecter

- **Coût du quota** : `cost = nb_marchés × nb_régions`. Le paramètre `bookmakers` prime sur `regions`, et chaque groupe de 10 bookmakers compte pour 1 région. On interroge donc `bookmakers=betclic_fr` seul → coût 1 par marché.
- L'endpoint `/sports` est **gratuit**. Les réponses vides ne sont **pas** facturées.
- Chaque réponse renvoie les headers `x-requests-remaining` et `x-requests-used`. **Les persister systématiquement dans `api_usage` et les exposer dans l'UI.**
- Betclic est disponible sous la clé `betclic_fr` (régions `eu` et `fr`).
- Les marchés profonds ne sont accessibles que **match par match**, via `/sports/{sport}/events/{eventId}/odds`.

### Endpoints utilisés

| Usage | Endpoint | Coût |
|---|---|---|
| Découverte des sports | `GET /sports` | 0 |
| Étage A — scan | `GET /sports/{sport}/odds?bookmakers=betclic_fr&markets=h2h,totals` | 2 |
| Liste d'événements | `GET /sports/{sport}/events` | 0 |
| Étage B — profondeur | `GET /sports/{sport}/events/{id}/odds?bookmakers=betclic_fr&markets=...` | 1 par marché |

### Marchés à demander à l'étage B (football)

```
correct_score, correct_score_h1, totals_h1, alternate_totals,
btts, btts_h1, double_chance, halftime_fulltime,
team_totals, alternate_team_totals,
alternate_totals_corners, alternate_totals_cards, corners_1x2,
alternate_spreads
```

### Marchés à demander à l'étage B (tennis)

```
h2h, spreads, totals, h2h_s1, h2h_s2, spreads_s1, totals_s1, alternate_totals_s1
```

### Limites connues — à gérer, pas à contourner

| Limite | Comportement attendu |
|---|---|
| Les props buteurs (`player_goal_scorer_anytime`, `player_first_goal_scorer`) ne sont servies que par des bookmakers US et sur Big 5 + MLS | Ne les demander que si la compétition est dans une liste blanche configurable. Sinon, ne pas dépenser de crédit. |
| Le tennis est limité aux Grand Chelems, ATP 1000/500, WTA 1000/500 | Les ATP/WTA 250 doivent passer par l'ajout manuel d'événement |
| Le cyclisme n'est pas couvert | Module manuel uniquement, aucun appel API |
| Un marché peut être absent pour un match donné | Absence silencieuse, jamais d'erreur. Le bloc de rendu omet simplement la ligne. |

Le client doit implémenter : timeout de 15 s, retry avec backoff exponentiel sur 429/5xx (3 tentatives max), et un **cache disque des réponses en développement** (activé par `DEV_CACHE=1`) pour ne pas brûler le quota pendant le dev.

---

## 5. Intégration — API-Football

Base URL : `https://v3.football.api-sports.io`, header `x-apisports-key`.

Endpoints à utiliser pour l'enrichissement contexte :

| Donnée | Endpoint |
|---|---|
| Fixtures du jour | `/fixtures?date=YYYY-MM-DD&league=&season=` |
| Forme et stats équipe | `/teams/statistics?league=&season=&team=` |
| Blessés / suspendus | `/injuries?fixture=` |
| Confrontations directes | `/fixtures/headtohead?h2h=ID1-ID2&last=5` |
| Classement | `/standings?league=&season=` |
| Compositions | `/fixtures/lineups?fixture=` (souvent dispo tard, tolérer l'absence) |

**Problème à résoudre proprement : le mapping.** The Odds API et API-Football n'utilisent ni les mêmes IDs ni les mêmes noms d'équipes. Implémenter un module `services/matching.py` :

1. Table de correspondance persistante `team_aliases(oddsapi_name, apifootball_id, apifootball_name)`.
2. Résolution automatique par normalisation (minuscules, accents retirés, suffixes `FC`/`IF`/`AIK` retirés) + distance de Levenshtein avec seuil.
3. Si aucune correspondance sûre : ne pas deviner. Marquer l'événement `mapping_pending` et proposer dans l'UI un petit formulaire de résolution manuelle, dont le choix est mémorisé pour toujours.

La couverture blessures d'API-Football est irrégulière selon les ligues. Un contexte manquant doit apparaître explicitement dans le prompt final comme « donnée non disponible », jamais être passé sous silence.

---

## 6. Écrans

### 6.1 Board (`/`)
Tableau des événements à venir sur une fenêtre glissante configurable (défaut : J+0 et J+1).

- Colonnes : heure locale, sport, compétition, affiche, cotes 1N2, ligne O/U principale, case à cocher.
- Filtres : sport, compétition, plage horaire, texte libre.
- Bandeau permanent en haut : crédits Odds API restants, date du dernier scan, bouton « Relancer le scan ».
- Bouton « Ajouter un événement manuel » (obligatoire pour le cyclisme et les ATP 250).

### 6.2 Shortlist (`/session/{id}`)
Les événements cochés, regroupés par sport.

- Bouton **« Enrichir la sélection »** → déclenche l'étage B + l'enrichissement API-Football, avec barre de progression (SSE ou polling HTMX).
- **Estimation du coût en crédits affichée avant le clic.** Si `remaining - coût_estimé < ODDS_API_CREDIT_FLOOR`, le bouton est désactivé avec un message clair.
- Zone de note libre par événement, injectée telle quelle dans le prompt sous `NOTE PERSO`.

### 6.3 Prompt (`/session/{id}/prompt`)
- Sélecteur de template.
- Le prompt rendu, en `<pre>`, avec estimation de tokens (approximation : caractères / 3.6, suffisant).
- Bouton « Copier » (Clipboard API), bouton « Télécharger en .md ».
- Le prompt est sauvegardé en base à chaque génération.

### 6.4 Historique (`/history`) et feuille de session (`/history/{id}`)
Liste des sessions passées. La feuille d'une session porte ses sélections et ses coupons joués : import du tableau de Claude, résultat en un clic, « jouer » qui enregistre un pari simple sans passer par le formulaire de coupon.

### 6.5 Statistiques (`/stats`)
Deux mesures que l'on aurait tort de confondre :

- **ce que vaut l'analyse** — toutes les sélections dont le résultat est connu, jouées ou non, par palier, par confiance annoncée, par sport et par marché. C'est le seul endroit où une sélection écartée compte, et c'est ce qui permet d'opposer le taux des sélections jouées à celui des écartées : si l'écarté gagne aussi souvent, le tri n'apporte rien ;
- **ce que valent les paris** — uniquement ce qui a été posé chez le bookmaker, plus les taux de coupons séparés entre simples et combinés.

**Aucun calcul de ROI, de value ou de CLV**, ici comme ailleurs.

---

## 7. Le rendu compact d'un événement

C'est le composant le plus important de l'application. Un match en JSON brut coûte ~3 000 tokens ; le même en bloc compact en coûte ~300. La qualité de la réponse de Claude dépend directement de cette densité.

`services/render.py` doit exposer `render_event(event) -> str` produisant exactement ce format :

```
### M3 · FOOT · Allsvenskan · Häcken – Djurgården · 03/08 17:30
CONTEXTE
  Classement  Häcken 4e (34pts, 16j) | Djurgården 2e (39pts, 16j)
  Forme 5     Häcken VVNDV (9-4) | Djurgården VVVND (11-3)
  Dom/Ext     Häcken dom 6V-1N-1D 2.1 bpm | Djurgården ext 4V-2N-2D 1.4 bpm
  Absents     Häcken — Rygaard (susp.), Svanbäck (blessé, MC titulaire)
              Djurgården — aucun signalé
  H2H (3)     1-1 · 0-2 D · 2-2
  Repos       Häcken 6j | Djurgården 3j
  NOTE PERSO  (texte libre saisi dans l'UI, omis si vide)

MARCHÉS (Betclic, relevé 08:12)
  1N2         2.55 / 3.55 / 2.60
  DC          1.48 / 1.29 / 1.50
  O/U         1.5: 1.22/4.10 | 2.5: 1.72/2.05 | 3.5: 2.90/1.38 | 4.5: 5.20/1.14
  BTTS        Oui 1.60 / Non 2.25
  MT O/U      0.5: 1.32/3.20 | 1.5: 2.55/1.48
  Éq. buts    Häcken O1.5 2.30 | Djurgården O1.5 2.45
  Score exact 1-1 6.50 | 2-1 8.00 | 1-2 8.50 | 0-1 9.50 | 1-0 8.50 | 2-2 11.0
              2-0 11.0 | 0-0 11.0 | 3-1 15.0 | 1-3 17.0
  Corners     O/U 9.5: 1.85/1.90
```

Règles de rendu :
- Toute ligne sans donnée est **omise**, jamais rendue vide ou avec « N/A ».
- Les scores exacts sont limités aux **10 cotes les plus basses**, triées croissant.
- Les lignes O/U sont limitées aux 5 lignes les plus proches de la ligne principale.
- Les cotes sont formatées à 2 décimales.
- Une donnée de contexte volontairement absente (ex. blessures non couvertes pour cette ligue) devient une ligne explicite : `Absents      données non disponibles pour cette compétition`.
- **L'en-tête ne nomme que le bookmaker principal**, celui dont les cotes sont jouables telles quelles. Toute ligne servie par une autre source la porte entre crochets, en fin de ligne : `[Pinnacle (ref.)]` pour un book de référence, `[saisie manuelle]` pour une cote relevée à la main, `[dont …]` quand une ligne fusionnée mélange les deux. Un en-tête « Betclic + Pinnacle (ref.) » laisse deviner quelle cote est jouable et laquelle ne fait que situer le marché — et une sélection se décide sur la ligne, pas sur l'en-tête.
- **Un marché demandé à l'API et jamais servi devient une ligne `Non servis`**, énumérant les marchés abandonnés pour cette compétition. Une absence constatée est une information : la taire fait chercher un handicap jeux qui n'existe pas, et fait remonter en section F une question déjà tranchée. Un marché qu'aucun bloc ne mentionne — ni en cote, ni en `Non servis` — est au contraire un marché que personne n'a encore demandé.

```
MARCHÉS (Betclic, relevé 14:22)
  Vainqueur   3.60 / 1.30
  Hand. jeux  Svajda +4 1.84 | Fils -4 2.02  [Pinnacle (ref.)]
  Non servis  Set 1, Set 2 — aucun book interrogé ne les sert sur cette compétition
```

Le tennis et le cyclisme ont leurs propres variantes de bloc, dans le même esprit.

---

## 8. Le template de prompt par défaut

À placer dans `templates/prompts/session_default.md.j2`. Il doit être éditable depuis l'UI sans redéploiement.

````
# SESSION D'ANALYSE — {{ date_fr }}

## TON RÔLE
Tu es analyste sportif. Tu évalues exclusivement le CADRE SPORTIF.

INTERDIT, sans exception : value bet, EV, edge, CLV, probabilités implicites,
comparaison de cotes entre bookmakers, devigging, Kelly.
Les cotes servent uniquement à (a) savoir ce qui est jouable et (b) classer
une sélection dans un palier. Elles ne sont jamais un argument en soi.

## RECHERCHE PRÉALABLE
Utilise la recherche web pour aller chercher ce que les données ci-dessous
ne contiennent pas :

FOOTBALL   enjeu réel (titre / Europe / maintien / rien à jouer), turnover
           annoncé, conférence de presse, météo, arbitre, contexte de club
TENNIS     forme récente hors classement, vitesse de la surface, charge de
           matchs sur 7 jours, historique sur ce tournoi, tenue mentale
           (abandons, tie-breaks perdus, retards au service)
CYCLISME   profil et final d'étape, vent, objectifs d'équipe, état du
           classement général, forme sur 10 jours, probabilité d'échappée

Si une information est introuvable, écris-le. N'invente jamais une absence,
une composition, une météo ou une déclaration.

## MATCHS
{% for block in event_blocks %}
{{ block }}
{% endfor %}

## SORTIE ATTENDUE

### A. Analyse par match — 8 lignes maximum chacune
L'angle sportif dominant, puis le marché qui traduit le mieux cet angle.
Si aucun angle net ne se dégage : écris « PASSE » et passe au suivant.
Passer est un résultat valable et attendu sur une partie du lot.

### B. Tableau des sélections
| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Angle (1 ligne) | Ce qui la tue |

Paliers, définis par bande de cote :
  🟢 SAFE        1.25 – 1.70
  🔵 FUN         1.70 – 2.60
  🟠 ULTRA FUN   2.60 – 5.00
  🔴 GIGA FUN    5.00 – 15.0
  💥 GIGA+       > 15.0   (scores exacts multichoix, marchés exotiques)

Quotas indicatifs : 2-4 🟢, 3-5 🔵, 2-4 🟠, 1-3 🔴, 0-2 💥.
Si la matière manque pour remplir un palier, laisse-le vide.
Ne remplis jamais un palier avec une sélection qui appartient à un palier
inférieur : c'est l'erreur la plus coûteuse que tu puisses commettre ici.

### C. Combinés
- Un combiné « solide » : 3-4 sélections 🟢/🔵, cote cible 6-12
- Un combiné « frisson » : 4-5 sélections, cote cible 25-80
- Optionnel : un multichoix scores exacts sur le match le plus lisible
Contrainte : une seule sélection par match dans un combiné.
Pour chaque combiné, nomme explicitement le maillon le plus fragile.

### D. Le match que tu ne jouerais pas
Désigne un match du lot où l'incertitude sportive est trop forte, et explique
pourquoi. Cette section est obligatoire.
````

Variables exposées au template : `date_fr`, `event_blocks` (liste de chaînes), `session_label`, `tiers` (bandes de cotes, configurables en base).

---

## 9. Interdits explicites

Ne code **jamais** les choses suivantes, même si elles semblent utiles :

1. Calcul de value, EV, edge, CLV, probabilités implicites, devigging, Kelly, mise conseillée. L'app est un outil de collecte et de mise en forme, pas un modèle.
2. Authentification, gestion d'utilisateurs, rôles.
3. Scraping de Betclic ou de tout autre bookmaker. Les cotes viennent d'APIs sous contrat.
4. Framework JS front (React, Vue, Next), bundler, TypeScript.
5. PostgreSQL, Redis, Docker Compose multi-services, Celery. Le projet tient sur un processus et un fichier SQLite.
6. Appel automatique à l'API Anthropic. L'app produit un prompt, l'humain le colle. Point.
7. Placement automatique de paris, ou toute intégration transactionnelle avec un bookmaker.

---

## 10. Plan de livraison

Travaille **phase par phase**. À la fin de chaque phase : lance `ruff` et `pytest`, fais un commit atomique avec un message descriptif, affiche-moi un résumé de ce qui a été fait, et **attends ma validation avant de démarrer la phase suivante**. Ne prends pas d'avance.

### Phase 0 — Fondations
- `pyproject.toml` avec `uv`, dépendances épinglées
- Structure de dossiers complète, `config.py` avec pydantic-settings
- `db.py` + système de migrations SQL + `001_init.sql` avec tout le schéma
- App FastAPI qui démarre, route `/health` retournant l'état DB et la config chargée
- `.env.example`, `.gitignore`, `README.md` (installation + lancement), `CLAUDE.md`
- CI GitHub Actions : ruff + pytest

*Critère d'acceptation :* `uv run uvicorn` démarre, `/health` répond 200, la base est créée avec toutes les tables, `pytest` passe.

### Phase 1 — Odds API et étage A
- Client `providers/oddsapi.py` : timeouts, retry, comptabilisation du quota dans `api_usage`, cache disque en mode dev
- `services/scan.py` : scan des compétitions actives, upsert des events et des odds
- Job APScheduler quotidien à 07:00 Europe/Paris, plus déclenchement manuel
- Écran Board avec filtres, cases à cocher, bandeau de crédits
- Seed des compétitions football initiales (Ligue 1, Premier League, Allsvenskan, Eliteserien, CSL, Liga Portugal, Süper Lig — modifiable ensuite en base)

*Critère d'acceptation :* je lance un scan, le board affiche les matchs du jour avec leurs cotes 1N2 et le compteur de crédits a diminué du montant exact attendu.

### Phase 2 — Étage B et génération du prompt
- Récupération des marchés profonds match par match, avec estimation de coût préalable et garde-fou `ODDS_API_CREDIT_FLOOR`
- `services/render.py` conforme à la section 7, **couvert par des tests unitaires sur fixtures réelles**
- `services/prompt.py` + template Jinja de la section 8
- Écrans Shortlist et Prompt, bouton Copier fonctionnel

*Critère d'acceptation :* je coche 6 matchs, je clique sur Enrichir, j'obtiens un prompt copiable de moins de 8 000 tokens contenant les scores exacts et les O/U de chaque match.

### Phase 3 — Contexte sportif
- Client `providers/apifootball.py`
- `services/matching.py` avec table d'alias et résolution manuelle en UI
- Enrichissement forme, classement, blessés, H2H, injectés dans le bloc CONTEXTE

*Critère d'acceptation :* un bloc de match rendu contient forme, classement, absents et H2H, et affiche explicitement « données non disponibles » quand l'API ne couvre pas la ligue.

### Phase 4 — Tennis, cyclisme, événements manuels
- Marchés tennis à l'étage B, bloc de rendu tennis dédié
- Formulaire d'ajout d'événement manuel : sport, libellé, date/heure, cotes saisies à la main, URLs de référence (ProCyclingStats, etc.)
- Bloc de rendu cyclisme (course, étape, profil, startlist)

*Critère d'acceptation :* je peux composer une session mixte foot + tennis + une étape de cyclisme saisie à la main, et obtenir un prompt cohérent.

### Phase 5 — Historique et personnalisation
- Saisie des picks joués et de leur résultat
- Taux de réussite par palier et par sport
- Édition des templates de prompt et des bandes de cotes depuis l'UI

### Phase 6 — Déploiement
- Unité systemd, exemple de configuration nginx
- Script de sauvegarde SQLite (`VACUUM INTO`, rotation 7 jours)
- Section « Déploiement VPS » dans le README

### Phase 8 — Boucle de retour et contexte permanent
Ce qui nourrit le prompt en dehors des cotes, entièrement local : aucun appel réseau, aucun crédit.

- **Retour d'expérience** (`history.feedback()`) : taux de réussite par palier, par confiance annoncée, par sport et par marché sur les N derniers paris tranchés, injectés dans le prompt. Ferme la boucle prompt → picks → résultats → prompt suivant, qui était ouverte : l'analyse repartait de zéro à chaque session.
  - Sous un seuil de picks, aucun détail n'est publié — le prompt dit qu'il manque du recul. Un taux sur trois paris mesure le hasard, et un pourcentage faux fait plus de dégâts que le silence.
  - **Le garde-fou fait partie de la fonctionnalité** : le prompt interdit explicitement de rapprocher un taux de réussite d'une cote. Ce serait calculer une espérance, et le fait que le chiffre vienne de mon propre historique n'y change rien (section 9). Le taux ne sert qu'à relever le niveau d'exigence là où le passé est mauvais.
  - L'écart entre la confiance annoncée et le taux constaté est le signal le plus utile du bloc : il dit que la notation de confiance dérive.
- **Fiches de compétition** (`competitions.notes`) : format, phase, enjeu, particularités. Saisies une fois, rendues **une seule fois par lot** et non à chaque match.
- **Consignes permanentes** (table `preferences`) : ce qui ne change pas d'une session à l'autre, recopié en tête de prompt. Prime sur les préférences générales du template, jamais sur les interdits.

*Critère d'acceptation :* après une dizaine de picks saisis et tranchés, le prompt généré contient mes taux par palier et par confiance, refuse toute comparaison à une cote, et affiche la fiche de chaque compétition du lot une seule fois.

### Phase 9 — Contexte tennis : classements Elo
Le football reçoit forme, classement, absents et H2H ; le tennis n'avait aucune source et son bloc CONTEXTE restait vide. Les classements Elo publiés par Tennis Abstract le comblent, gratuitement et sans clé.

- `providers/tennisabstract.py` : deux pages HTML statiques, une par circuit. Pas une API — le client de base sait désormais rendre du texte brut. Aucun quota, donc rien dans `api_usage`.
- Limites de la source, à respecter : le domaine apex ne répond pas (`www.` obligatoire), un User-Agent de navigateur est requis, et le `robots.txt` interdit les pages joueur et match. Seul `/reports/` est utilisé.
- `services/elo.py` : récupération, stockage, rapprochement des noms et rendu. Deux temps séparés comme pour le contexte football — régénérer un prompt ne déclenche aucun appel.
- Rapprochement plus sévère que pour les clubs, et **aucune résolution manuelle n'existe ici** : en cas de doute, pas de ligne du tout. Un rating attribué au mauvais joueur serait pire qu'une absence.
- La surface est portée par la compétition et saisie à la main. La déduire du libellé d'un tournoi serait une invention ; sans elle, seul l'Elo général est rendu.
- **Interdit sans exception : convertir un écart d'Elo en probabilité.** La page source publie la table de correspondance ; l'utiliser puis rapprocher le résultat d'une cote serait le calcul d'espérance qu'interdit la section 9. Le template de prompt porte l'interdiction, un test la vérifie.

*Critère d'acceptation :* le bloc d'un match de tennis porte une ligne Elo avec le rating général, celui de la surface du tournoi, le pic de carrière et le classement officiel des deux joueurs — et le prompt interdit explicitement d'en tirer une probabilité.

### Phase 10 — Coupons joués
Un pick est une sélection ; un coupon est ce qui a réellement été posé chez le bookmaker : une mise, une ou plusieurs jambes, un résultat global.

- `services/coupons.py` : un coupon se compose de picks déjà saisis, rien n'est retapé.
- **« Joué » veut dire posé chez le bookmaker**, pas proposé par l'analyse : `played` ne passe à vrai qu'au rattachement à un coupon, et repasse à faux si le coupon est supprimé. Une sélection écartée ne pèse donc ni sur les taux de réussite ni sur le retour d'expérience injecté dans le prompt — sinon les indicateurs mélangeraient deux questions distinctes : ce que vaut l'analyse, et ce que valent mes paris.
- Ni le type ni le résultat ne sont stockés : ils se déduisent des jambes. Une jambe perdue fait tomber le coupon même si d'autres sont en attente ; une jambe annulée est neutre ; tout annulé vaut annulé.
- **Ce que ça répare** : un combiné s'enregistrait comme un pick sans événement, donc sans sport, et les taux par sport l'ignoraient en silence. Ses jambes portent désormais chacune leur match.
- Les taux de coupons sont séparés entre simples et combinés — un combiné tombe dès qu'une jambe cède, il ne se compare pas à un pari simple.
- **Aucun calcul financier** (section 9) : la mise est mémorisée, jamais agrégée, jamais multipliée par une cote, et la cote totale du coupon n'est même pas calculée. La capture jointe la porte déjà.
- La capture d'écran est une **pièce jointe, pas une source de données** : la machine ne la lit jamais. La lire supposerait un modèle de vision — donc un appel à l'API Anthropic, interdit n°6 — ou un OCR local peu fiable sur une interface sombre de bookmaker.
- Téléversement : liste blanche de types confirmée par les octets de tête, taille bornée, et le nom fourni par le navigateur n'est jamais utilisé — il est refabriqué, et revalidé avant toute lecture.

*Critère d'acceptation :* j'enregistre un combiné de trois jambes avec sa mise et une capture, je saisis les résultats des jambes, et l'historique affiche le coupon perdu, ses jambes gagnantes comptées dans les taux de leur sport respectif, et aucun montant agrégé nulle part.

---

## 11. Exigences de qualité

- **Tests** : chaque service a ses tests. Les fixtures sont de vraies réponses API anonymisées, stockées dans `tests/fixtures/`. Aucun test ne touche le réseau. Viser une couverture utile sur `render.py`, `matching.py` et le calcul de coût quota — pas un pourcentage global.
- **Erreurs** : une API indisponible n'empêche jamais l'app de servir la page. Les données partielles sont affichées avec une mention visible de ce qui manque.
- **Logs** : logging structuré, un log par appel externe avec endpoint, coût, crédits restants et durée.
- **Idempotence** : relancer un scan deux fois ne duplique aucune ligne. Utiliser des upserts sur clés naturelles.
- **Migrations** : jamais de modification d'un fichier de migration déjà appliqué, toujours un nouveau fichier.
- **CLAUDE.md** : à écrire en Phase 0 et à maintenir. Il doit contenir les commandes usuelles, les conventions du projet, et un rappel de la section 9 (interdits).
- **Commits** : atomiques, en français ou en anglais mais de façon cohérente, sans mention d'outil dans le message.

---

## 12. Pour démarrer

Lis ce fichier en entier, puis :

1. Pose-moi les questions bloquantes s'il y en a — mais uniquement celles qui empêchent réellement de démarrer.
2. Propose-moi ton plan pour la Phase 0 en quelques lignes.
3. Attends ma validation, puis exécute la Phase 0 et rien d'autre.
