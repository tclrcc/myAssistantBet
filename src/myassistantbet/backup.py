"""Sauvegarde de la base SQLite, avec rotation.

Utilise `VACUUM INTO`, qui produit une copie **coherente et compactee** meme
pendant que l'application ecrit — contrairement a une copie de fichier, qui
laisserait de cote le journal WAL et pourrait livrer une base corrompue.

S'utilise en ligne de commande :

    uv run python -m myassistantbet.backup
    uv run myassistantbet-backup --keep-days 14
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

#: `VACUUM INTO` n'existe que depuis SQLite 3.27 (2019).
MIN_SQLITE = (3, 27, 0)

BACKUP_PREFIX = "myassistantbet-"
BACKUP_SUFFIX = ".db"


class BackupError(RuntimeError):
    """La sauvegarde n'a pas pu etre produite."""


def _check_sqlite() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise BackupError(
            f"SQLite {'.'.join(map(str, MIN_SQLITE))} minimum requis pour VACUUM INTO "
            f"(version presente : {sqlite3.sqlite_version})."
        )


def backup_name(moment: datetime | None = None) -> str:
    stamp = (moment or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"


def list_backups(directory: Path) -> list[Path]:
    """Sauvegardes existantes, de la plus ancienne a la plus recente."""
    if not directory.is_dir():
        return []
    found = directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
    return sorted(found, key=lambda path: (path.stat().st_mtime, path.name))


def create_backup(settings: Settings | None = None, moment: datetime | None = None) -> Path:
    """Ecrit une copie coherente de la base. Renvoie son chemin."""
    settings = settings or get_settings()
    _check_sqlite()

    source = settings.db_path_absolute
    if not source.is_file():
        raise BackupError(f"Base introuvable : {source}")

    directory = settings.backup_dir_absolute
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / backup_name(moment)
    if target.exists():
        raise BackupError(f"Une sauvegarde porte deja ce nom : {target}")

    conn = sqlite3.connect(source)
    try:
        # Le chemin ne peut pas etre passe en parametre lie dans un VACUUM :
        # on echappe donc les apostrophes a la main.
        escaped = str(target).replace("'", "''")
        conn.execute(f"VACUUM INTO '{escaped}'")
    except sqlite3.Error as exc:
        target.unlink(missing_ok=True)
        raise BackupError(f"Echec de la sauvegarde : {exc}") from exc
    finally:
        conn.close()

    logger.info("Sauvegarde ecrite : %s (%d octets)", target, target.stat().st_size)
    return target


def rotate(directory: Path, keep_days: int, now: datetime | None = None) -> list[Path]:
    """Supprime les sauvegardes plus vieilles que `keep_days`. Renvoie les supprimees.

    La sauvegarde la plus recente n'est **jamais** supprimee, meme si elle a
    depasse l'age limite : sans cette garde, une interruption prolongee des
    sauvegardes finirait par effacer la derniere copie existante.
    """
    backups = list_backups(directory)
    if len(backups) <= 1:
        return []

    now = now or datetime.now(UTC)
    cutoff = now.timestamp() - keep_days * 86400
    removed = []
    for path in backups[:-1]:
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed.append(path)
            logger.info("Sauvegarde expiree supprimee : %s", path.name)
    return removed


def run(
    settings: Settings | None = None,
    keep_days: int | None = None,
    moment: datetime | None = None,
) -> tuple[Path, list[Path]]:
    """Sauvegarde puis rotation. Renvoie (sauvegarde creee, sauvegardes supprimees)."""
    settings = settings or get_settings()
    created = create_backup(settings, moment)
    removed = rotate(
        settings.backup_dir_absolute,
        keep_days if keep_days is not None else settings.backup_keep_days,
        moment,
    )
    return created, removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sauvegarde la base SQLite de MyAssistantBet.")
    parser.add_argument(
        "--keep-days",
        type=int,
        default=None,
        help="Nombre de jours de sauvegardes conservees (defaut : BACKUP_KEEP_DAYS).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    try:
        created, removed = run(keep_days=args.keep_days)
    except BackupError as exc:
        logger.error("%s", exc)
        return 1

    print(f"Sauvegarde : {created}")
    if removed:
        print(f"Supprimees : {', '.join(path.name for path in removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
