"""§2 — la série des dossiers de recherche déclarés.

**Trois points ne concluent rien, et ce module ne conclut rien.** Il n'y a ni
pente, ni moyenne, ni projection : ce qui se rend est la liste, avec de quoi la
lire — le lot, le budget effectif, ce qui a été déclaré.

Ce que la mesure du 19/08/2026 a renversé : « 6, 7 et 9 repères pour un budget
de 10 » se lit comme un modèle qui s'approche de son budget sans l'épuiser. Les
lots correspondants comptaient **exactement 6, 7 et 9 blocs**, donc le budget
effectif valait 6, 7 et 9 et le lot entier a été déclaré les trois fois. Ce
n'est pas le réglage qui bornait, c'est le lot.
"""

from __future__ import annotations

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import prompt as prompt_service
from myassistantbet.services.history import dossiers


def _session(settings: Settings) -> int:
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)["id"])


def _prompt(settings: Settings, session_id: int, blocs: int, budget: int | None) -> int:
    """Un prompt archivé, avec son lot et le budget que son corps annonce."""
    corps = "### M1 · football · Amical · Lyon – Nice · 01/01 20:45\n"
    if budget is not None:
        corps += f"un dossier ouvert, et **ce prompt** en ouvre\n{budget}.\n"
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (session_id, corps, db.utcnow()),
        settings=settings,
    )
    prompt_id = int(db.query_one("SELECT MAX(id) AS id FROM prompts", settings=settings)["id"])
    for index in range(blocs):
        db.execute(
            "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
            "VALUES ((SELECT id FROM sports WHERE key='football'), ?, 'Adv', ?, 'manual', ?)",
            (f"Equipe {prompt_id}-{index}", "2099-01-01T20:00:00Z", db.utcnow()),
            settings=settings,
        )
        event_id = int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])
        db.execute(
            "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            (prompt_id, event_id),
            settings=settings,
        )
    return prompt_id


def _collage(settings: Settings, session_id: int, reperes: int, rang: int = 0) -> None:
    """Un collage conservé, portant sa ligne `dossiers_ouverts`.

    `rang` distingue deux collages qui déclarent le même nombre de repères :
    `imports_raw` déduplique sur l'empreinte du texte, donc deux textes
    identiques ne feraient qu'une ligne — ce qui est le bon comportement du
    service et pas ce qu'on veut simuler ici.
    """
    marques = ", ".join(f"M{index + 1}" for index in range(reperes))
    db.execute(
        "INSERT INTO imports_raw (session_id, raw_text, sha256, char_count, source, created_at) "
        "VALUES (?, ?, ?, ?, 'form', ?)",
        (
            session_id,
            f"dossiers_ouverts: [{marques}]\n",
            f"empreinte-{session_id}-{reperes}-{rang}",
            32,
            db.utcnow(),
        ),
        settings=settings,
    )


def test_le_budget_se_relit_dans_le_corps_du_prompt(migrated: Settings) -> None:
    """**Le corps est la preuve**, et c'est ce qui rend le rétro-remplissage sûr.

    Le gabarit écrit le nombre en toutes lettres. Rien n'est reconstitué : un
    prompt du 04/08 se relit exactement comme celui d'aujourd'hui — même
    argument que le découpage du coût, et que `feedback_active` avant lui.
    """
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=6, budget=6)
    _prompt(migrated, session_id, blocs=3, budget=None)

    ecrites = prompt_service.backfill_research_budget(migrated)

    assert ecrites == 1, "un corps qui n'annonce rien ne s'invente pas"
    lignes = db.query("SELECT research_budget FROM prompts ORDER BY id", settings=migrated)
    assert [ligne["research_budget"] for ligne in lignes] == [6, None]


def test_le_retro_remplissage_est_idempotent(migrated: Settings) -> None:
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=6, budget=6)

    assert prompt_service.backfill_research_budget(migrated) == 1
    assert prompt_service.backfill_research_budget(migrated) == 0


def test_un_lot_declare_en_entier_se_dit_sature(migrated: Settings) -> None:
    """**Le fait mesuré, et il renverse la lecture du brief.** Un lot de six
    blocs sous un réglage à dix donne un budget effectif de six : déclarer six
    repères, ce n'est pas s'approcher de son budget, c'est ne plus rien avoir à
    ouvrir."""
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=6, budget=6)
    _collage(migrated, session_id, reperes=6)
    prompt_service.backfill_research_budget(migrated)

    serie = dossiers(settings=migrated)

    assert len(serie.declared_points) == 1
    point = serie.declared_points[0]
    assert (point.lot, point.budget, point.declared) == (6, 6, 6)
    assert point.saturated is True
    assert "trois points ne font pas une tendance" in serie.line


def test_un_lot_plus_grand_que_le_budget_ne_se_dit_pas_sature(migrated: Settings) -> None:
    """Le réglage a bien mordu, mais avant : sur 28 prompts quand il valait 7,
    dont un lot de 26 blocs ramené à 7. Le distinguer est tout l'objet de la
    colonne."""
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=9, budget=7)
    _collage(migrated, session_id, reperes=9)
    prompt_service.backfill_research_budget(migrated)

    point = dossiers(settings=migrated).points[0]

    assert (point.lot, point.budget) == (9, 7)
    assert point.declared == 9
    assert point.saturated is True, "neuf déclarés au-delà d'un budget de sept"


def test_un_lot_sans_ligne_declaree_n_invente_pas_un_zero(migrated: Settings) -> None:
    """**Zéro et « pas de ligne » ne sont pas la même chose** : l'un est une
    déclaration du modèle, l'autre un collage qui l'a laissée derrière lui."""
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=6, budget=6)

    serie = dossiers(settings=migrated)

    assert serie.points and serie.points[0].declared is None
    assert serie.points[0].saturated is None
    assert not serie.declared_points


def test_une_declaration_ambigue_ne_se_rattache_a_aucun_lot(migrated: Settings) -> None:
    """Deux lots de même taille dans la même session : rien ne dit auquel la
    déclaration répond. En cas de doute, rien — même règle que partout."""
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=6, budget=6)
    _prompt(migrated, session_id, blocs=6, budget=6)
    _collage(migrated, session_id, reperes=6, rang=0)
    _collage(migrated, session_id, reperes=6, rang=1)
    prompt_service.backfill_research_budget(migrated)

    serie = dossiers(settings=migrated)

    assert not serie.declared_points, "deux candidats ne se départagent pas"


def test_la_serie_ne_rend_aucune_tendance(migrated: Settings) -> None:
    """**Le critère est une propriété, pas une valeur.** Ce bloc ne doit porter
    ni pente, ni moyenne, ni projection — trois relevés sur un seul jour ne
    décrivent aucun comportement, et la seule garde qui tienne est qu'aucun
    champ de ce genre n'existe."""
    interdits = {"slope", "trend", "average", "mean", "forecast", "pente", "moyenne"}

    champs = set(dossiers.__annotations__) | set(
        vars(type(dossiers(settings=migrated))).get("__dataclass_fields__", {})
    )

    assert not (champs & interdits)


@pytest.mark.parametrize("reperes", [1, 5])
def test_le_croisement_se_rend_a_cote_et_jamais_divise(migrated: Settings, reperes: int) -> None:
    """Les deux croisements demandés — part de `lecture`, résidu du cran 3 — se
    lisent **à côté** de la série. Trois points ne portent aucun rapport, donc
    aucune division ne se calcule."""
    session_id = _session(migrated)
    _prompt(migrated, session_id, blocs=reperes, budget=reperes)
    _collage(migrated, session_id, reperes=reperes)
    prompt_service.backfill_research_budget(migrated)

    serie = dossiers(settings=migrated)

    assert isinstance(serie.reading_share, dict)
    assert serie.rung_three is None, "aucune sélection de cran 3 tranchée dans cette base"
