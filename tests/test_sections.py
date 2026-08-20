"""Ce que le prompt demandait, et ce que le collage n'a pas rapporte.

Le trou que le lot 2 laissait : une section absente n'echoue pas — elle
n'arrive pas — donc elle ne produit aucun rejet et n'apparaissait nulle part.
C'est l'etat exact dans lequel les blocs `conf` sont restes quatre jours, et
celui que la population exploratoire a aujourd'hui.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import sections
from myassistantbet.services.manual import build, save

#: Un prompt qui reclame **les cinq** sections structurees. C'est ce que le
#: gabarit rend sur un lot de tennis dont les paliers hauts sont atteignables.
PROMPT_COMPLET = """
### M1 · tennis · ATP · Fritz – Michelsen · 01/01 20:45
### C-bis. Sélections exploratoires
Réponds par un bloc par ligne :
```conf
{"match": "M1", "source_level": "lecture", "faits": []}
```
Et un bloc par combiné :
```combo
{"type": "court", "jambes": ["M1"]}
```
Puis une ligne unique :

    sets: M1=2-0/2-1 | M2=PASSE

Et à côté des blocs : dossiers_ouverts: [M1, M4]
"""

#: Un rendu qui ne rapporte que le tableau — le collage ligne par ligne depuis
#: la section C, celui qui a coute 86 blocs de confiance.
COLLAGE_NU = """
| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Fritz – Michelsen | Vainqueur | Fritz | 1.45 | 🟢 SAFE | 4 |
"""

COLLAGE_COMPLET = (
    COLLAGE_NU
    + """
### C-bis. Sélections exploratoires

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Autre – Match | Vainqueur | Autre | 9.00 | 🔴 GIGA FUN | 1 |

```conf
{"match": "M1", "source_level": "lecture", "faits": []}
```
```combo
{"type": "court", "cote_declaree": 1.45, "jambes": [{"match": "M1"}]}
```
sets: M1=2-0/2-1
dossiers_ouverts: [M1]
"""
)


def test_ce_qui_n_a_jamais_ete_demande_ne_manque_pas() -> None:
    """**Le troisième état, et c'est lui qui rendait un zéro illisible.**

    Un lot dont aucun palier haut n'est atteignable ne porte pas de section
    C-bis : sa population exploratoire vaut zéro et ce zéro est juste. Le
    reprocher au collage enverrait chercher un lecteur muet là où il n'y a
    qu'une porte de gabarit fermée.
    """
    demandees, trouvees = sections.read(COLLAGE_NU, "un prompt sans aucune section")

    assert demandees == frozenset()
    assert trouvees == frozenset()
    releve = sections.SessionSections(1, has_paste=True, asked=demandees, found=trouvees)
    assert releve.missing == ()
    assert "aucune section structurée demandée" in releve.line


def test_les_cinq_sections_demandees_et_absentes_sont_nommees() -> None:
    """Le cas qui a coûté 86 blocs : un collage réduit au seul tableau."""
    demandees, trouvees = sections.read(COLLAGE_NU, PROMPT_COMPLET)

    assert demandees == {"c_bis", "conf", "combo", "sets", "dossiers_ouverts"}
    assert trouvees == frozenset()
    releve = sections.SessionSections(1, has_paste=True, asked=demandees, found=trouvees)
    # L'ordre est celui du gabarit, pas celui d'un ensemble : on relit un
    # compte-rendu dans l'ordre où l'on a collé.
    assert releve.missing == ("c_bis", "conf", "combo", "sets", "dossiers_ouverts")
    assert "section C-bis" in releve.line and "ligne sets:" in releve.line


def test_un_collage_entier_ne_reclame_rien() -> None:
    """La réciproque : sans elle, la ligne crierait au manque en permanence."""
    demandees, trouvees = sections.read(COLLAGE_COMPLET, PROMPT_COMPLET)

    releve = sections.SessionSections(1, has_paste=True, asked=demandees, found=trouvees)
    assert releve.missing == (), f"{sorted(demandees - trouvees)} devraient être trouvées"
    assert "toutes retrouvées" in releve.line


def test_une_ligne_dossiers_ouverts_vide_compte_comme_trouvee() -> None:
    """**Vide n'est pas absente**, et les deux appellent des gestes opposés.

    Une liste vide est une déclaration du modèle — il n'a rien ouvert, le
    gabarit l'autorise. Une ligne absente est un collage qui l'a laissée
    derrière lui. Les confondre ferait reprocher au collage une réponse
    parfaitement régulière, et c'est la distinction que la migration 049 a payée
    une fois.
    """
    _, trouvees = sections.read("dossiers_ouverts: []", PROMPT_COMPLET)
    assert "dossiers_ouverts" in trouvees

    _, absente = sections.read("rien du tout", PROMPT_COMPLET)
    assert "dossiers_ouverts" not in absente


def test_une_ligne_dossiers_ouverts_illisible_compte_comme_trouvee() -> None:
    """Illisible non plus n'est pas absente : c'est un défaut de lecteur.

    « dossiers_ouverts: M1, M4 » sans crochets a déjà fait accuser le gabarit
    d'un défaut de lecteur. Le compter comme manquant referait exactement cette
    erreur, et le relevé d'aperçu nomme déjà les quatre états.
    """
    _, trouvees = sections.read("dossiers_ouverts: M1, M4", PROMPT_COMPLET)
    assert "dossiers_ouverts" in trouvees


def test_la_section_c_bis_se_reconnait_comme_a_l_import() -> None:
    """Le même motif des deux côtés, sinon le relevé ment dans un sens ou l'autre.

    `parse_table` bascule en section exploratoire sur `EXPLORATORY_HEAD` : si ce
    module reconnaissait la section plus largement, il déclarerait « trouvée »
    une section dont aucune ligne n'est en réalité marquée exploratoire.
    """
    from myassistantbet.services import picks_import

    assert sections._finds_c_bis("### C-bis. Sélections exploratoires")
    assert sections._finds_c_bis("Sélections exploratoires")
    assert not sections._finds_c_bis("### C. Sélections")
    # Le motif est celui de l'import, pas une seconde écriture posée à côté.
    assert picks_import.EXPLORATORY_HEAD.search("selections exploratoires")


def test_le_releve_de_session_se_derive_du_prompt_et_du_collage(migrated: Settings) -> None:
    """**Rien n'est stocké**, et c'est le cœur du module.

    Les deux moitiés dorment en base : le prompt émis dans `prompts.body`, le
    collage dans `imports_raw`. Une colonne de plus aurait figé un constat que
    le code courant sait refaire.
    """
    event_id = save(
        build(
            "tennis",
            "ATP",
            "Fritz",
            "Michelsen",
            "2099-01-01",
            "20:45",
            "Fritz 1.45",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'test', ?, 0, ?)",
        (session_id, PROMPT_COMPLET, db.utcnow()),
        settings=migrated,
    )
    db.execute(
        "INSERT INTO imports_raw (session_id, raw_text, sha256, char_count, source, created_at) "
        "VALUES (?, ?, 'x', ?, 'form', ?)",
        (session_id, COLLAGE_NU, len(COLLAGE_NU), db.utcnow()),
        settings=migrated,
    )

    releve = sections.survey(migrated)

    assert [row.session_id for row in releve.rows] == [session_id]
    assert releve.missing_total == 5
    assert releve.concerned == releve.rows


def test_sans_collage_conserve_rien_ne_se_conclut(migrated: Settings) -> None:
    """`imports_raw` date de la migration 052 : avant, il n'y a rien à relire.

    Une session sans collage ne doit pas compter comme une session dont tout
    manque — ce serait accuser d'un défaut les quatorze sessions antérieures au
    lot 2, exactement le contraire de ce que ce relevé mesure.
    """
    assert sections.survey(migrated).rows == []
    orpheline = sections.SessionSections(3, has_paste=False)
    assert orpheline.missing == ()
    assert "rien à conclure" in orpheline.line


def test_l_apercu_nomme_les_sections_absentes(migrated: Settings) -> None:
    """La ligne se rend **à l'aperçu**, seul instant où elle change quelque chose.

    Dite une semaine plus tard sur la page de statistiques, une section laissée
    derrière ne se répare plus. Dite ici, elle se reprend en recollant.
    """
    event_id = save(
        build(
            "tennis",
            "ATP",
            "Fritz",
            "Michelsen",
            "2099-01-01",
            "20:45",
            "Fritz 1.45",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'test', ?, 0, ?)",
        (session_id, PROMPT_COMPLET, db.utcnow()),
        settings=migrated,
    )

    with TestClient(app) as client:
        page = client.post(f"/history/{session_id}/picks/preview", data={"table": COLLAGE_NU}).text

    assert "absente(s) du collage" in page
    assert "section C-bis" in page and "blocs conf" in page


@pytest.mark.parametrize("section", sections.SECTIONS, ids=lambda s: s.key)
def test_chaque_section_se_reconnait_dans_le_prompt_qui_la_demande(section: Any) -> None:
    """Une faute de frappe dans un motif de demande ne casse rien — elle éteint.

    La condition devient toujours fausse, la section n'est plus jamais réclamée,
    et le relevé annonce « aucune section demandée » sur un lot qui les demandait
    toutes. Même garde-fou que les portes du préambule et les identifiants du
    sprite.
    """
    assert section.asks(PROMPT_COMPLET), f"{section.key} n'est pas reconnue dans un prompt complet"
    assert section.finds(COLLAGE_COMPLET), f"{section.key} n'est pas reconnue dans un rendu complet"
    assert not section.finds(COLLAGE_NU), f"{section.key} est vue dans un collage qui ne l'a pas"


# -- Les consignes permanentes ne demandent aucune section -------------------


def test_une_consigne_permanente_ne_demande_aucune_section() -> None:
    """**Trouve en ecrivant le test de bout en bout du §4.**

    Les consignes permanentes sont recopiees telles quelles dans le prompt. Une
    dont une ligne commence par `sets:` faisait declarer la ligne des scores en
    sets **demandee** — sur un lot de football, ou le gabarit ne la demande
    jamais. Le releve annoncait alors une section demandee et non rapportee.

    C'est un faux manque, et il tombe sur la surface dont le seul role est de
    separer une absence de collecte d'une absence de demande : elle se mettait a
    produire exactement la confusion qu'elle existe pour lever.
    """
    prompt = (
        "# SESSION\n\n"
        "## CONSIGNES PERMANENTES\n"
        "Exemple de ce que j'attends :\n"
        "sets: M1=2-0 | M2=PASSE\n"
        "Et rappelle les dossiers_ouverts.\n\n"
        "## MÉTHODE\n"
        "Deux temps, dans cet ordre.\n"
    )

    asked, _ = sections.read("", prompt)

    assert "sets" not in asked
    assert "opened" not in asked


def test_le_gabarit_garde_ses_demandes_a_travers_les_consignes() -> None:
    """Le retrait porte sur le **bloc des consignes**, jamais sur le prompt : une
    demande ecrite par le gabarit reste lue, que des consignes la precedent ou
    non. Sans cette moitie, la correction troquerait un faux manque contre un
    silence."""
    consignes = "## CONSIGNES PERMANENTES\nJe pose depuis mon téléphone.\n\n"
    gabarit = "## SORTIE ATTENDUE\nDonne une ligne par match :\nsets: M1=2-0\n"

    sans, _ = sections.read("", f"# SESSION\n\n{gabarit}")
    avec, _ = sections.read("", f"# SESSION\n\n{consignes}{gabarit}")

    assert "sets" in sans
    assert avec == sans


def test_le_decoupage_se_fait_sur_le_titre_et_non_sur_le_texte() -> None:
    """Les consignes sont un texte **libre** : rien de ce qu'elles contiennent ne
    doit pouvoir deplacer la borne. Une consigne qui ecrirait « ## MÉTHODE » ou
    des lignes vides ne raccourcit ni n'allonge le bloc retire."""
    prompt = (
        "# SESSION\n\n"
        "## CONSIGNES PERMANENTES\n"
        "Ne me parle jamais de ## MÉTHODE.\n"
        "\n"
        "sets: M1=2-0\n\n"
        "## SORTIE ATTENDUE\n"
        "```conf\n"
    )

    asked, _ = sections.read("", prompt)

    assert "sets" not in asked, "le bloc court jusqu'au vrai titre suivant"
    assert "conf" in asked, "et il s'arrête là"
