"""Compression d'un evenement en bloc texte dense (SPEC.md section 7).

C'est le composant le plus important de l'application : la qualite de l'analyse
produite par Claude depend directement de cette densite. Un match en JSON brut
coute ~3 000 tokens, le meme bloc en coute ~300.

Regles absolues :
- une ligne sans donnee est omise, jamais rendue vide ni avec « N/A » ;
- une donnee volontairement absente devient une ligne explicite
  (« donnees non disponibles pour cette competition ») ;
- les cotes sont formatees a deux decimales ;
- les scores exacts sont limites aux 10 cotes les plus basses, triees croissant ;
- les lignes O/U sont limitees aux 5 lignes les plus proches de la ligne principale ;
- une ligne qui ne vient pas du book principal porte sa source en fin de ligne ;
- un marche demande et jamais servi devient une ligne « Non servis » explicite.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from .labels import affiche, bookmaker_label

INDENT = "  "
LABEL_WIDTH = 12
CONTINUATION = INDENT + " " * LABEL_WIDTH

#: Nombre de cotes de score exact retenues, et nombre par ligne rendue.
CORRECT_SCORE_KEEP = 10
CORRECT_SCORE_PER_LINE = 6
#: Nombre de lignes O/U retenues autour de la ligne principale.
TOTALS_KEEP = 5

SPORT_LABELS = {"football": "FOOT", "tennis": "TENNIS", "cycling": "CYCLISME"}

UNAVAILABLE = "donnees non disponibles pour cette competition"

#: Libelle de la ligne qui enumere les marches demandes et jamais obtenus.
UNSERVED_LABEL = "Non servis"
UNSERVED_NOTE = "aucun book interroge ne les sert sur cette competition"
#: Meme ligne, autre cause : le match n'existe pas chez le fournisseur de
#: cotes, et le book de substitution ne sert pas tout. Le distinguer evite de
#: chercher un reglage la ou il n'y a qu'une offre plus etroite.
UNSERVED_NOTE_SUBSTITUTE = "non servis par le book de substitution sur ce match"


@dataclass
class Outcome:
    """Une issue de marche, telle que stockee dans la table `odds`."""

    name: str
    price: float
    point: float | None = None
    description: str | None = None
    #: Book qui sert cette cote. Vide = source inconnue, aucune mention rendue.
    bookmaker: str = ""


@dataclass
class RenderableEvent:
    """Tout ce qu'il faut pour rendre un bloc de match."""

    index: int
    sport_key: str
    competition: str
    home: str
    away: str
    commence_local: datetime
    markets: dict[str, list[Outcome]] = field(default_factory=dict)
    #: Lignes de contexte deja formatees : (libelle, valeur). Alimentees en phase 3.
    context_lines: list[tuple[str, str]] = field(default_factory=list)
    note: str | None = None
    bookmaker_label: str = "Betclic"
    fetched_local: datetime | None = None
    #: Cle du book dont les cotes sont jouables telles quelles. Les lignes
    #: servies par un autre book portent leur source : une cote de reference
    #: situe le marche, elle n'est pas le prix qu'on obtiendra.
    primary_book: str = ""
    #: Marches demandes a l'API et jamais servis sur cette competition. Les
    #: taire laisserait croire a un bloc incomplet plutot qu'a une limite connue.
    unserved: list[str] = field(default_factory=list)
    #: Vrai quand les cotes viennent d'un book de repli et non du fournisseur
    #: principal : la cause d'une absence n'est alors pas la meme.
    substitute: bool = False


# -- Formatage elementaire --------------------------------------------------


def price(value: float) -> str:
    return f"{value:.2f}"


def _point(value: float) -> str:
    """Une ligne de handicap : 2.5 et non 2.50, mais 0.25 conserve ses decimales."""
    text = f"{value:g}"
    return text


def line(label: str, value: str) -> str:
    return f"{INDENT}{label:<{LABEL_WIDTH}}{value}"


def _wrap(label: str, chunks: Sequence[str], per_line: int) -> list[str]:
    """Rend une liste de fragments sur plusieurs lignes alignees."""
    rows: list[str] = []
    for start in range(0, len(chunks), per_line):
        piece = " | ".join(chunks[start : start + per_line])
        rows.append(line(label, piece) if start == 0 else CONTINUATION + piece)
    return rows


# -- Regroupements ----------------------------------------------------------


def _by_point(outcomes: Iterable[Outcome]) -> dict[float, dict[str, float]]:
    """Regroupe des issues Over/Under par ligne."""
    grouped: dict[float, dict[str, float]] = {}
    for outcome in outcomes:
        if outcome.point is None:
            continue
        grouped.setdefault(outcome.point, {})[outcome.name] = outcome.price
    return grouped


def main_line(lines: dict[float, dict[str, float]]) -> float | None:
    """Ligne principale : celle dont Over et Under sont les plus proches."""
    complete = {p: v for p, v in lines.items() if "Over" in v and "Under" in v}
    if complete:
        return min(complete, key=lambda p: abs(complete[p]["Over"] - complete[p]["Under"]))
    return min(lines) if lines else None


def _totals_fragments(outcomes: Iterable[Outcome], keep: int = TOTALS_KEEP) -> list[str]:
    """`1.5: 1.22/4.10 | 2.5: 1.72/2.05 | …`, limite aux lignes utiles."""
    grouped = _by_point(outcomes)
    reference = main_line(grouped)
    if reference is None:
        return []

    retained = sorted(grouped, key=lambda p: (abs(p - reference), p))[:keep]
    fragments = []
    for point in sorted(retained):
        prices = grouped[point]
        over, under = prices.get("Over"), prices.get("Under")
        if over is None and under is None:
            continue
        both = f"{price(over) if over else '·'}/{price(under) if under else '·'}"
        fragments.append(f"{_point(point)}: {both}")
    return fragments


# -- Rendu des marches ------------------------------------------------------


def _render_h2h(event: RenderableEvent, outcomes: list[Outcome], label: str = "1N2") -> list[str]:
    prices = {outcome.name: outcome.price for outcome in outcomes}
    home, draw, away = prices.get(event.home), prices.get("Draw"), prices.get(event.away)
    if home is None and away is None:
        return _render_generic(label, outcomes) if outcomes else []
    if draw is None:
        # Tennis et sports sans nul : deux issues seulement. Le libelle « 1N2 »
        # n'aurait alors aucun sens.
        parts = [price(value) for value in (home, away) if value is not None]
        return [line("1-2" if label == "1N2" else label, " / ".join(parts))]
    return [line(label, " / ".join(price(value) for value in (home, draw, away) if value))]


def _render_double_chance(event: RenderableEvent, outcomes: list[Outcome]) -> list[str]:
    """DC : 1X / 12 / X2, identifies par les equipes citees dans l'issue."""
    slots: dict[str, float] = {}
    for outcome in outcomes:
        name = outcome.name.casefold()
        has_home = event.home.casefold() in name
        has_away = event.away.casefold() in name
        has_draw = "draw" in name or "nul" in name
        if has_home and has_draw:
            slots["1X"] = outcome.price
        elif has_home and has_away:
            slots["12"] = outcome.price
        elif has_away and has_draw:
            slots["X2"] = outcome.price

    if len(slots) < 2:
        # Nommage inattendu : on rend brut plutot que de deviner.
        return _render_generic("DC", outcomes)
    ordered = [slots.get(key) for key in ("1X", "12", "X2")]
    return [line("DC", " / ".join(price(value) for value in ordered if value is not None))]


def _render_totals(label: str, outcomes: list[Outcome]) -> list[str]:
    fragments = _totals_fragments(outcomes)
    return [line(label, " | ".join(fragments))] if fragments else []


def _render_btts(label: str, outcomes: list[Outcome]) -> list[str]:
    prices = {outcome.name.casefold(): outcome.price for outcome in outcomes}
    yes, no = prices.get("yes"), prices.get("no")
    if yes is None and no is None:
        return []
    parts = []
    if yes is not None:
        parts.append(f"Oui {price(yes)}")
    if no is not None:
        parts.append(f"Non {price(no)}")
    return [line(label, " / ".join(parts))]


def _render_team_totals(event: RenderableEvent, outcomes: list[Outcome]) -> list[str]:
    """`Häcken O1.5 2.30 | Djurgården O1.5 2.45` — ligne principale par equipe."""
    by_team: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        team = outcome.description
        if team:
            by_team.setdefault(team, []).append(outcome)

    fragments = []
    for team in (event.home, event.away):
        team_outcomes = by_team.get(team)
        if not team_outcomes:
            continue
        grouped = _by_point(team_outcomes)
        reference = main_line(grouped)
        if reference is None:
            continue
        over = grouped[reference].get("Over")
        if over is not None:
            fragments.append(f"{team} O{_point(reference)} {price(over)}")
    return [line("Eq. buts", " | ".join(fragments))] if fragments else []


def _render_correct_score(label: str, outcomes: list[Outcome]) -> list[str]:
    scored = sorted(
        ((outcome.name.replace(" ", ""), outcome.price) for outcome in outcomes),
        key=lambda item: item[1],
    )[:CORRECT_SCORE_KEEP]
    if not scored:
        return []
    chunks = [f"{name} {price(value)}" for name, value in scored]
    return _wrap(label, chunks, CORRECT_SCORE_PER_LINE)


def _render_main_total_only(label: str, outcomes: list[Outcome]) -> list[str]:
    """Corners et cartons : uniquement la ligne principale, comme dans la SPEC."""
    fragments = _totals_fragments(outcomes, keep=1)
    return [line(label, f"O/U {fragments[0]}")] if fragments else []


def _render_spreads(
    event: RenderableEvent, outcomes: list[Outcome], label: str = "Handicap"
) -> list[str]:
    """Handicap : la ligne la plus serree pour chaque equipe (ou chaque joueur)."""
    by_team: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        by_team.setdefault(outcome.name, []).append(outcome)

    fragments = []
    for team in (event.home, event.away):
        team_outcomes = [item for item in by_team.get(team, []) if item.point is not None]
        if not team_outcomes:
            continue
        best = min(team_outcomes, key=lambda item: abs(item.price - 2.0))
        sign = "+" if best.point and best.point > 0 else ""
        fragments.append(f"{team} {sign}{_point(best.point)} {price(best.price)}")
    return [line(label, " | ".join(fragments))] if fragments else []


def _render_generic(label: str, outcomes: list[Outcome]) -> list[str]:
    """Repli pour un marche paye mais non modelise : on l'affiche plutot que le perdre."""
    chunks = []
    for outcome in sorted(outcomes, key=lambda item: item.price):
        name = outcome.name
        if outcome.point is not None:
            name = f"{name} {_point(outcome.point)}"
        if outcome.description:
            name = f"{outcome.description} {name}"
        chunks.append(f"{name} {price(outcome.price)}")
    return _wrap(label, chunks, 4) if chunks else []


#: Ordre d'affichage des marches football, et libelle de chaque ligne.
MARKET_ORDER: list[tuple[str, str]] = [
    ("h2h", "1N2"),
    ("double_chance", "DC"),
    ("alternate_spreads", "Handicap"),
    ("spreads", "Handicap"),
    ("totals", "O/U"),
    ("alternate_totals", "O/U"),
    ("btts", "BTTS"),
    ("totals_h1", "MT O/U"),
    ("btts_h1", "BTTS MT"),
    ("halftime_fulltime", "MT/FT"),
    ("team_totals", "Eq. buts"),
    ("alternate_team_totals", "Eq. buts"),
    ("correct_score", "Score exact"),
    ("correct_score_h1", "Score ex. MT"),
    ("alternate_totals_corners", "Corners"),
    ("corners_1x2", "Corners 1N2"),
    ("alternate_totals_cards", "Cartons"),
    # Saisie manuelle : marche libre, sans forme imposee.
    ("outright", "Cotes"),
]

#: Tennis : pas de nul, et tout se compte en sets et en jeux.
TENNIS_MARKET_ORDER: list[tuple[str, str]] = [
    ("h2h", "Vainqueur"),
    ("spreads", "Hand. jeux"),
    ("totals", "Jeux O/U"),
    ("h2h_s1", "Set 1"),
    ("h2h_s2", "Set 2"),
    ("spreads_s1", "Hand. S1"),
    ("totals_s1", "Jeux S1"),
    ("alternate_totals_s1", "Jeux S1"),
    # Marche libre de la saisie manuelle. Il ne peut pas s'appeler « Vainqueur »
    # comme `h2h` : depuis qu'une saisie peut completer un match d'API, les deux
    # coexistent sur le meme bloc et deux libelles identiques seraient illisibles.
    ("outright", "Cotes"),
]

#: Cyclisme : aucune API ne le couvre, tout est saisi a la main.
CYCLING_MARKET_ORDER: list[tuple[str, str]] = [
    ("outright", "Vainqueur"),
    ("podium", "Podium"),
]

MARKET_ORDER_BY_SPORT: dict[str, list[tuple[str, str]]] = {
    "football": MARKET_ORDER,
    "tennis": TENNIS_MARKET_ORDER,
    "cycling": CYCLING_MARKET_ORDER,
}

#: Marches fusionnes dans une meme ligne (la variante « alternate » complete la base).
MERGED_MARKETS = {
    "alternate_totals": "totals",
    "alternate_team_totals": "team_totals",
    "spreads": "alternate_spreads",
    "alternate_totals_s1": "totals_s1",
}


def market_label(sport_key: str, key: str) -> str:
    """Libelle d'affichage d'un marche, la cle brute a defaut."""
    order = MARKET_ORDER_BY_SPORT.get(sport_key, MARKET_ORDER)
    return next((label for market, label in order if market == key), key)


def ordered_labels(sport_key: str, keys: Iterable[str]) -> list[str]:
    """Libelles d'un ensemble de marches, dans l'ordre du bloc et sans doublon.

    Deux cles peuvent partager un libelle (`totals_s1` et sa variante
    `alternate_totals_s1`) : les repeter ferait croire a deux marches distincts.
    """
    order = [key for key, _ in MARKET_ORDER_BY_SPORT.get(sport_key, MARKET_ORDER)]
    ranked = sorted(keys, key=lambda key: (order.index(key) if key in order else len(order), key))
    labels: list[str] = []
    for key in ranked:
        label = market_label(sport_key, key)
        if label not in labels:
            labels.append(label)
    return labels


def _render_one(
    event: RenderableEvent, target: str, label: str, outcomes: list[Outcome]
) -> list[str]:
    """Rendu dedie d'un marche, ou repli generique s'il n'en a pas."""
    if target in {"h2h", "h2h_s1", "h2h_s2"}:
        return _render_h2h(event, outcomes, label)
    if target == "double_chance":
        return _render_double_chance(event, outcomes)
    if target in {"alternate_spreads", "spreads_s1"}:
        return _render_spreads(event, outcomes, label)
    if target in {"totals", "totals_h1", "totals_s1"}:
        return _render_totals(label, outcomes)
    if target in {"btts", "btts_h1"}:
        return _render_btts(label, outcomes)
    if target == "team_totals":
        return _render_team_totals(event, outcomes)
    if target in {"correct_score", "correct_score_h1"}:
        return _render_correct_score(label, outcomes)
    if target in {"alternate_totals_corners", "alternate_totals_cards"}:
        return _render_main_total_only(label, outcomes)
    return _render_generic(label, outcomes)


def _source_tag(event: RenderableEvent, outcomes: Iterable[Outcome]) -> str:
    """Mention de source d'une ligne. Vide quand elle vient du book principal.

    Sans elle, un bloc annoncant « Betclic + Pinnacle (ref.) » laisse deviner
    quelle ligne est jouable et laquelle ne fait que situer le marche.
    """
    books = {outcome.bookmaker for outcome in outcomes if outcome.bookmaker}
    if not books or not event.primary_book:
        return ""
    foreign = sorted(books - {event.primary_book})
    if not foreign:
        return ""
    names = " + ".join(bookmaker_label(book) for book in foreign)
    # Une ligne fusionnee peut melanger le principal et une reference : le dire,
    # sinon la mention condamnerait des cotes pourtant jouables.
    partial = "dont " if len(foreign) < len(books) else ""
    return f"  [{partial}{names}]"


def _render_markets(event: RenderableEvent) -> list[str]:
    """Rend chaque marche disponible, dans l'ordre, en fusionnant les variantes."""
    pooled: dict[str, list[Outcome]] = {}
    for key, outcomes in event.markets.items():
        target = MERGED_MARKETS.get(key, key)
        pooled.setdefault(target, []).extend(outcomes)

    order = MARKET_ORDER_BY_SPORT.get(event.sport_key, MARKET_ORDER)
    rendered: list[str] = []
    done: set[str] = set()
    for key, label in order:
        target = MERGED_MARKETS.get(key, key)
        if target in done or target not in pooled:
            continue
        done.add(target)
        outcomes = pooled[target]
        rendered += _with_source(event, _render_one(event, target, label, outcomes), outcomes)

    # Marches payes mais absents du catalogue : rendus en dernier, jamais perdus.
    # Un caractere de moins que la colonne, sinon le libelle touche sa valeur.
    for key in sorted(set(pooled) - done):
        raw = _render_generic(key[: LABEL_WIDTH - 1], pooled[key])
        rendered += _with_source(event, raw, pooled[key])

    return rendered


def _with_source(event: RenderableEvent, rows: list[str], outcomes: list[Outcome]) -> list[str]:
    """Ajoute la mention de source a la premiere ligne d'un marche rendu."""
    tag = _source_tag(event, outcomes)
    if tag and rows:
        rows[0] += tag
    return rows


def _unserved_line(event: RenderableEvent) -> list[str]:
    """Marches demandes et jamais servis : un fait acquis, pas un oubli.

    Les taire laisserait chercher un handicap jeux qui n'existe pas, ou croire
    a une collecte incomplete la ou l'API a repondu tout ce qu'elle avait.
    """
    absent = (key for key in event.unserved if key not in event.markets)
    labels = ordered_labels(event.sport_key, absent)
    note = UNSERVED_NOTE_SUBSTITUTE if event.substitute else UNSERVED_NOTE
    return [line(UNSERVED_LABEL, f"{', '.join(labels)} — {note}")] if labels else []


# -- Bloc complet -----------------------------------------------------------


def _header(event: RenderableEvent) -> str:
    sport = SPORT_LABELS.get(event.sport_key, event.sport_key.upper())
    when = event.commence_local.strftime("%d/%m %H:%M")
    # Le cyclisme n'a pas de second participant : l'etape tient lieu d'affiche.
    return (
        f"### M{event.index} · {sport} · {event.competition} · "
        f"{affiche(event.home, event.away)} · {when}"
    )


def _context_block(event: RenderableEvent) -> list[str]:
    rows: list[str] = []
    for label, value in event.context_lines:
        if not value:
            continue
        # Une valeur multiligne (les absents des deux equipes) est alignee sous
        # la premiere ligne plutot que de repeter le libelle.
        first, *rest = value.split("\n")
        rows.append(line(label, first))
        rows.extend(CONTINUATION + extra for extra in rest if extra)

    if event.note and event.note.strip():
        rows.append(line("NOTE PERSO", event.note.strip()))
    return ["CONTEXTE", *rows] if rows else []


def _markets_block(event: RenderableEvent) -> list[str]:
    rows = _render_markets(event) + _unserved_line(event)
    if not rows:
        return []
    heading = f"MARCHES ({event.bookmaker_label}"
    if event.fetched_local:
        heading += f", releve {event.fetched_local.strftime('%H:%M')}"
    return [heading + ")", *rows]


def render_event(event: RenderableEvent) -> str:
    """Bloc texte compact d'un evenement, pret a etre injecte dans le prompt."""
    parts = [_header(event), *_context_block(event), *_markets_block(event)]
    return "\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Approximation suffisante pour l'UI : un token vaut ~3.6 caracteres."""
    return round(len(text) / 3.6)
