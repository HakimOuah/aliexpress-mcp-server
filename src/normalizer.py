"""Passe-1 normalizer — raw IOP payloads → DropPilotProduct.

Applies the eliminatory quality filters inline: any product that fails
one filter is silently dropped (with a DEBUG-level log explaining
why). Products that pass every filter are returned as fully-populated
`DropPilotProduct` objects.

The margin scoring and competitor research layers are **not** here;
they run later in the scout agent (Phase 8).

Concurrency model:
    `normalize_search_results` processes all search hits in parallel,
    capped at `CONCURRENCY_LIMIT` concurrent AE calls via a shared
    `asyncio.Semaphore`. Inside a single item, `product.get` and
    `freight.query` are necessarily sequential (the freight call
    needs a SKU from the product details).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from .aliexpress_client import (
    AliExpressClient,
    IOPError,
    _parse_eur,
    _parse_float,
    _parse_order_count,
)
from .models import (
    DropPilotProduct,
    ItemDiagnostic,
    PackageInfo,
    ShippingInfo,
    SkuRef,
    StoreInfo,
)

log = structlog.get_logger(__name__)


# ── Filter thresholds (passe 1 — eliminatory) ────────────────────────────────

FILTERS_PASSE_1: dict[str, Any] = {
    "rating_min": 4.5,
    "orders_min": 300,
    "store_shipping_rating_min": 4.5,
    "store_communication_rating_min": 4.5,
    "store_as_described_rating_min": 4.5,
    "shipping_fr_available": True,
    "max_delivery_days": 15,
    "max_weight_kg": 3.0,
    "max_length_cm": 60,
    "min_stock_ref_sku": 1,
    # Strategy is high-ticket (€200-300+ sell price). A dropshipper cost
    # below €25 cannot reasonably multiply to this range while remaining
    # credible market-wise — filter these out before they pollute the
    # scout's shortlist.
    "offer_sale_price_min_eur": 25.0,
}

EU_COUNTRIES: frozenset[str] = frozenset(
    {"FR", "ES", "PL", "CZ", "DE", "IT", "NL", "BE", "AT", "PT"}
)

# Max parallel (product.get + freight.query) chains. AE doesn't publish
# a rate limit for DS endpoints; 5 is a prudent default.
CONCURRENCY_LIMIT = 5


# ── Additional parsers (complement those in src.aliexpress_client) ──────────


def _parse_weight_kg(raw: object) -> float:
    """AE exposes gross_weight as a dot-decimal string (e.g. "0.213")."""
    return _parse_float(raw)


def _parse_int_safe(raw: object, default: int = 0) -> int:
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _normalize_url(raw: object) -> str:
    """Prefix `https:` to protocol-relative URLs; pass others through."""
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    return text


def _split_images(raw: object) -> list[str]:
    """`image_urls` is a single string with URLs joined by `;`."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [u.strip() for u in text.split(";") if u.strip()]


def _parse_delivery_range(raw: object) -> tuple[int, int]:
    """'7-9' → (7, 9); '10' → (10, 10); '' → (0, 0)."""
    if raw is None:
        return 0, 0
    text = str(raw).strip()
    if not text:
        return 0, 0
    parts = [p.strip() for p in text.split("-") if p.strip()]
    try:
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
        if len(parts) == 1:
            return int(parts[0]), int(parts[0])
    except ValueError:
        pass
    return 0, 0


# ── Sub-dataclass builders ──────────────────────────────────────────────────


def _extract_sku_properties(sku: dict[str, Any]) -> dict[str, str]:
    container = sku.get("ae_sku_property_dtos") or {}
    if isinstance(container, dict):
        raw_props = container.get("ae_sku_property_d_t_o") or []
    elif isinstance(container, list):
        raw_props = container
    else:
        raw_props = []
    result: dict[str, str] = {}
    for p in raw_props:
        if not isinstance(p, dict):
            continue
        name = p.get("sku_property_name")
        value = p.get("sku_property_value")
        if name and value:
            result[str(name)] = str(value)
    return result


def _extract_first_sku_image(sku: dict[str, Any]) -> str | None:
    container = sku.get("ae_sku_property_dtos") or {}
    if isinstance(container, dict):
        raw_props = container.get("ae_sku_property_d_t_o") or []
    elif isinstance(container, list):
        raw_props = container
    else:
        raw_props = []
    for p in raw_props:
        if isinstance(p, dict) and p.get("sku_image"):
            return _normalize_url(p["sku_image"])
    return None


def _build_sku_ref(sku: dict[str, Any]) -> SkuRef | None:
    """None when `sku_id` is missing (the numeric AE identifier)."""
    sku_id = sku.get("sku_id")
    if not sku_id:
        return None
    return SkuRef(
        sku_id=str(sku_id),
        sku_attr=str(sku.get("sku_attr") or sku.get("id") or ""),
        offer_sale_price_eur=_parse_eur(sku.get("offer_sale_price")),
        sku_price_eur=_parse_eur(sku.get("sku_price")),
        currency_code=str(sku.get("currency_code") or "EUR"),
        available_stock=_parse_int_safe(sku.get("sku_available_stock")),
        sku_properties=_extract_sku_properties(sku),
        sku_image_url=_extract_first_sku_image(sku),
    )


def _select_cheapest_in_stock(skus: list[SkuRef]) -> SkuRef | None:
    """Cheapest `offer_sale_price_eur` among SKUs with stock >= 1 and a
    positive price (AE sometimes exposes `0` as a placeholder)."""
    in_stock = [
        s for s in skus
        if s.available_stock >= 1 and s.offer_sale_price_eur > 0
    ]
    if not in_stock:
        return None
    return min(in_stock, key=lambda s: s.offer_sale_price_eur)


def _is_cheapest_absolute(sku_ref: SkuRef, all_skus: list[SkuRef]) -> bool:
    """True iff `sku_ref.offer_sale_price_eur` equals the minimum price
    across `all_skus` (in stock or not). False means we fell back to a
    pricier in-stock SKU because the absolute cheapest was OOS — useful
    downstream to flag products where the advertised floor is unreachable.
    Zero-priced placeholders are ignored (AE returns 0 for unavailable
    variants)."""
    positive = [s.offer_sale_price_eur for s in all_skus if s.offer_sale_price_eur > 0]
    if not positive:
        return False
    return sku_ref.offer_sale_price_eur == min(positive)


def _build_store_info(raw: dict[str, Any] | None) -> StoreInfo:
    raw = raw or {}
    return StoreInfo(
        store_id=_parse_int_safe(raw.get("store_id")),
        store_name=str(raw.get("store_name") or ""),
        store_country_code=str(raw.get("store_country_code") or ""),
        shipping_speed_rating=_parse_float(raw.get("shipping_speed_rating")),
        communication_rating=_parse_float(raw.get("communication_rating")),
        item_as_described_rating=_parse_float(raw.get("item_as_described_rating")),
    )


def _build_package(raw: dict[str, Any] | None) -> PackageInfo | None:
    if not isinstance(raw, dict):
        return None
    return PackageInfo(
        weight_kg=_parse_weight_kg(raw.get("gross_weight")),
        length_cm=_parse_int_safe(raw.get("package_length")),
        width_cm=_parse_int_safe(raw.get("package_width")),
        height_cm=_parse_int_safe(raw.get("package_height")),
    )


def _is_aliexpress_choice(properties: Any) -> bool:
    if isinstance(properties, dict):
        props = properties.get("ae_item_property") or []
    elif isinstance(properties, list):
        props = properties
    else:
        return False
    for p in props:
        if (
            isinstance(p, dict)
            and str(p.get("attr_name", "")).strip().lower() == "choice"
            and str(p.get("attr_value", "")).strip().lower() == "yes"
        ):
            return True
    return False


# ── Freight parsing — defensive, shape not fully documented ─────────────────


def _build_shipping_info(
    freight_result: dict[str, Any] | None, target_country: str
) -> ShippingInfo | None:
    """Parse a freight.query `result` dict. Returns None when:
    - the result is absent / malformed
    - `success` is false (business-error case, e.g. DELIVERY_INFO_EMPTY)
    - no shipping methods are listed

    Picks the cheapest tracked method if available, otherwise the
    cheapest untracked. FIXME: pin field names on first live success
    capture; fallbacks below are best-effort.
    """
    if not isinstance(freight_result, dict):
        return None
    if not freight_result.get("success"):
        return None

    methods = (
        freight_result.get("aeop_freight_calculate_result_for_buyer_dtolist")
        or freight_result.get("aeop_freight_calculate_result_for_buyer_d_t_o_list")
        or freight_result.get("shipping_methods")
        or []
    )
    candidates = [m for m in methods if isinstance(m, dict)]
    if not candidates:
        return None

    def _amount(m: dict[str, Any]) -> float:
        freight = m.get("freight")
        if isinstance(freight, dict):
            return _parse_eur(freight.get("amount"))
        return 0.0

    def _is_tracked(m: dict[str, Any]) -> bool:
        return str(m.get("tracking_available") or m.get("trackingAvailable", "")).strip().lower() in (
            "true", "1", "yes",
        )

    tracked = [m for m in candidates if _is_tracked(m)]
    picked = min(tracked or candidates, key=_amount)

    freight = picked.get("freight") or {}
    amount = _amount(picked)
    cost_format = (
        str(freight.get("formatted_amount"))
        if isinstance(freight, dict) and freight.get("formatted_amount")
        else str(picked.get("formatted_amount") or picked.get("displayAmount") or "")
    )
    currency = str(freight.get("currency_code") or "EUR") if isinstance(freight, dict) else "EUR"

    min_days, max_days = _parse_delivery_range(
        picked.get("estimated_delivery_time") or picked.get("estimatedDeliveryTime")
    )

    ship_from = str(
        picked.get("send_goods_country_code")
        or picked.get("ship_from_country_code")
        or picked.get("ship_from_country")
        or picked.get("shipFromCountry")
        or ""
    ).upper()

    return ShippingInfo(
        country_code=target_country,
        cost_eur=amount,
        cost_format=cost_format,
        currency=currency,
        min_delivery_days=min_days,
        max_delivery_days=max_days,
        delivery_date_desc=str(
            picked.get("delivery_date_desc")
            or picked.get("deliveryDateDesc")
            or ""
        ),
        ship_from_country=ship_from,
        is_eu_warehouse=ship_from in EU_COUNTRIES,
        tracking=_is_tracked(picked),
        company=str(
            picked.get("service_name")
            or picked.get("company")
            or picked.get("shipping_method")
            or ""
        ),
        shipping_code=str(
            picked.get("service_code")
            or picked.get("shipping_code")
            or picked.get("code")
            or ""
        ),
        free_shipping=amount == 0.0,
    )


# ── Main pipeline ───────────────────────────────────────────────────────────


async def normalize_search_results(
    client: AliExpressClient,
    raw_items: list[dict[str, Any]],
    target_country: str = "FR",
) -> list[DropPilotProduct]:
    """Enrich & filter a list of text.search items.

    For each item: fetch product details + freight, apply passe-1
    filters, keep only the products that pass every filter. Uses
    the fast-fail evaluator (`early_exit=True`) — if a cheap filter
    (rating, store, ...) already rejects, we don't pay the cost of a
    freight.query for that item.
    """
    diagnostics = await _run_evaluations(
        client, raw_items, target_country, early_exit=True,
    )
    return [d.product for d in diagnostics if d.product is not None]


async def diagnose_search_results(
    client: AliExpressClient,
    raw_items: list[dict[str, Any]],
    target_country: str = "FR",
) -> list[ItemDiagnostic]:
    """Full-diagnosis variant of `normalize_search_results`.

    Returns one `ItemDiagnostic` per input item, PASS or KILL, with
    every passed/failed filter recorded. Useful to calibrate filter
    thresholds: the scout (or the operator) can see exactly which
    rule is cutting candidates.

    More expensive than `normalize_search_results`: runs
    freight.query on every candidate even when base filters already
    failed, so all applicable filters are evaluated.
    """
    return await _run_evaluations(
        client, raw_items, target_country, early_exit=False,
    )


async def _run_evaluations(
    client: AliExpressClient,
    raw_items: list[dict[str, Any]],
    target_country: str,
    *,
    early_exit: bool,
) -> list[ItemDiagnostic]:
    if not raw_items:
        return []

    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def process(item: dict[str, Any]) -> ItemDiagnostic:
        async with semaphore:
            return await _evaluate_item(
                client, item, target_country, early_exit=early_exit,
            )

    return list(await asyncio.gather(*(process(it) for it in raw_items)))


def _diagnostic(
    *,
    product_id: str,
    title: str,
    passed: list[str],
    failed: list[str],
    offer_sale_price_eur: float | None,
    rating: float | None,
    order_count: int | None,
    store_ratings: dict[str, float] | None,
    product: DropPilotProduct | None,
) -> ItemDiagnostic:
    verdict = "PASS" if product is not None else "KILL"
    return ItemDiagnostic(
        product_id=product_id,
        title=title,
        verdict=verdict,
        passed_filters=list(passed),
        failed_filters=list(failed),
        offer_sale_price_eur=offer_sale_price_eur,
        rating=rating,
        order_count=order_count,
        store_ratings=store_ratings,
        product=product,
    )


def _check(cond: bool, name: str, passed: list[str], failed: list[str]) -> bool:
    """Append `name` to `passed` or `failed` based on `cond`, return `cond`."""
    (passed if cond else failed).append(name)
    return cond


async def _evaluate_item(
    client: AliExpressClient,
    search_item: dict[str, Any],
    target_country: str,
    *,
    early_exit: bool,
) -> ItemDiagnostic:
    """Run the full passe-1 evaluator on one text.search item.

    `early_exit=True` short-circuits on the first failed filter to
    save API calls (used by `normalize_search_results` in production).
    `early_exit=False` keeps going so the diagnostic reports every
    filter that would have failed (used by `search_and_diagnose`).
    """
    product_id = str(search_item.get("itemId") or "")
    title = str(search_item.get("title") or "")
    passed: list[str] = []
    failed: list[str] = []

    if not product_id:
        failed.append("missing_itemId")
        return _diagnostic(
            product_id="", title=title, passed=passed, failed=failed,
            offer_sale_price_eur=None, rating=None, order_count=None,
            store_ratings=None, product=None,
        )

    # ── 1. product.get ──────────────────────────────────────────────
    try:
        details = await client.get_product_details(product_id)
    except IOPError as exc:
        log.debug(
            "normalizer.kill",
            product_id=product_id,
            stage="product.get",
            reason=str(exc),
        )
        failed.append("product_get_failed")
        return _diagnostic(
            product_id=product_id, title=title, passed=passed, failed=failed,
            offer_sale_price_eur=None, rating=None, order_count=None,
            store_ratings=None, product=None,
        )

    base = details.get("ae_item_base_info_dto") or {}
    rating = _parse_float(base.get("avg_evaluation_rating"))
    order_count = _parse_order_count(base.get("sales_count")) or _parse_order_count(
        search_item.get("orders")
    )
    evaluation_count = _parse_int_safe(base.get("evaluation_count"))
    store = _build_store_info(details.get("ae_store_info"))
    store_ratings = {
        "shipping": store.shipping_speed_rating,
        "communication": store.communication_rating,
        "as_described": store.item_as_described_rating,
    }

    def kill() -> ItemDiagnostic:
        """Return a KILL diagnostic with the metadata gathered so far."""
        return _diagnostic(
            product_id=product_id, title=title, passed=passed, failed=failed,
            offer_sale_price_eur=None, rating=rating, order_count=order_count,
            store_ratings=store_ratings, product=None,
        )

    # ── 2. Base filters (rating / orders / store ratings) ──────────
    _check(rating >= FILTERS_PASSE_1["rating_min"], "rating_min", passed, failed)
    _check(order_count >= FILTERS_PASSE_1["orders_min"], "orders_min", passed, failed)
    _check(
        store.shipping_speed_rating >= FILTERS_PASSE_1["store_shipping_rating_min"],
        "store_shipping_rating_min", passed, failed,
    )
    _check(
        store.communication_rating >= FILTERS_PASSE_1["store_communication_rating_min"],
        "store_communication_rating_min", passed, failed,
    )
    _check(
        store.item_as_described_rating >= FILTERS_PASSE_1["store_as_described_rating_min"],
        "store_as_described_rating_min", passed, failed,
    )
    if failed and early_exit:
        return kill()

    # ── 3. SKU selection ───────────────────────────────────────────
    sku_wrapper = details.get("ae_item_sku_info_dtos") or {}
    if isinstance(sku_wrapper, dict):
        raw_skus = sku_wrapper.get("ae_item_sku_info_d_t_o") or []
    elif isinstance(sku_wrapper, list):
        raw_skus = sku_wrapper
    else:
        raw_skus = []
    all_skus: list[SkuRef] = [
        s for s in (_build_sku_ref(rs) for rs in raw_skus if isinstance(rs, dict))
        if s is not None
    ]

    sku_ref = _select_cheapest_in_stock(all_skus)
    if sku_ref is None:
        failed.append("min_stock_ref_sku")
        # Can't evaluate price / package / shipping without a SKU — stop here.
        return kill()
    passed.append("min_stock_ref_sku")
    sku_ref_is_cheapest = _is_cheapest_absolute(sku_ref, all_skus)
    offer_price = sku_ref.offer_sale_price_eur

    # ── 4. SKU price floor (high-ticket strategy) ─────────────────
    _check(
        offer_price >= FILTERS_PASSE_1["offer_sale_price_min_eur"],
        "offer_sale_price_min_eur", passed, failed,
    )
    if failed and early_exit:
        return _diagnostic(
            product_id=product_id, title=title, passed=passed, failed=failed,
            offer_sale_price_eur=offer_price, rating=rating, order_count=order_count,
            store_ratings=store_ratings, product=None,
        )

    # ── 5. Package filters ─────────────────────────────────────────
    package = _build_package(details.get("package_info_dto"))
    if package is not None:
        _check(
            package.weight_kg <= FILTERS_PASSE_1["max_weight_kg"],
            "max_weight_kg", passed, failed,
        )
        max_dim = max(package.length_cm, package.width_cm, package.height_cm)
        _check(
            max_dim <= FILTERS_PASSE_1["max_length_cm"],
            "max_length_cm", passed, failed,
        )
        if failed and early_exit:
            return _diagnostic(
                product_id=product_id, title=title, passed=passed, failed=failed,
                offer_sale_price_eur=offer_price, rating=rating, order_count=order_count,
                store_ratings=store_ratings, product=None,
            )

    # ── 6. freight.query ───────────────────────────────────────────
    # Diagnostic logging — track the exact params we send and the
    # response we get back. Helps compare pipeline calls vs direct
    # MCP calls when only the former fails (see 2026-04-21 debug
    # session where direct calls returned success=true but the
    # pipeline consistently saw success=false).
    log.info(
        "normalizer.freight_call",
        product_id=product_id,
        product_id_type=type(product_id).__name__,
        sku_id=sku_ref.sku_id,
        sku_id_type=type(sku_ref.sku_id).__name__,
        country=target_country,
        quantity=1,  # client defaults to quantity=1; logged explicitly
    )
    freight_result: dict[str, Any] | None = None
    try:
        freight_result = await client.get_shipping_cost(
            product_id=product_id,
            sku_id=sku_ref.sku_id,
            country_code=target_country,
        )
        shipping = _build_shipping_info(freight_result, target_country)
    except IOPError as exc:
        log.warning(
            "normalizer.freight_raised",
            product_id=product_id,
            sku_id=sku_ref.sku_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        shipping = None

    # If the HTTP call returned but the AE business response was a
    # failure (e.g. DELIVERY_INFO_EMPTY), surface the code and message
    # so we can correlate with what get_shipping_cost alone returns.
    if shipping is None and isinstance(freight_result, dict):
        log.warning(
            "normalizer.freight_failed",
            product_id=product_id,
            sku_id=sku_ref.sku_id,
            country=target_country,
            response_success=freight_result.get("success"),
            response_code=freight_result.get("code"),
            response_msg=freight_result.get("msg"),
            response_keys=sorted(freight_result.keys()),
        )

    if shipping is None:
        failed.append("shipping_fr_available")
        return _diagnostic(
            product_id=product_id, title=title, passed=passed, failed=failed,
            offer_sale_price_eur=offer_price, rating=rating, order_count=order_count,
            store_ratings=store_ratings, product=None,
        )
    passed.append("shipping_fr_available")
    _check(
        shipping.max_delivery_days <= FILTERS_PASSE_1["max_delivery_days"],
        "max_delivery_days", passed, failed,
    )

    # ── 7. Build the final product (only if everything passed) ────
    if failed:
        return _diagnostic(
            product_id=product_id, title=title, passed=passed, failed=failed,
            offer_sale_price_eur=offer_price, rating=rating, order_count=order_count,
            store_ratings=store_ratings, product=None,
        )

    multimedia = details.get("ae_multimedia_info_dto") or {}
    product = DropPilotProduct(
        product_id=product_id,
        source="aliexpress",
        title=str(search_item.get("title") or base.get("subject") or ""),
        subject=str(base.get("subject") or ""),
        category_id=_parse_int_safe(base.get("category_id")) or None,
        product_url=_normalize_url(search_item.get("itemUrl")),
        main_image_url=_normalize_url(search_item.get("itemMainPic")),
        image_urls=_split_images(multimedia.get("image_urls")),
        rating=rating,
        evaluate_rate_pct=_parse_float(search_item.get("evaluateRate")),
        order_count=order_count,
        evaluation_count=evaluation_count,
        is_aliexpress_choice=_is_aliexpress_choice(details.get("ae_item_properties")),
        sku_ref=sku_ref,
        all_skus=all_skus,
        sku_ref_is_cheapest_absolute=sku_ref_is_cheapest,
        store=store,
        shipping_fr=shipping,
        package=package,
        passed_filters=passed,
        fetched_at=datetime.now(timezone.utc),
    )
    log.info(
        "normalizer.pass",
        product_id=product_id,
        passed=len(passed),
        price_eur=sku_ref.offer_sale_price_eur,
    )
    return _diagnostic(
        product_id=product_id, title=product.title, passed=passed, failed=failed,
        offer_sale_price_eur=offer_price, rating=rating, order_count=order_count,
        store_ratings=store_ratings, product=product,
    )
