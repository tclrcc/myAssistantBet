"""Le combine comme objet d'analyse.

Quatre risques propres a ce module, chacun avec son test : melanger un combine
d'analyse avec un pari pose, assembler des jambes que rien n'a jamais comparees
entre elles, croire la cote ecrite par le modele, et laisser un recouvrement
passer inapercu.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import combos as combos_service
from myassistantbet.services import picks_import
from myassistantbet.services.history import add_pick, list_picks, set_result
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import RenderedPrompt, save_prompt


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str) -> int:
    return save(
        build(
            "football",
            "Amical",
            nom,
            f"Adversaire {nom}",
            "2099-01-01",
            "20:45",
            f"{nom} 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _lot(settings: Settings, noms: list[str]) -> tuple[int, list[int]]:
    """Une session, ses matchs coches, et rien de plus."""
    session_id, events = 0, []
    for nom in noms:
        event_id = _match(settings, nom)
        events.append(event_id)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, events


def _prompt(settings: Settings, session_id: int, events: list[int]) -> int:
    """Un prompt archive portant ces matchs — c'est lui qui fait le lot."""
    return save_prompt(
        session_id,
        RenderedPrompt(
            template_name="test.md.j2",
            body="### M1 · football · Amical · A – B · 20:45",
            blocks=len(events),
            event_ids=events,
        ),
        settings,
    )


def _pick(settings: Settings, session_id: int, event_id: int, price: float, conf: int) -> int:
    return add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Domicile",
        event_id=str(event_id),
        price=str(price),
        confidence=str(conf),
        settings=settings,
    )


# -- La separation d'avec les paris poses -----------------------------------


def test_un_combine_ne_marque_aucune_selection_jouee(migrated: Settings) -> None:
    """**Le controle central du module.** Le suivi des paris poses est retire le
    28/08/2026 ; `played` et `coupon_id` restent en base, orphelines et vides.
    Un combine d'analyse ne doit y toucher ni maintenant ni si le suivi revenait
    — il regroupe des selections, il ne dit pas ce qui a ete pose."""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [_pick(migrated, session_id, event, 1.45, 4) for event in events]

    combos_service.record(session_id, prompt_id, kind="court", pick_ids=picks, settings=migrated)

    assert len(list_picks(session_id, migrated)) == 3, "les selections sont bien la"
    with connect(migrated) as conn:
        assert conn.execute("SELECT COUNT(*) FROM coupons").fetchone()[0] == 0
        laissees = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE played = 1 OR coupon_id IS NOT NULL"
        ).fetchone()[0]
    assert laissees == 0, "un combine d'analyse n'ecrit aucune colonne de pari pose"


def test_aucun_champ_financier_sur_un_combine(migrated: Settings) -> None:
    """Meme garde que sur `Coupon` : la section 9 interdit tout indicateur
    financier, et une mise sur un objet d'analyse n'aurait aucun sens."""
    interdits = {"stake", "mise", "profit", "roi", "bankroll", "gain"}
    for classe in (combos_service.Combo, combos_service.Leg):
        noms = {field.name for field in fields(classe)}
        assert not (noms & interdits), f"{classe.__name__} porte un champ financier"


# -- La contrainte de prompt ------------------------------------------------


def test_une_jambe_d_un_autre_prompt_est_refusee(migrated: Settings) -> None:
    """Les selections de deux prompts n'ont jamais ete comparees entre elles :
    chaque instance a choisi dans son lot, avec son quota et son budget propres.
    Mesure du 14/08/2026 — un match est rendu 2,23 fois en moyenne dans sa
    session, jusqu'a 13 fois : deux jambes venues de deux prompts sur le meme
    match seraient deux tirages du meme match presentes comme deux selections."""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims", "Brest"])
    premier = _prompt(migrated, session_id, events[:2])
    second = _prompt(migrated, session_id, events[2:])

    ici = [_pick(migrated, session_id, event, 1.45, 4) for event in events[:2]]
    ailleurs = _pick(migrated, session_id, events[2], 1.60, 4)

    # Le lot du premier prompt passe.
    combos_service.record(session_id, premier, kind="court", pick_ids=ici, settings=migrated)

    with pytest.raises(combos_service.ComboError, match="autre prompt"):
        combos_service.record(
            session_id, premier, kind="long", pick_ids=[*ici, ailleurs], settings=migrated
        )
    # Et la meme jambe passe sous **son** prompt : c'est le rattachement qui est
    # refuse, pas la selection.
    combos_service.record(session_id, second, kind="court", pick_ids=[ailleurs], settings=migrated)


def test_un_prompt_d_une_autre_session_est_refuse(migrated: Settings) -> None:
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    # `current_session` en rend une par jour local : la seconde s'ecrit donc a
    # la main, sans quoi les deux lots partagent la meme et le test ne teste
    # rien de ce qu'il annonce.
    with connect(migrated) as conn:
        autre_session = int(
            conn.execute(
                "INSERT INTO sessions (label, created_at) VALUES ('veille', '2026-01-01T00:00:00Z')"
            ).lastrowid
        )
    etranger = _prompt(migrated, autre_session, events)
    pick = _pick(migrated, session_id, events[0], 1.45, 4)

    with pytest.raises(combos_service.ComboError, match="session"):
        combos_service.record(
            session_id, etranger, kind="court", pick_ids=[pick], settings=migrated
        )


# -- La cote se recalcule ---------------------------------------------------


def test_la_cote_se_recalcule_depuis_les_jambes(migrated: Settings) -> None:
    """Le produit ecrit dans la reponse est une affirmation du modele ; celui-ci
    est une consequence des prix enregistres. Les deux se gardent, et c'est leur
    ecart qui se lit."""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims"])
    prompt_id = _prompt(migrated, session_id, events)
    prix = [1.45, 1.60, 1.95]
    picks = [
        _pick(migrated, session_id, event, valeur, 4)
        for event, valeur in zip(events, prix, strict=True)
    ]

    combos_service.record(
        session_id,
        prompt_id,
        kind="long",
        pick_ids=picks,
        declared_price=9.99,
        target_price=4,
        stop_reason="cible",
        settings=migrated,
    )
    combo = combos_service.list_for_session(session_id, migrated)[0]

    assert combo.computed_price == pytest.approx(math.prod(prix))
    assert combo.declared_price == 9.99
    assert combo.price_mismatch, "un ecart aussi gros doit se voir"
    assert combo.target_reached is True
    assert combo.stop_label == "cible atteinte"


def test_une_jambe_sans_prix_rend_la_cote_incalculable(migrated: Settings) -> None:
    """Et non un produit partiel : celui-ci serait plus bas que le vrai, sans que
    rien ne le dise. Meme regle que partout — en cas de doute, rien."""
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    prompt_id = _prompt(migrated, session_id, events)
    avec = _pick(migrated, session_id, events[0], 1.45, 4)
    sans = add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Domicile",
        event_id=str(events[1]),
        price="",
        confidence="4",
        settings=migrated,
    )

    combos_service.record(
        session_id, prompt_id, kind="court", pick_ids=[avec, sans], settings=migrated
    )
    combo = combos_service.list_for_session(session_id, migrated)[0]

    assert combo.computed_price is None
    assert combo.price_gap is None
    assert combo.target_reached is None


# -- Ce qui remplace le maillon fragile -------------------------------------


def test_la_repartition_par_palier_dit_ou_la_cote_a_ete_achetee(migrated: Settings) -> None:
    """Ce n'est pas un jugement sur la solidite des jambes — rien dans
    l'historique ne permettrait d'en porter un. C'est la structure, exposee, et
    le lecteur juge. Elle se calcule sans rien demander au modele."""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [
        _pick(migrated, session_id, events[0], 1.45, 4),
        _pick(migrated, session_id, events[1], 1.50, 3),
        add_pick(
            session_id,
            tier="fun",
            market="O/U 2.5",
            selection="Over",
            event_id=str(events[2]),
            price="1.95",
            confidence="3",
            settings=migrated,
        ),
    ]
    combos_service.record(session_id, prompt_id, kind="long", pick_ids=picks, settings=migrated)
    combo = combos_service.list_for_session(session_id, migrated)[0]

    repartition = {cle: (compte, apport) for cle, _label, compte, apport in combo.by_tier()}
    assert repartition["safe"][0] == 2
    assert repartition["safe"][1] == pytest.approx(1.45 * 1.50)
    assert repartition["fun"] == (1, pytest.approx(1.95))

    assert combo.confidence_min == 3
    assert combo.confidence_median == 3


def test_le_rang_de_la_premiere_jambe_perdue(migrated: Settings) -> None:
    """La seule mesure qui garde un sens sur un combine long : son taux de
    reussite ne sera jamais mesurable — une fois sur 280 au taux de jambe
    constate — mais le rang de la premiere perdante, si. C'est l'ordre d'ajout
    qui le porte, d'ou `combo_legs.position`."""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [_pick(migrated, session_id, event, 1.45, 4) for event in events]
    combos_service.record(session_id, prompt_id, kind="long", pick_ids=picks, settings=migrated)

    set_result(picks[0], "win", settings=migrated)
    set_result(picks[1], "loss", settings=migrated)

    combo = combos_service.list_for_session(session_id, migrated)[0]
    assert combo.first_loss_rank == 2
    assert combo.legs_won == 1
    assert combo.legs_settled == 2


def test_aucune_jambe_perdue_ne_se_confond_avec_aucun_resultat(migrated: Settings) -> None:
    """`first_loss_rank` a None pour les deux, donc il se lit avec `legs_settled`
    — meme regle que partout : une sortie identique pour deux etats differents
    est le defaut que ce projet corrige."""
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [_pick(migrated, session_id, event, 1.45, 4) for event in events]
    combos_service.record(session_id, prompt_id, kind="court", pick_ids=picks, settings=migrated)

    combo = combos_service.list_for_session(session_id, migrated)[0]
    assert combo.first_loss_rank is None and combo.legs_settled == 0

    set_result(picks[0], "win", settings=migrated)
    set_result(picks[1], "win", settings=migrated)
    combo = combos_service.list_for_session(session_id, migrated)[0]
    assert combo.first_loss_rank is None and combo.legs_settled == 2


# -- Le recouvrement ---------------------------------------------------------


def test_le_recouvrement_se_calcule_et_ne_s_interdit_pas(migrated: Settings) -> None:
    """Sur la moitie des sessions mesurees, un court et un long disjoints sont
    structurellement impossibles : il faudrait 14 matchs distincts et le vivier
    n'en offre que 5 a 11. Le recouvrement est donc **impose par le vivier**, et
    ce qui est interdit est qu'il passe inapercu."""
    session_id, events = _lot(migrated, ["A", "B", "C", "D", "E"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [_pick(migrated, session_id, event, 1.45, 4) for event in events]

    # Le court est inclus dans le long, comme il l'est par construction quand
    # les deux se batissent par tri decroissant de surete : J = 2/5 = 0,40.
    combos_service.record(
        session_id, prompt_id, kind="court", pick_ids=picks[:2], settings=migrated
    )
    combos_service.record(session_id, prompt_id, kind="long", pick_ids=picks, settings=migrated)

    paires = combos_service.overlaps(combos_service.list_for_session(session_id, migrated))
    assert len(paires) == 1
    assert paires[0].shared == 2
    assert paires[0].index == pytest.approx(0.4)
    assert "J = 0.40" in paires[0].line

    # Rien n'a ete refuse : les deux combines existent.
    assert len(combos_service.list_for_session(session_id, migrated)) == 2


def test_deux_combines_disjoints_ne_produisent_aucune_paire(migrated: Settings) -> None:
    """Une paire disjointe ne figure pas dans le releve : ce qui doit se voir est
    le recouvrement, pas son absence."""
    session_id, events = _lot(migrated, ["A", "B", "C", "D"])
    prompt_id = _prompt(migrated, session_id, events)
    picks = [_pick(migrated, session_id, event, 1.45, 4) for event in events]

    combos_service.record(
        session_id, prompt_id, kind="court", pick_ids=picks[:2], settings=migrated
    )
    combos_service.record(session_id, prompt_id, kind="long", pick_ids=picks[2:], settings=migrated)

    assert combos_service.overlaps(combos_service.list_for_session(session_id, migrated)) == []


# -- La lecture du bloc ------------------------------------------------------


def test_le_bloc_combine_se_lit() -> None:
    reading = combos_service.read_combos(
        '```combo\n{"type": "long", "jambes": ["M3", "M7"], "cote": 2.8, '
        '"cible": 100, "arret": "confiance"}\n```'
    )
    assert not reading.rejected
    combo = reading.combos[0]
    assert combo.kind == "long"
    assert combo.marks == ("M3", "M7")
    assert combo.declared_price == 2.8
    assert combo.stop_reason == "confiance"


@pytest.mark.parametrize(
    "corps",
    [
        '{"type": "moyen", "jambes": ["M1"]}',
        '{"type": "long", "jambes": []}',
        '{"type": "long", "jambes": ["M1", "M1"]}',
        '{"type": "long", "jambes": ["M1"], "arret": "fatigue"}',
        '{"type": "long", "jambes": ["M1"], "cote": "beaucoup"}',
    ],
)
def test_un_bloc_combine_illisible_est_dit_et_ne_coute_que_lui(corps: str) -> None:
    """Un motif hors vocabulaire est **refuse** plutot qu'ecrit : `load_for`
    l'ignorerait, et il paraitrait pose sans aucun effet — exactement le silence
    qu'on corrige."""
    reading = combos_service.read_combos(f"```combo\n{corps}\n```")
    assert not reading.combos
    assert len(reading.rejected) == 1


# -- Le parcours reel --------------------------------------------------------
#
# Le service et sa surface se livrent ensemble, ou la regle qu'on croit poser
# n'est pas celle qui s'applique : `add_pick` acceptait un motif de saisie
# tardive que ni le formulaire ni la route ne transmettaient, et la garde etait
# absolue sur le seul chemin qu'elle devait laisser ouvert. Ces tests postent
# donc le formulaire et **relisent la base**.


def test_le_formulaire_enregistre_un_combine(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, events = _lot(isolated_settings, ["Lyon", "Nice"])
    prompt_id = _prompt(isolated_settings, session_id, events)

    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "safe",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "price_1": "1.45",
            "confidence_1": "4",
            "event_1": str(events[0]),
            "keep_2": "on",
            "tier_2": "fun",
            "market_2": "O/U 2.5",
            "selection_2": "Over",
            "price_2": "1.95",
            "confidence_2": "3",
            "event_2": str(events[1]),
            "combo_1": json.dumps(
                {
                    "kind": "long",
                    "rows": [1, 2],
                    "prompt_id": prompt_id,
                    "declared": 2.83,
                    "target": 2,
                    "stop": "confiance",
                }
            ),
        },
    )

    assert response.status_code == 200
    combos = combos_service.list_for_session(session_id, isolated_settings)
    assert len(combos) == 1
    assert [leg.position for leg in combos[0].legs] == [0, 1]
    assert combos[0].computed_price == pytest.approx(1.45 * 1.95)
    assert combos[0].stop_reason == "confiance"
    # Et aucune selection n'a recu de colonne de pari pose.
    with connect(isolated_settings) as conn:
        laissees = conn.execute(
            "SELECT COUNT(*) FROM picks WHERE played = 1 OR coupon_id IS NOT NULL"
        ).fetchone()[0]
    assert laissees == 0


def test_un_combine_ampute_est_dit_et_non_tronque(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Une jambe decochee retire une jambe du combine, et un combine ampute
    porterait une cote que rien ne justifie. Il est dit, jamais tronque en
    silence — meme regle que partout : ce qui manque se nomme."""
    session_id, events = _lot(isolated_settings, ["Lyon", "Nice"])
    prompt_id = _prompt(isolated_settings, session_id, events)

    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "safe",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "price_1": "1.45",
            "event_1": str(events[0]),
            # La ligne 2 est presente mais **non cochee** : le combine la reclame.
            "tier_2": "fun",
            "market_2": "O/U 2.5",
            "selection_2": "Over",
            "price_2": "1.95",
            "event_2": str(events[1]),
            "combo_1": json.dumps({"kind": "long", "rows": [1, 2], "prompt_id": prompt_id}),
        },
    )

    assert response.status_code == 200
    assert combos_service.list_for_session(session_id, isolated_settings) == []
    assert "non enregistré" in response.text
    assert "1 de ses 2 jambes" in response.text


def test_les_blocs_de_confiance_et_de_combine_ne_se_mangent_pas() -> None:
    """Deux familles de blocs dans le meme collage. `confidence.BLOCK` ne lit que
    `conf` et `json` ; celui-ci ne lit que `combo`."""
    from myassistantbet.services.confidence import read_blocks

    rendu = (
        '```conf\n{"match": "M1", "confiance": 4, "type": "issue", '
        '"source_level": "lecture", "faits": [], "manque_touche_facteur": false}\n```\n'
        '```combo\n{"type": "court", "jambes": ["M1"]}\n```'
    )
    assert len(read_blocks(rendu).claims) == 1
    assert not read_blocks(rendu).rejected
    assert len(combos_service.read_combos(rendu).combos) == 1


# -- Le rattachement ne depend plus des blocs de confiance -------------------
#
# **Defaut en cascade.** Un repere de jambe se resolvait par `pick.claim.match`,
# si bien qu'un collage sans blocs — le cas de **toutes** les sessions de la
# base au 17/08/2026 — perdait aussi ses combines, alors que le prompt archive
# porte deja la table `M3 → affiche` qu'il faut. Un chemin d'ingestion qui tombe
# parce qu'un autre est tombe cumule deux silences pour une seule cause.


def _prompt_nomme(settings: Settings, session_id: int, events: list[int], noms: list[str]) -> int:
    """Un prompt archive dont les en-tetes nomment vraiment chaque match."""
    corps = "\n".join(
        f"### M{index} · football · Amical · {nom} – Adversaire {nom} · 01/01 20:45"
        for index, nom in enumerate(noms, start=1)
    )
    return save_prompt(
        session_id,
        RenderedPrompt(
            template_name="test.md.j2", body=corps, blocks=len(events), event_ids=events
        ),
        settings,
    )


def test_un_combine_se_rattache_sans_aucun_bloc_de_confiance(migrated: Settings) -> None:
    noms = ["Lyon", "Nice"]
    session_id, events = _lot(migrated, noms)
    _prompt_nomme(migrated, session_id, events, noms)
    rendu = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adversaire Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
        "| 2 | Nice – Adversaire Nice | 1N2 | Nice | 1.60 | 🟢 SAFE |\n"
        '\n```combo\n{"type": "court", "jambes": ["M1", "M2"], "cote": 2.32}\n```\n'
    )

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert len(preview.combos) == 1, "le prompt archivé suffit à résoudre les repères"
    assert preview.combos[0].rows == [1, 2]
    assert preview.combos[0].computed_price == pytest.approx(2.32)


def test_un_combine_sans_cloture_est_lu_lui_aussi(migrated: Settings) -> None:
    """Meme reparation que pour les blocs de confiance : un copier-coller depuis
    le rendu consomme la cloture, et le JSON arrive nu."""
    reading = combos_service.read_combos(
        'Voici le combiné.\n\n{"type": "long", "jambes": ["M3", "M7"], "cote": 2.8}\n\nEt la suite.'
    )

    assert [combo.marks for combo in reading.combos] == [("M3", "M7")]


def test_un_bloc_de_confiance_n_est_pas_lu_comme_un_combine() -> None:
    """Les deux familles portent `type` : c'est `jambes` qui tranche."""
    bloc = (
        '{"match": "M1", "confiance": 4, "type": "issue", "source_level": "lecture", "faits": []}'
    )

    assert not combos_service.read_combos(bloc).combos
