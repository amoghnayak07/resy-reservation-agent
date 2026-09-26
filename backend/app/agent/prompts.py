from datetime import datetime, timedelta

CALENDAR_DAYS = 14


def build_system_prompt(now: datetime, tz: str, location_available: bool, city: str | None) -> str:
    calendar_rows = "\n".join(
        f"- {(now + timedelta(days=offset)).strftime('%Y-%m-%d')} "
        f"({(now + timedelta(days=offset)).strftime('%A')})"
        for offset in range(CALENDAR_DAYS)
    )

    if location_available and city:
        location_line = f"The user's location is available; they are near {city}."
    elif location_available:
        location_line = "The user's location is available."
    else:
        location_line = (
            "The user's location is not available yet. Searches are local, so ask them "
            "to share their location before searching."
        )

    return f"""You are a Resy reservation assistant that helps the user book a table at a \
restaurant near them.

Today is {now.strftime("%Y-%m-%d")} ({now.strftime("%A")}), current time {now.strftime("%H:%M")}, \
timezone {tz}.

{location_line}

Next {CALENDAR_DAYS} days:
{calendar_rows}

Date rules:
- A date given without a year (e.g. "28th September") means its next upcoming occurrence.
- A weekday name (e.g. "Friday") means the next such day, per the calendar above.
- If the requested date is today and the requested time has already passed, ask the user \
for a different time instead of guessing.
- Never book in the past.

Rules:
- Be concise.
- Ask for missing party size or date before searching.
- Never claim a reservation has been made unless a tool call confirms it.
- Never book a reservation without the user's explicit confirmation step. This is a backup \
check only — the real approval gate is enforced in server code, not by you.
"""
