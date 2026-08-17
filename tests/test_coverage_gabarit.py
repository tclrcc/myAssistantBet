"""Quels cas decrits par le gabarit ne se declenchent jamais.

**Une regle qui ne se declenche jamais est un cout fixe pur**, paye sur toutes
les sessions. Ces tests gardent le releve — et surtout le **decoupage**, qui
s'est trompe deux fois avant de tenir.
"""

from __future__ import annotations

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import coverage_gabarit as cg

CORPS = """# SESSION

## COMMENT LIRE — le mode d'emploi cite TERRAIN NEUTRE et aucun signale.

## MATCHS

### M1 · FOOT · Ligue 1 · A – B · 20h
  Absents     A — aucun signale
  Lieu        Stade, Ville

### M2 · FOOT · Ligue 1 · C – D · 21h
  Absents     C — non interroges
  Lieu        Autre, Ailleurs TERRAIN NEUTRE

## SORTIE ATTENDUE

Le chapitre de sortie cite lui aussi TERRAIN NEUTRE et aucun signale.

## COMMENT LIRE LES BLOCS

Definition des trois etats : aucun signale, non interroges, source injoignable.
"""


def test_le_preambule_ne_compte_pas() -> None:
    """Un prompt cite chaque cas dans son mode d'emploi : l'y compter rendrait
    tous les cas presents partout."""
    blocs = cg.blocks_of(CORPS)

    assert len(blocs) == 2
    assert "mode d'emploi" not in "".join(blocs)


def test_les_sections_de_sortie_ne_comptent_pas_non_plus() -> None:
    """**C'est le defaut que ce decoupage a trouve chez lui-meme.**

    Sans borne haute, tout ce qui suit la section MATCHS tombait dans le
    **dernier** bloc — et le chapitre « COMMENT LIRE LES BLOCS » y definit
    chaque cas. Trois d'entre eux sortaient a exactement un par prompt, la
    signature d'un marqueur capte hors bloc.
    """
    blocs = cg.blocks_of(CORPS)

    assert "SORTIE ATTENDUE" not in "".join(blocs)
    assert "Definition des trois etats" not in "".join(blocs)
    # Le second bloc porte bien son propre TERRAIN NEUTRE, et **un seul**.
    assert sum(1 for bloc in blocs if "TERRAIN NEUTRE" in bloc) == 1


def test_le_releve_compte_les_blocs_et_non_les_occurrences(migrated: Settings) -> None:
    _archive(migrated, CORPS)

    par_cle = {hit.case.key: hit for hit in cg.survey(migrated)}

    assert par_cle["absents_vus"].blocks == 1
    assert par_cle["absents_non_interroges"].blocks == 1
    assert par_cle["lieu_neutre"].blocks == 1
    assert par_cle["absents_vus"].total_blocks == 2


def test_un_cas_jamais_rencontre_se_nomme(migrated: Settings) -> None:
    """**Et il ne se supprime pas.** C'est un constat pour arbitrage : un cas
    rare peut etre decisif, et son poids le dit a cote du compte."""
    _archive(migrated, CORPS)

    jamais = {hit.case.key for hit in cg.never_seen(migrated)}

    assert "absents_injoignable" in jamais
    assert "alerte_handicap" in jamais
    poids = {hit.case.key: hit.case.weight for hit in cg.survey(migrated)}
    assert poids["alerte_handicap"] == "décisif", "un cas rare peut etre decisif"


def test_les_marqueurs_sont_ceux_du_rendu_et_non_du_mode_d_emploi() -> None:
    """**Le premier jet s'y est trompe, et le compte sortait a zero.**

    Le bloc ecrit « aucun signale » sans accent — regle du module, « ni
    apostrophe ni accent dans une valeur rendue » — quand le chapitre ecrit
    « aucun absent signalé ». Le marqueur accentue ne trouvait que le mode
    d'emploi, donc rien une fois le preambule exclu : zero sur un cas qui arrive
    153 fois sur la base servie.
    """
    marqueurs = {case.key: case.marker for case in cg.CASES}

    assert marqueurs["absents_vus"] == "aucun signale"
    assert "signalé" not in marqueurs["absents_vus"]


def test_le_decoupage_est_le_meme_que_celui_du_cout(migrated: Settings) -> None:
    """Deux decoupages du meme corps doivent compter les memes blocs.

    `prompt.split_cost` et ce releve reperent la meme frontiere ; ecrites deux
    fois, elles auraient diverge au premier changement d'en-tete — et l'une des
    deux serait devenue fausse **en silence**.
    """
    from myassistantbet.services.prompt import split_cost

    assert split_cost(CORPS).blocks == len(cg.blocks_of(CORPS))


def _archive(settings: Settings, body: str) -> None:
    db.execute(
        "INSERT INTO sessions (created_at) VALUES ('2026-08-17T12:00:00Z')", settings=settings
    )
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (1, 'x', ?, 100, '2026-08-17T12:00:00Z')",
        (body,),
        settings=settings,
    )
