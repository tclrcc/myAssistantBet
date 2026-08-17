"""Ce que coute le cadre, et ce que coute un bloc.

**Le gabarit grossit a chaque lot livre, et personne ne le surveillait.** Mesure
du lot 4 sur 142 prompts archives : de 853 a 11 934 de cout fixe et de 145 a 698
par bloc en onze jours, quand le budget de recherche restait a sept dossiers. Ce
lot-ci y ajoute encore quatre lignes par bloc tennis.
"""

from __future__ import annotations

from datetime import UTC, datetime

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import prompt as prompt_service


def test_le_decoupage_se_mesure_et_ne_s_ajuste_pas() -> None:
    """**Un prompt est un preambule suivi de N blocs**, et la frontiere est un
    en-tete. Il n'y a rien a estimer."""
    corps = "cadre " * 100 + "\n\n### M1 · tennis · A – B · 18h\n" + "bloc " * 50

    cout = prompt_service.split_cost(corps)

    assert cout.blocks == 1
    assert cout.fixed > 0
    assert cout.block_tokens > 0
    assert cout.fixed + cout.block_tokens == cout.tokens


def test_le_cout_par_bloc_est_le_total_divise_par_le_compte() -> None:
    corps = "cadre\n\n### M1 · x\naaa\n### M2 · y\naaa\n### M3 · z\naaa"

    cout = prompt_service.split_cost(corps)

    assert cout.blocks == 3
    assert cout.per_block == cout.block_tokens / 3


def test_un_prompt_sans_bloc_est_tout_en_cadre() -> None:
    """**Et `per_block` rend None, jamais zero** : zero se lirait comme un bloc
    gratuit."""
    cout = prompt_service.split_cost("un preambule et rien d'autre")

    assert cout.blocks == 0
    assert cout.block_tokens == 0
    assert cout.per_block is None
    assert cout.fixed == cout.tokens


def test_le_decoupage_s_ecrit_a_la_generation(migrated: Settings) -> None:
    """Seul moment ou il ne coute rien : le corps est deja en main.

    Le lot 4 l'avait mesure en lecture seule, faute de pouvoir toucher ce chemin.
    """
    session_id = _session(migrated)
    rendu = prompt_service.build_prompt(
        session_id, settings=migrated, now=datetime(2026, 8, 17, 12, tzinfo=UTC)
    )
    prompt_service.save_prompt(session_id, rendu, migrated)

    row = db.query_one(
        "SELECT blocks, fixed_tokens, block_tokens, token_estimate FROM prompts",
        settings=migrated,
    )
    assert row is not None
    assert row["blocks"] is not None
    assert row["fixed_tokens"] + row["block_tokens"] == row["token_estimate"]


def test_la_reprise_relit_et_ne_reconstitue_rien(migrated: Settings) -> None:
    """**Retro-remplir est sur ici**, contrairement au cran calcule ou a la
    source d'un prix : le corps est archive depuis toujours et porte ses propres
    en-tetes. Le decoupage d'un prompt du 04/08 se refait comme celui
    d'aujourd'hui.
    """
    db.execute(
        "INSERT INTO sessions (created_at) VALUES ('2026-08-04T12:00:00Z')", settings=migrated
    )
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (1, 'x', ?, 40, '2026-08-04T12:00:00Z')",
        ("cadre ancien\n\n### M1 · x\nbloc\n### M2 · y\nbloc",),
        settings=migrated,
    )

    ecrits = prompt_service.backfill_costs(migrated)

    row = db.query_one("SELECT blocks, fixed_tokens FROM prompts", settings=migrated)
    assert ecrits == 1
    assert row is not None
    assert row["blocks"] == 2
    assert row["fixed_tokens"] > 0


def test_la_reprise_est_idempotente(migrated: Settings) -> None:
    """Une ligne deja decoupee n'est pas reprise."""
    db.execute(
        "INSERT INTO sessions (created_at) VALUES ('2026-08-04T12:00:00Z')", settings=migrated
    )
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (1, 'x', '### M1 · x', 10, '2026-08-04T12:00:00Z')",
        settings=migrated,
    )

    assert prompt_service.backfill_costs(migrated) == 1
    assert prompt_service.backfill_costs(migrated) == 0


def test_la_serie_est_par_jour_et_le_cadre_est_une_mediane(migrated: Settings) -> None:
    """**Par jour et non par prompt** : une session en genere jusqu'a vingt,
    tous rendus par le meme gabarit, et vingt points identiques ne dessinent pas
    une courbe.

    Le cadre est une **mediane** : une moyenne suivrait un prompt aberrant, et
    c'est justement l'aberration qu'on veut voir comme un point.
    """
    db.execute(
        "INSERT INTO sessions (created_at) VALUES ('2026-08-04T12:00:00Z')", settings=migrated
    )
    for fixe in (100, 200, 9000):
        db.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at, "
            "  blocks, fixed_tokens, block_tokens) "
            "VALUES (1, 'x', '', ?, '2026-08-04T12:00:00Z', 2, ?, 100)",
            (fixe + 100, fixe),
            settings=migrated,
        )

    serie = prompt_service.cost_series(migrated)

    assert len(serie) == 1
    assert serie[0].prompts == 3
    assert serie[0].fixed == 200, "la mediane, pas la moyenne — 3100 aurait suivi l'aberration"
    assert serie[0].per_block == 50.0


def _session(settings: Settings) -> int:
    from myassistantbet.services import board as board_service

    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key LIKE 'tennis%' LIMIT 1", settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 'e1', 'A', 'B', "
        "'2026-08-18T18:00:00Z', 'api', ?)",
        (sport["id"], competition["id"], db.utcnow()),
        settings=settings,
    )
    event = db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)
    return board_service.toggle_selection(int(event["id"]), True, settings)
