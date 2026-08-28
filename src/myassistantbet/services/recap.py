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
from .history import PRICE_REFERENCE, Pick, list_picks_for_day
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
INDETERMINE = "indetermine"

#: **Trois etats et jamais deux.** « rien ne court encore » et « on ne peut pas
#: le dire » appellent des comportements opposes, et les confondre reproduirait
#: le defaut de `Absents : donnees non disponibles` — un silence qui ressemble a
#: une information. Le rendu les definit tous les trois : un libelle sans
#: definition est le defaut que ce projet evite partout.
OVERLAP_LABELS: dict[str, str] = {
    LIBRE: "libre — rien de ce qui précède ne court encore",
    CHEVAUCHE: "chevauche — un match précédent court encore",
    INDETERMINE: "indéterminé — un match précédent n'a pas de fin connue",
}

#: Les trois propositions, **nommees par leur regle**. Aucun adjectif : ni
#: « safe » ni « conservateur ». Le mot « palier » n'a qu'un sens dans
#: l'application — la bande de la cote d'**une selection** — et `Combo` n'en
#: porte aucun. Trois jambes SAFE a 1.60 produisent 4.10 : appeler ce combine
#: « safe » ferait designer deux choses par un meme mot, et le taux de reussite
#: par bande de cote cesserait de mesurer une bande de cote.
#:
#: Chaque couple est `(jambes, cran minimal)`. Les deux grandeurs sont deja en
#: base ; l'adjectif ne l'est pas.
PROPOSALS: tuple[tuple[int, int], ...] = ((3, 4), (3, 3), (4, 3))

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


@dataclass(frozen=True)
class Proposal:
    """Une des trois propositions. **Elle porte sa regle comme nom.**

    Pas de champ `tier`, et un banc le verifie : le palier appartient a la
    selection, jamais au combine.
    """

    legs: int
    min_confidence: int
    pool: list[Pick] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.legs} jambes, cran ≥ {self.min_confidence}"

    @property
    def matches(self) -> int:
        """Le vivier **en matchs distincts**, jamais en lignes.

        Une seule selection par match dans un combine : deux lignes sur la meme
        rencontre n'en font qu'une. Le compte le dit avant le modele, plutot que
        de le laisser decouvrir qu'il lui manque une jambe.
        """
        return len({pick.event_id for pick in self.pool if pick.event_id is not None})

    @property
    def enough(self) -> bool:
        return self.matches >= self.legs


@dataclass
class DayRecap:
    """Ce qu'une journee d'analyse a produit, rassemble pour la composition."""

    day: str
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

    La regle est severe a dessein : des qu'une seule des lignes qui precedent
    n'a pas de fin connue, on ne conclut pas. Un `libre` prononce derriere un
    match de tennis affirmerait ce qu'aucune source ne permet de dire.
    """
    ordonnees = sorted(picks, key=lambda pick: (pick.commence_time or "9999", pick.pick_id))
    lignes: list[Line] = []
    derniere_fin: datetime | None = None
    fin_inconnue = False
    for pick in ordonnees:
        debut = _moment(pick.commence_time)
        if fin_inconnue or debut is None:
            etat = INDETERMINE
        elif derniere_fin is not None and debut < derniere_fin:
            etat = CHEVAUCHE
        else:
            etat = LIBRE
        lignes.append(Line(pick=pick, overlap=etat))
        fin = _end_of(pick)
        if fin is None:
            fin_inconnue = True
        elif derniere_fin is None or fin > derniere_fin:
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


def build(day: str, settings: Settings | None = None, now: datetime | None = None) -> DayRecap:
    """Rassemble une journee d'analyse. Aucun appel reseau, aucune ecriture."""
    settings = settings or get_settings()
    jour = str(day).strip()
    surs = _safest_keys(settings)

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

    propositions = [
        Proposal(
            legs=jambes,
            min_confidence=cran,
            pool=[pick for pick in candidates if (pick.confidence or 0) >= cran],
        )
        for jambes, cran in PROPOSALS
    ]

    return DayRecap(
        day=jour,
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
