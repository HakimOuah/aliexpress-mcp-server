"""Pure helpers that turn DataForSEO SERPs into business signals."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any
from urllib.parse import urlparse

from .dataforseo_client import extract_shopping_items, iter_serp_items


_MARKETPLACE_HINTS = (
    "amazon.",
    "cdiscount.",
    "manomano.",
    "fnac.",
    "ebay.",
    "aliexpress.",
    "temu.",
    "rakuten.",
    "etsy.",
    "leroymerlin.",
    "carrefour.",
)


def _domain(item: dict[str, Any]) -> str | None:
    domain = item.get("domain")
    if isinstance(domain, str) and domain:
        return domain.lower().removeprefix("www.")
    url = item.get("url")
    if isinstance(url, str) and url:
        host = urlparse(url).hostname
        if host:
            return host.lower().removeprefix("www.")
    return None


def _price(item: dict[str, Any]) -> float | None:
    value = item.get("price")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        current = value.get("current") or value.get("value")
        if isinstance(current, (int, float)):
            return float(current)
    return None


def analyze_serps(serps: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate competitive, paid and Shopping signals across SERPs."""
    organic_domains: Counter[str] = Counter()
    paid_domains: Counter[str] = Counter()
    shopping_domains: Counter[str] = Counter()
    shopping_prices: list[float] = []
    marketplaces: Counter[str] = Counter()
    item_types: Counter[str] = Counter()
    total_results_estimate = 0
    total_cost = 0.0

    for serp in serps:
        total_results_estimate = max(
            total_results_estimate,
            int(serp.get("se_results_count") or 0),
        )
        cost = serp.get("cost")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)

        raw_items = serp.get("items") or []
        items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
        for item in iter_serp_items(items):
            item_type = str(item.get("type") or "unknown")
            item_types[item_type] += 1
            domain = _domain(item)
            if item_type == "organic" and domain:
                organic_domains[domain] += 1
            elif item_type == "paid" and domain:
                paid_domains[domain] += 1

        for item in extract_shopping_items(serp):
            domain = _domain(item)
            if domain:
                shopping_domains[domain] += 1
            price = _price(item)
            if price is not None and price > 0:
                shopping_prices.append(price)

    all_visible_domains = set(organic_domains) | set(paid_domains) | set(shopping_domains)
    for domain in all_visible_domains:
        if any(hint in domain for hint in _MARKETPLACE_HINTS):
            marketplaces[domain] = (
                organic_domains[domain] + paid_domains[domain] + shopping_domains[domain]
            )

    price_summary: dict[str, float | int | None]
    if shopping_prices:
        price_summary = {
            "count": len(shopping_prices),
            "min": round(min(shopping_prices), 2),
            "median": round(median(shopping_prices), 2),
            "max": round(max(shopping_prices), 2),
        }
    else:
        price_summary = {"count": 0, "min": None, "median": None, "max": None}

    return {
        "queries_analyzed": len(serps),
        "unique_organic_domains": len(organic_domains),
        "unique_paid_domains": len(paid_domains),
        "unique_shopping_domains": len(shopping_domains),
        "paid_presence": bool(paid_domains),
        "shopping_presence": bool(shopping_domains or shopping_prices),
        "marketplace_presence": bool(marketplaces),
        "top_organic_domains": [
            {"domain": domain, "appearances": count}
            for domain, count in organic_domains.most_common(15)
        ],
        "paid_domains": [
            {"domain": domain, "appearances": count}
            for domain, count in paid_domains.most_common(15)
        ],
        "shopping_domains": [
            {"domain": domain, "appearances": count}
            for domain, count in shopping_domains.most_common(15)
        ],
        "marketplaces": [
            {"domain": domain, "appearances": count}
            for domain, count in marketplaces.most_common()
        ],
        "shopping_price_eur": price_summary,
        "serp_item_types": dict(item_types),
        "max_google_results_estimate": total_results_estimate,
        "dataforseo_cost": round(total_cost, 6),
    }
