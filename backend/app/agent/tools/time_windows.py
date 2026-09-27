"""Time-window rules (stage 6, step 7). All times are in the user's timezone, which is also
the venue's since searches are local."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

Precision = Literal["exact", "approximate", "range", "any"]
SearchKind = Literal["venue", "area", "cuisine"]

EXACT_MARGIN = timedelta(minutes=30)
APPROXIMATE_MARGIN = timedelta(minutes=60)
TODAY_LEAD = timedelta(minutes=30)
AREA_DEFAULT_START = time(18, 0)
AREA_DEFAULT_END = time(22, 0)
TONIGHT_END = time(22, 0)


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime
    precision: Precision
    requested: datetime | None  # exact/approximate target time
    defaulted: bool  # true when the tool picked the window (state it to the user)

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end

    def describe(self) -> str:
        return f"{self.start.strftime('%H:%M')}-{self.end.strftime('%H:%M')}"


def build_window(
    *,
    day: date,
    now: datetime,
    kind: SearchKind,
    precision: Precision | None,
    requested_time: time | None,
    time_start: time | None,
    time_end: time | None,
) -> TimeWindow:
    """now must be timezone-aware in the user's timezone; the window uses the same tz."""
    tz = now.tzinfo

    def at(t: time) -> datetime:
        return datetime.combine(day, t, tzinfo=tz)

    if requested_time is not None and precision in (None, "exact", "approximate"):
        target = at(requested_time)
        margin = APPROXIMATE_MARGIN if precision == "approximate" else EXACT_MARGIN
        resolved: Precision = precision or "exact"
        window = TimeWindow(target - margin, target + margin, resolved, target, defaulted=False)
    elif time_start is not None and time_end is not None:
        window = TimeWindow(at(time_start), at(time_end), "range", None, defaulted=False)
    elif kind == "venue":
        window = TimeWindow(at(time.min), at(time(23, 59)), "any", None, defaulted=False)
    else:
        window = TimeWindow(
            at(AREA_DEFAULT_START), at(AREA_DEFAULT_END), "any", None, defaulted=True
        )

    if day == now.date():
        earliest = now + TODAY_LEAD
        if window.precision == "any" and kind != "venue":
            # No time given for today: now + 30 min until 10 PM.
            window = TimeWindow(earliest, at(TONIGHT_END), "any", None, defaulted=True)
        elif window.start < earliest:
            window = TimeWindow(
                earliest, window.end, window.precision, window.requested, window.defaulted
            )
    return window
