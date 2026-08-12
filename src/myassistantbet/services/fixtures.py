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
    # Trois marches que `markets.py` demande a The Odds API depuis toujours et
    # que cette table ignorait : sur une competition ou le book principal ne sert
    # rien, ils n'arrivaient donc par aucun des deux chemins. Le fournisseur les
    # sert, `render.py` sait les ecrire, et l'entree ne coute aucun appel de plus
    # — un seul les rend tous.
    "HT/FT Double": "halftime_fulltime",
    # « Se qualifie », cote fournisseur de substitution. Sans cette entree, le
    # marche etait ajoute exactement la ou il ne pouvait pas arriver : un lot de
    # 21 manches retour servi integralement par Superbet et Bet365 de
    # substitution n'en aurait vu aucune cote — et le bloc ne l'aurait pas dit,
    # « Non servis » ne se construisant que sur ce que l'outil a demande a The
    # Odds API. Verifie le 12/08/2026 sur `/odds/bets` : « To Qualify », id 61.
    "To Qualify": "to_qualify",
    "Corners 1x2": "corners_1x2",
    "Correct Score - First Half": "correct_score_h1",
}


def _book_key(name: str) -> str:
    """`888Sport` -> `888sport`. Cle stable pour la colonne `bookmaker`."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _side(text: str, home: str, away: str) -> str:
    """`Home` -> le nom de l'equipe qui recoit, `Draw` inchange."""
    return {"home": home, "away": away, "draw": "Draw"}.get(text.strip().lower(), text.strip())


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
    if market in {"halftime_fulltime", "double_chance"}:
        # « Home/Draw » cote fournisseur, « Lyon/Draw » cote application : c'est
        # la convention de The Odds API, et deux ecritures du meme pari se
        # liraient comme deux paris.
        #
        # Ce n'est pas cosmetique pour la double chance : `render` identifie
        # 1X / 12 / X2 par **les equipes citees dans l'issue**, donc « Home/Away »
        # ne s'identifiait pas et le bloc tombait dans le repli generique. Le
        # prompt affichait alors « Home/Away 1.14 » — une ligne ou l'analyse ne
        # peut meme pas dire de quel camp on parle. Le defaut dormait depuis que
        # la double chance est dans la table ; il ne se voyait pas parce que le
        # releve n'allait que sur des matchs sans aucune cote, donc rarement.
        camps = [_side(part, home, away) for part in text.split("/")]
        return "/".join(camps), None, None
    if market in {"h2h", "spreads", "corners_1x2", "to_qualify"}:
        parts = text.rsplit(" ", 1)
        side, point = (parts[0], parts[1]) if len(parts) == 2 else (text, None)
        name = {"home": home, "away": away, "draw": "Draw"}.get(side.lower(), side)
        try:
            value = float(point) if point is not None else None
        except ValueError:
            return (
                {"home": home, "away": away, "draw": "Draw"}.get(text.lower(), text),
                None,
                None,
            )
        # **Le fournisseur ecrit le handicap du point de vue de l'equipe qui
        # recoit, des deux cotes** : « Home -0.5 » et « Away -0.5 » sont les deux
        # moities d'un meme pari, pas deux paris a -0.5. The Odds API fait
        # l'inverse — chaque issue porte son propre handicap — et c'est cette
        # convention-la que la base stocke et que `render` sait lire.
        #
        # Sans la conversion, le bloc a servi « Aston Villa -0.5 2.12 » sur une
        # Supercoupe d'Europe ou Aston Villa vainqueur valait 4.60 : le prix
        # etait juste, le signe designait le pari inverse. Mesure sur la base
        # entiere : 33 rencontres portaient la faute, toutes relevees par ce
        # chemin, aucune par The Odds API.
        if market == "spreads" and value is not None and side.strip().lower() == "away":
            value = -value
        return name, value, None
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
    #: Marches que le releve n'a pas ecrits parce qu'une autre source les sert
    #: deja sur cet evenement. Distinct de `ignored`, qui compte ce que
    #: l'application ne modelise pas : ici le marche est connu et rendu, il vient
    #: simplement d'ailleurs.
    held: int = 0
    error: str | None = None

    @property
    def note(self) -> str:
        if self.error:
            return f"{self.label} : {self.error}"
        if not self.outcomes:
            if self.held:
                return (
                    f"{self.label} : rien de neuf a relever, "
                    f"{self.held} marche(s) deja servi(s) par une autre source."
                )
            return f"{self.label} : aucune cote servie par les books retenus."
        detail = f"{self.label} : {self.outcomes} cote(s) sur {self.markets} marche(s)"
        detail += f", relevees chez {self.bookmaker}."
        if self.held:
            detail += f" {self.held} marche(s) deja servi(s) par une autre source."
        if self.ignored:
            detail += f" {self.ignored} marche(s) non modelise(s) ignore(s)."
        return detail


def _new_markets(book: dict[str, Any], served: frozenset[str]) -> set[str]:
    """Marches modelises que ce book apporterait, deduction faite du deja-servi."""
    return {
        market
        for bet in book.get("bets") or []
        if (market := BET_MARKETS.get(str(bet.get("name")))) and market not in served
    }


def _pick_bookmaker(
    entries: list[dict[str, Any]],
    wanted: tuple[str, ...],
    served_by_book: dict[str, set[str]] | None = None,
) -> dict[str, Any] | None:
    """Le plus fourni des books de la liste de preference, presents sur ce match.

    Aucun repli sur un book quelconque : prendre le premier venu ferait passer
    pour jouable un prix releve chez un book dont l'ecart a Betclic n'a jamais
    ete mesure. Une absence constatee est une information, pas un probleme.

    **Parmi les books mesures, en revanche, c'est le catalogue qui tranche**, et
    la difference n'est pas marginale : sur un tour preliminaire de Ligue des
    champions, le fournisseur sert quatorze books, et les six de la liste vont de
    6 a 11 marches modelises. Prendre le premier disponible donnait 888Sport et
    ses 7 marches quand Bet365 en servait 10 — cinquante cotes perdues pour un
    ecart de prix qui, lui, a ete mesure equivalent sur les deux. La garantie de
    proximite tient : tous les candidats sont deja verifies, l'ordre ne
    departage plus que les egalites.

    `served_by_book` retire du compte ce qu'une autre source sert deja : le book
    choisi doit etre celui qui apporte le plus, pas celui qui repete le mieux.
    Le deja-servi est lu **par book** et non en bloc, sans quoi un second
    passage compterait le releve precedent comme un acquis d'ailleurs, ne
    verrait plus aucun apport nulle part, et changerait de book a chaque fois.
    """
    deja = served_by_book or {}
    available: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for book in entry.get("bookmakers") or []:
            available[_book_key(str(book.get("name")))] = book

    def ailleurs(book_key: str) -> frozenset[str]:
        return frozenset(
            market for book, markets in deja.items() if book != book_key for market in markets
        )

    candidats = [
        (rang, _book_key(name), available[_book_key(name)])
        for rang, name in enumerate(wanted)
        if _book_key(name) in available
    ]
    if not candidats:
        return None
    # A egalite de marches apportes, le rang dans la liste — donc la proximite
    # mesuree a Betclic — departage.
    return max(
        candidats,
        key=lambda item: (len(_new_markets(item[2], ailleurs(item[1]))), -item[0]),
    )[2]


async def import_odds(
    client: APIFootballClient,
    event_id: int,
    settings: Settings | None = None,
) -> OddsReport:
    """Releve des cotes chez un substitut de Betclic, pour ce qu'aucune source ne sert.

    Ne touche jamais aux cotes existantes d'un autre book : elles sont
    remplacees pour ce book seulement, donc relancer ne duplique rien et
    n'ecrase pas un releve Betclic ni une saisie manuelle.

    **Une seule source par marche**, comme `services/reference.py` : un marche
    deja servi par une autre source n'est pas relu ici. Sans cette regle, un
    `h2h` de Pinnacle et un `h2h` de Bet365 coexisteraient sur la meme issue, le
    bloc en afficherait un au hasard, et l'outil inviterait a la comparaison de
    prix entre bookmakers que SPEC.md interdit. C'est ce qui permet au releve
    d'aller sur un match qui a **deja** des cotes, tant qu'aucune ne vient du
    book principal : il complete au lieu de doubler.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT id, home, away, apifootball_fixture_id FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        existing = conn.execute(
            "SELECT DISTINCT bookmaker, market_key FROM odds WHERE event_id = ?",
            (event_id,),
        ).fetchall()
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

    # Ce que chaque source sert deja sur cet evenement. Range par book : le
    # releve remplace ses propres lignes, elles ne comptent donc pas comme
    # « deja servi » quand c'est lui qu'on rappelle.
    deja: dict[str, set[str]] = {}
    for line in existing:
        deja.setdefault(str(line["bookmaker"]), set()).add(str(line["market_key"]))

    book = _pick_bookmaker(entries, settings.apifootball_books, deja)
    if book is None:
        return OddsReport(label=label)

    key = _book_key(str(book.get("name")))
    served = frozenset(
        market for other, markets in deja.items() if other != key for market in markets
    )
    report = OddsReport(label=label, bookmaker=str(book.get("name")))
    stamp = utcnow()
    with connect(settings) as conn:
        conn.execute("DELETE FROM odds WHERE event_id = ? AND bookmaker = ?", (event_id, key))
        for bet in book.get("bets") or []:
            market = BET_MARKETS.get(str(bet.get("name")))
            if market is None:
                report.ignored += 1
                continue
            if market in served:
                report.held += 1
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
