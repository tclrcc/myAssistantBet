"""Import des rencontres d'un tournoi que The Odds API ne sert pas (`tennis-api.com`).

Deux familles de cas, mesurees le 24/08/2026 sur `/sports?all=true` — 176 cles,
dont **44 au tennis** :

- **un tableau de qualification de Grand Chelem** n'y a aucune cle, sur aucun
  des quatre tournois ;
- **un tournoi entier** peut n'y figurer pas davantage : le Winston-Salem Open,
  ATP 250 en cours ce jour-la, n'a **aucune cle** — ni « winston », ni « salem ».

Les deux entrent par ici. Leurs rencontres n'arrivaient sinon que par la saisie
manuelle, une par une, quand `tennis-api.com` les sert deja et que le contrat est
en cours.

**Ce n'est pas le cas general** : une competition que The Odds API sert n'a rien
a faire ici — elle arrive par le scan, **avec ses cotes**. Le WTA Monterrey Open
est dans ce cas, et le seul chemin correct pour lui est de l'activer.

## Ce que ce module ne fait pas, et c'est delibere

- **Il ne ramene aucune cote**, et il n'en invente pas. Aucune source ne sert
  les prix d'une qualification : les rencontres arrivent nues et le restent
  jusqu'a une saisie a la main. Poser une valeur par defaut ferait entrer un
  prix qui n'a jamais ete releve dans le calcul de palier, donc dans le residu
  au prix de `/stats` — la mesure centrale du projet.
- **Il ne filtre pas sur l'heure de debut.** Voir « L'anteriorite ne se garde
  pas ici » plus bas.
- **Il ne cree aucun joueur** et n'entretient aucune table de joueurs : il n'en
  existe pas. Ce qu'il fait, c'est **dire** lesquels lui sont inconnus.

## La fenetre decide, pas le fournisseur

`fenetre_debut` / `fenetre_fin` portent **les dates pendant lesquelles les
rencontres de ce tournoi appartiennent a cette competition**. Sur un tournoi
entier c'est le tournoi ; sur un tableau de qualification c'en est une partie,
et la fenetre est alors le seul discriminant possible.

Le cas qui l'impose : les rencontres de qualification portent l'identifiant du
**tableau principal** (21349 ATP, 16743 WTA, `tier: Grand Slam`) — meme piege
que les qualifications europeennes chez The Odds API, qui partagent la cle de la
phase de ligue. Les deux discriminants qui viennent a l'esprit sont faux, et la
migration 077 porte la mesure : `roundId` a une semantique **invisible**
— l'endpoint ne servant rien au-dela de J+1, aucune rencontre de tableau
principal n'est la pour trancher — et la date de fiche du tournoi est fausse
d'un jour (31/08 annonce pour un tableau principal qui debute le 30/08).

**Elle reste obligatoire meme quand elle ne discrimine rien**, et c'est un
arbitrage : elle ne coute rien a saisir, elle borne l'edition, et elle rend
visible un tour repousse. La rendre facultative ferait qu'une fenetre oubliee
sur un Grand Chelem importerait le tableau principal sous le nom des
qualifications, en silence.

**Et ce qui tombe dehors est compte, jamais jete en silence.** C'est ce qui
permet a la fenetre d'etre serree sans risque : un report de pluie qui pousse le
tour final au vendredi se lit dans le rapport (`hors_fenetre`) et se corrige
d'un champ, au lieu de faire disparaitre seize rencontres sans un mot.

## Les doubles n'entrent pas

Le flux les sert — trois rencontres de double le 24/08, sur d'autres tournois —
et le projet ne les modelise nulle part : `tennis_load` note que le fournisseur
de cotes ne les sert pas, et le bloc CONTEXTE n'a rien a dire d'une paire. Ils
se reconnaissent a leur nom, `Jesse Delaney/Emile Hudd`. Le risque n'est pas
theorique : la Fan Week heberge un championnat de double mixte, et rien ne dit
qu'il ne porte pas l'identifiant du tournoi.

## Le rapprochement des joueurs passe par `elo.lookup`, pas par `sort_key`

**Mesure du 24/08/2026 sur les 112 joueurs du jour**, contre les 1 099 lignes de
`tennis_elo` : `elo.lookup` en rapproche **108 (96 %)**, une egalite stricte sur
`labels.sort_key` **106 (94 %)**. Les deux normalisations existent deja, et
c'est `elo.normalize` qui est ecrite **pour des noms de joueurs** — son
docstring dit qu'elle est volontairement distincte des autres. Employer
`sort_key` ici serait donc ecrire la seconde normalisation de joueur, pas
reutiliser la premiere.

Les quatre non rapproches sont de vraies absences du classement — des joueurs
hors des 550 premiers, ce qui est le regime normal d'un tableau de
qualification. `elo.lookup` refuse plutot que de deviner, et ce refus est juste.

**Ils sont rapportes, et la rencontre est creee quand meme.** Refuser de creer
ferait perdre 4 % des rencontres d'un lot pour une raison qui n'est pas
sportive : le joueur existe, c'est notre classement qui s'arrete avant lui. Ce
que le rapport dit, c'est de quels blocs le contexte sera pauvre.

**Le refus recouvre deux cas, et le libelle ne les separe pas** — releve sur
l'import reel du 24/08, scores de `elo.similarity` contre le meilleur candidat :

  * **trois quasi-homonymes**, le meme joueur sous une autre graphie —
    « Greetje Minnen » contre « Greet Minnen » (0.857), « Chak Lam Coleman
    Wong » contre « Coleman Wong » (0.571), « Diego Dedura » contre « Diego
    Dedura Palomero » (0.571) ;
  * **deux absences franches** — « Spencer Johnson » et « Jordan Lee », dont
    les meilleurs candidats sont « Alexander Donski » (0.438) et « Johan
    Nikles » (0.500), c'est-a-dire personne.

Aucun n'est une affaire de ponctuation ni de translitteration : `elo.normalize`
replie deja casse, accents et ponctuation. Ce qui separe est le **nombre de
prenoms** — un nom d'usage contre un etat civil complet — et `similarity`
compare des chaines entieres.

**Le seuil ne bouge pas pour autant.** `MIN_SCORE` vaut 0.88 par mesure, et le
cas le plus tentant est « Greetje Minnen » a 0.857, soit 0.023 sous la barre :
descendre pour l'attraper rapprocherait aussi des paires que le projet refuse
exprès — les freres Zverev tiennent sur cet ecart. Il n'existe aucune resolution
manuelle cote tennis, et attribuer a un joueur le rating d'un autre serait pire
qu'une ligne absente. Regler un seuil documente sur l'echantillon d'un jour est
la faute que ce depot a deja payee deux fois.

C'est le libelle du rapport qui porte la nuance : **« sans ligne Elo »** et non
« joueur inconnu ». Les deux etats appellent le meme comportement — verifier a
la main — mais le second serait faux quatre fois sur cinq.

## Le plancher de quota ne s'applique pas ici, et c'est le meme arbitrage

`rapidapi_call_floor` (20 000) garde `serve_stats`, qui depense **un appel par
joueur** — des centaines sur une reprise. Cet import en coute **deux par
competition et par jour**, soit quatre pour tout l'US Open : au releve du
24/08/2026, 139 244 appels restaient sur le mois. Le refuser sur le plancher
serait un refus sans objet.

Et surtout, la raison est celle du dossier d'equipe cote API-Football :
`APIFOOTBALL_CALL_FLOOR` ne bloque que le bonus, jamais le contexte d'un match.
Ici les rencontres **sont** la fonction premiere — sans elles il n'y a rien a
analyser — et un plancher qui les arreterait protegerait le quota en supprimant
ce pour quoi il est depense.

## L'anteriorite ne se garde pas ici, et c'est une decision

Les qualifications commencent a 11h00 a New York, soit 17h00 a Paris : un import
lance le soir touche des rencontres deja jouees. Elles entrent quand meme.

- La garde d'anteriorite porte sur l'**ecriture d'une selection**
  (`history.add_pick`), pas sur l'existence d'un match. Elle refuse deja un pari
  pose sur une rencontre commencee, et rien de ce qui est importe ici ne la
  contourne.
- `session.has_started()` retire deja du prompt, de l'enrichissement et du
  compteur de selection tout evenement dont l'heure est passee. Une rencontre
  importee en retard est donc **visible et marquee**, jamais jouable.
- Et surtout : `tennis_load` calcule `Repos`, `Parcours` et `Fraicheur` sur
  **nos propres releves**. Un tour manquant y produit un parcours faux — c'est
  le defaut deja paye sur Norrie, dont le premier tour manquait. Filtrer a
  l'import abimerait les lignes du lendemain pour proteger une garde qui existe
  ailleurs.

## L'heure est une **plage de session**, pas un horaire

Les 28 rencontres du 24/08 se repartissent sur quatre valeurs exactes (18:00,
19:30, 21:00, 22:30 UTC), six a huit par valeur ; le 25/08, 32 des 36 portent
18:00 pile. Ce sont des creneaux, pas des coups d'envoi. La consequence se
connait avant de lire un bloc : `Repos` et `has_started` s'appuient dessus a une
demi-journee pres, et deux rencontres du meme creneau ne se departagent pas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError
from ..providers.tennisapi import TennisAPIClient
from . import elo as elo_service

logger = logging.getLogger(__name__)

#: `events.source` de ce chemin. Distincte d'`api`, de `manual` et
#: d'`apifootball` : savoir d'ou vient un match explique pourquoi il n'a pas de
#: cotes, et evite de chercher une panne de scan.
SOURCE = "tennisapi"

#: Garde-fou de pagination. Une journee de Grand Chelem tient en une ou deux
#: pages de 100 ; au-dela, c'est que `hasNextPage` ment ou qu'on boucle.
MAX_PAGES = 12

#: Ce qui separe un double d'un simple dans ce flux : le nom porte les deux
#: joueurs separes par une barre. Il n'y a pas de champ pour le dire.
DOUBLES_MARK = "/"


@dataclass
class ImportJour:
    """Ce qu'un import a produit, y compris quand il n'a rien produit."""

    competition: str
    day: str
    created: int = 0
    updated: int = 0
    doubles: int = 0
    hors_fenetre: int = 0
    #: Joueurs pour qui `elo.lookup` ne rend rien — nom non rapproche **ou**
    #: hors classement, les deux etant indiscernables d'ici. Nommes et non
    #: comptes : un compte se lit comme une file d'attente qui se resorbe, alors
    #: qu'ici c'est le nom qui dit quel bloc verifier a la main.
    joueurs_inconnus: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def note(self) -> str:
        """Phrase affichable. Ne tait jamais un refus ni une absence."""
        if self.error:
            return f"{self.competition} : {self.error}"
        morceaux = [
            f"{self.competition}, {self.day} : "
            f"{self.created} rencontre(s) ajoutee(s), {self.updated} mise(s) a jour."
        ]
        if not self.created and not self.updated:
            morceaux = [f"{self.competition}, {self.day} : aucune rencontre servie."]
        if self.hors_fenetre:
            morceaux.append(
                f"{self.hors_fenetre} rattachee(s) au tournoi mais hors de la fenetre de "
                "qualification — a verifier si un tour a ete repousse."
            )
        if self.doubles:
            morceaux.append(f"{self.doubles} double(s) ignore(s).")
        if self.joueurs_inconnus:
            morceaux.append(
                f"{len(self.joueurs_inconnus)} joueur(s) sans ligne Elo "
                "(nom non rapproche ou hors classement) : " + ", ".join(self.joueurs_inconnus)
            )
        return " ".join(morceaux)


def _competition(competition_id: int, settings: Settings) -> dict[str, Any] | None:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT c.id, c.label, c.sport_id, c.fenetre_debut, c.fenetre_fin, "
            "       c.tennisapi_tour, c.tennisapi_tournament_id, s.key AS sport_key "
            "  FROM competitions c JOIN sports s ON s.id = c.sport_id WHERE c.id = ?",
            (competition_id,),
        ).fetchone()
    return dict(row) if row else None


def _iso_day(value: str | None) -> date | None:
    """`2026-08-24` -> date. Une saisie illisible vaut « non renseignee »."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _fixture_day(value: str | None) -> date | None:
    """Jour UTC d'une rencontre du fournisseur, ou None si la date est illisible."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC).date()


def _commence(value: str) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_doubles(fixture: dict[str, Any]) -> bool:
    noms = [
        ((fixture.get("player1") or {}).get("name") or ""),
        ((fixture.get("player2") or {}).get("name") or ""),
    ]
    return any(DOUBLES_MARK in nom for nom in noms)


def _upsert(conn: Any, competition: dict[str, Any], fixture: dict[str, Any]) -> str:
    """Insere ou met a jour une rencontre.

    **Cle naturelle : le couple (competition, identifiant de rencontre).** Les
    deux circuits tiennent des compteurs separes — 1277-1334 cote ATP le 24/08,
    844-902 cote WTA — donc l'identifiant seul finira par entrer en collision.
    L'index partiel de la migration 077 tient la contrainte dans le schema.

    L'heure et les noms peuvent bouger d'un releve a l'autre : un report est une
    information, pas une raison de creer une seconde ligne.
    """
    fixture_id = fixture.get("id")
    home = ((fixture.get("player1") or {}).get("name") or "").strip()
    away = ((fixture.get("player2") or {}).get("name") or "").strip()
    if fixture_id is None or not home or not away or not fixture.get("date"):
        return "ignore"

    commence = _commence(str(fixture["date"]))
    existing = conn.execute(
        "SELECT id FROM events WHERE competition_id = ? AND tennisapi_fixture_id = ?",
        (competition["id"], int(fixture_id)),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE events SET home = ?, away = ?, commence_time = ? WHERE id = ?",
            (home, away, commence, existing["id"]),
        )
        return "updated"

    conn.execute(
        "INSERT INTO events (sport_id, competition_id, tennisapi_fixture_id, home, away, "
        "                    commence_time, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            competition["sport_id"],
            competition["id"],
            int(fixture_id),
            home,
            away,
            commence,
            SOURCE,
            utcnow(),
        ),
    )
    return "created"


async def _all_pages(client: TennisAPIClient, tour: str, day: str) -> list[dict[str, Any]]:
    """Toutes les pages d'une journee. Le defaut du fournisseur est 10.

    Un premier releve sans pagination avait rendu dix lignes et fait croire a un
    flux minuscule ; cote WTA, `hasNextPage` etait encore vrai a cent.
    """
    lignes: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        response = await client.fixtures(tour, day, page)
        payload = response.data if isinstance(response.data, dict) else {}
        lignes += [item for item in (payload.get("data") or []) if isinstance(item, dict)]
        if not payload.get("hasNextPage"):
            return lignes
    logger.warning(
        "tennis-api : %s %s pagine au-dela de %d pages, lecture arretee", tour, day, MAX_PAGES
    )
    return lignes


async def import_day(
    client: TennisAPIClient,
    competition_id: int,
    day: str,
    settings: Settings | None = None,
) -> ImportJour:
    """Importe les rencontres d'un tableau de qualification pour **un jour**.

    Un jour et pas la fenetre entiere : l'endpoint est l'ordre du jour publie,
    pas un calendrier — il ne sert rien au-dela de J+1 (mesure dans le client).
    L'import se relance donc chaque jour du tableau, et relancer sur un jour
    deja importe ne cree rien.
    """
    settings = settings or get_settings()
    competition = _competition(competition_id, settings)
    if competition is None:
        return ImportJour(competition=str(competition_id), day=day, error="competition inconnue")

    label = competition["label"]
    if competition["sport_key"] != "tennis":
        return ImportJour(competition=label, day=day, error="ce fournisseur ne sert que le tennis")

    tour = (competition["tennisapi_tour"] or "").strip().lower()
    tournoi = competition["tennisapi_tournament_id"]
    if not tour or tournoi is None:
        return ImportJour(
            competition=label,
            day=day,
            error=(
                "aucun tournoi tennis-api rattache : le circuit et l'identifiant se "
                "saisissent, ils ne se deduisent pas du libelle"
            ),
        )

    debut = _iso_day(competition["fenetre_debut"])
    fin = _iso_day(competition["fenetre_fin"])
    if debut is None or fin is None:
        return ImportJour(
            competition=label,
            day=day,
            error=(
                "aucune fenetre du tournoi renseignee : c'est elle qui distingue "
                "une qualification du tableau principal, et elle ne se devine pas"
            ),
        )
    if debut > fin:
        return ImportJour(competition=label, day=day, error="fenetre du tournoi inversee")

    jour = _iso_day(day)
    if jour is None:
        return ImportJour(competition=label, day=day, error=f"date illisible : {day}")
    if not (debut <= jour <= fin):
        return ImportJour(
            competition=label,
            day=day,
            error=(
                f"le {jour:%d/%m} est hors de la fenetre du tournoi ({debut:%d/%m} au {fin:%d/%m})"
            ),
        )

    try:
        lignes = await _all_pages(client, tour, day)
    except ProviderError as exc:
        logger.warning("Import des qualifications impossible pour %s : %s", label, exc)
        return ImportJour(competition=label, day=day, error=str(exc))

    report = ImportJour(competition=label, day=day)
    inconnus: list[str] = []
    a_ecrire: list[dict[str, Any]] = []

    for fixture in lignes:
        if fixture.get("tournamentId") != tournoi:
            continue
        if _is_doubles(fixture):
            report.doubles += 1
            continue
        quand = _fixture_day(fixture.get("date"))
        if quand is None:
            continue
        if not (debut <= quand <= fin):
            report.hors_fenetre += 1
            continue
        a_ecrire.append(fixture)

    with connect(settings) as conn:
        for fixture in a_ecrire:
            issue = _upsert(conn, competition, fixture)
            if issue == "created":
                report.created += 1
            elif issue == "updated":
                report.updated += 1

    # Le rapprochement se fait apres l'ecriture et ne la conditionne pas : un
    # joueur hors classement reste un joueur, et c'est le contexte de son bloc
    # qui sera pauvre, pas la rencontre qui est fausse.
    for fixture in a_ecrire:
        for cote in ("player1", "player2"):
            nom = ((fixture.get(cote) or {}).get("name") or "").strip()
            if nom and nom not in inconnus and elo_service.lookup(nom, tour, settings) is None:
                inconnus.append(nom)
    report.joueurs_inconnus = inconnus

    logger.info(
        "Import tennis-api pour %s le %s : %d creee(s), %d mise(s) a jour, "
        "%d hors fenetre, %d double(s), %d joueur(s) hors classement",
        label,
        day,
        report.created,
        report.updated,
        report.hors_fenetre,
        report.doubles,
        len(inconnus),
    )
    return report
