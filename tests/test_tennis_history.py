"""Historique des matchs de tennis : source, rapprochement des noms, rendu.

Ce qui est verifie ici et qui n'allait pas de soi : les cotes de cloture du
fichier source ne doivent **jamais** entrer en base, le rapprochement des noms ne
doit jamais attribuer a un joueur l'historique d'un autre, et un match donne sur
tapis vert n'est pas un match joue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.tennisdata import (
    BASE_URL,
    ODDS_COLUMNS,
    RawMatch,
    TennisDataClient,
    parse_workbook,
)
from myassistantbet.services import tennis_history

FIXTURE = Path(__file__).parent / "fixtures" / "tennisdata_atp_2026.xlsx"
NOW = datetime(2026, 8, 6, tzinfo=UTC)
COMMENCE = "2026-08-10T13:00:00Z"


@pytest.fixture
def classeur() -> bytes:
    return FIXTURE.read_bytes()


@pytest.fixture
def api_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisDataClient:
    return TennisDataClient(http_client, migrated)


def _empty_workbook() -> bytes:
    """Un classeur valide sans aucun match : en-tetes seuls.

    Un match appartient a un seul circuit et a une seule saison, et les fichiers
    de la source ne se recouvrent pas. Servir la fixture pour les six couples
    circuit/saison mettrait donc six copies du meme match en base — le bilan des
    confrontations directes vaudrait alors « 6V-0D » sur une seule rencontre.
    """
    from io import BytesIO

    from openpyxl import Workbook, load_workbook

    source = load_workbook(FIXTURE, read_only=True)
    try:
        modele = source.active
        assert modele is not None
        entetes = [cell.value for cell in next(modele.iter_rows(max_row=1))]
    finally:
        source.close()
    vide = Workbook()
    assert vide.active is not None
    vide.active.append(entetes)
    tampon = BytesIO()
    vide.save(tampon)
    return tampon.getvalue()


def _mock_seasons(classeur: bytes) -> list[respx.Route]:
    """La fixture pour l'ATP 2026, un classeur vide pour les cinq autres.

    Rend les deux routes : ce qui se compte est la somme de leurs appels, la
    premiere ne servant qu'un seul des six couples circuit/saison.
    """
    precise = respx.get(f"{BASE_URL}/2026/2026.xlsx").mock(
        return_value=httpx.Response(
            200,
            content=classeur,
            headers={"content-type": "application/vnd.openxmlformats-officedocument"},
        )
    )
    generale = respx.get(url__regex=rf"{BASE_URL}/\d{{4}}w?/\d{{4}}\.xlsx").mock(
        return_value=httpx.Response(200, content=_empty_workbook())
    )
    return [precise, generale]


def _downloads(routes: list[respx.Route]) -> int:
    """Classeurs telecharges, toutes routes confondues."""
    return sum(route.call_count for route in routes)


def _lines(
    settings: Settings, home: str, away: str, surface: str | None = "clay"
) -> dict[str, str]:
    return dict(tennis_history.lines(home, away, surface, COMMENCE, settings))


# -- Lecture du classeur -----------------------------------------------------


def test_les_cotes_de_cloture_n_entrent_jamais_en_base(classeur: bytes, migrated: Settings) -> None:
    """Interdit n°1 de SPEC.md. Le fichier porte huit colonnes de cotes de
    fermeture — B365, Pinnacle, Max, Avg, Betfair — soit la matiere premiere d'un
    calcul de CLV et de value. Elles sont ecartees **a la lecture** et non au
    rendu : ce qui n'entre pas en base ne peut pas ressortir par accident."""
    matchs = parse_workbook(classeur)
    tennis_history.store("atp", 2026, matchs, migrated, NOW)

    colonnes = {
        row["name"] for row in db.query("PRAGMA table_info(tennis_matches)", settings=migrated)
    }
    assert not colonnes & ODDS_COLUMNS, "aucune colonne de cotes dans le schema"
    champs = {champ for match in matchs for champ in vars(match)}
    assert not champs & {colonne.casefold() for colonne in ODDS_COLUMNS}
    # Et aucune valeur de cote ne doit s'etre glissee dans un champ texte.
    lignes = db.query("SELECT * FROM tennis_matches LIMIT 5", settings=migrated)
    assert lignes and all("1.9" not in str(dict(ligne)) for ligne in lignes)


def test_une_ligne_sans_date_ni_joueurs_est_ignoree(classeur: bytes) -> None:
    """Le classeur porte une ligne de pied de tableau. La compter creerait un
    match fantome, avec un vainqueur vide."""
    matchs = parse_workbook(classeur)

    assert all(match.played_on and match.winner and match.loser for match in matchs)


def test_le_score_est_reconstruit_des_colonnes_de_sets(classeur: bytes) -> None:
    """Les sets vivent dans dix colonnes ; un match en deux sets n'en remplit que
    quatre, et les colonnes vides ne doivent pas produire de tirets orphelins."""
    matchs = {(m.winner, m.loser): m for m in parse_workbook(classeur)}

    finale = matchs[("Etcheverry T.", "Zverev A.")]
    assert finale.score == "7-5 6-4"
    abandon = matchs[("Etcheverry T. M.", "Sinner J.")]
    assert abandon.score == "6-3", "un seul set joue avant l'abandon"
    forfait = matchs[("Bautista Agut R.", "Alcaraz C.")]
    assert forfait.score == "", "un forfait n'a pas de score"


# -- Rapprochement des noms --------------------------------------------------


def test_une_identite_publiee_devient_une_cle(classeur: bytes) -> None:
    """« Bautista Agut R. » a un nom de famille en deux mots : le decouper sur le
    premier espace en ferait « agut|br »."""
    assert tennis_history.published_key("Bautista Agut R.") == "bautista agut|r"
    assert tennis_history.published_key("Etcheverry T. M.") == "etcheverry|tm"
    assert tennis_history.published_key("Sinner J.") == "sinner|j"


@respx.mock
@pytest.mark.anyio
async def test_le_decoupage_prenom_nom_n_est_jamais_devine(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """« Alex de Minaur » a pour nom « de Minaur », « Juan Manuel Cerundolo » a
    pour prenoms « Juan Manuel » : rien dans la chaine ne dit lequel des deux cas
    on lit. Tous les decoupages sont essayes, et un seul resultat est accepte."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    index = tennis_history.known_keys(migrated)

    assert tennis_history.resolve("Alex de Minaur", index) == ("de minaur|a",)
    assert tennis_history.resolve("Roberto Bautista Agut", index) == ("bautista agut|r",)


@respx.mock
@pytest.mark.anyio
async def test_deux_orthographes_du_meme_joueur_sont_reunies(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Neuf paires de ce genre existent dans les donnees reelles :
    « Etcheverry T. » et « Etcheverry T. M. » sont la meme personne. Les separer
    couperait son historique en deux ; les confondre avec un homonyme serait pire.
    Le critere est le nom identique **et** des initiales en chaine de prefixes."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    index = tennis_history.known_keys(migrated)

    assert tennis_history.resolve("Tomas Martin Etcheverry", index) == (
        "etcheverry|t",
        "etcheverry|tm",
    )


@respx.mock
@pytest.mark.anyio
async def test_deux_joueurs_de_meme_nom_ne_partagent_jamais_leur_historique(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Le piege des freres Zverev. Des initiales qui divergent — `a` et `m` — ne
    forment pas une chaine de prefixes : le doute vaut silence, et il n'existe
    ici aucune resolution manuelle pour rattraper une erreur."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    index = dict(tennis_history.known_keys(migrated))
    index["zverev"] = {"zverev|a", "zverev|m"}

    assert tennis_history.resolve("Sacha Zverev", index) == (), "deux joueurs : on refuse"
    assert tennis_history.resolve("Alexander Zverev", index) == ("zverev|a",), (
        "un seul : on accepte"
    )


def test_un_joueur_inconnu_ne_produit_aucune_ligne(migrated: Settings) -> None:
    """Base vierge : ecrire « aucun match connu » ferait chercher un probleme de
    rapprochement la ou il n'y a qu'une collecte jamais lancee."""
    assert tennis_history.lines("Jannik Sinner", "Carlos Alcaraz", "hard", COMMENCE, migrated) == []


# -- Collecte ----------------------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_la_collecte_est_idempotente(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Relancer ne duplique rien : cle naturelle (circuit, saison, date, joueurs)."""
    _mock_seasons(classeur)

    await tennis_history.refresh(api_client, migrated, now=NOW)
    avant = db.query_one("SELECT count(*) AS n FROM tennis_matches", settings=migrated)["n"]
    await tennis_history.refresh(api_client, migrated, now=NOW, force=True)
    apres = db.query_one("SELECT count(*) AS n FROM tennis_matches", settings=migrated)["n"]

    assert avant == apres > 0


@respx.mock
@pytest.mark.anyio
async def test_une_saison_terminee_n_est_jamais_retelechargee(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Elle ne changera plus. Seule la saison en cours se rafraichit, a la cadence
    de mise a jour du fichier — une fois par semaine."""
    routes = _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    premier = _downloads(routes)

    plus_tard = NOW + timedelta(hours=tennis_history.CURRENT_SEASON_TTL_HOURS + 1)
    rapport = await tennis_history.refresh(api_client, migrated, now=plus_tard)

    assert premier == 6, "trois saisons, deux circuits"
    assert _downloads(routes) == premier + 2, "seule la saison en cours des deux circuits"
    assert rapport.seasons == ["atp 2026", "wta 2026"]


@respx.mock
@pytest.mark.anyio
async def test_un_circuit_en_echec_n_empeche_pas_l_autre(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Une source indisponible ne coute que des lignes, jamais la collecte."""
    # Enregistree **avant** les autres : respx retient la premiere route qui
    # correspond, et l'inverse ne declencherait jamais le 404.
    respx.get(url__regex=rf"{BASE_URL}/\d{{4}}w/\d{{4}}\.xlsx").mock(
        return_value=httpx.Response(404, text="absent")
    )
    _mock_seasons(classeur)

    rapport = await tennis_history.refresh(api_client, migrated, now=NOW)

    assert not rapport.ok
    assert rapport.seasons == ["atp 2026", "atp 2025", "atp 2024"]
    assert len(rapport.errors) == 3


@respx.mock
@pytest.mark.anyio
async def test_aucun_credit_n_est_comptabilise(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Gratuit et sans cle : `api_usage` ne compte que des credits, et y ecrire un
    telechargement libre fausserait le bandeau."""
    _mock_seasons(classeur)

    await tennis_history.refresh(api_client, migrated, now=NOW)

    assert db.query("SELECT * FROM api_usage", settings=migrated) == []


# -- Rendu -------------------------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_le_bloc_porte_les_confrontations_directes_avec_leurs_scores(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Un 3V-1D dont les trois victoires tiennent en trois sets serres ne decrit
    pas le meme rapport de forces qu'un 3V-1D en deux sets secs. Le bilan s'ecrit
    `V-D` comme partout ailleurs dans le projet, jamais `3-1` — qui se lirait
    comme un score."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    lignes = _lines(migrated, "Tomas Martin Etcheverry", "Alexander Zverev")
    h2h = next(value for label, value in lignes.items() if label.startswith("H2H"))
    assert "Tomas Martin Etcheverry 1V-0D" in h2h
    assert "07/26 terre 7-5 6-4" in h2h, "la date porte l'annee : trois saisons se melangeraient"


@respx.mock
@pytest.mark.anyio
async def test_un_abandon_est_dit_dans_le_score(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """« 6-3 » seul sur une confrontation directe passerait pour une donnee
    tronquee. C'est un match interrompu, et ca se lit."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    lignes = _lines(migrated, "Tomas Martin Etcheverry", "Jannik Sinner")
    h2h = next(value for label, value in lignes.items() if label.startswith("H2H"))
    assert "6-3 ab." in h2h


@respx.mock
@pytest.mark.anyio
async def test_un_forfait_n_est_pas_un_match_joue(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Personne n'est entre sur le court : le compter en confrontation directe
    donnerait un rapport de forces sur un match qui n'a pas eu lieu. Il reste une
    information sur la disponibilite, portee par la ligne « Abandons »."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    lignes = _lines(migrated, "Roberto Bautista Agut", "Carlos Alcaraz")
    # Le forfait etait leur seul match : il ne compte pas comme une rencontre, et
    # la ligne le dit sans pretendre qu'ils n'ont jamais ete tires ensemble.
    assert lignes["H2H"] == "aucun match joue depuis 2024"
    assert "forfait" in lignes["Abandons"]


@respx.mock
@pytest.mark.anyio
async def test_le_bilan_de_surface_porte_sa_fenetre(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """« 8V-3D » sur un an et sur trois ans ne disent pas la meme chose — meme
    regle que le compte a cote d'une moyenne."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    surface = _lines(migrated, "Jannik Sinner", "Carlos Alcaraz")["Surface"]
    assert "terre" in surface
    assert "/12m" in surface


@respx.mock
@pytest.mark.anyio
async def test_aucune_ligne_de_surface_sans_surface_renseignee(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """La surface est saisie a la main sur la competition. La deduire du libelle
    d'un tournoi serait une invention — meme regle que pour l'Elo."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    lignes = _lines(migrated, "Jannik Sinner", "Carlos Alcaraz", surface=None)
    assert "Surface" not in lignes
    assert "Forme" in lignes, "le reste du bloc n'en depend pas"


@respx.mock
@pytest.mark.anyio
async def test_la_forme_se_lit_dans_le_meme_sens_qu_au_football(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """La derniere lettre est le dernier match, comme « Forme 5 ». Inverser le
    sens sur un seul des deux sports serait un piege a coup sur."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)

    forme = _lines(migrated, "Jannik Sinner", "Carlos Alcaraz")["Forme"]
    lettres = forme.split("Jannik Sinner ")[1].split("/")[0]
    assert set(lettres) <= {"V", "D"}
    dernier = db.query_one(
        "SELECT winner_key FROM tennis_matches WHERE winner_key LIKE 'sinner|%' "
        "OR loser_key LIKE 'sinner|%' ORDER BY played_on DESC LIMIT 1",
        settings=migrated,
    )
    attendu = "V" if str(dernier["winner_key"]).startswith("sinner|") else "D"
    assert lettres[-1] == attendu


@respx.mock
@pytest.mark.anyio
async def test_une_saison_sans_aucun_match_n_est_pas_redemandee_sans_fin(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Piege trouve en ecrivant le test precedent : la peremption etait deduite du
    `MAX(fetched_at)` des matchs, donc une saison vide n'avait pas de date, donc
    elle passait pour jamais telechargee. En janvier, le fichier de la saison qui
    commence est justement vide : la collecte se serait relancee a chaque
    enrichissement, sans fin et sans que rien ne le dise."""
    routes = _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    premier = _downloads(routes)

    await tennis_history.refresh(api_client, migrated, now=NOW)

    assert _downloads(routes) == premier, "rien n'est frais a redemander"
    vides = db.query(
        "SELECT tour, season FROM tennis_history_state WHERE matches = 0", settings=migrated
    )
    assert len(vides) == 5, "cinq couples circuit/saison sans match, tous memorises"


def test_le_prompt_encadre_les_bilans_de_tennis(migrated: Settings) -> None:
    """Le garde-fou compte autant que la donnee. Un bilan de confrontations est une
    frequence passee, comme les fractions du football : jamais rapprochee d'une
    cote. Et une ligne absente n'est pas un joueur sans passe."""
    from myassistantbet.services.prompt import build_prompt

    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )

    body = build_prompt(1, settings=migrated).body

    assert "ne les rapproche jamais d'une cote" in body
    assert "tapis vert n'entre dans aucun de ces comptes" in body
    assert "n'est pas un joueur sans passé" in body
    assert "même sens de lecture que « Forme 5 »" in body


# -- Ce qui s'est passe dans ce tournoi --------------------------------------


def _competition(settings: Settings, cle: str, tournois: str | None) -> int:
    """Renseigne la correspondance d'un tournoi et rend son identifiant."""
    row = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (cle,), settings=settings
    )
    assert row is not None
    db.execute(
        "UPDATE competitions SET tennisdata_tournaments = ? WHERE id = ?",
        (tournois, row["id"]),
        settings=settings,
    )
    return int(row["id"])


@respx.mock
@pytest.mark.anyio
async def test_le_palmares_distingue_une_finale_gagnee_d_une_finale_perdue(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """C'est l'erreur la plus visible que la ligne pourrait commettre : le rang du
    tour ne dit pas l'issue. Une finale gagnee vaut « vainqueur », perdue
    « finaliste »."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", "Generali Open")

    lignes = dict(
        tennis_history.lines(
            "Tomas Martin Etcheverry", "Alexander Zverev", None, COMMENCE, migrated, competition
        )
    )

    assert "Tomas Martin Etcheverry vainqueur 2026" in lignes["Palmares"]
    assert "Alexander Zverev finaliste 2026" in lignes["Palmares"]


@respx.mock
@pytest.mark.anyio
async def test_les_confrontations_dans_ce_tournoi_portent_leur_tour(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Un huitieme de finale et une finale dans le meme tournoi ne se valent pas,
    et c'est precisement ce que la question « ici » cherche."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", "Generali Open")

    lignes = dict(
        tennis_history.lines(
            "Tomas Martin Etcheverry", "Alexander Zverev", None, COMMENCE, migrated, competition
        )
    )

    assert lignes["H2H ici"] == (
        "Tomas Martin Etcheverry 1V-0D · 07/26 finale (Tomas Martin Etcheverry)"
    )


@respx.mock
@pytest.mark.anyio
async def test_sans_correspondance_de_tournoi_aucune_ligne_ici(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """La source nomme les tournois par leur sponsor : « ABN AMRO World Tennis
    Tournament » pour Rotterdam. Rien ne se deduit d'un libelle, et un palmares
    emprunte a un autre tournoi serait pire qu'une ligne absente."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", None)

    lignes = dict(
        tennis_history.lines(
            "Tomas Martin Etcheverry", "Alexander Zverev", None, COMMENCE, migrated, competition
        )
    )

    assert "Palmares" not in lignes
    assert "H2H ici" not in lignes
    assert "Forme" in lignes, "le reste du bloc n'en depend pas"


@respx.mock
@pytest.mark.anyio
async def test_un_tournoi_renomme_par_son_sponsor_reste_le_meme_tournoi(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """La source porte deja deux orthographes pour l'epreuve de Houston. Plusieurs
    noms se separent par `|` : les separer couperait un palmares en deux."""
    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    seul = _competition(migrated, "tennis_atp_french_open", "Generali Open")
    un_nom = dict(
        tennis_history.lines(
            "Tomas Martin Etcheverry", "Alexander Zverev", None, COMMENCE, migrated, seul
        )
    )
    _competition(migrated, "tennis_atp_french_open", "Generali Open|Nordea Open")
    deux_noms = dict(
        tennis_history.lines(
            "Tomas Martin Etcheverry", "Alexander Zverev", None, COMMENCE, migrated, seul
        )
    )

    # Le Generali Open porte sa finale, le Nordea Open sa victoire sur abandon.
    assert "Tomas Martin Etcheverry vainqueur 2026, 1V-0D" in un_nom["Palmares"]
    assert "Tomas Martin Etcheverry vainqueur 2026, 2V-0D" in deux_noms["Palmares"]


def test_la_table_de_correspondance_couvre_nos_tournois(migrated: Settings) -> None:
    """Le seed de la migration 020 et la table appliquee a la synchronisation
    doivent dire la meme chose : deux sources de verite finiraient par diverger."""
    from myassistantbet.services.competitions import TENNISDATA_TOURNAMENTS

    rows = db.query(
        "SELECT c.oddsapi_key, c.tennisdata_tournaments AS noms FROM competitions c "
        "JOIN sports s ON s.id = c.sport_id WHERE s.key = 'tennis'",
        settings=migrated,
    )
    assert rows, "le seed cree bien des competitions de tennis"
    for row in rows:
        attendu = TENNISDATA_TOURNAMENTS.get(row["oddsapi_key"])
        assert row["noms"] == attendu, row["oddsapi_key"]


def test_le_prompt_dit_ce_que_l_absence_des_lignes_ici_signifie(migrated: Settings) -> None:
    """Leur absence ne dit rien du passe des joueurs sur place : elle dit que le
    rattachement du tournoi manque. Confondre les deux ferait conclure d'un
    silence."""
    from myassistantbet.services.prompt import build_prompt

    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )

    body = build_prompt(1, settings=migrated).body

    assert "« finaliste » veut dire finale" in body
    assert "elle dit que le rattachement manque" in body


# -- Un seul assembleur de bloc ----------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_la_fiche_d_un_match_de_tennis_porte_le_meme_bloc_que_le_prompt(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """La fiche d'un match de tennis affichait un bloc CONTEXTE **vide** : l'Elo,
    le repos et l'historique n'existaient que dans le prompt. Deux assemblages
    paralleles ont diverge deux fois — c'est desormais le meme appel des deux
    cotes, et ce test compare les deux resultats."""
    from fastapi.testclient import TestClient

    from myassistantbet.main import app
    from myassistantbet.services import session as session_service

    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", "Generali Open")
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, source, "
        "created_at) SELECT 1, sport_id, ?, 'Tomas Martin Etcheverry', 'Alexander Zverev', ?, "
        "'api', ? FROM competitions WHERE id = ?",
        (competition, COMMENCE, db.utcnow(), competition),
        settings=migrated,
    )

    attendu = session_service.context_block(
        1,
        "Tomas Martin Etcheverry",
        "Alexander Zverev",
        COMMENCE,
        "tennis",
        oddsapi_key="tennis_atp_french_open",
        surface=None,
        competition_id=competition,
        settings=migrated,
    )
    assert any(label == "Palmares" for label, _ in attendu), "le bloc porte bien des lignes"

    with TestClient(app) as client:
        page = client.get("/events/1")

    for label, value in attendu:
        assert label in page.text, f"la fiche doit porter la ligne « {label} »"
        assert value.split(" · ")[0] in page.text


@respx.mock
@pytest.mark.anyio
async def test_la_fiche_montre_le_detail_que_la_ligne_forme_ne_peut_pas_dire(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """`VVDVDDVVVD` se lit comme un joueur irregulier ; le detail montre d'ou
    viennent les defaites. Sur l'ecran et non dans le prompt : dix rencontres par
    joueur avec adversaire, score, tournoi et tour couteraient cinq cents
    caracteres par bloc."""
    from fastapi.testclient import TestClient

    from myassistantbet.main import app

    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", "Generali Open")
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, source, "
        "created_at) SELECT 1, sport_id, ?, 'Tomas Martin Etcheverry', 'Alexander Zverev', ?, "
        "'api', ? FROM competitions WHERE id = ?",
        (competition, COMMENCE, db.utcnow(), competition),
        settings=migrated,
    )

    with TestClient(app) as client:
        page = client.get("/events/1").text

    assert "Derniers matchs joués" in page
    # Le detail : adversaire, score, tournoi et tour, pas seulement une lettre.
    assert "Zverev A." in page
    assert "7-5 6-4" in page
    assert "The Final" in page
    # Et le prompt, lui, n'en porte rien : son budget est en tokens.
    from myassistantbet.services.prompt import build_prompt

    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (9, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )
    db.execute("INSERT INTO session_events (session_id, event_id) VALUES (9, 1)", settings=migrated)
    corps = build_prompt(9, settings=migrated).body
    assert "7-5 6-4" in corps, "le score figure dans la ligne H2H ici"
    assert "Derniers matchs" not in corps, "mais pas la liste detaillee"


@respx.mock
@pytest.mark.anyio
async def test_un_joueur_non_rapproche_le_dit_sur_la_fiche(
    api_client: TennisDataClient, migrated: Settings, classeur: bytes
) -> None:
    """Sans ce message, l'absence de tableau passerait pour une panne."""
    from fastapi.testclient import TestClient

    from myassistantbet.main import app

    _mock_seasons(classeur)
    await tennis_history.refresh(api_client, migrated, now=NOW)
    competition = _competition(migrated, "tennis_atp_french_open", "Generali Open")
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, source, "
        "created_at) SELECT 1, sport_id, ?, 'Tomas Martin Etcheverry', 'Joueur Inconnu', ?, "
        "'api', ? FROM competitions WHERE id = ?",
        (competition, COMMENCE, db.utcnow(), competition),
        settings=migrated,
    )

    with TestClient(app) as client:
        page = client.get("/events/1").text

    assert "Aucun match dans l'historique collecté pour ce joueur." in page


# -- Dates hors saison -------------------------------------------------------


def _raw(played_on: str, tournament: str = "Iasi Open") -> RawMatch:
    return RawMatch(
        played_on=played_on,
        tournament=tournament,
        location="Iasi",
        series="WTA250",
        court="Outdoor",
        surface="Clay",
        round="The Final",
        winner="Sherif M.",
        loser="Badosa P.",
        score="6-4 4-0",
        comment="Retired",
    )


def test_la_saison_ouvre_en_decembre_de_l_annee_precedente() -> None:
    """Le garde-fou evident — « l'annee de la date egale la saison » — serait
    faux. Releve en base : le fichier 2025 porte 69 matchs joues du 29 au 31
    decembre 2024, celui de 2024 onze matchs du 31 decembre 2023. Les jeter
    amputerait chaque saison de son ouverture."""
    assert tennis_history.in_season("2024-12-29", 2025) is True
    assert tennis_history.in_season("2024-12-31", 2025) is True
    assert tennis_history.in_season("2025-01-01", 2025) is True
    assert tennis_history.in_season("2025-11-16", 2025) is True
    # Novembre de l'annee precedente, en revanche, est la saison d'avant.
    assert tennis_history.in_season("2024-11-16", 2025) is False


def test_une_date_posterieure_a_la_saison_est_une_coquille() -> None:
    """Le fichier 2026 datait une finale de l'Iasi Open du 20 juillet 2029."""
    assert tennis_history.in_season("2029-07-20", 2026) is False
    assert tennis_history.in_season("pas une date", 2026) is False


def test_une_date_hors_saison_n_entre_pas_en_base(migrated: Settings) -> None:
    """Le degat qu'elle fait est invisible : posterieure a tout match analyse,
    elle sort de chaque fenetre de forme, de surface et de H2H. Le match ne
    s'affiche donc nulle part, et rien ne signale le trou."""
    ecrits = tennis_history.store(
        "wta", 2026, [_raw("2026-07-20"), _raw("2029-07-20", "Iasi Open bis")], migrated, NOW
    )

    assert ecrits == 1
    dates = [
        row["played_on"]
        for row in db.query("SELECT played_on FROM tennis_matches", settings=migrated)
    ]
    assert dates == ["2026-07-20"]


def test_le_compte_de_la_collecte_ne_retient_que_les_lignes_gardees(migrated: Settings) -> None:
    """`tennis_history_state.matches` sert a savoir ce qui a ete collecte : y
    compter une ligne jetee ferait chercher en base un match qui n'y est pas."""
    tennis_history.store("wta", 2026, [_raw("2026-07-20"), _raw("2029-07-20")], migrated, NOW)

    etat = db.query_one(
        "SELECT matches FROM tennis_history_state WHERE tour = 'wta' AND season = 2026",
        settings=migrated,
    )
    assert etat["matches"] == 1


def test_la_migration_purge_les_dates_deja_ecrites(migrated: Settings) -> None:
    """Le garde-fou de `store()` ne nettoie pas le passe : une saison terminee
    n'est jamais retelechargee, et la saison en cours ne reecrit pas une ligne
    qu'elle ne renvoie plus. La migration 021 s'en charge une fois.

    Elle rejoue **le texte du fichier**, pas une copie de la regle : la version
    SQL et `in_season()` sont deux ecritures du meme critere, et rien d'autre ne
    les empeche de diverger.
    """
    from myassistantbet.config import PACKAGE_DIR

    lignes = [
        ("wta", 2026, "2026-07-20", "Iasi Open"),  # dans sa saison
        ("wta", 2026, "2029-07-20", "Iasi Open bis"),  # coquille de la source
        ("atp", 2025, "2024-12-29", "United Cup"),  # ouverture de saison, valide
        ("atp", 2025, "2024-11-29", "Tournoi d'avant"),  # saison precedente
    ]
    for tour, season, played_on, tournament in lignes:
        db.execute(
            "INSERT INTO tennis_matches (tour, season, played_on, tournament, winner, loser, "
            "winner_key, loser_key, fetched_at) VALUES (?, ?, ?, ?, 'A', 'B', ?, 'b|', ?)",
            (tour, season, played_on, tournament, f"a|{played_on}", db.utcnow()),
            settings=migrated,
        )

    sql = (PACKAGE_DIR / "migrations" / "021_dates_hors_saison.sql").read_text(encoding="utf-8")
    with db.connect(migrated) as conn:
        conn.execute(sql)

    restant = [
        row["played_on"]
        for row in db.query(
            "SELECT played_on FROM tennis_matches ORDER BY played_on", settings=migrated
        )
    ]
    assert restant == ["2024-12-29", "2026-07-20"]
