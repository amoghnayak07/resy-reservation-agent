"""Stage 12: Resy's city list, the 24h region directory, and GET /api/regions. Fixture
`location-config.json` is a slimmed copy of a real `/3/location/config` capture."""

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_region_directory
from app.errors import ApiError
from app.guards import rate_limit
from app.main import create_app
from app.regions import RegionDirectory, RegionsUnavailableError, region_config, resolve_region
from app.resy.client import ResyClient
from app.resy.models import City, LocationConfigCityRaw, LocationConfigResponse, parse_response

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "resy" / "location-config.json").read_text(
        encoding="utf-8"
    )
)


class FakeCityList:
    """Mocked /3/location/config: counts calls and can be switched to fail."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/3/location/config"
        self.calls += 1
        return httpx.Response(503) if self.fail else httpx.Response(200, json=FIXTURE)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def directory(fake: FakeCityList, clock: Clock) -> RegionDirectory:
    resy = ResyClient(
        api_key="k",
        auth_token="t",
        writes_enabled=False,
        transport=httpx.MockTransport(fake),
        retry_backoff_s=0,
    )
    return RegionDirectory(resy, clock=clock)


# --- parsing --------------------------------------------------------------------------------


def test_city_list_parses_fields_radius_and_legacy_time_zones() -> None:
    parsed = parse_response(LocationConfigResponse, FIXTURE, "/3/location/config")
    cities = {c.slug: c for c in (City.from_raw(raw) for raw in parsed.root) if c}

    ny = cities["new-york-ny"]
    assert (ny.name, ny.country_code, ny.time_zone) == ("New York", "US", "EST5EDT")
    assert ny.radius_miles == 10
    assert ny.radius_m == 16093  # resy.com's New York search sent geo.radius 16100
    assert ny.visible
    assert not cities["aberdeen-township-nj"].visible
    for city in cities.values():
        ZoneInfo(city.time_zone)  # legacy names (EST5EDT, PST8PDT, CST6CDT) all load


def test_city_without_center_or_unknown_time_zone_is_skipped() -> None:
    base = dict(FIXTURE[0])
    assert City.from_raw(LocationConfigCityRaw.model_validate(base | {"latitude": None})) is None
    bad_zone = base | {"time_zone": "Mars/Olympus_Mons"}
    assert City.from_raw(LocationConfigCityRaw.model_validate(bad_zone)) is None


# --- directory ------------------------------------------------------------------------------


async def test_directory_caches_for_24h_then_refreshes() -> None:
    fake, clock = FakeCityList(), Clock()
    regions = directory(fake, clock)

    assert (await regions.get("los-angeles-ca")) is not None
    await regions.get("new-york-ny")
    assert fake.calls == 1  # cached

    clock.now += 24 * 3600 + 1
    await regions.get("new-york-ny")
    assert fake.calls == 2


async def test_directory_serves_stale_list_when_refresh_fails() -> None:
    fake, clock = FakeCityList(), Clock()
    regions = directory(fake, clock)
    await regions.cities()

    fake.fail = True
    clock.now += 24 * 3600 + 1
    city = await regions.get("new-york-ny")
    assert city is not None and city.name == "New York"


async def test_directory_with_nothing_cached_raises_unavailable() -> None:
    fake = FakeCityList()
    fake.fail = True
    with pytest.raises(RegionsUnavailableError):
        await directory(fake, Clock()).cities()


async def test_visible_countries_groups_cities_without_coordinates() -> None:
    countries = await directory(FakeCityList(), Clock()).visible_countries()
    by_code = {c["code"]: c for c in countries}

    assert [c["name"] for c in countries] == sorted(c["name"] for c in countries)
    us_slugs = [city["slug"] for city in by_code["US"]["cities"]]
    assert "new-york-ny" in us_slugs
    assert "aberdeen-township-nj" not in us_slugs  # show_on_web: 0
    assert by_code["ES"]["cities"] == [{"slug": "a-coruna-spain", "name": "A Coruña, Spain"}]
    assert "latitude" not in json.dumps(countries)


async def test_resolve_region_unknown_slug_is_422() -> None:
    with pytest.raises(ApiError) as exc:
        await resolve_region(directory(FakeCityList(), Clock()), "atlantis")
    assert (exc.value.status_code, exc.value.code) == (422, "unknown_region")


async def test_region_config_carries_center_radius_and_timezone() -> None:
    city = await directory(FakeCityList(), Clock()).get("los-angeles-ca")
    assert city is not None
    config = region_config(city)
    assert config["timezone"] == "PST8PDT"
    assert config["radius_m"] == round(19 * 1609.344)
    assert config["location"] == {"lat": city.latitude, "lng": city.longitude}


# --- GET /api/regions -----------------------------------------------------------------------


@pytest.fixture
def api() -> Any:
    fake = FakeCityList()
    regions = directory(fake, Clock())
    app = create_app()
    app.dependency_overrides[get_region_directory] = lambda: regions
    return TestClient(app), fake


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit, "_limiter", rate_limit.SlidingWindowLimiter())


def test_regions_endpoint_returns_countries_and_caches(api: Any) -> None:
    client, fake = api
    first = client.get("/api/regions")
    client.get("/api/regions")

    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=3600"
    names = [c["name"] for c in first.json()["countries"]]
    assert "United States" in names and "Spain" in names
    assert fake.calls == 1


def test_regions_endpoint_503_when_city_list_unavailable(api: Any) -> None:
    client, fake = api
    fake.fail = True
    response = client.get("/api/regions")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "regions_unavailable"


def test_regions_endpoint_is_rate_limited_per_ip(api: Any) -> None:
    client, _ = api
    codes = [client.get("/api/regions").status_code for _ in range(31)]
    assert codes[-1] == 429 and set(codes[:30]) == {200}
