"""GET /api/regions (stage 12): countries → cities for the region selector, from Resy's cached
city list. Slugs and names only: the server resolves coordinates, radius, and time zone."""

from typing import Any

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_region_directory
from app.errors import ApiError
from app.guards.rate_limit import enforce_regions_rate_limit
from app.regions import RegionDirectory, RegionsUnavailableError

router = APIRouter(prefix="/api/regions", tags=["regions"])


@router.get("", dependencies=[Depends(enforce_regions_rate_limit)])
async def list_regions(
    response: Response, regions: RegionDirectory = Depends(get_region_directory)
) -> dict[str, Any]:
    try:
        countries = await regions.visible_countries()
    except RegionsUnavailableError as exc:
        raise ApiError(
            503, "regions_unavailable", "Resy's city list is unavailable. Try again shortly."
        ) from exc
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {"countries": countries}
