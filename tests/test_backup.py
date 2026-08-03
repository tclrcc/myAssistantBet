from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from myassistantbet import db
from myassistantbet.backup import (
    BackupError,
    backup_name,
    create_backup,
    list_backups,
    main,
    rotate,
    run,
)
from myassistantbet.config import Settings

MOMENT = datetime(2026, 8, 4, 6, 30, tzinfo=UTC)


def _age(path: Path, days: float) -> None:
    """Vieillit artificiellement un fichier de `days` jours."""
    stamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


# -- Creation ---------------------------------------------------------------


def test_nom_horodate() -> None:
    assert backup_name(MOMENT) == "myassistantbet-20260804-063000.db"


def test_sauvegarde_creee(migrated: Settings) -> None:
    target = create_backup(migrated, MOMENT)

    assert target.is_file()
    assert target.name == "myassistantbet-20260804-063000.db"
    assert target.parent == migrated.backup_dir_absolute


def test_le_dossier_est_cree_au_besoin(migrated: Settings) -> None:
    assert not migrated.backup_dir_absolute.exists()

    create_backup(migrated, MOMENT)

    assert migrated.backup_dir_absolute.is_dir()


def test_la_sauvegarde_contient_le_schema_complet(migrated: Settings) -> None:
    target = create_backup(migrated, MOMENT)

    conn = sqlite3.connect(target)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        conn.close()

    assert {"events", "odds", "picks", "tiers", "team_aliases"} <= tables


def test_les_donnees_non_encore_checkpointees_sont_incluses(migrated: Settings) -> None:
    """Le mode WAL est le piege : une copie de fichier ratrait ces lignes."""
    db.execute("INSERT INTO sports (key, label) VALUES ('handball', 'Handball')", settings=migrated)

    target = create_backup(migrated, MOMENT)

    conn = sqlite3.connect(target)
    try:
        rows = conn.execute("SELECT label FROM sports WHERE key = 'handball'").fetchall()
    finally:
        conn.close()
    assert rows == [("Handball",)]


def test_base_absente(isolated_settings: Settings) -> None:
    with pytest.raises(BackupError, match="Base introuvable"):
        create_backup(isolated_settings, MOMENT)


def test_collision_de_nom(migrated: Settings) -> None:
    create_backup(migrated, MOMENT)

    with pytest.raises(BackupError, match="porte deja ce nom"):
        create_backup(migrated, MOMENT)


def test_chemin_avec_apostrophe(tmp_path: Path, migrated: Settings, monkeypatch) -> None:
    # Un dossier nomme « L'archive » ne doit pas casser le VACUUM INTO.
    monkeypatch.setattr(migrated, "backup_dir", tmp_path / "L'archive")

    target = create_backup(migrated, MOMENT)

    assert target.is_file()
    assert "L'archive" in str(target)


# -- Rotation ---------------------------------------------------------------


def test_les_sauvegardes_recentes_sont_conservees(migrated: Settings) -> None:
    for day in range(3):
        path = create_backup(migrated, MOMENT + timedelta(hours=day))
        _age(path, day)

    removed = rotate(migrated.backup_dir_absolute, keep_days=7)

    assert removed == []
    assert len(list_backups(migrated.backup_dir_absolute)) == 3


def test_les_sauvegardes_expirees_sont_supprimees(migrated: Settings) -> None:
    ancienne = create_backup(migrated, MOMENT)
    _age(ancienne, 10)
    recente = create_backup(migrated, MOMENT + timedelta(hours=1))
    _age(recente, 1)

    removed = rotate(migrated.backup_dir_absolute, keep_days=7)

    assert removed == [ancienne]
    assert [path.name for path in list_backups(migrated.backup_dir_absolute)] == [recente.name]


def test_la_derniere_sauvegarde_n_est_jamais_supprimee(migrated: Settings) -> None:
    """Une interruption prolongee ne doit pas effacer la seule copie restante."""
    seule = create_backup(migrated, MOMENT)
    _age(seule, 400)

    removed = rotate(migrated.backup_dir_absolute, keep_days=7)

    assert removed == []
    assert seule.is_file()


def test_la_plus_recente_survit_meme_expiree(migrated: Settings) -> None:
    vieille = create_backup(migrated, MOMENT)
    _age(vieille, 100)
    moins_vieille = create_backup(migrated, MOMENT + timedelta(hours=1))
    _age(moins_vieille, 50)

    removed = rotate(migrated.backup_dir_absolute, keep_days=7)

    assert removed == [vieille]
    assert moins_vieille.is_file(), "la plus recente reste, meme au-dela du delai"


def test_rotation_sur_dossier_vide(migrated: Settings) -> None:
    assert rotate(migrated.backup_dir_absolute, keep_days=7) == []


def test_les_fichiers_etrangers_sont_ignores(migrated: Settings) -> None:
    create_backup(migrated, MOMENT)
    intrus = migrated.backup_dir_absolute / "notes.txt"
    intrus.write_text("a garder", encoding="utf-8")
    _age(intrus, 400)

    rotate(migrated.backup_dir_absolute, keep_days=7)

    assert intrus.is_file()


# -- Enchainement complet ---------------------------------------------------


def test_run_sauvegarde_puis_purge(migrated: Settings) -> None:
    expiree = create_backup(migrated, MOMENT)
    _age(expiree, 30)

    created, removed = run(migrated, keep_days=7, moment=MOMENT + timedelta(hours=2))

    assert created.is_file()
    assert removed == [expiree]


def test_run_utilise_la_configuration(migrated: Settings, monkeypatch) -> None:
    monkeypatch.setattr(migrated, "backup_keep_days", 1)
    expiree = create_backup(migrated, MOMENT)
    _age(expiree, 2)

    _, removed = run(migrated, moment=MOMENT + timedelta(hours=3))

    assert removed == [expiree]


def test_duree_de_retention_par_defaut() -> None:
    assert Settings(_env_file=None).backup_keep_days == 7


# -- Ligne de commande ------------------------------------------------------


def test_cli(migrated: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    code = main([])

    assert code == 0
    assert "Sauvegarde :" in capsys.readouterr().out
    assert len(list_backups(migrated.backup_dir_absolute)) == 1


def test_cli_signale_l_echec(
    isolated_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([])

    assert code == 1, "aucune base : la commande doit echouer proprement"


def test_cli_retention_personnalisee(migrated: Settings) -> None:
    expiree = create_backup(migrated, MOMENT)
    _age(expiree, 3)

    main(["--keep-days", "1"])

    assert not expiree.exists()
