"""Verifier que la journalisation des rejets est branchee partout.

**Une table de rejets qui reste vide ne prouve rien.** Elle peut vouloir dire
« rien ne s'est perdu » ou « le chemin qui devait ecrire ici n'y ecrit pas », et
les deux se ressemblent trait pour trait — c'est le defaut caracteristique du
projet, rencontre pour la sixieme fois.

Cette commande injecte un exemplaire **malforme** de chacun des quatre formats
structures sur chacun des chemins d'import recenses, et **echoue** si l'un
d'eux ne produit pas de rejet. Elle tourne en integration continue.

    uv run myassistantbet-selfcheck

Elle travaille dans une **base temporaire**, jamais sur la base servie : elle
ecrit des selections volontairement cassees, et le projet a deja paye une fois
d'avoir laisse un `TestClient` appliquer une migration sur la production.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from . import db
from .config import Settings, get_settings
from .services import board as board_service
from .services import imports_raw, picks_import
from .services import ingestion as ingestion_service
from .services.manual import build, save

logger = logging.getLogger(__name__)

#: Les champs caches du formulaire d'import, tels que le navigateur les
#: renverrait. Passer par le rendu reel plutot que par l'objet : c'est le chemin
#: complet qu'on verifie, et un champ oublie cote gabarit ne se verrait pas.
_HIDDEN = re.compile(r'name="([a-z_0-9]+)"\s+value="([^"]*)"')

#: Un exemplaire **malforme** de chaque format structure, et le type de rejet
#: qu'il doit produire. Malforme et non absent : un format absent ne prouve rien
#: sur la journalisation, c'est un format **recu et refuse** qu'il faut voir
#: arriver en base.
BROKEN: dict[str, tuple[str, str]] = {
    "conf": (
        '```conf\n{"match": "M1", "confiance": 4, "faits": [],}\n```\n',
        ingestion_service.CONF,
    ),
    "combo": (
        '```combo\n{"type": "moyen", "jambes": ["M1"]}\n```\n',
        ingestion_service.COMBO,
    ),
    "score_sets": (
        "| Personne Inconnue – Autre Inconnu | 2-0 | |\n",
        ingestion_service.SCORE_SETS,
    ),
    "selection": (
        "| 2 | Lyon – Adv Lyon | O/U 2.5 | Over 2.5 | 1.90 | 🟢 SAFE | 4 |\n",
        ingestion_service.SELECTION,
    ),
}

#: Le tableau minimal auquel les exemplaires malformes s'ajoutent. Sans lui,
#: `parse_table` refuse tout le collage et le rejet observe ne dirait rien du
#: format teste.
TABLE = (
    "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
    "|---|-------|--------|-----------|------|--------|--------|\n"
    "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
)


@dataclass
class Check:
    """Un format, un chemin, et ce qui est arrive."""

    path: str
    fmt: str
    seen: bool
    detail: str = ""

    @property
    def line(self) -> str:
        marque = "ok " if self.seen else "MANQUE"
        return f"  {marque}  {self.path:<12} {self.fmt:<12} {self.detail}"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.seen]

    @property
    def lines(self) -> list[str]:
        out = [f"{len(self.checks)} contrôle(s), {len(self.failures)} manque(s)"]
        out += [check.line for check in self.checks]
        if self.failures:
            out.append(
                "Un chemin d'import ne journalise pas ses rejets : une table vide y "
                "voudrait dire « rien ne s'est perdu » alors qu'elle veut dire « rien "
                "n'écrit ici »."
            )
        return out


def _seed(settings: Settings) -> int:
    """Une session minimale : deux matchs a venir et un prompt archive."""
    session_id = 0
    for nom in ("Lyon", "Nice"):
        event_id = save(
            build(
                "football",
                "Match amical",
                nom,
                f"Adv {nom}",
                "2099-01-01",
                "20:45",
                f"{nom} 1.45",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        session_id = board_service.toggle_selection(event_id, True, settings)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'selfcheck', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=settings,
    )
    return session_id


def _by_form(session_id: int, raw: str, settings: Settings) -> set[str]:
    """Le chemin ordinaire : **les deux vraies routes**, apercu puis import.

    Appeler `build_preview` seul ne verifierait que la moitie du chemin : les
    rejets d'ecriture — une ligne refusee par une garde — ne naissent qu'a
    l'import. C'est justement ce demi-controle qui a laisse le rejeu muet, et le
    banc l'a attrape.

    Le `TestClient` est importe ici et pas en tete : c'est un outil de
    verification, il n'a rien a faire dans le chemin servi.
    """
    from fastapi.testclient import TestClient

    from .main import app

    with TestClient(app) as client:
        apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": raw})
        champs = dict(_HIDDEN.findall(apercu.text))
        donnees = {
            "rejects": unescape(champs.get("rejects", "[]")),
            "import_id": champs.get("import_id", ""),
        }
        # Toutes les lignes proposees sont cochees : une ligne decochee ne passe
        # pas par l'ecriture, donc ne dirait rien de sa journalisation.
        for index, ligne in enumerate(picks_import.build_preview(session_id, raw, settings).picks):
            numero = ligne.index
            donnees |= {
                f"keep_{numero}": "1",
                f"event_{numero}": str(ligne.event_id or ""),
                f"market_{numero}": ligne.market,
                f"selection_{numero}": ligne.selection,
                f"tier_{numero}": ligne.tier or "safe",
                f"price_{numero}": ligne.price,
            }
            _ = index
        client.post(f"/history/{session_id}/picks/import", data=donnees)

    return {
        str(row["block_type"])
        for row in db.query("SELECT block_type FROM ingestion_rejects", settings=settings)
    }


def _by_replay(session_id: int, raw: str, settings: Settings) -> set[str]:
    """Le chemin du rejeu : un collage conserve, relu par la commande."""
    from .replay import replay

    import_id = imports_raw.record(session_id, raw, imports_raw.REPLAY, settings)
    if import_id is None:
        return set()
    return {
        reject.block_type for reject in replay(import_id, write=True, settings=settings).rejects
    }


#: Les chemins d'import recenses. **Un chemin ajoute sans entree ici passerait
#: au travers**, et c'est le seul point faible connu de cette verification : elle
#: prouve que les chemins declares journalisent, jamais qu'ils sont tous
#: declares. La regle de `CONTRIBUTING.md` en tient lieu.
PATHS: dict[str, Callable[[int, str, Settings], set[str]]] = {
    "formulaire": _by_form,
    "rejeu": _by_replay,
}


def run(settings: Settings | None = None) -> Report:
    """Injecte chaque format malforme sur chaque chemin. Rend ce qui manque."""
    report = Report()
    for chemin, executer in PATHS.items():
        for nom, (casse, attendu) in BROKEN.items():
            with _temporary() as temporaire:
                session_id = _seed(temporaire)
                vus = executer(session_id, TABLE + "\n" + casse, temporaire)
                en_base = {
                    row["block_type"]
                    for row in db.query(
                        "SELECT block_type FROM ingestion_rejects", settings=temporaire
                    )
                }
            report.checks.append(
                Check(
                    path=chemin,
                    fmt=nom,
                    seen=attendu in vus and attendu in en_base,
                    detail=f"attendu {attendu}, vu {sorted(en_base) or 'rien'}",
                )
            )
    _ = settings
    return report


class _temporary:
    """Une base jetable, migree, le temps d'un controle.

    **Jamais la base servie** : cette commande ecrit des selections
    volontairement cassees, et le projet a deja paye une fois d'avoir laisse un
    `TestClient` appliquer une migration sur la production.
    """

    def __enter__(self) -> Settings:
        self.dossier = Path(tempfile.mkdtemp())
        # `DB_PATH` **et** le cache de configuration : les routes appellent
        # `get_settings()` elles-memes, et sans cette bascule le controle
        # ecrirait ses selections cassees dans la base servie. Le projet a deja
        # paye une fois d'avoir laisse un `TestClient` toucher la production.
        self.precedent = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = str(self.dossier / "selfcheck.db")
        get_settings.cache_clear()
        self.settings = get_settings()
        db.run_migrations(self.settings)
        return self.settings

    def __exit__(self, *exc: object) -> None:
        if self.precedent is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.precedent
        get_settings.cache_clear()
        shutil.rmtree(self.dossier, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie que chaque chemin d'import journalise ses rejets. "
            "Travaille dans une base temporaire."
        )
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")
    report = run()
    for line in report.lines:
        print(line, file=sys.stderr if report.failures else sys.stdout)
    return 1 if report.failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
