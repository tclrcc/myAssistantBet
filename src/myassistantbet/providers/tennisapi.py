"""Client `tennis-api.com` (RapidAPI, editeur Matchstat).

C'est la source qui rouvre ce que la disparition de `JeffSackmann/tennis_atp`
avait ferme : la table de statistiques de service **avec ses denominateurs**.
Le lot 3 avait mesure le seul substitut connu — le Match Charting Project — et
conclu au rejet : premier quartile a 0 point de service cote ATP, 19,5 cote WTA,
pour un seuil de 400. Cette source-ci sert la meme table sur les memes joueurs.

## Ce qui est mesure, et qui contredit le dimensionnement du brief

Le brief annonce « ~196 joueurs, a 52 semaines et 10 matchs par page, se compte
en **centaines d'appels** ». Sonde du 17/08/2026 :

- `pageSize` est accepte jusqu'a **200**, plafonne silencieusement au-dela
  (500 et 1000 rendent 200 lignes). `PAGE_SIZE` vaut donc 100, et **une seule
  page couvre plus de 52 semaines pour tous les joueurs sondes** — un joueur
  joue 60 a 81 matchs par an, cent lignes remontent a quatorze a dix-neuf mois ;
- la reprise complete coute donc **un appel par joueur**, pas une dizaine.

## Le quota, lu dans les en-tetes et non sur une page de tarification

Le plan PRO annonce `x-ratelimit-requests-limit: 150000` et un
`x-ratelimit-requests-reset` de 2 677 861 s, soit **31 jours** : le quota est
mensuel. Les en-tetes font foi, comme partout ici.

**Aucun en-tete de debit n'existe** — ni `x-ratelimit-rate-limit`, ni `retry-after`.
Dix appels consecutifs passent en 2,25 s (4,4 req/s) sans un seul 429. Le debit
n'est donc pas borne par le fournisseur, et `REQUEST_INTERVAL` est une politesse
de notre cote, pas une contrainte relevee : ne pas la presenter comme telle.

**Une reponse servie par le cache de RapidAPI consomme quand meme** : les dix
appels identiques portaient `x-cached: HIT` et ont fait descendre le compteur de
dix. Il n'y a donc pas de repli gratuit a esperer d'une repetition.

## Trois pieges de la source, tous verifies

- **Cloudflare rend une erreur 1010 sans `User-Agent` de navigateur.** Meme
  precaution que Tennis Abstract, et pour la meme raison.
- **Le prefixe est `/tennis/v2/`**, `event/get/…` seul ne resout pas. Six appels
  ont ete perdus a le chercher au lot 4 avant de le lire dans la documentation.
- **`"success": true` sur un `result` vide.** C'est le defaut caracteristique du
  projet **dans la source** : le vide y a la meme sortie que la donnee. Aucun
  appelant ne doit le lire comme une absence — voir `services/serve_stats.py`.

## Ce qui ne sera jamais demande, et c'est ecrit dans le code

`FORBIDDEN` porte les familles d'endpoints interdites d'ingestion : toute cote de
bookmaker, toute prediction, toute selection. L'editeur alimente avec cette meme
API deux services commerciaux de pronostics ; les ingerer rendrait le residu au
prix de cette application **ininterpretable**, puisqu'il mesurerait un melange de
deux analyses.

Le plan PRO ne les sert pas aujourd'hui. L'interdit est ecrit quand meme, et
leve une exception plutot que de journaliser : le jour ou un plan les servirait,
un appel distrait ne doit pas pouvoir aboutir. C'est la meme regle que
`ODDS_COLUMNS` chez `tennisdata` — la barriere se pose en amont du parsing, pour
que la donnee ne puisse pas entrer, et non au rendu, ou elle pourrait ressortir.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import (
    BaseHTTPClient,
    ProviderError,
    ProviderResponse,
    archive_response,
    record_api_usage,
)

logger = logging.getLogger(__name__)

PROVIDER = "tennisapi"
HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE_URL = f"https://{HOST}"
PREFIX = "/tennis/v2"

#: Sans lui, Cloudflare rend une **erreur 1010** et non un 403 : le message ne
#: nomme pas la cause, et l'appel a l'air d'un refus d'authentification.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

#: Taille de page demandee. Le plafond mesure est 200 ; 100 suffit a couvrir plus
#: de 52 semaines pour tout joueur sonde, et demander le double doublerait le
#: volume transfere sans ajouter une ligne a la fenetre utile.
PAGE_SIZE = 100
PAGE_SIZE_MAX = 200

#: Circuits acceptes par l'endpoint de recherche.
TOURS = ("atp", "wta", "itf")

#: Les familles d'appel, **declarees par l'appelant et jamais derivees du chemin**.
#:
#: Le premier jet les redecoupait a partir de l'URL, et un test l'a attrape :
#: `profile/{nom}/matches-played` porte son segment variable au **milieu** quand
#: `profile/search/{nom}/{tour}` le porte a l'avant-dernier rang. Aucune regle de
#: position ne separe les deux, et « premier segment plus dernier segment »
#: rangeait la recherche sous `profile/atp`.
#:
#: C'est la regle du projet appliquee a une URL : **l'appelant tient deja
#: l'identifiant**, le redecouper revient a le deviner. `api_usage.endpoint`
#: porte donc la famille, et le chemin complet vit dans l'archive.
SEARCH = "profile/search"
MATCHES_PLAYED = "profile/matches-played"
EVENT = "event/get"
FIXTURES = "fixtures"
FAMILIES = (SEARCH, MATCHES_PLAYED, EVENT, FIXTURES)

#: **Interdits d'ingestion, definitivement.** Le test cite cette liste ; elle
#: existe pour qu'un endpoint de pronostic ajoute par le fournisseur ne se glisse
#: pas dans un appel a la faveur d'une relecture distraite.
FORBIDDEN = (
    "odds",
    "prediction",
    "predictions",
    "value-bet",
    "value-bets",
    "valuebets",
    "top-matches",
    "topmatches",
    "picks",
    "tips",
)


class ForbiddenEndpoint(RuntimeError):
    """Un endpoint de cote ou de pronostic a ete demande.

    **Une exception et non un rejet journalise.** Un rejet dit « ceci s'est
    perdu » ; ici rien ne doit se perdre, parce que rien ne doit partir. Le plan
    servi ne rend pas ces endpoints, donc cette exception ne peut se declencher
    qu'apres un changement de plan ou une faute de frappe — les deux cas ou l'on
    veut un arret franc plutot qu'une ligne dans un journal que personne ne lit.
    """


def check_path(path: str) -> None:
    """Refuse un chemin qui vise une cote ou un pronostic.

    Le controle porte sur les **segments** du chemin et non sur la chaine
    entiere : `"odds" in path` refuserait `/profile/Todds Martin/…`, et un
    interdit qui attrape des cas legitimes finit par etre desactive.
    """
    segments = {segment.lower() for segment in path.split("/") if segment}
    interdits = sorted(segments & set(FORBIDDEN))
    if interdits:
        raise ForbiddenEndpoint(
            f"endpoint interdit d'ingestion : {path} (segment {interdits[0]!r}). "
            "Les cotes et les pronostics de ce fournisseur alimentent deux services "
            "commerciaux ; les ingerer rendrait le residu au prix ininterpretable."
        )


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class TennisAPIClient(BaseHTTPClient):
    """Acces en lecture a `tennis-api.com`. Quota mensuel, lu dans les en-tetes."""

    provider_name = PROVIDER
    base_url = BASE_URL

    #: Le fournisseur ne borne pas le debit, mais il n'a pas non plus a encaisser
    #: une reprise complete a pleine vitesse. Un delai plancher sur les
    #: tentatives de repli suffit ; il ne pretend pas traduire une limite.
    payload_retry_delay = 2.0

    def _headers(self) -> dict[str, str]:
        return {
            "x-rapidapi-key": self._settings.rapidapi_key,
            "x-rapidapi-host": HOST,
            "User-Agent": USER_AGENT,
        }

    def _quota_reading(self, headers: dict[str, str]) -> int | None:
        """Appels restants sur le mois, lus dans l'en-tete du fournisseur.

        **Une tentative echouee consomme ici** — la facturation porte sur
        l'appel, pas sur ce qu'il rend, et une reponse servie par le cache de
        RapidAPI descend le compteur comme les autres. Lire le compteur sur les
        tentatives de repli est donc le seul moyen de ne pas sous-compter une
        rafale qui retente.
        """
        return _as_int(headers.get("x-ratelimit-requests-remaining"))

    async def get(
        self, path: str, family: str, params: dict[str, Any] | None = None
    ) -> ProviderResponse:
        """GET sur un chemin de l'API, quota comptabilise sous `family`.

        Le chemin est donne **sans** le prefixe `/tennis/v2`. Il passe par
        `check_path` avant tout, y compris avant le cache de developpement : un
        endroit unique ou l'interdit s'applique vaut mieux que deux.
        """
        check_path(path)
        if family not in FAMILIES:
            raise ProviderError(PROVIDER, path, f"famille d'appel inconnue : {family!r}")
        if not self._settings.rapidapi_key:
            raise ProviderError(PROVIDER, path, "aucune cle RapidAPI configuree")

        full = f"{PREFIX}{path if path.startswith('/') else '/' + path}"
        try:
            response = await self._get(full, params=params or {}, headers=self._headers())
        except ProviderError as exc:
            # **Une reponse en erreur s'archive aussi.** Ne garder que ce qui se
            # lit reproduirait ici le silence qu'on supprime partout ailleurs :
            # c'est precisement le cas ou le parsing echoue qu'on veut pouvoir
            # rejouer. `ProviderError` porte le statut et le debut du corps.
            archive_response(
                PROVIDER,
                family,
                full,
                str(exc),
                params=params,
                http_status=exc.status_code,
                settings=self._settings,
            )
            raise
        self._account(family, full, response)
        response.archive_id = archive_response(
            PROVIDER,
            family,
            full,
            response.data,
            params=params,
            http_status=200,
            quota_remaining=self._quota_reading(response.headers),
            settings=self._settings,
        )
        return response

    def _account(self, family: str, path: str, response: ProviderResponse) -> None:
        """Trace l'appel. **Un appel vaut un credit**, quel que soit ce qu'il rend.

        C'est la difference avec The Odds API, qui facture au marche servi : ici
        une reponse vide se paie comme une reponse pleine, et un `result` vide
        n'est donc pas un appel gratuit qu'on pourrait retenter sans compter.
        """
        if response.from_cache:
            logger.info("%s %s servi par le cache dev — cout 0", PROVIDER, path)
            return
        remaining = self._quota_reading(response.headers)
        record_api_usage(PROVIDER, family, 1, remaining, self._settings)
        logger.info(
            "%s %s — 1 appel, %s restants, %d ms",
            PROVIDER,
            path,
            remaining if remaining is not None else "?",
            response.duration_ms,
        )

    # -- Endpoints servis ---------------------------------------------------

    async def search_player(self, name: str, tour: str) -> list[str]:
        """Graphies canoniques correspondant a un nom, sur un circuit.

        Rend une **liste**, parfois de plusieurs elements — « Alexander Zverev »
        et « Alexander Zverev Sr » sortent du meme appel. Trancher est le travail
        de `serve_stats.resolve`, pas celui d'un client qui ne connait rien du
        metier.
        """
        if tour not in TOURS:
            raise ProviderError(PROVIDER, "search", f"circuit inconnu : {tour}")
        response = await self.search_raw(name, tour)
        data = response.data
        return [str(item) for item in data] if isinstance(data, list) else []

    async def search_raw(self, name: str, tour: str) -> ProviderResponse:
        """Idem, reponse entiere — l'archive a besoin du brut, pas du resultat."""
        return await self.get(f"/profile/search/{name}/{tour}", SEARCH)

    async def matches_played(self, name: str, page: int = 1) -> ProviderResponse:
        """Matchs joues par un joueur, avec la table de service des **deux** camps.

        `page` commence a 1. `singlesCount` donne le total historique, ce qui
        permet de savoir s'il reste des pages sans en demander une de plus.
        """
        return await self.get(
            f"/profile/{name}/matches-played",
            MATCHES_PLAYED,
            params={"pageSize": PAGE_SIZE, "page": max(1, int(page))},
        )

    async def fixtures(self, tour: str, day: str, page: int = 1) -> ProviderResponse:
        """Rencontres programmees un jour donne, sur un circuit.

        **La reponse est paginee et le defaut est 10.** Un premier releve fait
        sans `pageSize` a rendu dix lignes et fait croire a un flux minuscule ;
        cote WTA, `hasNextPage` etait encore vrai a cent. L'appelant doit
        parcourir les pages, et `PAGE_SIZE` (100) suffit a tenir une journee de
        Grand Chelem en une ou deux.

        **Ce n'est pas un calendrier : c'est l'ordre du jour publie.** Mesure du
        24/08/2026 — 12 lignes le 20/08, 72 le 23/08, 98 le 24/08, 62 le 25/08,
        puis **zero** le 26/08 et au-dela, tous tournois confondus. Le passe
        repond, l'avenir s'arrete a J+1 parce que les tournois publient la veille.
        Un import qui voudrait tout un tableau de qualification doit donc tourner
        **chaque jour** ; il ne peut pas rattraper quatre jours d'avance, et il
        peut en revanche rattraper un jour manque.
        """
        if tour not in TOURS:
            raise ProviderError(PROVIDER, "fixtures", f"circuit inconnu : {tour}")
        return await self.get(
            f"/{tour}/fixtures/{day}",
            FIXTURES,
            params={"pageSize": PAGE_SIZE, "page": max(1, int(page))},
        )

    async def event(self, first: str, second: str, day: str) -> ProviderResponse:
        """Rencontre entre deux joueurs a une date. **L'endpoint est positionnel.**

        L'ordre des joueurs compte et ne correspond pas a celui de la base : il
        faut essayer les deux avant de conclure a une absence. Ce client rend un
        appel ; c'est `serve_stats` qui tente les deux ordres, parce que
        « essayer l'autre ordre puis la veille et le lendemain » est une regle de
        collecte et non un detail de transport.
        """
        return await self.get(f"/extend/api/event/get/{first}/{second}/{day}", EVENT)
