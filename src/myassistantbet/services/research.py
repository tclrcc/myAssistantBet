"""Ou depenser un budget de recherche fini (SPEC.md section 9, corollaire).

Le prompt sert tous les matchs du lot a plat, et demande a l'analyse d'etablir
les faits avant d'analyser. Sur un petit lot c'est tenable ; sur vingt-et-un
matchs, non — et la mesure est nette.

Releve sur cinq sessions reelles : un lot de 21 manches retour de Conference
League a produit **3 dossiers reellement traites**, choisis au jugé sur les
matchs qui paraissaient les plus lisibles, et 2 selections a confiance >= 3 sur
8. Les 18 autres sont retombes en `lecture`, donc a confiance 1. Le lot ne
donnait **aucun moyen de savoir ou chercher** : ce n'est pas un manque de temps,
c'est un budget de requetes depense sans ordre de passage.

Ce module classe les dossiers par **ce qu'une recherche peut y changer**, et
emet pour chacun les questions que ses criteres declenchent. Il ne retire rien
au modele : il lui dit ou depenser.

**Aucun critere ne regarde les cotes, et c'est une decision.** Un match a 1.08
n'a en apparence pas besoin de recherche — mais trier sur le prix rendrait le
tri circulaire : on ne chercherait jamais la ou le marche est confiant, donc on
ne trouverait jamais l'information qui le contredit. Le preambule limite deja
les cotes a deux usages, et en ajouter un troisieme affaiblirait les deux
autres. Il y a assez de criteres sans prix.

Aucun appel reseau : tout sort de la base, et l'essentiel de ce qui a **deja**
ete calcule pour le bloc.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect
from . import context as context_service
from .context import NEUTRAL_MARK
from .labels import affiche, context_family, expected_context
from .render import RenderableEvent
from .session import context_density
from .thresholds import value_of as threshold

logger = logging.getLogger(__name__)

#: Poids des criteres, dans l'echelle de la specification : `+++` vaut 3, `++`
#: vaut 2, `+` vaut 1, `---` vaut -3. Ils ne sont pas reglables : ce ne sont pas
#: des preferences mais le rendement mesure de chaque piste sur des sessions
#: reelles. Le seul nombre qui se regle est **combien de dossiers** la fiche
#: porte, parce que celui-la depend de qui la lit.
STRONG = 3
MEDIUM = 2
WEAK = 1
PENALTY = -3

#: Ecart au cumul jusqu'auquel un tour est **ouvert** : un but, ou rien du tout.
#: Mesure : les quatre selections de maniere d'un lot reel venaient toutes de la.
OPEN_TIE_GAP = 1

#: Ecart au cumul a partir duquel un tour est **joue**. A trois buts, une
#: recherche ne change plus rien au scenario — c'est le seul critere negatif qui
#: porte sur le sport plutot que sur notre collecte.
#:
#: Entre les deux — deux buts — ni l'un ni l'autre. Deux buts a remonter n'est
#: pas un tour ouvert, mais ca se remonte : classer cet ecart avec l'un ou
#: l'autre aurait fait passer un tour jouable pour mort, ou l'inverse.
DEAD_TIE_GAP = 3

#: Densite du bloc, en pourcentage des lignes attendues, sous laquelle notre
#: collecte a laisse un trou que la recherche peut combler.
THIN_DENSITY = 50
#: Et sous laquelle il n'y a probablement rien a trouver du tout : une
#: competition que le fournisseur de contexte ne couvre pas ne se documente pas
#: mieux ailleurs. Le cas mesure : un bloc a 2 lignes sur 24, sur une
#: competition sans identifiant de ligue — chercher n'y a rien donne.
BARREN_DENSITY = 30

#: Nombre de lignes manquantes nommees dans la question d'un bloc pauvre. Toutes
#: les citer ferait une question de quinze noms que personne ne lit.
MISSING_NAMED = 4

#: Prochain match d'une equipe, en jours, sous lequel la rotation devient un
#: facteur. Lu dans la ligne « Calendrier », qui l'ecrit deja.
ROTATION_DAYS = 3
_CALENDAR = re.compile(r"dans (\d+)j")


@dataclass(frozen=True)
class Reason:
    """Un critere declenche : son poids, le motif rendu, la question emise.

    **La question est ce qui fait la valeur, pas la liste de matchs.** « Cherche
    sur ce match » ne fait rien gagner ; « ou se joue reellement ce match » se
    repond en une requete et clot un point.
    """

    weight: int
    motif: str
    question: str


@dataclass
class Dossier:
    """Un match de la fiche, son score et ce qu'il faut y chercher."""

    index: int
    label: str
    reasons: list[Reason] = field(default_factory=list)
    #: Ou aller. Aujourd'hui une requete de recherche par dossier : c'est le
    #: chemin qui a reellement fonctionne, et aucun lien profond n'est
    #: construisible sans identifiants que la base ne porte pas encore.
    links: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(reason.weight for reason in self.reasons)

    @property
    def motifs(self) -> str:
        """`tie ouvert : ecart 1 · l'equipe menee recoit`."""
        return " · ".join(reason.motif for reason in self.reasons if reason.weight > 0)

    @property
    def questions(self) -> list[str]:
        """Sans doublon : deux criteres peuvent viser la meme verification."""
        vues: list[str] = []
        for reason in self.reasons:
            if reason.question and reason.question not in vues:
                vues.append(reason.question)
        return vues


@dataclass
class Sheet:
    """La fiche de recherche d'un lot."""

    lot: int
    budget: int
    dossiers: list[Dossier] = field(default_factory=list)

    @property
    def crowded(self) -> bool:
        """Vrai quand le lot depasse ce qu'une session peut couvrir.

        En dessous, la fiche ne se rend pas : classer trois dossiers sur trois
        n'apprend rien, et la ligne de budget ferait renoncer a un match qu'il y
        avait tout le temps de traiter.
        """
        return self.lot > self.budget


def sheet(events: list[RenderableEvent], settings: Settings | None = None) -> Sheet:
    """Classe les dossiers d'un lot par ce qu'une recherche peut y changer.

    Les evenements arrivent **deja rendus** : leurs lignes de contexte sont
    celles du bloc, donc la fiche et le bloc ne peuvent pas se contredire. Le
    seul acces en base est la charge utile de la double confrontation, que le
    bloc n'expose pas en clair.
    """
    settings = settings or get_settings()
    budget = threshold("recherche_dossiers", settings)
    resultat = Sheet(lot=len(events), budget=budget)
    if len(events) <= budget:
        return resultat

    dossiers = [_dossier(event, settings) for event in events]
    # Un score nul ou negatif ne se propose pas : la fiche dirait « cherche
    # ici » sur un dossier dont tous les criteres disent l'inverse.
    retenus = [item for item in dossiers if item.score > 0]
    retenus.sort(key=lambda item: (-item.score, item.index))
    resultat.dossiers = retenus[:budget]
    logger.info(
        "fiche de recherche : %d dossiers retenus sur %d, budget %d",
        len(resultat.dossiers),
        len(events),
        budget,
    )
    return resultat


def _dossier(event: RenderableEvent, settings: Settings) -> Dossier:
    """Les criteres declenches par un match, un par un."""
    labels = [label for label, value in event.context_lines if value]
    lignes = {label: value for label, value in event.context_lines if value}
    item = Dossier(index=event.index, label=affiche(event.home, event.away))

    item.reasons += _tie_reasons(event, settings)
    item.reasons += _density_reasons(event, labels, settings)
    item.reasons += _venue_reasons(lignes)
    item.reasons += _weather_reasons(lignes)
    item.reasons += _squad_reasons(lignes)
    item.reasons += _rotation_reasons(lignes)
    item.reasons += _tennis_reasons(event, lignes)
    item.links = _links(event)
    return item


def _tie_reasons(event: RenderableEvent, settings: Settings) -> list[Reason]:
    """Le tour est-il encore ouvert ? C'est le critere le plus rentable mesure.

    Les quatre selections de « maniere » d'un lot reel venaient toutes d'un tie
    a un but d'ecart ou d'un aller nul. A trois buts, l'inverse : le scenario est
    ecrit, et une recherche n'y changera rien.

    **Trois etats et non deux, et l'ecart de deux buts est le troisieme.** Il ne
    monte pas — deux buts a remonter n'est pas un tour ouvert — et il ne descend
    pas non plus : il se remonte, et l'obligation qui va avec reste exploitable.
    Le classer avec l'un ou l'autre aurait fait passer un tour jouable pour mort,
    ou l'inverse.
    """
    if not event.event_id:
        return []
    etat = context_service.tie_state(event.event_id, _when(event), settings)
    if etat is None:
        return []
    if etat.gap >= DEAD_TIE_GAP:
        return [Reason(PENALTY, f"tour joue : ecart {etat.gap}", "")]

    raisons = []
    if etat.gap <= OPEN_TIE_GAP:
        raisons.append(
            Reason(
                STRONG,
                "aller nul" if not etat.gap else f"tie ouvert : ecart {etat.gap}",
                "Absences des deux cotes, et onze annonce ?",
            )
        )
    if etat.trailing_at_home:
        # L'obligation asymetrique est le fait le plus exploitable d'une manche
        # retour : celui qui doit marquer a le terrain, donc il s'ouvrira.
        raisons.append(
            Reason(
                STRONG,
                "l'equipe menee recoit",
                f"Composition annoncee de {event.home} : sort-elle son onze offensif ?",
            )
        )
    return raisons


def _density_reasons(event: RenderableEvent, labels: list[str], settings: Settings) -> list[Reason]:
    """Un bloc pauvre est un trou que la recherche comble — sauf s'il est vide.

    Distinction mesuree : a 10 lignes sur 24, la recherche a de quoi travailler ;
    a 2 sur 24 sur une competition que le fournisseur ne couvre pas, elle n'a
    rien trouve non plus, et le savoir d'avance epargne une requete.
    """
    density = context_density(labels, event.sport_key, settings)
    if not density.expected:
        return []
    part = round(100 * density.filled / density.expected)
    # **Un identifiant absent n'est pas une source absente.** Sans evenement en
    # base on ne peut pas savoir si la competition est rattachee, et l'ecarter
    # au benefice du doute reviendrait a punir un match de ce qu'on ignore de
    # lui — l'inverse de ce que cette fiche existe pour faire.
    sterile = event.event_id and not _has_context_source(event, settings)
    if part < BARREN_DENSITY and sterile:
        return [Reason(PENALTY, f"bloc quasi vide ({part} %) et aucune source", "")]
    if part < THIN_DENSITY:
        # **La question nomme les lignes manquantes**, parce que l'application
        # les connait. « Ce que le bloc ne porte pas » etait un doublon mou de
        # la question precedente et ne disait pas ou aller ; deux questions
        # precises valent mieux que trois dont une est du remplissage — la meme
        # regle que « ne remplis jamais un palier avec du vide ».
        manquantes = _missing(labels, event.sport_key)
        return [
            Reason(
                MEDIUM,
                f"bloc pauvre ({part} %)",
                f"Bloc a {part} % : ni {', ni '.join(manquantes)}. "
                "Chercher un compte rendu du dernier match des deux equipes.",
            )
        ]
    return []


def _missing(labels: list[str], sport_key: str) -> list[str]:
    """Les lignes que ce bloc n'a pas, limitees a ce qui se cite.

    Toutes les nommer ferait une question de quinze noms que personne ne lit :
    `MISSING_NAMED` en garde les premieres, dans l'ordre du referentiel — donc
    les plus decisives d'abord.
    """
    present = {context_family(label) for label in labels}
    absentes = [label for label in expected_context(sport_key) if label not in present]
    return [label.lower() for label in absentes[:MISSING_NAMED]] or ["aucune ligne"]


def _venue_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Un terrain neutre change qui pousse et qui subit — et le public avec.

    Le fait portait deux des huit selections d'un lot reel. La question ne porte
    pas sur le lieu, qui est deja ecrit, mais sur ce que le lieu **change** : le
    public est le vrai sujet.
    """
    if NEUTRAL_MARK not in (lignes.get("Lieu") or ""):
        return []
    return [
        Reason(
            MEDIUM,
            "terrain neutre",
            "Ou se joue reellement ce match, et quel public est attendu ?",
        )
    ]


def _weather_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Une alerte officielle en vigueur, et rien d'autre.

    Mesure : sur cinq sessions, la temperature n'a jamais rien change ; l'alerte
    a change une section entiere, deux fois, parce qu'elle disait que la
    rencontre pouvait ne pas se jouer. Le critere ne se declenche donc pas sur
    « 30 C, pluie 80 % » — un lot d'ete monterait en entier — mais sur le seul
    fait qui puisse l'emporter sur tout le reste du bloc.
    """
    if "ALERTE" not in (lignes.get("Meteo") or ""):
        return []
    return [
        Reason(
            MEDIUM,
            "alerte meteo en vigueur",
            "Etat de l'alerte a l'heure du coup d'envoi : report, huis clos, terrain praticable ?",
        )
    ]


def _squad_reasons(lignes: dict[str, str]) -> list[Reason]:
    """La ligne « Effectif » est une **piste datee**, pas un fait.

    Elle se reconstruit de feuilles de match, et elle s'est deja trompee : trois
    joueurs annonces « plus vus depuis le 23/07 » etaient titulaires le 06/08 —
    la fenetre lue avait manque les matchs europeens. Un signal faux qui aurait
    tue le bon angle, donc un signal a verifier.
    """
    valeur = lignes.get("Effectif")
    if not valeur:
        return []
    return [
        Reason(
            WEAK,
            "piste d'absence a confirmer",
            "Les joueurs de la ligne « Effectif » ont-ils rejoue depuis leur derniere "
            "date vue ? Blessure, suspension ou mise a l'ecart ?",
        )
    ]


def _rotation_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Un match rapproche est un facteur de rotation, et il est deja ecrit.

    La ligne « Calendrier » porte « dans 3j » : rien a recalculer, seulement a
    lire. Le nombre vient du rendu et non d'un second calcul — deux ecritures du
    meme ecart auraient fini par differer.
    """
    valeur = lignes.get("Calendrier")
    if not valeur:
        return []
    jours = [int(found) for found in _CALENDAR.findall(valeur)]
    if not jours or min(jours) > ROTATION_DAYS:
        return []
    return [
        Reason(
            WEAK,
            f"prochain match dans {min(jours)}j",
            "Une rotation a-t-elle ete annoncee en conference de presse ?",
        )
    ]


def _tennis_reasons(event: RenderableEvent, lignes: dict[str, str]) -> list[Reason]:
    """Les tours de ce tournoi que l'historique ne compte pas encore.

    C'est le trou le plus couteux d'une journee de tennis : « Fraicheur » dit
    combien de matchs manquent et les nomme, et ce sont exactement ceux dont
    aucune de nos lignes ne porte le score ni la duree.
    """
    if event.sport_key != "tennis":
        return []
    valeur = lignes.get("Fraicheur") or ""
    if "non comptes" not in valeur:
        return []
    return [
        Reason(
            MEDIUM,
            "tours de ce tournoi non recenses",
            "Score set par set, duree et statistiques de service des tours deja joues ici ?",
        )
    ]


def _links(event: RenderableEvent) -> list[str]:
    """Ou aller, et **sous quelle forme ca a reellement fonctionne**.

    Aucun lien profond n'est rendu, et ce n'est pas un renoncement : les
    construire demande des identifiants que la base ne porte pas — l'id de match
    UEFA, l'adresse du site d'un tournoi ou d'un club. Ils se collecteront, et la
    fonction les ajoutera.

    En attendant, la **requete de recherche** est le chemin qui a marche : les
    scores ATP d'une journee reelle ont ete obtenus par des extraits de recherche
    pointant vers `atptour.com`, la page elle-meme refusant nos agents. Le
    domaine reste l'editeur, donc le niveau de source tient ; c'est le chemin
    d'acces qui differe, et une requete formulee epargne une requete perdue.
    """
    quand = event.commence_local.strftime("%d/%m/%Y")
    return [f'rechercher "{event.home} {event.away} {event.competition} {quand}"']


def _has_context_source(event: RenderableEvent, settings: Settings) -> bool:
    """La competition est-elle rattachee a un fournisseur de contexte ?

    Sans identifiant de ligue, aucun contexte n'est jamais demande — et rien
    d'autre ne le sert. Un bloc vide y est un fait de couverture, pas un trou de
    collecte a combler.
    """
    if not event.event_id:
        return False
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT c.apifootball_league_id AS ligue, c.tennisdata_tournaments AS tournois "
            "FROM events e LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE e.id = ?",
            (event.event_id,),
        ).fetchone()
    if row is None:
        return False
    return bool(row["ligue"]) or bool(row["tournois"])


def _when(event: RenderableEvent) -> str:
    """Le coup d'envoi, sous une forme que `context` sait relire.

    `commence_local` porte son fuseau : son ISO designe le meme instant que la
    valeur stockee, et c'est l'instant qui compte pour dater un aller.
    """
    return event.commence_local.isoformat()
