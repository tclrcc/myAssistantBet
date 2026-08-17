"""Rejouer un collage conserve avec le code courant.

**C'est l'outil qui aurait sauve les 86 selections.** Le chantier precedent a
etabli que `picks.claim_raw_json` etait NULL sur 235 selections sur 235, que la
cause etait un lecteur qui ne reconnaissait un bloc que sous sa cloture, et que
le rattrapage etait impossible faute d'avoir garde le texte. Corriger le lecteur
ne servait a rien : il n'y avait plus rien a relire.

Depuis la migration 052 le texte est garde. Cette commande le relit.

**Simulation par defaut, et ce n'est pas une precaution de forme** : un rejeu qui
ecrirait d'office ferait d'un outil de diagnostic un outil de risque, sur des
donnees dont le projet entier dit qu'elles ne se reconstituent pas. On regarde
d'abord ce que le code courant produirait, on ecrit ensuite si ca convient.

    uv run python -m myassistantbet.replay 12
    uv run python -m myassistantbet.replay 12 --ecrire

Le second passage **ne double rien** : `parse_table` marque en `duplicate` toute
ligne dont la signature (match, marche, selection) existe deja dans la session,
et le rejeu ne garde que les lignes qui n'y sont pas.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field

from .config import Settings, get_settings
from .services import history as history_service
from .services import imports_raw, picks_import
from .services import ingestion as ingestion_service
from .services.ingestion import reject_reason

logger = logging.getLogger(__name__)


@dataclass
class ReplayReport:
    """Ce qu'un rejeu produirait, ou a produit."""

    import_id: int
    session_id: int
    char_count: int
    #: Les lignes que le code courant lit et que la session ne porte pas encore.
    fresh: list[picks_import.ParsedPick] = field(default_factory=list)
    #: Celles qu'elle porte deja. **Comptees et jamais reecrites** : un rejeu qui
    #: doublerait l'historique serait pire que pas de rejeu du tout.
    known: list[picks_import.ParsedPick] = field(default_factory=list)
    claims: int = 0
    combos: int = 0
    set_scores: int = 0
    rejects: list[ingestion_service.Reject] = field(default_factory=list)
    written: int = 0
    failures: list[str] = field(default_factory=list)
    #: Vrai quand rien n'a ete ecrit. C'est le defaut.
    dry_run: bool = True

    @property
    def lines(self) -> list[str]:
        """Le compte-rendu, une ligne par fait."""
        mode = "SIMULATION — rien n'est écrit" if self.dry_run else "ÉCRITURE"
        out = [
            f"Import {self.import_id} · session {self.session_id} · "
            f"{self.char_count} caractères · {mode}",
            f"  {len(self.fresh)} sélection(s) nouvelle(s), {len(self.known)} déjà présente(s)",
            f"  {self.claims} bloc(s) de confiance · {self.combos} combiné(s) · "
            f"{self.set_scores} score(s) en sets",
        ]
        for pick in self.fresh:
            cran = pick.claim.rung if pick.claim else None
            out.append(
                f"    + {pick.match_text or '—'} · {pick.market} {pick.selection} "
                f"· {pick.price or '—'} · cran {cran if cran is not None else '?'}"
            )
        for reject in self.rejects:
            out.append(f"    ! {reject.label}")
        for echec in self.failures:
            out.append(f"    ✗ {echec}")
        if not self.dry_run:
            out.append(f"  {self.written} sélection(s) écrite(s)")
        return out


def replay(
    import_id: int,
    write: bool = False,
    settings: Settings | None = None,
) -> ReplayReport:
    """Relit un collage conserve. **N'ecrit rien sans `write=True`.**

    Les selections produites pointent sur le collage **d'origine**, et c'est le
    bon choix : le texte est le meme texte, et le dupliquer sous une source
    `rejeu` recopierait trente kilo-octets pour ne rien dire de plus. Ce qui
    distingue un rejeu n'est pas la provenance mais le moment, et il se lit deja
    — `picks.created_at` y est tres posterieur a `imports_raw.created_at`.
    """
    settings = settings or get_settings()
    collage = imports_raw.get(import_id, settings)
    if collage is None:
        raise LookupError(f"Aucun collage conservé sous l'identifiant {import_id}.")

    preview = picks_import.build_preview(collage.session_id, collage.raw_text, settings)
    report = ReplayReport(
        import_id=import_id,
        session_id=collage.session_id,
        char_count=collage.char_count,
        fresh=[pick for pick in preview.picks if not pick.duplicate],
        known=[pick for pick in preview.picks if pick.duplicate],
        claims=preview.claims_attached,
        combos=len(preview.combos),
        set_scores=len(preview.scores),
        rejects=preview.rejects,
        dry_run=not write,
    )
    if not write:
        return report

    for pick in report.fresh:
        try:
            history_service.add_pick(
                collage.session_id,
                tier=pick.tier,
                market=pick.market,
                selection=pick.selection,
                event_id=str(pick.event_id or ""),
                price=pick.price,
                confidence=pick.confidence,
                angle=pick.angle,
                source_level=pick.source,
                price_source=pick.price_source,
                independence_note=pick.independence,
                late_reason=pick.late_reason,
                claim=pick.claim.raw if pick.claim else "",
                opened=pick.opened,
                override_cause=pick.override_cause,
                exploratory=pick.exploratory,
                import_id=import_id,
                offsets=_span(pick.start, pick.end),
                claim_offsets=(_span(pick.claim.start, pick.claim.end) if pick.claim else ""),
                settings=settings,
            )
            report.written += 1
        except history_service.HistoryError as exc:
            report.failures.append(f"{pick.market} {pick.selection} : {exc}")
            # **Une ligne refusee a l'ecriture est une perte comme une autre**, et
            # le rejeu ne la journalisait pas — trouve par `selfcheck-ingestion`,
            # qui existe precisement pour attraper un chemin muet.
            report.rejects.append(
                ingestion_service.Reject(
                    block_type=ingestion_service.SELECTION,
                    reason=reject_reason(str(exc)),
                    detail=f"{pick.market} {pick.selection} : {exc}",
                    payload=f"{pick.market} / {pick.selection}",
                    start=pick.start,
                    end=pick.end,
                )
            )
    ingestion_service.record(collage.session_id, report.rejects, settings, import_id=import_id)
    return report


def _span(start: int | None, end: int | None) -> str:
    return "" if start is None or end is None else f"{start}:{end}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rejoue un collage conservé avec le code courant. "
            "Simulation par défaut : rien n'est écrit sans --ecrire."
        )
    )
    # **Optionnel**, parce que `--lister` est ce qu'on tape en premier : on ne
    # connait pas l'identifiant avant d'avoir vu la liste, et l'exiger quand meme
    # obligerait a inventer un nombre pour lire un catalogue.
    parser.add_argument("import_id", type=int, nargs="?", help="identifiant dans imports_raw")
    parser.add_argument(
        "--ecrire",
        action="store_true",
        help="enregistre les sélections nouvelles au lieu de seulement les afficher",
    )
    parser.add_argument(
        "--lister",
        type=int,
        metavar="SESSION",
        help="liste les collages conservés d'une session, puis sort",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    settings = get_settings()

    if args.lister is not None:
        for collage in imports_raw.list_for_session(args.lister, settings):
            print(
                f"{collage.id:>5}  {collage.created_at}  {collage.char_count:>7} car."
                f"  {collage.source}"
            )
        return 0

    if args.import_id is None:
        parser.error("indique un identifiant, ou --lister SESSION pour les voir")
    try:
        report = replay(args.import_id, write=args.ecrire, settings=settings)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in report.lines:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
