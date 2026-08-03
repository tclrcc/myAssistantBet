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
| 1 | The Odds API, etage A (scan large), ecran Board | a venir |
| 2 | Etage B (marches profonds), rendu compact, generation du prompt | a venir |
| 3 | Contexte sportif via API-Football, mapping des equipes | a venir |
| 4 | Tennis, cyclisme, evenements manuels | a venir |
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

Aucune cle n'est necessaire pour demarrer l'application en Phase 0.

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
├── migrations/     # fichiers .sql numerotes
├── providers/      # clients d'APIs externes — ne connaissent rien du metier
├── services/       # logique metier — aucun appel HTTP direct
├── templates/      # Jinja2 (HTML et templates de prompt .j2)
└── static/
tests/
├── fixtures/       # reponses d'API reelles capturees, en JSON
└── test_*.py
```

## Fuseau horaire

Tout est stocke en UTC (chaines ISO 8601). L'affichage se fait en `Europe/Paris`, valeur
configurable via `TZ`.
