from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.prompts import build_system_prompt


def test_calendar_lists_14_days_with_correct_weekdays() -> None:
    now = datetime(2026, 9, 26, 10, 0)  # Saturday

    prompt = build_system_prompt(
        now=now, tz="America/New_York", location_available=False, city=None
    )

    assert "Today is 2026-09-26 (Saturday)" in prompt
    assert "- 2026-09-26 (Saturday)" in prompt
    assert "- 2026-10-02 (Friday)" in prompt  # next Friday
    assert "- 2026-10-09 (Friday)" in prompt  # 14th and last day
    assert "- 2026-10-10" not in prompt  # beyond the 14-day window


def test_date_rules_text_present() -> None:
    now = datetime(2026, 9, 26, 10, 0)

    prompt = build_system_prompt(
        now=now, tz="America/New_York", location_available=False, city=None
    )

    assert "next upcoming occurrence" in prompt
    assert "next such day" in prompt
    assert "Never book in the past" in prompt


def test_timezone_conversion_affects_calendar_start_day() -> None:
    utc_instant = datetime(2026, 9, 27, 5, 0, tzinfo=ZoneInfo("UTC"))

    now_la = utc_instant.astimezone(ZoneInfo("America/Los_Angeles"))
    prompt_la = build_system_prompt(
        now=now_la, tz="America/Los_Angeles", location_available=False, city=None
    )
    assert "Today is 2026-09-26 (Saturday)" in prompt_la
    assert "- 2026-09-26 (Saturday)" in prompt_la

    now_ny = utc_instant.astimezone(ZoneInfo("America/New_York"))
    prompt_ny = build_system_prompt(
        now=now_ny, tz="America/New_York", location_available=False, city=None
    )
    assert "Today is 2026-09-27 (Sunday)" in prompt_ny
    assert "- 2026-09-27 (Sunday)" in prompt_ny


def test_location_and_city_lines() -> None:
    now = datetime(2026, 9, 26, 10, 0)

    prompt_no_location = build_system_prompt(
        now=now, tz="America/New_York", location_available=False, city=None
    )
    assert "location is not available yet" in prompt_no_location

    prompt_with_city = build_system_prompt(
        now=now, tz="America/New_York", location_available=True, city="the West Village"
    )
    assert "near the West Village" in prompt_with_city
