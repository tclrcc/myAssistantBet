"""Ce que l'ingestion a perdu, et pourquoi.

**Aucun chemin d'import ne peut plus laisser tomber un bloc sans trace.** C'est
la cinquieme fois que le defaut caracteristique du projet se produit — une
sortie identique pour l'echec et pour le cas ordinaire — et cette fois il
portait sur le chemin qui alimente tout le reste : un bloc de confiance
malforme, un combine dont les reperes ne se resolvent pas, une ligne refusee a
l'ecriture disparaissaient tous en silence.

Mesure du 17/08/2026 qui fonde le module : `claim_raw_json` est NULL sur **235
selections sur 235**. Trois sessions ont ete importees apres la mise en service
du bloc de confiance, et rien nulle part ne disait qu'un bloc avait ete cherche
et pas trouve.

Deux regles tiennent le module :

- **un rejet decrit le chemin d'ingestion, jamais le modele.** Un bloc que le
  rendu n'a pas produit n'est pas un rejet ; un bloc produit et non transmis en
  est un, et c'est precisement celui-la qu'on ne voyait pas ;
- **le brut accompagne le motif.** Sans lui on saurait qu'un bloc a echoue et
  jamais lequel, ce qui reproduirait le silence sous un autre nom.

Rien n'est ecrit a l'apercu : `build_preview` n'ecrit pas en base, c'est un
contrat de longue date garde par un test. Les rejets voyagent donc avec le
formulaire, comme le bloc de confiance et la liste des dossiers ouverts, et
c'est l'import qui les enregistre.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

#: Les cinq familles de blocs que le rendu porte. Le type dit **quel chemin** a
#: perdu quelque chose, et c'est ce qui rend le compte actionnable : trois
#: `conf` disent qu'un gabarit est ambigu, trois `selection` qu'une garde mord.
CONF = "conf"
COMBO = "combo"
SCORE_SETS = "score_sets"
SELECTION = "selection"
EXPLORATOIRE = "exploratoire"
BLOCK_TYPES = (CONF, COMBO, SCORE_SETS, SELECTION, EXPLORATOIRE)

#: Pourquoi le bloc n'est pas entre. **Une enumeration et non du texte libre** :
#: c'est elle qui se compte, et deux orthographes du meme motif feraient deux
#: lignes qui ne se rapprochent plus.
FENCE_NOT_FOUND = "fence_not_found"
JSON_INVALID = "json_invalid"
SCHEMA_INVALID = "schema_invalid"
MATCH_REF_UNRESOLVED = "match_ref_unresolved"
DUPLICATE = "duplicate"
OTHER = "other"
REASONS = (
    FENCE_NOT_FOUND,
    JSON_INVALID,
    SCHEMA_INVALID,
    MATCH_REF_UNRESOLVED,
    DUPLICATE,
    OTHER,
)

#: Ce que chaque motif dit, la ou il se rend. Ecrit **une seule fois** : un
#: libelle recopie cote gabarit ne casse rien le jour ou la cle change, il
#: disparait — meme regle que `Pick.late_label` et `ParsedPick.override_label`.
REASON_LABELS: dict[str, str] = {
    FENCE_NOT_FOUND: "bloc introuvable dans le collage",
    JSON_INVALID: "JSON illisible",
    SCHEMA_INVALID: "champ manquant ou hors vocabulaire",
    MATCH_REF_UNRESOLVED: "repère de match non résolu",
    DUPLICATE: "déjà présente, ou seconde sélection non justifiée",
    OTHER: "refusée à l'écriture",
}

BLOCK_LABELS: dict[str, str] = {
    CONF: "bloc de confiance",
    COMBO: "combiné",
    SCORE_SETS: "score en sets",
    SELECTION: "sélection",
    EXPLORATOIRE: "sélection exploratoire",
}

#: Longueur au-dela de laquelle un brut est tronque a l'ecriture. Un bloc de
#: confiance tient en quelques centaines d'octets ; ce qui deborde n'est pas un
#: bloc mais un collage entier tombe dans la mauvaise branche, et le garder en
#: entier ferait grossir la table sans rien apprendre de plus.
PAYLOAD_MAX = 4000


# -- Ou trouver un bloc dans un collage -------------------------------------
#
# **Le rendu de Claude et ce qu'on en copie ne sont pas le meme texte.** Le
# module d'import le savait deja pour les tableaux — `_cells` lit les barres
# verticales *et* les tabulations, « ce que l'on copie depuis son interface est
# un tableau tabule, les barres ayant ete consommees par le rendu » — et
# l'appliquait a une seule des deux formes. Un bloc de code copie depuis le rendu
# arrive **sans sa cloture** : le JSON est intact, les trois accents graves ont
# disparu avec le reste du balisage.
#
# La lecture ne se posait donc que sur la forme que le copier-coller detruit, et
# la mesure du 17/08/2026 dit ce que ca a coute : `claim_raw_json` NULL sur 235
# selections sur 235, dont les 86 des trois sessions ou le gabarit demandait
# pourtant un bloc par ligne.


def _balanced(text: str, start: int) -> int | None:
    """Fin de l'objet JSON ouvert a `start`, ou rien s'il ne se referme pas.

    Compte les accolades **hors chaines** : un `{` dans un enonce — « le retour
    de {joueur} » — ne doit pas ouvrir un niveau, et une accolade echappee non
    plus. Sans ce soin, un enonce accidente ferait avaler la moitie du rendu.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def read_bodies(
    raw: str,
    fence: re.Pattern[str],
    belongs: Callable[[dict], bool],
) -> list[str]:
    """Les corps de blocs d'une famille, **clotures ou non**, dans l'ordre.

    Deux chemins, et il faut les deux :

    - la **cloture** fait foi quand elle est la. Elle nomme la famille, donc un
      bloc casse y est reconnu comme un bloc casse et se signale — c'est le seul
      chemin ou un JSON illisible peut etre rapporte ;
    - sans cloture, seule la **forme** peut trancher. `belongs` lit les cles qui
      appartiennent en propre a la famille : `faits` pour un bloc de confiance,
      `jambes` pour un combine. Un objet qui ne se relit pas est alors
      indiscernable de la prose et se perd — limite structurelle, notee ici
      plutot que rattrapee par une heuristique qui avalerait des phrases.

    Un objet deja couvert par une cloture n'est jamais compte deux fois, et un
    bloc cloture qui appartient manifestement a **l'autre** famille est ecarte :
    la cloture ```json est acceptee des deux cotes, et sans ce filtre un combine
    ecrit ainsi se rendrait en bloc de confiance refuse.
    """
    spans: list[tuple[int, int]] = []
    bodies: list[tuple[int, str]] = []
    for found in fence.finditer(raw or ""):
        spans.append((found.start(), found.end()))
        body = found.group(1)
        payload = _loads(body)
        if payload is not None and not belongs(payload) and _shaped(payload):
            continue
        bodies.append((found.start(), body))

    index, text = 0, raw or ""
    while index < len(text):
        if text[index] != "{" or any(low <= index < high for low, high in spans):
            index += 1
            continue
        end = _balanced(text, index)
        if end is None:
            index += 1
            continue
        payload = _loads(text[index:end])
        if payload is not None and belongs(payload):
            bodies.append((index, text[index:end]))
        index = end if payload is not None else index + 1

    return [body for _, body in sorted(bodies, key=lambda pair: pair[0])]


def _loads(body: str) -> dict | None:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


#: Les cles qui font qu'un objet est un bloc de l'application plutot qu'un objet
#: quelconque. Sert **uniquement** a ecarter un bloc cloture de la mauvaise
#: famille : un objet qui n'en porte aucune n'appartient a personne, et le
#: refuser des deux cotes le ferait disparaitre sans un mot.
_SHAPES = ("faits", "facts", "source_level", "confiance", "jambes", "legs")


def _shaped(payload: dict) -> bool:
    return any(key in payload for key in _SHAPES)


@dataclass(frozen=True)
class Reject:
    """Un bloc qui a existe et qui n'est pas entre."""

    block_type: str
    reason: str
    detail: str = ""
    payload: str = ""

    @property
    def label(self) -> str:
        """« bloc de confiance — JSON illisible : … »"""
        head = (
            f"{BLOCK_LABELS.get(self.block_type, self.block_type)} — "
            f"{REASON_LABELS.get(self.reason, self.reason)}"
        )
        return f"{head} : {self.detail}" if self.detail else head

    def as_payload(self) -> dict[str, str]:
        return {
            "block_type": self.block_type,
            "reason": self.reason,
            "detail": self.detail,
            "payload": self.payload,
        }


def _clean(value: object, allowed: tuple[str, ...], fallback: str) -> str:
    """Une valeur du vocabulaire, ou le repli. Jamais un refus.

    Un rejet mal type reste un rejet : le perdre parce que son etiquette est
    inconnue serait exactement le silence que ce module supprime.
    """
    text = str(value or "").strip()
    return text if text in allowed else fallback


def from_payload(raw: object) -> list[Reject]:
    """Relit les rejets transportes par le formulaire d'import.

    L'apercu n'ecrit rien — contrat de longue date — donc les rejets voyagent
    caches, comme le bloc de confiance. Une charge utile illisible ne fait pas
    echouer l'import : elle journalise, et le compte-rendu dira qu'il manque
    quelque chose.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        items = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("Rejets d'ingestion illisibles, ignores : %s", exc)
        return []
    if not isinstance(items, list):
        return []
    found: list[Reject] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        found.append(
            Reject(
                block_type=_clean(item.get("block_type"), BLOCK_TYPES, SELECTION),
                reason=_clean(item.get("reason"), REASONS, OTHER),
                detail=str(item.get("detail") or ""),
                payload=str(item.get("payload") or ""),
            )
        )
    return found


def to_payload(rejects: list[Reject]) -> str:
    """Les rejets, prets a voyager dans un champ cache du formulaire."""
    return json.dumps([reject.as_payload() for reject in rejects], ensure_ascii=False)


def record(
    session_id: int,
    rejects: list[Reject],
    settings: Settings | None = None,
) -> int:
    """Journalise ce que l'ingestion a perdu. Rend le nombre de lignes ecrites.

    **Aucune deduplication.** Deux collages successifs du meme rendu casse sont
    deux tentatives, et les fondre ferait passer un defaut qui persiste pour un
    defaut qui a eu lieu une fois — le contraire de ce qu'un compteur doit dire.
    """
    if not rejects:
        return 0
    now = utcnow()
    with connect(settings) as conn:
        conn.executemany(
            "INSERT INTO ingestion_rejects (session_id, block_type, raw_payload, reason, "
            "                               detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    session_id,
                    _clean(reject.block_type, BLOCK_TYPES, SELECTION),
                    (reject.payload or "")[:PAYLOAD_MAX],
                    _clean(reject.reason, REASONS, OTHER),
                    reject.detail or "",
                    now,
                )
                for reject in rejects
            ],
        )
    for reject in rejects:
        logger.warning("Rejet d'ingestion — session %d : %s", session_id, reject.label)
    return len(rejects)


@dataclass
class Report:
    """Le compte-rendu d'une ingestion, rendu a la fin de chaque import.

    **Il se lit au moment ou l'information est encore recuperable.** Un manque
    dit une semaine plus tard sur la page de statistiques ne se repare plus ;
    dit ici, il se reprend en recollant.
    """

    picks_read: int = 0
    picks_created: int = 0
    claims_read: int = 0
    combos_recorded: int = 0
    set_scores_read: int = 0
    rejects: list[Reject] = field(default_factory=list)

    @property
    def rejected(self) -> int:
        return len(self.rejects)

    @property
    def line(self) -> str:
        """« 12 sélections lues, 12 blocs conf lus, 1 rejet »."""
        parts = [
            f"{self.picks_read} sélection(s) lue(s)",
            f"{self.claims_read} bloc(s) conf lu(s)",
        ]
        if self.combos_recorded:
            parts.append(f"{self.combos_recorded} combiné(s)")
        if self.set_scores_read:
            parts.append(f"{self.set_scores_read} score(s) en sets")
        parts.append(f"{self.rejected} rejet(s)")
        return " · ".join(parts)


@dataclass(frozen=True)
class Tally:
    """Un motif de rejet et son compte, pour la page de statistiques."""

    block_type: str
    reason: str
    count: int

    @property
    def label(self) -> str:
        return (
            f"{BLOCK_LABELS.get(self.block_type, self.block_type)} — "
            f"{REASON_LABELS.get(self.reason, self.reason)}"
        )


@dataclass
class Summary:
    """Les rejets de la periode couverte, par type et par motif."""

    rows: list[Tally] = field(default_factory=list)
    sessions: int = 0
    since: str = ""

    @property
    def total(self) -> int:
        return sum(row.count for row in self.rows)

    @property
    def empty(self) -> bool:
        return not self.rows


def summary(settings: Settings | None = None) -> Summary:
    """Ce que l'ingestion a perdu, par type et par motif.

    **Un compte, jamais un taux** : il est juste a tout effectif, et aucun seuil
    ne le garde — meme regle que le compte des selections non classees. Une
    table vide dit qu'aucun bloc n'a ete perdu **depuis la mise en service**, et
    la date le rappelle : rien n'est retro-rempli.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT block_type, reason, COUNT(*) AS n FROM ingestion_rejects "
            "GROUP BY block_type, reason ORDER BY n DESC, block_type, reason"
        ).fetchall()
        scope = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS sessions, MIN(created_at) AS since "
            "FROM ingestion_rejects"
        ).fetchone()
    return Summary(
        rows=[Tally(row["block_type"], row["reason"], int(row["n"])) for row in rows],
        sessions=int(scope["sessions"] or 0) if scope else 0,
        since=str(scope["since"] or "") if scope else "",
    )
