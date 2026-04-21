"""In-memory tests for the FastMCP server.

FastMCP's `Client(mcp)` connects directly to a server instance without
spawning a subprocess or opening a socket — tests run the real tool
functions through the real MCP protocol layer.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import Client

from src.aliexpress_client import (
    AliExpressClient,
    IOPAuthError,
    IOPPermissionError,
)
from src.config import AliExpressConfig
from src.server import mcp, reset_for_testing, set_client_for_testing

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _real_search_items() -> list[dict[str, Any]]:
    data = _load("real_text_search_response.json")
    return data["aliexpress_ds_text_search_response"]["data"]["products"][
        "selection_search_product"
    ]


def _real_product_result() -> dict[str, Any]:
    data = _load("real_product_get_response.json")
    return data["aliexpress_ds_product_get_response"]["result"]


def _real_product_high_price() -> dict[str, Any]:
    """Real product fixture with SKU prices bumped above the €25 floor,
    so the normalizer doesn't kill it."""
    p = copy.deepcopy(_real_product_result())
    skus = p["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"]
    new_offers = ["40.05", "26.95", "41.95", "26.45", "41.95", "26.95"]
    new_retail = ["111.15", "74.85", "116.55", "73.45", "116.55", "74.85"]
    for sku, offer, retail in zip(skus, new_offers, new_retail):
        sku["offer_sale_price"] = offer
        sku["sku_price"] = retail
    return p


def _real_freight_success_result() -> dict[str, Any]:
    data = _load("real_freight_query_success_response.json")
    return data["aliexpress_ds_freight_query_response"]["result"]


# Use the live-captured success response so server tests exercise the
# same shape the real FastMCP tools will see in production.
FREIGHT_SUCCESS_CN: dict[str, Any] = _real_freight_success_result()


def _make_mock_client(
    *,
    search_items: list[dict[str, Any]] | None = None,
    product_result: dict[str, Any] | None = None,
    freight_result: dict[str, Any] | None = None,
    search_exc: Exception | None = None,
    product_exc: Exception | None = None,
    freight_exc: Exception | None = None,
) -> AliExpressClient:
    """Build an AliExpressClient with public methods mocked."""
    config = AliExpressConfig(
        app_key="test", app_secret="test", access_token="test",
        refresh_token="test", callback_url="https://example.test/cb",
        default_language="FR", default_currency="EUR", tracking_id="default",
    )
    client = AliExpressClient(config=config, http_client=MagicMock())

    client.search_products = AsyncMock(  # type: ignore[method-assign]
        return_value=search_items if search_items is not None else _real_search_items()[:1],
        side_effect=search_exc,
    )
    client.get_product_details = AsyncMock(  # type: ignore[method-assign]
        return_value=product_result if product_result is not None else _real_product_high_price(),
        side_effect=product_exc,
    )
    client.get_shipping_cost = AsyncMock(  # type: ignore[method-assign]
        return_value=freight_result if freight_result is not None else FREIGHT_SUCCESS_CN,
        side_effect=freight_exc,
    )
    return client


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Clear the server's shared client between tests."""
    reset_for_testing()
    yield
    reset_for_testing()


pytestmark = pytest.mark.asyncio


# ── Discovery ──────────────────────────────────────────────────────────────


async def test_server_exposes_expected_tools() -> None:
    set_client_for_testing(_make_mock_client())
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_and_normalize",
        "search_and_diagnose",
        "search_products_raw",
        "get_product_detail",
        "get_shipping_cost",
    }


# ── search_and_normalize (primary tool) ────────────────────────────────────


async def test_search_and_normalize_returns_serialized_product_dicts() -> None:
    set_client_for_testing(
        _make_mock_client(
            search_items=_real_search_items()[:1],
            product_result=_real_product_high_price(),
            freight_result=FREIGHT_SUCCESS_CN,
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_and_normalize",
            {"query": "tapis yoga", "max_results": 1, "target_country": "FR"},
        )

    payload = result.data
    assert isinstance(payload, list)
    assert len(payload) == 1
    product = payload[0]
    # Shape checks — keys the scout agent will read
    for key in (
        "product_id", "source", "title", "rating", "order_count",
        "is_aliexpress_choice", "sku_ref", "all_skus", "store",
        "shipping_fr", "package", "passed_filters", "fetched_at",
        "sku_ref_is_cheapest_absolute",
    ):
        assert key in product, f"missing key: {key}"
    # Exact-value checks on stable identity fields
    assert product["product_id"] == "1005006361450153"
    assert product["sku_ref"]["sku_id"] == "12000044126059464"
    assert product["source"] == "aliexpress"
    # Datetime must be serialized to ISO string
    assert isinstance(product["fetched_at"], str)
    assert "T" in product["fetched_at"]


async def test_search_and_normalize_returns_empty_when_product_below_price_floor() -> None:
    # Raw real product fixture has cheapest SKU at 5.09€ → below €25 floor.
    set_client_for_testing(
        _make_mock_client(
            search_items=_real_search_items()[:1],
            product_result=_real_product_result(),  # raw, low prices
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_and_normalize",
            {"query": "tapis", "max_results": 1},
        )
    assert result.data == []


async def test_search_and_normalize_raises_on_permission_error() -> None:
    set_client_for_testing(
        _make_mock_client(search_exc=IOPPermissionError("no access to this api"))
    )
    async with Client(mcp) as client:
        with pytest.raises(Exception) as excinfo:
            await client.call_tool(
                "search_and_normalize",
                {"query": "whatever", "max_results": 1},
            )
    # The FastMCP client re-raises server errors with the message preserved.
    assert "IOPPermissionError" in str(excinfo.value) or "no access" in str(excinfo.value).lower()


# ── search_products_raw ────────────────────────────────────────────────────


async def test_search_products_raw_returns_raw_items_unchanged() -> None:
    items = _real_search_items()
    set_client_for_testing(_make_mock_client(search_items=items))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_products_raw",
            {"query": "tapis", "max_results": 3, "target_country": "FR"},
        )
    assert isinstance(result.data, list)
    assert len(result.data) == 3
    # itemId is the canonical stable field.
    assert result.data[0]["itemId"] == items[0]["itemId"]


async def test_search_products_raw_raises_on_auth_error() -> None:
    set_client_for_testing(
        _make_mock_client(search_exc=IOPAuthError("token expired"))
    )
    async with Client(mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("search_products_raw", {"query": "x"})


# ── get_product_detail ─────────────────────────────────────────────────────


async def test_get_product_detail_returns_result_dict() -> None:
    set_client_for_testing(
        _make_mock_client(product_result=_real_product_result())
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_product_detail", {"product_id": "1005008177221739"}
        )
    data = result.data
    assert isinstance(data, dict)
    assert "ae_item_base_info_dto" in data
    assert "ae_item_sku_info_dtos" in data
    # Verify the sku_id field is carried through (not sku_attr!)
    sku0 = data["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"][0]
    assert sku0["sku_id"] == "12000044126059467"
    assert sku0["sku_id"] != sku0["id"]  # the id/sku_attr trap


# ── get_shipping_cost ──────────────────────────────────────────────────────


async def test_get_shipping_cost_returns_result_dict() -> None:
    set_client_for_testing(
        _make_mock_client(freight_result=FREIGHT_SUCCESS_CN)
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_shipping_cost",
            {
                "product_id": "1005008177221739",
                "sku_id": "12000044126059464",
                "country_code": "FR",
                "quantity": 1,
            },
        )
    assert result.data["success"] is True
    assert "delivery_options" in result.data
    assert "delivery_option_d_t_o" in result.data["delivery_options"]


# ── search_and_diagnose ────────────────────────────────────────────────────


async def test_search_and_diagnose_reports_pass_and_kill_for_same_query() -> None:
    """Two search items: one backed by a high-price product (PASS),
    one backed by a low-price product (KILL on offer_sale_price_min).
    """
    items = [
        {"itemId": "AAA", "title": "high ticket", "orders": "5,000+", "evaluateRate": "90"},
        {"itemId": "BBB", "title": "low ticket", "orders": "5,000+", "evaluateRate": "90"},
    ]
    high = _real_product_high_price()
    low = _real_product_result()

    async def product_side_effect(product_id):
        return high if product_id == "AAA" else low

    config = AliExpressConfig(
        app_key="test", app_secret="test", access_token="test",
        refresh_token="test", callback_url="https://example.test/cb",
        default_language="FR", default_currency="EUR", tracking_id="default",
    )
    client = AliExpressClient(config=config, http_client=MagicMock())
    client.search_products = AsyncMock(return_value=items)  # type: ignore[method-assign]
    client.get_product_details = AsyncMock(side_effect=product_side_effect)  # type: ignore[method-assign]
    client.get_shipping_cost = AsyncMock(return_value=FREIGHT_SUCCESS_CN)  # type: ignore[method-assign]
    set_client_for_testing(client)

    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "search_and_diagnose",
            {"query": "whatever", "max_results": 2, "target_country": "FR"},
        )

    payload = result.data
    assert payload["query"] == "whatever"
    assert payload["total_raw"] == 2
    assert payload["pass_count"] == 1
    assert payload["kill_count"] == 1

    by_id = {c["product_id"]: c for c in payload["candidates"]}
    assert by_id["AAA"]["verdict"] == "PASS"
    assert by_id["AAA"]["failed_filters"] == []
    assert "rating_min" in by_id["AAA"]["passed_filters"]

    assert by_id["BBB"]["verdict"] == "KILL"
    assert "offer_sale_price_min_eur" in by_id["BBB"]["failed_filters"]
    # The diagnostic surfaces the cheapest in-stock price for scout debugging
    assert by_id["BBB"]["offer_sale_price_eur"] == pytest.approx(5.09)


async def test_get_shipping_cost_passes_delivery_info_empty_through() -> None:
    error_result = _load("real_freight_query_error_response.json")[
        "aliexpress_ds_freight_query_response"
    ]["result"]
    set_client_for_testing(_make_mock_client(freight_result=error_result))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_shipping_cost",
            {
                "product_id": "x",
                "sku_id": "y",
                "country_code": "FR",
                "quantity": 1,
            },
        )
    # The tool doesn't interpret the business error — the scout does.
    assert result.data["success"] is False
    assert result.data["code"] == 501
    assert result.data["msg"] == "DELIVERY_INFO_EMPTY"
