"""Ligne « Meteo » du bloc : l'alerte officielle d'abord, les chiffres ensuite.

Mesure qui fixe cet ordre, sur cinq sessions reelles : la temperature n'a jamais
rien change ; l'**alerte en vigueur** a change une section entiere, deux fois,
parce qu'elle disait que la rencontre pouvait ne pas se jouer. Un chiffre
interessant et un fait bloquant ne se lisent pas au meme rang.

Deux temps separes, comme `context.py` et `dossier.py` : `refresh_event()`
appelle et persiste, `lines()` relit. Regenerer un prompt ne declenche aucun
appel.

**Les pays dont l'alerte n'est pas interrogee sont nommes.** C'est la regle
centrale du module, et elle vient d'un defaut deja paye : `Absents : donnees non
disponibles` rendait « aucun absent » sur des competitions non couvertes, soit
l'affirmation inverse de la verite. Un silence qui ressemble a une information
est pire qu'une absence declaree — ici, ne rien dire ferait lire « aucune alerte »
la ou personne n'a regarde.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings, get_settings
from ..providers.base import ProviderError
from ..providers.weather import WeatherClient
from .context import load, store

logger = logging.getLogger(__name__)

#: Type de contexte, dans la meme table que le reste du bloc.
KIND_WEATHER = "weather"

#: Peremption d'une prevision. Trois heures : au-dela, une prevision d'orage a
#: eu le temps de se preciser ou de se dissiper, et c'est justement le cas ou
#: elle decide. En dessous, la relire couterait un appel pour le meme chiffre.
TTL_HOURS = 3

#: Pays dont le service national d'alertes est interroge, par code ISO a deux
#: lettres. **Un seul aujourd'hui**, et c'est une limite assumee : chaque pays a
#: son service, et il n'existe pas d'agregateur qui soit lui-meme l'instance —
#: MeteoAlarm agrege l'Europe mais n'emet rien. Le nom de l'emetteur reel est
#: recopie de la charge utile, jamais devine.
ALERT_SOURCES = {"US": "NWS"}

#: Marqueur d'une alerte en vigueur, en tete de la ligne. **Constante et non
#: litteral** : le prompt croise l'alerte avec le deplacement des horaires, et
#: deux ecritures du meme mot auraient fini par ne plus se reconnaitre — meme
#: regle que `NEUTRAL_MARK`, relu par la fiche de recherche.
ALERT_MARK = "ALERTE"

#: Codes WMO qui decrivent un temps orageux. Ce sont les seuls que la ligne
#: nomme : « nuageux » n'a jamais change une lecture de match.
WMO_STORM = {95: "orage", 96: "orage grelant", 99: "orage grelant fort"}
WMO_RAIN = {
    61: "pluie faible",
    63: "pluie",
    65: "pluie forte",
    80: "averses",
    81: "averses",
    82: "averses fortes",
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _fresh(payload: dict[str, Any], now: datetime) -> bool:
    """Une prevision datee d'il y a plus de `TTL_HOURS` vaut perimee.

    Une date de releve illisible vaut perimee aussi : mieux vaut un appel de trop
    qu'une prevision dont on ne sait plus quand elle a ete prise — meme regle que
    la peremption du dossier d'equipe.
    """
    releve = _parse(payload.get("fetched_at"))
    return releve is not None and now - releve < timedelta(hours=TTL_HOURS)


async def refresh_event(
    client: WeatherClient,
    event_id: int,
    city: str,
    country: str | None,
    commence_time: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> bool:
    """Recupere et persiste la meteo d'un match. Rend vrai si elle a change.

    Trois appels au plus, tous gratuits : geocodage, prevision, alertes. Le
    geocodage n'est **pas** refait quand des coordonnees sont deja en base — une
    ville ne bouge pas, et c'est le seul des trois qui ne perime jamais.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    if not city:
        return False

    ancien = (load(event_id, settings) or {}).get(KIND_WEATHER) or {}
    if _fresh(ancien, now):
        return False

    point = {key: ancien.get(key) for key in ("latitude", "longitude", "timezone")}
    if point["latitude"] is None or point["longitude"] is None:
        try:
            point = await client.coordinates(city, country) or {}
        except ProviderError as exc:
            logger.warning("meteo : geocodage de %s indisponible — %s", city, exc)
            return False
        if not point.get("latitude"):
            # Ville non trouvee, ou homonyme que le pays n'a pas departage. En
            # cas de doute, aucune ligne : la meteo d'une autre ville serait une
            # erreur invisible.
            logger.info("meteo : %s (%s) non localisee", city, country or "pays inconnu")
            return False

    payload: dict[str, Any] = {
        "latitude": point.get("latitude"),
        "longitude": point.get("longitude"),
        "timezone": point.get("timezone"),
        "city": city,
        "country": country,
        "commence_time": commence_time,
        "fetched_at": now.isoformat(),
    }
    try:
        payload["hourly"] = _at_kickoff(await client.forecast(*_coords(point)), commence_time)
    except ProviderError as exc:
        logger.warning("meteo : prevision indisponible pour %s — %s", city, exc)
        return False

    code = _alert_country(country)
    payload["alert_source"] = ALERT_SOURCES.get(code or "", "")
    if payload["alert_source"]:
        try:
            brutes = await client.alerts(*_coords(point))
            payload["alerts"] = [_alert(row) for row in brutes if _covers(row, commence_time)]
        except ProviderError as exc:
            # L'absence d'alerte et une source injoignable ne se disent pas
            # pareil : la seconde laisse le champ absent, et la ligne le dira.
            logger.warning("meteo : alertes %s indisponibles — %s", payload["alert_source"], exc)
        else:
            payload["alerts_checked"] = True

    store(event_id, KIND_WEATHER, payload, settings)
    return True


def _coords(point: dict[str, Any]) -> tuple[float, float]:
    return float(point["latitude"]), float(point["longitude"])


def _alert_country(country: str | None) -> str | None:
    """Code a deux lettres d'un pays ecrit en toutes lettres.

    La table ne couvre que les pays dont une source est branchee : tout le reste
    rend `None`, ce qui fait dire a la ligne qu'elle n'a pas regarde. Deviner un
    code depuis un libelle inconnu ne servirait a rien — il n'y aurait aucune
    source au bout.
    """
    nom = (country or "").strip().casefold()
    return {"united states": "US", "usa": "US", "us": "US"}.get(nom)


def _at_kickoff(forecast: dict[str, Any], commence_time: str) -> dict[str, Any]:
    """La tranche horaire qui contient le coup d'envoi, et elle seule.

    Une prevision « du jour » ne dit rien d'un match a 21h : l'orage de
    l'apres-midi peut etre passe, ou pas encore arrive. C'est l'heure du coup
    d'envoi qui compte, et le fournisseur sert du horaire.
    """
    hourly = forecast.get("hourly") or {}
    heures = hourly.get("time") or []
    debut = _parse(commence_time)
    if not heures or debut is None:
        return {}
    cible = debut.astimezone(UTC).strftime("%Y-%m-%dT%H:00")
    if cible not in heures:
        return {}
    index = heures.index(cible)
    return {
        "time": cible,
        "temperature": _cell(hourly, "temperature_2m", index),
        "rain": _cell(hourly, "precipitation_probability", index),
        "gusts": _cell(hourly, "wind_gusts_10m", index),
        "code": _cell(hourly, "weather_code", index),
    }


def _cell(hourly: dict[str, Any], key: str, index: int) -> Any:
    values = hourly.get(key) or []
    return values[index] if index < len(values) else None


def _covers(alert: dict[str, Any], commence_time: str) -> bool:
    """L'alerte couvre-t-elle l'heure du coup d'envoi ?

    Une alerte active **maintenant** mais close avant le coup d'envoi ferait
    craindre un orage deja passe. Une borne absente ne disqualifie pas : le
    fournisseur ne date pas toujours la fin, et l'ecarter pour ca serait taire
    une alerte en cours.
    """
    debut = _parse(commence_time)
    if debut is None:
        return False
    onset, ends = _parse(alert.get("onset")), _parse(alert.get("ends"))
    if onset and debut < onset:
        return False
    return not (ends and debut > ends)


def _alert(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": (row.get("event") or "").strip(),
        # **L'emetteur est recopie, jamais devine** : « NWS Wilmington OH » est
        # l'instance, donc une source de niveau 1, et c'est ce nom que l'analyse
        # doit pouvoir citer.
        "sender": (row.get("senderName") or "").strip(),
        "severity": (row.get("severity") or "").strip(),
        "ends": row.get("ends") or "",
    }


def lines(
    event_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Meteo », relue en base et sans aucun appel.

    **L'alerte passe devant les chiffres**, et sur sa propre ligne quand elle
    existe : un fait qui peut empecher la rencontre ne se lit pas au milieu d'une
    enumeration de degres. Les chiffres suivent, avec l'heure a laquelle ils ont
    ete releves — une prevision de huit heures du matin pour un match du soir
    n'engage pas grand-chose, et le dire coute cinq caracteres.
    """
    settings = settings or get_settings()
    payload = (load(event_id, settings) or {}).get(KIND_WEATHER) or {}
    if not payload:
        return []
    # **Le fuseau vient du geocodage**, pas d'une saisie : le fournisseur publie
    # celui du lieu dans la meme reponse que les coordonnees. C'est le seul
    # fuseau qui soit certainement celui du stade, et il ne coute rien.
    zone = _zone(payload.get("timezone"))
    maintenant = now or datetime.now(UTC)

    fragments: list[str] = []
    for alerte in payload.get("alerts") or []:
        fragments.append(_alert_fragment(alerte, zone))
    chiffres = _numbers_fragment(payload, zone, maintenant)
    if chiffres:
        fragments.append(chiffres)
    mention = _source_mention(payload)
    if mention:
        fragments.append(mention)
    return [("Meteo", "\n".join(fragments))] if fragments else []


def _alert_fragment(alerte: dict[str, Any], zone: Any) -> str:
    """`ALERTE Flood Watch (Severe) — NWS Wilmington OH, jusqu'au 12/08 23:00`."""
    parts = [f"{ALERT_MARK} {alerte.get('event') or 'non nommee'}"]
    if alerte.get("severity"):
        parts[0] += f" ({alerte['severity']})"
    if alerte.get("sender"):
        parts.append(f"— {alerte['sender']}")
    fin = _parse(alerte.get("ends"))
    if fin is not None:
        parts.append(f", jusqu'au {_moment(fin, zone)}")
    return " ".join(parts).replace(" ,", ",")


def _numbers_fragment(payload: dict[str, Any], zone: Any, now: datetime) -> str:
    """`24 C, pluie 80 %, rafales 45 km/h a 21:30 local` — et le temps s'il parle.

    Le code WMO n'est nomme que s'il decrit de la pluie ou un orage : « nuageux »
    n'a jamais change une lecture de match, et l'ecrire couterait des tokens pour
    dire qu'il ne se passe rien.
    """
    heure = payload.get("hourly") or {}
    if not heure:
        return ""
    bouts = []
    if heure.get("temperature") is not None:
        bouts.append(f"{round(float(heure['temperature']))} C")
    code = heure.get("code")
    libelle = WMO_STORM.get(int(code)) if code is not None else None
    if libelle is None and code is not None:
        libelle = WMO_RAIN.get(int(code))
    if libelle:
        bouts.append(libelle)
    if heure.get("rain") is not None:
        bouts.append(f"pluie {round(float(heure['rain']))} %")
    if heure.get("gusts") is not None:
        bouts.append(f"rafales {round(float(heure['gusts']))} km/h")
    if not bouts:
        return ""
    quand = _parse(heure.get("time"))
    suffixe = f" a {_moment(quand, zone)}" if quand is not None else ""
    return ", ".join(bouts) + suffixe + _stamp(payload, zone, now)


def _stamp(payload: dict[str, Any], zone: Any, now: datetime) -> str:
    """`[Open-Meteo, releve 11:00 local]`, ou son **age** quand il a vieilli.

    Trois heures d'ecart sont sans consequence ; le meme mecanisme sur un releve
    du matin pour un match du soir servirait une prevision perimee avec la meme
    autorite. Au-dela de notre propre fenetre de fraicheur, la ligne compte donc
    les heures plutot que de donner une heure — un age se lit sans soustraction.

    Meme exigence que l'age du releve de cotes : deux lignes qui disent la meme
    chose doivent parler le meme langage.
    """
    releve = _parse(payload.get("fetched_at"))
    if releve is None:
        return ""
    age = now - releve
    if age >= timedelta(hours=TTL_HOURS):
        return f" [Open-Meteo, releve il y a {int(age.total_seconds() // 3600)} h]"
    return f" [Open-Meteo, releve {_moment(releve, zone)}]"


def _source_mention(payload: dict[str, Any]) -> str:
    """Ce que la ligne **n'a pas** regarde, nomme plutot que tu.

    Trois etats, et le troisieme est celui qui compte : une source interrogee
    sans resultat dit « aucune alerte », une source absente dit qu'on n'a pas
    regarde. Les confondre ferait lire une absence d'alerte la ou il n'y a qu'une
    absence de source — le defaut exact d'« Absents : donnees non disponibles »
    sur une competition non couverte.
    """
    source = payload.get("alert_source")
    if not source:
        pays = (payload.get("country") or "").strip() or "pays inconnu"
        return f"alertes officielles non interrogees ({pays}) — a verifier"
    if not payload.get("alerts_checked"):
        return f"alertes {source} injoignables — a verifier"
    if not payload.get("alerts"):
        return f"aucune alerte {source} en vigueur au coup d'envoi"
    return ""


def _zone(name: str | None) -> Any:
    """Le fuseau du lieu, ou `None` quand il est illisible.

    Une saisie devenue illisible d'une version de tzdata a l'autre vaut « non
    renseigne » : la ligne se rend alors en UTC et le dit, plutot que de faire
    tomber le bloc pour une precision d'affichage.
    """
    if not name:
        return None
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _moment(when: datetime, zone: Any) -> str:
    """`12/08 21:30 local`, ou en UTC quand le fuseau n'est pas connu.

    Meme regle que le repos au tennis : une heure de Paris presentee comme locale
    serait pire qu'une heure UTC annoncee comme telle.
    """
    if zone is None:
        return f"{when.astimezone(UTC).strftime('%d/%m %H:%M')} UTC"
    return f"{when.astimezone(zone).strftime('%d/%m %H:%M')} local"
