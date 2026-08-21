"""Historique des sessions, saisie des picks joues et taux de reussite.

**Aucun calcul financier ici** (SPEC.md section 9) : ni ROI, ni value, ni CLV, ni
esperance. La mise est enregistree parce qu'elle fait partie du souvenir de ce
qui a ete joue, mais elle n'est jamais agregee ni transformee en indicateur.

Le seul indicateur produit est un taux de reussite : gagnes / (gagnes + perdus).
Les paris annules et ceux encore en attente sont exclus du denominateur.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, execute, query, query_one, utcnow
from .competitions import category_label, category_rank
from .confidence import (
    OPEN_ABSENT,
    OPEN_EMPTY,
    OPEN_MALFORMED,
    OPEN_READ,
    OVERRIDE_CAUSES,
    OVERRIDE_INCONNUE,
    OVERRIDE_SANS_FAIT,
    Claim,
    ClaimError,
    is_collection_fault,
    is_unknown_cause,
)
from .confidence import parse as parse_claim
from .inference import (
    ALPHA,
    Equivalence,
    Evidence,
    Residual,
    benjamini_hochberg,
    clustered_p_value,
    evidence,
    jaccard,
    omnibus,
    ordinal_trend,
    required_sample,
    residual_horizon,
    two_proportions,
    wilson,
)
from .ingestion import CONF, EXPLORATOIRE, SELECTION, is_payload
from .labels import affiche, sort_key
from .market_families import family_key, family_label, family_of, family_rank, market_key_for
from .market_families import load as load_families
from .market_families import market_key as _market_key
from .thresholds import COUPON_TRACKING, toggle_of
from .thresholds import value_of as threshold_value
from .write_paths import writes

logger = logging.getLogger(__name__)

#: Les quatre issues de la ligne `dossiers_ouverts`, telles que la session les
#: garde. Une valeur hors de cet ensemble vaut « on ne sait pas » : le vocabulaire
#: est celui du lecteur, ecrit une fois, et la base ne doit pas porter un
#: cinquieme etat qu'aucun code ne sait produire.
#:
#: `vide` s'y est ajoute apres coup, et son absence etait le defaut : une ligne
#: lue sans repere retombait sur `lue`, donc se lisait comme une liste
#: renseignee. **Toute valeur produite par `read_opened` doit figurer ici** —
#: sinon elle est ecrite NULL, et l'etat disparait sans que rien ne le dise.
OPEN_STATES = (OPEN_READ, OPEN_EMPTY, OPEN_ABSENT, OPEN_MALFORMED)


RESULTS = ("pending", "win", "loss", "void")
RESULT_LABELS = {
    "pending": "en attente",
    "win": "gagné",
    "loss": "perdu",
    "void": "annulé",
}
NO_SPORT = "—"

#: Nature de l'angle qui porte la selection. **C'est ce mot qui choisit le
#: marche** : un raisonnement sur un rythme, une usure, un desequilibre, qui
#: finit sur un nom de camp, a perdu en route ce qu'il avait compris.
#:
#: Sans accent dans la cle, comme partout : elle voyage dans un formulaire, une
#: URL et une colonne SQLite. Le libelle, lui, s'ecrit correctement.
ANGLES = {
    "issue": "Issue",
    "maniere": "Manière",
}

#: La cle de l'angle qui decrit une **maniere**. Nommee plutot que recopiee :
#: c'est elle que le detecteur de conflit compare a la famille du marche rendu.
ANGLE_MANNER = "maniere"

#: D'ou vient la cote **recopiee du bloc**. Le palier est une bande de cote, et
#: il « sert a calculer un taux de reussite par bande de cote dans le temps » :
#: un 1.92 Pinnacle et un 1.92 Betclic ne decrivent pas le meme marche, et pres
#: des bornes l'ecart de marge fait basculer de palier. Sans cette colonne, la
#: serie longue melangeait deux populations — au tennis, **toute** selection de
#: maniere est enregistree a un prix de reference, le book principal n'y servant
#: que le vainqueur.
#:
#: `NULL` veut dire « on ne sait pas » : les selections anterieures a la colonne
#: n'ont pas ete devinees apres coup, un rapprochement de libelles fait des mois
#: plus tard se trompant sans qu'on puisse dire combien de fois.
PRICE_SOURCES = {
    "betclic": "Bookmaker principal",
    "reference": "Book de référence",
    "manuelle": "Saisie manuelle",
}

#: La seule source dont le prix n'est **pas** celui qu'on obtiendra. C'est elle
#: qui met une selection en quarantaine tant que sa cote reelle manque.
PRICE_REFERENCE = "reference"

#: D'ou vient la prose de la section C — l'argument en une ligne, et la
#: condition d'invalidation.
#:
#: **`reconstruit` n'est pas `import` en moins fiable, c'est autre chose.** Une
#: captation recopie une cellule que le lecteur avait sous les yeux ; une
#: reprise rapproche une selection deja en base d'une ligne de tableau, par une
#: regle qui peut apparier de travers. Sur les 76 lignes reprises le 21/08/2026
#: l'appariement portait sur `(session, sélection)` et une ligne sur 77 n'a pas
#: trouve sa jumelle — c'est ce taux-la que la colonne rend relisible.
#:
#: `NULL` veut dire que les deux colonnes sont vides, donc qu'il n'y a rien a
#: situer. Meme regle que `price_source` : ce qui a ete deduit se declare, et
#: ce qui n'existe pas ne se declare pas.
PROSE_FROM_IMPORT = "import"
PROSE_REBUILT = "reconstruit"
PROSE_SOURCES = {
    PROSE_FROM_IMPORT: "Captée au collage",
    PROSE_REBUILT: "Reconstruite depuis le collage archivé",
}

#: Niveau de la source qui porte le fait principal, sur l'echelle a quatre crans
#: du preambule. `lecture` n'est pas une absence de valeur mais une valeur de
#: l'echelle : l'analyse declare qu'aucun fait date ne porte la selection. La
#: distinguer de « non renseigne » est tout l'objet de la mesure — c'est
#: precisement la comparaison que le regroupement doit permettre.
SOURCE_LEVELS = {
    "1": "1 · officiel",
    "2": "2 · presse",
    "3": "3 · statistiques",
    "4": "4 · agrégateurs",
    "lecture": "Lecture seule",
}
#: Titre du bloc des selections qu'aucun match ne porte. Elles existent — un
#: pari sur un vainqueur de tournoi, une ligne dont le rapprochement a echoue —
#: et les taire les rendrait introuvables.
NO_COMPETITION = "Hors compétition"

#: Motifs d'une selection posee **apres** le coup d'envoi de son match.
#:
#: **Deux valeurs, et pas de texte libre.** Les deux cas legitimes ne se
#: ressemblent pas, et les confondre est ce qui a rendu inexploitables les 37
#: selections tardives de la base : une decision prise a temps mais saisie tard
#: porte une etiquette **valide** et un prix douteux ; un pari reellement pris
#: en cours de match porte les deux comme invalides. Un troisieme choix, ou un
#: champ libre, ferait retomber dans le melange que cette colonne defait.
#: Le niveau de source d'une selection ecrasee, et le cran qui va avec. Ecrits
#: une fois : c'est la meme regle que celle du preambule — `lecture` impose la
#: confiance 1 — et la recopier ailleurs l'aurait fait diverger.
READING_LEVEL = "lecture"
FORCED_RUNG = 1

LATE_REASONS = {
    "differee": "Saisie différée — décision prise avant le coup d'envoi",
    "live": "Live assumé — pari pris en cours de match",
}


class HistoryError(ValueError):
    """Saisie de pick invalide. Le message est affiche tel quel."""


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SessionSummary:
    """Une session passee, vue de l'historique."""

    session_id: int
    label: str
    created_local: datetime
    events: int = 0
    prompts: int = 0
    picks: int = 0


@dataclass
class Pick:
    """Un pick joue, saisi a posteriori."""

    pick_id: int
    session_id: int
    event_id: int | None
    event_label: str
    tier: str
    tier_label: str
    market: str
    selection: str
    price: float | None
    confidence: int | None
    #: Vrai uniquement si le pick est rattache a un coupon : « joue » veut dire
    #: pose chez le bookmaker, pas propose par l'analyse.
    played: bool
    stake: float | None
    result: str
    #: Renseignes depuis la phase des coupons ; vides sur les lectures qui n'en
    #: ont pas l'usage, ce qui evite une jointure a chaque affichage de pick.
    sport_label: str = ""
    coupon_id: int | None = None
    #: Justification d'independance d'une seconde selection sur le meme match.
    #: **Rendue sur la feuille de session**, sans quoi elle serait ecrite et
    #: jamais relue — le sort exact reserve a `/players/squads`, retire faute de
    #: lecteur. C'est en la relisant qu'on voit si deux angles etaient vraiment
    #: independants ou deux facons de dire la meme chose.
    independence_note: str = ""
    #: Motif d'une selection posee **apres** le coup d'envoi. Rendu sur la
    #: feuille de session pour la meme raison que la note d'independance : une
    #: donnee que rien ne lit finit par se retirer, et celle-ci decide de la
    #: lecture du prix — `differee` laisse l'etiquette valide et le prix
    #: douteux, `live` invalide les deux.
    late_reason: str = ""
    #: Le cran **calcule** par l'application, a cote de `confidence` qui reste le
    #: cran **annonce**. Les deux se gardent : leur ecart est la seule mesure
    #: possible de savoir si le modele notait au hasard, et c'est la question qui
    #: a fait naitre le calcul. `None` quand le bloc structure manque ou ne se
    #: lit pas — jamais un repli sur l'annonce.
    confidence_computed: int | None = None
    distinct_publishers: int | None = None
    #: D'ou vient la cote recopiee, ce qu'on a reellement obtenu, et le palier
    #: recalcule dessus. `tier` reste le palier **provisoire** : il vaut tant
    #: qu'aucun prix n'a ete releve, ce qui est le cas ordinaire.
    price_source: str = ""
    price_real: float | None = None
    tier_real: str = ""
    #: La prose du tableau de la section C, et d'ou elle vient. Rendues sur la
    #: feuille de session pour la meme raison que la note d'independance : une
    #: donnee que rien ne lit finit par se retirer — c'est le sort exact de
    #: l'effectif collecte des mois sans lecteur, retire par la migration 022.
    #:
    #: `invalidation` est en outre ce qui rend le **controle 7** relisible : la
    #: condition a ete ecrite avant le coup d'envoi, donc la relire apres le
    #: resultat ne peut pas la contaminer. C'est la seule colonne de ce
    #: chantier dont un bilan pourra se servir sans precaution de date.
    angle_note: str = ""
    invalidation: str = ""
    prose_source: str = ""
    #: Renseignes par `list_picks`, qui range les selections par competition.
    competition: str = ""
    sport_order: int = 99
    commence_local: datetime | None = None
    #: Cle de marche **figee a l'ecriture**, et la cle du sport qui permet de la
    #: resoudre a la lecture quand elle manque. Voir `market_key_effective`.
    market_key: str = ""
    sport_key: str = ""

    #: Horodatages bruts, en ISO 8601 UTC. Compares tels quels : le format est
    #: le meme partout dans la base, donc l'ordre lexicographique est l'ordre
    #: chronologique — et deux `datetime` construits sur deux fuseaux se
    #: compareraient mal.
    created_at: str = ""
    commence_time: str = ""

    @property
    def late_label(self) -> str:
        """Le motif d'une saisie tardive, en toutes lettres. Vide sans motif.

        Une propriete plutot qu'un champ passe au gabarit : le vocabulaire vit
        dans `LATE_REASONS` et nulle part ailleurs — l'ecrire une seconde fois
        cote rendu l'aurait fait diverger au premier libelle ajuste, exactement
        le piege de la liste de marches de `markets.py`.
        """
        return LATE_REASONS.get(self.late_reason, "")

    @property
    def antecedence(self) -> bool:
        """Enregistree avant le coup d'envoi, donc a un prix d'avant-match.

        **Sens unique.** `created_at` est l'heure d'enregistrement dans
        l'application, pas celle de la decision : la base peut prouver
        l'anteriorite, jamais son absence. Le libelle dit donc « anteriorite non
        etablie » et jamais « enregistre apres coup », meme quand l'ecart atteint
        vingt-six heures.
        """
        return bool(self.commence_time) and self.created_at < self.commence_time

    @property
    def market_key_effective(self) -> str:
        """La cle de marche qui fait foi : celle qui a ete figee, sinon celle
        que le libelle designe aujourd'hui.

        **Deux traitements pour une meme grandeur, et les deux sont justes.**
        Une selection ecrite depuis la migration 033 porte sa cle : elle
        rattache la selection au releve de marche pris le meme jour, et ce lien
        doit survivre a un libelle renomme dans `render`. Une selection
        anterieure n'en a pas, et la resoudre a la lecture vaut mieux que de
        retro-remplir une colonne — meme regle que la famille d'un marche ou le
        niveau d'une competition : reclasser reclasse tout l'historique.

        Vide quand le libelle sort du vocabulaire du bloc. On ne devine pas.
        """
        if self.market_key:
            return self.market_key
        if not self.sport_key:
            return ""
        return market_key_for(self.sport_key, self.market) or ""

    @property
    def group(self) -> str:
        """Sport et competition, tels qu'ils titrent un bloc de la feuille."""
        if not self.competition:
            return NO_COMPETITION
        return f"{self.sport_label} · {self.competition}"

    @property
    def tier_effective(self) -> str:
        """Le palier qui fait foi : celui de la cote obtenue, sinon le provisoire."""
        return self.tier_real or self.tier

    @property
    def quarantined(self) -> bool:
        """Sa cote vient d'un book de reference et rien n'a ete releve depuis.

        Son palier est bati sur un prix qu'on n'aurait pas obtenu : il sort des
        taux **par bande de cote**, et de ceux-la seulement. La selection compte
        partout ailleurs — l'angle qui la portait ne devient pas faux parce que
        le prix reste a verifier.
        """
        return self.price_source == PRICE_REFERENCE and self.price_real is None

    @property
    def settled(self) -> bool:
        """Le resultat est connu. Un pari annule l'est : il n'y a plus rien a saisir."""
        return self.result in ("win", "loss", "void")

    @property
    def result_label(self) -> str:
        return RESULT_LABELS.get(self.result, self.result)


# Les fonctions d'inference vivent desormais dans `services/inference.py` :
# une couche **pure**, sans base ni reglage, testable contre des valeurs
# publiees. Elles decident de ce que la page affirme, et les garder au milieu
# de trois mille lignes de requetes les rendait invisibles.
#
# Reexportees ici parce que ce module reste leur seul appelant metier, et que
# les deplacer sous un autre nom aurait touche une dizaine de tests pour un
# gain nul.


@dataclass
class Comparison:
    """Deux regroupements que la page invite implicitement a comparer.

    Elle les pose cote a cote sans jamais dire si l'ecart tient : ce bloc
    repond en donnant le volume qui le rendrait testable.
    """

    left: RateRow
    right: RateRow
    required: int | None

    @property
    def observed(self) -> int:
        """Selections tranchees deja accumulees sur les deux groupes."""
        return self.left.settled + self.right.settled

    @property
    def units(self) -> int:
        """Evenements distincts derriere ces deux groupes.

        Compte sur l'union et non par addition : un match portant une selection
        de chaque groupe ne compte qu'une fois.
        """
        return len(self.left.events | self.right.events) + (
            self.left.unattached + self.right.unattached
        )

    @property
    def clustered(self) -> bool:
        return 0 < self.units < self.observed


# -- Seuils de lecture, communs aux deux surfaces ---------------------------
#
# Sous quel compte un taux ne veut plus rien dire est une propriete des
# **donnees**, pas de l'endroit qui les affiche : les seuils sont donc ecrits
# une seule fois. Les copier des deux cotes les aurait fait diverger, et la
# page aurait fini par publier ce que le prompt refuse.
#
# Ce qui differe, c'est la **reaction**, et les deux sont justes :
#   · le prompt se tait. Claude n'a aucun moyen de savoir qu'il lit une semaine
#     de paris plutot qu'un historique, et un chiffre faux oriente plus
#     surement que pas de chiffre du tout ;
#   · la page le dit. C'est la surface ou l'utilisateur vient regarder ses
#     propres donnees : les lui cacher repondrait a cote de la question posee.
#     La ligne reste affichee et porte sa faiblesse, le detail chiffre sous le
#     graphique restant complet en toutes circonstances.

#: Sous ce total, aucun taux n'est publie. Un 2/3 se lit « 67 % » et n'apprend
#: rien ; dire qu'il manque du recul est en revanche une information juste.
#:
#: Releve a 40 apres coup : a 17 selections tranchees, le bloc publiait un
#: 2/6 en ATP contre 5/7 en WTA qui ne reposait que sur treize matchs d'un
#: seul tournoi, joues la meme nuit. Ce n'est pas « je lis mieux la WTA »,
#: c'est « une soiree s'est mal passee », et le prompt le presentait comme un
#: ordre de passage. Un chiffre faux oriente plus surement que pas de chiffre.
#: **Defaut**, et non plus la valeur en vigueur : les deux nombres qui decident
#: si le bloc se transmet sont exactement ce que la table des seuils heberge —
#: « des nombres qui decident d'une regle sans etre une donnee ». Ils se reglent
#: donc dans l'ecran, et `value_of` rend celui-ci quand rien n'est saisi.
FEEDBACK_MIN_TOTAL = 40

#: Meme regle a l'echelle d'une ligne : un regroupement vu sept fois reste tu.
#:
#: **Seuil de lecture, et non de transmission.** Il decide de ce qu'une surface
#: affiche a cote de son effectif ; ce qui part dans le prompt est garde par
#: `FEEDBACK_MIN_CELL`, bien plus haut. A n = 8 l'intervalle de Wilson fait
#: +/- 30 points : rien n'est affirme, mais l'estimation ponctuelle ancre quand
#: meme la lecture, et le prompt ne montre pas d'intervalle.
FEEDBACK_MIN_ROWS = 8

#: Effectif d'une **cellule** sous lequel elle ne part pas dans le prompt.
#:
#: `FEEDBACK_MIN_TOTAL` garde le taux **global**, affiche avec son intervalle, et
#: il est calibre pour ca. Un taux par cran, par palier, par sport ou par marche
#: est un sous-ensemble de cette meme population : le garder au meme seuil
#: reviendrait a transmettre des cellules de cinq ou six lignes.
FEEDBACK_MIN_CELL = 100

#: Competitions distinctes sous lesquelles aucun detail n'est publie.
#:
#: **La troisieme moitie du garde-fou d'etalement.** Dix journees distinctes
#: visent a ce qu'un taux ne repose pas sur un seul contexte — mais dix soirees
#: de tennis satisfont le compteur sans satisfaire l'intention. La note de
#: `FEEDBACK_MIN_DAYS` disait qu'« une concentration ne se mesure pas par
#: competition », et c'etait juste **contre** le calendrier : les deux tableaux
#: d'un meme tournoi sont deux competitions et une seule semaine. Ce n'est pas un
#: remplacement, c'est une condition de plus — il faut les trois.
FEEDBACK_MIN_COMPETITIONS = 3

#: Journees d'analyse distinctes sous lesquelles aucun detail n'est publie.
#:
#: `FEEDBACK_MIN_TOTAL` garde le **volume**, ce garde-fou garde l'**etalement**,
#: et les deux ne se remplacent pas : 63 selections tranchees prises en quatre
#: jours restent une semaine de paris, pas un historique. Mesure sur les donnees
#: reelles — les 60 selections de la fenetre couvraient du 5 au 8 aout, un seul
#: tournoi de tennis en deux tableaux et une seule soiree de coupes d'Europe.
#: Le bloc annoncait alors « Masters 1000 13/30 » et « Tennis 13/30 » comme deux
#: observations independantes, la ou c'etaient les memes matchs sous deux noms,
#: et « Conference League 11 » sur une unique soiree.
#:
#: Une concentration ne se mesure pas par competition : les deux tableaux du
#: Canadian Open sont deux competitions distinctes en base, si bien qu'un compte
#: de competitions aurait declare l'echantillon varie. C'est le calendrier qui
#: la dit — dix journees ne tiennent pas dans une semaine de tournoi.
FEEDBACK_MIN_DAYS = 10

#: La page reprend les seuils du prompt, sans en inventer d'autres. Le meme
#: 46 % sur 35 selections d'un seul tournoi est trompeur des deux cotes ; seule
#: change la facon de le dire.
ANALYSIS_MIN_TOTAL = FEEDBACK_MIN_TOTAL
ANALYSIS_MIN_ROWS = FEEDBACK_MIN_ROWS
ANALYSIS_MIN_DAYS = FEEDBACK_MIN_DAYS


# -- Ce que vaut l'analyse --------------------------------------------------

#: Indice de Jaccard au-dela duquel deux regroupements de deux axes differents
#: decrivent **le meme echantillon sous deux noms**.
#:
#: Le Jaccard remplace une double inclusion — « partage plus de 95 % de chaque
#: cote » — qui disait la meme chose en deux conditions. Une seule grandeur,
#: symetrique par construction, et comparable d'une paire a l'autre.
COLLINEAR_SHARE = 0.90

#: En dessous du seuil fort, la borne d'un recouvrement **partiel**.
#:
#: **Il se compte, il ne s'enumere pas.** Trente avertissements de recouvrement
#: faible reproduiraient sous un autre nom le defaut que cette page a mis huit
#: lots a corriger : des signalements qui n'affirment rien, en nombre tel que
#: plus personne ne les lit. Ce qui se dit tient en deux faits — un
#: recouvrement total, et une association entre les deux etiquetages — et le
#: reste vit dans la matrice, sous le pli.
PARTIAL_SHARE = 0.60

#: Part du volume au-dela de laquelle une echelle se comporte comme si elle
#: comptait moins de niveaux qu'elle n'en a. Mesure qui l'a fixee : 95 des 96
#: selections portent une confiance 3 ou 4, et 89 des 96 un palier SAFE ou FUN.
CONCENTRATION_SHARE = 0.80

#: Nombre de niveaux sur lesquels cette part se mesure.
CONCENTRATION_LEVELS = 2

#: Les cinq niveaux de confiance, **tous rendus meme jamais employes**. C'est
#: precisement le niveau qui ne sert jamais qui decrit la facon d'etiqueter :
#: omettre une confiance 5 a zero ferait lire une echelle a quatre crans, et
#: l'echelle n'aurait plus l'air d'etre sous-employee.
CONFIDENCE_SCALE = (5, 4, 3, 2, 1)

#: Sous ce nombre de paris tranches, un marche n'est pas liste : la longue
#: traine des libelles vus une fois noierait les marches qui comptent. C'est le
#: seul cas ou la page ecarte vraiment une ligne, et il ne contredit pas la
#: regle ci-dessus : un libelle vu une fois n'est pas un taux fragile, c'est du
#: bruit d'orthographe. Le compte des ecartes est annonce (`hidden_markets`).
ANALYSIS_MIN_MARKET = 2


@dataclass
class Band:
    """Cible d'un niveau de confiance, en **ecart de points au taux global**.

    Elle etait un taux absolu — conf 5 >= 70 %, conf 4 entre 60 et 70 — et
    rapprochee des paliers, elle recouplait la confiance et la cote. Les bandes
    de cote traduites en taux d'equilibre le montrent : GIGA FUN va de 28 % a
    12,5 %, donc une selection a 4.00 qui gagne 30 % du temps est un bon pari et
    tire pourtant son cran quarante points sous une bande a 70 %. Pour tenir
    cette bande, **conf 5 devait devenir quasi exclusivement du SAFE** — et le
    mecanisme qui ordonne de resserrer un cran employe trop largement poussait
    alors toute selection a cote haute vers le bas de l'echelle.

    En relatif, ce qui se mesure est la **monotonie** de la notation : un cran
    superieur bat-il le cran inferieur ? La reponse ne depend plus du melange de
    paliers du mois.

    `reference` est le taux global des selections tranchees, **sur la meme
    fenetre que les taux compares**. Si les deux fenetres divergeaient, l'ecart
    ne voudrait rien dire.
    """

    level: int
    #: `None` des deux cotes = **ce cran n'a pas de cible**, et c'est un etat a
    #: part entiere. Une bande sert a declencher un mouvement — resserrer un cran
    #: employe trop largement, relacher un cran trop etroit — et les crans 1 et 2
    #: sont pines par la source : `lecture` impose 1, une source de niveau 3-4
    #: plafonne a 2. Aucun mouvement n'y est un choix, donc aucune cible n'y
    #: mesure quoi que ce soit ; l'afficher ajouterait du bruit a un bloc dont
    #: c'est justement le defaut a eviter.
    low: float | None = None
    high: float | None = None
    #: Taux global, en points, contre lequel les ecarts se resolvent. `None`
    #: quand rien n'est tranche : il n'y a alors aucune reference, donc aucune
    #: cible resoluble — et surtout pas une cible a zero.
    reference: float | None = None

    @property
    def targeted(self) -> bool:
        """Ce cran porte une cible. Faux quand les deux bornes sont vides."""
        return self.low is not None

    @property
    def resolved(self) -> bool:
        """La cible peut etre comparee a un taux : elle a une reference."""
        return self.targeted and self.reference is not None

    def _absolute(self, offset: float | None) -> float | None:
        """Un ecart ramene en taux, borne a [0, 100] — un taux n'en sort pas."""
        if offset is None or self.reference is None:
            return None
        return min(100.0, max(0.0, self.reference + offset))

    @property
    def low_absolute(self) -> float | None:
        return self._absolute(self.low)

    @property
    def high_absolute(self) -> float | None:
        return self._absolute(self.high)

    @property
    def offset_label(self) -> str:
        """« global +3 → +12 » — la cible telle qu'elle se **regle**.

        C'est la forme de l'ecran de configuration : on y saisit un ecart. Les
        surfaces de lecture, elles, montrent la valeur resolue — donner un ecart
        a comparer a un taux ferait refaire l'addition a chaque ligne.
        """
        if not self.targeted:
            return "pas de cible"
        assert self.low is not None
        if self.high is None:
            return f"global {self.low:+.0f} et au-dessus"
        return f"global {self.low:+.0f} → {self.high:+.0f}"

    @property
    def label(self) -> str:
        """La cible **resolue**, celle a laquelle un taux se compare.

        Vide tant qu'aucune reference n'existe : sans taux global, un ecart ne
        se ramene a rien, et afficher l'ecart brut ferait faire l'addition au
        lecteur — exactement ce que ce projet retire partout ailleurs.
        """
        if not self.resolved:
            return ""
        low, high = self.low_absolute, self.high_absolute
        assert low is not None
        return f"{low:.0f} – {high:.0f} %" if high is not None else f"{low:.0f} % et plus"

    def excludes(self, interval: tuple[float, float]) -> bool:
        """L'intervalle est **entierement** hors de la bande.

        Le chevauchement le plus tenu suffit a se taire : signaler des qu'un
        taux sort de sa bande ferait crier a la derive sur du bruit, et au
        volume actuel presque chaque intervalle couvre plusieurs bandes.

        Un cran sans cible — ou sans reference a laquelle la ramener — n'est
        jamais hors bande : il n'y a rien a en sortir.
        """
        if not self.resolved:
            return False
        borne_basse, borne_haute = self.low_absolute, self.high_absolute
        assert borne_basse is not None
        low, high = interval[0] * 100, interval[1] * 100
        if high < borne_basse:
            return True
        return borne_haute is not None and low > borne_haute


@dataclass
class RateRow:
    """Taux de reussite d'un regroupement. Aucune notion d'argent.

    Le taux implicite moyen (`1/cote`) n'en est pas un : c'est de
    l'arithmetique sur des cotes deja connues, sur des selections deja
    tranchees. Rien n'y est multiplie par une mise, et rien n'en sort qui
    ressemble a un gain ou a une esperance.
    """

    key: str
    label: str
    won: int = 0
    lost: int = 0
    #: Seuil de lecture historique, garde pour les **surfaces qui le lisent
    #: encore** — le prompt, et lui seul. La page ne s'en sert plus : un compte
    #: de paris ne dit pas si une ligne affirme quelque chose, c'est le test qui
    #: le dit, et la fragilite dit a quel point.
    minimum: int = ANALYSIS_MIN_ROWS
    void: int = 0
    pending: int = 0
    #: Identifiants des selections **tranchees** du regroupement. Sert a
    #: reconnaitre deux regroupements de deux axes differents qui decrivent le
    #: meme echantillon.
    #:
    #: Tranchees seulement, donc `len(members) == settled` : c'est le taux
    #: affiche qui est en cause dans un recouvrement, et lui ne se calcule que
    #: sur elles. En y comptant les paris en attente, la note annoncait « les
    #: memes 39 selections » a cote de barres portant 17/37 — un nombre que
    #: rien d'autre sur la page ne permettait de verifier.
    members: set[int] = field(default_factory=set)
    #: Matchs distincts portes par ces selections tranchees. Plusieurs lignes
    #: sur la meme rencontre — Vainqueur, handicap jeux et total de jeux — sont
    #: **une seule issue comptee trois fois** : le joueur qui gagne en deux sets
    #: les fait passer ensemble.
    events: set[int] = field(default_factory=set)
    #: Selections tranchees qu'aucun match ne porte. Comptees chacune pour une
    #: unite : rien ne permet de les rapprocher, donc rien ne permet de les
    #: declarer correlees.
    unattached: int = 0
    #: Bande cible, sur les seuls regroupements par confiance. Les autres axes
    #: n'en ont pas : un sport ne se fixe pas d'objectif de taux.
    band: Band | None = None
    #: Le **reste de l'axe** : (reussites, tranchees) de toutes les autres
    #: lignes. Rempli par `_with_complements` a l'assemblage, jamais a la main.
    #:
    #: C'est **lui** la reference d'une ligne, et non 50 %. Un taux de reussite
    #: de 50 % n'est un repere pour rien — sur un 1N2 la base tourne autour de
    #: 33 %, sur un handicap asiatique autour de 50 %, sur un total tout depend
    #: de la ligne — si bien que comparer chaque tranche a pile ou face testait
    #: une hypothese que personne n'avait formulee. La question actionnable est
    #: « cette tranche differe-t-elle de ce que je fais **par ailleurs** ».
    complement: tuple[int, int] = (0, 0)
    #: L'axe entier separe-t-il les resultats. **Un axe est une partition**, donc
    #: ses lignes sont un seul test ecrit N fois : tant qu'il ne passe pas,
    #: aucune de ses lignes ne se lit comme un constat.
    axis_separates: bool = False
    #: L'axe survit-il a la correction de multiplicite entre axes. Huit axes
    #: testes a 5 % laissent attendre une « decouverte » par pur hasard.
    axis_survives: bool = False
    #: Toutes les lignes de l'axe, `(reussites, tranchees)`. Sert a recalculer
    #: le verdict quand on retourne des resultats — la fragilite ci-dessous.
    axis_cells: list[tuple[int, int]] = field(default_factory=list)

    @property
    def carried(self) -> bool:
        """La ligne est-elle **portee** par la page, ou repliee avec les autres.

        Trois conditions, dans cet ordre : l'axe separe, il survit a la
        correction entre axes, et la ligne s'ecarte de son complement.
        **Jamais un intervalle de Wilson** — sur la population reelle il
        retenait deux lignes a `0/4` (p = 0,12) et une dont la borne franchissait
        le seuil de 0,011 point.
        """
        return self.axis_separates and self.axis_survives and self.evidence.discriminant

    @property
    def fragility(self) -> int | None:
        """Resultats a retourner **dans cette ligne** pour que le verdict tombe.

        **Bloquante pour toute ligne portee**, et c'est la lecon du bloc
        « SCORE EXACT 100 % sur 2 » : un chiffre sans son effectif se lit comme
        un fait. Ici l'effectif ne suffit pas — une ligne a 40 paris peut tenir
        a un seul resultat — donc c'est le nombre de bascules qui accompagne le
        verdict.

        Le calcul refait **les deux tests** : l'omnibus de l'axe et celui de la
        ligne. Ne verifier que le second surestimerait la solidite, l'axe
        pouvant ceder le premier.

        `None` quand la ligne n'est pas portee : il n'y a alors rien a faire
        tomber.
        """
        if not self.carried:
            return None
        other_won, other_settled = self.complement
        for flips in range(1, self.settled + 1):
            for won in (self.won - flips, self.won + flips):
                if not 0 <= won <= self.settled:
                    continue
                cells = [
                    (won, self.settled) if cell == (self.won, self.settled) else cell
                    for cell in self.axis_cells
                ]
                verdict = omnibus(cells)
                tombe = verdict is None or not verdict.separates
                if tombe or two_proportions(won, self.settled, other_won, other_settled) >= ALPHA:
                    return flips
        return None

    @property
    def evidence(self) -> Evidence:
        """Ce que cette ligne permet d'affirmer, contre le reste de son axe."""
        other_won, other_settled = self.complement
        return evidence(self.won, self.settled, other_won, other_settled)

    @property
    def discriminant(self) -> bool:
        """La ligne s'ecarte du reste de l'axe plus que le hasard ne l'explique.

        **Deux conditions, et l'ordre compte** : l'axe doit d'abord separer.
        Mesure de ce que la seconde ecarte — « 1re division — Europe » vaut
        `2/13` contre `28/54`, soit p = 0,028 prise seule, mais son axe vaut
        p = 0,083 ; la porter serait presenter comme un constat une ligne
        ressortie d'un axe qui ne dit rien.
        """
        return self.axis_separates and self.evidence.discriminant

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def total(self) -> int:
        return self.won + self.lost + self.void + self.pending

    @property
    def rate(self) -> float | None:
        """Taux de reussite, ou None tant que rien n'est tranche."""
        return None if self.settled == 0 else self.won / self.settled

    @property
    def rate_label(self) -> str:
        return "—" if self.rate is None else f"{self.rate * 100:.0f} %"

    def merge(self, other: RateRow) -> None:
        """Ajoute un regroupement a celui-ci, champ par champ.

        Ecrit une seule fois, et c'est ce qui manquait : chaque chantier a
        ajoute un champ a cette classe, et les deux fusions recopiees a la main
        ne les ont pas suivis. `Analysis.overall` annoncait « 0 événements
        distincts » sur trois selections tranchees — un total qui oubliait
        silencieusement tout ce qui avait ete ajoute apres lui.
        """
        self.won += other.won
        self.lost += other.lost
        self.void += other.void
        self.pending += other.pending
        self.unattached += other.unattached
        self.members |= other.members
        self.events |= other.events

    @property
    def units(self) -> int:
        """Evenements distincts derriere les selections tranchees.

        C'est l'**effectif independant** : trois lignes sur le meme match ne
        sont pas trois observations. Quand il est nettement inferieur a
        `settled`, l'intervalle de Wilson — qui suppose l'independance — est
        optimiste, et le vrai est plus large.
        """
        return len(self.events) + self.unattached

    @property
    def clustered(self) -> bool:
        """Des selections se partagent des matchs."""
        return 0 < self.units < self.settled

    @property
    def units_label(self) -> str:
        """« 30 événements », et rien quand chaque pari a le sien."""
        return "" if not self.clustered else f"{self.units} événement(s)"

    @property
    def interval(self) -> tuple[float, float] | None:
        """Intervalle de Wilson a 95 % du taux constate."""
        return wilson(self.won, self.settled)

    @property
    def interval_label(self) -> str:
        """« [47 – 76] », en points de pourcentage."""
        bounds = self.interval
        if bounds is None:
            return ""
        low, high = bounds
        return f"[{low * 100:.0f} – {high * 100:.0f}]"

    @property
    def off_band(self) -> bool:
        """Le taux est hors de sa bande cible, et l'intervalle le confirme.

        Le test porte sur l'**intervalle** et non sur le taux : un 44 % dont
        l'intervalle va de 31 a 57 traverse deux bandes, et le declarer hors de
        la sienne serait affirmer plus que les donnees ne portent.
        """
        bounds = self.interval
        return self.band is not None and bounds is not None and self.band.excludes(bounds)


@dataclass
class Stats:
    """Taux de reussite par palier et par sport."""

    by_tier: list[RateRow] = field(default_factory=list)
    by_sport: list[RateRow] = field(default_factory=list)
    overall: RateRow = field(default_factory=lambda: RateRow("all", "Tous"))
    #: Paris poses a un prix de **reference** dont la cote obtenue n'a pas ete
    #: relevee. Ils sortent des taux par bande de cote, jamais du reste.
    quarantined: int = 0

    @property
    def empty(self) -> bool:
        return self.overall.total == 0


# -- Lecture ----------------------------------------------------------------


def list_sessions(settings: Settings | None = None) -> list[SessionSummary]:
    """Sessions passees, les plus recentes d'abord."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT s.id, s.label, s.created_at, "
            "  (SELECT COUNT(*) FROM session_events se WHERE se.session_id = s.id) AS events, "
            "  (SELECT COUNT(*) FROM prompts p WHERE p.session_id = s.id) AS prompts, "
            "  (SELECT COUNT(*) FROM picks k WHERE k.session_id = s.id) AS picks "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC"
        ).fetchall()

    return [
        SessionSummary(
            session_id=int(row["id"]),
            label=row["label"] or f"Session {row['id']}",
            created_local=_local(row["created_at"], settings.tz),
            events=int(row["events"]),
            prompts=int(row["prompts"]),
            picks=int(row["picks"]),
        )
        for row in rows
    ]


#: Un en-tete de bloc de match dans un prompt archive : `### M12 · TENNIS · …`.
#: Sert **uniquement** a reconstruire le lot des sessions anterieures a la table
#: `prompt_events`. Ce que le motif capture est l'identite du match — sport,
#: competition, affiche, heure — et non le numero de bloc, qui change d'une
#: generation a l'autre pour un meme match.
_BLOCK_HEADER = re.compile(r"^### M\d+ · (.+)$", re.MULTILINE)

#: Le nombre de matchs qu'un payload declare. **Ecrit ici et non importe de
#: `prompt`** : ce module-la importe celui-ci, et l'inverse fermerait le cycle.
#: Un test compare les deux expressions plutot que de laisser deux ecritures
#: diverger en silence.
PAYLOAD_MATCHS = re.compile(r'"nb_matchs"\s*:\s*(\d+)')

#: Le meme en-tete, **avec** son repere. Le numero de bloc ne survit pas d'une
#: generation a l'autre — c'est pour ca que `_BLOCK_HEADER` le jette — mais il
#: est coherent a l'interieur d'un rendu, ce qui en fait une somme de controle
#: pour l'import : voir `picks_import._verified`.
_NUMBERED_HEADER = re.compile(r"^### (M\d+) · (.+)$", re.MULTILINE)


def set_open_dossiers(
    session_id: int,
    marks: frozenset[str] | set[str],
    settings: Settings | None = None,
    state: str = "",
) -> None:
    """Memorise les dossiers que le rendu declare avoir ouverts, et ce qui est
    arrive a la ligne.

    **La liste entiere, et pas seulement son effet ligne par ligne.** Un dossier
    ouvert qui n'a produit aucune selection ne laisse aucune trace dans `picks`,
    et c'est pourtant lui qui manque a la comparaison avec l'ordre de passage
    que l'application avait propose : un ecart systematique entre les deux dirait
    que le tri par « ce qu'une recherche peut y changer » ne sert a rien.

    `state` separe **deux defauts qu'un meme repli confondait** : une ligne omise
    et une ligne qu'on ne sait pas relire envoient toutes deux le lot en lecture,
    mais l'un se reprend dans le gabarit et l'autre dans le lecteur. Une valeur
    hors vocabulaire vaut « on ne sait pas » plutot qu'un refus — meme regle que
    l'angle et le niveau de source.

    Ecrase a chaque lecture : le dernier rendu colle decrit l'analyse en cours.
    """
    with connect(settings) as conn:
        conn.execute(
            "UPDATE sessions SET open_dossiers = ?, open_dossiers_state = ? WHERE id = ?",
            (
                " ".join(sorted(marks, key=lambda mark: int(mark[1:]))) or None,
                state if state in OPEN_STATES else None,
                session_id,
            ),
        )


#: L'ordre de passage propose par l'application, tel que le prompt le rend :
#: `1. M3 Lyon – Nice  [motifs]`. Il est **archive avec le corps**, donc relisable
#: sans regenerer la fiche — qui, recalculee aujourd'hui, ne donnerait plus le
#: meme classement qu'au moment de l'analyse.
_PRIORITY_LINE = re.compile(r"^\d+\.\s+(M\d+)\s", re.MULTILINE)


def prompt_priorities(settings: Settings | None = None) -> dict[int, set[str]]:
    """Les dossiers proposes par session, tous prompts confondus.

    L'union et non le dernier : un dossier propose puis sorti du classement par
    une regeneration a bien ete propose, et l'ecart qu'on mesure porte sur ce que
    l'analyse a **vu passer**.
    """
    found: dict[int, set[str]] = {}
    with connect(settings) as conn:
        for row in conn.execute("SELECT session_id, body FROM prompts"):
            found.setdefault(int(row["session_id"]), set()).update(
                _PRIORITY_LINE.findall(row["body"] or "")
            )
    return found


@dataclass(frozen=True)
class PromptBlocks:
    """Les reperes de blocs d'un prompt, **et son identifiant**.

    L'identifiant voyage avec les reperes plutot que d'etre relu a cote : c'est
    le prompt qui valide l'appariement des blocs de confiance qui donne aussi
    son `prompt_id` a un combine, et deux lectures paralleles de la meme chose
    auraient fini par designer deux prompts differents.
    """

    prompt_id: int
    marks: dict[str, str]


def prompt_headers(session_id: int, settings: Settings | None = None) -> list[PromptBlocks]:
    """Les en-tetes de blocs de chaque prompt de la session, du plus recent.

    Une liste **par prompt** et non un dictionnaire fusionne : `M8` designe deux
    matchs differents dans deux generations, et les melanger validerait un
    appariement qu'aucun rendu n'a jamais produit.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        bodies = conn.execute(
            "SELECT id, body FROM prompts WHERE session_id = ? ORDER BY id DESC", (session_id,)
        ).fetchall()
    return [
        PromptBlocks(int(row["id"]), dict(_NUMBERED_HEADER.findall(row["body"] or "")))
        for row in bodies
    ]


@dataclass
class Lot:
    """Les matchs soumis a l'analyse au cours d'une session.

    Ce n'est **pas** la shortlist : celle-ci decrit ou en est le board et se
    vide a mesure qu'on decoche. Mesure sur les donnees reelles — la session du
    09/08 porte 4 lignes de shortlist pour 29 selections, et son premier prompt
    en servait 12.

    C'est l'**union des matchs entres dans un prompt**. Compter des matchs et
    non des prompts est ce qui rend la grandeur juste : regenerer vingt fois le
    meme lot ne l'agrandit pas d'une ligne, il ne grossit que lorsqu'un match
    nouveau apparait — ce que le scan fait plusieurs fois par jour.
    """

    size: int
    #: Lot recalcule depuis les corps de prompts archives, faute d'avoir ete
    #: enregistre a la generation. Dit plutot que tu : c'est la meme grandeur,
    #: mais elle n'a pas la meme garantie — un match ne figure dans le corps
    #: que par son libelle, et deux rencontres homonymes le meme jour n'en
    #: feraient qu'une. Ce chemin s'eteint de lui-meme.
    reconstructed: bool = False


def lots(settings: Settings | None = None) -> dict[int, Lot]:
    """Taille du lot analyse, par session.

    Deux origines, et la seconde se retire d'elle-meme : ce que `prompt_events`
    a enregistre, et — pour les sessions anterieures a cette table — ce que les
    corps de prompts archives permettent de recompter. La reconstruction n'est
    pas une invention : l'information dormait deja en base, elle n'etait juste
    lue par personne.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        recorded = {
            int(row["session_id"]): int(row["lot"])
            for row in conn.execute(
                "SELECT p.session_id, COUNT(DISTINCT pe.event_id) AS lot "
                "FROM prompts p JOIN prompt_events pe ON pe.prompt_id = p.id "
                "GROUP BY p.session_id"
            )
        }
        # Les corps ne sont relus que pour les sessions qui n'ont rien
        # d'enregistre : sur une base entierement passee a `prompt_events`,
        # cette requete ne rend aucune ligne et rien n'est parcouru.
        archived = conn.execute(
            "SELECT p.session_id, p.body FROM prompts p "
            "WHERE p.session_id NOT IN ("
            "  SELECT DISTINCT session_id FROM prompts WHERE id IN ("
            "    SELECT prompt_id FROM prompt_events))"
        ).fetchall()

    found = {session_id: Lot(size=size) for session_id, size in recorded.items()}
    rebuilt: dict[int, set[str]] = {}
    for row in archived:
        corps = row["body"] or ""
        # **Un payload n'a pas d'en-tetes de bloc**, et les y chercher rendrait un
        # lot vide sans le dire — un « 0 sur 0 » indiscernable d'une session ou
        # rien n'a ete soumis. Le lot s'y lit sur ce qu'il declare.
        #
        # Le cas ne devrait pas se produire : `save_prompt` ecrit toujours
        # `prompt_events`, et cette reconstruction ne concerne que les sessions
        # qui n'en ont pas. Il est ferme quand meme, parce qu'un lot faux ici se
        # lit comme un taux de selection faux ailleurs.
        if is_payload(corps):
            declare = PAYLOAD_MATCHS.search(corps)
            rebuilt.setdefault(int(row["session_id"]), set()).update(
                f"payload:{row['session_id']}:{rang}"
                for rang in range(int(declare.group(1)) if declare else 0)
            )
            continue
        rebuilt.setdefault(int(row["session_id"]), set()).update(_BLOCK_HEADER.findall(corps))
    for session_id, matches in rebuilt.items():
        found[session_id] = Lot(size=len(matches), reconstructed=True)
    return found


def list_prompts(session_id: int, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Prompts generes pour une session, du plus recent au plus ancien."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, template_name, token_estimate, created_at FROM prompts "
            "WHERE session_id = ? ORDER BY created_at DESC, id DESC",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "template_name": row["template_name"],
            "token_estimate": row["token_estimate"],
            "created_local": _local(row["created_at"], settings.tz),
        }
        for row in rows
    ]


def _tier_labels(conn) -> dict[str, str]:
    return {
        row["key"]: f"{row['emoji']} {row['label']}"
        for row in conn.execute("SELECT key, label, emoji FROM tiers")
    }


def _pick(row: Any, tier_labels: dict[str, str], tz: str = "") -> Pick:
    # « combine » designe desormais un coupon a plusieurs jambes : laisser ce
    # mot ici ferait lire un pari la ou il n'y a qu'une selection dont le match
    # n'a pas ete rapproche.
    event_label = affiche(row["home"], row["away"]) if row["home"] else "— hors match —"
    return Pick(
        pick_id=int(row["id"]),
        session_id=int(row["session_id"]),
        event_id=row["event_id"],
        event_label=event_label,
        tier=row["tier"],
        tier_label=tier_labels.get(row["tier"], row["tier"]),
        market=row["market"],
        selection=row["selection"],
        price=row["price"],
        confidence=row["confidence"],
        played=bool(row["played"]),
        stake=row["stake"],
        result=row["result"] or "pending",
        coupon_id=row["coupon_id"],
        price_source=_column(row, "price_source") or "",
        price_real=_column(row, "price_real"),
        tier_real=_column(row, "tier_real") or "",
        independence_note=_column(row, "independence_note") or "",
        angle_note=_column(row, "angle_note") or "",
        invalidation=_column(row, "invalidation") or "",
        prose_source=_column(row, "prose_source") or "",
        late_reason=_column(row, "late_reason") or "",
        confidence_computed=_column(row, "confidence_computed"),
        distinct_publishers=_column(row, "distinct_publishers"),
        sport_label=_column(row, "sport_label") or "",
        market_key=_column(row, "market_key") or "",
        sport_key=_column(row, "sport_key") or "",
        created_at=str(_column(row, "created_at") or ""),
        commence_time=str(_column(row, "commence_time") or ""),
        competition=_column(row, "competition") or "",
        sport_order=int(_column(row, "sport_order") or 99),
        commence_local=(
            _local(row["commence_time"], tz)
            if tz and _column(row, "commence_time") is not None
            else None
        ),
    )


def _column(row: Any, name: str) -> Any:
    """Valeur d'une colonne optionnelle. Toutes les lectures ne joignent pas
    les memes tables : `sqlite3.Row` leve sur une colonne absente."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


#: Colonnes communes aux lectures de picks : le match, sa competition et son
#: sport. `sports.id` sert a ranger le football avant le tennis, comme partout
#: ailleurs dans l'application.
_PICK_JOIN = (
    "SELECT k.*, e.home, e.away, e.commence_time, s.label AS sport_label, "
    # La **cle** du sport et non son libelle : c'est elle qui choisit le
    # vocabulaire de marches, « Vainqueur » etant le `h2h` d'un match de tennis
    # et l'`outright` d'une etape de cyclisme.
    "       s.key AS sport_key, "
    "       s.id AS sport_order, c.label AS competition FROM picks k "
    "LEFT JOIN events e ON e.id = k.event_id "
    "LEFT JOIN sports s ON s.id = e.sport_id "
    "LEFT JOIN competitions c ON c.id = e.competition_id "
)


def list_picks(session_id: int, settings: Settings | None = None) -> list[Pick]:
    """Picks enregistres pour une session, dans l'ordre de saisie."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        labels = _tier_labels(conn)
        rows = conn.execute(
            _PICK_JOIN + "WHERE k.session_id = ? ORDER BY k.id", (session_id,)
        ).fetchall()
    return [_pick(row, labels, settings.tz) for row in rows]


def get_pick(pick_id: int, settings: Settings | None = None) -> Pick | None:
    """Un pick, ou None s'il n'existe pas."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        labels = _tier_labels(conn)
        row = conn.execute(_PICK_JOIN + "WHERE k.id = ?", (pick_id,)).fetchone()
    return _pick(row, labels, settings.tz) if row is not None else None


@dataclass
class Worksheet:
    """Les selections d'une session, rangees pour le travail de la journee.

    Deux blocs plutot qu'une liste : **ce qui reste a trancher** et **ce qui
    l'est deja**. Melanges, la liste ne dit pas ou en est la saisie, et il faut
    relire quinze lignes pour trouver les trois qui attendent encore un
    resultat. Chaque bloc est groupe par competition — c'est ainsi qu'on relit
    une journee, tournoi par tournoi, pas dans l'ordre ou Claude a rendu son
    tableau.
    """

    pending: list[tuple[str, list[Pick]]] = field(default_factory=list)
    settled: list[tuple[str, list[Pick]]] = field(default_factory=list)

    @property
    def picks(self) -> list[Pick]:
        return [pick for _, groupe in self.pending + self.settled for pick in groupe]

    @property
    def without_antecedence(self) -> int:
        """Selections de la session dont l'anteriorite n'est pas etablie.

        **Un compteur, pas un filtre**, et c'est toute la difference : un filtre
        dit ce qui a ete perdu, un compteur evite de le perdre. L'information
        n'existe qu'a la saisie et aucune migration ne la reconstruira — 36 %
        des selections tranchees de la base sont deja dans ce cas, pour toujours.
        """
        return sum(1 for pick in self.picks if not pick.antecedence)

    @property
    def without_real_price(self) -> int:
        """Selections sans cote obtenue. **La lacune de couverture reelle.**

        Le chiffre de tete de la page repose sur `price`, un nombre recopie a la
        main ; `price_real` est le seul controle possible, et il est renseigne
        sur 9 lignes sur 116 — toutes issues d'un book de reference. Le controle
        qui valide ou invalide le resultat principal du projet ne peut donc pas
        etre fait, et il ne le sera jamais retroactivement.

        Meme lecon que l'anteriorite, sur une autre colonne : chaque session qui
        passe sans elle est une session definitivement non verifiable.
        """
        return sum(1 for pick in self.picks if pick.price_real is None)

    #: Le suivi des paris poses est-il ouvert. Ferme, la cote obtenue cesse
    #: d'etre une lacune : elle ne peut venir que d'une mise, et il n'y en a pas.
    #: La reclamer serait demander une valeur qui n'existera jamais.
    coupon_tracking: bool = False

    @property
    def coverage_line(self) -> str:
        """« 3 sur 8 sans antériorité établie · 5 sur 8 sans cote obtenue ».

        Rien quand tout est couvert : un compteur a zero sur chaque session
        serait du bruit, et c'est le manque qui doit se voir.

        **La cote obtenue n'y figure que si le suivi des paris est ouvert.** Elle
        ne se releve jamais toute seule — ce serait une integration
        transactionnelle avec un bookmaker — donc elle ne peut venir que d'une
        mise. Sans mise, l'annoncer comme un manque reclamerait une valeur qui
        n'existera jamais, et le palier se lit desormais sur la cote du bloc.
        """
        total = self.total
        manques = [
            f"{self.without_antecedence} sur {total} sans antériorité établie"
            if self.without_antecedence
            else "",
            f"{self.without_real_price} sur {total} sans cote obtenue"
            if self.coupon_tracking and self.without_real_price
            else "",
        ]
        return " · ".join(part for part in manques if part)

    @property
    def pending_count(self) -> int:
        return sum(len(picks) for _, picks in self.pending)

    @property
    def settled_count(self) -> int:
        return sum(len(picks) for _, picks in self.settled)

    @property
    def total(self) -> int:
        return self.pending_count + self.settled_count

    @property
    def empty(self) -> bool:
        return self.total == 0


def _grouped(picks: list[Pick]) -> list[tuple[str, list[Pick]]]:
    """Groupe par competition, sport par sport puis par ordre alphabetique.

    Les selections sans match ferment la marche : elles n'appartiennent a aucun
    tournoi et les mettre en tete decalerait tout le reste.
    """
    ordered = sorted(
        picks,
        key=lambda pick: (
            pick.competition == "",
            pick.sport_order,
            sort_key(pick.competition),
            pick.commence_local or datetime.max.replace(tzinfo=UTC),
            pick.pick_id,
        ),
    )
    groups: dict[str, list[Pick]] = {}
    for pick in ordered:
        groups.setdefault(pick.group, []).append(pick)
    return list(groups.items())


def worksheet(session_id: int, settings: Settings | None = None) -> Worksheet:
    """Feuille de session : ce qui reste a trancher, puis ce qui l'est deja."""
    picks = list_picks(session_id, settings)
    return Worksheet(
        pending=_grouped([pick for pick in picks if not pick.settled]),
        settled=_grouped([pick for pick in picks if pick.settled]),
        coupon_tracking=toggle_of(COUPON_TRACKING, settings),
    )


# -- Matchs proposes au rattachement d'une selection ------------------------

#: Fenetre des matchs proposes autour d'une session, en heures. Une session est
#: le travail d'une journee : ses paris portent sur les matchs du jour et sur la
#: nuit qui suit. Au-dela, on proposerait le catalogue entier dans un menu.
PICKABLE_BEFORE_H = 24
PICKABLE_AFTER_H = 48

#: Resultats rendus par une recherche de match. Au-dela, le menu redevient
#: illisible — et une recherche qui ramene cinquante matchs demande surtout a
#: etre precisee.
SEARCH_LIMIT = 50


@dataclass
class PickableEvent:
    """Un match proposable au rattachement d'une selection."""

    event_id: int
    home: str
    away: str
    sport_label: str
    competition: str
    local_time: datetime
    #: Le match fait partie de la shortlist, donc de ce qui a ete analyse.
    in_session: bool = True
    #: Le coup d'envoi est passe. `GridRow` porte deja ce drapeau ; celui-ci
    #: l'aligne, pour que l'apercu d'import puisse decocher une ligne quelle que
    #: soit l'origine du match rapproche — shortlist ou voisinage.
    started: bool = False

    @property
    def affiche(self) -> str:
        return affiche(self.home, self.away)

    @property
    def group(self) -> str:
        """Libelle de l'`optgroup`. Sport et competition, toujours."""
        prefix = "" if self.in_session else "Hors sélection — "
        return f"{prefix}{self.sport_label} · {self.competition}"

    @property
    def label(self) -> str:
        """L'horaire d'abord, l'affiche ensuite — sur **tous** les matchs.

        Il n'accompagnait que les matchs hors shortlist. Mais une shortlist
        porte trente affiches reparties sur deux jours, et le rattachement se
        fait de memoire — « le match de 20h30 ». Sans l'heure, il fallait
        reconnaitre l'affiche pour retrouver le match, alors que c'est
        justement ce dont on n'est pas sur.
        """
        return f"{self.local_time:%d/%m %H:%M} · {self.affiche}"


def pickable_events(
    session_id: int,
    settings: Settings | None = None,
    query: str = "",
) -> list[PickableEvent]:
    """Matchs proposes au rattachement d'une selection, shortlist d'abord.

    La shortlist ne suffit pas. Un match qui a commence quitte le board : il ne
    peut plus y etre coche, donc il n'entre plus dans aucune shortlist — et la
    selection qui le vise restait « — hors match — » pour toujours. Sans
    evenement, elle n'a ni sport ni competition : elle disparait des
    statistiques par sport sans que rien ne le dise.

    Les matchs voisins de la session sont donc proposes aussi, marques comme
    tels : ils n'ont pas ete analyses, et l'utilisateur doit le voir.

    **`query` leve la fenetre de temps, et elle seule.** Le voisinage de
    `PICKABLE_BEFORE_H` / `PICKABLE_AFTER_H` couvre la journee de travail, pas
    un pari pose trois jours plus tot ni un match reporte. Quand le match
    cherche n'est nulle part dans le menu, il n'y avait plus aucun recours :
    la selection restait sans evenement, donc sans sport, donc muette dans les
    statistiques. Une recherche par libelle rouvre tout le catalogue, bornee a
    `SEARCH_LIMIT` — un menu de mille lignes ne se lit pas davantage qu'un menu
    vide.
    """
    settings = settings or get_settings()
    needle = query.strip()
    with connect(settings) as conn:
        session = conn.execute(
            "SELECT created_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return []
        created = datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        columns = (
            "SELECT e.id, e.home, e.away, e.commence_time, s.id AS sport_id, "
            "       s.label AS sport_label, "
            "       COALESCE(c.label, 'Saisie manuelle') AS competition, "
            "       EXISTS (SELECT 1 FROM session_events se "
            "               WHERE se.session_id = ? AND se.event_id = e.id) AS in_session "
            "FROM events e "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
        )
        if needle:
            like = f"%{needle}%"
            rows = conn.execute(
                columns + "WHERE e.home LIKE ? OR e.away LIKE ? OR c.label LIKE ? "
                "ORDER BY in_session DESC, e.commence_time DESC LIMIT ?",
                (session_id, like, like, like, SEARCH_LIMIT),
            ).fetchall()
        else:
            rows = conn.execute(
                columns + "WHERE (e.commence_time >= ? AND e.commence_time <= ?) "
                "   OR EXISTS (SELECT 1 FROM session_events se "
                "              WHERE se.session_id = ? AND se.event_id = e.id) "
                "ORDER BY in_session DESC, e.commence_time",
                (
                    session_id,
                    _iso(created - timedelta(hours=PICKABLE_BEFORE_H)),
                    _iso(created + timedelta(hours=PICKABLE_AFTER_H)),
                    session_id,
                ),
            ).fetchall()

    return [
        PickableEvent(
            event_id=int(row["id"]),
            home=row["home"],
            away=row["away"] or "",
            sport_label=row["sport_label"],
            competition=row["competition"],
            local_time=_local(row["commence_time"], settings.tz),
            in_session=bool(row["in_session"]),
            started=str(row["commence_time"]) <= utcnow(),
        )
        for row in rows
    ]


def pickable_groups(
    session_id: int, settings: Settings | None = None, query: str = ""
) -> list[tuple[str, list[PickableEvent]]]:
    """Les memes matchs, groupes par sport et competition pour un `optgroup`.

    Retrouver un match parmi cent : une liste a plat oblige a lire chaque
    ligne. Le groupement est fait ici et non dans le template — le filtre
    `groupby` de Jinja retrie par ordre alphabetique, ce qui remettrait les
    matchs hors shortlist devant ceux de la session.

    **Les groupes sont ranges par heure du premier match**, la shortlist
    d'abord. Ils l'etaient par identifiant de sport puis par nom de
    competition : « Bundesliga 2 » passait donc devant « Premier League » pour
    des raisons alphabetiques, et on cherchait le match de 20h30 en parcourant
    des competitions rangees dans un ordre qui ne dit rien de la soiree. Une
    session se relit dans l'ordre ou elle s'est jouee.
    """
    groups: dict[str, list[PickableEvent]] = {}
    for event in pickable_events(session_id, settings, query):
        groups.setdefault(event.group, []).append(event)
    return sorted(
        groups.items(),
        key=lambda item: (not item[1][0].in_session, min(e.local_time for e in item[1])),
    )


def tiers(settings: Settings | None = None) -> list[dict[str, str]]:
    """Paliers pour les formulaires. `emoji` et `name` restent separes du
    `label` complet : l'import des picks doit pouvoir reconnaitre l'un ou
    l'autre dans un tableau ecrit a la main."""
    with connect(settings) as conn:
        return [
            {
                "key": row["key"],
                "label": f"{row['emoji']} {row['label']}",
                "emoji": row["emoji"] or "",
                "name": row["label"],
            }
            for row in conn.execute("SELECT key, label, emoji FROM tiers ORDER BY position")
        ]


def _nearest_band(price: float, settings: Settings | None = None) -> str:
    """Le palier le plus proche d'une cote qui n'en atteint aucun, et sa borne.

    « Le plus proche » se mesure en **distance a la borne franchie**, jamais en
    ordre de position : les bandes se reglent, et rien n'empeche d'en laisser un
    trou au milieu. Un message qui nommerait le premier palier de la liste
    enverrait alors corriger la mauvaise borne.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT label, min_price, max_price FROM tiers ORDER BY position"
        ).fetchall()

    candidats: list[tuple[float, str, str]] = []
    for row in rows:
        minimum = float(row["min_price"])
        if price < minimum:
            candidats.append((minimum - price, row["label"], f"à partir de {minimum:.2f}"))
        # Une borne haute vide veut dire « pas de limite » : ce palier ne peut
        # rien avoir au-dessus de lui, et aucune cote ne le depasse.
        elif row["max_price"] is not None and price >= float(row["max_price"]):
            maximum = float(row["max_price"])
            candidats.append((price - maximum, row["label"], f"jusqu'à {maximum:.2f}"))
    if not candidats:
        return ""
    _, label, borne = min(candidats, key=lambda item: item[0])
    return f"le plus proche est {label}, {borne}"


def _reject_out_of_band(price: float | None, settings: Settings | None, champ: str) -> None:
    """Refuse une cote qu'aucune bande de palier ne couvre.

    **Le comportement d'avant n'etait ni un rejet, ni une exception, ni un
    palier nul visible** — c'etait pire : `add_pick` acceptait sans rien
    verifier, et `set_real_price` ecrivait `tier_real = NULL`, indiscernable de
    « jamais saisi ». La selection sortait alors de la quarantaine des cotes de
    reference comme si son prix avait ete releve, **et se rangeait dans le
    palier provisoire auquel sa cote n'appartient pas**. Un faux negatif
    silencieux, sur l'axe que le lot precedent venait de fiabiliser.

    Audit fait avant de corriger : **zero ligne concernee en base**, les cotes
    enregistrees allant de 1.25 a 3.50. Le defaut etait latent, donc rien a
    reparer — seulement a fermer.
    """
    if price is None or tier_for_price(price, settings) is not None:
        return
    proche = _nearest_band(price, settings)
    detail = f" — {proche}" if proche else ""
    raise HistoryError(f"« {champ} » : {price:.2f} ne tombe dans aucun palier{detail}.")


def in_band(price: float, minimum: float, maximum: float | None) -> bool:
    """La convention de borne du projet, ecrite **une seule fois**.

    **Borne basse incluse, borne haute exclue** : une cote de 1.80 est FUN et
    non SAFE, une cote de 8.00 est GIGA+ et non GIGA FUN. Une borne haute vide
    veut dire « pas de limite » — le dernier palier ne peut rien avoir au-dessus
    de lui.

    Elle etait ecrite **trois fois** : ici, dans `prompt.Tier.covers`, et une
    troisieme en clair au milieu de `tier_offers`, que rien ne comparait aux
    deux autres. Les trois s'accordaient, et c'est exactement ce qui rend la
    situation couteuse — un desaccord se serait vu, une convergence entretenue a
    la main tient jusqu'au jour ou elle cede sans bruit. `prompt.py` importe ce
    module, l'inverse ferait un cycle : c'est donc ici que la regle vit, et les
    deux autres l'appellent.

    Pure, sans base : c'est ce qui permet de la tester sur des bornes choisies
    plutot que sur celles du jour, et de l'appeler depuis une boucle SQL sans
    rouvrir une connexion par ligne.
    """
    if price < minimum:
        return False
    return maximum is None or price < maximum


def tier_for_price(price: float | None, settings: Settings | None = None) -> str | None:
    """Palier d'une cote, lu sur les bandes reglees.

    **La cote decide seule.** La confiance n'entre pas ici et ne doit pas y
    entrer : ce palier sert a calculer un taux de reussite par bande de cote, et
    deux selections au meme prix doivent tomber dans le meme palier. La
    confiance a son propre axe.
    """
    if price is None:
        return None
    with connect(settings) as conn:
        for row in conn.execute("SELECT key, min_price, max_price FROM tiers ORDER BY position"):
            haute = row["max_price"]
            if in_band(price, float(row["min_price"]), None if haute is None else float(haute)):
                return str(row["key"])
    return None


@dataclass(frozen=True)
class TierOffer:
    """Ce qu'un palier a **recu** d'une session, et ce qu'il en a produit.

    Trois causes rendaient un palier vide indiscernables, et elles n'appellent
    pas la meme conclusion :

    1. aucune cote du lot n'atteint la bande — le palier n'a **jamais ete
       propose**, et il n'y a rien a corriger ;
    2. des cotes existaient et aucune selection n'est sortie dessus — c'est la
       **methode** qui ne va pas les chercher ;
    3. une selection a ete produite puis ecartee par le quota — c'est le
       **reglage** qui mord.

    La page annoncait « 🔴 GIGA FUN 0 sur 12 sessions » sans dire laquelle des
    trois. Sans cette distinction, une regle assouplie ne se mesure pas : on ne
    saurait pas si elle a marche.

    **Rien n'est stocke.** Tout se relit dans `prompt_odds`, qui fige le marche
    de chaque session au moment ou le prompt part — c'est la verite historique,
    et une table de plus l'aurait doublee.
    """

    session_id: int
    tier: str
    label: str
    #: Au moins une cote du lot tombe dans la bande.
    offered: bool
    #: Selections effectivement produites dans ce palier, cette session-la.
    produced: int

    @property
    def unused(self) -> bool:
        """Propose et jamais employe. **La seule ligne actionnable du releve.**"""
        return self.offered and not self.produced


def tier_offers(settings: Settings | None = None) -> list[TierOffer]:
    """Par session et par palier : la bande etait-elle atteignable, l'a-t-on
    employee.

    Le marche se lit dans `prompt_odds` — fige a l'archivage du prompt, donc
    exactement ce que l'analyse avait sous les yeux. Le relire dans `odds`
    donnerait le marche **d'aujourd'hui**, qui n'a plus rien a voir : c'est
    justement pour ca que `prompt_odds` existe.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        bandes = [
            (str(row["key"]), f"{row['emoji'] or ''} {row['label']}".strip(), row)
            for row in conn.execute(
                "SELECT key, label, emoji, min_price, max_price FROM tiers ORDER BY position"
            )
        ]
        # `prompt_odds` porte deja `session_id` : un releve par session et par
        # match, remplace a chaque prompt. C'est bien le marche fige, pas celui
        # d'aujourd'hui — et c'est toute la raison d'etre de la table.
        prix = conn.execute(
            "SELECT session_id, price FROM prompt_odds WHERE price > 1.0"
        ).fetchall()
        produits = conn.execute(
            "SELECT session_id, tier, COUNT(*) AS n FROM picks GROUP BY session_id, tier"
        ).fetchall()
        # **Seules les sessions dont le marche a ete fige**, et c'est
        # indispensable : `prompt_odds` date de la migration 033, et les sessions
        # anterieures n'ont aucun releve. Les compter ferait lire « bande jamais
        # atteinte » sur neuf sessions dont on ne sait simplement rien — le
        # defaut exact que ce releve existe pour supprimer.
        sessions = [
            int(row["id"])
            for row in conn.execute(
                "SELECT DISTINCT session_id AS id FROM prompt_odds ORDER BY session_id"
            )
        ]

    offerts: dict[tuple[int, str], bool] = {}
    for row in prix:
        price = float(row["price"])
        for key, _, bande in bandes:
            haute = bande["max_price"]
            # La convention de borne se lit dans `in_band`, jamais recopiee ici :
            # cette boucle en portait sa propre ecriture, et c'est la seule des
            # trois qu'aucun test ne comparait aux autres.
            if in_band(price, float(bande["min_price"]), None if haute is None else float(haute)):
                offerts[(int(row["session_id"]), key)] = True
                break
    comptes = {(int(row["session_id"]), str(row["tier"])): int(row["n"]) for row in produits}

    return [
        TierOffer(
            session_id=session_id,
            tier=key,
            label=label,
            offered=offerts.get((session_id, key), False),
            produced=comptes.get((session_id, key), 0),
        )
        for session_id in sessions
        for key, label, _ in bandes
    ]


@dataclass(frozen=True)
class TierUsage:
    """Un palier, vu sur toutes les sessions : propose, employe, jamais employe."""

    tier: str
    label: str
    sessions: int
    offered: int
    used: int
    produced: int

    @property
    def unused(self) -> int:
        """Sessions ou la bande etait atteignable et n'a rien produit.

        **C'est le chiffre qui dira si une regle assouplie a marche** : il ne
        bouge pas quand le lot ne portait pas la cote, et il baisse quand la
        methode va la chercher.
        """
        return self.offered - self.used

    @property
    def never_offered(self) -> int:
        return self.sessions - self.offered


#: La migration qui a cree `prompt_odds`, donc **la borne du releve des
#: paliers** : les sessions anterieures n'ont aucun marche fige, et rien ne dira
#: jamais quelles bandes elles proposaient. La page le dit plutot que de laisser
#: croire a une couverture complete — c'est le meme defaut que partout ici, une
#: absence qui se lit comme une mesure.
TIER_OFFER_MIGRATION = 33


def tier_scope(settings: Settings | None = None) -> tuple[int, int]:
    """`(sessions couvertes, sessions au total)` du releve des paliers.

    L'ecart entre les deux est ce que la page doit annoncer : un releve sur six
    sessions sur quatorze n'est pas un releve sur quatorze, et le taire ferait
    lire « bande jamais atteinte » sur huit sessions dont on ne sait rien.
    """
    with connect(settings) as conn:
        couvertes = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS n FROM prompt_odds"
        ).fetchone()["n"]
        total = conn.execute("SELECT COUNT(DISTINCT session_id) AS n FROM prompts").fetchone()["n"]
    return int(couvertes or 0), int(total or 0)


def tier_usage(settings: Settings | None = None) -> list[TierUsage]:
    """Le releve par palier, agrege sur toutes les sessions **couvertes**."""
    offres = tier_offers(settings)
    par_palier: dict[str, list[TierOffer]] = {}
    for offre in offres:
        par_palier.setdefault(offre.tier, []).append(offre)
    return [
        TierUsage(
            tier=key,
            label=lignes[0].label,
            sessions=len(lignes),
            offered=sum(1 for ligne in lignes if ligne.offered),
            used=sum(1 for ligne in lignes if ligne.offered and ligne.produced),
            produced=sum(ligne.produced for ligne in lignes),
        )
        for key, lignes in par_palier.items()
    ]


# -- Ecriture ---------------------------------------------------------------


def _as_float(value: str, field_name: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HistoryError(f"« {field_name} » doit être un nombre.") from exc


def _vocabulary(raw: str, allowed: dict[str, str]) -> str | None:
    """Une valeur du vocabulaire, ou None. Tolerante a l'orthographe rendue.

    Claude ecrit « manière » avec son accent, et l'accepter evite de perdre la
    colonne entiere sur un detail de rendu. Une valeur hors vocabulaire vaut
    « non renseigne » plutot qu'une erreur : refuser un import de vingt lignes
    pour un mot inattendu couterait plus que la ligne manquante.

    La normalisation est celle des libelles de marche — minuscules, sans
    accents — parce que c'est exactement le meme probleme : du texte ecrit a la
    main dont seule la forme varie.
    """
    value = _market_key(raw)
    if not value:
        return None
    return value if value in allowed else None


@dataclass(frozen=True)
class ClaimColumns:
    """Ce qu'un bloc de confiance ecrit sur une selection, une fois applique.

    **Ecrit une fois et pose deux fois** : `add_pick` s'en sert a la creation, le
    rattachement d'un rejeu s'en sert sur une selection deja en base. Deux
    ecritures de cette table auraient diverge — c'est le piege que le projet paie
    a chaque regle recopiee, et celle-ci porte le cran calcule, donc la seule
    chose que toute la chaine d'ingestion mesure.
    """

    source_effective: str | None
    computed: int | None
    claimed: int | None
    overridden: int | None
    cause: str | None
    raw: str | None
    gap_touches_factor: int | None
    distinct_publishers: int | None
    #: Le niveau de source **declare**, corrige par le bloc quand il en porte un.
    #: Reste une entree de la mesure : il ne se confond pas avec l'effectif.
    source_declared: str | None


def claim_columns(
    claim: str,
    source_level: str | None,
    opened: bool | None,
    override_cause: str = "",
) -> ClaimColumns:
    """Applique la table des crans a un bloc de confiance.

    **Aucun repli silencieux sur la valeur declaree.** Un bloc illisible laisse
    le cran a NULL et journalise : retomber sur l'annonce ferait passer pour
    calculee une note qui ne l'est pas, et le taux de desaccord — la seule chose
    que ce chantier mesure — annoncerait un accord parfait.
    """
    declaration: Claim | None = None
    if (claim or "").strip():
        try:
            declaration = parse_claim(claim)
        except ClaimError as exc:
            logger.warning("Bloc de confiance illisible, cran laisse inconnu : %s", exc)

    source_value = source_level
    if declaration is not None:
        # Le bloc structure fait foi sur le niveau de source : c'est la meme
        # declaration, sous une forme que l'application sait relire. En laisser
        # deux ecritures les aurait fait diverger au premier rendu ou la colonne
        # du tableau et le bloc ne disent pas la meme chose.
        source_value = _vocabulary(declaration.source_level, SOURCE_LEVELS) or source_value

    claimed = declaration.rung if declaration is not None else None
    computed = claimed
    overridden = opened is False
    effective = source_value
    cause = None
    if overridden:
        effective, computed = READING_LEVEL, FORCED_RUNG
        # **Une cause absente est une cause, pas un silence.** Cette ligne
        # ecrivait `None` des que le motif sortait du vocabulaire, et 43
        # selections des sessions 11 et 13 sont entrees ainsi : indiscernables
        # d'une ligne anterieure au typage, donc comptees comme des observations
        # sur le modele par `is_collection_fault(None) is False`. Le repli
        # nomme, et le compte cesse d'imputer ce qu'il ignore.
        cause = override_cause if override_cause in OVERRIDE_CAUSES else OVERRIDE_INCONNUE
    elif opened and declaration is not None and not declaration.facts:
        # **La regle est a sens unique.** L'absence de dossier force la lecture ;
        # la presence n'accorde rien. Un dossier ouvert dont l'analyse ne tire
        # aucun fait date **est** une lecture des blocs.
        effective, computed = READING_LEVEL, FORCED_RUNG
        cause = OVERRIDE_SANS_FAIT
    return ClaimColumns(
        source_effective=effective,
        computed=computed,
        claimed=claimed if overridden else None,
        overridden=_flag(None if opened is None else not opened),
        cause=cause,
        raw=declaration.raw if declaration is not None else None,
        gap_touches_factor=(
            _flag(declaration.gap_touches_factor) if declaration is not None else None
        ),
        distinct_publishers=declaration.distinct_publishers if declaration is not None else None,
        source_declared=source_value,
    )


def unique_pick(
    session_id: int,
    market: str,
    selection: str,
    settings: Settings | None = None,
) -> tuple[int | None, str]:
    """La selection de cette session portant ce marche et ce libelle, si elle est
    **unique**. Rend `(None, motif)` sinon.

    **Le rapprochement ne passe pas par l'evenement, et c'est mesure.** La
    signature d'import inclut l'identifiant de match, qui se resout par la
    shortlist et le voisinage — donc qui **se perime** : releve du 19/08/2026, les
    douze selections qu'un rejeu declarait « neuves » portaient toutes
    `event_id = None` parce que leur match avait quitte la fenetre, et **toutes
    les douze existaient deja en base**. Une cle qui decroit avec le temps ne peut
    pas servir a reconnaitre ce qu'on a deja.

    L'unicite est **exigee** plutot que contournee : une session peut porter deux
    selections du meme marche sur deux matchs, et poser un cran sur la mauvaise
    ligne serait le defaut que la somme de controle de l'appariement existe pour
    empecher. Le cas se dit, il ne se devine pas.
    """
    rows = query(
        "SELECT id FROM picks WHERE session_id = ? AND market = ? AND selection = ?",
        (session_id, market.strip(), selection.strip()),
        settings=settings,
    )
    if not rows:
        return None, "aucune sélection de la session ne porte ce marché et ce libellé"
    if len(rows) > 1:
        return None, f"{len(rows)} sélections portent ce marché et ce libellé"
    return int(rows[0]["id"]), ""


def has_claim(pick_id: int, settings: Settings | None = None) -> bool:
    """Cette selection porte-t-elle deja un bloc de confiance ?"""
    row = query_one("SELECT claim_raw_json FROM picks WHERE id = ?", (pick_id,), settings=settings)
    return row is not None and row["claim_raw_json"] is not None


def attach_claim(
    pick_id: int,
    claim: str,
    source_level: str = "",
    opened: bool | None = None,
    override_cause: str = "",
    claim_offsets: str = "",
    settings: Settings | None = None,
) -> bool:
    """Pose un bloc de confiance sur une selection **deja enregistree**.

    Rend vrai s'il a ete pose, faux si la selection en portait deja un.

    **Rien n'est ecrase.** Le premier releve fait foi : ce chemin repare un
    collage dont les blocs se sont perdus en route, il ne corrige pas une
    declaration. Et il applique **la meme table** que `add_pick`, par le meme
    appel — deux ecritures du calcul du cran auraient diverge, et c'est lui que
    toute la chaine d'ingestion mesure.

    **La declaration reste intacte** : `source_level` et `confidence` sont les
    entrees de la mesure, et cette fonction ne touche qu'a ce qui vit a cote.
    """
    if has_claim(pick_id, settings):
        return False
    row = query_one("SELECT source_level FROM picks WHERE id = ?", (pick_id,), settings=settings)
    if row is None:
        raise HistoryError(f"Sélection inconnue : {pick_id}")
    colonnes = claim_columns(claim, source_level or row["source_level"], opened, override_cause)
    debut, _, fin = str(claim_offsets or "").partition(":")
    execute(
        "UPDATE picks SET claim_raw_json = ?, confidence_computed = ?, "
        "  confidence_claimed = ?, gap_touches_factor = ?, distinct_publishers = ?, "
        "  source_level_effective = ?, research_overridden = ?, "
        "  research_override_cause = ?, claim_offset_start = ?, claim_offset_end = ? "
        " WHERE id = ?",
        (
            colonnes.raw,
            colonnes.computed,
            colonnes.claimed,
            colonnes.gap_touches_factor,
            colonnes.distinct_publishers,
            colonnes.source_effective,
            colonnes.overridden,
            colonnes.cause,
            int(debut) if debut.isdigit() else None,
            int(fin) if fin.isdigit() else None,
            pick_id,
        ),
        settings=settings,
    )
    return True


@writes(SELECTION, CONF, EXPLORATOIRE)
def add_pick(
    session_id: int,
    tier: str,
    market: str,
    selection: str,
    *,
    event_id: str = "",
    price: str = "",
    confidence: str = "",
    stake: str = "",
    angle: str = "",
    source_level: str = "",
    price_source: str = "",
    independence_note: str = "",
    #: La prose du tableau de la section C. **Facultative**, comme `angle` et
    #: `source_level` : une valeur absente vaut « non renseigne », jamais un
    #: refus. Le controle 7 se contente de la compter — l'opposer avant d'en
    #: connaitre le taux de base serait construire avant de mesurer.
    angle_note: str = "",
    invalidation: str = "",
    #: `import` quand les deux colonnes ont ete lues au collage, `reconstruit`
    #: quand elles ont ete reprises apres coup depuis `imports_raw.raw_text`.
    #: Une reprise passe par un rapprochement, donc par une regle faillible ;
    #: une captation recopie une cellule. Les deux ne se lisent pas pareil.
    prose_source: str = PROSE_FROM_IMPORT,
    late_reason: str = "",
    claim: str = "",
    opened: bool | None = None,
    override_cause: str = "",
    #: La ligne vient de la section **C-bis**, ou l'exigence d'un fait date
    #: tombe. **Comptee a part de bout en bout** : melanger les deux populations
    #: detruirait la comparaison que cette section existe pour rendre possible.
    exploratory: bool = False,
    #: D'ou vient cette ligne dans le collage brut. **Trois valeurs et non une** :
    #: la ligne du tableau et le bloc de confiance arrivent d'endroits differents
    #: du texte, et une seule paire de bornes ferait mentir l'autre.
    import_id: str | int = "",
    offsets: str = "",
    claim_offsets: str = "",
    played: bool = False,
    result: str = "pending",
    settings: Settings | None = None,
) -> int:
    """Enregistre une selection. Renvoie son id.

    **Elle n'est pas jouee pour autant** : `played` ne passe a vrai qu'au
    rattachement a un coupon, c'est a dire quand le pari a reellement ete pose
    chez le bookmaker. Sans cette regle, une selection proposee par Claude puis
    ecartee comptait dans les taux au meme titre qu'un pari joue, et les
    indicateurs melangeaient deux questions differentes : ce que vaut l'analyse,
    et ce que valent mes paris.
    """
    settings = settings or get_settings()
    if not market.strip():
        raise HistoryError("« Marché » est obligatoire.")
    if not selection.strip():
        raise HistoryError("« Sélection » est obligatoire.")
    if result not in RESULTS:
        raise HistoryError(f"Résultat inconnu : {result}")

    price_value = _as_float(price, "Cote")
    # Une cote se saisit au moment de l'analyse, et c'est celle-la qui compte :
    # elle vient du bloc CONTEXTE que Claude avait sous les yeux, releve au
    # scan. La relire chez le fournisseur au moment du rattachement donnerait
    # un prix posterieur, donc une autre grandeur. Elle reste facultative — les
    # selections anterieures a cette regle n'en portent pas — mais 1.00 ou
    # moins n'est pas une cote : ce serait un taux implicite d'au moins 100 %.
    if price_value is not None and price_value <= 1.0:
        raise HistoryError("« Cote » doit être supérieure à 1.00.")
    # Une cote hors de toutes les bandes n'a pas de palier, et la ranger sous
    # celui qui a ete choisi au formulaire ferait entrer dans un taux par bande
    # de cote une selection qui n'y appartient pas.
    _reject_out_of_band(price_value, settings, "Cote")
    stake_value = _as_float(stake, "Mise")
    confidence_value = _as_float(confidence, "Confiance")
    if confidence_value is not None and not 1 <= confidence_value <= 5:
        raise HistoryError("« Confiance » doit être comprise entre 1 et 5.")
    # Les deux dimensions du « pourquoi » sont facultatives : cent selections
    # deja en base n'en portent aucune, et une valeur inconnue vaut « non
    # renseigne » plutot qu'un refus — le seul effet est une ligne de moins dans
    # les statistiques, comme pour un niveau de competition.
    angle_value = _vocabulary(angle, ANGLES)
    source_value = _vocabulary(source_level, SOURCE_LEVELS)

    # LE CRAN CALCULE. Le gabarit definit la table des crans comme une fonction
    # de trois choses verifiables ; le modele l'appliquait lui-meme, et la mesure
    # dit ce que ca valait — 90 % du volume sur deux crans, aucun cran 1 sur 149,
    # et un ordre non monotone. Ce qui est deterministe se calcule.
    #
    # **Le calcul vit dans `claim_columns` et non ici**, parce qu'il se pose
    # aussi sur une selection **deja en base** : le rejeu d'un collage dont les
    # lignes sont entrees sans bloc ne cree rien, il complete. Deux ecritures de
    # cette table auraient diverge au premier ajustement.
    #
    # **La declaration reste intacte.** `source_level` et `confidence` sont les
    # **entrees** de la mesure ; l'effectif et le cran calcule vivent a cote. Les
    # ecraser ferait mesurer a la page sa propre correction.
    colonnes = claim_columns(claim, source_value, opened, override_cause)
    source_value = colonnes.source_declared

    # D'ou vient la cote recopiee. Facultative comme les deux precedentes : une
    # valeur inconnue vaut « on ne sait pas », jamais un refus.
    price_origin = _vocabulary(price_source, PRICE_SOURCES)

    attached = int(event_id) if str(event_id).strip().isdigit() else None
    # La prose se normalise ici et pas au parsing : la saisie a la main passe
    # par le meme chemin, et un espace de trop y arrive aussi bien que d'un
    # tableau colle.
    argument = " ".join((angle_note or "").split())
    condition = " ".join((invalidation or "").split())
    with connect(settings) as conn:
        known = {row["key"] for row in conn.execute("SELECT key FROM tiers")}
        if tier not in known:
            raise HistoryError(f"Palier inconnu : {tier}")

        # Une seconde selection sur le meme match reclame sa justification
        # d'independance. **Seul controle bloquant du module**, et c'est
        # deliberе : ailleurs une valeur manquante vaut « non renseigne », ici
        # elle vaudrait « je ne me suis pas pose la question ». Le prompt nomme
        # precisement ce cas — multiplier les lignes d'un meme match pour
        # atteindre un quota est une facon deguisee de remplir un palier avec du
        # vide — et rien d'autre ne permet de distinguer deux angles vraiment
        # independants de deux facons de dire la meme chose.
        note = (independence_note or "").strip()
        if attached is not None and not note:
            already = conn.execute(
                "SELECT COUNT(*) AS n FROM picks WHERE session_id = ? AND event_id = ?",
                (session_id, attached),
            ).fetchone()["n"]
            if already:
                raise HistoryError(
                    "Ce match porte déjà une sélection. Une seconde ne se justifie que "
                    "sur un angle réellement indépendant : dis lequel, en une ligne."
                )

        # La cle de marche est **figee ici**, au moment ou le releve du marche
        # est pris pour cette session : c'est ce qui rattache la selection a ce
        # releve, et le lien doit tenir meme si un libelle est renomme dans
        # `render` par la suite. Elle reste NULL quand le libelle sort du
        # vocabulaire du bloc — « Double chance » la ou il ecrit « DC » — et se
        # resout alors a la lecture, sans jamais etre devinee.
        #
        # L'ANTERIORITE. **Elle ne refuse plus rien, et c'est une decision datee
        # du 17/08/2026.**
        #
        # La garde d'origine (migration 034) reclamait un motif — `differee` ou
        # `live` — et refusait sans. Elle se laissait contourner : 37 selections
        # tardives sur 52 n'ont jamais rien declare. Et surtout, **refuser ferait
        # disparaitre la population qui porte la mesure du biais** : les 52
        # tardives sont au-dessus de leur prix (32 pour 28,8 payees), les 178
        # anterieures en dessous (89 pour 103,8), et l'ecart entre les deux est
        # la meilleure estimation disponible de ce que coute une selection ecrite
        # en connaissant le debut du match.
        #
        # Une selection dont le coup d'envoi est passe est donc enregistree en
        # **population tardive d'office**, declaration ou non. Le motif reste une
        # information — il separe une decision anterieure mal saisie d'un pari
        # pris en direct — mais ce n'est plus une autorisation.
        late = _vocabulary(late_reason, LATE_REASONS)
        tardive = False
        if attached is not None:
            debut = conn.execute(
                "SELECT commence_time FROM events WHERE id = ?", (attached,)
            ).fetchone()
            tardive = debut is not None and utcnow() >= str(debut["commence_time"])

        # Sans match rattache, aucun sport, donc aucun vocabulaire : rien.
        resolved = None
        if attached is not None:
            sport = conn.execute(
                "SELECT s.key AS sport_key FROM events e "
                "JOIN sports s ON s.id = e.sport_id WHERE e.id = ?",
                (attached,),
            ).fetchone()
            if sport is not None:
                resolved = market_key_for(sport["sport_key"], market.strip())

        cursor = conn.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, stake, result, angle, source_level, "
            "                   source_level_effective, "
            "                   price_source, independence_note, market_key, "
            "                   late_reason, confidence_computed, claim_raw_json, "
            "                   gap_touches_factor, distinct_publishers, "
            "                   confidence_claimed, research_overridden, "
            "                   research_override_cause, exploratoire, tardive, import_id, "
            "                   offset_start, offset_end, claim_offset_start, "
            "                   claim_offset_end, angle_note, invalidation, prose_source, "
            "                   created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                attached,
                tier,
                market.strip(),
                selection.strip(),
                price_value,
                int(confidence_value) if confidence_value is not None else None,
                1 if played else 0,
                stake_value,
                result,
                angle_value,
                source_value,
                colonnes.source_effective,
                price_origin,
                note or None,
                resolved,
                late,
                colonnes.computed,
                colonnes.raw,
                colonnes.gap_touches_factor,
                colonnes.distinct_publishers,
                colonnes.claimed,
                colonnes.overridden,
                colonnes.cause,
                1 if exploratory else 0,
                1 if tardive else 0,
                int(import_id) if str(import_id).strip().isdigit() else None,
                *_span(offsets),
                *_span(claim_offsets),
                argument or None,
                condition or None,
                # **La provenance ne s'ecrit que s'il y a quelque chose a
                # situer.** Un `import` pose sur deux colonnes vides dirait que
                # le collage les portait vides, quand il ne les portait pas du
                # tout — la distinction que cette colonne existe pour tenir.
                prose_source if (argument or condition) else None,
                utcnow(),
            ),
        )
        pick_id = int(cursor.lastrowid)
        # **Le retard en minutes se pose par la regle commune**, jamais a cote.
        # `tardive` est calcule ci-dessus contre `utcnow()` et la regle compare
        # `created_at` a `commence_time` : les deux coincident a l'insertion, et
        # rejouer la regle ici les fait coincider **par construction** plutot
        # que par chance. C'est aussi le seul endroit ou les deux colonnes
        # peuvent se poser d'un coup.
        if attached is not None:
            conn.execute(_LATE_RULE, (attached,))
        return pick_id


def _span(raw: str) -> tuple[int | None, int | None]:
    """« 120:154 » — les bornes d'un fragment du collage brut, ou deux `None`.

    Une valeur illisible ne coute que l'ancre : la selection s'enregistre quand
    meme, elle ne saura simplement plus d'ou elle vient. Refuser l'ecriture pour
    une ancre manquante ferait perdre la donnee pour sauver sa provenance.
    """
    parts = str(raw or "").split(":")
    if len(parts) != 2 or not all(part.strip().lstrip("-").isdigit() for part in parts):
        return None, None
    return int(parts[0]), int(parts[1])


def _flag(value: bool | None) -> int | None:
    """Un booleen a trois etats, tel que SQLite le porte. `None` reste `None`."""
    return None if value is None else int(value)


def set_result(pick_id: int, result: str, settings: Settings | None = None) -> None:
    """Met a jour le resultat d'un pick."""
    if result not in RESULTS:
        raise HistoryError(f"Résultat inconnu : {result}")
    with connect(settings) as conn:
        conn.execute("UPDATE picks SET result = ? WHERE id = ?", (result, pick_id))


def set_real_price(pick_id: int, price: str = "", settings: Settings | None = None) -> None:
    """Enregistre la cote **obtenue chez le bookmaker principal**, et son palier.

    Elle ne se releve jamais toute seule : ce serait une integration
    transactionnelle avec un bookmaker, interdit n°7 de SPEC.md. Elle se saisit
    apres avoir pose le pari, la ou l'on a le ticket sous les yeux.

    Le palier est recalcule **a l'ecriture** et non a la lecture, contrairement
    a la famille d'un marche : la selection a ete posee a ce prix-la, un jour
    donne, et un reglage de bande change plus tard ne doit pas reclasser un pari
    deja joue. C'est exactement l'inverse de la taxonomie, et pour la meme
    raison — l'une decrit une decision datee, l'autre un classement corrigeable.

    Une saisie vide efface la cote **et** son palier : sans elle, le palier
    recalcule resterait comme la trace d'un prix qui n'existe plus.
    """
    settings = settings or get_settings()
    value = _as_float(price, "Cote obtenue")
    if value is not None and value <= 1.0:
        raise HistoryError("« Cote obtenue » doit être supérieure à 1.00.")
    _reject_out_of_band(value, settings, "Cote obtenue")
    tier = tier_for_price(value, settings)
    with connect(settings) as conn:
        conn.execute(
            "UPDATE picks SET price_real = ?, tier_real = ? WHERE id = ?",
            (value, tier, pick_id),
        )
    logger.info("Cote obtenue sur le pick %d : %s (palier %s)", pick_id, value, tier or "—")


def set_event(pick_id: int, event_id: str = "", settings: Settings | None = None) -> None:
    """Rattache une selection a un match, ou l'en detache si `event_id` est vide.

    Se corrige apres coup : le rapprochement automatique de l'import refuse de
    deviner, et la shortlist ne contient pas toujours le match vise. Sans cette
    reprise, une selection restait « — hors match — » definitivement — donc
    sans sport ni competition, donc muette dans les statistiques.
    """
    identifier = str(event_id).strip()
    with connect(settings) as conn:
        if not identifier:
            # Le marche perd sa cle avec son match : sans sport, le meme libelle
            # designe deux marches differents. La laisser en place ferait
            # survivre la lecture d'un vocabulaire qui ne s'applique plus.
            # `tardive` part avec le match, pour la meme raison : le retard se
            # demontre contre un coup d'envoi, et sans match il n'y en a plus.
            # La regle du projet est a sens unique — on ne prouve pas l'absence.
            conn.execute(
                "UPDATE picks SET event_id = NULL, market_key = NULL, tardive = 0, "
                "                 late_minutes = NULL WHERE id = ?",
                (pick_id,),
            )
            return
        if not identifier.isdigit():
            raise HistoryError(f"Match inconnu : {event_id}")
        known = conn.execute(
            "SELECT s.key AS sport_key FROM events e "
            "JOIN sports s ON s.id = e.sport_id WHERE e.id = ?",
            (int(identifier),),
        ).fetchone()
        if known is None:
            raise HistoryError(f"Match inconnu : {event_id}")
        # Le rattachement corrige peut changer de sport, donc de vocabulaire :
        # la cle se **recalcule** au lieu d'etre conservee. C'est la seule
        # ecriture ou elle bouge apres coup, et c'est justifie — elle etait
        # fausse, pas perimee.
        label = conn.execute("SELECT market FROM picks WHERE id = ?", (pick_id,)).fetchone()
        resolved = market_key_for(known["sport_key"], label["market"]) if label else None
        # **`tardive` se recalcule aussi**, et pour la meme raison exactement :
        # le retard se demontre contre le coup d'envoi du match rattache, donc un
        # rattachement corrige peut le creer comme le lever. Elle etait fausse,
        # pas perimee.
        # Le rattachement d'abord, la regle du retard ensuite : elle se lit sur
        # `picks.event_id`, donc elle a besoin du lien pour dire quoi que ce
        # soit. Recalculer le retard a la main ici aurait fait une **seconde
        # ecriture de la meme regle**, qui aurait diverge de `_LATE_RULE` au
        # premier ajustement — et le premier ajustement est arrive des le lot
        # suivant, avec les minutes.
        conn.execute(
            "UPDATE picks SET event_id = ?, market_key = ? WHERE id = ?",
            (int(identifier), resolved, pick_id),
        )
        conn.execute(_LATE_RULE, (int(identifier),))


#: La regle du retard, ecrite **une seule fois** et en SQL parce que ses trois
#: appelants sont des ecritures : `add_pick` a la creation, `set_event` a la
#: correction d'un rattachement, et le scan quand un coup d'envoi bouge.
#:
#: **Sens unique, comme l'anteriorite** : les deux heures doivent etre connues.
#: Une selection sans match n'est pas tardive — son retard n'est pas plus
#: demontre que son anteriorite.
#: **Les deux colonnes dans le meme UPDATE**, et jamais deux regles paralleles.
#: Le drapeau dit *si* la selection est tardive, les minutes disent *de combien* ;
#: ecrits separement, ils auraient fini par ne plus dire la meme chose du meme
#: match — et le cas ou ils divergent est justement celui qui coute, un report
#: laissant un retard qui n'existe plus.
#:
#: `late_minutes` est **NULL et non zero** hors des tardives : une selection
#: anterieure n'a pas un retard nul, elle n'en a pas. Ecrire 0 la ferait entrer
#: dans la premiere bande de retard et gonflerait de 178 lignes une population
#: qui en porte 52.
_LATE_RULE = (
    "UPDATE picks SET tardive = ("
    "  SELECT e.commence_time IS NOT NULL AND picks.created_at >= e.commence_time "
    "  FROM events e WHERE e.id = picks.event_id"
    "), late_minutes = ("
    "  SELECT CASE WHEN e.commence_time IS NOT NULL "
    "                   AND picks.created_at >= e.commence_time "
    "              THEN CAST(ROUND("
    "                     (julianday(picks.created_at) - julianday(e.commence_time)) * 1440"
    "                   ) AS INTEGER) END "
    "  FROM events e WHERE e.id = picks.event_id"
    ") WHERE event_id = ?"
)


def refresh_late(event_id: int, settings: Settings | None = None) -> int:
    """Recalcule le retard des selections d'un match. Rend le nombre touche.

    **Appele des que le coup d'envoi bouge**, et c'est indispensable : un match
    reporte n'a pas commence, donc une selection ecrite « apres » l'ancien
    horaire n'a rien vu du tout. La colonne serait fausse, et une population
    fausse est pire qu'une population derivee — elle affirme.

    C'est le seul cas ou un report change une mesure deja ecrite ; le projet en
    garde d'ailleurs la trace depuis la migration 040.
    """
    with connect(settings) as conn:
        cursor = conn.execute(_LATE_RULE, (event_id,))
        return int(cursor.rowcount or 0)


def delete_pick(pick_id: int, settings: Settings | None = None) -> None:
    with connect(settings) as conn:
        conn.execute("DELETE FROM picks WHERE id = ?", (pick_id,))


# -- Statistiques -----------------------------------------------------------


def _late(row: Any) -> bool:
    """La selection est **demontrablement** posterieure au coup d'envoi.

    Le pendant exact de `_antecedence`, et le filtre porte sur celui-ci — pas
    sur la negation de l'autre. La difference tient aux selections **sans
    match** : leur anteriorite n'est pas etablie, mais leur retard ne l'est pas
    davantage, faute de coup d'envoi contre quoi les dater.

    **C'est la meme regle d'un seul sens que partout ici** : la base peut
    prouver l'anteriorite quand elle voit les deux heures, et le retard dans le
    meme cas ; elle ne peut rien prouver quand il en manque une. On n'ecarte
    donc que ce qui est demontre, et une selection sans match reste comptee —
    son propre manque est deja declare par les compteurs de non-classees.
    """
    commence = _column(row, "commence_time")
    return bool(commence) and str(row["created_at"]) >= str(commence)


def _antecedence(row: Any) -> bool:
    """La selection a-t-elle ete enregistree avant le coup d'envoi.

    **Ce n'est pas un filtre de proprete, c'est ce qui fait du prix un prix.**
    Une selection saisie apres le coup d'envoi porte une cote saisie apres le
    coup d'envoi, et son `1/cote` ne decrit alors plus le marche d'avant-match —
    donc plus rien de comparable a un resultat. Tout le residu en depend.

    Ce qui **s'observe** : sur les selections sans anteriorite etablie, le
    residu au prix est nul — 20 victoires pour 20,25 payees, p = 0,53 — quand il
    vaut -9,31 sur les autres, et l'ajustement y tient dans chaque bande de
    cote. Ce qui s'en **deduit** — qu'un prix collant a ce point au resultat
    aurait ete releve en le connaissant — est une inference, pas une
    observation : elle est plausible, elle vit dans `CLAUDE.md` au conditionnel,
    et ni cette fonction ni la page ne l'affirment.

    **Sens unique, et le libelle doit le respecter.** `created_at` est l'heure
    d'**enregistrement dans l'application**, pas celle de la decision : une
    saisie tardive d'une analyse faite a temps y ressemble a un pari pose apres
    coup. La base peut donc prouver l'anteriorite, jamais son absence — d'ou
    « anteriorite non etablie », et jamais « enregistre apres coup ».
    """
    commence = _column(row, "commence_time")
    return bool(commence) and str(row["created_at"]) < str(commence)


def _reference_priced(row: Any) -> bool:
    """La cote vient d'un book de reference et rien n'a ete releve depuis.

    **Ce n'est plus une quarantaine, et c'est une decision datee du
    17/08/2026.** Ces selections sortaient du regroupement par palier « en
    attendant leur prix reel » — un prix qui n'arrivera **jamais** : il ne peut
    venir que d'une mise, et aucun pari n'est pose. Zero coupon sur douze
    sessions ; l'application n'est pas un carnet de mises, c'est un banc de
    mesure de predictions.

    L'attente coutait donc 20 selections tranchees sur 178 a l'axe le plus
    fourni de la page, sans terme. Et le gabarit tranche deja la question
    d'autorite : « Ces cotes font autorite. Ne remplace jamais une cote du bloc
    par une cote trouvee en ligne. » La cote du bloc **est** la cote de calcul ;
    le code n'en tirait pas la consequence.

    Le drapeau reste, et il est desormais un **axe de mesure** : si les lignes
    servies par un book de reference se comportent autrement, il faut pouvoir le
    voir. C'est une mesure, jamais un filtre.
    """
    return _column(row, "price_source") == PRICE_REFERENCE and _column(row, "price_real") is None


#: Le palier **recalcule depuis la cote declaree**, celle que l'analyse avait
#: sous les yeux. C'est la lecture du journal d'analyse.
TIER_DECLARED = "declared"

#: Le palier de la cote **obtenue** quand elle existe, sinon le provisoire.
#: C'est la lecture du journal de mise.
TIER_EXECUTED = "executed"


def _bands(conn: Any) -> list[tuple[str, float, float | None]]:
    """Les bandes reglees, dans l'ordre de parcours, lues **une fois**.

    `tier_for_price` ouvre une connexion par appel : la passer sur quelques
    centaines de lignes en ouvrirait autant. La convention de borne, elle, reste
    celle de `in_band` — c'est la lecture qui change, pas la regle.
    """
    return [
        (str(row["key"]), float(row["min_price"]), _optional_float(row["max_price"]))
        for row in conn.execute("SELECT key, min_price, max_price FROM tiers ORDER BY position")
    ]


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _tier_of(row: Any, source: str, bands: Sequence[tuple[str, float, float | None]] = ()) -> str:
    """Le palier d'une selection, **selon le journal qui la lit**.

    Les deux lectures existaient deja, fondues dans une seule fonction qui
    rendait `tier_real or tier`. Le melange n'etait pas neutre : la cote obtenue
    est une donnee d'**execution**, et elle entrait dans la mesure d'**analyse**
    — une ligne changeait de palier dans le journal d'analyse parce qu'on avait
    saisi le prix paye. Latent tant que `price_real` ne couvre que neuf lignes ;
    la fenetre s'ouvre a mesure que la couverture monte.

    · `TIER_EXECUTED` — `stats()`, les paris **poses**. Le palier de la cote
      obtenue y est legitime : le pari a ete pose a ce prix-la.
    · `TIER_DECLARED` — `analysis()` et `exploratory()`, ce que vaut
      l'**analyse**. Le palier s'y recalcule depuis la cote declaree, et jamais
      depuis `picks.tier` : cette colonne porte l'emoji colle par le modele,
      donc le barme en vigueur le jour du collage. La recalculer rend toute
      derive entre le cadre et la configuration inoffensive **pour la mesure**,
      celle-ci comme les suivantes.

    L'emoji reste en base et cesse d'etre lu comme un classement : il devient un
    signal d'audit, l'ecart entre emis et recalcule mesurant l'adherence du
    cadre (`tier_drift`).

    **Deux replis, tous deux nommes plutot que silencieux** : une selection sans
    cote ne se recalcule pas, et une cote hors de toutes les bandes non plus.
    Les deux rendent le palier declare — la seule reponse disponible. Le second
    ne peut pas arriver sur une ecriture recente, `_reject_out_of_band` le
    refusant a la source, et le premier decrit les selections anterieures a la
    colonne `price`.
    """
    if source == TIER_EXECUTED:
        return str(_column(row, "tier_real") or row["tier"])
    if source != TIER_DECLARED:
        raise ValueError(f"Lecture de palier inconnue : {source!r}")
    price = _column(row, "price")
    if price is None:
        return str(row["tier"])
    for key, minimum, maximum in bands:
        if in_band(float(price), minimum, maximum):
            return key
    return str(row["tier"])


def _count(entry: RateRow, result: str, row: Any = None) -> None:
    """Ajoute une selection a un regroupement.

    Ecrit une seule fois parce que quatre endroits comptent la meme chose : le
    taux par palier, celui par sport, la comparaison joue / ecarte et le total.
    Les copier aurait suffi a ce qu'un seul d'entre eux oublie la cote.

    Recoit **la ligne** et non ses champs un a un : chaque chantier de cette
    page en a ajoute un — la cote, l'identifiant, puis le match — et la liste
    d'arguments grossissait a chaque fois, obligeant a retoucher les cinq axes
    d'`analysis()` pour un champ qu'un seul d'entre eux lit.
    """
    settled = result in ("win", "loss")
    if settled and _column(row, "id") is not None:
        entry.members.add(int(row["id"]))
    if settled:
        # Un pick sans match ne se regroupe pas : rien ne dit a quelle rencontre
        # il appartient, donc rien ne permet de le rapprocher d'un autre. Il
        # compte pour une unite a lui seul — c'est l'hypothese **optimiste**, et
        # la note du bloc dit dans quel sens elle penche.
        event_id = _column(row, "event_id")
        if event_id is None:
            entry.unattached += 1
        else:
            entry.events.add(int(event_id))
    if result == "win":
        entry.won += 1
    elif result == "loss":
        entry.lost += 1
    elif result == "void":
        entry.void += 1
    else:
        entry.pending += 1
    # **Le prix ne se cumule plus par regroupement.** Il l'a ete pendant
    # plusieurs lots, et le detail chiffre affichait donc un residu par ligne —
    # sans test, sans correction de multiplicite, sans fragilite. Personne ne
    # l'a jamais lu comme tel, et le residu a fini par etre « decouvert » cinq
    # lots plus tard par un calcul refait a la main. Trente residus non testes
    # ne se lisent pas, ils decorent : celui-ci vit en tete de page, sur la
    # population entiere, et nulle part ailleurs.


def _tally(rows: list[Any], key_field: str, labels: dict[str, str]) -> list[RateRow]:
    grouped: dict[str, RateRow] = {}
    for row in rows:
        # `tier_effective` n'est pas une colonne : c'est le palier de la cote
        # obtenue quand elle existe, sinon le provisoire. Le resoudre ici evite
        # de dupliquer la regle dans chaque appelant.
        key = (
            _tier_of(row, TIER_EXECUTED) if key_field == "tier_effective" else row[key_field]
        ) or NO_SPORT
        entry = grouped.setdefault(key, RateRow(key=key, label=labels.get(key, key)))
        _count(entry, row["result"] or "pending", row)
    return list(grouped.values())


def stats(settings: Settings | None = None) -> Stats:
    """Taux de reussite par palier et par sport, sur les picks **joues**.

    Joue veut dire rattache a un coupon, donc reellement pose chez le
    bookmaker. Une selection proposee puis ecartee ne compte pas : elle
    repondrait a une autre question que celle posee ici.

    Aucune ponderation par la mise : ce serait un indicateur financier, donc
    hors du perimetre de l'application.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        rows = conn.execute(
            "SELECT k.id, k.tier, k.result, k.price, k.event_id, "
            "       k.price_source, k.price_real, k.tier_real, s.key AS sport_key FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "WHERE k.played = 1 AND k.exploratoire = 0"
        ).fetchall()

    # **Plus aucune mise a l'ecart**, et c'est la decision du 17/08/2026 : le
    # prix reel n'arrivera jamais, il ne peut venir que d'une mise et aucun pari
    # n'est pose. La cote du bloc fait autorite — le gabarit le dit deja — donc
    # elle sert de cote de calcul. Le compte reste, comme description.
    quarantined = sum(1 for row in rows if _reference_priced(row))
    by_tier = _tally(rows, "tier_effective", tier_labels)
    by_tier.sort(key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99)
    by_sport = sorted(_tally(rows, "sport_key", sport_labels), key=lambda item: item.label)

    overall = RateRow(key="all", label="Tous")
    for entry in by_tier:
        overall.merge(entry)

    return Stats(by_tier=by_tier, by_sport=by_sport, overall=overall, quarantined=quarantined)


@dataclass
class SessionRate:
    """Une session : ce qu'elle a vu, ce qu'elle a retenu, ce que ca a donne.

    Le seul bloc de la page qui mesure le **tri** plutot que les selections.
    Passer est annonce par le prompt comme un resultat valable et attendu sur
    une partie du lot ; sans denominateur, cette phrase n'etait ni verifiable
    ni suivie.
    """

    session_id: int
    label: str
    day: str
    #: Sports du lot, tels qu'ils titrent la ligne. Resolus a la lecture depuis
    #: les matchs, jamais recopies sur la session : reclasser un sport ou
    #: corriger un rattachement doit se voir ici sans reprise de donnees.
    sports: str = ""
    #: Matchs entres dans un prompt. `None` quand la session n'en a genere
    #: aucun : elle n'a rien soumis a l'analyse, et lui preter un lot de zero
    #: ferait lire un taux de selection la ou il n'y a pas de mesure.
    lot: int | None = None
    reconstructed: bool = False
    #: Matchs distincts portant au moins une selection. C'est **lui** et non le
    #: nombre de lignes qui repond a « ai-je passe ce match ? » : deux
    #: selections sur la meme rencontre sont un match retenu, pas deux.
    covered: int = 0
    picks: int = 0
    rates: RateRow = field(default_factory=lambda: RateRow("session", ""))
    #: Le bloc de retour d'experience a transmis des taux dans au moins un
    #: prompt de cette session. Les selections qui en sortent ne sont plus des
    #: tirages independants de ce qui les mesure.
    feedback_active: bool = False
    #: La session est posterieure a la garde d'anteriorite. Son lot est propre
    #: **par construction** plutot que par filtrage.
    guarded: bool = False
    #: Le prompt le plus lourd de la session. Sert de garde-fou de poids, pas
    #: de mesure de qualite.
    tokens: int = 0
    #: Selections ramenees en lecture faute de dossier ouvert, **defauts de
    #: collecte deduits**. Un taux eleve est alors un signal sur le modele : il
    #: dit combien de fois l'analyse s'est notee comme si elle avait cherche.
    #:
    #: La deduction n'est pas un detail de presentation. Sans elle, la session
    #: du 14/08/2026 affichait 16 sur 16 et se lisait exactement ainsi — alors
    #: que la ligne `dossiers_ouverts` n'avait jamais ete collee et qu'aucune de
    #: ces selections ne dit quoi que ce soit du modele.
    overridden: int = 0
    #: Les ecrasees dont la cause est un **collage perdu** — ligne absente,
    #: illisible, ou reperes non resolus. Comptees a part et jamais avec les
    #: autres : elles ne mesurent pas une analyse, elles mesurent une
    #: transmission, et elles se reparent en recollant le rendu.
    override_faults: int = 0
    #: Les ecrasees dont **la cause elle-meme manque**. Ni une observation sur le
    #: modele, ni un defaut de collage identifie : on ne sait pas, et le dire est
    #: tout ce qu'on peut faire. Elles se comptaient auparavant avec les
    #: observations — `is_collection_fault(None)` etant faux — ce qui faisait
    #: lire 43 lignes des sessions 11 et 13 comme autant d'inflations du modele.
    override_unknown: int = 0

    @property
    def override_line(self) -> str:
        """« 3 », « 3 + 16 non transmises », « 3 + 16 non transmises + 43 sans cause ».

        Les trois nombres ne se fondent pas : ils distinguent une session ou
        l'analyse ne cherche jamais, une session dont le collage a echoue, et
        une session dont on ne sait rien. Les deux premiers appellent des gestes
        opposes ; le troisieme n'en appelle aucun, et c'est **pour ca** qu'il
        doit se voir plutot que de grossir l'un des deux autres.
        """
        if not self.overridden and not self.override_faults and not self.override_unknown:
            return ""
        return (
            str(self.overridden)
            + (f" + {self.override_faults} non transmise(s)" if self.override_faults else "")
            + (f" + {self.override_unknown} sans cause" if self.override_unknown else "")
        )

    #: Les dossiers que le rendu declarait avoir ouverts, et combien d'entre eux
    #: figuraient dans l'ordre de passage que l'application avait propose. Un
    #: dossier hors priorite est **legitime** — la section F demande justement de
    #: le dire — mais un ecart systematique dirait que le tri par « ce qu'une
    #: recherche peut y changer » ne sert a rien. Mesure, aucune decision.
    opened: int = 0
    on_priority: int = 0

    @property
    def priority_line(self) -> str:
        """« 4 dossiers ouverts, dont 1 hors de l'ordre proposé »."""
        if not self.opened:
            return ""
        hors = self.opened - self.on_priority
        return f"{self.opened} ouvert(s)" + (f", dont {hors} hors ordre proposé" if hors else "")

    @property
    def selection_rate(self) -> float | None:
        return None if not self.lot else self.covered / self.lot

    @property
    def degenerate(self) -> bool:
        """Un lot est parti a l'analyse et **rien** n'en est revenu.

        Ce n'est pas un taux de selection bas : c'est zero. Le cas s'est produit
        — 34 matchs partis le 04/08 pour aucune selection — et la ligne se
        confondait avec une journee severe. Passer est un resultat valable et
        attendu ; passer **tout** est un incident, parce qu'il ne se distingue
        pas d'un rendu jamais colle ni d'un import oublie.
        """
        return bool(self.lot) and self.picks == 0

    @property
    def density(self) -> float | None:
        """Selections par match retenu. **Une mesure de correlation**, pas de
        densite : deux selections sur la meme rencontre ne sont pas deux
        observations, et le residu suppose l'independance.
        """
        return None if not self.covered else self.picks / self.covered

    @property
    def tokens_per_match(self) -> int | None:
        """Poids du prompt rapporte au lot. Le cout fixe du cadre par match."""
        return None if not self.lot else round(self.tokens / self.lot)

    @property
    def selection_label(self) -> str:
        return "—" if self.selection_rate is None else f"{self.selection_rate * 100:.0f} %"

    @property
    def passed(self) -> int | None:
        """Matchs du lot qu'aucune selection ne porte — le PASSE, enfin compte.

        Jamais negatif : une selection rattachee a un match hors du lot — le
        voisinage propose au rattachement en offre — fait monter `covered`
        au-dela du lot sans qu'aucun match n'ait ete passe.
        """
        return None if self.lot is None else max(0, self.lot - self.covered)

    @property
    def outside(self) -> int:
        """Selections portant sur un match absent du lot.

        Dites plutot que rabotees : elles signalent soit un rattachement au
        voisinage, soit un lot sous-enregistre, et les deux meritent d'etre vus.
        """
        return 0 if self.lot is None else max(0, self.covered - self.lot)


#: Selections tranchees a anteriorite etablie par session, mesure sur les
#: sessions reelles. Sert a traduire un effectif manquant en nombre de sessions
#: — la seule unite dans laquelle une attente se decide.
SETTLED_PER_SESSION = 9.6


@dataclass
class Horizon:
    """Ce qu'il faudrait accumuler pour qu'une question devienne decidable.

    **De la planification, jamais un verdict.** Une version precedente portait un
    plafond de sessions au-dela duquel la question etait declaree tranchee — ce
    qui transformait une propriete de l'agenda de saisie en conclusion
    statistique. Il a d'ailleurs bascule d'un « rien a mesurer » a un
    « atteignable » sur les memes donnees lues a travers deux populations. Ce qui
    conclut, c'est `Equivalence` ; ceci dit seulement quand regarder a nouveau.

    **Le vrai contenu de la section des regroupements.** Elle ne conclut rien et
    ne conclura peut-etre jamais ; ce qui s'y lit utilement n'est pas un taux,
    c'est la distance au moment ou un taux voudra dire quelque chose. Un compte
    de lignes repliees n'est pas un aveu d'echec, c'est une mesure de
    progression.
    """

    question: str
    #: Selections **par ligne** deja accumulees et requises.
    have: int
    need: int
    #: Lignes comparees, pour que la question se relise sans y revenir.
    detail: str = ""

    @property
    def missing(self) -> int:
        return max(0, self.need - self.have)

    @property
    def sessions(self) -> int:
        """Sessions restantes, arrondies au superieur.

        Le manque porte sur **deux lignes** — c'est un effectif par groupe — et
        une session n'alimente pas les deux a parts egales. Le compte suppose
        un partage moyen, ce qui est une estimation et non une echeance : la
        page dit « environ ».
        """
        if not self.missing:
            return 0
        return ceil(self.missing * 2 / SETTLED_PER_SESSION)


#: Sessions d'import distinctes que la serie nulle doit couvrir avant qu'une
#: colonne muette soit une alerte.
#:
#: **Un compte de sessions, jamais de lignes** : une session peut rater son
#: collage, deux d'affilee est systematique — et le seuil s'echelonne tout seul
#: avec la taille du lot, ce qu'un seuil en lignes ne fait pas. En dessous, la
#: ligne se rend quand meme, sans le style d'alerte : elle dit alors combien de
#: sessions sont concernees, ce qui suffit a la relire la fois suivante.
COLUMN_GAP_MIN_SESSIONS = 2


@dataclass(frozen=True)
class AuditedColumn:
    """Une colonne qu'un **import** alimente, et la migration qui l'a creee.

    **Le critere d'entree est le geste qui la remplit.** Une colonne nourrie par
    une saisie a la main — `price_real` — est basse pour une raison connue et
    deja dite ailleurs ; une colonne que chaque import devrait remplir et qui
    reste nulle est un defaut invisible, parce qu'elle produit exactement la
    meme sortie qu'un succes.
    """

    column: str
    #: Migration qui l'a creee. Sa date d'application est **deja en base**
    #: (`schema_migrations.applied_at`) : l'age d'une colonne ne demande donc ni
    #: table ni saisie, seulement de le lire.
    version: int
    label: str


#: Ce que l'audit surveille.
#:
#: `confidence_claimed` en est **absente a dessein** : elle ne s'ecrit que sur
#: une selection ecrasee, donc nulle partout est son etat normal. L'auditer
#: ferait crier au defaut sur une base saine — exactement la faute que cet audit
#: existe pour attraper.
#:
#: `claim_raw_json` porte **le bloc entier** (`Claim.raw`) : non nulle veut dire
#: « un bloc de confiance a ete apparie », quel que soit son contenu. C'est bien
#: ce qu'il faut auditer — un bloc `"faits": []` est une reponse **normale**, que
#: le gabarit impose meme avec `source_level: lecture`, donc auditer les faits
#: confondrait le cas ordinaire avec le manque. La colonne s'appelait
#: `facts_json` jusqu'au 14/08/2026, et ce nom a fait construire un garde-fou
#: entier sur la premisse inverse (migration 046).
#:
#: **`confidence_claimed` en est absente, et cette exclusion se documente parce
#: qu'elle est un raisonnement humain — la classe de raisonnement que cet audit
#: existe pour remplacer.** Elle ne s'ecrit **que** sur une selection ecrasee
#: (`add_pick` : `claimed if overridden else None`), donc nulle partout est son
#: etat normal et le restera sur une base ou aucun dossier n'est jamais declare
#: ferme. L'ajouter de bonne foi ferait crier au defaut sur une base saine.
#:
#: L'objection est juste et il faut la connaitre : « nulle partout est son etat
#: normal » etait aussi vrai de `confidence_computed` jusqu'au 13/08/2026 au soir.
#: Ce qui separe les deux n'est pas leur taux de remplissage mais **ce qui les
#: remplit** : `confidence_computed` s'ecrit sur chaque import portant un bloc,
#: `confidence_claimed` sur le seul sous-cas de l'ecrasement. Le critere reste
#: donc « toute selection importee devrait la porter », et elle n'y repond pas.
#: Verifie le 14/08/2026.
AUDITED_COLUMNS: tuple[AuditedColumn, ...] = (
    AuditedColumn("angle", 26, "le type d'angle"),
    AuditedColumn("source_level", 26, "le niveau de source"),
    AuditedColumn("claim_raw_json", 42, "le bloc de confiance"),
    AuditedColumn("confidence_computed", 42, "le cran calculé"),
    AuditedColumn("research_overridden", 43, "les dossiers ouverts"),
)


@dataclass
class ColumnGap:
    """Une colonne restee nulle sur tout ce qui a ete importe depuis sa naissance.

    **Meme defaut qu'une densite a zero** : un echec qui produit exactement la
    meme sortie qu'un succes. La carte « par cran calcule » disait « aucun cran
    calcule » en l'imputant aux selections d'avant le chantier — c'etait vrai, et
    ca masquait que les nouvelles non plus n'en portaient pas.
    """

    column: str
    label: str
    #: Date d'application de la migration qui l'a creee.
    since: str
    #: Sessions d'import distinctes posterieures a cette date.
    sessions: int
    picks: int
    minimum: int = COLUMN_GAP_MIN_SESSIONS

    @property
    def alert(self) -> bool:
        return self.sessions >= self.minimum

    @property
    def line(self) -> str:
        jour = self.since[:10] if self.since else "?"
        return (
            f"{self.label} n'a reçu aucune valeur depuis sa mise en service le {jour} : "
            f"{self.picks} sélection(s) enregistrée(s) sur {self.sessions} session(s) "
            "d'import, toutes vides."
        )


def column_gaps(settings: Settings | None = None) -> list[ColumnGap]:
    """Les colonnes muettes depuis leur naissance.

    **L'age d'une colonne ne demande aucune donnee nouvelle** : la date
    d'application de sa migration est deja en base. Une colonne a 0 % n'est pas
    un signal — les lignes d'avant ne pouvaient pas la porter — mais une colonne
    a 0 % sur les lignes **posterieures a sa propre migration** en est un.

    Se tait sur une colonne dont aucune ligne n'est encore passee : un chantier
    livre ce matin n'a rien a prouver avant le premier import.
    """
    found: list[ColumnGap] = []
    with connect(settings) as conn:
        for audited in AUDITED_COLUMNS:
            row = conn.execute(
                "SELECT applied_at FROM schema_migrations WHERE version = ?",
                (audited.version,),
            ).fetchone()
            if row is None or not row["applied_at"]:
                continue
            since = str(row["applied_at"])
            state = conn.execute(
                f"SELECT COUNT(*) AS picks, COUNT(DISTINCT session_id) AS sessions, "  # noqa: S608
                f"       SUM({audited.column} IS NOT NULL) AS remplies "
                "FROM picks WHERE created_at > ?",
                (since,),
            ).fetchone()
            if not state["picks"] or state["remplies"]:
                continue
            found.append(
                ColumnGap(
                    column=audited.column,
                    label=audited.label,
                    since=since,
                    sessions=int(state["sessions"]),
                    picks=int(state["picks"]),
                )
            )
    return found


@dataclass
class AxisGap:
    """Un axe dont l'addition ne retombe pas sur le total tranche.

    Un regroupement qui perd des lignes en silence est la panne la plus
    couteuse que cette page puisse avoir : elle ne se voit pas, elle fait
    seulement baisser un compte que personne ne recompte. Chaque axe declare
    donc ce qu'il laisse dehors — `uncategorised`, `unlabelled_angle`, etc. — et
    ce controle verifie que la somme retombe juste.
    """

    axis: str
    missing: int
    reason: str

    @property
    def line(self) -> str:
        return f"{self.axis} : {self.missing} sélection(s) hors du compte — {self.reason}"


@dataclass
class Family:
    """Une famille de marches, et le detail fin qu'elle regroupe.

    Le detail est **entier**, sans le seuil qui filtre la carte « Par marché » :
    c'est tout l'interet du groupement — un libelle vu deux fois ne dit rien
    seul, il dit quelque chose sous sa famille, et la somme du deplie doit
    tomber juste sur le total de la ligne.
    """

    rates: RateRow
    markets: list[RateRow] = field(default_factory=list)


@dataclass
class Analysis:
    """Ce que vaut l'analyse, jouee ou non.

    Distincte de `stats()`, qui ne mesure que les paris reellement poses. Ici
    on juge la **selection** : avait-elle raison ? Une selection ecartee dont
    le resultat est connu compte donc autant qu'une selection jouee.

    `played` et `skipped` se lisent ensemble : si les selections ecartees
    gagnent aussi souvent que celles jouees, le tri n'apporte rien. C'est la
    seule facon de mesurer ce que vaut le geste de trier, et elle ne coute
    qu'un resultat saisi sur une ligne qu'on n'a pas jouee.
    """

    settled: int = 0
    #: Journees d'analyse distinctes couvertes par ces selections tranchees.
    #: Comptee comme dans `feedback()` : la journee ou la decision a ete prise,
    #: et non celle du match — deux paris pris dans la meme seance restent une
    #: seule seance, meme a cheval sur minuit.
    days: int = 0
    minimum: int = ANALYSIS_MIN_TOTAL
    minimum_days: int = ANALYSIS_MIN_DAYS
    minimum_rows: int = ANALYSIS_MIN_ROWS
    by_tier: list[RateRow] = field(default_factory=list)
    by_confidence: list[RateRow] = field(default_factory=list)
    #: Le meme axe, sur le cran **calcule** par l'application. Tenu a part de
    #: `by_confidence` et jamais fusionne : les deux colonnes ne portent pas sur
    #: la meme population tant que l'ancienne n'a pas de bloc structure, et les
    #: melanger ferait lire un ecart de taux la ou il n'y a qu'un ecart de
    #: couverture.
    by_confidence_computed: list[RateRow] = field(default_factory=list)
    #: L'accord entre les deux crans. Voir `Notation`.
    notation: Notation = field(default_factory=lambda: Notation())
    #: L'ecart entre l'emoji colle et le palier recalcule. Depuis que le journal
    #: d'analyse recalcule, cet emoji n'est plus lu comme un classement : il
    #: devient un signal d'adherence du cadre.
    tier_drift: TierDrift = field(default_factory=lambda: TierDrift())
    #: Les selections ramenees en lecture faute de dossier ouvert. Tenu **hors**
    #: de `notation` : ce sont deux fautes distinctes, et les melanger ferait
    #: designer toujours la meme clause du gabarit.
    override: Override = field(default_factory=lambda: Override())
    by_sport: list[RateRow] = field(default_factory=list)
    #: Niveau de competition. Un Grand Chelem et un 250 ne se jouent ni sur le
    #: meme format ni contre les memes joueurs, une Ligue des champions et une
    #: deuxieme division non plus : leurs taux ne se melangent pas.
    by_category: list[RateRow] = field(default_factory=list)
    #: Selections tranchees qu'aucun niveau ne porte — competition non classee,
    #: ou selection sans match rattache. **Comptees, jamais mises en barre** :
    #: un taux sur cet ensemble melangerait des competitions qui n'ont en commun
    #: que de n'avoir pas ete saisies, ce qui ne dit rien des matchs. Le compte,
    #: lui, ferme l'addition — sans lui, des selections quittaient le
    #: regroupement sans qu'une seule ligne ne le signale, et c'est ainsi que
    #: tout le football est reste invisible cent paris durant.
    uncategorised: int = 0
    by_market: list[RateRow] = field(default_factory=list)
    #: Marches groupes par famille. Rendu **avant** le detail fin : neuf
    #: regroupements dont six vus une seule fois ne se lisaient pas, la ou trois
    #: familles passent le seuil.
    by_family: list[Family] = field(default_factory=list)
    #: Libelles de marche qu'aucune famille ne porte. Jamais ranges d'office
    #: dans « Autre » : le regroupement se lirait comme une decision alors que
    #: ce serait un oubli, et un marche nouveau qu'on essaie serait le premier a
    #: disparaitre dans le fourre-tout.
    unclassified_markets: int = 0
    #: **Sur quoi** la selection reposait, et non de quoi elle avait l'air. Les
    #: autres axes sont des etiquettes de forme — un palier est une bande de
    #: cote, un marche un libelle. Ces deux-la portent la seule question dont la
    #: reponse changerait la methode : une selection adossee a un fait date de
    #: niveau 1-2 tient-elle mieux qu'une lecture ?
    by_angle: list[RateRow] = field(default_factory=list)
    by_source: list[RateRow] = field(default_factory=list)
    #: Les memes axes, mais sur le **residu au prix** plutot que sur le taux
    #: brut. Le taux brut ne compare pas ce qu'il pretend comparer : deux
    #: regroupements qui ne jouent pas aux memes prix ne se departagent pas par
    #: leur frequence de reussite. Rendus **a cote** des taux, jamais a la
    #: place — le taux dit combien de fois ca tombe, ce qui reste lisible.
    residual_by_angle: list[ResidualRow] = field(default_factory=list)
    residual_by_confidence: list[ResidualRow] = field(default_factory=list)
    residual_by_market: list[ResidualRow] = field(default_factory=list)
    #: Selections tranchees qui ne portent ni l'une ni l'autre. Meme role que
    #: `uncategorised` : fermer l'addition plutot que de laisser des lignes
    #: quitter un regroupement sans un mot. Les cent premieres selections sont
    #: dans ce cas, les colonnes n'existant pas encore.
    unlabelled_angle: int = 0
    unlabelled_source: int = 0
    #: Selections tranchees sans confiance annoncee. Le regroupement les ecarte
    #: — une confiance absente n'est pas un niveau de l'echelle — et le compte
    #: ferme l'addition, comme partout ailleurs.
    unlabelled_confidence: int = 0
    #: Selections tranchees dont le libelle de marche est vide. Impossible par
    #: `add_pick`, qui l'exige ; comptees quand meme, parce qu'un import ancien
    #: ou une base modifiee a la main les ferait sinon disparaitre des deux
    #: regroupements de marche sans un mot.
    unlabelled_market: int = 0
    #: Total lu **directement dans `picks`**, sans aucune jointure ni filtre.
    #: C'est le seul chiffre de la page qu'aucun regroupement ne peut faire
    #: baisser : il sert de temoin a tous les autres.
    recorded: int = 0
    #: Colonnes restees nulles sur tout ce qui a ete importe depuis leur
    #: naissance. **Meme defaut qu'une densite a zero** : un echec qui produit
    #: exactement la meme sortie qu'un succes, et que rien ne distinguait d'un
    #: chantier livre mais pas encore exerce.
    column_gaps: list[ColumnGap] = field(default_factory=list)
    #: Axes dont l'addition ne retombe pas sur `recorded`. Vide en marche
    #: normale ; non vide, la page le dit en clair plutot que d'afficher un
    #: denominateur amputé.
    gaps: list[AxisGap] = field(default_factory=list)
    #: Une ligne par session, la plus recente d'abord. Tenu hors de `groups` :
    #: les autres axes decoupent **les selections**, celui-ci decoupe le
    #: **travail**, et il porte une grandeur qu'aucun autre ne porte — le taux
    #: de selection. Le passer au detecteur de recouvrement n'aurait rien dit :
    #: une session ne recouvre par construction aucun palier ni aucun sport.
    by_session: list[SessionRate] = field(default_factory=list)
    played: RateRow = field(default_factory=lambda: RateRow("played", "Jouées"))
    skipped: RateRow = field(default_factory=lambda: RateRow("skipped", "Écartées"))
    #: Marches ecartes faute d'echantillon. Annonce plutot que tue en silence.
    hidden_markets: int = 0
    #: Selections tranchees dont la cote vient d'un book de **reference** et dont
    #: la cote obtenue n'a pas ete relevee. Elles sortent des taux **par bande de
    #: cote** — leur palier est bati sur un prix qu'on n'aurait pas obtenu — et
    #: de ceux-la seulement. Le compte ferme l'addition, comme partout ailleurs.
    quarantined: int = 0
    #: Regroupements de deux axes differents qui portent les memes selections.
    #: Signales, jamais masques : le bloc reste affiche, c'est sa redondance qui
    #: est dite. Le masquer choisirait a la place du lecteur lequel des deux
    #: axes est le bon, et rien ici ne permet de trancher ca.
    overlaps: list[Overlap] = field(default_factory=list)
    #: Selections dont l'angle declare est une **maniere** et dont le marche
    #: rendu appartient a la famille `Issue`. Le prompt demandait a l'analyse de
    #: compter ces lignes elle-meme ; les deux colonnes etant en base, le compte
    #: se fait ici — et se mesure enfin dans le temps.
    conflicts: Conflict = field(default_factory=lambda: Conflict())
    #: Paires de regroupements qui se recouvrent **partiellement**. Comptees,
    #: jamais enumerees : trente avertissements faibles reproduiraient le defaut
    #: que cette page a mis huit lots a corriger. Le detail vit dans la matrice.
    partial_overlaps: int = 0
    #: La matrice de recouvrement inter-axes, pour le depliant.
    overlap_matrix: list[tuple[str, str, list[tuple[str, str, float]]]] = field(
        default_factory=list
    )
    #: Horodatage de la lecture. **Une analyse est datee**, et ce n'est pas une
    #: precaution de style : l'axe « niveau de competition » est passe de
    #: p = 0,0443 a p = 0,0195 sur **six resultats saisis**, la base etant servie
    #: en continu. Un verdict qui bouge d'un facteur deux sur six saisies n'est
    #: pas un verdict, et la page doit dire de quand il date.
    as_of: str = ""
    #: Le residu au prix des selections dont l'anteriorite est **etablie**.
    #: C'est le chiffre de tete de la page.
    residual: Residual = field(default_factory=lambda: Residual(observed=0))
    #: Le meme, sur les selections dont l'anteriorite **ne l'est pas**. Tenu a
    #: part et **jamais additionne** : les deux populations ne mesurent pas la
    #: meme chose, et leur difference est justement le diagnostic.
    residual_late: Residual = field(default_factory=lambda: Residual(observed=0))
    #: Selections tranchees sans cote enregistree — elles sortent des deux
    #: residus et de ceux-la seulement.
    unpriced: int = 0
    #: `1/cote` des selections tranchees, **groupees par match**. Sert la borne
    #: conservatrice du residu : deux selections sur la meme rencontre ne sont
    #: pas deux observations, et la loi exacte suppose l'independance.
    residual_clusters: list[list[float]] = field(default_factory=list)
    #: Les rencontres qui portent **plus d'une selection tranchee**, nommees.
    #:
    #: Le compte seul disait qu'il faut elargir les intervalles ; il ne disait
    #: pas **ou aller regarder**. Or c'est la seule chose actionnable : deux
    #: lectures d'un meme rapport de forces se relisent, et le prompt n'autorise
    #: une seconde ligne que sur un angle reellement independant. Mesure du
    #: 17/08/2026 — sur le lot du 16/08, `Lens – Paris Saint Germain` porte
    #: `PSG O1.5 Eq. buts` et `Lens +0.5 Handicap`, deux lectures **opposees** du
    #: meme rapport de forces, notees l'une gagnante et l'autre perdante.
    clustered_events: list[tuple[str, int]] = field(default_factory=list)
    #: Selections tranchees **ecartees de toute la page** faute d'anteriorite
    #: etablie. Comptees et annoncees : une page qui perd un tiers de son volume
    #: sans le dire est pire que celle qui le melangeait.
    without_antecedence: int = 0
    #: Les memes, **par motif declare**. Un seul compte les melangeait, et les
    #: trois cas n'appellent pas la meme lecture ni le meme geste :
    #:
    #: · `differee` — la decision est anterieure, seule la **saisie** est
    #:   tardive. L'etiquette est valide, c'est le **prix** qui est douteux ;
    #: · `live` — pari reellement pris en cours de match : les deux invalides ;
    #: · aucun motif — la ligne est anterieure a la garde d'ecriture
    #:   (migration 034, 11/08/2026), et **rien ne dira jamais laquelle des deux
    #:   c'etait**. Ce n'est pas un defaut a reparer, c'est une population close.
    #:
    #: Mesure du 17/08/2026 sur les 230 selections tranchees : 52 tardives, dont
    #: 15 `differee`, 0 `live` et 37 sans motif. **Aucune ne manque d'horodatage
    #: — les 230 portent leur `created_at` et le `commence_time` de leur match**,
    #: ce qui ecarte la piste d'un defaut de collecte ou de fuseau.
    late_by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def late_reasons(self) -> list[tuple[str, int]]:
        """Les motifs de saisie tardive, libelles et ranges du plus frequent.

        Le libelle se resout depuis `LATE_REASONS` et **jamais cote gabarit** :
        deux ecritures du meme vocabulaire divergent au premier ajustement, et un
        libelle recopie dans un template ne casse rien le jour ou la cle change —
        il disparait. Meme regle que `Pick.late_label`.
        """
        return [
            (
                LATE_REASONS.get(reason)
                or "Aucun motif déclaré — antérieure à la garde d'écriture, population close",
                count,
            )
            for reason, count in sorted(self.late_by_reason.items(), key=lambda item: -item[1])
        ]

    #: Les taux **par origine du prix**. Il remplace la quarantaine : au lieu
    #: d'ecarter les lignes servies par un book de reference, on regarde si elles
    #: se comportent autrement. Une mesure, jamais un filtre.
    by_price_source: list[RateRow] = field(default_factory=list)
    #: Selections tranchees dont l'origine du prix n'est pas renseignee — les 103
    #: anterieures a la migration 030, qui ne se devinent pas apres coup.
    unlabelled_price_source: int = 0
    #: `(reussites, tranchees)` des deux paliers les plus employes **a l'interieur
    #: du niveau de confiance le plus fourni**. C'est l'ecart **residuel** entre
    #: les deux echelles, celui qui dit si la seconde ajoute quelque chose — bien
    #: plus tenu que l'ecart brut, et il faut les donnees croisees pour le voir.
    conditional: list[tuple[int, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.settled == 0

    @property
    def carried_rows(self) -> list[RateRow]:
        """Les lignes que la page **porte**. Toutes les autres sont repliees.

        **Un axe a deux niveaux ne porte qu'une ligne.** « conf 4 contre le
        reste » et « conf 3 contre le reste » y sont litteralement le meme test —
        memes donnees retournees, meme p-valeur — et les afficher tous deux
        presenterait un contraste comme deux decouvertes. C'est la meme regle
        que l'omnibus, poussee jusqu'a l'affichage : un axe est une partition.
        """
        portees: list[RateRow] = []
        for rows in self.groups:
            retenues = [row for row in rows if row.carried]
            peuplees = [row for row in rows if row.settled]
            if len(retenues) == 2 and len(peuplees) == 2:
                # Le mieux classe en premier : l'ecart se lit alors dans le sens
                # ou la phrase le raconte.
                retenues = [max(retenues, key=lambda row: row.rate or 0.0)]
            portees.extend(retenues)
        return portees

    @property
    def folded_rows(self) -> int:
        return sum(len(rows) for rows in self.groups) - len(self.carried_rows)

    @property
    def horizons(self) -> list[Horizon]:
        """Les questions ouvertes, et la distance a laquelle elles se ferment.

        **C'est le seul texte utile de la section des regroupements.** Elle ne
        conclut rien aujourd'hui : ce qui s'y lit est la progression vers un
        moment ou elle conclura, ou la constatation qu'elle n'y arrivera pas.
        """
        found: list[Horizon] = []
        for libelle, rows in (("le palier", self.by_tier), ("la confiance", self.by_confidence)):
            paire = sorted((row for row in rows if row.settled), key=lambda row: -row.settled)[:2]
            if len(paire) < 2:
                continue
            haut, bas = sorted(paire, key=lambda row: -(row.rate or 0.0))
            besoin = required_sample(haut.rate or 0.0, bas.rate or 0.0)
            if besoin is not None:
                found.append(
                    Horizon(
                        question=f"{libelle} départage ses deux niveaux les plus employés",
                        have=min(haut.settled, bas.settled),
                        need=besoin,
                        detail=f"{haut.label} contre {bas.label}",
                    )
                )
        return sorted(found, key=lambda item: item.sessions)

    @property
    def scales(self) -> Equivalence | None:
        """Faut-il deux echelles d'etiquetage, ou une seule ?

        **La question ne se pose pas en sessions restantes.** « Quarante-neuf
        sessions » repond a *quand saurai-je*, ce qui depend du rythme de saisie
        et non des donnees. La question produit est : *quel ecart residuel
        justifierait le cout d'un second axe ?* — decidee d'avance, une fois,
        et insensible a l'echantillon.

        L'ecart mesure est le **residuel**, dans la strate la plus fournie de
        l'autre axe : a confiance fixee, le palier separe-t-il encore ? L'ecart
        brut recopierait ce que l'axe dit deja tout seul.
        """
        if len(self.conditional) < 2:
            return None
        first, second = self.conditional[0], self.conditional[1]
        return Equivalence(first=first, second=second)

    @property
    def clustered_selections(self) -> int:
        """Selections tranchees partageant un match avec une autre."""
        return sum(len(group) - 1 for group in self.residual_clusters if len(group) > 1)

    def clustered_p_value(self, margin: float = 0.0) -> float:
        """La borne conservatrice du residu : les issues d'un meme match tombent
        ensemble. Sur les donnees reelles, quatre des cinq paires l'ont fait."""
        return clustered_p_value(self.residual_clusters, self.residual.observed, margin)

    @property
    def ordered_scales(self) -> list[tuple[str, float]]:
        """Les echelles d'etiquetage et leur tendance ordinale.

        **La seule question qu'aucun autre bloc ne pose.** L'omnibus dit si les
        crans separent, la fragilite dit a quel point ils tiennent — ni l'un ni
        l'autre ne dit **dans quel sens**. Une echelle inversee separerait tout
        autant, et c'est exactement ce que font les selections ecartees :
        l'ordre y est **inverse** (p = 0,90 et 0,93), quand il tient sur la
        population filtree (p = 0,013 et 0,0001).

        Les lignes arrivent deja rangees du cran le plus eleve au plus bas.
        """
        found = []
        for libelle, rows in (
            ("la confiance annoncée", self.by_confidence),
            ("le palier", self.by_tier),
        ):
            value = ordinal_trend([(row.won, row.settled) for row in rows])
            if value is not None:
                found.append((libelle, value))
        return found

    @property
    def as_of_label(self) -> str:
        """« 11/08 20:40 » — l'heure de lecture, en heure locale."""
        if not self.as_of:
            return ""
        return _local(self.as_of, "Europe/Paris").strftime("%d/%m %H:%M")

    @property
    def consistent(self) -> bool:
        """Tout ce qui est tranche en base est compte ou **declare** ici.

        Le temoin reste le compte brut ; les selections ecartees faute
        d'anteriorite s'y ajoutent explicitement. Une page qui perd un tiers de
        son volume doit le faire retomber juste, pas le faire disparaitre.
        """
        return self.settled + self.without_antecedence == self.recorded and not self.gaps

    @property
    def comparable(self) -> bool:
        """Vrai si les deux cotes de la comparaison ont de quoi etre lues."""
        return self.played.settled > 0 and self.skipped.settled > 0

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un regroupement se lise comme une tendance.

        Meme regle que `Feedback.enough`, et il faut les deux conditions : assez
        de selections **et** assez de journees. Ce que la page en fait differe —
        elle continue d'afficher, en disant ce que ces taux decrivent vraiment.
        """
        return self.settled >= self.minimum and self.days >= self.minimum_days

    @property
    def groups(self) -> tuple[list[RateRow], ...]:
        """Tous les regroupements de la vue, dans l'ordre ou la page les rend."""
        return (
            self.by_tier,
            self.by_confidence,
            self.by_sport,
            self.by_category,
            self.by_market,
            self.by_angle,
            self.by_source,
            [entry.rates for entry in self.by_family],
        )

    @staticmethod
    def _compare(rows: list[RateRow]) -> Comparison | None:
        """Les deux plus gros regroupements d'un axe, s'ils se lisent.

        Les deux plus gros et non deux cles fixees : c'est ce que l'oeil compare
        sur un graphique a barres, et la paire suit le lot au lieu de rester
        collee a des paliers qui pourraient ne plus etre les plus employes.
        """
        candidates = sorted((row for row in rows if row.settled), key=lambda row: -row.settled)
        if len(candidates) < 2:
            return None
        first, second = candidates[0], candidates[1]
        # Le meilleur taux en premier : l'ecart se lit alors dans le sens ou la
        # phrase le raconte, « X fait mieux que Y ».
        if (first.rate or 0) < (second.rate or 0):
            first, second = second, first
        return Comparison(
            left=first,
            right=second,
            required=required_sample(first.rate or 0.0, second.rate or 0.0),
        )

    @property
    def tier_comparison(self) -> Comparison | None:
        return self._compare(self.by_tier)

    @property
    def confidence_comparison(self) -> Comparison | None:
        return self._compare(self.by_confidence)

    @property
    def settled_events(self) -> int:
        """Matchs distincts derriere les selections tranchees.

        Compte sur `overall`, donc sur toutes les selections a la fois : c'est
        l'effectif independant de la page entiere. Trois lignes sur le meme
        match — Vainqueur, handicap jeux, total de jeux — sont une seule issue
        comptee trois fois, le joueur qui gagne en deux sets les faisant passer
        ensemble.
        """
        return self.overall.units

    @property
    def clustered_rows(self) -> int:
        """Regroupements ou des selections se partagent des matchs."""
        return sum(1 for rows in self.groups for row in rows if row.clustered)

    @property
    def overall(self) -> RateRow:
        """Les deux populations reunies : le taux de l'analyse, tous picks confondus.

        Deduit de `played` et `skipped` plutot que compte a part : deux
        comptages du meme ensemble finiraient par diverger, et il faudrait
        alors arbitrer lequel dit vrai.
        """
        total = RateRow(key="all", label="Toutes")
        for entry in (self.played, self.skipped):
            total.merge(entry)
        return total


#: Famille de marches qui ne retient d'un raisonnement que le nom d'un camp.
#: Un angle declare sur une **maniere** — un rythme, une usure, un desequilibre
#: au service — qui sort en `Issue` a perdu en route ce qu'il avait compris.
ISSUE_FAMILY = "issue"


@dataclass
class Conflict:
    """Selections dont l'angle declare et le marche rendu ne s'accordent pas.

    Le prompt demandait a l'analyse de s'auto-auditer : « compte tes lignes
    avant de rendre, si plus de la moitie du tableau porte sur le vainqueur,
    relis-les avec leur colonne Type ». Or les deux colonnes sont **en base** —
    `angle` depuis la migration 026, la famille du marche depuis la 027 — et le
    conflit se detecte en une requete. Une regle deterministe laissee au modele
    coute des tokens, se refait a chaque session et ne se mesure jamais.

    **C'est une mesure de la qualite du rendu, jamais un blocage** : un angle de
    maniere rendu en vainqueur reste une selection valable, simplement moins
    fidele a son propre raisonnement. On la compte, on ne la refuse pas.
    """

    #: Selections tranchees portant `maniere` et rendues dans la famille `Issue`.
    count: int = 0
    #: Selections tranchees portant `maniere`, quel que soit le marche rendu.
    labelled: int = 0
    #: Le meme compte par sport, puis par session — « dans le temps » etant la
    #: seule facon de voir si la consigne porte, ou si elle s'use.
    by_sport: list[tuple[str, int, int]] = field(default_factory=list)
    by_session: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def known(self) -> bool:
        """Au moins une selection declare son angle. Sinon rien ne se mesure."""
        return self.labelled > 0

    @property
    def rate(self) -> float | None:
        return None if not self.labelled else self.count / self.labelled


def conflicts(rows: list[Any], families: dict[str, str], tz: str) -> Conflict:
    """Compte les selections `maniere` rendues dans la famille `Issue`.

    Calcule **a la lecture**, jamais recopie sur la selection : c'est la regle du
    module — reclasser un marche reclasse tout l'historique, sans migration ni
    reprise de donnees. Stocker le conflit figerait la taxonomie du jour ou la
    ligne a ete saisie.
    """
    report = Conflict()
    par_sport: dict[str, list[int]] = {}
    par_session: dict[str, list[int]] = {}
    for row in rows:
        if row["result"] not in ("win", "loss") or row["angle"] != ANGLE_MANNER:
            continue
        report.labelled += 1
        heurte = family_of(row["market"] or "", families) == ISSUE_FAMILY
        report.count += 1 if heurte else 0
        sport = _column(row, "sport_key") or NO_SPORT
        jour = _local(str(row["created_at"]), tz).strftime("%d/%m")
        for cle, table in ((sport, par_sport), (jour, par_session)):
            compte = table.setdefault(cle, [0, 0])
            compte[0] += 1 if heurte else 0
            compte[1] += 1
    report.by_sport = sorted((cle, *valeurs) for cle, valeurs in par_sport.items())
    report.by_session = [(cle, *par_session[cle]) for cle in sorted(par_session, reverse=True)]
    return report


@dataclass
class Overlap:
    """Deux regroupements de deux axes differents portant les memes selections.

    La page les presente cote a cote comme deux observations independantes,
    alors qu'ils comptent les memes paris : « Tennis 46 % » et « Masters 1000
    46 % » sont les memes 37 selections, 100 % du tennis en base ayant ete joue
    sur le Canadian Open. Le bloc par niveau de tournoi n'apprend alors rien de
    plus que le bloc par sport.
    """

    left_axis: str
    left_label: str
    right_axis: str
    right_label: str
    shared: int
    #: Indice de Jaccard, garde pour que la note dise **a quel point**.
    share: float = 1.0

    @property
    def note(self) -> str:
        return (
            f"{self.left_label} et {self.right_label} décrivent les mêmes {self.shared} sélections"
        )


def _with_complements(axes: tuple[list[RateRow], ...]) -> None:
    """Donne a chaque ligne le reste de son axe, et a chaque axe son verdict.

    Ecrit une seule fois et applique a `Analysis.groups` en bloc : un
    remplissage axe par axe aurait ete oublie au premier axe ajoute, et une
    ligne sans complement se lit comme une ligne qui n'affirme rien — une panne
    qui ne casse pas, elle fait seulement disparaitre.

    **La correction de multiplicite se fait ici, entre axes**, parce que c'est
    le seul endroit qui les voit tous. Huit axes testes a 5 % laissent attendre
    une « decouverte » par pur hasard ; les corriger **par ligne** aurait au
    contraire compte chaque partition N fois et gonfle le nombre d'essais — la
    raison meme pour laquelle l'omnibus existe.
    """
    verdicts: list[tuple[list[RateRow], float]] = []
    for rows in axes:
        total_won = sum(row.won for row in rows)
        total_settled = sum(row.settled for row in rows)
        cells = [(row.won, row.settled) for row in rows]
        verdict = omnibus(cells)
        for row in rows:
            row.complement = (total_won - row.won, total_settled - row.settled)
            row.axis_separates = verdict is not None and verdict.separates
            row.axis_cells = cells
        if verdict is not None:
            verdicts.append((rows, verdict.p_value))

    retenus = benjamini_hochberg([value for _, value in verdicts])
    seuil = sorted(value for _, value in verdicts)[retenus - 1] if retenus else -1.0
    for rows, value in verdicts:
        for row in rows:
            row.axis_survives = value <= seuil


def _overlaps(axes: list[tuple[str, list[RateRow]]]) -> tuple[list[Overlap], int]:
    """Regroupements de deux axes distincts qui decrivent le meme echantillon.

    Compare des **ensembles d'identifiants** et non des comptes : deux
    regroupements de 37 lignes chacun peuvent n'avoir aucune selection commune,
    et un taux identique de part et d'autre serait alors une coincidence.

    Rend les recouvrements **forts**, qui se nomment, et le **compte** des
    partiels, qui ne se nomment pas : trente avertissements faibles
    reproduiraient le defaut que cette page a mis huit lots a corriger. Le
    detail vit dans la matrice, sous le pli.

    Le seuil de lecture s'applique ici aussi : deux regroupements d'une seule
    selection partagee se recouvrent a 100 % sans rien dire.
    """
    found: list[Overlap] = []
    partial = 0
    for index, (left_axis, left_rows) in enumerate(axes):
        for right_axis, right_rows in axes[index + 1 :]:
            for left in left_rows:
                for right in right_rows:
                    if min(len(left.members), len(right.members)) < ANALYSIS_MIN_ROWS:
                        continue
                    share = jaccard(left.members, right.members)
                    if share >= COLLINEAR_SHARE:
                        found.append(
                            Overlap(
                                left_axis=left_axis,
                                left_label=left.label,
                                right_axis=right_axis,
                                right_label=right.label,
                                shared=len(left.members & right.members),
                                share=share,
                            )
                        )
                    elif share >= PARTIAL_SHARE:
                        partial += 1
    return found, partial


def overlap_matrix(
    axes: list[tuple[str, list[RateRow]]],
) -> list[tuple[str, str, list[tuple[str, str, float]]]]:
    """La matrice de recouvrement inter-axes, pour le depliant.

    Chaque paire d'axes rend ses couples de lignes et leur Jaccard. C'est le
    detail que le compte des partiels resume : il existe pour qui veut verifier,
    il ne se lit pas de haut en bas.
    """
    matrix = []
    for index, (left_axis, left_rows) in enumerate(axes):
        for right_axis, right_rows in axes[index + 1 :]:
            cells = [
                (left.label, right.label, jaccard(left.members, right.members))
                for left in left_rows
                for right in right_rows
                if min(len(left.members), len(right.members)) >= ANALYSIS_MIN_ROWS
            ]
            if any(share >= PARTIAL_SHARE for _, _, share in cells):
                matrix.append((left_axis, right_axis, sorted(cells, key=lambda c: -c[2])))
    return matrix


@dataclass
class MixRow:
    """Un niveau d'une echelle d'etiquetage, et la part du volume qu'il porte."""

    key: str
    label: str
    count: int = 0
    total: int = 0
    #: Sessions ou ce niveau n'a **pas** ete employe, sur celles qui portent au
    #: moins une selection. C'est la mesure de la **vacance** : une part de
    #: volume a zero dit qu'un niveau ne sert jamais, elle ne dit pas si c'est
    #: parce qu'un lot ne l'offrait pas ou parce qu'on ne l'a jamais cherche.
    #: Compte des sessions, la ou la part compte des lignes.
    absent_sessions: int = 0

    @property
    def share(self) -> float | None:
        return None if self.total == 0 else self.count / self.total

    @property
    def share_label(self) -> str:
        return "—" if self.share is None else f"{self.share * 100:.0f} %"


@dataclass
class Mix:
    """Repartition des selections sur une echelle d'etiquetage.

    **Le seul bloc de la page qui ne parle pas de resultats**, et le seul qui
    soit concluant au volume actuel : il decrit un comportement — comment je
    note — et non des issues. Il ne souffre donc ni du garde-fou de volume ni
    de celui d'etalement, qui portent tous deux sur des taux.

    Toutes les selections y comptent, tranchees ou non : une confiance annoncee
    est un geste pose au moment de l'analyse, et le resultat n'y change rien.
    """

    key: str
    label: str
    rows: list[MixRow] = field(default_factory=list)
    total: int = 0
    #: Selections ne portant aucune valeur sur cette echelle. Tenues hors du
    #: total : ne pas etiqueter n'est pas un niveau de l'echelle.
    unlabelled: int = 0
    #: Sessions portant au moins une selection — le denominateur de la vacance.
    sessions: int = 0

    @property
    def levels(self) -> int:
        return len(self.rows)

    @property
    def used(self) -> int:
        """Niveaux effectivement employes au moins une fois."""
        return sum(1 for row in self.rows if row.count)

    @property
    def top(self) -> list[MixRow]:
        """Les niveaux les plus employes, du plus gros au plus petit."""
        return sorted(self.rows, key=lambda row: -row.count)[:CONCENTRATION_LEVELS]

    @property
    def top_share(self) -> float:
        return 0.0 if self.total == 0 else sum(row.count for row in self.top) / self.total

    @property
    def top_share_label(self) -> str:
        return f"{self.top_share * 100:.0f} %"

    @property
    def top_labels(self) -> str:
        return " et ".join(row.label for row in self.top if row.count)

    @property
    def concentrated(self) -> bool:
        """L'echelle est employee comme si elle comptait moins de crans.

        Deux conditions : la part concentree, et une echelle qui compte
        vraiment plus de niveaux que ceux-la. Sur une echelle a deux crans, la
        meme part ne dirait rien — il n'y a pas d'autre facon de s'en servir.
        """
        return (
            self.total > 0
            and self.levels > CONCENTRATION_LEVELS
            and self.top_share > CONCENTRATION_SHARE
        )


#: Version de l'echelle de confiance en vigueur, ecrite sur la session au moment
#: ou elle emet son premier prompt.
#:
#: **Elle ne sert a rien aujourd'hui, et c'est deliberе.** Une courbe de
#: fiabilite tracee a travers un changement d'echelle ne mesure rien : il faudra
#: savoir, session par session, contre quoi la confiance annoncee etait notee.
#: Le champ passe donc maintenant, pour que ces sessions-ci soient deja datees
#: quand la question se posera — une echelle ne se reconstitue pas apres coup.
#:
#: Le regime actuel est celui de la migration 032 : des bandes exprimees en
#: **ecart au taux global observe**, donc sans ancrage absolu. C'est ce que ce
#: nom dit, et rien de plus.
SCALE_VERSION = "relatif-032"

#: Mise en service de la garde d'anteriorite (migration 034).
#:
#: **La borne a partir de laquelle une population est propre par construction**
#: plutot que par filtrage. Avant elle, rien n'empechait d'enregistrer une
#: selection apres le coup d'envoi de son match, et 37 des 110 selections
#: tranchees sont dans ce cas — definitivement. Toute serie qui traverse cette
#: date melange deux regimes de collecte et doit le marquer.
GUARD_IN_SERVICE = "2026-08-11"

#: Le bloc de taux est-il retenu. Tant que c'est vrai, **aucun taux de reussite
#: ne part dans le prompt**, quel que soit le recul accumule.
#:
#: Des qu'un agregat de resultats entre dans le prompt, l'analyse lit son propre
#: tableau de bord : les selections suivantes cessent d'etre des tirages
#: independants de ce qui les mesure, et une categorie annoncee a 0/7 cesse
#: d'etre produite — donc cesse d'etre mesurable. Ce n'est pas une precaution
#: theorique : **9 prompts de 3 sessions l'ont fait**, quand les seuils valaient
#: encore 10 et 4, et l'un d'eux annoncait « confiance 4 — 10/15, 67 % » juste
#: avant que soient produites les etiquettes qu'on mesure aujourd'hui a 82 %.
#: Ces 3 sessions fournissent la majorite de la population propre.
#:
#: **Une constante et non un reglage.** Un seuil se baisse par inadvertance ; le
#: garde-fou d'origine etait justement un couple de seuils, et il a cede sans
#: que personne le decide. Rouvrir le bloc demande donc de modifier le code.
FEEDBACK_SUSPENDED = True

#: La date a laquelle ce drapeau se **re-decide**, et non celle ou il tombe.
#:
#: **Ce drapeau est la demonstration qu'un commentaire ne tient pas une
#: decision.** Sa note dit depuis des mois que sa bascule ne se produira pas
#: toute seule ; il est toujours leve, et plus personne ne sait s'il l'est encore
#: **volontairement** ou seulement par oubli. Les deux etats se ressemblent trait
#: pour trait — le defaut caracteristique de ce projet, applique cette fois a une
#: decision plutot qu'a une donnee.
#:
#: Un test devient donc rouge passe cette date. **Et la question qu'il pose n'est
#: pas « faut-il lever ce drapeau »** : celle-la fait choisir entre lever et
#: reporter, deux reponses qui supposent toutes deux un provisoire. La question
#: est *ce drapeau a-t-il jamais eu une condition de sortie* — et si la reponse
#: est non, ni le lever ni le reporter ne convient : il faut retirer le mot
#: provisoire et assumer une decision de conception.
#:
#: Meme idiome que `Threshold.remeasure_on` — « un provisoire non date devient
#: permanent par oubli » — augmente de la pile ci-dessous.
FEEDBACK_SUSPENDED_REVIEW = "2026-11-21"

#: Les reports deja consentis : `(date posee, raison)`, **empiles et jamais
#: remplaces**.
#:
#: **Une date remplacee ne garde aucune trace, et c'est ce qui rend la troisieme
#: branche invisible.** Chaque report parait raisonnable pris seul ; trois
#: raisons cote a cote disent ce qu'aucune date ne dit — que le drapeau n'attend
#: pas un evenement, et n'en a jamais attendu. **La liste est le diagnostic**, pas
#: l'historique.
#:
#: Vide aujourd'hui : le rendez-vous est le premier, il n'a jamais ete repousse.
FEEDBACK_SUSPENDED_DEFERRALS: tuple[tuple[str, str], ...] = ()

#: A partir de combien de reports la liste parle d'elle-meme.
#:
#: **Trois, et le nombre n'est pas un seuil de patience** : un report peut suivre
#: un evenement exterieur, deux peuvent etre une coincidence, trois disent que la
#: question posee n'est pas la bonne. Au-dela, le test cesse de proposer le report
#: comme une reponse valable.
DEFERRAL_TELL = 3


def load_bands(settings: Settings | None = None, reference: float | None = None) -> dict[int, Band]:
    """Bandes cibles par niveau de confiance, telles qu'elles sont reglees.

    `reference` est le taux global, en points, contre lequel les ecarts se
    resolvent. Sans lui, les bandes se lisent encore — l'ecran de configuration
    en a besoin — mais aucune ne se compare a un taux.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT level, low, high FROM confidence_bands ORDER BY level"
        ).fetchall()
    return {
        int(row["level"]): Band(
            level=int(row["level"]),
            low=None if row["low"] is None else float(row["low"]),
            high=None if row["high"] is None else float(row["high"]),
            reference=reference,
        )
        for row in rows
    }


def _count_vacancy(block: Mix, rows: list[Any], column: str) -> None:
    """Compte, par niveau, les sessions qui ne l'ont pas employe.

    Le brief demandait de compter les lignes de vacance ecrites sous le tableau ;
    l'application ne lit pas la prose du rendu, seulement le tableau des
    selections. Mais la vacance **est** dans les donnees : un palier qu'aucune
    selection de la session ne porte est un palier laisse vide ce jour-la.
    C'est la meme grandeur, mesuree sans rien parser.

    Une part de volume a zero dit qu'un niveau ne sert jamais. Ce compte-ci dit
    **a quel rythme** : un palier absent de cinq sessions sur cinq n'est pas dans
    le meme etat qu'un palier employe une fois sur deux, et c'est cette
    difference qui decidera un jour de reduire l'echelle.
    """
    sessions: dict[str, set[int]] = {}
    seen: set[int] = set()
    for row in rows:
        session_id = int(row["session_id"])
        seen.add(session_id)
        value = row[column]
        if value is not None:
            sessions.setdefault(str(value), set()).add(session_id)
    block.sessions = len(seen)
    for entry in block.rows:
        entry.absent_sessions = block.sessions - len(sessions.get(entry.key, set()))


@dataclass
class Notation:
    """L'accord entre le cran annonce et le cran calcule.

    **Ce que cet ecart mesure a change de nature, et le libelle doit le dire.**
    Tant que le modele notait seul, on aurait mesure son flair. Depuis qu'il
    declare ses entrees et que l'application applique la table, les deux valeurs
    sortent du **meme** faisceau : leur ecart ne teste plus son jugement, il
    teste s'il applique correctement sa propre table. C'est un **lint sur la
    redaction du gabarit**, pas sur l'analyse — et c'est plus utile ainsi, parce
    qu'une clause ambigue se reecrit quand un jugement ne se corrige pas.

    D'ou `transitions` : un desaccord disperse est du bruit de redaction, un
    desaccord concentre sur un passage — 3 vers 4, 4 vers 5 — designe la clause
    a reprendre.

    **Comptee sur toutes les selections portant les deux crans, tranchees ou
    non**, et c'est la seule carte de la page dans ce cas. Les garde-fous de
    resultat protegent des **taux de reussite**, qui mesurent des issues ; celle-ci
    compare deux declarations, toutes deux connues a l'import. Ecarter les
    selections en attente etait la meme erreur de categorie qu'un taux lu sans son
    prix — meme exemption que `labelling()`, et pour la meme raison.

    Mesure qui l'a rendue visible : les quinze premieres selections a porter les
    deux crans sont **toutes en attente**, et la page ne montrait rien le jour ou
    l'ecart est devenu mesurable pour la premiere fois.

    ## Hypothese datee du 21/08/2026, a retrouver quand la population montera

    Sur les **36** selections de section C portant un cran calcule depuis un bloc
    reel — les 127 autres sont forcees a 1 par l'ecrasement et ne mesurent que le
    collage — l'accord vaut 28, et **les 8 desaccords vont tous dans le meme
    sens** : `4` annonce la ou la table rend `5`. `drift` vaut donc -0,29, deja
    releve sur les 21 premieres paires.

    L'hypothese : le cran 5 serait **sous-peuple par autodepreciation** plutot
    que par rarete des faits de niveau 1. Elle rejoint par un chemin inattendu
    les 4,6 % de volume du cran 5, qu'on attribuait a la source.

    **A n = 36 elle est tres loin de tout seuil, et rien n'en est tire ici.**
    Elle est ecrite a cet endroit precis parce que c'est `transitions` qui la
    trancherait : le jour ou le denominateur passe le seuil de la page, la
    concentration sur `(4, 5)` se lira ou ne se lira pas. Ce qui la rendrait
    caduque n'est pas un raisonnement mais un desaccord qui se disperse.

    Corollaire a ne pas oublier en la relisant : `comparable` doit etre lu
    **hors selections ecrasees** — `_notation` les exclut deja — sans quoi la
    matiere de la mesure serait l'override et non la redaction du gabarit.
    """

    #: Selections portant les deux valeurs. C'est le seul denominateur honnete
    #: du taux de desaccord — les autres n'ont rien a comparer.
    comparable: int = 0
    agreed: int = 0
    #: Annoncees sans bloc structure lisible. **Compte a part et jamais fondu
    #: dans l'accord** : un cran manquant n'est pas un cran d'accord, et c'est
    #: l'erreur que la page a deja payee sur les lignes maigres.
    uncomputed: int = 0
    #: Ecart moyen en crans, signe. Positif : le modele se notait plus haut que
    #: la table ne l'autorise.
    drift: float = 0.0
    #: Les passages en desaccord, du plus frequent au moins : `(annonce, calcule,
    #: compte)`. C'est **le seul champ actionnable du bloc** — il nomme la clause
    #: du gabarit a reecrire.
    transitions: list[tuple[int, int, int]] = field(default_factory=list)
    #: Effectif sous lequel rien ne se conclut. **Le seuil de la page, reutilise
    #: et non redefini** — sous quel compte une repartition ne veut plus rien
    #: dire est une propriete des donnees, pas du bloc qui les affiche. Il
    #: **descend dans l'objet** au lieu d'etre lu d'une constante : une classe
    #: qui va chercher son propre reglage est intestable hors d'une base.
    minimum: int = ANALYSIS_MIN_ROWS

    @property
    def disagreed(self) -> int:
        return self.comparable - self.agreed

    @property
    def dominant(self) -> tuple[int, int, int] | None:
        """Le passage le plus frequent, sous **deux** conditions.

        **L'effectif d'abord.** Sous `minimum`, rien ne se conclut : c'est le
        seuil que la page applique deja a tout regroupement, et cette
        repartition-ci n'y echappe pas. La sortie de ce bloc n'est pas un taux
        mais une **consigne** — reecrire une clause du gabarit — donc la publier
        sur trois desaccords ferait reecrire un texte sur du bruit, ce qui coute
        plus qu'un silence.

        **La concentration ensuite**, et l'inegalite est **stricte** a dessein :
        deux desaccords partages un-un n'en designent aucun, et « au moins la
        moitie » les aurait declares concentres tous les deux. Trouve en ecrivant
        le test — la version large nommait une clause sur un ex aequo.
        """
        if self.comparable < self.minimum or not self.transitions:
            return None
        first = self.transitions[0]
        return first if first[2] * 2 > self.disagreed else None

    @property
    def clause_line(self) -> str:
        """La clause du gabarit a reprendre, quand une seule se designe.

        Vide tant que `dominant` ne rend rien — donc vide sur un effectif court,
        et vide sur un desaccord disperse.
        """
        if self.dominant is None:
            return ""
        declared, computed, count = self.dominant
        return (
            f"{count} des {self.disagreed} désaccords sont un {declared} annoncé que la "
            f"table met à {computed} : c'est cette clause du gabarit qui est ambiguë."
        )

    @property
    def rate(self) -> float | None:
        """Part de desaccord, ou `None` faute de quoi que ce soit a comparer."""
        return self.disagreed / self.comparable if self.comparable else None

    @property
    def line(self) -> str:
        """« 12 sur 30 en desaccord · le modele se note +0.4 cran trop haut »."""
        if not self.comparable:
            return f"aucune sélection ne porte les deux crans · {self.uncomputed} sans bloc lu"
        sens = "trop haut" if self.drift > 0 else "trop bas"
        return (
            f"{self.disagreed} sur {self.comparable} en désaccord · "
            f"le modèle se note {self.drift:+.1f} cran {sens}"
        )


@dataclass
class Exploratory:
    """Ce que valent les selections que la section C n'a pas retenues.

    **Produites sans *exigence* de fait date, jamais « sans fait date ».** C'est
    la regle qui definit la population, pas son contenu, et la nuance n'est pas
    de style : mesure du 21/08/2026 sur les 32 lignes en base, **26 declarent un
    niveau de source numerique** et **5 des 7 blocs lisibles portent des faits**.
    La description d'origine etait donc fausse la ou elle etait verifiable.

    **Un taux faible y est le resultat attendu, pas un defaut de la methode.**
    Cette population est le **temoin** : elle existe pour etre comparee a la
    section C a palier egal, pas pour etre bonne — et c'est ecrit sur la page,
    pas seulement ici. C'est aussi ce qui justifie qu'elle ne recoive aucune
    mise : un montant en ferait un pari plutot qu'un point de mesure, et ce
    motif-la tient quelle que soit la ligne.

    Elle est **entierement separee** de la population principale, et c'est tout
    l'objet du chantier : la comparaison qui donne son sens a la page — une
    selection adossee a un fait date tient-elle mieux qu'une lecture — devient
    definitivement sans reponse si les deux se melangent dans les bandes hautes.
    """

    settled: int = 0
    won: int = 0
    pending: int = 0
    by_tier: list[RateRow] = field(default_factory=list)
    residual: Residual = field(default_factory=lambda: Residual(observed=0, implied=[]))
    minimum_rows: int = ANALYSIS_MIN_ROWS
    #: `(palier, libelle, principale, exploratoire)` la ou les **deux**
    #: populations comptent assez pour etre comparees. Vide autrement, et c'est
    #: le cas ordinaire au depart — la comparaison est la mesure que toute cette
    #: construction existe pour rendre possible, pas une decoration.
    comparisons: list[tuple[str, str, RateRow, RateRow]] = field(default_factory=list)
    #: L'ecart entre cran annonce et cran calcule, **pour cette population-ci**.
    #:
    #: Il n'existait pas, et il ne pouvait pas exister : jusqu'au lot 9 le
    #: gabarit ne demandait pas de bloc `conf` sous la section C-bis, si bien que
    #: ces selections n'avaient aucun cran declare. La phrase ajoutee au gabarit
    #: est la moitie du chantier ; celle-ci est l'autre — un cran qu'on demande
    #: et que rien ne relit finirait par se retirer, comme l'effectif collecte
    #: des mois sans lecteur.
    #:
    #: **Jamais fondu dans celui de la population principale**, et ce n'est pas
    #: une precaution de forme : mesure du 19/08/2026 sur les quinze premieres
    #: selections a porter les deux crans, les desaccords des deux cotes ne sont
    #: pas le meme passage — 4 vers 5 en section C, 2 vers 3 en C-bis. Les
    #: additionner designerait une clause moyenne que ni l'une ni l'autre ne
    #: reclame, alors que ce champ n'a qu'un usage : nommer le passage a
    #: reecrire.
    notation: Notation = field(default_factory=Notation)
    #: L'adherence du cadre sur **cette** population. Tenue a part de celle de la
    #: section C, meme regle que `notation` : les deux populations sont produites
    #: par deux circuits, et un compte commun ne designerait aucun des deux.
    tier_drift: TierDrift = field(default_factory=lambda: TierDrift())

    @property
    def empty(self) -> bool:
        return self.settled == 0 and self.pending == 0

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)


#: Effectif qu'il faut **dans chacune des deux populations, sur une meme bande**,
#: pour que leur comparaison se rende. En dessous, elle opposerait deux nombres
#: dont aucun ne veut rien dire — exactement ce que la page a mis huit lots a
#: cesser de faire.
COMPARISON_MIN = 20


def exploratory(settings: Settings | None = None) -> Exploratory:
    """Les selections de la section C-bis, mesurees a part.

    Le filtre d'anteriorite s'y applique comme ailleurs : une selection saisie
    apres le coup d'envoi porte un prix saisi apres le coup d'envoi, et le
    residu n'aurait plus de sens. C'est la **seule** regle que les deux
    populations partagent.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        bandes = _bands(conn)
        rows = conn.execute(
            "SELECT k.id, k.tier, k.tier_real, k.result, k.price, k.event_id, k.created_at, "
            "       k.confidence, k.confidence_computed, k.research_overridden, "
            "       e.commence_time FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "WHERE k.exploratoire = 1"
        ).fetchall()

    report = Exploratory(minimum_rows=threshold_value("feedback_min_rows", settings))
    lignes = [row for row in rows if not _late(row)]
    report.pending = sum(1 for row in lignes if str(row["result"]) not in ("win", "loss"))
    tranchees = [row for row in lignes if str(row["result"]) in ("win", "loss")]
    report.settled = len(tranchees)
    report.won = sum(1 for row in tranchees if str(row["result"]) == "win")
    report.by_tier = _rate_tally(
        [
            (cle, tier_labels.get(cle, cle), str(row["result"]), row)
            for row in lignes
            for cle in (_tier_of(row, TIER_DECLARED, bandes),)
        ],
        readable=report.minimum_rows,
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )
    implicites = [
        1.0 / float(row["price"]) for row in tranchees if row["price"] and float(row["price"]) > 1.0
    ]
    report.residual = Residual(
        observed=sum(
            1
            for row in tranchees
            if str(row["result"]) == "win" and row["price"] and float(row["price"]) > 1.0
        ),
        implied=implicites,
    )
    # **Sur `lignes` et non sur `tranchees`**, exactement comme la population
    # principale : ce bloc compare deux declarations, toutes deux connues a
    # l'import, et non une issue. Ecarter les selections en attente serait la
    # meme erreur de categorie qu'un taux lu sans son prix — et le cas est ici
    # la regle plutot que l'exception, la premiere volee de blocs de C-bis etant
    # entiere en attente.
    report.notation = _notation(lignes, [str(row["result"]) for row in lignes], report.minimum_rows)
    return report


#: Les bandes de retard, bornes en minutes. `None` en borne haute = sans limite.
#:
#: **Trois bandes et ces trois-la, posees d'avance.** Elles ne sortent pas des
#: donnees — ce serait un decoupage choisi apres avoir regarde, la faute que la
#: page a mis huit lots a corriger — mais de ce qu'un retard **suppose** :
#: en dessous d'un quart d'heure une saisie peut n'avoir rien vu du match ;
#: au-dela d'une heure, un match de football est a la mi-temps et un set de
#: tennis est joue. La bande du milieu est le cas ambigu, et elle doit exister
#: pour que les deux autres soient nettes.
LATE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("moins de 15 min", 0, 15),
    ("15 à 60 min", 15, 60),
    ("plus de 60 min", 60, None),
)


@dataclass
class LateBand:
    """Une bande de retard : son effectif, son residu, son intervalle.

    **Elle se rend meme a trois selections.** Fusionner sous un seuil
    d'effectif refabriquerait le bloc unique que la stratification defait, et
    c'est l'intervalle — large — qui doit dire que la bande ne conclut rien.
    """

    label: str
    low: int
    high: int | None
    settled: int = 0
    won: int = 0
    pending: int = 0
    residual: Residual = field(default_factory=lambda: Residual(observed=0, implied=[]))

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def per_selection(self) -> float | None:
        """L'ecart au prix **par selection**, la seule forme comparable.

        Trois bandes d'effectifs tres differents ne se comparent pas par leur
        ecart brut : celle qui porte le plus de lignes porte mecaniquement le
        plus d'ecart. C'est la meme regle que l'ecart entre population
        principale et tardive, qui se lit lui aussi par selection.
        """
        if not self.residual.expected:
            return None
        return round(self.residual.gap / self.settled, 3) if self.settled else None

    @property
    def line(self) -> str:
        if not self.settled:
            return f"{self.label} : aucune sélection tranchée"
        borne = ""
        if self.interval:
            bas, haut = self.interval
            borne = f" · intervalle [{bas * 100:.0f} – {haut * 100:.0f}]"
        ecart = self.per_selection
        return (
            f"{self.label} : {self.won} sur {self.settled}{borne} · "
            f"écart au prix {self.residual.gap:+.2f}"
            + (f" ({ecart:+.3f} par sélection)" if ecart is not None else "")
        )


@dataclass
class Late:
    """Les selections **ecrites apres le coup d'envoi**, mesurees a part.

    **Ce n'est pas un manque, et la page les presentait comme tel.** Le
    diagnostic d'origine tenait les 52 ecartees pour un defaut de collecte ; la
    mesure dit 0 sur 230 sans horodatage. Elles ont reellement ete ecrites apres
    le coup d'envoi, et c'est un choix d'usage — donc une population, pas une
    reserve de lecture.

    **Ce qu'elles mesurent n'existe nulle part ailleurs** : elles sont au-dessus
    de leur prix la ou la population principale est en dessous, et l'ecart entre
    les deux est la meilleure estimation disponible du biais que produit une
    selection ecrite en connaissant le debut du match.
    """

    settled: int = 0
    won: int = 0
    pending: int = 0
    #: Combien portent un motif declare (`differee`, `live`) et combien n'en
    #: portent aucun. **Les additionner detruisait la distinction** : une
    #: decision anterieure mal saisie et un pari pris en direct n'ont ni la meme
    #: etiquette ni le meme prix.
    declared: dict[str, int] = field(default_factory=dict)
    undeclared: int = 0
    residual: Residual = field(default_factory=lambda: Residual(observed=0, implied=[]))
    minimum_rows: int = ANALYSIS_MIN_ROWS
    #: Le retard, en trois bandes. **Aucune fusion sous un seuil d'effectif** :
    #: une bande a trois selections s'affiche avec son compte, et son intervalle
    #: dit ce qu'il vaut. Les fondre reproduirait le bloc unique qu'on defait.
    bands: list[LateBand] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.settled == 0 and self.pending == 0

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def reasons(self) -> list[tuple[str, int]]:
        """Les motifs declares, libelles et ranges du plus frequent.

        Le libelle se resout depuis `LATE_REASONS` et jamais cote gabarit : deux
        ecritures du meme vocabulaire divergent au premier ajustement.
        """
        rendus = [
            (LATE_REASONS.get(cle, cle), compte)
            for cle, compte in sorted(self.declared.items(), key=lambda item: -item[1])
        ]
        if self.undeclared:
            rendus.append(("Aucun motif déclaré", self.undeclared))
        return rendus


def _late_bands(rows: list[Any]) -> list[LateBand]:
    """Range les tardives par bande de retard, sans en fusionner aucune.

    **Un retard inconnu n'entre dans aucune bande.** `late_minutes` est NULL sur
    les lignes anterieures a la migration 055 dont le match a ete detache
    depuis : les ranger dans la premiere bande les ferait passer pour des
    saisies immediates, ce qui est exactement la lecture que la stratification
    existe pour rendre impossible.

    La borne basse est **incluse** et la haute exclue, comme partout dans ce
    projet — c'est deja la convention des bandes de cote (`Tier.covers`), et
    deux conventions dans la meme base se liraient a l'envers.
    """
    bandes = [LateBand(label=nom, low=bas, high=haut) for nom, bas, haut in LATE_BANDS]
    for row in rows:
        minutes = _column(row, "late_minutes")
        if minutes is None:
            continue
        valeur = int(minutes)
        for bande in bandes:
            if valeur >= bande.low and (bande.high is None or valeur < bande.high):
                _fill_band(bande, row)
                break
    return bandes


def _fill_band(bande: LateBand, row: Any) -> None:
    """Ajoute une ligne a sa bande, resultat et prix compris."""
    resultat = str(row["result"])
    if resultat not in ("win", "loss"):
        bande.pending += 1
        return
    bande.settled += 1
    bande.won += 1 if resultat == "win" else 0
    if row["price"] and float(row["price"]) > 1.0:
        bande.residual = Residual(
            observed=bande.residual.observed + (1 if resultat == "win" else 0),
            implied=[*bande.residual.implied, 1.0 / float(row["price"])],
        )


def late(settings: Settings | None = None) -> Late:
    """Les selections tardives, mesurees a part.

    Le champ `tardive` est **stocke** et non recalcule a l'affichage : c'est une
    population au meme titre que l'exploratoire, et une population qui se
    deduirait a chaque lecture finirait par ne plus designer les memes lignes que
    le compte affiche a cote.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT result, price, late_reason, late_minutes FROM picks "
            " WHERE tardive = 1 AND exploratoire = 0"
        ).fetchall()

    report = Late(minimum_rows=threshold_value("feedback_min_rows", settings))
    report.bands = _late_bands(rows)
    tranchees = [row for row in rows if str(row["result"]) in ("win", "loss")]
    report.pending = len(rows) - len(tranchees)
    report.settled = len(tranchees)
    report.won = sum(1 for row in tranchees if str(row["result"]) == "win")
    for row in tranchees:
        motif = _column(row, "late_reason") or ""
        if motif:
            report.declared[motif] = report.declared.get(motif, 0) + 1
        else:
            report.undeclared += 1
    prix = [row for row in tranchees if row["price"] and float(row["price"]) > 1.0]
    report.residual = Residual(
        observed=sum(1 for row in prix if str(row["result"]) == "win"),
        implied=[1.0 / float(row["price"]) for row in prix],
    )
    return report


@dataclass(frozen=True)
class Populations:
    """Les trois populations et leur somme. **Un temoin, pas un regroupement.**

    Aucun indicateur ne les melange ; ce compte-ci existe pour verifier qu'aucune
    selection ne s'est perdue entre elles — meme role que `Analysis.recorded`,
    qui ne peut pas baisser.
    """

    main: int
    exploratory: int
    late: int
    total: int

    @property
    def consistent(self) -> bool:
        return self.main + self.exploratory + self.late == self.total


def populations(settings: Settings | None = None) -> Populations:
    """Le compte des trois populations, sur toutes les selections enregistrees."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "  SUM(exploratoire = 1) AS exploratoire, "
            "  SUM(exploratoire = 0 AND tardive = 1) AS tardive, "
            "  SUM(exploratoire = 0 AND tardive = 0) AS principale FROM picks"
        ).fetchone()
    return Populations(
        main=int(row["principale"] or 0),
        exploratory=int(row["exploratoire"] or 0),
        late=int(row["tardive"] or 0),
        total=int(row["total"] or 0),
    )


def compare_populations(
    principale: list[RateRow],
    exploratoire: list[RateRow],
    minimum: int = COMPARISON_MIN,
) -> list[tuple[str, str, RateRow, RateRow]]:
    """Fait date contre lecture, **a palier fixe**.

    C'est la mesure que toute la section C-bis existe pour rendre possible, et
    elle ne se rend qu'a **partir de vingt selections tranchees de chaque cote,
    dans une meme bande** : en dessous elle opposerait deux nombres dont aucun ne
    veut rien dire. Le palier est fixe parce que sans lui la comparaison opposerait
    des populations qui ne jouent pas aux memes prix — la faute exacte que la
    page a mis huit lots a cesser de commettre.
    """
    droite = {row.key: row for row in exploratoire}
    return [
        (row.key, row.label, row, droite[row.key])
        for row in principale
        if row.key in droite and row.settled >= minimum and droite[row.key].settled >= minimum
    ]


def labelling(settings: Settings | None = None) -> list[Mix]:
    """Comment les selections sont etiquetees, sans regarder ce qu'elles valent.

    Volontairement separe d'`analysis()` : celle-ci mesure des issues et porte
    tous ses garde-fous d'echantillon, celui-ci decrit un comportement et n'en
    a besoin d'aucun. Les melanger aurait fini par appliquer a l'un les seuils
    de l'autre — donc a taire le seul bloc que le volume actuel autorise.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        # Population principale seule, comme partout : une selection
        # exploratoire est produite sans fait date et par un autre circuit, donc
        # elle ne decrit pas la meme facon d'etiqueter.
        rows = conn.execute(
            "SELECT session_id, tier, confidence FROM picks WHERE exploratoire = 0"
        ).fetchall()

    # Aucune selection : aucune echelle a decrire. Rendre deux echelles a zero
    # ferait lire « je n'emploie aucun niveau » la ou il n'y a rien du tout.
    if not rows:
        return []

    confiance = Mix(key="confidence", label="confiance annoncée")
    niveaux = {str(level): 0 for level in CONFIDENCE_SCALE}
    for row in rows:
        if row["confidence"] is None:
            confiance.unlabelled += 1
        else:
            # Une valeur hors echelle ne peut pas arriver — `add_pick` borne a
            # 1..5 — mais la jeter en silence si elle arrivait ferait mentir le
            # total. Elle rejoint donc les non etiquetees.
            key = str(row["confidence"])
            if key in niveaux:
                niveaux[key] += 1
                confiance.total += 1
            else:
                confiance.unlabelled += 1
    confiance.rows = [
        MixRow(key=key, label=f"confiance {key}", count=count, total=confiance.total)
        for key, count in niveaux.items()
    ]
    _count_vacancy(confiance, rows, "confidence")

    palier = Mix(key="tier", label="palier")
    compte = dict.fromkeys(tier_order, 0)
    for row in rows:
        if row["tier"] in compte:
            compte[row["tier"]] += 1
            palier.total += 1
        else:
            palier.unlabelled += 1
    palier.rows = [
        MixRow(key=key, label=tier_labels.get(key, key), count=count, total=palier.total)
        for key, count in compte.items()
    ]
    _count_vacancy(palier, rows, "tier")

    return [confiance, palier]


@dataclass
class SessionMix:
    """La distribution d'une echelle sur **une** session d'analyse."""

    session_id: int
    #: Journee d'analyse : la date de la premiere selection de la session. C'est
    #: la journee de **decision**, jamais celle des matchs — meme regle que
    #: partout ailleurs sur cette page.
    day: str
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0
    #: Au moins un prompt de cette session transmettait les taux. **C'est la
    #: seule colonne qui rende la serie interpretable** : elle marque la
    #: frontiere entre une notation aveugle a ses propres resultats et une
    #: notation qui les lit.
    feedback: bool = False

    def share(self, key: str) -> float | None:
        return None if self.total == 0 else self.counts.get(key, 0) / self.total


@dataclass
class ScaleShift:
    """La serie d'une echelle d'etiquetage, session par session.

    **Ce que `labelling()` ne peut pas dire.** Il rend la distribution agregee
    sur toute la base et, par niveau, le nombre de sessions qui ne l'emploient
    pas. Aucune des deux ne montre une **deformation dans le temps** : une
    echelle qui glisserait d'un cran d'un mois sur l'autre y rendrait exactement
    la meme part globale et la meme vacance.

    Elle existe pour une question posee d'avance, et une seule : le jour ou les
    taux entreront dans le prompt, la consigne de resserrement fera-t-elle
    bouger la distribution des crans ? Sans releve d'avant, la reponse ne se
    reconstituera pas — `picks` porte les crans, mais personne ne les aura
    regardes dans l'ordre.

    Aucun test n'y est attache et aucun verdict n'en sort : **c'est un
    instrument, pas une mesure.** Il dit ou regarder, comme la fiche de
    priorite de recherche.
    """

    key: str
    label: str
    #: Les niveaux dans l'ordre de l'echelle, jamais dans celui des effectifs :
    #: une serie se lit en colonnes fixes, et un tri par volume les ferait
    #: changer de place d'une lecture a l'autre.
    levels: list[str] = field(default_factory=list)
    level_labels: dict[str, str] = field(default_factory=dict)
    #: Du plus ancien au plus recent. **Une serie se lit dans l'ordre ou elle
    #: s'est produite** ; la page, elle, range partout du plus recent au plus
    #: ancien, et suivre cette convention ici rendrait la deformation illisible.
    sessions: list[SessionMix] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.sessions

    @property
    def cut(self) -> int | None:
        """Rang de la premiere session qui a lu ses propres taux, ou `None`.

        C'est le point de coupe de la serie. `None` veut dire qu'aucune n'en a
        lu — l'etat au 21/08/2026, et l'etat souhaite tant que la suspension
        tient.
        """
        for rank, entry in enumerate(self.sessions):
            if entry.feedback:
                return rank
        return None


def scale_shift(settings: Settings | None = None) -> list[ScaleShift]:
    """La distribution des crans et des paliers, session par session.

    Instrumentation posee **avant** que le regime change, parce qu'une serie ne
    se reconstitue pas apres coup : ce qui manquerait n'est pas la donnee — les
    crans sont en base — mais le fait de l'avoir regardee dans l'ordre, et donc
    de pouvoir dire qu'elle ne bougeait pas.

    Population principale seule, comme `labelling()` : une selection
    exploratoire est produite par un autre circuit et sans exigence de fait
    date, donc elle ne decrit pas la meme facon de noter.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        rows = conn.execute(
            "SELECT session_id, tier, confidence, created_at FROM picks WHERE exploratoire = 0"
        ).fetchall()
        # Une session a lu ses propres taux des qu'**un** de ses prompts les
        # transmettait : les selections suivantes en dependent toutes, quel que
        # soit le prompt qui les a portees.
        parlantes = {
            int(row["session_id"])
            for row in conn.execute(
                "SELECT DISTINCT session_id FROM prompts WHERE feedback_active = 1"
            )
        }

    if not rows:
        return []

    jours: dict[int, str] = {}
    compte: dict[int, dict[str, dict[str, int]]] = {}
    total: dict[int, dict[str, int]] = {}
    for row in rows:
        session_id = int(row["session_id"])
        jour = str(row["created_at"] or "")[:10]
        # La journee d'analyse d'une session est celle de sa **premiere**
        # selection : une session a cheval sur minuit reste une seance.
        if jour and (session_id not in jours or jour < jours[session_id]):
            jours[session_id] = jour
        seau = compte.setdefault(session_id, {"confidence": {}, "tier": {}})
        somme = total.setdefault(session_id, {"confidence": 0, "tier": 0})
        if row["confidence"] is not None:
            cle = str(row["confidence"])
            seau["confidence"][cle] = seau["confidence"].get(cle, 0) + 1
            somme["confidence"] += 1
        if row["tier"]:
            seau["tier"][str(row["tier"])] = seau["tier"].get(str(row["tier"]), 0) + 1
            somme["tier"] += 1

    ordre = sorted(compte, key=lambda session_id: (jours.get(session_id, ""), session_id))
    echelles = [
        ScaleShift(
            key="confidence",
            label="confiance annoncée",
            levels=[str(level) for level in CONFIDENCE_SCALE],
            level_labels={str(level): f"conf {level}" for level in CONFIDENCE_SCALE},
        ),
        ScaleShift(
            key="tier",
            label="palier",
            levels=list(tier_order),
            level_labels={key: tier_labels.get(key, key) for key in tier_order},
        ),
    ]
    for echelle in echelles:
        echelle.sessions = [
            SessionMix(
                session_id=session_id,
                day=jours.get(session_id, ""),
                counts=dict(compte[session_id][echelle.key]),
                total=total[session_id][echelle.key],
                feedback=session_id in parlantes,
            )
            for session_id in ordre
        ]
    return echelles


def _rate_tally(
    entries: list[tuple[str, str, str, Any]],
    minimum: int = 1,
    readable: int = ANALYSIS_MIN_ROWS,
) -> list[RateRow]:
    """Agrege des quadruplets (cle, libelle, resultat, ligne) en lignes de taux.

    `minimum` **retire** une ligne du regroupement — la longue traine des
    libelles vus une fois. `readable` ne retire rien : il dit a partir de quel
    effectif le taux se lit, et la page affiche l'effectif en dessous. Les deux
    ne se confondent pas : la premiere est du bruit d'orthographe, la seconde
    une mesure trop courte.
    """
    grouped: dict[str, RateRow] = {}
    for key, label, result, row in entries:
        _count(
            grouped.setdefault(key, RateRow(key=key, label=label, minimum=readable)), result, row
        )
    return [row for row in grouped.values() if row.settled >= minimum]


@dataclass
class ResidualRow:
    """Le residu au prix d'un regroupement, avec de quoi le lire.

    **Le taux brut ne compare pas ce qu'il pretend comparer**, et la semaine du
    14/08/2026 l'a montre deux fois : « Issue 48 % contre Maniere 79 % » et
    « SAFE 66 % contre FUN 40 % » ont paru concluants a deux lecteurs
    differents, alors que les deux opposent des populations qui ne jouent pas
    aux memes prix. Le residu, lui, compare chaque selection a **son** prix.

    Le taux brut reste a cote, jamais seul : il dit combien de fois ca tombe,
    ce qui reste lisible tant qu'il ne sert pas a comparer deux populations.
    """

    key: str
    label: str
    residual: Residual
    won: int = 0
    settled: int = 0
    #: Selections **supplementaires** au meme regime pour que le deficit tienne.
    #: La page n'a pas a conclure, mais elle doit dire a quelle distance la
    #: question se tranche.
    horizon: int | None = None

    @property
    def rate(self) -> float | None:
        return self.won / self.settled if self.settled else None

    @property
    def rate_label(self) -> str:
        return f"{self.rate:.0%}" if self.rate is not None else "—"

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def interval_label(self) -> str:
        bornes = self.interval
        return f"[{bornes[0]:.0%} – {bornes[1]:.0%}]" if bornes else "—"

    @property
    def gap_label(self) -> str:
        gap = self.residual.gap
        return f"{gap:+.1f}" if gap is not None else "—"

    @property
    def expected_label(self) -> str:
        return f"{self.residual.expected:.1f}"

    @property
    def established(self) -> bool:
        """Le deficit s'ecarte de ce que les prix annoncaient.

        **Ce n'est pas une conclusion de la page** : c'est le meme test que le
        bloc de tete, applique a une ligne, et la page l'affiche sans en tirer
        de phrase. Ce qui separe se voit ; ce qui se conclut appartient au
        lecteur.
        """
        return self.settled > 0 and (self.residual.gap or 0) < 0 and self.residual.p_value < ALPHA


def _residual_rows(
    entries: list[tuple[str, str, Any]], minimum: int = ANALYSIS_MIN_ROWS
) -> list[ResidualRow]:
    """Le residu au prix, groupe par cle. Les lignes trop courtes sont retirees.

    **Un axe qui ne porte que du bruit vaut mieux non affiche.** Mesure du
    14/08/2026 sur les 103 selections a anteriorite etablie : le marche donne 13
    niveaux dont **huit sous dix selections**, et une case a trois lignes rend un
    intervalle si large que personne ne le lira. Le seuil est celui de la page,
    jamais un second — sous quel compte un taux ne veut plus rien dire est une
    propriete des donnees.
    """
    groupes: dict[str, tuple[str, list[Any]]] = {}
    for key, label, row in entries:
        groupes.setdefault(key, (label, []))[1].append(row)

    rendus: list[ResidualRow] = []
    for key, (label, lignes) in groupes.items():
        gagnees = sum(1 for row in lignes if str(row["result"]) == "win")
        implied = [1.0 / float(row["price"]) for row in lignes]
        residual = Residual(observed=gagnees, implied=implied)
        if len(lignes) < minimum:
            continue
        rendus.append(
            ResidualRow(
                key=key,
                label=label,
                residual=residual,
                won=gagnees,
                settled=len(lignes),
                horizon=residual_horizon(residual),
            )
        )
    return sorted(rendus, key=lambda row: -row.settled)


def _by_family(tally: list[RateRow], known: dict[str, str]) -> tuple[list[Family], int]:
    """Groupe les marches par famille. Rend aussi ce qui n'en a aucune.

    Une cle inconnue **ne tombe pas dans « Autre »** : `autre` est une decision
    prise marche par marche, pas le fourre-tout de ce qu'on n'a pas regarde. Le
    compte des non classes est rendu a part, et les reglages les reclament.
    """
    grouped: dict[str, Family] = {}
    orphans = 0
    for entry in tally:
        family = known.get(family_key(entry.key))
        if family is None:
            orphans += entry.settled
            continue
        target = grouped.setdefault(
            family, Family(rates=RateRow(key=family, label=family_label(family)))
        )
        target.rates.merge(entry)
        target.markets.append(entry)

    found = [entry for entry in grouped.values() if entry.rates.settled]
    for entry in found:
        entry.markets.sort(key=lambda item: (-item.settled, item.label))
    found.sort(key=lambda item: family_rank(item.rates.key))
    return found, orphans


def _by_session(
    sessions: list[Any],
    picks: list[Any],
    known: dict[int, Lot],
    sport_labels: dict[str, str],
    tz: str,
    priorities: dict[int, set[str]] | None = None,
) -> list[SessionRate]:
    """Une ligne par session : le lot vu, les matchs retenus, ce que ca a donne.

    Les sessions sans lot connu sont **gardees et marquees**, jamais retirees :
    une session qui n'a genere aucun prompt n'a rien soumis a l'analyse, et la
    faire disparaitre laisserait croire qu'elle n'a pas eu lieu.
    """
    grouped: dict[int, list[Any]] = {}
    priorities = priorities or {}
    for row in picks:
        grouped.setdefault(int(row["session_id"]), []).append(row)

    found = []
    for row in sessions:
        session_id = int(row["id"])
        lot = known.get(session_id)
        mine = grouped.get(session_id, [])
        # Le lot dit les sports quand il est enregistre ; sinon les selections
        # les disent, ce qui rate seulement une session ou l'on n'a rien retenu.
        # Les deux valent mieux qu'une colonne vide sur tout l'historique.
        sports = [name for name in (row["sports"] or "").split(",") if name] or sorted(
            {sport_labels[key] for pick in mine if (key := pick["sport_key"]) in sport_labels}
        )
        entry = SessionRate(
            session_id=session_id,
            label=row["label"] or f"Session {session_id}",
            day=_local(row["created_at"], tz).strftime("%d/%m"),
            sports=" · ".join(sports),
            lot=None if lot is None else lot.size,
            reconstructed=bool(lot and lot.reconstructed),
            # Les matchs distincts, et non les lignes : deux selections sur la
            # meme rencontre sont un match retenu, pas deux.
            covered=len({row["event_id"] for row in mine if row["event_id"]}),
            picks=len(mine),
            rates=RateRow(key=str(session_id), label=f"Session {session_id}"),
            tokens=int(row["tokens"] or 0),
            feedback_active=bool(_column(row, "feedback_active")),
            guarded=str(row["created_at"]) >= GUARD_IN_SERVICE,
            # Les deux comptes se font en un seul passage, sur la meme
            # population : deux parcours auraient fini par ne plus porter sur le
            # meme ensemble, et leur somme ne serait plus le total ecrase.
            overridden=sum(
                1
                for pick in mine
                if _column(pick, "research_overridden")
                and not is_collection_fault(_column(pick, "research_override_cause"))
                and not is_unknown_cause(_column(pick, "research_override_cause"))
            ),
            override_faults=sum(
                1
                for pick in mine
                if _column(pick, "research_overridden")
                and is_collection_fault(_column(pick, "research_override_cause"))
            ),
            override_unknown=sum(
                1
                for pick in mine
                if _column(pick, "research_overridden")
                and is_unknown_cause(_column(pick, "research_override_cause"))
            ),
            opened=len(declared := str(_column(row, "open_dossiers") or "").split()),
            on_priority=len(set(declared) & set(priorities.get(session_id, ()))),
        )
        for pick in mine:
            _count(entry.rates, pick["result"] or "pending", pick)
        found.append(entry)
    return found


@dataclass(frozen=True)
class DossierPoint:
    """Un lot, et ce que l'analyse y a declare ouvrir.

    **La maille est le prompt, pas la session, et c'est mesure.** La session 17
    porte trois lots — six, sept et neuf blocs — et trois declarations. Rangee
    par session, elle rendrait « 9 reperes pour 22 matchs », deux nombres qui ne
    se sont jamais rencontres : le lot de la declaration a neuf matchs, pas
    vingt-deux. `sessions.open_dossiers` ne garde d'ailleurs que la derniere des
    trois — c'est un etat courant, pas un historique.
    """

    prompt_id: int
    session_id: int
    day: str
    #: Blocs du prompt, c'est-a-dire la taille du lot soumis.
    lot: int
    #: `min(reglage, lot)` au moment de la generation, relu dans le corps.
    budget: int | None
    #: Reperes que le collage a declares, `None` si aucun collage de ce lot n'a
    #: porte la ligne. **Zero et « pas de ligne » ne sont pas la meme chose** :
    #: l'un est une declaration du modele, l'autre un collage qui l'a laissee.
    declared: int | None
    #: Etat de la ligne, vocabulaire de `confidence.OPEN_STATES`.
    state: str = ""

    @property
    def saturated(self) -> bool | None:
        """Le lot a-t-il ete declare en entier ?

        C'est la seule lecture que trois points autorisent, et elle ne suppose
        aucune tendance : soit la declaration touche le plafond, soit non.
        """
        if self.declared is None or self.budget is None:
            return None
        return self.declared >= self.budget


@dataclass
class Dossiers:
    """La serie des dossiers declares, **et rien de plus qu'une serie**.

    **Trois points ne concluent rien, et ce bloc ne conclut rien.** Il n'y a ni
    pente, ni moyenne, ni projection : trois releves sur un seul jour et une
    seule session ne decrivent pas un comportement. Ce qui se rend est la liste,
    avec de quoi la lire — le lot, le budget effectif, ce qui a ete declare.

    **Ce qu'elle dit deja, en revanche, contredit ce qu'on en attendait.** Les
    trois declarations valent 6, 7 et 9 pour un reglage a 10, ce qui se lit comme
    « le modele s'approche de son budget sans l'epuiser ». Les lots
    correspondants comptaient **exactement 6, 7 et 9 blocs** : le budget effectif
    valait donc 6, 7 et 9, et le modele a declare **tout le lot** les trois fois.
    Ce n'est pas le reglage qui bornait, c'est le lot.

    Consequence a connaitre, et elle porte sur la mesure elle-meme : quand la
    declaration couvre le lot entier, `hors_dossiers` **ne peut plus se
    produire**. Le compteur d'inflation des migrations 043 et 045 est alors
    neutralise — non par un defaut de code, mais par une declaration qui ne
    laisse rien dehors.
    """

    points: list[DossierPoint] = field(default_factory=list)
    #: Part des selections en lecture, par jour du releve. Le brief demande de
    #: croiser les deux ; les deux se rendent cote a cote et **ne se divisent
    #: pas l'une par l'autre** — trois points ne portent aucun rapport.
    reading_share: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Residu au prix du cran 3, la ou il se calcule. `None` tant qu'aucune
    #: selection de ce cran n'est tranchee.
    rung_three: Residual | None = None

    @property
    def empty(self) -> bool:
        return not self.points

    @property
    def declared_points(self) -> list[DossierPoint]:
        """Les lots dont un collage a rapporte la ligne."""
        return [point for point in self.points if point.declared is not None]

    @property
    def binding(self) -> list[DossierPoint]:
        """Les lots ou le **reglage** a borne, c'est-a-dire budget < taille du lot.

        C'est la seule lecture qui dise si la variable existe encore. Un lot
        entierement declare ne prouve rien tout seul : il peut venir d'un budget
        atteint ou d'un lot plus petit que lui, et ce sont deux faits opposes.
        """
        return [
            point for point in self.points if point.budget is not None and point.budget < point.lot
        ]

    @property
    def bounded_line(self) -> str:
        """Ce que le budget borne encore, en une phrase et sur la mesure du jour.

        **Le fait a rendre visible, pour ne pas etre relu a l'envers dans un
        mois.** Le reglage est passe de 7 a 10 : sur les lots soumis depuis, il
        n'a plus borne une seule fois — ce n'est pas lui qui limite, c'est la
        taille du lot. Consequence directe : quand la declaration couvre le lot
        entier, `hors_dossiers` **ne peut plus se produire**, et le compteur
        d'inflation des migrations 043 et 045 est neutralise.

        **Les bornes sont mesurees, jamais ecrites en dur** : elles bougeront au
        premier lot plus grand, et une phrase qui annoncerait « de 6 a 12 » se
        mettrait a mentir sans que rien ne le dise.
        """
        avec = [point for point in self.points if point.budget is not None]
        if not avec:
            return ""
        recents = [point for point in avec if not self.binding or point.day >= self.binding[-1].day]
        depuis = [point for point in recents if point not in self.binding]
        if not depuis:
            return ""
        tailles = [point.lot for point in depuis]
        return (
            f"Le budget de recherche ne borne plus : sur les {len(depuis)} derniers lots, "
            f"de {min(tailles)} à {max(tailles)} blocs, le réglage n'a pas mordu une seule "
            "fois — c'est la taille du lot qui limite, pas lui. Quand la déclaration couvre "
            "le lot entier, « hors_dossiers » ne peut donc plus se produire, et le seul "
            "discriminant restant pour comparer un fait daté à une lecture est le niveau "
            "de source."
        )

    @property
    def line(self) -> str:
        """Une phrase qui dit l'effectif et s'arrete la."""
        vus = self.declared_points
        if vus:
            satures = sum(1 for point in vus if point.saturated)
            out = (
                f"{len(vus)} lot(s) avec la ligne, sur {len(self.points)} soumis · "
                f"{satures} déclare(nt) le lot entier · "
                "trois points ne font pas une tendance, et rien n'en est tiré ici"
            )
        else:
            out = "aucun lot n'a encore rapporté sa ligne « dossiers_ouverts »"
        # **Le constat sur le budget ne depend pas d'une declaration collee** :
        # il porte sur les lots soumis, pas sur ce que le modele en a dit. Le
        # ranger sous la branche « il existe une declaration » le ferait
        # disparaitre exactement quand la ligne manque — et c'est alors qu'on se
        # demande si le budget y est pour quelque chose.
        borne = self.bounded_line
        return f"{out}. {borne}" if borne else out


def dossiers(settings: Settings | None = None) -> Dossiers:
    """La serie des dossiers de recherche declares, lot par lot.

    Les trois grandeurs viennent de trois endroits et aucune n'est estimee : le
    lot de `prompt_events`, le budget du corps du prompt (migration 063), les
    reperes du collage conserve (`imports_raw`). Aucun appel, aucune estimation.
    """
    from .confidence import read_opened

    settings = settings or get_settings()
    found = Dossiers()
    with connect(settings) as conn:
        prompts = conn.execute(
            "SELECT p.id, p.session_id, substr(p.created_at, 1, 10) AS day, "
            "       p.research_budget, "
            "       (SELECT COUNT(*) FROM prompt_events pe WHERE pe.prompt_id = p.id) AS lot "
            "  FROM prompts p ORDER BY p.id"
        ).fetchall()
        collages = conn.execute(
            "SELECT session_id, raw_text FROM imports_raw ORDER BY id"
        ).fetchall()
        # **La declaration se range par session et non par prompt**, parce que
        # c'est tout ce que le collage porte : rien dans le texte recu ne dit de
        # quel prompt il repond. On la rattache donc au lot dont la taille tombe
        # sur le nombre de reperes — ce qui est verifiable et se refuse en cas
        # d'ambiguite, plutot que devine.
        par_session: dict[int, list[Any]] = {}
        for row in collages:
            lu = read_opened(str(row["raw_text"]))
            if lu.marks:
                par_session.setdefault(int(row["session_id"]), []).append(lu)

        for row in prompts:
            lus = par_session.get(int(row["session_id"]), [])
            candidats = [lu for lu in lus if len(lu.marks) == int(row["lot"])]
            retenu = candidats[0] if len(candidats) == 1 else None
            found.points.append(
                DossierPoint(
                    prompt_id=int(row["id"]),
                    session_id=int(row["session_id"]),
                    day=str(row["day"]),
                    lot=int(row["lot"]),
                    budget=(
                        int(row["research_budget"]) if row["research_budget"] is not None else None
                    ),
                    declared=len(retenu.marks) if retenu is not None else None,
                    state=retenu.state if retenu is not None else "",
                )
            )

        # Le croisement demande : part de `lecture`, et residu du cran 3. Les
        # deux se rendent **a cote** de la serie, jamais fondus dedans.
        for row in conn.execute(
            "SELECT substr(k.created_at, 1, 10) AS day, "
            "       SUM(k.source_level_effective = ?) AS lecture, COUNT(*) AS total "
            "  FROM picks k WHERE k.exploratoire = 0 GROUP BY 1 ORDER BY 1",
            (READING_LEVEL,),
        ).fetchall():
            found.reading_share[str(row["day"])] = (int(row["lecture"] or 0), int(row["total"]))

        prix = conn.execute(
            "SELECT k.price, k.result FROM picks k "
            " WHERE k.exploratoire = 0 AND k.confidence_computed = 3 "
            "   AND k.result IN ('win', 'loss') AND k.price > 1.0"
        ).fetchall()
    if prix:
        found.rung_three = Residual(
            observed=sum(1 for row in prix if str(row["result"]) == "win"),
            implied=[1.0 / float(row["price"]) for row in prix],
        )
    return found


@dataclass
class Override:
    """Les selections ecrasees faute de dossier ouvert.

    **Deux fautes que le compte seul confondrait**, et c'est pourquoi la
    distribution compte plus que le total. Un `3` revendique sur un dossier que
    l'analyse declare elle-meme n'avoir pas ouvert est de l'**inflation** : elle
    s'est notee comme si elle avait cherche. Un `5` — deux faits dates, deux
    editeurs distincts, une origine — est de la **fabrication** : les faits
    n'existent pas, et ca ne se traite pas pareil.

    Un compte, jamais un taux : il est juste a tout effectif, comme celui des
    non-classees. Aucun seuil ne le garde donc.

    **Les defauts de collecte en sortent** (`is_collection_fault`). Ce compte
    impute une faute au modele ; l'imputer sur une ligne `dossiers_ouverts`
    jamais collee serait faux, puisqu'on ignore alors si le dossier a ete
    ouvert — la question n'a pas ete transmise. Ils se comptent a cote, dans
    `SessionRate.override_faults`.
    """

    total: int = 0
    #: Les ecrasees dont **la cause manque**. Ni imputables au modele, ni
    #: identifiables comme un collage perdu : elles sortent de `total` et se
    #: comptent ici. Sans ce troisieme compte, `is_collection_fault(None)` etant
    #: faux, les 43 lignes non typees des sessions 11 et 13 gonflaient le compte
    #: d'inflation d'un tiers — le chiffre affirmait ce qu'il ignorait.
    unknown: int = 0
    #: `(cran revendique, compte)`, du plus frequent au moins.
    claimed: list[tuple[int, int]] = field(default_factory=list)
    #: Selections hors dossiers ouverts qui declarent quand meme **un fait date
    #: avec son editeur**. Ce n'est pas un cran mal note : c'est une recherche
    #: qui n'a pas eu lieu. Le compte se lit sur les faits declares plutot que
    #: sur le cran revendique — un 3 peut venir d'un seul fait, un fait cite
    #: avec son editeur suppose une page ouverte, et c'est cette page-la qui
    #: n'existe pas.
    #:
    #: Distinct de `fabricated`, qui compte les crans hauts : les deux
    #: recouvrent souvent les memes lignes, mais l'un decrit une note et l'autre
    #: un geste. Un cran 2 adosse a un fait invente compte ici et pas la-bas.
    researched: int = 0
    #: Seuil a partir duquel un cran revendique suppose des faits produits. Un
    #: 4 demande un fait date verifie, un 5 en demande deux d'editeurs
    #: distincts : au-dela de 3, l'analyse n'a pas seulement gonfle son niveau,
    #: elle a decrit une recherche.
    fabricated_from: int = 4

    @property
    def fabricated(self) -> int:
        return sum(count for rung, count in self.claimed if rung >= self.fabricated_from)

    @property
    def line(self) -> str:
        if not self.total:
            # Le compte sans cause se dit **meme seul**, sinon une population
            # entierement non typee rendrait une ligne vide, indiscernable
            # d'une population ou rien n'a ete ecrase.
            return (
                f"{self.unknown} sélection(s) ramenée(s) en lecture sans cause enregistrée"
                if self.unknown
                else ""
            )
        detail = " · ".join(f"{rung} revendiqué ×{count}" for rung, count in self.claimed)
        fabrique = (
            f" · dont {self.fabricated} avec des faits déclarés sur un dossier non ouvert"
            if self.fabricated
            else ""
        )
        cherche = (
            f" · {self.researched} citent un éditeur sans que le dossier ait été ouvert"
            if self.researched
            else ""
        )
        inconnues = (
            f" · {self.unknown} autre(s) ramenée(s) en lecture sans cause enregistrée"
            if self.unknown
            else ""
        )
        return (
            f"{self.total} sélection(s) ramenée(s) en lecture — "
            f"{detail}{fabrique}{cherche}{inconnues}"
        )


def _override(rows: list[Any], results: list[str]) -> Override:
    """Compte les ecrasements et la distribution de ce qui etait revendique."""
    found = Override()
    tally: dict[int, int] = {}
    for row, result in zip(rows, results, strict=True):
        if result not in ("win", "loss") or not _column(row, "research_overridden"):
            continue
        # **Un defaut de collecte n'accuse personne.** Ce compte impute au modele
        # une inflation — « elle s'est notee comme si elle avait cherche » — et
        # l'imputer sur une ligne `dossiers_ouverts` jamais collee serait faux :
        # on ne sait pas si le dossier a ete ouvert, la question n'a pas ete
        # transmise. Mesure du 14/08/2026 : 13 des 16 ecrasees de la base
        # declaraient un niveau de source reel et auraient ete comptees ici.
        if is_collection_fault(_column(row, "research_override_cause")):
            continue
        # **Et une cause absente n'accuse personne non plus.** Meme raisonnement
        # d'un cran plus loin : on ignore si le dossier a ete ouvert *et* on
        # ignore pourquoi on l'ignore. Les compter dans `total` faisait affirmer
        # une inflation sur 43 lignes dont rien n'est su.
        if is_unknown_cause(_column(row, "research_override_cause")):
            found.unknown += 1
            continue
        found.total += 1
        # Ce que la declaration aurait donne ; a defaut de bloc lisible, ce que
        # le modele avait annonce. Les deux disent la meme chose ici : jusqu'ou
        # la selection se serait notee sans dossier derriere.
        rung = _column(row, "confidence_claimed") or _column(row, "confidence")
        if rung is not None:
            tally[int(rung)] = tally.get(int(rung), 0) + 1
        if (_column(row, "distinct_publishers") or 0) > 0:
            found.researched += 1
    found.claimed = sorted(tally.items(), key=lambda item: (-item[1], -item[0]))
    return found


def _notation(rows: list[Any], results: list[str], minimum: int) -> Notation:
    """Confronte les deux crans, selection par selection.

    **Les selections ecrasees en sortent**, et c'est indispensable : depuis
    l'override, la majorite des desaccords viendrait de lui — le modele annonce
    3, l'application force 1. Ce desaccord-la ne dit pas « le modele applique mal
    sa table », il dit « le modele revendique une recherche qu'il n'a pas
    faite ». Deux fautes differentes, deux compteurs ; les melanger ferait
    designer toujours la meme clause du gabarit, qui n'y serait pour rien.
    """
    found = Notation(minimum=minimum)
    ecarts: list[int] = []
    passages: dict[tuple[int, int], int] = {}
    for row, result in zip(rows, results, strict=True):
        # **Aucun filtre sur le resultat, et c'est une correction du 19/08/2026.**
        # Ce bloc ne mesure pas une issue : il compare **deux declarations** —
        # le cran annonce et celui que la table calcule — et les deux sont
        # connues a l'import. Une selection en attente porte donc exactement ce
        # qu'il faut, et l'ecarter etait la meme erreur de categorie que compter
        # un taux sans son prix.
        #
        # Meme exemption que `labelling()` : les garde-fous de resultat
        # protegent des **taux de reussite**, qui mesurent des issues. Ce qui
        # decrit un **comportement** n'a pas a les subir. Mesure qui l'a rendu
        # visible : les quinze premieres selections a porter les deux crans sont
        # toutes en attente, et la page n'affichait rien le jour ou l'ecart est
        # devenu mesurable.
        del result
        if _column(row, "research_overridden"):
            continue
        computed = _column(row, "confidence_computed")
        declared = _column(row, "confidence")
        if computed is None or declared is None:
            found.uncomputed += 1
            continue
        found.comparable += 1
        if int(computed) == int(declared):
            found.agreed += 1
        else:
            passages[(int(declared), int(computed))] = (
                passages.get((int(declared), int(computed)), 0) + 1
            )
        ecarts.append(int(declared) - int(computed))
    found.drift = sum(ecarts) / len(ecarts) if ecarts else 0.0
    found.transitions = sorted(
        ((declared, computed, count) for (declared, computed), count in passages.items()),
        key=lambda item: (-item[2], item[0], item[1]),
    )
    return found


#: La migration qui a deplace la frontiere SAFE/FUN. Sa date d'application est
#: la borne entre les selections emises sous l'ancien barme et celles emises sous
#: le nouveau — **la seule borne dont la base dispose**, `framework_version` etant
#: emis dans le payload et persiste nulle part.
TIER_PARTITION_MIGRATION = "071_partition_dure.sql"


@dataclass
class TierDrift:
    """L'ecart entre le palier **emis** et le palier **recalcule**.

    Depuis que le journal d'analyse recalcule le palier depuis la cote declaree,
    l'emoji colle par le modele n'est plus lu comme un classement. Il n'est pas
    pour autant sans usage : son ecart au recalcul mesure **l'adherence du cadre**
    — le modele classe-t-il comme l'application classe.

    Mesure du 21/08/2026, avant le deplacement de frontiere : **6 ecarts sur
    352**, soit 1,7 %. L'adherence etait donc excellente, et c'est ce qui rend le
    compteur lisible — au-dela du bruit, un ecart designe une derive entre le
    cadre publie et la configuration servie, jamais une distraction.

    Apres le deplacement : **39 ecarts sur 312** en section C, dont 37 par le
    seul passage de la frontiere SAFE/FUN a 1.80. Deux populations, deux comptes :
    c'est exactement ce que l'agregat cachait.

    **Et le zero de C-bis ne mesure rien**, ce qui est le piege de ce compteur.
    Ses 40 lignes vont de 2.34 a 9.86 : **aucune ne tombe sous 2.30**, donc
    aucune ne pouvait differer d'un deplacement de la frontiere SAFE/FUN.
    L'intersection est vide, le zero est **force** et non gagne, et il se lisait
    pourtant « le modele classe juste ici ». D'ou `low_price` et `high_price`,
    rendus a cote du compte : ce sont eux qui distinguent les deux zeros.

    **Un pic sur les lignes anterieures est attendu et n'est pas une
    regression** : elles ont ete emises sous l'ancien barme. C'est pour le dire
    que la repartition se coupe a la date d'application de la migration.
    """

    #: Selections portant une cote, donc recalculables.
    comparable: int = 0
    agreed: int = 0
    #: Selections sans cote : rien a recalculer, et ce n'est pas un desaccord.
    unpriced: int = 0
    #: Ecarts anterieurs au deplacement de frontiere, et posterieurs.
    before: int = 0
    after: int = 0
    #: `(emis, recalcule, compte)`, du plus frequent au moins frequent. Un ecart
    #: disperse est du bruit ; un ecart concentre sur un passage designe la
    #: frontiere qui a bouge.
    transitions: list[tuple[str, str, int]] = field(default_factory=list)
    #: Bornes des cotes comparees. **Ce qui distingue un zero gagne d'un zero
    #: force**, et la distinction n'est pas theorique : C-bis rendait 0 ecart sur
    #: 40, ce qui se lisait « le modele classe juste ici » — alors que ses 40
    #: lignes vont de 2.34 a 9.86 et qu'aucune ne tombe dans la bande deplacee.
    #: L'intersection etait vide, donc le zero ne mesurait aucune adherence. Un
    #: lecteur qui compare ces bornes a la frontiere qui a bouge le voit d'un
    #: coup d'oeil ; sans elles, personne ne le reverra dans six mois.
    low_price: float | None = None
    high_price: float | None = None

    @property
    def disagreed(self) -> int:
        return self.comparable - self.agreed

    @property
    def _span(self) -> str:
        """« (cotes de 2.34 à 9.86) », ou rien s'il n'y a pas de quoi comparer."""
        if self.low_price is None or self.high_price is None:
            return ""
        return f" (cotes de {self.low_price:.2f} à {self.high_price:.2f})"

    @property
    def line(self) -> str:
        """« 39 écarts sur 312 comparables — 39 avant le déplacement, 0 après »."""
        if not self.comparable:
            return ""
        if not self.disagreed:
            return f"palier émis et recalculé d'accord sur {self.comparable}{self._span}"
        return (
            f"{self.disagreed} écart(s) entre palier émis et recalculé "
            f"sur {self.comparable} — {self.before} avant le déplacement de frontière, "
            f"{self.after} après"
        )


def tier_drift(settings: Settings | None = None, *, exploratory: bool = False) -> TierDrift:
    """Combien de fois l'emoji colle differe du palier recalcule.

    **Un compte, jamais un taux**, donc aucun seuil ne le garde : il est juste a
    tout effectif, meme regle que le compte des non-classees.

    **Segmente par population, et pas agrege.** Le premier jet comptait les 352
    selections de la base — section C et C-bis melangees — puis se rendait en
    note d'une carte dont la population est la section C seule : deux
    denominateurs sur la meme carte, dont un qui refondait les deux populations
    que tout le reste du module tient separees. Meme regle que `Notation`, dont
    le commentaire dit que les additionner designerait une clause moyenne que ni
    l'une ni l'autre ne reclame.

    La coupe temporelle se fait sur la date d'application de la migration qui a
    deplace la frontiere, et non sur `framework_version` : ce champ est emis dans
    le payload et **persiste nulle part**. La date d'une migration, elle, est en
    base depuis toujours — meme idiome que l'audit des colonnes muettes.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        bandes = _bands(conn)
        borne = conn.execute(
            "SELECT applied_at FROM schema_migrations WHERE name = ?",
            (TIER_PARTITION_MIGRATION,),
        ).fetchone()
        rows = conn.execute(
            "SELECT tier, price, created_at FROM picks WHERE exploratoire = ?",
            (1 if exploratory else 0,),
        ).fetchall()

    bascule = str(borne["applied_at"]) if borne else ""
    found = TierDrift()
    passages: dict[tuple[str, str], int] = {}
    for row in rows:
        if row["price"] is None:
            found.unpriced += 1
            continue
        found.comparable += 1
        prix = float(row["price"])
        found.low_price = prix if found.low_price is None else min(found.low_price, prix)
        found.high_price = prix if found.high_price is None else max(found.high_price, prix)
        recalcule = _tier_of(row, TIER_DECLARED, bandes)
        emis = str(row["tier"])
        if emis == recalcule:
            found.agreed += 1
            continue
        passages[(emis, recalcule)] = passages.get((emis, recalcule), 0) + 1
        # Sans borne — la migration n'a pas encore tourne — tout est « avant » :
        # aucune selection n'a pu etre emise sous un barme qui n'existe pas.
        if not bascule or str(row["created_at"]) < bascule:
            found.before += 1
        else:
            found.after += 1
    found.transitions = sorted(
        ((emis, recalcule, compte) for (emis, recalcule), compte in passages.items()),
        key=lambda item: (-item[2], item[0], item[1]),
    )
    return found


def analysis(settings: Settings | None = None) -> Analysis:
    """Taux de reussite de **toutes** les selections, jouees ou non.

    Aucun filtre sur `played` : c'est precisement ce qui distingue cette vue de
    `stats()`. Aucun montant n'y entre non plus.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        bandes = _bands(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        rows = conn.execute(
            "SELECT k.id, k.session_id, k.tier, k.result, k.market, k.confidence, k.played, "
            "       k.event_id, k.created_at, k.price, k.angle, "
            # **La carte lit l'effectif, jamais la declaration.** Un niveau
            # annonce sur un dossier que l'analyse declare n'avoir pas ouvert
            # decrit ce qu'elle croyait avoir ; la question de la page est ce
            # sur quoi la selection reposait vraiment. La declaration reste en
            # base a cote, c'est elle l'entree de la mesure d'ecart.
            "       COALESCE(k.source_level_effective, k.source_level) AS source_level, "
            "       k.price_source, k.price_real, k.tier_real, "
            # Le cran calcule, a cote du cran annonce. Les deux sont lus dans la
            # meme passe : leur ecart est une mesure, pas un sous-produit.
            "       k.confidence_computed, k.confidence_claimed, k.research_overridden, "
            # **Et pourquoi.** Un ecrasement sans sa cause ne se lit pas : une
            # ligne jamais collee et un match hors de la liste donnent le meme
            # drapeau, et une seule des deux dit quelque chose du modele.
            "       k.research_override_cause, "
            # Les faits **declares**, pour compter les recherches qui n'ont pas
            # eu lieu : un editeur cite suppose une page ouverte, et sur un
            # dossier non ouvert c'est cette page-la qui n'existe pas.
            "       k.distinct_publishers, "
            # L'heure du coup d'envoi : c'est elle qui decide si le prix
            # enregistre est un prix d'avant-match. Voir `_antecedence`.
            "       e.commence_time, "
            # **Et le motif quand la saisie est tardive.** Sans lui les trois cas
            # se fondent en un seul compte : une decision anterieure saisie en
            # retard, un pari pris en direct, et une ligne anterieure a la garde
            # d'ecriture n'appellent ni la meme lecture ni le meme geste.
            "       k.late_reason, k.tardive, "
            # L'affiche, pour **nommer** les rencontres qui portent plus d'une
            # selection. Le compte seul disait qu'il faut elargir les
            # intervalles, jamais ou aller regarder.
            "       e.home, e.away, "
            "       s.key AS sport_key, c.category FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            # **La population principale, et elle seule.** Les selections
            # exploratoires sont produites sans **exigence** de fait date : les
            # melanger detruirait la comparaison que cette section existe pour
            # rendre possible — une selection adossee a un fait date tient-elle
            # mieux qu'une lecture. Elles ont leur propre bloc.
            "WHERE k.exploratoire = 0"
        ).fetchall()
        # Le temoin : compte direct, sans jointure. C'est lui qui rend le
        # denominateur verifiable — une regression de jointure le laisserait
        # intact et ferait bouger tout le reste. Il porte le meme filtre de
        # population que les lignes, sans quoi l'addition ne fermerait plus.
        recorded = conn.execute(
            "SELECT COUNT(*) AS n FROM picks WHERE result IN ('win', 'loss') AND exploratoire = 0"
        ).fetchone()["n"]
        sessions = conn.execute(
            "SELECT s.id, s.label, s.created_at, "
            "  (SELECT COALESCE(MAX(p.token_estimate), 0) FROM prompts p "
            "     WHERE p.session_id = s.id) AS tokens, "
            "  (SELECT GROUP_CONCAT(DISTINCT sp.label) FROM prompts p "
            "     JOIN prompt_events pe ON pe.prompt_id = p.id "
            "     JOIN events ev ON ev.id = pe.event_id "
            "     JOIN sports sp ON sp.id = ev.sport_id "
            "     WHERE p.session_id = s.id) AS sports, "
            # Le regime de collecte de la session. Une serie de poids qui les
            # melange ne dit rien : le bloc de retour d'experience a ete servi
            # sur trois sessions, puis suspendu, et la garde d'anteriorite ne
            # vaut que pour ce qui vient apres elle.
            "  (SELECT MAX(p.feedback_active) FROM prompts p "
            "     WHERE p.session_id = s.id) AS feedback_active, "
            # Les dossiers que le rendu declarait avoir ouverts. Se compare a
            # l'ordre de passage que l'application avait propose.
            "  s.open_dossiers "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC"
        ).fetchall()

    report = Analysis()
    report.minimum, report.minimum_days, report.minimum_competitions = reach(settings)
    report.minimum_rows = threshold_value("feedback_min_rows", settings)
    # **Le seuil descend dans la matrice des crans des l'assemblage**, et pas
    # seulement quand `_notation` tourne : sur un lot vide, celle-ci n'est jamais
    # appelee et la matrice gardait le defaut du module. Deux seuils differents
    # sur la meme page, dont un qui ne se voit qu'a effectif nul.
    report.notation.minimum = report.minimum_rows
    report.recorded = int(recorded)
    # **Le taux de selection garde toutes les selections, et lui seul.** Il ne
    # mesure pas ce que vaut une etiquette mais ce qui a ete retenu du lot : un
    # match passe ou pris l'a ete quel que soit le moment ou sa ligne a ete
    # saisie. Le filtre ci-dessous ne s'y applique donc pas.
    report.by_session = _by_session(
        sessions, rows, lots(settings), sport_labels, settings.tz, prompt_priorities(settings)
    )

    # LE FILTRE D'ANTERIORITE, applique **une seule fois et pour tout le reste**.
    #
    # Il ne protege pas seulement le prix : une selection saisie apres le coup
    # d'envoi porte une **etiquette** saisie apres le coup d'envoi, et rien ne
    # garantit qu'un « SAFE » ecrit a 22 h sur un match commence a 21 h ait ete
    # pense avant. C'est ce que la strate montre — l'echelle s'y **inverse** sur
    # les deux axes, et une echelle qui s'inverse n'est pas bruitee, elle decrit
    # au lieu de predire.
    #
    # Les compter reviendrait a mesurer la valeur predictive d'etiquettes dont
    # on a etabli qu'elles ne predisent pas. Et le melange **detruit du
    # signal** : sur 110 selections, la correction entre axes n'en retient
    # qu'un ; sur les 73 filtrees, elle en retenait trois.
    # **Le champ, jamais la derivation.** Il est stocke depuis la migration 053
    # et retro-rempli sur le meme critere : une population qui se recalculerait a
    # chaque lecture finirait par ne plus designer les memes lignes que le compte
    # affiche a cote — le piege deja paye par l'assembleur de contexte.
    tardifs = [row for row in rows if row["tardive"]]
    rows = [row for row in rows if not row["tardive"]]
    tranches = [row for row in tardifs if str(row["result"]) in ("win", "loss")]
    report.without_antecedence = len(tranches)
    # **Le motif se compte, il ne se fond pas.** « La decision etait anterieure,
    # la saisie non » et « on ne sait pas » se reparent aux deux bouts opposes :
    # la premiere ne se repare pas du tout — l'etiquette est valide — la seconde
    # ne se reparera jamais, sa population etant close depuis la garde
    # d'ecriture. Les additionner faisait lire un manque de collecte la ou il n'y
    # en a pas.
    for row in tranches:
        motif = _column(row, "late_reason") or ""
        report.late_by_reason[motif] = report.late_by_reason.get(motif, 0) + 1
    if not rows:
        return report

    results = [(row["result"] or "pending") for row in rows]
    report.settled = sum(1 for result in results if result in ("win", "loss"))
    # Les journees se comptent sur les seules selections tranchees, comme le
    # total : les compter sur toutes crediterait d'un etalement que le taux
    # affiche n'a pas.
    report.days = len(
        {
            str(row["created_at"])[:10]
            for row, result in zip(rows, results, strict=True)
            if result in ("win", "loss")
        }
    )

    # **Le palier se lit sur la cote obtenue quand elle existe**, sinon sur la
    # cote du bloc — et plus personne n'est mis a l'ecart en attendant un prix
    # qui n'arrivera pas. Le compte des lignes servies par un book de reference
    # reste, mais comme **description** et non comme filtre : il a son propre
    # axe, `by_price_source`, ou l'on peut voir si elles se comportent autrement.
    report.quarantined = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and _reference_priced(row)
    )
    report.by_tier = _rate_tally(
        [
            (cle, tier_labels.get(cle, cle), result, row)
            for row, result in zip(rows, results, strict=True)
            for cle in (_tier_of(row, TIER_DECLARED, bandes),)
        ],
        readable=report.minimum_rows,
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )

    report.by_confidence = sorted(
        _rate_tally(
            [
                (str(row["confidence"]), f"confiance {row['confidence']}", result, row)
                for row, result in zip(rows, results, strict=True)
                if row["confidence"] is not None
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: item.key,
        reverse=True,
    )
    # La bande cible se rattache ici et nulle part ailleurs : un sport ou un
    # marche ne se fixe pas d'objectif de taux, seule une confiance annoncee le
    # fait — c'est meme sa definition.
    #
    # **La reference est le taux global de la population que la page affiche**,
    # et non un chiffre pris ailleurs : une cible relative comparee a un taux
    # calcule sur un autre ensemble ne mesurerait rien.
    bands = load_bands(settings, reference=_global_rate(rows, results))
    for entry in report.by_confidence:
        entry.band = bands.get(int(entry.key)) if entry.key.isdigit() else None

    # Le meme axe, sur le cran **calcule**. Rendu a cote et jamais a la place :
    # tant que les deux populations ne se recouvrent pas, comparer leurs taux
    # comparerait deux echantillons differents — c'est le desaccord qui se lit,
    # pas la difference des taux.
    report.by_confidence_computed = sorted(
        _rate_tally(
            [
                (
                    str(row["confidence_computed"]),
                    f"cran calculé {row['confidence_computed']}",
                    result,
                    row,
                )
                for row, result in zip(rows, results, strict=True)
                if _column(row, "confidence_computed") is not None
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: item.key,
        reverse=True,
    )
    for entry in report.by_confidence_computed:
        entry.band = bands.get(int(entry.key)) if entry.key.isdigit() else None
    report.notation = _notation(rows, results, report.minimum_rows)
    report.override = _override(rows, results)

    # **Le residu au prix, decline.** La page ventilait des taux bruts par angle,
    # par marche et par confiance : la metrique retiree de la tete, et pour la
    # raison exacte qui a failli faire conclure deux fois cette semaine —
    # « Issue 48 % contre Maniere 79 % » et « SAFE 66 % contre FUN 40 % »
    # opposent des populations qui ne jouent pas aux memes prix.
    #
    # La population est celle du bloc de tete, a anteriorite etablie : un prix
    # enregistre apres le coup d'envoi ne decrit plus le marche d'avant-match, et
    # tout le residu en depend.
    prices = [
        row
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and _antecedence(row) and (_column(row, "price") or 0) > 1.0
    ]
    report.residual_by_angle = _residual_rows(
        [
            (row["angle"], ANGLES.get(row["angle"], row["angle"]), row)
            for row in prices
            if row["angle"]
        ],
        minimum=report.minimum_rows,
    )
    report.residual_by_confidence = sorted(
        _residual_rows(
            [
                (str(row["confidence"]), f"confiance {row['confidence']}", row)
                for row in prices
                if row["confidence"] is not None
            ],
            minimum=report.minimum_rows,
        ),
        key=lambda item: item.key,
        reverse=True,
    )
    # Le **meme** regroupement de libelles que la carte des taux : deux
    # normalisations paralleles auraient fini par ne plus decrire les memes
    # lignes, et leurs deux cartes se seraient contredites.
    report.residual_by_market = _residual_rows(
        [
            (_market_key(row["market"]), (row["market"] or "").strip(), row)
            for row in prices
            if _market_key(row["market"])
        ],
        minimum=report.minimum_rows,
    )

    report.by_sport = sorted(
        _rate_tally(
            [
                (
                    row["sport_key"] or NO_SPORT,
                    sport_labels.get(row["sport_key"], NO_SPORT),
                    result,
                    row,
                )
                for row, result in zip(rows, results, strict=True)
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: item.label,
    )

    # Le niveau n'existe que la ou il a ete renseigne : une **barre** « non
    # renseigne » ne dirait rien sur les matchs, seulement sur la saisie — un
    # taux moyen de tout ce qui n'a pas ete classe n'a aucune coherence
    # sportive. Le **compte**, lui, est une information juste, et il ferme
    # l'addition : la somme des niveaux plus `uncategorised` vaut `settled`.
    report.by_category = sorted(
        _rate_tally(
            [
                (row["category"], category_label(row["category"]), result, row)
                for row, result in zip(rows, results, strict=True)
                if row["category"]
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: category_rank(item.key),
    )
    report.uncategorised = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and not row["category"]
    )

    # Le « pourquoi ». L'ordre suit l'echelle et non l'effectif : « Manière »
    # apres « Issue », les sources du plus officiel au plus incertain, et
    # « Lecture seule » ferme la marche — c'est la lecture qu'on veut comparer
    # au reste, et la voir a sa place dans une echelle vaut mieux que de la
    # voir en tete parce qu'elle est la plus nombreuse.
    report.by_angle = sorted(
        _rate_tally(
            [
                (row["angle"], ANGLES[row["angle"]], result, row)
                for row, result in zip(rows, results, strict=True)
                if row["angle"] in ANGLES
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: list(ANGLES).index(item.key),
    )
    report.by_source = sorted(
        _rate_tally(
            [
                (row["source_level"], SOURCE_LEVELS[row["source_level"]], result, row)
                for row, result in zip(rows, results, strict=True)
                if row["source_level"] in SOURCE_LEVELS
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: list(SOURCE_LEVELS).index(item.key),
    )
    # **D'ou vient le prix, comme axe de mesure.** Il remplace la quarantaine :
    # au lieu d'ecarter les lignes servies par un book de reference, on regarde
    # si elles se comportent autrement. C'est une mesure, jamais un filtre — et
    # sans elle, lever la quarantaine reviendrait a melanger deux populations
    # sans plus aucun moyen de le voir.
    report.by_price_source = sorted(
        _rate_tally(
            [
                (row["price_source"], PRICE_SOURCES[row["price_source"]], result, row)
                for row, result in zip(rows, results, strict=True)
                if row["price_source"] in PRICE_SOURCES
            ],
            readable=report.minimum_rows,
        ),
        key=lambda item: list(PRICE_SOURCES).index(item.key),
    )
    report.unlabelled_price_source = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["price_source"] not in PRICE_SOURCES
    )
    report.unlabelled_angle = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["angle"] not in ANGLES
    )
    report.unlabelled_source = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["source_level"] not in SOURCE_LEVELS
    )
    report.unlabelled_confidence = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["confidence"] is None
    )
    report.unlabelled_market = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and not _market_key(row["market"])
    )

    markets = [
        (_market_key(row["market"]), (row["market"] or "").strip(), result, row)
        for row, result in zip(rows, results, strict=True)
        if _market_key(row["market"])
    ]
    # Un seul comptage, deux vues : la carte fine applique son seuil, le deplie
    # d'une famille non. Les recalculer separement les aurait fait diverger, et
    # la somme du deplie n'aurait plus tombe juste sur sa ligne de famille.
    tally = _rate_tally(markets, readable=report.minimum_rows)
    report.by_market = sorted(
        (entry for entry in tally if entry.settled >= ANALYSIS_MIN_MARKET),
        key=lambda item: (-item.settled, item.label),
    )
    report.hidden_markets = len(tally) - len(report.by_market)
    report.by_family, report.unclassified_markets = _by_family(tally, load_families(settings))

    # Chaque ligne recoit le reste de son axe, et chaque axe son verdict
    # d'ensemble. Fait **apres** l'assemblage de tous les axes et en un seul
    # endroit : rempli axe par axe, il aurait ete oublie au premier ajoute — le
    # piege exact de `RateRow.merge`, dont les deux fusions recopiees a la main
    # n'avaient pas suivi les champs ajoutes apres elles.
    _with_complements(report.groups)

    for row, result in zip(rows, results, strict=True):
        _count(report.played if row["played"] else report.skipped, result, row)

    # Le palier n'entre pas dans la comparaison : il est defini par des tranches
    # de cote, donc correle par construction a tout ce qui depend du prix. Le
    # signaler comme une redondance decouverte serait annoncer sa definition.
    axes_compares = [
        ("confidence", report.by_confidence),
        ("sport", report.by_sport),
        ("category", report.by_category),
        ("market", report.by_market),
        # Le « pourquoi » entre dans le detecteur comme les autres, et il en
        # a besoin plus qu'eux : un lot ou toutes les manieres se traduisent
        # en totaux ferait de « Manière » et « O/U » deux noms du meme
        # echantillon, et la page les presenterait comme deux constats.
        ("angle", report.by_angle),
        ("source", report.by_source),
    ]
    report.overlaps, report.partial_overlaps = _overlaps(axes_compares)
    report.overlap_matrix = overlap_matrix(axes_compares)

    # Le conflit entre l'angle declare et le marche rendu. Calcule a la lecture
    # comme la famille elle-meme : reclasser un marche reclasse tout
    # l'historique, et le figer sur la selection perdrait cette propriete.
    report.conflicts = conflicts(rows, load_families(settings), settings.tz)

    # Le residu au prix, sur **deux populations tenues separees**.
    #
    # Le chiffre de tete de la page ne porte que la premiere : une cote saisie
    # apres le coup d'envoi n'est pas un prix d'avant-match. La seconde est
    # publiee a cote et jamais additionnee — c'est leur **difference** qui est le
    # diagnostic. Mesure : residu nul sur les tardives (20 pour 20,25 payees),
    # -9,31 sur les autres. Un prix qui colle a ce point au resultat est un prix
    # releve en le connaissant, et c'est ce qui justifie l'exclusion plutot que
    # la simple precaution.
    def _releve(lignes: list[Any]) -> tuple[int, list[float]]:
        """Victoires observees et `1/cote` d'une population deja separee."""
        gagnees, implicites = 0, []
        for ligne in lignes:
            if str(ligne["result"]) not in ("win", "loss"):
                continue
            price = _column(ligne, "price")
            if not price or price <= 1.0:
                report.unpriced += 1
                continue
            implicites.append(1.0 / float(price))
            gagnees += str(ligne["result"]) == "win"
        return gagnees, implicites

    gagnees, implicites = _releve(rows)
    report.residual = Residual(observed=gagnees, implied=implicites)
    # Groupees par match, pour la borne conservatrice du bloc de tete.
    par_match: dict[Any, list[float]] = {}
    for row in rows:
        price = _column(row, "price")
        if str(row["result"]) in ("win", "loss") and price and price > 1.0:
            par_match.setdefault(row["event_id"] or f"seule-{row['id']}", []).append(1.0 / price)
    report.residual_clusters = list(par_match.values())
    # **Et les rencontres concernees, nommees.** Le compte disait qu'il faut
    # elargir les intervalles ; il ne disait pas ou aller regarder, alors que
    # c'est la seule chose actionnable — le prompt n'autorise une seconde ligne
    # que sur un angle reellement independant, et cette liste est ce qui permet
    # de le verifier. Elle sort du **meme** regroupement que la borne
    # conservatrice : deux comptages paralleles auraient fini par ne plus
    # designer les memes matchs.
    libelles = {
        row["event_id"]: affiche(_column(row, "home") or "", _column(row, "away") or "")
        for row in rows
        if row["event_id"] is not None
    }
    report.clustered_events = sorted(
        (
            (libelles.get(event_id) or f"match {event_id}", len(prix))
            for event_id, prix in par_match.items()
            if len(prix) > 1 and not str(event_id).startswith("seule-")
        ),
        key=lambda item: (-item[1], item[0]),
    )
    gagnees, implicites = _releve(tardifs)
    report.residual_late = Residual(observed=gagnees, implied=implicites)

    # Le croisement palier x confiance, pour l'ecart residuel entre les deux
    # echelles. Calcule ici parce que c'est le seul endroit qui voit les lignes
    # brutes : un `RateRow` ne connait que son propre axe.
    croise: dict[tuple[str, Any], list[int]] = {}
    for row in rows:
        resultat = str(row["result"])
        if resultat not in ("win", "loss") or row["confidence"] is None:
            continue
        cle = (_tier_of(row, TIER_DECLARED, bandes), row["confidence"])
        compte = croise.setdefault(cle, [0, 0])
        compte[0] += resultat == "win"
        compte[1] += 1
    if croise:
        dominant = max(
            {cle[1] for cle in croise},
            key=lambda niveau: sum(v[1] for k, v in croise.items() if k[1] == niveau),
        )
        report.conditional = sorted(
            (tuple(valeur) for cle, valeur in croise.items() if cle[1] == dominant and valeur[1]),
            key=lambda cell: -cell[1],
        )[:2]

    report.as_of = utcnow()
    report.gaps = _audit(report, tally)
    # Lu hors de la passe d'agregation : l'audit porte sur **toutes** les
    # selections enregistrees, pas sur celles que la page retient — une colonne
    # muette l'est aussi sur les lignes que le filtre d'anteriorite ecarte.
    report.column_gaps = column_gaps(settings)
    return report


def _audit(report: Analysis, tally: list[RateRow]) -> list[AxisGap]:
    """Verifie que chaque axe additionne bien toutes les selections tranchees.

    Le total, lui, ne peut pas baisser : il est compte sur les lignes brutes,
    sans jointure. Ce sont les **regroupements** qui peuvent perdre du monde —
    une cle inconnue, un champ nul, une jointure devenue stricte — et ils le
    font sans bruit. Le controle rend le silence impossible.

    `by_market` s'audite sur le comptage **complet** et non sur les lignes
    affichees : la carte en ecarte volontairement les marches vus une seule
    fois, et les compter comme perdus ferait crier a la panne sur une regle.
    """
    # Le temoin reste le compte brut ; ce sont les selections **ecartees faute
    # d'anteriorite** qui font la difference, et elles sont declarees comme
    # toutes les autres exclusions de cette page.
    total = report.recorded - report.without_antecedence
    axes = [
        # Plus aucune exclusion : la quarantaine a ete levee, la cote du bloc
        # faisant autorite. L'axe doit donc retomber **exactement** sur le total.
        ("Palier", sum(row.settled for row in report.by_tier), 0, "palier non résolu"),
        (
            "Origine du prix",
            sum(row.settled for row in report.by_price_source),
            report.unlabelled_price_source,
            "origine du prix non renseignée",
        ),
        (
            "Confiance",
            sum(row.settled for row in report.by_confidence),
            report.unlabelled_confidence,
            "confiance non annoncée",
        ),
        ("Sport", sum(row.settled for row in report.by_sport), 0, "sport non résolu"),
        (
            "Niveau",
            sum(row.settled for row in report.by_category),
            report.uncategorised,
            "compétition à classer",
        ),
        (
            "Marché",
            sum(row.settled for row in tally),
            report.unlabelled_market,
            "libellé de marché vide",
        ),
        (
            "Famille",
            sum(entry.rates.settled for entry in report.by_family),
            report.unclassified_markets + report.unlabelled_market,
            "marché à classer",
        ),
        (
            "Type d'angle",
            sum(row.settled for row in report.by_angle),
            report.unlabelled_angle,
            "type non renseigné",
        ),
        (
            "Niveau de source",
            sum(row.settled for row in report.by_source),
            report.unlabelled_source,
            "source non renseignée",
        ),
    ]
    return [
        AxisGap(axis=nom, missing=total - compte - declares, reason=motif)
        for nom, compte, declares, motif in axes
        if compte + declares != total
    ]


# -- Retour d'experience, pour le prompt ------------------------------------

#: Fenetre du retour : les N derniers picks tranches. Au-dela on parlerait
#: d'une autre saison, d'autres competitions et d'une autre facon de jouer.
FEEDBACK_WINDOW = 60

#: Sessions sur lesquelles se lit le taux de selection median injecte au prompt.
FEEDBACK_SESSIONS = 10

#: Sous ce nombre de sessions dotees d'un lot, aucun taux de selection n'est
#: publie. Meme regle que partout : une mediane sur deux valeurs est la moyenne
#: des deux, et sur une seule c'est cette session-la. Trois est le premier
#: nombre ou le mot « mediane » decrit autre chose que l'echantillon entier.
FEEDBACK_MIN_SESSIONS = 3

# Les trois seuils de publication — volume, ligne, etalement — vivent avec ceux
# de la page, plus haut : ils sont communs aux deux surfaces.


@dataclass
class FeedbackRow:
    """Un regroupement et son taux. Ni mise, ni gain, ni esperance.

    **Aucun champ de prix ici, et c'est deliberе** : l'ecart au taux implicite
    existe sur la page, ou il se lit a cote d'autres ecarts. L'injecter dans le
    prompt rapprocherait un taux de reussite d'une cote, c'est a dire calculerait
    une esperance — interdit de la section 9, et le fait que le chiffre vienne de
    l'historique de l'utilisateur n'y change rien.
    """

    key: str
    label: str
    won: int = 0
    lost: int = 0
    #: Bande cible, sur les seuls regroupements par confiance. C'est le seul
    #: referentiel du bloc qui parle de la **notation** plutot que des matchs :
    #: un sport ne se fixe pas d'objectif de taux, une confiance annoncee si —
    #: c'est meme sa definition.
    band: Band | None = None

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def rate(self) -> float | None:
        return None if self.settled == 0 else self.won / self.settled

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def gap(self) -> float | None:
        """Ecart en points a la borne la plus proche de la bande.

        None quand le taux tombe **dans** la bande : il n'y a alors rien a
        corriger, et ecrire « écart 0 pt » ferait chercher un probleme absent.
        """
        if self.band is None or not self.band.resolved or self.rate is None:
            return None
        borne_basse, borne_haute = self.band.low_absolute, self.band.high_absolute
        assert borne_basse is not None
        observed = self.rate * 100
        if observed < borne_basse:
            return observed - borne_basse
        if borne_haute is not None and observed > borne_haute:
            return observed - borne_haute
        return None

    @property
    def off_band(self) -> bool:
        """L'ecart est **confirme par l'intervalle**, pas seulement observe.

        Meme regle que la page, et elle compte plus encore ici : au volume
        courant presque chaque intervalle couvre plusieurs bandes, et faire
        resserrer une notation sur du bruit orienterait plus surement qu'aucun
        chiffre. La ligne dit alors l'ecart sans le mot.
        """
        bounds = self.interval
        return self.band is not None and bounds is not None and self.band.excludes(bounds)

    #: Largeur du libelle dans le prompt. Un nom de competition depasse volontiers
    #: la largeur d'un palier : sans troncature, une seule ligne longue casse
    #: l'alignement de tout le bloc et le rend penible a lire.
    LABEL_WIDTH = 20

    @property
    def line(self) -> str:
        """`🔴 GIGA FUN         2/14    14 %`, aligne comme le reste du prompt.

        Les lignes de confiance portent en plus leur bande cible et l'ecart :
        `confiance 3          25/63   40 %   cible 50 – 60 %, écart -10 pts`.
        """
        if self.rate is None:
            return self.label
        label = self.label
        if len(label) > self.LABEL_WIDTH:
            label = label[: self.LABEL_WIDTH - 1] + "…"
        compte = f"{self.won}/{self.settled}"
        line = f"{label:<{self.LABEL_WIDTH}} {compte:<7} {self.rate * 100:.0f} %"
        if self.band is None or not self.band.resolved:
            return line
        line += f"   cible {self.band.label}"
        if self.gap is not None:
            line += f", écart {self.gap:+.0f} pts"
            if self.off_band:
                line += ", hors bande"
        return line


@dataclass
class Feedback:
    """Ce que l'historique dit, tel qu'il entre dans le prompt.

    Le seul indicateur reste `gagnes / (gagnes + perdus)`, comme partout
    ailleurs dans ce module : aucun montant n'y entre, et rien ici ne se
    rapproche d'une cote — ce serait calculer une esperance.
    """

    settled: int = 0
    #: Journees d'analyse distinctes couvertes par ces selections.
    days: int = 0
    #: Competitions distinctes couvertes. **Troisieme condition du gate**, et il
    #: faut les trois : dix soirees de tennis satisfont le compte de journees
    #: sans satisfaire son intention — qu'un taux ne repose pas sur un seul
    #: contexte.
    competitions: int = 0
    window: int = FEEDBACK_WINDOW
    #: Selections tranchees **de tout l'historique**. Sans elle, `settled`
    #: plafonne a `window` et se lit comme un total : « 60 selections tranchees
    #: enregistrees » sur une base qui en porte cent a fait croire a une perte
    #: de donnees. Les deux nombres cote a cote rendent la fenetre visible.
    recorded: int = 0
    minimum: int = FEEDBACK_MIN_TOTAL
    minimum_days: int = FEEDBACK_MIN_DAYS
    minimum_competitions: int = FEEDBACK_MIN_COMPETITIONS
    #: Effectif d'une cellule sous lequel elle ne part pas. **Descend dans
    #: l'objet** comme les autres : une classe qui irait lire son propre reglage
    #: serait intestable hors d'une base.
    minimum_cell: int = FEEDBACK_MIN_CELL
    #: Une replication est en cours : aucun taux de reussite ne part dans le
    #: prompt, quel que soit le recul accumule.
    #:
    #: **Explicite, et surtout pas delegue a un seuil.** Le bloc est aujourd'hui
    #: tu par son volume et son etalement, mais un reglage abaisse le rouvrirait
    #: sans que personne s'en apercoive — et il l'a deja fait : 9 prompts de
    #: 3 sessions ont transmis des taux quand les seuils valaient encore 10 et 4,
    #: dont un annoncant « confiance 4 — 10/15, 67 % » juste avant que soient
    #: produites les etiquettes qu'on mesure aujourd'hui a 69 %.
    #:
    #: **Descend dans l'objet, comme `minimum`**, et n'est pas une propriete qui
    #: irait lire la constante du module : lue a l'acces, elle rendrait deux
    #: releves du meme lot indiscernables des qu'elle change entre les deux —
    #: donc la suspension intestable. Meme regle que le seuil d'effectif.
    suspended: bool = False
    by_tier: list[FeedbackRow] = field(default_factory=list)
    by_confidence: list[FeedbackRow] = field(default_factory=list)
    by_sport: list[FeedbackRow] = field(default_factory=list)
    #: Niveau de tournoi. Plus fourni que la competition — quatre Masters 1000
    #: font un echantillon la ou chacun pris seul n'en fait aucun.
    by_category: list[FeedbackRow] = field(default_factory=list)
    by_competition: list[FeedbackRow] = field(default_factory=list)
    by_market: list[FeedbackRow] = field(default_factory=list)
    #: Part mediane du lot effectivement selectionnee sur les dernieres
    #: sessions. C'est la seule grandeur du bloc qui ne parle pas de resultats
    #: mais du **tri** : le prompt annonce que passer est un resultat valable et
    #: attendu sur une partie du lot, et rien ne disait jusqu'ici ce qu'il en
    #: etait. Elle se donne comme un constat, jamais comme un objectif — se
    #: fixer un quota de passes ferait ecarter un match pour remplir un compte.
    selection_median: float | None = None
    #: Sessions derriere cette mediane. Le compte accompagne toujours le taux.
    selection_sessions: int = 0
    #: Taux global de la fenetre, en points. C'est la **reference des cibles
    #: relatives** : sans elle, une bande resolue s'afficherait sans qu'on sache
    #: contre quoi. `None` quand rien n'est tranche.
    global_rate: float | None = None

    @property
    def empty(self) -> bool:
        """Rien a dire du tout : le bloc disparait entierement du prompt.

        Le taux de selection entre dans la condition parce qu'il ne depend
        d'aucun resultat : une installation qui a monte trois sessions sans
        encore saisir un seul resultat a bien quelque chose a dire sur son tri.
        """
        return self.settled == 0 and self.selection_median is None

    @property
    def cell_gate_unreachable(self) -> bool:
        """Le seuil de cellule depasse la fenetre : **aucune cellule ne peut
        jamais passer**.

        `feedback()` ne lit que les `window` dernieres tranchees : une cellule en
        est un sous-ensemble, donc elle ne peut pas porter plus. Regle a 100 sur
        une fenetre de 60, le gate par cellule ne se ferme pas — il ne s'ouvre
        jamais, et le bloc rendrait exactement ce que rend un manque de recul.

        C'est le defaut caracteristique du projet applique a un reglage : une
        sortie identique pour « pas encore assez » et « jamais ». Il se dit donc,
        au lieu de se deviner.
        """
        return self.minimum_cell > self.window

    @property
    def scope_line(self) -> str:
        """« mes 60 dernières tranchées, sur 100 enregistrées ».

        Le second nombre n'est la que pour empecher de lire le premier comme un
        total. Il disparait tant que la fenetre ne mord pas.
        """
        base = f"mes {self.settled} dernière(s) sélection(s) tranchée(s)"
        return base if self.recorded <= self.settled else f"{base}, sur {self.recorded} au total"

    @property
    def missing_note(self) -> str:
        """Ce qui manque **exactement** : le volume, l'etalement, ou les deux.

        Le texte annoncait les deux seuils quel que soit celui qui bloquait, si
        bien qu'un bloc de 60 selections lisait « il en faudrait au moins 40 » —
        une phrase qui se contredit et fait chercher une panne la ou il n'y a
        qu'un etalement trop court.
        """
        volume = self.settled < self.minimum
        etalement = self.days < self.minimum_days
        contextes = self.competitions < self.minimum_competitions
        if volume and (etalement or contextes):
            return f"il en faudrait {self.minimum}, réparties sur {self.minimum_days} journées"
        if volume:
            return (
                f"l'étalement suffit ; c'est le volume qui manque — il en faudrait {self.minimum}"
            )
        if etalement:
            return (
                "le volume suffit ; c'est l'étalement qui manque — il faudrait "
                f"{self.minimum_days} journées d'analyse distinctes"
            )
        # **Troisieme cause, et elle ne se confond avec aucune des deux autres** :
        # dix journees sur un seul tournoi passent le compte de journees et ne
        # decrivent qu'un contexte. La nommer separement evite de faire chercher
        # du volume la ou il faut de la variete.
        return (
            "le volume et l'étalement suffisent ; c'est la variété qui manque — il faudrait "
            f"{self.minimum_competitions} compétitions distinctes"
        )

    @property
    def selection_line(self) -> str:
        """« 36 % en médiane, sur 6 sessions ». Vide sous le seuil.

        Le compte accompagne le taux, comme partout ailleurs : une mediane sur
        trois sessions et une mediane sur trente ne disent pas la meme chose.
        """
        if self.selection_median is None:
            return ""
        pluriel = "s" if self.selection_sessions > 1 else ""
        return (
            f"{self.selection_median * 100:.0f} % en médiane, "
            f"sur {self.selection_sessions} session{pluriel}"
        )

    @property
    def global_label(self) -> str:
        """« 50 % » — la reference des cibles, ecrite une fois pour le bloc."""
        return "—" if self.global_rate is None else f"{self.global_rate:.0f} %"

    @property
    def reach_line(self) -> str:
        """« 60 / 40 sélections tranchées · 4 / 10 journées distinctes ».

        C'est le **seul reglage dont l'effet est differe**, et le seul dont on ne
        pouvait pas mesurer la distance a l'activation : les deux nombres
        vivaient dans le code, l'ecran les citait en dur, et rien ne disait ou en
        etait le compte.
        """
        base = (
            f"{self.settled} / {self.minimum} sélection(s) tranchée(s) · "
            f"{self.days} / {self.minimum_days} journée(s) distincte(s) · "
            f"{self.competitions} / {self.minimum_competitions} compétition(s) distincte(s)"
        )
        if self.cell_gate_unreachable:
            base += (
                f" — aucune cellule ne passera : le seuil par cellule vaut "
                f"{self.minimum_cell} pour une fenêtre de {self.window}"
            )
        return base

    @property
    def missing_line(self) -> str:
        """Ce qu'il reste a franchir, ou ce qui bloque quand plus rien ne manque.

        **Le recul franchi ne suffit pas, et la phrase le disait mal.** Elle
        composait la liste de ce qui manque puis la joignait ; recul atteint sous
        suspension, la liste est **vide** et elle rendait « Il manque . Les taux
        ne sont pas transmis au prompt. » Une phrase cassee, et qui ne pouvait
        paraitre qu'au moment precis ou les deux seuils tombent — c'est-a-dire
        exactement le jour qu'on attend.

        Le compte a rebours doit donc dire **laquelle des deux conditions
        bloque**. Annoncer « il manque N journees » sans nommer la suspension
        serait un compte a rebours vers un evenement qui ne peut pas se produire :
        `FEEDBACK_SUSPENDED` est une constante, et la retourner demande de
        modifier le code.
        """
        if self.enough:
            return "Les taux sont transmis au prompt."
        manque = []
        if self.settled < self.minimum:
            manque.append(f"{self.minimum - self.settled} sélection(s) tranchée(s)")
        if self.days < self.minimum_days:
            manque.append(f"{self.minimum_days - self.days} journée(s) d'analyse")
        if self.competitions < self.minimum_competitions:
            manque.append(
                f"{self.minimum_competitions - self.competitions} compétition(s) distincte(s)"
            )
        if not manque:
            # Le recul est atteint : il ne manque plus rien, et rien ne part
            # quand meme. C'est le seul etat ou la cause est **entierement** une
            # decision, et il faut le dire comme tel.
            return (
                "Le recul est atteint. Les taux ne partent pas pour autant : leur "
                "transmission est retenue volontairement, et la rouvrir demande de "
                "modifier le code, pas un réglage."
            )
        reste = f"Il manque {' et '.join(manque)}."
        if self.suspended:
            # **Et ce n'est pas une note de bas de page.** Sans elle, la ligne
            # promet une transmission au franchissement du seuil, ce qui est
            # faux depuis que la suspension existe : elle ferait attendre un
            # evenement qui n'arrivera pas.
            return (
                f"{reste} Les taux ne sont pas transmis au prompt — et ils ne le "
                "seront pas au franchissement : leur transmission est en plus "
                "retenue volontairement."
            )
        return f"{reste} Les taux ne sont pas transmis au prompt."

    @property
    def any_band(self) -> bool:
        """Au moins un cran porte une cible.

        Sans elle, le paragraphe qui ordonne de resserrer un cran employe trop
        largement n'a rien a decrire : il expliquerait un mecanisme dont aucune
        ligne du bloc ne peut declencher l'action. C'est exactement le genre de
        texte que ce prompt passe son temps a retirer.
        """
        return any(row.band is not None and row.band.targeted for row in self.by_confidence)

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un pourcentage veuille dire quelque chose.

        Deux conditions, et il faut les deux : assez de selections, et assez de
        journees. Un lot nombreux mais concentre sur quelques jours mesure ces
        jours-la — un tournoi, une soiree de coupe d'Europe, une meteo — et le
        prompt le presenterait comme un ordre de passage durable.

        Une suspension prime sur les deux : voir `suspended`.
        """
        return (
            not self.suspended
            and self.settled >= self.minimum
            and self.days >= self.minimum_days
            and self.competitions >= self.minimum_competitions
        )


def _global_rate(rows: list[Any], results: list[str]) -> float | None:
    """Taux global des selections tranchees, en points. `None` s'il n'y en a pas.

    C'est la **reference des cibles relatives**, et elle se calcule sur la meme
    population que les taux auxquels elle sert de repere. Zero tranchee ne donne
    pas zero pour cent : elle ne donne rien, et une cible sans reference ne se
    resout pas.
    """
    tranchees = [result for result in results if result in ("win", "loss")]
    if not tranchees:
        return None
    return 100.0 * sum(1 for result in tranchees if result == "win") / len(tranchees)


def _feedback_tally(
    entries: list[tuple[str, str, str]], minimum: int = FEEDBACK_MIN_ROWS
) -> list[FeedbackRow]:
    """Agrege des triplets (cle, libelle, resultat) en lignes exploitables.

    Les regroupements trop peu fournis sont **ecartes**, et non pas marques : le
    bloc du prompt sert selon son propre texte a deux choses et a rien d'autre —
    dire ou chercher en premier, et ou relever l'exigence. Une ligne « effectif
    insuffisant » ne sert ni l'une ni l'autre. La page, elle, la garde : son
    lecteur n'est pas le meme.

    Les entrees arrivant du plus recent au plus ancien, le libelle retenu est la
    derniere orthographe employee.
    """
    grouped: dict[str, FeedbackRow] = {}
    for key, label, result in entries:
        row = grouped.setdefault(key, FeedbackRow(key=key, label=label))
        if result == "win":
            row.won += 1
        else:
            row.lost += 1
    return [row for row in grouped.values() if row.settled >= minimum]


def _selection_median(settings: Settings) -> tuple[float | None, int]:
    """Part mediane du lot selectionnee sur les `FEEDBACK_SESSIONS` dernieres.

    La mediane et non la moyenne : une session ou l'on n'a rien retenu — il y en
    a une dans l'historique reel, 0 sur 34 — tirerait une moyenne vers le bas
    au point de decrire une prudence qui n'existe pas le reste du temps.

    Seules les sessions **dotees d'un lot** comptent : une session qui n'a
    genere aucun prompt n'a rien soumis a l'analyse, et lui preter un lot de
    zero inventerait un taux.
    """
    known = lots(settings)
    if not known:
        return None, 0
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT s.id, "
            "  (SELECT COUNT(DISTINCT k.event_id) FROM picks k "
            "     WHERE k.session_id = s.id AND k.event_id IS NOT NULL) AS covered "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
            (FEEDBACK_SESSIONS,),
        ).fetchall()

    # Borne a 1 : une selection rattachee a un match hors du lot — le voisinage
    # propose au rattachement en offre — ferait sinon une part de lot au-dessus
    # de cent pour cent, qui ne veut rien dire. La page, elle, montre l'ecart.
    parts = sorted(
        min(1.0, int(row["covered"]) / lot.size)
        for row in rows
        if (lot := known.get(int(row["id"]))) and lot.size
    )
    if len(parts) < FEEDBACK_MIN_SESSIONS:
        return None, len(parts)
    middle = len(parts) // 2
    median = (
        parts[middle] if len(parts) % 2 else (parts[middle - 1] + parts[middle]) / 2  # noqa: E501
    )
    return median, len(parts)


def reach(settings: Settings | None = None) -> tuple[int, int, int]:
    """Les **trois** seuils du gate, tels qu'ils sont regles.

    Ecrits **une seule fois** et lus par les deux surfaces : sous quel compte un
    taux ne veut plus rien dire est une propriete des donnees, pas de l'endroit
    qui les affiche. Les copier des deux cotes les aurait fait diverger, et la
    page aurait fini par publier ce que le prompt refuse.
    """
    settings = settings or get_settings()
    return (
        threshold_value("feedback_min_total", settings),
        threshold_value("feedback_min_days", settings),
        threshold_value("feedback_min_competitions", settings),
    )


def feedback(settings: Settings | None = None, played_only: bool = False) -> Feedback:
    """Taux de reussite des dernieres selections tranchees, pour nourrir le prompt.

    Ferme la boucle du parcours : le prompt part, les picks reviennent, leurs
    resultats sont saisis — et la session suivante sait enfin ce qui a tenu.
    Sans cela l'analyse repart de zero a chaque fois et conseille un marche
    sans jamais apprendre qu'il ne passe pas.

    Par defaut, **toutes** les selections tranchees comptent, jouees ou non :
    ce bloc juge l'analyse, pas la discipline de mise. Une selection proposee
    puis ecartee dont on connait le resultat dit tout autant si l'angle etait
    bon. `played_only` restreint aux paris reellement poses, ce que mesure
    `stats()`.

    Les paris annules et ceux en attente sont exclus : une selection sans
    resultat n'apprend rien, et la compter au denominateur ferait mentir le taux.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        bandes = _bands(conn)
        rows = conn.execute(
            "SELECT k.tier, k.result, k.market, k.confidence, k.created_at, s.key AS sport_key, "
            # La cote declaree : c'est elle qui donne le palier de ce bloc. Voir
            # `_tier_of` et le choix de journal ci-dessous.
            "       k.price, "
            "       k.price_source, k.price_real, k.tier_real, "
            # `competition_id` et non le libelle : deux orthographes d'un meme
            # tournoi compteraient pour deux contextes, ce qui est exactement ce
            # que le garde-fou existe pour empecher.
            "       e.competition_id, "
            "       c.category, COALESCE(c.label, '') AS competition FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE k.result IN ('win', 'loss') AND k.exploratoire = 0 "
            + ("AND k.played = 1 " if played_only else "")
            + "ORDER BY k.created_at DESC, k.id DESC LIMIT ?",
            (FEEDBACK_WINDOW,),
        ).fetchall()

    # La journee d'analyse, et non celle du match : ce bloc juge des decisions,
    # et deux paris pris le meme soir sur deux jours de calendrier restent une
    # seule seance de travail.
    with connect(settings) as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) AS n FROM picks WHERE result IN ('win', 'loss') "
            "AND exploratoire = 0" + (" AND played = 1" if played_only else "")
        ).fetchone()["n"]

    minimum, minimum_days, minimum_competitions = reach(settings)
    # **Seuil de transmission, pas de lecture.** Une cellule part dans le prompt
    # bien plus tard qu'elle ne s'affiche sur la page : le lecteur d'une page voit
    # l'effectif a cote du taux, le prompt ne voit que le taux.
    minimum_rows = threshold_value("feedback_min_cell", settings)
    report = Feedback(
        minimum_cell=minimum_rows,
        settled=len(rows),
        days=len({str(row["created_at"])[:10] for row in rows}),
        competitions=len({row["competition_id"] for row in rows if row["competition_id"]}),
        recorded=int(recorded),
        minimum=minimum,
        minimum_days=minimum_days,
        minimum_competitions=minimum_competitions,
        suspended=FEEDBACK_SUSPENDED,
        global_rate=_global_rate(rows, [str(row["result"]) for row in rows]),
    )
    # Le taux de selection est publie **hors** des trois garde-fous ci-dessous,
    # et ce n'est pas un oubli : eux protegent des taux de reussite, qui
    # mesurent des issues. Celui-ci decrit un comportement — comment je trie —
    # et une part du lot ne devient pas trompeuse parce que les resultats
    # manquent. Meme exemption que `labelling()` sur la page.
    report.selection_median, report.selection_sessions = _selection_median(settings)

    if not report.enough:
        # En dessous d'un des deux seuils, on ne publie aucun detail : le prompt
        # dira ce qui manque, ce qui vaut mieux qu'un pourcentage trompeur.
        return report

    # Meme regle que sur la page : le palier se lit sur la cote obtenue quand
    # elle existe, sinon sur celle du bloc — qui fait autorite, le gabarit le dit
    # lui-meme. Plus personne n'attend un prix reel qui ne viendra pas.
    report.by_tier = _feedback_tally(
        [
            # **Journal d'analyse, sans condition.** Ce bloc est le retour
            # d'experience injecte au prompt : il affiche la cible de chaque cran
            # et l'ecart au taux constate, donc il juge la notation. Le palier s'y
            # recalcule depuis la cote declaree, comme dans `analysis()`.
            #
            # La variante « declare par defaut, execute sous `played_only` » a ete
            # ecartee : elle couplerait la source du palier a un filtre de
            # population, donc une meme ligne se classerait differemment selon
            # l'appelant — le motif a plusieurs verites que ce lot vient de fermer.
            (cle, tier_labels.get(cle, cle), row["result"])
            for row in rows
            for cle in (_tier_of(row, TIER_DECLARED, bandes),)
        ],
        minimum_rows,
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )

    report.by_confidence = _feedback_tally(
        [
            (str(row["confidence"]), f"confiance {row['confidence']}", row["result"])
            for row in rows
            if row["confidence"] is not None
        ],
        minimum_rows,
    )
    report.by_confidence.sort(key=lambda item: item.key, reverse=True)
    # La bande cible se rattache ici et nulle part ailleurs, comme sur la page :
    # un sport ou un marche ne se fixe pas d'objectif de taux. Sans elle,
    # « confiance 4 » n'etait qu'un nombre sans referentiel, et le prompt
    # affirmait pourtant qu'un ecart disait la derive de la notation.
    # **Sur la meme fenetre glissante que les taux compares.** Si les deux
    # divergeaient, l'ecart ne voudrait rien dire : on rapporterait un taux des
    # soixante dernieres a une moyenne de tout l'historique.
    bands = load_bands(settings, reference=report.global_rate)
    for entry in report.by_confidence:
        entry.band = bands.get(int(entry.key)) if entry.key.isdigit() else None

    report.by_sport = sorted(
        _feedback_tally(
            [
                (
                    row["sport_key"] or NO_SPORT,
                    sport_labels.get(row["sport_key"], NO_SPORT),
                    row["result"],
                )
                for row in rows
            ],
            minimum_rows,
        ),
        key=lambda item: item.label,
    )

    report.by_market = sorted(
        _feedback_tally(
            [
                (_market_key(row["market"]), (row["market"] or "").strip(), row["result"])
                for row in rows
                if _market_key(row["market"])
            ],
            minimum_rows,
        ),
        key=lambda item: (-item.settled, item.label),
    )

    # Par niveau de tournoi : entre le sport et la competition. « Tennis » est
    # trop large, « ATP Canadian Open » trop etroit pour tenir un echantillon —
    # les Masters 1000 pris ensemble en font un.
    report.by_category = sorted(
        _feedback_tally(
            [
                (row["category"], category_label(row["category"]), row["result"])
                for row in rows
                if row["category"]
            ],
            minimum_rows,
        ),
        key=lambda item: category_rank(item.key),
    )

    # Par competition : c'est la reponse a « quel type de match ». Un taux par
    # sport melange une Ligue 1 lisible et un championnat scandinave d'ete.
    report.by_competition = sorted(
        _feedback_tally(
            [
                (row["competition"], row["competition"], row["result"])
                for row in rows
                if row["competition"]
            ],
            minimum_rows,
        ),
        key=lambda item: (-item.settled, item.label),
    )

    logger.info(
        "Retour d'experience : %d pari(s) tranche(s) sur les %d derniers, %d journee(s)",
        report.settled,
        FEEDBACK_WINDOW,
        report.days,
    )
    return report
