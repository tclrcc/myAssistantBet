"""Le combine comme **objet d'analyse**, jamais comme pari pose.

La page pose deux questions distinctes — ce que valent les selections, ce que
valent les paris — et un combine produit par le modele appartient sans ambiguite
a la premiere. S'il devient un pari, c'est un geste fait apres, et qui ne le
change pas.

D'ou la separation stricte d'avec `coupons` : `coupons.attach()` est le **seul**
ecrivain de `picks.played`, et le faire passer par la ferait apparaitre comme
paris poses des combines que personne n'a joues. Le mot « joue » n'apparait donc
nulle part ici.

**Un combine reste rattache a un prompt** (`prompt_id NOT NULL`), et une jambe
venue d'un autre prompt est refusee. Les selections de deux prompts n'ont jamais
ete comparees entre elles : chaque instance a choisi dans son lot, avec son quota
et son budget de recherche propres. Mesure du 14/08/2026 — un match est rendu
2,23 fois en moyenne dans sa session, jusqu'a 13 fois : deux jambes venues de
deux prompts sur le meme match seraient deux tirages du meme match presentes
comme deux selections.

**Aucun taux de reussite par combine**, et ce n'est pas un oubli : au taux de
jambe constate (78/137, 57 %), un combine de dix jambes se tranche favorablement
une fois sur 280. Son taux ne sera jamais mesurable, quel que soit le temps qu'on
lui laisse. Le combine est un **regroupement** ; les jambes restent les unites de
mesure, comptees individuellement dans les statistiques existantes. Ce qui garde
un sens est **combien de jambes tombent** et **a quel rang la premiere tombe**.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .history import HistoryError
from .ingestion import COMBO, JSON_INVALID, SCHEMA_INVALID, Reject, read_bodies
from .write_paths import writes

logger = logging.getLogger(__name__)

#: Les deux combines que le gabarit demande. Le court est defini par sa forme —
#: quatre jambes, concentre ; le long prend ce que le lot autorise.
KINDS = ("court", "long")

#: Les trois motifs d'arret du combine long, dans l'ordre ou ils peuvent tomber.
#: **Aucun n'est un echec** : c'est leur repartition qui dira si la cible est
#: bien reglee — toujours `cible` et elle peut monter, toujours `confiance` et le
#: lot est la contrainte, la cible n'y pouvant rien.
STOP_REASONS = ("cible", "plafond", "confiance")

STOP_LABELS = {
    "cible": "cible atteinte",
    "plafond": "plafond du lot atteint",
    "confiance": "plus de jambe à confiance 3",
}

#: Le bloc structure, tel que le gabarit le demande. La cloture est `combo` et
#: non `conf` ou `json` : `confidence.BLOCK` ne lit que ces deux-la, donc les
#: deux familles de blocs ne peuvent pas se manger l'une l'autre.
BLOCK = re.compile(r"```combo\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)

#: Les cles qui font qu'un objet **est** un combine, quand il arrive sans sa
#: cloture — le cas d'un copier-coller depuis le rendu. `jambes` appartient en
#: propre a cette famille ; `type`, que les deux portent, ne peut pas trancher.
COMBO_KEYS = ("jambes", "legs")


def is_combo(payload: dict) -> bool:
    """Cet objet est-il un combine, lu sur sa seule forme ?"""
    return any(key in payload for key in COMBO_KEYS)


#: Ecart relatif au-dela duquel la cote ecrite et la cote recalculee ne
#: decrivent plus le meme combine. Il ne s'agit pas d'une tolerance de calcul
#: mais d'un arrondi de saisie : une cote a deux decimales par jambe, dix
#: jambes, et le produit derive de quelques dixiemes de pour cent.
PRICE_GAP = 0.01


class ComboError(HistoryError):
    """Un bloc de combine illisible, ou une jambe qui ne peut pas en etre une.

    Le motif accompagne le message, comme pour `ClaimError` : « le JSON ne se
    relit pas » et « un champ manque » ne se reparent pas au meme endroit.
    """

    def __init__(self, message: str, reason: str = SCHEMA_INVALID) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ParsedCombo:
    """Un bloc ```combo lu, avant tout rapprochement avec des selections."""

    kind: str
    #: Les reperes de bloc (`M3`, `M7`), **dans l'ordre d'ajout**. Cet ordre
    #: n'est pas decoratif : c'est lui qui permettra de dire a quel rang la
    #: premiere jambe tombe.
    marks: tuple[str, ...]
    declared_price: float | None
    target_price: float | None
    stop_reason: str | None
    #: L'endroit du collage brut d'ou le bloc vient.
    start: int | None = None
    end: int | None = None

    @property
    def label(self) -> str:
        return f"combiné {self.kind} ({len(self.marks)} jambe(s))"


@dataclass
class ComboReading:
    """Ce qu'un rendu portait comme combines."""

    combos: list[ParsedCombo] = field(default_factory=list)
    #: Les blocs qu'on n'a pas su lire, nommes un par un et **avec leur brut**.
    #: Ils ne coutent que leur combine — jamais l'import du tableau, qui passe
    #: par un autre canal.
    rejects: list[Reject] = field(default_factory=list)

    @property
    def rejected(self) -> list[str]:
        """Les motifs seuls. Derive, jamais tenu a cote — deux listes portant la
        meme information auraient diverge au premier motif ajoute d'un cote."""
        return [reject.detail for reject in self.rejects]


def _price(raw: object, champ: str) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(str(raw).replace(",", "."))
    except ValueError as exc:
        raise ComboError(f"{champ} illisible : {raw!r}") from exc
    if value <= 1:
        raise ComboError(f"{champ} invraisemblable : {value}")
    return value


def parse(body: str) -> ParsedCombo:
    """Un bloc ```combo, ou `ComboError`."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ComboError(f"bloc illisible : {exc}", JSON_INVALID) from exc
    if not isinstance(payload, dict):
        raise ComboError("un bloc de combine doit etre un objet")

    kind = str(payload.get("type") or "").strip().lower()
    if kind not in KINDS:
        raise ComboError(f"type de combine inconnu : {payload.get('type')!r}")

    raw_marks = payload.get("jambes") or payload.get("legs") or []
    if not isinstance(raw_marks, list) or not raw_marks:
        raise ComboError("un combine sans jambe n'en est pas un")
    marks = tuple(str(mark).strip().upper() for mark in raw_marks if str(mark).strip())
    if len(set(marks)) != len(marks):
        raise ComboError("deux jambes portent le meme repere")

    stop = str(payload.get("arret") or payload.get("stop") or "").strip().lower()
    # Le court n'en a pas : son nombre de jambes est fixe d'avance, donc rien
    # ne l'arrete. Un motif inconnu est refuse plutot qu'ecrit — `load_for`
    # l'ignorerait, et le motif paraitrait pose sans aucun effet.
    if stop and stop not in STOP_REASONS:
        raise ComboError(f"motif d'arret inconnu : {payload.get('arret')!r}")

    return ParsedCombo(
        kind=kind,
        marks=marks,
        declared_price=_price(payload.get("cote"), "cote"),
        target_price=_price(payload.get("cible"), "cible"),
        stop_reason=stop or None,
    )


def read_combos(raw: str) -> ComboReading:
    """Tous les blocs ```combo d'un rendu, **cloture ou non**.

    Meme reparation que pour les blocs de confiance : un copier-coller depuis le
    rendu consomme la cloture, et le JSON arrive nu.
    """
    reading = ComboReading()
    for index, body in enumerate(read_bodies(raw or "", BLOCK, is_combo), start=1):
        try:
            combo = parse(body.text)
        except ComboError as exc:
            reading.rejects.append(
                Reject(
                    block_type=COMBO,
                    reason=exc.reason,
                    detail=f"bloc combiné {index} : {exc}",
                    payload=body.text,
                    start=body.start,
                    end=body.end,
                )
            )
            continue
        reading.combos.append(replace(combo, start=body.start, end=body.end))
    return reading


@dataclass(frozen=True)
class Leg:
    """Une jambe : une selection ordinaire, vue depuis son combine."""

    pick_id: int
    position: int
    tier: str
    tier_label: str
    price: float | None
    confidence: int | None
    event_id: int | None
    market: str
    selection: str
    result: str


def product(prices: Sequence[float | None]) -> float | None:
    """Le produit de cotes, ou `None` des qu'une manque.

    **Incalculable plutot que faux** : omettre une jambe sans prix donnerait une
    cote plus basse que la vraie, sans que rien ne le dise. Une liste vide n'est
    pas non plus un produit — `math.prod` rendrait 1.0, qui se lirait comme une
    cote.

    Ecrite ici parce que le combine est le premier a en avoir eu besoin, et
    appelee telle quelle par le recapitulatif du jour : c'est la regle de la
    jambe manquante qui est partagee, pas la multiplication.
    """
    valeurs = list(prices)
    if not valeurs or any(prix is None for prix in valeurs):
        return None
    return math.prod(prix for prix in valeurs if prix is not None)


@dataclass
class Combo:
    """Un combine relu en base."""

    id: int
    session_id: int
    prompt_id: int
    kind: str
    target_price: float | None
    declared_price: float | None
    stop_reason: str | None
    legs: list[Leg] = field(default_factory=list)

    @property
    def computed_price(self) -> float | None:
        """La cote **recalculee depuis les jambes**, jamais celle qu'on a lue.

        Le produit ecrit dans la reponse est une affirmation du modele ; celui-ci
        est une consequence des prix enregistres.

        La regle vit dans `product`, et cette propriete l'appelle : le
        recapitulatif du jour multiplie les memes prix pour ses propositions, et
        deux ecritures du meme produit auraient fini par ne pas traiter la jambe
        sans prix de la meme facon.
        """
        return product([leg.price for leg in self.legs])

    @property
    def price_gap(self) -> float | None:
        """L'ecart relatif entre la cote ecrite et la cote recalculee."""
        computed = self.computed_price
        if computed is None or self.declared_price is None:
            return None
        return (self.declared_price - computed) / computed

    @property
    def price_mismatch(self) -> bool:
        gap = self.price_gap
        return gap is not None and abs(gap) > PRICE_GAP

    @property
    def target_reached(self) -> bool | None:
        computed = self.computed_price
        if computed is None or self.target_price is None:
            return None
        return computed >= self.target_price

    @property
    def stop_label(self) -> str:
        return STOP_LABELS.get(self.stop_reason or "", "non renseigné")

    @property
    def confidences(self) -> list[int]:
        return [leg.confidence for leg in self.legs if leg.confidence is not None]

    @property
    def confidence_min(self) -> int | None:
        """La plus basse des jambes.

        C'est le signal a surveiller : une confiance minimale qui chute quand la
        cible est atteinte de justesse dit qu'une jambe a ete ajoutee pour
        franchir la cible.
        """
        return min(self.confidences) if self.confidences else None

    @property
    def confidence_median(self) -> float | None:
        return statistics.median(self.confidences) if self.confidences else None

    @property
    def events(self) -> set[int]:
        return {leg.event_id for leg in self.legs if leg.event_id is not None}

    def by_tier(self) -> list[tuple[str, str, int, float | None]]:
        """La repartition par palier : combien de jambes, et **la cote apportee
        par chaque groupe**.

        C'est ce qui remplace la designation d'un maillon fragile sur un combine
        long : le lecteur voit ou la cote a ete achetee et juge lui-meme. Les
        deux se calculent sans rien demander au modele.
        """
        groupes: dict[str, list[Leg]] = {}
        for leg in self.legs:
            groupes.setdefault(leg.tier, []).append(leg)
        rendus = []
        for tier, legs in groupes.items():
            prix = [leg.price for leg in legs if leg.price is not None]
            apport = math.prod(prix) if len(prix) == len(legs) else None
            rendus.append((tier, legs[0].tier_label, len(legs), apport))
        rendus.sort(key=lambda item: -item[2])
        return rendus

    @property
    def first_loss_rank(self) -> int | None:
        """Le rang de la premiere jambe perdue, dans l'ordre d'ajout.

        `None` quand aucune n'est perdue — ce qui n'est pas la meme chose qu'un
        combine sans resultat, et se lit avec `settled`.
        """
        for leg in sorted(self.legs, key=lambda item: item.position):
            if leg.result == "loss":
                return leg.position + 1
        return None

    @property
    def legs_won(self) -> int:
        return sum(1 for leg in self.legs if leg.result == "win")

    @property
    def legs_settled(self) -> int:
        return sum(1 for leg in self.legs if leg.result in ("win", "loss"))


def jaccard(left: Combo, right: Combo) -> float:
    """Le recouvrement de deux combines, sur leurs **selections**.

    Meme indice que la matrice de `/stats`, et pour la meme raison : une
    grandeur unique et symetrique se compare d'une paire a l'autre, la ou une
    double inclusion disait la meme chose en deux conditions.

    **Il est impose par le vivier, pas par la redaction.** Sur la moitie des
    sessions mesurees, un 4-jambes et un 10-jambes disjoints sont
    structurellement impossibles : il faudrait 14 matchs distincts et le vivier
    n'en offre que 5 a 11. Et si les deux se batissent par tri decroissant de
    surete a partir du meme vivier, le court est inclus dans le long par
    construction — 4/10 = 0,40, valeur a attendre et non accident. Il s'affiche ;
    il ne s'interdit pas.
    """
    gauche = {leg.pick_id for leg in left.legs}
    droite = {leg.pick_id for leg in right.legs}
    union = gauche | droite
    return len(gauche & droite) / len(union) if union else 0.0


def _lot_of(conn, prompt_id: int) -> set[int]:
    return {
        int(row["event_id"])
        for row in conn.execute(
            "SELECT event_id FROM prompt_events WHERE prompt_id = ?", (prompt_id,)
        )
    }


@writes(COMBO)
def record(
    session_id: int,
    prompt_id: int,
    *,
    kind: str,
    pick_ids: list[int],
    declared_price: float | None = None,
    target_price: float | None = None,
    stop_reason: str | None = None,
    import_id: int | None = None,
    span: tuple[int | None, int | None] = (None, None),
    settings: Settings | None = None,
) -> int:
    """Enregistre un combine. Renvoie son id.

    **Une jambe venue d'un autre prompt est refusee**, et c'est la contrainte
    centrale du module : les selections de deux prompts n'ont jamais ete
    comparees entre elles. Le controle porte sur le **lot du prompt**
    (`prompt_events`), parce que c'est lui qui definit ce qu'une instance a eu
    sous les yeux.

    Une selection sans evenement y echappe : rien ne permet de la rattacher a un
    lot, et la refuser interdirait un combine que le prompt autorise.
    """
    settings = settings or get_settings()
    if kind not in KINDS:
        raise ComboError(f"type de combiné inconnu : {kind!r}")
    if stop_reason and stop_reason not in STOP_REASONS:
        raise ComboError(f"motif d'arrêt inconnu : {stop_reason!r}")
    if not pick_ids:
        raise ComboError("Un combiné sans jambe n'en est pas un.")
    if len(set(pick_ids)) != len(pick_ids):
        raise ComboError("Une même sélection ne peut pas être deux jambes.")

    with connect(settings) as conn:
        if not conn.execute(
            "SELECT 1 FROM prompts WHERE id = ? AND session_id = ?", (prompt_id, session_id)
        ).fetchone():
            raise ComboError("Ce prompt n'appartient pas à cette session.")

        rows = conn.execute(
            "SELECT id, event_id, session_id FROM picks WHERE id IN "
            f"({','.join('?' * len(pick_ids))})",
            tuple(pick_ids),
        ).fetchall()
        if len(rows) != len(pick_ids):
            raise ComboError("Certaines jambes n'existent pas.")
        if any(int(row["session_id"]) != session_id for row in rows):
            raise ComboError("Certaines jambes n'appartiennent pas à cette session.")

        lot = _lot_of(conn, prompt_id)
        dehors = [
            row["id"]
            for row in rows
            if row["event_id"] is not None and int(row["event_id"]) not in lot
        ]
        if dehors:
            raise ComboError(
                "Certaines jambes viennent d'un autre prompt : elles n'ont jamais été "
                "comparées à celles-ci."
            )

        cursor = conn.execute(
            "INSERT INTO combos (session_id, prompt_id, kind, target_price, declared_price, "
            "stop_reason, import_id, offset_start, offset_end, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                prompt_id,
                kind,
                target_price,
                declared_price,
                stop_reason,
                import_id,
                span[0],
                span[1],
                utcnow(),
            ),
        )
        combo_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO combo_legs (combo_id, pick_id, position) VALUES (?, ?, ?)",
            [(combo_id, pick_id, index) for index, pick_id in enumerate(pick_ids)],
        )

    logger.info(
        "Combine %d enregistre : %s, %d jambe(s), prompt %d",
        combo_id,
        kind,
        len(pick_ids),
        prompt_id,
    )
    return combo_id


def list_for_session(session_id: int, settings: Settings | None = None) -> list[Combo]:
    """Les combines d'une session, jambes comprises."""
    return _load(session_id, settings)


def list_all(settings: Settings | None = None) -> list[Combo]:
    """Tous les combines de la base, du plus recent.

    **Ils etaient enregistres et jamais restitues** : la page de statistiques
    n'en portait aucune section, si bien qu'un combine se lisait sur la feuille
    de sa session et nulle part ailleurs. Le releve reste un **compte et un
    ecart**, jamais un taux de reussite — au taux de jambe constate, un combine
    de dix jambes passe une fois sur 280, et son taux ne sera jamais mesurable.
    """
    return _load(None, settings)


def _load(session_id: int | None, settings: Settings | None = None) -> list[Combo]:
    """Les combines d'une session, ou de toute la base. **Une seule lecture.**

    Ecrite une fois : deux requetes paralleles auraient fini par ne plus rendre
    les memes jambes, et c'est le piege deja paye deux fois par l'assembleur de
    contexte.
    """
    settings = settings or get_settings()
    where = "WHERE session_id = ?" if session_id is not None else ""
    params: tuple[int, ...] = (session_id,) if session_id is not None else ()
    with connect(settings) as conn:
        tier_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM tiers")
        }
        combos = {
            int(row["id"]): Combo(
                id=int(row["id"]),
                session_id=int(row["session_id"]),
                prompt_id=int(row["prompt_id"]),
                kind=row["kind"],
                target_price=row["target_price"],
                declared_price=row["declared_price"],
                stop_reason=row["stop_reason"],
            )
            for row in conn.execute(f"SELECT * FROM combos {where} ORDER BY id", params)
        }
        if not combos:
            return []
        for row in conn.execute(
            "SELECT l.combo_id, l.pick_id, l.position, k.tier, k.price, k.confidence, "
            "       k.event_id, k.market, k.selection, k.result "
            "FROM combo_legs l JOIN picks k ON k.id = l.pick_id "
            "WHERE l.combo_id IN "
            f"({','.join('?' * len(combos))}) ORDER BY l.position",
            tuple(combos),
        ):
            combos[int(row["combo_id"])].legs.append(
                Leg(
                    pick_id=int(row["pick_id"]),
                    position=int(row["position"]),
                    tier=row["tier"],
                    tier_label=tier_labels.get(row["tier"], row["tier"]),
                    price=row["price"],
                    confidence=row["confidence"],
                    event_id=row["event_id"],
                    market=row["market"],
                    selection=row["selection"],
                    result=row["result"],
                )
            )
    return list(combos.values())


@dataclass(frozen=True)
class Overlap:
    """Deux combines qui partagent des jambes, et de combien."""

    left: Combo
    right: Combo
    shared: int
    index: float

    @property
    def line(self) -> str:
        return (
            f"« {self.left.kind} » et « {self.right.kind} » partagent "
            f"{self.shared} jambe(s), J = {self.index:.2f}"
        )


def overlaps(combos: list[Combo]) -> list[Overlap]:
    """Les paires de combines qui partagent au moins une selection.

    Une paire disjointe n'y figure pas : ce qui doit se voir est le
    recouvrement, pas son absence. Le compte partage accompagne l'indice — un
    J de 0,40 ne dit pas s'il porte sur deux jambes ou sur huit.
    """
    paires = []
    for position, gauche in enumerate(combos):
        for droite in combos[position + 1 :]:
            part = jaccard(gauche, droite)
            if part:
                communes = {leg.pick_id for leg in gauche.legs} & {
                    leg.pick_id for leg in droite.legs
                }
                paires.append(Overlap(gauche, droite, len(communes), part))
    return sorted(paires, key=lambda item: -item.index)


@dataclass
class Summary:
    """Ce que les combines proposes ont donne, sur toute la base.

    **Aucun taux de reussite par combine, et ce n'est pas un oubli** : au taux de
    jambe constate (57 %), un combine de dix jambes se tranche favorablement une
    fois sur 280. Son taux ne sera jamais mesurable, quel que soit le temps qu'on
    lui laisse. Le combine est un **regroupement** ; les jambes restent les
    unites de mesure, comptees individuellement ailleurs sur la page.

    Ce qui garde un sens : combien de jambes tombent, a quel rang la premiere
    tombe, et l'ecart entre la cote ecrite et la cote recalculee.
    """

    combos: list[Combo] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.combos

    @property
    def sessions(self) -> int:
        return len({combo.session_id for combo in self.combos})

    @property
    def mismatched(self) -> int:
        """Combines dont la cote ecrite ne decrit pas celle des jambes.

        C'est le seul chiffre du bloc qui juge le rendu plutot que le lot : un
        ecart repete dirait que le produit est ajuste pour tomber sur la cible.
        """
        return sum(1 for combo in self.combos if combo.price_mismatch)

    @property
    def by_stop(self) -> list[tuple[str, str, int]]:
        """La repartition des motifs d'arret, avec leur libelle.

        **Aucun n'est un echec**, et c'est leur repartition qui dit si la cible
        est bien reglee : toujours `cible` et elle peut monter, toujours
        `confiance` et le lot est la contrainte, la cible n'y pouvant rien.
        """
        comptes: dict[str, int] = {}
        for combo in self.combos:
            comptes[combo.stop_reason or ""] = comptes.get(combo.stop_reason or "", 0) + 1
        return sorted(
            (
                (cle, STOP_LABELS.get(cle, "non renseigné"), compte)
                for cle, compte in comptes.items()
            ),
            key=lambda item: -item[2],
        )


def summary(settings: Settings | None = None) -> Summary:
    """Les combines de toute la base, prets a etre rendus."""
    return Summary(combos=list_all(settings))
