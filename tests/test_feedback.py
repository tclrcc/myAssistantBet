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
    PREFERENCE_NOTES,
    CustomizationError,
    build_prompt,
    competition_notes,
    read_preference,
    save_preference,
)

from .helpers import NOW


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
            "2026-08-04",
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
    assert "Trop peu de recul" in body
    assert "%" not in body.split("CE QUE L'HISTORIQUE DIT")[1].split("## SORTIE")[0]


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
            "2026-08-04",
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
            session_id, "safe", "O/U 2.5", "Over", event_id=str(event_id), settings=migrated
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
    assert "Par niveau de tournoi" in build_prompt(session_id, settings=migrated, now=NOW).body


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
