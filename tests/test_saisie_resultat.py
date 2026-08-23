"""La saisie d'un resultat ne remonte plus la feuille de session.

**Ce que ces tests gardent, et pourquoi ils ne mesurent aucun pixel.** Le defaut
se voyait au navigateur — `window.scrollY` passait de 3000 a 181 px a chaque
resultat saisi, sur une hauteur de document **inchangee** (8183 avant, 8183
apres, mesure du 23/08/2026 sur une session de 59 selections). Sa cause n'etait
donc pas la ligne qui quitte la liste et raccourcit la page, mais le detachement
transitoire de `#worksheet` pendant un `hx-swap="outerHTML"` : le temps que le
bloc sorte du document, le navigateur ramene `scrollY` a ce que la page permet,
et la valeur ne remonte pas quand le contenu revient.

La suite n'a pas de navigateur — `SPEC.md` section 9.4 interdit tout `node_modules`
— donc elle ne verifie pas le nombre de pixels du jour mais **la propriete qui le
produit** : la reponse a une saisie ne porte pas le bloc, donc rien ne se
detache, donc rien ne peut clamper. Un test qui recopierait « 0 px » ne tiendrait
que jusqu'au prochain changement de gabarit.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services.history import (
    add_pick,
    get_pick,
    pending_count,
    set_result,
    worksheet,
)
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, home: str) -> int:
    return save(
        build(
            "football",
            "Match amical",
            home,
            f"Adv {home}",
            LOIN,
            "20:45",
            f"{home} 2.00\nAdv {home} 2.00",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _lot(settings: Settings, combien: int) -> tuple[int, list[int]]:
    """Une session portant `combien` selections en attente."""
    session_id = 0
    picks: list[int] = []
    for index in range(combien):
        event_id = _match(settings, f"Club {index}")
        session_id = board_service.toggle_selection(event_id, True, settings)
        picks.append(
            add_pick(
                session_id,
                "safe",
                "1N2",
                f"Domicile {index}",
                event_id=str(event_id),
                price="2.00",
                settings=settings,
            )
        )
    return session_id, picks


def _lignes(html: str) -> list[str]:
    return re.findall(r"<tr[ >]", html)


# -- La propriete qui tient le correctif --------------------------------------


def test_une_saisie_ne_remonte_pas_le_bloc(client: TestClient, migrated: Settings) -> None:
    """La reponse ne porte pas `#worksheet`, donc aucun detachement possible.

    C'est **l'assertion centrale du chantier**. Le jour ou elle casse, le saut
    de `scrollY` est revenu avec elle : le bloc entier serait a nouveau
    remplace, et le navigateur le clamperait comme avant.
    """
    _, picks = _lot(migrated, 3)

    reponse = client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    assert reponse.status_code == 200
    assert 'id="worksheet"' not in reponse.text, "la feuille entiere serait remontee"
    assert len(_lignes(reponse.text)) == 1, "une saisie rend une ligne, pas un tableau"
    assert f'id="pick-row-{picks[0]}"' in reponse.text


def test_la_ligne_tranchee_reste_en_place_jusqu_au_rechargement(
    client: TestClient, migrated: Settings
) -> None:
    """Elle ne migre vers « Tranchees » qu'au prochain chargement.

    Un tri qui se refait sous la main pendant qu'on saisit quarante lignes coute
    plus qu'il n'apporte : la ligne est **marquee** faite, elle ne bouge pas.
    """
    session_id, picks = _lot(migrated, 3)

    reponse = client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    assert "is-resolved" in reponse.text
    assert "disabled" in reponse.text, "les controles de resultat sont desactives"

    # La base, elle, est bien a jour : c'est l'affichage qui attend, pas
    # l'ecriture. Le rechargement range la ligne dans son bloc.
    assert get_pick(picks[0], migrated).result == "win"
    feuille = worksheet(session_id, migrated)
    assert feuille.pending_count == 2
    assert feuille.settled_count == 1


def test_le_compteur_suit_hors_bande(client: TestClient, migrated: Settings) -> None:
    """Le reste a trancher se met a jour sans que rien ne change de hauteur.

    Sans lui, le compte affiche resterait celui de l'ouverture et annoncerait un
    reste qui n'existe plus — un chiffre faux a l'endroit ou l'on vient de
    gagner en justesse.
    """
    _, picks = _lot(migrated, 3)

    reponse = client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    assert 'id="worksheet-pending-count" hx-swap-oob="true">2<' in reponse.text


def test_annuler_restaure_l_etat_precedent_et_non_l_attente(
    client: TestClient, migrated: Settings
) -> None:
    """« Annuler » rend ce qu'il y avait, jamais « en attente » en dur.

    Les deux se confondent sur une ligne de « A trancher », jamais sur une ligne
    qu'on corrige depuis « Tranchees » : y effacer un resultat qu'on n'avait pas
    pose serait une perte silencieuse.
    """
    _, picks = _lot(migrated, 2)
    set_result(picks[0], "loss", migrated)

    reponse = client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    assert '{"result": "loss"}' in reponse.text, "l'annulation revient a « perdu »"

    # Et elle fonctionne : la ligne redevient ordinaire, controles actifs.
    retour = client.post(f"/picks/{picks[0]}/result", data={"result": "loss"})
    assert get_pick(picks[0], migrated).result == "loss"
    assert "is-resolved" in retour.text, "« perdu » ferme la ligne, elle reste marquee"


def test_annuler_depuis_une_ligne_en_attente_la_rouvre(
    client: TestClient, migrated: Settings
) -> None:
    """Le cas ordinaire : la ligne revient en attente, ses controles reactives."""
    _, picks = _lot(migrated, 2)
    client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    retour = client.post(f"/picks/{picks[0]}/result", data={"result": "pending"})

    assert get_pick(picks[0], migrated).result == "pending"
    assert "is-resolved" not in retour.text
    assert 'hx-target="closest tr"' in retour.text, "les boutons redeviennent actifs"
    assert 'id="worksheet-pending-count" hx-swap-oob="true">2<' in retour.text


# -- Le refus, qui ne se voyait nulle part ------------------------------------


def test_un_resultat_refuse_se_voit_sur_sa_ligne(client: TestClient, migrated: Settings) -> None:
    """L'echec et le cas ordinaire rendaient la **meme** sortie.

    La route journalisait le refus et re-rendait la feuille inchangee : rien a
    l'ecran ne distinguait un resultat enregistre d'un resultat refuse. C'est le
    defaut caracteristique du depot, pose sur la seule action qu'on repete
    quarante fois de suite.
    """
    _, picks = _lot(migrated, 2)

    reponse = client.post(f"/picks/{picks[0]}/result", data={"result": "nimportequoi"})

    assert reponse.status_code == 200
    assert "Résultat inconnu" in reponse.text
    assert "is-resolved" not in reponse.text, "rien n'a ete ecrit, rien n'est marque fait"
    assert 'hx-target="closest tr"' in reponse.text, "les controles restent actifs"
    assert get_pick(picks[0], migrated).result == "pending", "la base n'a pas bouge"
    assert 'id="worksheet-pending-count" hx-swap-oob="true">2<' in reponse.text


# -- Le rafraichissement explicite --------------------------------------------


def test_rafraichir_rend_le_fragment_et_range_les_lignes(
    client: TestClient, migrated: Settings
) -> None:
    """Le geste qui reorganise la feuille, et le seul.

    Il remonte le bloc entier, donc il fait remonter la page — c'est voulu ici,
    et c'est exactement pourquoi la saisie d'un resultat ne passe plus par ce
    chemin.
    """
    session_id, picks = _lot(migrated, 3)
    client.post(f"/picks/{picks[0]}/result", data={"result": "win"})

    reponse = client.get(f"/history/{session_id}/worksheet")

    assert reponse.status_code == 200
    assert 'id="worksheet"' in reponse.text
    assert "<html" not in reponse.text, "un fragment HTMX, jamais la page entiere"
    assert "Tranchées" in reponse.text


# -- Le compte des lignes en attente ------------------------------------------


def test_le_compte_en_attente_suit_la_feuille(migrated: Settings) -> None:
    """Une propriete, jamais la valeur du jour.

    `pending_count` est un `COUNT` et `worksheet()` charge la session entiere :
    deux ecritures de la meme notion, et rien n'obligeait la seconde a suivre la
    premiere. C'est ce test qui les tient ensemble.
    """
    session_id, picks = _lot(migrated, 4)

    assert pending_count(session_id, migrated) == worksheet(session_id, migrated).pending_count

    for resultat in ("win", "loss", "void"):
        set_result(picks.pop(), resultat, migrated)
        assert pending_count(session_id, migrated) == worksheet(session_id, migrated).pending_count

    assert pending_count(session_id, migrated) == 1, "un annule ne s'attend plus"
