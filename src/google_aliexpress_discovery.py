"""Discover AliExpress product IDs from DataForSEO Google SERPs.

This is a fallback for niches where aliexpress.ds.text.search token matching is
poor. Google already surfaces AliExpress product pages for transactional
queries, so we can extract those product IDs and then qualify them with the
official AliExpress Drop Shipping API.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

_ALIEXPRESS_HOST_RE = re.compile(r"(^|\.)aliexpress\.", re.IGNORECASE)
_ITEM_ID_PATTERNS = (
    re.compile(r"/item/(\d{8,})\.html", re.IGNORECASE),
    re.compile(r"/item/(\d{8,})(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"[?&](?:product_id|productId|item_id|itemId)=(\d{8,})", re.IGNORECASE),
)


def extract_aliexpress_product_id(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not _ALIEXPRESS_HOST_RE.search(host):
        return None
    for pattern in _ITEM_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def discover_from_serps(serps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deduplicated AliExpress product refs found anywhere in SERPs."""
    found: dict[str, dict[str, Any]] = {}
    for serp in serps:
        keyword = str(serp.get("keyword") or "")
        for node in _walk(serp.get("items") or []):
            urls = [
                node.get("url"), node.get("product_url"), node.get("link"),
                node.get("check_url"), node.get("shopping_url"),
            ]
            for url in urls:
                product_id = extract_aliexpress_product_id(url if isinstance(url, str) else None)
                if not product_id:
                    continue
                row = found.setdefault(product_id, {
                    "itemId": product_id,
                    "title": str(node.get("title") or ""),
                    "itemUrl": url,
                    "discovery_source": "google_serp",
                    "discovery_queries": [],
                })
                if keyword and keyword not in row["discovery_queries"]:
                    row["discovery_queries"].append(keyword)
                if not row.get("title") and node.get("title"):
                    row["title"] = str(node.get("title"))
    return list(found.values())


def google_discovery_queries(target_terms: list[str]) -> list[str]:
    """Build a compact set of Google queries targeting AliExpress item pages."""
    candidates: list[str] = []
    for term in target_terms:
        clean = " ".join(str(term).split()).strip()
        if clean:
            candidates.append(f'site:aliexpress.com/item "{clean}"')
            candidates.append(f'site:fr.aliexpress.com/item "{clean}"')
    # Strong tufting aliases improve recall without over-expanding every niche.
    joined = " ".join(target_terms).lower()
    if "tuft" in joined or "touff" in joined:
        candidates.extend([
            'site:aliexpress.com/item "AK-V tufting gun"',
            'site:aliexpress.com/item "AK-I tufting gun"',
            'site:aliexpress.com/item "cut loop pile tufting gun"',
            'site:aliexpress.com/item "rug tufting machine"',
        ])
    seen: set[str] = set()
    out: list[str] = []
    for query in candidates:
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            out.append(query)
    return out[:8]
