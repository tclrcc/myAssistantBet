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
from .context import CAUSE_LABELS, CAUSE_NOT_COVERED, COLLECTION_FAULTS, NEUTRAL_MARK
from .labels import affiche, context_family, expected_context, sort_key
from .render import MERGED_MARKETS, RenderableEvent
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

#: Un cran de retrogradation, et **pas un veto**. `PENALTY` en est un en
#: pratique : `sheet()` ecarte tout dossier dont le score n'est pas positif, donc
#: -3 pose sur un bloc pauvre (+2) le fait disparaitre. Un critere qui ferait
#: disparaitre un match a lui seul serait un filtre, quand cette fiche est un
#: **ordre de passage**.
DEMOTION = -1

#: En dessous de combien de marches rendus un dossier ne peut plus produire
#: qu'une selection de 1N2 — le marche que le releve mesure a -3,4 de residu, et
#: que le gabarit demande precisement de depasser.
#:
#: **Un seul, et c'est mesure.** Sur les 462 blocs archives, marches fusionnes :
#: 3 blocs de football sur 271 et 1 de tennis sur 191 n'en portent qu'un, soit
#: **1 % de part et d'autre**. Le palier suivant est a trois — 99 blocs de
#: football et 190 de tennis — et trois marches suffisent a traduire un angle de
#: maniere. Un critere qui se declencherait sur 38 % des blocs ne classerait plus
#: rien : c'est la regle appliquee avant d'ecrire celui-la.
#:
#: Le seuil ne se decline pas par sport, et c'est la mesure qui l'autorise : la
#: norme est de 12 marches au football et de 3 au tennis, mais « un seul » y
#: designe la meme part infime.
NARROW_MARKETS = 1

#: Repos, en heures, sous lequel la charge de la veille devient un facteur.
#:
#: **Le seuil se pose sous un mode, et le mode est le cas ordinaire.** Mesure sur
#: les 48 blocs de tennis rendus depuis le 20/08/2026 : la distribution porte un
#: pic net a **23 h — 9 blocs** — qui est le retour de la meme session la veille,
#: c'est-a-dire le rythme normal d'un tournoi. « Moins de 24 h » designe donc
#: **27 %** des blocs, et un critere qui se declenche sur un quart du lot ne
#: classe plus rien — c'est le reproche fait aux deux criteres faibles du
#: football, et il vaudrait ici.
#:
#: Sous le mode, l'ecart veut dire autre chose : le joueur a joue **plus tard
#: hier qu'il ne joue aujourd'hui**. Ce seuil designe **4 blocs (8 %)**, et le
#: voisin a 22 h en designe 3.
#:
#: **Premiere version ecrite a 24 h sur un compte faux**, releve avec une lecture
#: qui ne captait que le premier joueur de la ligne : 6 blocs au lieu de 13. Un
#: taux qui surprend se re-verifie sur sa cle avant d'etre ecrit — et celui-la ne
#: surprenait meme pas.
#:
#: La ligne `Repos` porte deja l'ecart en heures : rien a recalculer, seulement a
#: lire. Deux ecritures du meme ecart auraient fini par differer — meme raison
#: que `Calendrier` au football.
SHORT_REST_HOURS = 23
_REST_HOURS = re.compile(r"\b(\d+) h \(")

#: En dessous de combien de matchs les lignes de forme d'un joueur ne decrivent
#: plus rien. **Mesure sur les 406 blocs de tennis archives** : ce seuil designe
#: 5 blocs (1 %), quand « moins de quatre » en designe 9 et « moins de cinq »
#: 15. Un critere de priorite doit designer une minorite stricte, sinon il ne
#: classe plus rien — c'est la regle appliquee avant de l'ecrire.
THIN_PLAYER_MATCHES = 3

#: Ecart au cumul jusqu'auquel un tour est **ouvert** : un but, ou rien du tout.
#: Mesure : les quatre selections de maniere d'un lot reel venaient toutes de la.
OPEN_TIE_GAP = 1

#: Ecart au cumul a partir duquel un tour est **joue**. A trois buts, une
#: recherche ne change plus rien au scenario — c'est le seul critere negatif qui
#: porte sur le sport plutot que sur notre collecte.
DEAD_TIE_GAP = 3

#: Poids d'un tour encore ouvert, **par ecart au cumul**.
#:
#: La documentation annoncait « trois etats » ; le code en produisait trois
#: autres — ouvert (+3), **rien du tout**, mort (-3). L'ecart de deux buts, cense
#: etre le troisieme, ne declenchait aucune raison : mesure sur le lot du
#: 13/08/2026, M12 (Egnatia, ecart 2) marquait comme un match sans manche aller,
#: et se retrouvait a egalite avec M10 (ecart 1). Un tour a deux buts se remonte,
#: donc il ne vaut pas zero ; il ne vaut pas non plus un tour a un but.
#:
#: L'echelle est graduee plutot que ternaire : un aller nul est plus ouvert qu'un
#: ecart d'un but, qui est plus ouvert qu'un ecart de deux. Les trois etats
#: existent enfin, et ils sont ceux que le code produit.
OPEN_TIE_WEIGHTS = {0: 4, 1: 3, 2: 2}

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
    #: Ecart au cumul de la manche aller, quand il y en a une. Sert au
    #: **departage** et non au score, que `OPEN_TIE_WEIGHTS` porte deja.
    gap: int | None = None
    #: Remplissage du bloc, en pourcentage. Second critere de departage.
    density: int = 0
    #: Ou aller. Aujourd'hui une requete de recherche par dossier : c'est le
    #: chemin qui a reellement fonctionne, et aucun lien profond n'est
    #: construisible sans identifiants que la base ne porte pas encore.
    links: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        """Ce qui **ordonne** le dossier : tous les criteres, retrogradations
        comprises."""
        return sum(reason.weight for reason in self.reasons)

    @property
    def merit(self) -> int:
        """Ce qui decide si le dossier **se propose** : tout sauf `DEMOTION`.

        **Les deux questions ne sont pas la meme, et les confondre fait un
        veto.** Un score nul ne se propose pas — la fiche dirait « cherche ici »
        sur un dossier dont les criteres disent l'inverse. Mais une
        retrogradation ne dit pas « ne cherche pas » : elle dit « ce que tu
        trouveras vaudra moins ». Comptee dans le filtre, elle faisait
        disparaitre un dossier a un seul critere — exactement ce que le brief
        interdit : *il descend au rang que sa densite lui donne, il ne descend
        pas en dernier pour autant*.
        """
        return sum(reason.weight for reason in self.reasons if reason.weight != DEMOTION)

    @property
    def rank_key(self) -> tuple[int, int, int, int]:
        """Ordre de passage : score, puis **ce qui veut dire quelque chose**.

        **Le departage etait l'heure du coup d'envoi**, par l'index du bloc, et
        ca n'a aucun rapport avec ce qu'une recherche peut changer. Mesure sur le
        lot du 13/08/2026 : M10 finissait 8e a egalite de score avec M4, et
        sortait des sept dossiers proposes sur ce seul critere — c'est lui qui a
        produit la meilleure information de la soiree.

        **Et le tri par heure n'est pas neutre, il est oriente.** Les diffuseurs
        programment les grosses affiches en dernier ; l'audience est correlee a
        la couverture presse, donc a ce qu'une recherche peut trouver. Trier par
        heure croissante trie approximativement par interet decroissant, et
        systematiquement plutot qu'accidentellement — le meme lot le refera a
        chaque journee europeenne.

        Deux criteres signifiants le remplacent. **L'ecart au cumul croissant**
        d'abord : a score egal, un tie plus serre passe avant. **La densite
        decroissante** ensuite : c'est la que la recherche complete au lieu de
        tout reconstruire. L'index ne sert plus que de dernier recours, pour que
        l'ordre reste deterministe.
        """
        return (
            -self.score,
            self.gap if self.gap is not None else DEAD_TIE_GAP,
            -self.density,
            self.index,
        )

    @property
    def motifs(self) -> str:
        """`tie ouvert : ecart 1 · l'equipe menee recoit`.

        **Les motifs negatifs y figurent aussi.** Une retrogradation que le
        lecteur ne voit pas est un garde-fou muet — le defaut que ce projet
        nomme partout ailleurs. Seul un poids nul reste tu : il ne decide de
        rien.
        """
        return " · ".join(reason.motif for reason in self.reasons if reason.weight)

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
        """Vrai quand le lot depasse ce que le budget couvre.

        **Ce n'est plus la condition d'affichage de la fiche, et c'est un
        changement voulu.** Elle ne se rendait pas sous le seuil, au motif que
        « classer trois dossiers sur trois n'apprend rien ». C'est vrai d'un
        **tri** et faux d'un **ordre de traitement** : sur un lot plus court que
        le budget, tous les matchs sont ouvrables, et le classement dit encore
        par lequel commencer.

        `crowded` ne decide donc plus que du **texte** : au-dessus, les matchs
        non recherches se rendent en `lecture` et c'est un resultat attendu ; en
        dessous, cette phrase serait hors sujet et trompeuse.
        """
        return self.lot > self.budget

    @property
    def available(self) -> int:
        """Dossiers reellement ouvrables : `min(budget, lot)`.

        Sur un lot plus court que le budget, c'est le lot qui borne — et le
        prompt annonce ce nombre-la plutot que le reglage, sinon il inviterait a
        chercher des dossiers qui n'existent pas.
        """
        return min(self.budget, self.lot)


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
    if not events:
        return resultat

    dossiers = [_dossier(event, settings) for event in events]
    # Un score nul ou negatif ne se propose pas : la fiche dirait « cherche
    # ici » sur un dossier dont tous les criteres disent l'inverse. Le filtre lit
    # `merit` et non `score` : une retrogradation ordonne, elle n'ecarte pas.
    retenus = [item for item in dossiers if item.merit > 0]
    retenus.sort(key=lambda item: item.rank_key)
    # `min(budget, lot)` par construction : `retenus` ne peut pas depasser le lot.
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

    item.gap = _gap_of(event, settings)
    item.density = _density_of(event, labels, settings) or 0
    item.reasons += _tie_reasons(event, settings)
    item.reasons += _density_reasons(event, labels, settings)
    item.reasons += _venue_reasons(lignes)
    item.reasons += _weather_reasons(lignes)
    item.reasons += _squad_reasons(lignes)
    item.reasons += _rotation_reasons(lignes)
    item.reasons += _tennis_reasons(event, lignes, settings)
    item.reasons += _market_reasons(event)
    item.links = _links(event)
    return item


def _market_reasons(event: RenderableEvent) -> list[Reason]:
    """Un bloc qui ne porte qu'un marche plafonne ce qu'une recherche peut y faire.

    **Ce n'est pas regarder une cote.** Compter les marches servis n'est pas
    comparer un prix, et la regle « la fiche ne regarde aucune cote » tient
    entiere : aucune **valeur** n'est lue, seulement le nombre de familles
    presentes. Le tri reste donc non circulaire — c'est ce que le prix vaut qui
    est interdit, pas le fait qu'un marche existe.

    Mesure du 20/08/2026 sur le lot de reference : M1 et M2 etaient classes
    **2e et 3e** a chercher sur leur densite (42 %), et ces deux blocs ne portent
    que le 1N2, tout le reste etant « non servi » **sur toute la competition**,
    donc definitivement. Quelle que soit la qualite de la recherche, ces dossiers
    ne peuvent produire qu'une selection de 1N2 — le marche que le releve mesure
    a -3,4 de residu, et que le gabarit demande de depasser.

    **Un cran, jamais un veto** : le dossier descend au rang que sa densite lui
    donne, corrige de ce qu'une decouverte peut s'y traduire. Un match sur lequel
    il n'y a qu'un marche reste un match sur lequel chercher peut valoir la
    peine ; c'est son rang qui change.

    Les variantes « alternate » sont fusionnees comme au rendu : c'est ce que
    l'analyse **voit** qu'on compte, pas ce que l'API a servi.
    """
    familles = {MERGED_MARKETS.get(cle, cle) for cle, prix in event.markets.items() if prix}
    if not familles or len(familles) > NARROW_MARKETS:
        return []
    return [
        Reason(
            DEMOTION,
            f"{len(familles)} seul marche servi — toute selection y sera un 1N2",
            "",
        )
    ]


def _tie_reasons(event: RenderableEvent, settings: Settings) -> list[Reason]:
    """Le tour est-il encore ouvert ? C'est le critere le plus rentable mesure.

    Les quatre selections de « maniere » d'un lot reel venaient toutes d'un tie
    a un but d'ecart ou d'un aller nul. A trois buts, l'inverse : le scenario est
    ecrit, et une recherche n'y changera rien.

    **Trois etats et non deux, et l'ecart de deux buts est le troisieme** — mais
    il ne l'etait que dans la documentation. Le code produisait ouvert (+3), rien
    du tout, mort (-3) : a deux buts, aucune raison ne se declenchait, et M12 du
    lot du 13/08 marquait comme un match sans manche aller. `OPEN_TIE_WEIGHTS`
    gradue l'echelle, et les trois etats existent enfin.

    **`l'equipe menee recoit` est un modificateur, pas un critere primaire**, et
    le peser `STRONG` en faisait l'egal du fait qu'il y ait encore un tour a
    jouer. Il doublait le score d'un tie ouvert : sur le lot du 13/08, M1 et M5
    montaient a 7 quand M10 restait a 5, et l'ecart au cumul y etait le meme.
    Recevoir change le scenario, il ne cree pas l'enjeu.
    """
    if not event.event_id:
        return []
    etat = context_service.tie_state(event.event_id, _when(event), settings)
    if etat is None:
        return []
    if etat.gap >= DEAD_TIE_GAP:
        return [Reason(PENALTY, f"tour joue : ecart {etat.gap}", "")]

    raisons = []
    poids = OPEN_TIE_WEIGHTS.get(etat.gap)
    if poids:
        raisons.append(
            Reason(
                poids,
                "aller nul" if not etat.gap else f"tie ouvert : ecart {etat.gap}",
                "Absences des deux cotes, et onze annonce ?",
            )
        )
    if etat.trailing_at_home:
        # L'obligation asymetrique est le fait le plus exploitable d'une manche
        # retour : celui qui doit marquer a le terrain, donc il s'ouvrira.
        raisons.append(
            Reason(
                MEDIUM,
                "l'equipe menee recoit",
                f"Composition annoncee de {event.home} : sort-elle son onze offensif ?",
            )
        )
    return raisons


def _density_of(event: RenderableEvent, labels: list[str], settings: Settings) -> int | None:
    """Remplissage du bloc en pourcentage, ou None si le sport n'a pas de referentiel.

    **Un seul calcul, deux lecteurs** : le critere de bloc pauvre et le
    departage. Deux appels separes auraient fini par ne plus dire la meme chose
    du meme bloc — le piege deja paye deux fois par l'assembleur de contexte.
    """
    density = context_density(labels, event.sport_key, settings)
    if not density.expected:
        return None
    return round(100 * density.filled / density.expected)


def _gap_of(event: RenderableEvent, settings: Settings) -> int | None:
    """Ecart au cumul de la manche aller, ou None s'il n'y en a pas."""
    if not event.event_id:
        return None
    etat = context_service.tie_state(event.event_id, _when(event), settings)
    return None if etat is None else etat.gap


def _density_reasons(event: RenderableEvent, labels: list[str], settings: Settings) -> list[Reason]:
    """Un bloc pauvre est un trou que la recherche comble — sauf s'il est vide.

    Distinction mesuree : a 10 lignes sur 24, la recherche a de quoi travailler ;
    a 2 sur 24 sur une competition que le fournisseur ne couvre pas, elle n'a
    rien trouve non plus, et le savoir d'avance epargne une requete.
    """
    part = _density_of(event, labels, settings)
    if part is None:
        return []
    # **Un identifiant absent n'est pas une source absente.** Sans evenement en
    # base on ne peut pas savoir si la competition est rattachee, et l'ecarter
    # au benefice du doute reviendrait a punir un match de ce qu'on ignore de
    # lui — l'inverse de ce que cette fiche existe pour faire.
    sterile = event.event_id and not _has_context_source(event, settings)
    cause = _cause_of(event, settings)
    # **Deux blocs vides ne valent pas le meme budget, et c'est tout l'objet du
    # typage.** Un bloc vide parce que personne n'a pose la question ne se
    # comble pas par une recherche : il se comble par une saisie, et le dossier
    # coute alors une place a un match ou chercher sert. Un bloc vide parce que
    # le fournisseur ne couvre pas la competition est l'inverse — la recherche
    # y est le **seul** chemin, donc le meilleur dossier du lot.
    if cause in COLLECTION_FAULTS:
        return [Reason(PENALTY, f"bloc à {part} % — {CAUSE_LABELS[cause]}", "")]
    if part < THIN_DENSITY and cause == CAUSE_NOT_COVERED:
        return [
            Reason(
                STRONG,
                f"bloc à {part} %, {CAUSE_LABELS[cause]}",
                f"Bloc a {part} % et competition non couverte par le fournisseur : "
                "chercher un compte rendu du dernier match des deux equipes, et la "
                "composition probable — rien d'autre ne les servira.",
            )
        ]
    if part == 0 and event.sport_key == "football" and not cause:
        # **Apres le typage, un bloc vide sans motif ne devrait plus exister.**
        # Les quatre causes couvrent le football entier : s'il en reste un, c'est
        # un cinquieme cas que personne n'a nomme, et le ranger par defaut dans
        # l'une des quatre cases lui donnerait un budget decide au hasard.
        #
        # Traite en dossier fort — on ne sait pas pourquoi il est vide, donc on
        # ne peut pas affirmer qu'une recherche n'y servirait a rien — et
        # journalise, parce que c'est le typage qu'il faut reprendre, pas la
        # fiche.
        logger.warning(
            "Bloc a 0 %% sans cause typee sur l'evenement %s (%s) : cinquieme cas, "
            "le typage du contexte ne le couvre pas",
            event.event_id,
            event.home,
        )
        return [
            Reason(
                STRONG,
                "bloc vide, cause inconnue",
                "Bloc entierement vide et l'application ne sait pas pourquoi : "
                "chercher un compte rendu du dernier match des deux equipes, et "
                "verifier a la main que la rencontre a bien lieu.",
            )
        ]
    if part < BARREN_DENSITY and sterile:
        return [Reason(PENALTY, f"bloc quasi vide ({part} %) et aucune source", "")]
    if part < THIN_DENSITY:
        # **La question nomme les lignes manquantes**, parce que l'application
        # les connait. « Ce que le bloc ne porte pas » etait un doublon mou de
        # la question precedente et ne disait pas ou aller ; deux questions
        # precises valent mieux que trois dont une est du remplissage — la meme
        # regle que « ne remplis jamais un palier avec du vide ».
        #
        # **« Source injoignable » passe ici, au budget ordinaire**, et c'est
        # mesure : rien ne rejoue le contexte tout seul. Le planificateur ne
        # porte que le scan, les sources gratuites et un balayage de
        # compositions — lequel exige un `apifootball_fixture_id`, donc ne peut
        # pas reparer le cas ou le rapprochement a echoue. Le motif dit pourquoi
        # le bloc est vide, pas qu'il sera rempli a temps : le coup d'envoi ne
        # recule pas parce qu'un enrichissement rejouera demain.
        manquantes = _missing(labels, event.sport_key)
        motif = f"bloc pauvre ({part} %)"
        if cause:
            motif += f", {CAUSE_LABELS[cause]}"
        return [
            Reason(
                MEDIUM,
                motif,
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


#: La question que le detail d'un tournoi en cours appelle. **Ecrite une fois**,
#: parce que trois etats du bloc y menent — aucun score du tournoi, un historique
#: en retard, un profil de service sous son seuil — et que trois formulations
#: voisines se liraient comme trois recherches quand c'est la meme.
TOURNAMENT_DETAIL = "Score set par set, duree et statistiques de service des tours deja joues ici ?"


def _tennis_reasons(
    event: RenderableEvent, lignes: dict[str, str], settings: Settings
) -> list[Reason]:
    """Ce qu'une journee de tennis laisse en blanc, et que la recherche comble.

    **Trois etats du bloc menent a la meme question, et un seul poids serait
    faux.** Nos sources ne portent aucun detail de match pour le tournoi en
    cours : le fichier hebdomadaire parait apres coup, et la ligne `Ici` ne
    couvre que ce que nos propres scans ont vu. Un bloc qui n'en porte **rien**
    n'est pas dans le meme etat qu'un bloc dont l'historique accuse quelques
    jours de retard.

    Mesure sur les 48 blocs rendus depuis le 20/08/2026 : `Ici` absente sur
    **10 blocs (21 %)**, `Service` absente sur 10 aussi. La question ne se
    duplique pas — `Dossier.questions` la rend une fois — mais le poids, lui,
    ordonne.
    """
    if event.sport_key != "tennis":
        return []
    reasons = []
    # **Une absence ne se reclame que sur un bloc par ailleurs servi.** Sans
    # cette garde, un bloc entierement vide produirait trois criteres pour une
    # seule cause — et `Densite` la nomme deja. Meme regle que `Stats match`, qui
    # rend une ligne pour trois absences plutot que trois. `Forme` est le marqueur
    # : les 48 blocs de tennis rendus depuis le 20/08/2026 la portent tous.
    servi = bool(lignes.get("Forme"))
    if servi and not (lignes.get("Ici") or ""):
        reasons.append(Reason(STRONG, "aucun score de ce tournoi dans le bloc", TOURNAMENT_DETAIL))
    elif "non comptes" in (lignes.get("Fraicheur") or ""):
        reasons.append(Reason(MEDIUM, "tours de ce tournoi non recenses", TOURNAMENT_DETAIL))
    if servi and not (lignes.get("Service") or ""):
        reasons.append(Reason(MEDIUM, "aucun profil de service", TOURNAMENT_DETAIL))
    reasons += _rest_reasons(lignes)
    reasons += _uncontested_reasons(lignes)
    reasons += _draw_status_reasons(event, settings)
    reasons += _thin_player_reasons(lignes)
    return reasons


def _rest_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Un joueur qui a rejoue en moins d'un jour, et ce que `Repos` ne dit pas.

    **La ligne porte l'ecart, jamais ce qu'il a coute.** Son propre mode d'emploi
    interdit de comparer deux ecarts a l'heure pres : elle part du **coup
    d'envoi** du match precedent, aucune source lisible ne publiant sa duree, si
    bien que celui qui a joue 2h33 et celui qui est passe en 1h05 portent la meme
    mention. C'est exactement ce qu'une recherche rapporte en une requete.

    Le **double** est dans la meme question, et pour la meme raison : le
    fournisseur de cotes ne le sert pas, donc un joueur peut porter ce `Repos` et
    une charge tout autre — releve en reel, 10 des 16 joueuses d'une journee WTA
    avaient joue le double la veille.

    **Le seuil se lit sur tous les joueurs de la ligne**, pas sur le premier :
    c'est par la que sa premiere mesure etait fausse.
    """
    valeur = lignes.get("Repos") or ""
    heures = [int(found) for found in _REST_HOURS.findall(valeur)]
    if not heures or min(heures) >= SHORT_REST_HOURS:
        return []
    return [
        Reason(
            MEDIUM,
            f"a rejoue en {min(heures)} h",
            "Duree du match precedent, session de jour ou de nuit, et double engage sur place ?",
        )
    ]


def _uncontested_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Un tour passe sans jouer, et depuis quand le joueur n'a pas disputé de match.

    La ligne `Non joue` dit le fait — forfait adverse, adversaire remplace — et
    s'arrete la, parce que c'est tout ce que nos scans etablissent. Ce qu'elle ne
    peut pas dire est ce que la recherche rapporte : un joueur offert d'un tour
    est frais, un joueur qui n'a plus joue depuis dix jours ne l'est pas.
    """
    if not (lignes.get("Non joue") or ""):
        return []
    return [
        Reason(
            MEDIUM,
            "un tour non dispute dans le parcours",
            "Dernier match reellement dispute, et etat physique depuis ?",
        )
    ]


def _draw_status_reasons(event: RenderableEvent, settings: Settings) -> list[Reason]:
    """Un joueur arrive par les qualifications, et le bloc ne le dit pas.

    **Derive sans un appel, des que `phase_de` est pose** : les rencontres de
    qualification entrent sous leur propre competition, declaree phase du tableau
    principal, et un joueur qui y figure **est** un qualifie — pas par inference,
    par definition. C'est la seule des quatre rubriques « statut dans le
    tableau » que l'application puisse etablir ; les trois autres — tete de
    serie, wild card, lucky loser — sont la question emise.

    **La part attendue est structurelle** : un tableau de Grand Chelem compte
    16 qualifies sur 128, soit 12,5 % du champ, et la proportion decroit ensuite.
    Elle n'a pas pu etre mesuree sur les prompts archives, aucun tableau
    principal n'etant encore entre — le rattachement date du 27/08/2026 — et
    c'est le premier scan de tableau principal qui la relevera.
    """
    if event.sport_key != "tennis" or not event.event_id:
        return []
    with connect(settings) as conn:
        # Le rapprochement des noms passe par `sort_key` — casse et accents
        # ignores, **rien de flou** : deux joueurs differents ne doivent jamais
        # partager un statut. Meme regle que `tennis_load`.
        rows = conn.execute(
            "SELECT q.home, q.away FROM events q "
            "JOIN competitions phase ON phase.id = q.competition_id "
            "JOIN events courant ON courant.id = ? "
            "WHERE phase.phase_de = courant.competition_id",
            (event.event_id,),
        ).fetchall()
    noms = {sort_key(row[cote]) for row in rows for cote in ("home", "away")}
    qualifies = [nom for nom in (event.home, event.away) if sort_key(nom) in noms]
    if not qualifies:
        return []
    return [
        Reason(
            MEDIUM,
            f"{' et '.join(qualifies)} passe(s) par les qualifications",
            "Statut de chaque joueur dans le tableau : tete de serie, wild card, lucky loser ?",
        )
    ]


def _thin_player_reasons(lignes: dict[str, str]) -> list[Reason]:
    """Un joueur dont **toutes** les lignes de forme tiennent sur deux matchs.

    Le critere de densite regarde le taux de remplissage du **bloc** ; celui-ci
    regarde ce qu'il y a derriere les lignes d'un **joueur**. Les deux ne se
    recouvrent pas : sur la soiree du 12/08, les deux blocs les plus vides du
    lot au niveau joueur — `Forme D/1` pour Lajal, `Forme VD/2` pour Mejia, ni
    Profil ni Marge — avaient un bloc complet par ailleurs, donc aucun critere
    ne les designait. La fiche a propose six dossiers portant tous la meme
    question et aucun sur les deux joueurs dont on ne savait rien.

    **Seuil mesure avant d'etre ecrit**, et c'est ce que la regle de revue
    demande : sur les 406 blocs de tennis archives, « moins de trois matchs »
    designe **5 blocs, soit 1 %** — les cinq de cette soiree-la. Un critere qui
    se declencherait partout ne classerait plus rien ; a 1 %, il designe une
    minorite stricte. Les seuils voisins ont ete mesures avec : 2 blocs a moins
    de deux matchs, 9 a moins de quatre, 15 a moins de cinq.
    """
    forme = lignes.get("Forme") or ""
    maigres = [
        (nom.strip(), int(compte))
        for fragment in forme.split(" | ")
        if (paire := re.match(r"^(.*?)\s+[VD]+/(\d+)$", fragment.strip()))
        for nom, compte in [paire.groups()]
        if int(compte) < THIN_PLAYER_MATCHES
    ]
    return [
        Reason(
            MEDIUM,
            f"{nom} : {compte} match(s) derriere ses lignes",
            f"Resultats et etat de {nom} depuis son dernier match connu :"
            f" le bloc ne porte que {compte} match(s).",
        )
        for nom, compte in maigres
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


def _cause_of(event: RenderableEvent, settings: Settings) -> str:
    """Pourquoi ce bloc est vide, quand une cause a pu etre nommee.

    **La meme resolution que le bloc et que l'ecran**, appelee ici pour trier :
    trois lectures paralleles de la meme question auraient fini par classer un
    match sur un motif que le bloc ne porte pas.
    """
    if not event.event_id:
        return ""
    return context_service.failure_causes([event.event_id], settings).get(event.event_id, "")


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
