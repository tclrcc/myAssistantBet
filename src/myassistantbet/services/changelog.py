"""Dater les changements de cadre, pour que leurs effets soient attribuables.

**Trois lots ont modifie ce qui est produit et ce qui est mesure en une seule
journee.** Dans trois semaines, un mouvement du residu au prix pourra venir du
gabarit, de l'ingestion, ou de rien du tout — et rien ne permettra de trancher.
Les selections portent leur date ; les changements de cadre n'en portaient
aucune.

Le module rend deux choses, et il faut les deux :

- **le journal**, une ligne par lot et par portee, qui donne les **points de
  coupe** ;
- **le decoupage lui-meme** : n'importe quelle population de selections se
  partage en « avant » et « apres » une date, et les deux moities se comparent
  avec le meme calcul qu'ailleurs.

## Ce que le decoupage n'est pas

Ce n'est **pas un test**, et le nombre qu'il rend n'a pas de valeur de preuve.
Un point de coupe choisi apres avoir regarde les donnees est exactement la faute
que la page a mis huit lots a corriger — la cellule `SAFE ∩ confiance 4`,
trouvee en fouillant le tableau puis testee sur le meme echantillon. Ici la date
est **posee d'avance**, par le journal, ce qui evite la multiplicite ; mais deux
moities d'une population de 235 selections restent deux petits echantillons, et
le module ecrit l'ecart sans jamais l'accompagner d'un `p`.

C'est un **outil de lecture**, au meme titre que la fiche de priorite de
recherche : il dit ou regarder, il ne conclut pas.

## Les trois portees

`gabarit` deplace ce que le modele recoit, donc ce qu'il produit. `ingestion`
deplace ce qui entre en base a production constante. `restitution` ne deplace
**rien** et se journalise quand meme : c'est elle qui explique qu'un chiffre ait
*paru* changer un jour ou aucune donnee n'a bouge.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings, get_settings
from ..db import connect, utcnow

#: Les portees, ecrites une fois. **Une enumeration et non du texte libre** :
#: c'est sur elle que le decoupage se filtre, et deux orthographes de
#: « ingestion » feraient deux journaux qui ne se rapprochent plus.
GABARIT = "gabarit"
INGESTION = "ingestion"
RESTITUTION = "restitution"
SCOPES = (GABARIT, INGESTION, RESTITUTION)

SCOPE_LABELS: dict[str, str] = {
    GABARIT: "ce que le modèle reçoit",
    INGESTION: "ce qui entre en base",
    RESTITUTION: "ce que la page montre",
}

#: Le libelle du cadre en vigueur, **incremente a la main**. C'est lui qui nomme
#: une decision ; l'empreinte, elle, se calcule et ne nomme rien. Le jour ou le
#: gabarit change de facon deliberee, cette constante bouge et une ligne entre au
#: journal — les deux gestes vont ensemble, et une empreinte qui bouge sans
#: libelle est le signal qu'on a oublie le second.
FRAME_VERSION = "lot-5"


def fingerprint(paths: list[Path]) -> str:
    """L'empreinte d'un jeu de gabarits, sur leur contenu et leur nom.

    **Le nom entre dans l'empreinte** : deux gabarits dont on echangerait le
    contenu rendraient la meme somme sans elle, et ce n'est pas le meme cadre.

    Tronquee a douze caracteres : elle sert a repondre « est-ce le meme
    gabarit », jamais a resister a une collision volontaire.
    """
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


@dataclass(frozen=True)
class Entry:
    """Un changement de cadre, date."""

    id: int
    day: str
    label: str
    description: str
    scope: str

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS.get(self.scope, self.scope)

    @property
    def line(self) -> str:
        return f"{self.day} · {self.label} — {self.scope_label} : {self.description}"


@dataclass
class Journal:
    """Le journal entier, du plus recent au plus ancien."""

    entries: list[Entry] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.entries

    @property
    def days(self) -> list[str]:
        """Les dates distinctes, decroissantes. **Ce sont les points de coupe.**

        Deux lots livres le meme jour n'en fournissent qu'un, et c'est un fait
        sur le rythme de livraison plutot qu'un defaut du journal : ils ne se
        separeront jamais par la date, et le masquer par une date inventee
        ferait croire a un decoupage qui ne decoupe rien.
        """
        seen: list[str] = []
        for entry in self.entries:
            if entry.day not in seen:
                seen.append(entry.day)
        return seen

    def at(self, day: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.day == day]


def journal(settings: Settings | None = None) -> Journal:
    """Les changements de cadre enregistres, du plus recent au plus ancien."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, day, label, description, scope FROM changelog_mesure "
            "ORDER BY day DESC, id DESC"
        ).fetchall()
    return Journal(
        entries=[
            Entry(
                id=int(row["id"]),
                day=str(row["day"]),
                label=str(row["label"]),
                description=str(row["description"] or ""),
                scope=str(row["scope"]),
            )
            for row in rows
        ]
    )


def add(
    day: str,
    label: str,
    description: str = "",
    scope: str = INGESTION,
    settings: Settings | None = None,
) -> int:
    """Enregistre un changement de cadre. Rend son identifiant.

    Une portee hors vocabulaire est **refusee** plutot que rangee ailleurs : le
    decoupage se filtre dessus, et une valeur inconnue produirait une ligne de
    journal qu'aucune coupe ne verrait jamais — le silence sous un autre nom.
    """
    if scope not in SCOPES:
        raise ValueError(f"Portée inconnue au journal des mesures : {scope!r}")
    if not str(day or "").strip() or not str(label or "").strip():
        raise ValueError("Un changement de cadre porte au moins une date et un libellé.")
    with connect(settings or get_settings()) as conn:
        cursor = conn.execute(
            "INSERT INTO changelog_mesure (day, label, description, scope, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(day).strip(), str(label).strip(), str(description or ""), scope, utcnow()),
        )
    return int(cursor.lastrowid)


#: Libelle de l'entree qui date la mise en service du retour d'experience.
#:
#: **Ce n'est pas un lot livre, c'est un regime qui commence.** Tout ce qui suit
#: cette date est produit par une analyse qui lit ses propres taux ; tout ce qui
#: precede ne l'est pas, et les deux populations ne mesurent pas la meme chose.
#: Sans elle, les taux d'apres se compareraient a ceux d'avant sans que rien ne
#: le signale — la forme exacte du defaut que ce journal existe pour fermer.
FEEDBACK_LABEL = "retour d'expérience — mise en service"


def note_feedback(day: str, settings: Settings | None = None) -> int | None:
    """Date le premier prompt qui transmet des taux. Rend son id, ou `None`.

    **La date est celle du prompt, jamais celle du code.** La bascule demande
    deux choses qui ne tombent pas le meme jour — assez de recul, et le retrait
    de la suspension, qui est une modification de source. Ni la date de
    livraison ni celle du franchissement de seuil ne decrivent donc le moment ou
    le regime change : seul le premier prompt qui **part** avec des taux le fait.

    **Une fois et une seule**, et la garde se lit sur le journal lui-meme :
    `save_prompt` est appele a chaque generation, et une session reelle en
    produit jusqu'a vingt. Un compteur en memoire ne survivrait pas au
    redemarrage ; un drapeau de plus en base serait une seconde ecriture de ce
    que le journal dit deja.

    Portee `gabarit` : ce qui bouge est **ce que le modele recoit**, et c'est de
    la que decoule tout le reste.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        deja = conn.execute(
            "SELECT 1 FROM changelog_mesure WHERE label = ? LIMIT 1", (FEEDBACK_LABEL,)
        ).fetchone()
        if deja is not None:
            return None
    return add(
        day,
        FEEDBACK_LABEL,
        "Premier prompt transmettant les taux par palier, par confiance, par sport, "
        "par competition et par marche. A partir d'ici, les selections produites ne "
        "sont plus independantes de ce qui les mesure : une categorie annoncee faible "
        "cesse d'etre produite, donc cesse d'etre mesurable. Point de coupe obligatoire "
        "pour toute lecture qui traverse cette date.",
        scope=GABARIT,
        settings=settings,
    )


@dataclass(frozen=True)
class Side:
    """Une moitie du decoupage : son effectif, ce qu'elle a gagne, son residu."""

    label: str
    settled: int = 0
    won: int = 0
    expected: float = 0.0

    @property
    def gap(self) -> float:
        """Victoires observees moins victoires payees par les prix."""
        return round(self.won - self.expected, 2)

    @property
    def per_selection(self) -> float | None:
        """L'ecart **rapporte a une selection**, la seule forme comparable.

        Deux moities d'effectifs differents ne se comparent pas par leur ecart
        brut : celle qui porte le plus de selections porte mecaniquement le plus
        d'ecart. C'est la meme regle que l'ecart de residu entre population
        principale et tardive, qui se lit lui aussi par selection.
        """
        return round(self.gap / self.settled, 3) if self.settled else None

    @property
    def line(self) -> str:
        if not self.settled:
            return f"{self.label} : aucune sélection tranchée"
        return (
            f"{self.label} : {self.won} sur {self.settled} · "
            f"{self.expected:.2f} payée(s) · écart {self.gap:+.2f} "
            f"({self.per_selection:+.3f} par sélection)"
        )


@dataclass(frozen=True)
class Split:
    """Le decoupage d'une population autour d'une date du journal.

    **Un outil de lecture, jamais un test.** Aucun `p` n'accompagne l'ecart, et
    c'est delibere : deux moities d'une base de 235 selections sont deux petits
    echantillons, et un seuil pose la-dessus se lirait comme un verdict. Ce qui
    est honnete est de montrer les deux moities et de laisser voir leur
    effectif.
    """

    day: str
    entries: list[Entry] = field(default_factory=list)
    before: Side = field(default_factory=lambda: Side("avant"))
    after: Side = field(default_factory=lambda: Side("après"))

    @property
    def shift(self) -> float | None:
        """L'ecart de residu par selection, apres moins avant.

        Rien quand l'une des deux moities est vide : un decoupage a sens unique
        ne compare rien, et rendre l'ecart de la moitie pleine ferait passer une
        population pour une difference.
        """
        avant, apres = self.before.per_selection, self.after.per_selection
        return None if avant is None or apres is None else round(apres - avant, 3)

    @property
    def readable(self) -> bool:
        """Les deux moities portent-elles quelque chose ?"""
        return bool(self.before.settled and self.after.settled)

    @property
    def line(self) -> str:
        if not self.readable:
            return f"{self.day} · un seul côté porte des sélections tranchées — rien à comparer"
        return f"{self.day} · écart de résidu par sélection : {self.shift:+.3f}"


def split(day: str, settings: Settings | None = None) -> Split:
    """Partage la population **principale** autour d'une date, et rend les deux moities.

    **La population principale seule**, et c'est la regle du projet : melanger
    l'exploratoire ou la tardive detruirait les comparaisons que ces populations
    existent pour rendre possibles. Le decoupage est un axe de plus, pas un axe
    qui remplace les autres.

    La coupe se fait sur `picks.created_at`, la date de la **decision**, et non
    sur celle du match — un changement de gabarit agit sur ce qui est ecrit ce
    jour-la, quelle que soit la date du coup d'envoi. Meme regle que
    l'etalement de `feedback()`.
    """
    settings = settings or get_settings()
    borne = f"{str(day).strip()}T00:00:00Z"
    with connect(settings) as conn:
        entries = [
            Entry(
                id=int(row["id"]),
                day=str(row["day"]),
                label=str(row["label"]),
                description=str(row["description"] or ""),
                scope=str(row["scope"]),
            )
            for row in conn.execute(
                "SELECT id, day, label, description, scope FROM changelog_mesure "
                "WHERE day = ? ORDER BY id",
                (str(day).strip(),),
            )
        ]
        rows = conn.execute(
            "SELECT created_at, result, price FROM picks "
            " WHERE result IN ('win', 'loss') "
            "   AND exploratoire = 0 AND tardive = 0 "
            # `price > 1.0` : `render.is_price` en SQL, troisieme et dernier
            # exemplaire — voir son docstring.
            "   AND price IS NOT NULL AND price > 1.0",
        ).fetchall()
    cotes: dict[str, list[tuple[bool, float]]] = {"avant": [], "apres": []}
    for row in rows:
        cote = cotes["avant"] if str(row["created_at"]) < borne else cotes["apres"]
        cote.append((str(row["result"]) == "win", float(row["price"])))
    return Split(
        day=str(day).strip(),
        entries=entries,
        before=_side("avant", cotes["avant"]),
        after=_side("après", cotes["apres"]),
    )


def _side(label: str, rows: list[tuple[bool, float]]) -> Side:
    """Une moitie, son effectif et ce que ses prix payaient.

    `1/cote` porte la marge du book, donc **surestime** la probabilite vraie :
    l'attendu est trop haut, et l'ecart calcule dessus est conservateur. C'est
    la meme convention que le residu du bloc de tete, et elle ne rouvre aucun
    interdit — aucun devig, aucune projection, seulement des issues tranchees
    comparees a des prix deja enregistres.
    """
    return Side(
        label=label,
        settled=len(rows),
        won=sum(1 for gagne, _ in rows if gagne),
        expected=sum(1.0 / cote for _, cote in rows),
    )
