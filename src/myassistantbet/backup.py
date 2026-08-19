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
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

#: `VACUUM INTO` n'existe que depuis SQLite 3.27 (2019).
MIN_SQLITE = (3, 27, 0)

BACKUP_PREFIX = "myassistantbet-"
BACKUP_SUFFIX = ".db"

#: Le prefixe que porte **tout** repertoire temporaire cree par le projet.
#:
#: **Il existe pour rendre l'appartenance explicite, et c'est toute la garde.**
#: `tempfile.mkdtemp()` sans prefixe rend `/tmp/tmpXXXXXXXX`, indiscernable de
#: celui de n'importe quel autre programme de la machine — et une purge qui
#: effacerait `tmp*` toucherait ce que le projet n'a pas cree. Mesure du
#: 19/08/2026 : **208 repertoires anonymes, 63 Mo**, tous laisses par
#: `tests/helpers.migre_jusqu_a`, et impossibles a reclamer par une regle sure
#: puisque rien dans leur nom ne les rattache a ce depot.
TEMP_PREFIX = "myassistantbet-"

#: Age au-dela duquel un artefact temporaire du projet est purge.
#:
#: **Vingt-quatre heures et non une**, parce qu'une suite de tests dure quatre
#: minutes mais qu'une session de travail garde ses copies ouvertes toute une
#: journee. En dessous, la purge retirerait un repertoire sous les pieds d'un
#: travail en cours ; au-dessus, elle laisserait passer une nuit de plus.
TEMP_MAX_HOURS = 24


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


@dataclass(frozen=True)
class Purge:
    """Ce qu'une purge d'artefacts temporaires a retire."""

    removed: int = 0
    freed: int = 0
    kept: int = 0

    @property
    def line(self) -> str:
        return (
            f"{self.removed} artefact(s) temporaire(s) purge(s), "
            f"{self.freed / 1_048_576:.1f} Mo liberes · {self.kept} conserve(s)"
        )


def _size(path: Path) -> int:
    """Les octets d'un fichier ou d'une arborescence. Ce qui ne se lit pas vaut 0."""
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def purge_temp(
    directory: Path | None = None,
    max_hours: int = TEMP_MAX_HOURS,
    now: datetime | None = None,
) -> Purge:
    """Retire les artefacts temporaires **du projet** vieux de plus de `max_hours`.

    **Le critere d'appartenance est le prefixe, jamais un motif large.** C'est la
    seule regle qui ne puisse pas emporter le travail d'un autre programme, et
    ce repertoire est partage par toute la machine : `pytest`, `uv`, `ruff` et
    les sessions de travail y ecrivent aussi. Un `tmp*` aurait tout pris.

    **Les repertoires de `pytest` ne sont pas touches**, et ce n'est pas un
    oubli : `pytest` fait sa propre rotation — il garde les trois dernieres
    executions — et les retirer pendant qu'une suite tourne lui retirerait sa
    base sous les pieds. La convention de `CONTRIBUTING.md` le dit deja.

    Un artefact qu'on ne sait pas supprimer est **compte et laisse** : une purge
    qui echoue en silence dirait « rien a faire » quand elle veut dire « je n'ai
    pas pu », et c'est le defaut caracteristique de ce projet.
    """
    racine = directory or Path(tempfile.gettempdir())
    limite = (now or datetime.now(UTC)).timestamp() - max_hours * 3600
    found = Purge()
    if not racine.is_dir():
        return found
    removed = freed = kept = 0
    for chemin in sorted(racine.glob(f"{TEMP_PREFIX}*")):
        try:
            age = chemin.stat().st_mtime
        except OSError:
            kept += 1
            continue
        if age >= limite:
            kept += 1
            continue
        taille = _size(chemin)
        try:
            if chemin.is_file():
                chemin.unlink()
            else:
                shutil.rmtree(chemin)
        except OSError as exc:
            logger.warning("Artefact temporaire non supprime : %s (%s)", chemin.name, exc)
            kept += 1
            continue
        removed += 1
        freed += taille
    found = Purge(removed=removed, freed=freed, kept=kept)
    if removed:
        logger.info("Purge des artefacts temporaires — %s", found.line)
    return found


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
