"""Le rendu **entier**, de bout en bout, sur un collage réel.

**C'est par là que le défaut du lot 8 est passé.** Le banc de transport
(`test_transport.py`) applique onze altérations à chaque format structuré, mais
il les teste **isolés** : un tableau seul, un bloc `conf` seul, une ligne
`sets:` seule. Or le défaut ne se voyait que dans le rendu complet — une phrase
de la section B mentionnant « C-bis » faisait basculer la lecture avant le
tableau de la section C, et toutes ses lignes partaient en rejet.

Chaque format passait donc son banc, et le rendu entier perdait la moitié de sa
substance. Le découpage en sections y est entré après coup, en sixième format ;
ce fichier-ci fait l'autre moitié du chemin — **un vrai rendu, par le vrai
chemin d'import, et un compte exact pour chaque objet**.

**Un compte, pas une présence.** C'est le compte qui aurait crié : le collage
portait cinq blocs de confiance et deux sélections sont entrées. Une assertion
« il y a des sélections » serait passée pendant toute la panne.

**Cette garde se met à jour à chaque changement de sortie attendue du gabarit**,
et c'est son rôle : elle doit casser quand le rendu change de forme, et le
diagnostic est alors de vérifier que le nouveau compte est celui qu'on voulait.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import board as board_service
from myassistantbet.services import picks_import
from myassistantbet.services.manual import build, save

#: Le collage réel du 19/08/2026, 21 559 caractères, tel qu'il a été reçu —
#: clôtures de blocs mangées par le rendu, tabulations à la place des barres,
#: sections A à F. **Il n'est ni nettoyé ni raccourci** : c'est exactement ce
#: que le transport en fait qui est testé ici.
COLLAGE = (Path(__file__).parent / "fixtures" / "collage_complet.md").read_text(encoding="utf-8")

#: Ce que ce collage doit produire. **Chaque nombre a été vérifié à la main sur
#: le texte** — ce n'est pas la sortie du jour recopiée.
ATTENDU = {
    "section_c": 5,
    "c_bis": 2,
    "blocs_conf": 5,
    "combines": 1,
    "sets": 10,
    "dossiers": 9,
}

#: Les en-têtes du prompt d'origine : c'est contre eux que la somme de contrôle
#: de l'appariement se fait. Recopiés du prompt 159 de la base servie.
ENTETES = "\n".join(
    f"### M{index} · TENNIS · {tournoi} Cincinnati Open · {affiche} · {heure}"
    for index, (tournoi, affiche, heure) in enumerate(
        [
            ("WTA", "Diana Shnaider – Elena Rybakina", "19/08 20:00"),
            ("WTA", "Linda Noskova – Amanda Anisimova", "19/08 20:00"),
            ("WTA", "Coco Gauff – Marie Bouzkova", "19/08 21:00"),
            ("ATP", "Nuno Borges – Brandon Nakashima", "19/08 21:10"),
            ("ATP", "Jaime Faria – Lorenzo Musetti", "19/08 22:20"),
            ("ATP", "Taylor Fritz – Christopher O'Connell", "20/08 01:00"),
            ("WTA", "Madison Keys – Xiyu Wang", "20/08 01:00"),
            ("ATP", "Frances Tiafoe – Felix Auger-Aliassime", "20/08 02:10"),
            ("WTA", "Aryna Sabalenka – Sara Bejlek", "20/08 02:30"),
        ],
        start=1,
    )
)

AFFICHES = [
    ("Diana Shnaider", "Elena Rybakina"),
    ("Linda Noskova", "Amanda Anisimova"),
    ("Coco Gauff", "Marie Bouzkova"),
    ("Nuno Borges", "Brandon Nakashima"),
    ("Jaime Faria", "Lorenzo Musetti"),
    ("Taylor Fritz", "Christopher O'Connell"),
    ("Madison Keys", "Xiyu Wang"),
    ("Frances Tiafoe", "Felix Auger-Aliassime"),
    ("Aryna Sabalenka", "Sara Bejlek"),
]


@pytest.fixture
def lot(migrated: Settings) -> int:
    """Le lot du collage : neuf matchs de tennis, et le prompt qui les nomme."""
    session_id = 0
    for home, away in AFFICHES:
        event_id = save(
            build(
                "tennis",
                "ATP Cincinnati Open",
                home,
                away,
                "2099-01-01",
                "20:00",
                f"{home} 1.50",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, ENTETES, db.utcnow()),
        settings=migrated,
    )
    return session_id


def test_un_rendu_complet_rend_chaque_objet_dans_le_compte_attendu(
    lot: int, migrated: Settings
) -> None:
    """**La garde de forme du gabarit entier.**

    Pendant la panne du lot 8, ce test aurait rendu 0 en section C et 5 blocs
    non appariés — sur les mêmes 21 559 caractères. C'est le seul endroit du
    dépôt où le rendu est vu comme le modèle le produit.
    """
    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    obtenu = {
        "section_c": sum(1 for pick in preview.picks if not pick.exploratory),
        "c_bis": sum(1 for pick in preview.picks if pick.exploratory),
        "blocs_conf": preview.claims_attached,
        "combines": len(preview.combos),
        "sets": len(preview.scores),
        "dossiers": len(preview.opened.marks or ()),
    }

    assert obtenu == ATTENDU, (
        "le rendu complet ne produit plus les mêmes comptes — vérifie que le "
        "nouveau compte est celui qu'on voulait avant de mettre ce test à jour"
    )


def test_le_collage_complet_ne_perd_aucune_ligne_de_section_c(lot: int, migrated: Settings) -> None:
    """**Le défaut du lot 8, rejoué sur son propre texte.**

    La section B de ce collage mentionne « C-bis » trois fois. Aucune de ces
    mentions ne doit basculer la lecture : les cinq lignes de la section C sont
    des sélections principales, et aucune ne part en rejet « exploratoire en
    palier sûr ».
    """
    assert COLLAGE.lower().count("c-bis") >= 3, "le texte porte bien le piège"

    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    assert not [note for note in preview.notes if "palier sûr" in note]
    assert [pick.exploratory for pick in preview.picks] == [False] * 5 + [True] * 2


def test_la_ligne_dossiers_ouverts_est_lue_avec_ses_neuf_reperes(
    lot: int, migrated: Settings
) -> None:
    """Neuf repères pour un budget de dix : c'est la première fois que ce nombre
    se mesure, et il dit que le budget n'était pas la contrainte."""
    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    assert preview.opened.state == "renseignee"
    assert preview.opened.declared
    assert sorted(preview.opened.marks, key=lambda mark: int(mark[1:])) == [
        f"M{index}" for index in range(1, 10)
    ]
