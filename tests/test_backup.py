from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from myassistantbet import backup, db
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


def _age(path: Path, days: float, reference: datetime | None = None) -> None:
    """Vieillit artificiellement un fichier de `days` jours.

    La reference par defaut est l'horloge reelle, ce qu'attendent les tests qui
    laissent `rotate` lire l'heure lui-meme. **Un test qui fige le moment de la
    rotation doit figer celui-ci aussi** : sinon l'ecart entre l'horloge reelle
    et `MOMENT` grandit chaque jour et finit par decider seul du resultat.
    """
    stamp = ((reference or datetime.now(UTC)) - timedelta(days=days)).timestamp()
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
    _age(expiree, 30, reference=MOMENT)

    created, removed = run(migrated, keep_days=7, moment=MOMENT + timedelta(hours=2))

    assert created.is_file()
    assert removed == [expiree]


def test_run_utilise_la_configuration(migrated: Settings, monkeypatch) -> None:
    monkeypatch.setattr(migrated, "backup_keep_days", 1)
    expiree = create_backup(migrated, MOMENT)
    _age(expiree, 2, reference=MOMENT)

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


# -- La purge des artefacts temporaires ---------------------------------------
#
# **Le disque plein est la seule panne d'exploitation que rien ne surveille.**
# `/tmp` est un tmpfs de 5,8 Go — de la mémoire vive — et il est passé par 96 %,
# 68 % puis 78 % en trois sessions. SQLite y pose ses fichiers temporaires : un
# `ENOSPC` ferait échouer un `VACUUM INTO` sur la base servie, et l'erreur ne
# ressemble pas à ce qu'elle est.


def _vieux(chemin: Path, heures: int) -> Path:
    """Vieillit un artefact de `heures`, sans attendre."""
    quand = datetime.now(UTC).timestamp() - heures * 3600
    os.utime(chemin, (quand, quand))
    return chemin


def test_la_purge_retire_les_artefacts_du_projet_expires(tmp_path: Path) -> None:
    """Le cas ordinaire : un répertoire du projet, vieux de plus de 24 h."""
    vieux = tmp_path / f"{backup.TEMP_PREFIX}ancien"
    vieux.mkdir()
    (vieux / "001_init.sql").write_text("x" * 2048, encoding="utf-8")
    _vieux(vieux, heures=30)

    purge = backup.purge_temp(tmp_path)

    assert purge.removed == 1
    assert purge.freed >= 2048
    assert not vieux.exists()


def test_un_artefact_hors_perimetre_survit(tmp_path: Path) -> None:
    """**Le critère d'appartenance est le préfixe, jamais un motif large.**

    Ce répertoire est partagé par toute la machine : `pytest`, `uv`, `ruff` et
    les sessions de travail y écrivent aussi. Un `tmp*` aurait tout pris — et
    c'est précisément pourquoi les 208 répertoires anonymes laissés par
    `migre_jusqu_a` n'étaient **pas** réclamables par une règle sûre : rien dans
    leur nom ne les rattachait à ce dépôt.
    """
    for nom in ("tmpXXXXXXXX", "pytest-of-ubuntu", "autre-programme", "myassistantbet"):
        etranger = tmp_path / nom
        etranger.mkdir()
        (etranger / "donnee").write_text("précieux", encoding="utf-8")
        _vieux(etranger, heures=999)

    purge = backup.purge_temp(tmp_path)

    assert purge.removed == 0, "aucun de ces noms ne porte le préfixe du projet"
    assert all((tmp_path / nom).exists() for nom in ("tmpXXXXXXXX", "pytest-of-ubuntu"))
    # `myassistantbet` sans tiret n'est pas `myassistantbet-` : le préfixe est
    # exact, et un préfixe qui déborde vaut un motif large.
    assert (tmp_path / "myassistantbet").exists()


def test_un_artefact_de_moins_de_24_h_survit(tmp_path: Path) -> None:
    """**Une purge trop courte retire un répertoire sous les pieds d'un travail
    en cours.** Une suite de tests dure quatre minutes, mais une session de
    travail garde ses copies ouvertes toute une journée."""
    recent = tmp_path / f"{backup.TEMP_PREFIX}en-cours"
    recent.mkdir()
    (recent / "base.db").write_text("x", encoding="utf-8")
    _vieux(recent, heures=23)

    purge = backup.purge_temp(tmp_path)

    assert (purge.removed, purge.kept) == (0, 1)
    assert recent.exists()


def test_la_purge_compte_ce_qu_elle_laisse(tmp_path: Path) -> None:
    """**Un artefact qu'on ne sait pas retirer est compté et laissé.** Une purge
    qui échouerait en silence dirait « rien à faire » quand elle veut dire « je
    n'ai pas pu » — le défaut caractéristique de ce projet."""
    for heures, nom in ((30, "vieux"), (1, "frais")):
        chemin = tmp_path / f"{backup.TEMP_PREFIX}{nom}"
        chemin.mkdir()
        _vieux(chemin, heures=heures)

    purge = backup.purge_temp(tmp_path)

    assert (purge.removed, purge.kept) == (1, 1)
    assert "1 artefact(s) temporaire(s) purge(s)" in purge.line
    assert "1 conserve(s)" in purge.line


def test_un_repertoire_absent_ne_fait_rien(tmp_path: Path) -> None:
    """Une machine sans répertoire temporaire n'est pas une erreur."""
    assert backup.purge_temp(tmp_path / "inexistant").removed == 0


def test_les_deux_repertoires_temporaires_du_projet_portent_le_prefixe() -> None:
    """**Sans le préfixe, la purge n'a rien à reconnaître.**

    C'est la moitié qui manquait : `tempfile.mkdtemp()` rend
    `/tmp/tmpXXXXXXXX`, indiscernable de n'importe quel autre programme. Les 208
    répertoires anonymes mesurés le 19/08/2026 — 63 Mo — venaient tous de là.
    """
    for chemin in (
        Path("src/myassistantbet/selfcheck.py"),
        Path("tests/helpers.py"),
    ):
        source = chemin.read_text(encoding="utf-8")
        assert "mkdtemp(prefix=TEMP_PREFIX)" in source, (
            f"{chemin} crée un répertoire temporaire sans le préfixe du projet"
        )
