"""Relire une reponse archivee, et la rejouer avec le code courant.

**L'ecriture n'est pas ici, et c'est voulu.** `providers.base.archive_response`
la porte, a cote de `record_api_usage`, pour deux raisons :

- c'est de l'instrumentation d'appel et non du metier, comme la comptabilisation
  du quota — et `providers/` ne doit rien savoir d'un service ;
- l'appeler **depuis le client** garantit structurellement qu'aucune charge
  utile n'est lue avant d'etre archivee : l'appelant ne la recoit qu'ensuite.

Ce module porte l'autre moitie, celle qui est un service : relire, lister, et
rejouer. Meme partage que `imports_raw` et `myassistantbet-replay` — le brut se
garde au plus pres du transport, le rejeu est un outil d'exploitation.

## Ce que le rejeu peut, et ce qu'il ne peut pas

`replay-api` re-parse une reponse **deja obtenue** avec le code courant, en
simulation par defaut. C'est l'outil de reprise apres un correctif de lecteur,
exactement comme `myassistantbet-replay` pour les collages.

Il ne rappelle **jamais** le fournisseur : une reponse archivee est un fait
date, et la redemander produirait une autre reponse — donc mesurerait autre
chose. C'est aussi ce qui le rend gratuit en quota.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings
from ..db import connect

_COLUMNS = (
    "id, provider, endpoint, path, params, raw_json, sha256, http_status, "
    "fetched_at, quota_remaining"
)


@dataclass(frozen=True)
class Response:
    """Une reponse relue depuis l'archive."""

    id: int
    provider: str
    endpoint: str
    path: str
    params: str
    raw_json: str
    sha256: str
    http_status: int | None
    fetched_at: str
    quota_remaining: int | None = None

    @property
    def data(self) -> Any:
        """La charge utile decodee, ou la chaine brute si ce n'est pas du JSON.

        **Un corps illisible se rend tel quel plutot que de lever** : c'est
        justement une reponse en erreur qu'on veut pouvoir regarder, et une
        archive qui refuserait de rendre ce qu'elle a garde ne servirait a rien.
        """
        try:
            return json.loads(self.raw_json)
        except (TypeError, ValueError):
            return self.raw_json

    @property
    def size(self) -> int:
        return len(self.raw_json)


def _row(row: Any) -> Response:
    return Response(
        id=int(row["id"]),
        provider=str(row["provider"]),
        endpoint=str(row["endpoint"]),
        path=str(row["path"]),
        params=str(row["params"] or ""),
        raw_json=str(row["raw_json"]),
        sha256=str(row["sha256"]),
        http_status=row["http_status"],
        fetched_at=str(row["fetched_at"]),
        quota_remaining=row["quota_remaining"],
    )


def load(response_id: int, settings: Settings | None = None) -> Response | None:
    """Relit une reponse archivee. None si l'identifiant n'existe pas."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM api_responses WHERE id = ?", (int(response_id),)
        ).fetchone()
    return None if row is None else _row(row)


def recent(
    provider: str | None = None,
    endpoint: str | None = None,
    limit: int = 20,
    settings: Settings | None = None,
) -> tuple[Response, ...]:
    """Les dernieres reponses archivees, pour choisir laquelle rejouer."""
    settings = settings or get_settings()
    clauses: list[str] = []
    params: list[Any] = []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if endpoint:
        clauses.append("endpoint = ?")
        params.append(endpoint)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(settings) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM api_responses {where} ORDER BY id DESC LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
    return tuple(_row(row) for row in rows)


def count(settings: Settings | None = None) -> int:
    """Nombre de reponses archivees. Sert au releve d'exploitation."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM api_responses").fetchone()
    return int(row["n"]) if row else 0
