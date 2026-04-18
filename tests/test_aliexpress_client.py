"""Tests for the AliExpress client wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aliexpress_api import models as ae_models
from aliexpress_api.errors import (
    ApiRequestException,
    ProductsNotFoudException,
)

from src.aliexpress_client import (
    AliExpressClient,
    ProductsNotFound,
    UpstreamError,
)

pytestmark = pytest.mark.asyncio


# --- search_products ---------------------------------------------------------

async def test_search_products_returns_all_results(client: AliExpressClient, mock_sdk: MagicMock) -> None:
    products = await client.search_products(query="yoga mat")

    assert len(products) == 4
    mock_sdk.get_products.assert_called_once()
    kwargs = mock_sdk.get_products.call_args.kwargs
    assert kwargs["keywords"] == "yoga mat"
    assert kwargs["page_size"] == 20
    assert kwargs["sort"] == ae_models.SortBy.LAST_VOLUME_DESC
    assert kwargs["ship_to_country"] == "FR"
    assert kwargs["max_sale_price"] is None


async def test_search_products_converts_max_price_to_cents(client: AliExpressClient, mock_sdk: MagicMock) -> None:
    await client.search_products(query="yoga", max_price_eur=15.0)

    assert mock_sdk.get_products.call_args.kwargs["max_sale_price"] == 1500


async def test_search_products_caps_page_size_at_50(client: AliExpressClient, mock_sdk: MagicMock) -> None:
    await client.search_products(query="yoga", max_results=200)

    assert mock_sdk.get_products.call_args.kwargs["page_size"] == 50


async def test_search_products_filters_by_min_orders(client: AliExpressClient) -> None:
    products = await client.search_products(query="yoga", min_orders=300)

    # fixtures have lastest_volume = 1284, 47, 312, 802 → keep 1284, 312, 802
    volumes = sorted(p.lastest_volume for p in products)
    assert volumes == [312, 802, 1284]


async def test_search_products_filters_by_min_rating(client: AliExpressClient) -> None:
    # evaluate_rate is in % out of 100, divided by 20 → /5 scale
    # fixtures: 92.5% (4.625), 88% (4.4), 78.4% (3.92), 95.2% (4.76)
    products = await client.search_products(query="yoga", min_rating=4.5)

    rates = sorted(p.evaluate_rate for p in products)
    assert rates == ["92.5%", "95.2%"]


async def test_search_products_respects_max_results_after_filter(client: AliExpressClient) -> None:
    products = await client.search_products(query="yoga", max_results=2)

    assert len(products) == 2


@pytest.mark.parametrize(
    "key,expected",
    [
        ("orders", ae_models.SortBy.LAST_VOLUME_DESC),
        ("price_asc", ae_models.SortBy.SALE_PRICE_ASC),
        ("price_desc", ae_models.SortBy.SALE_PRICE_DESC),
        ("unknown_key", ae_models.SortBy.LAST_VOLUME_DESC),  # falls back
    ],
)
async def test_search_products_sort_mapping(
    client: AliExpressClient, mock_sdk: MagicMock, key: str, expected: str
) -> None:
    await client.search_products(query="yoga", sort_by=key)

    assert mock_sdk.get_products.call_args.kwargs["sort"] == expected


async def test_search_products_returns_empty_on_not_found(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    mock_sdk.get_products.side_effect = ProductsNotFoudException("nope")

    products = await client.search_products(query="zzz nothing")

    assert products == []


async def test_search_products_wraps_upstream_error(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    mock_sdk.get_products.side_effect = ApiRequestException("AE down")

    with pytest.raises(UpstreamError, match="AE down"):
        await client.search_products(query="yoga")


# --- get_product_details -----------------------------------------------------

async def test_get_product_details_returns_first_product(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    product = await client.get_product_details(product_id="1005006123456789")

    assert product.product_id == 1005006123456789
    mock_sdk.get_products_details.assert_called_once_with(product_ids="1005006123456789")


async def test_get_product_details_raises_when_not_found(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    mock_sdk.get_products_details.side_effect = ProductsNotFoudException("missing")

    with pytest.raises(ProductsNotFound, match="missing"):
        await client.get_product_details(product_id="0000")


async def test_get_product_details_raises_when_empty_list(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    mock_sdk.get_products_details.return_value = []

    with pytest.raises(ProductsNotFound):
        await client.get_product_details(product_id="0000")


async def test_get_product_details_wraps_upstream_error(
    client: AliExpressClient, mock_sdk: MagicMock
) -> None:
    mock_sdk.get_products_details.side_effect = ApiRequestException("boom")

    with pytest.raises(UpstreamError, match="boom"):
        await client.get_product_details(product_id="1005006123456789")


# --- get_shipping_cost (stub) ------------------------------------------------

async def test_get_shipping_cost_not_implemented(client: AliExpressClient) -> None:
    with pytest.raises(NotImplementedError, match="freight"):
        await client.get_shipping_cost(
            product_id="1005006123456789", country_code="FR", quantity=1
        )
