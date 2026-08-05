"""Memoire des marches qu'une competition sert reellement.

The Odds API reserve ses marches additionnels a certains sports et a une
selection de bookmakers. Demander les marches par set sur du tennis servi par
Betclic renvoie une reponse vide — facturee. Sans memoire, l'etage B repaie ce
meme constat a chaque session.

Ce module observe ce qui revient, et laisse `enrich` ne redemander que ce qui a
deja ete servi au moins une fois. Aucun appel externe : il ne fait que lire et
ecrire ce que l'etage B a constate.

Un constat ne vaut que pour les books qui l'ont produit : ils font donc partie
de la cle. Sans cela, un marche constate vide chez Betclic seul resterait
condamne apres l'ajout d'un book de reference qui, lui, le sert.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.oddsapi import DEFAULT_BOOKMAKER

logger = logging.getLogger(__name__)

#: Marche d'ancrage : toujours demande, meme si tout le reste est connu vide.
#: Sans lui la reponse n'aurait aucune cote a rattacher a l'evenement.
ANCHOR_MARKET = "h2h"

#: Nombre de constats vides avant de cesser de demander un marche. Un seul match
#: peut ne pas proposer un marche que la competition sert habituellement ; deux
#: constats independants suffisent a conclure.
GIVE_UP_AFTER = 2


def query_books(settings: Settings | None = None) -> tuple[str, ...]:
    """Books interroges par l'etage B : le principal, puis les references."""
    settings = settings or get_settings()
    return (DEFAULT_BOOKMAKER, *settings.reference_books)


def books_key(books: Sequence[str] | None = None, settings: Settings | None = None) -> str:
    """Empreinte stable d'un ensemble de books.

    Triee : reordonner `REFERENCE_BOOKMAKERS` ne doit pas faire croire a un
    ensemble different et rejeter tout ce qui a ete appris. `None` designe la
    configuration courante ; un tuple vide, un ensemble inconnu.
    """
    chosen = query_books(settings) if books is None else books
    return ",".join(sorted({book.strip() for book in chosen if book.strip()}))


def _books_set(books: Sequence[str] | None, settings: Settings | None) -> set[str]:
    key = books_key(books, settings)
    return set(key.split(",")) if key else set()


def markets_in(payload: dict[str, Any]) -> set[str]:
    """Cles de marches effectivement presentes dans une reponse de cotes."""
    return {
        market.get("key")
        for bookmaker in payload.get("bookmakers") or []
        for market in bookmaker.get("markets") or []
        if market.get("key")
    }


def record(
    competition_id: int | None,
    requested: tuple[str, ...],
    payload: dict[str, Any],
    settings: Settings | None = None,
    books: Sequence[str] | None = None,
) -> None:
    """Note, pour cette competition et ces books, quels marches ont ete servis."""
    if not competition_id or not requested:
        return
    served = markets_in(payload)
    key = books_key(books, settings)
    now = utcnow()
    with connect(settings) as conn:
        for market in requested:
            hit = 1 if market in served else 0
            conn.execute(
                "INSERT INTO market_coverage (competition_id, market_key, books, served, "
                "                             checks, updated_at) VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(competition_id, market_key, books) DO UPDATE SET "
                "  served = MAX(served, excluded.served), "
                "  checks = checks + 1, "
                "  updated_at = excluded.updated_at",
                (competition_id, market, key, hit, now),
            )


def barren(
    competition_id: int | None,
    settings: Settings | None = None,
    books: Sequence[str] | None = None,
) -> set[str]:
    """Marches constates vides assez souvent pour cesser de les demander.

    Un constat vaut pour les books qui l'ont produit et pour tout ensemble plus
    etroit : si dix books ne servent pas un marche, aucun sous-ensemble ne le
    sert. L'inverse est faux, et c'est tout l'interet — ajouter un book de
    reference rouvre la question au lieu de la laisser tranchee par un constat
    plus pauvre.
    """
    if not competition_id:
        return set()
    return barren_by_competition([competition_id], settings, books).get(competition_id, set())


def barren_by_competition(
    competition_ids: Sequence[int],
    settings: Settings | None = None,
    books: Sequence[str] | None = None,
) -> dict[int, set[str]]:
    """Marches abandonnes, par competition. Une seule requete pour toute une session."""
    wanted = {int(item) for item in competition_ids if item}
    if not wanted:
        return {}
    asked = _books_set(books, settings)
    placeholders = ",".join("?" * len(wanted))
    with connect(settings) as conn:
        rows = conn.execute(
            f"SELECT competition_id, market_key, books FROM market_coverage "
            f"WHERE competition_id IN ({placeholders}) AND served = 0 AND checks >= ?",
            (*sorted(wanted), GIVE_UP_AFTER),
        ).fetchall()

    dead: dict[int, set[str]] = {}
    for row in rows:
        seen = set(row["books"].split(",")) if row["books"] else set()
        if not asked <= seen:
            continue
        dead.setdefault(int(row["competition_id"]), set()).add(row["market_key"])
    return dead


def useful(
    competition_id: int | None,
    markets: tuple[str, ...],
    settings: Settings | None = None,
    books: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Retire les marches que cette competition n'a jamais servis.

    Renvoie un tuple vide si plus rien d'utile ne reste : l'appel serait payant
    pour des cotes que l'etage A possede deja.
    """
    settings = settings or get_settings()
    dead = barren(competition_id, settings, books)
    if not dead:
        return markets
    kept = tuple(market for market in markets if market not in dead)
    if kept == (ANCHOR_MARKET,) or not kept:
        return ()
    return kept


def summary(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Etat de la couverture, par competition. Pour l'affichage et le diagnostic.

    Un marche teste sous deux ensembles de books reste un marche : les constats
    sont replies par marche avant d'etre comptes.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.label, c.oddsapi_key, SUM(m.servi) AS servis, COUNT(*) AS testes "
            "FROM (SELECT competition_id, market_key, MAX(served) AS servi "
            "      FROM market_coverage GROUP BY competition_id, market_key) m "
            "JOIN competitions c ON c.id = m.competition_id "
            "GROUP BY c.id ORDER BY c.label"
        ).fetchall()
    return [dict(row) for row in rows]


def reset(competition_id: int, settings: Settings | None = None) -> int:
    """Oublie ce qui a ete constate pour cette competition. Renvoie le nombre efface.

    Un bookmaker peut se mettre a servir un marche qu'il ignorait, et un constat
    fait un jour creux peut etre trompeur. Sans ce retour en arriere,
    l'apprentissage serait une porte a sens unique.
    """
    with connect(settings) as conn:
        cursor = conn.execute(
            "DELETE FROM market_coverage WHERE competition_id = ?", (competition_id,)
        )
    logger.info(
        "Couverture reinitialisee pour la competition %d : %d ligne(s)",
        competition_id,
        cursor.rowcount,
    )
    return cursor.rowcount


def by_competition(settings: Settings | None = None) -> dict[int, dict[str, int]]:
    """Etat de la couverture indexe par competition, pour l'affichage."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT competition_id, SUM(servi) AS servis, COUNT(*) AS testes "
            "FROM (SELECT competition_id, market_key, MAX(served) AS servi "
            "      FROM market_coverage GROUP BY competition_id, market_key) "
            "GROUP BY competition_id"
        ).fetchall()
    return {
        int(row["competition_id"]): {"servis": int(row["servis"]), "testes": int(row["testes"])}
        for row in rows
    }
