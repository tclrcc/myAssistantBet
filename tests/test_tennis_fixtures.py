"""Import d'un tableau de qualification depuis `tennis-api.com`.

Ce que ces tests gardent, dans l'ordre de ce qui couterait le plus cher :

- **la fenetre decide**, et rien d'autre. Les rencontres de qualification
  portent l'identifiant du tableau principal, donc un discriminant tire du
  fournisseur classerait un jour une rencontre du mauvais cote ;
- **ce qui tombe dehors est compte**, jamais jete en silence. C'est ce qui
  permet a la fenetre d'etre serree : un report se lit dans le rapport ;
- **l'idempotence**, tenue par un index partiel et pas seulement par la
  discipline du service ;
- **aucune cote n'est inventee**. Un prix jamais releve qui entrerait dans un
  palier fausserait le residu au prix de `/stats`.

Aucun test ne sort sur le reseau : `respx` sert les deux pages figees a partir
d'une reponse reelle du 24/08/2026.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.tennisapi import BASE_URL, TennisAPIClient
from myassistantbet.services import elo as elo_service
from myassistantbet.services import tennis_fixtures
from myassistantbet.services.competitions import create_manual

QUOTA_HEADERS = {"x-ratelimit-requests-remaining": "139000"}

#: Le tournoi vise : c'est celui du **tableau principal**, les qualifications
#: n'en ayant pas de distinct chez le fournisseur.
TOURNOI = 21349
FENETRE = ("2026-08-24", "2026-08-27", "atp", str(TOURNOI))

#: Les joueurs des rencontres figees, moins « Chak Lam Coleman Wong » : il est
#: absent du classement reel, et c'est lui qui eprouve le rapport.
CLASSES = (
    "Stefano Napolitano",
    "Mackenzie Mcdonald",
    "Raul Brancaccio",
    "Thiago Seyboth Wild",
    "Pol Martin Tiffon",
    "Tom Gentzsch",
    "Keegan Smith",
    "Alexis Galarneau",
    "Francesco Passaro",
    "Felix Gill",
    "Dalibor Svrcina",
    "Felipe Meligeni Alves",
    "Elmer Moller",
)


@pytest.fixture
def tennis_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAPIClient:
    return TennisAPIClient(http_client, migrated)


@pytest.fixture
def qualifs(migrated: Settings) -> int:
    """La competition, avec sa fenetre et son rattachement."""
    return create_manual(
        "ATP US Open Qualifications",
        "tennis",
        "qualifications",
        migrated,
        qualification=FENETRE,
    )


def _classement(settings: Settings, noms: tuple[str, ...] = CLASSES) -> None:
    elo_service.store(
        "atp",
        [{"player": nom, "elo_rank": rang, "elo": 1800} for rang, nom in enumerate(noms, 1)],
        settings,
    )


def _servir(load_fixture: Callable[[str], Any]) -> None:
    """Les deux pages figees. La pagination est reelle : page 1 annonce une suite."""
    pages = {
        1: load_fixture("tennisapi_fixtures_qualif_page1.json"),
        2: load_fixture("tennisapi_fixtures_qualif_page2.json"),
    }

    def _repondre(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=pages[page], headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)


# -- Ce que l'import ecrit ---------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_les_rencontres_du_tournoi_entrent(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    _classement(migrated)
    _servir(load_fixture)

    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert report.error is None
    assert report.created == 7, "sept simples du tournoi, dans la fenetre"
    rows = db.query(
        "SELECT home, away, source, tennisapi_fixture_id FROM events WHERE competition_id = ?",
        (qualifs,),
        settings=migrated,
    )
    assert len(rows) == 7
    assert {row["source"] for row in rows} == {tennis_fixtures.SOURCE}
    assert all(row["tennisapi_fixture_id"] for row in rows)


@respx.mock
@pytest.mark.anyio
async def test_la_pagination_est_parcourue(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """Le defaut du fournisseur est **10** lignes par page, et `hasNextPage`
    etait encore vrai a cent cote WTA. Un import qui lirait la premiere page
    seule rendrait un tableau ampute sans que rien ne le dise."""
    _classement(migrated)
    _servir(load_fixture)

    await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert len(respx.calls) == 2, "deux pages lues, pas une"


@respx.mock
@pytest.mark.anyio
async def test_aucune_cote_n_est_creee(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """Aucune source ne sert les prix d'une qualification. Un prix par defaut
    entrerait dans un palier, donc dans le residu au prix de `/stats`."""
    _classement(migrated)
    _servir(load_fixture)

    await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert db.query("SELECT * FROM odds", settings=migrated) == []


@respx.mock
@pytest.mark.anyio
async def test_relancer_ne_duplique_rien(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    _classement(migrated)
    _servir(load_fixture)

    premier = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)
    second = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert (premier.created, premier.updated) == (7, 0)
    assert (second.created, second.updated) == (0, 7)
    assert (
        db.query_one(
            "SELECT COUNT(*) AS n FROM events WHERE competition_id = ?",
            (qualifs,),
            settings=migrated,
        )["n"]
        == 7
    )


@respx.mock
@pytest.mark.anyio
async def test_l_index_refuse_le_doublon_meme_hors_du_service(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """L'idempotence vit dans le **schema** et pas seulement dans la discipline
    du service : une seconde ecriture par un autre chemin doit echouer."""
    _classement(migrated)
    _servir(load_fixture)
    await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    row = db.query_one(
        "SELECT sport_id, tennisapi_fixture_id FROM events WHERE competition_id = ?",
        (qualifs,),
        settings=migrated,
    )
    with pytest.raises(Exception, match="UNIQUE"):
        db.execute(
            "INSERT INTO events (sport_id, competition_id, tennisapi_fixture_id, home, away, "
            "commence_time, source, created_at) VALUES (?, ?, ?, 'A', 'B', 'x', 'x', 'x')",
            (row["sport_id"], qualifs, row["tennisapi_fixture_id"]),
            settings=migrated,
        )


# -- Ce que l'import ecarte, et le dit ---------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_une_rencontre_hors_fenetre_est_comptee_pas_jetee(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """C'est ce qui autorise une fenetre serree. Le tour final peut glisser au
    vendredi sous la pluie : il faut que ca se voie, pas que ca disparaisse."""
    _classement(migrated)
    _servir(load_fixture)

    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert report.hors_fenetre == 1
    assert "hors de la fenetre" in report.note
    assert not db.query(
        "SELECT id FROM events WHERE commence_time LIKE '2026-08-30%'", settings=migrated
    )


@respx.mock
@pytest.mark.anyio
async def test_les_doubles_n_entrent_pas(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """Le projet ne modelise le double nulle part, et la Fan Week en heberge un
    championnat. Ils se reconnaissent au nom, faute de champ pour le dire."""
    _classement(migrated)
    _servir(load_fixture)

    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert report.doubles == 1
    assert "double(s) ignore(s)" in report.note
    assert not db.query("SELECT id FROM events WHERE home LIKE '%/%'", settings=migrated)


@respx.mock
@pytest.mark.anyio
async def test_un_autre_tournoi_du_meme_jour_est_ignore(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """Le flux est par circuit et par jour, pas par tournoi : il porte tout ce
    qui se joue ce jour-la."""
    _classement(migrated)
    _servir(load_fixture)

    await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert not db.query("SELECT id FROM events WHERE home = 'Jan Choinski'", settings=migrated), (
        "Winston-Salem n'a rien a faire dans un tableau de qualification"
    )


# -- Le rapprochement des joueurs --------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_un_joueur_hors_classement_est_nomme_et_sa_rencontre_creee(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """**Nomme, et non compte** : c'est le nom qui dit quel bloc verifier. Et la
    rencontre entre quand meme — refuser la creerait un trou dans `Parcours`
    pour une raison qui n'est pas sportive.

    Le libelle dit « sans ligne Elo » et non « joueur inconnu », parce qu'il
    recouvre deux cas que rien ne separe d'ici : sur l'import reel du 24/08,
    trois des cinq refus sont le meme joueur sous une autre graphie et deux sont
    de vraies absences. Le seuil de `elo.lookup` ne bouge pas pour autant — il
    n'y a aucune resolution manuelle cote tennis, et un rating attribue au
    mauvais joueur est pire qu'une ligne absente."""
    _classement(migrated)
    _servir(load_fixture)

    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert report.joueurs_inconnus == ["Chak Lam Coleman Wong"]
    assert "sans ligne Elo" in report.note
    assert db.query_one(
        "SELECT id FROM events WHERE home = 'Chak Lam Coleman Wong'", settings=migrated
    )


@respx.mock
@pytest.mark.anyio
async def test_un_classement_vide_ne_fait_pas_echouer_l_import(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int, load_fixture: Any
) -> None:
    """Base vierge : tous les joueurs sont inconnus, et les rencontres entrent."""
    _servir(load_fixture)

    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-24", migrated)

    assert report.created == 7
    assert len(report.joueurs_inconnus) == 14


# -- Les refus ----------------------------------------------------------------


@pytest.mark.anyio
async def test_sans_fenetre_l_import_refuse_et_dit_pourquoi(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Sans elle, rien ne distingue une qualification du tableau principal.

    **Etat inatteignable par l'interface**, et c'est pour ca qu'il est monte a
    la main : `set_qualification` et `create_manual` posent les quatre champs
    ensemble ou aucun. Il reste atteignable en base — une competition rattachee
    a la main, une retouche — et un import qui partirait alors sans fenetre
    importerait le tableau principal sous le nom des qualifications.
    """
    competition_id = create_manual("Tournoi a moitie regle", "tennis", settings=migrated)
    db.execute(
        "UPDATE competitions SET tennisapi_tour = 'atp', tennisapi_tournament_id = ? WHERE id = ?",
        (TOURNOI, competition_id),
        settings=migrated,
    )

    report = await tennis_fixtures.import_day(tennis_client, competition_id, "2026-08-24", migrated)

    assert "aucune fenetre de qualification" in (report.error or "")
    assert report.created == 0


@pytest.mark.anyio
async def test_une_competition_ordinaire_refuse_et_nomme_ce_qui_manque(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Le cas courant : une competition de tennis sans rien de tout ceci.

    Le refus nomme le **rattachement**, qui manque en premier — dire « aucune
    fenetre » enverrait renseigner deux champs sur quatre.
    """
    competition_id = create_manual("Tournoi ordinaire", "tennis", settings=migrated)

    report = await tennis_fixtures.import_day(tennis_client, competition_id, "2026-08-24", migrated)

    assert "aucun tournoi tennis-api rattache" in (report.error or "")
    assert report.created == 0


@pytest.mark.anyio
async def test_un_jour_hors_fenetre_est_refuse(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int
) -> None:
    report = await tennis_fixtures.import_day(tennis_client, qualifs, "2026-08-30", migrated)

    assert "hors de la fenetre" in (report.error or "")


@pytest.mark.anyio
async def test_une_competition_non_tennis_est_refusee(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    competition_id = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key LIKE 'soccer_%'", settings=migrated
    )["id"]

    report = await tennis_fixtures.import_day(tennis_client, competition_id, "2026-08-24", migrated)

    assert "ne sert que le tennis" in (report.error or "")


@pytest.mark.anyio
async def test_aucun_appel_reseau_sur_un_refus(
    tennis_client: TennisAPIClient, migrated: Settings, qualifs: int
) -> None:
    """Un refus se prononce sur ce qui est en base, avant de payer un appel.

    Ce test **ne monte aucun mock** : le moindre appel sortant le ferait echouer.
    """
    report = await tennis_fixtures.import_day(tennis_client, qualifs, "pas-une-date", migrated)

    assert "date illisible" in (report.error or "")
