"""Static-token auth for the single demo account. No refresh: tokens come from env and
are rotated by hand. Header values are never logged or traced."""

from app.resy.errors import ResyAuthError

# Verified minimal header set (stage 5 probe, 2026-09-27): the three auth headers plus a
# User-Agent. Without a User-Agent, Resy returns 500; Origin/Referer aren't needed.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)


def build_headers(api_key: str, auth_token: str) -> dict[str, str]:
    if not api_key or not auth_token:
        raise ResyAuthError(
            "Resy credentials not configured; set RESY_API_KEY and RESY_AUTH_TOKEN."
        )
    return {
        "Authorization": f'ResyAPI api_key="{api_key}"',
        "X-Resy-Auth-Token": auth_token,
        "X-Resy-Universal-Auth": auth_token,
        "User-Agent": USER_AGENT,
    }
