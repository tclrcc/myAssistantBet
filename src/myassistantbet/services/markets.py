"""Marches que l'application demande a The Odds API, par sport.

Extrait de `services/enrich.py` parce que deux modules en ont besoin et qu'une
seule doit en detenir la definition. `enrich` s'en sert pour savoir quoi
acheter ; `session` s'en sert pour savoir ce qui a ete demande et n'est jamais
revenu — sans quoi un marche absent d'un bloc reste indiscernable d'un marche
jamais reclame.

La copier des deux cotes aurait fini par les faire diverger, et le prompt
aurait alors annonce « Non servis » sur un marche que l'outil ne demande plus.
"""

from __future__ import annotations

from ..config import Settings

#: Marches profonds football (SPEC.md section 4).
FOOTBALL_MARKETS: tuple[str, ...] = (
    "correct_score",
    "correct_score_h1",
    "totals_h1",
    "alternate_totals",
    "btts",
    "btts_h1",
    "double_chance",
    "halftime_fulltime",
    "team_totals",
    "alternate_team_totals",
    "alternate_totals_corners",
    "alternate_totals_cards",
    "corners_1x2",
    "alternate_spreads",
)

#: Marches profonds tennis (SPEC.md section 4).
TENNIS_MARKETS: tuple[str, ...] = (
    "h2h",
    "spreads",
    "totals",
    "h2h_s1",
    "h2h_s2",
    "spreads_s1",
    "totals_s1",
    "alternate_totals_s1",
)

#: Props buteurs : servies uniquement sur quelques competitions (liste blanche).
PLAYER_PROP_MARKETS: tuple[str, ...] = (
    "player_goal_scorer_anytime",
    "player_first_goal_scorer",
)


def markets_for(sport_key: str, oddsapi_sport_key: str, settings: Settings) -> tuple[str, ...]:
    """Marches a demander pour cet evenement, props incluses si la ligue y donne droit."""
    if sport_key == "tennis":
        return TENNIS_MARKETS
    if sport_key != "football":
        return ()
    if oddsapi_sport_key in settings.player_props_whitelist:
        return FOOTBALL_MARKETS + PLAYER_PROP_MARKETS
    return FOOTBALL_MARKETS
