"""Resy probes. Local only, read-only: never calls /3/details or /3/book. Prints counts,
names, and latencies only, never headers or tokens.

Default: header check (one /4/find call with the client's documented header set).
Finding behind that set (stage 5, 2026-09-27): auth headers alone → 500; adding Origin or
Referer alone → 500; adding User-Agent → 200. See app/resy/auth.py.

--payloads: stage 6 venue-search payload probe (variants of the search-page payload).
--save-japanese PATH: also save the "japanese" page-1 response as a trimmed fixture.

Run from backend/:
  uv run python -m scripts.resy_probe
  uv run python -m scripts.resy_probe --payloads
"""

import argparse
import asyncio
import copy
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.resy.client import ResyClient
from app.resy.errors import ResyError

# Lower Manhattan, as in the stage 6 Capture 3 search-page payload.
PROBE_LAT, PROBE_LNG = 40.712941, -74.006393


async def header_check(venue_id: int, day: date, party_size: int) -> bool:
    async with ResyClient.from_settings() as client:
        try:
            result = await client.find(venue_id, day, party_size)
        except ResyError as exc:
            print(f"FAIL  {type(exc).__name__} (status {exc.status_code}): {exc.message}")
            return False
    slots = sum(len(v.slots) for v in result.results.venues)
    print(f"OK    {slots} slots for venue {venue_id} on {day.isoformat()}")
    return True


# --- payload probe --------------------------------------------------------------------------


def base_payload(day: date, party_size: int, query: str = "", **overrides: Any) -> dict[str, Any]:
    """The captured search-page payload (Capture 3)."""
    body: dict[str, Any] = {
        "availability": True,
        "geo": {"latitude": PROBE_LAT, "longitude": PROBE_LNG, "radius": 16100},
        "include_tock_inventory": True,
        "order_by": "availability",
        "page": 1,
        "per_page": 20,
        "query": query,
        "slot_filter": {"day": day.isoformat(), "party_size": party_size},
        "types": ["venue"],
    }
    body.update(overrides)
    return body


def without(body: dict[str, Any], *keys: str) -> dict[str, Any]:
    trimmed = copy.deepcopy(body)
    for key in keys:
        trimmed.pop(key, None)
    return trimmed


def km_from_probe(hit: dict[str, Any]) -> float | None:
    geo = hit.get("_geoloc") or {}
    if "lat" not in geo or "lng" not in geo:
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (PROBE_LAT, PROBE_LNG, geo["lat"], geo["lng"]))
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def summarize(label: str, data: Any, latency_ms: int) -> dict[str, Any]:
    search = data.get("search") or {}
    hits: list[dict[str, Any]] = search.get("hits") or []
    total = (data.get("meta") or {}).get("total", search.get("nbHits"))
    with_slots = sum(1 for h in hits if (h.get("availability") or {}).get("slots"))
    tock = sum(1 for h in hits if h.get("is_tock_inventory"))
    distances = [d for d in (km_from_probe(h) for h in hits) if d is not None]
    top = ", ".join(f"{h.get('name')} ({h.get('neighborhood') or '-'})" for h in hits[:5])
    print(f"\n[{label}] {latency_ms} ms · total {total} · returned {len(hits)}")
    print(f"  with slots: {with_slots} · tock: {tock}", end="")
    if distances:
        print(f" · km from probe: min {min(distances):.1f}, max {max(distances):.1f}", end="")
    print(f"\n  top 5: {top}")
    return {"hits": hits, "total": total}


async def run(client: ResyClient, label: str, body: dict[str, Any]) -> dict[str, Any] | None:
    start = time.monotonic()
    try:
        data = await client.venue_search_raw(body)
    except ResyError as exc:
        print(f"\n[{label}] FAIL {type(exc).__name__} (status {exc.status_code})")
        return None
    latency_ms = int((time.monotonic() - start) * 1000)
    await asyncio.sleep(0.5)  # stay polite: low, sequential volume
    return summarize(label, data, latency_ms) | {"raw": data}


def slot_party_sizes(hits: list[dict[str, Any]]) -> set[str]:
    """Party-size segment of each slot token: rgs://resy/<venue>/<tpl>/<svc>/<d>/<d>/<t>/<party>/<seating>."""
    sizes: set[str] = set()
    for hit in hits:
        for slot in (hit.get("availability") or {}).get("slots") or []:
            token = (slot.get("config") or {}).get("token")
            if token:
                sizes.add(token.split("/")[-2])
    return sizes


def cuisine_share(hits: list[dict[str, Any]], label: str) -> str:
    in_cuisine = [
        h for h in hits if any(label.lower() in c.lower() for c in h.get("cuisine") or [])
    ]
    in_name = [h for h in in_cuisine if label.lower() in (h.get("name") or "").lower()]
    return (
        f"{len(in_cuisine)}/{len(hits)} have '{label}' in cuisine; "
        f"{len(in_name)} of those also have it in the name"
    )


def save_fixture(raw: dict[str, Any], path: Path) -> None:
    """Trim a real response for use as a test fixture: drop embedded slots/templates and
    long text, and scrub venue phone numbers."""
    data = copy.deepcopy(raw)
    for hit in (data.get("search") or {}).get("hits") or []:
        hit.pop("availability", None)
        hit.pop("content", None)
        hit.pop("collections", None)
        hit["images"] = (hit.get("images") or [])[:1]
        if (hit.get("contact") or {}).get("phone_number"):
            hit["contact"]["phone_number"] = "+10000000000"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"\nSaved trimmed fixture: {path}")


async def payload_probe(day: date, save_japanese: Path | None) -> None:
    async with ResyClient.from_settings() as client:
        print(f"Probe point: Lower Manhattan · day {day.isoformat()} · party 2")

        await run(client, "#1 baseline, query 'amori'", base_payload(day, 2, "amori"))

        base_area = base_payload(day, 2)
        await run(
            client,
            "#3a area, include_tock_inventory false",
            base_area | {"include_tock_inventory": False},
        )
        await run(
            client,
            "#3b area, include_tock_inventory removed",
            without(base_area, "include_tock_inventory"),
        )

        for per_page in (10, 20, 50, 100):
            await run(client, f"#5 area, per_page {per_page}", base_area | {"per_page": per_page})

        await run(client, "#6 'amori', geo removed", without(base_payload(day, 2, "amori"), "geo"))

        p2 = await run(client, "#8 area, party 2", base_area)
        p6 = await run(client, "#8 area, party 6", base_payload(day, 6))
        if p2 and p6:
            print(
                f"\n  #8 token party segments: party 2 → {sorted(slot_party_sizes(p2['hits']))}; "
                f"party 6 → {sorted(slot_party_sizes(p6['hits']))}"
            )

        page1 = await run(client, "#9 area, page 1", base_area)
        page2 = await run(client, "#9 area, page 2", base_area | {"page": 2})
        if page1 and page2:
            ids1 = {h["id"]["resy"] for h in page1["hits"]}
            ids2 = {h["id"]["resy"] for h in page2["hits"]}
            print(f"\n  #9 overlap between pages: {len(ids1 & ids2)} venues")

        await run(client, "#10 exact name 'Mori'", base_payload(day, 2, "Mori"))

        jp1 = await run(
            client,
            "#12 'japanese', per_page 50, page 1",
            base_payload(day, 2, "japanese", per_page=50),
        )
        jp2 = await run(
            client,
            "#12 'japanese', per_page 50, page 2",
            base_payload(day, 2, "japanese", per_page=50, page=2),
        )
        if jp1:
            print(f"\n  #12 page 1: {cuisine_share(jp1['hits'], 'japanese')}")
        if jp2:
            print(f"  #12 page 2: {cuisine_share(jp2['hits'], 'japanese')}")

        await run(
            client, "#13 'japanese', geo removed", without(base_payload(day, 2, "japanese"), "geo")
        )

        for radius in (1000, 40000):
            geo = {"latitude": PROBE_LAT, "longitude": PROBE_LNG, "radius": radius}
            await run(client, f"radius {radius} m, area", base_area | {"geo": geo})

        if save_japanese and jp1:
            save_fixture(jp1["raw"], save_japanese)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue-id", type=int, default=87134)
    parser.add_argument("--day", type=date.fromisoformat, default=date.today() + timedelta(days=7))
    parser.add_argument("--party-size", type=int, default=2)
    parser.add_argument("--payloads", action="store_true", help="run the stage 6 payload probe")
    parser.add_argument("--save-japanese", type=Path, default=None)
    args = parser.parse_args()
    if args.payloads:
        asyncio.run(payload_probe(args.day, args.save_japanese))
        return
    ok = asyncio.run(header_check(args.venue_id, args.day, args.party_size))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
