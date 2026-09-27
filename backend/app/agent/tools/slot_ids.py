"""Slot-ID map: short `slot_id`s shown to the LLM → server-side slot details (config token,
etc.). In memory with a 15-minute expiry (CLAUDE.md "State and caching"); resets on restart,
and an expired ID means "search again". Keyed by conversation, so IDs never cross users."""

import time
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

SLOT_ID_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class SlotRef:
    config_token: str
    venue_id: int
    venue_name: str
    party_size: int
    start: datetime
    seating_type: str | None
    bookable: bool
    requires_payment: bool
    venue_url: str | None = None  # Resy link for "book it on Resy instead" answers


class SlotIdMap:
    def __init__(self, ttl_seconds: float = SLOT_ID_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], tuple[float, SlotRef]] = {}
        self._counters: dict[str, int] = {}
        self._lock = Lock()

    def add(self, conversation_id: str, ref: SlotRef, *, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._purge(now)
            number = self._counters.get(conversation_id, 0) + 1
            self._counters[conversation_id] = number
            slot_id = f"s{number}"
            self._entries[(conversation_id, slot_id)] = (now + self._ttl, ref)
            return slot_id

    def get(
        self, conversation_id: str, slot_id: str, *, now: float | None = None
    ) -> SlotRef | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            entry = self._entries.get((conversation_id, slot_id))
            if entry is None or entry[0] < now:
                return None
            return entry[1]

    def _purge(self, now: float) -> None:
        expired = [key for key, (expires, _) in self._entries.items() if expires < now]
        for key in expired:
            del self._entries[key]


# Process-wide instance (single Render instance; see CLAUDE.md).
slot_ids = SlotIdMap()
