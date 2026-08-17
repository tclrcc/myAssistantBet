"""Le banc de tolerance au transport.

**Le mode de destruction est connu, et il a ete traite au cas par cas trois fois
de suite.** Le collage depuis une interface de conversation vers le formulaire
d'import abime le markdown : clotures supprimees ou deplacees, barres verticales
converties, tabulations, guillemets typographiques, lignes rejointes. Le module
d'import le savait pour les **tableaux** — `_cells` lit les barres et les
tabulations — et les blocs `conf` et `combo` ont malgre tout ete introduits dans
des clotures. La panne qui a suivi a coute 86 selections.

Ce fichier cesse de le decouvrir format par format : dix alterations connues,
appliquees a chacun des quatre formats structures, avec **un seul resultat
acceptable** — soit une lecture correcte, soit une ligne dans
`ingestion_rejects`. Jamais un silence.

**Regle a tenir** : tout nouveau format structure echange avec le modele entre
dans ce banc avant d'etre mis en service. Elle est aussi ecrite dans
`CONTRIBUTING.md`, la ou on la lit avant d'ajouter un format.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import combos as combos_service
from myassistantbet.services import (
    confidence,
    imports_raw,
    ingestion,
    picks_import,
    set_scores,
    write_paths,
)
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# -- Les alterations connues -------------------------------------------------
#
# Chacune reproduit un degat **observe** sur un copier-coller reel, pas un degat
# imagine : c'est ce qui les rend utiles, et c'est aussi pourquoi la liste ne
# cherche pas a etre exhaustive.


def _sans_ouvrante(texte: str) -> str:
    return re.sub(r"```[a-z]*\n", "", texte, count=1)


def _sans_fermante(texte: str) -> str:
    index = texte.rfind("```")
    return texte if index < 0 else texte[:index] + texte[index + 3 :]


def _sans_clotures(texte: str) -> str:
    return texte.replace("```conf", "").replace("```combo", "").replace("```", "")


def _sans_info_string(texte: str) -> str:
    return re.sub(r"```[a-z]+", "```", texte)


def _info_string_json(texte: str) -> str:
    return re.sub(r"```[a-z]+", "```json", texte)


def _barres_en_tabulations(texte: str) -> str:
    """Ce que produit un copier-coller d'un tableau **rendu** : les barres ont
    ete consommees par l'affichage, il ne reste que des tabulations."""
    lignes = []
    for line in texte.splitlines():
        if line.strip().startswith("|") and set(line.replace("|", "").replace(" ", "")) <= {
            "-",
            ":",
        }:
            continue
        if line.strip().startswith("|"):
            lignes.append("\t".join(cell.strip() for cell in line.strip().strip("|").split("|")))
        else:
            lignes.append(line)
    return "\n".join(lignes)


def _tabulations_en_espaces(texte: str) -> str:
    return texte.replace("\t", "    ")


def _guillemets_typographiques(texte: str) -> str:
    """Une correction automatique de traitement de texte, et le degat le plus
    silencieux de la liste : le JSON cesse d'etre du JSON sans que rien n'y
    paraisse a l'oeil."""
    return texte.replace('"', "“", 1).replace('"', "”", 1)


def _lignes_rejointes(texte: str) -> str:
    """Un JSON indente sur quatre lignes rendu sur une seule."""
    return re.sub(r"\n(?=\s*[\"}])", " ", texte)


def _espaces_insecables(texte: str) -> str:
    return texte.replace(" ", " ")


def _numerotation(texte: str) -> str:
    """Un copier-coller depuis une vue qui numerote les lignes."""
    return "\n".join(f"{index:>3}  {line}" for index, line in enumerate(texte.splitlines(), 1))


ALTERATIONS: dict[str, Callable[[str], str]] = {
    "fence ouvrante retirée": _sans_ouvrante,
    "fence fermante retirée": _sans_fermante,
    "les deux fences retirées": _sans_clotures,
    "info string absente": _sans_info_string,
    "info string remplacée par json": _info_string_json,
    "barres converties en tabulations": _barres_en_tabulations,
    "tabulations converties en espaces": _tabulations_en_espaces,
    "guillemets typographiques": _guillemets_typographiques,
    "lignes rejointes": _lignes_rejointes,
    "espaces insécables": _espaces_insecables,
    "préfixe de numérotation": _numerotation,
}


# -- Les quatre formats ------------------------------------------------------

TABLEAU = (
    "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
    "|---|-------|--------|-----------|------|--------|--------|\n"
    "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
    "| 2 | Nice – Adv Nice | 1N2 | Nice | 1.60 | 🟢 SAFE | 4 |\n"
)


def _conf(mark: str) -> str:
    return (
        "```conf\n"
        f'{{"match": "{mark}", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour de blessure", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
        "```\n"
    )


#: **Un bloc par ligne du tableau, et c'est indispensable.** Un seul bloc pour
#: deux lignes fait echouer l'appariement quoi qu'il arrive au transport : le
#: banc aurait alors rendu « rejet » partout, donc serait passe pour la mauvaise
#: raison sans jamais mesurer ce qu'il pretend mesurer.
CONF = _conf("M1") + "\n" + _conf("M2")

COMBO = '```combo\n{"type": "court", "jambes": ["M1", "M2"], "cote": 2.32}\n```\n'

SETS = "sets: M1=2-0/2-1 | M2=PASSE\n"


def _match(settings: Settings, nom: str) -> int:
    return save(
        build(
            "football",
            "Match amical",
            nom,
            f"Adv {nom}",
            LOIN,
            "20:45",
            f"{nom} 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _session(settings: Settings) -> int:
    session_id = 0
    for nom in ("Lyon", "Nice"):
        session_id = board_service.toggle_selection(_match(settings, nom), True, settings)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=settings,
    )
    return session_id


#: Ce que chaque format doit produire quand rien ne l'abime, et comment le lire
#: sur un apercu. La lecture se fait **par le parcours reel** — `build_preview` —
#: parce que c'est le transport qui est teste, et qu'un lecteur appele
#: directement ne dit rien de ce que le formulaire lui donne.
#: Le type de rejet qui **prouve** qu'un format a ete cherche et refuse. Un
#: format tombe sans lui est un silence, et c'est le seul resultat que ce banc
#: interdit.
ATTENDU = {
    "tableau": ingestion.SELECTION,
    "conf": ingestion.CONF,
    "combo": ingestion.COMBO,
    "sets": ingestion.SCORE_SETS,
}

FORMATS = {
    "tableau": (TABLEAU, lambda preview: len(preview.picks)),
    "conf": (TABLEAU + "\n" + CONF, lambda preview: preview.claims_attached),
    "combo": (TABLEAU + "\n" + CONF + "\n" + COMBO, lambda preview: len(preview.combos)),
    "sets": (TABLEAU + "\n" + SETS, lambda preview: len(preview.scores)),
}


@pytest.mark.parametrize("format_name", sorted(FORMATS))
@pytest.mark.parametrize("alteration", sorted(ALTERATIONS))
def test_aucune_alteration_ne_produit_de_perte_silencieuse(
    format_name: str, alteration: str, migrated: Settings
) -> None:
    """**Le banc, 4 formats × 11 altérations.**

    Un seul résultat est acceptable : soit le format est lu, soit son échec
    laisse une trace. La troisième issue — lu à moitié, ou pas lu du tout, sans
    un mot — est celle qui a coûté 86 sélections, et c'est elle que ce test
    interdit.

    Le test **n'exige pas** que chaque altération soit survécue : certaines
    détruisent l'information pour de bon, et prétendre les lire serait inventer.
    Il exige que l'échec se voie.
    """
    session_id = _session(migrated)
    rendu, compter = FORMATS[format_name]
    abime = ALTERATIONS[alteration](rendu)

    preview = picks_import.build_preview(session_id, abime, migrated)

    lu = compter(preview)
    # **La trace doit porter sur le format teste**, jamais sur n'importe quoi :
    # un collage sans ligne `dossiers_ouverts` produit toujours une note, et
    # accepter n'importe quelle trace ferait passer ce banc partout sans rien
    # verifier — l'assertion decrirait au lieu de contraindre.
    motifs = {reject.block_type for reject in preview.rejects}
    trace = ATTENDU[format_name] in motifs or (format_name == "tableau" and preview.ignored)
    assert lu or trace, (
        f"« {format_name} » sous « {alteration} » : ni lu, ni signalé — "
        "c'est exactement la perte silencieuse que ce banc interdit"
    )


def test_le_banc_couvre_bien_quatre_formats_et_onze_alterations() -> None:
    """**Le compte, ecrit pour qu'il ne derive pas en silence.** Un format ajoute
    a `FORMATS` sans entree dans `ATTENDU` tomberait sur une cle manquante ; une
    alteration retiree ferait baisser ce produit sans que personne le voie."""
    assert len(FORMATS) == 4
    assert len(ALTERATIONS) == 11
    assert set(FORMATS) == set(ATTENDU), "tout format testé sait quel rejet le prouve"


def test_la_panne_d_origine_est_rejouee(migrated: Settings) -> None:
    """**Le bloc `conf` sans clôture**, la panne du 13 au 17/08 : 86 sélections
    sans cran, et rien nulle part ne le disait."""
    session_id = _session(migrated)
    sans_cloture = TABLEAU + "\n" + _sans_clotures(CONF)

    preview = picks_import.build_preview(session_id, sans_cloture, migrated)

    assert preview.claims_attached == 2, "les blocs sans clôture sont lus à leur forme"


def test_un_bloc_conf_se_reconnait_a_sa_forme_sans_cloture() -> None:
    """**Détection de repli** : à défaut de clôture exploitable, un objet JSON
    portant `match` et `faits` est un bloc de confiance. `type` ne peut pas
    trancher — les deux familles le portent."""
    lecture = confidence.read_blocks(_sans_clotures(CONF))

    assert [claim.match for claim in lecture.claims] == ["M1", "M2"]


def test_un_combine_se_reconnait_a_sa_forme_sans_cloture() -> None:
    lecture = combos_service.read_combos(_sans_clotures(COMBO))

    assert [combo.marks for combo in lecture.combos] == [("M1", "M2")]


def test_la_ligne_sets_traverse_toutes_les_alterations() -> None:
    """**Elle n'a pas de clôture à perdre**, et c'est tout l'argument : elle est
    bâtie sur le modèle de `dossiers_ouverts`, la seule structure du gabarit qui
    n'ait jamais posé de problème de transport."""
    for nom, altere in ALTERATIONS.items():
        lecture = set_scores.read(altere(SETS))
        assert [row.mark for row in lecture.rows] == ["M1", "M2"], nom


def test_les_rejets_du_banc_arrivent_bien_en_base(client: TestClient, migrated: Settings) -> None:
    """Un banc qui ne vérifierait que l'objet en mémoire laisserait passer un
    transport cassé entre l'aperçu et l'écriture."""
    session_id = _session(migrated)
    casse = TABLEAU + '\n```conf\n{"match": "M1", "faits": [],}\n```\n'

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": casse})
    champs = dict(re.findall(r'name="([a-z_0-9]+)"\s+value="([^"]*)"', apercu.text))
    client.post(
        f"/history/{session_id}/picks/import",
        data={"rejects": champs.get("rejects", "[]").replace("&#34;", '"')},
    )

    motifs = {
        row["reason"] for row in db.query("SELECT reason FROM ingestion_rejects", settings=migrated)
    }
    assert ingestion.JSON_INVALID in motifs


# -- Le contrôle de journalisation -------------------------------------------


def test_le_selfcheck_passe_sur_tous_les_chemins() -> None:
    """**Une table de rejets qui reste vide ne prouve rien.** Ce contrôle injecte
    un exemplaire malformé de chaque format sur chaque chemin d'import et échoue
    si l'un reste muet — il a attrapé le rejeu dès sa première exécution, qui
    collectait ses échecs d'écriture sans jamais les journaliser."""
    from myassistantbet import selfcheck

    rapport = selfcheck.run()

    assert rapport.failures == [], "\n".join(rapport.lines)
    # Le compte se compare a **ce que le registre attend**, jamais a la taille de
    # `BROKEN` : comparer une liste a elle-meme rendait « 8 sur 8 » vrai par
    # construction, et c'est ce que ce lot corrige.
    assert len(rapport.checks) == rapport.expected
    assert rapport.families == write_paths.declared_block_types()


def test_le_selfcheck_echoue_si_une_famille_declaree_n_a_pas_d_exemplaire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Le dénominateur doit pouvoir contredire le numérateur.**

    Une famille de blocs déclarée au registre d'écriture et privée de son
    exemplaire malformé n'est plus vérifiée nulle part. Avant ce lot elle
    disparaissait simplement du compte, et « 8 sur 8 » restait vrai — la sortie
    identique pour le succès et pour la lacune, une septième fois.
    """
    from myassistantbet import selfcheck

    ampute = {nom: valeur for nom, valeur in selfcheck.BROKEN.items() if nom != ingestion.COMBO}
    monkeypatch.setattr(selfcheck, "BROKEN", ampute)

    rapport = selfcheck.run()

    manquants = [check for check in rapport.failures if check.fmt == ingestion.COMBO]
    assert len(manquants) == len(rapport.paths)
    assert "aucun exemplaire malformé" in manquants[0].detail
    # Le compte attendu, lui, n'a pas bougé : il vient du registre.
    assert rapport.expected == len(rapport.paths) * len(write_paths.declared_block_types())


def test_le_selfcheck_echoue_si_un_chemin_devient_muet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Sans ce test, le contrôle pourrait passer sans rien contrôler.** Un
    chemin muet doit le faire tomber, sinon il ne dit rien de plus qu'une table
    vide — le défaut exact qu'il existe pour supprimer."""
    from myassistantbet import selfcheck

    monkeypatch.setitem(selfcheck.PATHS, "muet", lambda session_id, raw, settings: set())

    rapport = selfcheck.run()

    assert [check.path for check in rapport.failures] == ["muet"] * len(rapport.families)


# -- §5a — la numérotation de lignes se récupère, elle n'est plus seulement vue


def test_la_numerotation_de_lignes_est_recuperee(migrated: Settings) -> None:
    """**La dette nommée au lot 2**, et elle méritait d'être payée.

    Le banc rapportait la numérotation comme « le seul cas qui casse le
    tableau ». Contrairement aux guillemets typographiques sur du JSON, elle
    **ne détruit aucune information** : un préfixe `  12  ` se retire sans
    perte, et prétendre le lire n'invente rien.
    """
    session_id = _session(migrated)
    complet = TABLEAU + "\n" + CONF + "\n" + COMBO + "\n" + SETS

    preview = picks_import.build_preview(session_id, _numerotation(complet), migrated)

    assert len(preview.picks) == 2, "le tableau se lit malgré la numérotation"
    assert preview.claims_attached == 2
    assert len(preview.combos) == 1
    assert len(preview.scores) == 2, "les deux scores en sets sont rattachés"


def test_le_retrait_ne_deplace_aucun_caractere() -> None:
    """**La contrainte du projet, et elle n'est pas négociable.**

    `imports_raw` garde le texte tel quel et chaque ligne lue garde son
    intervalle de position dedans. Un retrait qui raccourcirait les lignes
    ferait cesser toutes ces bornes de désigner quoi que ce soit, et le rejeu
    ciblé — la raison d'être du collage brut — tomberait avec.
    """
    numerote = _numerotation(TABLEAU + "\n" + CONF)

    propre = ingestion.unnumber(numerote)

    assert len(propre) == len(numerote)
    assert propre != numerote
    assert [len(ligne) for ligne in propre.splitlines()] == [
        len(ligne) for ligne in numerote.splitlines()
    ]


def test_les_bornes_designent_encore_le_collage_conserve(migrated: Settings) -> None:
    """Le brut est gardé **avant** le retrait, et les bornes le désignent lui.

    C'est ce qui rend le retrait acceptable : ce qu'on conserve reste le texte
    reçu, balisage abîmé compris, et une borne relue y retombe sur la même
    ligne.
    """
    session_id = _session(migrated)
    numerote = _numerotation(TABLEAU)

    preview = picks_import.build_preview(session_id, numerote, migrated)

    assert preview.import_id is not None
    collage = imports_raw.get(preview.import_id, migrated)
    assert collage is not None
    assert collage.raw_text == numerote, "le brut est gardé tel quel"
    ligne = preview.picks[0]
    assert ligne.start is not None and ligne.end is not None
    assert "Lyon" in collage.raw_text[ligne.start : ligne.end]


def test_un_tableau_tabule_a_colonne_numerique_n_est_pas_mange() -> None:
    """**Le faux positif que cette règle existe pour éviter.**

    Le module d'import sait depuis toujours qu'un tableau copié depuis le rendu
    arrive **tabulé**, les barres ayant été consommées. Si `12\\t` comptait comme
    un préfixe de numérotation, la première colonne d'un tel tableau — le numéro
    de ligne, qui est une donnée — se ferait manger.

    Le faux positif coûte une colonne de données ; le faux négatif coûte un
    rejet déjà visible. La sévérité va donc dans ce sens-là.
    """
    tabule = "1\tLyon – Adv Lyon\t1N2\n2\tNice – Adv Nice\t1N2"

    assert ingestion.unnumber(tabule) == tabule


def test_une_numerotation_incomplete_ou_desordonnee_n_est_pas_retiree() -> None:
    """Une numérotation est une propriété du **bloc**, pas d'une ligne.

    Sans cette condition, trois lignes de prose commençant par un nombre
    passeraient pour une vue numérotée — et ce qui suit leur nombre serait lu
    comme la ligne entière.
    """
    partielle = "  1  première\ndeuxième sans numéro\n  3  troisième"
    desordonnee = "  1  a\n  5  b\n  9  c"

    assert ingestion.unnumber(partielle) == partielle
    assert ingestion.unnumber(desordonnee) == desordonnee
