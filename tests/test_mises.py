"""La repartition de mise, et les gardes qui protegent la mesure d'analyse.

**Ce fichier porte deux choses de nature differente**, et c'est deliberе : les
proprietes arithmetiques de la table, et les gardes **structurelles** qui font
qu'aucun montant ne peut atteindre le residu au prix.

Les secondes se testent sur la **signature** et sur la **source**, jamais sur le
comportement : une fonction qui se contente de ne pas lire une cote pourrait la
lire demain, une fonction a qui la cote n'est pas passee ne le peut pas.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import stakes

SRC = Path(__file__).resolve().parents[1] / "src" / "myassistantbet"

#: La table servie. Ecrite ici plutot que lue des reglages : un test qui lit la
#: base testerait la fixture au lieu de la regle.
TABLE = stakes.Table(unite_bp=25, plafond_bp=500, combine_pct=50)


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# -- Les trois proprietes non negociables ------------------------------------


def test_la_mise_ne_peut_pas_dependre_de_la_cote_du_palier_ni_du_cran() -> None:
    """**La garde est la signature, pas le corps.**

    Le brief interdit de moduler une mise par la cote, le palier ou le cran de
    confiance. Une consigne se contourne ; un parametre absent non. Ce test
    verifie donc que ces axes ne sont **pas passes** a `plan()` — s'ils le
    devenaient, la garde tomberait sans qu'aucune assertion de comportement ne
    bouge.
    """
    parametres = set(inspect.signature(stakes.plan).parameters)
    interdits = {
        "price",
        "prix",
        "cote",
        "odds",
        "tier",
        "palier",
        "confidence",
        "confiance",
        "cran",
    }
    assert not (parametres & interdits), (
        "`plan()` reçoit un axe que la répartition n'a pas le droit de lire. "
        "La mise se dose sur une table, jamais sur un prix ni sur une note."
    )
    assert parametres == {"bankroll", "selections", "combines", "table_"}


def test_aucune_progression_possible_faute_de_parametre() -> None:
    """La mise ne monte pas apres une perte et ne descend pas apres un gain.

    Meme garde, et c'est pour elle qu'elle a ete choisie : `plan()` ne recoit
    aucun resultat, aucun historique, aucune session anterieure. Elle ne peut
    donc pas progresser, non parce qu'elle s'en abstient mais parce qu'elle n'en
    sait rien.
    """
    parametres = set(inspect.signature(stakes.plan).parameters)
    passe = {"result", "resultat", "history", "historique", "precedent", "solde", "streak"}
    assert not (parametres & passe)

    # Et la consequence, verifiee : deux appels identiques rendent la meme chose,
    # quel que soit ce qui s'est passe entre les deux.
    premier = stakes.plan(200.0, ["M1", "M2"], (), TABLE)
    second = stakes.plan(200.0, ["M1", "M2"], (), TABLE)
    assert [line.montant for line in premier.lines] == [line.montant for line in second.lines]


@pytest.mark.parametrize("selections", [0, 1, 12, 19, 20, 21, 29, 49, 140])
def test_le_plafond_est_un_refus_et_ne_se_franchit_jamais(selections: int) -> None:
    """**Le plafond tient, arrondi compris.**

    L'arrondi au plus proche le faisait franchir : 21 selections sur 200 EUR
    donnaient 10,08 pour un plafond a 10,00. Un plan de mise qui depasse son
    propre refus est exactement le defaut qu'on evite ici.
    """
    bankroll = 200.0
    plan = stakes.plan(bankroll, [f"M{i}" for i in range(selections)], (), TABLE)
    plafond = bankroll * TABLE.plafond_pct / 100.0
    assert plan.total <= plafond + 1e-9
    assert plan.accordees <= TABLE.plafond_unites + 1e-9


def test_la_reduction_est_annoncee_nommement_et_jamais_absorbee() -> None:
    """La garde demandee au §1a : combien d'unites demandees, combien accordees.

    Un rabot qui ne se nomme pas se lit comme une mise choisie — et le lot 14 a
    mesure ce que vaut un signal sans pouvoir de refus : vingt fois sur vingt.
    """
    plan = stakes.plan(200.0, [f"M{i}" for i in range(29)], (), TABLE)
    assert plan.reduit
    ligne = plan.reduction_line
    assert "29" in ligne and "20" in ligne, ligne
    assert "unités demandées" in ligne and "accordées" in ligne, ligne


def test_rien_ne_se_dit_quand_le_plafond_ne_mord_pas() -> None:
    """**Rien quand tout va bien.** Une ligne « réduction 0 % » à chaque session
    ferait chercher un rabot absent, et cesserait d'informer le jour où il y en
    a vraiment un."""
    plan = stakes.plan(200.0, [f"M{i}" for i in range(12)], (), TABLE)
    assert not plan.reduit
    assert plan.reduction_line == ""


# -- La table -----------------------------------------------------------------


def test_le_plafond_tombe_sur_un_compte_entier_de_selections() -> None:
    """L'arrondi de 0,245 a 0,25 n'est pas cosmetique : il fait tomber le
    plafond sur vingt selections, un nombre qui se verifie de tete."""
    assert TABLE.unite_pct == 0.25
    assert TABLE.plafond_pct == 5.0
    assert TABLE.plafond_unites == 20.0


def test_une_selection_exploratoire_ne_recoit_aucune_mise() -> None:
    """C-bis est produite sans fait date : lui mettre une mise paierait une
    information qu'on obtient sans payer.

    **Et elle ne figure pas non plus a 0,00 dans le plan** : une ligne a zero se
    lit comme une mise oubliee, pas comme une decision.
    """
    assert stakes.UNITES_EXPLORATOIRE == 0.0
    plan = stakes.plan(200.0, ["M1", "M2"], (), TABLE)
    assert [line.mark for line in plan.lines] == ["M1", "M2"]


def test_une_mise_sous_le_centime_est_nommee_et_non_rendue_muette() -> None:
    """Une bankroll trop petite pour le lot du jour rend des mises a 0,00.

    Le nommer plutot que le rendre est la regle du projet : un echec ne doit pas
    avoir la meme sortie que le cas ordinaire.
    """
    plan = stakes.plan(1.0, [f"M{i}" for i in range(20)], (), TABLE)
    assert len(plan.sous_le_centime) == 20
    normal = stakes.plan(200.0, [f"M{i}" for i in range(20)], (), TABLE)
    assert normal.sous_le_centime == ()


def test_le_combine_pese_une_demi_unite() -> None:
    plan = stakes.plan(200.0, ["M1"], ["combine_court"], TABLE)
    assert plan.demandees == 1.5
    montants = {line.mark: line.montant for line in plan.lines}
    assert montants["M1"] == 0.50
    assert montants["combine_court"] == 0.25


# -- La ligne `mises:` --------------------------------------------------------


def test_la_ligne_se_lit_et_distingue_l_absence_du_vide() -> None:
    """**Une sortie identique pour l'echec et pour le cas ordinaire** est le
    defaut caracteristique du projet : la ligne omise et la ligne vide rendent
    le meme dictionnaire, et ni la meme cause ni le meme correctif."""
    absente = stakes.read("Aucune ligne ici.")
    assert not absente.present
    assert absente.montants == {}

    vide = stakes.read("mises:")
    assert vide.present
    assert vide.montants == {}


def test_la_ligne_lit_la_bankroll_les_reperes_et_les_combines() -> None:
    lu = stakes.read("mises: bankroll=200 | M3=0.50 | M7=0.50 | combine_court=0.25")
    assert lu.bankroll == 200.0
    assert lu.montants == {"M3": 0.50, "M7": 0.50, "combine_court": 0.25}


def test_la_virgule_decimale_et_l_accent_ne_font_pas_echouer_la_lecture() -> None:
    """Un rendu francais ecrit `0,50`, et le collage peut rendre `combiné_court`.

    Refuser la ligne entiere pour un separateur ou un accent serait echouer pour
    la mauvaise raison — la lecon deja payee sur les tirets des scores en sets.
    """
    lu = stakes.read("mises: bankroll=150,00 | M1=0,37 | combiné_long=0,18")
    assert lu.bankroll == 150.0
    assert lu.montants == {"M1": 0.37, "combine_long": 0.18}


def test_l_ecart_entre_le_declare_et_le_recalcule_se_lit() -> None:
    """Ni l'un ni l'autre ne fait autorite : le recalcul s'ecrit, la declaration
    se garde a cote, et l'ecart est ce qui se lit — comme la cote declaree d'un
    combine et le cran annonce d'une selection."""
    plan = stakes.plan(200.0, ["M1", "M2"], (), TABLE)
    juste = stakes.read("mises: bankroll=200 | M1=0.50 | M2=0.50")
    assert stakes.gaps(juste, plan) == []

    faux = stakes.read("mises: bankroll=200 | M1=2.00 | M2=0.50")
    ecarts = stakes.gaps(faux, plan)
    assert [(e.mark, e.declare, e.propose) for e in ecarts] == [("M1", 2.00, 0.50)]


def test_un_repere_manquant_des_deux_cotes_ressort_comme_un_ecart() -> None:
    """Une selection oubliee de la ligne et une ligne inventee sont deux
    ecarts, et se rendent comme tels plutot que d'etre ignorees."""
    plan = stakes.plan(200.0, ["M1", "M2"], (), TABLE)
    partiel = stakes.read("mises: bankroll=200 | M1=0.50 | M9=0.50")
    marques = {e.mark for e in stakes.gaps(partiel, plan)}
    assert marques == {"M2", "M9"}


# -- La garde qui protege la mesure d'analyse --------------------------------

#: Les modules qui produisent la mesure d'analyse : residu au prix, crans,
#: intervalles, populations. **Aucun ne doit connaitre l'argent.**
MESURE = (
    "services/history.py",
    "services/inference.py",
    "services/confidence.py",
    "services/stats_export.py",
    "services/serve_stats.py",
    "services/research.py",
    "services/market_families.py",
)

#: Les **canaux** par lesquels un montant pourrait entrer dans un module de
#: mesure : lire une des deux tables d'argent, ou importer le module qui les
#: sert. Un mot suffisait dans une premiere version, et il etait faux — « mises »
#: est un mot francais courant, et `history.py` porte deja « l'application n'est
#: pas un carnet de mises » et « comptees, jamais mises en barre ». Un garde-fou
#: qui crie sur de la prose se fait desactiver.
ARGENT = (
    "mises",
    "bankroll_journee",
    "bankroll",
    "montant_joue",
    "montant_declare",
    "stakes",
)


def _code_seul(source: str) -> str:
    """La source, **commentaires et docstrings retires**.

    Le controle porte sur ce que le module *fait*, pas sur ce qu'il raconte. Le
    prosateur a le droit de nommer l'argent — c'est meme ce que fait la note qui
    explique pourquoi le suivi des paris est eteint.
    """
    arbre = ast.parse(source)
    for noeud in ast.walk(arbre):
        if not isinstance(
            noeud, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        corps = getattr(noeud, "body", [])
        if (
            corps
            and isinstance(corps[0], ast.Expr)
            and isinstance(corps[0].value, ast.Constant)
            and isinstance(corps[0].value.value, str)
        ):
            corps.pop(0)
    return ast.unparse(arbre)


@pytest.mark.parametrize("relatif", MESURE)
def test_aucun_montant_n_entre_dans_la_mesure_d_analyse(relatif: str) -> None:
    """**La garde qui protege la seule mesure que ce projet sache produire.**

    Le residu au prix compare des issues tranchees a des prix enregistres. Y
    meler des montants le rendrait ininterpretable — et c'est pour ca que le
    journal de mises vit dans ses propres tables plutot que dans une colonne de
    `picks`, que tout ce qui lit `picks` aurait pu ramasser par megarde.

    Le test lit la **source** : ce qui doit etre vrai est qu'aucun module de
    mesure ne touche a l'argent, et cette propriete se lit dans le texte du
    depot, pas dans l'etat d'un interpreteur.
    """
    code = _code_seul((SRC / relatif).read_text(encoding="utf-8"))
    fautifs = sorted(mot for mot in ARGENT if mot in code)
    assert not fautifs, (
        f"{relatif} touche à {fautifs} : un montant est en train d'entrer dans la "
        "mesure d'analyse. Le journal de mises et le résidu au prix sont deux "
        "journaux séparés, et leur séparation n'est tenue que par ce test."
    )


def test_le_journal_de_mises_ne_recopie_ni_la_cote_obtenue_ni_le_resultat() -> None:
    """Les deux vivent sur `picks` et se lisent par jointure.

    Une valeur recopiee diverge — le projet l'a paye sur le niveau d'une
    competition, la famille d'un marche et le palier d'une cote. Le journal ne
    porte donc que ce qui n'existe nulle part ailleurs : l'argent.
    """
    migration = (SRC / "migrations" / "065_journal_des_mises.sql").read_text(encoding="utf-8")
    schema = migration[migration.index("CREATE TABLE IF NOT EXISTS mises") :]
    schema = schema[: schema.index(");")]
    # Les commentaires SQL sont retires : ils **expliquent** pourquoi la cote
    # obtenue n'est pas recopiee, donc ils la nomment. Un controle qui les lit
    # echoue sur sa propre justification.
    schema = "\n".join(ligne.split("--")[0] for ligne in schema.splitlines())
    for colonne in ("price_real", "tier_real", "result", "resultat", "cote"):
        assert colonne not in schema, (
            f"`mises` porte une colonne `{colonne}` : elle recopie ce que `picks` "
            "sait déjà, et les deux divergeront."
        )


def test_la_garde_se_teste_contre_sa_propre_panne() -> None:
    """**Un garde-fou se teste contre sa propre panne**, regle de `CONTRIBUTING`.

    Sans ces deux moities, on ne saurait pas distinguer une garde qui marche
    d'une garde qui accepte tout — et la premiere version acceptait de la prose
    tout en refusant du code, ce qui est exactement l'inverse du contrat.
    """
    prose = '''
"""L'application n'est pas un carnet de mises, et la bankroll ne l'interesse pas."""
# mises en barre, bankroll, montant_joue : rien de tout cela n'est du code.
def taux() -> int:
    """Comptees, jamais mises en barre."""
    return 1
'''
    assert not [mot for mot in ARGENT if mot in _code_seul(prose)], (
        "La garde refuse de la prose : elle se ferait désactiver au premier "
        "commentaire qui nomme l'argent."
    )

    code = """
def taux(conn) -> int:
    return conn.execute("SELECT SUM(montant_joue) FROM mises").fetchone()[0]
"""
    assert [mot for mot in ARGENT if mot in _code_seul(code)], (
        "La garde accepte une lecture réelle du journal de mises : elle ne protège plus rien."
    )


# -- Le parcours reel ---------------------------------------------------------
#
# **Le service et sa surface se livrent ensemble, ou la regle qu'on croit poser
# n'est pas celle qui s'applique.** `add_pick` a accepte pendant deux jours un
# motif de saisie tardive que ni le formulaire ni la route ne transmettaient : la
# garde etait absolue sur le seul chemin qu'elle devait laisser ouvert, les tests
# du service passaient, et rien ne le disait. Ces tests postent donc le
# formulaire et **relisent la base**.


def _lot(settings: Settings, noms: list[str]) -> tuple[int, list[int]]:
    """Une session, ses matchs coches, et rien de plus."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.manual import build, save

    session_id, events = 0, []
    for nom in noms:
        event_id = save(
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
        events.append(event_id)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, events


def test_le_formulaire_ecrit_le_journal_des_mises_et_pas_picks(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le parcours entier**, du POST a la relecture des deux tables.

    Ce qui doit etre vrai est que l'argent atterrit dans `mises` et **nulle part
    ailleurs** : `picks.stake` reste vide, parce que c'est la colonne que tout
    ce qui lit `picks` aurait pu ramasser par megarde.
    """
    from myassistantbet.db import connect

    session_id, events = _lot(isolated_settings, ["Lyon", "Nice"])
    charge = {
        "journee": "2026-08-20",
        "bankroll": 200.0,
        "lignes": [
            {"index": 1, "unites": 1.0, "montant": 0.50, "declare": 0.50},
            {"index": 2, "unites": 1.0, "montant": 0.50, "declare": 2.00},
        ],
        "combines": [],
    }
    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "safe",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "price_1": "1.45",
            "event_1": str(events[0]),
            "keep_2": "on",
            "tier_2": "fun",
            "market_2": "O/U 2.5",
            "selection_2": "Over",
            "price_2": "1.95",
            "event_2": str(events[1]),
            "stakes": json.dumps(charge),
        },
    )
    assert response.status_code == 200

    with connect(isolated_settings) as conn:
        lignes = conn.execute(
            "SELECT journee, unites, montant, montant_declare FROM mises ORDER BY id"
        ).fetchall()
        stakes_sur_picks = conn.execute(
            "SELECT COUNT(*) AS n FROM picks WHERE stake IS NOT NULL"
        ).fetchone()["n"]
        bankroll = conn.execute(
            "SELECT montant FROM bankroll_journee WHERE journee = '2026-08-20'"
        ).fetchone()

    assert len(lignes) == 2, "le journal des mises n'a rien reçu du parcours réel"
    assert [row["montant"] for row in lignes] == [0.50, 0.50]
    # L'ecart declare/recalcule est **garde**, jamais aligne : c'est lui qui se lit.
    assert [row["montant_declare"] for row in lignes] == [0.50, 2.00]
    assert stakes_sur_picks == 0, "un montant a atteint `picks` — la séparation est rompue"
    assert bankroll is not None and bankroll["montant"] == 200.0


def test_une_seconde_ecriture_remplace_au_lieu_de_doubler(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Idempotence sur la selection visee.** Un second import du meme collage
    creerait sinon une seconde mise, et le plafond de la journee compterait deux
    fois le meme engagement."""
    from myassistantbet.db import connect
    from myassistantbet.services import stakes as service

    session_id, events = _lot(isolated_settings, ["Lyon"])
    from myassistantbet.services.history import add_pick

    pick_id = add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        event_id=str(events[0]),
        price="1.45",
        settings=isolated_settings,
    )
    for montant in (0.50, 0.40):
        service.record(
            "2026-08-20",
            session_id,
            [service.Entry(unites=1.0, montant=montant, pick_id=pick_id)],
            isolated_settings,
        )
    with connect(isolated_settings) as conn:
        lignes = conn.execute("SELECT montant FROM mises").fetchall()
    assert [row["montant"] for row in lignes] == [0.40]
    assert service.engaged_units("2026-08-20", isolated_settings) == 1.0


def test_les_unites_engagees_ferment_le_contournement_par_decoupage(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le plafond par journee ne se contourne pas en decoupant.**

    Un plafond par session aurait fait de la bonne pratique d'analyse — quatre
    prompts, quatre budgets de dossiers — un multiplicateur d'exposition. Chaque
    rendu annonce donc ce qu'il **reste**, pas le plafond nu.
    """
    from myassistantbet.services import stakes as service
    from myassistantbet.services.history import add_pick

    session_id, events = _lot(isolated_settings, ["Lyon"])
    pick_id = add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        event_id=str(events[0]),
        price="1.45",
        settings=isolated_settings,
    )
    brief = service.brief("2026-08-20", isolated_settings)
    assert brief.engagees == 0.0
    assert brief.restantes == "20"

    service.record(
        "2026-08-20",
        session_id,
        [service.Entry(unites=12.0, montant=6.0, pick_id=pick_id)],
        isolated_settings,
    )
    ensuite = service.brief("2026-08-20", isolated_settings)
    assert ensuite.engagees == 12.0
    assert ensuite.restantes == "8", (
        "le second rendu de la journée croit disposer du plafond entier : "
        "le découpage redevient un multiplicateur d'exposition"
    )


def test_la_section_g_se_paie_seulement_si_le_suivi_de_l_argent_est_ouvert(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**La porte n'est pas cosmétique : elle vaut 592 tokens de coût fixe.**

    Le préambule de ce projet ne paie que ce que le lot porte — les sports du
    lot, les libellés de contexte réellement rendus. Une section de répartition
    de mise rendue à qui ne mise pas serait exactement ce que ces portes
    existent pour éviter.

    Le nombre est écrit dans la note du réglage ; ce test vérifie que la porte
    l'économise vraiment, plutôt que de croire la note sur parole.
    """
    from myassistantbet.services.prompt import build_prompt
    from myassistantbet.services.thresholds import COUPON_TRACKING, save_toggle

    session_id, _ = _lot(isolated_settings, ["Lyon", "Nice"])

    ouvert = build_prompt(session_id, settings=isolated_settings).body
    assert "### G. Répartition de mise" in ouvert
    assert "BANKROLL DE SESSION" in ouvert

    save_toggle(COUPON_TRACKING, "0", isolated_settings)
    ferme = build_prompt(session_id, settings=isolated_settings).body

    assert "### G. Répartition de mise" not in ferme
    assert "BANKROLL DE SESSION" not in ferme
    # Et la porte économise vraiment : le renvoi ne reste pas seul derrière elle.
    assert "mises:" not in ferme
    assert len(ouvert) > len(ferme) + 1500, (
        "la porte ne retire presque rien : soit la section a fondu, soit elle "
        "n'est plus gardée là où elle coûte"
    )


def test_l_etat_de_la_journee_lit_la_bankroll_et_ne_projette_rien(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le lecteur de `bankroll_journee`.** Sans lui la table serait écrite et
    jamais relue — la faute exacte de `/players/squads`, collecté des mois sans
    lecteur et retiré par la migration 022.

    Et il rend un **état**, jamais une projection : aucun solde attendu, aucun
    objectif, aucune tendance. Une projection supposerait une espérance de gain.
    """
    from myassistantbet.services import stakes as service
    from myassistantbet.services.history import add_pick

    session_id, events = _lot(isolated_settings, ["Lyon"])
    pick_id = add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        event_id=str(events[0]),
        price="1.45",
        settings=isolated_settings,
    )

    # Rien engagé : rien à dire.
    assert service.day_state("2026-08-20", isolated_settings).line == ""

    service.set_bankroll("2026-08-20", 200.0, settings=isolated_settings)
    service.record(
        "2026-08-20",
        session_id,
        [service.Entry(unites=4.0, montant=2.0, pick_id=pick_id)],
        isolated_settings,
    )
    etat = service.day_state("2026-08-20", isolated_settings)

    assert etat.bankroll == 200.0, "la bankroll enregistrée n'est jamais relue"
    assert etat.unites == 4.0
    assert etat.restantes == 16.0
    assert etat.part_bankroll == pytest.approx(1.0)
    assert "4 unité(s) engagée(s) sur 20" in etat.line

    # **Aucun mot de projection.** Le vocabulaire est celui d'un relevé.
    plat = etat.line.lower()
    for interdit in ("attendu", "objectif", "espérance", "gain", "tendance", "prévu"):
        assert interdit not in plat


def test_aucune_fonction_du_module_de_mise_n_est_sans_lecteur() -> None:
    """**Une donnée sans lecteur ne se collecte pas**, et une fonction sans
    appelant non plus.

    `/players/squads` a été collecté des mois sans que rien ne le lise, et son
    retrait a coûté une migration. Ce test ferme la porte du même côté : toute
    fonction publique de `stakes` doit être appelée quelque part.
    """
    import ast

    source = (SRC / "services" / "stakes.py").read_text(encoding="utf-8")
    publiques = {
        n.name
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
    }
    appels: set[str] = set()
    for chemin in list(SRC.rglob("*.py")):
        texte = chemin.read_text(encoding="utf-8")
        arbre = ast.parse(texte)
        interne = chemin.name == "stakes.py"
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            cible = noeud.func
            if isinstance(cible, ast.Attribute):
                appels.add(cible.attr)
            elif isinstance(cible, ast.Name) and interne:
                appels.add(cible.id)
    orphelines = sorted(publiques - appels)
    assert not orphelines, (
        f"Ces fonctions de `stakes` n'ont aucun appelant : {orphelines}. "
        "Une fonction sans lecteur se retire ou reçoit sa surface — c'est la "
        "leçon de `/players/squads`, retiré par la migration 022."
    )


def test_l_unite_de_mise_porte_son_caractere_provisoire_et_son_echeance() -> None:
    """**Un « provisoire » non daté devient permanent par oubli.**

    L'unité de 0,25 % a été mesurée sur quatre journées d'analyse, quand un 90e
    centile défendable en demande une dizaine. Elle doit donc porter, à côté du
    champ et non noyé dans sa note, sur quoi elle a été mesurée et quand elle
    doit l'être à nouveau.
    """
    from myassistantbet.services.thresholds import THRESHOLDS

    unite = THRESHOLDS["mise_unite_bp"]
    assert unite.provisional, "l'unité est mesurée sur quatre journées : elle est provisoire"
    assert "4 journées" in unite.measured_on
    assert unite.remeasure_on == "2026-09-20"

    # Le plafond, lui, est un arbitrage et non une grandeur observée.
    assert not THRESHOLDS["mise_plafond_bp"].provisional


def test_l_echeance_est_visible_sur_la_page_des_reglages(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Elle ne vaut que si elle se voit : une échéance dans un commentaire de
    code n'a jamais rappelé personne."""
    page = client.get("/settings").text
    assert "provisoire" in page
    assert "2026-09-20" in page
    assert "4 journées" in page


def test_l_echeance_entre_au_journal_des_mesures(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le journal des mesures est le seul endroit du produit qui porte des
    **dates** plutôt que des états — donc le seul où une échéance ne se perd
    pas."""
    from myassistantbet.services.changelog import journal

    entrees = {e.day: e.label for e in journal(isolated_settings).entries}
    assert "2026-09-20" in entrees
    assert "re-mesurer l'unité de mise" in entrees["2026-09-20"]
