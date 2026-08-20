"""Les consignes permanentes : ce qu'elles font, et ce qu'elles ne doivent pas faire.

Le champ est recopie en tete de chaque prompt et **prime sur les preferences
generales du gabarit**. C'est la seule surface de l'application qui puisse
faire entrer dans un prompt une regle que le gabarit retient volontairement —
les taux par palier et par confiance — sans qu'une ligne du gabarit soit
touchee. La contamination passe par le lecteur, pas par le texte.

Rien ici ne refuse une consigne : l'application ne peut pas lire l'intention
derriere une phrase, et un controle automatique refuserait des consignes
legitimes. Ce qui se teste est donc l'**avertissement** et le **chemin** —
les consignes arrivent-elles vraiment dans le prompt, et le gabarit dit-il quoi
faire quand l'une d'elles rend une section impossible.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import prompt as prompt_service
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt

from .helpers import NOW


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


CONSIGNES = "Betclic est le seul bookmaker où je pose.\nJe ne joue jamais : cartons, corners"


def _lot(settings: Settings) -> int:
    event_id = save(
        build(
            "football",
            "Match amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10\nNul 3.40\nNice 3.20",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings)


# -- Le chemin : les consignes arrivent-elles dans le prompt ? ---------------


def test_les_consignes_arrivent_telles_quelles_dans_le_prompt(migrated: Settings) -> None:
    """**Le chemin n'avait aucun test.** `save_preference` en avait, le rendu du
    gabarit en avait, et rien ne verifiait que le texte enregistre atteignait le
    corps du prompt — l'endroit ou une rupture serait invisible, le bloc entier
    disparaissant en silence.

    Recopie **telle quelle** : ce texte n'est ni compile ni interprete, et une
    apostrophe echappee ou une ligne rejointe changerait ce que le modele lit.
    """
    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, CONSIGNES, migrated)

    corps = build_prompt(_lot(migrated), settings=migrated, now=NOW).body

    assert "## CONSIGNES PERMANENTES" in corps
    assert CONSIGNES in corps


def test_le_bloc_disparait_entierement_sans_consigne(migrated: Settings) -> None:
    """Une ligne sans donnee est omise, jamais rendue vide — meme regle que
    partout. Un titre suivi de rien ferait chercher une consigne perdue."""
    corps = build_prompt(_lot(migrated), settings=migrated, now=NOW).body

    assert "CONSIGNES PERMANENTES" not in corps


def test_une_consigne_qui_rend_une_section_impossible_se_dit(migrated: Settings) -> None:
    """**§1c.** Le cas est certain de se produire : la consigne servie porte une
    ligne de marches jamais joues, et rien ne garantit qu'un lot en offre
    d'autres. Le gabarit doit alors faire **ecrire** l'impossibilite plutot que
    la contourner en remplissant avec autre chose — c'est la meme regle que
    « un palier vide est un resultat ».

    La phrase se compare **a plat** : le retour a la ligne n'est pas la regle,
    c'est une largeur de colonne.
    """
    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, CONSIGNES, migrated)

    corps = " ".join(build_prompt(_lot(migrated), settings=migrated, now=NOW).body.split())

    assert "Si l'une d'elles rend une section impossible à remplir, dis-le en une ligne" in corps
    assert "plutôt que de la contourner" in corps


def test_les_consignes_ne_priment_pas_sur_les_interdits(migrated: Settings) -> None:
    """Elles priment sur les habitudes et sur les preferences du gabarit, jamais
    sur les interdits ni sur les cotes des blocs. Sans cette phrase, le champ
    serait une porte de sortie de la section 9."""
    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, CONSIGNES, migrated)

    corps = " ".join(build_prompt(_lot(migrated), settings=migrated, now=NOW).body.split())

    assert "Elles ne priment **jamais** sur les interdits ci-dessus" in corps
    assert "ni sur les cotes des blocs" in corps


# -- L'avertissement, sur la surface qui saisit ------------------------------


def test_l_ecran_avertit_contre_une_regle_tiree_des_statistiques(
    client: TestClient, migrated: Settings
) -> None:
    """**§1a.** Le texte d'aide decrivait ce que le champ peut contenir, jamais
    ce qu'il ne doit pas contenir. Or c'est la seule surface qui puisse defaire
    la retenue des taux, et elle le ferait sans qu'aucun garde-fou morde.

    Trois moities, et il faut les trois : l'interdit, sa raison — une categorie
    qu'on cesse de produire cesse d'etre mesurable — et le **test** a appliquer
    a une consigne, qui est le seul des trois qu'on puisse suivre en ecrivant
    une phrase.
    """
    page = " ".join(client.get("/settings").text.split())

    assert "Aucune règle tirée de la page Statistiques" in page
    assert "cesse d'être mesurable" in page
    assert "Aucune de ces consignes ne dépend d'un résultat" in page


def test_l_avertissement_dit_qu_aucun_controle_ne_le_garde(
    client: TestClient, migrated: Settings
) -> None:
    """**Un garde-fou qu'on croit automatique et qui ne l'est pas est pire que
    pas de garde-fou.** L'application ne peut pas lire l'intention derriere une
    phrase, et le taire laisserait croire qu'une consigne refusee aurait ete
    signalee."""
    page = " ".join(client.get("/settings").text.split())

    assert "Rien ne le vérifie" in page


def test_aucune_consigne_n_est_refusee_sur_son_contenu(migrated: Settings) -> None:
    """Le controle porte sur la **longueur** et sur rien d'autre. Une regle qui
    refuserait un mot produirait des faux positifs sur des consignes legitimes,
    et l'utilisateur n'aurait aucun moyen de la lever."""
    interdite = "Évite le marché Vainqueur, il est à 46 % sur la page Statistiques."

    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, interdite, migrated)

    assert prompt_service.read_preference(prompt_service.PREFERENCE_NOTES, migrated) == interdite
