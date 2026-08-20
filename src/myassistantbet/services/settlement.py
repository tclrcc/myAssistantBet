"""Le reglement d'une selection : propose, jamais impose.

**Ce module ne tranche rien tout seul.** Il lit un resultat, applique une regle
ecrite, et depose une proposition. C'est un humain qui promeut — parce que 293
selections tranchees portent tout ce que ce projet sait produire, et qu'un
reglement errone les corromprait en silence.

## Le taux d'accord de 93,3 % du lot 15 etait un artefact, et sa cause est ici

Le rejeu du lot 15 indexait les resultats par **paire de noms de famille**, sans
date. Deux rencontres du meme couple s'ecrasaient donc l'une l'autre, et le
dernier releve gagnait. Les quatre divergences rapportees n'etaient pas des cas
limites du sport — c'etaient quatre fois le meme defaut d'appariement.

Mesure du 20/08/2026, sur 800 matchs recoupables entre `event/get` et
`tennis-data.co.uk`, deux sources independantes :

| Cle de rapprochement | Accord sur le vainqueur |
| --- | ---: |
| paire de noms seule | 94,1 % |
| **paire de noms + jour** | **99,75 %** |

Et les **deux** desaccords restants sur 800 ne sont pas des desaccords : leur
score porte un set **inacheve** — `7-5,3-6,2-1` et `6-1,1-0` — donc un abandon.
Compter les sets y designe celui qui menait quand le jeu s'est arrete,
c'est-a-dire le perdant.

## La convention du score, etablie et non supposee

`event/get` ecrit le score du point de vue de **`participant1`**, set par set,
separes par des virgules. Ce n'est pas une lecture de la documentation : c'est le
recoupement ci-dessus, contre une source qui nomme explicitement son vainqueur.

## Ce qui part en « non tranche », et pourquoi c'est la moitie du travail

Un marche dont la regle n'est pas ecrite ne produit **aucune ligne**. Il n'est
pas range dans un etat « inconnu » — il est absent, et c'est ce qui le distingue
d'un marche couvert dont le resultat manque.

Sont hors regle, deliberement : les abandons, les handicaps, les scores exacts,
les mi-temps/fin de match, la qualification, les totaux d'equipe. Leur regle
existe, elle n'est simplement pas ecrite ici — et un marche a 98 % reste manuel,
parce que 2 % de reglements faux sur 293 selections corrompent le residu.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

#: Les etats d'une proposition. **Le troisieme est celui qui compte** : il dit
#: qu'un reglage a la main existe deja et que le calcul ne le confirme pas.
PROPOSE = "propose"
APPLIQUE = "applique"
DIVERGENT = "divergent"
ETATS = (PROPOSE, APPLIQUE, DIVERGENT)

#: Les verdicts, meme vocabulaire que `picks.result` — sans `pending`, une
#: proposition sans verdict ne s'ecrivant pas.
WIN, LOSS, VOID = "win", "loss", "void"

#: D'ou vient un resultat. Ecrit sur chaque ligne : sans lui, une divergence ne
#: se distingue pas d'une source qui a change d'avis.
SRC_TENNIS = "tennisapi/event"
SRC_FOOT = "apifootball/season"

#: Fenetre de rapprochement, en jours, autour du coup d'envoi. Une session du
#: soir a Cincinnati part apres minuit UTC — le decalage est deja documente par
#: `tournament_day`, et exiger le jour exact ferait manquer le cas cherche.
DAY_WINDOW = (0, -1, 1)


def _fold(value: str) -> list[str]:
    """Les mots d'un nom, casse et accents retires."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z ]+", " ", stripped.lower()).split()


def surname(value: str) -> str:
    """Le nom de famille d'un libelle d'affiche : `Alex Michelsen` -> `michelsen`.

    **Le dernier mot, et c'est une convention de source, pas une heuristique
    generale** : `events` ecrit le prenom devant. `tennis_matches`, lui, ecrit
    « Mensik J. » — nom de famille en tete — et confondre les deux a rendu un
    `0 sur 127` au lot 15, corrige a 65,8 %. Chaque source a sa fonction.
    """
    mots = _fold(value)
    return mots[-1] if mots else ""


#: Un set, ou qu'il soit dans la chaine. **Une recherche et non un decoupage**,
#: parce que les deux sources qui servent un score ne le separent pas de la meme
#: facon : `event/get` ecrit `4-6,7-6,0-6` — virgules — et `matches-played`
#: ecrit `3-6 7-6(5) 6-0` — espaces, avec le detail du jeu decisif. Decouper sur
#: la virgule rendait **un seul set** sur la seconde, donc un palmares vide sur
#: 589 matchs. Le score entre guillemets d'une troisieme graphie passe aussi.
_SET = re.compile(r"(\d+)\s*-\s*(\d+)(?:\s*\(\d+\))?")


def _set_complete(a: int, b: int) -> bool:
    """Ce set est-il alle a son terme ?

    Un set se gagne a 6 avec deux jeux d'ecart, ou 7 — ce qui couvre 7-5, 7-6 et
    les sets longs d'un cinquieme set (8-6, 10-8). `2-1` n'est pas un set, c'est
    l'instant ou le jeu s'est arrete.
    """
    haut = max(a, b)
    return haut >= 6 and (abs(a - b) >= 2 or haut >= 7)


@dataclass(frozen=True)
class Score:
    """Un score de tennis lu set par set, du point de vue de `participant1`."""

    sets_un: int
    sets_deux: int
    brut: str
    #: Les **jeux**, tous sets confondus. Ils etaient deja dans la chaine lue —
    #: `4-6,7-6,0-6` compte des jeux, pas des sets — et ce module affirmait le
    #: contraire : « au tennis un total porte sur les jeux, que `event/get` ne
    #: donne pas dans son champ `score` agrege ». Les compter ne coute aucun
    #: appel et ouvre les deux plus gros marches non regles du projet.
    jeux_un: int
    jeux_deux: int
    #: Vrai des qu'un set n'est pas alle a son terme. **C'est la detection
    #: d'abandon**, et elle ne demande aucun champ de plus : compter les sets sur
    #: un match interrompu designe celui qui menait, donc le perdant.
    incomplet: bool

    @property
    def decisif(self) -> bool:
        return not self.incomplet and self.sets_un != self.sets_deux


def read_score(brut: str) -> Score | None:
    """Le score d'`event/get`, en sets. `None` si rien ne s'y lit."""
    jeux = list(_SET.finditer(str(brut or "")))
    if not jeux:
        return None
    un = deux = 0
    jeux_un = jeux_deux = 0
    incomplet = False
    for m in jeux:
        a, b = int(m.group(1)), int(m.group(2))
        # **Les jeux d'un set inacheve comptent quand meme**, et c'est sans
        # consequence : un match interrompu est refuse en bloc plus haut. Les
        # ecarter ici ferait deux comptes differents du meme match selon le
        # chemin de lecture, ce qui est le piege que ce depot paie ailleurs.
        jeux_un += a
        jeux_deux += b
        if not _set_complete(a, b):
            incomplet = True
            continue
        if a > b:
            un += 1
        elif b > a:
            deux += 1
    return Score(
        sets_un=un,
        sets_deux=deux,
        brut=str(brut),
        incomplet=incomplet,
        jeux_un=jeux_un,
        jeux_deux=jeux_deux,
    )


@dataclass(frozen=True)
class MatchResult:
    """Ce qu'on sait de l'issue d'une rencontre, et d'ou on le sait."""

    sport: str
    source: str
    observed_at: str
    detail: str
    #: `home`, `away`, ou `draw`. Le camp gagnant du point de vue de l'affiche
    #: telle qu'elle est ecrite dans `events`.
    winner: str | None = None
    #: Football seulement : le score a **90 minutes**, jamais prolongation
    #: comprise — un marche O/U se regle sur le temps reglementaire.
    goals: tuple[int, int] | None = None
    #: Tennis seulement : les sets, du point de vue du premier joueur nomme.
    sets: tuple[int, int] | None = None
    #: Tennis seulement : les **jeux**, meme point de vue. C'est la grandeur du
    #: handicap jeux et du total de jeux, les deux plus gros marches que ce
    #: module laissait a la main.
    games: tuple[int, int] | None = None
    #: Le match ne s'est pas termine normalement. **Aucune regle ne s'y
    #: applique** : c'est un cas non couvert, pas un resultat.
    unfinished: bool = False


# -- Les resultats, lus dans ce qui est deja en base --------------------------


def _tennis_index(conn) -> dict[tuple[frozenset[str], str], tuple[str, Score, str]]:
    """Les resultats de tennis, ranges par (paire de noms, jour).

    **La date fait partie de la cle, et c'est tout le correctif du lot 16.**
    Sans elle, deux rencontres du meme couple s'ecrasent, et le taux d'accord
    tombe de 99,75 % a 94,1 % — le defaut qui a fait rapporter 93,3 % au lot 15.
    """
    index: dict[tuple[frozenset[str], str], tuple[str, Score, str]] = {}
    for row in conn.execute(
        "SELECT raw_json, fetched_at FROM api_responses WHERE endpoint = 'event/get'"
    ):
        brut = (row["raw_json"] or "").strip()
        if not brut.startswith("{"):
            continue
        try:
            charge = json.loads(brut)
        except json.JSONDecodeError:
            continue
        res = charge.get("result")
        if not isinstance(res, dict) or res.get("status") != "Ended":
            continue
        un, deux = res.get("participant1"), res.get("participant2")
        nom_un = surname(un.get("name") if isinstance(un, dict) else un)
        nom_deux = surname(deux.get("name") if isinstance(deux, dict) else deux)
        if not nom_un or not nom_deux or nom_un == nom_deux:
            continue
        horodate = res.get("startTimestamp")
        if not isinstance(horodate, int | float):
            continue
        score = read_score(res.get("score"))
        if score is None:
            continue
        jour = datetime.fromtimestamp(horodate, UTC).strftime("%Y-%m-%d")
        index[(frozenset([nom_un, nom_deux]), jour)] = (nom_un, score, row["fetched_at"])
    return index


def _football_index(conn) -> dict[tuple[int, str], tuple[list[int] | None, str]]:
    """Les scores de football, ranges par (identifiant d'equipe, jour).

    Ils dorment dans les resumes de saison du dossier d'equipe : `goals` porte le
    score **a 90 minutes** (`_score_90`), donc deja ce qu'un marche O/U demande,
    et `status = FT` dit que la rencontre est allee au bout.
    """
    index: dict[tuple[int, str], tuple[list[int] | None, str]] = {}
    for row in conn.execute(
        "SELECT team_id, payload_json, fetched_at FROM team_context WHERE kind = 'season'"
    ):
        try:
            lignes = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(lignes, list):
            continue
        for ligne in lignes:
            if not isinstance(ligne, dict) or ligne.get("status") != "FT":
                continue
            jour = str(ligne.get("date") or "")[:10]
            buts = ligne.get("goals")
            if not jour or not isinstance(buts, list) or len(buts) != 2:
                continue
            if any(not isinstance(v, int) for v in buts):
                continue
            index[(int(row["team_id"]), jour)] = (buts, row["fetched_at"])
    return index


def _team_ids(conn, event_id: int) -> list[int]:
    """Les identifiants d'equipe rapproches pour cet evenement."""
    row = conn.execute(
        "SELECT payload_json FROM context WHERE event_id = ? AND kind = 'teams'", (event_id,)
    ).fetchone()
    if row is None:
        return []
    try:
        charge = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(charge, dict):
        return []
    return [v for v in charge.values() if isinstance(v, int)]


def results_for(event_ids: list[int], settings: Settings | None = None) -> dict[int, MatchResult]:
    """Les resultats connus pour ces evenements, **sans un appel reseau**.

    Tout se lit dans ce qui est deja archive : `api_responses` pour le tennis,
    les resumes de saison du dossier d'equipe pour le football.
    """
    settings = settings or get_settings()
    trouves: dict[int, MatchResult] = {}
    with connect(settings) as conn:
        tennis = _tennis_index(conn)
        foot = _football_index(conn)
        marques = ",".join("?" for _ in event_ids)
        if not event_ids:
            return {}
        lignes = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, s.key AS sport "
            "  FROM events e JOIN competitions c ON c.id = e.competition_id "
            "  JOIN sports s ON s.id = c.sport_id "
            f" WHERE e.id IN ({marques})",
            event_ids,
        ).fetchall()
        for ligne in lignes:
            jour = str(ligne["commence_time"])[:10]
            if ligne["sport"] == "tennis":
                trouve = _tennis_result(tennis, ligne, jour)
            else:
                trouve = _football_result(foot, conn, ligne, jour)
            if trouve is not None:
                trouves[int(ligne["id"])] = trouve
    return trouves


def _jours(jour: str) -> list[str]:
    try:
        base = datetime.fromisoformat(jour)
    except ValueError:
        return [jour]
    return [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in DAY_WINDOW]


def _tennis_result(index: dict, ligne, jour: str) -> MatchResult | None:
    un, deux = surname(ligne["home"]), surname(ligne["away"])
    if not un or not deux:
        return None
    cle = frozenset([un, deux])
    for candidat in _jours(jour):
        trouve = index.get((cle, candidat))
        if trouve is None:
            continue
        nom_un, score, releve = trouve
        # Le score est du point de vue de `participant1` — etabli par
        # recoupement, pas suppose. On le ramene au point de vue de l'affiche.
        a_lendroit = nom_un == un
        sets = (score.sets_un, score.sets_deux) if a_lendroit else (score.sets_deux, score.sets_un)
        jeux = (score.jeux_un, score.jeux_deux) if a_lendroit else (score.jeux_deux, score.jeux_un)
        vainqueur = None
        if score.decisif:
            vainqueur = "home" if sets[0] > sets[1] else "away"
        return MatchResult(
            sport="tennis",
            source=SRC_TENNIS,
            observed_at=releve,
            detail=score.brut,
            winner=vainqueur,
            sets=sets,
            games=jeux,
            unfinished=score.incomplet,
        )
    return None


def _football_result(index: dict, conn, ligne, jour: str) -> MatchResult | None:
    for team_id in _team_ids(conn, int(ligne["id"])):
        for candidat in _jours(jour):
            trouve = index.get((team_id, candidat))
            if trouve is None:
                continue
            buts, releve = trouve
            domicile, exterieur = int(buts[0]), int(buts[1])
            if domicile > exterieur:
                vainqueur = "home"
            elif exterieur > domicile:
                vainqueur = "away"
            else:
                vainqueur = "draw"
            return MatchResult(
                sport="football",
                source=SRC_FOOT,
                observed_at=releve,
                detail=f"{domicile}-{exterieur}",
                winner=vainqueur,
                goals=(domicile, exterieur),
            )
    return None


# -- Les regles de marche ----------------------------------------------------
#
# **Trois seulement, et c'est la consigne.** Un marche dont la regle n'est pas
# ecrite ne se regle pas : il ne produit aucune ligne, et un humain le tranche.
# Un marche a 98 % resterait manuel — 2 % de reglements faux sur 293 selections
# corrompent le residu, qui est tout ce que ce projet sait produire.

#: Les cles de marche que ce module sait lire, et leur famille de regle. La cle
#: vide est le cas des selections anterieures a la migration 033, dont le marche
#: se relit sur le libelle.
ISSUE_KEYS = ("h2h",)
TOTAL_KEYS = ("totals", "alternate_totals")
#: **Une seule cle pour les deux sports**, et deux regles derriere : au football
#: un handicap porte sur les buts, au tennis sur les jeux. Le sport se lit sur
#: le resultat — `goals` ou `games` — jamais sur le libelle.
HANDICAP_KEYS = ("spreads", "alternate_spreads")
DC_KEYS = ("double_chance",)
BTTS_KEYS = ("btts",)

#: Les libelles que le rendu ecrit pour ces memes marches, pour les selections
#: sans cle. **Reconnaitre un mot qu'on a soi-meme imprime**, jamais deviner.
ISSUE_LABELS = ("1n2", "vainqueur")
TOTAL_LABELS = ("o/u", "jeux o/u", "total", "totals")
HANDICAP_LABELS = ("handicap", "hand. jeux", "hand jeux")
DC_LABELS = ("dc", "double chance")
BTTS_LABELS = ("btts", "les 2 equipes marquent", "les deux equipes marquent")

_LIGNE = re.compile(r"(\d+(?:[.,]\d+)?)")
_OVER = re.compile(r"\b(over|plus de|\+)\b", re.IGNORECASE)
_UNDER = re.compile(r"\b(under|moins de|-)\b", re.IGNORECASE)

#: Le handicap signe, en fin de libelle : `Lyon -1`, `Tirante +3.5`, `Örgryte 0`.
#: **Le signe est obligatoire des que la ligne n'est pas nulle** : sans lui,
#: « Lyon 1 » ne dit pas de quel cote le but est donne, et un handicap lu a
#: l'envers est l'erreur la plus couteuse que ce module puisse commettre.
_HANDICAP = re.compile(r"(?:^|\s)([+-]\d+(?:[.,]\d+)?|0)(?=\s|$|\s*jeux|\s*\()", re.IGNORECASE)

#: Les paires de la double chance, telles que le rendu les imprime — `1X`, `12`,
#: `X2` — et les variantes francaises du nul (`N`) que l'analyse recopie.
#: **Lues sur le libelle brut**, jamais replie : `_fold` mange les chiffres, et
#: `1X` comme `X2` s'y reduisent tous deux a `x`.
_DC_MARK = re.compile(r"(?:^|[\s(\[])([12XN]{2})(?=[\s)\]]|$)", re.IGNORECASE)
_DC_SIDE = {"1": "home", "2": "away", "x": "draw", "n": "draw"}

#: Les mots du nul, une fois le libelle replie.
_DRAW_WORDS = frozenset({"nul", "draw", "x", "n"})


def _plat(market: str) -> str:
    """Le libelle d'un marche, casse et accents retires.

    Les deux graphies `Eq. buts` et `Éq. buts` sont en base, et la comparaison
    d'origine sur `.lower()` seul les separait.
    """
    return " ".join(_fold(market)) or (market or "").strip().lower()


def _famille(market_key: str, market: str) -> str:
    """La famille de regle d'une selection, ou vide si aucune ne s'applique."""
    cle = (market_key or "").strip().lower()
    if cle in ISSUE_KEYS:
        return "issue"
    if cle in TOTAL_KEYS:
        return "total"
    if cle in HANDICAP_KEYS:
        return "handicap"
    if cle in DC_KEYS:
        return "double_chance"
    if cle in BTTS_KEYS:
        return "btts"
    if cle:
        # Une cle connue mais hors regle : elle reste manuelle, et le dire ici
        # evite qu'un libelle voisin la fasse entrer par la porte des libelles.
        return ""
    brut = (market or "").strip().lower()
    if brut in ISSUE_LABELS:
        return "issue"
    if any(brut.startswith(prefixe) for prefixe in TOTAL_LABELS):
        return "total"
    if any(brut.startswith(prefixe) for prefixe in HANDICAP_LABELS):
        return "handicap"
    if brut in DC_LABELS:
        return "double_chance"
    plat = _plat(market)
    if any(plat.startswith(prefixe) for prefixe in BTTS_LABELS):
        return "btts"
    return ""


def rule_family(market_key: str, market: str, sport: str) -> str:
    """La famille de regle, **sport compris**, et c'est ce qui se met en service.

    Un handicap de football porte sur les buts et un handicap de tennis sur les
    jeux : meme arithmetique, deux marches, et **deux taux d'accord**. Mesure du
    20/08/2026 sur les selections tranchees a la main — 34 sur 34 au tennis,
    13 sur 14 au football. Les fondre aurait interdit la famille sure a cause de
    l'autre, ou mis en service une famille a 92,9 %.

    **Le sport se lit sur la competition, jamais sur le libelle.** Les deux
    graphies sont pourtant propres — `Hand. jeux` au tennis, `Handicap` au
    football, exactement ce que `MARKET_ORDER_BY_SPORT` imprime — mais une
    saisie a la main peut ecrire l'un pour l'autre, et le seul cout de cette
    faute serait de mettre en service la famille qu'on refuse.
    """
    famille = _famille(market_key, market)
    if famille == "handicap":
        return "handicap_jeux" if str(sport or "").lower() == "tennis" else "handicap_buts"
    return famille


def _camp(selection: str, home: str, away: str) -> str | None:
    """Quel camp cette selection designe. `None` si elle n'en designe aucun.

    Le rapprochement se fait sur le **nom de famille** au tennis et sur les mots
    de l'affiche au football, avec la meme regle qu'ailleurs : en cas de doute,
    rien. Une selection qui designerait les deux camps ne se regle pas.
    """
    plat = " ".join(_fold(selection))
    if not plat:
        return None
    if plat in ("nul", "draw", "x", "match nul"):
        return "draw"
    mots_home, mots_away = set(_fold(home)), set(_fold(away))
    # **Les mots communs aux deux camps ne designent personne, et les garder
    # fabriquait un doute la ou il n'y en a pas.** Mesure : `Los Angeles FC`
    # contre `San Diego FC` — le `fc` touchait les deux cotes, la selection
    # partait « hors regle » et un reglement sur parfaitement lisible etait
    # perdu. Ce n'est pas un assouplissement de la regle du doute : un jeton
    # partage ne porte aucune information sur le camp vise.
    communs = mots_home & mots_away
    mots_sel = set(plat.split()) - communs
    touche_home = bool(mots_sel & (mots_home - communs))
    touche_away = bool(mots_sel & (mots_away - communs))
    if touche_home == touche_away:
        return None
    return "home" if touche_home else "away"


def _total(selection: str, somme: int) -> str | None:
    """Le verdict d'un O/U. `None` si la ligne ou le sens ne se lit pas."""
    sens_over = bool(_OVER.search(selection or ""))
    sens_under = bool(_UNDER.search(selection or ""))
    if sens_over == sens_under:
        return None
    lignes = _LIGNE.findall(selection or "")
    if not lignes:
        return None
    ligne = float(lignes[-1].replace(",", "."))
    if somme == ligne:
        # Une ligne entiere touchee est remboursee. Elle ne se produit pas sur
        # une ligne en `.5`, qui est le cas ordinaire.
        return VOID
    depasse = somme > ligne
    return WIN if depasse == sens_over else LOSS


def _double_chance(selection: str, home: str, away: str) -> frozenset[str] | None:
    """Les deux issues couvertes par une double chance, ou `None` en cas de doute.

    **Deux lectures, dans cet ordre, et la premiere est la bonne** : le rendu
    imprime `1X / 12 / X2`, et l'analyse recopie ce marqueur dans neuf libelles
    sur dix. A defaut, le camp se lit comme ailleurs et le nul a son mot.

    Le marqueur se cherche sur le libelle **brut** : `_fold` retire les
    chiffres, si bien que `1X` et `X2` s'y replient tous deux sur `x` — deux
    paris opposes sous la meme forme.
    """
    marque = _DC_MARK.search(selection or "")
    if marque is not None:
        couvertes = {_DC_SIDE[lettre.lower()] for lettre in marque.group(1)}
        return frozenset(couvertes) if len(couvertes) == 2 else None
    mots = set(_fold(selection))
    couvertes: set[str] = set()
    if mots & _DRAW_WORDS:
        couvertes.add("draw")
    # Le camp se lit sur le libelle prive de ses mots de nul : « Nul ou Levski »
    # ne doit pas voir « nul » compter comme un jeton d'equipe.
    reste = " ".join(mot for mot in _fold(selection) if mot not in _DRAW_WORDS)
    camp = _camp(reste, home, away)
    if camp in ("home", "away"):
        couvertes.add(camp)
    return frozenset(couvertes) if len(couvertes) == 2 else None


def _btts(selection: str) -> bool | None:
    """Le sens d'un « les deux equipes marquent ». `None` s'il ne se lit pas.

    Le rendu ecrit `Oui` et `Non` (`_render_btts`), le fournisseur `Yes` et
    `No` : les quatre sont reconnus, et rien d'autre.
    """
    mots = set(_fold(selection))
    oui = bool(mots & {"oui", "yes"})
    non = bool(mots & {"non", "no"})
    if oui == non:
        return None
    return oui


def _handicap(selection: str, home: str, away: str, pour: int, contre: int) -> str | None:
    """Le verdict d'un handicap europeen. `None` quand la regle ne s'ecrit pas.

    **Les lignes en quart n'ont pas de regle, et c'est definitif** : `-0.25` et
    `+0.75` sont des paris asiatiques **scindes**, une demi-mise sur chacune des
    deux lignes voisines, donc un verdict qui n'est ni gagne ni perdu. Le
    gabarit interdit deja de les selectionner ; celles qui restent en base sont
    anterieures a cette regle et se tranchent a la main.

    **Le remboursement sur ligne entiere touchee est un etat a part**, et c'est
    tout l'objet de cette famille : `Örgryte 0` sur un nul n'est ni gagne ni
    perdu, et le ranger avec l'un des deux fausserait le residu au prix.
    """
    camp = _camp(selection, home, away)
    if camp not in ("home", "away"):
        return None
    trouve = _HANDICAP.search(selection or "")
    if trouve is None:
        return None
    ligne = float(trouve.group(1).replace(",", "."))
    if abs(ligne * 4) % 2 == 1:
        # Ligne en quart : pari scinde, aucun verdict unique.
        return None
    marge = (pour - contre if camp == "home" else contre - pour) + ligne
    if marge > 0:
        return WIN
    if marge < 0:
        return LOSS
    return VOID


def settle(
    market_key: str, market: str, selection: str, home: str, away: str, result: MatchResult
) -> str | None:
    """Le verdict d'une selection, ou `None` quand aucune regle ne s'applique.

    **`None` n'est pas un echec** : c'est un marche laisse a la main, et c'est la
    moitie du travail de ce module. Il se distingue d'une absence de resultat par
    le fait que le resultat, lui, est la.
    """
    if result.unfinished:
        # Un abandon ne se regle pas en comptant les sets : le compte designe
        # celui qui menait quand le jeu s'est arrete, donc le perdant. Mesure :
        # deux cas sur 800 matchs recoupes, et les deux ressortaient a l'envers.
        return None
    famille = _famille(market_key, market)
    if famille == "issue":
        camp = _camp(selection, home, away)
        if camp is None or result.winner is None:
            return None
        return WIN if camp == result.winner else LOSS
    if famille == "total":
        if result.goals is not None:
            return _total(selection, result.goals[0] + result.goals[1])
        if result.games is not None:
            # Au tennis un total porte sur les **jeux**, et ils sont dans le
            # meme champ `score` : `4-6,7-6,0-6` compte des jeux. Le module
            # affirmait le contraire, et c'est ce qui laissait a la main le plus
            # gros bloc de selections tranchees du projet.
            return _total(selection, result.games[0] + result.games[1])
        return None
    if famille == "double_chance":
        couvertes = _double_chance(selection, home, away)
        if couvertes is None or result.winner is None:
            return None
        return WIN if result.winner in couvertes else LOSS
    if famille == "btts":
        if result.goals is None:
            # Il n'y a pas de « les deux marquent » au tennis.
            return None
        oui = _btts(selection)
        if oui is None:
            return None
        marquent = result.goals[0] > 0 and result.goals[1] > 0
        return WIN if marquent == oui else LOSS
    if famille == "handicap":
        cotes = result.goals or result.games
        if cotes is None:
            return None
        return _handicap(selection, home, away, cotes[0], cotes[1])
    return None


# -- La mise en service ------------------------------------------------------

#: Les familles de regle **effectivement en service**, et rien d'autre.
#:
#: Mesure du 20/08/2026, rejeu sur les 293 selections tranchees a la main :
#:
#: | Famille | Regles | Accord | Divergence |
#: | --- | ---: | ---: | ---: |
#: | `issue` (1N2, Vainqueur) | 79 | 79 | **0** |
#: | `total` (O/U football) | 16 | 16 | **0** |
#:
#: **100,00 % sur les deux, et c'est la condition de mise en service.** Un marche
#: a 98 % resterait manuel : 2 % de reglements faux sur 293 selections
#: corrompent le residu au prix, qui est tout ce que ce projet sait produire.
#:
#: Mesure du 20/08/2026, meme rejeu, sur les 307 selections tranchees :
#:
#: | Famille | Regles | Accord | Taux |
#: | --- | ---: | ---: | ---: |
#: | `issue` (1N2, Vainqueur) | 79 | 79 | **100,00 %** |
#: | `total` (O/U buts **et** jeux) | 26 | 26 | **100,00 %** |
#: | `handicap_jeux` (Hand. jeux, tennis) | 34 | 34 | **100,00 %** |
#: | `btts` | 7 | 7 | **100,00 %** |
#: | `double_chance` | 5 | 5 | **100,00 %** |
#: | `handicap_buts` (Handicap, football) | 14 | 13 | 92,86 % |
#:
#: **Le handicap de football reste dehors sur une seule divergence**, et c'est
#: la regle : `Valerenga +1` perdu 1-2 fait 2-2 apres handicap, donc un
#: remboursement sur un marche a deux issues — le reglement manuel dit `win`.
#: L'arithmetique n'est pas en cause ; ce qui n'est pas etabli est le nombre
#: d'issues du marche ou le pari a ete pose, et un remboursement compte pour un
#: `void` dans le residu au prix. Une seule ligne fausse sur 307 suffit a le
#: corrompre.
#:
#: Ce qui reste dehors, deliberement : le handicap de football, les scores
#: exacts, les mi-temps/fin de match, la qualification, les totaux d'equipe.
ENABLED = ("issue", "total", "handicap_jeux", "btts", "double_chance")


def enabled_for(market_key: str, market: str, sport: str = "") -> bool:
    """Ce marche est-il en service ? Sinon il reste a la main.

    Le sport a un **defaut vide**, qui range un handicap du cote non servi : un
    appelant qui ne le passe pas ne peut donc pas mettre en service une famille
    par omission.
    """
    return rule_family(market_key, market, sport) in ENABLED


@dataclass(frozen=True)
class Proposal:
    """Un reglement calcule, avant toute promotion."""

    pick_id: int
    verdict: str
    etat: str
    source: str
    observed_at: str
    market_key: str
    detail: str
    #: Le resultat deja saisi a la main, quand il y en a un. **C'est lui qui fait
    #: la divergence**, et il est garde a cote plutot que compare a la volee : la
    #: ligne doit dire contre quoi elle diverge.
    manuel: str | None = None


@dataclass
class Run:
    """Ce qu'une passe de reglement a produit."""

    proposals: list[Proposal] = None  # type: ignore[assignment]
    sans_resultat: int = 0
    hors_regle: int = 0
    inacheves: int = 0

    def __post_init__(self) -> None:
        if self.proposals is None:
            self.proposals = []

    @property
    def divergents(self) -> list[Proposal]:
        return [p for p in self.proposals if p.etat == DIVERGENT]

    @property
    def nouveaux(self) -> list[Proposal]:
        return [p for p in self.proposals if p.etat == PROPOSE]


def compute(settings: Settings | None = None) -> Run:
    """Calcule les reglements possibles. **N'ecrit rien, n'appelle rien.**

    La separation est celle de `context.py` : un temps qui calcule, un temps qui
    persiste. Elle rend la passe rejouable et testable sans base d'ecriture.
    """
    settings = settings or get_settings()
    passe = Run()
    with connect(settings) as conn:
        lignes = conn.execute(
            "SELECT p.id, p.market_key, p.market, p.selection, p.result, p.event_id, "
            "       e.home, e.away, s.key AS sport "
            "  FROM picks p JOIN events e ON e.id = p.event_id "
            "  JOIN competitions c ON c.id = e.competition_id "
            "  JOIN sports s ON s.id = c.sport_id "
            " WHERE p.event_id IS NOT NULL"
        ).fetchall()
    if not lignes:
        return passe
    resultats = results_for([int(r["event_id"]) for r in lignes], settings)
    for ligne in lignes:
        if not enabled_for(ligne["market_key"] or "", ligne["market"], ligne["sport"]):
            passe.hors_regle += 1
            continue
        resultat = resultats.get(int(ligne["event_id"]))
        if resultat is None:
            passe.sans_resultat += 1
            continue
        if resultat.unfinished:
            passe.inacheves += 1
            continue
        verdict = settle(
            ligne["market_key"] or "",
            ligne["market"],
            ligne["selection"],
            ligne["home"],
            ligne["away"],
            resultat,
        )
        if verdict is None:
            passe.hors_regle += 1
            continue
        manuel = ligne["result"] if ligne["result"] in (WIN, LOSS, VOID) else None
        # **Un reglement automatique n'ecrase jamais un reglement manuel.** Le
        # lot 14 a paye cette lecon sur `set_open_dossiers` : un bon etat ecrase
        # par un mauvais, sans trace, et il a fallu un rejeu pour s'en
        # apercevoir. Ici la divergence se voit, et c'est tout ce qu'elle fait.
        etat = PROPOSE
        if manuel is not None:
            etat = APPLIQUE if manuel == verdict else DIVERGENT
        passe.proposals.append(
            Proposal(
                pick_id=int(ligne["id"]),
                verdict=verdict,
                etat=etat,
                source=resultat.source,
                observed_at=resultat.observed_at,
                # **La cle telle qu'elle est, vide comprise.** Y verser le
                # libelle a defaut faisait relire ce libelle comme une cle par
                # `_famille`, qui le rendait alors « autre » : le taux d'accord
                # rangeait 51 reglements sous une famille inexistante. Le
                # libelle vient de la jointure sur `picks`, jamais d'une copie.
                market_key=ligne["market_key"] or "",
                detail=resultat.detail,
                manuel=manuel,
            )
        )
    return passe


def record(passe: Run, settings: Settings | None = None) -> int:
    """Persiste les propositions. Rend le nombre de lignes ecrites.

    **Idempotent sur la selection** : rejouer la passe met la ligne a jour au
    lieu d'en creer une seconde. Une proposition deja promue (`applique`) n'est
    pas retrogradee — la promotion est un geste humain, et le calcul ne la defait
    pas.
    """
    moment = utcnow()
    ecrites = 0
    with connect(settings) as conn:
        for prop in passe.proposals:
            conn.execute(
                "INSERT INTO reglements (pick_id, verdict, etat, source, observed_at, "
                "                        market_key, detail, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(pick_id) DO UPDATE SET verdict = excluded.verdict, "
                "  etat = CASE WHEN reglements.etat = ? THEN ? ELSE excluded.etat END, "
                "  source = excluded.source, observed_at = excluded.observed_at, "
                "  market_key = excluded.market_key, detail = excluded.detail, "
                "  updated_at = excluded.updated_at",
                (
                    prop.pick_id,
                    prop.verdict,
                    prop.etat,
                    prop.source,
                    prop.observed_at,
                    prop.market_key,
                    prop.detail,
                    moment,
                    moment,
                    APPLIQUE,
                    APPLIQUE,
                ),
            )
            ecrites += 1
    if ecrites:
        logger.info(
            "Reglement : %d proposition(s), dont %d divergence(s)",
            ecrites,
            len(passe.divergents),
        )
    return ecrites


def run(settings: Settings | None = None) -> Run:
    """Calcule puis persiste. C'est ce que le planificateur appelle."""
    passe = compute(settings)
    record(passe, settings)
    return passe


def apply(pick_id: int, settings: Settings | None = None) -> bool:
    """Promeut une proposition dans `picks.result`. **Geste humain, jamais cron.**

    Refuse une ligne `divergent` : promouvoir contre un reglement deja pose
    serait exactement l'ecrasement que ce module existe pour empecher. Il faut
    d'abord trancher la divergence a la main.
    """
    from .history import set_result

    with connect(settings) as conn:
        row = conn.execute(
            "SELECT verdict, etat FROM reglements WHERE pick_id = ?", (pick_id,)
        ).fetchone()
    if row is None or row["etat"] != PROPOSE:
        logger.warning("Reglement non promouvable pour la selection %d", pick_id)
        return False
    set_result(pick_id, row["verdict"], settings)
    with connect(settings) as conn:
        conn.execute(
            "UPDATE reglements SET etat = ?, updated_at = ? WHERE pick_id = ?",
            (APPLIQUE, utcnow(), pick_id),
        )
    logger.info("Reglement promu : selection %d -> %s", pick_id, row["verdict"])
    return True


@dataclass(frozen=True)
class Pending:
    """Une proposition telle que la feuille de session la rend."""

    pick_id: int
    verdict: str
    etat: str
    source: str
    detail: str
    manuel: str | None


def pending(settings: Settings | None = None) -> list[Pending]:
    """Les propositions en attente et les divergences, pour l'interface."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT r.pick_id, r.verdict, r.etat, r.source, r.detail, p.result AS manuel "
            "  FROM reglements r JOIN picks p ON p.id = r.pick_id "
            " WHERE r.etat IN (?, ?) ORDER BY r.etat, r.pick_id",
            (DIVERGENT, PROPOSE),
        ).fetchall()
    return [
        Pending(
            pick_id=int(r["pick_id"]),
            verdict=r["verdict"],
            etat=r["etat"],
            source=r["source"],
            detail=r["detail"] or "",
            manuel=r["manuel"] if r["manuel"] in (WIN, LOSS, VOID) else None,
        )
        for r in rows
    ]


def agreement(settings: Settings | None = None) -> dict[str, tuple[int, int]]:
    """Le taux d'accord par famille, **mesurable dans le temps**.

    C'est ce qui permet de voir une regle se degrader : une source qui change de
    convention ferait chuter ce taux sans qu'aucun test ne tombe.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT r.etat, r.market_key, p.market, s.key AS sport FROM reglements r "
            "  JOIN picks p ON p.id = r.pick_id "
            "  JOIN events e ON e.id = p.event_id "
            "  JOIN competitions c ON c.id = e.competition_id "
            "  JOIN sports s ON s.id = c.sport_id "
            " WHERE r.etat IN (?, ?)",
            (APPLIQUE, DIVERGENT),
        ).fetchall()
    tally: dict[str, list[int]] = {}
    for row in rows:
        famille = rule_family(row["market_key"] or "", row["market"], row["sport"]) or "autre"
        seau = tally.setdefault(famille, [0, 0])
        seau[0] += 1
        if row["etat"] == APPLIQUE:
            seau[1] += 1
    return {famille: (total, ok) for famille, (total, ok) in tally.items()}
