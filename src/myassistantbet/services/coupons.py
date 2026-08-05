"""Coupons joues : ce qui a reellement ete pose chez le bookmaker.

Un pick est une selection ; un coupon est un pari : une mise, une ou plusieurs
jambes, un resultat global. La distinction repare un angle mort — un combine
s'enregistrait jusqu'ici comme un pick unique sans evenement, donc sans sport,
et les taux par sport l'ignoraient en silence.

**Aucun calcul financier** (SPEC.md section 9). La mise est memorisee parce
qu'elle fait partie du souvenir de ce qui a ete joue, mais elle n'est jamais
agregee, jamais multipliee par une cote, et la cote totale du coupon n'est
meme pas calculee : la capture jointe la porte deja. Le seul indicateur reste
`gagnes / (gagnes + perdus)`.

La capture est une **piece jointe**, pas une source de donnees : elle n'est
jamais lue par la machine. La lire automatiquement supposerait un modele de
vision, donc un appel a l'API Anthropic — interdit n°6 — ou un OCR local peu
fiable sur une interface sombre de bookmaker.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .history import RESULT_LABELS, HistoryError, Pick, RateRow, _tier_labels

logger = logging.getLogger(__name__)

DEFAULT_BOOKMAKER = "betclic"

#: Formats de capture acceptes. La liste est blanche : tout le reste est refuse,
#: y compris ce qui se fait passer pour une image.
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

#: Signatures de fichier, verifiees en plus du type declare : le navigateur
#: annonce ce qu'il veut, l'octet de tete ne ment pas.
MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "webp": (b"RIFF",),
}

#: Nom de fichier de capture, entierement fabrique ici. Le nom fourni par le
#: navigateur n'est jamais utilise : c'est la porte d'entree classique d'une
#: traversee de repertoire.
SCREENSHOT_NAME = re.compile(r"^coupon-\d+-[0-9a-f]{12}\.(png|jpg|webp)$")


@dataclass
class Coupon:
    """Un pari pose, avec ses jambes."""

    coupon_id: int
    session_id: int
    bookmaker: str
    stake: float | None
    placed_local: datetime | None
    screenshot: str | None
    note: str
    legs: list[Pick] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "simple" if len(self.legs) <= 1 else f"combiné {len(self.legs)}"

    @property
    def combined(self) -> bool:
        return len(self.legs) > 1

    @property
    def result(self) -> str:
        """Resultat du coupon, **deduit** de ses jambes et jamais saisi.

        Une seule jambe perdue fait tomber le coupon, quel que soit le sort des
        autres. Une jambe annulee est neutre — le bookmaker recalcule la cote
        sans elle — et un coupon entierement annule l'est aussi. Tant qu'une
        jambe reste en attente sans qu'aucune soit perdue, rien n'est tranche.
        """
        results = [leg.result for leg in self.legs]
        if not results:
            return "pending"
        if "loss" in results:
            return "loss"
        if "pending" in results:
            return "pending"
        if all(result == "void" for result in results):
            return "void"
        return "win"

    @property
    def result_label(self) -> str:
        return RESULT_LABELS.get(self.result, self.result)

    @property
    def sports(self) -> list[str]:
        """Sports representes, pour reperer un combine adosse a un seul sport."""
        seen = []
        for leg in self.legs:
            if leg.sport_label and leg.sport_label not in seen:
                seen.append(leg.sport_label)
        return seen


# -- Lecture ----------------------------------------------------------------


def _local(value: str | None, tz: str) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _legs(conn, tier_labels: dict[str, str]) -> dict[int, list[Pick]]:
    """Jambes de tous les coupons, indexees par coupon."""
    rows = conn.execute(
        "SELECT k.*, e.home, e.away, s.label AS sport_label FROM picks k "
        "LEFT JOIN events e ON e.id = k.event_id "
        "LEFT JOIN sports s ON s.id = e.sport_id "
        "WHERE k.coupon_id IS NOT NULL ORDER BY k.id"
    ).fetchall()

    grouped: dict[int, list[Pick]] = {}
    for row in rows:
        grouped.setdefault(int(row["coupon_id"]), []).append(_as_pick(row, tier_labels))
    return grouped


def _as_pick(row, tier_labels: dict[str, str]) -> Pick:
    if row["home"]:
        label = f"{row['home']} – {row['away']}" if row["away"] else row["home"]
    else:
        label = "hors match"
    return Pick(
        pick_id=int(row["id"]),
        session_id=int(row["session_id"]),
        event_id=row["event_id"],
        event_label=label,
        tier=row["tier"],
        tier_label=tier_labels.get(row["tier"], row["tier"]),
        market=row["market"],
        selection=row["selection"],
        price=row["price"],
        confidence=row["confidence"],
        played=bool(row["played"]),
        stake=row["stake"],
        result=row["result"] or "pending",
        sport_label=row["sport_label"] or "",
    )


def list_for_session(session_id: int, settings: Settings | None = None) -> list[Coupon]:
    """Coupons d'une session, du plus recent au plus ancien."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        legs = _legs(conn, tier_labels)
        rows = conn.execute(
            "SELECT * FROM coupons WHERE session_id = ? ORDER BY id DESC", (session_id,)
        ).fetchall()

    return [
        Coupon(
            coupon_id=int(row["id"]),
            session_id=int(row["session_id"]),
            bookmaker=row["bookmaker"],
            stake=row["stake"],
            placed_local=_local(row["placed_at"], settings.tz),
            screenshot=row["screenshot"],
            note=row["note"] or "",
            legs=legs.get(int(row["id"]), []),
        )
        for row in rows
    ]


def available_picks(session_id: int, settings: Settings | None = None) -> list[Pick]:
    """Picks de la session pas encore rattaches a un coupon."""
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        rows = conn.execute(
            "SELECT k.*, e.home, e.away, s.label AS sport_label FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "WHERE k.session_id = ? AND k.coupon_id IS NULL ORDER BY k.id",
            (session_id,),
        ).fetchall()
    return [_as_pick(row, tier_labels) for row in rows]


# -- Ecriture ---------------------------------------------------------------


def _as_stake(value: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        stake = float(text)
    except ValueError as exc:
        raise HistoryError("« Mise » doit être un nombre.") from exc
    if stake < 0:
        raise HistoryError("« Mise » ne peut pas être négative.")
    return stake


def _as_moment(date_value: str, time_value: str, settings: Settings) -> str | None:
    """`2026-08-05` + `19:04` en heure locale -> instant UTC. Vide = maintenant."""
    date_value, time_value = (date_value or "").strip(), (time_value or "").strip()
    if not date_value:
        return utcnow()
    try:
        naive = datetime.strptime(f"{date_value} {time_value or '00:00'}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise HistoryError("Date ou heure invalide (attendu AAAA-MM-JJ et HH:MM).") from exc
    return (
        naive.replace(tzinfo=ZoneInfo(settings.tz)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def create(
    session_id: int,
    pick_ids: list[int],
    *,
    stake: str = "",
    date_value: str = "",
    time_value: str = "",
    bookmaker: str = DEFAULT_BOOKMAKER,
    note: str = "",
    settings: Settings | None = None,
) -> int:
    """Enregistre un coupon a partir de picks existants. Renvoie son id.

    Les jambes sont des picks deja saisis : rien n'est retape. Un pick rattache
    passe a `played = 1` — c'est precisement ce que veut dire « joue ».
    """
    settings = settings or get_settings()
    if not pick_ids:
        raise HistoryError("Sélectionne au moins une jambe.")

    stake_value = _as_stake(stake)
    placed_at = _as_moment(date_value, time_value, settings)

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, coupon_id FROM picks WHERE id IN "
            f"({','.join('?' * len(pick_ids))}) AND session_id = ?",
            (*pick_ids, session_id),
        ).fetchall()
        found = {int(row["id"]) for row in rows}
        if found != set(pick_ids):
            raise HistoryError("Certaines jambes n'appartiennent pas à cette session.")
        if any(row["coupon_id"] is not None for row in rows):
            raise HistoryError("Certaines jambes sont déjà dans un coupon.")

        cursor = conn.execute(
            "INSERT INTO coupons (session_id, bookmaker, stake, placed_at, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                (bookmaker or DEFAULT_BOOKMAKER).strip().lower(),
                stake_value,
                placed_at,
                note.strip() or None,
                utcnow(),
            ),
        )
        coupon_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE picks SET coupon_id = ?, played = 1 WHERE id IN "
            f"({','.join('?' * len(pick_ids))})",
            (coupon_id, *pick_ids),
        )

    logger.info("Coupon %d enregistre : %d jambe(s)", coupon_id, len(pick_ids))
    return coupon_id


def delete(coupon_id: int, settings: Settings | None = None) -> None:
    """Supprime un coupon. Ses jambes redeviennent des picks libres.

    Les picks ne sont jamais supprimes avec le coupon : ils ont ete proposes et
    analyses, ce qui reste vrai meme si le pari a ete saisi par erreur.
    """
    settings = settings or get_settings()
    path = screenshot_path(coupon_id, settings)
    with connect(settings) as conn:
        conn.execute("UPDATE picks SET coupon_id = NULL WHERE coupon_id = ?", (coupon_id,))
        conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    if path is not None and path.is_file():
        path.unlink()
    logger.info("Coupon %d supprime", coupon_id)


# -- Captures ---------------------------------------------------------------


def _extension(content_type: str, content: bytes) -> str:
    """Extension deduite du type declare, **confirmee par les octets de tete**."""
    extension = IMAGE_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if extension is None:
        raise HistoryError("Format non accepté : envoie une image PNG, JPEG ou WebP.")
    if not any(content.startswith(prefix) for prefix in MAGIC[extension]):
        raise HistoryError("Ce fichier n'est pas l'image qu'il prétend être.")
    return extension


def save_screenshot(
    coupon_id: int,
    filename: str,
    content: bytes,
    content_type: str,
    settings: Settings | None = None,
) -> str:
    """Attache une capture a un coupon. Renvoie le nom de fichier enregistre.

    Le nom fourni par le navigateur est ignore : il est refabrique ici, ce qui
    ferme d'un coup la traversee de repertoire et les collisions de noms.
    """
    settings = settings or get_settings()
    if not content:
        raise HistoryError("Fichier vide.")
    if len(content) > settings.upload_max_bytes:
        raise HistoryError(
            f"Capture trop lourde : {len(content) // 1024} Ko pour un maximum de "
            f"{settings.upload_max_bytes // 1024} Ko."
        )

    extension = _extension(content_type, content)
    digest = hashlib.sha256(content).hexdigest()[:12]
    name = f"coupon-{coupon_id}-{digest}.{extension}"

    directory = settings.upload_dir_absolute
    directory.mkdir(parents=True, exist_ok=True)

    previous = screenshot_path(coupon_id, settings)
    (directory / name).write_bytes(content)
    with connect(settings) as conn:
        conn.execute("UPDATE coupons SET screenshot = ? WHERE id = ?", (name, coupon_id))
    if previous is not None and previous.name != name and previous.is_file():
        previous.unlink()

    logger.info("Capture attachee au coupon %d : %s (%d octets)", coupon_id, name, len(content))
    return name


def screenshot_path(coupon_id: int, settings: Settings | None = None) -> Path | None:
    """Chemin de la capture d'un coupon, ou None.

    Le nom relu en base est revalide contre le motif fabrique a l'ecriture :
    une base modifiee a la main ne doit pas pouvoir faire servir `../../.env`.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute("SELECT screenshot FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    if row is None or not row["screenshot"]:
        return None
    name = str(row["screenshot"])
    if not SCREENSHOT_NAME.match(name):
        logger.warning("Nom de capture refuse pour le coupon %d : %r", coupon_id, name)
        return None
    return settings.upload_dir_absolute / name


# -- Taux -------------------------------------------------------------------


def rates(settings: Settings | None = None) -> list[RateRow]:
    """Taux de reussite des coupons, simples et combines separes.

    Un combine et un pari simple ne se comparent pas : le premier tombe des
    qu'une jambe cede. Les melanger produirait un taux qui ne decrit ni l'un ni
    l'autre. Aucun montant n'entre ici.
    """
    settings = settings or get_settings()
    grouped = {
        "simple": RateRow(key="simple", label="Paris simples"),
        "combine": RateRow(key="combine", label="Combinés"),
    }

    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        legs = _legs(conn, tier_labels)
        rows = conn.execute("SELECT id FROM coupons").fetchall()

    for row in rows:
        coupon = Coupon(
            coupon_id=int(row["id"]),
            session_id=0,
            bookmaker="",
            stake=None,
            placed_local=None,
            screenshot=None,
            note="",
            legs=legs.get(int(row["id"]), []),
        )
        entry = grouped["combine" if coupon.combined else "simple"]
        result = coupon.result
        if result == "win":
            entry.won += 1
        elif result == "loss":
            entry.lost += 1
        elif result == "void":
            entry.void += 1
        else:
            entry.pending += 1

    return [entry for entry in grouped.values() if entry.total]
