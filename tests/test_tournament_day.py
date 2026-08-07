"""Journees de tournoi : regrouper les matchs comme le tournoi les joue."""

from __future__ import annotations

from datetime import UTC, datetime

from myassistantbet.services import tournament_day

TZ = "Europe/Paris"


def _keys(*events: tuple[int, str, str]) -> dict[int, str]:
    return tournament_day.day_keys(events, TZ)


def test_la_session_de_nuit_reste_avec_sa_soiree() -> None:
    """Le cas qui motive tout le module. A Montreal la session du soir commence
    vers 19h locales : le dernier match part a 23h a Paris, le suivant a 01h le
    lendemain. Dates a la journee civile, ils se retrouvent separes alors qu'ils
    forment la meme soiree de tournoi."""
    keys = _keys(
        (1, "canada", "2026-08-03T19:00:00Z"),  # 21h00 a Paris, le 3
        (2, "canada", "2026-08-03T23:00:00Z"),  # 01h00 a Paris, le 4
    )

    assert keys == {1: "2026-08-03", 2: "2026-08-03"}


def test_un_match_de_nuit_a_melbourne_reste_au_jour_meme() -> None:
    """L'inverse exact, et c'est pourquoi une heure de bascule fixe ne convient
    pas : a 01h a Paris, l'Open d'Australie ouvre sa journee, il ne termine pas
    celle de la veille. Une bascule a 06h rangerait ce match la veille."""
    keys = _keys(
        (1, "melbourne", "2026-01-19T23:30:00Z"),  # 00h30 a Paris, le 20
        (2, "melbourne", "2026-01-20T05:00:00Z"),  # 06h00 a Paris, le 20
    )

    assert keys == {1: "2026-01-20", 2: "2026-01-20"}


def test_un_trou_plus_long_ouvre_une_nouvelle_journee() -> None:
    keys = _keys(
        (1, "canada", "2026-08-03T19:00:00Z"),
        (2, "canada", "2026-08-03T23:00:00Z"),
        (3, "canada", "2026-08-04T15:00:00Z"),  # 16h apres : autre journee
    )

    assert keys == {1: "2026-08-03", 2: "2026-08-03", 3: "2026-08-04"}


def test_deux_tournois_ne_partagent_pas_leurs_trous() -> None:
    """Le regroupement est par competition : deux tournois joues sur deux
    continents n'ont aucune raison de decouper leurs journees ensemble."""
    keys = _keys(
        (1, "canada", "2026-08-03T19:00:00Z"),
        (2, "melbourne", "2026-08-03T23:00:00Z"),
    )

    # Isole, le second match ouvre sa propre journee, datee de son heure locale.
    assert keys == {1: "2026-08-03", 2: "2026-08-04"}


def test_un_horodatage_illisible_est_ecarte() -> None:
    """Mieux vaut un match absent du filtre qu'un match range au hasard."""
    assert _keys((1, "canada", "pas une date")) == {}


def test_les_journees_portent_leur_compte_et_leurs_noms() -> None:
    """Sans le compte, choisir une date revient a tenter sa chance."""
    now = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)

    jours = tournament_day.options(
        ["2026-08-03", "2026-08-03", "2026-08-04", "2026-08-05"], TZ, now
    )

    assert [(jour.key, jour.label, jour.count) for jour in jours] == [
        ("2026-08-03", "aujourd'hui · 03/08", 2),
        ("2026-08-04", "demain · 04/08", 1),
        ("2026-08-05", "05/08", 1),
    ]
