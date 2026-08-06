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
| 5 | Historique des picks, personnalisation des templates | fait |
| 6 | Deploiement VPS (systemd, nginx, sauvegardes) | fait |

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
uv run uvicorn myassistantbet.main:app --reload --port 8022
```

L'application ecoute sur <http://127.0.0.1:8022>. Les migrations de base sont appliquees
automatiquement au demarrage.

Le port est explicite parce que le defaut d'uvicorn (8000) est souvent pris, et parce que
le service systemd occupe deja 8021 : garder le developpement sur un troisieme port evite
de croire qu'on regarde ses modifications alors qu'on lit l'instance de production.

Verification :

```bash
curl -s http://127.0.0.1:8022/health | python3 -m json.tool
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

### Historique (`/history`)

Sessions passees, prompts generes, et saisie **a posteriori** des picks joues.

- Un pick porte un palier, un marche, une selection, et facultativement une cote, une
  confiance sur 5 et une mise. Il peut etre rattache a un match ou rester un combine.
- Le resultat se change directement depuis la ligne : en attente, gagne, perdu, annule.
- **Taux de reussite** par palier, par sport et au total : `gagnes / (gagnes + perdus)`.
  Les paris annules et en attente sont exclus du denominateur.

**Aucun indicateur financier n'est produit** (SPEC section 9) : ni ROI, ni value, ni CLV, ni
esperance. La mise est memorisee parce qu'elle fait partie de ce qui a ete joue, mais elle
n'est jamais agregee. Un test verifie explicitement qu'aucun de ces champs n'existe.

### Reglages (`/settings`)

- **Templates de prompt** : editeur, creation de variantes, suppression. Un template qui ne
  compile pas est **refuse avant ecriture** — sinon toute generation serait cassee. Le nom
  est valide strictement, aucune traversee de repertoire n'est possible. Le template par
  defaut n'est pas supprimable.
- **Bandes de cotes** : libelle, emoji, bornes et quotas de chaque palier. Les bornes sont
  controlees (haute > basse, quota max >= quota min) avant ecriture. Les valeurs alimentent
  directement le prompt.

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
│   ├── history.py  # sessions passees, picks joues, taux de reussite
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

## Sauvegardes

```bash
uv run myassistantbet-backup                 # sauvegarde + rotation
uv run myassistantbet-backup --keep-days 30  # retention ponctuelle differente
```

La sauvegarde utilise `VACUUM INTO`, qui produit une copie **coherente et compactee meme
pendant que l'application ecrit**. Une simple copie du fichier `.db` laisserait de cote le
journal WAL et pourrait livrer une base inutilisable : ne jamais faire ca.

Les fichiers sont ecrits dans `BACKUP_DIR` sous la forme
`myassistantbet-AAAAMMJJ-HHMMSS.db`, et ceux de plus de `BACKUP_KEEP_DAYS` jours sont
supprimes. **La sauvegarde la plus recente n'est jamais supprimee**, meme si elle a depasse
le delai : sans cette garde, une interruption prolongee finirait par effacer la derniere
copie existante.

Restauration :

```bash
sudo systemctl stop myassistantbet
cp data/backups/myassistantbet-20260804-063000.db data/myassistantbet.db
rm -f data/myassistantbet.db-wal data/myassistantbet.db-shm
sudo systemctl start myassistantbet
```

## Deploiement VPS

Testé sur Debian 12 et Ubuntu 24.04. Les fichiers cites sont dans [`deploy/`](./deploy),
et decrivent le deploiement reel : l'application vit dans le home de l'utilisateur `ubuntu`,
uvicorn ecoute sur `127.0.0.1:8021`, nginx expose le port 443.

### 1. Code et dependances

```bash
git clone https://github.com/tclrcc/myAssistantBet.git ~/myAssistantBet
cd ~/myAssistantBet
uv sync --frozen
cp .env.example .env
nano .env                                 # renseigner ODDS_API_KEY et APIFOOTBALL_KEY
chmod 600 .env                            # le fichier contient des secrets
```

L'unite systemd appelle `uv` par son chemin absolu (`~/.local/bin/uv`, l'emplacement de
l'installateur officiel). Verifier avec `command -v uv` et corriger l'unite si besoin.

### 2. Service

```bash
sudo cp deploy/myassistantbet.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now myassistantbet
sudo systemctl status myassistantbet
curl -s localhost:8021/health | python3 -m json.tool
```

`enable` est le mot important : c'est lui qui fait repartir l'application au demarrage de la
machine. Lancee a la main dans un shell, elle mourait avec la session SSH.

L'application n'ecoute que sur `127.0.0.1` : elle n'est joignable que par le proxy. L'unite
est durcie (`ProtectSystem=strict`, `NoNewPrivileges`, filtrage d'appels systeme) et seuls
la base, les sauvegardes, les templates de prompt et l'environnement virtuel sont accessibles
en ecriture.

Le port 8021 n'est pas arbitraire : 8000, le defaut d'uvicorn, est couramment occupe par un
autre service sur la meme machine.

### 3. nginx, TLS et mot de passe

Aucun domaine a acheter : le nom d'hote attribue au VPS (ici `vps-5ff241d3.vps.ovh.net`)
resout deja publiquement, ce qui suffit a Let's Encrypt.

```bash
sudo apt install nginx certbot apache2-utils
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/myassistantbet.conf
sudo ln -s /etc/nginx/sites-available/myassistantbet.conf /etc/nginx/sites-enabled/
```

Le fichier livre contient les deux blocs, dont celui en 443 qui reference des certificats
qui n'existent pas encore — `nginx -t` echouerait. Commenter le bloc 443 le temps d'obtenir
le certificat, puis le retablir :

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/html -d vps-5ff241d3.vps.ovh.net
sudo htpasswd -c /etc/nginx/myassistantbet.htpasswd <utilisateur>
sudo chown root:www-data /etc/nginx/myassistantbet.htpasswd && sudo chmod 640 $_
sudo nginx -t && sudo systemctl reload nginx
```

Le renouvellement est porte par `certbot.timer`, deja installe par le paquet. En mode
`--webroot`, certbot ne recharge pas nginx : sans le crochet ci-dessous, le certificat est
bien renouvele sur le disque mais l'ancien continue d'etre servi jusqu'a son expiration.

```bash
printf '#!/bin/sh\nsystemctl reload nginx\n' \
  | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
sudo certbot renew --dry-run --no-random-sleep-on-renew
```

> **L'application n'a aucune authentification** — c'est un choix assume (section 1 de
> `SPEC.md`), et c'est ce proxy qui la protege. Ne pas la deployer sans l'authentification
> basique, ou sans une restriction equivalente (VPN, filtrage par IP). Sans cela, n'importe
> qui peut declencher des scans et bruler le quota d'API.

Verifier la version de nginx (`nginx -v`) : la directive HTTP/2 change de forme avant et
apres la 1.25.1, un commentaire dans le fichier explique les deux ecritures.

### 4. Sauvegardes automatiques

```bash
sudo cp deploy/myassistantbet-backup.service deploy/myassistantbet-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now myassistantbet-backup.timer
sudo systemctl list-timers myassistantbet-backup
sudo systemctl start myassistantbet-backup   # declenchement immediat, pour verifier
```

Le minuteur tourne a 06:30, avant le scan de 07:00. `Persistent=true` rattrape la sauvegarde
si le serveur etait eteint a l'heure prevue.

### 5. Exploitation

```bash
sudo journalctl -u myassistantbet -f              # logs en direct
sudo journalctl -u myassistantbet-backup --since today
sudo systemctl restart myassistantbet             # apres un changement de .env
```

Mise a jour :

```bash
cd ~/myAssistantBet
git pull
uv sync --frozen
sudo systemctl restart myassistantbet
```

Les migrations de base sont appliquees automatiquement au demarrage. Faire une sauvegarde
avant une mise a jour qui en contient une nouvelle :
`sudo systemctl start myassistantbet-backup`.

## Fuseau horaire

Tout est stocke en UTC (chaines ISO 8601). L'affichage se fait en `Europe/Paris`, valeur
configurable via `TZ`.
