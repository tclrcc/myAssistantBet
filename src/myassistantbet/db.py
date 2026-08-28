"""Acces SQLite : connexion, PRAGMAs, migrations et helpers de requetage."""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import sys
import tempfile
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


@contextmanager
def scratch_copy(
    settings: Settings | None = None,
    into: Path | None = None,
    keep: bool = False,
) -> Iterator[Settings]:
    """Une copie **jetable et inscriptible** de la base servie, et ses parametres.

    Le pendant du `VACUUM INTO` de lecture, pour tout ce qui doit ecrire —
    migrer, rejouer un import, mesurer un correctif. La regle du depot n'avait
    que la moitie lecture, et c'est par la moitie manquante qu'une migration est
    partie sur la production le 21/08/2026.

    `VACUUM INTO` et non une copie de fichier : en mode WAL, copier le `.db` seul
    livrerait une base incomplete — meme raison que `backup.py`.

    Rend des `Settings` **derives** plutot que de poser une variable
    d'environnement : c'est le detour par l'environnement qui a echoue, et un
    objet passe en argument ne peut pas se tromper de nom.

    ## Elle se jette, et pendant une semaine elle ne se jetait pas

    Le mot « jetable » etait dans ce docstring des le premier jour, et **rien ne
    jetait**. Chaque appel laissait son `mkdtemp` derriere lui. Trouve par
    accident le 28/08/2026 : `/tmp`, un tmpfs de 5,8 Go, sature, et **884 tests
    en erreur sur `database or disk is full`** — des tests qui n'avaient rien a
    se reprocher. Deux repertoires de 392 Mo anterieurs a la session qui l'a
    trouve etablissent que c'est le mecanisme et non un incident isole.

    **Un garde ecrit pour proteger la base servie finissait par casser le banc**,
    ce qui contredit sa raison d'etre : un banc rouge pour une cause sans rapport
    avec le code est ce qui fait defaire un correctif juste, et la prochaine
    personne a le rencontrer cherchera dans le code plutot que dans `/tmp`.

    ## Ce qui est supprime, et ce qui ne l'est pas

    · **a la sortie normale** : le repertoire temporaire, entier. C'est le
      comportement par defaut, et il n'a pas besoin d'etre demande ;
    · **sur exception** : rien, et le chemin passe en `WARNING`. Supprimer la
      retirerait la piece a conviction au moment precis ou elle sert ;
    · **`keep=True`** : rien, et le chemin passe en `INFO`. Relire une copie
      apres coup est un cas minoritaire, et il doit **se voir dans l'appel** ;
    · **`into=`** : rien, jamais. C'est un chemin de l'appelant — **on ne
      supprime que ce qu'on a cree**, sinon le menage emporterait ce qu'il a mis
      a cote.

    Dans les trois cas ou la copie survit, **le chemin est annonce** : une copie
    conservee sans son adresse est un fichier perdu de plus dans `/tmp`, donc le
    defaut qu'on corrige sous un autre nom.
    """
    settings = settings or get_settings()
    dossier = Path(tempfile.mkdtemp(prefix="mab-copie-")) if into is None else None
    cible = Path(into) if into is not None else dossier / "copie.db"  # type: ignore[union-attr]
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path_absolute)
        try:
            conn.execute("VACUUM INTO ?", (str(cible),))
        finally:
            conn.close()
    except BaseException:
        # La copie n'a pas abouti : il n'y a rien a examiner, et le repertoire
        # que nous venons de creer ne doit pas survivre a son echec.
        if dossier is not None:
            shutil.rmtree(dossier, ignore_errors=True)
        raise
    logger.info("Copie de travail : %s", cible)
    try:
        yield settings.model_copy(update={"db_path": cible})
    except BaseException:
        logger.warning("Copie de travail conservee apres erreur : %s", cible)
        raise
    if dossier is not None and not keep:
        shutil.rmtree(dossier, ignore_errors=True)
    else:
        logger.info("Copie de travail conservee : %s", cible)


class MigrationRefused(RuntimeError):
    """Une migration a ete demandee hors du demarrage de l'application."""


def _under_test() -> bool:
    """Vrai sous pytest. La suite migre des bases isolees, par construction.

    Lu sur `sys.modules` et non sur `PYTEST_CURRENT_TEST`, qui n'est pose que
    pendant l'execution d'un test : une fixture de portee session tombe entre
    deux, et se ferait refuser une migration parfaitement legitime.
    """
    return "pytest" in sys.modules


def run_migrations(
    settings: Settings | None = None,
    migrations_dir: Path | None = None,
    *,
    deliberate: bool = False,
) -> list[str]:
    """Applique les migrations non encore appliquees. Renvoie les noms appliques.

    Chaque migration est jouee dans sa propre transaction : une migration qui
    echoue laisse la base dans l'etat de la derniere migration reussie.

    **`deliberate` est une garde, et elle a ete payee.** Le 21/08/2026, un script
    de controle a cru isoler sa base par une variable d'environnement inexistante
    (`MYASSISTANTBET_DB`, quand le champ s'appelle `db_path` donc `DB_PATH`) :
    l'override n'a rien fait, `get_settings()` a rendu les parametres servis, et
    la migration du jour est partie sur la **base de production**. Le projet
    avait deja paye une fois d'avoir laisse un `TestClient` toucher la
    production, et le commentaire de `selfcheck` le disait.

    La regle du depot — « toute lecture se fait sur une copie » — avait son
    reflexe pour lire (`VACUUM INTO`) et **aucun equivalent pour ecrire**. Elle
    en a un : `scratch_copy()`. Ici, l'appel se declare.

    L'exemption sous pytest n'est pas une porte laissee ouverte : la suite migre
    une base temporaire par test, et exiger le drapeau sur trente fixtures aurait
    fait du bruit sans rien garder de plus.

    Ce que la garde **n'attrape pas**, et il faut le savoir : un mauvais nom de
    variable d'environnement reste sans effet et sans message. `extra="forbid"`
    ne couvre que les cles inconnues d'un `.env`, pas celles de l'environnement —
    mesure du 21/08/2026, pydantic-settings ne regarde l'environnement que pour
    les champs declares. C'est donc **cette garde-ci** qui porte le cas, et elle
    seule.
    """
    settings = settings or get_settings()
    if not deliberate and not _under_test():
        raise MigrationRefused(
            f"run_migrations() refuse : appel non declare sur {settings.db_path_absolute}. "
            "Un script d'exploration ne migre pas une base servie — prends une copie "
            "jetable avec `with db.scratch_copy() as copie:`. Si l'appel est bien celui "
            "du demarrage ou "
            "d'un deploiement, passe deliberate=True."
        )
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
