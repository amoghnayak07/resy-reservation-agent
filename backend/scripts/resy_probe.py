"""Header probe for api.resy.com. Local only, read-only: one /4/find call (never
/3/details or /3/book) with the client's documented header set, to confirm the Resy
credentials in .env and the header set still work. Prints status and slot counts only,
never headers or tokens.

Finding behind that set (stage 5, 2026-09-27): auth headers alone → 500; adding Origin or
Referer alone → 500; adding User-Agent → 200. See app/resy/auth.py.

Run from backend/: uv run python -m scripts.resy_probe
"""

import argparse
import asyncio
from datetime import date, timedelta

from app.resy.client import ResyClient
from app.resy.errors import ResyError


async def probe(venue_id: int, day: date, party_size: int) -> bool:
    async with ResyClient.from_settings() as client:
        try:
            result = await client.find(venue_id, day, party_size)
        except ResyError as exc:
            print(f"FAIL  {type(exc).__name__} (status {exc.status_code}): {exc.message}")
            return False
    slots = sum(len(v.slots) for v in result.results.venues)
    print(f"OK    {slots} slots for venue {venue_id} on {day.isoformat()}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue-id", type=int, default=87134)
    parser.add_argument("--day", type=date.fromisoformat, default=date.today() + timedelta(days=7))
    parser.add_argument("--party-size", type=int, default=2)
    args = parser.parse_args()
    ok = asyncio.run(probe(args.venue_id, args.day, args.party_size))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
