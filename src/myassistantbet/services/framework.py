"""Le cadre publie, et la preuve qu'il a ete lu.

**Ce module existe a cause d'une erreur qui n'etait detectable ni par le code ni
par les tests.** Le 21/08/2026, `FRAMEWORK_VERSION` est passe a `1.4` sur une
declaration de publication ; le cadre servi disait encore `1.3`, et six copies
du cache de plugin le disaient — elles disaient vrai. Aucune sortie n'a ete
produite dans la fenetre, mais la faute n'est pas la : ce champ n'a qu'une
utilite, ne pas melanger deux regimes dans une population, et un numero pose en
avance la lui retire entierement.

## Une declaration n'est pas une preuve

Le numero se bouge desormais accompagne d'une **lecture mecanique** du cadre
publie. Deux chemins, dans cet ordre :

1. **Le cadre est lisible sur cette machine** — il fait foi, sans appel. C'est
   la source, pas une copie.
2. **Il ne l'est pas** — un depot frais, une CI, une machine sans le plugin —
   alors la **preuve enregistree** (`deploy/cadre-lu.json`) prend le relais. Elle
   est ecrite par `myassistantbet-cadre --relire`, qui lit le fichier reel :
   c'est ce qui la distingue d'une affirmation.

**Sans aucun des deux, le garde est rouge.** C'est deliberе et c'est le coeur du
module : un garde qui se tait quand il ne peut pas verifier est indiscernable
d'un garde qui a verifie — le defaut caracteristique de ce projet, applique
cette fois au dispositif de verification lui-meme.

## Le garde suit ce qui produit, et il ne s'est pas tu

**Depuis le 27/08/2026 l'exigence est conditionnee a `ACTIVE_PRODUCER`.** Le
numero declare ne garde quelque chose que s'il **etiquette une sortie** : tant
que le gabarit produit, il n'en etiquette aucune — la Skill a ete publiee en 1.4
puis desactivee, et un ecart entre deux copies dont aucune ne sert n'apprend
rien. Le referent d'une sortie est `sessions.gabarit_sha`, ecrit par
`save_prompt` sur ce qui produit vraiment, et il est **mecanique** : rien a
tenir a jour a la main, donc rien qui puisse diverger.

Ce n'est **pas** un garde tu. La lecture se fait toujours, seule l'exigence est
conditionnee, et une seconde sentinelle tient l'equivalence dans les deux sens :
`framework_version` est auditee si et seulement si le payload produit. Le jour
ou `ACTIVE_PRODUCER` bascule, `build_payload` reemet le champ dans ce qui part,
et tout ce module redevient exigeant sans que personne ait a s'en souvenir.

## Ce que le garde ne fait pas

Il ne **releve pas** le numero a la place de qui exploite, et il ne bloque pas
davantage une publication qu'un retour arriere : il demande de choisir. Un cadre
publie en `1.4` alors que la constante dit `1.3` le rend rouge exactement comme
l'inverse — dans les deux cas, deux ecritures de la meme chose ont diverge, et
c'est ca qu'on veut voir.

**Le fichier de preuve peut se retoucher a la main**, et rien ne l'empeche. Ce
n'est pas la faille qu'on croit : ce qui a produit l'erreur du 21/08 n'est pas
une falsification, c'est un raccourci de bonne foi. Le garde retire le
raccourci ; il ne pretend pas resister a une intention contraire, et le dire est
plus honnete que de laisser croire l'inverse.

## Le residu, et il est structurel

**Le cache de plugin est lui-meme une copie.** Ce module compare la constante a
ce que **cette machine a recu**, jamais a ce que le fournisseur sert : un cache
en retard rendrait le garde vert sur un desaccord reel — cadre publie en `1.4`,
cache encore a `1.3`, constante a `1.3`, tout concorde et rien n'est vrai.

C'est **un cran de moins** que le raccourci qu'il remplace, pas zero. Le
raccourci d'hier n'avait aucune source ; celui-ci en a une, decalee dans le
temps par un mecanisme qu'on ne controle pas. La difference se mesure : une
declaration ne vieillit pas, elle est fausse ou vraie a l'instant ou elle est
faite ; un cache, lui, **converge** — il finit par recevoir la publication, et le
garde vire alors au rouge tout seul.

Ce qui le fermerait vraiment est une lecture de la source publiee, et elle n'est
pas a notre portee : le fournisseur n'expose pas de point de lecture, et
`read_at` est la seule chose qui dise a quel age la reponse remonte. Porte
laissee **entrouverte et datee**, contrairement a celles que ce projet ferme :
le jour ou un tel point existe, `published()` est le seul endroit a changer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

#: La version du cadre d'analyse sous lequel une sortie a ete produite.
#:
#: **Sans elle, une analyse archivee ne se relit plus contre les regles en
#: vigueur au moment ou elle a ete produite**, et la base de calibration devient
#: inhomogene sans que rien ne le signale. Meme raison que `sessions.scale_version`,
#: dont le commentaire dit qu'une echelle ne se reconstitue pas apres coup.
#:
#: Elle **suit la Skill** et non le code de cette application : c'est le cadre qui
#: decide de ce qui est rendu. 1.3 — plancher de cote explicite, regle
#: d'agregation du niveau de source, deux controles de plus en fin de checklist.
#:
#: **Passee a 1.4 le 21/08/2026 puis ramenee a 1.3 le meme soir.** Le bump avait
#: ete fait sur une declaration de publication ; le cadre servi disait encore
#: 1.3 — six copies de cache le disaient, et elles disaient vrai. Aucune sortie
#: n'a ete produite dans la fenetre, mais la faute n'est pas la : bumper avant
#: publication fait mentir le payload, et ce champ perd alors sa seule utilite,
#: qui est de ne pas melanger deux regimes dans une population.
#:
#: **Le garde qui en sort vit dans `services/framework.py`** : ce numero ne se
#: bouge qu'accompagne d'une preuve de lecture mecanique du cadre publie. Une
#: declaration n'en est pas une — c'est le seul moyen que cette erreur ne se
#: reproduise pas, elle n'etait detectable ni par le code ni par les tests.
#:
#: **Cette constante est declarative, et c'est structurel.** Le cadre vit chez le
#: fournisseur de Skill, cette valeur en base : rien ne peut forcer les deux a
#: concorder depuis ici, et le troisieme cas de la regle des copies s'applique —
#: quand on ne peut pas forcer l'accord, on cesse de dependre de la copie. Ce
#: numero ne prouve donc pas quel cadre a produit une sortie ; il dit sous quel
#: cadre elle etait **cense** l'etre, ce qui suffit a ne pas melanger deux
#: regimes dans une meme population et ne suffit a rien d'autre.
#:
#: **Elle n'etiquette plus aucune sortie depuis le 27/08/2026, et le referent est
#: ailleurs.** `add_pick` l'a estampillee sur 205 selections entre le 22 et le
#: 27/08 ; c'etait une troisieme copie, et du mauvais sujet — elle suit la Skill
#: quand `ACTIVE_PRODUCER` vaut le gabarit. Ce qui date une sortie est
#: `sessions.gabarit_sha`, empreinte **mecanique** du gabarit rendu, doublee de
#: `gabarit_version` qui nomme la decision : mesure du 27/08, **5 empreintes
#: distinctes sous le seul libelle « lot-3 »**. Le hash faisait deja le travail.
#:
#: Elle reste emise par `payload.build_payload`, qui ne sert pas encore — et
#: c'est ce qui rend le garde de lecture **conditionnel** plutot que retire :
#: voir `tests/test_sentinelles.py`, ou l'exigence revient avec le payload.
FRAMEWORK_VERSION = "1.3"

#: Ou le cadre publie se lit sur cette machine. Le cache de plugin range chaque
#: revision sous une empreinte differente : on les lit toutes et on garde la
#: plus recente, comme `prompt_odds` garde le dernier releve.
PUBLISHED_GLOBS: tuple[str, ...] = (
    ".claude/remote/plugins/*/skills/myassistantbet-framework/SKILL.md",
    ".claude/plugins/*/skills/myassistantbet-framework/SKILL.md",
    ".claude/skills/myassistantbet-framework/SKILL.md",
)

#: La ligne qui porte le numero, telle que le cadre l'ecrit : `**Version 1.3.**`
VERSION_LINE = re.compile(r"\*\*Version\s+([0-9]+(?:\.[0-9]+)*)\s*\.?\*\*")

#: La preuve enregistree, versionnee avec le depot. Elle voyage donc avec le
#: commit qui bouge le numero, ce qui est exactement la contrainte demandee.
PROOF_PATH = Path(__file__).resolve().parents[3] / "deploy" / "cadre-lu.json"


@dataclass(frozen=True)
class Published:
    """Un exemplaire du cadre, lu quelque part."""

    version: str
    sha256: str
    path: str
    #: L'instant de la lecture, en ISO 8601 UTC. Sur un exemplaire lu a
    #: l'instant c'est maintenant ; sur une preuve enregistree, c'est la date du
    #: `--relire` qui l'a produite — et c'est elle qui dit si la preuve a vieilli.
    read_at: str = ""


def _version_of(text: str) -> str | None:
    trouve = VERSION_LINE.search(text or "")
    return trouve.group(1) if trouve else None


def _candidates(home: Path | None = None) -> list[Path]:
    racine = home or Path.home()
    trouves: list[Path] = []
    for motif in PUBLISHED_GLOBS:
        trouves.extend(racine.glob(motif))
    return trouves


def published(home: Path | None = None, now: str = "") -> Published | None:
    """Le cadre publie, lu sur le disque. `None` si aucun exemplaire n'est lisible.

    **Le plus recent fait foi**, par date de modification : le cache garde les
    revisions precedentes a cote, et lire la plus ancienne ferait declarer un
    ecart la ou il n'y en a pas. Un exemplaire dont la ligne de version ne se lit
    pas est ecarte — un cadre sans numero ne prouve rien sur un numero.
    """
    lisibles: list[tuple[float, Published]] = []
    for chemin in _candidates(home):
        try:
            texte = chemin.read_text(encoding="utf-8")
            stamp = chemin.stat().st_mtime
        except OSError:
            continue
        numero = _version_of(texte)
        if numero is None:
            continue
        lisibles.append(
            (
                stamp,
                Published(
                    version=numero,
                    sha256=hashlib.sha256(texte.encode("utf-8")).hexdigest(),
                    path=str(chemin),
                    read_at=now,
                ),
            )
        )
    if not lisibles:
        return None
    return max(lisibles, key=lambda item: item[0])[1]


def recorded(path: Path | None = None) -> Published | None:
    """La preuve enregistree, ou `None` si elle manque ou ne se lit pas.

    Une preuve illisible vaut une preuve absente : elle ne prouve rien, et la
    traiter autrement ferait passer un fichier casse pour une verification.
    """
    cible = path or PROOF_PATH
    try:
        charge = json.loads(cible.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return Published(
            version=str(charge["version"]),
            sha256=str(charge["sha256"]),
            path=str(charge.get("path", "")),
            read_at=str(charge.get("read_at", "")),
        )
    except (KeyError, TypeError):
        return None


def record(lu: Published, path: Path | None = None) -> Path:
    """Ecrit la preuve de lecture. Rend le chemin ecrit."""
    cible = path or PROOF_PATH
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(asdict(lu), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cible


def evidence(home: Path | None = None, path: Path | None = None) -> Published | None:
    """Ce sur quoi le garde se prononce : le cadre lu, sinon la preuve gardee.

    **L'ordre n'est pas negociable.** Un exemplaire lisible est la source ; la
    preuve enregistree n'est qu'un souvenir de lecture, et lui laisser la
    priorite rendrait le garde vert sur une machine qui a le vrai fichier sous
    les yeux et le contredit.
    """
    return published(home) or recorded(path)
