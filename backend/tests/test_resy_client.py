"""Resy client: request shapes, auth headers, error classification, retries, and the write
guard. All offline via httpx.MockTransport and recorded fixtures; nothing reaches
api.resy.com."""

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.resy.client import ResyClient
from app.resy.errors import (
    ResyAuthError,
    ResyBlockedError,
    ResyError,
    ResyNotFoundError,
    ResyRateLimitedError,
    ResySchemaError,
    ResyUpstreamError,
    ResyWritesDisabledError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "resy"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Recorder:
    """MockTransport handler that replays queued responses and records requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(
    recorder: Recorder,
    *,
    api_key: str = "test-api-key",
    auth_token: str = "test-auth-token",
    writes_enabled: bool = False,
) -> ResyClient:
    return ResyClient(
        api_key=api_key,
        auth_token=auth_token,
        writes_enabled=writes_enabled,
        transport=httpx.MockTransport(recorder),
        retry_backoff_s=0,
    )


async def test_sends_auth_headers() -> None:
    recorder = Recorder(httpx.Response(200, json={"ok": True}))
    async with make_client(recorder) as client:
        assert await client._request("GET", "/4/venue/calendar") == {"ok": True}

    headers = recorder.requests[0].headers
    assert headers["Authorization"] == 'ResyAPI api_key="test-api-key"'
    assert headers["X-Resy-Auth-Token"] == "test-auth-token"
    assert headers["X-Resy-Universal-Auth"] == "test-auth-token"
    assert headers["User-Agent"].startswith("Mozilla/5.0")


async def test_missing_credentials_raise_before_any_request() -> None:
    recorder = Recorder()
    async with make_client(recorder, auth_token="") as client:
        with pytest.raises(ResyAuthError):
            await client._request("GET", "/4/venue/calendar")
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ResyAuthError),
        (403, ResyBlockedError),
        (404, ResyNotFoundError),
        (429, ResyRateLimitedError),
        (400, ResyError),
    ],
)
async def test_non_retryable_statuses_raise_typed_errors_after_one_call(
    status: int, error_type: type[ResyError]
) -> None:
    recorder = Recorder(httpx.Response(status, json={}))
    async with make_client(recorder) as client:
        with pytest.raises(error_type) as exc_info:
            await client._request("GET", "/4/venue/calendar")
    assert type(exc_info.value) is error_type
    assert exc_info.value.status_code == status
    assert len(recorder.requests) == 1


async def test_read_retries_twice_on_500_then_raises() -> None:
    recorder = Recorder(*(httpx.Response(500) for _ in range(3)))
    async with make_client(recorder) as client:
        with pytest.raises(ResyUpstreamError):
            await client._request("GET", "/4/venue/calendar")
    assert len(recorder.requests) == 3


async def test_read_recovers_after_timeout() -> None:
    recorder = Recorder(httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": True}))
    async with make_client(recorder) as client:
        assert await client._request("GET", "/4/venue/calendar") == {"ok": True}
    assert len(recorder.requests) == 2


async def test_timeout_on_every_attempt_raises_upstream() -> None:
    recorder = Recorder(*(httpx.ReadTimeout("slow") for _ in range(3)))
    async with make_client(recorder) as client:
        with pytest.raises(ResyUpstreamError):
            await client._request("GET", "/4/venue/calendar")


async def test_malformed_json_raises_schema_error() -> None:
    recorder = Recorder(
        httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"})
    )
    async with make_client(recorder) as client:
        with pytest.raises(ResySchemaError):
            await client._request("GET", "/4/venue/calendar")


async def test_html_challenge_page_raises_blocked() -> None:
    recorder = Recorder(
        httpx.Response(
            200, content=b"<html>challenge</html>", headers={"content-type": "text/html"}
        )
    )
    async with make_client(recorder) as client:
        with pytest.raises(ResyBlockedError):
            await client._request("GET", "/4/venue/calendar")


async def test_logs_never_include_tokens(caplog: pytest.LogCaptureFixture) -> None:
    recorder = Recorder(httpx.Response(401, json={}))
    async with make_client(recorder) as client:
        with pytest.raises(ResyAuthError):
            await client._request("GET", "/4/venue/calendar")
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__}"
        assert "test-auth-token" not in rendered
        assert "test-api-key" not in rendered


# --- book() write guard -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("writes_enabled", "allow_write"), [(False, False), (False, True), (True, False)]
)
async def test_book_blocked_unless_enabled_and_allowed(
    writes_enabled: bool, allow_write: bool
) -> None:
    recorder = Recorder()
    async with make_client(recorder, writes_enabled=writes_enabled) as client:
        with pytest.raises(ResyWritesDisabledError):
            await client.book("fake-book-token", allow_write=allow_write)
    assert recorder.requests == []


async def test_book_sends_captured_form_body_without_payment_method() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("book.json")))
    async with make_client(recorder, writes_enabled=True) as client:
        result = await client.book("fake-book-token", allow_write=True)

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/3/book"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {
        "book_token": ["fake-book-token"],
        "replace": ["1"],
        "source_id": ["resy.com-venue-details"],
        "venue_marketing_opt_in": ["0"],
    }
    assert result.reservation_id == 933020068
    assert result.resy_token == "SCRUBBED_RESY_TOKEN"


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(401, json={}), ResyAuthError),
        (httpx.Response(500), ResyUpstreamError),
        (httpx.ReadTimeout("slow"), ResyUpstreamError),
    ],
)
async def test_book_is_never_retried(
    response: httpx.Response | Exception, error_type: type[ResyError]
) -> None:
    recorder = Recorder(response)
    async with make_client(recorder, writes_enabled=True) as client:
        with pytest.raises(error_type):
            await client.book("fake-book-token", allow_write=True)
    assert len(recorder.requests) == 1


# --- read methods: request shapes as captured, responses parsed from fixtures ------------


async def test_find_posts_captured_json_body() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("venue-find.json")))
    async with make_client(recorder) as client:
        result = await client.find(87134, date(2026, 10, 22), 2)

    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("POST", "/4/find")
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "lat": 0,
        "long": 0,
        "day": "2026-10-22",
        "party_size": 2,
        "venue_id": 87134,
    }
    assert len(result.results.venues[0].slots) == 33


async def test_get_details_posts_json_with_commit_flag() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("details-commit0.json")))
    token = "rgs://resy/87134/3533613/3/2026-10-22/2026-10-22/12:00:00/2/Dining Room"
    async with make_client(recorder) as client:
        result = await client.get_details(token, date(2026, 10, 22), 2, commit=False)

    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("POST", "/3/details")
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "commit": 0,
        "config_id": token,
        "day": "2026-10-22",
        "party_size": 2,
    }
    assert result.book_token is None


async def test_venue_search_posts_captured_geo_payload() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("venue-search-geo.json")))
    async with make_client(recorder) as client:
        result = await client.venue_search(
            lat=40.712941, lng=-74.006393, radius_m=16100, day=date(2026, 10, 22), party_size=2
        )

    assert json.loads(recorder.requests[0].content) == fixture("venue-search-geo-request.json")
    assert len(result.search.hits) == 8


async def test_get_venue_sends_slug_params() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("venue.json")))
    async with make_client(recorder) as client:
        result = await client.get_venue("brooklyn-chop-house-downtown-fidi", "new-york-ny")

    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("GET", "/3/venue")
    assert dict(request.url.params) == {
        "url_slug": "brooklyn-chop-house-downtown-fidi",
        "location": "new-york-ny",
    }
    assert result.id.resy == 87134


async def test_get_calendar_sends_num_seats_params() -> None:
    recorder = Recorder(httpx.Response(200, json=fixture("venue-calendar.json")))
    async with make_client(recorder) as client:
        result = await client.get_calendar(87134, 2, date(2026, 9, 27), date(2027, 9, 27))

    request = recorder.requests[0]
    assert (request.method, request.url.path) == ("GET", "/4/venue/calendar")
    assert dict(request.url.params) == {
        "venue_id": "87134",
        "num_seats": "2",
        "start_date": "2026-09-27",
        "end_date": "2027-09-27",
    }
    assert result.last_calendar_day == date(2026, 10, 31)


async def test_unexpected_shape_raises_schema_error_without_values() -> None:
    recorder = Recorder(httpx.Response(200, json={"results": {"venues": [{"slots": "nope"}]}}))
    async with make_client(recorder) as client:
        with pytest.raises(ResySchemaError) as exc_info:
            await client.find(87134, date(2026, 10, 22), 2)
    assert any(path.startswith("results.venues.0") for path in exc_info.value.field_paths)
    assert "nope" not in str(exc_info.value)
