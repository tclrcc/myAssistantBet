"""Une source morte repond encore. Ce module date son **contenu**.

**La panne redoutee n'a pas eu lieu, et c'est ce qui rend ce module
necessaire.** Le lot 3 a etabli que les depots Sackmann sont supprimes ; la
mesure du 17/08/2026 dit qu'aucune ligne tennis n'en dependait. Les six URL
amont repondent 200, les collectes ont tourne ce matin, le dernier match connu
est du 14/08.

Rien n'etait casse — et **rien ne l'aurait dit**. C'est le defaut
caracteristique du projet, une septieme fois : un depot supprime rend 404 et se
voit ; un fichier hebdomadaire qui cesse d'etre publie rend 200, le meme
classeur, indefiniment. La sortie de l'echec est identique a celle du cas
ordinaire.

L'instrumentation existante ne pouvait pas l'attraper : `tennis_history_state`
date la **tentative** et compte les lignes. La tentative avance tous les matins
quoi qu'il arrive.

## Deux mecanismes, et il faut les deux

- **La stagnation** — la source ne bouge plus. Elle se mesure entre `moved_at`
  et `checked_at`, jamais entre deux executions consecutives : une source
  relancee trois fois dans la journee ferait sinon trois comparaisons de moins
  de 48 h et ne stagnerait jamais, quel que soit son age reel. Elle se
  journalise dans `ingestion_rejects`, la ou tout ce qui se perd se journalise
  deja.
- **L'escalade** — ce que le bloc en dit. La stagnation parle a qui exploite ;
  l'escalade parle a qui analyse, et elle doit apparaitre dans le bloc lui-meme
  parce que c'est la que la donnee est lue.

## Les seuils, et pourquoi ils sont ceux-la

Le fichier de resultats est **hebdomadaire et publie apres coup** : trois a
quatre jours de retard sont le regime normal, et `HISTORY_LATE_DAYS` vaut deja 2
pour cette raison. Un seuil sous la semaine crierait donc tous les jours.

- **7 jours** : au-dela, deux publications ont ete manquees. Une seule peut
  l'etre pour une raison benigne ; deux, non.
- **21 jours** : trois semaines sans publication sur une source hebdomadaire.
  A ce stade la question n'est plus « est-elle en retard » mais « existe-t-elle
  encore », et la ligne cesse d'etre neutre.

**L'application decide, jamais le modele.** Le bloc porte un etat calcule et non
une date a interpreter : le gabarit dit ce que chaque etat autorise a conclure,
et le calcul dit lequel s'applique.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .ingestion import SOURCE, SOURCE_FIGEE, Reject

#: Les sources datees. **Une enumeration** : le compte se fait dessus, et deux
#: orthographes du meme nom feraient deux lignes qui ne se rapprochent plus.
TENNISDATA = "tennisdata"
TENNISABSTRACT = "tennisabstract"
SOURCES = (TENNISDATA, TENNISABSTRACT)

#: Heures sans que `source_as_of` avance au-dela desquelles on parle de
#: stagnation. Deux jours : une publication hebdomadaire manquee d'un jour est
#: le cas ordinaire, manquee de deux ne l'est plus.
STAGNATION_HOURS = 48

#: Jours d'age du contenu a partir desquels la ligne cesse d'etre neutre, puis
#: cesse d'etre lisible comme de la forme.
STALE_DAYS = 7
FROZEN_DAYS = 21

FRESH = "fraiche"
STALE = "en retard"
FROZEN = "figee"


@dataclass(frozen=True)
class State:
    """L'age du contenu d'une source, et ce que le bloc doit en dire."""

    source: str
    scope: str
    source_as_of: str = ""
    checked_at: str = ""
    moved_at: str = ""
    age_days: int | None = None

    @property
    def level(self) -> str:
        """`fraiche`, `en retard` ou `figee`. **Calcule, jamais interprete.**"""
        if self.age_days is None:
            return FRESH
        if self.age_days > FROZEN_DAYS:
            return FROZEN
        return STALE if self.age_days > STALE_DAYS else FRESH

    @property
    def note(self) -> str:
        """La mention a poser sur la ligne `Fraicheur`. Vide quand tout va bien.

        **Rien sous le seuil**, et c'est la regle du projet : une ligne qui
        annoncerait « source a jour » sur chaque bloc cesserait d'informer au
        bout d'un lot, et l'exception qu'il faut lire s'y noierait — le meme
        defaut que `A relever` sur vingt-quatre blocs.
        """
        if self.level == FRESH or not self.source_as_of:
            return ""
        jour = _short(self.source_as_of)
        if self.level == FROZEN:
            return (
                f"SOURCE FIGEE depuis le {jour} — Forme, Usure, Profil, Marge et "
                "Niveau adv. decrivent un etat perime, ne les traite pas comme la "
                "forme du moment"
            )
        return f"source non rafraichie depuis le {jour}"


def _short(iso: str) -> str:
    """« 2026-08-14 » devient « 14/08 ». Meme forme que partout dans le bloc."""
    parts = str(iso)[:10].split("-")
    return f"{parts[2]}/{parts[1]}" if len(parts) == 3 else str(iso)[:10]


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def record(
    source: str,
    scope: str,
    source_as_of: str,
    settings: Settings | None = None,
    now: str | None = None,
) -> Reject | None:
    """Date le **contenu** obtenu. Rend un rejet quand la source stagne.

    `now` va jusqu'a l'ecriture, comme dans `dossier.store` et `elo.store` : la
    stagnation compare une date de mouvement a une date de controle, et les
    prendre sur deux horloges differentes rend le calcul faux, donc intestable.

    **Une source qui recule ne compte pas comme un mouvement.** Un fichier
    republie ampute — Sackmann le pratiquait, et le brief du lot 3 le notait —
    ferait sinon repartir le compteur a zero en perdant des donnees, ce qui est
    l'inverse de ce qu'un temoin de fraicheur doit dire.
    """
    settings = settings or get_settings()
    moment = now or utcnow()
    valeur = str(source_as_of or "")[:10]
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT source_as_of, checked_at, moved_at FROM source_freshness "
            " WHERE source = ? AND scope = ?",
            (source, scope),
        ).fetchone()
        precedent = str(row["source_as_of"] or "") if row else ""
        # Le premier passage ne peut rien conclure : on ne dit pas qu'une source
        # ne bouge plus quand on ne l'a vue qu'une fois. `moved_at` prend donc
        # l'instant du premier releve, et le compte part de la.
        avance = valeur > precedent
        moved = moment if (avance or row is None) else str(row["moved_at"] or moment)
        conn.execute(
            "INSERT INTO source_freshness (source, scope, source_as_of, checked_at, moved_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(source, scope) DO UPDATE SET "
            "  source_as_of = excluded.source_as_of, checked_at = excluded.checked_at, "
            "  moved_at = excluded.moved_at",
            (source, scope, max(valeur, precedent), moment, moved),
        )
    if row is None or avance:
        return None
    depuis, controle = _parse(moved), _parse(moment)
    if depuis is None or controle is None:
        return None
    heures = (controle - depuis).total_seconds() / 3600
    if heures < STAGNATION_HOURS:
        return None
    return Reject(
        block_type=SOURCE,
        reason=SOURCE_FIGEE,
        detail=(
            f"{source} / {scope} : la source répond mais n'avance plus — dernier fait "
            f"obtenu le {_short(precedent)}, inchangé depuis {int(heures)} h. "
            "Une source morte répond encore ; c'est son contenu qui cesse de bouger."
        ),
        payload=f"{source}/{scope} as_of={precedent} moved_at={moved}",
    )


def state(
    source: str,
    scope: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> State:
    """L'age du contenu d'une source, tel que le bloc doit le rendre."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT source_as_of, checked_at, moved_at FROM source_freshness "
            " WHERE source = ? AND scope = ?",
            (source, scope),
        ).fetchone()
    if row is None or not row["source_as_of"]:
        return State(source=source, scope=scope)
    quand = _parse(str(row["source_as_of"]))
    maintenant = now or datetime.now(UTC)
    age = None
    if quand is not None:
        if quand.tzinfo is None:
            quand = quand.replace(tzinfo=UTC)
        age = (maintenant - quand) // timedelta(days=1)
    return State(
        source=source,
        scope=scope,
        source_as_of=str(row["source_as_of"]),
        checked_at=str(row["checked_at"] or ""),
        moved_at=str(row["moved_at"] or ""),
        age_days=int(age) if age is not None else None,
    )


def note_for(collected: date | str, now: datetime | None = None) -> str:
    """La mention d'escalade pour une donnee arretee a cette date.

    **Une fonction pure de la date**, et c'est voulu : `collected` est deja le
    dernier match obtenu, c'est-a-dire exactement `source_as_of`. Relire la
    table ici ajouterait une lecture pour retrouver une valeur qu'on tient, et
    ferait dependre le rendu d'un enregistrement qui peut manquer sur une base
    neuve — le bloc perdrait sa mention pour une raison qui n'a rien a voir
    avec la fraicheur.

    La table sert l'autre moitie, celle que la date seule ne peut pas donner :
    **une source qui n'avance plus**. Un fichier fige au 14/08 et un fichier
    publie hier qui s'arrete au 14/08 portent la meme date et ne sont pas le
    meme probleme.
    """
    return State(
        source=TENNISDATA,
        scope="",
        source_as_of=str(collected),
        age_days=_age(str(collected), now),
    ).note


def _age(as_of: str, now: datetime | None = None) -> int | None:
    """L'age en jours entiers ecoules. Rien si la date est illisible.

    Jours **entierement ecoules** et jamais arrondis, meme regle que le repos au
    tennis : un plancher est monotone et se lit comme la phrase le dit.
    """
    quand = _parse(as_of)
    if quand is None:
        return None
    if quand.tzinfo is None:
        quand = quand.replace(tzinfo=UTC)
    return int(((now or datetime.now(UTC)) - quand) // timedelta(days=1))


def worst(scopes: set[str], settings: Settings | None = None, now: datetime | None = None) -> State:
    """L'etat **le plus degrade** parmi les circuits d'un bloc.

    Un bloc de tennis peut porter deux joueurs de deux circuits, et l'ATP peut
    etre a jour quand la WTA ne l'est pas — les deux fichiers vivent leur vie.
    Rendre le meilleur des deux tairait exactement le cas qu'on veut voir.
    """
    etats = [state(TENNISDATA, scope, settings, now) for scope in sorted(scopes)]
    connus = [etat for etat in etats if etat.source_as_of]
    if not connus:
        return State(source=TENNISDATA, scope="")
    return max(connus, key=lambda etat: etat.age_days or 0)
