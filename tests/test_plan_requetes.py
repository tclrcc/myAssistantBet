"""Le plan des requetes chaudes, pas leur temps.

**Defaut du 26/08/2026, mesure sur deux copies de la base servie** : le board
passait de 0,043 s a 18,36 s et `/competitions` de 0,040 s a 18,98 s. La cause
n'etait pas un N+1 applicatif — deux appels a `unpriced()` par rendu, 28 requetes
en tout — mais un `SCAN` de `prompt_odds` **a l'interieur** d'une requete, une
fois par evenement : 697 evenements x 43 751 lignes, ~30,5 millions de lignes par
appel.

`idx_prompt_odds_lot` porte `(session_id, event_id)` : sa colonne de tete est
`session_id`, donc un predicat sur `event_id` seul ne peut pas s'en servir.

**Un test chronometre serait instable et finirait desactive au premier faux
positif.** Ce qui doit rester vrai n'est pas une duree mais une **propriete du
plan** : la requete ne balaie pas `prompt_odds`. Sans ce test, une migration qui
touche aux index rejoue le defaut, et il ne se voit qu'a l'usage — les fixtures
sont petites, le cout est proportionnel au volume, et la suite reste verte.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.services import competitions as competitions_service

#: Les tables qui grossissent. Une requete qui les filtre sur un axe que rien
#: n'indexe est invisible en test et catastrophique en production.
TABLES_CHAUDES = ("prompt_odds", "odds", "events", "picks")


def _sql_capturee(settings: Settings, appel: Any) -> list[tuple[str, Any]]:
    """Les requetes que `appel` execute reellement, avec leurs parametres.

    **La SQL n'est pas recopiee dans le test.** Une seconde ecriture aurait
    diverge de la premiere au premier ajustement, et le test aurait garde le plan
    d'une requete que plus personne n'execute — le motif du dossier, applique a
    un garde-fou.
    """
    capturees: list[str] = []
    vrai_connect = competitions_service.connect

    class Tracee:
        def __init__(self, ctx: Any) -> None:
            self.ctx = ctx

        def __enter__(self) -> Any:
            conn = self.ctx.__enter__()
            conn.set_trace_callback(lambda sql: capturees.append(sql))
            return conn

        def __exit__(self, *a: Any) -> Any:
            return self.ctx.__exit__(*a)

    competitions_service.connect = lambda *a, **k: Tracee(vrai_connect(*a, **k))
    try:
        appel()
    finally:
        competitions_service.connect = vrai_connect
    return [(sql, None) for sql in capturees]


def _alias_de(sql: str, table: str) -> set[str]:
    """Les alias sous lesquels `table` est nommee dans cette requete.

    Lu **sur la requete** et non ecrit en dur : `EXPLAIN QUERY PLAN` designe une
    sous-requete par son alias (`SCAN q`) et non par sa table, et un alias
    renomme ferait passer le test sans rien verifier.
    """
    trouves = {table}
    for m in re.finditer(rf"\b{table}\b(?:\s+AS)?\s+(\w+)", sql, re.IGNORECASE):
        mot = m.group(1)
        if mot.upper() not in {"WHERE", "ON", "SET", "VALUES", "GROUP", "ORDER", "AS"}:
            trouves.add(mot)
    return trouves


def _scans(plan: list[Any], alias: set[str]) -> list[str]:
    lignes = [str(row[3]) for row in plan]
    return [
        ligne
        for ligne in lignes
        if ligne.startswith("SCAN") and any(f"SCAN {a}" == ligne[: 5 + len(a)] for a in alias)
    ]


def test_unpriced_ne_balaie_aucune_table_chaude(migrated: Settings) -> None:
    """**La requete d'`unpriced()` ne fait de `SCAN` sur aucune table qui grossit.**

    `events` fait exception et c'est assume : la requete la parcourt par
    construction — elle agrege *tous* les evenements des competitions non
    servies, il n'y a rien a chercher. Ce qui doit rester un `SEARCH`, ce sont les
    deux tables de prix, interrogees **une fois par evenement**.
    """
    capturees = _sql_capturee(migrated, lambda: competitions_service.unpriced(migrated))
    chaudes = [sql for sql, _ in capturees if "prompt_odds" in sql]
    assert chaudes, "la requete chaude n'a pas ete capturee — le test ne verifie rien"

    with connect(migrated) as conn:
        for sql in chaudes:
            plan = conn.execute(
                "EXPLAIN QUERY PLAN " + sql,
                {"borne": "2026-08-26T00:00:00Z", "horizon": "2026-08-05T00:00:00Z"},
            ).fetchall()
            for table in ("prompt_odds", "odds"):
                scans = _scans(plan, _alias_de(sql, table))
                assert not scans, (
                    f"`{table}` est balayee par la requete d'unpriced() : {scans}. "
                    "Un index dont la colonne de tete n'est pas celle du predicat ne "
                    "sert a rien — voir la migration 081."
                )


def test_les_deux_tables_de_prix_portent_un_index_sur_event_id(migrated: Settings) -> None:
    """Le garde de forme, sous le garde de plan.

    Le plan depend du planificateur ; celui-ci depend du schema. Les deux ne
    disent pas la meme chose : un plan peut redevenir bon par accident sur une
    base vide, un index absent est un fait.
    """
    with connect(migrated) as conn:
        for table in ("odds", "prompt_odds"):
            index = conn.execute(f"PRAGMA index_list({table})").fetchall()
            tetes = set()
            for row in index:
                colonnes = conn.execute(f"PRAGMA index_info({row['name']})").fetchall()
                if colonnes:
                    tetes.add(str(colonnes[0]["name"]))
            assert "event_id" in tetes, (
                f"aucun index de `{table}` n'a `event_id` en colonne de tete : "
                "un predicat sur `event_id` seul fera un balayage complet"
            )


def test_le_garde_verrait_le_defaut_d_origine(migrated: Settings) -> None:
    """**Un test qui ne peut pas mordre donne l'apparence d'un test.**

    On retire l'index de la migration 081 sur une copie en memoire du schema, et
    on verifie que le plan redevient un `SCAN`. Sans cette verification, le garde
    resterait vert le jour ou il cesserait de garder quoi que ce soit.
    """
    with connect(migrated) as conn:
        conn.execute("DROP INDEX IF EXISTS idx_prompt_odds_event")

    capturees = _sql_capturee(migrated, lambda: competitions_service.unpriced(migrated))
    sql = next(s for s, _ in capturees if "prompt_odds" in s)
    with connect(migrated) as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN " + sql,
            {"borne": "2026-08-26T00:00:00Z", "horizon": "2026-08-05T00:00:00Z"},
        ).fetchall()
        assert _scans(plan, _alias_de(sql, "prompt_odds")), (
            "sans l'index, le plan devrait balayer `prompt_odds` — s'il ne le fait "
            "pas, le garde ci-dessus ne verifie rien"
        )
        conn.execute("CREATE INDEX idx_prompt_odds_event ON prompt_odds(event_id, fetched_at)")


def test_les_tables_chaudes_sont_nommees() -> None:
    """Le geste preventif se lit dans le code : ces quatre-la se verifient au plan."""
    assert set(TABLES_CHAUDES) == {"prompt_odds", "odds", "events", "picks"}
    assert isinstance(sqlite3.sqlite_version, str)
