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
import re
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


# -- Cotes de substitution ----------------------------------------------------

#: Libelles de marches du fournisseur -> cles de l'application. Rapproches par
#: libelle, comme partout ailleurs. Un marche absent d'ici est ignore : il n'est
#: pas paye a l'unite, un seul appel les rend tous.
BET_MARKETS = {
    "Match Winner": "h2h",
    "Double Chance": "double_chance",
    "Asian Handicap": "spreads",
    "Goals Over/Under": "totals",
    "Goals Over/Under First Half": "totals_h1",
    "Both Teams Score": "btts",
    "Both Teams Score - First Half": "btts_h1",
    "Exact Score": "correct_score",
    "Corners Over Under": "alternate_totals_corners",
    "Cards Over/Under": "alternate_totals_cards",
    # Le fournisseur nomme « Total - Home » ce que l'app appelle des buts
    # d'equipe : le marche existait des deux cotes et se perdait faute de
    # rapprochement, alors qu'un rendu dedie l'attendait deja.
    "Total - Home": "team_totals",
    "Total - Away": "team_totals",
}


def _book_key(name: str) -> str:
    """`888Sport` -> `888sport`. Cle stable pour la colonne `bookmaker`."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _outcome(
    market: str, value: str, home: str, away: str, bet: str = ""
) -> tuple[str, float | None, str | None] | None:
    """Traduit une issue du fournisseur vers (nom, ligne) de l'application.

    Le fournisseur ecrit « Home », « Over 2.5 » ou « Home -0.5 » la ou l'app
    stocke un nom d'equipe et une ligne separee : sans cette traduction, les
    cotes existeraient en base sans jamais rejoindre celles de The Odds API
    dans le rendu.
    """
    text = (value or "").strip()
    if not text:
        return None
    if market == "team_totals":
        # L'equipe concernee est dans le nom du marche, la ligne dans la valeur :
        # `Total - Home` + `Over 1.5` devient (Over, 1.5, equipe a domicile).
        team = home if bet.endswith("Home") else away
        parts = text.split()
        if len(parts) == 2:
            try:
                return parts[0], float(parts[1]), team
            except ValueError:
                return text, None, team
        return text, None, team
    if market in {"h2h", "spreads"}:
        parts = text.rsplit(" ", 1)
        side, point = (parts[0], parts[1]) if len(parts) == 2 else (text, None)
        name = {"home": home, "away": away, "draw": "Draw"}.get(side.lower(), side)
        try:
            return name, float(point) if point is not None else None, None
        except ValueError:
            return (
                {"home": home, "away": away, "draw": "Draw"}.get(text.lower(), text),
                None,
                None,
            )
    if market in {"totals", "totals_h1", "alternate_totals_corners", "alternate_totals_cards"}:
        parts = text.split()
        if len(parts) == 2:
            try:
                return parts[0], float(parts[1]), None
            except ValueError:
                return text, None, None
        return text, None, None
    return text, None, None


@dataclass
class OddsReport:
    """Ce qu'un import de cotes a produit, et chez qui."""

    label: str
    bookmaker: str | None = None
    markets: int = 0
    outcomes: int = 0
    ignored: int = 0
    error: str | None = None

    @property
    def note(self) -> str:
        if self.error:
            return f"{self.label} : {self.error}"
        if not self.outcomes:
            return f"{self.label} : aucune cote servie par les books retenus."
        detail = f"{self.label} : {self.outcomes} cote(s) sur {self.markets} marche(s)"
        detail += f", relevees chez {self.bookmaker}."
        if self.ignored:
            detail += f" {self.ignored} marche(s) non modelise(s) ignore(s)."
        return detail


def _pick_bookmaker(
    entries: list[dict[str, Any]], wanted: tuple[str, ...]
) -> dict[str, Any] | None:
    """Premier book de la liste de preference effectivement present.

    Aucun repli sur un book quelconque : prendre le premier venu ferait passer
    pour jouable un prix releve chez un book dont l'ecart a Betclic n'a jamais
    ete mesure. Une absence constatee est une information, pas un probleme.
    """
    available = {}
    for entry in entries:
        for book in entry.get("bookmakers") or []:
            available[_book_key(str(book.get("name")))] = book
    for name in wanted:
        book = available.get(_book_key(name))
        if book:
            return book
    return None


async def import_odds(
    client: APIFootballClient,
    event_id: int,
    settings: Settings | None = None,
) -> OddsReport:
    """Releve des cotes chez un substitut de Betclic, pour un match qui n'en a pas.

    Ne touche jamais aux cotes existantes d'un autre book : elles sont
    remplacees pour ce book seulement, donc relancer ne duplique rien et
    n'ecrase pas un releve Betclic ni une saisie manuelle.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT id, home, away, apifootball_fixture_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        return OddsReport(label=str(event_id), error="evenement inconnu")

    label = f"{row['home']} – {row['away']}"
    if not row["apifootball_fixture_id"]:
        return OddsReport(
            label=label,
            error="aucun match API-Football rattache — enrichir d'abord, ou resoudre le mapping",
        )

    try:
        entries = await client.odds(int(row["apifootball_fixture_id"]))
    except ProviderError as exc:
        logger.warning("Cotes de substitution indisponibles pour %s : %s", label, exc)
        return OddsReport(label=label, error=str(exc))

    book = _pick_bookmaker(entries, settings.apifootball_books)
    if book is None:
        return OddsReport(label=label)

    key = _book_key(str(book.get("name")))
    report = OddsReport(label=label, bookmaker=str(book.get("name")))
    stamp = utcnow()
    with connect(settings) as conn:
        conn.execute("DELETE FROM odds WHERE event_id = ? AND bookmaker = ?", (event_id, key))
        for bet in book.get("bets") or []:
            market = BET_MARKETS.get(str(bet.get("name")))
            if market is None:
                report.ignored += 1
                continue
            written = 0
            for value in bet.get("values") or []:
                outcome = _outcome(
                    market, str(value.get("value")), row["home"], row["away"], str(bet.get("name"))
                )
                try:
                    price = float(value.get("odd"))
                except (TypeError, ValueError):
                    continue
                if outcome is None:
                    continue
                conn.execute(
                    "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, "
                    "                  description, point, price, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, key, market, outcome[0], outcome[2], outcome[1], price, stamp),
                )
                written += 1
            if written:
                report.markets += 1
                report.outcomes += written

    logger.info("Cotes de substitution pour %s : %d cote(s) chez %s", label, report.outcomes, key)
    return report
