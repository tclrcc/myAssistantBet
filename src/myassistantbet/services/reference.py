"""Bookmakers de reference : combler les marches que le book principal ne sert pas.

Betclic propose sur son site des marches que The Odds API n'expose pas pour
`betclic_fr` : handicap jeux, total jeux, score par set. D'autres bookmakers de
la meme zone les servent, et interroger dix books coute exactement le meme prix
qu'un seul — le cout vaut `marches x ceil(books / 10)`.

Regle non negociable : **une seule source par marche**. Le book principal est
toujours prioritaire ; un book de reference n'est retenu que pour un marche que
le principal n'a pas servi. Sans cette regle, deux prix coexisteraient pour la
meme issue, le bloc en afficherait un au hasard, et l'outil inviterait a la
comparaison de cotes entre bookmakers que SPEC interdit.

Les cotes de reference ne sont pas jouables telles quelles : elles disent ou se
situe le marche, pas le prix qu'on obtiendra. C'est le rendu qui doit le dire.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _key(bookmaker: dict[str, Any]) -> str:
    return str(bookmaker.get("key") or "")


def merge(payload: dict[str, Any], primary: str, order: tuple[str, ...] = ()) -> dict[str, Any]:
    """Ne garde qu'une source par marche : le principal, puis les references.

    `order` fixe la priorite des books de reference. Un book absent de `order`
    et different du principal est ignore : on ne stocke que ce qu'on a choisi.
    """
    books = {_key(book): book for book in payload.get("bookmakers") or [] if _key(book)}
    ranked = [name for name in (primary, *order) if name in books]

    kept: dict[str, dict[str, Any]] = {}
    claimed: set[str] = set()
    for name in ranked:
        book = books[name]
        markets = [
            market
            for market in book.get("markets") or []
            if market.get("key") and market["key"] not in claimed
        ]
        if not markets:
            continue
        claimed.update(market["key"] for market in markets)
        kept[name] = {**book, "markets": markets}

    merged = {**payload, "bookmakers": [kept[name] for name in ranked if name in kept]}
    borrowed = {
        market["key"]: name
        for name, book in kept.items()
        if name != primary
        for market in book["markets"]
    }
    if borrowed:
        logger.info(
            "Marches empruntes a un book de reference : %s",
            ", ".join(f"{market} <- {book}" for market, book in sorted(borrowed.items())),
        )
    return merged


def borrowed_markets(payload: dict[str, Any], primary: str) -> dict[str, str]:
    """Marches de la charge utile fusionnee qui ne viennent pas du book principal."""
    return {
        market["key"]: _key(book)
        for book in payload.get("bookmakers") or []
        if _key(book) != primary
        for market in book.get("markets") or []
        if market.get("key")
    }
