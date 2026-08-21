"""L'extraction pour le classement a l'aveugle.

**Le seul point du protocole qui se detruisait en s'appliquant.** Extraire les
angles a la main oblige a lire les deux sorties : l'anonymat serait fictif avant
d'avoir commence.
"""

from __future__ import annotations

from myassistantbet.blind import read_angles, shuffled

TABLEAU = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf 5 | Type | Source | Angle |
|---|-------|--------|-----------|------|--------|--------|------|--------|-------|
| 1 | Lyon – Nice | 1N2 | Lyon | 1.85 | 🟢 SAFE | 4 | issue | 1 | Lyon reste sur cinq victoires |
| 2 | Lens – Brest | O/U 2.5 | Over | 2.05 | 🔵 FUN | 3 | manière | 2 | Les deux encaissent tôt |

### C-bis. Exploratoires

| # | Match | Marché | Sélection | Cote | Palier | Conf 5 | Type | Source | Angle |
|---|-------|--------|-----------|------|--------|--------|------|--------|-------|
| 3 | Reims – Metz | Hand. | Reims -1 | 2.40 | 🟠 ULTRA | 2 | manière | lecture | Reims domine |
"""


def test_les_deux_sections_sont_lues() -> None:
    """**C-bis porte la population temoin** : l'ecarter comparerait deux
    ensembles construits sous des exigences differentes."""
    angles = read_angles(TABLEAU, "gabarit")

    assert [angle.texte for angle in angles] == [
        "Lyon reste sur cinq victoires",
        "Les deux encaissent tôt",
        "Reims domine",
    ]


def test_rien_de_ce_qui_trahit_la_version_ne_sort() -> None:
    """Le jugement porte sur **les arguments, pas sur la structure**. Palier,
    cran, nature et niveau de source designeraient la version avant qu'on ait
    lu la premiere ligne."""
    textes = " ".join(angle.texte for angle in read_angles(TABLEAU, "gabarit"))

    for trace in ("SAFE", "FUN", "issue", "manière", "lecture", "1N2", "2.05", "|"):
        assert trace not in textes, f"« {trace} » trahit la structure"


def test_le_melange_se_rejoue_a_l_identique() -> None:
    """**La graine est la cle, et c'est ce qui evite un fichier a ne pas
    ouvrir.** La levee rejoue le melange : rien n'a a etre ecrit a cote.
    """
    angles = read_angles(TABLEAU, "gabarit") + read_angles(TABLEAU, "payload")

    assert shuffled(angles, 4712) == shuffled(angles, 4712)


def test_deux_graines_ne_donnent_pas_le_meme_ordre() -> None:
    """Un melange qui ne melangerait pas rendrait l'anonymat decoratif."""
    angles = read_angles(TABLEAU, "gabarit") + read_angles(TABLEAU, "payload")

    assert shuffled(angles, 1) != shuffled(angles, 2)


def test_le_melange_entrelace_les_deux_origines() -> None:
    """**L'ordre d'entree groupe les origines** — tout le gabarit, puis tout le
    payload. Un melange qui les laisserait groupees serait un anonymat de
    facade : le lecteur devinerait la coupure a la premiere lecture.
    """
    angles = read_angles(TABLEAU, "gabarit") + read_angles(TABLEAU, "payload")

    ordre = [angle.origine for angle in shuffled(angles, 4712)]

    coupures = sum(1 for gauche, droite in zip(ordre, ordre[1:], strict=False) if gauche != droite)
    assert coupures > 1, f"les origines restent groupees : {ordre}"


def test_un_rendu_sans_tableau_ne_rend_aucun_angle() -> None:
    """Et il ne rend pas non plus de la prose prise pour des lignes : le
    decoupage vient du lecteur d'import, pas d'une seconde expression."""
    assert read_angles("## SECTION B\n\nDu texte, et une phrase sur C-bis.", "gabarit") == []


def test_une_colonne_angle_absente_ne_fait_pas_lire_une_autre_colonne() -> None:
    """**En cas de doute, rien.** Lire la derniere colonne par defaut ferait
    entrer un niveau de source ou une cote dans le classement, et le jugement
    porterait sur une valeur au lieu d'un argument.
    """
    sans_angle = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf 5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Lyon – Nice | 1N2 | Lyon | 1.85 | 🟢 SAFE | 4 |
"""

    assert read_angles(sans_angle, "gabarit") == []
