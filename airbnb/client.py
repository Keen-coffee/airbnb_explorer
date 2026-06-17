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
_pdp_hash: str | None = None
_lock = asyncio.Lock()

_PDP_HASH_PATTERN = re.compile(
    r"name:['\"]StaysPdpSections['\"].*?operationId:['\"]([a-f0-9]{64})['\"]",
    re.DOTALL,
)

# Pattern variations to find the StaysSearch persisted query hash.
# Airbnb minified bundles vary — try from most specific to broadest.
_HASH_PATTERNS = [
    # name:'StaysSearch',...,operationId:'<hash>'  (original format)
    re.compile(r"name:['\"]StaysSearch['\"].*?operationId:['\"]([a-f0-9]{64})['\"]", re.DOTALL),
    # operationId:'<hash>',...,name:'StaysSearch'  (reversed order)
    re.compile(r"operationId:['\"]([a-f0-9]{64})['\"].*?name:['\"]StaysSearch['\"]", re.DOTALL),
    # sha256Hash:'<hash>' near StaysSearch (persisted query inline reference)
    re.compile(r"StaysSearch.{0,800}?sha256Hash['\"]?\s*:\s*['\"]([a-f0-9]{64})['\"]", re.DOTALL),
    re.compile(r"sha256Hash['\"]?\s*:\s*['\"]([a-f0-9]{64})['\"].{0,800}?StaysSearch", re.DOTALL),
    # Broader window around StaysSearch label
    re.compile(r"StaysSearch.{0,1500}?([a-f0-9]{64})", re.DOTALL),
    re.compile(r"([a-f0-9]{64}).{0,1500}?StaysSearch", re.DOTALL),
]


async def get_api_key() -> str:
    global _api_key
    if _api_key:
        return _api_key
    async with _lock:
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
    for pattern in _HASH_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def _collect_bundle_urls(page: str) -> list[str]:
    """Collect all JS bundle URLs from an Airbnb page, deduped and ordered."""
    raw: list[str] = []
    raw += re.findall(r'src=["\x27](https?://[^"\x27]+\.js)["\x27]', page)
    raw += re.findall(r'"(https://a0\.muscache\.com[^"]+\.js)"', page)
    raw += re.findall(r"'(https://a0\.muscache\.com[^']+\.js)'", page)
    seen: set[str] = set()
    result: list[str] = []
    for u in raw:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


async def get_search_hash() -> str:
    """Extract the StaysSearch persisted query SHA256 hash from Airbnb's JS bundles."""
    global _search_hash
    if _search_hash:
        return _search_hash
    async with _lock:
        if _search_hash:
            return _search_hash

        js_headers = {**BROWSER_HEADERS, "accept": "*/*", "sec-fetch-dest": "script"}

        # Try multiple seed pages — different pages load different bundle sets
        seed_urls = [
            "https://www.airbnb.com/s/United-States/homes",
            "https://www.airbnb.com/s/homes",
            "https://www.airbnb.com",
        ]

        async with AsyncSession(impersonate="chrome124") as session:
            all_bundle_urls: list[str] = []
            seen_bundles: set[str] = set()

            for seed in seed_urls:
                try:
                    resp = await session.get(seed, headers=BROWSER_HEADERS, timeout=30)
                    page = resp.text

                    # Check if hash is inlined directly in the page
                    found = _find_hash_in_text(page)
                    if found:
                        _search_hash = found
                        return _search_hash

                    for u in _collect_bundle_urls(page):
                        if u not in seen_bundles:
                            seen_bundles.add(u)
                            all_bundle_urls.append(u)
                except Exception:
                    continue

            for url in all_bundle_urls:
                try:
                    r = await session.get(url, headers=js_headers, timeout=20)
                    found = _find_hash_in_text(r.text)
                    if found:
                        _search_hash = found
                        return _search_hash
                except Exception:
                    continue

        raise RuntimeError(
            "Could not find StaysSearch hash in Airbnb bundles. "
            "Set AIRBNB_SEARCH_HASH env var to override."
        )


async def get_pdp_hash(listing_id: str = "48620583") -> str:
    """Extract the StaysPdpSections persisted query hash from Airbnb's PDP bundle.

    The PDP bundle is only loaded on /rooms/ pages, so we fetch one listing page to
    find the bundle URL, then extract the hash from it.
    Set AIRBNB_PDP_HASH env var to skip auto-discovery.
    """
    import os
    global _pdp_hash
    if _pdp_hash:
        return _pdp_hash
    env = os.environ.get("AIRBNB_PDP_HASH")
    if env:
        _pdp_hash = env
        return _pdp_hash

    async with _lock:
        if _pdp_hash:
            return _pdp_hash

        js_headers = {**BROWSER_HEADERS, "accept": "*/*", "sec-fetch-dest": "script"}
        listing_headers = {**BROWSER_HEADERS, "referer": "https://www.airbnb.com/"}

        try:
            async with AsyncSession(impersonate="chrome124") as session:
                # Fetch a listing page to discover the PDP route bundle URL
                resp = await session.get(
                    f"https://www.airbnb.com/rooms/{listing_id}",
                    headers=listing_headers,
                    timeout=15,
                )
                page = resp.text

                # Find the PdpPlatformRoute bundle URL
                pdp_bundle_urls = re.findall(
                    r'(https://a0\.muscache\.com[^"\']+PdpPlatformRoute[^"\']+\.js)',
                    page,
                )
                pdp_bundle_urls = list(dict.fromkeys(pdp_bundle_urls))

                for url in pdp_bundle_urls:
                    try:
                        r = await session.get(url, headers=js_headers, timeout=15)
                        m = _PDP_HASH_PATTERN.search(r.text)
                        if m:
                            _pdp_hash = m.group(1)
                            return _pdp_hash
                    except Exception:
                        continue
        except Exception as exc:
            raise RuntimeError(
                f"Could not fetch PDP page for hash discovery: {exc}"
            ) from exc

        raise RuntimeError(
            "Could not find StaysPdpSections hash. "
            "Set AIRBNB_PDP_HASH env var to override."
        )


def invalidate_cache() -> None:
    """Clear cached credentials so they are re-fetched on next request."""
    global _api_key, _search_hash, _pdp_hash
    _api_key = None
    _search_hash = None
    _pdp_hash = None
