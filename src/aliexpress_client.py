"""Async AliExpress Drop Shipping API client.

Direct httpx calls to the IOP gateway (`/sync`), signed with
HMAC-SHA256 via `src.iop_signature`. Covers the three Drop-Shipping
endpoints we need for sourcing:

    aliexpress.ds.text.search    — keyword search
    aliexpress.ds.product.get    — product details
    aliexpress.ds.freight.query  — shipping cost

Returns **raw** payloads — normalization / scoring happens in Phase 4.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
import structlog

from .config import AliExpressConfig
from .iop_signature import (
    build_business_system_params,
    sign_business_request,
)

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

IOP_BUSINESS_GATEWAY = "https://api-sg.aliexpress.com/sync"
DEFAULT_TIMEOUT_S = 30.0

METHOD_TEXT_SEARCH = "aliexpress.ds.text.search"
METHOD_PRODUCT_GET = "aliexpress.ds.product.get"
METHOD_FREIGHT_QUERY = "aliexpress.ds.freight.query"

# Accepted inner success codes across AE endpoints. Include both int 0
# and the string forms — aliexpress.ds.text.search returns "00", the
# older affiliate-style endpoints return "200", and some internal paths
# return plain 0 / "0". We compare on str(value).
_SUCCESS_CODES: frozenset[str] = frozenset({"0", "00", "200"})

# Sort direction syntax for aliexpress.ds.text.search.
# Source: Indigoiamlove/AEDropShipperPHPDemoCode/DsTextSearch.php uses
# the comma form ("min_price,asc"), which is the most authoritative
# public reference we have.
#
# If IOP returns "Invalid sortBy parameter" at runtime:
#   1. Replace commas with underscores: "orders,desc" -> "orders_desc"
#   2. Try legacy constant names: "orders,desc" -> "LAST_VOLUME,desc"
SORT_MAP: dict[str, str] = {
    "orders": "orders,desc",
    "price_asc": "min_price,asc",
    "price_desc": "min_price,desc",
    "latest": "latest,desc",
}


# ── Exceptions ───────────────────────────────────────────────────────────────


class IOPError(Exception):
    """Base class for all IOP client errors."""

    def __init__(
        self,
        message: str,
        *,
        ae_code: str | None = None,
        ae_msg: str | None = None,
        ae_sub_code: str | None = None,
        ae_sub_msg: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.ae_code = ae_code
        self.ae_msg = ae_msg
        self.ae_sub_code = ae_sub_code
        self.ae_sub_msg = ae_sub_msg
        self.request_id = request_id


class IOPAuthError(IOPError):
    """Access token expired / invalid / not provided."""


class IOPRateLimitError(IOPError):
    """IOP flow-control / rate limit triggered."""


class IOPPermissionError(IOPError):
    """App does not have permission for the endpoint (wrong app type)."""


class IOPUpstreamError(IOPError):
    """Any other AE-side failure (malformed response, business error, ...)."""


class IOPNetworkError(IOPError):
    """Transport-level failure: timeout, connection refused, DNS, TLS, ..."""


# ── Client ───────────────────────────────────────────────────────────────────


class AliExpressClient:
    """Async façade over the AE IOP Drop Shipping endpoints.

    The `http_client` argument is injectable so tests can pass an
    AsyncMock without hitting the network. In production, omit it and
    the client provisions its own `httpx.AsyncClient` with a 30 s
    timeout. When you do so, remember to call `close()` on the client
    at shutdown (or use it as an async context manager).
    """

    def __init__(
        self,
        config: AliExpressConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S)

    async def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AliExpressClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # --- Public API --------------------------------------------------------

    async def search_products(
        self,
        query: str,
        max_results: int = 20,
        min_orders: int | None = None,
        min_rating: float | None = None,
        max_price_eur: float | None = None,
        sort_by: str = "orders",
        target_country: str = "FR",
    ) -> list[dict[str, Any]]:
        """Keyword search via `aliexpress.ds.text.search`.

        Returns the raw list of item dicts from IOP (not normalized).
        `min_orders` / `min_rating` / `max_price_eur` are applied
        client-side because the endpoint does not expose them as filters.

        Warning: client-side filters may reduce the result count below
        `max_results` because we issue a single page of size
        `max_results` and filter afterwards. TODO: implement pagination
        for guaranteed result count.
        """
        sort_value = SORT_MAP.get(sort_by, SORT_MAP["orders"])
        # IOP pageSize upper bound is undocumented publicly; PHP demo
        # uses 20. Cap at 50 as a prudent default — adjust if needed.
        page_size = max(1, min(max_results, 50))

        business_params = {
            "keyWord": query,
            "local": "fr_FR",  # FIXME: verify on first smoke test
            "countryCode": target_country,
            "currency": self._config.default_currency,
            "pageSize": str(page_size),
            "pageIndex": "1",
            "sortBy": sort_value,
        }

        envelope = await self._call_iop(METHOD_TEXT_SEARCH, business_params)
        items = _extract_items(envelope, METHOD_TEXT_SEARCH)

        filtered = _filter_items(
            items,
            min_orders=min_orders,
            min_rating=min_rating,
            max_price_eur=max_price_eur,
        )
        return filtered[:max_results]

    async def get_product_details(self, product_id: str) -> dict[str, Any]:
        """Fetch detailed product information via `aliexpress.ds.product.get`.

        Returns the raw `result` dict (the DS_Product structure with
        `ae_item_base_info_dto`, `ae_item_sku_info_dtos`, etc.).
        """
        business_params = {
            "product_id": product_id,
            "ship_to_country": "FR",  # FIXME: parameterize in Phase 4
            "target_currency": self._config.default_currency,
            "target_language": self._config.default_language.lower(),
            "remove_personal_benefit": "false",
        }

        envelope = await self._call_iop(METHOD_PRODUCT_GET, business_params)
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise IOPUpstreamError(
                f"Unexpected product.get envelope shape: {envelope!r}"
            )
        return result

    async def get_shipping_cost(
        self,
        product_id: str,
        sku_id: str,
        country_code: str,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Freight lookup via `aliexpress.ds.freight.query`.

        `sku_id` is **mandatory** — AE rejects requests without a
        `selectedSkuId` and the error messages are opaque. Never pass
        an arbitrary value: always source `sku_id` from a prior
        `get_product_details` call.

        Workflow::

            items = await client.search_products("yoga mat")
            details = await client.get_product_details(items[0]["product_id"])
            sku_id = details["ae_item_sku_info_dtos"][0]["id"]  # or pick another
            freight = await client.get_shipping_cost(
                product_id=items[0]["product_id"],
                sku_id=sku_id,
                country_code="FR",
            )

        Returns the raw freight `result` dict (shipping methods list,
        delivery time, cost).
        """
        # The API takes a single param `queryDeliveryReq` whose value
        # is a JSON string — not a nested object. Wrong type or missing
        # `selectedSkuId` yields opaque errors.
        query_req = {
            "quantity": str(quantity),
            "shipToCountry": country_code,
            "productId": str(product_id),
            "provinceCode": "",
            "cityCode": "",
            "selectedSkuId": str(sku_id),
            "language": "fr_FR",  # FIXME: verify on first smoke test
            "currency": self._config.default_currency,
            "locale": "fr_FR",
        }
        business_params = {"queryDeliveryReq": json.dumps(query_req)}

        envelope = await self._call_iop(METHOD_FREIGHT_QUERY, business_params)
        result = envelope.get("result")
        if not isinstance(result, dict):
            raise IOPUpstreamError(
                f"Unexpected freight.query envelope shape: {envelope!r}"
            )
        return result

    # --- Private ----------------------------------------------------------

    async def _call_iop(
        self, method: str, business_params: Mapping[str, str]
    ) -> dict[str, Any]:
        """Sign + POST an IOP business request, return the inner envelope.

        The inner envelope is the value of the top-level
        `aliexpress_<method_slug>_response` key. Callers extract
        `result` / `resp_result` from there — the nesting varies across
        AE endpoints.
        """
        system_params = build_business_system_params(
            app_key=self._config.app_key,
            access_token=self._config.access_token,
            method=method,
        )
        all_params = {**system_params, **business_params}
        signature = sign_business_request(self._config.app_secret, all_params)
        all_params["sign"] = signature

        log.info("ae.iop.call", method=method, status="request")
        start = time.monotonic()

        try:
            response = await self._http.post(
                IOP_BUSINESS_GATEWAY,
                data=all_params,
                headers={
                    "Content-Type": (
                        "application/x-www-form-urlencoded;charset=UTF-8"
                    ),
                },
            )
        except httpx.HTTPError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            log.error(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="network_error",
                error=str(exc),
            )
            raise IOPNetworkError(f"Network error calling {method}: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)

        if response.status_code >= 500:
            log.error(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="error",
                http_status=response.status_code,
            )
            raise IOPUpstreamError(
                f"{method} returned HTTP {response.status_code}",
                ae_msg=response.text[:500],
            )

        try:
            body = response.json()
        except ValueError as exc:
            log.error(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="error",
                http_status=response.status_code,
                parse_error=str(exc),
            )
            raise IOPUpstreamError(
                f"{method} returned non-JSON body: {response.text[:200]!r}"
            ) from exc

        if "error_response" in body:
            err = body["error_response"] or {}
            request_id = err.get("request_id") or body.get("request_id")
            exc = _classify_ae_error(err, request_id=request_id)
            log.warning(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="error",
                ae_code=exc.ae_code,
                ae_msg=exc.ae_msg,
                ae_sub_code=exc.ae_sub_code,
                request_id=request_id,
            )
            raise exc

        response_key = _response_key_for_method(method)
        envelope = body.get(response_key)
        if not isinstance(envelope, dict):
            log.error(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="error",
                reason="missing_response_envelope",
                response_key=response_key,
            )
            raise IOPUpstreamError(
                f"{method}: missing '{response_key}' in body: {body!r}"
            )

        # Some endpoints ship an inner success code. Names vary across
        # endpoints (`code` for text.search, `rsp_code` for product.get,
        # `resp_code` for category.get, no code at all for freight).
        # Accepted success values cover both int 0 and the string forms
        # ("0", "00", "200") that AE has been seen to return.
        inner_code = next(
            (envelope[k] for k in ("code", "rsp_code", "resp_code")
             if k in envelope and envelope[k] is not None),
            None,
        )
        if inner_code is not None and str(inner_code) not in _SUCCESS_CODES:
            inner_msg = envelope.get("msg") or envelope.get("rsp_msg") or envelope.get("resp_msg", "")
            request_id = envelope.get("request_id")
            exc = _classify_ae_error(
                {"code": inner_code, "msg": inner_msg},
                request_id=request_id,
            )
            log.warning(
                "ae.iop.call",
                method=method,
                duration_ms=duration_ms,
                status="error",
                ae_code=str(inner_code),
                ae_msg=inner_msg,
                request_id=request_id,
            )
            raise exc

        log.info(
            "ae.iop.call",
            method=method,
            duration_ms=duration_ms,
            status="success",
        )
        return envelope


# ── Helpers (module-level, easy to unit-test) ────────────────────────────────


def _response_key_for_method(method: str) -> str:
    """`aliexpress.ds.text.search` → `aliexpress_ds_text_search_response`."""
    return method.replace(".", "_") + "_response"


# Heuristic markers for error classification. All comparisons run
# against a lowercased haystack = code + msg + sub_code + sub_msg, so
# every marker below must itself be lowercase. Substring match.
# Broad by design: AE returns wildly different error strings across
# endpoints and account types — we'd rather route a mystery "session
# invalid" error to IOPAuthError than to the catch-all.
_AUTH_MARKERS: tuple[str, ...] = (
    "accesstoken",  # IllegalAccessToken, AccessTokenExpired, ...
    "access_token",
    "access-token",
    "session",  # AE uses "session" as the access-token param name, so
    # any error mentioning "session" is auth-related in this domain
    # (InvalidSession, SessionExpired, "The session has expired", ...)
    "invalidtoken",
    "invalid_token",
    "invalid token",
    "token expired",
    "token_expired",
    "tokenexpired",
    "unauthenticated",
)
_AUTH_CODES: frozenset[str] = frozenset({"27", "41"})

_RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "flow-control",
    "flow_control",
    "flow control",
    "top-flow",
    "topflow",
    "too many requests",
    "toomanyrequests",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "throttl",  # throttled / throttling
    "quota",
    "limited",  # app_call_limited, api_call_limited, isp.call-limited
    "exceeded",
    "429",
)
_RATE_LIMIT_CODES: frozenset[str] = frozenset({"7"})

_PERMISSION_MARKERS: tuple[str, ...] = (
    "insufficientpermission",
    "insufficient permission",
    "insufficient_permission",
    "permission-api",
    "permission_api",
    "permission not granted",
    "permissions not granted",
    "permission denied",
    "permission_denied",
    "permissiondenied",
    "does not have permission",
    "no permission",
    "forbidden",
    "access denied",
    "access_denied",
    "accessdenied",
    "not authorized",
    "not_authorized",
    "unauthorized",
    "api-package-user-permission",
)
_PERMISSION_CODES: frozenset[str] = frozenset({"15"})


def _classify_ae_error(
    err: Mapping[str, Any], *, request_id: str | None = None
) -> IOPError:
    """Map an AE error payload to a specific exception type.

    Classification is tolerant: we run case-insensitive substring
    matches against a haystack built from `code + msg + sub_code +
    sub_msg`, and we also match on stable numeric AE codes (27, 15, 7)
    as a cross-check. The order of checks is auth → rate limit →
    permission → catch-all, so overlapping markers route to the most
    specific family.
    """
    code = str(err.get("code") or "")
    msg = str(err.get("msg") or "")
    sub_code = str(err.get("sub_code") or "")
    sub_msg = str(err.get("sub_msg") or "")
    haystack = " ".join([code, msg, sub_code, sub_msg]).lower()

    base_kwargs = dict(
        ae_code=code or None,
        ae_msg=msg or None,
        ae_sub_code=sub_code or None,
        ae_sub_msg=sub_msg or None,
        request_id=request_id,
    )
    display = msg or sub_msg or code or "unknown IOP error"

    if code in _AUTH_CODES or any(m in haystack for m in _AUTH_MARKERS):
        return IOPAuthError(f"Auth error: {display}", **base_kwargs)
    if code in _RATE_LIMIT_CODES or any(
        m in haystack for m in _RATE_LIMIT_MARKERS
    ):
        return IOPRateLimitError(f"Rate limited: {display}", **base_kwargs)
    if code in _PERMISSION_CODES or any(
        m in haystack for m in _PERMISSION_MARKERS
    ):
        return IOPPermissionError(f"Permission denied: {display}", **base_kwargs)
    return IOPUpstreamError(f"IOP error: {display}", **base_kwargs)


def _extract_items(
    envelope: Mapping[str, Any],
    method: str = METHOD_TEXT_SEARCH,
) -> list[dict[str, Any]]:
    """Pull the list of items out of a search-style envelope.

    Currently only `aliexpress.ds.text.search` is supported. Other
    methods fall through to an empty list — extend the dispatch here
    when we capture additional list-returning endpoints.

    Shape observed on the live API (2026-04-20) for text.search:
        envelope["data"]["products"]["selection_search_product"] -> list[dict]

    Defensive: any missing / wrongly-typed step returns []. The item
    schema (field names) is preserved as-is — normalization to our
    internal Product model happens in the Phase 4 normalizer.
    """
    if method != METHOD_TEXT_SEARCH:
        return []
    data = envelope.get("data")
    if not isinstance(data, dict):
        return []
    products = data.get("products")
    if not isinstance(products, dict):
        return []
    items = products.get("selection_search_product")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


# ── Field parsers for client-side filters ───────────────────────────────────
#
# The text.search items expose order count / rating / price as strings
# with human-friendly formatting (e.g. "5,000+"). The helpers below
# normalize them to numbers so filter thresholds can be compared
# straightforwardly. Each is tolerant: empty / None / garbage input
# yields a neutral zero, never raises.


def _parse_order_count(raw: object) -> int:
    """Parse AE's `orders` field. Examples:
        "5,000+"  -> 5000
        "1000"    -> 1000
        "1,284"   -> 1284
        ""        -> 0
        None      -> 0
        "abc"     -> 0
    """
    if raw is None:
        return 0
    text = str(raw).strip()
    if not text:
        return 0
    # Strip trailing "+" and thousands separators (comma or space).
    text = text.rstrip("+").replace(",", "").replace(" ", "")
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _parse_float(raw: object) -> float:
    """Parse a dotted-decimal float string. Examples:
        "4.5"  -> 4.5
        ""     -> 0.0
        None   -> 0.0
        "abc"  -> 0.0
    """
    if raw is None:
        return 0.0
    text = str(raw).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_eur(raw: object) -> float:
    """Parse AE's `targetSalePrice` (EUR, dot-decimal string). Examples:
        "3.29"  -> 3.29
        "10"    -> 10.0
        ""      -> 0.0
        None    -> 0.0
        "abc"   -> 0.0

    Intentionally does NOT handle French comma-decimal ("3,29") — AE
    exposes both formats in the response, and this parser is paired
    with `targetSalePrice` which is always dotted. For the localized
    form use `salePriceFormat` separately.
    """
    return _parse_float(raw)


def _filter_items(
    items: list[dict[str, Any]],
    *,
    min_orders: int | None,
    min_rating: float | None,
    max_price_eur: float | None,
) -> list[dict[str, Any]]:
    """Apply client-side quality / price filters.

    Field names mirror the live AE text.search shape:
      * `orders`          -> order count, formatted like "5,000+"
      * `score`           -> average rating, "4.5" style (0-5 scale)
      * `targetSalePrice` -> price in the target currency (EUR), "3.29"
    """

    def passes(item: Mapping[str, Any]) -> bool:
        if min_orders is not None:
            if _parse_order_count(item.get("orders")) < min_orders:
                return False
        if min_rating is not None:
            if _parse_float(item.get("score")) < min_rating:
                return False
        if max_price_eur is not None:
            if _parse_eur(item.get("targetSalePrice")) > max_price_eur:
                return False
        return True

    return [item for item in items if passes(item)]
