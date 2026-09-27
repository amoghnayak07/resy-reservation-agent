"""Resy's city list (`GET /3/location/config`), cached in memory for 24h: the one Resy response
we cache (CLAUDE.md "State and caching"). The user's selected region (a city slug) resolves here
to the search center, radius, and time zone; client coordinates are never trusted."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from app.errors import ApiError
from app.resy.client import ResyClient
from app.resy.errors import ResyError
from app.resy.models import City

logger = logging.getLogger(__name__)

CACHE_SECONDS = 24 * 3600
RETRY_AFTER_FAILURE_SECONDS = 5 * 60  # while serving a stale list


class RegionsUnavailableError(Exception):
    """No city list could be loaded (Resy failed and nothing is cached)."""


class RegionDirectory:
    def __init__(
        self,
        resy: ResyClient,
        *,
        ttl_seconds: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resy = resy
        self._ttl = ttl_seconds
        self._clock = clock
        self._cities: dict[str, City] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def cities(self) -> dict[str, City]:
        if self._cities and self._clock() < self._expires_at:
            return self._cities
        async with self._lock:
            if self._cities and self._clock() < self._expires_at:
                return self._cities
            try:
                raw = await self._resy.get_location_config()
            except ResyError as exc:
                if not self._cities:
                    raise RegionsUnavailableError from exc
                logger.warning("regions_refresh_failed_serving_stale")
                self._expires_at = self._clock() + RETRY_AFTER_FAILURE_SECONDS
                return self._cities
            parsed = [City.from_raw(entry) for entry in raw.root]
            self._cities = {city.slug: city for city in parsed if city is not None}
            self._expires_at = self._clock() + self._ttl
            return self._cities

    async def get(self, slug: str) -> City | None:
        return (await self.cities()).get(slug)

    async def visible_countries(self) -> list[dict[str, Any]]:
        """What the region selector needs: countries → cities (slug and name only)."""
        countries: dict[str, dict[str, Any]] = {}
        for city in (await self.cities()).values():
            if not city.visible:
                continue
            country = countries.setdefault(
                city.country_code,
                {"code": city.country_code, "name": city.country_name, "cities": []},
            )
            country["cities"].append({"slug": city.slug, "name": city.name})
        for country in countries.values():
            country["cities"].sort(key=lambda c: c["name"].casefold())
        return sorted(countries.values(), key=lambda c: c["name"].casefold())


async def resolve_region(regions: RegionDirectory, slug: str) -> City:
    """The selected region for a request: 422 for an unknown slug (the frontend reopens the
    region selector), 503 when no city list can be loaded."""
    try:
        city = await regions.get(slug)
    except RegionsUnavailableError as exc:
        raise ApiError(
            503, "regions_unavailable", "Resy's city list is unavailable. Try again shortly."
        ) from exc
    if city is None:
        raise ApiError(422, "unknown_region", "Unknown region; choose your location again.")
    return city


def region_config(city: City) -> dict[str, Any]:
    """Run-config entries for the selected region. Tools read `location`, `radius_m`, and
    `timezone`; the prompt reads the names. Coordinates never reach the LLM."""
    return {
        "region_slug": city.slug,
        "region_name": city.name,
        "country_name": city.country_name,
        "location": {"lat": city.latitude, "lng": city.longitude},
        "radius_m": city.radius_m,
        "timezone": city.time_zone,
    }
