from __future__ import annotations

import asyncio
import base64
import os
import random
import re
import string
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from curl_cffi.requests import AsyncSession

from .client import BROWSER_HEADERS, get_api_key, get_search_hash, get_pdp_hash, invalidate_cache


TREATMENT_FLAGS = [
    "feed_map_decouple_m11_treatment",
    "recommended_amenities_2024_treatment_b",
    "filter_redesign_2024_treatment",
    "filter_reordering_2024_roomtype_treatment",
    "p2_category_bar_removal_treatment",
    "selected_filters_2024_treatment",
    "recommended_filters_2024_treatment_b",
    "m13_search_input_phase2_treatment",
    "m13_search_input_services_enabled",
    "m13_2025_experiences_p2_treatment",
]

# Max concurrent page requests
_PAGE_CONCURRENCY = 4

# Max concurrent PDP review-count requests — keep low to avoid overwhelming home DNS
_PDP_CONCURRENCY = 3

# Max listings to enrich with PDP review counts
_PDP_ENRICH_LIMIT = 40

# All StaysPdpSections fragment flags set to False — we only need the metadata
# which always returns regardless of fragment selection
_PDP_ALL_FALSE_FLAGS: dict = {k: False for k in [
    "includeGpAccessibilityFeaturesFragment", "includeGpAdminBannerFragment",
    "includeGpAmenitiesFragment", "includeGpAvailabilityCalendarInlineFragment",
    "includeGpBathroomFragment", "includeGpBookItFragment",
    "includeGpBookItNonExperiencedGuestFragment",
    "includeGpCancellationPolicyPickerModalFragment", "includeGpDescriptionFragment",
    "includeGpHeroFragment", "includeGpHighlightsCompactFragment",
    "includeGpHighlightsFragment", "includeGpHostOverviewDefaultFragment",
    "includeGpLocationPdpFragment", "includeGpLuxeServicesFragment",
    "includeGpMarqueeBookItFloatingFooterFragment", "includeGpMarqueeBookItNavFragment",
    "includeGpMarqueeBookItSidebarFragment", "includeGpMeetYourHostFragment",
    "includeGpMessageBannerFragment", "includeGpNavFragment", "includeGpNavMobileFragment",
    "includeGpNonExperiencedGuestLearnMoreModalFragment", "includeGpOverviewV2Fragment",
    "includeGpPoliciesFragment", "includeGpReportToAirbnbFragment",
    "includeGpReviewsEmptyFragment", "includeGpReviewsFragment",
    "includeGpReviewsHighlightBannerFragment", "includeGpSeoLinksFragment",
    "includeGpSleepingArrangementFragment", "includeGpSleepingArrangementImagesFragment",
    "includeGpTitleFragment", "includeGpUgcTranslationFragment",
    "includePdpMigrationAccessibilityFeaturesModalFragment",
    "includePdpMigrationAccessibilityFeaturesPreviewCarouselFragment",
    "includePdpMigrationAmenitiesFragment",
    "includePdpMigrationAvailabilityCalendarInlineFragment",
    "includePdpMigrationBathroomFragment", "includePdpMigrationBookItCalendarSheetFragment",
    "includePdpMigrationBookItFloatingFooterFragment", "includePdpMigrationBookItNavFragment",
    "includePdpMigrationBookItNonExperiencedGuestFragment",
    "includePdpMigrationBookItSidebarFragment", "includePdpMigrationDescriptionFragment",
    "includePdpMigrationHeroFragment", "includePdpMigrationHighlightsCompactFragment",
    "includePdpMigrationHighlightsFragment", "includePdpMigrationHostOverviewDefaultFragment",
    "includePdpMigrationLocationPdpFragment", "includePdpMigrationLuxeServicesFragment",
    "includePdpMigrationMarqueeBookItFloatingFooterFragment",
    "includePdpMigrationMarqueeBookItNavFragment",
    "includePdpMigrationMarqueeBookItSidebarFragment",
    "includePdpMigrationMeetYourHostFragment", "includePdpMigrationMessageBannerFragment",
    "includePdpMigrationNavFragment", "includePdpMigrationNavMobileFragment",
    "includePdpMigrationNonExperiencedGuestLearnMoreModalFragment",
    "includePdpMigrationOnlyOnBookItFragment", "includePdpMigrationOnlyOnBookItNavFragment",
    "includePdpMigrationOverviewV2Fragment", "includePdpMigrationPdpEducationFragment",
    "includePdpMigrationPoliciesFragment", "includePdpMigrationReportToAirbnbFragment",
    "includePdpMigrationReviewsEmptyFragment", "includePdpMigrationReviewsFragment",
    "includePdpMigrationReviewsHighlightBannerFragment",
    "includePdpMigrationSeoLinksFragment", "includePdpMigrationSleepingArrangementFragment",
    "includePdpMigrationSleepingArrangementImagesFragment", "includePdpMigrationTitleFragment",
]}


@dataclass
class Listing:
    id: str
    name: str
    url: str
    bedrooms: Optional[str]       # e.g. "2 bedrooms" (when API provides it)
    beds: Optional[str]           # e.g. "2 king beds", "Studio"
    bathrooms: Optional[str]      # e.g. "1 bath", "1.5 baths"
    avg_rating: Optional[float]
    review_count: Optional[int]
    price_per_night: Optional[float]   # for sorting when no dates given
    total_price: Optional[float]       # for sorting when dates given
    total_price_display: Optional[str] # e.g. "$928 for 5 nights"
    currency: str
    image_url: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]

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
    review_count: Optional[int] = None
    rating_raw = result.get("avgRatingLocalized") or ""
    if rating_raw:
        # Format is "4.96 (27)" or just "4.96" or "New"
        m = re.match(r"([\d.]+)\s*\((\d+)\)", rating_raw)
        if m:
            try:
                avg_rating = float(m.group(1))
                review_count = int(m.group(2))
            except (ValueError, TypeError):
                pass
        else:
            try:
                avg_rating = float(rating_raw)
            except (ValueError, TypeError):
                pass

    review_count: Optional[int] = None
    for key in ("reviewsCount", "reviewCount", "totalReviewCount"):
        val = result.get(key)
        if val is not None:
            try:
                review_count = int(val)
                break
            except (ValueError, TypeError):
                pass
    if review_count is None:
        # Some a11y labels include review count: "4.94 out of 5 average rating, 50 reviews"
        a11y = result.get("avgRatingA11yLabel") or ""
        m = re.search(r",\s*(\d+)\s+review", a11y, re.IGNORECASE)
        if m:
            try:
                review_count = int(m.group(1))
            except ValueError:
                pass

    structured_price = result.get("structuredDisplayPrice") or {}
    price_per_night, total_price, total_price_display = _extract_price_info(structured_price)

    # Bed / bath info from structuredContent.primaryLine typed items.
    # BEDINFO sometimes returns "N bedrooms" (whole-home listings) or "N beds"/"N king beds".
    # Split them so the UI can show separate Bedrooms and Beds columns.
    bedrooms: Optional[str] = None
    beds: Optional[str] = None
    bathrooms: Optional[str] = None
    for item in (result.get("structuredContent") or {}).get("primaryLine") or []:
        t = item.get("type", "")
        body = item.get("body") or ""
        if t == "BEDINFO" and body:
            if re.search(r"\bbedroom", body, re.IGNORECASE):
                bedrooms = body
            else:
                beds = body
        elif t == "BATHROOMINFO" and body:
            bathrooms = body

    pictures = result.get("contextualPictures") or []
    image_url: Optional[str] = pictures[0].get("picture") if pictures else None

    coord = _nested(demand, "location", "coordinate")
    latitude: Optional[float] = coord.get("latitude") if coord else None
    longitude: Optional[float] = coord.get("longitude") if coord else None

    return Listing(
        id=listing_id,
        name=name,
        url=f"https://www.airbnb.com/rooms/{listing_id}",
        bedrooms=bedrooms,
        beds=beds,
        bathrooms=bathrooms,
        avg_rating=avg_rating,
        review_count=review_count,
        price_per_night=price_per_night,
        total_price=total_price,
        total_price_display=total_price_display,
        currency="USD",
        image_url=image_url,
        latitude=latitude,
        longitude=longitude,
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
    location: str,
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
) -> tuple[list[dict], list[dict]]:
    """Return (map_params, search_params). The search params add itemsPerGrid."""
    base: list[dict] = [
        {"filterName": "cdnCacheSafe", "filterValues": ["false"]},
        {"filterName": "query", "filterValues": [location]},
        {"filterName": "refinementPaths", "filterValues": ["/homes"]},
        {"filterName": "screenSize", "filterValues": ["large"]},
        {"filterName": "tabId", "filterValues": ["home_tab"]},
        {"filterName": "version", "filterValues": ["1.8.8"]},
    ]

    if checkin and checkout:
        base.append({"filterName": "checkin", "filterValues": [checkin]})
        base.append({"filterName": "checkout", "filterValues": [checkout]})
        try:
            from datetime import date
            nights = (date.fromisoformat(checkout) - date.fromisoformat(checkin)).days
            if nights > 0:
                base.append({"filterName": "priceFilterNumNights", "filterValues": [str(nights)]})
        except ValueError:
            pass

    if adults > 0:
        base.append({"filterName": "adults", "filterValues": [str(adults)]})
    if children > 0:
        base.append({"filterName": "children", "filterValues": [str(children)]})
    if infants > 0:
        base.append({"filterName": "infants", "filterValues": [str(infants)]})
    if min_bedrooms:
        base.append({"filterName": "min_bedrooms", "filterValues": [str(min_bedrooms)]})
    if min_beds:
        base.append({"filterName": "min_beds", "filterValues": [str(min_beds)]})
    if min_bathrooms:
        base.append({"filterName": "min_bathrooms", "filterValues": [str(min_bathrooms)]})
    if price_min:
        base.append({"filterName": "price_min", "filterValues": [str(price_min)]})
    if price_max:
        base.append({"filterName": "price_max", "filterValues": [str(price_max)]})

    search_params = base + [{"filterName": "itemsPerGrid", "filterValues": ["18"]}]
    return base, search_params


def _build_body(
    search_hash: str,
    map_params: list[dict],
    search_params: list[dict],
    cursor: Optional[str],
) -> dict:
    shared = {
        "cursor": cursor,
        "requestedPageType": "STAYS_SEARCH",
        "metadataOnly": False,
        "source": "structured_search_input_header",
        "searchType": "user_map_move",
        "treatmentFlags": TREATMENT_FLAGS,
    }
    return {
        "operationName": "StaysSearch",
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": search_hash}},
        "variables": {
            "skipExtendedSearchParams": False,
            "includeMapResults": False,
            "isLeanTreatment": False,
            "aiSearchEnabled": False,
            "staysMapSearchRequestV2": {**shared, "rawParams": map_params, "maxMapItems": 0},
            "staysSearchRequest": {**shared, "rawParams": search_params, "maxMapItems": 9999},
        },
    }


async def _fetch_page(
    session: AsyncSession,
    url: str,
    api_headers: dict,
    search_hash: str,
    map_params: list[dict],
    search_params: list[dict],
    cursor: Optional[str],
    sem: asyncio.Semaphore,
) -> list[Listing]:
    async with sem:
        body = _build_body(search_hash, map_params, search_params, cursor)
        resp = await session.post(url, json=body, headers=api_headers, timeout=30)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except Exception:
            return []
        listings, _ = _extract_page(data)
        return listings



async def _fetch_one_review_count(
    session: AsyncSession,
    listing_id: str,
    pdp_hash: str,
    api_key: str,
    checkin: Optional[str],
    checkout: Optional[str],
    sem: asyncio.Semaphore,
) -> Optional[int]:
    async with sem:
        gid = base64.b64encode(f"StayListing:{listing_id}".encode()).decode()
        demand_gid = base64.b64encode(f"DemandStayListing:{listing_id}".encode()).decode()
        rand_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        impression_id = f"p3_{int(time.time())}_{rand_suffix}"

        variables: dict = {
            "id": gid,
            "demandStayListingId": demand_gid,
            "p3ImpressionId": impression_id,
            "bypassTargetings": False,
            "hostPreview": False,
            "preview": False,
            "privateBooking": False,
            "pdpSectionsRequest": {
                "adults": "1",
                "checkIn": checkin or "",
                "checkOut": checkout or "",
                "layouts": ["SIDEBAR", "SINGLE_COLUMN"],
            },
            **_PDP_ALL_FALSE_FLAGS,
        }

        body = {
            "operationName": "StaysPdpSections",
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": pdp_hash}},
            "variables": variables,
        }

        headers = {
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
            "referer": f"https://www.airbnb.com/rooms/{listing_id}",
        }

        url = (
            f"https://www.airbnb.com/api/v3/StaysPdpSections/{pdp_hash}"
            "?operationName=StaysPdpSections&locale=en&currency=USD"
        )

        try:
            resp = await session.post(url, json=body, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None
            rc = _nested(
                resp.json(),
                "data", "presentation", "stayProductDetailPage",
                "sections", "metadata", "sharingConfig", "reviewCount",
            )
            if rc is not None:
                return int(rc)
        except Exception:
            pass
        return None


async def _fetch_review_counts(
    listings: list[Listing],
    api_key: str,
    checkin: Optional[str],
    checkout: Optional[str],
) -> None:
    """Batch-fetch review counts via StaysPdpSections for listings missing one."""
    targets = [l for l in listings if l.review_count is None][:_PDP_ENRICH_LIMIT]
    if not targets:
        return
    try:
        pdp_hash = await get_pdp_hash()
    except Exception:
        return

    sem = asyncio.Semaphore(_PDP_CONCURRENCY)
    try:
        async with AsyncSession(impersonate="chrome124") as session:
            counts = await asyncio.gather(
                *[
                    _fetch_one_review_count(session, l.id, pdp_hash, api_key, checkin, checkout, sem)
                    for l in targets
                ],
                return_exceptions=True,
            )
    except Exception:
        return

    for listing, count in zip(targets, counts):
        if isinstance(count, int):
            listing.review_count = count


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
    min_rating: Optional[float] = None,
    min_reviews: Optional[int] = None,
) -> SearchResults:
    search_hash = os.environ.get("AIRBNB_SEARCH_HASH") or await get_search_hash()
    api_key = os.environ.get("AIRBNB_API_KEY") or await get_api_key()

    map_params, search_params = _build_raw_params(
        location, checkin, checkout, adults, children, infants,
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
        first_body = _build_body(search_hash, map_params, search_params, None)
        first_resp = await session.post(url, json=first_body, headers=api_headers, timeout=30)

        if first_resp.status_code in (401, 403):
            invalidate_cache()
            raise RuntimeError(
                f"Airbnb returned {first_resp.status_code}. Credentials refreshed — please retry."
            )
        if first_resp.status_code != 200:
            raise RuntimeError(f"Airbnb API error {first_resp.status_code}: {first_resp.text[:300]}")

        try:
            first_data = first_resp.json()
        except Exception:
            invalidate_cache()
            raise RuntimeError(
                "Airbnb returned a non-JSON response (bot detection / CAPTCHA). "
                "Try again in a few seconds, or set AIRBNB_API_KEY and AIRBNB_SEARCH_HASH env vars."
            )
        page1_listings, all_cursors = _extract_page(first_data)

        # all_cursors[0] is page 1 (already fetched), rest are subsequent pages
        remaining_cursors = all_cursors[1:] if len(all_cursors) > 1 else []

        # --- Remaining pages (concurrent) ---
        all_listings = list(page1_listings)
        if remaining_cursors:
            sem = asyncio.Semaphore(_PAGE_CONCURRENCY)
            tasks = [
                _fetch_page(session, url, api_headers, search_hash, map_params, search_params, cursor, sem)
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

    # Enrich with review counts from PDP API (StaysSearch omits reviewCount entirely)
    try:
        await _fetch_review_counts(unique, api_key, checkin, checkout)
    except Exception:
        pass

    if min_rating is not None:
        unique = [l for l in unique if l.avg_rating is not None and l.avg_rating >= min_rating]
    if min_reviews is not None:
        unique = [l for l in unique if l.review_count is not None and l.review_count >= min_reviews]

    return SearchResults(
        location=location,
        checkin=checkin,
        checkout=checkout,
        listings=unique,
        count=len(unique),
    )
