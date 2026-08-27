"""Une competition peut etre une phase d'une autre — et tous les lecteurs ne la suivent pas.

**Le defaut mesure le 26/08/2026.** Les qualifications de l'US Open vivent sous
les competitions 116 et 117, le tableau principal entrera sous 11 et 15, et rien
ne reliait les deux. `tennis_load.load_for` filtre sur `competition_id` : un
qualifie arrivant au tableau principal perdait ses trois tours sur les six lignes
qui en descendent — `Repos`, `Parcours`, `Non joue`, `Fraicheur`, `Tour`, `Ici`.

La matiere etait deja en base — 128 rencontres, 256 joueurs — et rien d'autre ne
la porte : `tennis_matches` compte huit tours distincts sur 14 239 lignes, aucun
de qualification.

**Ce que ce fichier garde surtout, c'est que le lien n'est pas une etendue.**
Applique a `tennis_round`, il ferait decider le compte des joueurs sur
128 + 256 = 384, taille d'aucun tableau : `is_bracket` rendrait faux et `Tour`
passerait en « phase non renseignee » sur toute la quinzaine. C'est le cas qui a
revele la conception, donc c'est le cas qui la garde.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import competitions as competitions_service
from myassistantbet.services import tennis_load, tennis_round

PRINCIPAL = "ATP US Open"
QUALIFS = "ATP US Open Qualifications"


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _competition(settings: Settings, label: str, sport: str = "tennis") -> int:
    row = db.query_one("SELECT id FROM sports WHERE key = ?", (sport,), settings=settings)
    db.execute(
        "INSERT INTO competitions (sport_id, label, active) VALUES (?, ?, 1)",
        (row["id"], label),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM competitions", settings=settings)["id"])


def _match(settings: Settings, competition_id: int, home: str, away: str, quand: str) -> int:
    row = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "                    created_at) VALUES (?, ?, ?, ?, ?, 'tennisapi', ?)",
        (row["id"], competition_id, home, away, quand, quand),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _tableau(settings: Settings, competition_id: int, joueurs: int, jour: str) -> None:
    """Un tour complet : `joueurs` joueurs, donc `joueurs // 2` rencontres."""
    for numero in range(joueurs // 2):
        _match(
            settings,
            competition_id,
            f"J{competition_id}-{2 * numero}",
            f"J{competition_id}-{2 * numero + 1}",
            f"{jour}T{11 + numero % 8:02d}:00:00Z",
        )


# -- Le parcours traverse le lien -------------------------------------------


def test_un_qualifie_arrive_au_tableau_principal_avec_ses_trois_tours(migrated: Settings) -> None:
    """**La matiere etait en base, et c'est l'identifiant qui ne la designait pas.**

    Sans le lien, `Repos` donne le qualifie frais, `Parcours` le donne entrant et
    `Fraicheur` ne signale rien — trois lignes fausses, aucune vide, donc rien
    qui se voie.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    for jour, adversaire in (("24", "Adv 1"), ("25", "Adv 2"), ("26", "Adv 3")):
        _match(migrated, qualifs, "Billy Harris", adversaire, f"2026-08-{jour}T18:00:00Z")

    avant = tennis_load.load_for("Billy Harris", principal, "2026-08-30T15:00:00Z", migrated)
    assert avant.opponents == (), "sans lien, le tableau principal ignore la qualification"

    competitions_service.set_phase(qualifs, principal, migrated)
    apres = tennis_load.load_for("Billy Harris", principal, "2026-08-30T15:00:00Z", migrated)

    assert apres.opponents == ("Adv 1", "Adv 2", "Adv 3")
    assert apres.days_rest is not None, "le repos se compte enfin sur un match reel"
    assert len(apres.faced) == 3


def test_un_qualifie_elimine_plus_tot_rend_moins_de_tours(migrated: Settings) -> None:
    """**Le parcours est celui du joueur, jamais celui du tableau.**

    Cas reel du 27/08/2026, apres rattrapage des deux journees manquantes : dans
    la meme competition, Svrcina porte trois tours — 24, 26 et 27/08 — et Harris
    deux, elimine au second. Un lien qui ramenerait le tableau plutot que le
    parcours rendrait trois a l'un comme a l'autre, et rien ne le signalerait :
    les deux comptes sont plausibles.

    C'est le cas sur lequel le controle empirique peut tomber, et c'est pour ca
    qu'il est ici : un parcours court doit se lire comme une elimination, pas
    comme un rattachement casse.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    competitions_service.set_phase(qualifs, principal, migrated)

    for jour, adversaire in (("24", "Meligeni Alves"), ("26", "Galarneau"), ("27", "Mcdonald")):
        _match(migrated, qualifs, "Dalibor Svrcina", adversaire, f"2026-08-{jour}T18:00:00Z")
    for jour, adversaire in (("24", "Gonzalo Bueno"), ("26", "Toby Samuel")):
        _match(migrated, qualifs, "Billy Harris", adversaire, f"2026-08-{jour}T18:00:00Z")

    quand = "2026-08-30T15:00:00Z"
    svrcina = tennis_load.load_for("Dalibor Svrcina", principal, quand, migrated)
    harris = tennis_load.load_for("Billy Harris", principal, quand, migrated)

    assert len(svrcina.faced) == 3, "le qualifie va au bout de son tableau"
    assert len(harris.faced) == 2, "elimine au second tour, et le parcours le dit"
    assert svrcina.opponents == ("Meligeni Alves", "Galarneau", "Mcdonald")
    assert harris.opponents == ("Gonzalo Bueno", "Toby Samuel")


def test_la_qualification_lue_pour_elle_meme_ne_ramene_pas_le_tableau_principal(
    migrated: Settings,
) -> None:
    """**Le lien va de la partie vers le tout, et il ne se remonte pas.**

    Un joueur n'a pas de parcours dans un tableau principal qui ne s'est pas
    encore joue ; le ramener y ferait entrer, plus tard, les matchs du tableau
    principal dans le parcours de qualification d'un autre joueur.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    competitions_service.set_phase(qualifs, principal, migrated)
    _match(migrated, principal, "Billy Harris", "Tete de serie", "2026-08-30T15:00:00Z")
    _match(migrated, qualifs, "Billy Harris", "Adv 1", "2026-08-24T18:00:00Z")

    assert competitions_service.phase_scope(principal, migrated) == (principal, qualifs)
    assert competitions_service.phase_scope(qualifs, migrated) == (qualifs,)


def test_les_journees_de_tournoi_se_decoupent_ensemble(migrated: Settings) -> None:
    """**Une qualification et son tableau principal se jouent au meme endroit.**

    `day_keys` separe par competition pour que deux tournois joues sur deux
    continents ne partagent pas leurs coupures ; ici la separation serait fausse,
    et `load_for` fait donc entrer les lignes des deux sous une seule cle.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    competitions_service.set_phase(qualifs, principal, migrated)
    # Une session de nuit : le match part apres minuit a Paris, donc sa date
    # civile est celle du lendemain. Regroupes, les deux tombent le meme jour.
    _match(migrated, qualifs, "Billy Harris", "Adv 1", "2026-08-24T22:10:00Z")
    _match(migrated, qualifs, "Autre", "Encore", "2026-08-24T23:40:00Z")

    charge = tennis_load.load_for("Billy Harris", principal, "2026-08-26T15:00:00Z", migrated)
    assert charge.days == ("2026-08-25",), "une seule journee de tournoi, sous une seule cle"


# -- Le lecteur qui ne le suit pas, et c'est le cas qui a revele la conception -


def test_le_compte_des_joueurs_ne_suit_jamais_le_lien(migrated: Settings) -> None:
    """**128 + 256 = 384, et 384 n'est la taille d'aucun tableau.**

    Tout `tennis_round` repose sur « joueurs en lice = joueurs vus moins matchs
    joues », donc sur un compte qui doit etre une taille de tableau. Une extension
    d'etendue uniforme ferait rendre faux a `is_bracket`, vrai a `truncated`, et
    ferait passer `Tour` en « phase non renseignee » sur toute la quinzaine.

    Ce test existe pour qu'elle ne soit pas retentee dans six mois : c'est le cas
    qui a impose la conception, donc c'est lui qui la garde.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    competitions_service.set_phase(qualifs, principal, migrated)
    # **Les deux tableaux sont a trois jours l'un de l'autre**, donc sous
    # `EDITION_GAP_DAYS` (5) : suivre le lien les fondrait en une seule edition
    # de 384 joueurs. Ecrits a six jours, ils se separeraient tout seuls et ce
    # test passerait quoi qu'on fasse — un test qui ne peut pas mordre donne
    # l'apparence d'un test.
    _tableau(migrated, principal, joueurs=128, jour="2026-08-30")
    _tableau(migrated, qualifs, joueurs=256, jour="2026-08-27")

    edition = tennis_round._edition_in_base(principal, "2026-08-30T15:00:00Z", migrated)
    assert edition.players == 128, "le tableau principal se compte seul"
    assert tennis_round.is_bracket(edition.players)
    assert not tennis_round.truncated(principal, "2026-08-30T15:00:00Z", migrated)

    # Et le tour se nomme, ce qui est tout l'enjeu : suivre le lien l'aurait tu.
    assert tennis_round.round_for(principal, "2026-08-30T15:00:00Z", migrated)


def test_le_nombre_de_tours_disputes_suit_le_lien(migrated: Settings) -> None:
    """`au moins 4 tours disputes` **compte les tours de qualification**.

    L'arbitrage inverse de celui du dessus, et les deux sont justes : le compte
    des joueurs decrit un tableau, le compte des tours decrit un joueur. Le
    gabarit fait de l'enjeu asymetrique une condition d'acces aux paliers hauts —
    un qualifie a trois tours dans les jambes, et c'est ce qu'il faut lire.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    for jour, adversaire in (("24", "Adv 1"), ("25", "Adv 2"), ("26", "Adv 3")):
        _match(migrated, qualifs, "Billy Harris", adversaire, f"2026-08-{jour}T18:00:00Z")

    quand = "2026-08-30T15:00:00Z"
    avant = tennis_round._rounds_played("Billy Harris", "", principal, quand, migrated)
    competitions_service.set_phase(qualifs, principal, migrated)
    apres = tennis_round._rounds_played("Billy Harris", "", principal, quand, migrated)

    assert avant == "", "sans lien, aucun tour n'est compte"
    assert "3 tours disputes par Billy Harris" in apres


# -- Les refus, et le menu qui les reprend ----------------------------------


def test_les_quatre_refus(migrated: Settings) -> None:
    """Chacun evite un lien qui se lirait ensuite comme un fait."""
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    football = _competition(migrated, "Ligue 1", sport="football")

    with pytest.raises(competitions_service.CompetitionError, match="elle-meme"):
        competitions_service.set_phase(qualifs, qualifs, migrated)
    with pytest.raises(competitions_service.CompetitionError, match="inconnue"):
        competitions_service.set_phase(qualifs, 999_999, migrated)
    with pytest.raises(competitions_service.CompetitionError, match="meme sport"):
        competitions_service.set_phase(qualifs, football, migrated)

    # La chaine, dans les deux sens : `phase_scope` lit un niveau, et une chaine
    # s'y tronquerait sans un mot.
    competitions_service.set_phase(qualifs, principal, migrated)
    troisieme = _competition(migrated, "ATP US Open Pre-qualifications")
    with pytest.raises(competitions_service.CompetitionError, match="deja une phase"):
        competitions_service.set_phase(troisieme, qualifs, migrated)
    with pytest.raises(competitions_service.CompetitionError, match="porte deja des phases"):
        competitions_service.set_phase(principal, troisieme, migrated)


def test_le_menu_ne_propose_que_ce_que_le_service_accepte(migrated: Settings) -> None:
    """**Les deux ecritures existent, donc un test lit les deux sources.**

    `set_phase` leve avec un message, `phase_options` filtre une liste : elles ne
    peuvent pas etre fondues. C'est la seconde branche de la regle du dossier —
    un menu qui propose ce que le service refuse est pire qu'absent, et un menu
    qui tait ce qu'il accepte fait chercher une panne.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)
    _competition(migrated, "Ligue 1", sport="football")
    competitions_service.set_phase(qualifs, principal, migrated)

    options = competitions_service.phase_options(migrated)
    toutes = [int(row["id"]) for row in db.query("SELECT id FROM competitions", settings=migrated)]

    for competition_id in toutes:
        proposees = {candidat["id"] for candidat in options.get(competition_id, [])}
        for cible in toutes:
            try:
                competitions_service.set_phase(competition_id, cible, migrated)
            except competitions_service.CompetitionError:
                assert cible not in proposees, (
                    f"le menu propose {cible} pour {competition_id}, le service le refuse"
                )
                continue
            assert cible in proposees, (
                f"le service accepte {cible} pour {competition_id}, le menu le tait"
            )
            competitions_service.set_phase(competition_id, "", migrated)
        # L'etat de depart se remet : la boucle a ecrit puis efface.
        if competition_id == qualifs:
            competitions_service.set_phase(qualifs, principal, migrated)


# -- Le service et sa surface se livrent ensemble ---------------------------


def test_le_formulaire_ecrit_le_lien_et_le_defait(client: TestClient, migrated: Settings) -> None:
    """**Poster le formulaire rendu et relire la base**, jamais appeler le service.

    Le motif de saisie tardive a vecu deux jours accepte par le service et
    saisissable nulle part ; la regle qui en sort ne se teste que d'une facon.
    """
    principal = _competition(migrated, PRINCIPAL)
    qualifs = _competition(migrated, QUALIFS)

    page = client.get("/competitions")
    assert page.status_code == 200
    assert f"/competitions/{qualifs}/phase" in page.text, "le formulaire est rendu"

    reponse = client.post(f"/competitions/{qualifs}/phase", data={"phase_de": str(principal)})
    assert reponse.status_code == 200
    row = db.query_one(
        "SELECT phase_de FROM competitions WHERE id = ?", (qualifs,), settings=migrated
    )
    assert int(row["phase_de"]) == principal

    client.post(f"/competitions/{qualifs}/phase", data={"phase_de": ""})
    row = db.query_one(
        "SELECT phase_de FROM competitions WHERE id = ?", (qualifs,), settings=migrated
    )
    assert row["phase_de"] is None, "une saisie vide efface le lien"


def test_un_refus_se_voit_a_l_ecran(client: TestClient, migrated: Settings) -> None:
    """Un refus journalise et une page inchangee rendraient la meme sortie."""
    qualifs = _competition(migrated, QUALIFS)
    football = _competition(migrated, "Ligue 1", sport="football")

    reponse = client.post(f"/competitions/{qualifs}/phase", data={"phase_de": str(football)})
    assert reponse.status_code == 200
    assert "meme sport" in reponse.text
