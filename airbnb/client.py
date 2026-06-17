from __future__ import annotations

import re
import asyncio
from curl_cffi.requests import AsyncSession

BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

_api_key: str | None = None
_search_hash: str | None = None
# Lazy lock: created on first use inside a running event loop to avoid
# Python 3.9 "Future attached to a different loop" errors.
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# Exact pattern seen in Airbnb's JS bundle:
# name:'StaysSearch',type:'query',operationId:'<64-char-hex>'
_HASH_PATTERN = re.compile(
    r"name:['\"]StaysSearch['\"].*?operationId:['\"]([a-f0-9]{64})['\"]",
    re.DOTALL,
)
# Broader fallback patterns
_HASH_FALLBACKS = [
    re.compile(r"StaysSearch.{0,400}?([a-f0-9]{64})", re.DOTALL),
    re.compile(r"([a-f0-9]{64}).{0,400}?StaysSearch", re.DOTALL),
]


async def get_api_key() -> str:
    global _api_key
    if _api_key:
        return _api_key
    async with _get_lock():
        if _api_key:
            return _api_key
        async with AsyncSession(impersonate="chrome124") as session:
            resp = await session.get(
                "https://www.airbnb.com", headers=BROWSER_HEADERS, timeout=30
            )
            match = re.search(r'"api_config":\{"key":"([^"]+)"', resp.text)
            if not match:
                match = re.search(r'"key"\s*:\s*"([a-zA-Z0-9_-]{20,})"', resp.text)
            if not match:
                raise RuntimeError("Could not extract Airbnb API key from homepage")
            _api_key = match.group(1)
            return _api_key


def _find_hash_in_text(text: str) -> str | None:
    m = _HASH_PATTERN.search(text)
    if m:
        return m.group(1)
    for pattern in _HASH_FALLBACKS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _extract_bundle_urls(page: str) -> list[str]:
    """Return deduplicated JS bundle URLs from a page (src= and href= attributes)."""
    raw: list[str] = re.findall(r'(?:src|href)="(https?://[^"]+\.js)"', page)
    raw += re.findall(r'"(https://a0\.muscache\.com[^"]+\.js)"', page)
    seen: set[str] = set()
    result: list[str] = []
    for u in raw:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


async def _scan_bundles(session: AsyncSession, bundle_urls: list[str]) -> str | None:
    """Fetch each bundle URL and return the first StaysSearch hash found."""
    js_headers = {**BROWSER_HEADERS, "accept": "*/*", "sec-fetch-dest": "script"}
    for url in bundle_urls:
        try:
            r = await session.get(url, headers=js_headers, timeout=20)
            found = _find_hash_in_text(r.text)
            if found:
                return found
        except Exception:
            continue
    return None


async def get_search_hash() -> str:
    """Extract the StaysSearch persisted query SHA256 hash from Airbnb's JS bundles.

    Airbnb bundles are served from a0.muscache.com (their CDN). The hash lives in
    a bundle that exports the StaysSearch GraphQL operation definition with its
    operationId field.
    """
    global _search_hash
    if _search_hash:
        return _search_hash
    async with _get_lock():
        if _search_hash:
            return _search_hash

        # Pages to try in order; /s/homes loads the most search-relevant bundles.
        candidate_pages = [
            "https://www.airbnb.com/s/homes",
            "https://www.airbnb.com/s/united-states/homes",
            "https://www.airbnb.com",
        ]

        async with AsyncSession(impersonate="chrome124") as session:
            for page_url in candidate_pages:
                resp = await session.get(
                    page_url, headers=BROWSER_HEADERS, timeout=30
                )
                page = resp.text

                found = _find_hash_in_text(page)
                if found:
                    _search_hash = found
                    return _search_hash

                bundle_urls = _extract_bundle_urls(page)
                found = await _scan_bundles(session, bundle_urls)
                if found:
                    _search_hash = found
                    return _search_hash

        raise RuntimeError(
            "Could not find StaysSearch hash in Airbnb bundles. "
            "Set AIRBNB_SEARCH_HASH env var to override."
        )


def invalidate_cache() -> None:
    """Clear cached credentials so they are re-fetched on next request."""
    global _api_key, _search_hash
    _api_key = None
    _search_hash = None
