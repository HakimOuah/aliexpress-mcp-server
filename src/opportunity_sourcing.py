"""Relevant AliExpress sourcing for opportunity analysis.

This path is intentionally separate from the strict legacy normalizer. It treats
missing ratings as UNKNOWN (not zero-quality), uses text.search score as a
fallback, and filters lexical relevance before expensive product/freight calls.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from .aliexpress_client import AliExpressClient, IOPError, _parse_float, _parse_order_count
from .normalizer import (
    EU_COUNTRIES,
    _build_package,
    _build_shipping_info,
    _build_sku_ref,
    _build_store_info,
    _select_cheapest_in_stock,
)
from .sourcing_relevance import build_sourcing_queries, dedupe_and_filter

CONCURRENCY_LIMIT = 5


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
    # Ask for enough results per alias to overcome AE's weak English-token matching.
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
    min_rating: float = 4.3,
    min_store_rating: float = 4.0,
    min_product_cost_eur: float = 25.0,
    max_delivery_days: int = 15,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def inspect(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _inspect_one(
                client,
                item,
                country_code=country_code,
                min_orders=min_orders,
                min_rating=min_rating,
                min_store_rating=min_store_rating,
                min_product_cost_eur=min_product_cost_eur,
                max_delivery_days=max_delivery_days,
            )

    diagnostics = list(await asyncio.gather(*(inspect(item) for item in items)))
    qualified = [d["candidate"] for d in diagnostics if d.get("candidate")]
    failure_counts: Counter[str] = Counter()
    for diagnostic in diagnostics:
        failure_counts.update(diagnostic.get("failed_filters") or [])
    summary = {
        "total_candidates": len(diagnostics),
        "qualified_count": len(qualified),
        "failure_counts": [
            {"filter": name, "count": count}
            for name, count in failure_counts.most_common()
        ],
        "candidates": diagnostics,
    }
    return qualified, summary


async def _inspect_one(
    client: AliExpressClient,
    item: dict[str, Any],
    *,
    country_code: str,
    min_orders: int,
    min_rating: float,
    min_store_rating: float,
    min_product_cost_eur: float,
    max_delivery_days: int,
) -> dict[str, Any]:
    product_id = str(item.get("itemId") or "")
    title = str(item.get("title") or "")
    failed: list[str] = []
    passed: list[str] = ["title_relevance"]

    try:
        details = await client.get_product_details(product_id)
    except IOPError as exc:
        return {
            "product_id": product_id, "title": title,
            "failed_filters": ["product_get_failed"], "passed_filters": passed,
            "error": str(exc), "candidate": None,
        }

    base = details.get("ae_item_base_info_dto") or {}
    detail_rating = _parse_float(base.get("avg_evaluation_rating"))
    search_rating = _parse_float(item.get("score"))
    rating = detail_rating if detail_rating > 0 else search_rating
    rating_source = "product_get" if detail_rating > 0 else ("text_search" if search_rating > 0 else "unknown")

    order_count = _parse_order_count(base.get("sales_count")) or _parse_order_count(item.get("orders"))
    store = _build_store_info(details.get("ae_store_info"))

    if rating > 0:
        (passed if rating >= min_rating else failed).append("rating_min")
    else:
        passed.append("rating_unknown_non_eliminatory")
    (passed if order_count >= min_orders else failed).append("orders_min")

    store_values = [
        store.shipping_speed_rating,
        store.communication_rating,
        store.item_as_described_rating,
    ]
    known_store = [v for v in store_values if v > 0]
    if known_store:
        (passed if min(known_store) >= min_store_rating else failed).append("store_rating_min")
    else:
        passed.append("store_rating_unknown_non_eliminatory")

    sku_wrapper = details.get("ae_item_sku_info_dtos") or {}
    raw_skus = sku_wrapper.get("ae_item_sku_info_d_t_o") or [] if isinstance(sku_wrapper, dict) else sku_wrapper
    if not isinstance(raw_skus, list):
        raw_skus = []
    skus = [s for s in (_build_sku_ref(x) for x in raw_skus if isinstance(x, dict)) if s is not None]
    sku = _select_cheapest_in_stock(skus)
    if sku is None:
        failed.append("in_stock_sku")
        return _diag(product_id, title, rating, rating_source, order_count, None, failed, passed)

    product_cost = sku.offer_sale_price_eur
    (passed if product_cost >= min_product_cost_eur else failed).append("product_cost_floor")

    package = _build_package(details.get("package_info_dto"))
    if package is not None:
        (passed if package.weight_kg <= 3.0 else failed).append("max_weight_kg")
        longest = max(package.length_cm, package.width_cm, package.height_cm)
        (passed if longest <= 60 else failed).append("max_length_cm")

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
        failed.append("shipping_available")
        return _diag(product_id, title, rating, rating_source, order_count, product_cost, failed, passed)

    passed.append("shipping_available")
    (passed if shipping.max_delivery_days <= max_delivery_days else failed).append("max_delivery_days")

    hard_failures = [f for f in failed if f not in {"rating_unknown_non_eliminatory", "store_rating_unknown_non_eliminatory"}]
    candidate = None
    if not hard_failures:
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
        }

    return {
        "product_id": product_id,
        "title": title,
        "rating": rating if rating > 0 else None,
        "rating_source": rating_source,
        "order_count": order_count,
        "offer_sale_price_eur": product_cost,
        "failed_filters": failed,
        "passed_filters": passed,
        "candidate": candidate,
    }


def _diag(
    product_id: str, title: str, rating: float, rating_source: str,
    order_count: int, product_cost: float | None, failed: list[str], passed: list[str],
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "title": title,
        "rating": rating if rating > 0 else None,
        "rating_source": rating_source,
        "order_count": order_count,
        "offer_sale_price_eur": product_cost,
        "failed_filters": failed,
        "passed_filters": passed,
        "candidate": None,
    }
