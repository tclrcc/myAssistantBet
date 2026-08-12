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

    @property
    def contested(self) -> bool:
        return self.outcome is None


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

    apparitions = _resolve_duplicates(programmees, {int(r["id"]): r["created_at"] for r in toutes})
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

    Limite assumee : un tableau retarde par la pluie peut faire jouer deux
    simples dans la meme journee. Le cas ne s'observe pas en base, et le degat
    serait une ligne de parcours en trop plutot qu'un repos faux — l'inverse de
    ce que le silence coutait.
    """
    par_jour: dict[str, list[tuple[datetime, int, str, str | None, str | None]]] = {}
    for item in programmees:
        par_jour.setdefault(str(item[3]), []).append(item)

    resolues: list[tuple[datetime, int, Appearance]] = []
    for lot in par_jour.values():
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
                    ),
                )
            )
    # L'ordre est celui du calendrier, pas celui des journees : le `Parcours` se
    # lit du premier tour au dernier, et deux rencontres d'une meme journee
    # doivent rester dans leur ordre reel.
    return [item for _, _, item in sorted(resolues, key=lambda row: (row[0], row[1]))]


def _rest(here: str | None, previous: list[str]) -> int | None:
    """Journees de tournoi entre la derniere apparition et celle du jour."""
    if not here or not previous:
        return None
    try:
        return (date.fromisoformat(here) - date.fromisoformat(max(previous))).days
    except ValueError:
        return None


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
    """
    settings = settings or get_settings()
    fragments = []
    for player in (home, away):
        if not player:
            continue
        fragment = load_for(player, competition_id, commence_time, settings).fragment
        if fragment:
            fragments.append(f"{player} {fragment}")
    return [("Repos", " | ".join(fragments))] if fragments else []


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
    fragments = []
    for player in (home, away):
        if not player:
            continue
        for item in load_for(player, competition_id, commence_time, settings).uncontested:
            adversaire = _with_elo(item.opponent, oddsapi_key, settings)
            fragments.append(
                f"{player} — {adversaire} le {_short(item.day)}, {OUTCOMES[str(item.outcome)]}"
            )
    return [("Non joue", " | ".join(fragments))] if fragments else []


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
