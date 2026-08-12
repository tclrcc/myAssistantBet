"""Meteo du lieu et **alerte officielle en vigueur** au coup d'envoi.

Ce qui a compte, sur cinq sessions reelles, n'etait pas la temperature : c'etait
l'**alerte**. « 30 C, orages 80 % » situe une soiree ; « alerte aux orages
violents cet apres-midi » a change une section entiere d'analyse, parce qu'elle
disait que la session pouvait ne pas se jouer. L'ordre de ce module suit cette
mesure — l'alerte d'abord, les chiffres ensuite.

Trois endpoints, tous **gratuits et sans cle** :

- `geocoding-api.open-meteo.com` : une ville et un pays vers des coordonnees.
  Rend aussi le **fuseau** du lieu, ce qui recoupe gratuitement une saisie
  manuelle de `competitions.timezone`.
- `api.open-meteo.com` : la prevision horaire. Ce sont les chiffres.
- `api.weather.gov` : les alertes officielles du National Weather Service.
  **L'emetteur est nomme dans la charge utile** (« NWS Wilmington OH ») : c'est
  l'instance, donc une source de niveau 1, et le bloc le recopie tel quel.

Aucun quota, donc **rien n'est ecrit dans `api_usage`**, qui ne comptabilise que
des credits — meme regle que l'Elo tennis et l'historique des matchs.

**Deux obligations, tenues ici et pas ailleurs.** Le NWS exige un `User-Agent`
qui identifie l'appelant, avec un contact ; Open-Meteo publie ses donnees sous
CC-BY 4.0, donc l'attribution est due — c'est pourquoi le nom de la source est
rendu dans la ligne et non seulement dans les logs.

Sur les `robots.txt`, et c'est une decision : `api.weather.gov` et
`api.open-meteo.com` servent tous deux `User-agent: * / Disallow: /`. Ce sont
des **APIs publiques documentees**, dont les conditions autorisent explicitement
l'acces programmatique — « all of the information presented via the API is
intended to be open data, free to use for any purpose » cote NWS. Le
`Disallow: /` d'un hote d'API ecarte les robots d'indexation, il ne s'adresse pas
a un client d'API, et ni l'un ni l'autre ne nomme d'agent ni ne porte de reserve
de droits. C'est ce qui les distingue d'`atptour.com`, qui interdit ClaudeBot
nommement et porte un `Content-Signal: ai-train=no` — refuse, et qui doit le
rester.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseHTTPClient, ProviderError

logger = logging.getLogger(__name__)

PROVIDER = "weather"

GEOCODING_URL = "https://geocoding-api.open-meteo.com"
FORECAST_URL = "https://api.open-meteo.com"
NWS_URL = "https://api.weather.gov"

#: Grandeurs demandees a la prevision horaire. Quatre et pas dix : ce qui change
#: la lecture d'un match est la pluie, le vent et la chaleur — l'humidite et la
#: pression ne se traduisent en aucun angle.
HOURLY = "temperature_2m,precipitation_probability,wind_gusts_10m,weather_code"

#: Le NWS **exige** un `User-Agent` qui identifie l'appelant. Le contact vient
#: des reglages : le coder en dur ferait porter a un autre les appels d'une
#: installation qui n'est pas la sienne.
USER_AGENT = "myAssistantBet/1.0 (outil personnel d'analyse sportive; {contact})"


class WeatherClient(BaseHTTPClient):
    """Trois sources publiques, aucune cle, aucun quota."""

    provider = PROVIDER

    def _headers(self) -> dict[str, str]:
        contact = (self.settings.weather_contact or "contact non renseigne").strip()
        return {"User-Agent": USER_AGENT.format(contact=contact), "Accept": "application/json"}

    async def coordinates(self, city: str, country: str | None = None) -> dict[str, Any] | None:
        """Coordonnees d'une ville, **verifiees contre son pays**.

        Le geocodage rend plusieurs homonymes — « Springfield » en a des
        dizaines. Le pays departage, et **en cas de doute il n'y a pas de
        ligne** : donner la meteo de la mauvaise ville serait pire que ne pas la
        donner, et c'est le genre d'erreur qui ne se voit pas.

        Rend aussi le fuseau du lieu, que le fournisseur publie dans la meme
        reponse — de quoi recouper une saisie manuelle sans un appel de plus.
        """
        response = await self._get(
            f"{GEOCODING_URL}/v1/search",
            params={"name": city, "count": 10, "language": "en", "format": "json"},
            headers=self._headers(),
        )
        results = (response.data or {}).get("results") or []
        if not results:
            return None
        if country:
            wanted = country.strip().casefold()
            results = [
                row
                for row in results
                if wanted in {str(row.get("country") or "").casefold(), _code(row)}
            ]
        if not results:
            return None
        # Le plus peuple des homonymes restants : un stade est dans une ville, pas
        # dans un hameau du meme nom. C'est le seul arbitrage du module, et il
        # n'intervient qu'apres que le pays a deja tranche.
        best = max(results, key=lambda row: int(row.get("population") or 0))
        return {
            "latitude": best.get("latitude"),
            "longitude": best.get("longitude"),
            "timezone": best.get("timezone"),
            "name": best.get("name"),
            "country": best.get("country"),
        }

    async def forecast(self, latitude: float, longitude: float) -> dict[str, Any]:
        """Prevision horaire sur deux jours, en UTC.

        En UTC parce que le coup d'envoi est stocke en UTC : demander au
        fournisseur de convertir ajouterait un fuseau a rapprocher d'un autre.
        """
        response = await self._get(
            f"{FORECAST_URL}/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": HOURLY,
                "forecast_days": 2,
                "timezone": "UTC",
            },
            headers=self._headers(),
        )
        return response.data or {}

    async def alerts(self, latitude: float, longitude: float) -> list[dict[str, Any]]:
        """Alertes officielles en vigueur sur ce point, chez le NWS.

        **Etats-Unis seulement**, et c'est la limite du module : chaque pays a
        son service national, et il n'existe pas d'agregateur qui soit lui-meme
        l'instance. Le service appelant nomme donc les pays qu'il n'interroge
        pas, plutot que de laisser un silence passer pour une absence d'alerte.
        """
        response = await self._get(
            f"{NWS_URL}/alerts/active",
            params={"point": f"{latitude:.4f},{longitude:.4f}"},
            headers=self._headers(),
        )
        features = (response.data or {}).get("features") or []
        return [feature.get("properties") or {} for feature in features]


def _code(row: dict[str, Any]) -> str:
    return str(row.get("country_code") or "").casefold()


__all__ = ["NWS_URL", "PROVIDER", "ProviderError", "WeatherClient"]
