"""Location scope: distance from the user, neighborhood matching by name, city label.

Coordinates stay server-side: nothing here is returned to the LLM or traced."""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0

# Common shorthand → Resy's neighborhood name (compared after normalization).
NEIGHBORHOOD_ALIASES = {
    "les": "lower east side",
    "ues": "upper east side",
    "uws": "upper west side",
    "fidi": "financial district",
    "the village": "greenwich village",
    "west vil": "west village",
    "ev": "east village",
    "wv": "west village",
    "soho": "soho",
    "noho": "noho",
    "nomad": "nomad",
    "bk heights": "brooklyn heights",
}


@dataclass(frozen=True)
class UserLocation:
    lat: float
    lng: float


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def within_radius(
    user: UserLocation, lat: float | None, lng: float | None, radius_km: float
) -> bool:
    """Venues without coordinates can't be placed, so they're out of scope."""
    if lat is None or lng is None:
        return False
    return haversine_km(user.lat, user.lng, lat, lng) <= radius_km


def normalize_text(value: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z0-9]+", " ", ascii_only.lower())
    return " ".join(cleaned.split())


def normalize_neighborhood(value: str) -> str:
    normalized = normalize_text(value)
    return NEIGHBORHOOD_ALIASES.get(normalized, normalized)


def neighborhood_matches(requested: str, venue_neighborhood: str | None) -> bool:
    """Venues with no usable neighborhood (None, e.g. just the city) never match."""
    if not venue_neighborhood:
        return False
    return normalize_neighborhood(requested) == normalize_neighborhood(venue_neighborhood)


def city_label(cities: Iterable[str | None]) -> str | None:
    """The most common city among in-radius venues (e.g. "New York")."""
    counts = Counter(c for c in cities if c)
    return counts.most_common(1)[0][0] if counts else None
