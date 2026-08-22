"""AliExpress qualification for product-opportunity analysis.

This path is deliberately more exploratory than the strict legacy normalizer.
A supplier is rejected only when the evidence makes fulfillment unsafe or
impossible (no saleable SKU, bad known quality, no shipping, extreme delay).
Low order volume, heavier packages and moderately slow delivery are retained as
WATCH signals so niche/high-ticket products are not discarded before economics
can be calculated.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from .aliexpress_client import AliExpressClient, IOPError, _parse_float, _parse_order_count
from .models import SkuRef
from .normalizer import (
    EU_COUNTRIES,
    _build_package,
    _build_shipping_info,
    _build_sku_ref,
    _build_store_info,
)
from .offer_classifier import ACCESSORY_HINTS
from .sourcing_relevance import build_sourcing_queries, dedupe_and_filter

CONCURRENCY_LIMIT = 5

_SKU_ACCESSORY_HINTS = tuple(ACCESSORY_HINTS) + (
    "threader",
    "enfile",
    "replacement",
    "spare",
    "only",
)
_SKU_DEVICE_HINTS = (
    "tufting gun",
    "tufting machine",
    "pistolet",
    "machine",
    "gun",
    "ak-v",
    "ak v",
    "ak-i",
    "ak i",
    "ak duo",
    "cut loop",
    "cut pile",
    "loop pile",
)
_WARNING_PENALTIES = {
    "rating_unknown": 8,
    "rating_watch": 12,
    "orders_watch": 8,
    "low_orders": 18,
    "store_rating_unknown": 8,
    "suspicious_low_product_cost": 8,
    "weight_over_preferred": 10,
    "length_over_preferred": 8,
    "delivery_slow": 15,
    "sku_accessory_semantics_uncertain": 15,
}


async def search_relevant_candidates(
    client: AliExpressClient,
    *,
    aliexpress_query: str,
    target_terms: list[str],
    country_code: str,
    max_results: int,
) -> tuple[list[str], list[dict[str, Any]], int]:
    queries = build_sourcing_queries(aliexpress_query, target_terms)
    raw_pool: list[dict[str, Any]] = []
    per_query = max(10, min(30, max_results))
    for query in queries:
        raw_pool.extend(
            await client.search_products(
                query=query,
                max_results=per_query,
                target_country=country_code,
                sort_by="orders",
            )
        )
    relevant = dedupe_and_filter(raw_pool, target_terms)
    return queries, relevant[:max_results], len(raw_pool)


async def qualify_relevant_candidates(
    client: AliExpressClient,
    items: list[dict[str, Any]],
    *,
    country_code: str,
    min_orders: int = 100,
    min_orders_watch: int = 50,
    min_rating: float = 4.3,
    min_rating_watch: float = 4.0,
    min_store_rating: float = 4.0,
    min_product_cost_eur: float = 25.0,
    preferred_max_weight_kg: float = 3.0,
    preferred_max_length_cm: int = 60,
    preferred_max_delivery_days: int = 15,
    hard_max_delivery_days: int = 30,
    max_delivery_days: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return fulfillment-viable candidates plus qualification diagnostics.

    `min_orders`, preferred package limits and the preferred delivery window are
    advisory. They create WATCH flags but do not kill a supplier. Known ratings
    below the WATCH floor, missing saleable SKU, unavailable shipping and very
    slow delivery remain hard failures.

    `max_delivery_days` is a backward-compatible alias for
    `preferred_max_delivery_days` used by earlier Product Factory callers.
    """
    if max_delivery_days is not None:
        preferred_max_delivery_days = max_delivery_days

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def inspect(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _inspect_one(
                client,
                item,
                country_code=country_code,
                min_orders=min_orders,
                min_orders_watch=min_orders_watch,
                min_rating=min_rating,
                min_rating_watch=min_rating_watch,
                min_store_rating=min_store_rating,
                min_product_cost_eur=min_product_cost_eur,
                preferred_max_weight_kg=preferred_max_weight_kg,
                preferred_max_length_cm=preferred_max_length_cm,
                preferred_max_delivery_days=preferred_max_delivery_days,
                hard_max_delivery_days=hard_max_delivery_days,
            )

    diagnostics = list(await asyncio.gather(*(inspect(item) for item in items)))
    viable = [d["candidate"] for d in diagnostics if d.get("candidate")]

    hard_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for diagnostic in diagnostics:
        hard_counts.update(diagnostic.get("hard_failures") or [])
        warning_counts.update(diagnostic.get("warnings") or [])

    summary = {
        "total_candidates": len(diagnostics),
        "qualified_count": len(viable),
        "pass_count": sum(c.get("quality_verdict") == "PASS" for c in viable),
        "watch_count": sum(c.get("quality_verdict") == "WATCH" for c in viable),
        "hard_failure_counts": [
            {"filter": name, "count": count}
            for name, count in hard_counts.most_common()
        ],
        "warning_counts": [
            {"warning": name, "count": count}
            for name, count in warning_counts.most_common()
        ],
        "failure_counts": [
            {"filter": name, "count": count}
            for name, count in hard_counts.most_common()
        ],
        "candidates": diagnostics,
    }
    return viable, summary


def _sku_text(sku: SkuRef) -> str:
    parts: list[str] = []
    for name, value in sku.sku_properties.items():
        parts.extend((str(name), str(value)))
    return " ".join(parts).lower()


def _sku_looks_accessory_only(sku: SkuRef) -> bool:
    text = _sku_text(sku)
    if not text:
        return False
    accessory = any(hint in text for hint in _SKU_ACCESSORY_HINTS)
    device = any(hint in text for hint in _SKU_DEVICE_HINTS)
    return accessory and not device


def _select_reference_sku(
    raw_skus: list[dict[str, Any]],
) -> tuple[SkuRef | None, dict[str, Any], list[str]]:
    """Pick the cheapest saleable device-like SKU, not the cheapest accessory."""
    skus = [
        sku
        for sku in (
            _build_sku_ref(raw)
            for raw in raw_skus
            if isinstance(raw, dict)
        )
        if sku is not None
        and sku.available_stock >= 1
        and sku.offer_sale_price_eur > 0
    ]
    if not skus:
        return None, {"saleable_sku_count": 0}, []

    device_like = [sku for sku in skus if not _sku_looks_accessory_only(sku)]
    warnings: list[str] = []
    if device_like:
        pool = device_like
        selection_reason = "cheapest_device_like_in_stock"
    else:
        pool = skus
        selection_reason = "fallback_cheapest_saleable_sku"
        warnings.append("sku_accessory_semantics_uncertain")

    chosen = min(pool, key=lambda sku: sku.offer_sale_price_eur)
    metadata = {
        "saleable_sku_count": len(skus),
        "device_like_sku_count": len(device_like),
        "excluded_accessory_sku_count": len(skus) - len(device_like),
        "selection_reason": selection_reason,
        "selected_sku_properties": dict(chosen.sku_properties),
        "saleable_price_min_eur": round(min(s.offer_sale_price_eur for s in skus), 2),
        "saleable_price_max_eur": round(max(s.offer_sale_price_eur for s in skus), 2),
    }
    return chosen, metadata, warnings


def _quality_score(warnings: list[str]) -> int:
    penalty = sum(_WARNING_PENALTIES.get(warning, 5) for warning in set(warnings))
    return max(0, 100 - penalty)


async def _inspect_one(
    client: AliExpressClient,
    item: dict[str, Any],
    *,
    country_code: str,
    min_orders: int,
    min_orders_watch: int,
    min_rating: float,
    min_rating_watch: float,
    min_store_rating: float,
    min_product_cost_eur: float,
    preferred_max_weight_kg: float,
    preferred_max_length_cm: int,
    preferred_max_delivery_days: int,
    hard_max_delivery_days: int,
) -> dict[str, Any]:
    product_id = str(item.get("itemId") or "")
    title = str(item.get("title") or "")
    hard_failures: list[str] = []
    warnings: list[str] = []
    passed: list[str] = ["title_relevance"]

    try:
        details = await client.get_product_details(product_id)
    except IOPError as exc:
        return _diag(
            product_id=product_id,
            title=title,
            hard_failures=["product_get_failed"],
            warnings=warnings,
            passed=passed,
            error=str(exc),
        )

    base = details.get("ae_item_base_info_dto") or {}
    detail_rating = _parse_float(base.get("avg_evaluation_rating"))
    search_rating = _parse_float(item.get("score"))
    rating = detail_rating if detail_rating > 0 else search_rating
    rating_source = (
        "product_get"
        if detail_rating > 0
        else ("text_search" if search_rating > 0 else "unknown")
    )
    order_count = _parse_order_count(base.get("sales_count")) or _parse_order_count(
        item.get("orders")
    )
    store = _build_store_info(details.get("ae_store_info"))

    if rating <= 0:
        warnings.append("rating_unknown")
    elif rating < min_rating_watch:
        hard_failures.append("rating_below_watch")
    elif rating < min_rating:
        warnings.append("rating_watch")
    else:
        passed.append("rating_pass")

    if order_count >= min_orders:
        passed.append("orders_pass")
    elif order_count >= min_orders_watch:
        warnings.append("orders_watch")
    else:
        warnings.append("low_orders")

    store_values = [
        store.shipping_speed_rating,
        store.communication_rating,
        store.item_as_described_rating,
    ]
    known_store = [value for value in store_values if value > 0]
    if not known_store:
        warnings.append("store_rating_unknown")
    elif min(known_store) < min_store_rating:
        hard_failures.append("store_rating_below_watch")
    else:
        passed.append("store_rating_pass")

    sku_wrapper = details.get("ae_item_sku_info_dtos") or {}
    if isinstance(sku_wrapper, dict):
        raw_skus = sku_wrapper.get("ae_item_sku_info_d_t_o") or []
    elif isinstance(sku_wrapper, list):
        raw_skus = sku_wrapper
    else:
        raw_skus = []
    if not isinstance(raw_skus, list):
        raw_skus = []

    sku, sku_selection, sku_warnings = _select_reference_sku(
        [raw for raw in raw_skus if isinstance(raw, dict)]
    )
    warnings.extend(sku_warnings)
    if sku is None:
        hard_failures.append("in_stock_device_sku")
        return _diag(
            product_id=product_id,
            title=title,
            rating=rating,
            rating_source=rating_source,
            order_count=order_count,
            hard_failures=hard_failures,
            warnings=warnings,
            passed=passed,
            sku_selection=sku_selection,
        )

    product_cost = sku.offer_sale_price_eur
    if product_cost < min_product_cost_eur:
        warnings.append("suspicious_low_product_cost")
    else:
        passed.append("product_cost_sane")

    package = _build_package(details.get("package_info_dto"))
    package_weight_kg: float | None = None
    package_max_length_cm: int | None = None
    if package is not None:
        package_weight_kg = package.weight_kg
        package_max_length_cm = max(
            package.length_cm,
            package.width_cm,
            package.height_cm,
        )
        if package.weight_kg > preferred_max_weight_kg:
            warnings.append("weight_over_preferred")
        else:
            passed.append("weight_preferred")
        if package_max_length_cm > preferred_max_length_cm:
            warnings.append("length_over_preferred")
        else:
            passed.append("length_preferred")

    try:
        freight_raw = await client.get_shipping_cost(
            product_id=product_id,
            sku_id=sku.sku_id,
            country_code=country_code,
        )
        shipping = _build_shipping_info(freight_raw, country_code)
    except IOPError:
        shipping = None

    if shipping is None:
        hard_failures.append("shipping_available")
        return _diag(
            product_id=product_id,
            title=title,
            rating=rating,
            rating_source=rating_source,
            order_count=order_count,
            product_cost=product_cost,
            hard_failures=hard_failures,
            warnings=warnings,
            passed=passed,
            sku_selection=sku_selection,
            package_weight_kg=package_weight_kg,
            package_max_length_cm=package_max_length_cm,
        )

    passed.append("shipping_available")
    if shipping.max_delivery_days > hard_max_delivery_days:
        hard_failures.append("delivery_too_slow")
    elif shipping.max_delivery_days > preferred_max_delivery_days:
        warnings.append("delivery_slow")
    else:
        passed.append("delivery_preferred")

    candidate = None
    if not hard_failures:
        quality_verdict = "WATCH" if warnings else "PASS"
        candidate = {
            "product_id": product_id,
            "title": title,
            "product_url": str(item.get("itemUrl") or ""),
            "store": store.store_name,
            "rating": rating if rating > 0 else None,
            "rating_source": rating_source,
            "orders": order_count,
            "product_cost_eur": round(product_cost, 2),
            "shipping_cost_eur": round(shipping.cost_eur, 2),
            "landed_cost_eur": round(product_cost + shipping.cost_eur, 2),
            "delivery_max_days": shipping.max_delivery_days,
            "ship_from_country": shipping.ship_from_country,
            "is_eu_warehouse": shipping.ship_from_country in EU_COUNTRIES,
            "sku_id": sku.sku_id,
            "sku_selection": sku_selection,
            "package_weight_kg": package_weight_kg,
            "package_max_length_cm": package_max_length_cm,
            "quality_verdict": quality_verdict,
            "quality_score": _quality_score(warnings),
            "risk_flags": list(dict.fromkeys(warnings)),
        }

    return {
        "product_id": product_id,
        "title": title,
        "rating": rating if rating > 0 else None,
        "rating_source": rating_source,
        "order_count": order_count,
        "offer_sale_price_eur": product_cost,
        "shipping_cost_eur": round(shipping.cost_eur, 2),
        "landed_cost_eur": round(product_cost + shipping.cost_eur, 2),
        "delivery_max_days": shipping.max_delivery_days,
        "package_weight_kg": package_weight_kg,
        "package_max_length_cm": package_max_length_cm,
        "sku_selection": sku_selection,
        "hard_failures": hard_failures,
        "warnings": list(dict.fromkeys(warnings)),
        "failed_filters": hard_failures,
        "passed_filters": passed,
        "quality_verdict": candidate.get("quality_verdict") if candidate else "KILL",
        "quality_score": candidate.get("quality_score") if candidate else 0,
        "candidate": candidate,
    }


def _diag(
    *,
    product_id: str,
    title: str,
    hard_failures: list[str],
    warnings: list[str],
    passed: list[str],
    rating: float = 0.0,
    rating_source: str = "unknown",
    order_count: int = 0,
    product_cost: float | None = None,
    sku_selection: dict[str, Any] | None = None,
    package_weight_kg: float | None = None,
    package_max_length_cm: int | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "product_id": product_id,
        "title": title,
        "rating": rating if rating > 0 else None,
        "rating_source": rating_source,
        "order_count": order_count,
        "offer_sale_price_eur": product_cost,
        "package_weight_kg": package_weight_kg,
        "package_max_length_cm": package_max_length_cm,
        "sku_selection": sku_selection,
        "hard_failures": hard_failures,
        "warnings": list(dict.fromkeys(warnings)),
        "failed_filters": hard_failures,
        "passed_filters": passed,
        "quality_verdict": "KILL",
        "quality_score": 0,
        "candidate": None,
    }
    if error:
        row["error"] = error
    return row
