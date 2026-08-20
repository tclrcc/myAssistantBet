"""Le palmares d'un joueur, sur son historique entier.

**Ce que le lot 15 annoncait comme blocage n'en etait pas un.** Le gabarit dit
que `Palmares` « n'existe que si le tournoi a ete rattache a la main » ; mesure,
les **43 competitions de tennis sur 43** sont rattachees, et aucune n'a jamais
ete analysee sans l'etre. Ce qui manquait etait la **profondeur**.

## La profondeur, et ce qu'elle coute

`matches-played` annonce `singlesCount` — **mediane 509 matchs**, maximum 1 593 —
et la collecte n'en demandait **100**. 99,2 % des profils archives etaient donc
tronques par notre propre pagination.

Sondage du 20/08/2026, six joueurs du dernier lot, `pageSize=200` :

| Joueur | Pages | Matchs | Historique |
| --- | ---: | ---: | --- |
| Pegula | 5 | 824 | **2009** → 2026 |
| Paul | 4 | 788 | 2013 → 2026 |
| Rybakina | 4 | 644 | 2014 → 2026 |
| Anisimova | 2 | 390 | 2015 → 2026 |

Cout : **mediane 3 pages, maximum 8**, soit deux appels de plus par joueur —
~60 par lot de 24, contre 139 480 appels de quota restants.

## Le tour se lit sur un identifiant, pas sur un libelle

`roundId` est servi a **100 %**. Son sens n'est pas suppose : il se mesure en
comptant les matchs par `(tournoi, roundId)` sur l'archive entiere, le
**maximum** d'une edition donnant la profondeur du tour.

| `roundId` | Matchs max | Tour |
| ---: | ---: | --- |
| 12 | 1 | finale |
| 10 | 2 | demi-finale |
| 9 | 4 | quart |
| 7 | 8 | huitieme |
| 6 | 16 | 3e tour |
| 5 | 32 | 2e tour |
| 4 | 64 | 1er tour |

**Les identifiants sont absolus, pas relatifs a la taille du tableau** : un
ATP 250 emploie 9, 10 et 12, un Grand Chelem descend jusqu'a 4. C'est ce qui rend
la correspondance utilisable sans connaitre le tableau.

**Ce qui en est exclu, et c'est mesure** : `1`, `2`, `3` sont des qualifications
— leurs maxima valent 33, 23 et 16, donc aucun tour de tableau — et `8`, `13` a
`17` sont des formats par equipes ou en poules : `Finals` et `United Cup` pour le
premier, `Fed Cup` et coupe Davis pour les autres. Ils n'ont pas leur place dans
un palmares de simple, et les nommer « tour » serait inventer.

## La categorie est servie, elle ne se deduit pas

`tournament.tier` vaut `Grand Slam`, `WTA 1000`, `ATP Masters 1000`,
`Challenger 125`… **85 % de couverture sur l'historique profond**, contre 99,1 %
sur les 52 dernieres semaines : la difference est le passe, dont les petites
editions ne portent pas de categorie.

Une edition sans categorie est donc **ignoree du bilan par categorie**, jamais
rangee ailleurs — et le denominateur ne compte que les editions categorisees.
Deduire la categorie d'un libelle serait exactement ce que le projet refuse
partout.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

#: Le tour, par identifiant du fournisseur. **Etabli par comptage**, pas lu dans
#: une documentation — voir le mode d'emploi du module. Les cles absentes sont
#: volontairement absentes : qualifications et formats par equipes.
ROUND_BY_ID: dict[int, str] = {
    4: "1st round",
    5: "2nd round",
    6: "3rd round",
    7: "4th round",
    9: "quarterfinals",
    10: "semifinals",
    12: "the final",
}

#: Les alias historiques d'une meme categorie. **Reunir deux graphies du meme
#: niveau chez le meme fournisseur est un fait de renommage, pas une deduction**
#: — le projet le fait deja pour les tournois avec ses alias separes par `|`.
TIER_ALIASES = {
    "ATP World Tour Masters 1000": "ATP Masters 1000",
    "ATP World Tour 500": "ATP 500",
    "ATP World Tour 250": "ATP 250",
    "ATP World Tour Finals": "Finals",
}

#: Nombre de pages au-dela duquel on cesse de demander. **Un garde-fou, pas une
#: profondeur voulue** : le maximum mesure est 8 pages (1 593 matchs), et
#: `singlesCount` arrete la boucle bien avant dans le cas ordinaire.
MAX_PAGES = 12


@dataclass(frozen=True)
class Edition:
    """Une edition de tournoi disputee par un joueur, reduite a l'essentiel."""

    tournament: str
    tier: str
    surface: str
    year: str
    #: Le tour le plus profond atteint, dans le vocabulaire de
    #: `tennis_history.ROUND_RANKS` — une seule ecriture des tours pour tout le
    #: projet, sans quoi deux echelles finiraient par ne plus se lire pareil.
    round: str
    #: A-t-il gagne son dernier match de l'edition ? Une finale gagnee vaut
    #: « vainqueur », perdue « finaliste », et le rang du tour ne le dit pas.
    won: bool


@dataclass
class Palmares:
    """Ce qu'un joueur a fait, edition par edition."""

    player: str
    circuit: str = ""
    editions: list[Edition] = field(default_factory=list)
    as_of: str = ""
    pages: int = 0
    announced: int = 0

    @property
    def truncated(self) -> bool:
        """L'historique lu est-il plus court que celui annonce ?

        **Ca se voit plutot que ca ne se devine** : un palmares tronque par une
        erreur de pagination ne doit pas se lire comme un joueur qui a peu joue.
        """
        return bool(self.announced) and self.pages >= MAX_PAGES


def normalise_tier(value: object) -> str:
    """La categorie, alias historiques reunis. Vide si la source n'en sert pas."""
    brut = str(value or "").strip()
    if not brut or brut.lower() == "none":
        return ""
    return TIER_ALIASES.get(brut, brut)


def summarise(matchs: list[dict], player: str) -> list[Edition]:
    """Reduit un historique brut a une edition par tournoi et par annee.

    Le **tour le plus profond** de chaque edition est retenu, et l'issue de ce
    tour-la : c'est ce qui distingue « vainqueur » de « finaliste ».
    """
    from .tennis_history import ROUND_RANKS

    par_edition: dict[tuple[str, str], tuple[int, str, bool, str, str]] = {}
    for m in matchs:
        tid = m.get("roundId")
        cle_tour = ROUND_BY_ID.get(tid) if isinstance(tid, int) else None
        if cle_tour is None:
            continue
        tournoi = m.get("tournament") or {}
        nom = str(tournoi.get("name") or "").strip()
        annee = str(m.get("date") or "")[:4]
        if not nom or not annee:
            continue
        gagne = _won(m, player)
        if gagne is None:
            continue
        rang = ROUND_RANKS.get(cle_tour, 0)
        cle = (nom, annee)
        courant = par_edition.get(cle)
        if courant is None or rang > courant[0]:
            par_edition[cle] = (
                rang,
                cle_tour,
                gagne,
                normalise_tier(tournoi.get("tier")),
                str(((tournoi.get("court") or {}).get("name")) or ""),
            )
    return [
        Edition(tournament=nom, tier=tier, surface=surface, year=annee, round=tour, won=gagne)
        for (nom, annee), (_, tour, gagne, tier, surface) in sorted(par_edition.items())
    ]


def _won(match: dict, player: str) -> bool | None:
    """Le joueur a-t-il gagne ce match ? `None` si on ne peut pas le dire.

    Le score s'ecrit du point de vue de `player1` — convention **etablie par
    recoupement** sur 800 matchs, voir `services/settlement.py`. Un set inacheve
    est un abandon : compter les sets y designerait celui qui menait.
    """
    from .settlement import read_score, surname

    # **Le champ ne s'appelle pas pareil selon la source** : `matches-played`
    # ecrit `result`, `event/get` ecrit `score`. Les deux sont acceptes, et
    # l'ordre dit lequel fait foi ici.
    score = read_score(match.get("result") or match.get("score"))
    if score is None or not score.decisif:
        return None
    un = (match.get("player1") or {}).get("name")
    deux = (match.get("player2") or {}).get("name")
    cible = surname(player)
    if surname(un) == cible:
        return score.sets_un > score.sets_deux
    if surname(deux) == cible:
        return score.sets_deux > score.sets_un
    return None


# -- Collecte et persistance -------------------------------------------------


async def collect(client, player: str, circuit: str, settings: Settings | None = None) -> Palmares:
    """Pagine l'historique d'un joueur et en tire son palmares.

    **`singlesCount` arrete la boucle sans demander une page de plus** : la
    source annonce son total des la premiere reponse, donc on ne paie jamais une
    page vide pour savoir qu'on a fini.
    """
    from ..providers.tennisapi import MATCHES_PLAYED, PAGE_SIZE_MAX

    matchs: list[dict] = []
    annonce = 0
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        reponse = await client.get(
            f"/profile/{player.replace(' ', '%20')}/matches-played",
            MATCHES_PLAYED,
            params={"pageSize": PAGE_SIZE_MAX, "page": page},
        )
        pages += 1
        charge = reponse.data if isinstance(reponse.data, dict) else {}
        lot = charge.get("singles") or []
        annonce = int(charge.get("singlesCount") or annonce)
        matchs.extend(lot)
        if not lot or (annonce and len(matchs) >= annonce):
            break
    editions = summarise(matchs, player)
    dates = sorted(str(m.get("date") or "")[:10] for m in matchs if m.get("date"))
    return Palmares(
        player=player,
        circuit=circuit,
        editions=editions,
        as_of=dates[-1] if dates else "",
        pages=pages,
        announced=annonce,
    )


def store(entry: Palmares, settings: Settings | None = None) -> None:
    """Persiste le resume. La charge utile brute n'est **jamais** gardee."""
    charge = json.dumps(
        [
            {
                "t": e.tournament,
                "c": e.tier,
                "s": e.surface,
                "y": e.year,
                "r": e.round,
                "w": e.won,
            }
            for e in entry.editions
        ]
    )
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO player_palmares (player, circuit, payload_json, as_of, fetched_at, "
            "                             pages, announced) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(player, circuit) DO UPDATE SET payload_json = excluded.payload_json, "
            "  as_of = excluded.as_of, fetched_at = excluded.fetched_at, "
            "  pages = excluded.pages, announced = excluded.announced",
            (
                entry.player,
                entry.circuit,
                charge,
                entry.as_of,
                utcnow(),
                entry.pages,
                entry.announced,
            ),
        )
    logger.info(
        "Palmares %s (%s) : %d edition(s), %d page(s) pour %d matchs annonces",
        entry.player,
        entry.circuit,
        len(entry.editions),
        entry.pages,
        entry.announced,
    )


def load(player: str, settings: Settings | None = None) -> Palmares | None:
    """Le palmares memorise d'un joueur, ou `None`."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT circuit, payload_json, as_of, pages, announced FROM player_palmares "
            " WHERE player = ? LIMIT 1",
            (player,),
        ).fetchone()
    if row is None:
        return None
    try:
        charge = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    return Palmares(
        player=player,
        circuit=row["circuit"],
        editions=[
            Edition(
                tournament=e.get("t", ""),
                tier=e.get("c", ""),
                surface=e.get("s", ""),
                year=e.get("y", ""),
                round=e.get("r", ""),
                won=bool(e.get("w")),
            )
            for e in charge
            if isinstance(e, dict)
        ],
        as_of=row["as_of"],
        pages=int(row["pages"] or 0),
        announced=int(row["announced"] or 0),
    )


# -- Rendu -------------------------------------------------------------------


def _best(editions: list[Edition]) -> tuple[str, str, str] | None:
    """Le tour le plus profond atteint, son annee et **sa surface**.

    La surface est celle de l'edition qui a produit ce resultat, pas celle du
    lot : une categorie s'etale sur plusieurs surfaces, et en rendre une seule
    pour l'ensemble serait faux. Ce qui interesse est **ou** le joueur est alle
    le plus loin.
    """
    from .tennis_history import FINAL, ROUND_LABELS, ROUND_RANKS

    meilleur: tuple[int, str, bool, str, str] | None = None
    for e in editions:
        rang = ROUND_RANKS.get(e.round)
        if rang is None:
            continue
        candidat = (rang, e.round, e.won, e.year, e.surface)
        if meilleur is None or rang > meilleur[0] or (rang == meilleur[0] and e.year > meilleur[3]):
            meilleur = candidat
    if meilleur is None:
        return None
    _, cle, gagne, annee, surface = meilleur
    if cle == FINAL:
        return ("vainqueur" if gagne else "finaliste", annee, surface)
    return (ROUND_LABELS.get(cle, cle), annee, surface)


#: La categorie d'un tournoi, de la taxonomie du projet vers celle du
#: fournisseur. **Table verifiee a la main**, six entrees, exactement l'idiome
#: d'`APIFOOTBALL_LEAGUES` : les deux vocabulaires sont fermes et courts, et
#: deduire l'un de l'autre par le libelle serait l'invention que ce projet
#: refuse partout.
#:
#: `masters_1000` couvre les Masters 1000 de l'ATP **et** les WTA 1000 — meme
#: etage de la hierarchie, deja amalgames par la taxonomie du projet — donc le
#: circuit tranche.
TIER_BY_CATEGORY: dict[tuple[str, str], str] = {
    ("grand_slam", "atp"): "Grand Slam",
    ("grand_slam", "wta"): "Grand Slam",
    ("masters_1000", "atp"): "ATP Masters 1000",
    ("masters_1000", "wta"): "WTA 1000",
    ("level_500", "atp"): "ATP 500",
    ("level_500", "wta"): "WTA 500",
}


def tier_for(category: str, circuit: str) -> str:
    """La categorie du fournisseur pour cette competition, ou vide."""
    return TIER_BY_CATEGORY.get((str(category or ""), str(circuit or "").lower()), "")


def fragment(player: str, entry: Palmares | None, tier: str, names: tuple[str, ...] = ()) -> str:
    """`Swiatek WTA 1000 vainqueur 2024 (21 éditions, dur)`.

    **Le denominateur accompagne toujours le resultat**, et c'est tout l'objet de
    la ligne : « demi-finale 2019, 6 participations » et « demi-finale 2019, 2
    participations » ne decrivent pas le meme joueur.

    ## La moitie « ici », et pourquoi elle revient

    Le lot precedent l'a refusee, et le refus etait juste : les deux sources ne
    nomment pas les tournois pareil — `tennis-data` ecrit « Western & Southern
    Financial Group Women's Open », `matches-played` « Cincinnati Open -
    Cincinnati » — et rendue au juge, elle annoncait « ici jamais joue » a quatre
    joueuses qui y avaient toutes joue.

    Ce qui a change n'est pas la regle mais la table : `names` porte la graphie
    du fournisseur de profils, declaree a la main et verifiee contre l'archive.
    **Sans elle, aucune moitie « ici »** — jamais une affirmation par defaut.

    Le preambule, lui, promettait deja « le meilleur resultat atteint **ici** et
    l'annee » : c'est le rendu qui s'en etait ecarte, pas le mode d'emploi.

    La categorie ne demande aucun rattachement de tournoi : elle se lit sur la
    taxonomie deja saisie de la competition.
    """
    profond = _tier_fragment(player, entry, tier)
    ici = here_fragment(player, entry, names)
    if not profond:
        return ici
    if not ici:
        return profond
    # Le joueur est deja nomme par la premiere moitie : la seconde ne le repete
    # pas, elle se pose a la suite.
    return f"{profond} · {ici.removeprefix(player + ' ')}"


def _tier_fragment(player: str, entry: Palmares | None, tier: str) -> str:
    """La moitie « a ce niveau » : `Swiatek WTA 1000 vainqueur 2024 (21 éditions, dur)`."""
    if entry is None or not entry.editions or not tier:
        return ""
    meme = [e for e in entry.editions if e.tier == tier]
    if not meme:
        # **Jamais venu a ce niveau, et c'est l'angle meme de la ligne** : un
        # joueur en finale de Masters 1000 qui n'y est jamais alle ne l'aborde
        # pas comme un habitue. Ici l'affirmation est sure — la categorie est
        # servie par la source, pas rapprochee par un libelle.
        return f"{player} {tier} jamais joué"
    meilleur = _best(meme)
    if meilleur is None:
        return ""
    from .tennis_history import SURFACE_LABELS

    tour, annee, surface = meilleur
    editions = f"{len(meme)} édition" + ("s" if len(meme) > 1 else "")
    # La surface en francais, **par la table du projet** : deux vocabulaires de
    # surface dans un meme bloc se liraient comme deux choses differentes.
    libelle = SURFACE_LABELS.get(surface.strip().lower(), surface.lower())
    detail = ", ".join(part for part in (editions, libelle) if part)
    return f"{player} {tier} {tour} {annee} ({detail})"


def _fold_name(value: str) -> str:
    """Un nom de tournoi, casse et espaces normalises. **Rien de flou.**

    L'egalite porte sur la graphie canonique de la source, declaree a la main :
    ce repli ne rattrape qu'un espace en trop ou une majuscule, jamais un nom
    voisin. Rapprocher deux tournois par similarite de libelle est exactement ce
    que ce projet refuse — et ce que le lot precedent a refuse de livrer.
    """
    return " ".join(str(value or "").split()).casefold()


def here_fragment(player: str, entry: Palmares | None, names: tuple[str, ...]) -> str:
    """`Swiatek ici vainqueur 2025 (8 éditions)` — le meilleur resultat **ici**.

    ## Le mode d'echec commande le dessin

    **Sans rattachement, aucune ligne** : ni « ici jamais joue », ni rien. Le lot
    precedent a refuse de livrer cette moitie pour cette raison exacte — rendue
    au juge, elle annoncait « ici jamais joue » a quatre joueuses qui avaient
    toutes joue a Cincinnati. Une affirmation fausse est pire qu'une ligne
    absente, et c'est la meme regle que les alertes meteo non interrogees et que
    le repli par pays du lieu.

    **Avec rattachement, en revanche, « jamais joue » est un fait** — et c'est
    l'angle meme de la ligne : un joueur en quart ici qui n'y est jamais passe
    ne l'aborde pas comme un habitue. La difference entre les deux cas n'est pas
    de degre : dans le premier on ignore le nom du tournoi chez la source, dans
    le second on le connait et on ne le trouve pas dans son historique.

    L'annee n'accompagne pas le compte comme dans `fragment` : le palmares
    profond dit deja jusqu'ou remonte l'historique lu.
    """
    if entry is None or not names:
        return ""
    if not entry.editions:
        return ""
    voulus = {_fold_name(nom) for nom in names}
    ici = [e for e in entry.editions if _fold_name(e.tournament) in voulus]
    if not ici:
        return f"{player} ici jamais joué"
    meilleur = _best(ici)
    if meilleur is None:
        return ""
    tour, annee, _ = meilleur
    editions = f"{len(ici)} édition" + ("s" if len(ici) > 1 else "")
    return f"{player} ici {tour} {annee} ({editions})"


def unmatched(entry: Palmares | None, names: tuple[str, ...]) -> list[str]:
    """Les noms declares que l'historique de ce joueur ne porte pas.

    **Ce n'est pas une erreur** : un joueur peut n'avoir jamais joue ici, et une
    graphie ancienne ne figure dans l'historique de personne. C'est une mesure —
    un nom declare qui ne sert jamais est soit une faute de frappe, soit une
    edition sortie de la fenetre, et le seul moyen de les distinguer est de les
    compter dans le temps. Le journal les nomme des **deux** cotes.
    """
    if entry is None or not names:
        return []
    presents = {_fold_name(e.tournament) for e in entry.editions}
    return [nom for nom in names if _fold_name(nom) not in presents]


#: Peremption d'un palmares. **Long, et c'est justifie** : une edition de plus
#: ne change un palmares que si le joueur y va plus loin qu'il n'est jamais alle.
#: Le relire chaque jour paierait la pagination pour un resultat identique.
TTL_HOURS = 24 * 7


def due(player: str, now: str, settings: Settings | None = None) -> bool:
    """Ce palmares doit-il etre repris ?

    Une date de releve illisible vaut **perimee** : mieux vaut un appel de trop
    qu'un palmares dont on ne sait plus quand il a ete pris — meme regle que
    `dossier.ttl_for`.
    """
    from datetime import datetime, timedelta

    with connect(settings) as conn:
        row = conn.execute(
            "SELECT fetched_at FROM player_palmares WHERE player = ?", (player,)
        ).fetchone()
    if row is None:
        return True
    try:
        pris = datetime.fromisoformat(str(row["fetched_at"]).replace("Z", "+00:00"))
        maintenant = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    except ValueError:
        return True
    return maintenant - pris >= timedelta(hours=TTL_HOURS)


async def refresh(client, joueurs, settings: Settings | None = None) -> int:
    """Reprend les palmares perimes. Rend le nombre de joueurs repris.

    **Un joueur qui echoue n'empeche pas les suivants** : la pagination porte sur
    un profil, et une source muette sur l'un n'apprend rien sur les autres.
    """
    settings = settings or get_settings()
    repris = 0
    maintenant = utcnow()
    for nom, circuit in joueurs:
        if not due(nom, maintenant, settings):
            continue
        try:
            store(await collect(client, nom, circuit, settings), settings)
            repris += 1
        except Exception:
            logger.exception("Palmares indisponible pour %s", nom)
    return repris
