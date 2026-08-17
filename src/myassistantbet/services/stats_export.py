"""Export de la page de statistiques dans un fichier autoportant.

La page n'etait consultable qu'a l'ecran : la faire relire ailleurs demandait
d'enchainer les captures — illisible et incomplet. Ce module rend le meme etat
des lieux en **un** fichier, Markdown pour un lecteur humain ou un modele, JSON
pour une machine.

**Une seule source de calcul, et c'est tout l'enjeu.** `report()` assemble ce
que la page consomme ; le gabarit HTML et les deux ecritures ci-dessous lisent
ce meme objet, et rien n'est recalcule ici. Un chiffre qui differerait entre
l'ecran et le fichier serait pire que pas de fichier du tout — l'export existe
justement pour faire relire ces chiffres-la.

**Chaque taux porte son denominateur et son intervalle.** A l'ecran, l'effectif
est a cote de la barre et l'intervalle est materialise dessus ; le fichier n'a
ni l'un ni l'autre, il les ecrit. Un pourcentage seul, hors de la page, est
exactement ce que cet export doit corriger.

**Les reserves voyagent avec les chiffres** (`StatsReport.warnings`). Un
sous-effectif, une population ecartee, un axe sans cran calcule : la page les
dit, et un chiffre exporte sans sa reserve est un chiffre qui sera mal lu. Elles
sont assemblees **une fois** ici, pour que le Markdown et le JSON en portent la
meme liste.

Aucun indicateur financier n'en sort, pas plus que de la page : rien n'est
multiplie par une mise, et le residu compare des issues deja tranchees a des
prix deja enregistres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from . import combos as combos_service
from . import coupons as coupons_service
from . import history as history_service
from . import ingestion as ingestion_service
from . import set_scores as set_scores_service
from .inference import EQUIVALENCE_MARGIN, MARGIN_REFERENCE

#: Formats servis. `md` s'adresse a un lecteur — humain ou modele — hors de
#: l'application ; `json` rend le meme releve a une machine.
FORMATS = ("md", "json")


@dataclass(frozen=True)
class Section:
    """Une section de la page, et son equivalent dans le fichier.

    **Le registre est le contrat de parite**, et c'est lui que le test lit : une
    section rendue a l'ecran sans entree ici est une section que l'export perd.
    Le titre est le meme des deux cotes a dessein — deux libelles pour un meme
    contenu auraient diverge au premier renommage, et la parite se verifierait
    alors sur une table de correspondance plutot que sur les pages elles-memes.

    `block` vide designe un bloc de premier niveau (un `h2` de la page, un `##`
    du fichier) ; sinon c'est le bloc auquel la section appartient. Deux blocs
    portent des cartes de meme titre — « Par palier » mesure l'analyse dans
    l'un, les paris poses dans l'autre — et seul le couple les distingue.
    """

    block: str
    title: str


#: Le bloc de tete ne porte pas de titre propre a l'ecran : c'est une tuile, pas
#: une section. Il en recoit un dans le fichier, ou rien ne le distinguerait
#: sinon du decompte qui le suit.
ANALYSIS_BLOCK = "Ce que ces sélections ont produit"
SELECTION_BLOCK = "Ce que tu écartes"
SETS_BLOCK = "Le score en sets annoncé"
#: Les combines etaient enregistres et **jamais restitues** : la page n'en portait
#: aucune section, si bien qu'un combine se lisait sur la feuille de sa session et
#: nulle part ailleurs.
COMBOS_BLOCK = "Les combinés proposés"
LABELLING_BLOCK = "Comment tu étiquettes"
BETS_BLOCK = "Ce que valent tes paris"

#: Les sections que la page peut rendre, bloc par bloc. Une carte absente du lot
#: n'est rendue ni a l'ecran ni dans le fichier ; c'est l'inverse qui serait un
#: defaut, et c'est lui que le test attrape.
SECTIONS: tuple[Section, ...] = (
    Section("", ANALYSIS_BLOCK),
    Section(ANALYSIS_BLOCK, "Par confiance annoncée"),
    Section(ANALYSIS_BLOCK, "Dossiers non ouverts"),
    Section(ANALYSIS_BLOCK, "Par cran calculé"),
    Section(ANALYSIS_BLOCK, "Par palier"),
    Section(ANALYSIS_BLOCK, "Par sport"),
    Section(ANALYSIS_BLOCK, "Par niveau de compétition"),
    Section(ANALYSIS_BLOCK, "Par famille de marché"),
    Section(ANALYSIS_BLOCK, "Par marché"),
    Section(ANALYSIS_BLOCK, "Par type d'angle"),
    Section(ANALYSIS_BLOCK, "Par niveau de source"),
    Section(ANALYSIS_BLOCK, "Résidu au prix, par cran de confiance"),
    Section(ANALYSIS_BLOCK, "Résidu au prix, par type d'angle"),
    Section(ANALYSIS_BLOCK, "Résidu au prix, par marché"),
    Section(ANALYSIS_BLOCK, "Angle « manière » rendu en vainqueur"),
    Section("", SELECTION_BLOCK),
    Section(SELECTION_BLOCK, "Par session"),
    Section("", SETS_BLOCK),
    Section("", COMBOS_BLOCK),
    Section("", LABELLING_BLOCK),
    Section(LABELLING_BLOCK, "Par confiance annoncée"),
    Section(LABELLING_BLOCK, "Par palier"),
    Section("", BETS_BLOCK),
    Section(BETS_BLOCK, "Par palier"),
    Section(BETS_BLOCK, "Par sport"),
    Section(BETS_BLOCK, "Coupons"),
)


@dataclass
class StatsReport:
    """L'etat des lieux complet, assemble une fois et lu par les deux surfaces.

    Ce n'est pas un calcul de plus : c'est le **point d'assemblage** qui
    manquait. La route de la page composait son contexte a la main, si bien
    qu'un export ecrit a cote aurait eu sa propre liste de sources — donc sa
    propre facon de vieillir.
    """

    analysis: history_service.Analysis
    labelling: list[history_service.Mix] = field(default_factory=list)
    stats: history_service.Stats = field(default_factory=history_service.Stats)
    coupon_rates: list[history_service.RateRow] = field(default_factory=list)
    set_scores: set_scores_service.Report = field(default_factory=set_scores_service.Report)
    #: Ce que l'ingestion a perdu, par type et par motif. **Un compte, jamais un
    #: taux** : il est juste a tout effectif, et il dit ce qu'aucun autre chiffre
    #: de la page ne dit — que le probleme n'est pas dans les donnees mais dans
    #: le chemin qui les amene.
    ingestion: ingestion_service.Summary = field(default_factory=ingestion_service.Summary)
    #: Les combines proposes, sur toute la base. **Un compte et un ecart, jamais
    #: un taux** : au taux de jambe constate, un combine de dix jambes passe une
    #: fois sur 280, et son taux ne sera jamais mesurable.
    combos: combos_service.Summary = field(default_factory=combos_service.Summary)
    set_score_options: list[str] = field(default_factory=list)
    set_score_matrix: list[tuple[str, list[int], int]] = field(default_factory=list)
    #: Point de comparaison du residu, pas une estimation de l'overround reel.
    margin_reference: float = MARGIN_REFERENCE
    #: L'ecart sous lequel un second axe d'etiquetage ne se justifie pas.
    equivalence_margin: float = EQUIVALENCE_MARGIN
    #: Instant de la generation du fichier, en heure locale. Distinct de
    #: `analysis.as_of`, qui date la **lecture des donnees** : les deux
    #: coincident sur un export immediat et divergent sur un fichier relu plus
    #: tard, ou c'est l'arrete qui fait foi.
    generated_at: datetime | None = None

    @property
    def context(self) -> dict[str, Any]:
        """Ce que le gabarit de la page attend, dans son vocabulaire."""
        return {
            "analysis": self.analysis,
            "margin_reference": self.margin_reference,
            "equivalence_margin": self.equivalence_margin,
            "labelling": self.labelling,
            "stats": self.stats,
            "coupon_rates": self.coupon_rates,
            "set_scores": self.set_scores,
            "set_score_options": self.set_score_options,
            "set_score_matrix": self.set_score_matrix,
            "ingestion": self.ingestion,
            "combos": self.combos,
        }

    @property
    def empty(self) -> bool:
        """Meme condition que la page : rien a mesurer nulle part."""
        return (
            self.analysis.empty
            and self.stats.empty
            and not self.labelling
            and self.set_scores.empty
            and self.combos.empty
        )

    @property
    def generated_label(self) -> str:
        """« 14/08/2026 21:07 ». Vide tant que rien n'est date."""
        return "" if self.generated_at is None else self.generated_at.strftime("%d/%m/%Y %H:%M")

    @property
    def day(self) -> str:
        """La date qui nomme le fichier, `YYYY-MM-DD`."""
        return "" if self.generated_at is None else self.generated_at.strftime("%Y-%m-%d")

    @property
    def sessions(self) -> int:
        """Sessions couvertes par le releve."""
        return len(self.analysis.by_session)

    @property
    def sections(self) -> list[Section]:
        """Les sections que **ce** releve rend, dans l'ordre de la page.

        C'est cette liste que les deux ecritures parcourent : le fichier ne
        peut donc pas porter une section que la page tait, ni l'inverse.
        """
        analysis = self.analysis
        rendered: list[tuple[Section, bool]] = [
            (Section("", ANALYSIS_BLOCK), not analysis.empty),
            (Section(ANALYSIS_BLOCK, "Par confiance annoncée"), not analysis.empty),
            (Section(ANALYSIS_BLOCK, "Dossiers non ouverts"), bool(analysis.override.total)),
            (Section(ANALYSIS_BLOCK, "Par cran calculé"), not analysis.empty),
            (Section(ANALYSIS_BLOCK, "Par palier"), not analysis.empty),
            (Section(ANALYSIS_BLOCK, "Par sport"), not analysis.empty),
            (Section(ANALYSIS_BLOCK, "Par niveau de compétition"), bool(analysis.by_category)),
            (Section(ANALYSIS_BLOCK, "Par famille de marché"), bool(analysis.by_family)),
            (Section(ANALYSIS_BLOCK, "Par marché"), bool(analysis.by_market)),
            (Section(ANALYSIS_BLOCK, "Par type d'angle"), bool(analysis.by_angle)),
            (
                Section(ANALYSIS_BLOCK, "Résidu au prix, par cran de confiance"),
                bool(analysis.residual_by_confidence),
            ),
            (
                Section(ANALYSIS_BLOCK, "Résidu au prix, par type d'angle"),
                bool(analysis.residual_by_angle),
            ),
            (
                Section(ANALYSIS_BLOCK, "Résidu au prix, par marché"),
                bool(analysis.residual_by_market),
            ),
            (Section(ANALYSIS_BLOCK, "Par niveau de source"), bool(analysis.by_source)),
            (
                Section(ANALYSIS_BLOCK, "Angle « manière » rendu en vainqueur"),
                analysis.conflicts.known,
            ),
            (Section("", SELECTION_BLOCK), bool(analysis.by_session)),
            (Section(SELECTION_BLOCK, "Par session"), bool(analysis.by_session)),
            (Section("", SETS_BLOCK), not self.set_scores.empty),
            (Section("", COMBOS_BLOCK), not self.combos.empty),
            (Section("", LABELLING_BLOCK), bool(self.labelling)),
            *((Section(LABELLING_BLOCK, f"Par {block.label}"), True) for block in self.labelling),
            # **Le bloc est rendu meme vide**, et ses cartes ne le sont pas :
            # l'absence de pari pose est une information, l'absence d'une carte
            # a l'interieur n'en est pas une.
            (Section("", BETS_BLOCK), True),
            (Section(BETS_BLOCK, "Par palier"), not self.stats.empty),
            (Section(BETS_BLOCK, "Par sport"), not self.stats.empty),
            (Section(BETS_BLOCK, "Coupons"), bool(self.coupon_rates)),
        ]
        return [section for section, shown in rendered if shown]

    @property
    def warnings(self) -> list[str]:
        """Les reserves de lecture, dans l'ordre ou la page les pose.

        **Un chiffre exporte sans sa reserve est un chiffre mal lu**, et le
        fichier est justement destine a etre relu loin de la page qui les
        porte. Elles sont derivees des memes champs que l'ecran, jamais
        recomptees : ce sont des phrases sur des nombres deja calcules.
        """
        analysis = self.analysis
        notes: list[str] = []
        if not analysis.enough:
            notes.append(
                f"{analysis.settled} sélection(s) tranchée(s) sur seulement "
                f"{analysis.days} journée(s) d'analyse. Ces taux décrivent ces "
                "journées-là — un tournoi, une soirée de coupe — et non une façon "
                f"d'analyser : il en faudrait {analysis.minimum} réparties sur "
                f"{analysis.minimum_days} journées pour qu'un regroupement se lise "
                "comme une tendance."
            )
        if analysis.without_antecedence:
            notes.append(
                f"{analysis.without_antecedence} sélection(s) tranchée(s) sont écartées "
                "de tout ce relevé : leur antériorité n'est pas établie, donc leur "
                "étiquette non plus."
            )
        for gap in analysis.column_gaps:
            # **Une reserve qui voyage.** Un fichier relu ailleurs doit dire
            # qu'une de ses colonnes n'a jamais rien recu : sans elle, un
            # regroupement vide s'y lit comme une mesure a zero.
            notes.append(
                gap.line
                + (
                    " Deux sessions d'import d'affilée sans une valeur : rien ne l'alimente."
                    if gap.alert
                    else " Une seule session concernée : peut-être un collage incomplet."
                )
            )
        if not analysis.consistent:
            detail = "".join(f" {gap.line}." for gap in analysis.gaps)
            notes.append(
                "Ce que la page compte ne retombe pas sur ce que porte la base : "
                f"{analysis.recorded} sélection(s) tranchée(s) enregistrée(s), "
                f"{analysis.settled} comptée(s) ici.{detail} C'est un défaut de "
                "l'outil, pas de la saisie."
            )
        if not self.ingestion.empty:
            detail = " · ".join(f"{row.label} ({row.count})" for row in self.ingestion.rows)
            notes.append(
                f"{self.ingestion.total} bloc(s) rejeté(s) à l'import sur "
                f"{self.ingestion.sessions} session(s) : {detail}. Un rejet décrit le "
                "chemin d'ingestion, jamais le modèle — c'est un bloc qui a existé et "
                "qui n'est pas entré, donc quelque chose que la page ne mesure pas."
            )
        if analysis.clustered_selections:
            notes.append(
                f"{analysis.clustered_selections} sélection(s) partagent un match avec "
                "une autre : les intervalles supposent l'indépendance, ils sont donc "
                "optimistes et les vrais sont plus larges."
            )
        if not analysis.by_confidence_computed:
            notes.append(
                "Aucun cran calculé : les sélections antérieures à ce chantier n'ont "
                "pas de bloc structuré, et rien ne les rétro-remplit — un faisceau "
                "d'information ne s'invente pas après coup."
            )
        if analysis.quarantined:
            notes.append(
                f"{analysis.quarantined} sélection(s) tranchée(s) assise(s) sur une cote "
                "de référence attendent leur prix réel : elles sortent du regroupement "
                "par palier, et de celui-là seulement."
            )
        if analysis.uncategorised:
            notes.append(
                f"{analysis.uncategorised} sélection(s) tranchée(s) ne portent aucun "
                "niveau de compétition — compétition à classer, ou sélection sans match "
                "rattaché."
            )
        for count, libelle in (
            (analysis.unlabelled_confidence, "confiance annoncée"),
            (analysis.unlabelled_angle, "type d'angle"),
            (analysis.unlabelled_source, "niveau de source"),
            (analysis.unlabelled_market, "libellé de marché"),
        ):
            if count:
                notes.append(
                    f"{count} sélection(s) tranchée(s) sans {libelle} : comptée(s) au "
                    "total, hors de ce regroupement."
                )
        if analysis.unclassified_markets:
            notes.append(
                f"{analysis.unclassified_markets} sélection(s) tranchée(s) portent un "
                "marché qu'aucune famille ne couvre — il n'est jamais rangé d'office "
                "dans « Autre »."
            )
        if analysis.hidden_markets:
            notes.append(
                f"{analysis.hidden_markets} marché(s) vu(s) une seule fois ne sont pas "
                "listés : un libellé vu une fois n'est pas un taux fragile, c'est du "
                "bruit d'orthographe."
            )
        for overlap in analysis.overlaps:
            notes.append(
                f"{overlap.note} : le second regroupement n'ajoute aucune observation au premier."
            )
        if analysis.partial_overlaps:
            notes.append(
                f"{analysis.partial_overlaps} autre(s) paire(s) de regroupements se "
                "recouvrent partiellement — voir la matrice de recouvrement."
            )
        notes.append(
            "Aucun indicateur financier n'est produit : rien n'est multiplié par une "
            "mise, aucun solde ni aucun gain n'est calculé. Le résidu lui-même compare "
            "des issues déjà tranchées à des prix déjà enregistrés."
        )
        return notes


def report(settings: Settings | None = None) -> StatsReport:
    """Assemble l'etat des lieux. **Le seul point de calcul de la page.**"""
    settings = settings or get_settings()
    # `report()` du module des scores en sets etait appele deux fois par la
    # route : la matrice se derive du meme releve, elle ne se releve pas a part.
    sets = set_scores_service.report(settings)
    return StatsReport(
        analysis=history_service.analysis(settings),
        labelling=history_service.labelling(settings),
        stats=history_service.stats(settings),
        coupon_rates=coupons_service.rates(settings),
        set_scores=sets,
        set_score_options=list(set_scores_service.SCORES),
        set_score_matrix=set_scores_service.matrix_rows(sets),
        ingestion=ingestion_service.summary(settings),
        combos=combos_service.summary(settings),
        generated_at=datetime.now(ZoneInfo(settings.tz)),
    )


def filename(found: StatsReport, fmt: str = "md") -> str:
    """« stats_myassistantbet_2026-08-14.md »."""
    return f"stats_myassistantbet_{found.day}.{fmt}"


# -- Ecriture JSON ----------------------------------------------------------


def _residual_row(row: history_service.ResidualRow) -> dict[str, Any]:
    """Un niveau et son residu au prix, avec son taux brut a cote.

    Le taux y figure parce qu'il reste lisible pour ce qu'il est ; il n'y figure
    **jamais seul**, et c'est toute la difference avec les cartes de taux.
    """
    return {
        "key": row.key,
        "label": row.label,
        "settled": row.settled,
        "won": row.won,
        "expected": row.residual.expected,
        "gap": row.residual.gap,
        "p_value": row.residual.p_value,
        "rate": row.rate,
        "interval": list(row.interval) if row.interval else None,
        #: Selections supplementaires au meme regime pour que l'ecart tienne.
        "horizon": row.horizon,
        "established": row.established,
    }


def _rate(row: history_service.RateRow) -> dict[str, Any]:
    """Un regroupement, avec **toujours** son denominateur et son intervalle."""
    return {
        "key": row.key,
        "label": row.label,
        "won": row.won,
        "lost": row.lost,
        "settled": row.settled,
        "void": row.void,
        "pending": row.pending,
        "rate": row.rate,
        "interval": list(row.interval) if row.interval else None,
        # L'effectif independant : trois lignes sur le meme match ne sont pas
        # trois observations.
        "units": row.units,
        "clustered": row.clustered,
        "complement": list(row.complement),
        "carried": row.carried,
        "fragility": row.fragility,
        "band": row.band.label if row.band and row.band.targeted else None,
        "off_band": row.off_band,
    }


def _residual(residu: Any) -> dict[str, Any]:
    return {
        "observed": residu.observed,
        "settled": residu.settled,
        "expected": residu.expected,
        "gap": residu.gap,
        "p_value": residu.p_value,
        "annulling_overround": residu.annulling_overround,
        "tipping_margin": residu.tipping_margin,
        "fragility": residu.fragility,
    }


def _session(row: history_service.SessionRate) -> dict[str, Any]:
    return {
        "session_id": row.session_id,
        "day": row.day,
        "sports": row.sports,
        "lot": row.lot,
        "reconstructed": row.reconstructed,
        "covered": row.covered,
        "passed": row.passed,
        "picks": row.picks,
        "outside": row.outside,
        "selection_rate": row.selection_rate,
        "degenerate": row.degenerate,
        "density": row.density,
        "overridden": row.overridden,
        "opened": row.opened,
        "on_priority": row.on_priority,
        "tokens": row.tokens,
        "tokens_per_match": row.tokens_per_match,
        "feedback_active": row.feedback_active,
        "guarded": row.guarded,
        "rates": _rate(row.rates),
    }


def _mix(block: history_service.Mix) -> dict[str, Any]:
    return {
        "key": block.key,
        "label": block.label,
        "total": block.total,
        "unlabelled": block.unlabelled,
        "sessions": block.sessions,
        "levels": block.levels,
        "used": block.used,
        "concentrated": block.concentrated,
        "top_share": block.top_share,
        "rows": [
            {
                "key": row.key,
                "label": row.label,
                "count": row.count,
                "share": row.share,
                "absent_sessions": row.absent_sessions,
            }
            for row in block.rows
        ],
    }


def as_json(found: StatsReport) -> dict[str, Any]:
    """Le releve brut, pour usage machine. Aucun recalcul : les memes objets."""
    analysis = found.analysis
    return {
        "meta": {
            "generated_at": found.generated_at.isoformat() if found.generated_at else None,
            "as_of": analysis.as_of,
            "sessions": found.sessions,
            "settled": analysis.settled,
            "recorded": analysis.recorded,
            "without_antecedence": analysis.without_antecedence,
            "void": analysis.overall.void,
            "pending": analysis.overall.pending,
            "days": analysis.days,
            "scope": SCOPE,
            "consistent": analysis.consistent,
            "enough": analysis.enough,
            "minimum": analysis.minimum,
            "minimum_days": analysis.minimum_days,
            "minimum_rows": analysis.minimum_rows,
            "margin_reference": found.margin_reference,
            "equivalence_margin": found.equivalence_margin,
        },
        "warnings": found.warnings,
        # Ce que l'ingestion a perdu. **Hors des regroupements** : ce n'est pas
        # une mesure sur les selections, c'est une mesure sur le chemin qui les
        # amene, et l'y ranger ferait croire a un axe de plus.
        "ingestion": {
            "total": found.ingestion.total,
            "sessions": found.ingestion.sessions,
            "since": found.ingestion.since,
            "rows": [
                {"block_type": row.block_type, "reason": row.reason, "count": row.count}
                for row in found.ingestion.rows
            ],
        },
        # Les combines proposes. **Aucun taux** : au taux de jambe constate, un
        # combine de dix jambes passe une fois sur 280.
        "combos": [
            {
                "id": combo.id,
                "session_id": combo.session_id,
                "prompt_id": combo.prompt_id,
                "kind": combo.kind,
                "legs": len(combo.legs),
                "declared_price": combo.declared_price,
                "computed_price": combo.computed_price,
                "price_gap": combo.price_gap,
                "target_price": combo.target_price,
                "stop_reason": combo.stop_reason,
                "legs_won": combo.legs_won,
                "legs_settled": combo.legs_settled,
                "first_loss_rank": combo.first_loss_rank,
            }
            for combo in found.combos.combos
        ],
        "sections": [
            {"block": section.block, "title": section.title} for section in found.sections
        ],
        "residual": {
            "antecedence": _residual(analysis.residual),
            "late": _residual(analysis.residual_late),
            "under_margin": _residual(analysis.residual.with_margin(found.margin_reference)),
            "clustered_selections": analysis.clustered_selections,
            "clustered_p_value": analysis.clustered_p_value(found.margin_reference),
            "unpriced": analysis.unpriced,
        },
        "totals": {
            "overall": _rate(analysis.overall),
            "played": _rate(analysis.played),
            "skipped": _rate(analysis.skipped),
            "comparable": analysis.comparable,
            "settled_events": analysis.settled_events,
        },
        "groups": {
            "by_confidence": [_rate(row) for row in analysis.by_confidence],
            "by_confidence_computed": [_rate(row) for row in analysis.by_confidence_computed],
            "by_tier": [_rate(row) for row in analysis.by_tier],
            "by_sport": [_rate(row) for row in analysis.by_sport],
            "by_category": [_rate(row) for row in analysis.by_category],
            "by_family": [
                {"rates": _rate(entry.rates), "markets": [_rate(row) for row in entry.markets]}
                for entry in analysis.by_family
            ],
            "by_market": [_rate(row) for row in analysis.by_market],
            "by_angle": [_rate(row) for row in analysis.by_angle],
            "by_source": [_rate(row) for row in analysis.by_source],
            "carried": [_rate(row) for row in analysis.carried_rows],
            "folded": analysis.folded_rows,
        },
        # Le residu decline. Rendu **a cote** des taux et jamais a leur place :
        # un taux dit combien de fois ca tombe, un residu si ca tombe plus
        # souvent que les prix ne l'annoncaient.
        "residuals": {
            "by_confidence": [_residual_row(row) for row in analysis.residual_by_confidence],
            "by_angle": [_residual_row(row) for row in analysis.residual_by_angle],
            "by_market": [_residual_row(row) for row in analysis.residual_by_market],
        },
        "discrimination": {
            "horizons": [
                {
                    "question": horizon.question,
                    "detail": horizon.detail,
                    "have": horizon.have,
                    "need": horizon.need,
                    "missing": horizon.missing,
                    "sessions": horizon.sessions,
                }
                for horizon in analysis.horizons
            ],
            "scales": (
                {
                    "gap": analysis.scales.gap,
                    "interval": list(analysis.scales.interval)
                    if analysis.scales.interval
                    else None,
                    "established": analysis.scales.established,
                }
                if analysis.scales
                else None
            ),
            "ordered_scales": [
                {"scale": libelle, "p_value": valeur} for libelle, valeur in analysis.ordered_scales
            ],
        },
        "overlap_matrix": [
            {
                "left_axis": gauche,
                "right_axis": droite,
                "cells": [{"left": gl, "right": dl, "jaccard": part} for gl, dl, part in cellules],
            }
            for gauche, droite, cellules in analysis.overlap_matrix
        ],
        "notation": {
            "comparable": analysis.notation.comparable,
            "agreed": analysis.notation.agreed,
            "disagreed": analysis.notation.disagreed,
            "uncomputed": analysis.notation.uncomputed,
            "drift": analysis.notation.drift,
            "transitions": [
                {"declared": declared, "computed": computed, "count": count}
                for declared, computed, count in analysis.notation.transitions
            ],
            "clause": analysis.notation.clause_line,
        },
        "override": {
            "total": analysis.override.total,
            "fabricated": analysis.override.fabricated,
            "claimed": [
                {"rung": rung, "count": count} for rung, count in analysis.override.claimed
            ],
        },
        "conflicts": {
            "count": analysis.conflicts.count,
            "labelled": analysis.conflicts.labelled,
            "rate": analysis.conflicts.rate,
            "by_sport": [
                {"sport": sport, "conflict": conflit, "manners": total}
                for sport, conflit, total in analysis.conflicts.by_sport
            ],
            "by_session": [
                {"day": jour, "conflict": conflit, "manners": total}
                for jour, conflit, total in analysis.conflicts.by_session
            ],
        },
        "by_session": [_session(row) for row in analysis.by_session],
        "set_scores": {
            "settled": found.set_scores.settled,
            "exact": found.set_scores.exact,
            "issue_only": found.set_scores.issue_only,
            "alternate": found.set_scores.alternate,
            "missed": found.set_scores.missed,
            "pending": found.set_scores.pending,
            "exact_rate": found.set_scores.exact_rate,
            "issue_rate": found.set_scores.issue_rate,
            "matrix": [
                {
                    "predicted": annonce,
                    # `matrix_rows` compte sur `SCORES` : les deux listes ont la
                    # meme longueur par construction, et `strict` le garde.
                    "counts": dict(zip(found.set_score_options, comptes, strict=True)),
                    "total": total,
                }
                for annonce, comptes, total in found.set_score_matrix
            ],
        },
        "labelling": [_mix(block) for block in found.labelling],
        "bets": {
            "overall": _rate(found.stats.overall),
            "by_tier": [_rate(row) for row in found.stats.by_tier],
            "by_sport": [_rate(row) for row in found.stats.by_sport],
            "quarantined": found.stats.quarantined,
            "coupons": [_rate(row) for row in found.coupon_rates],
        },
    }


# -- Ecriture Markdown ------------------------------------------------------

#: Le perimetre, dit une fois et repris par les deux ecritures. La page le pose
#: en sous-titre de bloc ; hors d'elle, il doit voyager avec les chiffres.
SCOPE = (
    "toutes les sélections, jouées ou non, dont l'antériorité est établie — "
    "une sélection écartée dont le résultat est connu dit autant si l'analyse "
    "voyait juste"
)

#: Colonnes du tableau de taux. L'intervalle y est une colonne a part entiere :
#: c'est la precision du taux, et le fichier ne peut pas la dessiner.
RATE_HEADER = (
    "| Ligne | Gagnées | Tranchées | Taux | Intervalle 95 % | Annulées | En attente "
    "| Reste de l'axe | Portée |"
)
RATE_RULE = "| --- | ---: | ---: | ---: | :---: | ---: | ---: | ---: | --- |"


def _decimal(value: float, digits: int = 1) -> str:
    """La virgule decimale, comme partout dans l'interface."""
    return f"{value:.{digits}f}".replace(".", ",")


def _prix(value: float | None) -> str:
    """Une cote, ou un tiret quand elle est incalculable.

    Une cote de combine l'est des qu'une jambe n'a pas de prix : un produit
    partiel serait plus bas que le vrai sans que rien ne le dise.
    """
    return "—" if value is None else _decimal(value, 2)


def _rate_table(rows: list[history_service.RateRow]) -> list[str]:
    """Un regroupement en tableau. Rien n'y est calcule : tout vient des lignes."""
    if not rows:
        return ["_Rien de tranché sur ce regroupement._"]
    lines = [RATE_HEADER, RATE_RULE]
    for row in rows:
        other_won, other_settled = row.complement
        reste = f"{other_won}/{other_settled}" if other_settled else "—"
        if row.carried:
            portee = "portée"
            if row.fragility:
                portee += f" — {row.fragility} résultat(s) retourné(s) l'effaceraient"
        else:
            portee = "—"
        libelle = row.label
        if row.clustered:
            # Meme idiome que la barre : l'effectif independant ne s'ecrit que
            # s'il differe, sinon ce serait payer une colonne pour dire qu'il
            # n'y a rien a signaler.
            libelle += f" ({row.units} év.)"
        if row.band is not None and row.band.targeted:
            libelle += f" [bande {row.band.offset_label} → {row.band.label}]"
        lines.append(
            f"| {libelle} | {row.won} | {row.settled} | {row.rate_label} "
            f"| {row.interval_label or '—'} | {row.void} | {row.pending} | {reste} | {portee} |"
        )
    return lines


def _card(
    found: StatsReport, block: str, title: str, rows: list[history_service.RateRow]
) -> list[str]:
    """Une carte de la page : son titre, son tableau.

    Rendue seulement si la page la rend — c'est `sections` qui tranche, jamais
    ce module. Deux conditions ecrites cote a cote auraient diverge, et la
    divergence se serait vue comme une section manquante dans le fichier.
    """
    if Section(block, title) not in found.sections:
        return []
    return ["", f"### {title}", "", *_rate_table(rows)]


def _residual_card(
    found: StatsReport, title: str, rows: list[history_service.ResidualRow]
) -> list[str]:
    """Le residu au prix d'un axe : son tableau, et l'horizon de chaque ligne.

    **Le taux brut y figure a cote et jamais seul.** Il dit combien de fois ca
    tombe, ce qui reste lisible tant qu'il ne sert pas a comparer deux
    regroupements qui ne jouent pas aux memes prix — la faute exacte que ces
    cartes existent pour eviter.
    """
    if Section(ANALYSIS_BLOCK, title) not in found.sections:
        return []
    out = [
        "",
        f"### {title}",
        "",
        "| Niveau | Tranchées | Gagnées | Payées par les prix | Écart | Taux | Intervalle |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        marque = " **·**" if row.established else ""
        out.append(
            f"| {row.label}{marque} | {row.settled} | {row.won} | {row.expected_label} | "
            f"{row.gap_label} | {row.rate_label} | {row.interval_label} |"
        )
    horizons = [row for row in rows if row.horizon]
    if horizons:
        out.append("")
        for row in horizons:
            out.append(
                f"- « {row.label} » : il faudrait environ {row.horizon} sélection(s) de "
                "plus au même régime pour que cet écart tienne."
            )
    if any(row.established for row in rows):
        out += [
            "",
            "Les lignes marquées **·** s'écartent de leurs propres prix au seuil habituel "
            "de la page. Ce n'est pas une conclusion : c'est le même test que le bloc de "
            "tête, appliqué à une ligne.",
        ]
    return out


def as_markdown(found: StatsReport) -> str:
    """Le releve complet, en un fichier lisible hors de l'application."""
    analysis = found.analysis
    out: list[str] = [
        "# Statistiques — MyAssistantBet",
        "",
        "## Métadonnées",
        "",
        f"- **Généré le** : {found.generated_label}",
        f"- **Données arrêtées au** : {analysis.as_of_label or '—'}",
        f"- **Sessions couvertes** : {found.sessions}",
        f"- **Sélections tranchées** : {analysis.settled} "
        f"({analysis.overall.won} gagnée(s), {analysis.overall.lost} perdue(s))",
        f"- **Dont jouées / écartées** : {analysis.played.settled} / {analysis.skipped.settled}",
        f"- **Annulées** : {analysis.overall.void} · **en attente** : {analysis.overall.pending}",
        f"- **Hors périmètre, antériorité non établie** : {analysis.without_antecedence}",
        f"- **Journées d'analyse** : {analysis.days}",
        f"- **Événements distincts** : {analysis.settled_events}",
        f"- **Périmètre** : {SCOPE}",
        "",
        "## Réserves de lecture",
        "",
    ]
    out += [f"- {note}" for note in found.warnings]

    if not analysis.empty:
        out += ["", f"## {ANALYSIS_BLOCK}", ""]
        residu = analysis.residual
        out += [
            "### Face aux prix des sélections",
            "",
            f"**{residu.observed} victoire(s) pour {residu.expected_label} payée(s) par les "
            f"prix** des {residu.settled} sélection(s) retenue(s).",
            "",
            f"- Écart : {_decimal(residu.gap) if residu.gap is not None else '—'}",
            f"- P (unilatéral, marge nulle) : {residu.p_label}",
            f"- P sous une marge de {_decimal(found.margin_reference * 100, 0)} % : "
            f"{residu.with_margin(found.margin_reference).p_label}",
        ]
        if residu.annulling_overround:
            out.append(
                f"- Marge qui annulerait l'écart : {residu.overround_label}"
                + (
                    f" · il n'en faudrait que {_decimal(residu.tipping_margin * 100)} % "
                    "pour qu'il cesse d'être net"
                    if residu.tipping_margin
                    else ""
                )
            )
        if residu.fragility:
            out.append(f"- Fragilité : {residu.fragility} résultat(s) suffiraient à l'effacer")
        if analysis.clustered_selections:
            out.append(
                f"- {analysis.clustered_selections} sélection(s) partagent un match ; en les "
                "supposant liées, P monte à "
                f"{_decimal(analysis.clustered_p_value(found.margin_reference), 3)}"
            )
        if analysis.residual_late.settled:
            tardif = analysis.residual_late
            out += [
                "",
                f"Sur les sélections dont l'antériorité **n'est pas** établie : "
                f"{tardif.observed} victoire(s) pour {tardif.expected_label} payée(s), "
                f"P = {tardif.p_label}. **Jamais additionné au précédent** — les deux "
                "populations ne mesurent pas la même chose, et leur différence est le "
                "diagnostic.",
            ]
        if analysis.unpriced:
            out.append(
                f"\n{analysis.unpriced} sélection(s) tranchée(s) sans cote enregistrée "
                "sortent des deux résidus, et de ceux-là seulement."
            )

        out += [
            "",
            "### Sélections tranchées",
            "",
            "| Population | Gagnées | Tranchées | Taux | Intervalle 95 % |",
            "| --- | ---: | ---: | ---: | :---: |",
        ]
        populations = [(analysis.overall, "Toutes")]
        if analysis.comparable:
            populations += [(analysis.played, "Jouées"), (analysis.skipped, "Écartées")]
        for row, libelle in populations:
            out.append(
                f"| {libelle} | {row.won} | {row.settled} | {row.rate_label} "
                f"| {row.interval_label or '—'} |"
            )
        if analysis.comparable:
            out += [
                "",
                "Les deux dernières lignes se lisent ensemble : si ce qui est écarté gagne "
                "aussi souvent que ce qui est joué, le tri n'apporte rien.",
            ]

        if analysis.carried_rows:
            out += ["", "### Ce qui s'écarte", ""]
            for row in analysis.carried_rows:
                fragilite = (
                    f" — {row.fragility} résultat(s) retourné(s) l'effaceraient"
                    if row.fragility
                    else ""
                )
                out.append(
                    f"- **{row.label}** {row.won}/{row.settled} ({row.rate_label}) contre "
                    f"{row.complement[0]}/{row.complement[1]}{fragilite}."
                )
        else:
            out += [
                "",
                "### Ce qui s'écarte",
                "",
                f"_Aucune des {analysis.folded_rows} lignes ne s'écarte de sa référence._",
            ]

        if analysis.horizons:
            out += ["", "### Pouvoir de discrimination des échelles", ""]
            for horizon in analysis.horizons:
                reste = (
                    f", soit environ {horizon.sessions} session(s)"
                    if horizon.missing
                    else " — **atteint**"
                )
                out.append(
                    f"- **{horizon.question.capitalize()}** — {horizon.have} sélections sur "
                    f"les ~{horizon.need} nécessaires{reste}. _{horizon.detail}_"
                )
            out += [
                "",
                "Ces comptes disent quand regarder à nouveau. Ils ne tranchent rien : un "
                "rythme de saisie n'est pas un résultat.",
            ]
        if analysis.scales and analysis.scales.interval:
            verdict = (
                "l'écart tient entièrement sous la marge : **une seule échelle suffit**"
                if analysis.scales.established
                else "l'intervalle sort de la marge : **on ne peut pas encore conclure** "
                "qu'une seule échelle suffirait"
            )
            out += [
                "",
                f"**Faut-il deux échelles ?** À confiance fixée, le palier écarte de "
                f"{analysis.scales.gap * 100:+.0f} points, intervalle "
                f"{analysis.scales.interval_label}, pour une marge d'équivalence de "
                f"{_decimal(found.equivalence_margin * 100, 0)} points — {verdict}.",
            ]
        if analysis.ordered_scales:
            ordres = "; ".join(
                f"{libelle} : "
                + ("oui, dans le sens attendu" if valeur < 0.05 else "rien de concluant")
                + f" (p = {_decimal(valeur, 3)})"
                for libelle, valeur in analysis.ordered_scales
            )
            out += [
                "",
                f"**Les échelles ordonnent-elles ?** {ordres}. Séparer et ordonner sont "
                "deux choses : une échelle inversée sépare tout autant.",
            ]

        if analysis.overlap_matrix:
            out += ["", "### Matrice de recouvrement", ""]
            for gauche, droite, cellules in analysis.overlap_matrix:
                out += [
                    f"**{gauche} × {droite}**",
                    "",
                    f"| {gauche} | {droite} | Jaccard |",
                    "| --- | --- | ---: |",
                ]
                out += [f"| {gl} | {dl} | {_decimal(part, 2)} |" for gl, dl, part in cellules]
                out.append("")
            out.append(
                "Un indice de 1,00 dit que les deux regroupements portent exactement les "
                "mêmes sélections ; 0,00 qu'ils n'en partagent aucune."
            )

        out += _card(found, ANALYSIS_BLOCK, "Par confiance annoncée", analysis.by_confidence)
        if Section(ANALYSIS_BLOCK, "Dossiers non ouverts") in found.sections:
            out += ["", "### Dossiers non ouverts", "", analysis.override.line]
        out += _card(found, ANALYSIS_BLOCK, "Par cran calculé", analysis.by_confidence_computed)
        if analysis.notation.line:
            out += [
                "",
                analysis.notation.line
                + (f" · {analysis.notation.clause_line}" if analysis.notation.clause_line else ""),
            ]
        out += _card(found, ANALYSIS_BLOCK, "Par palier", analysis.by_tier)
        out += _card(found, ANALYSIS_BLOCK, "Par sport", analysis.by_sport)
        out += _card(found, ANALYSIS_BLOCK, "Par niveau de compétition", analysis.by_category)
        if Section(ANALYSIS_BLOCK, "Par famille de marché") in found.sections:
            out += ["", "### Par famille de marché", ""]
            out += _rate_table([entry.rates for entry in analysis.by_family])
            for entry in analysis.by_family:
                out += ["", f"**{entry.rates.label} — le marché fin**", ""]
                out += _rate_table(entry.markets)
        out += _card(found, ANALYSIS_BLOCK, "Par marché", analysis.by_market)
        out += _card(found, ANALYSIS_BLOCK, "Par type d'angle", analysis.by_angle)
        out += _card(found, ANALYSIS_BLOCK, "Par niveau de source", analysis.by_source)
        # Les trois cartes ci-dessus ventilent des **taux bruts** ; celles-ci
        # comparent chaque selection a son prix. Les deux se lisent ensemble, et
        # seule la seconde permet de comparer deux regroupements.
        out += _residual_card(
            found, "Résidu au prix, par cran de confiance", analysis.residual_by_confidence
        )
        out += _residual_card(found, "Résidu au prix, par type d'angle", analysis.residual_by_angle)
        out += _residual_card(found, "Résidu au prix, par marché", analysis.residual_by_market)
        if Section(ANALYSIS_BLOCK, "Angle « manière » rendu en vainqueur") in found.sections:
            taux = (
                f" — {analysis.conflicts.rate * 100:.0f} %"
                if analysis.conflicts.rate is not None
                else ""
            )
            out += [
                "",
                "### Angle « manière » rendu en vainqueur",
                "",
                f"**{analysis.conflicts.count}** sur {analysis.conflicts.labelled} "
                f"sélection(s) tranchée(s) déclarant une manière{taux}.",
            ]
            if len(analysis.conflicts.by_sport) > 1:
                out += ["", "| Sport | En conflit | Manières |", "| --- | ---: | ---: |"]
                out += [
                    f"| {sport} | {conflit} | {total} |"
                    for sport, conflit, total in analysis.conflicts.by_sport
                ]

    if analysis.by_session:
        out += [
            "",
            f"## {SELECTION_BLOCK}",
            "",
            "Part du lot retenue — matchs sélectionnés / matchs entrés dans un prompt.",
            "",
            "### Par session",
            "",
            "| Session | Sports | Lot | Retenus | Passés | Sélections | Réussite "
            "| Lecture forcée | Sél./match | Prompt | / match |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for row in analysis.by_session:
            marques = " · ".join(
                mot
                for mot, actif in (
                    ("reconstruit", row.reconstructed),
                    ("bloc servi", row.feedback_active),
                    ("gardée", row.guarded),
                    ("lot entier passé", row.degenerate),
                )
                if actif
            )
            jour = f"{row.day}" + (f" ({marques})" if marques else "")
            reussite = f"{row.rates.won}/{row.rates.settled}" if row.rates.settled else "—"
            selections = f"{row.picks}" + (f" (dont {row.outside} hors lot)" if row.outside else "")
            out.append(
                f"| {jour} | {row.sports or '—'} | {row.lot if row.lot is not None else '—'} "
                f"| {row.covered} | {row.passed if row.passed is not None else '—'} "
                f"| {selections} | {reussite} | {row.overridden or '—'} "
                f"| {_decimal(row.density) if row.density else '—'} | {row.tokens or '—'} "
                f"| {row.tokens_per_match or '—'} |"
            )
        out += [
            "",
            "« Sél./match » mesure la corrélation entre paris, pas une densité : deux "
            "sélections sur la même rencontre ne sont pas deux observations. "
            "« Prompt/match » est le coût fixe du cadre, et ne se lit qu'à régime "
            "constant.",
        ]

    if not found.set_scores.empty:
        sets = found.set_scores
        out += [
            "",
            f"## {SETS_BLOCK}",
            "",
            "La lecture de la manière, mesurée sans aucun prix : ni cote, ni palier, ni "
            "mise n'entrent ici. Le score est écrit du point de vue du premier joueur "
            "nommé.",
            "",
        ]
        if sets.settled:
            out += [
                f"- **Score exact** : {sets.exact} sur {sets.settled} · intervalle "
                f"{sets.exact_interval_label}"
                + (f" · {sets.exact_flip_label}" if sets.exact_flip_label else ""),
                f"- **Vainqueur juste** : {sets.exact + sets.issue_only} sur {sets.settled} "
                f"· intervalle {sets.issue_interval_label}"
                + (f" · {sets.issue_flip_label}" if sets.issue_flip_label else ""),
                f"- **Issue juste, manière fausse** : {sets.issue_only}"
                + (f" · {sets.pending} en attente" if sets.pending else ""),
                f"- **Second scénario tombé** : {sets.alternate} — compté à part, deux "
                "scores proposés ne valant pas une lecture deux fois plus juste.",
                "",
                "| Annoncé \\ constaté | " + " | ".join(found.set_score_options) + " | Total |",
                "| --- |" + " ---: |" * (len(found.set_score_options) + 1),
            ]
            for annonce, comptes, total in found.set_score_matrix:
                cases = " | ".join(str(compte) for compte in comptes)
                out.append(f"| {annonce} | {cases} | {total} |")
        else:
            out.append(f"{sets.pending} score(s) annoncé(s), aucun résultat saisi pour l'instant.")

    if not found.combos.empty:
        out += [
            "",
            f"## {COMBOS_BLOCK}",
            "",
            f"{len(found.combos.combos)} combiné(s) sur {found.combos.sessions} session(s). "
            "**Aucun taux de réussite par combiné, et ce n'est pas un oubli** : au taux de "
            "jambe constaté, un combiné de dix jambes se tranche favorablement une fois sur "
            "280. Le combiné est un regroupement ; les jambes restent les unités de mesure, "
            "comptées individuellement plus haut. Aucune mise n'entre ici.",
            "",
            "| Session | Type | Jambes | Cote écrite | Cote recalculée | Écart | Arrêt "
            "| Jambes gagnées | 1re perdue |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
        for combo in found.combos.combos:
            ecart = "—" if combo.price_gap is None else f"{combo.price_gap * 100:+.1f} %"
            out.append(
                f"| {combo.session_id} | {combo.kind} | {len(combo.legs)} "
                f"| {_prix(combo.declared_price)} | {_prix(combo.computed_price)} "
                f"| {ecart} | {combo.stop_label} "
                f"| {combo.legs_won}/{combo.legs_settled} "
                f"| {combo.first_loss_rank or '—'} |"
            )
        if found.combos.mismatched:
            out += [
                "",
                f"{found.combos.mismatched} combiné(s) portent une cote écrite qui ne décrit "
                "pas celle de leurs jambes. C'est le seul chiffre du bloc qui juge le rendu "
                "plutôt que le lot : un écart répété dirait que le produit est ajusté pour "
                "tomber sur la cible.",
            ]

    if found.labelling:
        out += [
            "",
            f"## {LABELLING_BLOCK}",
            "",
            "Toutes les sélections, tranchées ou non — le seul bloc qui ne dépende "
            "d'aucun résultat.",
        ]
        for block in found.labelling:
            out += [
                "",
                f"### Par {block.label}",
                "",
                "| Niveau | Sélections | Part | Absent de |",
                "| --- | ---: | ---: | ---: |",
            ]
            for row in block.rows:
                absent = (
                    f"{row.absent_sessions}/{block.sessions} session(s)"
                    if row.absent_sessions
                    else "—"
                )
                out.append(f"| {row.label} | {row.count} | {row.share_label} | {absent} |")
            note = (
                f"{block.used} niveau(x) employé(s) sur {block.levels}, {block.total} sélection(s)."
            )
            if block.unlabelled:
                note += (
                    f" {block.unlabelled} sélection(s) sans {block.label} ne comptent pas "
                    "ici : ne pas étiqueter n'est pas un niveau de l'échelle."
                )
            if block.concentrated:
                note += (
                    f" {block.top_share_label} du volume tiennent sur {len(block.top)} "
                    f"niveaux — {block.top_labels} — sur {block.levels}."
                )
            out += ["", note]

    out += ["", f"## {BETS_BLOCK}", "", "Uniquement ce qui a été posé chez le bookmaker."]
    if found.stats.empty:
        # **Deux phrases, jamais une, et jamais un silence.** Le bloc etait
        # masque des deux cotes : une meme sortie pour « aucun pari pose » et
        # pour « cette page ne mesure pas les paris poses ». Le fichier existe
        # justement pour faire relire ces chiffres ailleurs, ou un bloc absent
        # ne se distingue pas d'un bloc qui n'a jamais existe.
        out += [
            "",
            "**Aucun coupon saisi.** Rien n'a été enregistré comme posé chez le "
            "bookmaker : ce n'est pas une collecte qui manque, c'est un geste qui n'a "
            "pas eu lieu.",
            "",
            "**Et le reste de ce relevé ne mesure pas les paris posés.** Les sélections "
            "comptées ailleurs valent qu'elles aient été jouées ou non — elles répondent "
            "à « ce que vaut l'analyse », pas à « ce que valent mes paris ».",
        ]
    else:
        out += [
            "",
            f"**Taux des paris posés** : {found.stats.overall.won} gagné(s), "
            f"{found.stats.overall.lost} perdu(s) — {found.stats.overall.settled} tranché(s), "
            f"{found.stats.overall.rate_label}, intervalle "
            f"{found.stats.overall.interval_label or '—'}.",
        ]
        out += _card(found, BETS_BLOCK, "Par palier", found.stats.by_tier)
        out += _card(found, BETS_BLOCK, "Par sport", found.stats.by_sport)
        out += _card(found, BETS_BLOCK, "Coupons", found.coupon_rates)
        if found.coupon_rates:
            out += [
                "",
                "Les types de coupons sont séparés : un combiné tombe dès qu'une jambe "
                "cède, il ne se compare pas à un pari simple. Aucune cote d'ensemble "
                "n'est calculée.",
            ]

    out += [
        "",
        "---",
        "",
        "Taux = gagnés / (gagnés + perdus). Les paris annulés et ceux en attente sont "
        "exclus du dénominateur.",
    ]
    return "\n".join(out).rstrip() + "\n"
