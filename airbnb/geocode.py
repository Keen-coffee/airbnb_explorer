from __future__ import annotations

from dataclasses import dataclass
from curl_cffi.requests import AsyncSession


@dataclass
class BoundingBox:
    ne_lat: float
    ne_lng: float
    sw_lat: float
    sw_lng: float
    display_name: str


async def geocode(location: str) -> BoundingBox:
    """Convert a location string to a bounding box using OpenStreetMap Nominatim."""
    headers = {
        "accept": "application/json",
        "user-agent": "AirbnbExplorer/1.0 (local research tool)",
    }
    params = {
        "q": location,
        "format": "json",
        "limit": "1",
        "addressdetails": "0",
    }
    url = "https://nominatim.openstreetmap.org/search"
    query_string = "&".join(f"{k}={v}" for k, v in params.items())

    async with AsyncSession() as session:
        resp = await session.get(
            f"{url}?{query_string}", headers=headers, timeout=15
        )
        data = resp.json()

    if not data:
        raise ValueError(f"Location not found: {location!r}")

    result = data[0]
    bbox = result.get("boundingbox")  # [south, north, west, east]
    if not bbox or len(bbox) < 4:
        raise ValueError(f"No bounding box returned for: {location!r}")

    south, north, west, east = (float(x) for x in bbox)

    # Expand small bounding boxes (e.g. a single point) to a reasonable area (~5km)
    lat_span = north - south
    lng_span = east - west
    if lat_span < 0.05:
        pad = (0.05 - lat_span) / 2
        north += pad
        south -= pad
    if lng_span < 0.05:
        pad = (0.05 - lng_span) / 2
        east += pad
        west -= pad

    return BoundingBox(
        ne_lat=north,
        ne_lng=east,
        sw_lat=south,
        sw_lng=west,
        display_name=result.get("display_name", location),
    )
