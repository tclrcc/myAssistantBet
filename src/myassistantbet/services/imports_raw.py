"""Le collage brut, garde avant toute tentative de lecture.

**C'est la ligne la plus rentable du projet, et elle n'avait pas ete faite.** Le
chantier precedent a etabli que `picks.claim_raw_json` etait NULL sur 235
selections sur 235 et que le texte colle n'etait conserve **nulle part** : le
rattrapage des 86 selections des trois sessions concernees etait donc impossible,
et il l'est toujours. Une douzaine de sessions perdues, definitivement.

La journalisation des rejets ne l'aurait pas evite, et il faut le dire
precisement : elle attrape ce qui **leve**, pas ce qui passe et se trompe. La
panne d'origine ne levait rien — la lecture ne trouvait aucun bloc, faute de
cloture, et se taisait. Une table de rejets serait restee vide.

Ce module ne repare donc aucun bug. Il rend le **prochain** rattrapable :

- `record()` ecrit avant toute lecture, y compris quand le parsing echouera
  entierement — c'est precisement ce cas-la qu'on veut pouvoir rejouer ;
- chaque ligne produite garde son **intervalle de position** dans le texte brut,
  ce qui rend un rejeu cible possible sans re-parser l'ensemble ;
- `replay()` relit un collage conserve avec le code courant, en **simulation par
  defaut** — ecrire d'office ferait de l'outil de diagnostic un outil de risque.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass

from ..config import Settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

#: D'ou vient le collage. **Un rejeu se distingue d'une saisie humaine** : sans
#: cette colonne, relire un import ancien en creerait un nouveau indiscernable de
#: l'original, et la chaine de provenance se perdrait des le premier rejeu.
FORM = "formulaire"
API = "api"
REPLAY = "rejeu"
SOURCES = (FORM, API, REPLAY)


@dataclass(frozen=True)
class Import:
    """Un collage conserve, tel qu'il a ete recu."""

    id: int
    session_id: int
    sha256: str
    char_count: int
    source: str
    created_at: str
    #: Le texte lui-meme. Absent des listes — un relevé de trente collages ne
    #: charge pas un mega-octet de texte pour afficher des dates.
    raw_text: str = ""

    def fragment(self, start: int | None, end: int | None) -> str:
        """Le fragment d'ou une ligne a ete extraite, ou « » sans bornes.

        **Les bornes sont celles du texte brut**, jamais d'une version
        normalisee : c'est ce qui permet a un test de verifier qu'elles
        redonnent bien le fragment d'origine, et a un lecteur humain de voir ce
        que le parseur avait sous les yeux.
        """
        if start is None or end is None or not self.raw_text:
            return ""
        return self.raw_text[start:end]


def digest(raw: str) -> str:
    """L'empreinte d'un collage. Calculee sur le texte **tel quel**."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def record(
    session_id: int,
    raw: str,
    source: str = FORM,
    settings: Settings | None = None,
) -> int | None:
    """Garde le collage et rend son identifiant. `None` sur un texte vide.

    **Ecrit avant toute tentative de lecture**, et c'est tout l'objet : un
    collage dont le parsing echoue entierement laisse quand meme sa ligne. Le
    critere d'acceptation du chantier tient dans cette phrase.

    **Deduplique sur l'empreinte**, contrairement aux rejets ou deux tentatives
    identiques sont deux tentatives. Ici le texte est le meme, et ce qu'on garde
    est de quoi rejouer — pas un compteur d'essais. L'apercu puis l'import
    postent le meme texte a la suite : deux lignes n'apprendraient rien et
    doubleraient le volume.
    """
    text = raw or ""
    if not text.strip():
        return None
    empreinte = digest(text)
    origine = source if source in SOURCES else FORM
    try:
        with connect(settings) as conn:
            existant = conn.execute(
                "SELECT id FROM imports_raw WHERE session_id = ? AND sha256 = ?",
                (session_id, empreinte),
            ).fetchone()
            if existant is not None:
                return int(existant["id"])
            cursor = conn.execute(
                "INSERT INTO imports_raw (session_id, raw_text, sha256, char_count, source, "
                "                         created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, text, empreinte, len(text), origine, utcnow()),
            )
            import_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError:
        # **Garder le collage est un filet, jamais une condition.** Une session
        # inconnue — une lecture hors parcours, un identifiant invente — ne doit
        # pas faire echouer l'apercu : le remede serait pire que le mal qu'il
        # previent. Le manque se journalise, et la ligne n'existe pas.
        logger.warning(
            "Collage non conserve : la session %d n'existe pas, %d caracteres perdus",
            session_id,
            len(text),
        )
        return None
    logger.info(
        "Collage conserve : import %d, session %d, %d caracteres (%s)",
        import_id,
        session_id,
        len(text),
        origine,
    )
    return import_id


def get(import_id: int, settings: Settings | None = None) -> Import | None:
    """Un collage, texte compris."""
    with connect(settings) as conn:
        row = conn.execute("SELECT * FROM imports_raw WHERE id = ?", (import_id,)).fetchone()
    return None if row is None else _row(row, with_text=True)


def list_for_session(session_id: int, settings: Settings | None = None) -> list[Import]:
    """Les collages d'une session, du plus recent. **Sans leur texte.**"""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, session_id, sha256, char_count, source, created_at FROM imports_raw "
            "WHERE session_id = ? ORDER BY id DESC",
            (session_id,),
        ).fetchall()
    return [_row(row) for row in rows]


def _row(row: object, with_text: bool = False) -> Import:
    return Import(
        id=int(row["id"]),  # type: ignore[index]
        session_id=int(row["session_id"]),  # type: ignore[index]
        sha256=str(row["sha256"]),  # type: ignore[index]
        char_count=int(row["char_count"]),  # type: ignore[index]
        source=str(row["source"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        raw_text=str(row["raw_text"]) if with_text else "",  # type: ignore[index]
    )
