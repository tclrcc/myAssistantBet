"""Le budget de recherche a **une** source, et le gabarit n'en recopie aucune.

**Ce test vaut plus que le changement qu'il accompagne**, et c'est le brief qui
le dit : il protege tous les suivants. Le nombre de dossiers parait a trois
endroits du gabarit — la section BUDGET DE RECHERCHE, la phrase sur les bornes
des paliers hauts, et le plafond de jambes d'un combine — et une divergence
rendrait le prompt contradictoire pour son lecteur, **sans rien casser**.

C'est la forme exacte du defaut caracteristique du projet : trois nombres qui ne
disent pas la meme chose se lisent tous les trois comme des faits.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from myassistantbet.config import Settings
from myassistantbet.services.thresholds import value_of

GABARIT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "myassistantbet"
    / "templates"
    / "prompts"
    / "session_default.md.j2"
)

#: Les trois passages ou le budget parait. Ils sont reperes par une **phrase**
#: et non par un numero de ligne : une insertion ailleurs dans le gabarit
#: deplacerait les lignes et ferait echouer ce test pour rien.
EMPLACEMENTS = (
    ("section BUDGET DE RECHERCHE", "dossiers** en recherche approfondie"),
    ("bornes des paliers hauts", "et **ce prompt** en ouvre"),
    ("plafond de jambes du combiné", "prompt en ouvre"),
)

#: Un nombre ecrit en clair. Les valeurs de gabarit passent par `{{ ... }}`, donc
#: tout chiffre nu sur ces lignes-la est un littéral recopié.
_CHIFFRE = re.compile(r"(?<![{\w])\d+(?![}\w])")


def _lignes() -> list[str]:
    return GABARIT.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(("nom", "repere"), EMPLACEMENTS)
def test_aucun_litteral_numerique_au_budget(nom: str, repere: str) -> None:
    """**Un littéral ici ne casse rien, et c'est ce qui le rend cher.**

    Le prompt annoncerait « 7 dossiers » a un endroit et « 10 » a un autre, les
    deux se liraient comme des faits, et le lecteur suivrait celui qu'il aura lu
    en dernier.

    Le controle porte sur la ligne du repere **et la suivante** : la valeur
    tombe souvent au debut de la ligne d'apres, la ou le texte se replie a
    cent caracteres.
    """
    lignes = _lignes()
    index = next((i for i, ligne in enumerate(lignes) if repere in ligne), None)
    assert index is not None, f"le repère de {nom} a disparu du gabarit"

    fenetre = " ".join(lignes[index : index + 2])
    # Ce qui est deja dans une expression Jinja n'est pas un littéral.
    hors_jinja = re.sub(r"\{\{.*?\}\}", " ", fenetre)
    trouves = _CHIFFRE.findall(hors_jinja)

    assert not trouves, (
        f"{nom} porte un nombre écrit en clair ({trouves}) : "
        "le budget a une seule source, et ce passage doit la lire"
    )


def test_les_trois_emplacements_lisent_la_meme_valeur(migrated: Settings) -> None:
    """**Un seul nombre dans le rendu**, quelle que soit la façon de l'écrire.

    Les trois passages passent par deux variables — `research.available` et
    `research_budget` — et rien ne garantirait sans ce test qu'elles portent la
    même chose.
    """
    from myassistantbet.services.prompt import build_prompt

    from .test_research import _lot

    lot = 21
    budget = value_of("recherche_dossiers", migrated)
    corps = " ".join(
        build_prompt(
            _lot(migrated, lot), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
        ).body.split()
    )

    attendu = min(budget, lot)
    assert f"**{attendu} dossiers** en recherche approfondie" in corps
    assert f"et **ce prompt** en ouvre {attendu}." in corps
    assert f"prompt en ouvre {attendu}." in corps


def test_sur_un_lot_plus_court_le_budget_annonce_est_le_lot(migrated: Settings) -> None:
    """**`min(budget, lot)`, et non le réglage.**

    Annoncer dix dossiers sur un lot de six inviterait à en chercher quatre qui
    n'existent pas — la pression exacte que le reste du gabarit supprime.
    """
    from myassistantbet.services.prompt import build_prompt

    from .test_research import _lot

    corps = " ".join(
        build_prompt(
            _lot(migrated, 6), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
        ).body.split()
    )

    assert "**6 dossiers** en recherche approfondie" in corps
