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
    incomplet = False
    for m in jeux:
        a, b = int(m.group(1)), int(m.group(2))
        if not _set_complete(a, b):
            incomplet = True
            continue
        if a > b:
            un += 1
        elif b > a:
            deux += 1
    return Score(sets_un=un, sets_deux=deux, brut=str(brut), incomplet=incomplet)


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

#: Les libelles que le rendu ecrit pour ces memes marches, pour les selections
#: sans cle. **Reconnaitre un mot qu'on a soi-meme imprime**, jamais deviner.
ISSUE_LABELS = ("1n2", "vainqueur")
TOTAL_LABELS = ("o/u", "jeux o/u", "total", "totals")

_LIGNE = re.compile(r"(\d+(?:[.,]\d+)?)")
_OVER = re.compile(r"\b(over|plus de|\+)\b", re.IGNORECASE)
_UNDER = re.compile(r"\b(under|moins de|-)\b", re.IGNORECASE)


def _famille(market_key: str, market: str) -> str:
    """La famille de regle d'une selection, ou vide si aucune ne s'applique."""
    cle = (market_key or "").strip().lower()
    if cle in ISSUE_KEYS:
        return "issue"
    if cle in TOTAL_KEYS:
        return "total"
    if cle:
        # Une cle connue mais hors regle : elle reste manuelle, et le dire ici
        # evite qu'un libelle voisin la fasse entrer par la porte des libelles.
        return ""
    plat = (market or "").strip().lower()
    if plat in ISSUE_LABELS:
        return "issue"
    if any(plat.startswith(prefixe) for prefixe in TOTAL_LABELS):
        return "total"
    return ""


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
        # Au tennis un total porte sur les **jeux**, que `event/get` ne donne pas
        # dans son champ `score` agrege. La regle n'est donc pas ecrite ici.
        return None
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
#: Ce qui reste dehors, deliberement : handicaps, scores exacts, mi-temps/fin de
#: match, qualification, totaux d'equipe, deux-equipes-marquent, double chance,
#: et **les totaux au tennis** — `event/get` ne sert pas le compte de jeux dans
#: son score agrege, donc la regle ne s'ecrit pas.
ENABLED = ("issue", "total")


def enabled_for(market_key: str, market: str) -> bool:
    """Ce marche est-il en service ? Sinon il reste a la main."""
    return _famille(market_key, market) in ENABLED


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
            "       e.home, e.away "
            "  FROM picks p JOIN events e ON e.id = p.event_id "
            " WHERE p.event_id IS NOT NULL"
        ).fetchall()
    if not lignes:
        return passe
    resultats = results_for([int(r["event_id"]) for r in lignes], settings)
    for ligne in lignes:
        if not enabled_for(ligne["market_key"] or "", ligne["market"]):
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
            "SELECT r.etat, r.market_key, p.market FROM reglements r "
            "  JOIN picks p ON p.id = r.pick_id WHERE r.etat IN (?, ?)",
            (APPLIQUE, DIVERGENT),
        ).fetchall()
    tally: dict[str, list[int]] = {}
    for row in rows:
        famille = _famille(row["market_key"] or "", row["market"]) or "autre"
        seau = tally.setdefault(famille, [0, 0])
        seau[0] += 1
        if row["etat"] == APPLIQUE:
            seau[1] += 1
    return {famille: (total, ok) for famille, (total, ok) in tally.items()}
