"""Tests for the DropPilot normalizer (Phase 4).

Uses the committed live fixtures (`tests/fixtures/real_*.json`) as
ground truth for the shape parsers. For the freight.query success
case — which wasn't captured to a fixture (the live call returned a
business error) — a synthetic payload `FREIGHT_SUCCESS_CN` mirrors
the shape observed on 2026-04-21 by the smoke test chain.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.aliexpress_client import AliExpressClient, IOPUpstreamError
from src.config import AliExpressConfig
from src.models import DropPilotProduct
from src.normalizer import (
    EU_COUNTRIES,
    normalize_search_results,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ── Live-captured envelopes ─────────────────────────────────────────────────


def _real_search_items() -> list[dict[str, Any]]:
    data = _load("real_text_search_response.json")
    inner = data["aliexpress_ds_text_search_response"]
    return inner["data"]["products"]["selection_search_product"]


def _real_product_result() -> dict[str, Any]:
    data = _load("real_product_get_response.json")
    return data["aliexpress_ds_product_get_response"]["result"]


def _real_product_high_price() -> dict[str, Any]:
    """Real product fixture with SKU prices bumped above the €25
    high-ticket floor so downstream filters can be exercised without
    tripping the price gate. Preserves the relative price ordering —
    SKU[3] remains the cheapest in-stock variant."""
    p = copy.deepcopy(_real_product_result())
    skus = p["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]
    # Original offers: 8.01 / 5.19 / 8.19 / 5.09 / 8.19 / 5.19 (cheapest
    # in stock = SKU[3]). Retail sku_price mirrors ×~2.7.
    new_offers = ["40.05", "26.95", "41.95", "26.45", "41.95", "26.95"]
    new_retail = ["111.15", "74.85", "116.55", "73.45", "116.55", "74.85"]
    for sku, offer, retail in zip(skus, new_offers, new_retail):
        sku["offer_sale_price"] = offer
        sku["sku_price"] = retail
    return p


def _real_freight_error_result() -> dict[str, Any]:
    """The committed real_freight_query_response.json is the FIRST live
    capture — an error case (code 501, DELIVERY_INFO_EMPTY) from the
    buggy chain that passed sku_attr as selectedSkuId. A subsequent
    live chain (after the sku_id fix) returned a Cainiao success but
    wasn't dumped to the repo, so success paths rely on the synthetic
    `FREIGHT_SUCCESS_*` dicts below."""
    data = _load("real_freight_query_response.json")
    return data["aliexpress_ds_freight_query_response"]["result"]


# Synthetic freight success, shape inferred from 2026-04-21 live chain
# ("Cainiao Fulfillment, 1.99€, 7-9 jours, tracké, CN warehouse").
FREIGHT_SUCCESS_CN: dict[str, Any] = {
    "success": True,
    "aeop_freight_calculate_result_for_buyer_dtolist": [
        {
            "service_name": "Cainiao Fulfillment",
            "service_code": "CAINIAO_FULFILLMENT_STD",
            "estimated_delivery_time": "7-9",
            "tracking_available": "true",
            "freight": {
                "amount": "1.99",
                "formatted_amount": "1,99€",
                "currency_code": "EUR",
            },
            "send_goods_country_code": "CN",
            "delivery_date_desc": "avr. 28 - 30",
        }
    ],
}


FREIGHT_SUCCESS_ES: dict[str, Any] = {
    "success": True,
    "aeop_freight_calculate_result_for_buyer_dtolist": [
        {
            "service_name": "AliExpress Selection Standard",
            "service_code": "AE_STANDARD",
            "estimated_delivery_time": "3-5",
            "tracking_available": "true",
            "freight": {
                "amount": "0.99",
                "formatted_amount": "0,99€",
                "currency_code": "EUR",
            },
            "send_goods_country_code": "ES",
            "delivery_date_desc": "avr. 24 - 26",
        }
    ],
}


FREIGHT_SUCCESS_SLOW: dict[str, Any] = {
    "success": True,
    "aeop_freight_calculate_result_for_buyer_dtolist": [
        {
            "service_name": "AliExpress Saver",
            "service_code": "AE_SAVER",
            "estimated_delivery_time": "20-25",
            "tracking_available": "true",
            "freight": {"amount": "0.50", "currency_code": "EUR"},
            "send_goods_country_code": "CN",
        }
    ],
}


# ── Client mock helper ──────────────────────────────────────────────────────


def _make_client(
    *,
    product_result: dict[str, Any] | None = None,
    freight_result: dict[str, Any] | None = None,
    product_exc: Exception | None = None,
    freight_exc: Exception | None = None,
) -> AliExpressClient:
    """Build an AliExpressClient with its IOP methods mocked.

    `product_result` / `freight_result` are returned as-is from the
    respective async methods. `product_exc` / `freight_exc` let a test
    simulate an upstream error.
    """
    config = AliExpressConfig(
        app_key="test",
        app_secret="test",
        access_token="test",
        refresh_token="test",
        callback_url="https://example.test/cb",
        default_language="FR",
        default_currency="EUR",
        tracking_id="default",
    )
    client = AliExpressClient(config=config, http_client=MagicMock())

    if product_exc is not None:
        client.get_product_details = AsyncMock(side_effect=product_exc)  # type: ignore[method-assign]
    else:
        client.get_product_details = AsyncMock(  # type: ignore[method-assign]
            return_value=product_result if product_result is not None else _real_product_result()
        )

    if freight_exc is not None:
        client.get_shipping_cost = AsyncMock(side_effect=freight_exc)  # type: ignore[method-assign]
    else:
        client.get_shipping_cost = AsyncMock(  # type: ignore[method-assign]
            return_value=freight_result if freight_result is not None else FREIGHT_SUCCESS_CN
        )

    return client


pytestmark = pytest.mark.asyncio


# ── Happy path ──────────────────────────────────────────────────────────────


async def test_pipeline_returns_product_when_all_filters_pass() -> None:
    # High-price variant of the real product so it clears the €25 floor.
    client = _make_client(product_result=_real_product_high_price())
    items = _real_search_items()[:1]

    results = await normalize_search_results(client, items, target_country="FR")

    assert len(results) == 1
    product = results[0]
    assert isinstance(product, DropPilotProduct)
    assert product.product_id == "1005006361450153"  # from text.search fixture
    assert product.source == "aliexpress"
    # Base info
    assert product.rating == pytest.approx(4.6)
    assert product.order_count == 5000  # from product.get "sales_count": "5000+"
    assert product.evaluation_count == 1446
    assert product.is_aliexpress_choice is True
    # Store
    assert product.store.store_id == 1102602078
    assert product.store.shipping_speed_rating == pytest.approx(4.7)
    # SKU — cheapest in stock after the price bump is still SKU[3].
    assert product.sku_ref.sku_id == "12000044126059464"
    assert product.sku_ref.offer_sale_price_eur == pytest.approx(26.45)
    assert product.sku_ref_is_cheapest_absolute is True
    assert len(product.all_skus) == 6
    # Shipping (from FREIGHT_SUCCESS_CN)
    assert product.shipping_fr is not None
    assert product.shipping_fr.cost_eur == pytest.approx(1.99)
    assert product.shipping_fr.max_delivery_days == 9
    assert product.shipping_fr.ship_from_country == "CN"
    assert product.shipping_fr.is_eu_warehouse is False
    assert product.shipping_fr.tracking is True
    # Package
    assert product.package is not None
    assert product.package.weight_kg == pytest.approx(0.213)
    assert product.package.length_cm == 39
    # Passed filters must cover all 11 eliminatory checks
    for name in (
        "rating_min",
        "orders_min",
        "min_stock_ref_sku",
        "offer_sale_price_min_eur",
        "shipping_fr_available",
    ):
        assert name in product.passed_filters


async def test_pipeline_selects_cheapest_in_stock_sku() -> None:
    client = _make_client(product_result=_real_product_high_price())
    items = _real_search_items()[:1]

    [product] = await normalize_search_results(client, items, target_country="FR")

    # Bumped fixture SKUs: 40.05 (27), 26.95 (6), 41.95 (6), 26.45 (7),
    # 41.95 (4), 26.95 (83). Cheapest in stock = 26.45 € @ SKU[3].
    assert product.sku_ref.offer_sale_price_eur == pytest.approx(26.45)
    assert product.sku_ref.sku_id == "12000044126059464"


# ── Filter rejections (KILL paths) ──────────────────────────────────────────


def _product_with_mutated_base(**base_overrides: Any) -> dict[str, Any]:
    """Start from the high-price variant so the €25 price floor is
    already cleared — tests here target a different filter."""
    product = _real_product_high_price()
    product["ae_item_base_info_dto"].update(base_overrides)
    return product


def _product_with_mutated_store(**store_overrides: Any) -> dict[str, Any]:
    product = _real_product_high_price()
    product["ae_store_info"].update(store_overrides)
    return product


def _product_with_mutated_package(**package_overrides: Any) -> dict[str, Any]:
    product = _real_product_high_price()
    product["package_info_dto"].update(package_overrides)
    return product


async def test_kill_when_rating_below_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_base(avg_evaluation_rating="4.2")
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_orders_below_threshold() -> None:
    # Zero out both search-item orders and base sales_count to trigger.
    product = _product_with_mutated_base(sales_count="150")
    client = _make_client(product_result=product)
    item = copy.deepcopy(_real_search_items()[0])
    item["orders"] = "150"
    assert await normalize_search_results(client, [item]) == []


async def test_kill_when_store_shipping_rating_below_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_store(shipping_speed_rating="4.3")
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_store_communication_rating_below_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_store(communication_rating="4.3")
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_store_as_described_rating_below_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_store(item_as_described_rating="4.3")
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_package_weight_above_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_package(gross_weight="4.0")
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_package_longest_dim_above_threshold() -> None:
    client = _make_client(
        product_result=_product_with_mutated_package(package_length=70)
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_shipping_unavailable() -> None:
    client = _make_client(
        product_result=_real_product_high_price(),
        freight_result=_real_freight_error_result(),
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_delivery_days_above_threshold() -> None:
    client = _make_client(
        product_result=_real_product_high_price(),
        freight_result=FREIGHT_SUCCESS_SLOW,
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_no_sku_in_stock() -> None:
    product = _real_product_high_price()
    for sku in product["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]:
        sku["sku_available_stock"] = 0
    client = _make_client(product_result=product)
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_cheapest_in_stock_sku_below_25eur() -> None:
    """High-ticket strategy: reject products whose cheapest in-stock
    SKU is below the €25 floor, even when every other quality signal
    (rating, store, stock, package) passes. The raw real fixture
    exposes SKUs between 5€ and 8€, so it trips this filter."""
    client = _make_client(product_result=_real_product_result())  # raw low prices
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_sku_price_exactly_at_12eur() -> None:
    """Explicit at-threshold test: a product pinned at €12 fails."""
    product = _real_product_high_price()
    for sku in product["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]:
        sku["offer_sale_price"] = "12.00"
    client = _make_client(product_result=product)
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_product_get_raises() -> None:
    client = _make_client(product_exc=IOPUpstreamError("boom"))
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


async def test_kill_when_freight_query_raises() -> None:
    client = _make_client(
        product_result=_real_product_high_price(),
        freight_exc=IOPUpstreamError("boom"),
    )
    items = _real_search_items()[:1]
    assert await normalize_search_results(client, items) == []


# ── EU warehouse detection ─────────────────────────────────────────────────


async def test_eu_warehouse_flag_when_ship_from_es() -> None:
    client = _make_client(
        product_result=_real_product_high_price(),
        freight_result=FREIGHT_SUCCESS_ES,
    )
    items = _real_search_items()[:1]
    [product] = await normalize_search_results(client, items)
    assert product.shipping_fr is not None
    assert product.shipping_fr.ship_from_country == "ES"
    assert product.shipping_fr.is_eu_warehouse is True
    assert "ES" in EU_COUNTRIES


# ── sku_ref_is_cheapest_absolute flag ──────────────────────────────────────


async def test_sku_ref_is_cheapest_absolute_true_when_cheapest_in_stock() -> None:
    client = _make_client(product_result=_real_product_high_price())
    items = _real_search_items()[:1]
    [product] = await normalize_search_results(client, items)
    assert product.sku_ref_is_cheapest_absolute is True


async def test_sku_ref_is_cheapest_absolute_false_when_cheapest_is_oos() -> None:
    """When the absolute cheapest SKU is OOS, the selected (pricier)
    SKU should carry the flag False so the scout knows the advertised
    price floor is unreachable."""
    product_payload = _real_product_high_price()
    skus = product_payload["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]
    # SKU[3] @ 26.45 is the cheapest; take it OOS. Next cheapest in
    # stock becomes SKU[1] or SKU[5] @ 26.95 (both have stock).
    skus[3]["sku_available_stock"] = 0
    client = _make_client(product_result=product_payload)
    items = _real_search_items()[:1]

    [product] = await normalize_search_results(client, items)

    assert product.sku_ref.offer_sale_price_eur == pytest.approx(26.95)
    assert product.sku_ref_is_cheapest_absolute is False


# ── Empty-input & concurrency sanity ───────────────────────────────────────


async def test_pipeline_on_empty_list() -> None:
    client = _make_client()
    assert await normalize_search_results(client, []) == []


async def test_pipeline_parallelizes_processing() -> None:
    """Three items → three downstream calls, all succeed."""
    client = _make_client(product_result=_real_product_high_price())
    items = _real_search_items()  # 3 items
    results = await normalize_search_results(client, items)
    assert len(results) == 3
    # Each item triggered one product.get + one freight.query
    assert client.get_product_details.call_count == 3  # type: ignore[attr-defined]
    assert client.get_shipping_cost.call_count == 3  # type: ignore[attr-defined]


# See `tests/test_normalizer_helpers.py` for sync-only tests of the
# pure parsers (_parse_weight_kg, _normalize_url, _parse_delivery_range,
# _is_aliexpress_choice, _select_cheapest_in_stock, _build_shipping_info).
