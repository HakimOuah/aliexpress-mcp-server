"""Async façade around `python-aliexpress-api`.

Wraps the synchronous SDK calls inside `asyncio.to_thread`, adds structured
logging, and re-raises SDK errors as domain-specific exceptions. No caching
here — that lives in the layer above (Phase 4).
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from aliexpress_api import AliexpressApi
from aliexpress_api import models as ae_models
from aliexpress_api.errors import (
    ApiRequestException,
    ApiRequestResponseException,
    InvalidArgumentException,
    ProductsNotFoudException,
)

from .config import AliExpressConfig

log = structlog.get_logger(__name__)


SORT_MAP: dict[str, str] = {
    "orders": ae_models.SortBy.LAST_VOLUME_DESC,
    "orders_asc": ae_models.SortBy.LAST_VOLUME_ASC,
    "price_asc": ae_models.SortBy.SALE_PRICE_ASC,
    "price_desc": ae_models.SortBy.SALE_PRICE_DESC,
}


class AliExpressClientError(Exception):
    """Base error for the client wrapper."""


class ProductsNotFound(AliExpressClientError):
    """Raised when AE returns zero products."""


class UpstreamError(AliExpressClientError):
    """Raised on any other AE / network failure."""


class AliExpressClient:
    """Async wrapper for the AliExpress Affiliate SDK.

    The `sdk` argument exists so tests can inject a mock without touching
    the network. In production it is constructed from `config`.
    """

    def __init__(
        self,
        config: AliExpressConfig,
        sdk: AliexpressApi | None = None,
    ) -> None:
        self._config = config
        self._sdk: AliexpressApi = sdk or AliexpressApi(
            key=config.app_key,
            secret=config.app_secret,
            language=config.default_language,
            currency=config.default_currency,
            tracking_id=config.tracking_id,
        )

    async def search_products(
        self,
        query: str,
        max_results: int = 20,
        min_orders: int | None = None,
        min_rating: float | None = None,
        max_price_eur: float | None = None,
        sort_by: str = "orders",
        target_country: str = "FR",
    ) -> list[ae_models.Product]:
        """Search affiliated products.

        `min_orders` and `min_rating` are applied client-side (the SDK API
        does not support them). `max_price_eur` is converted to the AE
        "lowest currency denomination" (cents).

        Warning: client-side filters (`min_orders`, `min_rating`) may reduce
        the result count below `max_results` because we issue a single page
        of size `max_results` and filter afterwards. TODO: implement
        pagination for guaranteed result count.
        """
        sort_value = SORT_MAP.get(sort_by, ae_models.SortBy.LAST_VOLUME_DESC)
        # NOTE: page_size = max_results, no over-fetch. When `min_orders` or
        # `min_rating` are set, the post-fetch filter can shrink the result
        # below `max_results`. Acceptable for MVP. AE caps page_size at 50
        # so over-fetch alone (without pagination) tops out at 2.5x.
        # TODO(phase4): paginate (`page_no`) until we hit `max_results`
        # post-filter, with a hard upper bound on the number of pages.
        page_size = min(max(max_results, 1), 50)
        max_sale_price_cents = (
            int(round(max_price_eur * 100)) if max_price_eur is not None else None
        )

        log.info(
            "ae.search.start",
            query=query,
            page_size=page_size,
            sort=sort_value,
            country=target_country,
            max_price_eur=max_price_eur,
        )

        try:
            response = await asyncio.to_thread(
                self._sdk.get_products,
                keywords=query,
                page_size=page_size,
                sort=sort_value,
                ship_to_country=target_country,
                max_sale_price=max_sale_price_cents,
            )
        except ProductsNotFoudException as exc:
            log.info("ae.search.empty", query=query, reason=str(exc))
            return []
        except (ApiRequestException, ApiRequestResponseException, InvalidArgumentException) as exc:
            log.error("ae.search.upstream_error", query=query, error=str(exc))
            raise UpstreamError(str(exc)) from exc

        products: list[ae_models.Product] = list(response.products or [])
        filtered = _filter_products(products, min_orders, min_rating)

        log.info(
            "ae.search.done",
            query=query,
            returned=len(products),
            after_filter=len(filtered),
        )
        return filtered[:max_results]

    async def get_product_details(
        self,
        product_id: str,
        include_shipping: bool = True,
    ) -> ae_models.Product:
        """Fetch a single product's full detail.

        `include_shipping` is currently informational — the Affiliate detail
        endpoint does not return a shipping table. Use `get_shipping_cost`
        for that.
        """
        log.info(
            "ae.product.start",
            product_id=product_id,
            include_shipping=include_shipping,
        )

        try:
            products = await asyncio.to_thread(
                self._sdk.get_products_details,
                product_ids=product_id,
            )
        except ProductsNotFoudException as exc:
            log.info("ae.product.not_found", product_id=product_id, reason=str(exc))
            raise ProductsNotFound(str(exc)) from exc
        except (ApiRequestException, ApiRequestResponseException, InvalidArgumentException) as exc:
            log.error("ae.product.upstream_error", product_id=product_id, error=str(exc))
            raise UpstreamError(str(exc)) from exc

        if not products:
            raise ProductsNotFound(f"Empty result for product {product_id}")

        log.info("ae.product.done", product_id=product_id)
        return products[0]

    async def get_shipping_cost(
        self,
        product_id: str,
        country_code: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Freight / shipping cost lookup.

        NOT IMPLEMENTED YET. The underlying SDK (`python-aliexpress-api`)
        only exposes Affiliate endpoints; freight queries belong to the
        Drop Shipping API (`aliexpress.ds.freight.query`) which requires a
        raw signed HTTP call against the IOP gateway. To be implemented in
        a follow-up phase via `httpx` + manual HMAC-SHA256 signing
        (sign_method=sha256), with the OAuth `access_token` passed as a
        system parameter — same scheme used by the AE / Lazada IOP SDK.
        """
        log.warning(
            "ae.shipping.not_implemented",
            product_id=product_id,
            country=country_code,
            quantity=quantity,
        )
        raise NotImplementedError(
            "get_shipping_cost requires the Drop Shipping freight endpoint, "
            "which is not exposed by python-aliexpress-api. Implement via raw "
            "HTTP call to aliexpress.ds.freight.query in a follow-up phase."
        )


def _filter_products(
    products: list[ae_models.Product],
    min_orders: int | None,
    min_rating: float | None,
) -> list[ae_models.Product]:
    """Apply post-fetch quality filters that the API doesn't expose."""

    def passes(p: ae_models.Product) -> bool:
        if min_orders is not None:
            volume = getattr(p, "lastest_volume", 0) or 0
            if volume < min_orders:
                return False
        if min_rating is not None:
            raw = getattr(p, "evaluate_rate", "") or ""
            try:
                rate_pct = float(str(raw).rstrip("%"))
            except ValueError:
                return False
            rating_5 = rate_pct / 20.0
            if rating_5 < min_rating:
                return False
        return True

    return [p for p in products if passes(p)]
