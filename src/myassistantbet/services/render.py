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

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Settings
from .coverage import query_books
from .labels import REFERENCE_SUFFIX, affiche, bookmaker_label

INDENT = "  "
LABEL_WIDTH = 12
CONTINUATION = INDENT + " " * LABEL_WIDTH

#: Longueur utile d'un libelle. Le separateur entre le libelle et sa valeur
#: n'existe pas en propre : c'est le remplissage du champ de `LABEL_WIDTH` qui
#: le fabrique. Un libelle qui occupe le champ entier ne laisse donc **rien** —
#: constate en reel, « Buts encais. » faisait exactement 12 caracteres et
#: sortait colle a sa valeur (`Buts encais.Lillestrom >0.5 10/15`). Les cles de
#: marche etaient deja tronquees ici ; les libelles de contexte, eux, ne
#: passaient par aucune troncature. Un test verifie le registre entier.
LABEL_MAX = LABEL_WIDTH - 1

#: Nombre de cotes de score exact retenues, et nombre par ligne rendue.
CORRECT_SCORE_KEEP = 10
CORRECT_SCORE_PER_LINE = 6
#: Nombre de lignes O/U retenues autour de la ligne principale.
TOTALS_KEEP = 5

SPORT_LABELS = {"football": "FOOT", "tennis": "TENNIS", "cycling": "CYCLISME"}

UNAVAILABLE = "donnees non disponibles pour cette competition"

#: Libelle de la ligne qui enumere les marches demandes et jamais obtenus.
UNSERVED_LABEL = "Non servis"

#: La note de cette ligne, **avec son perimetre**.
#:
#: « aucun book interroge ne les sert » etait exact au mot pres et trompeur a la
#: lecture : trois books sont interroges, et le lecteur — humain ou modele —
#: comprenait « ce marche n'existe pas sur cette competition ». Mesure du
#: 14/08/2026 : `btts` sur la Ligue 2 est servi par 1xBet, William Hill et
#: Matchbook, donc bien present chez The Odds API ; il ne l'est simplement pas
#: chez les trois books que nous interrogeons.
#:
#: Le nombre et les noms se **derivent** de `query_books()`, jamais ecrits en
#: dur : ajouter un book de reference met la phrase a jour sans qu'on y touche,
#: et c'est la meme source que celle qui decide des appels — deux ecritures
#: auraient fini par decrire un perimetre qui n'est pas celui qu'on interroge.
UNSERVED_NOTE = "aucun des {count} books interroges ({books}) ne les sert sur cette competition"
#: Meme ligne, autre cause : le match n'existe pas chez le fournisseur de
#: cotes, et le book de substitution ne sert pas tout. Le distinguer evite de
#: chercher un reglage la ou il n'y a qu'une offre plus etroite.
#:
#: Elle porte deja son perimetre — « le book de substitution » **est** l'ensemble
#: interroge, et il n'y en a qu'un — donc rien a enumerer ici.
UNSERVED_NOTE_SUBSTITUTE = "non servis par le book de substitution sur ce match"

#: Troisieme note, et elle dit **ce qui a ete observe** : ces marches sont servis
#: ailleurs dans la competition, ils ne sont pas arrives sur ce match-la.
#:
#: Elle manquait, et son absence faisait mentir la ligne. `_unserved_for` a trois
#: causes ; la deuxieme — demande et non recu **pour ce match** — se rendait sous
#: la note de competition, qui affirme donc au-dela de ce qui a ete constate. Or
#: la consequence de lecture n'est pas la meme : sur la competition, la ligne dit
#: a l'analyste de ne pas chercher ; sur un seul match, elle ne le dit plus — ce
#: peut n'etre qu'un trou de ce releve-la.
UNSERVED_NOTE_MATCH = (
    "demandes et non revenus sur ce match — servis ailleurs dans la competition, "
    "donc a verifier plutot qu'a ecarter"
)

#: Troisieme etat, distinct des deux precedents : le marche **est la**, mais
#: aucun de ses prix ne vient du book principal. Le prix affiche situe le marche,
#: ce n'est pas celui qu'on obtiendra — il reste a relever avant de miser.
#:
#: Mesure qui l'a fait naitre : sur 127 matchs de tennis a venir, `betclic_fr`
#: ne sert **que** le `h2h` via The Odds API. Tout le handicap jeux et tout le
#: total de jeux viennent de Pinnacle. Chaque ligne le disait par son
#: `[Pinnacle (ref.)]`, mais il fallait les lire toutes pour le voir.
#:
#: **Ce n'est pas « non jouable », et le premier libelle se trompait de mot.**
#: Betclic sert bien ces marches sur son site : c'est notre collecte qui ne les
#: remonte pas, pas l'offre qui manque. Nommer la ligne « Non jouable » a fait
#: exactement le degat qu'elle devait empecher — une analyse reelle a renonce a
#: deux angles de jeux pour se rabattre sur le vainqueur, alors que les paris
#: etaient parfaitement posables. Elle dit donc ce qu'il y a a faire : relever le
#: prix chez le book principal avant de miser.
UNPLAYABLE_LABEL = "A relever"

#: Une incoherence **constatee** dans le releve lui-meme, et non une donnee
#: manquante. Les autres lignes de fin de bloc disent ce qui n'est pas la ;
#: celle-ci dit que ce qui est la ne doit pas etre lu tel quel.
ALERT_LABEL = "Alerte"

#: Ecart minimal, en probabilite implicite, entre « gagne » et « gagne ou fait
#: nul » en dessous duquel le signe d'un handicap ne se controle plus. Sur un
#: favori extreme les deux paris se confondent — 0.952 contre 0.981 a la cote
#: 1.05 — et l'ecart tombe sous le bruit qui separe deux books. Au-dessus la
#: separation est franche : 0.26 sur la Supercoupe qui a revele le defaut, 0.21
#: sur le plus court des trois blocs fautifs. Sous le seuil, aucune alerte : un
#: silence vaut mieux qu'une accusation que la donnee ne porte pas.
HANDICAP_ALERT_MARGIN = 0.05

#: Ecart minimal, en minutes, au-dela duquel l'en-tete compte le temps. En
#: dessous, un releve n'a rien traverse et l'ecrire ferait du bruit sur les blocs
#: les plus frais. Meme regle que l'age du releve meteo : on ne compte le temps
#: qu'une fois qu'il commence a vouloir dire quelque chose.
#:
#: **Il doit rester nettement sous `LINEUP_LEAD_MINUTES`**, sinon les deux seuils
#: coincident et la mention des compositions accompagne *toutes* les lignes
#: rendues — elle cesse alors d'etre un signal pour devenir un decor. Trouve en
#: ecrivant le test : les deux valaient soixante.
LEAD_TIME_MIN_MINUTES = 15

#: Heure de publication des compositions, en minutes avant le coup d'envoi. Un
#: releve anterieur n'a pas vu les onze, et c'est **le seul moment a heure connue
#: ou le marche se reajuste en masse** — mesure ailleurs dans le projet : les
#: clubs publient environ une heure avant, et l'endpoint des compositions rend
#: zero equipe a 2h30 du coup d'envoi contre deux a 8 minutes.
LINEUP_LEAD_MINUTES = 60

#: Les paliers que les cotes **de ce bloc** rendent atteignables. Un palier est
#: une bande de cote : si aucune cote du bloc n'y tombe, aucune selection de ce
#: match ne pourra s'y ranger, quel que soit l'angle. Mesure qui l'a fait naitre :
#: sur un lot de quatre quarts de finale, la cote la plus basse d'un bloc valait
#: 1.71 — aucun 🟢 SAFE n'en sortirait jamais, et rien ne le disait.
#:
#: La valeur est calculee par `prompt.py`, qui detient les paliers : ce module
#: n'en connait rien et ne doit pas commencer, ils vivent en base.
TIERS_LABEL = "Paliers"


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
    #: Identifiant du match. Le rendu ne s'en sert pas — un bloc se lit sans —
    #: mais le prompt archive ses matchs pour se donner un denominateur de taux
    #: de selection, et il ne peut le faire qu'a partir de ce qu'il a rendu.
    event_id: int = 0
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
    #: Marches demandes et non revenus **sur ce match**, alors que la competition
    #: les sert ailleurs. Tenus a part : leur note dit ce qui a ete observe, et
    #: la consequence de lecture differe — celle-ci n'interdit pas de chercher.
    unserved_here: list[str] = field(default_factory=list)
    #: Vrai quand les cotes viennent d'un book de repli et non du fournisseur
    #: principal : la cause d'une absence n'est alors pas la meme.
    substitute: bool = False
    #: Paliers atteignables sur ce bloc, deja formates par `prompt.py`. Vide
    #: quand le lot n'a pas de bandes reglees, ou quand le bloc n'a aucune cote.
    tiers_line: str = ""
    #: Coup d'envoi precedent et instant du constat, quand l'horaire a bouge de
    #: plus de `LEAD_TIME_MIN_MINUTES`. Le fait dominant d'une soiree d'orages
    #: peut etre un report de cinq heures, et l'en-tete ne portait que l'heure
    #: du moment — le decalage etait a retrouver ailleurs.
    previous_local: datetime | None = None
    shifted_local: datetime | None = None


# -- Formatage elementaire --------------------------------------------------


def price(value: float) -> str:
    return f"{value:.2f}"


def _point(value: float) -> str:
    """Une ligne de handicap : 2.5 et non 2.50, mais 0.25 conserve ses decimales."""
    text = f"{value:g}"
    return text


def line(label: str, value: str) -> str:
    """`  Classement  Lyon 4e` — libelle cale sur `LABEL_WIDTH`, puis la valeur.

    Le `max` n'est pas une precaution de principe : sans lui, un libelle aussi
    long que le champ colle sa valeur au dernier caractere. Un libelle trop
    long decale desormais sa ligne d'une colonne, ce qui se voit et se corrige,
    au lieu de souder deux mots, ce qui se lit de travers. Le vrai garde-fou
    reste le test sur `LABEL_MAX`.
    """
    return f"{INDENT}{label:<{max(LABEL_WIDTH, len(label) + 1)}}{value}"


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


def _signed(value: float) -> str:
    """`+1.5`, `-0.5`, `0` — le signe est toujours porte, sauf sur la ligne nulle.

    Un handicap sans signe explicite se lit du cote de celui qui le donne, donc
    de travers une fois sur deux. Le zero, lui, n'a pas de cote : l'ecrire
    « +0 » ou « -0 » inventerait une direction.
    """
    if not value:
        return "0"
    return f"+{_point(value)}" if value > 0 else _point(value)


def _by_handicap(
    event: RenderableEvent, outcomes: Iterable[Outcome]
) -> dict[float, dict[str, float]]:
    """Regroupe des issues de handicap par palier, vu du **premier nomme**.

    La cle est le handicap de l'equipe (ou du joueur) de gauche dans le titre,
    signe compris : les deux cotes d'un meme palier tombent alors sous une seule
    entree, et le signe cesse de suivre le favori. Regrouper sur la valeur
    absolue faisait designer par « -2.5 » le second joueur quand il etait favori
    et le premier sinon — les prix restaient justes, mais une selection lue a
    l'envers est l'erreur la plus couteuse que ce bloc puisse produire.

    La fonction est **partagee par le football et le tennis**, qui n'en tirent
    pas la meme forme : une seule ligne d'un cote, une echelle de l'autre. La
    convention d'ancrage, elle, ne peut pas differer — ecrite deux fois, elle
    aurait diverge, et les deux sports ne se seraient plus lus pareil.
    """
    ladder: dict[float, dict[str, float]] = {}
    for outcome in outcomes:
        if outcome.point is None:
            continue
        anchor = outcome.point if outcome.name == event.home else -outcome.point
        ladder.setdefault(anchor, {})[outcome.name] = outcome.price
    return ladder


def _main_handicap(ladder: dict[float, dict[str, float]]) -> float | None:
    """Palier principal : celui dont les deux prix sont les plus proches.

    Meme notion que `main_line` pour un total — la ligne que le book a posee au
    milieu. A defaut de palier servi des deux cotes, le plus proche de zero.
    """
    complete = {point: prices for point, prices in ladder.items() if len(prices) == 2}
    if complete:
        return min(
            complete,
            key=lambda point: (
                abs(max(complete[point].values()) - min(complete[point].values())),
                abs(point),
            ),
        )
    return min(ladder, key=abs) if ladder else None


def _render_spreads(
    event: RenderableEvent, outcomes: list[Outcome], label: str = "Handicap"
) -> list[str]:
    """Handicap : **un seul palier, ses deux cotes**, dans l'ordre du titre.

    Chaque camp choisissait auparavant sa ligne de son cote — la plus proche de
    2.00 — si bien que rien ne garantissait que les deux moities affichees
    soient les deux faces d'un meme pari, ni que leurs signes soient opposes.
    Elles sortent desormais du meme palier et le second signe est l'oppose du
    premier **par construction** : c'est la seule forme ou l'invariant ne peut
    pas se defaire.

    Sur des donnees saines la ligne rendue ne bouge pas — les deux prix les plus
    proches de 2.00 sont deja les deux faces de la ligne d'equilibre.
    """
    ladder = _by_handicap(event, outcomes)
    point = _main_handicap(ladder)
    if point is None:
        return []
    prices = ladder[point]
    fragments = [
        f"{team} {_signed(handicap)} {price(prices[team])}"
        for team, handicap in ((event.home, point), (event.away, -point))
        if team in prices
    ]
    return [line(label, " | ".join(fragments))] if fragments else []


def _render_spread_ladder(event: RenderableEvent, outcomes: list[Outcome], label: str) -> list[str]:
    """`-3.5: 1.65/2.30 | -2.5: 1.88/2.01 | …` — l'echelle des handicaps jeux.

    Au tennis, le handicap jeux est un continuum comme un total, et le book en
    sert une dizaine de lignes. N'en montrer que la plus serree — ce que fait
    `_render_spreads` pour le football, ou un handicap buts est une ligne et non
    une echelle — jetait neuf cotes sur dix.

    Les lignes sont vues du **premier joueur nomme**, celui de gauche dans
    l'affiche : lister les deux cotes sous un seul signe evite d'avoir a se
    demander a qui « +2.5 » se rapporte.
    """
    by_point = _by_handicap(event, outcomes)
    reference = min(by_point, key=abs) if by_point else None
    if reference is None:
        return _render_spreads(event, outcomes, label)

    retained = sorted(by_point, key=lambda point: (abs(point - reference), point))[:TOTALS_KEEP]
    fragments = []
    for point in sorted(retained):
        prices = by_point[point]
        home, away = prices.get(event.home), prices.get(event.away)
        if home is None and away is None:
            continue
        both = f"{price(home) if home else '·'}/{price(away) if away else '·'}"
        fragments.append(f"{_signed(point)}: {both}")
    if not fragments:
        return _render_spreads(event, outcomes, label)
    return [line(label, " | ".join(fragments))]


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
    # Qui passe le tour, toutes manches confondues. Juste apres le 1N2 : les deux
    # repondent a « qui gagne », a deux echelles differentes, et sur une manche
    # retour c'est la seconde qui porte l'enjeu.
    ("to_qualify", "Se qualifie"),
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
    # Props buteurs, demandees sur les seules competitions de la liste blanche.
    # Sans entree ici, `ordered_labels` rendait leur **cle brute** :
    # « player_goal_scorer_anytime » s'affichait tel quel dans la ligne
    # « Non servis » et dans la liste des marches en tete de prompt. Meme piege
    # que pour `alternate_totals`, et meme regle — tout marche demande doit
    # avoir son libelle, servi ou non.
    ("player_goal_scorer_anytime", "Buteur"),
    ("player_first_goal_scorer", "1er buteur"),
    # Saisie manuelle : marche libre, sans forme imposee.
    ("outright", "Cotes"),
]

#: Tennis : pas de nul, et tout se compte en sets et en jeux.
TENNIS_MARKET_ORDER: list[tuple[str, str]] = [
    ("h2h", "Vainqueur"),
    # `alternate_spreads` doit figurer ici : `MERGED_MARKETS` fait de lui la cible
    # de `spreads`, et sans entree dans cet ordre il tombait dans le repli
    # generique — rendu sous sa cle brute, en fin de bloc.
    ("alternate_spreads", "Hand. jeux"),
    ("spreads", "Hand. jeux"),
    ("totals", "Jeux O/U"),
    # Sans cette entree, `ordered_labels` ne trouvait pas le marche et rendait sa
    # **cle brute** — « alternate_totals » s'affichait tel quel dans la liste des
    # marches demandes en tete de prompt.
    ("alternate_totals", "Jeux O/U"),
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
    if target in {"h2h", "h2h_s1", "h2h_s2", "to_qualify"}:
        return _render_h2h(event, outcomes, label)
    if target == "double_chance":
        return _render_double_chance(event, outcomes)
    if target in {"alternate_spreads", "spreads_s1"}:
        # Le tennis compte en jeux, donc en echelle ; le football en buts, donc en
        # une ligne par equipe. Meme marche, deux formes de marche.
        if event.sport_key == "tennis":
            return _render_spread_ladder(event, outcomes, label)
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


def _pooled(event: RenderableEvent) -> dict[str, list[Outcome]]:
    """Les marches du bloc, variantes « alternate » fusionnees dans leur base."""
    pooled: dict[str, list[Outcome]] = {}
    for key, outcomes in event.markets.items():
        pooled.setdefault(MERGED_MARKETS.get(key, key), []).extend(outcomes)
    return pooled


def _render_markets(event: RenderableEvent) -> list[str]:
    """Rend chaque marche disponible, dans l'ordre, en fusionnant les variantes."""
    pooled = _pooled(event)

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


def unserved_note(settings: Settings | None = None) -> str:
    """La note de « Non servis », **son perimetre enumere**.

    Un constat d'absence ne vaut que pour ce qui a ete interroge, et la phrase
    doit donc le porter : « aucun des 3 books interroges (Betclic, Pinnacle,
    Unibet NL) ne les sert ». Sans cela, elle se lit comme « ce marche n'existe
    pas ici », ce qui est faux — mesure du 14/08/2026, `btts` sur la Ligue 2 est
    servi par trois books europeens que nous n'interrogeons pas.

    Les noms viennent de `query_books()`, la **meme** source que celle qui
    decide des appels : une seconde liste aurait fini par decrire un perimetre
    different de celui qu'on interroge vraiment. Le suffixe « (ref.) » est
    retire — il qualifie un prix affiche, pas un book interroge, et il ferait
    ici une parenthese dans une parenthese.
    """
    noms = [
        bookmaker_label(key).removesuffix(REFERENCE_SUFFIX).strip() for key in query_books(settings)
    ]
    return UNSERVED_NOTE.format(count=len(noms), books=", ".join(noms))


def _unserved_line(event: RenderableEvent) -> list[str]:
    """Marches demandes et jamais servis : un fait acquis, pas un oubli.

    Les taire laisserait chercher un handicap jeux qui n'existe pas, ou croire
    a une collecte incomplete la ou l'API a repondu tout ce qu'elle avait.
    """
    # Le rapprochement se fait sur le marche **fusionne**, pas sur la cle brute :
    # `spreads` et `alternate_spreads` partagent une ligne et un libelle, et
    # comparer les cles laissait annoncer « Handicap non servi » juste sous une
    # ligne de handicap affichee. Constate en reel sur un match de Super League.
    served = set(event.markets) | {MERGED_MARKETS.get(key, key) for key in event.markets}

    def _rendus(keys: Sequence[str]) -> list[str]:
        absent = (key for key in keys if MERGED_MARKETS.get(key, key) not in served)
        return ordered_labels(event.sport_key, absent)

    lignes: list[str] = []
    labels = _rendus(event.unserved)
    if labels:
        note = UNSERVED_NOTE_SUBSTITUTE if event.substitute else unserved_note()
        lignes.append(line(UNSERVED_LABEL, f"{', '.join(labels)} — {note}"))
    # **Une ligne par cause, jamais fondues.** Une absence constatee sur la
    # competition et une absence constatee sur ce match n'appellent pas le meme
    # comportement, et les ranger sous la meme note faisait affirmer la premiere
    # sur des faits qui ne portaient que la seconde.
    ici = _rendus(event.unserved_here)
    if ici:
        lignes.append(line(UNSERVED_LABEL, f"{', '.join(ici)} — {UNSERVED_NOTE_MATCH}"))
    return lignes


def unplayable_markets(event: RenderableEvent) -> list[str]:
    """Marches presents dont **aucun** prix ne vient du book principal.

    Ni un marche absent, ni un marche jouable : un troisieme etat, et c'est
    celui qui decide de ce qu'on peut reellement parier. Le distinguer evite de
    batir un angle sur un marche qu'on ne pourra pas jouer.

    Un evenement servi par un book de substitution n'en produit aucun : tous ses
    prix sont de reference par construction, le bloc le dit deja en entier, et
    repeter la liste de ses marches n'ajouterait rien.
    """
    if not event.primary_book or event.substitute:
        return []
    absent = {
        key
        for key, outcomes in event.markets.items()
        if outcomes and all(outcome.bookmaker != event.primary_book for outcome in outcomes)
    }
    # Meme rapprochement que pour « Non servis » : `spreads` et
    # `alternate_spreads` partagent une ligne, et n'en declarer qu'une moitie
    # non jouable ferait chercher le prix manquant dans la ligne affichee.
    merged = {MERGED_MARKETS.get(key, key) for key in absent}
    playable = {MERGED_MARKETS.get(key, key) for key in set(event.markets) - absent}
    return ordered_labels(event.sport_key, sorted(merged - playable))


def handicap_alert(event: RenderableEvent) -> str | None:
    """Le handicap ±0.5 redit le 1N2 : un ecart y denonce un signe inverse.

    Au football, `-0.5` sur une equipe **est** sa victoire seche, et `+0.5` sa
    double chance. Les deux prix se deduisent donc du 1N2, ce qui permet de
    controler le signe du handicap sans rien supposer du fournisseur — c'est le
    seul controle du bloc qui confronte deux marches l'un a l'autre.

    Le controle n'a pas de seuil de tolerance a regler : il demande seulement
    lequel des deux paris le prix observe decrit le mieux, ce qui ne derive ni
    avec la marge du book ni avec l'ecart entre deux books. `HANDICAP_ALERT_MARGIN`
    n'est pas cette tolerance mais la condition de lisibilite de la question :
    sous elle, les deux paris se valent et on ne demande rien.

    Mesure qui l'a fait naitre : le fournisseur de substitution ecrit le
    handicap asiatique du point de vue de l'equipe qui recoit, **des deux
    cotes**. Le bloc a servi « Aston Villa -0.5 2.12 » quand Aston Villa
    vainqueur valait 4.60 — le prix de sa double chance sous le libelle de sa
    victoire. La conversion est faite a l'ingestion ; cette ligne est ce qui
    dira que le fournisseur a change d'avis, plutot qu'une analyse reelle.
    """
    if event.sport_key != "football":
        return None
    pooled = _pooled(event)
    prices = {outcome.name: outcome.price for outcome in pooled.get("h2h", [])}
    draw, home, away = prices.get("Draw"), prices.get(event.home), prices.get(event.away)
    if not draw or not home or not away:
        return None

    ladder = _by_handicap(event, pooled.get("alternate_spreads", []))
    suspects: list[str] = []
    for anchor in (-0.5, 0.5):
        served = ladder.get(anchor)
        if not served:
            continue
        for team, handicap, win in ((event.home, anchor, home), (event.away, -anchor, away)):
            observed = served.get(team)
            if observed is None:
                continue
            # `-0.5` ne se gagne que sur une victoire, `+0.5` se gagne aussi sur
            # un nul : la double chance est la somme des deux probabilites.
            chance = 1 / (1 / win + 1 / draw)
            attendu, inverse = (win, chance) if handicap < 0 else (chance, win)
            if abs(1 / attendu - 1 / inverse) < HANDICAP_ALERT_MARGIN:
                continue
            if abs(1 / observed - 1 / inverse) < abs(1 / observed - 1 / attendu) and (
                team not in suspects
            ):
                suspects.append(team)

    if not suspects:
        return None
    return (
        f"le handicap de {' et '.join(suspects)} est cote comme le pari inverse "
        "— signe non fiable sur ce releve, la ligne se lit sur le 1N2"
    )


def _alert_line(event: RenderableEvent) -> list[str]:
    """Rendue avec les lignes de fin de bloc, qui toutes qualifient le releve.

    Elle ne coute rien tant que rien ne cloche : sur des donnees saines la
    fonction rend `None`, et le bloc ne porte pas la ligne.
    """
    message = handicap_alert(event)
    return [line(ALERT_LABEL, message)] if message else []


#: Blocs partageant le meme releve « A relever » sous lesquels la condensation
#: ne paie pas.
#:
#: **Mesure du 14/08/2026 sur les vingt derniers prompts** : douze ne portent
#: aucune ligne, et parmi les huit qui en portent, le motif dominant couvre
#: 85 %, 100 %, 100 %, 66 % et 33 % des blocs. Remplacer n lignes identiques par
#: une phrase de portee generale en coute deux : la condensation gagne a partir
#: de quatre, et en dessous elle coute plus en lecture qu'elle ne gagne en
#: lignes. A ce seuil, trois des vingt prompts condensent — dont celui de 28
#: blocs, ou 24 lignes identiques se lisaient a la suite.
COMMON_UNPLAYABLE_MIN = 4


def common_unplayable(
    events: Sequence[RenderableEvent], minimum: int = COMMON_UNPLAYABLE_MIN
) -> list[str]:
    """Le releve « A relever » que **la majorite du lot** partage, s'il y en a un.

    **Derive du lot, jamais code en dur.** « Handicap et O/U en reference » est
    vrai un jour parce que le book principal ne sert que le 1N2 sur ces
    competitions-la ; ce n'est pas une propriete de l'application, et l'ecrire
    dans le gabarit ferait mentir le prompt le jour ou la collecte change.

    La majorite se compte sur **tous les blocs du lot**, pas sur ceux qui
    portent une ligne : une phrase de portee generale sur un lot dont deux blocs
    sur six sont concernes se lirait comme valant pour les six.
    """
    if not events:
        return []
    releves = [tuple(unplayable_markets(event)) for event in events]
    porteurs = [item for item in releves if item]
    if not porteurs:
        return []
    dominant, compte = Counter(porteurs).most_common(1)[0]
    if compte < minimum or compte * 2 <= len(releves):
        return []
    return list(dominant)


def _unplayable_line(event: RenderableEvent, common: Sequence[str] = ()) -> list[str]:
    """La ligne est **seche, sans note**, contrairement a « Non servis ».

    Celle-la porte la sienne parce qu'elle a trois causes qu'il faut distinguer.
    Ici il n'y en a qu'une, le preambule l'explique une fois pour tout le lot, et
    la repeter huit fois coutait cent tokens pour redire le libelle.

    **Omise quand elle redit ce que le lot dit deja** : sur un lot de 28 blocs,
    24 portaient mot pour mot « Handicap, O/U ». La phrase generale les remplace
    et seules les exceptions restent, qui sont justement ce qu'il fallait voir.
    """
    labels = unplayable_markets(event)
    if labels and list(common) == labels:
        return []
    return [line(UNPLAYABLE_LABEL, ", ".join(labels))] if labels else []


# -- Bloc complet -----------------------------------------------------------


def _header(event: RenderableEvent) -> list[str]:
    sport = SPORT_LABELS.get(event.sport_key, event.sport_key.upper())
    when = event.commence_local.strftime("%d/%m %H:%M")
    # Le cyclisme n'a pas de second participant : l'etape tient lieu d'affiche.
    rows = [
        f"### M{event.index} · {sport} · {event.competition} · "
        f"{affiche(event.home, event.away)} · {when}"
    ]
    shift = _shift_line(event)
    if shift:
        rows.append(shift)
    return rows


def _shift_line(event: RenderableEvent) -> str:
    """`(horaire deplace de +5h05, constate le 12/08 22:14)` — ou rien.

    Elle se pose **sous l'heure**, parce que c'est l'heure qu'elle corrige. Une
    journee d'orages a Cincinnati a repousse tout un programme de cinq heures :
    l'application avait les deux relevés, n'en gardait qu'un, et le fait
    dominant de la soiree a du etre retrouve dans la presse.

    Elle reste un **signal** : rien ne s'ecrit quand l'horaire n'a pas bouge, et
    le seuil est celui de l'age d'un releve — au-dessous, un ecart n'a rien
    traverse.
    """
    if event.previous_local is None:
        return ""
    ecart = event.commence_local - event.previous_local
    minutes = round(ecart.total_seconds() / 60)
    if not minutes:
        return ""
    signe = "+" if minutes > 0 else "-"
    heures, reste = divmod(abs(minutes), 60)
    duree = f"{heures}h{reste:02d}" if heures else f"{reste} min"
    constat = ""
    if event.shifted_local is not None:
        constat = f", constate le {event.shifted_local.strftime('%d/%m %H:%M')}"
    # Deux crans d'indentation : la ligne appartient a l'en-tete, pas au bloc
    # CONTEXTE, et l'alignement des libelles ne la concerne pas.
    return f"{INDENT * 2}(horaire deplace de {signe}{duree}{constat})"


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


def _tiers_line(event: RenderableEvent) -> list[str]:
    """Les paliers que les cotes du bloc rendent atteignables.

    Elle ferme le bloc parce qu'elle porte sur ses prix et sur rien d'autre :
    c'est une consequence des lignes qui precedent, pas une donnee de plus.
    """
    return [line(TIERS_LABEL, event.tiers_line)] if event.tiers_line else []


def _markets_block(event: RenderableEvent, common: Sequence[str] = ()) -> list[str]:
    rows = (
        _render_markets(event)
        + _alert_line(event)
        + _unplayable_line(event, common)
        + _unserved_line(event)
        + _tiers_line(event)
    )
    if not rows:
        return []
    heading = f"MARCHES ({event.bookmaker_label}"
    if event.fetched_local:
        heading += f", releve {event.fetched_local.strftime('%H:%M')}{_lead_time(event)}"
    return [heading + ")", *rows]


def _lead_time(event: RenderableEvent) -> str:
    """` — coup d'envoi dans 13h07`, et ce que cet ecart traverse.

    **C'est l'ecart au coup d'envoi qui compte, pas l'age du releve.** Huit
    heures sur une qualification obscure ne bougent rien ; le meme ecart sur une
    Supercoupe couvre l'annonce des compositions, et la moitie des prix avec.
    L'heure seule laissait cette soustraction a faire, donc personne ne la
    faisait.

    Au football, la ligne dit en plus si le releve **precede la publication des
    compositions** — `LINEUP_LEAD_MINUTES` avant le coup d'envoi, l'heure ou les
    clubs publient et ou le marche se reajuste. C'est le seul moment nomme,
    parce que c'est le seul qui deplace les prix a heure connue.

    En dessous de `LEAD_TIME_MIN_MINUTES`, rien : un releve de dix minutes n'a
    rien traverse, et l'ecrire ferait du bruit sur les blocs les plus frais.
    """
    if event.fetched_local is None:
        return ""
    ecart = event.commence_local - event.fetched_local
    minutes = int(ecart.total_seconds() // 60)
    if minutes < LEAD_TIME_MIN_MINUTES:
        return ""
    detail = f" — coup d'envoi dans {minutes // 60}h{minutes % 60:02d}"
    if event.sport_key == "football" and minutes > LINEUP_LEAD_MINUTES:
        detail += ", avant les compositions"
    return detail


def render_event(event: RenderableEvent, common_unplayable: Sequence[str] = ()) -> str:
    """Bloc texte compact d'un evenement, pret a etre injecte dans le prompt.

    `common_unplayable` est le releve « A relever » que la majorite du lot
    partage : le bloc qui le redirait mot pour mot se tait, la phrase generale
    du prompt le porte une seule fois.
    """
    parts = [
        *_header(event),
        *_context_block(event),
        *_markets_block(event, common_unplayable),
    ]
    return "\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Approximation suffisante pour l'UI : un token vaut ~3.6 caracteres."""
    return round(len(text) / 3.6)
