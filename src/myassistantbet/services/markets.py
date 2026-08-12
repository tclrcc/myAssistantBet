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
#:
#: Les deux variantes « alternate » manquaient, et c'est ce qui rendait un bloc de
#: tennis pauvre : `spreads` et `totals` ne servent que **la ligne principale**,
#: quand leurs variantes servent toute l'echelle. Verifie en reel sur un match du
#: Canadian Open : Pinnacle rend 10 cotes de handicap jeux et 10 cotes de total
#: jeux, la ou le bloc n'en affichait que deux et deux. Deux credits de plus par
#: match, pour vingt cotes.
#:
#: Les marches par set (`h2h_s1`, `totals_s1`, …) restent demandes mais ne sont
#: servis par **aucun book europeen** — verifie avec `regions=eu`, donc tous books
#: confondus. Ils sont conserves ici parce que la ligne « Non servis » du prompt
#: tire son sens de la difference entre demande et recu : les retirer ferait
#: disparaitre l'information au lieu de la dire.
TENNIS_MARKETS: tuple[str, ...] = (
    "h2h",
    "spreads",
    "alternate_spreads",
    "totals",
    "alternate_totals",
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

#: Marche de l'etage A **sans equivalent profond**, a reclamer a l'etage B quand
#: l'etage A n'a rien ramene sur cet evenement.
#:
#: `h2h` et `totals` sont normalement acquis par le scan, chez Betclic seul, et
#: les racheter couterait deux credits par match pour des cotes deja en base.
#: Mais sur une competition que **Betclic ne sert pas du tout** — Super League
#: chinoise, Veikkausliiga — l'etage A ne ramene rien, et le 1N2 n'arrivait
#: alors jamais : ni en cote, ni meme en « Non servis », puisque la ligne se
#: calcule sur cette liste. Constate en reel sur Beijing FC - Shenzhen Peng City,
#: ou l'analyse s'est rabattue sur le handicap faute de 1N2.
#:
#: `totals` n'y figure pas, et ce n'est pas un oubli : `alternate_totals` est
#: deja demande et le rendu les fusionne dans la meme ligne O/U. Le reclamer
#: couterait un credit pour une ligne deja affichee.
FOOTBALL_BASE_MARKETS: tuple[str, ...] = ("h2h",)

#: « Se qualifie » : qui passe le tour, toutes manches confondues. C'est le
#: marche que **24 manches retour d'une meme semaine** appelaient sans qu'il
#: existe nulle part — ni en cote, ni en « Non servis », donc dans l'angle mort
#: que le prompt reserve a la section F.
#:
#: Il traduit directement ce que la ligne `Scenario` calcule : un tie plie a 0-3
#: y vaut un prix, la ou le 1N2 du match retour ne dit rien de la qualification.
#:
#: **Demande sur les seules coupes** (`KNOCKOUT_CATEGORIES`) : ailleurs il n'a
#: aucun sens, et un credit par match pour un constat vide serait paye avant
#: d'etre memorise. Sur un tour aller simple il ne sera pas servi non plus, et
#: c'est tres bien : l'absence devient une ligne « Non servis », ce qui est
#: exactement le livrable qui manquait.
KNOCKOUT_MARKETS: tuple[str, ...] = ("to_qualify",)


def markets_for(
    sport_key: str,
    oddsapi_sport_key: str,
    settings: Settings,
    base_served: bool = True,
    knockout: bool = False,
) -> tuple[str, ...]:
    """Marches a demander pour cet evenement, props incluses si la ligue y donne droit.

    `knockout` dit si la competition se joue a elimination directe : « Se
    qualifie » n'est demande que la. Le drapeau vient du **niveau** de la
    competition, deja saisi — le deduire d'un libelle serait une invention, et le
    stocker une seconde fois l'aurait fait diverger.

    `base_served` dit si l'etage A a ramene ses cotes sur cet evenement. A faux,
    le 1N2 est reclame en plus : voir `FOOTBALL_BASE_MARKETS`. Le tennis demande
    deja `h2h` en toute circonstance, la question ne s'y pose pas.
    """
    if sport_key == "tennis":
        return TENNIS_MARKETS
    if sport_key != "football":
        return ()
    base = () if base_served else FOOTBALL_BASE_MARKETS
    if knockout:
        base += KNOCKOUT_MARKETS
    if oddsapi_sport_key in settings.player_props_whitelist:
        return base + FOOTBALL_MARKETS + PLAYER_PROP_MARKETS
    return base + FOOTBALL_MARKETS
