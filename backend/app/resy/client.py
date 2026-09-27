"""Async HTTP client for api.resy.com. Reads retry on upstream failures with backoff;
writes are never retried and are blocked unless RESY_WRITES_ENABLED=true and the caller
passes allow_write=True (CLAUDE.md hard rules 1 and 10).

Logs record method, endpoint path, status, latency, and attempt only: never headers,
tokens, query params, or bodies."""

import asyncio
import json
import logging
import time
from datetime import date
from typing import Any, Self

import httpx

from app.config import settings
from app.resy.auth import build_headers
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
from app.resy.models import (
    BookResponse,
    CalendarResponse,
    DetailsResponse,
    FindResponse,
    VenueResponse,
    VenueSearchResponse,
    parse_response,
)

logger = logging.getLogger("app.resy")

BASE_URL = "https://api.resy.com"
TIMEOUT_S = 10.0
READ_RETRIES = 2
BOOK_SOURCE_ID = "resy.com-venue-details"


def _classify_status(status: int, endpoint: str) -> ResyError | None:
    if status < 400:
        return None
    if status == 401:
        return ResyAuthError(
            "Resy token expired or invalid; rotate RESY_AUTH_TOKEN.", status_code=status
        )
    if status == 403:
        return ResyBlockedError(f"Resy blocked the request to {endpoint}.", status_code=status)
    if status == 404:
        return ResyNotFoundError(f"Resy returned 404 for {endpoint}.", status_code=status)
    if status == 429:
        return ResyRateLimitedError(f"Resy rate-limited {endpoint}.", status_code=status)
    if status >= 500:
        return ResyUpstreamError(f"Resy returned {status} for {endpoint}.", status_code=status)
    return ResyError(f"Resy returned {status} for {endpoint}.", status_code=status)


def _parse_json(response: httpx.Response, endpoint: str) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # api.resy.com only serves JSON; an HTML body means a bot-challenge page.
        if "html" in response.headers.get("content-type", ""):
            raise ResyBlockedError(
                f"Resy returned a challenge page for {endpoint}.",
                status_code=response.status_code,
            ) from exc
        raise ResySchemaError(f"Resy returned malformed JSON for {endpoint}.") from exc


class ResyClient:
    def __init__(
        self,
        *,
        api_key: str,
        auth_token: str,
        writes_enabled: bool,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_s: float = 0.5,
    ) -> None:
        self._api_key = api_key
        self._auth_token = auth_token
        self._writes_enabled = writes_enabled
        self._retry_backoff_s = retry_backoff_s
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT_S, transport=transport)

    @classmethod
    def from_settings(cls, transport: httpx.AsyncBaseTransport | None = None) -> Self:
        return cls(
            api_key=settings.resy_api_key,
            auth_token=settings.resy_auth_token,
            writes_enabled=settings.resy_writes_enabled,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # --- reads (request shapes as captured; see PLAN.md endpoint table) --------------------

    async def venue_search(
        self,
        *,
        query: str,
        geo: tuple[float, float, int] | None,
        day: date | None = None,
        party_size: int | None = None,
        per_page: int = 20,
        page: int = 1,
    ) -> VenueSearchResponse:
        """POST /3/venuesearch/search with the stage 6 probe-approved payload.

        geo: (lat, lng, radius_m); Resy enforces the radius server-side. None searches
        globally (only for out-of-area checks). With day + party_size, hits embed their
        slots for that day, already filtered by party size; without them, name resolution
        only."""
        endpoint = "/3/venuesearch/search"
        body: dict[str, Any] = {
            "include_tock_inventory": False,
            "order_by": "availability",
            "page": page,
            "per_page": per_page,
            "query": query,
            "types": ["venue"],
        }
        if geo is not None:
            lat, lng, radius_m = geo
            body["geo"] = {"latitude": lat, "longitude": lng, "radius": radius_m}
        if day is not None and party_size is not None:
            body["availability"] = True
            body["slot_filter"] = {"day": day.isoformat(), "party_size": party_size}
        data = await self._request("POST", endpoint, json_body=body)
        return parse_response(VenueSearchResponse, data, endpoint)

    async def venue_search_raw(self, body: dict[str, Any]) -> Any:
        """POST /3/venuesearch/search with a caller-built body; returns the raw JSON.
        Only for the local payload probe (scripts/resy_probe.py); tools use venue_search."""
        return await self._request("POST", "/3/venuesearch/search", json_body=body)

    async def find(self, venue_id: int, day: date, party_size: int) -> FindResponse:
        """POST /4/find. lat/long 0 as captured (travel_time is then meaningless; ignored)."""
        endpoint = "/4/find"
        data = await self._request(
            "POST",
            endpoint,
            json_body={
                "lat": 0,
                "long": 0,
                "day": day.isoformat(),
                "party_size": party_size,
                "venue_id": venue_id,
            },
        )
        return parse_response(FindResponse, data, endpoint)

    async def get_details(
        self, config_id: str, day: date, party_size: int, *, commit: bool
    ) -> DetailsResponse:
        """POST /3/details. commit=True issues a book token and may hold the table: only
        prepare_booking and the confirm flow may pass it (CLAUDE.md hard rule 1)."""
        endpoint = "/3/details"
        data = await self._request(
            "POST",
            endpoint,
            json_body={
                "commit": 1 if commit else 0,
                "config_id": config_id,
                "day": day.isoformat(),
                "party_size": party_size,
            },
        )
        return parse_response(DetailsResponse, data, endpoint)

    async def get_venue(self, venue_id: int) -> VenueResponse:
        """GET /3/venue?id= (same response as the slug lookup resy.com pages use)."""
        endpoint = "/3/venue"
        data = await self._request("GET", endpoint, params={"id": venue_id})
        return parse_response(VenueResponse, data, endpoint)

    async def get_calendar(
        self, venue_id: int, num_seats: int, start_date: date, end_date: date
    ) -> CalendarResponse:
        """GET /4/venue/calendar. Resy caps the window at last_calendar_day."""
        endpoint = "/4/venue/calendar"
        data = await self._request(
            "GET",
            endpoint,
            params={
                "venue_id": venue_id,
                "num_seats": num_seats,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        )
        return parse_response(CalendarResponse, data, endpoint)

    # --- writes ---------------------------------------------------------------------------

    async def book(self, book_token: str, *, allow_write: bool = False) -> BookResponse:
        """POST /3/book, form-encoded exactly as captured (including the undocumented
        replace=1). Free reservations need no payment method, so none is sent. Never retried."""
        if not (self._writes_enabled and allow_write):
            raise ResyWritesDisabledError(
                "Resy writes are disabled (need RESY_WRITES_ENABLED=true and allow_write=True)."
            )
        endpoint = "/3/book"
        data = await self._request(
            "POST",
            endpoint,
            form_body={
                "book_token": book_token,
                "replace": "1",
                "source_id": BOOK_SOURCE_ID,
                "venue_marketing_opt_in": "0",
            },
            write=True,
        )
        return parse_response(BookResponse, data, endpoint)

    # --- transport ------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, str] | None = None,
        write: bool = False,
    ) -> Any:
        headers = build_headers(self._api_key, self._auth_token)
        max_attempts = 1 if write else 1 + READ_RETRIES
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._send_once(
                    method, endpoint, headers, params, json_body, form_body, attempt
                )
            except ResyUpstreamError:
                if attempt == max_attempts:
                    raise
                await asyncio.sleep(self._retry_backoff_s * 2 ** (attempt - 1))
        raise AssertionError("unreachable")

    async def _send_once(
        self,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        form_body: dict[str, str] | None,
        attempt: int,
    ) -> Any:
        start = time.monotonic()
        status: int | str = "error"
        try:
            response = await self._http.request(
                method, endpoint, headers=headers, params=params, json=json_body, data=form_body
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            status = "connect_error"
            raise ResyUpstreamError(
                f"Could not connect to Resy for {endpoint}.", request_sent=False
            ) from exc
        except httpx.TimeoutException as exc:
            status = "timeout"
            raise ResyUpstreamError(f"Resy timed out on {endpoint}.") from exc
        except httpx.TransportError as exc:
            status = "transport_error"
            raise ResyUpstreamError(f"Could not reach Resy for {endpoint}.") from exc
        else:
            status = response.status_code
        finally:
            logger.info(
                "resy_request",
                extra={
                    "method": method,
                    "endpoint": endpoint,
                    "status": status,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                    "attempt": attempt,
                },
            )

        error = _classify_status(response.status_code, endpoint)
        if error is not None:
            raise error
        return _parse_json(response, endpoint)
