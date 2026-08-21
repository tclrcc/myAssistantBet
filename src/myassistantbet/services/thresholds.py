"""Seuils numeriques regles par l'utilisateur.

Certaines regles du prompt et du rendu dependent d'un nombre qui n'est ni une
constante du projet ni une donnee : « a partir de combien de matchs un lot
porte-t-il deux combines », « a partir de quelle avancee de saison un enjeu de
classement veut-il dire quelque chose ». Ces nombres sont des **decisions de
l'utilisateur**, au meme titre que les bandes de cote ou de confiance, et les
coder en dur obligerait a redeployer pour changer d'avis.

Ils vivent donc dans `preferences`, la table cle/valeur qui porte deja les
consignes permanentes. Un registre les declare — libelle, defaut, bornes,
raison — pour que l'ecran des reglages les rende sans les connaitre un par un,
et pour qu'un seuil ajoute demain n'ait pas a toucher au gabarit.

**Une valeur illisible vaut le defaut**, jamais une erreur : un seuil est une
preference d'affichage, et refuser de servir une page parce qu'un nombre a ete
mal saisi serait hors de proportion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Threshold:
    """Un seuil reglable, et ce qu'il decide."""

    key: str
    label: str
    default: int
    low: int
    high: int
    #: Ce que le seuil change, en une phrase. Rendu sous le champ : un nombre
    #: sans son effet ne se regle pas, il se subit.
    note: str
    #: Sur combien d'observations la valeur a ete mesuree, et la date a laquelle
    #: elle doit l'etre a nouveau. **Un « provisoire » non date devient permanent
    #: par oubli** : la valeur reste, la raison de la revoir s'efface, et
    #: personne ne sait plus qu'elle attendait du volume.
    #:
    #: Rendus en clair a cote du champ, jamais noyes dans la note : un nombre
    #: mesure sur quatre journees et un nombre mesure sur quarante ne se reglent
    #: pas de la meme main.
    measured_on: str = ""
    remeasure_on: str = ""

    @property
    def provisional(self) -> bool:
        return bool(self.remeasure_on)


#: Cle de preference : le prefixe evite qu'un seuil et une consigne de texte se
#: disputent un nom.
PREFIX = "seuil_"

THRESHOLDS: dict[str, Threshold] = {
    "combo_min_lot": Threshold(
        key="combo_min_lot",
        label="Lot minimum pour deux combinés",
        default=6,
        low=2,
        high=40,
        note=(
            "En dessous, le prompt ne demande qu'un seul combiné. Sur un lot de 5 matchs "
            "et un taux de sélection médian de 36 %, réclamer un combiné de 3-4 jambes "
            "et un second de 4-5 jambes — une seule sélection par match — était "
            "insatisfiable avant même que l'analyse commence."
        ),
    ),
    "combo_solo_min_lot": Threshold(
        key="combo_solo_min_lot",
        label="Lot minimum pour un combiné",
        default=5,
        low=2,
        high=20,
        note=(
            "En dessous, le prompt ne demande **aucun** combiné. Le seuil des deux combinés "
            "avait son symétrique manquant : sur un lot de 4 matchs et un taux de sélection "
            "médian de 36 %, l'espérance tourne autour de 1.4 sélection, quand la section D "
            "en réclame trois indépendantes. Mieux vaut supprimer la demande que faire écrire "
            "qu'elle était insatisfiable."
        ),
    ),
    "combo_court_jambes": Threshold(
        key="combo_court_jambes",
        label="Jambes visées — combiné solide",
        default=4,
        low=2,
        high=20,
        note=(
            "Le nombre de jambes est un paramètre et non une conséquence : « cote ≥ 100 » "
            "se satisfait par 5 jambes à 2.50 comme par 10 à 1.55, et ce sont deux objets "
            "sans rapport. Ce combiné-ci est le court et concentré."
        ),
    ),
    "combo_court_cote": Threshold(
        key="combo_court_cote",
        label="Cote cible — combiné solide",
        default=9,
        low=2,
        high=10_000,
        note=(
            "Une cible, jamais un plancher : le prompt interdit déjà d'ajouter une jambe "
            "pour atteindre une fourchette, et demande alors un combiné plus court avec sa "
            "cote réelle."
        ),
    ),
    "combo_long_cote": Threshold(
        key="combo_long_cote",
        label="Cote cible — combiné frisson",
        default=100,
        low=2,
        high=10_000,
        note=(
            "Mesuré sur les 6 sessions offrant 10 jambes sûres ou plus : les dix meilleures "
            "cotes donnent de 302 à 1396, médiane 565. « ≥ 100 » est donc confortable — ce "
            "qui contraint n'est pas la cote mais le nombre de sélections produites, 4 "
            "sessions sur 10 n'atteignant pas dix jambes. Le combiné long n'a pas de nombre "
            "de jambes visé : il prend ce que le lot autorise et s'arrête au premier des "
            "trois motifs, cible atteinte, plafond du lot, ou plus de jambe à confiance 3."
        ),
    ),
    "combo_maillon_jambes": Threshold(
        key="combo_maillon_jambes",
        label="Jambes à partir desquelles un combiné est « long »",
        default=6,
        low=3,
        high=20,
        note=(
            "En dessous, la section D demande le maillon le plus fragile. À partir de ce "
            "nombre elle ne le demande plus — sur dix jambes toutes décisives la question ne "
            "veut plus rien dire — et fait nommer les jambes du palier haut, celles par "
            "lesquelles la cote a été achetée. Le défaut vaut 6 parce que tous les combinés "
            "que le gabarit a demandés jusqu'ici tiennent en 3 à 5 jambes : c'est le premier "
            "compte qui sorte de son propre vocabulaire."
        ),
    ),
    "feedback_min_total": Threshold(
        key="feedback_min_total",
        label="Sélections tranchées avant transmission des taux",
        default=40,
        low=10,
        high=500,
        note=(
            "En dessous, le prompt annonce qu'il manque du recul et ne transmet aucun taux. "
            "Relevé à 40 après observation : à 17 sélections tranchées, le bloc publiait "
            "« ATP 2/6 contre WTA 5/7 » — treize matchs d'un seul tournoi, joués la même "
            "nuit. Présenté comme un ordre de passage, un chiffre faux oriente plus sûrement "
            "que pas de chiffre du tout."
        ),
    ),
    "feedback_min_days": Threshold(
        key="feedback_min_days",
        label="Journées d'analyse distinctes avant transmission",
        default=10,
        low=3,
        high=100,
        note=(
            "L'autre moitié du même garde-fou, et il faut les deux : soixante sélections "
            "prises en quatre jours mesurent ces quatre jours-là — un tournoi, une soirée de "
            "coupe, une météo — et non une façon d'analyser. C'est la journée de décision "
            "qui compte, pas celle du match."
        ),
    ),
    "feedback_min_rows": Threshold(
        key="feedback_min_rows",
        label="Sélections tranchées avant qu'un regroupement affiche un taux",
        default=8,
        low=2,
        high=100,
        note=(
            "Un regroupement moins fourni ne mesure que le hasard. Le seuil est unique, mais "
            "les deux surfaces n'en font pas le même usage et c'est voulu : la page garde la "
            "ligne et affiche son effectif à la place du taux — un humain doit savoir qu'une "
            "case est vide parce qu'elle est maigre et non parce qu'elle est nulle — quand le "
            "prompt la tait, un « effectif insuffisant » n'y servant ni à dire où chercher, "
            "ni où relever l'exigence."
        ),
    ),
    "enjeu_min_journees": Threshold(
        key="enjeu_min_journees",
        label="Journées avant qu'un classement compte",
        default=8,
        low=1,
        high=20,
        note=(
            "En dessous, les lignes « Classement » et « Enjeu » sont datées et marquées "
            "« indicatif ». À la 3e journée sur 32, « Relegation Playoffs » décrit l'ordre "
            "alphabetique autant que le niveau — et le prompt ordonne de la recopier comme "
            "l'enjeu réel, sans recherche. Un rang n'en dit pas plus : deux 5es séparés par "
            "une division sortent à égalité apparente. Environ un quart d'une saison "
            "ordinaire."
        ),
    ),
    "recherche_dossiers": Threshold(
        key="recherche_dossiers",
        label="Dossiers couverts en recherche approfondie",
        default=10,
        low=2,
        high=30,
        note=(
            "Le prompt ouvre une fiche « À CHERCHER EN PRIORITÉ » et y porte "
            "min(ce nombre, la taille du lot) dossiers. Mesuré sur un lot réel de 21 "
            "manches retour : 3 dossiers ont été traités, choisis au jugé, et 18 sélections "
            "sont retombées en « lecture » donc à confiance 1. Le budget de requêtes d'une "
            "session est fini — ce seuil dit combien il en couvre, et c'est une propriété "
            "du lecteur, pas des données. **C'est un plafond, jamais un objectif.** Il "
            "borne aussi le nombre de jambes d'un combiné, qui réclament chacune un fait "
            "daté donc un dossier ouvert : sur 170 prompts archivés, il l'a fait 47 fois, "
            "soit sur tout lot de 11 blocs ou plus. Il ne borne en revanche **jamais** les "
            "paliers hauts — les trois n'offrent que 6 places quand ce seuil en ouvre 10, "
            "mesuré 0 fois sur 170 — et le §2a du lot 19 éloigne encore ce cas, un palier "
            "haut offert prenant désormais au moins une place."
        ),
    ),
    "mise_unite_bp": Threshold(
        key="mise_unite_bp",
        label="Unité de mise (centièmes de % de bankroll)",
        default=25,
        low=1,
        high=1000,
        note=(
            "L'unité de référence d'une sélection de section C, en centièmes de pour-cent "
            "de la bankroll : 25 vaut 0,25 %. **Elle se mesure, elle ne se choisit pas** — "
            "c'est le plafond divisé par le 90e centile des journées d'analyse. Mesure du "
            "20/08/2026 sur le régime actuel (4 journées, section C seule) : P90 = 20,4 "
            "sélections, donc 5 % / 20,4 = 0,245 %, arrondi à 0,25 % pour que le plafond "
            "tombe sur un compte entier de sélections. **Provisoire** : quatre journées, "
            "quand un 90e centile défendable en demande une dizaine — à re-mesurer vers le "
            "20/09/2026."
        ),
        measured_on="4 journées d'analyse (17 – 20/08/2026)",
        remeasure_on="2026-09-20",
    ),
    "mise_plafond_bp": Threshold(
        key="mise_plafond_bp",
        label="Plafond de journée (centièmes de % de bankroll)",
        default=500,
        low=10,
        high=5000,
        note=(
            "Ce qu'une **journée** engage au plus, tous paris confondus : 500 vaut 5 %. "
            "Divisé par l'unité, il donne le nombre de sélections au-delà duquel la "
            "réduction s'applique — vingt au réglage servi, et c'est ce compte-là qui se "
            "vérifie de tête. **Par journée et non par session** : un plafond de session se "
            "contournerait en découpant, et le découpage doit rester gratuit — c'est une "
            "bonne pratique d'analyse, la coupler au garde-fou d'argent en ferait un "
            "multiplicateur d'exposition. La journée se compte sur la date de la sélection, "
            "jamais sur l'heure de coup d'envoi. **Le plafond ne choisit pas la sécurité, il "
            "choisit l'échelle** : le taux de déclenchement est fixé par l'ancrage au 90e "
            "centile, pas par sa valeur."
        ),
    ),
    "cadre_max": Threshold(
        key="cadre_max",
        label="Cadre maximum d'un prompt (tokens)",
        default=10_000,
        low=1_000,
        high=60_000,
        note=(
            "Au-dela, la generation previent. **Le cadre est ce qui se paie une fois par "
            "prompt quel que soit le lot** — preambule, mode d'emploi, sections de sortie — "
            "et c'est lui seul qui doit alerter : un lot de vingt-et-un blocs pese "
            "legitimement 21 707 tokens, et une alarme posee sur le total se declencherait "
            "sur la taille du lot. **Rien n'est refuse** : un prompt long ne gene pas, et "
            "refuser de servir une page pour un depassement serait hors de proportion."
        ),
        measured_on=(
            "172 prompts archives — cadre de 8 048 tokens le 10/08, 12 160 le 15/08, "
            "15 232 le 20/08, soit environ 700 par jour. Le defaut mord donc sur le "
            "regime courant, et c'est le constat qu'il existe pour porter : jusqu'ici "
            "les deux budgets vivaient dans les tests et ne voyaient jamais un lot reel."
        ),
        remeasure_on="apres la coupe du gabarit — son extinction en sera la preuve",
    ),
    "mise_combine_pct": Threshold(
        key="mise_combine_pct",
        label="Mise d'un combiné (% d'une unité)",
        default=50,
        low=0,
        high=200,
        note=(
            "Ce qu'un combiné porte, en pour-cent d'une unité : 50 vaut une demi-unité. "
            "Il pèse pour rien dans l'addition — deux combinés existent en base, à une "
            "demi-unité pièce contre 12 à 21 pour les simples — donc ce réglage ne déplace "
            "pas le plafond. **Les sélections de section C-bis ne sont pas réglables ici** : "
            "elles ne reçoivent aucune mise, et c'est un arbitrage de principe et non de "
            "calibrage — en faire un seuil inviterait à le rouvrir."
        ),
    ),
}


def value_of(key: str, settings: Settings | None = None) -> int:
    """Valeur reglee d'un seuil, ou son defaut.

    Une valeur absente, illisible ou hors bornes rend le defaut : un seuil mal
    saisi doit degrader vers un comportement connu, pas casser une generation
    de prompt.
    """
    threshold = THRESHOLDS[key]
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (PREFIX + key,)
        ).fetchone()
    if row is None:
        return threshold.default
    try:
        value = int(str(row["value"]).strip())
    except (TypeError, ValueError):
        logger.warning("Seuil %s illisible (%r) : valeur par defaut", key, row["value"])
        return threshold.default
    return value if threshold.low <= value <= threshold.high else threshold.default


def current(settings: Settings | None = None) -> list[tuple[Threshold, int]]:
    """Tous les seuils et leur valeur courante, pour l'ecran des reglages."""
    settings = settings or get_settings()
    return [(entry, value_of(key, settings)) for key, entry in THRESHOLDS.items()]


def save(key: str, raw: str, settings: Settings | None = None) -> None:
    """Enregistre un seuil. Hors bornes ou illisible, il revient au defaut.

    Le retour au defaut est **ecrit en base** plutot que laisse a la lecture :
    sans cela, le champ afficherait la valeur par defaut alors que la table
    porte encore la saisie refusee, et la prochaine relecture repartirait dessus.
    """
    if key not in THRESHOLDS:
        return
    threshold = THRESHOLDS[key]
    try:
        value = int((raw or "").strip())
    except (TypeError, ValueError):
        value = threshold.default
    value = value if threshold.low <= value <= threshold.high else threshold.default
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "                               updated_at = excluded.updated_at",
            (PREFIX + key, str(value), utcnow()),
        )
    logger.info("Seuil %s : %d", key, value)


# -- Les interrupteurs -------------------------------------------------------
#
# **Un seuil regle un nombre, un interrupteur ouvre ou ferme une surface.** Les
# forcer dans le meme registre aurait demande d'inventer des bornes a un
# booleen ; ils vivent donc a cote, dans la meme table et sous le meme prefixe.


@dataclass(frozen=True)
class Toggle:
    """Un reglage a deux etats, et ce qu'il decide."""

    key: str
    label: str
    default: bool
    #: Ce que l'interrupteur change, en une phrase. Meme regle qu'un seuil : un
    #: reglage sans son effet ne se regle pas, il se subit.
    note: str


#: Le suivi de l'argent. **Rallume le 20/08/2026, et le constat qui l'avait
#: eteint a change** : il avait ete pose sur une mesure — aucun pari pose,
#: `coupons` vide, `played` faux sur 235 selections — et cette mesure decrivait
#: un usage, pas une regle. L'usage a change, l'interrupteur suit.
#:
#: **Ce qu'il ouvre desormais**, en plus du bloc de paris poses : la section G du
#: gabarit et le journal des mises. Le gate n'est pas cosmetique — la section
#: pese **592 tokens de cout fixe** sur chaque prompt, et la faire payer a qui ne
#: mise pas serait exactement ce que les portes du preambule existent pour
#: eviter.
#:
#: **Ce qu'il n'ouvre pas, et ne doit jamais ouvrir** : la mesure d'analyse. Le
#: residu au prix, les crans et les intervalles ne connaissent aucun montant,
#: quel que soit l'etat de cet interrupteur — c'est une separation de tables,
#: gardee par un test qui lit la source, pas un reglage.
COUPON_TRACKING = "suivi_coupons"

TOGGLES: dict[str, Toggle] = {
    COUPON_TRACKING: Toggle(
        key=COUPON_TRACKING,
        label="Suivi de l'argent",
        default=True,
        note=(
            "Ouvre le bloc « Ce que valent tes paris » de la page de statistiques, la "
            "colonne « cote obtenue », le bouton « jouer » de la feuille de session, la "
            "**section G du gabarit** (répartition de mise) et le journal des mises. "
            "Désactivé, l'application ne mesure que les prédictions et le prompt "
            "économise 592 tokens de coût fixe. **La mesure d'analyse ne dépend pas de "
            "cet interrupteur** : le résidu au prix, les crans et les intervalles "
            "ignorent les montants dans les deux états, et c'est une séparation de "
            "tables, pas un réglage."
        ),
    ),
}


def toggle_of(key: str, settings: Settings | None = None) -> bool:
    """Etat d'un interrupteur, ou son defaut.

    Une valeur illisible vaut le defaut, jamais une erreur — meme regle que les
    seuils : un reglage mal saisi degrade vers un comportement connu.
    """
    toggle = TOGGLES[key]
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (PREFIX + key,)
        ).fetchone()
    if row is None:
        return toggle.default
    valeur = str(row["value"]).strip().lower()
    if valeur in ("1", "true", "oui"):
        return True
    if valeur in ("0", "false", "non"):
        return False
    logger.warning("Interrupteur %s illisible (%r) : valeur par defaut", key, row["value"])
    return toggle.default


def toggles(settings: Settings | None = None) -> list[tuple[Toggle, bool]]:
    """Tous les interrupteurs et leur etat, pour l'ecran des reglages."""
    settings = settings or get_settings()
    return [(entry, toggle_of(key, settings)) for key, entry in TOGGLES.items()]


def save_toggle(key: str, raw: str, settings: Settings | None = None) -> None:
    """Enregistre un interrupteur. Une case non cochee n'est pas postee du tout,
    donc l'absence vaut faux — c'est la convention des formulaires HTML, et la
    contourner demanderait un champ cache que rien ne justifie."""
    if key not in TOGGLES:
        return
    etat = "1" if str(raw or "").strip().lower() in ("1", "true", "on", "oui") else "0"
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "                               updated_at = excluded.updated_at",
            (PREFIX + key, etat, utcnow()),
        )
    logger.info("Interrupteur %s : %s", key, etat)
