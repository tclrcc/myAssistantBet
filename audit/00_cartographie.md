# Phase 0 — Cartographie

Copie de travail : `audit/data/work.db`, obtenue par `VACUUM INTO` (jamais `cp` :
la base est en WAL, une copie de fichier livrerait un etat incomplet).
Sauvegarde : `data/myassistantbet.db.audit-2026-08-24.bak`.
`PRAGMA integrity_check` = ok. Derniere migration appliquee : **078**.

Toute la phase 0 est en lecture seule. Aucune ecriture, aucune migration.

---

## 0. Six premisses du prompt d'audit, dementies par la mesure

Ecrites ici parce qu'elles changent le perimetre de plusieurs phases.

| Premisse du prompt | Ce que la mesure dit |
| --- | --- |
| front **Next.js** | il n'y en a pas : HTMX + CSS vanilla, `SPEC.md` §9.4 interdit tout framework JS, bundler et TypeScript. Aucun `package.json`, aucun `node_modules`. **La phase 0 « pages Next.js » est sans objet** ; elle devient l'inventaire des 38 gabarits Jinja2 |
| `SKILL.md` **v1.4** a lire | le cadre publie est en **1.3** (`deploy/cadre-lu.json`, sha256 verifie le 21/08). `FRAMEWORK_VERSION = "1.3"`. La 1.4 est redigee mais **non publiee** — un numero pose en avance est un incident deja documente (21/08) |
| `SKILL.md` dans le repo | non : il vit dans un cache de plugin (`~/.claude/remote/plugins/90291e180b16a5e6/…`), non editable depuis ici. Le repo n'en porte qu'une **preuve de lecture** |
| **de-vigging Shin**, **Kelly**, **EV**, **CLV** implementes | aucun des quatre n'existe. 4 occurrences en tout, **toutes des commentaires disant l'inverse** (`inference.py:702` « aucun devig », `stakes.py:6` « ni edge, ni probabilite implicite, ni Kelly »). `SPEC.md` §9.1 les interdit nommement. **Phases 1.4 / 2.4 / 3.2 / 3.6 reformulees** en « l'interdit tient-il ? » (arbitrage valide) |
| **write-time guard refuse** une saisie sur match commence | **il ne refuse plus rien depuis le 17/08/2026** (`history.py:2160-2176`). Decision datee et mesuree : la garde d'origine se laissait contourner (37 tardives sur 52 sans motif) et surtout refuser ferait disparaitre la population qui mesure le biais. Une saisie tardive est desormais **marquee** `tardive`, pas refusee |
| bugs « suspectes » : peuplement de `confidence_computed`, collage, collisions de reperes | a instruire en phase 1 — mais le depot porte deja `write_paths.py`, `ingestion_rejects` (593 lignes) et `imports_raw` (69), c'est-a-dire l'instrumentation qui repond a ces trois questions. Rien n'est tenu pour acquis |

Consequence pratique : le prompt decrit un projet qui calcule des esperances et
protege ses saisies par un refus. L'application fait exactement l'inverse, **par
decision documentee**. L'audit porte donc sur la tenue de ces decisions, pas sur
la correction d'implementations absentes.

---

## 1. Volumetrie

Periode reelle : **05/08/2026 → 24/08/2026**, 20 jours. Tout tient dans un seul mois.

| Objet | n |
| --- | ---: |
| sessions | 22 |
| prompts archives | 216 |
| `prompt_events` (lot soumis) | 1 126 |
| evenements | 1 319 |
| selections (`picks`) | **523** |
| — section C | 428 |
| — section C-bis (exploratoires) | 95 |
| — tranchees (`win`/`loss`) | **491** |
| cotes vivantes (`odds`) | 48 401 |
| releves figes (`prompt_odds`) | 41 520 |
| mouvements de cote (`odds_history`) | 8 204 |
| reglements | 226 |
| rejets d'ingestion | 593 |
| collages bruts conserves | 69 |

**Par sport** — football 378 selections (351 tranchees), tennis 145 (140).
Aucune selection sans evenement rattache. 1 evenement de cyclisme, 0 selection.

**Statuts de resultat** : `loss` 249, `win` 242, `pending` 17, `void` 15.
**Ni `push`, ni `cashout`** — la question 1.5 du prompt porte donc sur trois
statuts seulement, et le traitement de `void` au denominateur est le seul point a
verifier.

**Tables vides** : `coupons`, `combo_legs` a 28 mais `coupons` a 0, `mises`,
`bankroll_journee`. Coherent avec le dossier : aucun pari n'a ete pose chez un
book, la mesure porte sur les selections et non sur des paris.

---

## 2. Schema

**41 tables**, aucune vue, **aucun trigger**. Schema complet : `audit/data/schema.sql`.

### Les six tables qui portent la mesure

- **`picks`** — 43 colonnes, la table centrale. Au-dela des champs de selection
  (`tier`, `market`, `selection`, `price`, `confidence`, `result`), elle porte
  quatre familles ajoutees par les chantiers successifs :
  - *provenance du prix* : `price_source`, `price_real`, `tier_real`, `market_key` ;
  - *anteriorite* : `tardive`, `late_minutes`, `late_reason`, `result_at` ;
  - *notation calculee* : `confidence_computed`, `confidence_claimed`,
    `claim_raw_json`, `gap_touches_factor`, `distinct_publishers`,
    `research_overridden`, `research_override_cause`, `source_level_effective` ;
  - *tracabilite du collage* : `import_id`, `offset_start`/`_end`,
    `claim_offset_start`/`_end`, `prose_source`, `prompt_id`, `framework_version`.
- **`events`** — 15 colonnes. `commence_time` + `previous_commence_time` +
  `commence_shifted_at` (report d'horaire), `match_outcome_type` (forfait).
- **`odds`** — cotes vivantes, une ligne par issue, remplacees a chaque scan.
- **`prompt_odds`** — **le marche fige a l'archivage du prompt**. 41 520 lignes,
  6 books, 18 marches. **Point decisif pour la phase 3** : les groupes
  `(session, event, book, h2h)` portent 2 issues (tennis) ou 3 (football) —
  jamais 1. **L'overround est donc calculable sur donnees reelles**, ce qui rend
  faisable la quantification du biais demandee en tete de phase 3.
- **`prompts`** / **`prompt_events`** — corps archives et lot soumis.
- **`reglements`** (226) — reglement automatique, avec `observed_at`.

### Index

Presents : `events(commence_time)`, `odds(event_id, market_key)`,
`prompt_odds(session_id, event_id)`, `prompt_events(event_id)`, et sur `picks`
les colonnes `coupon_id`, `exploratoire`, `import_id`, `tardive`, `late_minutes`.

**Absents et attendus en phase 5** : `picks(session_id)`, `picks(event_id)`,
`picks(created_at)`, `picks(result)` — ce sont les quatre colonnes de filtrage de
toutes les requetes de statistiques.

---

## 3. Modules

50 378 lignes de Python. Les dix plus gros, avec leur **role reel** :

| Module | Lignes | Role reel |
| --- | ---: | --- |
| `services/history.py` | 6 686 | selections, agregats, `analysis()`, `feedback()`, et **le seul `INSERT INTO picks` du depot** |
| `services/context.py` | 3 289 | contexte sportif football, assemblage des lignes |
| `services/serve_stats.py` | 3 181 | statistiques de service tennis |
| `main.py` | 2 268 | 79 routes FastAPI |
| `services/prompt.py` | 2 023 | assemblage du prompt et budget de tokens |
| `services/picks_import.py` | 1 912 | lecture du tableau colle, blocs `conf`, sections |
| `services/stats_export.py` | 1 835 | export Markdown/JSON de `/stats` |
| `services/inference.py` | 1 085 | **couche statistique pure** : Wilson, Fisher exact, BH, residu |
| `services/settlement.py` | 959 | reglement automatique |
| `services/confidence.py` | 582 | `Claim.rung()` — le cran calcule |

Architecture en trois etages, conforme a `CLAUDE.md` : `providers/` (HTTP seul),
`services/` (metier, aucun appel HTTP), `main.py` (routes, aucune logique).

**Modules qui portent deja un garde-fou d'audit** :
- `services/write_paths.py` — registre des chemins d'ecriture vers
  `picks`/`combos`/`combo_legs`/`set_scores`, **verifie par analyse statique**
  (`ast` + regex sur `INSERT INTO`), donc complet par construction et non par
  discipline. Repond d'avance a la question 1.3 « liste tous les chemins d'ecriture ».
- `services/framework.py` — compare `FRAMEWORK_VERSION` au cadre publie lu sur le
  disque, avec repli sur `deploy/cadre-lu.json`.
- `selfcheck.py` — controles de bout en bout.

**Chemins d'ecriture reels vers `picks`** : un seul `INSERT`, dans
`history.add_pick()`, appele depuis trois endroits — `main.py:1326` (saisie a la
main), `main.py:1446` (import d'un collage), `replay.py:148` (rejeu). Trois
chemins a verifier en 1.3, pas davantage.

---

## 4. Endpoints

**79 routes**, toutes rendues en HTML sauf trois : `/session/{id}/payload.json`,
`/api/stats/export`, `/health`. Regroupement par fonction :

| Famille | n | Ecrit dans |
| --- | ---: | --- |
| board / scan / shortlist | 11 | `events`, `odds`, `session_events` |
| evenement (contexte, cotes, statut) | 8 | `context`, `odds`, `events` |
| session (enrichissement, prompt) | 10 | `context`, `prompts`, `prompt_events`, `prompt_odds` |
| competitions | 17 | `competitions`, `market_coverage` |
| historique / picks / coupons | 22 | **`picks`**, `coupons`, `reglements`, `set_scores` |
| reglages | 9 | `preferences`, `tiers`, `confidence_bands`, `market_families` |
| lecture seule (`/stats`, `/history`, `/health`, export) | 2 | — |

Les trois routes qui ecrivent une selection sont
`POST /history/{session_id}/picks` (a la main),
`POST /history/{session_id}/picks/import` (collage),
et le rejeu hors HTTP.

---

## 5. Front

Pas de Next.js. **38 gabarits Jinja2** : 24 fragments HTMX (`_*.html`), 13 pages
completes, et **un seul gabarit de prompt** — `templates/prompts/session_default.md.j2`.

HTMX est vendorise (`static/htmx.min.js`), Inter aussi. Aucun appel reseau depuis
la page, un test le verifie. `static/app.js` est le seul JavaScript maison.

Le gabarit de prompt est **relu sur le disque a chaque generation**
(`FileSystemLoader` reconstruit par appel), quand le code Python est charge au
demarrage — mode de panne connu et documente : un `git pull` sans redemarrage
laisse gabarit du lot et code d'avant, sans qu'aucune erreur ne se leve.
A instruire en phase 4.

---

## 6. Tests

**80 fichiers, 49 657 lignes** — soit un volume de test presque egal au volume de
code. Les noms couvrent nommement les regles critiques citees par le prompt :
`test_anteriorite.py`, `test_confidence.py`, `test_inference.py`,
`test_controles_cadre.py`, `test_collage_complet.py`, `test_bornes_du_resultat.py`,
`test_marche_a_la_prise.py`, `test_reglement.py`.

La phase 5 ne demandera donc pas « y a-t-il des tests » mais **quelles regles
metier ne sont couvertes par aucun**, et surtout — lecon deja payee deux fois
dans ce projet — **quels tests mesurent le service sans voir la surface qui le rend**.
