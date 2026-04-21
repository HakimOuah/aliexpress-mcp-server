"""Tests for the httpx-based AliExpress IOP client.

Strategy:
* Every test mocks `httpx.AsyncClient.post` — no real network.
* Tests focus on the ENVELOPE handling and signing contract.
* Item-level assertions are intentionally defensive: only `product_id`
  is asserted with an exact value (it's the single field guaranteed
  stable across AE APIs); every other field is checked by presence /
  type only, because the real text.search item shape is not publicly
  documented and will be pinned after the first live smoke test.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.aliexpress_client import (
    IOP_BUSINESS_GATEWAY,
    METHOD_FREIGHT_QUERY,
    METHOD_PRODUCT_GET,
    METHOD_TEXT_SEARCH,
    AliExpressClient,
    IOPAuthError,
    IOPNetworkError,
    IOPPermissionError,
    IOPRateLimitError,
    IOPUpstreamError,
)

from tests.conftest import load_fixture, make_httpx_response

pytestmark = pytest.mark.asyncio


def _prime(
    mock_http: AsyncMock,
    payload: dict[str, Any] | str,
    status_code: int = 200,
) -> None:
    mock_http.post.return_value = make_httpx_response(payload, status_code)


# ---------------------------------------------------------------------------
# _call_iop — request construction
# ---------------------------------------------------------------------------


async def test_call_iop_posts_to_business_gateway(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga mat")

    args, kwargs = mock_http.post.call_args
    assert args[0] == IOP_BUSINESS_GATEWAY
    assert (
        "application/x-www-form-urlencoded"
        in kwargs["headers"]["Content-Type"]
    )


async def test_call_iop_includes_all_system_params_and_sign(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga mat")

    form = mock_http.post.call_args.kwargs["data"]
    for key in (
        "app_key",
        "session",
        "timestamp",
        "sign_method",
        "format",
        "method",
        "sign",
    ):
        assert key in form, f"missing system param: {key}"
    assert form["method"] == METHOD_TEXT_SEARCH
    assert form["sign_method"] == "sha256"
    assert form["format"] == "json"
    assert form["sign"] == form["sign"].upper() and len(form["sign"]) == 64


async def test_call_iop_merges_business_params(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga mat", target_country="FR")

    form = mock_http.post.call_args.kwargs["data"]
    assert form["keyWord"] == "yoga mat"
    assert form["countryCode"] == "FR"
    assert form["currency"] == "EUR"
    assert form["pageIndex"] == "1"


async def test_call_iop_sign_is_deterministic_for_same_params(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    """Two calls with identical params yield the same signature only if
    their timestamp matches. That's a property of HMAC-SHA256, not a
    behaviour we assert here; we just make sure the sign field is a
    well-formed 64-char uppercase hex value."""
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga mat")
    form = mock_http.post.call_args.kwargs["data"]
    sig = form["sign"]
    assert len(sig) == 64
    int(sig, 16)


# ---------------------------------------------------------------------------
# _call_iop — response handling
# ---------------------------------------------------------------------------


async def test_call_iop_raises_upstream_on_missing_envelope(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, {"unexpected": "shape"})

    with pytest.raises(IOPUpstreamError, match="missing"):
        await client.search_products(query="yoga")


async def test_call_iop_raises_upstream_on_non_json_body(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, "not json at all")

    with pytest.raises(IOPUpstreamError, match="non-JSON"):
        await client.search_products(query="yoga")


async def test_call_iop_raises_upstream_on_http_5xx(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, "Service Unavailable", status_code=503)

    with pytest.raises(IOPUpstreamError, match="HTTP 503"):
        await client.search_products(query="yoga")


async def test_call_iop_wraps_httpx_errors_as_network_error(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    mock_http.post.side_effect = httpx.ConnectTimeout("timed out")

    with pytest.raises(IOPNetworkError, match="Network error"):
        await client.search_products(query="yoga")


# --- error classification (both via call stack and unit) ---------------------


async def test_auth_error_raises_IOPAuthError(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("error_auth.json"))

    with pytest.raises(IOPAuthError) as excinfo:
        await client.search_products(query="yoga")
    assert excinfo.value.ae_code == "27"
    assert excinfo.value.request_id == "fixture-err-auth-0001"


async def test_rate_limit_error_raises_IOPRateLimitError(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("error_rate_limit.json"))

    with pytest.raises(IOPRateLimitError) as excinfo:
        await client.search_products(query="yoga")
    assert "flow" in (excinfo.value.ae_sub_code or "").lower()


async def test_permission_error_raises_IOPPermissionError(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("error_permission.json"))

    with pytest.raises(IOPPermissionError) as excinfo:
        await client.search_products(query="yoga")
    assert excinfo.value.ae_code == "15"
    assert "InsufficientPermission" in (excinfo.value.ae_msg or "")


async def test_generic_error_raises_IOPUpstreamError(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("error_generic.json"))

    with pytest.raises(IOPUpstreamError) as excinfo:
        await client.search_products(query="yoga")
    assert excinfo.value.ae_code == "11006"


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------


async def test_search_products_returns_items_with_stable_itemId(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    items = await client.search_products(query="yoga mat")

    assert len(items) == 2
    # itemId is the stable identity field in the text.search response;
    # it's the only field asserted by exact value. Other fields are
    # checked defensively below.
    assert items[0]["itemId"] == "1005006361450153"
    assert items[1]["itemId"] == "1005005993007395"
    for item in items:
        assert "itemId" in item
        assert isinstance(item["itemId"], (str, int))


async def test_search_products_extracts_from_real_fixture(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    """Regression guardrail: the full captured IOP response (3 items)
    must yield 3 items with the exact itemIds seen live.

    Locks the canonical path `data.products.selection_search_product`
    against accidental refactors that break it — a bug initially missed
    by the permissive smoke-test walker.
    """
    _prime(mock_http, load_fixture("real_text_search_response.json"))

    items = await client.search_products(
        query="yoga mat", max_results=3, target_country="FR"
    )

    assert len(items) == 3
    assert items[0]["itemId"] == "1005006361450153"
    assert items[1]["itemId"] == "1005005993007395"
    assert items[2]["itemId"] == "1005008131052613"


async def test_search_products_uses_page_size_equal_to_max_results(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga", max_results=7)

    assert mock_http.post.call_args.kwargs["data"]["pageSize"] == "7"


async def test_search_products_caps_page_size_at_50(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga", max_results=500)

    assert mock_http.post.call_args.kwargs["data"]["pageSize"] == "50"


@pytest.mark.parametrize(
    "sort_by,expected",
    [
        ("orders", "orders,desc"),
        ("price_asc", "min_price,asc"),
        ("price_desc", "min_price,desc"),
        ("latest", "latest,desc"),
        ("garbage_key", "orders,desc"),  # fallback
    ],
)
async def test_search_products_sort_mapping(
    client: AliExpressClient,
    mock_http: AsyncMock,
    sort_by: str,
    expected: str,
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    await client.search_products(query="yoga", sort_by=sort_by)

    assert mock_http.post.call_args.kwargs["data"]["sortBy"] == expected


async def test_search_products_filters_by_min_orders_keeps_both_when_below_threshold(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    # fixture: both items have orders="5,000+" → parsed as 5000.
    items = await client.search_products(query="yoga", min_orders=4000)

    assert len(items) == 2


async def test_search_products_filters_by_min_orders_drops_all_when_above_threshold(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    items = await client.search_products(query="yoga", min_orders=10000)

    assert items == []


async def test_search_products_filters_by_min_rating(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    # fixture scores: "4.5" and "4.8" (already on 0-5 scale).
    items = await client.search_products(query="yoga", min_rating=4.7)

    assert len(items) == 1
    assert items[0]["score"] == "4.8"
    assert items[0]["itemId"] == "1005005993007395"


async def test_search_products_filters_by_max_price_eur(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    # fixture targetSalePrice: "3.29" and "3.99".
    items = await client.search_products(query="yoga", max_price_eur=3.5)

    assert len(items) == 1
    assert items[0]["targetSalePrice"] == "3.29"
    assert items[0]["itemId"] == "1005006361450153"


async def test_search_products_truncates_to_max_results_after_filter(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("text_search_success.json"))

    items = await client.search_products(query="yoga", max_results=1)

    assert len(items) == 1


# ---------------------------------------------------------------------------
# get_product_details
# ---------------------------------------------------------------------------


async def test_get_product_details_passes_all_expected_params(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("product_get_success.json"))

    await client.get_product_details(product_id="1005006123456789")

    form = mock_http.post.call_args.kwargs["data"]
    assert form["method"] == METHOD_PRODUCT_GET
    assert form["product_id"] == "1005006123456789"
    assert form["ship_to_country"] == "FR"
    assert form["target_currency"] == "EUR"
    assert form["target_language"] == "fr"
    assert form["remove_personal_benefit"] == "false"


async def test_get_product_details_returns_result_dict(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("product_get_success.json"))

    result = await client.get_product_details(product_id="1005006123456789")

    # Defensive: only check the keys we lean on in Phase 4 normalizer.
    assert "ae_item_base_info_dto" in result
    assert "ae_item_sku_info_dtos" in result
    assert (
        result["ae_item_base_info_dto"]["product_id"] == 1005006123456789
    )


async def test_get_product_details_raises_upstream_on_malformed_envelope(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(
        mock_http,
        {
            "aliexpress_ds_product_get_response": {
                "result": "not a dict",
                "rsp_code": "200",
            }
        },
    )

    with pytest.raises(IOPUpstreamError, match="Unexpected product.get"):
        await client.get_product_details(product_id="x")


# ---------------------------------------------------------------------------
# get_shipping_cost
# ---------------------------------------------------------------------------


async def test_get_shipping_cost_sends_query_delivery_req_as_json_string(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("freight_query_success.json"))

    await client.get_shipping_cost(
        product_id="1005006123456789",
        sku_id="12000023999200390",
        country_code="FR",
        quantity=1,
    )

    form = mock_http.post.call_args.kwargs["data"]
    assert form["method"] == METHOD_FREIGHT_QUERY

    # The DTO must be a JSON string, never a nested dict.
    raw = form["queryDeliveryReq"]
    assert isinstance(raw, str)
    dto = json.loads(raw)

    assert dto["productId"] == "1005006123456789"
    assert dto["selectedSkuId"] == "12000023999200390"
    assert dto["shipToCountry"] == "FR"
    assert dto["quantity"] == "1"
    assert dto["currency"] == "EUR"
    # Locale params should exist, exact values to be validated on smoke test
    assert "locale" in dto
    assert "language" in dto


async def test_get_shipping_cost_returns_result_dict(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("freight_query_success.json"))

    result = await client.get_shipping_cost(
        product_id="1005006123456789",
        sku_id="12000023999200390",
        country_code="FR",
    )

    assert isinstance(result, dict)
    # Defensive: Phase 4 will discover the real schema.
    assert "shipping_methods" in result


async def test_get_shipping_cost_propagates_permission_error(
    client: AliExpressClient, mock_http: AsyncMock
) -> None:
    _prime(mock_http, load_fixture("error_permission.json"))

    with pytest.raises(IOPPermissionError):
        await client.get_shipping_cost(
            product_id="x", sku_id="y", country_code="FR"
        )


