"""Le cran de confiance, calcule a partir de ce que l'analyse declare.

**Toute regle que l'application peut trancher de facon deterministe ne se
delegue pas au modele.** C'est le principe deja pose pour la famille d'un marche
et pour le comptage de la section C : une regle laissee au modele coute des
tokens, se refait a chaque session et ne se mesure jamais.

Le cran etait exactement dans ce cas. Le gabarit le definit depuis toujours
comme une fonction de trois choses verifiables — le niveau des sources, le
nombre d'editeurs distincts, et si un manque de la section A touche le facteur
porteur — et il demandait ensuite au modele d'appliquer la table lui-meme.
Mesure sur les 141 selections tranchees : **90 % du volume sur deux crans**
(3 et 4), aucune en cran 1, et l'ordre observe n'est pas monotone — cran 2 a
77 %, cran 4 a 60 %, cran 3 a 44 %. Une echelle dont deux crans sur cinq
portent tout, et dans le desordre, ne note plus rien.

Ce module ne fait donc que deux choses, et aucune ne touche a la base :
lire ce que l'analyse a declare, et appliquer la table.

**Il ne remplace pas la valeur declaree, il la double.** L'ecart entre les deux
est la seule mesure possible de savoir si le modele notait au hasard : le jeter
serait perdre la reponse a la question qui a fait naitre ce module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

#: Niveaux de l'echelle de sources du preambule. `lecture` n'est pas une absence
#: de valeur mais une valeur : l'analyse declare qu'aucun fait date ne porte la
#: selection. La distinguer de « non renseigne » est tout l'objet de la mesure.
READING = "lecture"
LEVELS = ("1", "2", "3", "4", READING)

#: Niveaux qui portent un cran haut. Au-dela, la table plafonne a 2 — c'est la
#: regle du preambule, ecrite une seule fois ici.
STRONG_LEVELS = (1, 2)

#: Editeurs distincts qu'il faut pour atteindre le cran 5.
PUBLISHERS_FOR_TOP = 2

#: Un domaine, tel qu'il reste apres normalisation. Sert a refuser une valeur
#: qui n'en est pas un — un titre d'article, une phrase — plutot qu'a valider
#: finement : c'est l'unicite qui compte, pas l'existence du site.
DOMAIN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}$")


class ClaimError(ValueError):
    """Le bloc structure est illisible. Le cran reste inconnu, jamais devine."""


def publisher_of(raw: str) -> str:
    """Le domaine normalise d'un editeur. « » si la valeur n'en est pas un.

    **L'unicite se teste sur le domaine, jamais sur le titre de l'article.**
    Deux articles rapportant la meme conference de presse sont un seul facteur :
    l'editeur d'origine est le club, et deux titres differents ne feraient pas
    deux origines qui puissent se tromper separement.

    `https://www.motherwellfc.co.uk/news/2026/…` et `Motherwell FC` ne se
    ramenent pas au meme jeton — le second n'est pas un domaine et sort vide.
    C'est voulu : ce qui n'est pas verifiable ne compte pas.
    """
    value = (raw or "").strip().lower()
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("/", 1)[0].split("?", 1)[0]
    value = re.sub(r"^www\.", "", value).strip(".")
    return value if DOMAIN.match(value) else ""


@dataclass(frozen=True)
class Fact:
    """Un fait date, tel que l'analyse le declare.

    Les quatre champs sont **obligatoires**. Un fait incomplet ne se repare pas
    a moitie : sans editeur il ne compte dans aucune unicite, sans date il ne se
    verifie pas, et l'accepter quand meme ferait porter un cran par une
    affirmation que personne ne peut recouper.
    """

    statement: str
    day: str
    publisher: str
    level: int


@dataclass
class Claim:
    """Ce que l'analyse declare pour une selection, avant tout calcul."""

    source_level: str = ""
    facts: tuple[Fact, ...] = ()
    #: Un manque de la section A touche-t-il le facteur porteur. **Trois etats**,
    #: et le troisieme n'est pas un defaut : `None` veut dire que le bloc ne l'a
    #: pas dit, et les crans 3, 4 et 5 ne se distinguent que par lui. Le deviner
    #: reviendrait a choisir un cran a la place de l'analyse.
    gap_touches_factor: bool | None = None
    #: Ce que le modele avait annonce. Conserve ici pour que l'ecart se mesure
    #: sans relire la base, jamais lu par le calcul.
    declared: int | None = None
    match: str = ""
    raw: str = ""

    @property
    def strong_facts(self) -> tuple[Fact, ...]:
        return tuple(fact for fact in self.facts if fact.level in STRONG_LEVELS)

    @property
    def distinct_publishers(self) -> int:
        """Editeurs distincts **parmi les faits de niveau 1-2**.

        Le filtre de niveau fait partie de la regle du cran 5 — « au moins deux
        faits d'editeurs distincts, tous en niveau 1-2 » — et le compte stocke
        est celui que la regle a employe. Un compte plus large s'afficherait a
        cote d'un cran qu'il n'expliquerait pas.
        """
        return len({fact.publisher for fact in self.strong_facts})

    @property
    def reading_only(self) -> bool:
        """Aucun fait date ne porte la selection.

        **La liste de faits fait foi, pas le niveau declare.** Le gabarit
        autorise une liste vide et impose alors `lecture` : une selection sans
        fait est une lecture des blocs, quelle que soit la qualite du
        fournisseur qui les a remplis. C'est ecrit noir sur blanc dans le
        preambule, et c'est ce qui empeche un bloc de contexte d'etre promu au
        rang de source citee.
        """
        return not self.facts or self.source_level == READING

    @property
    def rung(self) -> int | None:
        """Le cran, ou `None` quand le bloc ne permet pas de le dire.

        `None` n'est pas un echec du calcul : c'est le refus de choisir un cran
        a la place de l'analyse quand elle n'a pas dit si un manque touche son
        facteur. Un repli sur la valeur declaree ferait exactement ce que ce
        module existe pour defaire.
        """
        if self.reading_only:
            return 1
        if self.source_level in ("3", "4"):
            return 2
        if self.gap_touches_factor is None:
            return None
        if self.gap_touches_factor:
            return 3
        if self.distinct_publishers >= PUBLISHERS_FOR_TOP:
            return 5
        return 4

    @property
    def disagrees(self) -> bool:
        """Le cran calcule differe de celui annonce. Faux si l'un manque."""
        return self.declared is not None and self.rung is not None and self.declared != self.rung


#: Le bloc structure, tel que le gabarit le demande : une cloture ```conf
#: suivie d'un objet JSON. Un bloc par selection, dans le meme copier-coller que
#: le tableau — demander un second geste a l'utilisateur ferait perdre le champ
#: le jour ou il l'oublie.
BLOCK = re.compile(r"```(?:conf|json)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def _level(raw: object) -> int:
    value = str(raw).strip()
    if value not in ("1", "2", "3", "4"):
        raise ClaimError(f"niveau de source inconnu : {raw!r}")
    return int(value)


def _day(raw: object) -> str:
    """La date d'un fait, en ISO. Une date illisible invalide le fait.

    Elle n'est pas decorative : c'est elle qui rend le fait verifiable en une
    recherche, et un fait qu'on ne peut pas dater ne porte pas un cran.
    """
    value = str(raw or "").strip()
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ClaimError(f"date de fait illisible : {raw!r}") from exc
    return value


def _fact(payload: object) -> Fact:
    if not isinstance(payload, dict):
        raise ClaimError("un fait doit etre un objet")
    publisher = publisher_of(str(payload.get("editeur") or payload.get("publisher") or ""))
    if not publisher:
        raise ClaimError(f"editeur sans domaine exploitable : {payload.get('editeur')!r}")
    statement = str(payload.get("enonce") or payload.get("statement") or "").strip()
    if not statement:
        raise ClaimError("un fait sans enonce ne se verifie pas")
    return Fact(
        statement=statement,
        day=_day(payload.get("date")),
        publisher=publisher,
        level=_level(payload.get("niveau") or payload.get("level")),
    )


def parse(payload: str | dict) -> Claim:
    """Lit un bloc structure. Leve `ClaimError` plutot que de deviner.

    **Aucun repli silencieux.** Un bloc illisible laisse le cran a `NULL` et
    l'echec se journalise : retomber sur la valeur declaree ferait passer pour
    calculee une note qui ne l'est pas, et le taux de desaccord — la seule chose
    que ce chantier mesure — annoncerait alors un accord parfait.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ClaimError(f"bloc illisible : {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ClaimError("le bloc doit etre un objet")

    raw_level = payload.get("source_level", payload.get("source", ""))
    level = str(raw_level).strip().lower()
    if level not in LEVELS:
        raise ClaimError(f"source_level inconnu : {raw_level!r}")

    facts_payload = payload.get("faits", payload.get("facts", []))
    if facts_payload is None:
        facts_payload = []
    if not isinstance(facts_payload, list):
        raise ClaimError("« faits » doit etre une liste")

    gap = payload.get("manque_touche_facteur", payload.get("gap_touches_factor"))
    if gap is not None and not isinstance(gap, bool):
        raise ClaimError(f"« manque_touche_facteur » doit etre un booleen : {gap!r}")

    declared = payload.get("confiance", payload.get("confidence"))
    return Claim(
        source_level=level,
        facts=tuple(_fact(item) for item in facts_payload),
        gap_touches_factor=gap,
        declared=int(declared) if str(declared or "").strip().isdigit() else None,
        match=str(payload.get("match") or "").strip(),
        raw=json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )


@dataclass
class Reading:
    """Ce qu'un copier-coller a rendu : les blocs lus, et ceux qui ont echoue."""

    claims: list[Claim] = field(default_factory=list)
    #: Un bloc par motif d'echec. **Affiche, jamais tu** — meme regle que les
    #: lignes rejetees de la saisie manuelle : un bloc qui ne passe pas doit se
    #: voir, sans quoi la colonne reste vide sans que personne sache pourquoi.
    rejected: list[str] = field(default_factory=list)

    def for_match(self, label: str) -> list[Claim]:
        return [claim for claim in self.claims if claim.match == label]


def read_blocks(raw: str) -> Reading:
    """Tous les blocs structures d'un rendu, dans leur ordre d'apparition."""
    reading = Reading()
    for index, body in enumerate(BLOCK.findall(raw or ""), start=1):
        try:
            reading.claims.append(parse(body))
        except ClaimError as exc:
            reading.rejected.append(f"bloc {index} : {exc}")
    return reading
