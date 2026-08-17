"""L'export de la page de statistiques, et sa parite avec l'ecran.

Le risque propre a ce module n'est pas de mal ecrire un fichier : c'est d'en
ecrire un qui **dise autre chose que la page**. Un export relu ailleurs, dont un
chiffre differerait de celui affiche, serait pire que pas d'export du tout — la
page resterait la reference et le fichier deviendrait un piege.

Deux garanties, donc, et ce sont les deux criteres d'acceptation :

- **aucune section rendue a l'ecran n'est absente du fichier** — la parite se
  verifie sur les deux rendus reels, jamais sur une table de correspondance qui
  aurait vieilli de son cote ;
- **aucun ecart de valeur** : les taux, leurs denominateurs et leurs intervalles
  sont ceux des memes objets.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html import unescape

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import combos as combos_service
from myassistantbet.services import history as history_service
from myassistantbet.services import set_scores as set_scores_service
from myassistantbet.services import stats_export
from myassistantbet.services import thresholds as thresholds_service
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt, save_prompt

from .helpers import lot_avec_recul


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _lot_complet(settings: Settings) -> int:
    """Un releve qui allume le plus de sections possible.

    Un lot minimal ferait passer la parite pour de mauvaises raisons : les
    sections conditionnelles — angle, source, famille de marche, score en sets —
    seraient absentes des **deux** cotes, et le test ne comparerait rien.
    """
    session_id = lot_avec_recul(settings)

    # Les deux axes qui disent **sur quoi** la selection reposait. Ils n'ont
    # aucun point de capture dans la fixture de base : sans eux, deux cartes de
    # la page ne se rendent pas.
    for index, (angle, source) in enumerate(
        (("issue", "1"), ("maniere", "3"), ("maniere", "lecture"), ("issue", "2"))
    ):
        pick_id = history_service.add_pick(
            session_id,
            tier="safe",
            market="1N2" if angle == "issue" else "Handicap",
            selection=f"Angle {index}",
            price="1.50",
            confidence="4",
            angle=angle,
            source_level=source,
            settings=settings,
        )
        history_service.set_result(pick_id, "win" if index % 2 else "loss", settings)

    # Une session de tennis avec son prompt archive : c'est elle qui donne un
    # lot, donc un taux de selection, donc un score en sets a relever.
    event_id = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Moutet",
            "Bergs",
            "2099-08-04",
            "20:45",
            "Moutet 1.85\nBergs 1.95",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    tennis_id = board_service.toggle_selection(event_id, True, settings)
    prompt_id = save_prompt(tennis_id, build_prompt(tennis_id, settings=settings), settings)
    set_scores_service.save(tennis_id, event_id, "2-0", actual="2-1", settings=settings)

    # Le second circuit : une selection produite **sans fait date**, comptee a
    # part de tout le reste. Sans elle, le bloc exploratoire est absent des deux
    # cotes et la parite ne compare rien.
    exploratoire = history_service.add_pick(
        tennis_id,
        tier="giga_fun",
        market="Vainqueur",
        selection="Bergs",
        event_id=str(event_id),
        price="7.50",
        confidence="1",
        exploratory=True,
        settings=settings,
    )
    history_service.set_result(exploratoire, "loss", settings)

    # Un combine, pour que son bloc se rende lui aussi.
    jambe = history_service.add_pick(
        tennis_id,
        tier="safe",
        market="Vainqueur",
        selection="Moutet",
        event_id=str(event_id),
        price="1.85",
        confidence="4",
        independence_note="angle distinct du précédent",
        settings=settings,
    )
    combos_service.record(
        tennis_id, prompt_id, kind="court", pick_ids=[jambe], declared_price=1.85, settings=settings
    )

    # Le suivi des paris poses : **desactive par defaut**, donc son bloc est
    # absent d'une page ordinaire. La parite doit pouvoir le comparer.
    thresholds_service.save_toggle(thresholds_service.COUPON_TRACKING, "1", settings)
    return session_id


_HEADING = re.compile(r"<(h2|h3)[^>]*>(.*?)</\1>", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def _page_sections(html: str) -> list[stats_export.Section]:
    """Les sections **reellement rendues** par la page, dans l'ordre.

    Un `h2` ouvre un bloc, les `h3` qui suivent lui appartiennent. C'est le
    couple qui identifie une section : deux blocs portent une carte « Par
    palier », et seul leur bloc les distingue.
    """
    found: list[stats_export.Section] = []
    bloc = ""
    for niveau, brut in _HEADING.findall(html):
        # Jinja echappe l'apostrophe : « Par type d&#39;angle ». Comparer le
        # texte echappe ferait echouer la parite pour une raison typographique,
        # c'est-a-dire pour la mauvaise.
        titre = " ".join(unescape(_TAGS.sub(" ", brut)).split())
        if niveau == "h2":
            bloc = titre
            section = stats_export.Section("", titre)
        else:
            section = stats_export.Section(bloc, titre)
        if section not in found:
            found.append(section)
    return found


def _markdown_blocks(text: str) -> dict[str, list[str]]:
    """Le fichier decoupe en blocs `##`, et les titres `###` de chacun."""
    blocs: dict[str, list[str]] = {}
    courant = ""
    for line in text.splitlines():
        if line.startswith("## "):
            courant = line[3:].strip()
            blocs.setdefault(courant, [])
        elif line.startswith("### ") and courant:
            blocs[courant].append(line[4:].strip())
    return blocs


def test_chaque_section_de_la_page_a_son_equivalent_dans_le_fichier(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le critere d'acceptation du chantier.**

    La parite se lit sur les deux rendus reels : une carte ajoutee a la page et
    oubliee dans l'export fait tomber ce test, et c'est le seul defaut que
    l'export puisse produire sans que rien d'autre ne bronche — le fichier
    resterait valide, simplement amputé.
    """
    _lot_complet(isolated_settings)

    page = client.get("/stats")
    fichier = client.get("/api/stats/export?format=md")
    assert page.status_code == 200
    assert fichier.status_code == 200

    sections = _page_sections(page.text)
    blocs = _markdown_blocks(fichier.text)
    # Une page qui ne rendrait qu'un titre ferait passer la parite sans rien
    # comparer : le lot doit allumer les sept blocs.
    assert len([section for section in sections if not section.block]) == 7

    manquantes = []
    for section in sections:
        if section.title == "Statistiques":
            continue
        if not section.block:
            if section.title not in blocs:
                manquantes.append(section)
        elif section.title not in blocs.get(section.block, []):
            manquantes.append(section)
    assert manquantes == [], f"sections de la page sans equivalent dans le fichier : {manquantes}"


def test_le_registre_couvre_ce_que_la_page_rend(
    client: TestClient, isolated_settings: Settings
) -> None:
    """`SECTIONS` est le contrat, et il doit suivre la page.

    Sans ce test, une carte nouvelle passerait la parite ci-dessus le jour ou
    elle serait ajoutee aux deux cotes — et le registre, lui, resterait faux
    pour tout autre lecteur.
    """
    _lot_complet(isolated_settings)
    rendues = {
        section
        for section in _page_sections(client.get("/stats").text)
        if section.title != "Statistiques"
    }
    assert rendues <= set(stats_export.SECTIONS)
    # Et l'inverse : ce que le releve declare rendre est bien ce que la page rend.
    assert set(stats_export.report(isolated_settings).sections) == rendues


def test_aucun_ecart_entre_les_taux_de_la_page_et_ceux_du_fichier(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Ecart attendu nul, ligne a ligne, sur les deux ecritures.

    Le JSON est compare aux **objets** de l'analyse — c'est la que se lirait un
    recalcul divergent — et le Markdown au meme couple `gagnees/tranchees`.
    """
    _lot_complet(isolated_settings)
    analysis = history_service.analysis(isolated_settings)
    export = client.get("/api/stats/export?format=json").json()
    markdown = client.get("/api/stats/export?format=md").text

    axes = {
        "by_confidence": analysis.by_confidence,
        "by_tier": analysis.by_tier,
        "by_sport": analysis.by_sport,
        "by_angle": analysis.by_angle,
        "by_source": analysis.by_source,
    }
    for cle, rows in axes.items():
        assert rows, f"l'axe {cle} ne porte aucune ligne : le test ne compare rien"
        assert len(export["groups"][cle]) == len(rows)
        for rendu, row in zip(export["groups"][cle], rows, strict=True):
            assert rendu["label"] == row.label
            assert (rendu["won"], rendu["settled"]) == (row.won, row.settled)
            assert rendu["rate"] == row.rate
            assert rendu["interval"] == (list(row.interval) if row.interval else None)
            assert f"| {row.won} | {row.settled} | {row.rate_label} " in markdown

    assert export["meta"]["settled"] == analysis.settled
    assert export["meta"]["recorded"] == analysis.recorded
    assert export["totals"]["overall"]["won"] == analysis.overall.won
    assert export["residual"]["antecedence"]["observed"] == analysis.residual.observed
    assert export["residual"]["antecedence"]["expected"] == analysis.residual.expected


def test_chaque_taux_du_fichier_porte_son_denominateur_et_son_intervalle(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**C'est la raison d'etre de l'export.** A l'ecran l'effectif est a cote de
    la barre et l'intervalle est dessine dessus ; hors de la page, un pourcentage
    seul n'est pas lisible."""
    _lot_complet(isolated_settings)
    markdown = client.get("/api/stats/export?format=md").text

    # Les tableaux de taux se reconnaissent a leur en-tete : les autres — la
    # matrice des scores, la repartition des etiquettes — comptent autre chose
    # qu'un taux de reussite et n'ont pas d'intervalle a porter.
    lignes: list[str] = []
    dans_un_tableau = False
    for line in markdown.splitlines():
        if line == stats_export.RATE_HEADER:
            dans_un_tableau = True
        elif not line.startswith("|"):
            dans_un_tableau = False
        elif dans_un_tableau and line != stats_export.RATE_RULE:
            lignes.append(line)
    assert lignes

    for line in lignes:
        cases = [case.strip() for case in line.strip("|").split("|")]
        # Gagnees, tranchees, taux, intervalle : les quatre voyagent ensemble.
        assert cases[1].isdigit() and cases[2].isdigit(), line
        if cases[3] == "—":
            # Rien de tranche : ni taux, ni intervalle. C'est la seule forme ou
            # les deux manquent, et ils manquent **ensemble**.
            assert cases[2] == "0" and cases[4] == "—", line
            continue
        assert cases[3].endswith("%"), line
        assert re.fullmatch(r"\[\d+ – \d+\]", cases[4]), line

    export = client.get("/api/stats/export?format=json").json()
    for rows in export["groups"].values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if row.get("rate") is None:
                continue
            assert row["settled"] > 0
            assert row["interval"] is not None


def test_les_reserves_de_lecture_accompagnent_les_chiffres(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Un chiffre exporte sans sa reserve est un chiffre qui sera mal lu.

    Les deux ecritures portent **la meme** liste : elle est assemblee une fois,
    et deux redactions cote a cote auraient fini par ne plus dire la meme chose.
    """
    _lot_complet(isolated_settings)
    export = client.get("/api/stats/export?format=json").json()
    markdown = client.get("/api/stats/export?format=md").text

    assert export["warnings"]
    for note in export["warnings"]:
        assert note in markdown
    # Deux reserves que ce lot produit a coup sur : aucun cran calcule — rien
    # ne retro-remplit l'historique — et l'interdit financier.
    assert any("cran calculé" in note for note in export["warnings"])
    assert any("Aucun indicateur financier" in note for note in export["warnings"])


def test_les_metadonnees_ouvrent_le_fichier(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Un export sans son perimetre ni sa date d'arrete se lit comme un etat
    courant : c'est un releve date, et il doit le dire en tete."""
    _lot_complet(isolated_settings)
    found = stats_export.report(isolated_settings)
    markdown = client.get("/api/stats/export?format=md").text
    entete = markdown.split("## Réserves")[0]

    assert "## Métadonnées" in entete
    assert found.generated_label in entete
    assert found.analysis.as_of_label in entete
    assert f"**Sessions couvertes** : {found.sessions}" in entete
    assert f"**Sélections tranchées** : {found.analysis.settled}" in entete
    assert f"**Annulées** : {found.analysis.overall.void}" in entete
    assert stats_export.SCOPE in entete


def test_le_fichier_porte_le_nom_du_jour(client: TestClient, isolated_settings: Settings) -> None:
    _lot_complet(isolated_settings)
    found = stats_export.report(isolated_settings)
    reponse = client.get("/api/stats/export?format=md")
    attendu = f"stats_myassistantbet_{found.day}.md"
    assert reponse.headers["content-disposition"] == f'attachment; filename="{attendu}"'
    assert re.fullmatch(r"stats_myassistantbet_\d{4}-\d{2}-\d{2}\.md", attendu)


def test_un_format_inconnu_est_refuse(client: TestClient, isolated_settings: Settings) -> None:
    """Un format inconnu ne doit pas retomber en silence sur le Markdown : le
    fichier telecharge porterait alors une extension qui ment sur son contenu."""
    assert client.get("/api/stats/export?format=csv").status_code == 400


def test_l_export_ne_porte_aucun_indicateur_financier(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Meme regle que partout : la mise est memorisee, jamais agregee.

    Le fichier est la surface la plus exposee du projet — il part ailleurs, et
    personne ne relira le code en le lisant.
    """
    _lot_complet(isolated_settings)
    brut = json.dumps(client.get("/api/stats/export?format=json").json())
    for interdit in ("roi", "profit", "stake", "mise", "bankroll", "gain"):
        assert f'"{interdit}"' not in brut


def test_la_page_et_l_export_lisent_le_meme_assemblage(migrated: Settings) -> None:
    """Aucun recalcul cote export : le contexte de la page **est** le releve.

    C'est la garantie structurelle derriere l'ecart nul — sans elle, les deux
    surfaces auraient chacune leur liste de sources, donc leur facon de vieillir.
    """
    found = stats_export.report(migrated)
    contexte = found.context
    assert contexte["analysis"] is found.analysis
    assert contexte["stats"] is found.stats
    assert contexte["set_scores"] is found.set_scores
    assert contexte["set_score_matrix"] is found.set_score_matrix


# -- Le residu decline -------------------------------------------------------
#
# La page ventilait des taux bruts par angle, par marche et par confiance : la
# metrique retiree de la tete, et pour la raison exacte qui a failli faire
# conclure deux fois — « Issue 48 % contre Maniere 79 % » et « SAFE 66 % contre
# FUN 40 % » opposent des populations qui ne jouent pas aux memes prix.


def test_le_residu_se_decline_avec_son_taux_brut_a_cote(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le taux brut reste, jamais seul.** Il dit combien de fois ca tombe, ce
    qui est lisible tant qu'il ne sert pas a comparer deux regroupements."""
    _lot_complet(isolated_settings)
    corps = stats_export.as_markdown(stats_export.report(isolated_settings))

    titres = [
        titre
        for sections in _markdown_blocks(corps).values()
        for titre in sections
        if titre.startswith("Résidu au prix")
    ]
    assert titres, "au moins un axe doit se decliner sur cette fixture"

    section = corps.split("### Résidu au prix, par cran de confiance")[1].split("###")[0]
    for colonne in ("Tranchées", "Gagnées", "Payées par les prix", "Écart", "Taux", "Intervalle"):
        assert colonne in section, f"« {colonne} » manque au tableau"


def test_la_page_ne_conclut_rien_sur_le_residu_decline(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Elle affiche, elle ne conclut pas. Une ligne qui s'ecarte est **marquee**,
    et l'horizon dit a quelle distance les autres se trancheraient — comme le
    fait deja la carte « ce qui s'ecarte »."""
    _lot_complet(isolated_settings)
    page = client.get("/stats").text

    assert "Résidu au prix, par cran de confiance" in page
    # Aucune phrase de conclusion : ni verdict, ni recommandation.
    for interdit in ("donc tu devrais", "il faut privilégier", "ce cran est meilleur"):
        assert interdit not in page

    analyse = history_service.analysis(settings=isolated_settings)
    portees = [row for row in analyse.residual_by_confidence if row.horizon]
    for row in portees:
        assert f"{row.horizon} sélection(s) de plus" in page


def test_un_axe_trop_court_ne_se_decline_pas(migrated: Settings) -> None:
    """**Un axe qui ne porte que du bruit vaut mieux non affiche.** Mesure du
    14/08/2026 : le marche donne 13 niveaux dont huit sous dix selections, et une
    case a trois lignes rend un intervalle que personne ne lira. Le seuil est
    celui de la page, jamais un second."""
    analyse = history_service.analysis(settings=migrated)

    assert analyse.residual_by_confidence == []
    assert analyse.residual_by_angle == []
    assert analyse.residual_by_market == []


def test_le_residu_decline_ne_porte_aucun_indicateur_financier(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Meme garde que le reste : le residu compare des issues tranchees a des
    prix deja enregistres, et rien n'y est multiplie par une mise."""
    _lot_complet(isolated_settings)
    charge = stats_export.as_json(stats_export.report(isolated_settings))

    for axe in charge["residuals"].values():
        for ligne in axe:
            assert not ({"stake", "mise", "profit", "roi", "gain"} & set(ligne))
