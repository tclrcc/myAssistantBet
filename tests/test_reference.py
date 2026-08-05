"""Books de reference : combler les trous du principal, sans jamais le doubler."""

from __future__ import annotations

from typing import Any

from myassistantbet.services import reference

PRIMARY = "betclic_fr"


def _book(key: str, *markets: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": key.title(),
        "markets": [
            {"key": market, "outcomes": [{"name": "A", "price": 1.5}]} for market in markets
        ],
    }


def _payload(*books: dict[str, Any]) -> dict[str, Any]:
    return {"id": "evt", "bookmakers": list(books)}


def _markets(payload: dict[str, Any]) -> dict[str, list[str]]:
    return {
        book["key"]: sorted(market["key"] for market in book["markets"])
        for book in payload["bookmakers"]
    }


def test_le_principal_garde_ses_marches() -> None:
    """Un marche servi par les deux revient au principal, jamais a la reference."""
    payload = _payload(_book(PRIMARY, "h2h"), _book("pinnacle", "h2h", "totals"))

    merged = reference.merge(payload, PRIMARY, ("pinnacle",))

    assert _markets(merged) == {PRIMARY: ["h2h"], "pinnacle": ["totals"]}


def test_une_seule_source_par_marche() -> None:
    """Deux prix pour la meme issue inviteraient a la comparaison entre books."""
    payload = _payload(
        _book(PRIMARY, "h2h"), _book("pinnacle", "totals"), _book("onexbet", "totals", "spreads")
    )

    merged = reference.merge(payload, PRIMARY, ("pinnacle", "onexbet"))

    assert _markets(merged) == {PRIMARY: ["h2h"], "pinnacle": ["totals"], "onexbet": ["spreads"]}
    vus = [market["key"] for book in merged["bookmakers"] for market in book["markets"]]
    assert len(vus) == len(set(vus)), "aucun marche ne doit apparaitre deux fois"


def test_l_ordre_des_references_est_respecte() -> None:
    payload = _payload(
        _book(PRIMARY, "h2h"), _book("onexbet", "totals"), _book("pinnacle", "totals")
    )

    merged = reference.merge(payload, PRIMARY, ("pinnacle", "onexbet"))

    assert _markets(merged) == {PRIMARY: ["h2h"], "pinnacle": ["totals"]}


def test_un_book_non_choisi_est_ignore() -> None:
    """On ne stocke que les books decides : le reste n'a rien a faire en base."""
    payload = _payload(_book(PRIMARY, "h2h"), _book("tipico_de", "totals", "spreads"))

    merged = reference.merge(payload, PRIMARY, ("pinnacle",))

    assert _markets(merged) == {PRIMARY: ["h2h"]}


def test_sans_reference_configuree_rien_ne_change() -> None:
    payload = _payload(_book(PRIMARY, "h2h", "totals"))

    merged = reference.merge(payload, PRIMARY, ())

    assert _markets(merged) == {PRIMARY: ["h2h", "totals"]}


def test_le_principal_absent_ne_bloque_pas_la_reference() -> None:
    """Betclic peut ne rien servir du tout sur un match."""
    payload = _payload(_book("pinnacle", "h2h", "totals"))

    merged = reference.merge(payload, PRIMARY, ("pinnacle",))

    assert _markets(merged) == {"pinnacle": ["h2h", "totals"]}


def test_une_charge_vide_ne_casse_rien() -> None:
    assert reference.merge({}, PRIMARY, ("pinnacle",))["bookmakers"] == []
    assert reference.merge({"bookmakers": []}, PRIMARY, ())["bookmakers"] == []


def test_les_marches_empruntes_sont_identifies() -> None:
    payload = _payload(_book(PRIMARY, "h2h"), _book("pinnacle", "totals", "spreads"))

    merged = reference.merge(payload, PRIMARY, ("pinnacle",))

    assert reference.borrowed_markets(merged, PRIMARY) == {
        "totals": "pinnacle",
        "spreads": "pinnacle",
    }


def test_rien_d_emprunte_quand_le_principal_suffit() -> None:
    payload = _payload(_book(PRIMARY, "h2h", "totals"))

    merged = reference.merge(payload, PRIMARY, ("pinnacle",))

    assert reference.borrowed_markets(merged, PRIMARY) == {}


def test_le_libelle_d_une_reference_porte_sa_mention() -> None:
    """Le prompt doit lire « (ref.) » : une telle cote n'est pas jouable telle quelle."""
    from myassistantbet.services.labels import bookmaker_label

    assert bookmaker_label("betclic_fr") == "Betclic"
    assert bookmaker_label("pinnacle").endswith("(ref.)")
    assert bookmaker_label("onexbet").endswith("(ref.)")
