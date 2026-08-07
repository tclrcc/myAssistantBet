"""Libelles d'evenements, de bookmakers et de marches, partages entre services.

Le cyclisme (une etape) et les marches outright n'ont pas de second
participant. Chaque service qui recopiait la concatenation risquait d'oublier
la garde et d'afficher un tiret orphelin : elle vit ici, une seule fois.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from ..providers.oddsapi import DEFAULT_BOOKMAKER

#: Nom lisible d'un bookmaker. Une cle inconnue est rendue telle quelle.
BOOKMAKER_LABELS = {
    "betclic_fr": "Betclic",
    "manual": "saisie manuelle",
    # Books de reference : ils comblent les marches que Betclic ne sert pas via
    # l'API. Le suffixe « (ref.) » est porte jusque dans le prompt — une cote de
    # reference situe le marche, elle n'est pas jouable telle quelle.
    "pinnacle": "Pinnacle (ref.)",
    "unibet_nl": "Unibet NL (ref.)",
    "matchbook": "Matchbook (ref.)",
    "onexbet": "1xBet (ref.)",
    "betonlineag": "BetOnline (ref.)",
    "gtbets": "GTbets (ref.)",
    "nordicbet": "NordicBet (ref.)",
    "pmu_fr": "PMU (ref.)",
    # Substituts releves chez API-Football, pour les matchs que The Odds API ne
    # sert pas du tout. Betclic n'est pas au catalogue de ce fournisseur : ces
    # prix ne sont donc jamais jouables, d'ou le meme suffixe. L'ordre de
    # preference est mesure (`Settings.apifootball_bookmakers`).
    "888sport": "888Sport (ref.)",
    "william_hill": "William Hill (ref.)",
    "betvictor": "BetVictor (ref.)",
    "10bet": "10Bet (ref.)",
    "bet365": "Bet365 (ref.)",
    "superbet": "Superbet (ref.)",
}

#: Bookmakers pour lesquels un horodatage de releve n'aurait aucun sens : la
#: saisie manuelle porte l'heure de la frappe, pas celle d'un releve de marche.
UNTIMED_BOOKMAKERS = frozenset({"manual"})

#: Pictogramme de chaque ligne du bloc CONTEXTE, pour la fiche d'un match.
#: Purement decoratif : le libelle reste ecrit a cote, et le prompt genere ne
#: porte aucun de ces caracteres — il compte ses tokens.
CONTEXT_ICONS = {
    "Classement": "🏆",
    "Forme 5": "📈",
    "Dom/Ext": "🏟️",
    "Entraineur": "🧑‍🏫",
    "Buts marq.": "⚽",
    "Clean sheet": "🧱",
    "1re MT": "⏱️",
    "Formations": "🧩",
    "Corners": "🚩",
    "Cartons": "🟨",
    # Le rapprochement se fait sur le premier mot : « Cartons tps » heriterait
    # sinon du pictogramme de « Cartons », alors que la ligne dit *quand* ils
    # tombent et non combien.
    "Cartons tps": "🕐",
    "Tirs": "🎯",
    "Stats match": "🚫",
    "Compos": "📋",
    "Absents": "🩹",
    "H2H": "⚔️",
    "Tour": "🪜",
    "Repos": "🛌",
    "Elo": "♟️",
    "H2H ici": "📍",
    "Palmares": "🏅",
    "Forme": "📈",
    "Surface": "🎾",
    "Abandons": "🚑",
    "Profil": "🗺️",
    "Startlist": "📋",
    "References": "🔗",
    "Infos": "📝",
}


def context_icon(label: str) -> str:
    """Pictogramme d'une ligne de contexte, vide si elle n'en a pas.

    Le rapprochement se fait sur le debut du libelle : « H2H (5) » porte son
    nombre de confrontations, qui varie d'un match a l'autre.
    """
    head = (label or "").split(" ")[0]
    return CONTEXT_ICONS.get(label) or CONTEXT_ICONS.get(head, "")


#: Sports dont le sprite `base.html` porte un pictogramme. Sur un board de
#: soixante-seize lignes, le sport se lit d'un coup d'oeil sans occuper une
#: colonne entiere de texte ; le libelle reste porte par `title` et par un texte
#: masque, un pictogramme seul n'etant pas lisible au lecteur d'ecran.
#:
#: **Des SVG, plus des emoji.** Un emoji est rendu par la police de l'appareil :
#: different d'une machine a l'autre, absent de certaines — la colonne se vidait
#: alors sans rien dire — et il ne prend jamais la couleur de sa pastille.
SPORT_ICONS = frozenset({"football", "tennis", "cycling"})


def has_sport_icon(key: str | None) -> bool:
    """Vrai si le sprite porte un pictogramme pour ce sport."""
    return (key or "") in SPORT_ICONS


def sort_key(text: str | None) -> str:
    """Cle de tri d'un libelle affiche : sans accent, sans casse.

    Un tri brut place « Serie A » avant « Série A » et « Ligue 2 » apres
    « Ligue 1 » mais aussi apres « ligue des champions » : dans une liste
    deroulante de cent competitions, l'oeil cesse de suivre. Les accents et la
    casse ne doivent pas peser sur l'ordre alphabetique.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def affiche(home: str, away: str | None) -> str:
    """Affiche d'un evenement, sans tiret orphelin quand `away` est absent."""
    return f"{home} – {away}" if away else home


def bookmaker_label(key: str | None) -> str:
    """Nom lisible d'un bookmaker, la cle brute a defaut."""
    return BOOKMAKER_LABELS.get(key or "", key or "—")


def primary_book(books: Sequence[str]) -> str:
    """Source principale d'un evenement : celle dont les cotes sont jouables.

    Betclic s'il a servi quoi que ce soit, sinon le premier book releve. La
    saisie manuelle n'arrive qu'en dernier : elle complete un releve, elle ne le
    remplace pas — sauf sur un evenement entierement saisi a la main, ou elle
    est bien la source principale.
    """
    if DEFAULT_BOOKMAKER in books:
        return DEFAULT_BOOKMAKER
    timed = [book for book in books if book not in UNTIMED_BOOKMAKERS]
    return next(iter(timed or list(books)), "")
