"""Cuisine search helpers. Cuisines are matched against each hit's `cuisine` list, not its
name (CLAUDE.md agent rules)."""

from app.agent.tools.location import normalize_text

# Common phrasing → Resy cuisine label fragments (matched as substrings of normalized labels,
# e.g. "japanese" matches "Japanese - Peruvian").
CUISINE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sushi": ("sushi", "japanese", "omakase"),
    "omakase": ("omakase", "sushi", "japanese"),
    "ramen": ("ramen", "japanese"),
    "tacos": ("tacos", "mexican"),
    "taco": ("tacos", "mexican"),
    "pizza": ("pizza", "italian"),
    "pasta": ("pasta", "italian"),
    "steak": ("steakhouse", "steak"),
    "steakhouse": ("steakhouse", "steak"),
    "dim sum": ("dim sum", "chinese"),
    "bbq": ("bbq", "barbecue", "korean"),
    "korean bbq": ("korean",),
    "seafood": ("seafood", "oyster"),
    "burgers": ("burger", "american"),
    "burger": ("burger", "american"),
    "vegan": ("vegan", "vegetarian", "plant based"),
    "vegetarian": ("vegetarian", "vegan", "plant based"),
    "tapas": ("tapas", "spanish"),
    "curry": ("indian", "thai"),
}


def cuisine_terms(cuisine: str) -> tuple[str, ...]:
    """Label fragments to match for the user's cuisine phrasing; unknown phrasing passes
    through unchanged."""
    normalized = normalize_text(cuisine)
    return CUISINE_SYNONYMS.get(normalized, (normalized,))


def cuisine_matches(terms: tuple[str, ...], venue_cuisines: list[str]) -> bool:
    labels = [normalize_text(c) for c in venue_cuisines]
    return any(term in label for term in terms for label in labels)


def looks_like_cuisine(word: str, facet_labels: list[str]) -> bool:
    """For the ambiguous-word fallback: is a name query really a cuisine?"""
    normalized = normalize_text(word)
    if normalized in CUISINE_SYNONYMS:
        return True
    return any(normalize_text(label) == normalized for label in facet_labels)
