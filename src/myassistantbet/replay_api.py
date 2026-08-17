"""Rejouer une reponse d'API archivee avec le code courant.

**Meme contrat que `myassistantbet-replay`**, et pour la meme raison : un
lecteur se corrige, et sans le brut il n'y a plus rien a relire. La difference
est ce qu'on relit — la un collage humain, ici une reponse de fournisseur.

    uv run myassistantbet-replay-api --lister
    uv run myassistantbet-replay-api 12

**Simulation par defaut**, et ici c'est plus qu'une precaution de forme : ce
rejeu ne rappelle **jamais** le fournisseur. Une reponse archivee est un fait
date ; la redemander produirait une autre reponse, donc mesurerait autre chose.
C'est aussi ce qui le rend gratuit en quota — on peut le relancer autant qu'on
veut sur toute l'archive.

## Ce qu'il rend, et pourquoi c'est un compte et non un verdict

Le rejeu dit ce que le code courant **extrairait** : combien de lignes de
service, combien de matchs ecartes, et pourquoi. Il ne dit pas si c'est bien —
c'est au lecteur de comparer avec ce que la base porte. Un outil de diagnostic
qui conclut a la place de celui qui diagnostique fait perdre le seul geste qui
comptait.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from .config import Settings, get_settings
from .providers import tennisapi
from .services import api_archive, serve_stats

logger = logging.getLogger(__name__)


@dataclass
class ApiReplayReport:
    """Ce que le code courant lirait dans une reponse archivee."""

    response_id: int
    provider: str
    endpoint: str
    path: str
    fetched_at: str
    size: int
    #: Le nom sur lequel la lecture a ete faite. Une reponse `matches-played`
    #: n'a de sens que rapportee a un joueur, et le chemin le porte.
    subject: str = ""
    lines: tuple[serve_stats.ServeLine, ...] = ()
    skipped: int = 0
    note: str = ""
    inconsistent: list[str] = field(default_factory=list)

    @property
    def service_points(self) -> int:
        return sum(ligne.service_points for ligne in self.lines)

    @property
    def report_lines(self) -> list[str]:
        out = [
            f"Réponse {self.response_id} · {self.provider} · {self.endpoint} · "
            f"{self.fetched_at} · {self.size} octets · SIMULATION",
            f"  {self.path}",
        ]
        if self.note:
            out.append(f"  ! {self.note}")
            return out
        out.append(
            f"  sujet : {self.subject or '—'} · {len(self.lines)} ligne(s) de service, "
            f"{self.skipped} match(s) écarté(s) · {self.service_points} points de service"
        )
        for ligne in self.lines[:10]:
            out.append(
                f"    {ligne.played_on}  {ligne.surface or '—':7} vs {ligne.opponent[:24]:24} "
                f"{ligne.first_serve}/{ligne.first_serve_of} 1re · {ligne.aces} aces"
            )
        if len(self.lines) > 10:
            out.append(f"    … et {len(self.lines) - 10} de plus")
        for souci in self.inconsistent:
            out.append(f"    ✗ {souci}")
        return out


def _subject(path: str) -> str:
    """Le joueur d'un chemin `matches-played`.

    **Le chemin le porte deja** : on ne redemande pas au fournisseur ce qu'on a
    dans la main. `/tennis/v2/profile/{nom}/matches-played` — le nom est le
    segment qui precede le dernier.
    """
    segments = [segment for segment in path.split("/") if segment]
    return segments[-2] if len(segments) >= 2 else ""


def replay_response(response_id: int, settings: Settings | None = None) -> ApiReplayReport:
    """Relit une reponse archivee avec le code courant. **N'ecrit rien.**"""
    settings = settings or get_settings()
    archive = api_archive.load(response_id, settings)
    if archive is None:
        raise LookupError(f"Aucune réponse archivée sous l'identifiant {response_id}.")

    report = ApiReplayReport(
        response_id=archive.id,
        provider=archive.provider,
        endpoint=archive.endpoint,
        path=archive.path,
        fetched_at=archive.fetched_at,
        size=archive.size,
    )
    if archive.endpoint != tennisapi.MATCHES_PLAYED:
        report.note = f"aucun lecteur pour la famille {archive.endpoint!r}"
        return report

    report.subject = _subject(archive.path)
    lignes, ecartes = serve_stats.parse_matches_played(
        archive.data, report.subject, archive_id=archive.id
    )
    report.lines = lignes
    report.skipped = ecartes
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rejoue une réponse d'API archivée avec le code courant. "
            "Ne rappelle jamais le fournisseur, et n'écrit rien."
        )
    )
    parser.add_argument("response_id", type=int, nargs="?", help="identifiant dans api_responses")
    parser.add_argument(
        "--lister",
        action="store_true",
        help="liste les dernières réponses archivées, puis sort",
    )
    parser.add_argument("--fournisseur", help="restreint la liste à un fournisseur")
    parser.add_argument("--limite", type=int, default=20, help="taille de la liste (défaut 20)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    settings = get_settings()

    if args.lister:
        for archive in api_archive.recent(args.fournisseur, limit=args.limite, settings=settings):
            statut = archive.http_status if archive.http_status is not None else "—"
            print(
                f"{archive.id:>6}  {archive.fetched_at}  {str(statut):>4}  "
                f"{archive.endpoint:24}  {archive.size:>8} o.  {archive.path}"
            )
        return 0

    if args.response_id is None:
        parser.error("indique un identifiant, ou --lister pour les voir")
    try:
        report = replay_response(args.response_id, settings)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in report.report_lines:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
