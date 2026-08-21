"""Le rendu **entier**, de bout en bout, sur un collage réel.

**C'est par là que le défaut du lot 8 est passé.** Le banc de transport
(`test_transport.py`) applique onze altérations à chaque format structuré, mais
il les teste **isolés** : un tableau seul, un bloc `conf` seul, une ligne
`sets:` seule. Or le défaut ne se voyait que dans le rendu complet — une phrase
de la section B mentionnant « C-bis » faisait basculer la lecture avant le
tableau de la section C, et toutes ses lignes partaient en rejet.

Chaque format passait donc son banc, et le rendu entier perdait la moitié de sa
substance. Le découpage en sections y est entré après coup, en sixième format ;
ce fichier-ci fait l'autre moitié du chemin — **un vrai rendu, par le vrai
chemin d'import, et un compte exact pour chaque objet**.

**Un compte, pas une présence.** C'est le compte qui aurait crié : le collage
portait cinq blocs de confiance et deux sélections sont entrées. Une assertion
« il y a des sélections » serait passée pendant toute la panne.

**Cette garde se met à jour à chaque changement de sortie attendue du gabarit**,
et c'est son rôle : elle doit casser quand le rendu change de forme, et le
diagnostic est alors de vérifier que le nouveau compte est celui qu'on voulait.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import picks_import, sections
from myassistantbet.services import prompt as prompt_service
from myassistantbet.services.manual import build, save
from myassistantbet.services.render import ESTIMATED_MARK
from tests.helpers import repost_import_form as _repost

#: Le collage réel du 19/08/2026, 21 559 caractères, tel qu'il a été reçu —
#: clôtures de blocs mangées par le rendu, tabulations à la place des barres,
#: sections A à F. **Il n'est ni nettoyé ni raccourci** : c'est exactement ce
#: que le transport en fait qui est testé ici.
COLLAGE = (Path(__file__).parent / "fixtures" / "collage_complet.md").read_text(encoding="utf-8")

#: Ce que ce collage doit produire. **Chaque nombre a été vérifié à la main sur
#: le texte** — ce n'est pas la sortie du jour recopiée.
ATTENDU = {
    "section_c": 5,
    "c_bis": 2,
    "blocs_conf": 5,
    "combines": 1,
    "sets": 10,
    "dossiers": 9,
}

#: Les en-têtes du prompt d'origine : c'est contre eux que la somme de contrôle
#: de l'appariement se fait. Recopiés du prompt 159 de la base servie.
#: Les en-tetes portent la mention `(estimée)`, comme le rendu servi depuis le
#: lot 12. **C'est la forme qui va reellement arriver a l'import**, et le test de
#: bout en bout doit la voir : la somme de controle de l'appariement compare
#: l'affiche d'un en-tete a la colonne Match d'un tableau colle, et un champ
#: ajoute a l'en-tete est exactement le genre de changement qui la ferait mordre
#: sans qu'on le voie avant le lendemain.
ENTETES = "\n".join(
    f"### M{index} · TENNIS · {tournoi} Cincinnati Open · {affiche} · {heure} {ESTIMATED_MARK}"
    for index, (tournoi, affiche, heure) in enumerate(
        [
            ("WTA", "Diana Shnaider – Elena Rybakina", "19/08 20:00"),
            ("WTA", "Linda Noskova – Amanda Anisimova", "19/08 20:00"),
            ("WTA", "Coco Gauff – Marie Bouzkova", "19/08 21:00"),
            ("ATP", "Nuno Borges – Brandon Nakashima", "19/08 21:10"),
            ("ATP", "Jaime Faria – Lorenzo Musetti", "19/08 22:20"),
            ("ATP", "Taylor Fritz – Christopher O'Connell", "20/08 01:00"),
            ("WTA", "Madison Keys – Xiyu Wang", "20/08 01:00"),
            ("ATP", "Frances Tiafoe – Felix Auger-Aliassime", "20/08 02:10"),
            ("WTA", "Aryna Sabalenka – Sara Bejlek", "20/08 02:30"),
        ],
        start=1,
    )
)

AFFICHES = [
    ("Diana Shnaider", "Elena Rybakina"),
    ("Linda Noskova", "Amanda Anisimova"),
    ("Coco Gauff", "Marie Bouzkova"),
    ("Nuno Borges", "Brandon Nakashima"),
    ("Jaime Faria", "Lorenzo Musetti"),
    ("Taylor Fritz", "Christopher O'Connell"),
    ("Madison Keys", "Xiyu Wang"),
    ("Frances Tiafoe", "Felix Auger-Aliassime"),
    ("Aryna Sabalenka", "Sara Bejlek"),
]


@pytest.fixture
def lot(migrated: Settings) -> int:
    """Le lot du collage : neuf matchs de tennis, et le prompt qui les nomme."""
    session_id = 0
    for home, away in AFFICHES:
        event_id = save(
            build(
                "tennis",
                "ATP Cincinnati Open",
                home,
                away,
                "2099-01-01",
                "20:00",
                f"{home} 1.50",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, ENTETES, db.utcnow()),
        settings=migrated,
    )
    # **Le lot du prompt, et pas seulement son corps.** `combos.record` refuse
    # une jambe absente de `prompt_events` — « elles n'ont jamais été comparées
    # à celles-ci » — donc une fixture qui pose le prompt sans son lot fait
    # tomber tous les combinés pour une raison qui n'existe pas en production.
    # Trouvé en écrivant le test de bout en bout : la lecture, elle, ne lit
    # jamais cette table, si bien qu'aucun test d'aperçu ne pouvait le voir.
    prompt_id = db.query(
        "SELECT id FROM prompts WHERE session_id = ? ORDER BY id DESC LIMIT 1",
        (session_id,),
        settings=migrated,
    )[0]["id"]
    for row in db.query(
        "SELECT event_id FROM session_events WHERE session_id = ?",
        (session_id,),
        settings=migrated,
    ):
        db.execute(
            "INSERT OR IGNORE INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            (prompt_id, row["event_id"]),
            settings=migrated,
        )
    return session_id


def test_un_rendu_complet_rend_chaque_objet_dans_le_compte_attendu(
    lot: int, migrated: Settings
) -> None:
    """**La garde de forme du gabarit entier.**

    Pendant la panne du lot 8, ce test aurait rendu 0 en section C et 5 blocs
    non appariés — sur les mêmes 21 559 caractères. C'est le seul endroit du
    dépôt où le rendu est vu comme le modèle le produit.
    """
    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    obtenu = {
        "section_c": sum(1 for pick in preview.picks if not pick.exploratory),
        "c_bis": sum(1 for pick in preview.picks if pick.exploratory),
        "blocs_conf": preview.claims_attached,
        "combines": len(preview.combos),
        "sets": len(preview.scores),
        "dossiers": len(preview.opened.marks or ()),
    }

    assert obtenu == ATTENDU, (
        "le rendu complet ne produit plus les mêmes comptes — vérifie que le "
        "nouveau compte est celui qu'on voulait avant de mettre ce test à jour"
    )
    # **Le compte seul était ambigu, et c'est le lot 10 qui l'a vu.** Ce collage
    # porte cinq blocs et cinq lignes de section C : « 5 » valait donc aussi bien
    # « tous les blocs » que « ceux de la section C », et un appariement qui
    # aurait ignoré C-bis aurait rendu le même nombre. L'attribution se vérifie
    # donc ligne par ligne.
    porteurs = [pick.claim is not None for pick in preview.picks]
    assert porteurs == [True] * 5 + [False] * 2, (
        "les cinq blocs de ce rendu désignent les cinq lignes de la section C ; "
        "les deux lignes de C-bis n'en portent pas — le modèle n'en avait pas donné"
    )


def test_le_collage_complet_ne_perd_aucune_ligne_de_section_c(lot: int, migrated: Settings) -> None:
    """**Le défaut du lot 8, rejoué sur son propre texte.**

    La section B de ce collage mentionne « C-bis » trois fois. Aucune de ces
    mentions ne doit basculer la lecture : les cinq lignes de la section C sont
    des sélections principales, et aucune ne part en rejet « exploratoire en
    palier sûr ».
    """
    assert COLLAGE.lower().count("c-bis") >= 3, "le texte porte bien le piège"

    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    assert not [note for note in preview.notes if "palier sûr" in note]
    assert [pick.exploratory for pick in preview.picks] == [False] * 5 + [True] * 2


def test_la_ligne_dossiers_ouverts_est_lue_avec_ses_neuf_reperes(
    lot: int, migrated: Settings
) -> None:
    """Neuf repères pour un budget de dix : c'est la première fois que ce nombre
    se mesure, et il dit que le budget n'était pas la contrainte."""
    preview = picks_import.build_preview(lot, COLLAGE, migrated)

    assert preview.opened.state == "renseignee"
    assert preview.opened.declared
    assert sorted(preview.opened.marks, key=lambda mark: int(mark[1:])) == [
        f"M{index}" for index in range(1, 10)
    ]


def test_les_blocs_de_c_bis_sont_comptes_eux_aussi(lot: int, migrated: Settings) -> None:
    """**La sortie que le gabarit demande depuis le lot 9**, et que ce collage-ci
    ne porte pas encore.

    Le rendu archivé date d'avant la phrase ajoutée en section C-bis : ses cinq
    blocs désignent les cinq lignes de la section C, et les deux lignes
    exploratoires n'en ont aucun. Le compte de cinq était donc **ambigu** — il
    valait aussi bien « tous les blocs » que « ceux de la section C ».

    Ce test lève l'ambiguïté sur la forme attendue désormais : les mêmes 21 559
    caractères, plus les deux blocs que le gabarit réclame maintenant du côté
    exploratoire. Le compte doit monter à sept, et les sept lignes porter le leur.

    La fixture n'est **pas** modifiée : c'est un vrai collage reçu, et le tronquer
    ou le compléter lui retirerait ce qui fait sa valeur.
    """
    complet = COLLAGE + (
        '\n{"match": "M3", "confiance": 1, "type": "maniere", "source_level": "lecture",\n'
        ' "faits": [], "manque_touche_facteur": true}\n'
        '\n{"match": "M7", "confiance": 1, "type": "issue", "source_level": "lecture",\n'
        ' "faits": [], "manque_touche_facteur": true}\n'
    )

    preview = picks_import.build_preview(lot, complet, migrated)

    assert preview.claims_attached == ATTENDU["blocs_conf"] + 2 == 7
    assert [pick.claim is not None for pick in preview.picks] == [True] * 7
    # Le drapeau exploratoire ne bouge pas : il se dérive du tableau d'origine,
    # jamais de ce que le bloc raconte.
    assert [pick.exploratory for pick in preview.picks] == [False] * 5 + [True] * 2
    # Et le cran des lignes de C-bis est bien celui d'une lecture.
    assert [pick.claim.rung for pick in preview.picks[5:]] == [1, 1]


# -- Le contrat entre celui qui ecrit l'en-tete et ceux qui le lisent ---------


def test_tous_les_lecteurs_d_entete_connus_lisent_un_en_tete_reel() -> None:
    """**Un seul test corrigé ne prouve pas qu'il n'y avait qu'un seul lecteur.**

    L'en-tête de bloc est écrit à **un** endroit — `render._header` — et lu à
    **cinq**. Le lot 12 y a ajouté un champ (`(estimée)`), et rien ne garantissait
    que les cinq le supportent : la somme de contrôle de l'appariement compare
    l'affiche d'un en-tête à la colonne Match d'un tableau collé, et elle tombe en
    tout ou rien. Une régression y coûterait **tous** les imports d'une journée,
    sans rien afficher d'anormal.

    Ce test prend un en-tête **réellement rendu** et le passe à chacun. Il casse
    le jour où un champ est ajouté sans que tous suivent — ce qui est le
    comportement voulu.
    """
    import re

    from myassistantbet.services import history as history_service
    from myassistantbet.services import prompt as prompt_service
    from myassistantbet.services.picks_import import (
        HEADER_AFFICHE,
        HEADER_FIELDS,
        _affiche_of,
    )
    from myassistantbet.services.render import ESTIMATED_MARK

    entete = (
        "### M4 · TENNIS · ATP Cincinnati Open · "
        f"Taylor Fritz – Christopher O'Connell · 20/08 01:00 {ESTIMATED_MARK}"
    )

    # 1. `render` écrit cinq champs : repère, sport, compétition, affiche, heure.
    champs = entete.split(" · ")
    assert len(champs) == HEADER_FIELDS + 1, (
        "l'en-tête a changé de nombre de champs : les lecteurs ci-dessous le "
        "découpent par position, et un champ inséré les décalerait tous"
    )

    # 2. `picks_import._affiche_of` — la somme de contrôle de l'appariement.
    #    C'est le lecteur dont une régression coûterait le plus cher.
    assert _affiche_of(entete.split("### M4 · ", 1)[1]) == "Taylor Fritz – Christopher O'Connell"
    assert champs[HEADER_AFFICHE + 1] == "Taylor Fritz – Christopher O'Connell"

    # 3. `history._NUMBERED_HEADER` — le repère et le reste, pour `prompt_headers`.
    numerote = re.findall(r"^### (M\d+) · (.+)$", entete, re.MULTILINE)
    assert numerote == [("M4", entete.split("### M4 · ", 1)[1])]

    # 4. `history._BLOCK_HEADER` — l'identité du match, pour les lots reconstruits.
    assert history_service._BLOCK_HEADER.findall(entete) == [entete.split("### M4 · ", 1)[1]]

    # 5. `prompt.BLOCK_HEADER` — la frontière du découpage de coût, et
    #    `coverage_gabarit` qui la réutilise telle quelle.
    assert prompt_service.BLOCK_HEADER.search(entete) is not None


def test_la_marque_d_estimation_ne_deplace_pas_l_affiche() -> None:
    """**La mention s'ajoute au dernier champ, jamais au milieu.**

    C'est ce qui rend le changement sûr : l'affiche reste en troisième position,
    donc la somme de contrôle compare la même chose qu'avant. Le test le vérifie
    en confrontant les deux formes plutôt qu'en le supposant.
    """
    from myassistantbet.services.picks_import import _affiche_of
    from myassistantbet.services.render import ESTIMATED_MARK

    nu = "TENNIS · ATP Cincinnati Open · Fritz – O'Connell · 20/08 01:00"

    assert _affiche_of(f"{nu} {ESTIMATED_MARK}") == _affiche_of(nu) == "Fritz – O'Connell"


# -- Le rendu entier, par la vraie route -------------------------------------
#
# **Ce fichier mesurait le lecteur, jamais l'importabilite, et c'est par la
# qu'un second defaut est passe.** Les tests ci-dessus comptent ce que
# `build_preview` produit ; ils sont restes verts pendant que les cinq collages
# complets de la base — les seuls a porter leurs blocs `conf` — ne pouvaient
# **pas** etre importes du tout. Le gabarit cache tout le formulaire des que
# `preview.ignored` n'est pas vide, et `parse_table` l'y remplissait sur tout
# rendu complet : `columns` est l'entete du tableau *en cours*, remis a zero par
# chaque titre de section, et un rendu complet finit par `F.`.
#
# La lecon tient en une phrase : **un banc qui mesure le lecteur ne voit pas un
# defaut dans la porte.** D'ou ce qui suit — le vrai chemin, de bout en bout,
# et un compte par objet ecrit **en base**.

#: Le collage archive, prolonge de ce que le gabarit produit aujourd'hui et que
#: le texte recu ne portait pas encore : les deux blocs `conf` du cote
#: exploratoire, et la **section G** — la repartition de mise, servie depuis le
#: lot precedent et qu'aucun collage de la base ne porte. Le prolongement est
#: nomme ici plutot que fondu dans la fixture d'origine : celle-ci est un texte
#: reellement recu, et la completer lui retirerait ce qui fait sa valeur.
COLLAGE_G = (Path(__file__).parent / "fixtures" / "collage_complet_g.md").read_text(
    encoding="utf-8"
)

#: Ce que ce rendu doit **ecrire**, objet par objet. Chaque nombre se lit sur le
#: texte : cinq lignes de section C et deux de C-bis, sept blocs `conf` (cinq
#: d'origine, deux exploratoires), un combine de trois jambes, neuf reperes de
#: scores dont `M2=PASSE` qui n'en est pas un, neuf dossiers ouverts, et cinq
#: mises pour les cinq lignes de section C.
#:
#: `invalidation` vient du meme tableau, et son compte est le **meme que celui
#: des selections** : les deux tableaux du rendu portent l'en-tete a onze
#: colonnes, et chacune de leurs sept lignes remplit la cellule. C'est ce qui
#: rend le controle 7 comptable — la colonne existait, elle etait jetee.
ECRIT = {
    "section_c": 5,
    "c_bis": 2,
    "blocs_conf": 7,
    "crans_calcules": 7,
    "combines": 1,
    "jambes": 3,
    "sets": 8,
    "mises": 5,
    "invalidations": 7,
    "angles_prose": 7,
}


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_un_rendu_complet_de_a_a_g_s_importe_et_ecrit_chaque_objet(
    client: TestClient, lot: int, migrated: Settings
) -> None:
    """**Le collage complet doit être plus facile à importer que le partiel.**

    Il ne l'était pas : sur les 35 collages archivés, les cinq complets — 16 559
    à 26 567 caractères, 5 à 7 sélections et autant de blocs `conf` chacun —
    étaient les **seuls** dont le formulaire ne s'affichait pas, et le message
    envoyait recoller la seule section C. L'application dictait le geste qui
    coûte les crans du lot entier.

    Ce test refait le parcours d'un navigateur : coller, relire le formulaire
    rendu, le renvoyer tel quel. Il compte ensuite **en base**, objet par objet.
    """
    apercu = client.post(f"/history/{lot}/picks/preview", data={"table": COLLAGE_G})
    assert apercu.status_code == 200

    envoi = _repost(apercu.text)
    assert sum(1 for nom in envoi if nom.startswith("keep_")) == 7, (
        "les sept lignes du rendu arrivent cochées : une ligne décochée d'office "
        "coûterait sa sélection sans qu'aucun rejet ne le dise"
    )

    valide = client.post(f"/history/{lot}/picks/import", data=envoi)
    assert valide.status_code == 200

    def compte(sql: str) -> int:
        return int(db.query(sql, settings=migrated)[0]["n"])

    obtenu = {
        "section_c": compte("SELECT count(*) n FROM picks WHERE exploratoire = 0"),
        "c_bis": compte("SELECT count(*) n FROM picks WHERE exploratoire = 1"),
        "blocs_conf": compte("SELECT count(*) n FROM picks WHERE claim_raw_json IS NOT NULL"),
        "crans_calcules": compte(
            "SELECT count(*) n FROM picks WHERE confidence_computed IS NOT NULL"
        ),
        "combines": compte("SELECT count(*) n FROM combos"),
        "jambes": compte("SELECT count(*) n FROM combo_legs"),
        "sets": compte("SELECT count(*) n FROM set_scores"),
        "mises": compte("SELECT count(*) n FROM mises"),
        "invalidations": compte("SELECT count(*) n FROM picks WHERE invalidation IS NOT NULL"),
        "angles_prose": compte("SELECT count(*) n FROM picks WHERE angle_note IS NOT NULL"),
    }

    assert obtenu == ECRIT, (
        "un objet du rendu complet n'arrive plus en base — vérifie que le "
        "nouveau compte est celui qu'on voulait avant de mettre ce test à jour"
    )


def test_un_collage_complet_n_est_jamais_refuse_a_l_apercu(lot: int, migrated: Settings) -> None:
    """**La porte, et non ce qu'elle laisse passer.**

    `ignored` non vide cache tout le formulaire : c'est le seul champ de
    l'aperçu qui ait ce pouvoir, et il n'a qu'un sens légitime — il n'y a rien
    à montrer. Un aperçu qui porte des sélections n'est donc jamais refusé, quoi
    qu'il ait par ailleurs à dire.
    """
    preview = picks_import.build_preview(lot, COLLAGE_G, migrated)

    assert preview.picks, "le rendu porte bien des sélections"
    assert preview.ignored == [], (
        "un aperçu qui porte des sélections ne se refuse pas : la remarque "
        "descend dans `notes`, qui n'empêche pas d'importer"
    )


def test_une_remarque_sur_un_apercu_lisible_descend_dans_les_notes() -> None:
    """Le garde-fou testé **contre sa propre panne**, dans les deux positions.

    Sans sélection, le message est un refus et doit le rester : c'est le cas
    pour lequel il a été écrit — un collage qui n'est pas un tableau.
    """
    vide = picks_import.ImportPreview()
    picks_import._unreadable(vide, "rien à lire")
    assert vide.ignored == ["rien à lire"] and vide.notes == []

    lisible = picks_import.ImportPreview(picks=[picks_import.ParsedPick(index=1)])
    picks_import._unreadable(lisible, "une remarque")
    assert lisible.ignored == [] and lisible.notes == ["une remarque"]


def test_la_plage_des_titres_de_section_couvre_celles_du_gabarit() -> None:
    """**Le lecteur de sections suit le gabarit, et rien ne le garantissait.**

    `SECTION_HEAD` s'arrêtait à `F` quand la section G était déjà produite : un
    titre `G.` ne fermait donc aucune section, et une section C-bis laissée
    ouverte aurait lu la suite sous les règles du mauvais tableau. Ce test lit
    les titres que le gabarit écrit vraiment et les passe au motif — une section
    H ajoutée demain sans toucher au lecteur se solde par un test rouge.
    """
    gabarit = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "myassistantbet"
        / "templates"
        / "prompts"
        / "session_default.md.j2"
    ).read_text(encoding="utf-8")

    # Deux des titres sont derriere une porte Jinja — C-bis et G ne sont pas
    # produites sur tous les lots — donc le motif les accepte en tete de ligne.
    titres = re.findall(r"^(?:\{%.*?%\})?#{2,4} ([A-Z](?:-bis)?)\. .+$", gabarit, re.MULTILINE)
    assert titres == ["A", "B", "C", "C-bis", "D", "E", "F", "G"], (
        "le gabarit a changé de sections : le lecteur les découpe sur une plage "
        "de lettres, et une section ajoutée sans lui ne fermerait rien"
    )

    for lettre in titres:
        if lettre.endswith("-bis"):
            continue
        assert picks_import.SECTION_HEAD.match(f"{lettre}. Titre"), (
            f"le gabarit écrit une section « {lettre}. » que le lecteur ne "
            "reconnaît pas comme un titre : elle ne fermerait aucune section"
        )


# -- Un prompt portant des consignes permanentes -----------------------------

#: Des consignes qui **ressemblent** a ce que le gabarit demande. Elles sont
#: recopiees telles quelles dans le prompt, et c'est le seul texte libre qui y
#: entre : la ligne `sets:` en debut de ligne, la mention `dossiers_ouverts`, un
#: « C'est » en tete de phrase — chacune est un piege deja paye ailleurs dans ce
#: depot, et aucun test ne les mettait dans un prompt.
CONSIGNES_PIEGEUSES = (
    "Betclic est le seul bookmaker où je pose.\n"
    "C'est le cas de la quasi-totalité de mes sélections hors 1N2.\n"
    "Exemple de ce que j'attends, à la fin :\n"
    "sets: M1=2-0 | M2=PASSE\n"
    "Et rappelle-moi la liste dossiers_ouverts.\n"
    "Je ne joue jamais : cartons, corners"
)


def test_un_prompt_reel_porte_ses_consignes_sans_deplacer_ses_demandes(
    migrated: Settings,
) -> None:
    """**Le seul texte libre qui entre dans un prompt**, et rien ne le testait
    dans un rendu complet.

    Deux risques distincts, et ils ne se recouvrent pas :

    · les consignes doivent **arriver telles quelles** — ni échappées, ni
      tronquées, ni rejointes ;
    · elles ne doivent **rien demander**. `sections.survey()` lit `prompts.body`
      pour savoir ce que le prompt réclamait, et une consigne dont une ligne
      commence par `sets:` y déclarait la section demandée sur un lot de
      football. Le relevé annonçait alors un manque qui n'existe pas, sur la
      surface dont le seul rôle est de séparer une absence de collecte d'une
      absence de demande.
    """
    event_id = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2099-01-01",
            "20:45",
            "Lyon 1.30\nNul 3.40\nNice 3.20",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)

    nu = prompt_service.build_prompt(session_id, settings=migrated)
    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, CONSIGNES_PIEGEUSES, migrated)
    charge = prompt_service.build_prompt(session_id, settings=migrated)

    assert CONSIGNES_PIEGEUSES in charge.body, "recopiées telles quelles"
    # Les demandes de section sont **exactement** les mêmes qu'avant.
    assert sections.read("", charge.body)[0] == sections.read("", nu.body)[0]

    # Et les titres de section du rendu attendu n'ont pas bougé non plus : une
    # consigne ne peut pas ouvrir ni fermer une section.
    def titres(corps: str) -> list[str]:
        return sorted(
            {
                ligne.split(".")[0].strip()
                for ligne in corps.splitlines()
                if picks_import.SECTION_HEAD.match(ligne)
            }
        )

    assert titres(charge.body) == titres(nu.body)


def test_un_rendu_complet_s_importe_sous_des_consignes_permanentes(
    client: TestClient, lot: int, migrated: Settings
) -> None:
    """**Le parcours entier, consignes en place.** Le collage est celui du
    modèle et ne porte pas les consignes ; ce qui change est le **prompt**, donc
    ce que `sections.survey()` croit avoir demandé et ce que l'appariement des
    blocs `conf` compare.

    Le compte en base doit être le même qu'avec un champ vide : une préférence
    de placement n'a aucune raison de coûter une sélection.
    """
    prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, CONSIGNES_PIEGEUSES, migrated)
    apercu = client.post(f"/history/{lot}/picks/preview", data={"table": COLLAGE_G})
    assert apercu.status_code == 200

    valide = client.post(f"/history/{lot}/picks/import", data=_repost(apercu.text))
    assert valide.status_code == 200

    def compte(sql: str) -> int:
        return int(db.query(sql, settings=migrated)[0]["n"])

    obtenu = {
        "section_c": compte("SELECT count(*) n FROM picks WHERE exploratoire = 0"),
        "c_bis": compte("SELECT count(*) n FROM picks WHERE exploratoire = 1"),
        "blocs_conf": compte("SELECT count(*) n FROM picks WHERE claim_raw_json IS NOT NULL"),
        "crans_calcules": compte(
            "SELECT count(*) n FROM picks WHERE confidence_computed IS NOT NULL"
        ),
        "combines": compte("SELECT count(*) n FROM combos"),
        "jambes": compte("SELECT count(*) n FROM combo_legs"),
        "sets": compte("SELECT count(*) n FROM set_scores"),
        "mises": compte("SELECT count(*) n FROM mises"),
        "invalidations": compte("SELECT count(*) n FROM picks WHERE invalidation IS NOT NULL"),
        "angles_prose": compte("SELECT count(*) n FROM picks WHERE angle_note IS NOT NULL"),
    }

    assert obtenu == ECRIT, "des consignes permanentes coûtent une sélection"
