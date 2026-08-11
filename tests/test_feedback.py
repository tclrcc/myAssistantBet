"""Boucle de retour : ce que l'historique des picks renvoie dans le prompt.

Le risque propre a cette fonctionnalite n'est pas de manquer un chiffre, c'est
d'en publier un qui ne veut rien dire — un 2/3 lu « 67 % » — ou de laisser
entrer un indicateur financier par la porte de derriere. Les deux ont leur test.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import competitions as competitions_service
from myassistantbet.services import coupons as coupons_service
from myassistantbet.services.history import (
    FEEDBACK_MIN_DAYS,
    FEEDBACK_MIN_ROWS,
    FEEDBACK_MIN_TOTAL,
    FEEDBACK_WINDOW,
    Feedback,
    FeedbackRow,
    add_pick,
    feedback,
    set_result,
)
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import (
    DEFAULT_TEMPLATE,
    PREFERENCE_NOTES,
    TEMPLATES_DIR,
    CustomizationError,
    build_prompt,
    competition_notes,
    read_preference,
    save_preference,
    save_prompt,
)

from .helpers import NOW, lot_avec_recul, pose_bandes


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session_avec_match(
    settings: Settings,
    sport: str = "football",
    competition: str = "Amical",
) -> tuple[int, int]:
    """Cree un evenement manuel cote, et renvoie (session_id, event_id)."""
    event_id = save(
        build(
            sport,
            competition,
            "Lyon" if sport == "football" else "Moutet",
            "Nice" if sport == "football" else "Bergs",
            "2099-01-01",
            "20:45",
            "Lyon 2.10\nNice 3.40",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings), event_id


def _regle(
    settings: Settings,
    session_id: int,
    event_id: int,
    tier: str,
    result: str,
    market: str = "O/U 2.5",
    confidence: str = "",
) -> None:
    pick_id = add_pick(
        session_id,
        tier,
        market,
        "Over",
        event_id=str(event_id),
        confidence=confidence,
        # Ces tests montent plusieurs selections sur un **meme match** par
        # commodite — c'est le match le moins couteux a fabriquer. La note
        # d'independance est donc fournie d'office : c'est un test dedie qui
        # verifie qu'elle est exigee, pas chaque montage de fixture.
        independence_note="angles indépendants (fixture)",
        settings=settings,
    )
    set_result(pick_id, result, settings)
    # Le retour d'experience exige un etalement autant qu'un volume : des
    # selections toutes prises le meme jour mesurent ce jour-la. Les tests de
    # volume n'ont pas a porter cette seconde contrainte, donc les picks sont
    # repartis sur assez de journees — le seuil a son propre test.
    db.execute(
        "UPDATE picks SET created_at = ? WHERE id = ?",
        (f"2026-07-{1 + pick_id % FEEDBACK_MIN_DAYS:02d}T12:00:00Z", pick_id),
        settings=settings,
    )
    # Un pick ne nourrit le retour d'experience que s'il a ete joue, c'est a
    # dire rattache a un coupon. Le marquer a la main ferait passer le test
    # sans que le parcours reel fonctionne.
    coupons_service.create(session_id, [pick_id], settings=settings)


# -- Le seuil de publication ------------------------------------------------


def test_aucun_pick_ne_produit_aucun_bloc(migrated: Settings) -> None:
    report = feedback(migrated)

    assert report.empty, "sans pari tranche, le bloc disparait entierement du prompt"
    assert not report.enough
    assert report.by_tier == []


def test_sous_le_seuil_aucun_detail_n_est_publie(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL - 1):
        _regle(migrated, session_id, event_id, "safe", "win")

    report = feedback(migrated)

    assert not report.empty
    assert not report.enough
    assert report.by_tier == [], "un taux sous le seuil mesure le hasard, pas une tendance"


def test_un_lot_concentre_sur_quelques_jours_ne_publie_aucun_detail(migrated: Settings) -> None:
    """Le volume ne suffit pas : soixante selections prises en quatre jours
    mesurent ces quatre jours-la — un tournoi, une soiree de coupe, une meteo.

    Constate en reel : les soixante selections de la fenetre couvraient du 5 au
    8 aout, et le bloc publiait « Masters 1000 13/30 » a cote de « Tennis 13/30 »
    comme deux observations, la ou c'etaient les memes matchs sous deux noms.
    """
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL + 10):
        _regle(migrated, session_id, event_id, "safe", "win")
    db.execute("UPDATE picks SET created_at = '2026-07-01T12:00:00Z'", settings=migrated)

    report = feedback(migrated)

    assert report.settled >= FEEDBACK_MIN_TOTAL, "le volume, lui, est atteint"
    assert report.days == 1
    assert not report.enough
    assert report.by_tier == []


def test_le_meme_lot_etale_publie_ses_taux(migrated: Settings) -> None:
    """Le pendant du precedent : c'est bien l'etalement qui manquait, pas autre
    chose. Sans ce test, un garde-fou trop severe passerait inapercu."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL + 10):
        _regle(migrated, session_id, event_id, "safe", "win")

    report = feedback(migrated)

    assert report.days >= FEEDBACK_MIN_DAYS
    assert report.enough
    assert [row.key for row in report.by_tier] == ["safe"]


def test_deux_paris_du_meme_soir_ne_font_qu_une_journee(migrated: Settings) -> None:
    """C'est la journee d'**analyse** qui est comptee, pas celle du match : deux
    paris pris dans la meme seance restent une seule decision de travail, meme
    s'ils portent sur deux jours de calendrier."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(4):
        _regle(migrated, session_id, event_id, "safe", "win")
    db.execute(
        "UPDATE picks SET created_at = '2026-07-01T22:00:00Z' WHERE id % 2 = 0", settings=migrated
    )
    db.execute(
        "UPDATE picks SET created_at = '2026-07-01T23:30:00Z' WHERE id % 2 = 1", settings=migrated
    )

    assert feedback(migrated).days == 1


def test_un_regroupement_trop_maigre_est_ecarte(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(FEEDBACK_MIN_ROWS - 1):
        _regle(migrated, session_id, event_id, "giga_fun", "loss")

    keys = {row.key for row in feedback(migrated).by_tier}

    assert "safe" in keys
    assert "giga_fun" not in keys, "trois paris ne font pas un taux"


# -- Ce que le taux compte --------------------------------------------------


def test_les_annules_et_les_attentes_sortent_du_denominateur(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    moitie = FEEDBACK_MIN_TOTAL // 2
    for _ in range(moitie):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(moitie):
        _regle(migrated, session_id, event_id, "safe", "loss")
    _regle(migrated, session_id, event_id, "safe", "void")
    _regle(migrated, session_id, event_id, "safe", "pending")

    row = next(row for row in feedback(migrated).by_tier if row.key == "safe")

    assert (row.won, row.lost) == (moitie, moitie)
    assert row.settled == 2 * moitie, "ni l'annule ni l'attente n'entrent au denominateur"
    assert row.rate == 0.5


def test_la_fenetre_ne_retient_que_les_derniers(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_WINDOW + 10):
        _regle(migrated, session_id, event_id, "safe", "loss")

    assert feedback(migrated).settled == FEEDBACK_WINDOW


def test_les_libelles_de_marche_sont_regroupes(migrated: Settings) -> None:
    """`Over 2.5 buts` et `over 2,5 Buts` sont le meme marche, ecrit deux fois."""
    session_id, event_id = _session_avec_match(migrated)
    # Le groupe doit survivre au minimum par ligne : on repete les orthographes
    # jusqu'a l'atteindre, sinon le test mesurerait le seuil et non le regroupement.
    orthographes = ("Over 2.5 buts", "over 2,5  Buts", "OVER 2.5 BUTS", "Over 2.5 buts")
    for index in range(FEEDBACK_MIN_ROWS):
        _regle(
            migrated,
            session_id,
            event_id,
            "safe",
            "win",
            market=orthographes[index % len(orthographes)],
        )
    for _ in range(FEEDBACK_MIN_TOTAL - FEEDBACK_MIN_ROWS):
        _regle(migrated, session_id, event_id, "fun", "loss", market="Score exact")

    markets = {row.key: row for row in feedback(migrated).by_market}

    assert set(markets) == {"over 2 5 buts", "score exact"}
    assert markets["over 2 5 buts"].settled == FEEDBACK_MIN_ROWS, (
        "quatre orthographes, un seul marche"
    )


def test_le_taux_par_confiance_est_expose(migrated: Settings) -> None:
    """L'ecart entre confiance annoncee et taux constate est le signal utile."""
    session_id, event_id = _session_avec_match(migrated)
    moitie = FEEDBACK_MIN_TOTAL // 2
    for _ in range(moitie):
        _regle(migrated, session_id, event_id, "safe", "loss", confidence="5")
    for _ in range(moitie):
        _regle(migrated, session_id, event_id, "fun", "win", confidence="3")

    by_confidence = {row.key: row for row in feedback(migrated).by_confidence}

    assert by_confidence["5"].rate == 0.0
    assert by_confidence["3"].rate == 1.0


# -- Section 9 : aucun indicateur financier ---------------------------------


def test_aucun_champ_financier_sur_le_retour() -> None:
    """Meme garde que sur les agregats de l'historique (SPEC.md section 9)."""
    interdits = {"roi", "profit", "stake", "mise", "gain", "esperance", "ev", "value", "edge"}

    for classe in (Feedback, FeedbackRow):
        noms = {item.name for item in fields(classe)}
        noms |= {name for name in dir(classe) if not name.startswith("_")}
        assert not (noms & interdits), f"{classe.__name__} expose un indicateur financier"


# -- Rendu dans le prompt ---------------------------------------------------


def test_le_prompt_omet_le_bloc_sans_historique(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CE QUE L'HISTORIQUE DIT" not in body, "aucune ligne vide sur une base neuve"


def test_le_prompt_annonce_le_manque_de_recul(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _regle(migrated, session_id, event_id, "safe", "win")

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CE QUE L'HISTORIQUE DIT" in body
    bloc = body.split("CE QUE L'HISTORIQUE DIT")[1].split("## SORTIE")[0]
    assert "recul insuffisant, non transmis" in bloc
    assert "%" not in bloc
    # Le chapitre consommait vingt-cinq lignes pour expliquer qu'il manque du
    # recul, puis interdisait d'en tirer quoi que ce soit : des tokens pour
    # transmettre une information qu'il defend ensuite d'utiliser. Le texte
    # pedagogique vit desormais dans CLAUDE.md.
    assert len(bloc.strip().splitlines()) <= 8, "le bloc reste court quand il n'a rien a dire"


def test_le_prompt_publie_les_taux_et_interdit_la_comparaison(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_ROWS):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(FEEDBACK_MIN_TOTAL - FEEDBACK_MIN_ROWS):
        _regle(migrated, session_id, event_id, "giga_fun", "loss")

    body = build_prompt(session_id, settings=migrated, now=NOW).body
    bloc = body.split("CE QUE L'HISTORIQUE DIT")[1].split("## SORTIE")[0]

    assert "🟢 SAFE" in bloc
    assert "100 %" in bloc
    assert "🔴 GIGA FUN" in bloc and "0 %" in bloc
    # Le garde-fou compte autant que le chiffre : sans lui, on a fabrique un
    # detecteur de value a partir de son propre historique.
    assert "jamais" in bloc and "cote" in bloc
    assert "espérance" in bloc


# -- Fiches de competition --------------------------------------------------


def _competition_id(settings: Settings, label: str) -> int:
    return next(
        row["id"] for row in competitions_service.list_all(settings) if row["label"] == label
    )


def test_la_fiche_de_competition_entre_dans_le_prompt(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    competitions_service.set_notes(
        _competition_id(migrated, "Amical"), "Match de préparation, effectifs remaniés.", migrated
    )

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Amical : Match de préparation, effectifs remaniés." in body


def test_la_fiche_n_est_rendue_qu_une_fois(migrated: Settings) -> None:
    """Deux matchs d'une meme competition ne repetent pas sa fiche."""
    session_id, _ = _session_avec_match(migrated)
    second = save(
        build(
            "football",
            "Amical",
            "Reims",
            "Brest",
            "2099-01-01",
            "21:00",
            "Reims 2.00",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(second, True, migrated)
    competitions_service.set_notes(_competition_id(migrated, "Amical"), "Hors saison.", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert body.count("Amical : Hors saison.") == 1


def test_une_fiche_vide_ne_produit_aucune_ligne(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    competitions_service.set_notes(_competition_id(migrated, "Amical"), "   ", migrated)

    assert competition_notes(session_id, migrated, NOW) == []
    assert (
        "Fiches des compétitions" not in build_prompt(session_id, settings=migrated, now=NOW).body
    )


# -- Consignes permanentes --------------------------------------------------


def test_les_consignes_entrent_en_tete_du_prompt(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    save_preference(PREFERENCE_NOTES, "Je ne joue jamais les cartons.", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CONSIGNES PERMANENTES" in body
    assert "Je ne joue jamais les cartons." in body
    assert body.index("CONSIGNES PERMANENTES") < body.index("## MATCHS")


def test_des_consignes_vides_ne_produisent_aucune_section(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    save_preference(PREFERENCE_NOTES, "   ", migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == ""
    assert "CONSIGNES PERMANENTES" not in build_prompt(session_id, settings=migrated, now=NOW).body


def test_des_consignes_trop_longues_sont_refusees(migrated: Settings) -> None:
    with pytest.raises(CustomizationError, match="trop longues"):
        save_preference(PREFERENCE_NOTES, "a" * 5000, migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == "", "rien n'a ete ecrit"


def test_les_consignes_se_remplacent(migrated: Settings) -> None:
    save_preference(PREFERENCE_NOTES, "Première version.", migrated)
    save_preference(PREFERENCE_NOTES, "Seconde version.", migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == "Seconde version."


# -- Ecrans -----------------------------------------------------------------


def test_enregistrement_des_consignes_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post("/settings/preferences", data={"preferences": "Pas de corners."})

    assert response.status_code == 200
    assert "Consignes enregistrées." in response.text
    assert read_preference(PREFERENCE_NOTES, isolated_settings) == "Pas de corners."


def test_consignes_trop_longues_refusees_par_l_ecran(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post("/settings/preferences", data={"preferences": "a" * 5000})

    assert response.status_code == 200
    assert "trop longues" in response.text
    assert read_preference(PREFERENCE_NOTES, isolated_settings) == ""


def test_enregistrement_d_une_fiche_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    _session_avec_match(isolated_settings)
    competition_id = _competition_id(isolated_settings, "Amical")

    response = client.post(f"/competitions/{competition_id}/notes", data={"notes": "Aller-retour."})

    assert response.status_code == 200
    assert "Aller-retour." in response.text


# -- Orientation : vers quoi se tourner -------------------------------------


def test_le_retour_compte_les_selections_non_jouees(migrated: Settings) -> None:
    """Le bloc juge l'analyse : une selection ecartee dit autant si l'angle etait bon."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL):
        pick_id = add_pick(
            session_id,
            "safe",
            "O/U 2.5",
            "Over",
            event_id=str(event_id),
            independence_note="angles indépendants (fixture)",
            settings=migrated,
        )
        set_result(pick_id, "win", migrated)

    assert feedback(migrated).settled == FEEDBACK_MIN_TOTAL
    assert feedback(migrated, played_only=True).empty


def test_le_taux_par_competition_est_expose(migrated: Settings) -> None:
    """« Quel type de match » se lit par competition, pas par sport."""
    session_id, event_id = _session_avec_match(migrated, competition="Ligue 1")
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")

    par_competition = {row.label: row for row in feedback(migrated).by_competition}

    assert par_competition["Ligue 1"].rate == 1.0


def test_le_taux_par_niveau_de_tournoi_est_expose(migrated: Settings) -> None:
    """Entre le sport et la competition : « tennis » est trop large, un tournoi
    pris seul trop etroit pour tenir un echantillon."""
    session_id, event_id = _session_avec_match(
        migrated, sport="tennis", competition="ATP Canadian Open"
    )
    db.execute(
        "UPDATE competitions SET category = 'masters_1000' WHERE label = 'ATP Canadian Open'",
        settings=migrated,
    )
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")

    par_niveau = {row.label: row for row in feedback(migrated).by_category}

    assert par_niveau["Masters 1000"].rate == 1.0
    assert "Par niveau de compétition" in build_prompt(session_id, settings=migrated, now=NOW).body


def test_un_tournoi_sans_niveau_ne_produit_pas_de_ligne(migrated: Settings) -> None:
    """« Non renseigne » ne dirait rien sur les matchs, seulement sur la saisie."""
    session_id, event_id = _session_avec_match(migrated, competition="Amical")
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")

    assert feedback(migrated).by_category == []


def test_le_prompt_oriente_sans_devenir_un_argument(migrated: Settings) -> None:
    """Un taux dit ou chercher en premier, jamais pourquoi selectionner."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")

    bloc = (
        build_prompt(session_id, settings=migrated, now=NOW)
        .body.split("CE QUE L'HISTORIQUE DIT")[1]
        .split("## SORTIE")[0]
    )

    assert "Par compétition" in bloc
    assert "ordre de passage, pas un argument" in bloc
    assert "ne remplace pas un angle manquant" in bloc
    assert "PASSE" in bloc, "sans angle, la reponse reste PASSE"
    # Le garde-fou de la section 9 survit a l'ajout de l'orientation.
    assert "jamais" in bloc and "cote" in bloc and "espérance" in bloc


def test_un_libelle_long_ne_casse_pas_l_alignement(migrated: Settings) -> None:
    """Un nom de competition depasse volontiers la largeur d'un palier."""
    ligne = FeedbackRow(key="x", label="Championnat de Belgique deuxieme division", won=3, lost=1)

    assert ligne.line.startswith("Championnat de Belg…")
    assert len(ligne.line.split("3/4")[0]) == FeedbackRow.LABEL_WIDTH + 1


def test_le_prompt_exige_un_fait_date_pour_un_palier_haut(migrated: Settings) -> None:
    """Mesure sur les soixante-trois premieres selections : le palier ULTRA FUN
    est a 0/6, et les selections a cote superieure a 2.00 a 1/7. Les favoris,
    eux, tiennent. L'analyse ne detecte pas les surprises, elle les tente — et
    rien dans la sortie attendue ne relevait l'exigence a mesure que le palier
    monte. Le fait doit etre **nomme et date**, donc verifiable en section A."""
    session_id, event_id = _session_avec_match(migrated)
    _regle(migrated, session_id, event_id, "safe", "win")

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "Les paliers hauts se méritent" in corps
    assert "fait nommé et daté" in corps
    assert "un favori qu'on n'a pas envie de jouer" in corps
    assert "Deux lignes qui tombent ensemble n'en font qu'une" in corps


# -- Le taux de selection : ce que l'analyse ecarte --------------------------


def _lot(settings: Settings, session_id: int, event_ids: list[int]) -> None:
    """Archive un prompt portant ces matchs — le denominateur du taux."""
    with db.connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
            "VALUES (?, 't', '', 10, '2026-08-04T10:00:00Z')",
            (session_id,),
        )
        conn.executemany(
            "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            [(int(cursor.lastrowid), event_id) for event_id in event_ids],
        )


def _session_de_lot(settings: Settings, retenus: int, lot: int, jour: int = 4) -> int:
    """Une session dont `lot` matchs sont entres au prompt, `retenus` selectionnes.

    La session est creee explicitement : `toggle_selection` rend celle du jour,
    si bien que trois appels d'affilee donneraient trois fois la meme.
    """
    with db.connect(settings) as conn:
        session_id = int(
            conn.execute(
                "INSERT INTO sessions (label, created_at) VALUES (?, ?)",
                (f"Session {jour}", f"2026-08-{jour:02d}T10:00:00Z"),
            ).lastrowid
        )
    matchs = [_session_avec_match(settings)[1] for _ in range(lot)]
    _lot(settings, session_id, matchs)
    for event_id in matchs[:retenus]:
        _regle(settings, session_id, event_id, "safe", "win")
    return session_id


def test_le_taux_de_selection_median_entre_dans_le_prompt(migrated: Settings) -> None:
    """L'application enregistrait ce qui avait ete selectionne, jamais ce qui
    avait ete ecarte. Le prompt annonce pourtant que passer est un resultat
    valable et attendu sur une partie du lot : sans denominateur, cette phrase
    n'etait ni verifiable ni suivie."""
    for jour, (retenus, lot) in enumerate(((1, 4), (2, 4), (3, 4)), start=4):
        _session_de_lot(migrated, retenus, lot, jour)

    report = feedback(migrated)

    assert report.selection_median == 0.5, "la mediane de 25, 50 et 75 %"
    assert report.selection_sessions == 3
    assert report.selection_line == "50 % en médiane, sur 3 sessions"


def test_la_mediane_ignore_les_sessions_sans_lot(migrated: Settings) -> None:
    """Une session qui n'a genere aucun prompt n'a rien soumis a l'analyse."""
    for jour, (retenus, lot) in enumerate(((1, 4), (2, 4), (3, 4)), start=4):
        _session_de_lot(migrated, retenus, lot, jour)
    _session_avec_match(migrated)

    assert feedback(migrated).selection_sessions == 3


def test_sous_trois_sessions_aucune_mediane(migrated: Settings) -> None:
    """Une mediane sur deux valeurs est la moyenne des deux, sur une seule c'est
    cette session-la. Meme regle que partout : un chiffre faux oriente plus
    surement que pas de chiffre du tout."""
    for jour, (retenus, lot) in enumerate(((1, 4), (3, 4)), start=4):
        _session_de_lot(migrated, retenus, lot, jour)

    report = feedback(migrated)

    assert report.selection_median is None
    assert report.selection_line == ""


def test_la_mediane_survit_au_manque_de_recul_sur_les_resultats(migrated: Settings) -> None:
    """Les trois garde-fous du bloc protegent des **taux de reussite**.

    Celui-ci decrit un comportement — comment je trie — et une part de lot ne
    devient pas trompeuse parce que les resultats manquent. Meme exemption que
    `labelling()` sur la page.
    """
    for jour, (retenus, lot) in enumerate(((1, 4), (2, 4), (3, 4)), start=4):
        _session_de_lot(migrated, retenus, lot, jour)

    report = feedback(migrated)

    assert not report.enough, "trop peu de recul pour publier des taux"
    assert report.by_tier == [], "et rien n'est publie de ce cote"
    assert report.selection_line, "mais le tri, lui, se dit"


def test_le_prompt_presente_le_taux_de_selection_comme_un_constat(migrated: Settings) -> None:
    """Un quota de passes ferait ecarter un match pour remplir un compte —
    l'erreur que le prompt nomme ailleurs comme la plus couteuse."""
    for jour, (retenus, lot) in enumerate(((1, 4), (2, 4), (3, 4)), start=4):
        session_id = _session_de_lot(migrated, retenus, lot, jour)

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "Part du lot que je sélectionne" in corps
    assert "50 % en médiane, sur 3 sessions" in corps
    assert "constat sur mon tri, pas un quota" in corps
    assert "Il ne se compare à aucune cote" in corps


def test_le_taux_de_selection_ne_produit_aucun_champ_financier() -> None:
    """Meme garde-fou que le reste du bloc (SPEC.md section 9)."""
    noms = {field.name for field in fields(Feedback)}

    assert not noms & {"roi", "profit", "stake", "mise", "gain", "bankroll"}


def test_le_prompt_demande_le_type_et_la_source(migrated: Settings) -> None:
    """Le prompt reclamait deja les deux elements en sections A et B, et les
    jetait une fois l'analyse rendue. Deux colonnes suffisent a les garder.

    « lecture » est presente comme une reponse normale : le contraire ferait
    promouvoir un bloc de contexte au rang de source citee, et detruirait la
    seule comparaison qui puisse changer la methode.
    """
    session_id, event_id = _session_avec_match(migrated)
    _regle(migrated, session_id, event_id, "safe", "win")

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "| Type | Source |" in corps
    assert "reprend le mot de la section B" in corps
    assert "`lecture` est une réponse **normale et fréquente**" in corps
    assert "adossée à un fait daté tient mieux qu'une lecture" in corps


# -- La bande cible, reinjectee dans le prompt ------------------------------


def _bande(settings: Settings, level: int, low: float, high: float | None) -> None:
    db.execute(
        "UPDATE confidence_bands SET low = ?, high = ? WHERE level = ?",
        (low, high, level),
        settings=settings,
    )


def _confiance(
    settings: Settings, session_id: int, event_id: int, gagnes: int, perdus: int, cran: str = "3"
) -> None:
    """Des selections d'un cran donne, pour peupler sa ligne.

    **Il en faut au moins deux crans depuis que les cibles sont relatives** : un
    seul cran fait a lui seul le taux global, sa cible se resout donc autour de
    son propre taux, et tout ecart serait nul par construction.
    """
    for index in range(gagnes + perdus):
        _regle(
            settings,
            session_id,
            event_id,
            "safe",
            "win" if index < gagnes else "loss",
            confidence=cran,
        )


def _deux_crans(settings: Settings, session_id: int, event_id: int) -> None:
    """Cran 3 a 40 %, cran 4 a 60 %, **taux global a 50 %**.

    Soixante selections en tout, soit exactement la fenetre glissante : au-dela
    elle tronque par date, et les taux ne seraient plus ceux qu'on a poses. Les
    cibles se resolvent alors a 44-53 % pour le cran 3 et 53-62 % pour le 4.
    """
    _confiance(settings, session_id, event_id, gagnes=12, perdus=18, cran="3")
    _confiance(settings, session_id, event_id, gagnes=18, perdus=12, cran="4")


def test_la_bande_cible_accompagne_le_taux_dans_le_prompt(migrated: Settings) -> None:
    """« Confiance 4 » n'est pas un pourcentage : sans referentiel, l'ecart entre
    la confiance annoncee et le taux constate ne se mesurait contre rien — et le
    seul signal reellement actionnable de tout l'historique ne remontait jamais.
    """
    session_id, event_id = _session_avec_match(migrated)
    _deux_crans(migrated, session_id, event_id)

    ligne = next(row for row in feedback(migrated).by_confidence if row.key == "3")

    assert ligne.rate == 0.4
    assert ligne.band is not None and ligne.band.label == "44 – 53 %"
    assert ligne.band.offset_label == "global -6 → +3", "stockee en ecart, rendue en taux"
    assert ligne.gap == pytest.approx(-4.0)
    assert "cible 44 – 53 %, écart -4 pts" in ligne.line


def test_un_taux_dans_sa_bande_n_affiche_aucun_ecart(migrated: Settings) -> None:
    """« Écart 0 pt » ferait chercher un probleme absent. Le cran 4 tient sa
    cible : a 60 % pour un global de 50, il vise `global +3 -> +12`."""
    session_id, event_id = _session_avec_match(migrated)
    _deux_crans(migrated, session_id, event_id)

    ligne = next(row for row in feedback(migrated).by_confidence if row.key == "4")

    assert ligne.gap is None
    assert "cible 53 – 62 %" in ligne.line
    assert "écart" not in ligne.line


def test_un_ecart_non_confirme_ne_porte_pas_la_mention(migrated: Settings) -> None:
    """Au volume courant presque chaque intervalle couvre plusieurs bandes.

    Faire resserrer une notation sur du bruit orienterait plus surement
    qu'aucun chiffre : la ligne dit alors l'ecart **sans** le mot qui declenche
    l'action. Meme regle que la page, et elle compte plus encore ici.
    """
    session_id, event_id = _session_avec_match(migrated)
    _deux_crans(migrated, session_id, event_id)

    ligne = next(row for row in feedback(migrated).by_confidence if row.key == "3")

    assert ligne.gap is not None, "l'ecart observe existe"
    assert not ligne.off_band, "mais l'intervalle traverse encore la bande"
    assert "hors bande" not in ligne.line


def test_un_ecart_confirme_porte_la_mention(migrated: Settings) -> None:
    """Bande resserree : l'intervalle en sort alors entierement, et c'est le seul
    cas ou le prompt demande d'agir."""
    session_id, event_id = _session_avec_match(migrated)
    _bande(migrated, 3, 80.0, 90.0)
    _confiance(migrated, session_id, event_id, gagnes=16, perdus=24)

    ligne = next(row for row in feedback(migrated).by_confidence if row.key == "3")

    assert ligne.off_band
    assert ligne.line.endswith("hors bande")


def test_le_prompt_explique_comment_lire_l_ecart(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _deux_crans(migrated, session_id, event_id)

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "cible 44 – 53 %, écart -4 pts" in corps
    assert "seul chiffre de ce bloc qui parle de ma notation plutôt que des matchs" in corps
    assert "resserrer dès la section C" in corps
    assert "n'en fais pas une règle" in corps
    assert "il ne se compare à aucune cote" in corps


def test_les_autres_axes_ne_portent_aucune_bande(migrated: Settings) -> None:
    """Un sport ou un marche ne se fixe pas d'objectif de taux — seule une
    confiance annoncee le fait, c'est meme sa definition."""
    session_id, event_id = _session_avec_match(migrated)
    _confiance(migrated, session_id, event_id, gagnes=16, perdus=24)

    report = feedback(migrated)

    for axe in (report.by_tier, report.by_sport, report.by_market, report.by_competition):
        assert all(row.band is None for row in axe)
        assert all("cible" not in row.line for row in axe)


def test_le_taux_implicite_ne_remonte_jamais_au_prompt(migrated: Settings) -> None:
    """Il est calcule a partir des cotes. L'injecter reviendrait a autoriser le
    raisonnement d'esperance que le prompt interdit partout ailleurs — et le fait
    que le chiffre vienne de mon propre historique n'y change rien (section 9).

    Il reste sur la page, ou il se lit a cote d'autres ecarts de la meme page.
    """
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win", confidence="3")

    noms = {field.name for field in fields(FeedbackRow)}
    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert not noms & {"price", "priced", "implied", "implied_sum"}
    assert "taux implicite" not in corps.lower()
    assert "probabilités implicites" in corps, "l'interdit, lui, reste écrit"


def test_un_cran_trop_peu_fourni_n_entre_pas_dans_le_prompt(migrated: Settings) -> None:
    """Sous huit selections tranchees, un cran est tu — bande comprise.

    Une cible affichee a cote d'un 3/4 ferait resserrer une notation sur quatre
    paris. Le garde-fou existait deja pour le taux ; il vaut d'autant plus pour
    l'ecart, qui est le seul chiffre du bloc sur lequel on agit.
    """
    session_id, event_id = _session_avec_match(migrated)
    _confiance(migrated, session_id, event_id, gagnes=16, perdus=24)
    for index in range(4):
        _regle(migrated, session_id, event_id, "safe", "win" if index else "loss", confidence="5")

    report = feedback(migrated)

    assert [row.key for row in report.by_confidence] == ["3"]
    assert "confiance 5" not in build_prompt(session_id, settings=migrated, now=NOW).body


def test_les_reglages_disent_que_les_bandes_partent_dans_le_prompt(client: TestClient) -> None:
    """Corriger un rendu sans relire son mode d'emploi laisse une affirmation
    perimee a l'endroit precis ou l'on vient de gagner en justesse."""
    page = " ".join(client.get("/settings").text.split())

    assert "ici et dans le prompt" in page
    assert "C'est la mention qui déclenche l'action, pas le nombre." in page


# -- Une fenetre ne se lit pas comme un total -------------------------------


def test_le_bloc_annonce_sa_fenetre_et_non_un_total(migrated: Settings) -> None:
    """`settled` plafonne a `FEEDBACK_WINDOW` : ecrit « 60 selections tranchees
    enregistrees » sur une base qui en porte cent, il se lit comme un total et
    fait croire a une perte de donnees. C'est exactement ce qui s'est produit.
    """
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_WINDOW + 12):
        _regle(migrated, session_id, event_id, "safe", "win")

    report = feedback(migrated)

    assert report.settled == FEEDBACK_WINDOW, "la fenetre plafonne"
    assert report.recorded == FEEDBACK_WINDOW + 12, "le total, lui, ne plafonne pas"
    attendu = (
        f"mes {FEEDBACK_WINDOW} dernière(s) sélection(s) tranchée(s), "
        f"sur {FEEDBACK_WINDOW + 12} au total"
    )
    assert report.scope_line == attendu

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())
    assert "fenêtre glissante" in corps
    assert f"sur {FEEDBACK_WINDOW + 12} au total" in corps


def test_le_total_ne_s_ecrit_pas_quand_la_fenetre_ne_mord_pas(migrated: Settings) -> None:
    """« 12 sur 12 au total » serait du bruit sur chaque prompt."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(12):
        _regle(migrated, session_id, event_id, "safe", "win")

    assert feedback(migrated).scope_line == "mes 12 dernière(s) sélection(s) tranchée(s)"


def test_le_manque_nomme_ce_qui_bloque_vraiment(migrated: Settings) -> None:
    """« Il en faudrait au moins 40 » sur un bloc qui en compte 60 est une phrase
    qui se contredit, et fait chercher une panne la ou il n'y a qu'un etalement
    trop court. Le texte nomme desormais celle des deux conditions qui manque.
    """
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL + 5):
        _regle(migrated, session_id, event_id, "safe", "win")
    # Toutes le meme jour : le volume est la, l'etalement non — le cas reel.
    db.execute("UPDATE picks SET created_at = '2026-07-01T12:00:00Z'", settings=migrated)

    report = feedback(migrated)

    assert report.settled >= report.minimum, "le volume est atteint"
    assert report.days < report.minimum_days, "l'etalement, non"
    assert "le volume suffit" in report.missing_note
    assert f"{report.minimum}" not in report.missing_note, "ne pas citer un seuil deja franchi"


def test_le_manque_nomme_les_deux_quand_les_deux_manquent(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _regle(migrated, session_id, event_id, "safe", "win")

    note = feedback(migrated).missing_note

    assert f"il en faudrait {FEEDBACK_MIN_TOTAL}" in note
    assert f"{FEEDBACK_MIN_DAYS} journées" in note


def test_le_taux_de_selection_dit_qu_il_compte_autre_chose() -> None:
    """« 36 % sur 6 sessions » et « 60 sélections sur 4 journées » a quelques
    lignes d'ecart se lisent comme un seul compte qui se contredit. Les deux
    populations sont differentes, et le texte le dit.

    **La phrase vit desormais dans la seule branche « assez de recul »**, et le
    test la lit dans le gabarit plutot que dans un rendu : c'est la que les deux
    nombres coexistent. Sous le seuil, aucun taux n'est publie — il n'y a qu'un
    nombre, donc rien a confondre, et l'expliquer couterait des tokens pour
    lever une ambiguite absente.
    """
    gabarit = (TEMPLATES_DIR / DEFAULT_TEMPLATE).read_text(encoding="utf-8")
    enough = " ".join(
        gabarit.split("{% if feedback.enough %}", 1)[1].split("{% else %}", 1)[0].split()
    )

    assert "ne lit pas la même population que le taux de sélection" in enough
    assert "sessions ayant produit un prompt" in enough
    assert "Deux nombres différents à quelques lignes d'écart sont donc normaux" in enough


# -- L'etat « pas de cible », vu du prompt -----------------------------------
#
# **Gate ouvert obligatoire.** En production il est ferme — 4 journees sur 10 —
# donc rien de ce qui touche aux bandes ne se voit dans un rendu reel. Ces tests
# passent par la fixture partagee, jamais par la base du jour.


def test_un_cran_sans_cible_ne_porte_ni_ecart_ni_hors_bande(migrated: Settings) -> None:
    """La ligne donne le taux et rien d'autre. Un cran pine par la source —
    `lecture` impose 1, une source de niveau 3-4 plafonne a 2 — ne peut ni se
    resserrer ni se relacher : lui afficher une cible reviendrait a demander un
    mouvement qui n'existe pas."""
    session_id = lot_avec_recul(migrated)
    pose_bandes(migrated, {3: (None, None), 4: (53.0, 62.0), 5: (62.0, None)})

    corps = build_prompt(session_id, settings=migrated, now=NOW).body
    bloc = corps.split("Par confiance annoncée")[1].split("Par sport")[0]

    sans_cible = next(
        ligne for ligne in bloc.splitlines() if ligne.strip().startswith("confiance 3")
    )
    assert "40 %" in sans_cible, "le taux constate reste, c'est un fait"
    assert "cible" not in sans_cible
    assert "écart" not in sans_cible
    assert "hors bande" not in sans_cible


def test_aucun_cran_cible_retire_le_paragraphe_de_resserrement(migrated: Settings) -> None:
    """Il explique un mecanisme dont aucune ligne ne peut declencher l'action :
    c'est exactement le genre de texte que ce prompt passe son temps a retirer."""
    session_id = lot_avec_recul(migrated)
    pose_bandes(migrated, {level: (None, None) for level in range(1, 6)})

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "Par confiance annoncée" in corps, "les taux, eux, restent publies"
    assert "seul chiffre de ce bloc qui parle de ma notation" not in corps
    assert "hors bande" not in corps


def test_un_cran_cible_garde_son_paragraphe(migrated: Settings) -> None:
    """Le pendant du precedent : c'est bien l'absence de cible qui le retire, et
    non un garde-fou trop large. Sans ce test, un conditionnement casse
    passerait inapercu."""
    session_id = lot_avec_recul(migrated)
    pose_bandes(migrated, {1: (None, None), 2: (None, None), 5: (62.0, None)})

    corps = " ".join(build_prompt(session_id, settings=migrated, now=NOW).body.split())

    assert "seul chiffre de ce bloc qui parle de ma notation" in corps
    assert "Les crans sans cible n'en portent pas parce qu'aucun mouvement" in corps


def test_la_page_dit_qu_un_cran_n_a_pas_de_cible(client: TestClient, migrated: Settings) -> None:
    """Un humain qui lit un tableau doit savoir qu'une case est vide par
    decision et non par oubli — et jamais voir un zero, qui se lirait comme une
    cible de 0 %."""
    lot_avec_recul(migrated)
    pose_bandes(migrated, {3: (None, None)})

    page = client.get("/stats").text

    assert "pas de cible" in page
    assert "cible 0" not in page


# -- La suspension pendant une replication ----------------------------------


def test_une_replication_en_cours_coupe_les_taux(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le lot a tout le recul necessaire, et le bloc se tait quand meme.

    C'est le point : la suspension ne passe pas par les seuils. Ils sont ici
    largement franchis — `lot_avec_recul` les ouvre tous les deux — et aucun
    taux ne part. Des qu'un agregat de resultats entre dans le prompt, l'analyse
    lit son propre tableau de bord et les selections a venir cessent d'etre des
    tirages independants de ce qu'elles servent a eprouver.
    """
    monkeypatch.setattr("myassistantbet.services.history.FEEDBACK_SUSPENDED", True)
    session_id = lot_avec_recul(migrated)

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "retenus volontairement" in corps
    assert "Par palier" not in corps
    assert "Par confiance annoncée" not in corps


def test_la_suspension_ne_se_presente_pas_comme_un_manque_de_recul(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une fausse explication a l'endroit meme ou l'on cherche a etre rigoureux
    serait le pire endroit ou en mettre une.

    « Recul insuffisant » deviendra faux pendant la fenetre — le lot en a — et
    ferait chercher un volume qui est deja la.
    """
    monkeypatch.setattr("myassistantbet.services.history.FEEDBACK_SUSPENDED", True)
    session_id = lot_avec_recul(migrated)

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "recul insuffisant" not in corps
    assert "rien à en déduire sur mon\nhistorique" in corps


def test_la_suspension_ne_touche_que_les_taux_de_reussite(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le taux de selection ne depend d'aucun resultat, donc il ne referme
    aucune boucle : la suspension doit le laisser exactement ou il est.

    Compare **les deux etats du meme lot** plutot que de chercher une phrase
    dans un corps : le taux de selection a ses propres conditions d'apparition —
    trois sessions distinctes — et un test qui les confondrait avec la
    suspension passerait au vert pour la mauvaise raison.
    """
    lot_avec_recul(migrated)

    ouvert = feedback(migrated)
    monkeypatch.setattr("myassistantbet.services.history.FEEDBACK_SUSPENDED", True)
    suspendu = feedback(migrated)

    assert (ouvert.enough, suspendu.enough) == (True, False)
    assert suspendu.suspended and not ouvert.suspended
    assert suspendu.selection_line == ouvert.selection_line
    assert suspendu.settled == ouvert.settled, "les donnees ne bougent pas, leur diffusion oui"


def test_un_prompt_suspendu_n_est_pas_marque_comme_alimente(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prompts.feedback_active` doit rester faux : il enregistre ce qui est
    **parti**, pas ce qui aurait pu partir. Sans quoi la fenetre de replication
    se marquerait elle-meme comme contaminee."""
    monkeypatch.setattr("myassistantbet.services.history.FEEDBACK_SUSPENDED", True)
    session_id = lot_avec_recul(migrated)

    prompt_id = save_prompt(
        session_id, build_prompt(session_id, settings=migrated, now=NOW), migrated
    )

    ligne = db.query_one(
        "SELECT feedback_active FROM prompts WHERE id = ?", (prompt_id,), settings=migrated
    )
    assert ligne["feedback_active"] == 0
