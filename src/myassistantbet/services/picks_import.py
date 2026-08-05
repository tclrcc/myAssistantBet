"""Import des picks depuis le tableau de selections rendu par Claude.

Le prompt demande un tableau Markdown (section C). Le retaper ligne par ligne
dans le formulaire des picks est la derniere corvee du parcours : ce module le
lit et propose un pre-remplissage.

Il ne fait aucun calcul financier — la mise reste absente de l'import, comme
elle est absente des agregats. Il n'ecrit rien non plus : il rend une
proposition que l'utilisateur valide, corrige ou rejette ligne par ligne.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .grid import GridRow, anchor, build_view
from .history import PickableEvent, list_picks, pickable_events
from .history import tiers as load_tiers

#: Une ligne de tableau Markdown : `| a | b | c |`.
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")

#: La ligne de separation d'un tableau Markdown : `|---|:--:|`.
SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")

#: Tabulations minimales pour tenir une ligne pour une ligne de tableau copiee.
#: Trois colonnes : de la prose n'en contient pratiquement jamais autant.
MIN_TABS = 2

#: Entetes acceptes pour chaque champ, normalises. Claude suit le gabarit du
#: prompt, mais un synonyme ne doit pas faire echouer tout l'import.
HEADERS: dict[str, tuple[str, ...]] = {
    "match": ("match", "rencontre", "affiche"),
    "market": ("marche", "marché", "market"),
    "selection": ("selection", "sélection", "pick", "pari"),
    "price": ("cote", "odds", "cotes"),
    "tier": ("palier", "tier", "bande"),
    "confidence": ("conf 5", "conf", "confiance", "conf/5", "confiance 5"),
}


@dataclass
class ParsedPick:
    """Une ligne du tableau, telle qu'elle sera proposee au formulaire."""

    index: int
    match_text: str = ""
    event_id: int | None = None
    event_label: str = ""
    market: str = ""
    selection: str = ""
    price: str = ""
    tier: str = ""
    tier_text: str = ""
    confidence: str = ""
    #: Une selection identique existe deja dans la session, ou plus haut dans
    #: le meme tableau. Elle reste proposee — c'est peut-etre voulu — mais
    #: decochee : coller deux fois le meme rendu ne doit pas doubler l'historique.
    duplicate: bool = False

    @property
    def ready(self) -> bool:
        """Vrai si la ligne peut etre enregistree sans correction humaine."""
        return bool(self.market and self.selection and self.tier)

    @property
    def keep(self) -> bool:
        """Vrai si la ligne est cochee par defaut dans le formulaire."""
        return self.ready and not self.duplicate

    @property
    def problems(self) -> list[str]:
        issues = []
        if not self.market:
            issues.append("marché absent")
        if not self.selection:
            issues.append("sélection absente")
        if not self.tier:
            issues.append(f"palier non reconnu ({self.tier_text or 'vide'})")
        if self.event_id is None and self.match_text:
            issues.append("match non rapproché")
        if self.duplicate:
            issues.append("déjà présente")
        return issues


@dataclass
class ImportPreview:
    """Proposition d'import. Rien n'est ecrit avant validation."""

    picks: list[ParsedPick] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.picks)

    @property
    def ready_count(self) -> int:
        return sum(1 for pick in self.picks if pick.ready)

    @property
    def duplicate_count(self) -> int:
        return sum(1 for pick in self.picks if pick.duplicate)


def _signature(event_id: int | None, market: str, selection: str) -> tuple[Any, ...]:
    """Ce qui fait qu'une selection est « la meme » qu'une autre.

    Le match compte : la meme cote sur deux affiches differentes sont deux
    paris. Les accents et la casse ne comptent pas — « Plíšková » recopie a la
    main ne doit pas passer pour une seconde selection.
    """
    return (event_id, _fold(market), _fold(selection))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def _normalize_header(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s/]", " ", text.strip().lower())
    return " ".join(cleaned.split())


def _cells(line: str) -> list[str] | None:
    """Cellules d'une ligne de tableau, quel que soit son format de copie.

    Le Markdown a barres verticales est ce que Claude *ecrit* ; ce que l'on
    *copie* depuis son interface est un tableau tabule, les barres ayant ete
    consommees par le rendu. Les deux doivent passer, sans quoi la fonction
    echoue precisement sur le geste qu'elle est censee servir.
    """
    match = TABLE_ROW.match(line)
    if match is not None:
        return [cell.strip() for cell in match.group(1).split("|")]
    if line.count("\t") >= MIN_TABS:
        return [cell.strip() for cell in line.split("\t")]
    return None


def _is_separator(cells: list[str]) -> bool:
    filled = [cell.replace(" ", "") for cell in cells if cell]
    return bool(filled) and all(SEPARATOR_CELL.match(cell) for cell in filled)


def _map_columns(cells: list[str]) -> dict[str, int] | None:
    """Associe chaque champ a son indice de colonne, d'apres l'entete."""
    found: dict[str, int] = {}
    for index, cell in enumerate(cells):
        header = _normalize_header(cell)
        for field_name, aliases in HEADERS.items():
            if field_name in found:
                continue
            if any(header == _normalize_header(alias) for alias in aliases):
                found[field_name] = index
    # Sans marche ni selection, ce n'est pas le tableau des selections.
    return found if {"market", "selection"} <= set(found) else None


def _at(cells: list[str], position: int | None) -> str:
    """Cellule a cet indice, ou chaine vide si la colonne manque sur la ligne."""
    if position is None or position >= len(cells):
        return ""
    return cells[position]


def _price(text: str) -> str:
    """Extrait la cote d'une cellule. `1,55` ou `@1.55` ou `1.55 (Betclic)`."""
    match = re.search(r"\d+[.,]\d+|\d+", text or "")
    return match.group(0).replace(",", ".") if match else ""


def _confidence(text: str) -> str:
    """Extrait la note de confiance. `4`, `4/5` et `⭐⭐⭐⭐` donnent tous 4."""
    match = re.search(r"[1-5]", text or "")
    if match:
        return match.group(0)
    stars = (text or "").count("⭐") or (text or "").count("★")
    return str(stars) if 1 <= stars <= 5 else ""


def _resolve_tier(text: str, tiers: list[dict[str, str]]) -> str:
    """Retrouve la cle du palier depuis « 🟢 SAFE », « SAFE » ou « safe »."""
    raw = (text or "").strip()
    if not raw:
        return ""
    normalized = _normalize_header(raw)
    for tier in tiers:
        if tier.get("emoji") and tier["emoji"] in raw:
            return tier["key"]
    for tier in tiers:
        name = _normalize_header(tier.get("name") or tier["label"])
        if name and normalized in {name, tier["key"].replace("_", " ")}:
            return tier["key"]
    # Repli : le nom du palier apparait dans la cellule. Le plus long gagne,
    # sans quoi « GIGA FUN » serait reconnu comme « FUN ».
    candidates = sorted(
        tiers, key=lambda t: len(_normalize_header(t.get("name") or t["label"])), reverse=True
    )
    for tier in candidates:
        name = _normalize_header(tier.get("name") or tier["label"])
        if name and name in normalized:
            return tier["key"]
    return ""


def parse_table(
    raw: str,
    rows: list[GridRow],
    tiers: list[dict[str, str]],
    known: set[tuple[Any, ...]] | None = None,
    nearby: list[PickableEvent] | None = None,
) -> ImportPreview:
    """Lit le tableau de selections. Ne rapproche jamais un match au hasard.

    Tolere les colonnes en plus, en moins et dans le desordre : seul l'entete
    fait foi. Une ligne qui n'appartient pas au tableau est ignoree en silence
    (c'est de la prose), une ligne du tableau qui pose probleme est conservee
    avec son motif pour que l'utilisateur tranche.

    La shortlist est essayee **avant** le voisinage : c'est elle qui a ete
    analysee, et l'elargissement ne doit pas rendre ambigu un match qu'elle
    designait seule. Le voisinage ne sert qu'a ce qu'elle ne contient pas.
    """
    preview = ImportPreview()
    columns: dict[str, int] | None = None
    index = 0
    # Les doublons se cherchent contre la session **et** contre le tableau
    # lui-meme : un rendu recopie deux fois se repete a l'interieur.
    seen = set(known or ())

    for line in (raw or "").splitlines():
        cells = _cells(line)
        if cells is None:
            continue
        if _is_separator(cells):
            continue

        if columns is None:
            columns = _map_columns(cells)
            continue

        values = {name: _at(cells, columns.get(name)) for name in HEADERS}
        if not values["market"] and not values["selection"]:
            continue

        index += 1
        found = None
        if values["match"]:
            found = anchor(values["match"], rows) or anchor(values["match"], nearby or [])
        event_id = found.event_id if found else None
        signature = _signature(event_id, values["market"], values["selection"])
        preview.picks.append(
            ParsedPick(
                index=index,
                match_text=values["match"],
                event_id=event_id,
                event_label=found.affiche if found else "",
                market=values["market"],
                selection=values["selection"],
                price=_price(values["price"]),
                tier=_resolve_tier(values["tier"], tiers),
                tier_text=values["tier"],
                confidence=_confidence(values["confidence"]),
                duplicate=signature in seen,
            )
        )
        seen.add(signature)

    if columns is None:
        preview.ignored.append(
            "Aucun tableau de sélections reconnu : colle la section C, "
            "en-tête compris (« Match | Marché | Sélection | … »)."
        )
    return preview


def build_preview(
    session_id: int,
    raw: str,
    settings: Settings | None = None,
) -> ImportPreview:
    """Proposition d'import pour une session, matchs rapproches par leur nom."""
    settings = settings or get_settings()
    rows = build_view(session_id, settings).rows
    known = {
        _signature(pick.event_id, pick.market, pick.selection)
        for pick in list_picks(session_id, settings)
    }
    # Le voisinage rattrape ce que la shortlist ne contient pas : un match qui a
    # commence a quitte le board et n'a jamais pu y etre coche.
    nearby = [event for event in pickable_events(session_id, settings) if not event.in_session]
    return parse_table(raw, rows, load_tiers(settings), known, nearby)
