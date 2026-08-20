from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from myassistantbet import db
from myassistantbet.config import Settings

#: Attendus derives du disque : ajouter une migration ne doit pas casser ces tests.
ALL_MIGRATIONS = [
    name for _, name, _ in db.discover_migrations(Settings(_env_file=None).migrations_dir)
]
LATEST_VERSION = len(ALL_MIGRATIONS)

EXPECTED_TABLES = {
    "api_responses",
    "api_usage",
    "bankroll_journee",
    "changelog_mesure",
    "combo_legs",
    "combos",
    "competitions",
    "confidence_bands",
    "context",
    "context_outcomes",
    "coupons",
    "events",
    "imports_raw",
    "ingestion_rejects",
    "league_context",
    "market_coverage",
    "market_families",
    "mises",
    "odds",
    "odds_history",
    "picks",
    "player_alias",
    "player_context",
    "player_serve_agg",
    "preferences",
    "prompt_events",
    "prompt_odds",
    "prompts",
    "reglements",
    "schema_migrations",
    "session_events",
    "source_freshness",
    "sessions",
    "set_scores",
    "sports",
    "team_aliases",
    "team_context",
    "tennis_elo",
    "tennis_history_state",
    "tennis_matches",
    "tiers",
}


def test_migrations_creent_toutes_les_tables(isolated_settings: Settings) -> None:
    applied = db.run_migrations(isolated_settings)

    assert applied == ALL_MIGRATIONS
    assert set(db.list_tables(isolated_settings)) == EXPECTED_TABLES


def test_migrations_sont_idempotentes(isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    second_run = db.run_migrations(isolated_settings)

    assert second_run == []
    assert set(db.list_tables(isolated_settings)) == EXPECTED_TABLES

    rows = db.query("SELECT version, name FROM schema_migrations", settings=isolated_settings)
    assert [(row["version"], row["name"]) for row in rows] == list(
        enumerate(ALL_MIGRATIONS, start=1)
    )


def test_pragmas_actifs(isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)

    with db.connect(isolated_settings) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_cles_etrangeres_appliquees(isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (999, "betclic_fr", "h2h", "Home", 1.85, db.utcnow()),
            settings=isolated_settings,
        )


def test_rollback_sur_erreur(isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)

    with pytest.raises(sqlite3.OperationalError), db.connect(isolated_settings) as conn:
        conn.execute("INSERT INTO sports (key, label) VALUES ('handball', 'Handball')")
        conn.execute("SELECT * FROM table_inexistante")

    assert (
        db.query_one("SELECT id FROM sports WHERE key = 'handball'", settings=isolated_settings)
        is None
    )


def test_health_retourne_l_etat_du_schema(isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    state = db.health(isolated_settings)

    assert state["ok"] is True
    assert state["schema_version"] == LATEST_VERSION
    assert state["journal_mode"] == "wal"
    assert set(state["tables"]) == EXPECTED_TABLES


def test_discover_migrations_trie_par_version(tmp_path: Path) -> None:
    for name in ("010_dix.sql", "002_deux.sql", "001_un.sql"):
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")

    assert [item[0] for item in db.discover_migrations(tmp_path)] == [1, 2, 10]


def test_discover_migrations_refuse_un_nom_invalide(tmp_path: Path) -> None:
    (tmp_path / "init.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="Nom de migration invalide"):
        db.discover_migrations(tmp_path)


def test_discover_migrations_refuse_une_version_dupliquee(tmp_path: Path) -> None:
    (tmp_path / "001_un.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "001_bis.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ValueError, match="dupliquee"):
        db.discover_migrations(tmp_path)


def test_migration_en_echec_est_annulee(tmp_path: Path, isolated_settings: Settings) -> None:
    (tmp_path / "001_ok.sql").write_text(
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    (tmp_path / "002_ko.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);\nCECI N'EST PAS DU SQL;", encoding="utf-8"
    )

    with pytest.raises(sqlite3.OperationalError):
        db.run_migrations(isolated_settings, migrations_dir=tmp_path)

    tables = set(db.list_tables(isolated_settings))
    assert "t1" in tables
    assert "t2" not in tables
    versions = [
        row["version"]
        for row in db.query("SELECT version FROM schema_migrations", settings=isolated_settings)
    ]
    assert versions == [1]
