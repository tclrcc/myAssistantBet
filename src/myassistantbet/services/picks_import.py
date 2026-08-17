"""Import des picks depuis le tableau de selections rendu par Claude.

Le prompt demande un tableau Markdown (section C). Le retaper ligne par ligne
dans le formulaire des picks est la derniere corvee du parcours : ce module le
lit et propose un pre-remplissage.

Il ne fait aucun calcul financier — la mise reste absente de l'import, comme
elle est absente des agregats. Il n'ecrit rien non plus : il rend une
proposition que l'utilisateur valide, corrige ou rejette ligne par ligne.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .combos import PRICE_GAP, ParsedCombo, read_combos
from .confidence import (
    OPEN_ABSENT,
    OPEN_EMPTY,
    OPEN_MALFORMED,
    OPEN_READ,
    OVERRIDE_CAUSES,
    Claim,
    Opened,
    read_blocks,
    read_opened,
)
from .grid import GridRow, anchor, build_view
from .history import (
    ANGLES,
    PickableEvent,
    PromptBlocks,
    list_picks,
    pickable_events,
    prompt_headers,
)
from .history import tiers as load_tiers

#: Une ligne de tableau Markdown : `| a | b | c |`.
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")

#: La ligne de separation d'un tableau Markdown : `|---|:--:|`.
SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")

#: Tabulations minimales pour tenir une ligne pour une ligne de tableau copiee.
#: Trois colonnes : de la prose n'en contient pratiquement jamais autant.
MIN_TABS = 2

#: Entetes acceptes pour chaque champ, normalises. Claude suit le gabarit du
#: prompt, mais un synonyme ne doit pas faire echouer tout l'import.
HEADERS: dict[str, tuple[str, ...]] = {
    "match": ("match", "rencontre", "affiche"),
    "market": ("marche", "marché", "market"),
    "selection": ("selection", "sélection", "pick", "pari"),
    "price": ("cote", "odds", "cotes"),
    "tier": ("palier", "tier", "bande"),
    "confidence": ("conf 5", "conf", "confiance", "conf/5", "confiance 5"),
    # Le « pourquoi ». « Angle » n'est **pas** un alias de `angle` : c'est le
    # nom de la colonne de prose qui decrit la selection en une ligne, et la
    # confondre avec la nature de l'angle ferait entrer une phrase entiere dans
    # un champ a deux valeurs.
    "angle": ("type", "nature"),
    "source": ("source", "niveau de source", "niveau source", "src"),
}


@dataclass
class ParsedPick:
    """Une ligne du tableau, telle qu'elle sera proposee au formulaire."""

    index: int
    match_text: str = ""
    event_id: int | None = None
    event_label: str = ""
    market: str = ""
    selection: str = ""
    price: str = ""
    #: D'ou vient cette cote — lu sur la mention « (ref.) » que le prompt impose.
    price_source: str = ""
    tier: str = ""
    tier_text: str = ""
    confidence: str = ""
    #: Le « pourquoi » : la nature de l'angle, et le niveau de la source qui
    #: porte le fait principal. Vides quand le rendu ne les a pas donnes — ils
    #: ne font pas partie de `ready`, une selection restant enregistrable sans.
    angle: str = ""
    source: str = ""
    #: Un autre pick de la session porte deja ce match — dans le tableau colle,
    #: ou en base. La ligne reste proposee, mais elle reclame sa justification
    #: d'independance : `add_pick` la refuse sans.
    same_event: bool = False
    independence: str = ""
    #: Le match a **deja commence** au moment de l'import. La ligne reste
    #: proposee — la decision est peut-etre anterieure, seule la saisie est
    #: tardive — mais decochee tant que son motif manque : `add_pick` la
    #: refuserait, et une ligne qui echoue au milieu de vingt se remarque moins
    #: qu'une case qu'on doit cocher. Meme traitement que l'independance.
    started: bool = False
    late_reason: str = ""
    #: Le bloc structure qui porte les faits declares. Lu dans le **meme**
    #: copier-coller que le tableau : demander un second geste ferait perdre la
    #: colonne le jour ou on l'oublie, et c'est la seule qui rende le cran
    #: calculable. Vide quand le rendu n'en portait pas — la ligne reste
    #: importable, le cran restera simplement inconnu.
    claim: Claim | None = None
    #: L'analyse declare avoir **ouvert un dossier** sur ce match. Faux par
    #: defaut, et c'est tout le chantier : ce qui n'est pas demontre ouvert est
    #: une lecture des blocs, quel que soit le niveau de source annonce.
    opened: bool = False
    #: **Pourquoi** cette ligne part en lecture, quand elle y part. Vide sur une
    #: ligne dont le dossier est ouvert.
    #:
    #: Le drapeau seul ne suffit pas : « ce match n'est pas dans la liste » et
    #: « la liste n'a pas ete collee » donnent tous deux `opened = False` et ne
    #: sont pas la meme observation. Sans la cause, les deux alimentent le meme
    #: taux et une panne de transmission se lit comme un resultat.
    override_cause: str = ""
    #: Une selection identique existe deja dans la session, ou plus haut dans
    #: le meme tableau. Elle reste proposee — c'est peut-etre voulu — mais
    #: decochee : coller deux fois le meme rendu ne doit pas doubler l'historique.
    duplicate: bool = False

    @property
    def override_label(self) -> str:
        """Le libelle de la cause, resolu ici et **jamais recopie cote gabarit**.

        Meme regle que `Pick.late_label` : deux ecritures du meme vocabulaire
        divergeraient au premier ajout, et un libelle recopie dans un template
        ne casse rien le jour ou la cle change — il disparait.
        """
        return OVERRIDE_CAUSES.get(self.override_cause, "")

    @property
    def ready(self) -> bool:
        """Vrai si la ligne peut etre enregistree sans correction humaine."""
        return bool(self.market and self.selection and self.tier)

    @property
    def keep(self) -> bool:
        """Vrai si la ligne est cochee par defaut dans le formulaire.

        Une seconde selection sur un match deja pris reste **decochee** tant que
        sa justification manque : `add_pick` la refuserait, et une ligne qui
        echoue a l'import se remarque moins qu'une case qu'on doit cocher.
        """
        return (
            self.ready
            and not self.duplicate
            and not (self.same_event and not self.independence)
            and not (self.started and not self.late_reason)
        )

    @property
    def problems(self) -> list[str]:
        issues = []
        if not self.market:
            issues.append("marché absent")
        if not self.selection:
            issues.append("sélection absente")
        if not self.tier:
            issues.append(f"palier non reconnu ({self.tier_text or 'vide'})")
        if self.event_id is None and self.match_text:
            issues.append("match non rapproché")
        if self.duplicate:
            issues.append("déjà présente")
        if self.same_event and not self.independence:
            issues.append("2e sélection sur ce match : dire l'angle indépendant")
        if self.started and not self.late_reason:
            issues.append("match déjà commencé : saisie différée, ou live assumé ?")
        # Ni un refus ni une case a cocher : la ligne s'importe telle quelle,
        # ecrasee. Le dire ici evite qu'un `lecture` en base passe pour une
        # declaration franche du modele.
        if not self.opened:
            # La cause accompagne le motif : elle dit si la ligne se repare en
            # recollant, ou si elle decrit vraiment ce que l'analyse a fait.
            motif = self.override_label
            issues.append("enregistrée en lecture, cran 1" + (f" — {motif}" if motif else ""))
        return issues


@dataclass(frozen=True)
class AttachedCombo:
    """Un combine lu, ses jambes rapprochees des lignes du tableau.

    `rows` porte les **indices de lignes** et non des identifiants de selection :
    rien n'est encore ecrit a l'apercu, donc les picks n'existent pas. C'est
    l'import qui les traduira en identifiants une fois les lignes creees.
    """

    combo: ParsedCombo
    rows: list[int]
    prompt_id: int
    #: La cote **recalculee depuis les lignes du tableau**, jamais celle que le
    #: modele a ecrite : le produit d'une reponse est une affirmation, celui-ci
    #: est une consequence des prix recopies. `None` des qu'une jambe n'a pas de
    #: prix lisible — un produit partiel serait plus bas que le vrai sans que
    #: rien ne le dise.
    computed_price: float | None = None

    @property
    def price_gap(self) -> float | None:
        declared = self.combo.declared_price
        if declared is None or not self.computed_price:
            return None
        return (declared - self.computed_price) / self.computed_price

    @property
    def price_mismatch(self) -> bool:
        gap = self.price_gap
        return gap is not None and abs(gap) > PRICE_GAP


@dataclass
class ImportPreview:
    """Proposition d'import. Rien n'est ecrit avant validation."""

    picks: list[ParsedPick] = field(default_factory=list)
    #: Les combines lus et rattaches. Vides, ils ne coutent rien ; rejetes, ils
    #: passent par `notes` et jamais par `ignored` — un combine illisible ne doit
    #: pas couter la possibilite d'importer le tableau.
    combos: list[AttachedCombo] = field(default_factory=list)
    #: Ce qui empeche de lire le collage. **Non vide, le tableau ne s'affiche
    #: pas du tout** : c'est la porte du gabarit, et y verser une remarque ferait
    #: disparaitre l'import entier pour un detail.
    ignored: list[str] = field(default_factory=list)
    #: Ce qui accompagne un apercu **lisible** : blocs rejetes, appariement
    #: refuse, dossiers non ouverts. La distinction n'est pas cosmetique — un
    #: bloc de confiance illisible ne doit pas couter la possibilite d'importer
    #: le tableau, il ne coute que les crans.
    notes: list[str] = field(default_factory=list)
    #: Les dossiers que le rendu declare avoir ouverts. Conserve entier — et pas
    #: seulement applique ligne par ligne — parce que c'est la **liste** qui se
    #: compare a l'ordre de passage propose par l'application, y compris pour
    #: les dossiers qui n'ont produit aucune selection.
    opened: Opened = field(default_factory=Opened)

    @property
    def count(self) -> int:
        return len(self.picks)

    @property
    def ready_count(self) -> int:
        return sum(1 for pick in self.picks if pick.ready)

    @property
    def duplicate_count(self) -> int:
        return sum(1 for pick in self.picks if pick.duplicate)

    @property
    def same_event_count(self) -> int:
        """Lignes portant sur un match qu'une autre selection porte deja."""
        return sum(1 for pick in self.picks if pick.same_event)

    @property
    def claims_attached(self) -> int:
        """Lignes auxquelles un bloc de confiance a ete apparie.

        **Le compte porte sur la presence du bloc, jamais sur son contenu.** Un
        bloc `"faits": []` est une reponse normale — le gabarit l'impose meme,
        avec `source_level: lecture` — donc compter les faits confondrait le cas
        ordinaire avec le defaut. C'est la meme faute que partout ailleurs : la
        meme sortie pour un succes et pour un manque.
        """
        return sum(1 for pick in self.picks if pick.claim is not None)

    @property
    def readout(self) -> str:
        """Ce que le collage a vraiment porte, avant que quoi que ce soit soit
        enregistre.

        **C'est le seul moment ou l'information est encore recuperable.** Un
        collage ligne par ligne depuis le tableau de la section C laisse les
        blocs derriere lui ; le dire une semaine plus tard sur la page de
        statistiques signale un defaut qui se reparait en dix secondes en
        recollant.
        """
        # Les quatre etats sont nommes un par un, et l'inconnu se dit comme tel :
        # un etat neuf qui retomberait dans « absente » reproduirait exactement
        # le defaut que ce releve existe pour rendre visible.
        etats = {
            OPEN_READ: f"ligne dossiers_ouverts lue ({len(self.opened.marks)} dossier(s))",
            OPEN_EMPTY: "ligne dossiers_ouverts lue, aucun dossier déclaré",
            OPEN_MALFORMED: "ligne dossiers_ouverts illisible",
            OPEN_ABSENT: "ligne dossiers_ouverts absente",
        }
        return (
            f"{self.count} sélection(s) détectée(s) · "
            f"{self.claims_attached} bloc(s) de confiance apparié(s) · "
            f"{len(self.combos)} combiné(s) rattaché(s) · "
            + etats.get(self.opened.state, f"état inconnu ({self.opened.state})")
        )

    @property
    def overlap(self) -> str:
        """Le recouvrement entre les combines rattaches, nomme et jamais tu.

        **Il est impose par le vivier et non par la redaction** : sur la moitie
        des sessions mesurees, un court et un long disjoints sont
        structurellement impossibles. Ce qui est interdit n'est pas qu'ils se
        recouvrent, c'est que le recouvrement passe inapercu.
        """
        paires = []
        for index, gauche in enumerate(self.combos):
            for droite in self.combos[index + 1 :]:
                a, b = set(gauche.rows), set(droite.rows)
                union = a | b
                part = len(a & b) / len(union) if union else 0.0
                if part:
                    paires.append(
                        f"{gauche.combo.kind} et {droite.combo.kind} partagent "
                        f"{len(a & b)} jambe(s), J = {part:.2f}"
                    )
        return " · ".join(paires)

    @property
    def complete(self) -> bool:
        """Le collage porte tout ce que le gabarit demande.

        Faux, le releve se lit en avertissement : ce qui manque ne se rattrape
        pas apres l'import.
        """
        return bool(self.picks) and self.claims_attached == self.count and self.opened.declared


def _signature(event_id: int | None, market: str, selection: str) -> tuple[Any, ...]:
    """Ce qui fait qu'une selection est « la meme » qu'une autre.

    Le match compte : la meme cote sur deux affiches differentes sont deux
    paris. Les accents et la casse ne comptent pas — « Plíšková » recopie a la
    main ne doit pas passer pour une seconde selection.
    """
    return (event_id, _fold(market), _fold(selection))


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def _normalize_header(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s/]", " ", text.strip().lower())
    return " ".join(cleaned.split())


def _cells(line: str) -> list[str] | None:
    """Cellules d'une ligne de tableau, quel que soit son format de copie.

    Le Markdown a barres verticales est ce que Claude *ecrit* ; ce que l'on
    *copie* depuis son interface est un tableau tabule, les barres ayant ete
    consommees par le rendu. Les deux doivent passer, sans quoi la fonction
    echoue precisement sur le geste qu'elle est censee servir.
    """
    match = TABLE_ROW.match(line)
    if match is not None:
        return [cell.strip() for cell in match.group(1).split("|")]
    if line.count("\t") >= MIN_TABS:
        return [cell.strip() for cell in line.split("\t")]
    return None


def _is_separator(cells: list[str]) -> bool:
    filled = [cell.replace(" ", "") for cell in cells if cell]
    return bool(filled) and all(SEPARATOR_CELL.match(cell) for cell in filled)


def _map_columns(cells: list[str]) -> dict[str, int] | None:
    """Associe chaque champ a son indice de colonne, d'apres l'entete."""
    found: dict[str, int] = {}
    for index, cell in enumerate(cells):
        header = _normalize_header(cell)
        for field_name, aliases in HEADERS.items():
            if field_name in found:
                continue
            if any(header == _normalize_header(alias) for alias in aliases):
                found[field_name] = index
    # Sans marche ni selection, ce n'est pas le tableau des selections.
    return found if {"market", "selection"} <= set(found) else None


def _at(cells: list[str], position: int | None) -> str:
    """Cellule a cet indice, ou chaine vide si la colonne manque sur la ligne."""
    if position is None or position >= len(cells):
        return ""
    return cells[position]


def _price(text: str) -> str:
    """Extrait la cote d'une cellule. `1,55` ou `@1.55` ou `1.55 (Betclic)`."""
    match = re.search(r"\d+[.,]\d+|\d+", text or "")
    return match.group(0).replace(",", ".") if match else ""


#: La mention que le prompt impose dans la colonne Cote quand le prix ne vient
#: pas du bookmaker principal. Elle etait ecrite, lue, puis jetee : c'est
#: pourtant elle qui dit qu'un palier repose sur un prix qu'on n'obtiendra pas.
REFERENCE_MARK = re.compile(r"\(\s*ref\.?\s*\)", re.IGNORECASE)


def _price_source(text: str) -> str:
    """`reference` si la cellule porte « (ref.) », `betclic` sinon.

    Le prompt exige la mention des la premiere ligne du preambule, et la liste
    des prix a relever, sous le tableau C, la reprend. Sans elle, une cote est
    celle du bookmaker principal — c'est la regle du bloc, pas une supposition.
    Une cellule vide ne dit rien : elle ne porte pas de cote du tout.
    """
    if not (text or "").strip():
        return ""
    return "reference" if REFERENCE_MARK.search(text) else "betclic"


def _confidence(text: str) -> str:
    """Extrait la note de confiance. `4`, `4/5` et `⭐⭐⭐⭐` donnent tous 4."""
    match = re.search(r"[1-5]", text or "")
    if match:
        return match.group(0)
    stars = (text or "").count("⭐") or (text or "").count("★")
    return str(stars) if 1 <= stars <= 5 else ""


def _angle(text: str) -> str:
    """Reconnait `issue` ou `manière` dans une cellule.

    Cherche le mot **dans** la cellule plutot que de la comparer entiere : le
    rendu ecrit volontiers « manière (rythme) », et exiger le mot seul perdrait
    la colonne sur une precision utile a la lecture.
    """
    folded = _fold(text)
    for key in ANGLES:
        if key in folded:
            return key
    return ""


def _source(text: str) -> str:
    """Reconnait le niveau de source. `2`, `niveau 2`, `2 (L'Équipe)`, `lecture`.

    Le chiffre prime sur le mot : une cellule qui porte les deux — « 2, sinon
    lecture » — decrit une source, et c'est elle qu'on veut mesurer.
    """
    folded = _fold(text)
    if not folded:
        return ""
    match = re.search(r"[1-4]", folded)
    if match:
        return match.group(0)
    return "lecture" if "lecture" in folded else ""


def _resolve_tier(text: str, tiers: list[dict[str, str]]) -> str:
    """Retrouve la cle du palier depuis « 🟢 SAFE », « SAFE » ou « safe »."""
    raw = (text or "").strip()
    if not raw:
        return ""
    normalized = _normalize_header(raw)
    for tier in tiers:
        if tier.get("emoji") and tier["emoji"] in raw:
            return tier["key"]
    for tier in tiers:
        name = _normalize_header(tier.get("name") or tier["label"])
        if name and normalized in {name, tier["key"].replace("_", " ")}:
            return tier["key"]
    # Repli : le nom du palier apparait dans la cellule. Le plus long gagne,
    # sans quoi « GIGA FUN » serait reconnu comme « FUN ».
    candidates = sorted(
        tiers, key=lambda t: len(_normalize_header(t.get("name") or t["label"])), reverse=True
    )
    for tier in candidates:
        name = _normalize_header(tier.get("name") or tier["label"])
        if name and name in normalized:
            return tier["key"]
    return ""


def parse_table(
    raw: str,
    rows: list[GridRow],
    tiers: list[dict[str, str]],
    known: set[tuple[Any, ...]] | None = None,
    nearby: list[PickableEvent] | None = None,
    taken: set[int] | None = None,
    headers: list[dict[str, str]] | None = None,
) -> ImportPreview:
    """Lit le tableau de selections. Ne rapproche jamais un match au hasard.

    Tolere les colonnes en plus, en moins et dans le desordre : seul l'entete
    fait foi. Une ligne qui n'appartient pas au tableau est ignoree en silence
    (c'est de la prose), une ligne du tableau qui pose probleme est conservee
    avec son motif pour que l'utilisateur tranche.

    La shortlist est essayee **avant** le voisinage : c'est elle qui a ete
    analysee, et l'elargissement ne doit pas rendre ambigu un match qu'elle
    designait seule. Le voisinage ne sert qu'a ce qu'elle ne contient pas.
    """
    preview = ImportPreview()
    columns: dict[str, int] | None = None
    index = 0
    # Les doublons se cherchent contre la session **et** contre le tableau
    # lui-meme : un rendu recopie deux fois se repete a l'interieur.
    seen = set(known or ())
    # Les matchs deja pris, en base **et** plus haut dans le tableau : deux
    # lignes du meme rendu sur une meme affiche sont le cas que le prompt
    # encadre, et le second des deux doit se justifier.
    events = set(taken or ())

    for line in (raw or "").splitlines():
        cells = _cells(line)
        if cells is None:
            continue
        if _is_separator(cells):
            continue

        if columns is None:
            columns = _map_columns(cells)
            continue

        values = {name: _at(cells, columns.get(name)) for name in HEADERS}
        if not values["market"] and not values["selection"]:
            continue

        index += 1
        found = None
        if values["match"]:
            found = anchor(values["match"], rows) or anchor(values["match"], nearby or [])
        event_id = found.event_id if found else None
        signature = _signature(event_id, values["market"], values["selection"])
        preview.picks.append(
            ParsedPick(
                index=index,
                match_text=values["match"],
                event_id=event_id,
                event_label=found.affiche if found else "",
                market=values["market"],
                selection=values["selection"],
                price=_price(values["price"]),
                price_source=_price_source(values["price"]),
                tier=_resolve_tier(values["tier"], tiers),
                tier_text=values["tier"],
                confidence=_confidence(values["confidence"]),
                angle=_angle(values["angle"]),
                source=_source(values["source"]),
                duplicate=signature in seen,
                same_event=event_id is not None and event_id in events,
                # Les deux types de match rapproches portent ce drapeau : la
                # ligne se decoche quelle que soit l'origine du rapprochement.
                started=bool(found and found.started),
            )
        )
        seen.add(signature)
        if event_id is not None:
            events.add(event_id)

    if columns is None:
        preview.ignored.append(
            "Aucun tableau de sélections reconnu : colle la section C, "
            "en-tête compris (« Match | Marché | Sélection | … »)."
        )
    valide = _attach_claims(preview, raw, headers)
    _apply_research(preview, raw, headers or [], valide)
    _attach_combos(preview, raw, valide)
    return preview


def _attach_combos(preview: ImportPreview, raw: str, valide: PromptBlocks | None) -> None:
    """Rattache les blocs ```combo aux lignes du tableau, par leur repere.

    **Le prompt est celui qui a valide l'appariement des blocs de confiance**, et
    pas un second choisi ici : deux resolutions paralleles finiraient par
    designer deux matchs sous le meme repere. Sans lui, les combines sont lus et
    dits, mais aucun n'est rattache — un combine porte l'identifiant de son
    prompt, et l'inventer serait pire que ne pas l'enregistrer.

    La resolution passe par la ligne du tableau et non par l'evenement : c'est la
    selection qui est une jambe, et un match peut en porter deux. Un repere qui
    designe deux lignes est donc **refuse** plutot que tranche au hasard.
    """
    reading = read_combos(raw)
    preview.notes.extend(reading.rejected)
    if not reading.combos:
        return
    if valide is None:
        preview.notes.append(
            f"{len(reading.combos)} combiné(s) lu(s), aucun rattaché : le prompt d'origine "
            "n'a pas pu être identifié, et un combiné porte l'identifiant de son prompt."
        )
        return

    # Le repere de chaque ligne vient de son bloc de confiance, deja apparie.
    # `rows` porte le **numero de ligne du formulaire** (`ParsedPick.index`), le
    # seul identifiant qui traverse le POST : c'est lui qui permettra de
    # retrouver la selection creee.
    par_repere: dict[str, list[ParsedPick]] = {}
    for pick in preview.picks:
        if pick.claim and pick.claim.match:
            par_repere.setdefault(pick.claim.match.upper(), []).append(pick)

    for combo in reading.combos:
        jambes: list[ParsedPick] = []
        manquants: list[str] = []
        for mark in combo.marks:
            trouve = par_repere.get(mark, [])
            if len(trouve) == 1:
                jambes.append(trouve[0])
            else:
                manquants.append(mark)
        if manquants:
            cause = "introuvable(s) ou ambigu(s) dans le tableau"
            preview.notes.append(f"{combo.label} non rattaché : {', '.join(manquants)} {cause}.")
            continue
        attache = AttachedCombo(
            combo=combo,
            rows=[pick.index for pick in jambes],
            prompt_id=valide.prompt_id,
            computed_price=_product([pick.price for pick in jambes]),
        )
        preview.combos.append(attache)
        if attache.price_mismatch:
            preview.notes.append(
                f"{combo.label} : cote écrite {combo.declared_price:.2f}, recalculée depuis "
                f"les jambes {attache.computed_price:.2f}. C'est la recalculée qui est "
                "enregistrée."
            )


def _product(prices: list[str]) -> float | None:
    """Le produit de cotes recopiees, ou rien si l'une manque."""
    valeurs = []
    for raw in prices:
        try:
            valeurs.append(float(str(raw).replace(",", ".")))
        except ValueError:
            return None
    return math.prod(valeurs) if valeurs else None


def _apply_research(
    preview: ImportPreview,
    raw: str,
    headers: list[PromptBlocks],
    valide: PromptBlocks | None,
) -> None:
    """Marque les selections portant sur un dossier que l'analyse n'a pas ouvert.

    **Le defaut est `lecture`, jamais « ouvert », et toute la valeur du chantier
    tient la.** Mesure : 0 selection en `lecture` sur 149, quand le budget de
    recherche vaut sept dossiers pour des lots de 57 a 72 matchs. Le niveau de
    source est donc gonfle, et la table qui le regroupe ne mesure rien.

    Liste absente, illisible, ou portant un repere qui ne se resout contre aucun
    prompt : **tout le lot** passe en lecture. Meme raisonnement que la somme de
    controle de l'appariement — un `lecture` de trop se voit et se corrige, un
    niveau de source gonfle qui passe pour verifie ne se voit pas.
    """
    opened = read_opened(raw)
    preview.opened = opened
    # Le meme prompt que les blocs, quand ils en ont designe un. Sans blocs, le
    # premier qui porte **tous** les reperes declares : deux resolutions
    # paralleles finiraient par designer deux matchs sous le meme numero.
    mapping = valide or next(
        (candidate for candidate in headers if opened.marks <= set(candidate.marks)), None
    )
    if mapping is not None and not opened.marks <= set(mapping.marks):
        mapping = None
    affiches: set[str] = set()
    if mapping is not None:
        affiches = {_fold(_affiche_of(mapping.marks[mark])) for mark in opened.marks}
    if opened.note:
        preview.notes.append(opened.note)
    elif mapping is None:
        preview.notes.append(
            "Les repères de « dossiers_ouverts » ne se résolvent contre aucun prompt de "
            "cette session : toutes les sélections sont enregistrées en lecture, cran 1. "
            "Le rendu porte bien la ligne — c'est le rattachement qui a échoué, et il se "
            "reprend en recollant le prompt qui l'a produite."
        )
    # La cause est calculee **une fois pour le lot** : elle depend de l'etat de
    # la ligne et du rattachement, pas de la selection. Seul `hors_dossiers` se
    # decide ligne par ligne, et c'est le seul cas ou la liste a vraiment servi.
    cause = opened.cause(resolved=bool(affiches))
    for pick in preview.picks:
        pick.opened = bool(affiches) and _fold(pick.match_text) in affiches
        pick.override_cause = "" if pick.opened else cause


def _attach_claims(
    preview: ImportPreview, raw: str, headers: list[PromptBlocks] | None = None
) -> PromptBlocks | None:
    """Rattache les blocs de confiance aux lignes du tableau, **par l'ordre**.

    Rend le prompt qui a valide, pour que la liste des dossiers ouverts se
    resolve contre **le meme** — deux resolutions paralleles finiraient par
    designer deux matchs differents sous le meme repere.

    Le gabarit demande un bloc par ligne, dans l'ordre du tableau. Le champ
    `match` porte le numero de bloc du prompt (`M8`), qui **change d'une
    generation a l'autre** et ne peut donc pas servir de cle de jointure — mais
    il est coherent **a l'interieur d'un meme rendu**, ce qui en fait une somme
    de controle.

    **Le compte seul ne suffisait pas.** Nombre egal et ordre different donnait
    des crans tous decales d'un rang, en silence — et un cran faux ne se voit
    pas, la ou un cran inconnu se voit. Meme raisonnement que la garde
    d'anteriorite : ce qui ne peut pas se relire ne doit pas pouvoir s'ecrire.

    Le controle se fait donc contre les **en-tetes de blocs du prompt archive**,
    qui portent `### M8 · sport · competition · affiche · heure`. Un rendu vient
    forcement de l'un d'eux, et c'est le prompt entier qui doit valider **toutes**
    les paires : une seule qui ne correspond pas, et rien n'est rattache.
    """
    reading = read_blocks(raw)
    preview.notes.extend(reading.rejected)
    if not reading.claims:
        # **La seule branche muette du module, et c'est celle qui a servi.** Un
        # bloc pour trois lignes avertissait ; zero bloc ne disait rien, si bien
        # qu'un collage ligne par ligne depuis le tableau de la section C passait
        # sans un mot — les blocs etaient produits, jamais transmis, et rien a
        # l'ecran ne le disait. Trois selections sont entrees ainsi.
        #
        # Le message ne dit pas « complete les blocs » comme celui du compte : ce
        # n'est pas le rendu qui manque de blocs, c'est le collage qui les a
        # laisses derriere lui.
        if preview.picks:
            preview.notes.append(
                f"Aucun bloc de confiance dans ce collage, pour {len(preview.picks)} "
                "ligne(s) : le cran ne sera pas calculé. Ils suivent le tableau dans le "
                "rendu — recolle la réponse entière plutôt que les seules lignes."
            )
        return None
    if len(reading.claims) != len(preview.picks):
        preview.notes.append(
            f"{len(reading.claims)} bloc(s) de confiance pour {len(preview.picks)} "
            "ligne(s) : aucun n'est rattaché, le cran resterait faux sans qu'on le voie. "
            "Complète les blocs manquants et recolle."
        )
        return
    valide = _select(preview.picks, reading.claims, headers or [])
    if valide is None:
        preview.notes.append(
            "Les repères de match des blocs (M1, M2…) ne correspondent à aucun prompt de "
            "cette session, ligne par ligne. Rien n'est rattaché — un cran décalé serait "
            "faux sans se voir. "
            + (_mismatch(preview.picks, reading.claims, headers or []) or "")
            + "Corrige l'affiche dans le tableau, telle qu'elle est écrite en tête du "
            "bloc, et recolle."
        )
        return None
    for pick, claim in zip(preview.picks, reading.claims, strict=True):
        pick.claim = claim
        # Le bloc fait foi sur les deux colonnes qu'il porte aussi : c'est la
        # meme declaration sous une forme relisable, et en garder deux
        # ecritures les aurait fait diverger au premier rendu discordant.
        pick.source = claim.source_level or pick.source
        if claim.declared is not None:
            pick.confidence = str(claim.declared)
    return valide


def _mismatch(picks: list[ParsedPick], claims: list[Claim], headers: list[PromptBlocks]) -> str:
    """La premiere paire qui bloque, nommee — sinon l'echec est **terminal**.

    « Ça se rattrape en recollant » n'est un chemin de reprise que si le message
    dit **quoi** corriger : recoller le meme texte echoue a l'identique, et il ne
    reste rien a faire. La paire est donc sortie avec son indice, la chaine
    attendue et la chaine recue, **apres normalisation** — c'est sous cette forme
    que la comparaison a eu lieu, et deux caracteres a corriger se voient alors.

    Le prompt retenu pour le **message** est celui qui s'en approche le plus.
    Cela n'affaiblit pas la garde : la validation, elle, reste en tout ou rien —
    ce choix ne sert qu'a designer une paire, jamais a en accepter une.
    """
    if not headers:
        return "Aucun prompt n'est archivé pour cette session. "
    proche = min(
        headers,
        key=lambda mapping: sum(
            not _pairs(pick, claim, mapping) for pick, claim in zip(picks, claims, strict=True)
        ),
    )
    for index, (pick, claim) in enumerate(zip(picks, claims, strict=True), start=1):
        if not _pairs(pick, claim, proche):
            attendu = _fold(_affiche_of(proche.marks.get(claim.match, ""))) or "—"
            recu = _fold(pick.match_text) or "—"
            return (
                f"Première paire en cause : ligne {index}, bloc {claim.match or '?'} — "
                f"attendu « {attendu} », reçu « {recu} ». "
            )
    return ""


def _select(
    picks: list[ParsedPick], claims: list[Claim], headers: list[PromptBlocks]
) -> PromptBlocks | None:
    """Le prompt de la session qui valide toutes les paires a la fois, ou rien.

    **Une paire qui ne passe pas fait tomber le lot entier**, jamais elle seule :
    la retirer en laissant passer les autres serait le « meilleur des prompts
    paire par paire » qu'on a justement ecarte, et l'appariement des lignes
    restantes ne serait plus demontre par rien.

    Le rapprochement se fait sur le **texte de la colonne Match**, pas sur
    l'evenement resolu : c'est l'appariement du modele qu'on verifie, et il doit
    l'etre meme sur une ligne dont le rapprochement de nom a echoue.

    Un prompt valide l'ensemble ou ne le valide pas. Retenir le meilleur des
    prompts paire par paire reviendrait a piocher la lecture qui arrange, ce qui
    ne demontrerait plus rien.
    """
    return next(
        (
            mapping
            for mapping in headers
            if all(_pairs(pick, claim, mapping) for pick, claim in zip(picks, claims, strict=True))
        ),
        None,
    )


#: L'en-tete d'un bloc de prompt : `sport · competition · affiche · heure`. Le
#: repere `M8` a deja ete retire par la lecture, il reste quatre champs.
HEADER_FIELDS = 4
HEADER_AFFICHE = 2


def _affiche_of(header: str) -> str:
    """L'affiche seule, extraite de l'en-tete d'un bloc de prompt.

    Une forme inattendue rend `""`, ce qui fait echouer la paire — donc tomber le
    lot, `_verified` etant en tout ou rien. Cette fonction est une **somme de
    controle**, et une somme de controle qui s'accommode de ce qu'elle ne
    reconnait pas ne controle plus rien.
    """
    parts = [part.strip() for part in header.split(" · ")]
    return parts[HEADER_AFFICHE] if len(parts) >= HEADER_FIELDS else ""


def _pairs(pick: ParsedPick, claim: Claim, blocks: PromptBlocks) -> bool:
    """Ce bloc designe-t-il bien la ligne avec laquelle il a ete apparie.

    **Normalisation deterministe, puis egalite stricte.** C'est le seul reglage
    qui tienne les deux bouts. Une egalite stricte sur le texte brut ferait
    tomber tout un lot sur un tiret long rendu en tiret court ou sur un accent
    perdu ; une similarite floue laisserait passer l'appariement decale qu'on
    cherche justement a attraper. `_fold` absorbe la **typographie** — casse,
    accents, tirets, espaces, `Győri ETO FC` contre `Gyori ETO FC` — et rien
    d'autre.

    **Ce qu'une orthographe reellement differente coute est donc les crans du
    lot entier**, et c'est le bon sens du compromis : la perte est visible, elle
    se rattrape en recollant, et elle ne s'ecrit jamais en base. Un cran decale
    ne se voit pas.

    La comparaison porte sur l'**affiche seule** et non sur l'en-tete complet :
    chercher la cellule quelque part dans la ligne est un test de contenance,
    donc flou par construction — « Lyon » se serait trouve dans n'importe quel
    en-tete portant Lyon, y compris celui de l'autre match.

    Les deux valeurs vides ne se valident pas l'une l'autre : une colonne Match
    absente et un repere inconnu rendraient le controle vrai sans rien avoir
    verifie, ce qui est exactement le silence qu'il existe pour supprimer.
    """
    cell = _fold(pick.match_text)
    header = _fold(_affiche_of(blocks.marks.get(claim.match, "")))
    return bool(cell) and bool(header) and cell == header


def build_preview(
    session_id: int,
    raw: str,
    settings: Settings | None = None,
) -> ImportPreview:
    """Proposition d'import pour une session, matchs rapproches par leur nom."""
    settings = settings or get_settings()
    rows = build_view(session_id, settings).rows
    known = {
        _signature(pick.event_id, pick.market, pick.selection)
        for pick in list_picks(session_id, settings)
    }
    # Le voisinage rattrape ce que la shortlist ne contient pas : un match qui a
    # commence a quitte le board et n'a jamais pu y etre coche.
    nearby = [event for event in pickable_events(session_id, settings) if not event.in_session]
    taken = {pick.event_id for pick in list_picks(session_id, settings) if pick.event_id}
    # Les en-tetes des prompts archives : c'est contre eux que l'appariement des
    # blocs de confiance se verifie. L'information dormait deja en base — les
    # corps sont stockes depuis toujours.
    return parse_table(
        raw,
        rows,
        load_tiers(settings),
        known,
        nearby,
        taken,
        prompt_headers(session_id, settings),
    )
