"""La borne haute de la fenetre pre-resultat, et le cadre qui etiquette.

**`result_at` n'est pas une colonne de provenance.** `created_at` ouvre la
fenetre — la selection a ete posee avant le coup d'envoi, donc son prix est un
prix d'avant-match ; il manquait la borne **haute**, l'instant ou l'issue est
devenue connue. Sans elle, aucun bilan ne peut prouver qu'un fait qu'il invoque
a ete releve avant que le resultat soit su, c'est-a-dire qu'il ne retrospecte
pas. C'est l'anteriorite appliquee a la relecture plutot qu'a la selection.

Mesure du 21/08/2026 : `picks` ne portait **qu'une seule colonne de date**. Sur
300 selections tranchees, 148 sont datees par `reglements.observed_at` et 152
n'ont aucune date.

**`framework_version` n'a jamais rien etiquete.** Le champ etait emis par
`payload.build_payload` ; la route payload n'a jamais servi en production, et
`ACTIVE_PRODUCER` vaut le gabarit, qui ne l'ecrit pas. Zero prompt sur 180 porte
la chaine. L'application l'estampille desormais elle-meme, depuis sa propre
constante — pas d'aller-retour par le modele pour une valeur qu'elle connait.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coupons as coupons_service
from myassistantbet.services import imports_raw
from myassistantbet.services.framework import FRAMEWORK_VERSION
from myassistantbet.services.history import add_pick, analysis, set_result
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str) -> int:
    return save(
        build(
            "football",
            "Match amical",
            nom,
            f"Adv {nom}",
            LOIN,
            "20:45",
            f"{nom} 2.00\nAdv {nom} 2.00",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _pick(settings: Settings, nom: str = "Lyon") -> tuple[int, int]:
    event_id = _match(settings, nom)
    session_id = board_service.toggle_selection(event_id, True, settings)
    pick_id = add_pick(
        session_id,
        "safe",
        "1N2",
        nom,
        event_id=str(event_id),
        price="1.45",
        settings=settings,
    )
    return session_id, pick_id


def _colonne(settings: Settings, pick_id: int, nom: str) -> object:
    with connect(settings) as conn:
        return conn.execute(f"SELECT {nom} AS v FROM picks WHERE id = ?", (pick_id,)).fetchone()[
            "v"
        ]


# -- La borne haute ----------------------------------------------------------


def test_un_resultat_pose_date_l_instant_ou_il_est_su(migrated: Settings) -> None:
    """La borne que `created_at` n'ouvrait que d'un côté."""
    _, pick_id = _pick(migrated)
    assert _colonne(migrated, pick_id, "result_at") is None, "rien à dater sans résultat"

    set_result(pick_id, "win", migrated)

    date = _colonne(migrated, pick_id, "result_at")
    assert date is not None
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(date)), date


def test_une_ligne_remise_en_attente_perd_sa_date(migrated: Settings) -> None:
    """**Un horodatage qui survivrait à l'effacement affirmerait une connaissance
    qui n'existe plus** — le défaut caractéristique du projet, posé sur la
    colonne qui sert justement à dater ce qu'on sait."""
    _, pick_id = _pick(migrated)
    set_result(pick_id, "win", migrated)
    assert _colonne(migrated, pick_id, "result_at") is not None

    set_result(pick_id, "pending", migrated)

    assert _colonne(migrated, pick_id, "result_at") is None


def test_un_resultat_annule_est_date_aussi(migrated: Settings) -> None:
    """`void` est un verdict : on sait ce qui est arrivé, et quand on l'a su."""
    _, pick_id = _pick(migrated)

    set_result(pick_id, "void", migrated)

    assert _colonne(migrated, pick_id, "result_at") is not None


def test_les_jambes_d_un_coupon_sont_datees_par_le_meme_geste(migrated: Settings) -> None:
    """**Ce chemin écrit `result` en masse**, et le laisser sans date ferait de
    chaque jambe de combiné une ligne hors de portée de toute relecture."""
    session_id, premier = _pick(migrated, "Lyon")
    second = add_pick(
        session_id,
        "safe",
        "1N2",
        "Nice",
        event_id=str(_match(migrated, "Nice")),
        price="1.60",
        settings=migrated,
    )
    coupon_id = coupons_service.create(session_id, [premier, second], settings=migrated)

    coupons_service.settle_all(coupon_id, "win", migrated)

    assert _colonne(migrated, premier, "result_at") is not None
    assert _colonne(migrated, second, "result_at") is not None


def test_les_tranchees_sans_date_se_comptent(migrated: Settings) -> None:
    """**Elles ne sont pas suspectes, elles sont hors de portée** d'une relecture
    qui a besoin d'une borne — ce qui n'est pas la même chose et n'appelle pas le
    même geste. Population close : tout résultat posé depuis est daté."""
    _, pick_id = _pick(migrated)
    set_result(pick_id, "win", migrated)
    with connect(migrated) as conn:
        conn.execute("UPDATE picks SET result_at = NULL WHERE id = ?", (pick_id,))

    assert analysis(settings=migrated).settled_undated == 1


def test_une_tranchee_datee_ne_compte_pas_comme_un_manque(migrated: Settings) -> None:
    _, pick_id = _pick(migrated)
    set_result(pick_id, "win", migrated)

    assert analysis(settings=migrated).settled_undated == 0


def test_la_reprise_ne_date_pas_une_ligne_divergente() -> None:
    """**Le sens de l'erreur est tout ce qui compte pour une borne.**

    Sur une ligne appliquée, le règlement a posé le résultat : `observed_at`
    précède l'écriture, donc la borne est trop tôt et un garde qui s'en sert
    refuse un peu trop — il se trompe du bon côté. Sur une ligne divergente, le
    résultat vient d'une saisie humaine antérieure et `observed_at` n'est que la
    date où la règle a relu la source : la borne serait **trop tard**, donc
    permissive, et laisserait passer un fait relevé entre les deux.

    Une borne qui se trompe dans le sens permissif est pire qu'une borne
    absente — celle-là se voit et se compte.
    """
    sql = (
        Path(__file__).parents[1] / "src/myassistantbet/migrations/075_instant_du_resultat.sql"
    ).read_text(encoding="utf-8")
    instructions = "\n".join(
        ligne for ligne in sql.splitlines() if ligne.strip() and not ligne.startswith("--")
    )

    assert instructions.count("r.etat = 'applique'") == 2, (
        "la clause scope la sous-requête ET l'existence : sans les deux, une "
        "ligne divergente sortirait datée à NULL plutôt que non touchée"
    )


# -- Le cadre qui etiquette --------------------------------------------------


def test_chaque_selection_porte_le_cadre_sous_lequel_elle_est_ecrite(
    migrated: Settings,
) -> None:
    """**L'application l'estampille depuis sa propre constante.** Le faire
    déclarer par le modèle puis relire à l'import ajouterait un chemin de perte à
    une valeur locale — et c'est exactement par ce chemin que le champ n'a jamais
    rien étiqueté : émis par une route payload que rien ne sert, persisté nulle
    part, zéro prompt sur 180 le portant."""
    _, pick_id = _pick(migrated)

    assert _colonne(migrated, pick_id, "framework_version") == FRAMEWORK_VERSION


def test_la_migration_ne_retro_remplit_aucun_cadre(migrated: Settings) -> None:
    """**Les sélections d'avant n'ont pas été produites sous un cadre que la base
    connaisse**, et leur en prêter un ferait ce que le champ existe pour empêcher
    — mélanger deux régimes dans une population.

    Le test relit le fichier de migration plutôt que d'en recopier la règle :
    deux écritures de la même décision divergeraient sans un mot.
    """
    sql = (
        Path(__file__).parents[1] / "src/myassistantbet/migrations/075_instant_du_resultat.sql"
    ).read_text(encoding="utf-8")
    instructions = [
        ligne for ligne in sql.splitlines() if ligne.strip() and not ligne.startswith("--")
    ]

    assert any("ADD COLUMN framework_version" in ligne for ligne in instructions)
    assert not any(
        "framework_version" in ligne and ligne.strip().upper().startswith("UPDATE")
        for ligne in instructions
    ), "aucun retro-remplissage : NULL est la vérité"


# -- La sonde dans le corpus -------------------------------------------------


def test_la_sonde_est_une_provenance_declaree_et_non_une_ligne_effacee() -> None:
    """**Effacer une ligne d'un corpus d'audit est le geste que ce projet passe
    son temps à éviter.** Un trou dans les identifiants ne s'explique plus six
    mois après. Ce qui a été produit autrement se déclare — même idiome que
    `prose_source` et `price_source`."""
    assert imports_raw.SONDE in imports_raw.SOURCES

    sql = (
        Path(__file__).parents[1] / "src/myassistantbet/migrations/074_sonde_dans_le_corpus.sql"
    ).read_text(encoding="utf-8")
    instructions = "\n".join(
        ligne for ligne in sql.splitlines() if ligne.strip() and not ligne.startswith("--")
    )

    assert "DELETE" not in instructions.upper(), "une ligne d'audit se marque, elle ne s'efface pas"
    assert "sha256 =" in instructions, (
        "le critère est l'empreinte et jamais l'identifiant : un id désigne une "
        "ligne sur cette base et rien ailleurs"
    )


def test_un_collage_de_sonde_garde_sa_provenance(migrated: Settings) -> None:
    """La sonde se reproduira — l'aperçu enregistre par construction — donc le
    vocabulaire doit la nommer quand elle arrive."""
    session_id = board_service.toggle_selection(_match(migrated, "Lyon"), True, migrated)

    import_id = imports_raw.record(session_id, "texte de sonde", imports_raw.SONDE, migrated)

    assert import_id is not None
    collage = imports_raw.get(import_id, migrated)
    assert collage is not None and collage.source == imports_raw.SONDE
