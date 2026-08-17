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

**Il ne remplace pas la valeur declaree, il la double** — et ce que leur ecart
mesure a change de nature au passage. Tant que le modele notait seul, on aurait
mesure son flair ; depuis qu'il declare ses entrees et que la table s'applique
ici, les deux valeurs sortent du **meme** faisceau. L'ecart teste donc s'il
applique correctement sa propre table : c'est un **lint sur la redaction du
gabarit**, pas sur son jugement. Plus utile ainsi — une clause ambigue se
reecrit, un jugement ne se corrige pas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

from .ingestion import CONF, JSON_INVALID, SCHEMA_INVALID, Reject, read_bodies

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
    """Le bloc structure est illisible. Le cran reste inconnu, jamais devine.

    Le motif accompagne le message, et il n'est pas decoratif : « le JSON ne se
    relit pas » et « un champ manque » se reparent aux deux bouts opposes du
    parcours — l'un dans le collage, l'autre dans le gabarit. Les compter
    ensemble ferait chercher au mauvais endroit.
    """

    def __init__(self, message: str, reason: str = SCHEMA_INVALID) -> None:
        super().__init__(message)
        self.reason = reason


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

    Les quatre premiers champs sont **obligatoires**. Un fait incomplet ne se
    repare pas a moitie : sans editeur il ne compte dans aucune unicite, sans
    date il ne se verifie pas, et l'accepter quand meme ferait porter un cran
    par une affirmation que personne ne peut recouper.
    """

    statement: str
    day: str
    publisher: str
    level: int
    #: L'editeur **d'origine**, quand il differe de celui qui publie. Vide dans
    #: le cas ordinaire, ou les deux se confondent.
    #:
    #: **Sans lui, la normalisation de domaine sur-compte l'independance
    #: exactement la ou le gabarit previent.** Un article OneFootball qui reprend
    #: Glorioso 1904, ou deux titres rapportant la meme conference de presse,
    #: sortent sur deux domaines distincts et feraient un cran 5 — alors que le
    #: gabarit dit depuis toujours que l'editeur d'origine est alors le club, et
    #: que ca ne fait **qu'un seul facteur**. Le domaine qui publie ne peut pas
    #: le savoir : c'est une propriete du contenu, pas de l'URL.
    origin: str = ""

    @property
    def source(self) -> str:
        """L'origine si elle est declaree, l'editeur sinon.

        **C'est elle qui compte l'independance**, jamais `publisher` : deux faits
        qui remontent a la meme conference de presse ne peuvent pas se tromper
        separement, quel que soit le nombre de sites qui les relaient.
        """
        return self.origin or self.publisher


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
    #: sans relire la base, et **jamais lu par le calcul** : le cran se deduit
    #: des entrees, sans quoi l'annonce se validerait elle-meme.
    declared: int | None = None
    match: str = ""
    raw: str = ""
    #: L'endroit du collage brut d'ou le bloc vient. Le bloc de confiance vit sur
    #: la meme ligne de `picks` que la selection mais **arrive d'ailleurs** dans
    #: le texte : une seule paire de bornes ne pourrait pas porter les deux.
    start: int | None = None
    end: int | None = None

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

        Compte sur `Fact.source` et non sur `publisher` : c'est l'origine qui
        peut se tromper, pas le site qui relaie.
        """
        return len({fact.source for fact in self.strong_facts})

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

#: Les cles qui font qu'un objet **est** un bloc de confiance, quand il arrive
#: sans sa cloture. Un copier-coller depuis le rendu consomme les trois accents
#: graves comme il consomme les barres d'un tableau ; la forme est alors tout ce
#: qui reste pour reconnaitre le bloc.
#:
#: `faits` et `source_level` appartiennent en propre a cette famille — un
#: combine n'en porte aucune — et `confiance` est gardee pour un rendu qui
#: omettrait les deux autres. `type` n'y figure **pas** : les deux familles la
#: portent, et s'en servir ferait lire un combine comme un bloc de confiance.
CLAIM_KEYS = ("faits", "facts", "source_level", "confiance", "confidence")


def is_claim(payload: dict) -> bool:
    """Cet objet est-il un bloc de confiance, lu sur sa seule forme ?"""
    return any(key in payload for key in CLAIM_KEYS) and not (
        "jambes" in payload or "legs" in payload
    )


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
    # L'origine est facultative — le cas ordinaire est qu'elle se confonde avec
    # l'editeur — mais si elle est declaree elle doit etre un domaine comme lui.
    # Une origine illisible laisserait compter l'independance sur le relais.
    declared_origin = str(payload.get("editeur_origine") or payload.get("origin") or "").strip()
    origin = publisher_of(declared_origin) if declared_origin else ""
    if declared_origin and not origin:
        raise ClaimError(f"editeur d'origine sans domaine exploitable : {declared_origin!r}")
    return Fact(
        statement=statement,
        day=_day(payload.get("date")),
        publisher=publisher,
        level=_level(payload.get("niveau") or payload.get("level")),
        origin=origin,
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
            raise ClaimError(f"bloc illisible : {exc.msg}", JSON_INVALID) from exc
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
    #: Un bloc par motif d'echec, **avec son texte brut**. Affiche, jamais tu —
    #: meme regle que les lignes rejetees de la saisie manuelle : un bloc qui ne
    #: passe pas doit se voir, sans quoi la colonne reste vide sans que personne
    #: sache pourquoi. Le brut est ce qui rend le rejet reparable : sans lui on
    #: saurait qu'un bloc a echoue et jamais lequel.
    rejects: list[Reject] = field(default_factory=list)

    @property
    def rejected(self) -> list[str]:
        """Les motifs seuls, pour les surfaces qui n'affichent que du texte.

        **Derive, jamais tenu a cote** : deux listes portant la meme information
        auraient diverge au premier motif ajoute d'un seul cote.
        """
        return [reject.detail for reject in self.rejects]

    def for_match(self, label: str) -> list[Claim]:
        return [claim for claim in self.claims if claim.match == label]


#: La ligne des dossiers ouverts, au niveau de la session :
#: `dossiers_ouverts: [M1, M4, M7]`. Une **ligne structuree** et non la prose de
#: la section F : celle-ci est redigee pour etre lue, elle change de tournure
#: d'une session a l'autre, et ses reperes s'y trouvent au milieu de phrases qui
#: expliquent pourquoi. La prose garde son travail a cote.
OPEN_LINE = re.compile(r"dossiers_ouverts\"?\s*:\s*\[([^\]]*)\]", re.IGNORECASE)

#: La **cle** seule, sans sa valeur. Elle separe deux defauts que le repli
#: confondait : le modele qui omet la ligne, et la ligne presente dont la valeur
#: ne se relit pas. Les deux produisent le meme repli — tout le lot en lecture —
#: mais ni la meme cause ni le meme correctif : l'un se reprend dans le gabarit,
#: l'autre dans le lecteur. Leur somme se lirait comme un seul taux.
OPEN_KEY = re.compile(r"dossiers_ouverts", re.IGNORECASE)

#: Un repere de bloc, tel que le prompt les numerote.
MARK = re.compile(r"M\d+")

#: La ligne a ete lue et porte au moins un repere.
OPEN_READ = "renseignee"
#: La ligne a ete lue et sa liste est vide. **Etat a part entiere, et c'est la
#: moitie du defaut** : `dossiers_ouverts: []` est une declaration legitime — le
#: modele n'a rien ouvert, le gabarit l'autorise — quand une ligne absente est
#: un collage rate. Les deux forcent tout le lot en lecture, donc les confondre
#: melangeait une reponse du modele et une panne de transmission dans le meme
#: taux. Meme regle que les trois etats de la ligne `Absents`.
OPEN_EMPTY = "vide"
#: Aucune trace de la cle dans le rendu : le gabarit ne l'a pas fait ecrire, ou
#: le modele l'a omise.
OPEN_ABSENT = "absente"
#: La cle est la, sa valeur ne se relit pas. Defaut de lecteur, pas de modele.
OPEN_MALFORMED = "illisible"

#: Pourquoi une selection a ete ramenee en lecture, cran 1.
#:
#: **Les six ne se lisent pas ensemble.** Les trois premieres decrivent ce que
#: l'analyse a fait — ce sont des observations, et elles ont leur place dans une
#: statistique sur le modele. Les trois dernieres decrivent ce que le collage a
#: perdu : elles se reparent en recollant, et les compter avec les autres ferait
#: passer une panne de transmission pour un resultat.
OVERRIDE_HORS_DOSSIERS = "hors_dossiers"
OVERRIDE_AUCUN_DOSSIER = "aucun_dossier"
OVERRIDE_SANS_FAIT = "sans_fait"
OVERRIDE_LIGNE_ABSENTE = "ligne_absente"
OVERRIDE_LIGNE_ILLISIBLE = "ligne_illisible"
OVERRIDE_REPERES = "reperes_non_resolus"

#: Ce que chaque cause dit, en une phrase, la ou elle se rend.
OVERRIDE_CAUSES: dict[str, str] = {
    OVERRIDE_HORS_DOSSIERS: "match hors des dossiers déclarés ouverts",
    OVERRIDE_AUCUN_DOSSIER: "aucun dossier déclaré ouvert sur cette session",
    OVERRIDE_SANS_FAIT: "dossier ouvert, aucun fait daté n'en est tiré",
    OVERRIDE_LIGNE_ABSENTE: "ligne « dossiers_ouverts » absente du collage",
    OVERRIDE_LIGNE_ILLISIBLE: "ligne « dossiers_ouverts » illisible",
    OVERRIDE_REPERES: "repères de « dossiers_ouverts » non résolus",
}

#: Les causes qui ne disent **rien du modele**. Un cran 1 pose par l'une d'elles
#: n'est pas une lecture constatee : c'est une mesure qui n'a pas eu lieu, et
#: elle doit sortir de toute statistique sur la notation.
COLLECTION_FAULTS = frozenset({OVERRIDE_LIGNE_ABSENTE, OVERRIDE_LIGNE_ILLISIBLE, OVERRIDE_REPERES})


def is_collection_fault(cause: str | None) -> bool:
    """La cause decrit-elle un collage perdu plutot qu'une analyse ?"""
    return cause in COLLECTION_FAULTS


@dataclass(frozen=True)
class Opened:
    """Les dossiers que l'analyse declare avoir ouverts.

    **Le defaut est `lecture`, jamais « ouvert ».** Toute la valeur du chantier
    tient la : liste absente, illisible, ou portant un repere qui ne se resout
    pas, et l'ensemble des selections de la session passe en lecture. Meme
    raisonnement que la somme de controle de l'appariement — un `lecture` de
    trop se voit et se corrige, un niveau de source gonfle qui passe pour
    verifie ne se voit pas.
    """

    marks: frozenset[str] = frozenset()
    #: Ce qui est arrive a la ligne : `renseignee`, `vide`, `absente` ou
    #: `illisible`.
    #:
    #: **Quatre etats et non deux.** Une ligne omise, une ligne qu'on ne sait
    #: pas relire et une ligne vide produisent le meme repli — tout le lot en
    #: lecture — mais ni la meme cause ni le meme correctif, et leur somme se
    #: lirait comme un seul taux. C'est la meme regle que les trois etats de la
    #: ligne `Absents`.
    #:
    #: L'etat est **persiste** et non deduit apres coup : une fois les
    #: selections ecrites, plus rien dans la base ne dirait laquelle des quatre
    #: situations a produit les crans 1.
    state: str = OPEN_ABSENT

    @property
    def declared(self) -> bool:
        """La ligne etait presente **et** lisible.

        **Vrai n'est pas « des dossiers »** : une liste vide est une
        declaration, et elle porte son propre etat plutot que de se confondre
        avec une ligne absente.
        """
        return self.state in (OPEN_READ, OPEN_EMPTY)

    def cause(self, *, resolved: bool) -> str:
        """La cause a enregistrer sur une selection ramenee en lecture.

        `resolved` dit si les reperes se sont resolus contre un prompt : une
        liste renseignee qui ne se rattache a rien est un defaut de collage, pas
        une observation sur le modele.
        """
        if self.state == OPEN_ABSENT:
            return OVERRIDE_LIGNE_ABSENTE
        if self.state == OPEN_MALFORMED:
            return OVERRIDE_LIGNE_ILLISIBLE
        if self.state == OPEN_EMPTY:
            return OVERRIDE_AUCUN_DOSSIER
        return OVERRIDE_HORS_DOSSIERS if resolved else OVERRIDE_REPERES

    @property
    def note(self) -> str:
        if self.state == OPEN_MALFORMED:
            return (
                "La ligne « dossiers_ouverts » est présente mais sa liste ne se relit "
                "pas : toutes les sélections sont enregistrées en lecture, cran 1."
            )
        if not self.declared:
            return (
                "Aucune ligne « dossiers_ouverts: [M1, M4, …] » dans le rendu : toutes "
                "les sélections sont enregistrées en lecture, cran 1. Ce n'est pas une "
                "mesure — c'est le collage qui l'a laissée derrière lui, et il se "
                "reprend en recollant le rendu entier."
            )
        if not self.marks:
            # Une declaration, pas un defaut : le gabarit autorise la liste vide,
            # et le repli qui suit est alors une observation sur le modele. Le
            # dire evite qu'on aille chercher un collage rate qui n'existe pas.
            return (
                "Aucun dossier déclaré ouvert : toutes les sélections sont enregistrées "
                "en lecture, cran 1. La ligne a bien été lue — c'est une déclaration du "
                "modèle, pas un collage manquant."
            )
        return ""


def read_opened(raw: str) -> Opened:
    """Lit la ligne des dossiers ouverts. Absente, elle ne se devine pas.

    **Trois issues, parce que deux defauts differents s'y confondaient.** La cle
    seule dit que le modele a repondu ; sa valeur dit que nous savons la lire. Un
    rendu qui ecrit `dossiers_ouverts: M1, M4` sans crochets passait pour une
    ligne omise, et le gabarit se faisait accuser d'un defaut de lecteur.
    """
    found = OPEN_LINE.search(raw or "")
    if found is not None:
        marks = frozenset(MARK.findall(found.group(1).upper()))
        # La liste vide porte son propre etat. Elle produit le meme repli qu'une
        # ligne absente et n'a pas la meme cause : ici le modele a repondu.
        return Opened(marks=marks, state=OPEN_READ if marks else OPEN_EMPTY)
    if OPEN_KEY.search(raw or ""):
        return Opened(state=OPEN_MALFORMED)
    return Opened(state=OPEN_ABSENT)


def read_blocks(raw: str) -> Reading:
    """Tous les blocs structures d'un rendu, dans leur ordre d'apparition.

    **Clotures ou non**, et c'est la reparation du chantier : un bloc de code
    copie depuis le rendu de Claude arrive sans ses trois accents graves,
    exactement comme un tableau y arrive tabule plutot qu'a barres verticales.
    Le module savait deja lire les deux formes d'un tableau et n'en lisait
    qu'une pour les blocs — celle que le copier-coller detruit.
    """
    reading = Reading()
    for index, body in enumerate(read_bodies(raw or "", BLOCK, is_claim), start=1):
        found = body.text
        # La ligne des dossiers ouverts se donne hors de tout bloc, mais un
        # rendu peut la cloturer quand meme. L'ecarter ici plutot que de la
        # rejeter : comptee comme un bloc de confiance en echec, elle ferait
        # diverger le compte des blocs de celui des lignes et couterait les
        # crans de tout le lot pour une ligne qui n'en est pas un.
        if OPEN_LINE.search(found):
            continue
        try:
            claim = parse(found)
        except ClaimError as exc:
            reading.rejects.append(
                Reject(
                    block_type=CONF,
                    reason=exc.reason,
                    detail=f"bloc {index} : {exc}",
                    payload=found,
                    start=body.start,
                    end=body.end,
                )
            )
            continue
        claim.start, claim.end = body.start, body.end
        reading.claims.append(claim)
    return reading
