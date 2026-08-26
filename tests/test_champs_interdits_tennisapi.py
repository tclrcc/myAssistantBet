"""Un champ interdit entre par une porte autorisee.

**Mesure du 26/08/2026, sur les 106 701 matchs archives de
`profile/matches-played`** — un endpoint permis, appele quotidiennement :

  * `playerN.odd`, une **cote de bookmaker**, sur **49,2 %** des matchs ;
  * `poll_vote`, un **sondage de pronostic**, sur **100 %**.

`FORBIDDEN` garde les **chemins**, et son commentaire annonce une barriere « en
amont du parsing, pour que la donnee ne puisse pas entrer ». Elle ne portait pas
sur les champs. L'admission se prononce donc par champ, comme
`tennisdata.ODDS_COLUMNS` le fait deja pour les huit colonnes de cloture.
"""

from __future__ import annotations

import inspect
from typing import Any

from myassistantbet.providers import tennisapi
from myassistantbet.services import serve_stats


def _match(odd: str, poll: str) -> dict[str, Any]:
    """Un match reel, reduit — avec ses deux champs interdits renseignes."""
    stats = {
        "firstServe": 33,
        "firstServeOf": 58,
        "aces": 6,
        "doubleFaults": 3,
        "winningOnFirstServe": 28,
        "winningOnFirstServeOf": 33,
        "winningOnSecondServe": 10,
        "winningOnSecondServeOf": 25,
        "breakPointsConverted": 4,
        "breakPointsConvertedOf": 4,
        # L'invariant de la source : service gagne + retour gagne = total.
        # 28 + 10 + (50 - 28 - 10) = 50, sinon `consistent` ecarte la ligne et le
        # test passerait sur un match jamais lu.
        "totalPointsWon": 50,
    }
    adverse = dict(stats) | {"totalPointsWon": 45, "firstServeOf": 50}
    return {
        "date": "2026-08-24T22:10:00.000Z",
        "result": "6-1 6-3",
        "roundId": 1,
        "tournamentId": 21349,
        "poll_vote": poll,
        "tournament": {"name": "US Open", "court": {"name": "Hard"}},
        "player1": {"name": "Billy Harris", "odd": odd, "stats": stats},
        "player2": {"name": "Gonzalo Bueno", "odd": "2.85", "stats": adverse},
    }


def test_ni_la_cote_ni_le_sondage_n_atteignent_une_ligne_de_service() -> None:
    """**La ligne lue ne porte aucune des deux valeurs**, sous aucun champ.

    Le controle porte sur les **valeurs** et non sur les noms de champs : un
    champ recopie sous un autre nom passerait un controle de noms.
    """
    lignes, ecartes = serve_stats.parse_matches_played(
        {"singles": [_match(odd="1.37", poll="400")]}, "Billy Harris"
    )
    assert len(lignes) == 1 and ecartes == 0, "le match est bien lu par ailleurs"

    valeurs = {
        str(getattr(lignes[0], champ.name)) for champ in lignes[0].__dataclass_fields__.values()
    }
    assert "1.37" not in valeurs, "une cote de bookmaker a atteint la ligne"
    assert "400" not in valeurs, "un sondage de pronostic a atteint la ligne"


def test_la_liste_blanche_et_la_liste_interdite_sont_disjointes() -> None:
    """Une liste blanche qui contiendrait un interdit ne garderait rien."""
    assert not set(tennisapi.READ_FIELDS) & set(tennisapi.FORBIDDEN_FIELDS)


def test_le_lecteur_ne_nomme_aucun_champ_interdit() -> None:
    """**La barriere se pose en amont du parsing**, donc elle se lit sur le lecteur.

    Un test sur la sortie ne verrait pas un champ lu puis journalise, ou lu puis
    range dans une charge utile. Celui-ci echoue des qu'un module de lecture
    **nomme** l'un des deux — c'est le geste qu'on veut rendre impossible par
    distraction, pas la valeur qu'on veut filtrer.
    """
    source = inspect.getsource(serve_stats)
    for champ in tennisapi.FORBIDDEN_FIELDS:
        assert f'"{champ}"' not in source and f"'{champ}'" not in source, (
            f"le lecteur nomme le champ interdit {champ!r}"
        )


def test_le_lecteur_ne_lit_que_des_champs_declares() -> None:
    """Tout champ de la charge utile nomme par le lecteur figure dans la liste blanche.

    C'est ce qui rend la liste vivante plutot que decorative : elle ne peut pas
    prendre du retard sur le lecteur sans qu'un test le dise. Et la reciproque
    n'est pas testee — une liste blanche a le droit d'annoncer un champ qu'on ne
    lit pas encore.
    """
    source = inspect.getsource(serve_stats.parse_matches_played)
    lus = {morceau.split('"')[0] for morceau in source.split('.get("')[1:]}
    inconnus = lus - set(tennisapi.READ_FIELDS)
    assert not inconnus, f"champs lus et non declares : {sorted(inconnus)}"
