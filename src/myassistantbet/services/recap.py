"""Le recapitulatif du jour : composer, jamais reanalyser.

Les analyses sont faites, les selections sont posees avec leur cran et leur
source. Ce prompt-ci **rassemble** une journee — toutes sessions confondues — et
ne demande au modele que ce qu'aucun calcul ne peut trancher : deux angles
sont-ils independants.

## Ce qu'il ne fait pas, et pourquoi chacun des quatre a coute quelque chose

**Il ne reanalyse pas.** Une seconde analyse serait contaminee par la premiere,
et l'ecart entre les deux ne mesurerait rien.

**Il ne transmet aucun taux.** Meme raison que `FEEDBACK_SUSPENDED` : transmettre
la mesure ferme la boucle qu'elle mesure, et une categorie annoncee faible cesse
d'etre produite, donc cesse d'etre mesurable. Un compte de selections par palier
n'en est pas un ; un « dont N gagnees » en serait un.

**Il n'invente aucune cote.** Les prix enregistres font autorite, et ceux qui
viennent d'un book de reference gardent leur `(ref.)` — le combine qui en
contient un porte la sienne.

**Il ne nomme aucun systeme de mise.** La demande d'origine etait « quelles
selections peuvent servir pour une montante ». Deux mesures l'ont fermee, et
c'est la premiere qui decide :

· une montante **enchaine**, donc elle a besoin de matchs qui ne se chevauchent
  pas. Restreint a ce qui s'etablit — le football, dont la duree est le format du
  sport — l'enchainement le plus long sur `SAFE` et cran >= 4 vaut **1 en
  mediane**, 20 journees sur 23 sous trois pas, 3 journees a zero. Le calendrier
  interdit la fonctionnalite les trois quarts du temps ;
· et nommer un filtre par un systeme de mise deux jours apres le retrait de la
  section G ferait deux dispositifs pour la meme chose, l'un en pause et
  documente, l'autre neuf et muet.

Ce qui reste est ce qui manquait vraiment : le **chevauchement horaire**, que
rien n'affichait. Le palier et le cran sont deja a l'ecran ; la decision de mise
reste entiere et hors de l'outil.

## Les propositions ne s'enregistrent pas, et c'est voulu

`combos.prompt_id` est `NOT NULL`, et `combos.record` refuse une jambe venue d'un
autre prompt — les selections de deux prompts n'ont jamais ete comparees entre
elles. Une journee en porte 3 a 19. Un combine de journee n'est donc pas
enregistrable, la contrainte n'est pas levee, et le rendu le dit plutot que de
laisser chercher un bouton absent.

Consequence a connaitre : ces propositions ne feront pas grossir `combos`. Le
corpus ne croit que par la section D du gabarit d'analyse.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import Settings, get_settings
from ..db import connect
from . import changelog
from .combos import overlap_index, product
from .history import PRICE_REFERENCE, Pick, list_picks_for_day
from .market_families import family_key, load
from .prompt import QUOTA_FLOOR_TIERS, collapse_blank_lines, load_tiers
from .session import has_started

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PACKAGE_DIR / "templates" / "recap"
TEMPLATE_NAME = "jour.md.j2"

#: 45 + mi-temps + 45 + arrets de jeu. **Ce n'est pas une duree mesuree, c'est le
#: format du sport** — et c'est ce qui autorise a s'en servir. Aucun autre sport
#: du catalogue n'a d'equivalent : la duree d'un match de tennis n'est publiee
#: par aucune source, verifie le 07/08/2026, et les fichiers qui la portaient ont
#: disparu. Un chevauchement calcule sur un nombre invente serait un booleen bati
#: sur un champ dont on sait qu'il ment.
FOOTBALL_MINUTES = 115

DUREE_STRUCTURELLE: dict[str, timedelta] = {"football": timedelta(minutes=FOOTBALL_MINUTES)}

LIBRE = "libre"
CHEVAUCHE = "chevauche"
FIN_INCONNUE = "fin inconnue"

#: **Trois etats, et le troisieme porte sur la ligne elle-meme.** C'est la
#: correction du 29/08/2026, et elle inverse la premiere version.
#:
#: Celle-ci faisait basculer en « indetermine » **tout ce qui suivait** une ligne
#: sans fin connue. Rejeu des 23 journees : **57,3 % des lignes** en sortaient,
#: 8,0 % seulement en « libre ». La cause est structurelle — la ligne de tennis
#: tombe au rang 1 a 3 sur onze journees, les sessions de jour se jouant avant le
#: programme de football du soir — et une seule ligne suffisait a effacer le
#: calcul de toutes les autres : 48 lignes sur 80 le 22/08.
#:
#: **La regle etait juste, la forme ne l'etait pas.** Une ligne de football qui
#: suit une autre ligne de football a un enchainement **exact**, et le tennis
#: intercale n'y change rien. L'etat se calcule donc contre les seules lignes
#: dont la fin est connue, et celle qui n'en a pas porte son propre etat plutot
#: que de contaminer les suivantes. Meme discipline que `Non servis` et ses trois
#: causes tenues separees : deux constats qui n'appellent pas le meme
#: comportement ne se fondent pas.
OVERLAP_LABELS: dict[str, str] = {
    LIBRE: "libre — rien de ce qui précède et dont la fin est connue ne court encore",
    CHEVAUCHE: "chevauche — un match précédent court encore",
    FIN_INCONNUE: "fin inconnue — la durée de ce match n'est publiée par aucune source",
}

#: Ce qu'une ligne rend quand la prose de sa section C manque. **Un etat, jamais
#: un silence** : les deux colonnes datent du 17/08/2026 et sont pleines depuis,
#: donc la population sans prose est **close** — mais un rendu sur une journee
#: anterieure existera, et une cellule vide s'y lirait comme une analyse sans
#: angle plutot que comme une colonne plus jeune que la ligne.
PROSE_ABSENTE = "— colonne postérieure à cette sélection"

#: Les trois propositions, **nommees par leur regle**. Aucun adjectif : ni
#: « safe » ni « conservateur ». Le mot « palier » n'a qu'un sens dans
#: l'application — la bande de la cote d'**une selection** — et `Combo` n'en
#: porte aucun. Trois jambes SAFE a 1.60 produisent 4.10 : appeler ce combine
#: « safe » ferait designer deux choses par un meme mot, et le taux de reussite
#: par bande de cote cesserait de mesurer une bande de cote.
#:
#: **Les trois different par leur regle, jamais par la seule longueur.** La
#: premiere version portait `3 jambes cran >= 3` et `4 jambes cran >= 3` : meme
#: vivier, seule la longueur changeait, donc l'une etait l'autre plus une jambe
#: **par construction**. Ce n'etait pas le modele qui emboitait, c'etait la
#: nomenclature.
#:
#: Le troisieme axe est la **famille de marche**, et il a ete choisi sur mesure
#: contre la competition, l'angle et le niveau de source :
#:
#: · familles distinctes — identique a la proposition voisine **4 fois sur 20** ;
#: · competitions distinctes — identique **10 fois sur 21**, donc la moitie du
#:   temps une proposition de plus qui n'en est pas une ;
#: · `angle` porte deux valeurs, donc aucune journee n'en offre trois ;
#: · niveau de source : trois valeurs distinctes sur 5 journees seulement.
#:
#: Recouvrement mesure du jeu retenu : mediane 0,75 / 0,50 / 0,40 selon la paire,
#: jamais disjointes, et constructibles 20 a 22 journees sur 23. Le 0,40 est
#: exactement la valeur que `combos.jaccard` annonce pour deux combines tires du
#: meme vivier — **impose par le vivier, pas par la redaction**.
PROPOSALS: tuple[tuple[int, int, bool], ...] = ((3, 4, False), (4, 3, False), (3, 3, True))

SERVICE_LABEL = "récapitulatif du jour — mise en service"


@dataclass(frozen=True)
class Line:
    """Une selection de la journee, et ce qui court encore quand elle part."""

    pick: Pick
    overlap: str

    @property
    def overlap_label(self) -> str:
        return OVERLAP_LABELS.get(self.overlap, self.overlap)

    @property
    def reference(self) -> bool:
        """Son prix ne vient pas du book principal.

        Le pari reste **selectionnable** — un marche « A relever » se pose, il
        faut relever le prix avant. Ce qui ne se pose pas est le **produit** :
        multiplier des prix qu'on n'obtiendra pas rend une cote qui n'existe
        nulle part. Mesure : mediane 0 selection cotee chez le book principal
        parmi les candidates, 18 journees sur 23 sous deux.
        """
        return self.pick.price_source == PRICE_REFERENCE

    @property
    def angle_note(self) -> str:
        """L'angle en une ligne, tel que la section C l'a ecrit."""
        return self.pick.angle_note or PROSE_ABSENTE

    @property
    def invalidation(self) -> str:
        """Ce qui tue la selection, et c'est ce qui se controle avant de poser.

        Mesure du 28/08/2026 sur les 17 conditions du jour : **14 reposent sur
        l'annonce des compositions**, 3 sur le mercato, 1 sur un report. Quatre
        jambes font donc le plus souvent **une seule fenetre de controle**, une
        heure avant le coup d'envoi — c'est ce qui rend un combine posable en
        pratique, et son absence le rendait inutilisable.
        """
        return self.pick.invalidation or PROSE_ABSENTE


@dataclass(frozen=True)
class Proposal:
    """Une des trois propositions. **Elle porte sa regle comme nom.**

    Pas de champ `tier`, et un banc le verifie : le palier appartient a la
    selection, jamais au combine.

    **Les jambes sont choisies par l'application depuis le 29/08/2026.** La
    premiere version donnait le vivier et laissait composer ; mesure sur les 23
    journees, le vivier n'est determine que **1 fois sur 69**, si bien que deux
    rendus de la meme journee ne donnaient pas les memes combines. Ce qui reste
    au modele est le seul jugement que le document ait jamais annonce — deux
    jambes reposent-elles sur la meme cause.
    """

    legs: int
    min_confidence: int
    #: Trois familles de marche distinctes. **C'est l'axe qui separe cette
    #: proposition de sa voisine**, la longueur ne suffisant pas : deux regles
    #: qui ne different que par elle puisent dans le meme vivier et l'une est
    #: l'autre plus une jambe, par construction.
    distinct_families: bool = False
    pool: list[Pick] = field(default_factory=list)
    chosen: list[Pick] = field(default_factory=list)

    @property
    def label(self) -> str:
        suffixe = ", familles de marché distinctes" if self.distinct_families else ""
        return f"{self.legs} jambes, cran ≥ {self.min_confidence}{suffixe}"

    @property
    def matches(self) -> int:
        """Le vivier **en matchs distincts**, jamais en lignes.

        Une seule selection par match dans un combine : deux lignes sur la meme
        rencontre n'en font qu'une.
        """
        return len({pick.event_id for pick in self.pool if pick.event_id is not None})

    @property
    def enough(self) -> bool:
        return len(self.chosen) == self.legs

    @property
    def price(self) -> float | None:
        """La cote du combine, **calculee et non demandee**.

        `combos.product` porte la regle — une jambe sans prix rend le produit
        incalculable plutot que faux — et cette propriete l'appelle. Demander la
        multiplication au modele etait le defaut que ce projet corrige partout
        ailleurs : rien ne la verifiait.
        """
        return product([pick.price for pick in self.chosen])

    @property
    def price_label(self) -> str:
        """Deux decimales, comme une cote et comme partout dans le projet."""
        valeur = self.price
        return f"{valeur:.2f}" if valeur is not None else "incalculable"

    @property
    def reference_legs(self) -> int:
        """Jambes dont le prix ne vient pas du book principal.

        Le produit ne s'obtient alors pas tel quel, et c'est ce qui se dit — pas
        une interdiction : la selection reste posable, il faut relever le prix.
        """
        return sum(1 for pick in self.chosen if pick.price_source == PRICE_REFERENCE)


@dataclass
class DayRecap:
    """Ce qu'une journee d'analyse a produit, rassemble pour la composition."""

    day: str
    #: **L'instant du rendu, et il decide de tout ce qui suit.** Le vivier de la
    #: premiere proposition valait 4 matchs a l'aube du 28/08 et **0** a 19:00 :
    #: un document dont le contenu depend de l'heure sans le dire produit des
    #: observations qu'on croit structurelles. C'est le meme fait que le vivier
    #: jamais determine, vu a un second endroit — la « coincidence de nombres »
    #: du premier rendu etait une coincidence d'heure.
    rendered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    lines: list[Line] = field(default_factory=list)
    exploratory: list[Pick] = field(default_factory=list)
    started: list[Pick] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.lines and not self.exploratory

    @property
    def sessions(self) -> int:
        picks = [line.pick for line in self.lines] + self.exploratory
        return len({pick.session_id for pick in picks})

    @property
    def competitions(self) -> int:
        return len({line.pick.competition for line in self.lines if line.pick.competition})

    @property
    def without_end(self) -> list[Pick]:
        """Les lignes dont la duree n'est publiee nulle part.

        **Nommees plutot que propagees** : ce qu'elles laissent indetermine est
        leur chevauchement avec ce qui suit, et rien d'autre. Mediane 3 par
        journee, maximum 18.
        """
        return [line.pick for line in self.lines if line.overlap == FIN_INCONNUE]

    @property
    def overlaps(self) -> list[tuple[str, str, int, float]]:
        """Le recouvrement des propositions constructibles, paire par paire.

        **Il s'affiche, il ne s'interdit pas** — `combos.jaccard` a deja tranche
        la question pour les combines du gabarit d'analyse, et pour la meme
        raison : il est impose par le vivier. Mediane mesuree 0,75 / 0,50 / 0,40
        selon la paire, jamais disjointes.
        """
        rendus = [prop for prop in self.proposals if prop.enough]
        paires = []
        for rang, gauche in enumerate(rendus):
            for droite in rendus[rang + 1 :]:
                a = {pick.pick_id for pick in gauche.chosen}
                b = {pick.pick_id for pick in droite.chosen}
                paires.append((gauche.label, droite.label, len(a & b), overlap_index(a, b)))
        return paires


def _end_of(pick: Pick) -> datetime | None:
    """L'instant ou le match est fini, **quand le sport le rend structurel**.

    `None` partout ailleurs, et c'est ce `None` qui produit `INDETERMINE`.
    """
    duree = DUREE_STRUCTURELLE.get(pick.sport_key)
    if duree is None:
        return None
    debut = _moment(pick.commence_time)
    return None if debut is None else debut + duree


def _moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _chronology(picks: Sequence[Pick]) -> list[Line]:
    """Les selections dans l'ordre des coups d'envoi, chacune avec son etat.

    **L'etat se calcule contre les seules lignes dont la fin est connue**, et une
    ligne qui n'en a pas porte son propre etat plutot que de contaminer les
    suivantes. C'est la correction du 29/08/2026 : la premiere version faisait
    basculer tout ce qui suivait, et **57,3 % des lignes du corpus** sortaient
    indeterminees pour une cause portee par une ou deux d'entre elles.

    Ce qui est rendu reste exact des deux cotes : le chevauchement entre deux
    matchs de football est une soustraction, et le fait qu'un match de tennis se
    joue entre les deux ne la change pas. Ce que la ligne de tennis laisse
    indetermine est dit a part, et nomme — `DayRecap.without_end`.
    """
    ordonnees = sorted(picks, key=lambda pick: (pick.commence_time or "9999", pick.pick_id))
    lignes: list[Line] = []
    derniere_fin: datetime | None = None
    for pick in ordonnees:
        debut = _moment(pick.commence_time)
        fin = _end_of(pick)
        if fin is None:
            etat = FIN_INCONNUE
        elif derniere_fin is not None and debut is not None and debut < derniere_fin:
            etat = CHEVAUCHE
        else:
            etat = LIBRE
        lignes.append(Line(pick=pick, overlap=etat))
        if fin is not None and (derniere_fin is None or fin > derniere_fin):
            derniere_fin = fin
    return lignes


def _safest_keys(settings: Settings) -> set[str]:
    """Les deux paliers les plus surs, lus en base et jamais recopies.

    `QUOTA_FLOOR_TIERS` porte deja la frontiere pour le gabarit d'analyse, qui
    puise ses combines dans les memes deux paliers. L'ecrire ici en clair aurait
    fait diverger les deux le jour ou une bande bouge.
    """
    tiers = load_tiers(settings)
    return {tier.key for tier in tiers[:QUOTA_FLOOR_TIERS]}


def _rank(pick: Pick) -> tuple[int, int, str, int]:
    """Le critere de choix d'une jambe. **Sa cle primaire n'est pas un prix.**

    · **cran de confiance decroissant** — la qualite des preuves, jamais un
      prix. C'est le seul axe que le projet reconnaisse comme mesurant la
      solidite d'une selection ;
    · **prix maison d'abord**, en departage seulement. Sa justification est
      qu'un produit compose de prix qu'on obtient existe reellement — mais il ne
      peut pas etre primaire : mesure sur les 23 journees, le vivier n'en porte
      **aucun** cinq fois, et un critere qui rendrait « non constructible » une
      journee de 18 matchs n'en est pas un. En second rang il ne bloque jamais,
      le cran ayant deja tranche ;
    · **heure de coup d'envoi**, puis identifiant — deux departages neutres qui
      rendent le choix reproductible. Deux rendus de la meme journee a la meme
      heure donnent le meme combine, ce qui n'etait pas le cas avant.
    """
    return (
        -(pick.confidence or 0),
        0 if pick.price_source != PRICE_REFERENCE else 1,
        pick.commence_time or "9999",
        pick.pick_id,
    )


def _family(pick: Pick, known: dict[str, str]) -> str:
    """La famille de marche d'une selection, lue la ou elle vit.

    **Sur le libelle et non sur `market_key`**, et ce n'etait pas le cas au
    premier jet : `market_key_effective` rend la cle du vocabulaire de rendu
    (`h2h`), que la table des familles ne connait pas — chaque jambe recevait
    donc une pseudo-famille unique et la contrainte ne mordait jamais. Constate
    sur le rendu reel du 28/08, ou `1N2` et `DC` — tous deux `issue` — ont ete
    retenus ensemble. `history._by_family` lit le meme libelle ; deux lectures
    d'une meme famille auraient fini par ne plus grouper pareil.

    Un marche non classe recoit **sa propre cle** plutot que « autre » : `autre`
    est une decision prise marche par marche, pas le fourre-tout de ce qu'on n'a
    pas regarde, et ranger deux inconnus ensemble ecarterait une jambe sur une
    identite qu'on n'a pas mesuree.
    """
    cle = family_key(pick.market)
    return known.get(cle) or f"?{cle}"


def _choose(pool: Sequence[Pick], legs: int, families: bool, known: dict[str, str]) -> list[Pick]:
    """Les jambes retenues, ou une liste vide si la contrainte ne tient pas.

    **Une seule jambe par match, sans exception** : deux selections sur la meme
    rencontre sont correlees par construction. Rendre une liste incomplete
    plutot que vide donnerait un combine plus court que sa regle, sans que rien
    ne le dise.
    """
    retenus: list[Pick] = []
    matchs: set[int] = set()
    familles: set[str] = set()
    for pick in sorted(pool, key=_rank):
        if pick.event_id is not None and pick.event_id in matchs:
            continue
        famille = _family(pick, known)
        if families and famille in familles:
            continue
        retenus.append(pick)
        if pick.event_id is not None:
            matchs.add(pick.event_id)
        familles.add(famille)
        if len(retenus) == legs:
            return retenus
    return []


def build(day: str, settings: Settings | None = None, now: datetime | None = None) -> DayRecap:
    """Rassemble une journee d'analyse. Aucun appel reseau, aucune ecriture."""
    settings = settings or get_settings()
    jour = str(day).strip()
    surs = _safest_keys(settings)
    familles = load(settings)

    principales: list[Pick] = []
    exploratoires: list[Pick] = []
    for pick in list_picks_for_day(jour, settings):
        (exploratoires if pick.exploratory else principales).append(pick)

    commences = [
        pick for pick in principales if pick.commence_time and has_started(pick.commence_time, now)
    ]
    ids_commences = {pick.pick_id for pick in commences}

    candidates = [
        pick
        for pick in principales
        if pick.pick_id not in ids_commences
        and not pick.late
        and pick.price is not None
        and pick.tier in surs
    ]

    propositions = []
    for jambes, cran, distinctes in PROPOSALS:
        vivier = [pick for pick in candidates if (pick.confidence or 0) >= cran]
        propositions.append(
            Proposal(
                legs=jambes,
                min_confidence=cran,
                distinct_families=distinctes,
                pool=vivier,
                chosen=_choose(vivier, jambes, distinctes, familles),
            )
        )

    return DayRecap(
        day=jour,
        rendered_at=now or datetime.now(UTC),
        lines=_chronology(principales),
        exploratory=exploratoires,
        started=commences,
        proposals=propositions,
    )


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
        trim_blocks=False,
    )


def render(recap: DayRecap, settings: Settings | None = None) -> str:
    """Le corps du prompt. **Le gabarit vit dans son propre repertoire.**

    `prompt.list_templates` et `prompt.template_fingerprint` balaient
    `templates/prompts` : y poser ce gabarit l'aurait propose comme gabarit
    d'analyse dans le menu, et fait bouger l'empreinte du cadre sans qu'aucune
    decision d'analyse ait ete prise.
    """
    settings = settings or get_settings()
    template = _environment().get_template(TEMPLATE_NAME)
    # Chaque porte du gabarit laisse sa ligne vide quand elle ne rend rien.
    # La regle est celle du prompt d'analyse, appelee et non recopiee.
    return collapse_blank_lines(template.render(recap=recap, overlap_labels=OVERLAP_LABELS))


def note_service(day: str, settings: Settings | None = None) -> int | None:
    """Date le **premier rendu** du recapitulatif. Rend son id, ou `None`.

    Ni la livraison ni le deploiement : le regime change au moment ou la surface
    sert, et c'est l'idiome de `note_price_coverage` et de `note_feedback`.

    **Ce qu'elle date, et ce qu'elle ne date pas.** Le recapitulatif n'ecrit rien
    et ne touche aucune population : `analysis()` ignore `played`, et rien de ce
    qu'il rend n'entre en base. Ce n'est donc pas un point de rupture. Mais
    `picks.played` vaut zero sur toute la base depuis toujours ; le jour ou il
    cesse de valoir zero, cette surface sera la cause la plus probable, et seule
    une entree datee dira a partir de quand. Sans elle, un journal muet dirait de
    la meme facon « rien n'a bouge » et « la surface n'a jamais servi ».

    Portee `RESTITUTION` : ce qui apparait est une **page**, pas un changement du
    gabarit d'analyse. L'y ranger aurait fait lire un point de coupe du cadre la
    ou le cadre n'a pas bouge.

    **Une fois et une seule**, et la garde se lit sur le journal lui-meme — un
    compteur en memoire ne survivrait pas au redemarrage, et un drapeau en base
    serait une seconde ecriture de ce que le journal dit deja.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        deja = conn.execute(
            "SELECT 1 FROM changelog_mesure WHERE label = ? LIMIT 1", (SERVICE_LABEL,)
        ).fetchone()
    if deja is not None:
        return None
    return changelog.add(
        str(day).strip(),
        SERVICE_LABEL,
        "Premier rendu du recapitulatif du jour. Il n'ecrit rien et ne touche aucune "
        "population mesuree ; ce qu'il change est ce qui est pose. `picks.played` vaut "
        "zero avant cette date : toute lecture qui la traverse doit le savoir.",
        scope=changelog.RESTITUTION,
        settings=settings,
    )
