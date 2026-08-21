"""Verifier que la journalisation des rejets est branchee partout.

**Une table de rejets qui reste vide ne prouve rien.** Elle peut vouloir dire
« rien ne s'est perdu » ou « le chemin qui devait ecrire ici n'y ecrit pas », et
les deux se ressemblent trait pour trait — c'est le defaut caracteristique du
projet, rencontre pour la sixieme fois.

Cette commande injecte un exemplaire **malforme** de chacun des quatre formats
structures sur chacun des chemins d'import recenses, et **echoue** si l'un
d'eux ne produit pas de rejet. Elle tourne en integration continue.

    uv run myassistantbet-selfcheck

Elle travaille dans une **base temporaire**, jamais sur la base servie : elle
ecrit des selections volontairement cassees, et le projet a deja paye une fois
d'avoir laisse un `TestClient` appliquer une migration sur la production.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path

from . import db
from .backup import TEMP_PREFIX
from .config import Settings, get_settings
from .services import board as board_service
from .services import imports_raw, picks_import, write_paths
from .services import ingestion as ingestion_service
from .services.manual import build, save

logger = logging.getLogger(__name__)

#: Les champs caches du formulaire d'import, tels que le navigateur les
#: renverrait. Passer par le rendu reel plutot que par l'objet : c'est le chemin
#: complet qu'on verifie, et un champ oublie cote gabarit ne se verrait pas.
_HIDDEN = re.compile(r'name="([a-z_0-9]+)"\s+value="([^"]*)"')

#: Un exemplaire **malforme** de chaque format structure, et le type de rejet
#: qu'il doit produire. Malforme et non absent : un format absent ne prouve rien
#: sur la journalisation, c'est un format **recu et refuse** qu'il faut voir
#: arriver en base.
#:
#: **Cette table est le numerateur, jamais le denominateur.** Ce qu'il faut
#: couvrir se derive du registre des chemins d'ecriture
#: (`write_paths.declared_block_types`) : une famille declaree sans exemplaire
#: ici fait tomber le controle au lieu d'etre sautee en silence. C'est la
#: correction du « 8 sur 8 » du lot 2, ou les deux nombres etaient ecrits a la
#: main et ne pouvaient donc pas se contredire.
BROKEN: dict[str, tuple[str, str]] = {
    "conf": (
        '```conf\n{"match": "M1", "confiance": 4, "faits": [],}\n```\n',
        ingestion_service.CONF,
    ),
    "combo": (
        '```combo\n{"type": "moyen", "jambes": ["M1"]}\n```\n',
        ingestion_service.COMBO,
    ),
    "score_sets": (
        "| Personne Inconnue – Autre Inconnu | 2-0 | |\n",
        ingestion_service.SCORE_SETS,
    ),
    "selection": (
        "| 2 | Lyon – Adv Lyon | O/U 2.5 | Over 2.5 | 1.90 | 🟢 SAFE | 4 |\n",
        ingestion_service.SELECTION,
    ),
    # Une ligne de section C-bis sur un match **deja retenu en section C** :
    # « une seule selection par match, tous tableaux confondus » est une
    # contrainte qui ne tombe pas, donc elle est refusee. Malformee au sens de la
    # section et non du JSON — c'est la seule forme que cette famille puisse
    # prendre, et c'est le registre qui a exige qu'elle soit couverte.
    #
    # **L'exemplaire d'origine etait une ligne C-bis en palier sur**, et ce
    # refus-la a saute : l'appartenance a C-bis se decide par la confiance et le
    # caractere speculatif, jamais par le prix. Un exemplaire choisi parmi
    # plusieurs refus possibles se perime avec celui qu'il exploite — le controle
    # a bien signale sa disparition, ce qui est exactement son role.
    "exploratoire": (
        "\n### C-bis. Sélections exploratoires\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|-------|--------|-----------|------|--------|--------|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 3.20 | 🟠 ULTRA FUN | 2 |\n",
        ingestion_service.EXPLORATOIRE,
    ),
}

#: Le tableau minimal auquel les exemplaires malformes s'ajoutent. Sans lui,
#: `parse_table` refuse tout le collage et le rejet observe ne dirait rien du
#: format teste.
TABLE = (
    "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
    "|---|-------|--------|-----------|------|--------|--------|\n"
    "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
)


@dataclass
class Check:
    """Un format, un chemin, et ce qui est arrive."""

    path: str
    fmt: str
    seen: bool
    detail: str = ""

    @property
    def line(self) -> str:
        marque = "ok " if self.seen else "MANQUE"
        return f"  {marque}  {self.path:<12} {self.fmt:<12} {self.detail}"


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    #: Les familles de blocs que le **registre** declare, et les chemins
    #: d'import recenses. Le produit des deux est le nombre de controles
    #: attendus : sans lui, « 8 sur 8 » compare une liste a elle-meme.
    families: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    #: Les desaccords entre les trois vues du registre d'ecriture.
    #:
    #: **C'est ce qui manquait, et le lot 9 l'a prouve.** `add_pick` a perdu sa
    #: declaration — un `@dataclass` glisse entre le decorateur et la fonction —
    #: et ce controle affichait `10 sur 10` pendant ce temps. La cause n'est pas
    #: qu'il regardait mal : c'est que son denominateur, `declared_block_types()`,
    #: **agrege les familles**. La declaration etait passee sur la classe, donc
    #: les memes trois familles restaient declarees, et l'agregat ne bougeait pas
    #: d'un mot. Le registre restait plein, avec la mauvaise cle.
    #:
    #: Un controle dont le denominateur vient de ce qu'il controle ne peut pas
    #: voir un deplacement a l'interieur. Celui-ci lit donc **aussi** la source,
    #: par `write_paths.mismatches()` — la meme fonction que le test, ecrite une
    #: fois : deux implementations de la meme regle auraient diverge.
    mismatches: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.seen]

    @property
    def expected(self) -> int:
        return len(self.families) * len(self.paths)

    @property
    def lines(self) -> list[str]:
        out = [
            f"{len(self.checks)} contrôle(s) sur {self.expected} attendu(s) — "
            f"{len(self.paths)} chemin(s) × {len(self.families)} famille(s) dérivée(s) du "
            f"registre d'écriture ({', '.join(self.families) or 'aucune'}), "
            f"{len(self.failures)} manque(s)"
        ]
        out += [check.line for check in self.checks]
        for ecart in self.mismatches:
            out.append(f"  REGISTRE  {ecart}")
        if self.mismatches:
            out.append(
                "Le registre d'écriture ne décrit plus le code : une fonction insère sans "
                "être déclarée, ou une déclaration s'est posée ailleurs que sur une "
                "fonction. Le compte ci-dessus reste vert parce qu'il dérive du registre — "
                "c'est précisément ce qu'il ne peut pas voir tout seul."
            )
        if self.failures:
            out.append(
                "Un chemin d'import ne journalise pas ses rejets : une table vide y "
                "voudrait dire « rien ne s'est perdu » alors qu'elle veut dire « rien "
                "n'écrit ici »."
            )
        return out


def _seed(settings: Settings) -> int:
    """Une session minimale : deux matchs a venir et un prompt archive."""
    session_id = 0
    for nom in ("Lyon", "Nice"):
        event_id = save(
            build(
                "football",
                "Match amical",
                nom,
                f"Adv {nom}",
                "2099-01-01",
                "20:45",
                f"{nom} 1.45",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        session_id = board_service.toggle_selection(event_id, True, settings)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'selfcheck', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=settings,
    )
    return session_id


def _by_form(session_id: int, raw: str, settings: Settings) -> set[str]:
    """Le chemin ordinaire : **les deux vraies routes**, apercu puis import.

    Appeler `build_preview` seul ne verifierait que la moitie du chemin : les
    rejets d'ecriture — une ligne refusee par une garde — ne naissent qu'a
    l'import. C'est justement ce demi-controle qui a laisse le rejeu muet, et le
    banc l'a attrape.

    Le `TestClient` est importe ici et pas en tete : c'est un outil de
    verification, il n'a rien a faire dans le chemin servi.
    """
    from fastapi.testclient import TestClient

    from .main import app

    with TestClient(app) as client:
        apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": raw})
        champs = dict(_HIDDEN.findall(apercu.text))
        donnees = {
            "rejects": unescape(champs.get("rejects", "[]")),
            "import_id": champs.get("import_id", ""),
            # **Les deux confirmations sont cochees, comme un humain le ferait.**
            # Ce banc mesure la journalisation des refus **d'ecriture** ; les
            # gardes d'import sont une autre question, et les laisser mordre ici
            # ferait passer un chemin pour muet alors qu'il n'a simplement
            # jamais ete atteint. C'est ce qui s'est produit en livrant le compte
            # des controles : le banc a vire au rouge sur `selection`, et c'est
            # exactement le travail qu'on attend de lui.
            "confirm_partial": "1",
            "confirm_controls": "1",
        }
        # Toutes les lignes proposees sont cochees : une ligne decochee ne passe
        # pas par l'ecriture, donc ne dirait rien de sa journalisation.
        for index, ligne in enumerate(picks_import.build_preview(session_id, raw, settings).picks):
            numero = ligne.index
            donnees |= {
                f"keep_{numero}": "1",
                f"event_{numero}": str(ligne.event_id or ""),
                f"market_{numero}": ligne.market,
                f"selection_{numero}": ligne.selection,
                f"tier_{numero}": ligne.tier or "safe",
                f"price_{numero}": ligne.price,
            }
            _ = index
        client.post(f"/history/{session_id}/picks/import", data=donnees)

    return {
        str(row["block_type"])
        for row in db.query("SELECT block_type FROM ingestion_rejects", settings=settings)
    }


def _by_replay(session_id: int, raw: str, settings: Settings) -> set[str]:
    """Le chemin du rejeu : un collage conserve, relu par la commande."""
    from .replay import replay

    import_id = imports_raw.record(session_id, raw, imports_raw.REPLAY, settings)
    if import_id is None:
        return set()
    return {
        reject.block_type for reject in replay(import_id, write=True, settings=settings).rejects
    }


#: Les chemins d'**entree** : par ou un collage atteint la base. Ils restent
#: enumeres a la main, et c'est assume — ce sont des routes, pas des fonctions
#: d'ecriture, et rien dans la source ne les distingue d'une route quelconque.
#:
#: Ce qui est desormais garanti est l'autre moitie, celle qui manquait : les
#: **familles de blocs** couvertes ne s'ecrivent plus ici mais se derivent du
#: registre des chemins d'ecriture, qu'un test tient complet
#: (`tests/test_write_paths.py`). Un chemin d'ecriture ajoute sans declaration
#: fait echouer la suite ; c'est ce que la regle de `CONTRIBUTING.md` ne faisait
#: pas, et `replay` l'a prouve en la violant le jour meme de sa redaction.
def _by_attach(session_id: int, raw: str, settings: Settings) -> set[str]:
    """Le chemin du rattachement : `--rattacher`, qui complete sans creer.

    **Il etait muet, et c'est la deuxieme fois sur ce fichier.**
    `CONTRIBUTING.md` dit de la premiere : « `myassistantbet-replay` a ete ecrit
    le meme jour et par la meme main que cette phrase, et il a laisse tomber ses
    echecs d'ecriture sans les journaliser ». Le rattachement l'a refait, et rien
    ne l'aurait dit — `PATHS` s'enumere a la main, donc un chemin d'entree ajoute
    sans son entree ici ne se controle nulle part.
    """
    from .replay import attach

    import_id = imports_raw.record(session_id, raw, imports_raw.REPLAY, settings)
    if import_id is None:
        return set()
    return {
        reject.block_type for reject in attach(import_id, write=True, settings=settings).rejects
    }


PATHS: dict[str, Callable[[int, str, Settings], set[str]]] = {
    "formulaire": _by_form,
    "rejeu": _by_replay,
    "rattachement": _by_attach,
}

#: Les familles qu'un chemin **ne peut pas** produire, avec la raison.
#:
#: **Une exemption se declare, elle ne se devine pas.** Sans cette table, un
#: chemin qui n'ecrit qu'une partie des familles apparaitrait en manque sur les
#: autres, et le premier reflexe serait de le retirer de `PATHS` — c'est-a-dire
#: de le rendre muet a nouveau, exactement ce qu'on vient de corriger.
#:
#: Le sens est celui du controle et non celui de la commodite : une famille n'y
#: entre que si le chemin est **structurellement** incapable de la produire.
IMPOSSIBLE: dict[str, dict[str, str]] = {
    "rattachement": {
        "selection": (
            "ce chemin ne crée aucune sélection — il complète celles qui existent, "
            "donc il ne peut pas en refuser une"
        ),
    },
}


def run(settings: Settings | None = None) -> Report:
    """Injecte chaque format malforme sur chaque chemin. Rend ce qui manque.

    **L'enumeration part du registre d'ecriture, jamais de `BROKEN`.** Une
    famille declaree par un `@writes` et sans exemplaire malforme produit un
    controle en echec, avec son motif : c'est le seul moyen qu'un chemin
    d'ecriture ajoute demain ne passe pas au travers en silence.
    """
    write_paths.load()
    familles = write_paths.declared_block_types()
    # **La source, en plus du registre.** Un controle dont le denominateur vient
    # de ce qu'il controle ne peut pas voir un deplacement a l'interieur : c'est
    # ce qui l'a laisse afficher « 10 sur 10 » pendant que `add_pick` n'etait
    # plus declaree.
    report = Report(families=familles, paths=tuple(PATHS), mismatches=write_paths.mismatches())
    for chemin, executer in PATHS.items():
        for nom in familles:
            motif = IMPOSSIBLE.get(chemin, {}).get(nom)
            if motif:
                report.checks.append(
                    Check(path=chemin, fmt=nom, seen=True, detail=f"sans objet — {motif}")
                )
                continue
            if nom not in BROKEN:
                report.checks.append(
                    Check(
                        path=chemin,
                        fmt=nom,
                        seen=False,
                        detail=(
                            "aucun exemplaire malformé : cette famille est déclarée au "
                            "registre d'écriture et n'est donc vérifiée nulle part"
                        ),
                    )
                )
                continue
            casse, attendu = BROKEN[nom]
            with _temporary() as temporaire:
                session_id = _seed(temporaire)
                vus = executer(session_id, TABLE + "\n" + casse, temporaire)
                en_base = {
                    row["block_type"]
                    for row in db.query(
                        "SELECT block_type FROM ingestion_rejects", settings=temporaire
                    )
                }
            report.checks.append(
                Check(
                    path=chemin,
                    fmt=nom,
                    seen=attendu in vus and attendu in en_base,
                    detail=f"attendu {attendu}, vu {sorted(en_base) or 'rien'}",
                )
            )
    _ = settings
    return report


class _temporary:
    """Une base jetable, migree, le temps d'un controle.

    **Jamais la base servie** : cette commande ecrit des selections
    volontairement cassees, et le projet a deja paye une fois d'avoir laisse un
    `TestClient` appliquer une migration sur la production.
    """

    def __enter__(self) -> Settings:
        # Le prefixe rend l'appartenance explicite : sans lui, ce repertoire est
        # indiscernable de celui de n'importe quel programme de la machine, et la
        # purge ne pourrait pas le reclamer sans emporter le reste.
        self.dossier = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
        # `DB_PATH` **et** le cache de configuration : les routes appellent
        # `get_settings()` elles-memes, et sans cette bascule le controle
        # ecrirait ses selections cassees dans la base servie. Le projet a deja
        # paye une fois d'avoir laisse un `TestClient` toucher la production.
        self.precedent = os.environ.get("DB_PATH")
        os.environ["DB_PATH"] = str(self.dossier / "selfcheck.db")
        get_settings.cache_clear()
        self.settings = get_settings()
        db.run_migrations(self.settings, deliberate=True)
        return self.settings

    def __exit__(self, *exc: object) -> None:
        if self.precedent is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = self.precedent
        get_settings.cache_clear()
        shutil.rmtree(self.dossier, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Vérifie que chaque chemin d'import journalise ses rejets. "
            "Travaille dans une base temporaire."
        )
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)-8s %(message)s")
    report = run()
    faute = bool(report.failures or report.mismatches)
    for line in report.lines:
        print(line, file=sys.stderr if faute else sys.stdout)
    return 1 if faute else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
