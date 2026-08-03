"""Acces SQLite : connexion, PRAGMAs, migrations et helpers de requetage."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

MIGRATION_PATTERN = re.compile(r"^(\d+)_[\w-]+\.sql$")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
"""


def utcnow() -> str:
    """Horodatage ISO 8601 UTC, format unique de stockage dans toute la base."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")


@contextmanager
def connect(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    """Ouvre une connexion configuree. Commit en sortie, rollback en cas d'erreur."""
    settings = settings or get_settings()
    path = settings.db_path_absolute
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        _configure(conn)
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        _rollback(conn)
        raise
    finally:
        conn.close()


def _rollback(conn: sqlite3.Connection) -> None:
    """Annule la transaction en cours, si elle existe encore.

    SQLite peut avoir deja annule de lui-meme : tenter un ROLLBACK inconditionnel
    leverait une seconde erreur qui masquerait la premiere.
    """
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def query(
    sql: str, params: Sequence[Any] | dict[str, Any] = (), *, settings: Settings | None = None
) -> list[sqlite3.Row]:
    """Execute un SELECT et renvoie toutes les lignes."""
    with connect(settings) as conn:
        return list(conn.execute(sql, params))


def query_one(
    sql: str, params: Sequence[Any] | dict[str, Any] = (), *, settings: Settings | None = None
) -> sqlite3.Row | None:
    """Execute un SELECT et renvoie la premiere ligne, ou None."""
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchone()


def execute(
    sql: str, params: Sequence[Any] | dict[str, Any] = (), *, settings: Settings | None = None
) -> int:
    """Execute une ecriture et renvoie le nombre de lignes affectees."""
    with connect(settings) as conn:
        return conn.execute(sql, params).rowcount


def discover_migrations(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    """Liste les migrations `NNN_nom.sql` du dossier, triees par version croissante."""
    if not migrations_dir.is_dir():
        return []

    found: list[tuple[int, str, Path]] = []
    seen: dict[int, Path] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise ValueError(f"Nom de migration invalide : {path.name} (attendu NNN_nom.sql)")
        version = int(match.group(1))
        if version in seen:
            raise ValueError(
                f"Version de migration dupliquee {version} : {seen[version].name} et {path.name}"
            )
        seen[version] = path
        found.append((version, path.name, path))

    return sorted(found, key=lambda item: item[0])


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.executescript(SCHEMA_MIGRATIONS_DDL)
    return {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}


def run_migrations(
    settings: Settings | None = None, migrations_dir: Path | None = None
) -> list[str]:
    """Applique les migrations non encore appliquees. Renvoie les noms appliques.

    Chaque migration est jouee dans sa propre transaction : une migration qui
    echoue laisse la base dans l'etat de la derniere migration reussie.
    """
    settings = settings or get_settings()
    migrations_dir = migrations_dir or settings.migrations_dir
    migrations = discover_migrations(migrations_dir)
    if not migrations:
        logger.warning("Aucune migration trouvee dans %s", migrations_dir)
        return []

    path = settings.db_path_absolute
    path.parent.mkdir(parents=True, exist_ok=True)

    applied: list[str] = []
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        _configure(conn)
        done = applied_versions(conn)
        for version, name, file_path in migrations:
            if version in done:
                continue
            sql = file_path.read_text(encoding="utf-8")
            try:
                # Le BEGIN doit faire partie du script : executescript() valide
                # toute transaction en cours avant de s'executer.
                conn.executescript(f"BEGIN;\n{sql}")
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, utcnow()),
                )
                conn.execute("COMMIT")
            except Exception:
                _rollback(conn)
                logger.exception("Echec de la migration %s", name)
                raise
            applied.append(name)
            logger.info("Migration appliquee : %s", name)
    finally:
        conn.close()

    return applied


def list_tables(settings: Settings | None = None) -> list[str]:
    """Noms des tables utilisateur, hors tables internes SQLite."""
    rows = query(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name",
        settings=settings,
    )
    return [row["name"] for row in rows]


def health(settings: Settings | None = None) -> dict[str, Any]:
    """Etat de la base : accessibilite, version de schema, tables presentes."""
    settings = settings or get_settings()
    try:
        with connect(settings) as conn:
            version_row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            tables = [
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return {
            "ok": True,
            "path": str(settings.db_path_absolute),
            "schema_version": version_row["v"] if version_row else None,
            "journal_mode": journal,
            "tables": tables,
        }
    except sqlite3.Error as exc:
        logger.exception("Base inaccessible")
        return {
            "ok": False,
            "path": str(settings.db_path_absolute),
            "error": str(exc),
        }
