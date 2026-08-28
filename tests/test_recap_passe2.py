"""Deuxieme passe sur le recapitulatif : ce que le premier rendu reel a montre.

Six defauts releves sur la journee du 28/08/2026, et deux mesures qui ont
renverse leur ordre d'importance.

**L'enchainement ecrasait ce qu'il savait calculer.** Rejeu des 23 journees :
**57,3 % des lignes** sortaient `indetermine`, 8,0 % seulement `libre`. La cause
est structurelle — la ligne de tennis tombe au rang 1 a 3 sur onze journees, les
sessions de jour se jouant avant le programme de football du soir — et une seule
ligne sans fin connue faisait basculer tout ce qui la suivait. Le 22/08, 48
lignes sur 80. La regle etait juste : aucune source ne publie la duree d'un match
de tennis. La **forme** ne l'etait pas.

**Le vivier n'est jamais determine.** 1 fois sur 69 (23 journees x 3
propositions). Le document disait « compose trois combines » sans dire comment
choisir : deux rendus de la meme journee ne donnaient pas les memes combines.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import combos as combos_service
from myassistantbet.services import recap as recap_service
from myassistantbet.services.history import add_pick
from myassistantbet.services.manual import build, save

MIDI = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _proposition(lot, *, legs: int, cran: int):
    """La proposition d'une regle donnee. Nommee plutot que prise au rang :
    l'ordre de `PROPOSALS` est une decision, pas une cle."""
    return next(
        p
        for p in lot.proposals
        if p.legs == legs and p.min_confidence == cran and not p.distinct_families
    )


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _today(settings: Settings) -> str:
    from myassistantbet.db import utcnow

    return utcnow()[:10]


def _match(settings: Settings, nom: str, *, sport: str = "football", heure: str = "20:45") -> int:
    return save(
        build(
            sport,
            f"Coupe {nom[:3]}",
            nom,
            f"Adv {nom}",
            "2099-01-01",
            heure,
            f"{nom} 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _pick(
    settings: Settings,
    event_id: int,
    *,
    conf: str = "4",
    marche: str = "1N2",
    selection: str = "Domicile",
    source: str = "betclic",
    prix: str = "1.50",
    note: str = "Angle de fixture.",
    invalidation: str = "Condition de fixture.",
) -> int:
    session_id = board_service.toggle_selection(event_id, True, settings)
    return add_pick(
        session_id,
        tier="safe",
        market=marche,
        selection=selection,
        event_id=str(event_id),
        price=prix,
        confidence=conf,
        price_source=source,
        angle_note=note,
        invalidation=invalidation,
        independence_note="angles indépendants (fixture)",
        settings=settings,
    )


# --- Defaut 6 : une ligne sans fin connue ne contamine plus ----------------


def test_le_football_qui_suit_un_tennis_garde_son_enchainement(migrated: Settings) -> None:
    """**Le defaut le plus lourd des six**, et nous l'avions classe mineur.

    Une ligne de football qui suit une autre ligne de football a un enchainement
    exact, et le tennis intercale ne change rien a ce calcul. L'ancienne regle
    faisait basculer tout ce qui suivait — 57,3 % des lignes du corpus.
    """
    settings = migrated
    tot = _match(settings, "Alpha", heure="18:00")
    tennis = _match(settings, "Bravo", sport="tennis", heure="19:00")
    tard = _match(settings, "Charlie", heure="20:30")
    for event in (tot, tennis, tard):
        _pick(settings, event)

    etats = {
        ligne.pick.event_id: ligne.overlap
        for ligne in recap_service.build(_today(settings), settings, now=MIDI).lines
    }

    assert etats[tot] == recap_service.LIBRE
    assert etats[tennis] == recap_service.FIN_INCONNUE
    # 18:00 + 115 min = 19:55, donc 20:30 est libre — et le tennis n'y change rien.
    assert etats[tard] == recap_service.LIBRE


def test_le_chevauchement_se_calcule_a_travers_une_ligne_sans_fin(
    migrated: Settings,
) -> None:
    settings = migrated
    tot = _match(settings, "Alpha", heure="20:00")
    tennis = _match(settings, "Bravo", sport="tennis", heure="20:30")
    tard = _match(settings, "Charlie", heure="21:00")
    for event in (tot, tennis, tard):
        _pick(settings, event)

    etats = {
        ligne.pick.event_id: ligne.overlap
        for ligne in recap_service.build(_today(settings), settings, now=MIDI).lines
    }

    assert etats[tard] == recap_service.CHEVAUCHE


def test_les_lignes_sans_fin_connue_sont_nommees(migrated: Settings) -> None:
    """Nommer la ligne, dire ce qu'elle laisse indetermine, rendre le reste."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))
    tennis = _match(settings, "Bravo", sport="tennis", heure="19:00")
    _pick(settings, tennis)

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    corps = recap_service.render(lot, settings)

    assert [pick.event_id for pick in lot.without_end] == [tennis]
    assert "Alpha" in corps, "le rendu doit porter le lot"
    assert "Bravo" in corps
    assert "Fin non publiée" in corps
    assert recap_service.INDETERMINE not in corps if hasattr(recap_service, "INDETERMINE") else True


def test_les_trois_etats_sont_definis_dans_le_rendu(migrated: Settings) -> None:
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    for etat in recap_service.OVERLAP_LABELS.values():
        assert etat in corps


# --- Defaut 1 : l'angle et la condition d'invalidation ---------------------


def test_le_rendu_porte_l_angle_et_la_condition(migrated: Settings) -> None:
    """Sans elles, « meme cause » ne se juge que sur les metadonnees — deux
    championnats differents, donc pas de cause commune. Et quatre jambes font
    quatre conditions a controler avant de poser."""
    settings = migrated
    _pick(
        settings,
        _match(settings, "Alpha"),
        note="Rythme casse par la trêve.",
        invalidation="Kane titulaire à l'annonce des onze.",
    )

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "Rythme casse par la trêve." in corps
    assert "Kane titulaire à l'annonce des onze." in corps


def test_le_vocabulaire_ferme_de_l_angle_ne_remonte_pas(migrated: Settings) -> None:
    """`angle` porte deux valeurs — 10 « maniere » et 7 « issue » le 28/08. Deux
    lignes « maniere » n'ont pas une cause commune : le transmettre couterait des
    tokens pour un mot qui ne discrimine rien."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "Alpha" in corps, "le rendu doit porter le lot"
    assert "maniere" not in corps.lower()


def test_une_ligne_sans_prose_porte_un_etat_et_non_un_silence(migrated: Settings) -> None:
    """Les colonnes datent du 17/08/2026 et sont pleines depuis. La population
    anterieure est **close**, mais un rendu sur une journee ancienne existera."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"), note="", invalidation="")

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "Alpha" in corps, "le rendu doit porter le lot"
    assert recap_service.PROSE_ABSENTE in corps


def test_le_rendu_renvoie_a_la_definition_stricte_de_la_cause(migrated: Settings) -> None:
    """**14 conditions sur 17 reposent sur les compositions.** Juger « meme
    cause » sur le mecanisme la declencherait presque partout : le document
    renvoie a la definition du gabarit d'analyse et n'en ecrit pas une seconde.
    """
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "au sens strict" in corps
    assert "section C" in corps


# --- Defaut 3 : le critere de composition ----------------------------------


def test_l_application_choisit_les_jambes(migrated: Settings) -> None:
    """**Determine 1 fois sur 69** : sans critere ecrit, deux rendus de la meme
    journee ne donnaient pas les memes combines."""
    settings = migrated
    fort = _pick(settings, _match(settings, "Alpha", heure="18:00"), conf="5")
    moyen = _pick(settings, _match(settings, "Bravo", heure="19:00"), conf="4")
    tot = _pick(settings, _match(settings, "Charlie", heure="20:00"), conf="3")
    tard = _pick(settings, _match(settings, "Delta", heure="21:00"), conf="3")

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    prop = _proposition(lot, legs=4, cran=3)

    # Cran decroissant, puis l'heure departage les deux crans 3.
    assert [pick.pick_id for pick in prop.chosen] == [fort, moyen, tot, tard]


def test_le_prix_maison_departage_et_ne_decide_jamais_seul(migrated: Settings) -> None:
    """Il n'est jamais primaire : sur 5 journees le vivier n'en porte **aucun**,
    et un critere qui rendrait « non constructible » sur une journee de 18
    matchs n'en est pas un."""
    settings = migrated
    ref = _pick(settings, _match(settings, "Alpha", heure="18:00"), conf="4", source="reference")
    maison = _pick(settings, _match(settings, "Bravo", heure="19:00"), conf="4")
    _pick(settings, _match(settings, "Charlie", heure="20:00"), conf="3")
    _pick(settings, _match(settings, "Delta", heure="21:00"), conf="3")

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    prop = _proposition(lot, legs=4, cran=3)

    assert prop.chosen[0].pick_id == maison, "à cran égal, le prix maison passe devant"
    assert ref in {pick.pick_id for pick in prop.chosen}, "il ne l'écarte jamais"


def test_une_proposition_ne_porte_jamais_deux_jambes_sur_un_match(
    migrated: Settings,
) -> None:
    settings = migrated
    seul = _match(settings, "Alpha", heure="18:00")
    _pick(settings, seul, conf="5")
    _pick(settings, seul, conf="5", marche="O/U", selection="Over 2.5")
    _pick(settings, _match(settings, "Bravo", heure="19:00"), conf="4")
    _pick(settings, _match(settings, "Charlie", heure="20:00"), conf="4")

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    prop = _proposition(lot, legs=3, cran=4)

    matchs = [pick.event_id for pick in prop.chosen]
    assert len(matchs) == len(set(matchs))


# --- Defaut 2 : le produit se calcule --------------------------------------


def test_le_produit_est_calcule_par_l_application(migrated: Settings) -> None:
    """`combos.product` porte la regle, et le recapitulatif l'appelle : deux
    ecritures du meme produit auraient fini par ne pas traiter la jambe sans
    prix de la meme facon."""
    settings = migrated
    for nom, heure in (("Alpha", "18:00"), ("Bravo", "19:00"), ("Charlie", "20:00")):
        _pick(settings, _match(settings, nom, heure=heure), conf="4", prix="1.50")

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    prop = _proposition(lot, legs=3, cran=4)

    assert prop.price == pytest.approx(combos_service.product([1.5, 1.5, 1.5]))
    corps = recap_service.render(lot, settings)
    assert "3.38" in corps, "deux décimales, comme un bookmaker"


def test_le_document_ne_demande_plus_de_multiplication(migrated: Settings) -> None:
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "Alpha" in corps, "le rendu doit porter le lot"
    assert "produit des cotes" not in corps
    assert "sans arrondi de complaisance" not in corps


# --- Defaut 4 : trois contraintes qui different par leur regle -------------


def test_les_trois_contraintes_ne_different_pas_que_par_la_longueur(
    migrated: Settings,
) -> None:
    """Deux propositions qui ne different que par la longueur puisent dans le
    **meme vivier** : c'est une proposition et son extension, pas deux."""
    regles = set(recap_service.PROPOSALS)

    assert len(regles) == 3
    for gauche in regles:
        for droite in regles:
            if gauche is droite or gauche == droite:
                continue
            differences = sum(1 for a, b in zip(gauche, droite, strict=True) if a != b)
            assert differences >= 1
            assert not (gauche[0] != droite[0] and gauche[1:] == droite[1:]), (
                f"{gauche} et {droite} ne different que par la longueur"
            )


def test_le_recouvrement_se_declare(migrated: Settings) -> None:
    """`combos.jaccard` a deja tranche : impose par le vivier, il s'affiche et
    ne s'interdit pas."""
    settings = migrated
    for nom, heure in (
        ("Alpha", "17:00"),
        ("Bravo", "18:00"),
        ("Charlie", "19:00"),
        ("Delta", "20:00"),
        ("Echo", "21:00"),
    ):
        _pick(settings, _match(settings, nom, heure=heure), conf="4")

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "jambe(s) commune(s)" in corps


# --- Defaut 5 : l'heure, et ce qui en depend -------------------------------


def test_l_heure_de_generation_est_dans_l_en_tete(migrated: Settings) -> None:
    """Le vivier de la premiere proposition passait de 4 a l'aube a **0** a
    19:00 sur la journee du 28/08. Un document dont le contenu depend de l'heure
    sans le dire produit des observations qu'on croit structurelles."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    corps = recap_service.render(lot, settings)

    assert lot.rendered_at == MIDI
    assert "12:00" in corps
    assert "dépendent de cette heure" in corps


def test_le_titre_dit_auditer_et_non_composer(migrated: Settings) -> None:
    """L'application choisit les jambes et calcule le produit ; ce qui reste au
    modele est le seul jugement annonce."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(
        recap_service.build(_today(settings), settings, now=MIDI), settings
    )

    assert "Alpha" in corps, "le rendu doit porter le lot"
    assert "Compose trois combinés" not in corps


def test_la_contrainte_de_familles_lit_le_libelle_et_non_la_cle(
    migrated: Settings,
) -> None:
    """**Defaut trouve sur le rendu reel, pas par ce banc** — il est ecrit
    apres, et c'est une entorse a la regle.

    `market_key_effective` rend la cle du vocabulaire de rendu (`h2h`), que la
    table des familles ne connait pas : chaque jambe recevait une pseudo-famille
    unique et la contrainte ne mordait jamais. Le 28/08, `1N2` et `DC` — tous
    deux `issue` — ont ete retenus ensemble.
    """
    settings = migrated
    _pick(settings, _match(settings, "Alpha", heure="18:00"), conf="5", marche="1N2")
    _pick(
        settings,
        _match(settings, "Bravo", heure="19:00"),
        conf="5",
        marche="DC",
        selection="Domicile ou Match nul",
    )
    _pick(
        settings,
        _match(settings, "Charlie", heure="20:00"),
        conf="4",
        marche="O/U",
        selection="Plus de 2.5 buts",
    )
    _pick(
        settings,
        _match(settings, "Delta", heure="21:00"),
        conf="4",
        marche="Handicap",
        selection="Delta -0.5",
    )

    lot = recap_service.build(_today(settings), settings, now=MIDI)
    familles = next(p for p in lot.proposals if p.distinct_families)

    marches = [pick.market for pick in familles.chosen]
    assert marches, "la proposition doit être constructible"
    assert not ("1N2" in marches and "DC" in marches), "les deux sont de la famille « issue »"
