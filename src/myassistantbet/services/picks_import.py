"""Import des picks depuis le tableau de selections rendu par Claude.

Le prompt demande un tableau Markdown (section C). Le retaper ligne par ligne
dans le formulaire des picks est la derniere corvee du parcours : ce module le
lit et propose un pre-remplissage.

Il ne fait aucun calcul financier — la mise reste absente de l'import, comme
elle est absente des agregats. Il n'ecrit rien non plus : il rend une
proposition que l'utilisateur valide, corrige ou rejette ligne par ligne.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from .confidence import Claim, read_blocks
from .grid import GridRow, anchor, build_view
from .history import ANGLES, PickableEvent, list_picks, pickable_events, prompt_headers
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
    #: Une selection identique existe deja dans la session, ou plus haut dans
    #: le meme tableau. Elle reste proposee — c'est peut-etre voulu — mais
    #: decochee : coller deux fois le meme rendu ne doit pas doubler l'historique.
    duplicate: bool = False

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
        return issues


@dataclass
class ImportPreview:
    """Proposition d'import. Rien n'est ecrit avant validation."""

    picks: list[ParsedPick] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

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
    _attach_claims(preview, raw, headers)
    return preview


def _attach_claims(
    preview: ImportPreview, raw: str, headers: list[dict[str, str]] | None = None
) -> None:
    """Rattache les blocs de confiance aux lignes du tableau, **par l'ordre**.

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
    preview.ignored.extend(reading.rejected)
    if not reading.claims:
        return
    if len(reading.claims) != len(preview.picks):
        preview.ignored.append(
            f"{len(reading.claims)} bloc(s) de confiance pour {len(preview.picks)} "
            "ligne(s) : aucun n'est rattaché, le cran resterait faux sans qu'on le voie. "
            "Complète les blocs manquants et recolle."
        )
        return
    if not _verified(preview.picks, reading.claims, headers or []):
        preview.ignored.append(
            "Les repères de match des blocs (M1, M2…) ne correspondent à aucun prompt de "
            "cette session, ligne par ligne. Rien n'est rattaché — un cran décalé serait "
            "faux sans se voir. "
            + (_mismatch(preview.picks, reading.claims, headers or []) or "")
            + "Corrige l'affiche dans le tableau, telle qu'elle est écrite en tête du "
            "bloc, et recolle."
        )
        return
    for pick, claim in zip(preview.picks, reading.claims, strict=True):
        pick.claim = claim
        # Le bloc fait foi sur les deux colonnes qu'il porte aussi : c'est la
        # meme declaration sous une forme relisable, et en garder deux
        # ecritures les aurait fait diverger au premier rendu discordant.
        pick.source = claim.source_level or pick.source
        if claim.declared is not None:
            pick.confidence = str(claim.declared)


def _mismatch(picks: list[ParsedPick], claims: list[Claim], headers: list[dict[str, str]]) -> str:
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
            attendu = _fold(_affiche_of(proche.get(claim.match, ""))) or "—"
            recu = _fold(pick.match_text) or "—"
            return (
                f"Première paire en cause : ligne {index}, bloc {claim.match or '?'} — "
                f"attendu « {attendu} », reçu « {recu} ». "
            )
    return ""


def _verified(picks: list[ParsedPick], claims: list[Claim], headers: list[dict[str, str]]) -> bool:
    """Vrai si **un** prompt de la session valide toutes les paires a la fois.

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
    if not headers:
        return False
    return any(
        all(_pairs(pick, claim, mapping) for pick, claim in zip(picks, claims, strict=True))
        for mapping in headers
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


def _pairs(pick: ParsedPick, claim: Claim, mapping: dict[str, str]) -> bool:
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
    header = _fold(_affiche_of(mapping.get(claim.match, "")))
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
