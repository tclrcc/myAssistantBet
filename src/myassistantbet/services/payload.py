"""Le bloc de donnees : des faits attribues, et rien d'autre.

Le prompt portait sa propre methode — 1 411 lignes de gabarit pour 26 lignes
factuelles, et **65,4 % du volume archive en cadre**. Ce qui decide de la sortie
vit desormais dans le `SKILL.md` ; ce module ne rend que des faits.

Le format est fixe par `references/payload-contrat.md`, et les regles qui ont
decide de sa forme par `SPEC-PAYLOAD.md`. Trois tiennent tout :

- **un objet JSON unique**, jamais une suite. Le scan du lecteur de blocs avance
  de `{` en `{`, et un objet qui se relit fait sauter tout ce qu'il contient : la
  racine avale ses matchs, et c'est elle qui protege les objets internes ;
- **des dates, jamais des ages.** Un age calcule au rendu est vrai a la seconde
  ou il s'ecrit et faux pour toujours ensuite. La fraicheur se derive a la
  lecture ;
- **`origine` sur la racine et sur chaque match**, sans quoi un objet-match
  recopie dans une reponse serait lu comme un bloc de confiance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from .attribution import TRANCHES, UNKNOWN_LEVEL, Fait
from .context import failure_causes
from .prompt import FRAMEWORK_VERSION
from .render import MERGED_MARKETS, RenderableEvent, market_label, price
from .session import context_density, renderable_events

#: L'emetteur, pose sur la racine **et sur chaque objet-match**.
#:
#: **Le discriminant qui empeche une panne silencieuse.** `confidence.is_claim`
#: reconnait un bloc de confiance a sa seule forme quand la cloture manque, et sa
#: liste de cles recoupe celles d'un objet-match. Sans ce champ, un objet recopie
#: dans une reponse serait lu comme une reclamation : echec au parse, divergence
#: entre le compte des blocs et celui des lignes, perte des crans du lot.
ORIGIN = "myassistantbet"

#: Les sections que la session reclame. **Des cles, pas une methode** — le
#: gabarit ne les decrit plus, mais `sections.survey` deduit ce qui etait attendu
#: en cherchant ses motifs dans le corps du prompt : sans gabarit, il conclurait
#: « rien n'etait demande », donc « rien a reclamer ». C'est le defaut que ce
#: module-la existe pour corriger, retourne contre lui.
SECTIONS = ("A", "B", "C", "C-bis", "D", "E", "F")

#: Les libelles du socle nomme, par cle du contrat. Tout le reste passe par
#: `attributs[]`.
#:
#: **La regle de promotion est stricte** : un libelle monte ici le jour ou il est
#: reference par une regle de decision, ou sert d'axe de calibration. Pas avant.
#: Sans elle, le socle absorbe le conteneur en six mois et chaque libelle ajoute
#: demande une modification de schema — le piege de `markets.py` et `render.py`.
TEAM_FIELDS = {"classement": "Classement", "forme_5": "Forme 5", "entraineur": "Entraineur"}

#: Les libelles rendus ailleurs que dans `attributs[]`, et qui n'y entrent donc
#: pas une seconde fois.
NAMED = frozenset(
    {*TEAM_FIELDS.values(), "Compos", "Absents", "Meteo", "Lieu", "Statut", "Tour", "Densite"}
)

#: Ce que `_pair` intercale entre les deux equipes. **Notre propre separateur**,
#: pas une convention devinee sur un texte etranger : la scission se verifie en
#: comparant chaque moitie au nom de l'equipe, et une moitie qui ne s'y rattache
#: pas laisse la valeur entiere dans `attributs[]`.
PAIR = " | "


@dataclass(frozen=True)
class Payload:
    """Le bloc rendu, et de quoi le mesurer sans le relire."""

    data: dict[str, Any]

    def dumps(self) -> str:
        """UTF-8, accents non echappes, cles triees : les accents coutent moins
        que leurs echappements, et l'ordre stable rend deux rendus comparables."""
        return json.dumps(self.data, ensure_ascii=False, sort_keys=True, indent=1)


def _attribut(fait: Fait) -> dict[str, Any]:
    return {
        "cle": fait.label,
        "valeur": fait.valeur,
        "source": fait.source,
        "date": fait.date,
        "niveau": fait.niveau,
    }


def _fact(fait: Fait | None, champ: str = "valeur") -> dict[str, Any] | None:
    """Un fait du socle nomme, ou `None` quand la ligne n'a pas ete rendue.

    **`None` et jamais un objet vide** : le contrat distingue « verifie, rien a
    dire » de « non instrumente », et un objet aux champs nuls confondrait les
    deux.
    """
    if fait is None:
        return None
    return {champ: fait.valeur, "source": fait.source, "date": fait.date, "niveau": fait.niveau}


def _split(fait: Fait, home: str, away: str) -> tuple[str, str] | None:
    """Les deux moities d'une ligne par equipe, ou `None` si la scission n'est
    pas verifiable.

    `_pair` joint les deux cotes par ` | ` : c'est **notre** separateur, et le
    lire n'est pas deviner. Mais une ligne peut n'en porter qu'un seul cote, ou
    contenir le separateur pour une autre raison — la scission n'est donc
    retenue que si chaque moitie commence par le nom de son equipe.

    **En cas de doute, rien** : la valeur entiere part dans `attributs[]`, ou
    elle reste lisible. Une scission devinee mettrait le classement d'une equipe
    en face de l'autre, ce qui est l'erreur la plus couteuse que ce bloc puisse
    produire.
    """
    moities = fait.valeur.split(PAIR)
    if len(moities) != 2:
        return None
    gauche, droite = (part.strip() for part in moities)
    if not gauche.startswith(home) or not droite.startswith(away):
        return None
    return gauche, droite


def _side(faits: dict[str, Fait], nom: str, home: str, away: str, cote: str) -> dict[str, Any]:
    """Le socle nomme d'une equipe : ce que le contrat lui reserve."""
    bloc: dict[str, Any] = {"nom": nom}
    for cle, label in TEAM_FIELDS.items():
        fait = faits.get(label)
        if fait is None:
            bloc[cle] = None
            continue
        moities = _split(fait, home, away)
        valeur = fait.valeur if moities is None else moities[0 if cote == "home" else 1]
        champ = "nom" if cle == "entraineur" else "valeur"
        bloc[cle] = {champ: valeur, "source": fait.source, "date": fait.date, "niveau": fait.niveau}
    return bloc


def _odds(event: RenderableEvent) -> dict[str, Any]:
    """Les cotes, **en colonnaire** : densite d'un tableau, un seul parseur.

    `releve_le` monte au niveau du bloc — il decrit le releve, pas chaque prix —
    et une saisie manuelle, qui n'a pas d'horodatage de marche, laisse la colonne
    `source` dire d'ou elle vient.
    """
    lignes: list[list[Any]] = []
    for cle, issues in sorted(event.markets.items()):
        marche = market_label(event.sport_key, MERGED_MARKETS.get(cle, cle))
        for issue in issues:
            libelle = issue.name if issue.point is None else f"{issue.name} {issue.point}"
            if issue.description:
                libelle = f"{issue.description} {libelle}"
            lignes.append([marche, libelle, float(price(issue.price)), issue.bookmaker or None])
    return {
        "releve_le": event.fetched_local.isoformat() if event.fetched_local else None,
        "colonnes": ["marche", "selection", "cote", "source"],
        "lignes": lignes,
    }


def _venue(fait: Fait | None) -> dict[str, Any] | None:
    """Le lieu, **degrade au niveau 4 quand il sort du geocodage**.

    Mesure du 21/08/2026 : sur 399 relevés `venue`, **4 seulement portent un
    pays**. Le reste est situe a la lecture, par population d'homonymes — et la
    regle a etiquete « La Cartuja (Sevilla) » en Colombie, faute du garde-fou du
    pays du club, absent des relevés anterieurs a `home_country`.

    **Aucun code nouveau pour autant** : le contrat porte deja le mecanisme. Un
    fait qu'on ne sait pas gager sort en niveau 4, ou il reste lisible sans
    pouvoir porter seul une confiance. C'est la reponse juste a un champ qui se
    trompe parfois — la meme que pour le drapeau de terrain neutre.
    """
    if fait is None:
        return None
    geocode = "pas d'identifiant de stade" in fait.valeur
    niveau = UNKNOWN_LEVEL if geocode else fait.niveau
    return {
        "valeur": fait.valeur,
        "source": fait.source,
        "date": fait.date,
        "niveau": niveau,
        "situe_par_geocodage": geocode,
    }


def _match(
    event: RenderableEvent, settings: Settings, index: int, servies: set[str]
) -> dict[str, Any]:
    # **Les faits deja assembles**, portes par l'evenement. Les recalculer ici
    # perdrait la cle du fournisseur, la surface et la competition — un bloc de
    # tennis y perdait son repos, son parcours et ses lignes de service, sans
    # qu'aucune erreur ne se leve. Le defaut caracteristique du projet, refait
    # dans le module qui le documente.
    faits = event.context_facts
    par_label = {fait.label: fait for fait in faits}
    # **Toutes les sources, y compris celles du socle nomme.** Les compter sur
    # les seuls `attributs` declarait muette une tranche dont la ligne etait
    # montee dans le socle — `Tour` et `Repos` sortent de nos propres scans.
    servies.update(fait.source for fait in faits if fait.source)
    densite = context_density([fait.label for fait in faits], event.sport_key, settings)
    statut = par_label.get("Statut")
    return {
        "origine": ORIGIN,
        "id": f"M{index}",
        "competition": event.competition,
        "tour": (par_label.get("Tour").valeur if par_label.get("Tour") else None),
        "debut_local": event.commence_local.isoformat(),
        "debut_paris": event.commence_local.isoformat(),
        "lieu": _venue(par_label.get("Lieu")),
        "statut": statut.valeur if statut else "programme",
        "domicile": _side(par_label, event.home, event.home, event.away, "home"),
        "exterieur": _side(par_label, event.away, event.home, event.away, "away"),
        "compositions": _fact(par_label.get("Compos"), "contenu"),
        "absences": _fact(par_label.get("Absents"), "contenu"),
        "h2h": _fact(next((f for f in faits if f.label.startswith("H2H (")), None), "resume"),
        "meteo": _fact(par_label.get("Meteo"), "contenu"),
        # **Tout ce qui n'est pas dans le socle, attribue.** Aujourd'hui les deux
        # tiers des faits : tout le tennis, et les statistiques de match du
        # football. Rien n'est perdu, et un libelle ajoute demain n'exige pas de
        # toucher au schema.
        "attributs": [_attribut(fait) for fait in faits if fait.label not in NAMED],
        "cotes": _odds(event),
        # **La densite n'est pas un fait sur le match** : elle mesure ce que la
        # collecte a rapporte. « 0 sur 25 » lu comme une propriete de la
        # rencontre dirait l'inverse de ce que la ligne existe pour dire.
        # La cause typee **quand le bloc est maigre ou vide**, et elle seule
        # decide du budget de recherche : « competition non rattachee » ne vaut
        # aucun dossier — ca se repare d'un geste — quand « non interroges » fait
        # du bloc le meilleur du lot, la recherche y etant le seul chemin.
        "collecte": {
            "densite": {"attendus": densite.expected, "obtenus": densite.filled},
            "cause": failure_causes([event.event_id], settings).get(event.event_id)
            if densite.known and (densite.empty or densite.thin)
            else None,
        },
    }


def build_payload(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
    competition_id: int | None = None,
) -> Payload:
    """Le bloc de donnees d'une session. **Aucun appel reseau.**

    Tout est relu en base, comme le prompt : regenerer ne coute rien et ne
    declenche aucun appel.

    `sports` porte une liste — les lots mixtes existent et se rendent deja — et
    `framework_version` suit la **Skill**, pas ce code : c'est le cadre qui
    decide de ce qui est rendu, et une analyse archivee doit pouvoir se relire
    contre les regles en vigueur au moment ou elle a ete produite.
    """
    settings = settings or get_settings()
    moment = (now or datetime.now(ZoneInfo(settings.tz))).astimezone(ZoneInfo(settings.tz))
    events = renderable_events(session_id, settings, moment, competition_id)
    servies: set[str] = set()
    matchs = [
        _match(event, settings, index, servies) for index, event in enumerate(events, start=1)
    ]

    books = [event.primary_book for event in events if event.primary_book]
    # **Les sources qui n'ont rien rendu sur tout le lot**, lues dans le registre
    # d'attribution et jamais dans une liste posee ici.
    #
    # Le premier jet comparait le **nom de la tranche** au contenu du champ
    # `source` — « elo » cherche dans « tennis-abstract » — et declarait donc
    # muette une tranche qui venait de rendre sa ligne. C'est la table parallele
    # que ce projet proscrit, refaite a trois lignes de la regle qui l'interdit.
    muets = sorted(
        {
            tranche.source
            for cle, tranche in TRANCHES.items()
            if cle != "collecte" and tranche.source not in servies
        }
    )
    return Payload(
        {
            "origine": ORIGIN,
            "framework_version": FRAMEWORK_VERSION,
            "genere_le": moment.isoformat(),
            "sports": sorted({event.sport_key for event in events}),
            "nb_matchs": len(matchs),
            "bookmaker_principal": books[0] if books else None,
            "bookmaker_reference": next((book for book in books[1:] if book != books[0]), None)
            if books
            else None,
            "sections_attendues": list(SECTIONS),
            "collecte": {
                "densite": {
                    "attendus": sum(m["collecte"]["densite"]["attendus"] for m in matchs),
                    "obtenus": sum(m["collecte"]["densite"]["obtenus"] for m in matchs),
                },
                # **Quels producteurs n'ont rien rendu sur tout le lot.** Un
                # `tennis_history` silencieux se voit une fois, pas vingt.
                "producteurs_muets": muets,
            },
            "matchs": matchs,
        }
    )
