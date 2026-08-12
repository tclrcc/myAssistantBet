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
#: La valeur est l'identifiant d'un `<symbol>` du sprite de `base.html`, sans
#: son prefixe `i-`. Purement decoratif : le libelle reste ecrit a cote, et le
#: prompt genere n'en porte aucune trace — il compte ses tokens.
#:
#: **Des SVG, plus des emoji**, meme raison que pour les sports : un emoji est
#: rendu par la police de l'appareil, donc absent de certaines, et il ne prend
#: pas la couleur de son entourage. Un theme clair l'aurait rendu criard la ou
#: `currentColor` suit sagement le texte de l'etiquette.
#:
#: Plusieurs libelles partagent volontiers un pictogramme : « Forme » et
#: « Forme 5 » decrivent la meme chose a deux echelles, « Compos » et
#: « Startlist » sont deux noms d'une liste de partants.
CONTEXT_ICONS = {
    # Un match que le fournisseur ne donne pas jouable a l'heure annoncee. Le
    # panneau d'interdiction est le bon signe : la ligne ne decrit pas le match,
    # elle dit qu'il n'aura pas lieu.
    "Statut": "interdit",
    "Classement": "trophee",
    "Enjeu": "enjeu",
    "Palmares": "medaille",
    "Forme 5": "courbe",
    "Forme": "courbe",
    "Dom/Ext": "stade",
    "Entraineur": "personne",
    "Buts marq.": "football",
    "Buts pris": "bouclier",
    "Clean sheet": "bouclier",
    "1re MT": "chrono",
    # Les deux fenetres d'une meme repartition : meme pictogramme, la valeur
    # ecrit laquelle.
    "Buts tard.": "chrono",
    # Le rapprochement se fait sur le premier mot : « Cartons tps » heriterait
    # sinon du pictogramme de « Cartons », alors que la ligne dit *quand* ils
    # tombent et non combien.
    "Cartons tps": "horloge",
    "Cartons": "carte",
    "Formations": "grille",
    "Corners": "drapeau",
    "Fautes": "interdit",
    "Possession": "jauge",
    "Tirs": "cible",
    "xG": "cible",
    "Stats match": "interdit",
    "Compos": "liste",
    "Startlist": "liste",
    "Absents": "absent",
    # L'effectif reconstruit des feuilles de match : meme famille que
    # « Absents », dont il prend la place quand le fournisseur ne couvre pas.
    "Effectif": "absent",
    # L'aller d'une double confrontation : meme signe que la confrontation
    # directe, dont il est la premiere entree.
    "Aller": "duel",
    # L'arithmetique de la double confrontation. Le signe de l'enjeu et non
    # celui du duel : « Aller » dit qui a joue qui, « Scenario » dit ce qui se
    # joue ce soir.
    "Scenario": "enjeu",
    "H2H ici": "epingle",
    "H2H": "duel",
    "Tour": "echelons",
    "Repos": "lit",
    "Parcours": "echelons",
    # Ce que le parcours vient d'ecarter : une rencontre programmee qui n'a pas
    # eu lieu. Meme signe que « Abandons », qui dit la meme chose sur les six
    # derniers mois — un joueur qui n'entre pas sur le court.
    "Non joue": "sortie",
    "Usure": "chrono",
    "Elo": "jauge",
    "Surface": "tennis",
    "Abandons": "sortie",
    "Profil": "relief",
    # La forme d'un match et son cadre. « Marge » partage le pictogramme de
    # « Profil » : les deux decrivent le relief d'une rencontre, l'un par la
    # duree, l'autre par l'ecart. « Historique » prend l'horloge, parce qu'elle
    # ne dit rien du match — seulement jusqu'ou remonte ce qu'on sait.
    "Niveau adv.": "jauge",
    "Marge": "relief",
    "Historique": "horloge",
    # Ce que le retard de l'historique coute en matchs. Meme horloge que la ligne
    # dont elle tire la consequence : les deux parlent du temps, pas du match.
    "Fraicheur": "horloge",
    # Huit libelles sortaient sans pictogramme, releves en rendant le bloc de
    # 250 evenements reels : la colonne se vidait sans rien dire, exactement le
    # defaut que le sprite devait supprimer. Les doublons sont assumes — deux
    # lignes qui parlent de la meme chose meritent le meme signe.
    "Buteurs": "football",
    "Buteur abs.": "absent",
    "Total buts": "football",
    "Serie": "courbe",
    "Calendrier": "horloge",
    "Precedent": "medaille",
    #: Un match delocalise : l'epingle dit un endroit, pas un terrain.
    "Lieu": "epingle",
    "Pelouse": "stade",
    "References": "lien",
    "Infos": "note",
    # La densite du bloc lui-meme : combien de ses lignes ont ete peuplees. Une
    # jauge, parce que c'est une mesure de remplissage et non une donnee sur le
    # match — elle dit ce que la collecte a rapporte, pas ce que les equipes
    # valent.
    "Densite": "jauge",
}


#: Lignes de contexte qu'un match **pleinement servi** porte, par sport. C'est le
#: denominateur de la densite affichee sur la shortlist.
#:
#: Mesure qui les fixe : sur 128 evenements de football en base, les blocs les
#: mieux servis en portent 24 — Vasteras SK en Allsvenskan, Yunnan Yukun en Super
#: League chinoise. Cote tennis, huit lignes sortent sur 100 % des 135 evenements
#: et six autres sur la majorite.
#:
#: **Les lignes structurellement conditionnelles en sont exclues**, sans quoi
#: chaque match paraitrait pauvre pour de mauvaises raisons :
#:
#:   · `Aller` n'existe que sur une manche retour, `Statut` que sur un report,
#:     `Pelouse` que sur un synthetique ;
#:   · `Compos` depend de l'**heure** — elles paraissent une heure avant le coup
#:     d'envoi, et un match du lendemain n'en aura jamais a la generation ;
#:   · `Effectif` est le substitut employe la ou `/injuries` ne couvre pas : le
#:     compter ferait baisser la densite des matchs bien couverts ;
#:   · `Stats match` est une ligne **negative** — elle dit que le fournisseur ne
#:     publie rien — et la compter recompenserait une absence ;
#:   · `Abandons` et `H2H ici` au tennis ne sortent que si le passe s'y prete ;
#:   · `Non joue` ne sort que si une rencontre programmee n'a pas eu lieu, ce qui
#:     est l'exception — et la compter ferait paraitre plus riche un parcours
#:     qui, justement, porte un match de moins.
CONTEXT_EXPECTED: dict[str, tuple[str, ...]] = {
    "football": (
        # `Lieu` a rejoint le referentiel le jour ou elle a cesse d'etre
        # conditionnelle : elle porte desormais trois etats et se rend sur tout
        # match dont le lieu a ete recupere, y compris pour dire qu'il est
        # inconnu. L'exclure sous-estimerait la densite d'un bloc complet.
        "Lieu",
        "Classement",
        "Enjeu",
        "Forme 5",
        "Dom/Ext",
        "H2H",
        "Absents",
        "Repos",
        "Buts marq.",
        "Buts pris",
        "Clean sheet",
        "1re MT",
        "Buts tard.",
        "Cartons tps",
        "Formations",
        "Corners",
        "Cartons",
        "Tirs",
        "Fautes",
        "Possession",
        "xG",
        "Entraineur",
        "Total buts",
        "Serie",
        "Calendrier",
    ),
    "tennis": (
        "Elo",
        "Surface",
        "Forme",
        "Profil",
        "Marge",
        "Niveau adv.",
        "Usure",
        "Precedent",
        "Palmares",
        "Tour",
        "Repos",
        "Parcours",
        "Historique",
        "H2H",
    ),
}


def context_family(label: str) -> str:
    """Libelle sans son compte : « H2H (5) » et « H2H (1) » sont la meme ligne.

    Meme rapprochement que `context_icon`, et pour la meme raison : le nombre de
    confrontations varie d'un match a l'autre, la ligne non.
    """
    return (label or "").split(" (")[0].strip()


def expected_context(sport_key: str | None) -> tuple[str, ...]:
    """Lignes attendues pour ce sport. Vide s'il n'a pas de referentiel.

    Le cyclisme n'en a pas : ses trois lignes sont saisies a la main, et une
    densite y mesurerait la saisie plutot que la collecte.
    """
    return CONTEXT_EXPECTED.get(sport_key or "", ())


def context_icon(label: str) -> str:
    """Identifiant du pictogramme d'une ligne de contexte, vide si elle n'en a pas.

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


#: Suffixe qui marque un book de reference. Il vit dans les libelles depuis
#: toujours ; le nommer permet de **lire** cette qualite au lieu de la retaper.
REFERENCE_SUFFIX = "(ref.)"


def bookmaker_label(key: str | None) -> str:
    """Nom lisible d'un bookmaker, la cle brute a defaut."""
    return BOOKMAKER_LABELS.get(key or "", key or "—")


def is_reference(key: str | None) -> bool:
    """Vrai si ce book sert des prix de **reference**, jamais jouables tels quels.

    Un book inconnu n'en est pas un : il est rendu sous sa cle brute, sans
    suffixe, et le presenter comme une reference serait une affirmation qu'on ne
    peut pas gager.
    """
    return bookmaker_label(key).endswith(REFERENCE_SUFFIX)


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
