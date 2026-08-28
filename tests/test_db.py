from __future__ import annotations

import logging
import shutil
import sqlite3
import sys
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
    "player_palmares",
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


def test_une_variable_prefixee_sans_effet_est_refusee(monkeypatch) -> None:
    """**Le cas residuel de l'incident du 21/08.** `extra="forbid"` ne regarde
    que les cles d'un `.env` : une variable d'environnement inconnue n'y est ni
    lue ni refusee, elle ne fait rien en silence. C'est ainsi que
    `MYASSISTANTBET_DB` a laisse un script se croire isole pendant que
    `get_settings()` rendait les parametres servis.

    Le prefixe n'a aucun usage legitime — l'application ne declare pas
    d'`env_prefix` — donc le refus est sans faux positif possible.
    """
    monkeypatch.setenv("MYASSISTANTBET_DB", "/tmp/jamais-lu.db")

    with pytest.raises(ValueError, match="sans effet"):
        Settings(_env_file=None)


def test_une_variable_prefixee_qui_correspond_a_un_champ_passe(monkeypatch) -> None:
    """L'autre moitie : le refus porte sur ce qui **ne correspond a rien**, pas
    sur le prefixe lui-meme. Une garde qui refuserait aussi un nom reconnu serait
    pire qu'absente."""
    monkeypatch.setenv("MYASSISTANTBET_DB_PATH", "/tmp/reconnu.db")

    assert Settings(_env_file=None) is not None


def test_une_migration_non_declaree_est_refusee(isolated_settings: Settings, monkeypatch) -> None:
    """**Garde payee le 21/08/2026.** Un script de controle a cru isoler sa base
    par `MYASSISTANTBET_DB` — le champ s'appelle `db_path`, donc la variable est
    `DB_PATH` — et l'override n'a rien fait : `get_settings()` a rendu les
    parametres servis, et la migration du jour est partie sur la production.

    Le test simule l'absence de pytest, sans quoi l'exemption couvrirait
    l'appel : c'est la seule facon de verifier la garde depuis la suite qu'elle
    exempte.
    """
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    with pytest.raises(db.MigrationRefused, match="deliberate"):
        db.run_migrations(isolated_settings)


def test_un_demarrage_declare_migre(isolated_settings: Settings, monkeypatch) -> None:
    """L'autre moitie : une garde qui refuserait aussi l'appel legitime serait
    pire qu'absente."""
    monkeypatch.delitem(sys.modules, "pytest", raising=False)

    assert db.run_migrations(isolated_settings, deliberate=True), "les migrations passent"


def test_une_copie_de_travail_est_inscriptible_et_detachee(isolated_settings: Settings) -> None:
    """Le pendant du `VACUUM INTO` de lecture. La regle du depot n'avait que la
    moitie lecture, et c'est par la moitie manquante qu'une migration est partie
    sur la base servie.

    Les parametres sont **derives**, jamais poses dans l'environnement : c'est le
    detour par l'environnement qui a echoue.
    """
    db.run_migrations(isolated_settings)
    with db.scratch_copy(isolated_settings) as copie:
        assert copie.db_path_absolute != isolated_settings.db_path_absolute
        assert copie.db_path_absolute.exists()
        with db.connect(copie) as conn:
            conn.execute("UPDATE tiers SET max_price = 9.99 WHERE key = 'safe'")
    with db.connect(isolated_settings) as conn:
        intact = conn.execute("SELECT max_price FROM tiers WHERE key = 'safe'").fetchone()
    assert float(intact["max_price"]) != 9.99, "la base d'origine n'a pas bouge"


def test_une_copie_de_travail_ne_survit_pas_a_son_usage(isolated_settings: Settings) -> None:
    """**C.26 — le garde ecrit pour proteger la base servie faisait tomber le
    banc.**

    `scratch_copy` creait un `mkdtemp` et ne le supprimait jamais, alors que son
    docstring annonce une copie « jetable ». Trouve par accident le 28/08/2026 :
    `/tmp`, un tmpfs de 5,8 Go, sature, et **884 tests en erreur sur
    `database or disk is full`** — des tests qui n'avaient rien a se reprocher.
    Deux repertoires de 392 Mo, anterieurs a la session qui l'a trouve,
    etablissent que c'est le mecanisme et non un incident.

    Un banc qui tombe pour une cause **sans rapport avec le code** est ce qui
    fait defaire un correctif juste, et la prochaine personne a le rencontrer
    cherchera dans le code plutot que dans `/tmp`.
    """
    db.run_migrations(isolated_settings)
    with db.scratch_copy(isolated_settings) as copie:
        cible = copie.db_path_absolute
        assert cible.exists()

    assert not cible.exists(), "la copie est supprimee a la sortie"
    assert not cible.parent.exists(), "et le repertoire temporaire avec elle"


def test_une_copie_survit_a_l_exception_et_son_chemin_se_dit(
    isolated_settings: Settings, caplog
) -> None:
    """**La seule raison de garder une copie est qu'on va la regarder.**

    Supprimer sur exception retirerait la piece a conviction au moment precis ou
    elle sert. Elle survit donc, et le chemin est **annonce** — une copie
    conservee sans son adresse est un fichier perdu de plus dans `/tmp`, ce qui
    est exactement le defaut qu'on corrige.
    """
    db.run_migrations(isolated_settings)
    with (
        caplog.at_level(logging.WARNING, logger="myassistantbet.db"),
        pytest.raises(ZeroDivisionError),
        db.scratch_copy(isolated_settings) as copie,
    ):
        cible = copie.db_path_absolute
        raise ZeroDivisionError("mesure interrompue")

    assert cible.exists(), "la copie reste a examiner"
    assert str(cible) in caplog.text, "et le journal dit ou elle est"
    shutil.rmtree(cible.parent, ignore_errors=True)


def test_une_copie_gardee_se_declare_dans_l_appel(isolated_settings: Settings, caplog) -> None:
    """Relire la copie apres coup est un cas **minoritaire**, et il doit se voir
    la ou on appelle — pas dans le comportement par defaut."""
    db.run_migrations(isolated_settings)
    with (
        caplog.at_level(logging.INFO, logger="myassistantbet.db"),
        db.scratch_copy(isolated_settings, keep=True) as copie,
    ):
        cible = copie.db_path_absolute

    assert cible.exists(), "l'appel l'a demandee"
    assert str(cible) in caplog.text
    shutil.rmtree(cible.parent, ignore_errors=True)


def test_un_chemin_choisi_par_l_appelant_ne_se_supprime_pas(
    isolated_settings: Settings, tmp_path
) -> None:
    """**On ne supprime que ce qu'on a cree.** Un `into=` est un repertoire de
    l'appelant : y faire le menage effacerait ce qu'il y a mis a cote."""
    db.run_migrations(isolated_settings)
    voisin = tmp_path / "a-garder.txt"
    voisin.write_text("releve", encoding="utf-8")

    with db.scratch_copy(isolated_settings, into=tmp_path / "copie.db") as copie:
        cible = copie.db_path_absolute

    assert cible.exists(), "le chemin est celui de l'appelant, il en dispose"
    assert voisin.exists(), "et rien d'autre n'a ete touche"


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
