"""Import de matchs depuis API-Football, pour ce que The Odds API ne sert pas.

Le board se remplit normalement par le scan (`services/scan.py`), qui interroge
The Odds API. Mais ce fournisseur ne couvre pas tout : les tours preliminaires
d'Europa League et de Conference League n'ont chez lui **aucun evenement**,
alors qu'API-Football les connait, les date et les nomme.

Ces matchs entrent donc par ici, sans cotes. Les cotes se saisissent a la main
(`services/manual.py`) : le prix jouable est celui de Betclic, qu'aucun des
deux fournisseurs ne donne pour ces rencontres.

Regle qui evite tout doublon a la racine : **on n'importe que ce que The Odds
API ne sert pas** (`api_active = 0`). Une competition servie par les deux
produirait deux fois le meme match, sous deux orthographes differentes et sans
moyen fiable de les rapprocher — « KFUM » et « KFUM Oslo » sont deja au-dessus
du seuil de rapprochement automatique.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.apifootball import APIFootballClient
from ..providers.base import ProviderError
from .scan import scan_window

logger = logging.getLogger(__name__)

#: Source portee par les evenements importes ici. Distincte de `api` (The Odds
#: API) et de `manual` : savoir d'ou vient un match explique pourquoi il n'a pas
#: de cotes, et evite de chercher une panne de scan la ou il n'y en a pas.
SOURCE = "apifootball"


@dataclass
class ImportReport:
    """Ce qu'un import a produit, y compris quand il n'a rien produit."""

    competition: str
    created: int = 0
    updated: int = 0
    served_elsewhere: bool = False
    error: str | None = None

    @property
    def note(self) -> str:
        """Phrase affichable. Ne tait jamais un refus ni une absence."""
        if self.error:
            return f"{self.competition} : {self.error}"
        if self.served_elsewhere:
            return (
                f"{self.competition} : deja servie par The Odds API, "
                "import inutile et source de doublons."
            )
        if not self.created and not self.updated:
            return f"{self.competition} : aucun match sur la fenetre."
        return f"{self.competition} : {self.created} match(s) ajoute(s), {self.updated} mis a jour."


def _competition(competition_id: int, settings: Settings) -> dict[str, Any] | None:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT c.id, c.label, c.active, c.api_active, c.apifootball_league_id, "
            "       c.sport_id, s.key AS sport_key "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id WHERE c.id = ?",
            (competition_id,),
        ).fetchone()
    return dict(row) if row else None


def _upsert(conn: Any, competition: dict[str, Any], fixture: dict[str, Any]) -> str:
    """Insere ou met a jour un match. Cle naturelle : l'identifiant du fixture.

    Relancer un import ne duplique rien. L'heure et les noms peuvent bouger
    d'une fois sur l'autre — un report de match est une information, pas une
    raison de creer une seconde ligne.
    """
    fixture_id = int((fixture.get("fixture") or {}).get("id"))
    teams = fixture.get("teams") or {}
    home = ((teams.get("home") or {}).get("name") or "").strip()
    away = ((teams.get("away") or {}).get("name") or "").strip()
    commence = (fixture.get("fixture") or {}).get("date")
    if not home or not away or not commence:
        return "ignore"

    commence_utc = datetime.fromisoformat(commence).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing = conn.execute(
        "SELECT id FROM events WHERE apifootball_fixture_id = ?", (fixture_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE events SET home = ?, away = ?, commence_time = ?, competition_id = ? "
            "WHERE id = ?",
            (home, away, commence_utc, competition["id"], existing["id"]),
        )
        return "updated"

    conn.execute(
        "INSERT INTO events (sport_id, competition_id, apifootball_fixture_id, home, away, "
        "                    commence_time, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            competition["sport_id"],
            competition["id"],
            fixture_id,
            home,
            away,
            commence_utc,
            SOURCE,
            utcnow(),
        ),
    )
    return "created"


async def import_competition(
    client: APIFootballClient,
    competition_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ImportReport:
    """Importe les matchs d'une competition sur la fenetre de scan.

    Gratuit en credits The Odds API : aucun appel ne lui est adresse. Cote
    API-Football, deux appels — la saison puis les matchs.
    """
    settings = settings or get_settings()
    competition = _competition(competition_id, settings)
    if competition is None:
        return ImportReport(competition=str(competition_id), error="competition inconnue")

    label = competition["label"]
    if competition["sport_key"] != "football":
        return ImportReport(competition=label, error="seul le football a un fournisseur de matchs")
    if competition["apifootball_league_id"] is None:
        return ImportReport(competition=label, error="aucune ligue API-Football rattachee")
    if competition["api_active"]:
        return ImportReport(competition=label, served_elsewhere=True)

    start, end = scan_window(settings, now)
    try:
        season = await client.current_season(int(competition["apifootball_league_id"]))
        rows = await client.fixtures_by_range(
            int(competition["apifootball_league_id"]),
            season,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )
    except ProviderError as exc:
        logger.warning("Import des matchs impossible pour %s : %s", label, exc)
        return ImportReport(competition=label, error=str(exc))

    report = ImportReport(competition=label)
    with connect(settings) as conn:
        for fixture in rows:
            # La plage du fournisseur est en jours pleins : on retaille sur la
            # fenetre reelle, sinon un match deja joue ce matin reviendrait.
            commence = (fixture.get("fixture") or {}).get("date")
            if not commence:
                continue
            when = datetime.fromisoformat(commence).astimezone(UTC)
            if when < start or when > end:
                continue
            outcome = _upsert(conn, competition, fixture)
            if outcome == "created":
                report.created += 1
            elif outcome == "updated":
                report.updated += 1

    logger.info(
        "Import API-Football pour %s : %d cree(s), %d mis a jour sur %d match(s) servis",
        label,
        report.created,
        report.updated,
        len(rows),
    )
    return report
