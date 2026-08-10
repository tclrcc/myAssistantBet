"""Assemblage du prompt final a partir des blocs de rendu et d'un template Jinja.

Le livrable de l'application est ce bloc de texte. L'app ne l'envoie nulle part :
elle le rend, l'humain le copie.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import (
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    TemplateSyntaxError,
    select_autoescape,
)

from ..config import PACKAGE_DIR, Settings, get_settings
from ..db import connect, utcnow
from ..providers.oddsapi import SCAN_MARKETS
from .enrich import markets_for
from .history import feedback
from .labels import affiche, bookmaker_label, is_reference
from .render import (
    MERGED_MARKETS,
    Outcome,
    RenderableEvent,
    estimate_tokens,
    market_label,
    ordered_labels,
    render_event,
    unplayable_markets,
)
from .session import has_started, renderable_events, session_label, started_labels
from .thresholds import value_of as threshold

logger = logging.getLogger(__name__)

TEMPLATES_DIR = PACKAGE_DIR / "templates" / "prompts"

#: Taille de lot pour laquelle les quotas des paliers sont regles. En dessous,
#: ils se reduisent a proportion — c'est ce que le prompt expliquait en prose,
#: et que personne ne pouvait appliquer sans refaire le calcul.
QUOTA_REFERENCE_LOT = 10

#: Paliers gardant un plancher de 1 quel que soit le lot : les deux plus surs.
QUOTA_FLOOR_TIERS = 2
DEFAULT_TEMPLATE = "session_default.md.j2"
TEMPLATE_SUFFIX = ".md.j2"

MOIS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

#: Le jour de la semaine porte du sens : calendrier, session de nuit, reprise
#: apres week-end. Le laisser deduire de la date etait une devinette inutile.
JOURS_FR = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)


@dataclass
class Tier:
    """Une bande de cote, exposee au template."""

    key: str
    label: str
    emoji: str
    min_price: float
    max_price: float | None
    quota_min: int
    quota_max: int

    @property
    def range_label(self) -> str:
        if self.max_price is None:
            return f"> {self.min_price:.2f}   (scores exacts multichoix, marches exotiques)"
        return f"{self.min_price:.2f} – {self.max_price:.2f}"

    def covers(self, value: float) -> bool:
        """Vrai si cette cote tombe dans la bande.

        **La borne haute appartient au palier suivant** : une cote a 1.70 est
        FUN et non SAFE. C'est la convention du prompt, ecrite une seule fois.
        """
        if value < self.min_price:
            return False
        return self.max_price is None or value < self.max_price

    @property
    def quota_label(self) -> str:
        """La borne telle qu'elle est **reglee**, pour l'ecran des reglages.

        Le prompt, lui, n'ecrit jamais celle-ci : il ecrit la borne du lot,
        calculee par `quota_for()`. Une borne qu'il faut recalculer soi-meme ne
        contraint rien.
        """
        return f"{self.quota_min}-{self.quota_max} {self.emoji}"

    def quota_for(self, lot: int, *, safest: bool) -> tuple[int, int]:
        """Bornes reelles de ce palier sur un lot de `lot` matchs.

        Les quotas sont regles pour un lot de reference (`QUOTA_REFERENCE_LOT`)
        et se reduisent a proportion. Le prompt expliquait cette reduction en
        prose et laissait le calcul a faire — donc une borne de dix matchs
        s'affichait sur un lot de cinq, et ne contraignait rien.

        **Les deux paliers les plus surs gardent un plancher a 1** : un petit
        lot doit pouvoir porter une selection sure, sinon la reduction
        interdirait de rendre quoi que ce soit. Au-dela, le plancher est 0 — un
        palier haut vide est un resultat, et la section C demande de le
        commenter.

        L'arrondi est **au plus proche, moities vers le haut**, et non celui de
        `round()` : la regle bancaire de Python rendrait 2 pour 2.5 et 2 pour
        1.5, deux comportements differents sur deux paliers voisins.
        """
        if lot <= 0:
            return (0, 0)
        scaled = int(self.quota_max * lot / QUOTA_REFERENCE_LOT + 0.5)
        high = max(min(self.quota_max, scaled), 1 if safest else 0)
        # La borne basse ne peut pas depasser la haute : « 2-1 » ne se lit pas.
        return (min(self.quota_min, high), high)

    def quota_line(self, lot: int, *, safest: bool) -> str:
        low, high = self.quota_for(lot, safest=safest)
        return f"{low}-{high} {self.emoji}"


@dataclass(frozen=True)
class Price:
    """Une cote du lot, avec de quoi la retrouver dans son bloc."""

    value: float
    block: int
    market: str
    outcome: str

    @property
    def label(self) -> str:
        """`3.40 (M2 · Vainqueur Diana Shnaider)`.

        L'emplacement compte autant que le nombre : une borne annoncee sans
        l'endroit ou elle se lit oblige a relire les quatre blocs pour la
        verifier, et personne ne le fait.
        """
        where = " ".join(part for part in (self.market, self.outcome) if part)
        return f"{self.value:.2f} (M{self.block} · {where})"


def _outcome_text(outcome: Outcome) -> str:
    """`Over 22.5`, `Diana Shnaider`, `Hacken O1.5` — l'issue telle qu'elle se lit."""
    parts = [outcome.description or "", outcome.name or ""]
    if outcome.point is not None:
        parts.append(f"{outcome.point:g}")
    return " ".join(part for part in parts if part)


def prices_of(event: RenderableEvent) -> list[Price]:
    """Toutes les cotes d'un bloc, dans l'ordre croissant.

    Le marche est nomme par son **libelle fusionne**, celui qu'affiche le bloc :
    une cote annoncee sous `alternate_totals` serait introuvable a l'oeil, la
    ligne s'appelant `Jeux O/U`.
    """
    found = [
        Price(
            value=float(outcome.price),
            block=event.index,
            market=market_label(event.sport_key, MERGED_MARKETS.get(key, key)),
            outcome=_outcome_text(outcome),
        )
        for key, outcomes in event.markets.items()
        for outcome in outcomes
    ]
    return sorted(found, key=lambda price: price.value)


@dataclass
class TierScope:
    """Les paliers que les cotes du lot rendent reellement atteignables.

    Mesure qui l'a fait naitre : sur un lot de quatre quarts de finale, la cote
    la plus haute valait **3.40**. Les paliers 🔴 GIGA FUN et 💥 GIGA+ etaient
    donc hors d'atteinte avant meme que l'analyse commence — et le prompt
    injectait pourtant leurs quotas, puis exigeait qu'un palier vide soit
    commente « en nommant ce qu'il aurait fallu trouver ». L'analyse produisait
    une ligne d'excuse pour un palier que le lot rendait impossible.

    **L'atteignabilite se mesure sur les cotes reellement offertes, pas sur
    l'intervalle qu'elles couvrent.** Un lot dont les prix seraient 1.34 et 3.40
    ne porte aucune cote entre 1.70 et 2.30 : declarer 🔵 FUN atteignable parce
    qu'il tombe « entre les deux » ferait chercher un prix qui n'existe nulle
    part. Une selection recopie **une** cote d'**un** bloc, jamais un intervalle.
    """

    lowest: Price | None = None
    highest: Price | None = None
    present: list[Tier] = field(default_factory=list)
    absent: list[Tier] = field(default_factory=list)

    @property
    def known(self) -> bool:
        """Le lot porte au moins une cote. Sans cote, rien ne se calcule."""
        return self.lowest is not None and self.highest is not None

    @property
    def range_line(self) -> str:
        if not self.known:
            return ""
        assert self.highest is not None and self.lowest is not None
        return f"Cote max du lot : {self.highest.label}. Cote min : {self.lowest.label}."

    @property
    def present_line(self) -> str:
        if not self.present:
            return "Aucun palier réglé ne couvre les cotes de ce lot."
        return (
            "Paliers présents dans ce lot : " + ", ".join(tier.label for tier in self.present) + "."
        )

    @property
    def absent_line(self) -> str:
        """« Absents du lot : GIGA FUN, GIGA+ — ne les commente pas. »

        Nommes plutot que sous-entendus par leur cote : « les paliers
        superieurs » serait faux le jour ou c'est le palier le plus sur qui
        manque, ce qui arrive des qu'un lot n'offre aucun favori net.
        """
        if not self.absent:
            return ""
        return (
            "Absents du lot : "
            + ", ".join(tier.label for tier in self.absent)
            + " — aucune cote du lot n'y tombe, ne les commente pas."
        )


def reachable(tiers: list[Tier], prices: Sequence[Price] | Sequence[float]) -> list[Tier]:
    """Paliers qu'au moins une de ces cotes atteint, dans l'ordre des bandes.

    Un palier dont le **quota reglé** vaut zero n'y figure jamais : l'annoncer
    reviendrait a proposer une case qu'on s'est deja interdit de remplir.
    """
    values = [price.value if isinstance(price, Price) else float(price) for price in prices]
    return [
        tier for tier in tiers if tier.quota_max > 0 and any(tier.covers(value) for value in values)
    ]


def tier_scope(tiers: list[Tier], events: Sequence[RenderableEvent]) -> TierScope:
    """Bornes du lot et paliers atteignables, calcules sur toutes ses cotes."""
    prices = sorted(
        (price for event in events for price in prices_of(event)),
        key=lambda price: price.value,
    )
    if not prices:
        return TierScope()
    present = reachable(tiers, prices)
    keys = {tier.key for tier in present}
    return TierScope(
        lowest=prices[0],
        highest=prices[-1],
        present=present,
        absent=[tier for tier in tiers if tier.quota_max > 0 and tier.key not in keys],
    )


def _enumerate_fr(labels: Sequence[str]) -> str:
    """`SAFE`, `SAFE ni ULTRA FUN`, `SAFE, ULTRA FUN ni GIGA FUN`."""
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " ni " + labels[-1]


def block_tiers_line(tiers: list[Tier], event: RenderableEvent, scope: TierScope) -> str:
    """Ligne `Paliers` d'un bloc : ce que **ses** cotes rendent atteignable.

    La parenthese n'apparait que si le bloc restreint **au-dela du lot** : ce
    que le lot exclut partout est deja dit une fois, en tete de la section C, et
    le repeter sous chaque match couterait quatre fois la meme phrase.

    Ni accent ni apostrophe, comme toute valeur rendue dans un bloc.
    """
    prices = prices_of(event)
    if not prices or not scope.present:
        return ""
    present = reachable(tiers, prices)
    keys = {tier.key for tier in present}
    missing = [tier.label for tier in scope.present if tier.key not in keys]
    bounds = f"cotes du bloc {prices[0].value:.2f}-{prices[-1].value:.2f}"
    if not present:
        return f"aucun — {bounds}, hors des bandes reglees"
    labels = ", ".join(tier.label for tier in present)
    if not missing:
        return labels
    return f"{labels} ({bounds} — aucun {_enumerate_fr(missing)})"


@dataclass
class ReferenceNote:
    """Un bloc dont tout ou partie des prix vient d'un book de reference."""

    block: int
    label: str
    books: list[str] = field(default_factory=list)
    #: Les marches concernes. **Vide = tout le bloc**, et la distinction compte :
    #: un match servi par un book de substitution n'a aucun prix maison, alors
    #: qu'un match ordinaire n'en manque que sur deux marches.
    markets: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        quoi = ", ".join(self.markets) if self.markets else "tout le bloc"
        return f"M{self.block} {self.label} — {quoi} [{' + '.join(self.books)}]"


def reference_notes(events: Sequence[RenderableEvent]) -> list[ReferenceNote]:
    """Les rappels « (ref.) » du lot, calcules au lieu d'etre reclames.

    La section F est plafonnee a **trois lignes** et doit porter les marches
    manquants ; avec deux ou trois selections assises sur une cote de reference,
    elle etait pleine avant d'avoir dit quoi que ce soit d'utile. Or
    l'application sait exactement quels marches sont en reference : elle les
    ecrit sous le tableau C, et F redevient ce qu'elle doit etre — les seuls
    echecs de recherche.

    Deux cas, et le second n'a aucune ligne « A relever » pour le signaler :

    - un bloc ordinaire dont certains marches n'ont aucun prix maison, ce que
      `unplayable_markets` sait deja dire ;
    - un bloc dont la **source principale elle-meme** est un book de reference —
      releve de substitution, ou competition que Betclic ne sert pas du tout.
      Tous ses prix sont de reference par construction, donc aucun ne se detache,
      donc rien ne le disait ici.
    """
    notes: list[ReferenceNote] = []
    for event in events:
        if not event.markets:
            continue
        name = affiche(event.home, event.away)
        if event.substitute or is_reference(event.primary_book):
            books = sorted(
                {
                    outcome.bookmaker
                    for outcomes in event.markets.values()
                    for outcome in outcomes
                    if is_reference(outcome.bookmaker)
                }
            )
            if books:
                notes.append(
                    ReferenceNote(
                        block=event.index,
                        label=name,
                        books=[bookmaker_label(book) for book in books],
                    )
                )
            continue

        labels = unplayable_markets(event)
        if not labels:
            continue
        concerned = {
            key
            for key, outcomes in event.markets.items()
            if market_label(event.sport_key, MERGED_MARKETS.get(key, key)) in labels
        }
        books = sorted(
            {
                outcome.bookmaker
                for key in concerned
                for outcome in event.markets[key]
                if outcome.bookmaker and outcome.bookmaker != event.primary_book
            }
        )
        notes.append(
            ReferenceNote(
                block=event.index,
                label=name,
                books=[bookmaker_label(book) for book in books] or ["source inconnue"],
                markets=labels,
            )
        )
    return notes


@dataclass
class Catalogue:
    """Ce que l'app sait demander a l'API pour un sport du lot.

    Sans cette annonce, un marche absent partout est indiscernable d'un marche
    que personne n'a jamais demande : la section F reclame alors des marches que
    le fournisseur ne sert pas — scores exacts ou tie-break en tennis — et la
    boucle de retour tourne a vide.
    """

    sport: str
    markets: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        if not self.markets:
            return f"{self.sport} : aucun marché d'API, saisie manuelle uniquement."
        return f"{self.sport} : {', '.join(self.markets)}."


@dataclass
class CompetitionNote:
    """Fiche d'une competition presente dans le lot.

    Rendue une seule fois, et non a chaque match : le format d'une coupe, la
    phase en cours ou une reprise de championnat ne changent pas d'une affiche
    a l'autre. Les repeter couterait des tokens sans rien apprendre de plus.
    """

    label: str
    notes: str

    @property
    def line(self) -> str:
        return f"{self.label} : {self.notes}"


@dataclass
class RenderedPrompt:
    """Le prompt genere, avant sauvegarde."""

    template_name: str
    body: str
    blocks: int
    #: Matchs de la session absents du prompt parce qu'ils ont commence. Une
    #: selection amputee en silence se remarquerait trop tard.
    started: list[str] = field(default_factory=list)
    #: Les matchs que ce prompt porte vraiment. Archives avec lui, ils forment
    #: le **denominateur du taux de selection** : sans eux, l'application
    #: enregistrait ce qui avait ete selectionne et jamais ce qui avait ete
    #: ecarte. La shortlist ne peut pas jouer ce role — elle se vide a mesure
    #: qu'on decoche, et une session reelle porte 4 lignes de shortlist pour 29
    #: selections.
    event_ids: list[int] = field(default_factory=list)

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.body)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
        trim_blocks=False,
    )


def list_templates() -> list[str]:
    """Templates disponibles, lus sur le disque a chaque appel.

    Editer un fichier suffit donc a changer le prompt, sans redeploiement.
    """
    if not TEMPLATES_DIR.is_dir():
        return []
    names = sorted(path.name for path in TEMPLATES_DIR.glob(f"*{TEMPLATE_SUFFIX}"))
    if DEFAULT_TEMPLATE in names:
        names.remove(DEFAULT_TEMPLATE)
        names.insert(0, DEFAULT_TEMPLATE)
    return names


def load_tiers(settings: Settings | None = None) -> list[Tier]:
    """Bandes de cotes, lues en base pour rester modifiables sans redeploiement."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT key, label, emoji, min_price, max_price, quota_min, quota_max "
            "FROM tiers ORDER BY position"
        ).fetchall()
    return [
        Tier(
            key=row["key"],
            label=row["label"],
            emoji=row["emoji"],
            min_price=float(row["min_price"]),
            max_price=None if row["max_price"] is None else float(row["max_price"]),
            quota_min=int(row["quota_min"]),
            quota_max=int(row["quota_max"]),
        )
        for row in rows
    ]


def date_fr(moment: datetime) -> str:
    jour = JOURS_FR[moment.weekday()]
    return f"{jour} {moment.day} {MOIS_FR[moment.month - 1]} {moment.year}"


def catalogues(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[Catalogue]:
    """Marches demandes a l'API, par sport du lot. Lecture locale, aucun appel.

    Les marches de l'etage A comptent : le bloc les affiche, taire qu'on les
    demande laisserait croire que le « 1N2 » vient d'ailleurs.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT s.key AS sport_key, s.label AS sport_label, "
            "       c.oddsapi_key AS competition_key, e.commence_time "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY s.id",
            (session_id,),
        ).fetchall()

    asked: dict[str, tuple[str, set[str]]] = {}
    for row in rows:
        if has_started(row["commence_time"], now):
            continue
        label, keys = asked.setdefault(row["sport_key"], (row["sport_label"], set()))
        if not row["competition_key"]:
            # Evenement saisi a la main : aucun appel possible, rien a annoncer.
            continue
        keys.update(SCAN_MARKETS)
        keys.update(markets_for(row["sport_key"], row["competition_key"], settings))

    return [
        Catalogue(sport=label, markets=ordered_labels(sport_key, keys))
        for sport_key, (label, keys) in asked.items()
    ]


def competition_notes(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[CompetitionNote]:
    """Fiches des competitions du lot. Lecture locale, aucun appel.

    Un match deja commence n'entre pas dans le prompt : sa competition n'a donc
    pas a y apporter sa fiche, sans quoi le lot semblerait plus large qu'il
    n'est.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.label, c.notes, e.commence_time "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? AND c.notes IS NOT NULL AND TRIM(c.notes) <> '' "
            "ORDER BY c.label",
            (session_id,),
        ).fetchall()

    seen: dict[str, CompetitionNote] = {}
    for row in rows:
        if has_started(row["commence_time"], now) or row["label"] in seen:
            continue
        seen[row["label"]] = CompetitionNote(label=row["label"], notes=row["notes"].strip())
    return list(seen.values())


def _collapse_blank_lines(body: str) -> str:
    """Au plus une ligne vide d'affilee, partout.

    Chaque porte du preambule — `{% if 'tennis' in sports %}` et les quinze
    autres — laisse sa propre ligne vide quand elle ne rend rien. Un lot de
    tennis en portait **onze** coupures de deux lignes ou plus, dont une de
    quatre : le prompt paraissait mal fini la ou il ne manquait rien.

    Le remede est ici et non porte par porte. Regler les blancs de chaque
    `{%- if -%}` marche une fois, puis se defait a la porte suivante — et il
    s'en ajoute a chaque ligne de contexte documentee. Une regle de rendu tient
    toute seule, quel que soit le nombre de portes.
    """
    return re.sub(r"\n{3,}", "\n\n", body)


def build_prompt(
    session_id: int,
    template_name: str = DEFAULT_TEMPLATE,
    settings: Settings | None = None,
    now: datetime | None = None,
    competition_id: int | None = None,
) -> RenderedPrompt:
    """Rend le prompt d'une session. Ne touche ni au reseau ni a la base d'ecriture."""
    settings = settings or get_settings()
    if template_name not in list_templates():
        raise TemplateNotFound(template_name)

    moment = (now or datetime.now(ZoneInfo(settings.tz))).astimezone(ZoneInfo(settings.tz))
    events = renderable_events(session_id, settings, moment, competition_id)

    # Les paliers que les cotes du lot rendent atteignables, calcules **avant**
    # le rendu : chaque bloc porte les siens, et la section C n'annonce que ceux
    # qui existent. Un palier hors d'atteinte annonce puis reclame produisait une
    # ligne d'excuse pour une case que le lot rendait impossible.
    tiers = load_tiers(settings)
    scope = tier_scope(tiers, events)
    for event in events:
        event.tiers_line = block_tiers_line(tiers, event, scope)

    blocks = [render_event(event) for event in events]

    body = (
        _environment()
        .get_template(template_name)
        .render(
            date_fr=date_fr(moment),
            event_blocks=blocks,
            session_label=session_label(session_id, settings),
            # **Seuls les paliers du lot.** Definir 💥 GIGA+ sur un lot dont la
            # cote maximale vaut 3.40 coute des tokens pour proposer une case
            # que rien ne peut remplir — et le prompt reclamait ensuite de
            # commenter sa vacance.
            tiers=scope.present or tiers,
            tier_scope=scope,
            tz=settings.tz,
            # Les sports presents dans le lot. Le preambule documente les
            # lignes de chaque sport, et une session de football payait jusqu'ici
            # les quarante lignes d'explication du tennis — et l'inverse. C'est
            # la meme regle que pour les blocs : ce qui n'a pas de donnee est
            # omis, jamais rendu vide.
            sports=sorted({event.sport_key for event in events}),
            # Les libelles de contexte reellement presents dans le lot. Le
            # preambule expliquait des lignes qu'aucun bloc ne portait : le mode
            # d'emploi du palmares sur un tournoi jamais rattache, celui des
            # buteurs sur une competition sans props. C'est le prolongement d'un
            # cran de la regle des sports — ce qui n'a pas de donnee est omis —
            # et ca rend au budget de quoi payer ce qui, lui, est la.
            context_labels=sorted({label for event in events for label, _ in event.context_lines}),
            catalogues=catalogues(session_id, settings, moment),
            # Les rappels « (ref.) », calcules plutot que reclames a l'analyse.
            # La section F est plafonnee a trois lignes : deux selections de
            # reference la remplissaient avant qu'elle ait dit quoi que ce soit.
            reference_notes=reference_notes(events),
            competition_notes=competition_notes(session_id, settings, moment),
            # Les consignes permanentes et le retour d'experience ne coutent
            # aucun appel : ils sortent de la base, donc ils sont toujours la.
            preferences=read_preference(PREFERENCE_NOTES, settings),
            feedback=feedback(settings),
            # Le multichoix scores exacts n'a de sens que si un bloc sert
            # vraiment ce marche : l'imposer a un lot de tennis fait ecrire
            # « impossible » a chaque session, ce qui n'apprend rien.
            # Le seuil se lit dans les reglages : « a partir de combien de
            # matchs un lot porte-t-il deux combines » est une decision de
            # l'utilisateur, pas une constante du projet.
            combo_min_lot=threshold("combo_min_lot", settings),
            # Les bornes **de ce lot**, calculees ici. Le prompt annoncait
            # celles d'un lot de dix et expliquait qu'elles se reduisaient : une
            # borne qu'il faut recalculer soi-meme ne contraint rien.
            # Le rang du plancher se compte sur les paliers **regles**, jamais
            # sur ceux du lot : un lot sans 🟢 SAFE ferait sinon du palier
            # suivant « l'un des deux plus surs », et lui accorderait un
            # plancher de 1 que la regle ne lui donne pas.
            quotas=[
                tier.quota_line(len(blocks), safest=rank < QUOTA_FLOOR_TIERS)
                for rank, tier in enumerate(tiers)
                if tier in (scope.present or tiers)
            ],
            exact_scores=any(
                key.startswith("correct_score") for event in events for key in event.markets
            ),
        )
    )
    return RenderedPrompt(
        template_name=template_name,
        body=_collapse_blank_lines(body),
        blocks=len(blocks),
        started=started_labels(session_id, settings, moment, competition_id),
        event_ids=[event.event_id for event in events],
    )


def save_prompt(session_id: int, prompt: RenderedPrompt, settings: Settings | None = None) -> int:
    """Archive le prompt genere. Renvoie son id."""
    with connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                prompt.template_name,
                prompt.body,
                prompt.token_estimate,
                utcnow(),
            ),
        )
        prompt_id = int(cursor.lastrowid)
        # Les matchs partent avec le prompt : c'est ce qui donne au taux de
        # selection son denominateur. La shortlist ne peut pas le fournir, elle
        # se vide a mesure qu'on decoche — une session reelle porte 4 lignes de
        # shortlist pour 29 selections, et son premier prompt en servait 12.
        conn.executemany(
            "INSERT OR IGNORE INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            [(prompt_id, event_id) for event_id in prompt.event_ids],
        )
    logger.info(
        "Prompt genere pour la session %d : %d blocs, ~%d tokens",
        session_id,
        prompt.blocks,
        prompt.token_estimate,
    )
    return prompt_id


def template_path(name: str) -> Path:
    return TEMPLATES_DIR / name


# -- Personnalisation --------------------------------------------------------

#: Un nom de template est un simple slug : aucune traversee de repertoire possible.
TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md\.j2$")

#: Consignes permanentes de l'utilisateur, injectees dans chaque prompt.
PREFERENCE_NOTES = "session_notes"

#: Garde-fou de taille : ces consignes sont recopiees dans chaque prompt, et
#: un pave de plusieurs pages y noierait les blocs de match qu'il doit servir.
PREFERENCE_MAX_LENGTH = 4000


def read_preference(key: str, settings: Settings | None = None) -> str:
    """Valeur d'une consigne permanente, ou chaine vide si elle n'existe pas."""
    with connect(settings) as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def save_preference(key: str, value: str, settings: Settings | None = None) -> str:
    """Ecrit une consigne permanente. Vide, elle est supprimee plutot que stockee.

    Ce texte part tel quel dans le prompt : il n'est ni compile ni interprete,
    donc rien ne peut le casser — seule sa longueur est bornee.
    """
    cleaned = (value or "").strip()
    if len(cleaned) > PREFERENCE_MAX_LENGTH:
        raise CustomizationError(
            f"Consignes trop longues : {len(cleaned)} caractères pour un maximum "
            f"de {PREFERENCE_MAX_LENGTH}. Elles sont recopiées dans chaque prompt."
        )

    with connect(settings) as conn:
        if not cleaned:
            conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "                               updated_at = excluded.updated_at",
                (key, cleaned, utcnow()),
            )
    logger.info("Consigne « %s » : %d caracteres", key, len(cleaned))
    return cleaned


class CustomizationError(ValueError):
    """Saisie invalide. Le message est affiche tel quel a l'utilisateur."""


def read_template(name: str) -> str:
    """Contenu d'un template, pour l'editeur."""
    if name not in list_templates():
        raise CustomizationError(f"Template inconnu : {name}")
    return template_path(name).read_text(encoding="utf-8")


def save_template(name: str, body: str) -> str:
    """Ecrit un template apres l'avoir compile.

    Un template qui ne compile pas casserait toute generation de prompt : on
    refuse d'ecrire plutot que de laisser l'application dans cet etat.
    """
    name = (name or "").strip()
    if not TEMPLATE_NAME.match(name):
        raise CustomizationError(
            "Nom invalide : minuscules, chiffres, tirets et underscores, "
            "et l'extension .md.j2 (exemple : session_court.md.j2)."
        )
    if not body.strip():
        raise CustomizationError("Le template est vide.")

    try:
        _environment().from_string(body)
    except TemplateSyntaxError as exc:
        raise CustomizationError(
            f"Erreur de syntaxe Jinja ligne {exc.lineno} : {exc.message}"
        ) from exc

    path = template_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    logger.info("Template enregistre : %s (%d caracteres)", name, len(body))
    return name


def delete_template(name: str) -> None:
    """Supprime un template. Le template par defaut n'est jamais supprimable."""
    if name == DEFAULT_TEMPLATE:
        raise CustomizationError("Le template par défaut ne peut pas être supprimé.")
    if name not in list_templates():
        raise CustomizationError(f"Template inconnu : {name}")
    template_path(name).unlink()
    logger.info("Template supprime : %s", name)


def load_bands(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Bandes cibles par niveau de confiance, du cran le plus haut au plus bas."""
    with connect(settings) as conn:
        return [
            {"level": int(row["level"]), "low": row["low"], "high": row["high"]}
            for row in conn.execute(
                "SELECT level, low, high FROM confidence_bands ORDER BY level DESC"
            )
        ]


def save_bands(rows: list[dict[str, Any]], settings: Settings | None = None) -> None:
    """Met a jour les bandes cibles. Memes controles que les bandes de cotes.

    Ce sont des **points de pourcentage** : une borne hors de [0, 100] ne
    decrit aucun taux, et une bande dont la borne haute n'excede pas la basse
    est vide — dans les deux cas la page ne pourrait plus rien en dire.
    """
    for row in rows:
        level = row.get("level")
        low, high = row.get("low"), row.get("high")
        if low is None:
            raise CustomizationError(f"Confiance {level} : borne basse manquante.")
        if not 0 <= low <= 100:
            raise CustomizationError(f"Confiance {level} : la borne basse sort de 0 à 100 %.")
        if high is not None and not 0 <= high <= 100:
            raise CustomizationError(f"Confiance {level} : la borne haute sort de 0 à 100 %.")
        if high is not None and high <= low:
            raise CustomizationError(
                f"Confiance {level} : la borne haute doit dépasser la borne basse."
            )

    with connect(settings) as conn:
        for row in rows:
            conn.execute(
                "UPDATE confidence_bands SET low = ?, high = ? WHERE level = ?",
                (row["low"], row["high"], row["level"]),
            )
    logger.info("Bandes de confiance mises a jour : %d niveaux", len(rows))


def save_tiers(rows: list[dict[str, Any]], settings: Settings | None = None) -> None:
    """Met a jour les bandes de cotes. Les bornes doivent rester coherentes."""
    for row in rows:
        minimum = row.get("min_price")
        maximum = row.get("max_price")
        if minimum is None:
            raise CustomizationError(f"Palier {row.get('key')} : borne basse manquante.")
        if maximum is not None and maximum <= minimum:
            raise CustomizationError(
                f"Palier {row.get('key')} : la borne haute doit dépasser la borne basse."
            )
        quota_min, quota_max = row.get("quota_min"), row.get("quota_max")
        if quota_min is not None and quota_max is not None and quota_max < quota_min:
            raise CustomizationError(
                f"Palier {row.get('key')} : le quota maximum est inférieur au minimum."
            )

    with connect(settings) as conn:
        for row in rows:
            conn.execute(
                "UPDATE tiers SET label = ?, emoji = ?, min_price = ?, max_price = ?, "
                "                 quota_min = ?, quota_max = ? WHERE key = ?",
                (
                    row["label"],
                    row["emoji"],
                    row["min_price"],
                    row["max_price"],
                    row["quota_min"],
                    row["quota_max"],
                    row["key"],
                ),
            )
    logger.info("Bandes de cotes mises a jour : %d paliers", len(rows))
