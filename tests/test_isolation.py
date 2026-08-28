"""Aucun test n'ecrit dans un chemin de l'instance servie.

**Regle de la moitie ecriture**, apprise deux fois : une migration est partie
sur la base servie le 21/08/2026 parce qu'un override n'avait pas pris, et
`db.scratch_copy` en est sortie. Le 28/08, la meme famille a ete trouvee un cran
plus bas — `isolated_settings` redirigeait `DB_PATH`, `DEV_CACHE_DIR` et
`BACKUP_DIR`, et **pas `UPLOAD_DIR`** : les bancs de capture de coupon ecrivaient
dans `data/uploads` de l'instance servie.

**Le symptome etait masque par un detail sans rapport** : le nom d'un fichier de
capture porte l'empreinte de son contenu, donc chaque execution ecrasait le meme
fichier au lieu d'en ajouter un. Le compte ne grossissait jamais. Prouve par
releve des dates de modification avant et apres une execution, jamais deduit du
code.

**Le banc enonce la propriete, pas le champ.** Ecrit sur `UPLOAD_DIR`, il aurait
disparu avec lui ; ecrit sur « tout chemin des reglages », il garde les trois
autres et le cinquieme du jour ou quelqu'un l'ajoute.
"""

from __future__ import annotations

from pathlib import Path

from myassistantbet.config import Settings, get_settings


def chemins_de_reglage() -> list[str]:
    """Les champs de `Settings` qui designent un chemin.

    Lus sur le modele et non recopies : une liste tenue a la main serait la
    seconde ecriture que ce banc existe pour rendre inutile.
    """
    return [
        nom
        for nom, champ in Settings.model_fields.items()
        if isinstance(champ.default, Path) or champ.annotation is Path
    ]


def test_le_modele_porte_bien_des_chemins() -> None:
    """La presence avant l'absence : sans ce banc, le suivant passerait sur une
    liste vide et ne garderait rien."""
    trouves = chemins_de_reglage()

    assert trouves, "aucun chemin trouve dans Settings : le critere ne voit plus rien"
    assert "db_path" in trouves


def test_aucun_chemin_de_reglage_ne_pointe_hors_du_bac_a_sable(
    isolated_settings: Settings, tmp_path: Path
) -> None:
    """Chaque chemin des reglages, en test, vit sous le repertoire du test.

    C'est la propriete entiere : `db_path` la porte depuis toujours, les autres
    la portent depuis ce banc, et un champ ajoute demain la portera ou fera
    rougir la suite.
    """
    settings = get_settings()
    dehors = []
    for nom in chemins_de_reglage():
        valeur = Path(getattr(settings, nom)).resolve()
        if not valeur.is_relative_to(tmp_path.resolve()):
            dehors.append(f"{nom} = {valeur}")

    assert not dehors, "chemins hors du bac a sable : " + ", ".join(dehors)


def test_le_depot_ne_recoit_aucune_ecriture_de_reglage(isolated_settings: Settings) -> None:
    """Le cas concret qui a fonde le banc : aucun chemin ne designe `data/`.

    Redondant avec le precedent tant que les tests tournent depuis la racine du
    depot, et c'est voulu — celui-ci nomme le degat, celui-la nomme la regle.
    """
    depot = Path(__file__).resolve().parent.parent
    settings = get_settings()
    for nom in chemins_de_reglage():
        valeur = Path(getattr(settings, nom)).resolve()
        assert not valeur.is_relative_to(depot / "data"), f"{nom} pointe dans le depot servi"
