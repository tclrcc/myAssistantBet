"""L'accuse d'appariement : dire pourquoi un lot perd tous ses crans.

**Le defaut vise par la directive d'origine est eteint.** Le collage partiel —
le seul tableau de la section C, sans les blocs ni la ligne `dossiers_ouverts` —
s'arrete net le 20/08/2026 avec le durcissement du refus (`09e4694`) : 150 crans
forces jusque-la, **zero depuis**.

Ce qui reste est un autre defaut, et un marqueur de format n'y peut rien. Les
dix-huit crans forces des 24 et 25/08 viennent de collages **complets** — 9 blocs
et la ligne `dossiers_ouverts` dans les deux cas — dont les reperes ne se sont
apparies a aucun prompt. Deux causes :

· **aucun prompt n'existe dans la session** (9 selections, session 23). Rien ne
  peut valider les reperes : le referent manque, pas l'annonce ;
· **l'appariement echoue malgre cinq prompts** (9 selections, session 22), dont
  deux portent 9 blocs comme le collage. Le compte concorde ; c'est la somme de
  controle sur l'affiche qui ne tombe pas.

Dans les deux cas le lot entier perd ses crans, et rien ne le retenait — le
signal existait en note, et **un signal qui n'arrete rien ne se distingue pas
d'un signal absent** : celui de la section manquante a parle 20 fois sur 20 et
les 20 imports ont ete valides quand meme.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import picks_import
from myassistantbet.services.manual import build, save
from tests.helpers import repost_import_form

LOIN = "2099-01-01"

#: **Sans ecart au cadre**, et c'est indispensable : la fixture partagee viole le
#: controle 7 sur sa seconde ligne, et sa confirmation se declencherait avant
#: celle-ci. Un test qui passerait par elle verifierait l'autre garde.
TABLEAU = (Path(__file__).parent / "fixtures" / "tableau_section_c_sans_ecart.md").read_text(
    encoding="utf-8"
)


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _lot(settings: Settings, noms: list[str]) -> int:
    session_id = 0
    for nom in noms:
        event_id = save(
            build(
                "football",
                "Match amical",
                nom,
                f"Adv {nom}",
                LOIN,
                "20:45",
                f"{nom} 1.45",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id


def _bloc(repere: str) -> str:
    return json.dumps(
        {
            "match": repere,
            "type": "issue",
            "source_level": 1,
            "faits": [
                {
                    "enonce": "compo officielle publiee",
                    "date": "2026-08-20",
                    "editeur": "ol.fr",
                    "niveau": 1,
                }
            ],
            "manque_touche_facteur": False,
        },
        ensure_ascii=False,
    )


#: Un collage **complet** : le tableau, ses deux blocs, la ligne des dossiers.
#: C'est le cas des sessions 22 et 23 — rien n'y manque, et le lot perd quand
#: meme tous ses crans.
COMPLET = (
    TABLEAU
    + "\n```conf\n"
    + _bloc("M1")
    + "\n```\n\n```conf\n"
    + _bloc("M2")
    + "\n```\n\ndossiers_ouverts: [M1, M2]\n"
)


def _compte(settings: Settings) -> int:
    with connect(settings) as conn:
        return int(conn.execute("SELECT COUNT(*) n FROM picks").fetchone()["n"])


def _import_id(settings: Settings) -> str:
    with connect(settings) as conn:
        row = conn.execute("SELECT MAX(id) AS id FROM imports_raw").fetchone()
    return str(row["id"])


# -- Le diagnostic -----------------------------------------------------------


def test_une_session_sans_prompt_nomme_sa_cause(client: TestClient, migrated: Settings) -> None:
    """**Le referent manque, pas l'annonce.** Aucun marqueur de format ne repare
    ce cas : le collage annonce correctement ses deux blocs et sa ligne de
    dossiers, et il n'y a simplement aucun prompt contre quoi les valider.

    Le motif doit donc dire le geste — generer le prompt, puis recoller — et non
    « recolle la reponse entiere », qui est le conseil de l'autre defaut et qui
    echouerait a l'identique ici.
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    client.post(f"/history/{session_id}/picks/preview", data={"table": COMPLET})

    garde = picks_import.claims_guard(session_id, _import_id(migrated), migrated)

    assert garde is not None
    assert garde.blocking
    assert garde.prompts == 0
    assert garde.blocks == 2
    assert garde.matched == 0
    assert "aucun prompt" in garde.note.lower()


def test_sans_collage_relisable_on_ne_bloque_pas(migrated: Settings) -> None:
    """**On ne bloque pas sur ce qu'on n'a pas vu.**

    La saisie a la main et le rejeu n'ont pas d'identifiant d'import : les
    refuser fermerait deux chemins pour garder le troisieme. Meme regle que la
    garde des sections, et elle est ecrite la-bas.
    """
    session_id = _lot(migrated, ["Lyon"])

    assert picks_import.claims_guard(session_id, "", migrated) is None
    assert picks_import.claims_guard(session_id, "417", migrated) is None


def test_un_collage_sans_bloc_ne_releve_pas_de_cette_garde(
    client: TestClient, migrated: Settings
) -> None:
    """**Deux defauts, deux gardes.** Un collage qui ne porte aucun bloc est le
    defaut eteint le 20/08 ; il a son avertissement et sa confirmation. Le
    compter ici ferait bloquer deux fois pour une seule chose, et le second
    message enverrait corriger ce qui n'est pas en cause.
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})

    garde = picks_import.claims_guard(session_id, _import_id(migrated), migrated)

    assert garde is not None
    assert garde.blocks == 0
    assert not garde.blocking


# -- La garde a l'import -----------------------------------------------------


def test_l_import_est_retenu_tant_que_l_appariement_n_est_pas_confirme(
    client: TestClient, migrated: Settings
) -> None:
    """**Le service et sa surface se livrent ensemble.**

    Un test qui appellerait `claims_guard` verrait le diagnostic et pas la garde.
    C'est en postant le formulaire rendu et en relisant la base qu'on voit si
    l'import a ete retenu — la lecon des cinq collages complets refuses par une
    porte qu'aucun banc ne regardait.
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": COMPLET})

    reponse = client.post(
        f"/history/{session_id}/picks/import", data=repost_import_form(apercu.text)
    )

    assert reponse.status_code == 200
    assert _compte(migrated) == 0, "rien n'est ecrit tant que la perte n'a pas ete vue"
    assert "cran" in reponse.text.lower()


def test_la_confirmation_laisse_passer_sans_rien_corriger(
    client: TestClient, migrated: Settings
) -> None:
    """**Aucun blocage au-dela.** Les selections restent legitimes : ce qu'elles
    perdent est leur cran calcule, pas leur validite. Les refuser ferait
    disparaitre le lot entier pour un defaut de collecte — exactement ce que
    `is_collection_fault` existe pour tenir a part.
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": COMPLET})
    envoi = repost_import_form(apercu.text)
    envoi["confirm_claims"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=envoi)

    assert _compte(migrated) == 2, "les deux lignes entrent, sans leur cran"


def test_les_autres_cases_ne_valent_pas_pour_l_appariement(
    client: TestClient, migrated: Settings
) -> None:
    """**Trois confirmations et non deux.** Cocher pour une section manquante ou
    pour un ecart au cadre ferait passer au meme geste une perte de crans qu'on
    n'aurait pas lue, et chacune cesserait d'etre « ce qu'on ne franchit pas sans
    le voir ».
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": COMPLET})
    envoi = repost_import_form(apercu.text)
    envoi["confirm_partial"] = "1"
    envoi["confirm_controls"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=envoi)

    assert _compte(migrated) == 0


def test_la_garde_relit_le_collage_et_jamais_le_formulaire(
    client: TestClient, migrated: Settings
) -> None:
    """**Ce qui garde l'import ne peut pas voyager par le formulaire qu'il
    garde.** `imports_raw` fait foi : un champ cache qui porterait la condition
    serait fourni par la page que la condition retient.
    """
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": COMPLET})
    envoi = repost_import_form(apercu.text)
    envoi["import_id"] = ""

    client.post(f"/history/{session_id}/picks/import", data=envoi)

    assert _compte(migrated) == 2, "sans collage relisable on ne bloque pas, on n'invente pas"
