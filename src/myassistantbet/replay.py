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
from .services import combos as combos_service
from .services import history as history_service
from .services import imports_raw, picks_import
from .services import ingestion as ingestion_service
from .services.confidence import OPEN_ABSENT
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
    # **La garde de doublon de l'apercu se perime, et il a fallu la mesurer pour
    # le voir.** `parse_table` marque `duplicate` sur une signature qui inclut
    # l'identifiant de match — lequel se resout par la shortlist et le voisinage,
    # donc **cesse de se resoudre** des que le match sort de la fenetre. Releve du
    # 19/08/2026 sur les dix-neuf collages archives : douze selections declarees
    # « neuves », **douze avec `event_id = None`, et douze deja en base**. Un
    # `--ecrire` naif aurait donc insere douze doublons orphelins — sans sport, ni
    # competition, ni palier reel, donc muets dans toutes les statistiques.
    #
    # Le second filet ne depend d'aucune resolution : marche et libelle, dans la
    # session. Il est **plus large** que la signature d'origine et c'est voulu —
    # ici on refuse d'ecrire, et refuser une ligne se rattrape en la saisissant,
    # quand un doublon ne se rattrape pas.
    deja = {
        (pick.market.strip(), pick.selection.strip())
        for pick in history_service.list_picks(collage.session_id, settings)
    }
    fresh, known = [], []
    for pick in preview.picks:
        cible = known if pick.duplicate else fresh
        if not pick.duplicate and (pick.market.strip(), pick.selection.strip()) in deja:
            cible = known
        cible.append(pick)
    report = ReplayReport(
        import_id=import_id,
        session_id=collage.session_id,
        char_count=collage.char_count,
        fresh=fresh,
        known=known,
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


@dataclass
class AttachReport:
    """Ce qu'un rattachement a pose sur des selections **deja en base**.

    Distinct de `ReplayReport`, et il fallait qu'il le soit : l'un cree des
    selections, l'autre en complete. Les fondre aurait fait lire « 5 recuperees »
    la ou rien n'est apparu et ou cinq lignes ont gagne leur cran.
    """

    import_id: int
    session_id: int
    #: Blocs lus et apparies a une ligne du collage.
    claims_read: int = 0
    #: Selections **deja en base** qui ont recu leur bloc.
    attached: int = 0
    #: Selections qui en portaient deja un. **Jamais reecrites** : le premier
    #: releve fait foi, et le rattachement n'est pas une correction.
    already: int = 0
    #: Blocs sans selection correspondante, ou correspondant a plusieurs.
    unmatched: list[str] = field(default_factory=list)
    dossiers: str = ""
    marks: int = 0
    combos: int = 0
    combo_failures: list[str] = field(default_factory=list)
    #: Ce que ce chemin a **perdu**, journalise comme partout ailleurs.
    #:
    #: **Il ne l'etait pas, et c'est la deuxieme fois sur ce fichier.**
    #: `CONTRIBUTING.md` dit de la premiere : « `myassistantbet-replay` a ete
    #: ecrit le meme jour et par la meme main que cette phrase, et il a laisse
    #: tomber ses echecs d'ecriture sans les journaliser ». Le rattachement l'a
    #: refait — un bloc qui ne trouve pas sa selection, un combine dont une jambe
    #: manque, se disaient a l'ecran et nulle part ailleurs.
    rejects: list[ingestion_service.Reject] = field(default_factory=list)
    dry_run: bool = True

    @property
    def lines(self) -> list[str]:
        mode = "SIMULATION — rien n'est écrit" if self.dry_run else "ÉCRITURE"
        out = [
            f"Import {self.import_id} · session {self.session_id} · {mode}",
            f"  {self.claims_read} bloc(s) lu(s) · {self.attached} posé(s) sur une "
            f"sélection existante · {self.already} déjà pourvue(s)",
            f"  dossiers_ouverts : {self.dossiers or '—'} ({self.marks} repère(s))"
            f" · {self.combos} combiné(s)",
        ]
        out += [f"    ! {motif}" for motif in self.unmatched]
        out += [f"    ✗ {motif}" for motif in self.combo_failures]
        return out


def attach(
    import_id: int,
    write: bool = False,
    settings: Settings | None = None,
) -> AttachReport:
    """Pose sur les selections **deja en base** ce qu'un collage portait en plus.

    **C'est le geste que `replay` ne peut pas faire, et le seul qui recupere
    quelque chose ici.** Mesure du 19/08/2026 : les trois collages complets de la
    base ne rendent **aucune selection neuve** — leurs lignes de section C sont
    entrees par les re-collages du seul tableau qui les ont suivis. Ce qui manque
    a ces lignes n'est pas leur existence, c'est leur **bloc de confiance**, donc
    le cran calcule, donc la mesure que toute la chaine attend depuis le lot 1.

    Trois regles, et c'est le dessin entier :

    - **on ne cree rien.** Une selection absente reste absente : la creer serait
      le travail de `replay`, et melanger les deux ferait lire « recuperees » la
      ou rien n'est apparu ;
    - **on n'ecrase rien.** Une selection qui porte deja un bloc est comptee et
      laissee : le premier releve fait foi, et ce rattachement n'est pas une
      correction ;
    - **le rapprochement exige l'unicite.** Un bloc qui correspond a zero ou a
      plusieurs selections de la session est **dit**, jamais pose au hasard —
      poser un cran sur la mauvaise ligne serait exactement le defaut que la
      somme de controle de l'appariement existe pour empecher.
    """
    settings = settings or get_settings()
    collage = imports_raw.get(import_id, settings)
    if collage is None:
        raise LookupError(f"Aucun collage conservé sous l'identifiant {import_id}.")

    preview = picks_import.build_preview(collage.session_id, collage.raw_text, settings)
    report = AttachReport(
        import_id=import_id,
        session_id=collage.session_id,
        claims_read=preview.claims_attached,
        dossiers=preview.opened.state or "",
        marks=len(preview.opened.marks or ()),
        dry_run=not write,
    )

    par_ligne: dict[int, int] = {}
    for pick in preview.picks:
        cible, motif = history_service.unique_pick(
            collage.session_id, pick.market, pick.selection, settings
        )
        if cible is not None:
            par_ligne[pick.index] = cible
        if pick.claim is None:
            continue
        if cible is None:
            report.unmatched.append(f"{pick.market} {pick.selection} : {motif}")
            report.rejects.append(
                ingestion_service.Reject(
                    block_type=ingestion_service.CONF,
                    reason=ingestion_service.MATCH_REF_UNRESOLVED,
                    detail=f"{pick.market} {pick.selection} : {motif}",
                    payload=pick.claim.raw,
                    start=pick.claim.start,
                    end=pick.claim.end,
                )
            )
            continue
        if not write:
            deja = history_service.has_claim(cible, settings)
            report.already += int(deja)
            report.attached += int(not deja)
            continue
        pose = history_service.attach_claim(
            cible,
            claim=pick.claim.raw,
            source_level=pick.source,
            opened=pick.opened,
            override_cause=pick.override_cause,
            claim_offsets=_span(pick.claim.start, pick.claim.end),
            settings=settings,
        )
        report.attached += int(pose)
        report.already += int(not pose)

    # **Un rattachement ne pose jamais `absente`.** L'etat de session s'ecrase a
    # chaque lecture — « le dernier rendu colle decrit l'analyse en cours » — et
    # c'est juste pour un import ordinaire. Ici on repare : un collage qui n'a pas
    # emporte la ligne ne dit rien de l'analyse, il dit quelque chose du collage,
    # et le laisser effacer une declaration recuperee inverserait le sens du geste.
    if write and preview.opened.state and preview.opened.state != OPEN_ABSENT:
        history_service.set_open_dossiers(
            collage.session_id,
            set(preview.opened.marks or ()),
            settings,
            state=preview.opened.state,
        )
    for attache in preview.combos:
        manquantes = [row for row in attache.rows if row not in par_ligne]
        if manquantes:
            motif = (
                f"Combiné {attache.combo.kind or '?'} non rattaché : "
                f"{len(manquantes)} de ses {len(attache.rows)} jambes ne se retrouvent pas."
            )
            report.combo_failures.append(motif)
            report.rejects.append(
                ingestion_service.Reject(
                    block_type=ingestion_service.COMBO,
                    reason=ingestion_service.MATCH_REF_UNRESOLVED,
                    detail=motif,
                    payload=", ".join(attache.combo.marks),
                    start=attache.combo.start,
                    end=attache.combo.end,
                )
            )
            continue
        if not write:
            report.combos += 1
            continue
        try:
            combos_service.record(
                collage.session_id,
                attache.prompt_id,
                kind=attache.combo.kind,
                pick_ids=[par_ligne[row] for row in attache.rows],
                declared_price=attache.combo.declared_price,
                target_price=attache.combo.target_price,
                stop_reason=attache.combo.stop_reason or None,
                import_id=import_id,
                settings=settings,
            )
            report.combos += 1
        except (combos_service.ComboError, KeyError, ValueError) as exc:
            report.combo_failures.append(f"Combiné {attache.combo.kind or '?'} : {exc}")
            report.rejects.append(
                ingestion_service.Reject(
                    block_type=ingestion_service.COMBO,
                    reason=reject_reason(str(exc)),
                    detail=f"Combiné {attache.combo.kind or '?'} : {exc}",
                    payload=", ".join(attache.combo.marks),
                    start=attache.combo.start,
                    end=attache.combo.end,
                )
            )
    # **Les rejets de l'apercu voyagent avec les notres.** Ce chemin relit un
    # collage entier : ce que la lecture y perd est perdu ici aussi, et le taire
    # ferait de `--rattacher` le chemin muet que `selfcheck-ingestion` existe
    # pour interdire.
    report.rejects.extend(preview.rejects)
    if write:
        ingestion_service.record(collage.session_id, report.rejects, settings, import_id=import_id)
    return report


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
    parser.add_argument(
        "--rattacher",
        action="store_true",
        help=(
            "pose les blocs de confiance, les dossiers ouverts et les combinés sur "
            "les sélections DÉJÀ en base, sans en créer aucune"
        ),
    )
    parser.add_argument(
        "--prose",
        action="store_true",
        help=(
            "reprend « Angle (1 ligne) » et « Ce qui la tue » sur TOUTES les sélections "
            "dont le collage est conservé, sans en créer aucune"
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    settings = get_settings()

    # **Une passe globale, pas un rejeu de collage.** Elle ne prend aucun
    # identifiant : les deux colonnes ont ete jetees par tous les imports depuis
    # le premier, et reprendre collage par collage ferait dependre la couverture
    # de la memoire de celui qui tape. Le compte affiche dit ce qui n'a pas ete
    # retrouve, et c'est lui qui rend la passe verifiable.
    if args.prose:
        rapport = picks_import.rebuild_prose(apply=args.ecrire, settings=settings)
        print(rapport.line)
        if not args.ecrire:
            print("Simulation : rien n'a été écrit. Ajoute --ecrire pour enregistrer.")
        return 0

    if args.lister is not None:
        for collage in imports_raw.list_for_session(args.lister, settings):
            print(
                f"{collage.id:>5}  {collage.created_at}  {collage.char_count:>7} car."
                f"  {collage.source}"
            )
        return 0

    if args.import_id is None:
        parser.error("indique un identifiant, ou --lister SESSION pour les voir")
    action = attach if args.rattacher else replay
    try:
        report = action(args.import_id, write=args.ecrire, settings=settings)
    except LookupError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for line in report.lines:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
