"""Restaurant-name matching. Resy search is fuzzy ("amori" → "Mori"), so a hit only counts as
the user's restaurant on an exact or strong name match (CLAUDE.md agent rules)."""

from enum import StrEnum

from app.agent.tools.location import normalize_text


class NameMatch(StrEnum):
    EXACT = "exact"
    STRONG = "strong"
    FUZZY = "fuzzy"


def normalize_name(name: str) -> str:
    """normalize_text plus: drop a leading "the"."""
    return normalize_text(name).removeprefix("the ")


def classify_name(query: str, name: str) -> NameMatch:
    q, n = normalize_name(query), normalize_name(name)
    if not q or not n:
        return NameMatch.FUZZY
    if q == n:
        return NameMatch.EXACT
    # Whole-word containment either way ("carbone" ↔ "carbone new york").
    q_tokens, n_tokens = q.split(), n.split()
    if _contains_run(n_tokens, q_tokens) or _contains_run(q_tokens, n_tokens):
        return NameMatch.STRONG
    return NameMatch.FUZZY


def _contains_run(haystack: list[str], needle: list[str]) -> bool:
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))
