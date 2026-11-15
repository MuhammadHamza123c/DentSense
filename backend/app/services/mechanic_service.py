"""Nearby mechanics (Geoapify → Overpass fallback).

Flow:
  1) lat/lng -> Geoapify places (service.vehicle.repair, 10 km circle) limit 5.
  2) If Geoapify fails, reverse-geocode and use Overpass bbox, then radius.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from app.config import Settings
from app.exceptions import ExternalServiceError, ServiceUnavailableError
from app.logging_config import get_logger

logger = get_logger(__name__)

GEOAPIFY_PLACES_URL = "https://api.geoapify.com/v2/places"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"


class MechanicService:
    """Fetches nearby car-repair places from Geoapify."""

    def __init__(self, settings: Settings, client_factory: Any | None = None) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        self._nominatim: httpx.AsyncClient | None = None

    async def nearby(self, *, lat: float, lng: float) -> list[dict[str, Any]]:
        places = await self._search_geoapify(lat, lng)
        if not places:
            places = await self._search_overpass(lat, lng)
        places.sort(key=lambda p: p["distance_m"])
        return places[:5]

    # -- Geoapify -------------------------------------------------------------
    async def _search_geoapify(self, lat: float, lng: float) -> list[dict[str, Any]]:
        if not self._settings.geoapify_api_key:
            logger.warning("GEOAPIFY_API_KEY not configured")
            return []
        radius = self._settings.mechanic_search_radius_m
        url = GEOAPIFY_PLACES_URL
        params = {
            "categories": "service.vehicle.repair",
            "filter": f"circle:{lng},{lat},{radius}",
            "bias": f"proximity:{lng},{lat}",
            "limit": 20,
            "apiKey": self._settings.geoapify_api_key.get_secret_value(),
        }
        try:
            response = await self._client_call(url, params=params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geoapify call failed: %s", type(exc).__name__)
            return []

        try:
            features = response.get("features", []) or response.get("results", []) or []
        except Exception:
            return []

        places: list[dict[str, Any]] = []
        seen = set()
        for feat in features:
            props = feat.get("properties") or {}
            geom = (feat.get("geometry") or {}).get("coordinates") or []
            if len(geom) >= 2:
                plng, plat = float(geom[0]), float(geom[1])
            else:
                plat = feat.get("lat")
                plng = feat.get("lon")
            if plat is None or plng is None:
                continue
            name = props.get("name") or feat.get("name") or "Car repair"
            if name in seen:
                continue
            seen.add(name)
            places.append(
                {
                    "name": name,
                    "lat": float(plat),
                    "lng": float(plng),
                    "distance_m": round(_haversine_m(lat, lng, float(plat), float(plng)), 1),
                    "address": props.get("formatted") or props.get("address_line1") or props.get("address_line2"),
                    "phone": props.get("phone") or props.get("datasource", {}).get("raw", {}).get("contact:phone"),
                    "website": props.get("website") or props.get("datasource", {}).get("raw", {}).get("website"),
                    "opening_hours": props.get("opening_hours")
                        if props.get("opening_hours") and isinstance(props.get("opening_hours"), str)
                        else props.get("datasource", {}).get("raw", {}).get("opening_hours"),
                    "osm_type": None,
                    "osm_id": props.get("datasource", {}).get("raw", {}).get("osm_id"),
                }
            )
        places.sort(key=lambda p: p["distance_m"])
        return places[:5]

    # -- Overpass fallback ------------------------------------------------------
    async def _search_overpass(self, lat: float, lng: float) -> list[dict[str, Any]]:
        client = await self._get_client()
        try:
            city_info = await self._nominatim_json(
                NOMINATIM_REVERSE,
                params={"lat": lat, "lon": lng, "format": "json", "zoom": 10},
            )
            box = city_info.get("boundingbox") or []
            if len(box) == 4:
                south, north, west, east = float(box[0]), float(box[1]), float(box[2]), float(box[3])
                query = OVERPASS_BBOX.format(south=south, west=west, north=north, east=east)
            else:
                query = OVERPASS_RADIUS.format(
                    radius=self._settings.mechanic_search_radius_m, lat=lat, lng=lng
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Overpass plan skipped: %s", type(exc).__name__)
            query = OVERPASS_RADIUS.format(
                radius=self._settings.mechanic_search_radius_m, lat=lat, lng=lng
            )

        try:
            response = await client.post(
                "",
                content=query.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
        except httpx.TimeoutException as exc:
            logger.warning("Overpass timed out")
            raise ServiceUnavailableError(
                "Nearby-mechanic search timed out. Please try again."
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("Overpass network error: %s", type(exc).__name__)
            raise ExternalServiceError("The nearby-mechanic service is unreachable.") from exc

        if response.status_code >= 400:
            logger.error("Overpass request failed: status=%s", response.status_code)
            if response.status_code == 429:
                raise ServiceUnavailableError(
                    "The nearby-mechanic service is rate-limited. Please try again shortly.",
                    headers={"Retry-After": "30"},
                )
            return []

        try:
            elements = response.json().get("elements", [])
        except ValueError:
            return []

        places: list[dict[str, Any]] = []
        for el in elements:
            tags = el.get("tags", {})
            if el.get("type") == "node":
                plat, plng = el.get("lat"), el.get("lon")
            else:
                center = el.get("center") or {}
                plat, plng = center.get("lat"), center.get("lon")
            if plat is None or plng is None:
                continue
            places.append(
                {
                    "name": tags.get("name") or tags.get("operator") or tags.get("brand") or "Car repair",
                    "lat": plat,
                    "lng": plng,
                    "distance_m": round(_haversine_m(lat, lng, plat, plng), 1),
                    "address": _build_address(tags),
                    "phone": tags.get("phone") or tags.get("contact:phone"),
                    "website": tags.get("website") or tags.get("contact:website"),
                    "opening_hours": tags.get("opening_hours"),
                    "osm_type": el.get("type"),
                    "osm_id": el.get("id"),
                }
            )
        return places

    # -- clients ----------------------------------------------------------------
    def _ensure_clients(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.overpass_api_url,
                timeout=httpx.Timeout(self._settings.mechanic_http_timeout_seconds),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "User-Agent": f"cardamageai-backend/{self._settings.app_version}",
                },
            )
        if self._nominatim is None:
            self._nominatim = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": f"cardamageai-backend/{self._settings.app_version}"},
            )

    async def _get_client(self) -> httpx.AsyncClient:
        self._ensure_clients()
        client = self._client
        assert client is not None
        return client

    async def _client_call(self, url: str, params: dict[str, Any]) -> Any:
        if self._nominatim is None:
            self._ensure_clients()
        assert self._nominatim is not None
        response = await self._nominatim.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _nominatim_json(self, url: str, params: dict[str, Any]) -> Any:
        return await self._client_call(url, params=params)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._nominatim is not None:
            await self._nominatim.aclose()
            self._nominatim = None


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _build_address(tags: dict[str, Any]) -> str | None:
    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    addr = ", ".join(p for p in parts if p)
    return addr or tags.get("addr:full") or tags.get("addr:place")


OVERPASS_BBOX = """
[out:json][timeout:25];
(
  node["shop"="car_repair"]({south},{west},{north},{east});
  way["shop"="car_repair"]({south},{west},{north},{east});
  node["craft"="car_repair"]({south},{west},{north},{east});
  way["craft"="car_repair"]({south},{west},{north},{east});
);
out center tags;
""".strip()

OVERPASS_RADIUS = """
[out:json][timeout:25];
(
  node["shop"="car_repair"](around:{radius},{lat},{lng});
  way["shop"="car_repair"](around:{radius},{lat},{lng});
  node["craft"="car_repair"](around:{radius},{lat},{lng});
  way["craft"="car_repair"](around:{radius},{lat},{lng});
);
out center tags;
""".strip()
