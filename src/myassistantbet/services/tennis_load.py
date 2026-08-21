"""Repos et charge de matchs d'un joueur, calcules sur nos propres donnees.

Le football recoit sa forme, ses absents et son classement d'API-Football ; le
tennis n'a que l'Elo, ce qui laisse le bloc CONTEXTE presque vide. Or une
information decisive dort deja dans la base : les tours precedents du meme
tournoi ont ete scannes les jours d'avant.

De ces lignes on tire deux choses, sans un seul appel reseau :

- **les jours de repos** — un joueur qui a joue hier et un joueur qui a joue
  avant-hier n'abordent pas le meme match ;
- **le nombre de tours deja disputes** dans ce tournoi.

Ce qu'on ne tire **pas**, et qu'il ne faut pas inventer : la duree des matchs,
le score, ni la maniere. La base ne stocke aucun resultat. Un joueur present au
tour suivant a forcement passe le precedent, mais l'ecrire supposerait qu'aucun
forfait n'existe — on se contente donc de dater ses apparitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings, get_settings
from ..db import connect
from . import elo, tournament_day
from .labels import sort_key

logger = logging.getLogger(__name__)

#: Au-dela, on parle d'un autre tournoi ou d'une autre semaine : le repos
#: n'a plus de sens comme information de fraicheur.
MAX_DAYS = 10

#: Identifiant sentinelle du match analyse dans le regroupement en journees. Un
#: entier negatif ne peut collisionner avec aucune cle primaire d'evenement.
_ICI = -1

#: Ce qui peut empecher une rencontre **programmee** d'avoir ete disputee, et le
#: mot rendu dans le bloc. Le fournisseur de cotes programme, il ne rapporte
#: pas : sans cette distinction, un forfait se lit comme un match joue.
WALKOVER = "walkover"
REPLACED = "replaced"
SUSPENDED = "suspended"
OUTCOMES: dict[str, str] = {
    WALKOVER: "forfait adverse, non disputee",
    REPLACED: "adversaire remplace, non disputee",
    SUSPENDED: "interrompue, non terminee",
}


@dataclass(frozen=True)
class Appearance:
    """Une rencontre **programmee** de ce joueur dans ce tournoi.

    Programmee, et non jouee : c'est toute la nuance que le module ignorait. Une
    apparition dont `outcome` est renseigne n'a pas eu lieu, et ne doit donc
    compter ni dans le repos, ni dans le parcours, ni nulle part ailleurs.
    """

    #: Journee de tournoi, au format ISO. Jamais une date civile.
    day: str
    opponent: str
    #: `None` = rien ne s'oppose a ce qu'elle ait ete disputee. Ce n'est **pas**
    #: la meme chose que « disputee » : nous ne le savons pas, nous n'avons rien
    #: qui dise le contraire. Meme regle que la ligne `Statut` du football.
    outcome: str | None = None
    #: Coup d'envoi programme, en UTC. C'est le seul instant que nous connaissons
    #: vraiment : ni la fin du match, ni sa duree ne sont publiees par une source
    #: que l'application sache lire.
    start: datetime | None = None

    @property
    def contested(self) -> bool:
        return self.outcome is None


#: Sur quoi le repos a ete calcule. Le chiffre seul ne suffit pas : un ecart de
#: deux heures entre deux joueurs se lit tout autrement s'il vient d'une duree
#: mesuree ou d'un coup d'envoi a un coup d'envoi. La mention compte donc autant
#: que le nombre, et un repos estime porte un `~`.
FROM_START = "start"
FROM_END = "end"


@dataclass(frozen=True)
class Rest:
    """Le repos d'un joueur avant ce match, et sur quoi il a ete mesure."""

    hours: int
    #: Journees de tournoi, **en parallele et non a la place**. Les deux mesurent
    #: des choses differentes : a ecart horaire egal, un tournoi de douze jours
    #: et un tournoi de sept ne fatiguent pas pareil.
    days: int | None
    #: Instant de reference : coup d'envoi du dernier match, ou sa fin quand on
    #: la connait.
    since: datetime
    basis: str = FROM_START
    #: Vrai quand l'instant de reference est deduit plutot que releve. Aucune
    #: source ne le permet aujourd'hui — voir `_rest`.
    estimated: bool = False


@dataclass
class Load:
    """Ce que la base sait du parcours d'un joueur dans ce tournoi."""

    rounds: int = 0
    days_rest: int | None = None
    #: Adversaires deja rencontres ici, du premier tour au dernier.
    opponents: tuple[str, ...] = ()
    #: Premiere journee de tournoi que nos scans ont vue, au format ISO. Ce que
    #: le tournoi a joue avant elle n'existe nulle part chez nous.
    first_day: str = ""
    #: **Journees de tournoi** de chaque apparition precedente, au format ISO.
    #: Des journees de tournoi et non des dates civiles, pour la meme raison que
    #: le repos : a Montreal, un match de la session du soir part apres minuit a
    #: Paris. C'est aussi l'echelle du fichier de resultats, qui date un match du
    #: jour ou il se joue sur place — les deux se comparent donc directement.
    days: tuple[str, ...] = ()
    #: `(journee de tournoi, adversaire)` de chaque rencontre precedente, dans
    #: l'ordre. `opponents` et `days` s'en deduisent, mais separement ils ne se
    #: rapprochent pas : c'est la paire qui permet de nommer **quels** matchs
    #: manquent a l'historique, et non seulement combien.
    faced: tuple[tuple[str, str], ...] = ()
    #: Les rencontres programmees qui n'ont **pas** eu lieu. Elles sortent de
    #: tout le reste — `rounds`, `days_rest`, `opponents`, `days`, `faced` — et
    #: ne vivent que la. Les taire ferait chercher un match qui n'existe pas ;
    #: les compter avec les autres est le defaut qu'on corrige.
    uncontested: tuple[Appearance, ...] = ()
    #: Le repos en temps reel ecoule. `None` quand aucune rencontre disputee ne
    #: precede — la meme condition que `days_rest`.
    rest: Rest | None = None

    @property
    def fragment(self) -> str:
        """`2j`, ou rien si aucun tour precedent n'est connu.

        Le nombre de tours **ne l'accompagne plus**. Il comptait les apparitions
        que nous avions scannees, pas les matchs joues : sur un tournoi dont les
        premiers jours precedent notre fenetre, il en manque. Constate en reel —
        le bloc creditait Michelsen d'un tour la ou l'ATP lui en donne deux. La
        ligne « Tour » dit desormais ou en est le tournoi, et elle le dit juste.
        """
        return f"{self.days_rest}j" if self.days_rest is not None else ""


def load_for(
    player: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> Load:
    """Parcours d'un joueur dans ce tournoi, avant le match considere.

    Rapprochement des noms par `sort_key` : le fournisseur ecrit le meme joueur
    de la meme facon d'un tour a l'autre, mais la casse et les accents peuvent
    varier. Aucun rapprochement flou ici — deux joueurs differents ne doivent
    jamais partager un parcours.
    """
    if not competition_id or not player:
        return Load()
    settings = settings or get_settings()
    key = sort_key(player)

    with connect(settings) as conn:
        # Toute la competition, match du jour compris : le regroupement en
        # journees de tournoi a besoin de la suite pour placer ses coupures.
        toutes = conn.execute(
            "SELECT id, home, away, commence_time, created_at, match_outcome_type "
            "FROM events WHERE competition_id = ? ORDER BY commence_time",
            (competition_id,),
        ).fetchall()
    rows = [row for row in toutes if row["commence_time"] < commence_time]

    # **Le repos se compte en journees de tournoi, jamais en dates civiles.** A
    # Montreal, un match de la session du soir part a 01h du matin a Paris : sa
    # date civile est celle du lendemain, et le repos calcule dessus perdait un
    # jour d'un cote et en gagnait un de l'autre. Constate en reel — le bloc
    # donnait van de Zandschulp a 1j et Paul a 3j la ou l'ATP date leurs deux
    # matchs precedents du meme mercredi.
    #
    # Le match du jour entre dans le regroupement sous un identifiant sentinelle
    # plutot que d'y etre cherche : rien ne garantit qu'il figure en base — un
    # contexte peut se calculer sur une rencontre saisie a la main, ou pas
    # encore scannee — et l'y supposer faisait disparaitre la ligne entiere.
    journees = tournament_day.day_keys(
        [(int(row["id"]), competition_id, row["commence_time"]) for row in toutes]
        + [(_ICI, competition_id, commence_time)],
        settings.tz,
    )
    ici = journees.get(_ICI)

    when = _parse(commence_time)
    if when is None:
        return Load()

    # Une entree par rencontre programmee, dans l'ordre du calendrier. Le tri se
    # fait sur l'heure de coup d'envoi et non sur la journee de tournoi : deux
    # matchs d'une meme journee doivent rester dans leur ordre reel.
    programmees: list[tuple[datetime, int, str, str | None, str | None]] = []
    for row in rows:
        if key not in (sort_key(row["home"]), sort_key(row["away"])):
            continue
        played = _parse(row["commence_time"])
        if played is None or (when - played).days > MAX_DAYS:
            continue
        # L'adversaire est l'autre nom de la ligne. Il est retenu tel qu'il a ete
        # scanne : c'est ainsi qu'il figure partout ailleurs dans l'application.
        autre = row["away"] if key == sort_key(row["home"]) else row["home"]
        if not autre:
            continue
        jour = journees.get(int(row["id"]))
        if not jour:
            continue
        programmees.append(
            (played, int(row["id"]), autre, jour, _outcome(row["match_outcome_type"]))
        )

    # **Ce que la source affirme demonte la premisse, jour par jour.** La
    # deduction ci-dessous repose sur « un joueur ne dispute qu'une rencontre par
    # journee de tournoi » ; la ou la source en compte deux, elle est fausse pour
    # ce jour-la. Aucun nom n'est rapproche — c'est le jour qui parle, et c'est
    # ce qui rend la levee sure.
    from .serve_stats import contested_days

    disputes = contested_days(player, competition_id, commence_time, settings)
    apparitions = _resolve_duplicates(
        programmees, {int(r["id"]): r["created_at"] for r in toutes}, disputes
    )
    disputees = [item for item in apparitions if item.contested]

    if not apparitions:
        return Load()
    connues = [journees[int(row["id"])] for row in toutes if int(row["id"]) in journees]
    return Load(
        rounds=len(disputees),
        days_rest=_rest(ici, [item.day for item in disputees]),
        opponents=tuple(item.opponent for item in disputees),
        first_day=min(connues) if connues else "",
        days=tuple(sorted(item.day for item in disputees)),
        faced=tuple((item.day, item.opponent) for item in disputees),
        rest=_elapsed(when, disputees, _rest(ici, [item.day for item in disputees])),
        uncontested=tuple(item for item in apparitions if not item.contested),
    )


def _outcome(value: str | None) -> str | None:
    """Le marquage porte par l'evenement, ignore s'il est hors vocabulaire.

    Une valeur inconnue vaut « rien ne s'y oppose » plutot qu'une erreur : elle
    ferait disparaitre une rencontre du parcours sans que rien ne le dise, ce
    qui est exactement le defaut qu'on corrige.
    """
    mot = (value or "").strip().casefold()
    return mot if mot in OUTCOMES else None


def _resolve_duplicates(
    programmees: list[tuple[datetime, int, str, str | None, str | None]],
    created: dict[int, str],
    contested: dict[str, int] | None = None,
) -> list[Appearance]:
    """**Un joueur ne dispute qu'une rencontre par journee de tournoi.**

    Quand nos scans en portent deux, l'adversaire programme a ete remplace — un
    forfait de derniere minute comble par un `alternate`. Les deux lignes
    existent en base, et le `Parcours` les listait toutes les deux : le joueur
    paraissait avoir disputé deux tours au lieu d'un.

    **C'est la plus recemment creee qui tient.** Une rencontre reprogrammee garde
    son identifiant chez le fournisseur, donc son enregistrement ; un adversaire
    remplace en produit un nouveau, decouvert au scan suivant. Mesure sur la base
    entiere : **un seul cas**, et il est exactement celui-la — JJ Wolf programme
    contre Toby Samuel a 19h00 (enregistre a 12h32), puis contre Shintaro
    Mochizuki a 21h45 (enregistre a 21h51).

    **La limite assumee s'est produite, et `contested` la leve.** Le commentaire
    d'origine disait : « un tableau retarde par la pluie peut faire jouer deux
    simples dans la meme journee. Le cas ne s'observe pas en base. » Il s'observe
    depuis le 19/08/2026 — Xiyu Wang a joue Kudermetova puis Andreescu le 13/08,
    et la deduction annoncait « adversaire remplace, non disputee » sur un match
    dont la source porte le score et les statistiques de service.

    `contested` compte, par journee, les matchs que la source declare **disputes**.
    Au-dela d'un, la premisse est fausse pour cette journee : toutes les
    apparitions scannees y sont tenues pour reelles.

    **La levee est positive seulement.** Un jour absent de `contested` ne prouve
    rien — la source peut ne pas couvrir ce tournoi, ce joueur, ou n'avoir pas
    encore publie — et la deduction s'applique alors comme avant. On ne se fie
    qu'a ce que la source **affirme**, jamais a son silence.
    """
    par_jour: dict[str, list[tuple[datetime, int, str, str | None, str | None]]] = {}
    for item in programmees:
        par_jour.setdefault(str(item[3]), []).append(item)

    resolues: list[tuple[datetime, int, Appearance]] = []
    for jour, lot in par_jour.items():
        if (contested or {}).get(jour, 0) > 1:
            # Deux matchs disputes ce jour-la : rien n'est remplace, tout a ete
            # joue. Le marquage a la main, lui, n'est pas touche — il n'est pas
            # deduit, c'est un geste humain.
            resolues.extend(
                (
                    played,
                    identifiant,
                    Appearance(day=str(j), opponent=autre, outcome=marque, start=played),
                )
                for played, identifiant, autre, j, marque in lot
            )
            continue
        tenue = max(lot, key=lambda item: (created.get(item[1]) or "", item[0], item[1]))
        for played, identifiant, autre, jour, marque in lot:
            remplacee = identifiant != tenue[1]
            resolues.append(
                (
                    played,
                    identifiant,
                    Appearance(
                        day=str(jour),
                        opponent=autre,
                        outcome=REPLACED if remplacee else marque,
                        start=played,
                    ),
                )
            )
    # L'ordre est celui du calendrier, pas celui des journees : le `Parcours` se
    # lit du premier tour au dernier, et deux rencontres d'une meme journee
    # doivent rester dans leur ordre reel.
    return [item for _, _, item in sorted(resolues, key=lambda row: (row[0], row[1]))]


def _elapsed(when: datetime, disputees: list[Appearance], days: int | None) -> Rest | None:
    """Temps reellement ecoule depuis la derniere rencontre disputee.

    **C'est la grandeur que la journee de tournoi ne sait pas dire.** Sur les
    demi-finales du Canadian Open, le bloc donnait 2j aux joueurs sortis de la
    session de jour et 1j a ceux de la session du soir, quand l'ecart reel etait
    d'environ vingt-quatre heures pour tout le monde. A Cincinnati, six joueuses
    dont le premier tour s'etait joue la veille en fin d'apres-midi local
    recevaient « 0j » — une bascule de date a Paris, pas un double.

    **Le calcul part du coup d'envoi precedent, et il faut le dire.** La fin d'un
    match serait la bonne borne ; ni sa duree ni son heure de fin ne sont
    publiees par une source que l'application sache lire — verifie le 7 aout
    2026, et le blocage tient toujours : `tennis-data.co.uk` ne sert que des
    scores et retarde de dix jours, les pages de match de Tennis Abstract sont
    interdites par son `robots.txt`, les CSV de Jeff Sackmann ont disparu, et
    `atptour.com` interdit nos agents. `Rest.basis` porte donc la reponse, et le
    jour ou une duree entrera, seule cette fonction changera.
    """
    if not disputees:
        return None
    dernier = max((item.start for item in disputees if item.start), default=None)
    if dernier is None:
        return None
    # Heures **entierement ecoulees**, jamais arrondies au plus proche : `round`
    # applique la regle bancaire, si bien que 25h30 et 78h30 tomberaient l'une
    # vers le haut et l'autre vers le bas. Un plancher est monotone et se lit
    # comme la phrase le dit — « il a eu 25 heures ».
    hours = int((when - dernier).total_seconds() // 3600)
    return Rest(hours=max(hours, 0), days=days, since=dernier, basis=FROM_START)


def _rest(here: str | None, previous: list[str]) -> int | None:
    """Journees de tournoi entre la derniere apparition et celle du jour."""
    if not here or not previous:
        return None
    try:
        return (date.fromisoformat(here) - date.fromisoformat(max(previous))).days
    except ValueError:
        return None


#: Au-dela, l'ecart se lit mieux en jours qu'en heures : « 121 h » demande une
#: division, « 5j » se lit d'un coup. En dessous, c'est l'inverse — c'est
#: justement la ou la journee de tournoi confondait 24 h et 0 h.
HOURS_MAX = 72


def venue_zone(competition_id: int | None, settings: Settings) -> ZoneInfo | None:
    """Fuseau du lieu, saisi a la main. `None` quand il n'est pas renseigne.

    Rien ne se deduit d'un libelle, meme regle que la surface : « Cincinnati
    Open » ne dit pas `America/New_York`, et une table de villes se tromperait
    le jour ou le tournoi demenage.
    """
    if not competition_id:
        return None
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT timezone FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
    name = (row["timezone"] if row else None) or ""
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        # Une saisie devenue illisible — fuseau supprime d'une version de tzdata
        # a l'autre — vaut « non renseigne » : la ligne se rend en UTC et le dit,
        # plutot que de refuser tout le bloc pour une colonne d'appoint.
        logger.warning("Fuseau illisible sur la competition %s : %s", competition_id, name)
        return None


def _moment(when: datetime, zone: ZoneInfo | None) -> str:
    """`11/08 18:47 local` ou `11/08 22:47 UTC` — jamais l'un pour l'autre.

    **Une heure de Paris presentee comme locale serait pire qu'une heure UTC
    presentee comme distante.** C'est l'erreur exacte que la ligne corrige : le
    forfait de Bencic, annonce le mardi 11 au soir a Toronto, s'ecrivait
    « le 12/08 » parce que 23h UTC tombe apres minuit a Paris.
    """
    if zone is None:
        return f"{when.astimezone(UTC).strftime('%d/%m %H:%M')} UTC"
    return f"{when.astimezone(zone).strftime('%d/%m %H:%M')} local"


def _rest_fragment(player: str, rest: Rest, zone: ZoneInfo | None) -> str:
    """`Coco Gauff 78 h (4 j. tournoi, depuis 09/08 14:00 local)`.

    Trois choses dans un ordre voulu : l'ecart reel, l'avancee du tournoi, puis
    **sur quoi l'ecart a ete mesure**. Le dernier n'est pas un ornement — deux
    joueurs a « 23 h » et « 26 h » ne se comparent que si l'on sait lequel est
    releve et lequel est deduit, et sans cette mention l'ecart se lit comme un
    fait quand il peut n'etre qu'un artefact d'estimation.
    """
    approx = "~" if rest.estimated else ""
    if rest.hours > HOURS_MAX:
        # Au-dela de trois jours, « 78 h » demande une division pour se situer.
        # Les deux unites dans un seul nombre plutot que deux nombres cote a
        # cote : `78 h (3 j)` ferait trois chiffres sur la ligne avec la journee
        # de tournoi, et c'est un de trop pour un coup d'oeil.
        ecart = f"{approx}{rest.hours // 24} j {rest.hours % 24} h"
    else:
        ecart = f"{approx}{rest.hours} h"
    detail = [f"{rest.days} j. tournoi"] if rest.days is not None else []
    mot = "fin" if rest.basis == FROM_END else "depuis"
    detail.append(f"{mot} {_moment(rest.since, zone)}")
    return f"{player} {ecart} ({', '.join(detail)})"


def lines(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Repos » du bloc, vide si aucun tour precedent n'est connu.

    Un tournoi dont on n'a scanne que le jour meme ne produit rien : ecrire
    « 0 tour » laisserait croire a une entree en lice alors qu'on ne sait
    simplement pas.

    **Le repos se compte depuis la derniere rencontre reellement disputee**, et
    non depuis la derniere programmee. Un forfait adverse n'a pas mis le joueur
    sur le court : le compter donnait « Coco Gauff 1j » a une joueuse qui
    n'avait pas joue depuis trois jours, sur la ligne meme qui existe pour dire
    sa fraicheur.

    **Un joueur par ligne**, contrairement aux autres lignes du bloc. Chaque
    fragment porte trois choses — l'ecart, l'avancee du tournoi, l'instant de
    reference — et deux joueurs bout a bout depassaient largement la largeur ou
    une ligne se lit d'un coup d'oeil.
    """
    settings = settings or get_settings()
    zone = venue_zone(competition_id, settings)
    fragments = []
    for player in (home, away):
        if not player:
            continue
        charge = load_for(player, competition_id, commence_time, settings)
        if charge.rest is not None:
            fragments.append(_rest_fragment(player, charge.rest, zone))
    return [("Repos", "\n".join(fragments))] if fragments else []


def path_lines(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    oddsapi_key: str | None = None,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Parcours » : qui chaque joueur a deja rencontre dans ce tournoi.

    **Les adversaires, jamais les resultats.** La base ne stocke aucun score : un
    joueur present au tour suivant a forcement passe le precedent, mais l'ecrire
    « il a battu X » supposerait qu'aucun forfait n'existe. La ligne nomme donc
    qui a ete rencontre, et laisse le reste a la recherche.

    L'Elo de l'adversaire accompagne son nom quand il est connu — c'est lui qui
    distingue un parcours facile d'un parcours d'usure, et il ne coute rien : le
    classement est deja en base pour la ligne « Elo ».

    Ce que cette ligne **ne peut pas** donner, et qu'il ne faut pas lui prêter :
    la duree des matchs et les statistiques de service. Aucune source gratuite ne
    les publie — verifie le 7 aout 2026 : `tennis-data.co.uk` ne sert ni l'une ni
    les autres et retarde d'une semaine sur le tournoi en cours, et les CSV de
    Jeff Sackmann, qui les portaient, ont disparu de GitHub.
    """
    settings = settings or get_settings()
    fragments = []
    debut = ""
    for player in (home, away):
        if not player:
            continue
        charge = load_for(player, competition_id, commence_time, settings)
        debut = debut or charge.first_day
        if not charge.opponents:
            continue
        noms = ", ".join(_with_elo(nom, oddsapi_key, settings) for nom in charge.opponents)
        fragments.append(f"{player} {noms}")
    if not fragments:
        return []
    return [("Parcours", " | ".join(fragments) + _since(debut))]


def unplayed_lines(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    oddsapi_key: str | None = None,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Non joue » : les rencontres programmees qui n'ont pas eu lieu.

    Elles sortent du `Parcours` et du `Repos`, mais **elles ne disparaissent
    pas** : un forfait est une information sur le tournoi et sur l'adversaire,
    et le retirer sans un mot ferait chercher un tour manquant. C'est la meme
    regle que partout — une absence constatee se dit.

    La ligne porte la **date** et la **cause**, parce que les deux se verifient
    en une recherche et qu'elles ne se valent pas : un forfait adverse offre un
    tour gratuit, un adversaire remplace veut dire que le joueur a bien joue ce
    jour-la, mais contre quelqu'un d'autre.
    """
    settings = settings or get_settings()
    zone = venue_zone(competition_id, settings)
    fragments = []
    for player in (home, away):
        if not player:
            continue
        for item in load_for(player, competition_id, commence_time, settings).uncontested:
            adversaire = _with_elo(item.opponent, oddsapi_key, settings)
            fragments.append(
                f"{player} — {adversaire} {_when(item, zone)}, {OUTCOMES[str(item.outcome)]}"
            )
    return [("Non joue", "\n".join(fragments))] if fragments else []


def _when(item: Appearance, zone: ZoneInfo | None) -> str:
    """`le 11/08 19:00 local`, ou la journee de tournoi a defaut.

    **Toute la valeur de cette ligne est de dater un fait**, et le forfait de
    Bencic, annonce le mardi 11 au soir a Toronto, s'ecrivait « le 12/08 » : sa
    journee de tournoi vient de l'heure de Paris, ou 23h UTC tombe apres minuit.
    Avec le fuseau du lieu, l'instant se rend a l'heure du lieu ; sans lui, en
    UTC, annonce comme tel.
    """
    if item.start is not None:
        return f"le {_moment(item.start, zone)}"
    return f"le {_short(item.day)}"


def _short(day: str) -> str:
    """`2026-08-11` -> `11/08`. La date brute alourdirait une ligne deja longue."""
    try:
        return date.fromisoformat(day).strftime("%d/%m")
    except ValueError:
        return day


def mark_unplayed(event_id: int, outcome: str, settings: Settings | None = None) -> str:
    """Marque — ou demarque — une rencontre comme non disputee. Rend l'etat pose.

    **C'est la seule source vivante, et c'est un constat, pas une preference.**
    Un forfait annonce trente minutes avant le coup d'envoi n'existe dans aucune
    source que l'application sache lire : le fichier de resultats parait une
    fois par semaine et apres coup — mesure le 12/08, il s'arretait au 03/08 —
    donc il arrive toujours apres que le tournoi a cesse d'etre rendu. La regle
    des deux rencontres d'une meme journee attrape le remplacement d'adversaire
    et rien d'autre. Restait la saisie, ou le silence.

    Une valeur vide efface le marquage : se tromper doit se defaire, sinon on
    hesite a marquer et la ligne ne sert plus a rien. Une valeur hors
    vocabulaire est refusee plutot qu'ecrite — `load_for` l'ignorerait, et le
    marquage paraitrait pose sans avoir aucun effet.
    """
    settings = settings or get_settings()
    mot = (outcome or "").strip().casefold()
    if mot and mot not in OUTCOMES:
        raise ValueError(f"Etat inconnu : {outcome}")
    with connect(settings) as conn:
        conn.execute(
            "UPDATE events SET match_outcome_type = ? WHERE id = ?", (mot or None, event_id)
        )
    logger.info("evenement %s marque « %s »", event_id, mot or "disputee")
    return mot


@dataclass(frozen=True)
class Uncounted:
    """Ce que l'historique ne connait pas encore du tournoi en cours."""

    count: int = 0
    #: Les adversaires de ces matchs-la, quand ils sont nommes. C'est ce qui
    #: transforme un constat en **liste de taches** : chaque nom est un match
    #: identifiable, donc une recherche que l'analyse peut mener une par une.
    opponents: tuple[str, ...] = ()
    #: Vrai quand **tout** le parcours connu manque a l'historique. Les nommer
    #: alors ne ferait que recopier la ligne « Parcours », deux lignes plus
    #: haut : autant le dire en trois mots.
    whole_path: bool = False


def played_since(
    player: str,
    competition_id: int | None,
    commence_time: str,
    cutoff: date,
    settings: Settings | None = None,
) -> Uncounted:
    """Matchs de ce tournoi joues par ce joueur **apres** la date de collecte.

    Ce sont les matchs que l'historique ne connait pas encore, donc que
    « Forme », « Usure », « Profil », « Marge » et « Niveau adv. » ne comptent
    pas. Sur un quart de finale, il y en a trois : l'usure affichee ignore alors
    tout le tournoi en cours, et rien ne le disait.

    **Les adversaires sont nommes**, parce que c'est la seule chose que le bloc
    ne dit nulle part ailleurs : le compte se lit dans « Fraicheur » et la liste
    complete dans « Parcours », mais rapprocher les deux pour savoir *lesquels*
    manquent etait laisse a l'analyse. Quand ils manquent tous, la ligne le dit
    en trois mots plutot que de recopier « Parcours ».

    Aucun appel, aucune cle : les tours precedents ont ete scannes les jours
    d'avant, et leurs journees de tournoi sont deja calculees pour le repos.
    """
    charge = load_for(player, competition_id, commence_time, settings)
    limite = cutoff.isoformat()
    count = sum(1 for jour in charge.days if jour > limite)
    if not count:
        return Uncounted()
    noms = tuple(nom for jour, nom in charge.faced if jour > limite)
    return Uncounted(
        count=count,
        opponents=noms,
        whole_path=bool(charge.opponents) and len(noms) == len(charge.opponents),
    )


def last_scan(competition_id: int | None, settings: Settings | None = None) -> str | None:
    """La borne haute de notre fenetre de scans, en ISO 8601.

    Ce que produisent `Repos`, `Parcours` et `Tour` ne vient d'aucun
    fournisseur : ce sont nos propres relevés, et leur date est celle de la
    derniere rencontre entree en base sur ce tournoi. `scan_window` en rend la
    phrase, celle-ci l'horodatage — le payload transporte des dates, jamais des
    ages.
    """
    if not competition_id:
        return None
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS moment FROM events WHERE competition_id = ?",
            (competition_id,),
        ).fetchone()
    return str(row["moment"]) if row and row["moment"] else None


def scan_window(competition_id: int | None, settings: Settings | None = None) -> str:
    """`scans du 04/08 09:12 au 12/08 06:30 UTC` — les bords de notre fenetre.

    « vu depuis le 04/08 » etait ambigu : premier jour du tournoi, ou premier
    jour ou nous avons regarde ? Il a fallu le deviner, et c'est precisement ce
    que cette ligne existe pour eviter. `events.created_at` porte l'instant ou
    chaque rencontre est entree en base — donc l'instant ou nous l'avons vue
    pour la premiere fois.

    **La borne haute compte autant que la basse.** Elle est « maintenant » quand
    tout va bien, et c'est justement ce qui la rend utile : une borne haute
    vieille de deux jours dit qu'un scan ne tourne plus, et rien d'autre dans le
    bloc ne le dirait. En UTC des deux cotes — un instant de collecte n'a pas de
    lieu, et l'ecrire a l'heure du tournoi serait une precision inventee.
    """
    if not competition_id:
        return ""
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT MIN(created_at) AS debut, MAX(created_at) AS fin FROM events "
            "WHERE competition_id = ?",
            (competition_id,),
        ).fetchone()
    debut, fin = _parse(row["debut"] if row else None), _parse(row["fin"] if row else None)
    if debut is None or fin is None:
        return ""
    return f"scans du {debut.strftime('%d/%m %H:%M')} au {fin.strftime('%d/%m %H:%M')} UTC"


def _since(first_day: str) -> str:
    """` [vu depuis le 04/08]` — la fenetre de nos scans, jamais celle du tournoi.

    La liste se lisait comme un parcours complet, et elle ne l'est pas : un
    tournoi commence avant notre fenetre de scan a des premiers tours que nous
    n'avons jamais vus. Constate en reel — le `Parcours` de Norrie omettait son
    premier tour contre Ugo Carabelli, joue la veille du premier jour scanne, et
    seule une recherche exterieure l'a rattrape.

    La date suffit a rendre le trou visible : comparee a « Tour », elle dit tout
    de suite si le debut du tableau manque. Compter les tours absents demanderait
    la taille du tableau, que rien ne donne.
    """
    try:
        jour = date.fromisoformat(first_day)
    except ValueError:
        return ""
    return f" [vu depuis le {jour.strftime('%d/%m')}]"


def _with_elo(name: str, oddsapi_key: str | None, settings: Settings) -> str:
    """`Krejcikova (1905)` — le nom, et son Elo quand le rapprochement est sur."""
    row = elo.lookup(name, elo.tour_for(oddsapi_key), settings)
    rating = (row or {}).get("elo")
    return f"{name} ({int(rating)})" if rating else name


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
