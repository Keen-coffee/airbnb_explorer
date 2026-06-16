from __future__ import annotations

import asyncio
import base64
import math
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Optional

from curl_cffi.requests import AsyncSession

from .client import BROWSER_HEADERS, get_api_key, get_search_hash, invalidate_cache
from .geocode import BoundingBox


TREATMENT_FLAGS = [
    "feed_map_decouple_m11_treatment",
    "stays_search_rehydration_treatment_desktop",
    "stays_search_rehydration_treatment_web",
]

# Max concurrent page requests
_PAGE_CONCURRENCY = 4


@dataclass
class Listing:
    id: str
    name: str
    url: str
    beds: Optional[str]           # e.g. "2 beds", "Studio"
    bathrooms: Optional[str]      # e.g. "1 bath", "1.5 baths"
    avg_rating: Optional[float]
    price_per_night: Optional[float]   # for sorting when no dates given
    total_price: Optional[float]       # for sorting when dates given
    total_price_display: Optional[str] # e.g. "$928 for 5 nights"
    currency: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SearchResults:
    location: str
    checkin: Optional[str]
    checkout: Optional[str]
    listings: list[Listing]
    count: int


def _nested(obj: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, default)
        if obj is None:
            return default
    return obj


def _decode_listing_id(encoded_id: str) -> str:
    try:
        decoded = base64.b64decode(encoded_id + "==").decode("utf-8")
        return decoded.split(":")[-1].strip()
    except Exception:
        return encoded_id


def _parse_amount(s: str) -> Optional[float]:
    m = re.search(r"[\d,]+(?:\.\d+)?", s)
    if m:
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_price_info(structured_price: dict) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Returns (price_per_night, total_price, total_price_display).

    Airbnb's primaryLine.price is the total when qualifier says 'for N nights',
    or the nightly rate when qualifier is 'night'.
    """
    primary = structured_price.get("primaryLine") or {}
    # QualifiedDisplayPriceLine uses "price"; DiscountedDisplayPriceLine uses "discountedPrice"
    price_str = primary.get("price") or primary.get("discountedPrice") or ""
    qualifier = (primary.get("qualifier") or "").lower()

    price_per_night: Optional[float] = None
    total_price: Optional[float] = None
    total_price_display: Optional[str] = None

    if "for" in qualifier and "night" in qualifier:
        # primaryLine.price is the total
        total_price = _parse_amount(price_str)
        total_price_display = f"{price_str} {primary.get('qualifier', '')}".strip()

        # Per-night from explanation data
        explanation = structured_price.get("explanationData") or {}
        for group in (explanation.get("priceDetails") or []):
            for item in (group.get("items") or []):
                desc = item.get("description", "") or ""
                m = re.search(r"(\d+)\s+night.*?\$([\d,]+(?:\.\d+)?)", desc, re.IGNORECASE)
                if m:
                    try:
                        price_per_night = float(m.group(2).replace(",", ""))
                        break
                    except ValueError:
                        pass
            if price_per_night:
                break

        # Fallback: divide total by nights
        if price_per_night is None and total_price:
            nm = re.search(r"(\d+)\s+night", qualifier)
            if nm:
                nights = int(nm.group(1))
                if nights > 0:
                    price_per_night = round(total_price / nights, 2)

    elif "night" in qualifier:
        # primaryLine.price is per night
        price_per_night = _parse_amount(price_str)
        total_price_display = price_str  # no total without dates
    else:
        # Unknown — treat as total
        total_price = _parse_amount(price_str)
        total_price_display = price_str

    return price_per_night, total_price, total_price_display


def _parse_result(result: dict) -> Optional[Listing]:
    name = _nested(result, "nameLocalized", "localizedStringWithTranslationPreference")
    if not name:
        name = _nested(
            result, "demandStayListing", "description", "name",
            "localizedStringWithTranslationPreference"
        )
    if not name:
        return None

    demand = result.get("demandStayListing") or {}
    listing_id = _decode_listing_id(demand.get("id", ""))
    if not listing_id:
        return None

    avg_rating: Optional[float] = None
    rating_raw = result.get("avgRatingLocalized")
    if rating_raw:
        try:
            avg_rating = float(rating_raw)
        except (ValueError, TypeError):
            pass

    structured_price = result.get("structuredDisplayPrice") or {}
    price_per_night, total_price, total_price_display = _extract_price_info(structured_price)

    # Bed / bath info from structuredContent.primaryLine typed items
    beds: Optional[str] = None
    bathrooms: Optional[str] = None
    for item in (result.get("structuredContent") or {}).get("primaryLine") or []:
        t = item.get("type", "")
        body = item.get("body") or ""
        if t == "BEDINFO" and body:
            beds = body
        elif t == "BATHROOMINFO" and body:
            bathrooms = body

    return Listing(
        id=listing_id,
        name=name,
        url=f"https://www.airbnb.com/rooms/{listing_id}",
        beds=beds,
        bathrooms=bathrooms,
        avg_rating=avg_rating,
        price_per_night=price_per_night,
        total_price=total_price,
        total_price_display=total_price_display,
        currency="USD",
    )


def _extract_page(data: dict) -> tuple[list[Listing], list[str]]:
    """Returns (listings, next_page_cursors)."""
    results_block = _nested(data, "data", "presentation", "staysSearch", "results") or {}
    raw_results = results_block.get("searchResults") or []
    page_cursors: list[str] = _nested(results_block, "paginationInfo", "pageCursors") or []

    listings: list[Listing] = []
    for raw in raw_results:
        parsed = _parse_result(raw)
        if parsed:
            listings.append(parsed)
    return listings, page_cursors


def _build_raw_params(
    bbox: BoundingBox,
    checkin: Optional[str],
    checkout: Optional[str],
    adults: int,
    children: int,
    infants: int,
    min_bedrooms: Optional[int],
    min_beds: Optional[int],
    min_bathrooms: Optional[float],
    price_min: Optional[int],
    price_max: Optional[int],
) -> list[dict]:
    params: list[dict] = [
        {"filterName": "cdnCacheSafe", "filterValues": ["false"]},
        {"filterName": "channel", "filterValues": ["EXPLORE"]},
        {"filterName": "datePickerType", "filterValues": ["calendar"]},
        {"filterName": "source", "filterValues": ["structured_search_input_header"]},
        {"filterName": "searchType", "filterValues": ["autocomplete_click"]},
        {"filterName": "neLat", "filterValues": [str(bbox.ne_lat)]},
        {"filterName": "neLng", "filterValues": [str(bbox.ne_lng)]},
        {"filterName": "swLat", "filterValues": [str(bbox.sw_lat)]},
        {"filterName": "swLng", "filterValues": [str(bbox.sw_lng)]},
        {"filterName": "zoomLevel", "filterValues": ["12"]},
    ]

    if checkin and checkout:
        params.append({"filterName": "checkin", "filterValues": [checkin]})
        params.append({"filterName": "checkout", "filterValues": [checkout]})
        try:
            from datetime import date
            nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
            if nights > 0:
                params.append({"filterName": "priceFilterNumNights", "filterValues": [str(nights)]})
        except ValueError:
            pass

    if adults > 0:
        params.append({"filterName": "adults", "filterValues": [str(adults)]})
    if children > 0:
        params.append({"filterName": "children", "filterValues": [str(children)]})
    if infants > 0:
        params.append({"filterName": "infants", "filterValues": [str(infants)]})
    if min_bedrooms:
        params.append({"filterName": "min_bedrooms", "filterValues": [str(min_bedrooms)]})
    if min_beds:
        params.append({"filterName": "min_beds", "filterValues": [str(min_beds)]})
    if min_bathrooms:
        params.append({"filterName": "min_bathrooms", "filterValues": [str(min_bathrooms)]})
    if price_min:
        params.append({"filterName": "price_min", "filterValues": [str(price_min)]})
    if price_max:
        params.append({"filterName": "price_max", "filterValues": [str(price_max)]})

    return params


def _build_body(search_hash: str, raw_params: list[dict], cursor: Optional[str]) -> dict:
    shared = {
        "cursor": cursor,
        "requestedPageType": "STAYS_SEARCH",
        "metadataOnly": False,
        "source": "structured_search_input_header",
        "searchType": "user_map_move",
        "treatmentFlags": TREATMENT_FLAGS,
        "rawParams": raw_params,
    }
    return {
        "operationName": "StaysSearch",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": search_hash}},
        "variables": {
            "skipExtendedSearchParams": False,
            "includeMapResults": False,
            "isLeanTreatment": False,
            "aiSearchEnabled": False,
            "staysMapSearchRequestV2": {**shared, "maxMapItems": 0},
            "staysSearchRequest": {**shared, "maxMapItems": 9999},
        },
    }


async def _fetch_page(
    session: AsyncSession,
    url: str,
    api_headers: dict,
    search_hash: str,
    raw_params: list[dict],
    cursor: Optional[str],
    sem: asyncio.Semaphore,
) -> list[Listing]:
    async with sem:
        body = _build_body(search_hash, raw_params, cursor)
        resp = await session.post(url, json=body, headers=api_headers, timeout=30)
        if resp.status_code != 200:
            return []
        listings, _ = _extract_page(resp.json())
        return listings


async def search(
    location: str,
    checkin: Optional[str] = None,
    checkout: Optional[str] = None,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    min_bedrooms: Optional[int] = None,
    min_beds: Optional[int] = None,
    min_bathrooms: Optional[float] = None,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
) -> SearchResults:
    from .geocode import geocode

    bbox = await geocode(location)
    search_hash = os.environ.get("AIRBNB_SEARCH_HASH") or await get_search_hash()
    api_key = os.environ.get("AIRBNB_API_KEY") or await get_api_key()

    raw_params = _build_raw_params(
        bbox, checkin, checkout, adults, children, infants,
        min_bedrooms, min_beds, min_bathrooms, price_min, price_max,
    )

    api_headers = {
        **BROWSER_HEADERS,
        "accept": "application/json",
        "content-type": "application/json",
        "x-airbnb-api-key": api_key,
        "x-airbnb-graphql-platform": "web",
        "x-airbnb-graphql-platform-client": "minimalist-niobe",
        "x-niobe-short-circuited": "true",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "referer": "https://www.airbnb.com/s/homes",
    }

    url = (
        f"https://www.airbnb.com/api/v3/StaysSearch/{search_hash}"
        "?operationName=StaysSearch&locale=en&currency=USD"
    )

    async with AsyncSession(impersonate="chrome124") as session:
        # --- Page 1 ---
        first_body = _build_body(search_hash, raw_params, None)
        first_resp = await session.post(url, json=first_body, headers=api_headers, timeout=30)

        if first_resp.status_code in (401, 403):
            invalidate_cache()
            raise RuntimeError(
                f"Airbnb returned {first_resp.status_code}. Credentials refreshed — please retry."
            )
        if first_resp.status_code != 200:
            raise RuntimeError(f"Airbnb API error {first_resp.status_code}: {first_resp.text[:300]}")

        first_data = first_resp.json()
        page1_listings, all_cursors = _extract_page(first_data)

        # all_cursors[0] is page 1 (already fetched), rest are subsequent pages
        remaining_cursors = all_cursors[1:] if len(all_cursors) > 1 else []

        # --- Remaining pages (concurrent) ---
        all_listings = list(page1_listings)
        if remaining_cursors:
            sem = asyncio.Semaphore(_PAGE_CONCURRENCY)
            tasks = [
                _fetch_page(session, url, api_headers, search_hash, raw_params, cursor, sem)
                for cursor in remaining_cursors
            ]
            pages = await asyncio.gather(*tasks)
            for page in pages:
                all_listings.extend(page)

    # Deduplicate by listing ID (same property can appear on multiple pages)
    seen_ids: set[str] = set()
    unique: list[Listing] = []
    for l in all_listings:
        if l.id not in seen_ids:
            seen_ids.add(l.id)
            unique.append(l)

    # Sort by total price when available, else per-night; no-price listings go last
    def sort_key(l: Listing) -> tuple:
        price = l.total_price if l.total_price is not None else (
            l.price_per_night if l.price_per_night is not None else math.inf
        )
        return (price == math.inf, price)

    unique.sort(key=sort_key)

    return SearchResults(
        location=bbox.display_name,
        checkin=checkin,
        checkout=checkout,
        listings=unique,
        count=len(unique),
    )
