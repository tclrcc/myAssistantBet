"""Assemblage du prompt final a partir des blocs de rendu et d'un template Jinja.

Le livrable de l'application est ce bloc de texte. L'app ne l'envoie nulle part :
elle le rend, l'humain le copie.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median
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
from . import changelog, stakes
from .competitions import is_knockout, reads_domestic_aggregates
from .enrich import markets_for
from .history import SCALE_VERSION, feedback
from .labels import affiche, bookmaker_label, is_reference
from .render import (
    COMMON_UNPLAYABLE_MIN,
    MERGED_MARKETS,
    Outcome,
    RenderableEvent,
    common_unplayable,
    estimate_tokens,
    handicap_alert,
    market_label,
    ordered_labels,
    render_event,
    unplayable_markets,
)
from .research import sheet as research_sheet
from .session import has_started, renderable_events, session_label, started_labels
from .thresholds import COUPON_TRACKING, toggle_of
from .thresholds import value_of as threshold
from .weather import ALERT_MARK

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


def research_capped(
    tiers: Sequence[Tier],
    lot: int,
    budget: int,
    offered: Container[str] | None = None,
) -> list[tuple[Tier, int, int]]:
    """Les bornes de chaque palier, **le budget de recherche deduit**.

    Tout palier au-dela des deux plus surs reclame un fait nomme et date de la
    section A, donc un dossier ouvert ; et une session n'en ouvre qu'un nombre
    fini. Le quota autorisait plus que la methode ne permet de justifier — une
    invitation a remplir, exactement ce que le prompt nomme ailleurs comme
    l'erreur la plus couteuse.

    **La contrainte porte sur le total des paliers hauts, pas palier par
    palier.** Un `min` par palier ne mordrait jamais : le plus genereux des
    trois vaut 3 quand le budget par defaut en ouvre 7. C'est leur somme qui
    consomme le budget, et c'est elle qu'il faut borner.

    La coupe part du **bas** — le palier haut le plus sur d'abord — parce qu'un
    fait date justifie plus facilement un 2.50 qu'un 9.00.

    **Les dossiers disponibles sont `min(budget, lot)`** et non le budget seul :
    sur un lot plus court que le budget, la fiche de priorite ne se rend meme
    pas, et tout match peut recevoir un dossier. Borner au budget y accorderait
    plus de paliers hauts qu'il n'y a de matchs.

    Mesure du 14/08/2026, et il faut la connaitre : **au reglage par defaut la
    contrainte ne mord sur aucun lot**. Les trois paliers hauts totalisent 6 au
    plus par construction, quand `recherche_dossiers` en ouvre 7. Elle mord des
    que le seuil descend a 5 — sa borne basse est 2 — ou qu'un quota_max
    augmente. C'est une porte fermee, pas un defaut repare : les deux nombres ne
    peuvent plus deriver l'un de l'autre en silence. Reverifie le 21/08/2026 sur
    les 170 prompts archives : **zero prompt** ou le budget deplace une borne.

    `offered` porte les cles des paliers qu'une cote du lot atteint vraiment.
    **Un palier que le lot offre garde un quota d'au moins un**, et c'est
    exactement l'argument qui a donne son plancher aux deux paliers les plus
    surs — « sinon la reduction interdirait de rendre quoi que ce soit » —
    applique un cran plus haut, et plus strictement : eux l'ont sans condition,
    celui-ci ne l'a que si un prix y tombe.

    Sans lui, le prorata seul rendait un palier **declare present et interdit** :
    sur un lot de 2, `2 x 2/10 = 0.4` arrondit a 0, et le prompt 170 annoncait
    `Paliers presents … GIGA FUN` puis `0-0 🔴` sur une cote a 3.80 — donc un
    palier vide a commenter dont la cause n'a rien a voir avec la recherche.
    Mesure : 6 prompts sur les 86 qui portent les deux lignes, et **3 des 4
    petits lots** du regime recent.

    **Le plancher passe avant le budget, jamais apres.** Un zero cause par le
    budget est un zero explique — le paragraphe qui suit les quotas dit qu'un
    palier haut reclame un dossier ouvert, et ce prompt en ouvre N — quand un
    zero cause par le prorata n'avait aucune cause enoncable. Les deux ne se
    confondent donc pas, et seul le second disparait.
    """
    dossiers = max(0, min(budget, lot))
    rendus: list[tuple[Tier, int, int]] = []
    for rank, tier in enumerate(tiers):
        safest = rank < QUOTA_FLOOR_TIERS
        low, high = tier.quota_for(lot, safest=safest)
        if not safest:
            if offered is not None and tier.key not in offered:
                # **Un palier qu'aucune cote du lot n'atteint ne consomme aucun
                # dossier.** Il ne peut recevoir aucune selection — le rendu le
                # retire, et `TierScope` le declare absent — donc lui laisser
                # prendre sa part du budget affamait les paliers reellement
                # offerts. Sa borne reste celle du prorata : elle n'est lue
                # nulle part, et la mettre a zero ferait passer une absence de
                # cote pour une absence de dossier.
                rendus.append((tier, low, high))
                continue
            if offered is not None:
                high = max(high, 1)
            high = min(high, dossiers)
            dossiers -= high
            low = min(low, high)
        rendus.append((tier, low, high))
    return rendus


def safe_legs_available(tiers: Sequence[Tier], lot: int, dossiers: int) -> int:
    """Jambes que ce lot autorise vraiment : quotas, lot **et budget de
    recherche**.

    Un combine long se batit dans les deux bandes les plus sures — six SAFE a
    1.45 et quatre FUN a 1.95 donnent 135 sans consommer une seule place haute —
    donc les quotas a sommer sont ceux de ces deux paliers, jamais le total des
    cinq.

    **Le budget de recherche s'y applique, et le docstring d'origine se trompait
    de regle.** Il ecartait le budget au motif que `research_capped` ne touche
    pas les deux paliers surs, « aucun d'eux ne reclamant de dossier » : c'est
    vrai de la regle de **palier**, et sans effet ici, parce que ce n'est pas
    elle qui contraint une jambe. La section D en impose une seconde — aucune
    jambe sous confiance 3 — et celle-la passe par le dossier :

      · une jambe reclame confiance 3 (section D) ;
      · le cran 3 exige au moins un fait date (`confidence.Claim.rung`, qui rend
        1 des que `reading_only`) ;
      · une selection hors des dossiers declares ouverts est ramenee en lecture,
        donc au cran 1 (`history.add_pick`).

    Une jambe suppose donc un dossier ouvert, et une session n'en ouvre qu'un
    nombre fini. Le plafond est le plus petit des trois.

    **Le nombre est lu par le modele**, et c'est ce qui rend l'erreur couteuse :
    un plafond trop haut dans le prompt invite a chercher des jambes qui ne
    peuvent pas exister — exactement la pression que le reste du gabarit
    travaille a supprimer.

    Il ne mordait sur aucun lot de la base au 14/08/2026 — le vivier s'epuise
    avant, 37 prompts sur 39, avec un maximum de 6 jambes produites contre 7 de
    budget — mais cette mesure vaut pour un regime ou la ligne `dossiers_ouverts`
    n'etait jamais collee, donc ou aucune selection ne pouvait depasser le cran
    1. Elle ne dit rien de ce que le vivier vaudra une fois la ligne lue. Meme
    forme que le rejet d'une cote hors bande : porte fermee, pas defaut repare.

    Les deux autres bornes n'ont pas bouge. `quota_for` plafonne a `quota_max`,
    regle pour `QUOTA_REFERENCE_LOT` : au reglage servi le 14/08/2026 les quotas
    valent 6 + 5 = 11 des dix matchs, et un lot de 140 n'en donne pas un de plus
    qu'un lot de 28. Le lot borne aussi — une seule selection par match dans un
    combine, donc cinq matchs ne portent pas six jambes.
    """
    quotas = sum(tier.quota_for(lot, safest=True)[1] for tier in tiers[:QUOTA_FLOOR_TIERS])
    return max(0, min(quotas, lot, dossiers))


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
    #: Les paliers **hauts** que ce lot propose : ceux qui sortent des deux
    #: bandes les plus sures, donc ceux que la section C soumet a l'exigence d'un
    #: fait date. La frontiere est celle du gabarit et elle est ecrite **une
    #: fois** (`QUOTA_FLOOR_TIERS`) — la recopier cote gabarit l'aurait fait
    #: diverger au premier reglage de bande.
    high: list[Tier] = field(default_factory=list)

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
    # Les deux plus surs se comptent sur l'ordre des **bandes reglees**, jamais
    # sur les seuls paliers presents : un lot sans favori ferait sinon passer
    # ULTRA FUN pour un palier sur.
    surs = {tier.key for tier in tiers[:QUOTA_FLOOR_TIERS]}
    return TierScope(
        lowest=prices[0],
        highest=prices[-1],
        present=present,
        absent=[tier for tier in tiers if tier.quota_max > 0 and tier.key not in keys],
        high=[tier for tier in present if tier.key not in surs],
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


@dataclass(frozen=True)
class CommonReference:
    """Le rappel « (ref.) » que la majorite du lot partage.

    **Meme regle que `render.common_unplayable`, un cran plus loin.** Mesure du
    14/08/2026 sur les 30 prompts archives qui portent au moins une ligne : 271
    lignes au total, dont **234 redisent le motif dominant de leur lot**, et 24
    lots sur 30 condensent. Le lot de 28 blocs du 14/08 ecrivait 28 fois
    « Handicap, O/U [Pinnacle (ref.)] » ; une ligne qui parait sur tout le lot
    cesse d'informer, et les exceptions — celles qu'il faut lire — s'y noient.

    Le motif est la paire **marches + books** : deux blocs auxquels manquent les
    memes marches chez deux books differents ne partagent pas un constat.
    """

    markets: tuple[str, ...]
    books: tuple[str, ...]
    count: int

    @property
    def line(self) -> str:
        quoi = ", ".join(self.markets) if self.markets else "tout le bloc"
        return f"{quoi} [{' + '.join(self.books)}]"


def common_reference(
    notes: Sequence[ReferenceNote], blocks: int, minimum: int = COMMON_UNPLAYABLE_MIN
) -> CommonReference | None:
    """Le motif « (ref.) » dominant du lot, s'il vaut d'etre dit une seule fois.

    **Derive du lot, jamais code en dur** : « Handicap et O/U en reference » est
    vrai un jour parce que le book principal ne sert que le 1N2 sur ces
    competitions-la, et l'ecrire dans le gabarit ferait mentir le prompt le jour
    ou la collecte change.

    **La majorite se compte sur tous les blocs du lot**, jamais sur ceux qui
    portent une note : une phrase de portee generale sur un lot dont deux blocs
    sur six sont concernes se lirait comme valant pour les six.

    Le seuil est celui de `common_unplayable` et pour la meme arithmetique :
    remplacer n lignes par une phrase en coute deux, donc la condensation ne
    gagne qu'a partir de quatre.
    """
    if not notes or blocks <= 0:
        return None
    motifs = Counter((tuple(note.markets), tuple(note.books)) for note in notes)
    (markets, books), compte = motifs.most_common(1)[0]
    if compte < minimum or compte * 2 <= blocks:
        return None
    return CommonReference(markets=markets, books=books, count=compte)


def _exception(note: ReferenceNote, commun: CommonReference) -> bool:
    """Ce bloc s'ecarte-t-il du motif general du lot.

    Seules les exceptions gardent leur ligne : ce sont elles qu'il fallait lire,
    et elles se noyaient dans vingt-quatre repetitions du meme constat.
    """
    return (tuple(note.markets), tuple(note.books)) != (commun.markets, commun.books)


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
    #: Le bloc de retour d'experience a-t-il publie des taux dans ce prompt.
    #:
    #: Des qu'un agregat de resultats entre dans le prompt, les selections
    #: suivantes ne sont plus des tirages independants : l'analyse lit son propre
    #: tableau de bord, et une categorie annoncee a 0/7 cesse d'etre produite —
    #: donc cesse d'etre mesurable. La question « depuis quand » se posait sans
    #: qu'aucune donnee n'y reponde autrement qu'en relisant des corps archives.
    feedback_active: bool = False
    #: Le nombre de dossiers de recherche que **ce prompt** a ouverts, soit
    #: `min(reglage, taille du lot)`.
    #:
    #: **Il se stocke parce que le reglage change et que la taille du lot en
    #: decide autant.** Recalculer a la lecture ferait decrire les sessions
    #: d'hier par le reglage d'aujourd'hui — meme famille que
    #: `sessions.scale_version`. Et sans lui, « 9 reperes declares » se lit comme
    #: « il restait de la marge » alors que la mesure dit l'inverse : les trois
    #: lots concernes comptaient exactement 6, 7 et 9 blocs, donc le modele a
    #: declare tout le lot et il n'y avait plus rien a ouvrir.
    research_budget: int | None = None

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.body)


#: Combien de reperes au plus dans un exemple de format. **Quatre**, comme le
#: litteral qu'ils remplacent : au-dela l'exemple cesse d'etre lisible.
SAMPLE_MARKERS = 4


def sample_markers(
    blocks: list[RenderableEvent], sport: str = "", whole: bool = False
) -> list[str]:
    """Des reperes de bloc **qui existent dans ce lot**, pour un exemple de format.

    Le gabarit ecrivait `dossiers_ouverts: [M1, M4, M7, M8]` sur un lot de sept
    matchs, et `sets: M3=... | M4=... | M8=PASSE` sur un lot dont M3 et M4 sont du
    **football** et qui ne porte qu'un seul match de tennis. Aucun des deux
    n'induit vraiment en erreur, et les deux sement un doute au moment precis ou
    le format doit etre sans ambiguite.

    **Espaces reguliers plutot qu'un prefixe**, et jamais tout le lot : `M1, M2,
    M3` se lirait comme « ouvre-les tous », quand un echantillon disperse se lit
    comme un exemple. Le compte plafonne donc a la moitie du lot, et a
    `SAMPLE_MARKERS`.

    **Deux besoins, deux regles, et il faut les deux.** `dossiers_ouverts` liste
    un **sous-ensemble** choisi, donc un echantillon disperse le decrit bien ;
    `sets` reprend **chaque** match de tennis du lot, donc en montrer la moitie
    contredirait la phrase qui l'introduit. `whole` porte la difference.

    Un lot sans bloc du sport demande rend une liste vide : la ligne et son
    exemple s'omettent alors ensemble, meme regle que partout — ce qui n'a pas de
    donnee est omis, jamais rendu vide.
    """
    reperes = [item.index for item in blocks if not sport or item.sport_key == sport]
    if not reperes:
        return []
    if whole:
        # Plafonne quand meme : au-dela de quatre l'exemple cesse d'etre lisible,
        # et la phrase qui l'introduit dit deja « chaque match ».
        return [f"M{index}" for index in reperes[:SAMPLE_MARKERS]]
    combien = max(1, min(SAMPLE_MARKERS, (len(reperes) + 1) // 2))
    if combien == 1:
        return [f"M{reperes[0]}"]
    # Etales du premier au dernier, et non pris en tete : un prefixe se lirait
    # comme « les quatre premiers », ce qui est une consigne et pas un exemple.
    dernier = len(reperes) - 1
    choisis = sorted({round(rang * dernier / (combien - 1)) for rang in range(combien)})
    return [f"M{reperes[position]}" for position in choisis]


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
        trim_blocks=False,
    )


def template_fingerprint() -> str:
    """L'empreinte des gabarits **presents sur le disque**, au moment de l'appel.

    Sur tous, et pas seulement sur celui qui rend : le prompt en vigueur peut
    changer d'une session a l'autre, et une empreinte qui ne couvrirait que le
    gabarit utilise ne dirait pas qu'un autre a ete ajoute ou retire — ce qui est
    aussi un changement de cadre, l'edition etant possible sans redeploiement.
    """
    return changelog.fingerprint(list(TEMPLATES_DIR.glob(f"*{TEMPLATE_SUFFIX}")))


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
            "       c.oddsapi_key AS competition_key, c.category, e.commence_time "
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
        keys.update(
            markets_for(
                row["sport_key"],
                row["competition_key"],
                settings,
                knockout=is_knockout(row["category"]),
            )
        )

    return [
        Catalogue(sport=label, markets=ordered_labels(sport_key, keys))
        for sport_key, (label, keys) in asked.items()
    ]


def domestic_aggregates(events: list[RenderableEvent], settings: Settings | None = None) -> bool:
    """Un bloc du lot lit-il ses agregats hors de sa propre competition ?

    La question se pose sur les **matchs rendus** et non sur la session : un
    prompt restreint a une competition ne doit pas payer le mode d'emploi d'une
    lecture croisee qui ne s'y produit pas. Meme regle que les libelles de
    contexte, un cran plus loin.
    """
    settings = settings or get_settings()
    ids = [event.event_id for event in events if event.event_id]
    if not ids:
        return False
    placeholders = ",".join("?" * len(ids))
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.oddsapi_key FROM events e "
            "JOIN competitions c ON c.id = e.competition_id "
            f"WHERE e.id IN ({placeholders})",
            ids,
        ).fetchall()
    return any(reads_domestic_aggregates(row["oddsapi_key"]) for row in rows)


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


def schedule_notice(events: list[RenderableEvent]) -> str:
    """Une ligne, et **seulement quand les deux faits se rencontrent**.

    Le soir des orages de Cincinnati, l'alerte du NWS etait en base et les
    horaires avaient bouge de cinq heures. Les deux informations existaient
    separement, chacune sur sa ligne, et personne ne les rapprochait — alors que
    **l'une explique l'autre**, et que leur conjonction dit ce qu'aucune des
    deux ne dit seule : le programme a deja cede, il peut ceder encore.

    Rien ne s'ecrit si une seule des deux tient : chacune a deja sa ligne, et
    une conjonction qui se declenche a moitie redirait ce qui est ecrit juste en
    dessous.
    """
    alerte = any(
        ALERT_MARK in (valeur or "")
        for event in events
        for label, valeur in event.context_lines
        if label == "Meteo"
    )
    ecarts = [
        round((event.commence_local - event.previous_local).total_seconds() / 60)
        for event in events
        if event.previous_local is not None
    ]
    if not alerte or not ecarts:
        return ""
    pire = max(ecarts, key=abs)
    heures, minutes = divmod(abs(pire), 60)
    duree = f"{heures}h{minutes:02d}" if heures else f"{minutes} min"
    return (
        f"Ce lot : une alerte meteo en vigueur **et** {len(ecarts)} match(s) deja deplace(s) "
        f"(jusqu'a {'+' if pire > 0 else '-'}{duree}). Le programme a deja glisse aujourd'hui "
        "et peut glisser encore : les horaires ci-dessous ne sont pas acquis."
    )


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
    # Les paliers qu'une cote du lot atteint vraiment. Ils servent deux fois — a
    # filtrer la ligne des quotas, et a lui donner son plancher — et c'est **le
    # meme ensemble** : un palier annonce present et un palier autorise ne
    # peuvent pas se separer sans que le prompt se contredise. Un lot sans cote
    # n'en offre aucun, et rien ne se planche alors.
    offered_keys = {tier.key for tier in scope.present}
    for event in events:
        event.tiers_line = block_tiers_line(tiers, event, scope)

    # **Derive du lot, jamais code en dur.** Sur 28 blocs du 14/08, 24 portaient
    # mot pour mot « Handicap, O/U » : la phrase generale les remplace et seules
    # les exceptions restent, qui sont justement ce qu'il fallait voir.
    commun = common_unplayable(events)
    blocks = [render_event(event, commun) for event in events]

    # Meme regle un cran plus loin, sur la liste « Prix a relever » : le motif
    # dominant se dit une fois, et **seules les exceptions gardent leur ligne**.
    rappels = reference_notes(events)
    commun_ref = common_reference(rappels, len(events))

    # Retenu plutot que passe en ligne au rendu : c'est lui qui dit si le prompt
    # a transmis des taux, donc si les selections qui vont en sortir ont ete
    # prises en lisant leur propre tableau de bord. La reponse s'archive avec le
    # prompt, elle ne se relit pas dans son corps des mois apres.
    retour = feedback(settings)

    # Les bornes de ce lot, calculees **une fois** : la ligne des quotas les
    # rend, et la section C-bis a besoin de savoir lesquelles valent zero. Deux
    # appels auraient fini par decrire deux lots differents — le piege deja paye
    # deux fois par l'assembleur de contexte.
    bornes = research_capped(
        tiers,
        len(blocks),
        threshold("recherche_dossiers", settings),
        offered=offered_keys,
    )
    body = (
        _environment()
        .get_template(template_name)
        .render(
            date_fr=date_fr(moment),
            event_blocks=blocks,
            # L'alerte meteo et le deplacement des horaires ne disent rien
            # separement de ce qu'ils disent ensemble.
            schedule_notice=schedule_notice(events),
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
            # Vrai des qu'un bloc porte une ligne « Alerte ». Le mode d'emploi
            # d'une ligne qui ne parait presque jamais ne se paie pas a chaque
            # session : meme regle que les libelles de contexte, un cran plus
            # loin — celle-ci est faite pour ne jamais servir.
            handicap_alerts=any(handicap_alert(event) for event in events),
            # Vrai des qu'un bloc du lot porte les lignes de service.
            #
            # **La porte est plus etroite que le drapeau, et c'est voulu.** Le
            # brief demande de conditionner les trois passages a
            # `SERVE_LINES_ENABLED` ; se poser sur les libelles reellement
            # rendus l'implique — le drapeau bas ne produit aucune ligne, donc
            # aucun libelle — et ferme en plus le cas ou le drapeau est haut sur
            # un lot dont aucun joueur n'atteint le seuil. Meme regle que partout
            # ici : un mode d'emploi ne se paie que sur un lot qui le porte.
            serve_lines="Service"
            in {label for event in events for label, _ in event.context_lines},
            # Vrai des qu'un bloc du lot lit ses agregats dans le championnat
            # domestique de ses equipes. Meme regle que les libelles de
            # contexte : le mode d'emploi d'une lecture croisee ne se paie pas
            # sur une soiree de championnat, ou elle ne se produit jamais.
            domestic_aggregates=domestic_aggregates(events, settings),
            # Ou depenser un budget de recherche fini. Le lot ne donnait aucun
            # ordre de passage : sur 21 manches retour, 3 dossiers ont ete
            # traites au juge et 18 selections sont retombees en `lecture`. La
            # fiche ne parait qu'au-dela du seuil — classer trois dossiers sur
            # trois n'apprend rien.
            research=research_sheet(events, settings),
            catalogues=catalogues(session_id, settings, moment),
            # Les rappels « (ref.) », calcules plutot que reclames a l'analyse.
            # La section F est plafonnee a trois lignes : deux selections de
            # reference la remplissaient avant qu'elle ait dit quoi que ce soit.
            reference_notes=[
                note for note in rappels if (commun_ref is None or _exception(note, commun_ref))
            ],
            # Le motif « (ref.) » que la majorite du lot partage, dit **une
            # fois**. Mesure du 14/08/2026 sur les 30 prompts archives qui en
            # portent : 271 lignes, dont 234 redisent le motif dominant de leur
            # lot, et 24 lots sur 30 condensent. Sur le lot de 28 blocs du
            # 14/08, la meme ligne paraissait 28 fois.
            common_reference=commun_ref,
            # Le releve « A relever » que la majorite du lot partage. Vide des
            # qu'aucune majorite ne se degage : la liste plate reprend alors sa
            # place, et la phrase generale disparait avec elle.
            common_unplayable=commun,
            competition_notes=competition_notes(session_id, settings, moment),
            # Les consignes permanentes et le retour d'experience ne coutent
            # aucun appel : ils sortent de la base, donc ils sont toujours la.
            preferences=read_preference(PREFERENCE_NOTES, settings),
            feedback=retour,
            # Le multichoix scores exacts n'a de sens que si un bloc sert
            # vraiment ce marche : l'imposer a un lot de tennis fait ecrire
            # « impossible » a chaque session, ce qui n'apprend rien.
            # Le seuil se lit dans les reglages : « a partir de combien de
            # matchs un lot porte-t-il deux combines » est une decision de
            # l'utilisateur, pas une constante du projet.
            combo_min_lot=threshold("combo_min_lot", settings),
            # Le symetrique du precedent, qui manquait : sous ce lot, aucun
            # combine n'est demande du tout. Sur quatre matchs et un taux de
            # selection median de 36 %, l'esperance tourne autour de 1.4
            # selection quand la section D en reclame trois independantes —
            # reclamer puis faire ecrire que c'etait impossible coute deux fois.
            combo_solo_min_lot=threshold("combo_solo_min_lot", settings),
            # Le nombre de jambes est un **parametre**, pas une consequence de la
            # cote visee : « >= 100 » se satisfait par 5 jambes a 2.50 comme par
            # 10 a 1.55, et ce sont deux objets sans rapport.
            #
            # Sur le long il est un **plafond et jamais une cible** : la mesure
            # dit que la cote n'est pas la contrainte — les six sessions offrant
            # dix jambes sures depassent toutes 100 — et que c'est le compte qui
            # l'est. Viser un compte fabriquerait donc la pression que la section
            # interdit. Il prend ce que le lot autorise et s'arrete au premier
            # des trois motifs, dont il rend le nom.
            combo_court_jambes=threshold("combo_court_jambes", settings),
            combo_court_cote=threshold("combo_court_cote", settings),
            combo_long_cote=threshold("combo_long_cote", settings),
            # Le seuil qui fait basculer « court » en « long » se calcule et
            # s'annonce : une regle qu'il faut appliquer de tete ne contraint
            # rien, meme raison que les bornes de palier.
            combo_maillon_jambes=threshold("combo_maillon_jambes", settings),
            # Ce que ce lot autorise vraiment : quotas des deux paliers surs,
            # taille du lot, et budget de recherche — une jambe reclame la
            # confiance 3, donc un fait date, donc un dossier ouvert. Reclamer
            # dix jambes a un lot qui n'en porte que quatre ferait ecrire que la
            # demande etait insatisfiable — le defaut deja corrige par
            # `combo_solo_min_lot`, un cran plus loin.
            combo_legs_max=safe_legs_available(
                tiers, len(blocks), threshold("recherche_dossiers", settings)
            ),
            # Les bornes **de ce lot**, calculees ici. Le prompt annoncait
            # celles d'un lot de dix et expliquait qu'elles se reduisaient : une
            # borne qu'il faut recalculer soi-meme ne contraint rien.
            # Le rang du plancher se compte sur les paliers **regles**, jamais
            # sur ceux du lot : un lot sans 🟢 SAFE ferait sinon du palier
            # suivant « l'un des deux plus surs », et lui accorderait un
            # plancher de 1 que la regle ne lui donne pas.
            # Le budget de recherche est deduit des paliers hauts : chacun
            # reclame un fait date, donc un dossier ouvert, et une session n'en
            # ouvre qu'un nombre fini. Calcule et annonce comme tel — jamais
            # formule en consigne, qui laisserait le calcul a faire.
            # `scope.present` descend dans le calcul et ne sert plus seulement
            # a filtrer l'affichage : un palier que les cotes du lot atteignent
            # garde un quota d'au moins un. Sans lui, le prorata declarait un
            # palier **present et interdit** — 6 prompts archives, dont le
            # dernier rendu.
            quotas=[
                f"{low}-{high} {tier.emoji}"
                for tier, low, high in bornes
                if tier in (scope.present or tiers)
            ],
            # Les paliers hauts que le lot offre et que la section C interdit
            # quand meme. **Il ne reste qu'une cause possible** : le budget de
            # recherche, le prorata ayant desormais son plancher. C'est
            # exactement le cas que C-bis existe pour porter — le seul endroit
            # ou l'exigence d'un fait date tombe — et la phrase ne se paie que
            # la ou elle decrit quelque chose.
            tiers_sans_dossier=[
                tier for tier, _, high in bornes if high == 0 and tier in scope.high
            ],
            research_budget=min(threshold("recherche_dossiers", settings), len(blocks)),
            # La table de mises, **en unites et jamais en monnaie** : le montant
            # est saisi au collage, donc l'application ne le connait pas ici.
            # Elle annonce ce qu'il **reste** pour la journee et non le plafond
            # nu — sans quoi quatre rendus du meme jour auraient chacun cru
            # disposer du plafond entier, c'est-a-dire le contournement par
            # decoupage que le plafond par journee existe pour fermer.
            # **Garde par l'interrupteur du suivi d'argent**, et ce n'est pas
            # cosmetique : la section pese 592 tokens de cout fixe, et la faire
            # payer a qui ne mise pas serait exactement ce que les portes du
            # preambule existent pour eviter.
            mise=(
                stakes.brief(moment.strftime("%Y-%m-%d"), settings)
                if toggle_of(COUPON_TRACKING, settings)
                else None
            ),
            exact_scores=any(
                key.startswith("correct_score") for event in events for key in event.markets
            ),
            # **Les exemples de format se batissent sur les reperes du lot.** Un
            # `M8` sur un lot de sept, ou un `sets: M3=...` sur un M3 de
            # football, ne trompent pas vraiment mais sement un doute a l'endroit
            # precis ou le format doit etre sans ambiguite — et ils occupent de
            # la place dans un cadre qui pese la moitie d'un prompt median.
            exemple_reperes=sample_markers(events),
            exemple_tennis=sample_markers(events, "tennis", whole=True),
        )
    )
    return RenderedPrompt(
        template_name=template_name,
        body=_collapse_blank_lines(body),
        blocks=len(blocks),
        # Le meme calcul que celui ecrit dans le corps, et par la meme
        # expression : deux ecritures auraient diverge au premier ajustement, et
        # la colonne aurait alors decrit un budget que le prompt n'annoncait pas.
        research_budget=min(threshold("recherche_dossiers", settings), len(blocks)),
        started=started_labels(session_id, settings, moment, competition_id),
        event_ids=[event.event_id for event in events],
        # `enough` et non `not empty` : le bloc peut paraitre en ne portant que
        # le taux de selection, qui ne depend d'aucun resultat et ne referme
        # donc aucune boucle. Ce qui la referme, ce sont les taux de reussite.
        feedback_active=retour.enough,
    )


def _capture_odds(conn: Any, session_id: int, event_ids: Sequence[int]) -> int:
    """Fige le marche complet des matchs qui partent a l'analyse.

    **`odds` ne garde que le dernier releve** : le scan fait un DELETE puis un
    INSERT par (match, book, marche), si bien que l'etat du marche au moment ou
    l'analyse l'a lu n'existe nulle part une heure apres. C'est le seul instant
    ou l'on sait ce que le bloc portait, donc le seul ou ce releve se prend.

    Tous les books, pas seulement le principal : un favori se lit sur le marche
    entier, et sur une competition que Betclic ne sert pas, un book de reference
    est le seul a servir la ligne.

    **Un releve par session et par match, remplace a chaque prompt.** Ni par
    prompt — une session reelle en genere jusqu'a vingt, ce serait vingt copies
    de la meme chose — ni fige au premier : un match entre parfois dans un
    prompt avant d'etre enrichi, et le dernier prompt qui le porte est celui
    dont l'etat est le plus proche de la decision. Meme forme que `scan._store`,
    dont c'est deja la regle.
    """
    if not event_ids:
        return 0
    moment = utcnow()
    marks = ",".join("?" for _ in event_ids)
    conn.execute(
        f"DELETE FROM prompt_odds WHERE session_id = ? AND event_id IN ({marks})",
        (session_id, *event_ids),
    )
    cursor = conn.execute(
        "INSERT INTO prompt_odds (session_id, event_id, bookmaker, market_key, outcome_name, "
        "                         description, point, price, fetched_at, captured_at) "
        "SELECT ?, o.event_id, o.bookmaker, o.market_key, o.outcome_name, "
        "       o.description, o.point, o.price, o.fetched_at, ? "
        f"FROM odds o WHERE o.event_id IN ({marks})",
        (session_id, moment, *event_ids),
    )
    return int(cursor.rowcount or 0)


def save_prompt(session_id: int, prompt: RenderedPrompt, settings: Settings | None = None) -> int:
    """Archive le prompt genere. Renvoie son id.

    Le decoupage cout fixe / cout par bloc s'ecrit **a la generation**, seul
    moment ou il ne coute rien : le corps est deja en main. Le lot 4 l'avait
    mesure en lecture seule, faute de pouvoir toucher ce chemin.
    """
    cout = split_cost(prompt.body)
    # **L'alarme se prononce ici, sur le prompt reellement produit.** Elle ne
    # refuse rien : elle journalise, et la generation continue. Sans ce point,
    # `fixed_tokens` etait archive depuis toujours et personne ne le regardait —
    # le cadre a double en dix jours sans qu'une ligne le signale.
    alerte = FrameAlert(fixed=cout.fixed, ceiling=threshold("cadre_max", settings))
    if alerte.exceeded:
        logger.warning("cadre du prompt %d tokens, seuil %d", alerte.fixed, alerte.ceiling)
    moment = utcnow()
    with connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, "
            "                     feedback_active, created_at, blocks, fixed_tokens, "
            "                     block_tokens, research_budget) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                prompt.template_name,
                prompt.body,
                prompt.token_estimate,
                1 if prompt.feedback_active else 0,
                moment,
                cout.blocks,
                cout.fixed,
                cout.block_tokens,
                prompt.research_budget,
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
        captured = _capture_odds(conn, session_id, prompt.event_ids)
        # L'echelle de confiance en vigueur, ecrite une fois pour la session.
        # `COALESCE` la fige au premier prompt : changer d'echelle en cours de
        # session ne doit pas reetiqueter les selections deja rendues sous
        # l'ancienne.
        conn.execute(
            "UPDATE sessions SET scale_version = COALESCE(scale_version, ?) WHERE id = ?",
            (SCALE_VERSION, session_id),
        )
        # Le cadre sous lequel la session a ete rendue, fige au premier prompt
        # pour la meme raison. **Deux valeurs et deux questions** : l'empreinte
        # dit *le gabarit a-t-il change*, le libelle dit *quel changement*. Une
        # empreinte qui bouge sans que le libelle bouge est le signal qu'un
        # changement de gabarit n'a pas ete journalise.
        conn.execute(
            "UPDATE sessions SET gabarit_version = COALESCE(gabarit_version, ?), "
            "                    gabarit_sha = COALESCE(gabarit_sha, ?) WHERE id = ?",
            (changelog.FRAME_VERSION, template_fingerprint(), session_id),
        )
    # **La bascule du retour d'experience se date toute seule.** Elle demande
    # deux choses qui ne tombent pas le meme jour — assez de recul, et le retrait
    # d'une suspension qui est une modification de source — donc ni la date de
    # livraison ni celle du franchissement de seuil ne decrivent le moment ou le
    # regime change. Seul le premier prompt qui **part** avec des taux le fait,
    # et c'est ici qu'on le sait.
    #
    # Hors de la transaction : une entree de journal n'a rien a voir avec
    # l'archivage du prompt, et la faire echouer avec lui ferait perdre le prompt
    # pour une ligne de journal.
    if prompt.feedback_active:
        changelog.note_feedback(moment[:10], settings)
    logger.info(
        "Marche fige pour la session %d : %d cotes sur %d matchs",
        session_id,
        captured,
        len(prompt.event_ids),
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


def load_bands(
    settings: Settings | None = None, reference: float | None = None
) -> list[dict[str, Any]]:
    """Bandes cibles par niveau de confiance, du cran le plus haut au plus bas.

    Rend l'ecart **tel qu'il se regle** et sa valeur **resolue** contre le taux
    global. Les deux cote a cote : l'ecran saisit un ecart, mais sans la seconde
    il faudrait refaire l'addition pour savoir ce que la cible vaut aujourd'hui.
    """
    from .history import Band

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT level, low, high FROM confidence_bands ORDER BY level DESC"
        ).fetchall()
    resolues = {
        int(row["level"]): Band(
            level=int(row["level"]),
            low=None if row["low"] is None else float(row["low"]),
            high=None if row["high"] is None else float(row["high"]),
            reference=reference,
        )
        for row in rows
    }
    return [
        {
            "level": int(row["level"]),
            "low": row["low"],
            "high": row["high"],
            "resolved_label": resolues[int(row["level"])].label
            or resolues[int(row["level"])].offset_label,
        }
        for row in rows
    ]


def save_bands(rows: list[dict[str, Any]], settings: Settings | None = None) -> None:
    """Met a jour les bandes cibles. Memes controles que les bandes de cotes.

    Ce sont des **ecarts en points au taux global**, et non plus des taux :
    « conf 4 vise six points au-dessus de ma moyenne » ne depend pas du melange
    de paliers du mois, quand « conf 4 vise 60 % » y est entierement soumis. Un
    ecart est donc **signe** — un cran bas vise en dessous — et la borne de 0 n'a
    plus lieu d'etre.

    Reste borne a cent points de part et d'autre : au-dela, l'ecart depasse
    l'amplitude d'un taux et ne peut plus decrire aucune cible atteignable.
    """
    for row in rows:
        level = row.get("level")
        low, high = row.get("low"), row.get("high")
        # **Les deux vides = pas de cible sur ce cran**, et c'est un reglage
        # attendu la ou aucun mouvement correctif n'existe : les crans 1 et 2
        # sont pines par la source — `lecture` impose 1, une source de niveau
        # 3-4 plafonne a 2 — donc ni les resserrer ni les relacher n'est un
        # choix. Une bande qui ne peut declencher aucune action ne mesure rien.
        if low is None and high is None:
            continue
        if low is None:
            # `high` seul reste une **saisie incomplete**, et le rejet est garde
            # exprès : c'est le dernier cas d'erreur que ce validateur sache
            # attraper, et une borne effacee par megarde doit se voir.
            raise CustomizationError(f"Confiance {level} : borne basse manquante.")
        if not -100 <= low <= 100:
            raise CustomizationError(f"Confiance {level} : l'écart bas sort de -100 à +100 points.")
        if high is not None and not -100 <= high <= 100:
            raise CustomizationError(
                f"Confiance {level} : l'écart haut sort de -100 à +100 points."
            )
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


# -- Ce que coute le cadre, et ce que coutent les blocs -----------------------
#
# **Le gabarit grossit a chaque lot livre, et personne ne le surveille.** Mesure
# du lot 4 : de 853 a 11 934 de cout fixe et de 145 a 698 par bloc en onze jours,
# quand le budget de recherche restait a sept dossiers. Ce lot-ci y ajoute encore
# quatre lignes par bloc tennis.
#
# **Le decoupage se mesure, il ne s'ajuste pas.** Un prompt est un preambule
# suivi de N blocs, et la frontiere est un en-tete `### M1`. Une regression sur
# une donnee dont on tient la decomposition exacte est de la mecanique pour
# rien — et elle se trompe : ajustee sur onze jours d'un gabarit qui bouge tous
# les jours, la pente absorbe la croissance du fixe, parce que la taille des lots
# est correlee a la date. C'est exactement ce qui produisait `8 107 + 344`, un
# couple qui ne decrit aucun des trois regimes qu'il melange.

#: L'en-tete qui ouvre le premier bloc de match. **Reutilise depuis
#: `history`** : deux expressions pour un meme en-tete auraient diverge au
#: premier changement de forme, et le decoupage serait devenu faux en silence.
BLOCK_HEADER = re.compile(r"^### M\d+ ", re.MULTILINE)

#: Ce qui **ferme** la section des blocs. Les sections de sortie et le chapitre
#: « COMMENT LIRE LES BLOCS » viennent apres, et ils se paient **une fois par
#: prompt** : ils appartiennent donc au cadre, pas aux blocs.
#:
#: **Trouve en relisant le releve reel**, pas en ecrivant le code. Sans cette
#: borne, le cout par bloc du 17/08 sortait a 2 238 tokens contre ~1 400 les
#: jours precedents — l'inflation venant entierement du chapitre verse dans le
#: dernier bloc, et d'autant plus forte que le lot est court. Une mesure de
#: derive qui bouge avec la taille du lot ne mesure pas la derive.
BLOCKS_END = re.compile(r"^## (CE QUE L'HISTORIQUE DIT|SORTIE ATTENDUE)", re.MULTILINE)


@dataclass(frozen=True)
class PromptCost:
    """Le cout d'un prompt, decoupe entre son cadre et ses blocs."""

    tokens: int
    blocks: int
    fixed: int
    block_tokens: int

    @property
    def per_block(self) -> float | None:
        """Cout marginal reel. None sans bloc — et jamais zero, qui se lirait
        comme un bloc gratuit."""
        return None if not self.blocks else self.block_tokens / self.blocks


def split_cost(body: str) -> PromptCost:
    """Decoupe un prompt entre son cadre et ses blocs. **Aucune estimation.**

    Le **cadre** est tout ce qui se paie une fois par prompt, quel que soit le
    nombre de matchs : le preambule et le mode d'emploi avant le premier bloc,
    **et** les sections de sortie et le chapitre « COMMENT LIRE LES BLOCS » qui
    viennent apres. Les **blocs** sont ce qui reste, entre les deux.

    **La borne haute a ete trouvee sur le releve reel et non en ecrivant le
    code.** Sans elle, le cout par bloc du 17/08 sortait a 2 238 tokens contre
    ~1 400 les jours precedents, l'inflation venant entierement du chapitre verse
    dans le dernier bloc — et d'autant plus forte que le lot est court, puisque
    ce chapitre se divise alors par moins de blocs. Une mesure de derive qui
    bouge avec la taille du lot ne mesure pas la derive.
    """
    texte = body or ""
    entetes = list(BLOCK_HEADER.finditer(texte))
    total = estimate_tokens(texte)
    if not entetes:
        return PromptCost(tokens=total, blocks=0, fixed=total, block_tokens=0)
    fin = BLOCKS_END.search(texte, entetes[0].end())
    borne = fin.start() if fin is not None else len(texte)
    blocs = estimate_tokens(texte[entetes[0].start() : borne])
    return PromptCost(
        tokens=total,
        blocks=len(entetes),
        fixed=max(0, total - blocs),
        block_tokens=blocs,
    )


#: L'alarme de cadre se **tait a l'ecran** jusqu'a la coupe du gabarit.
#:
#: **Un signal toujours actif ne se distingue pas d'un signal absent.** Mesure du
#: 21/08/2026 : 20 prompts sur 20 depassent le seuil sur la fenetre courante, 49
#: sur 50 — la ligne paraitrait a chaque generation et deviendrait du decor,
#: exactement le defaut qu'elle existe pour corriger.
#:
#: **Ce qui se coupe est l'affichage, et rien d'autre.** `frame_history` continue
#: de mesurer, `fixed_tokens` continue de s'ecrire, le journal continue d'avertir :
#: c'est cette observation-la qui rendra la coupe interpretable, et l'interrompre
#: reviendrait a perdre l'« avant » qu'on vient de se donner.
#:
#: **Le seuil, lui, ne bouge pas.** Le deplacer pour faire taire l'alarme
#: fabriquerait un « avant » incomparable avec l'« apres » — le nombre suivrait le
#: confort au lieu de suivre la realite.
#:
#: Une **constante et non un reglage**, meme forme que `FEEDBACK_SUSPENDED` : ce
#: n'est pas une preference d'affichage mais un etat d'exploitation date, et sa
#: bascule ne se produira pas toute seule. Elle se rallume avec la coupe.
FRAME_ALERT_MUTED = True


@dataclass(frozen=True)
class FrameAlert:
    """Le cadre d'un prompt, oppose a ce que l'utilisateur accepte d'en payer.

    **Les deux budgets du projet n'ont jamais rien vu passer** : ils vivent dans
    `tests/`, s'appliquent a des fixtures de six et trois matchs, et rien ne les
    lit a l'execution. C'est ce qui a laisse le cadre passer de 8 048 a 15 232
    tokens en dix jours sans qu'une seule alarme se declenche — la derive etait
    integralement archivee dans `prompts.fixed_tokens`, et personne ne la
    regardait.

    Une **alarme et non un refus** : un prompt long ne gene pas l'utilisateur, et
    refuser de servir une page pour un depassement serait hors de proportion —
    meme arbitrage qu'un seuil illisible qui revient au defaut.
    """

    fixed: int
    ceiling: int
    #: L'affichage est-il suspendu ? **Un champ et non une propriete qui irait
    #: lire la constante** : relue a chaque acces, deux releves du meme prompt
    #: deviendraient indiscernables des qu'elle change, et la classe ne serait
    #: plus testable hors de son module. Le piege deja paye par
    #: `Feedback.suspended`.
    muted: bool = False

    @property
    def exceeded(self) -> bool:
        return self.fixed > self.ceiling

    @property
    def visible(self) -> bool:
        """Le depassement se dit-il a l'ecran ? **Distinct de `exceeded`.**

        Le premier decrit le prompt, le second decrit ce que l'interface en
        montre. Les confondre ferait disparaitre la mesure avec l'affichage.
        """
        return self.exceeded and not self.muted

    @property
    def line(self) -> str:
        """Ce que l'ecran affiche, ou rien quand le cadre tient — ou se tait."""
        if not self.visible:
            return ""
        return (
            f"Cadre du prompt : {self.fixed} tokens pour {self.ceiling} acceptes "
            f"(+{self.fixed - self.ceiling}). Ce qui se paie une fois par prompt, "
            "quel que soit le nombre de matchs."
        )


def frame_alert(body: str, settings: Settings | None = None) -> FrameAlert:
    """Confronte le cadre d'un prompt au seuil regle. Aucun effet de bord."""
    return FrameAlert(
        fixed=split_cost(body).fixed,
        ceiling=threshold("cadre_max", settings),
        muted=FRAME_ALERT_MUTED,
    )


@dataclass(frozen=True)
class FrameHistory:
    """Combien de fois l'alarme a mordu, sur les derniers prompts rendus.

    **C'est cette lecture qui rendra la coupe interpretable, et elle seule.**
    Apres la migration, une alarme muette aura deux causes indiscernables : le
    cadre a fondu, ou l'alarme n'a jamais mordu. La seule facon de les separer
    est d'avoir mesure **avant**, sur des lots reels et non sur les archives —
    d'ou l'ordre de livraison : l'alarme d'abord, la coupe ensuite.
    """

    prompts: int
    exceeded: int
    worst: int
    ceiling: int

    @property
    def share(self) -> float | None:
        """Part des prompts qui depassent. None sans prompt — jamais zero, qui
        se lirait comme « aucun depassement »."""
        return None if not self.prompts else self.exceeded / self.prompts

    @property
    def line(self) -> str:
        if not self.prompts:
            return "Aucun prompt rendu : l'alarme de cadre n'a rien mesure."
        return (
            f"Cadre : {self.exceeded} prompt(s) sur {self.prompts} au-dela de "
            f"{self.ceiling} tokens, maximum {self.worst}."
        )


#: Sur combien de prompts la lecture porte. **Une fenetre et non tout
#: l'historique** : le cadre a change de regime deux fois en deux semaines, et
#: une moyenne sur 172 prompts decrirait surtout le regime d'avant.
FRAME_WINDOW = 20


def frame_history(settings: Settings | None = None, window: int = FRAME_WINDOW) -> FrameHistory:
    """Ce que l'alarme a vu sur les derniers prompts rendus."""
    ceiling = threshold("cadre_max", settings)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT fixed_tokens FROM prompts WHERE fixed_tokens IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (window,),
        ).fetchall()
    cadres = [int(row["fixed_tokens"]) for row in rows]
    return FrameHistory(
        prompts=len(cadres),
        exceeded=sum(1 for cadre in cadres if cadre > ceiling),
        worst=max(cadres, default=0),
        ceiling=ceiling,
    )


def backfill_costs(settings: Settings | None = None) -> int:
    """Remplit le decoupage des prompts archives. Rend le nombre de lignes ecrites.

    **Retro-remplir est sur ici**, contrairement au cran calcule ou a la source
    d'un prix : rien n'est reconstitue, tout est **relu**. Le corps est archive
    depuis toujours et porte ses propres en-tetes ; le decoupage d'un prompt du
    04/08 se refait exactement comme celui d'aujourd'hui.

    Idempotent : une ligne deja decoupee n'est pas reprise.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, body FROM prompts WHERE blocks IS NULL AND body IS NOT NULL"
        ).fetchall()
        for row in rows:
            cout = split_cost(str(row["body"]))
            conn.execute(
                "UPDATE prompts SET blocks = ?, fixed_tokens = ?, block_tokens = ? WHERE id = ?",
                (cout.blocks, cout.fixed, cout.block_tokens, int(row["id"])),
            )
    return len(rows)


#: Le budget de recherche, tel que le gabarit l'ecrit dans le corps du prompt.
#:
#: **Le corps est la preuve**, et c'est ce qui rend le retro-remplissage sur —
#: meme argument que le decoupage du cout, et que `prompts.feedback_active`
#: avant lui. Rien n'est reconstitue : le nombre est ecrit en toutes lettres.
#:
#: Le motif traverse un retour a la ligne (`\s+`) parce que le gabarit coupe la
#: phrase la : ecrit sans, il ne trouverait rien et la colonne resterait vide
#: sans qu'aucune erreur ne le dise — le defaut caracteristique du projet.
RESEARCH_BUDGET = re.compile(r"\*\*ce prompt\*\* en ouvre\s+(\d+)")


def read_research_budget(body: str) -> int | None:
    """Le budget qu'un prompt archive annonce, ou `None` s'il ne l'annonce pas.

    Les prompts anterieurs a cette phrase du gabarit rendent `None`, et c'est la
    verite : il n'y a rien a relire chez eux. Un repli sur le reglage courant
    ferait decrire un prompt du 04/08 par une valeur posee le 18.
    """
    found = RESEARCH_BUDGET.search(body or "")
    return int(found.group(1)) if found else None


def backfill_research_budget(settings: Settings | None = None) -> int:
    """Remplit le budget des prompts archives. Rend le nombre de lignes ecrites.

    Idempotent : une ligne deja remplie n'est pas reprise. Un corps qui n'annonce
    rien est **repasse a chaque fois**, ce qui ne coute qu'une lecture et evite
    d'ecrire un zero qui se lirait comme « aucun dossier ouvert ».
    """
    settings = settings or get_settings()
    ecrites = 0
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, body FROM prompts WHERE research_budget IS NULL AND body IS NOT NULL"
        ).fetchall()
        for row in rows:
            budget = read_research_budget(str(row["body"]))
            if budget is None:
                continue
            conn.execute(
                "UPDATE prompts SET research_budget = ? WHERE id = ?",
                (budget, int(row["id"])),
            )
            ecrites += 1
    return ecrites


@dataclass(frozen=True)
class CostPoint:
    """Le cout moyen d'une journee d'analyse."""

    day: str
    prompts: int
    blocks: int
    fixed: int
    per_block: float | None


def cost_series(settings: Settings | None = None) -> tuple[CostPoint, ...]:
    """La derive du cout du gabarit, par jour d'analyse.

    **Par jour et non par prompt** : une session en genere jusqu'a vingt, tous
    rendus par le meme gabarit a quelques minutes d'ecart, et vingt points
    identiques ne dessinent pas une courbe. Le cout fixe est une **mediane** —
    une moyenne suivrait un prompt aberrant, et c'est justement l'aberration
    qu'on veut voir apparaitre comme un point et non comme une pente.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, blocks, fixed_tokens, block_tokens "
            "  FROM prompts WHERE blocks IS NOT NULL ORDER BY day"
        ).fetchall()

    par_jour: dict[str, list[Any]] = {}
    for row in rows:
        par_jour.setdefault(str(row["day"]), []).append(row)

    points = []
    for day, lot in sorted(par_jour.items()):
        blocs = sum(int(r["blocks"]) for r in lot)
        fixes = sorted(int(r["fixed_tokens"]) for r in lot)
        marginal = sum(int(r["block_tokens"]) for r in lot)
        points.append(
            CostPoint(
                day=day,
                prompts=len(lot),
                blocks=blocs,
                fixed=int(median(fixes)) if fixes else 0,
                per_block=(marginal / blocs) if blocs else None,
            )
        )
    return tuple(points)
