"""Classements Elo tennis, publies par Tennis Abstract.

Pas une API : deux pages HTML statiques, une par circuit, mises a jour une fois
par semaine. Gratuit, sans cle, sans quota — **rien n'est donc ecrit dans
`api_usage`**, qui ne comptabilise que des credits consommes. L'appel est
journalise comme tous les autres.

Trois particularites du site, toutes verifiees :

- le domaine apex `tennisabstract.com` ne repond pas, seul `www.` fonctionne ;
- sans en-tete `User-Agent` de navigateur, la reponse est un HTTP 403 ;
- son `robots.txt` interdit `/jsfrags/`, `/jsmatches/` et `/jsplayers/`, c'est
  a dire les pages joueur et match. `/reports/` est autorise : ce module ne
  touche qu'a ces pages-la, et rien d'autre ne doit y etre ajoute.

Ce module rend des lignes brutes. **Il ne convertit jamais un ecart d'Elo en
probabilite** — la page le fait pourtant en toutes lettres — parce que ce
serait produire une probabilite implicite, interdite par la section 9.
"""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from typing import Any

from .base import BaseHTTPClient, ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "tennisabstract"
BASE_URL = "https://www.tennisabstract.com"

#: Sans cela, le site repond 403.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

#: Rapports Elo par circuit. Seuls ceux-la sont autorises par le robots.txt.
REPORTS = {
    "atp": "/reports/atp_elo_ratings.html",
    "wta": "/reports/wta_elo_ratings.html",
}

#: En-tetes de colonne -> champ. Le rapprochement se fait par **libelle** et non
#: par position : une colonne ajoutee en tete du tableau ne doit pas decaler
#: silencieusement l'Elo sur gazon dans la colonne de l'Elo sur terre.
COLUMNS = {
    "elo rank": "elo_rank",
    "player": "player",
    "age": "age",
    "elo": "elo",
    "helo rank": "hard_rank",
    "helo": "hard_elo",
    "celo rank": "clay_rank",
    "celo": "clay_elo",
    "gelo rank": "grass_rank",
    "gelo": "grass_elo",
    "peak elo": "peak_elo",
    "peak month": "peak_month",
    "atp rank": "tour_rank",
    "wta rank": "tour_rank",
}

#: Champs numeriques, convertis apres lecture. Le reste reste du texte.
INTEGERS = frozenset({"elo_rank", "hard_rank", "clay_rank", "grass_rank", "tour_rank"})
FLOATS = frozenset({"elo", "hard_elo", "clay_elo", "grass_elo", "peak_elo", "age"})


def _clean(text: str) -> str:
    """Espaces insecables et blancs multiples ramenes a un espace simple."""
    return " ".join(text.replace("\xa0", " ").split())


class _TableParser(HTMLParser):
    """Extrait les lignes d'un tableau HTML, cellule par cellule.

    Volontairement naif : il collecte le texte de chaque `<td>` / `<th>` sans
    se soucier des balises imbriquees — le nom du joueur est dans un `<a>`, et
    seul son texte nous interesse.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.header: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._row_is_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
            self._row_is_header = False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._row_is_header = True

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row_is_header:
                # Le premier en-tete rencontre fait foi : la page en contient
                # un seul, mais une page remaniee ne doit pas ecraser celui-ci.
                if not self.header:
                    self.header = self._row
            elif self._row:
                self.rows.append(self._row)
            self._row = None


def parse_elo_report(html: str) -> list[dict[str, Any]]:
    """Lit un rapport Elo et rend une ligne par joueur.

    Une ligne sans nom ou sans Elo est ecartee : elle ne servirait a rien et
    polluerait le rapprochement des noms.
    """
    parser = _TableParser()
    parser.feed(html)

    if not parser.header:
        raise ProviderError(PROVIDER, "elo", "aucun en-tete de tableau trouve")

    positions = {
        index: COLUMNS[_clean(cell).lower()]
        for index, cell in enumerate(parser.header)
        if _clean(cell).lower() in COLUMNS
    }
    if "player" not in positions.values() or "elo" not in positions.values():
        raise ProviderError(
            PROVIDER, "elo", f"colonnes inattendues : {', '.join(parser.header)[:120]}"
        )

    players: list[dict[str, Any]] = []
    for cells in parser.rows:
        entry: dict[str, Any] = {}
        for index, field in positions.items():
            if index < len(cells):
                entry[field] = _as_number(field, cells[index])
        if entry.get("player") and entry.get("elo") is not None:
            players.append(entry)
    return players


def _as_number(field: str, raw: str) -> Any:
    value = _clean(raw)
    if not value:
        return None
    if field in INTEGERS:
        try:
            return int(float(value))
        except ValueError:
            return None
    if field in FLOATS:
        try:
            return float(value)
        except ValueError:
            return None
    return value


class TennisAbstractClient(BaseHTTPClient):
    """Acces en lecture aux classements Elo. Aucun quota, aucun credit."""

    provider_name = PROVIDER
    base_url = BASE_URL

    async def elo_ratings(self, tour: str) -> list[dict[str, Any]]:
        """Classement Elo d'un circuit (`atp` ou `wta`)."""
        if tour not in REPORTS:
            raise ProviderError(PROVIDER, tour, f"circuit inconnu : {tour}")

        endpoint = REPORTS[tour]
        result = await self._get(endpoint, headers={"User-Agent": USER_AGENT}, as_text=True)
        if not isinstance(result.data, str):
            raise ProviderError(PROVIDER, endpoint, "reponse non textuelle")

        players = parse_elo_report(result.data)
        logger.info(
            "%s %s — %d joueur(s), %d ms%s",
            PROVIDER,
            endpoint,
            len(players),
            result.duration_ms,
            " (cache dev)" if result.from_cache else "",
        )
        return players
