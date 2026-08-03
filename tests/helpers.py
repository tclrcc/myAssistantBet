"""Constantes partagees par les tests."""

from __future__ import annotations

from datetime import UTC, datetime

#: Instant de reference des tests. La fenetre de scan par defaut couvre alors
#: le 3 et le 4 aout 2026 (heure de Paris).
NOW = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)

#: Headers de quota renvoyes par The Odds API sur un scan a deux marches.
QUOTA_HEADERS = {
    "x-requests-remaining": "4821",
    "x-requests-used": "179",
    "x-requests-last": "2",
}
