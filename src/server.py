"""FastMCP server exposing the AliExpress Drop Shipping pipeline.

Four tools:

* `search_and_normalize` — primary tool. Runs the full passe-1 chain
  (text.search → product.get → freight.query → filters) and returns
  JSON-ready `DropPilotProduct` dicts. ~95% of scout agent calls go
  here.

* `search_products_raw`, `get_product_detail`, `get_shipping_cost` —
  thin passthroughs over the client for debugging and edge cases
  where the scout needs to investigate a specific item outside the
  normalizer.

Shared `AliExpressClient` instance: created lazily on first tool call,
held for the server's lifetime, closed on shutdown. Single httpx
connection pool → lower latency and fewer TLS handshakes.

Run locally::

    python -m src.server

Serves Streamable HTTP on ``0.0.0.0:8080`` (MCP port is bound to
``127.0.0.1:8080`` in the Docker-compose layer so it never hits the
public internet).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import structlog
from fastmcp import FastMCP

from .aliexpress_client import (
    AliExpressClient,
    IOPAuthError,
    IOPError,
    IOPNetworkError,
    IOPPermissionError,
    IOPRateLimitError,
    IOPUpstreamError,
)
from .config import AppConfig, load_config
from .normalizer import normalize_search_results
from .serializers import serialize_product

log = structlog.get_logger(__name__)


# ── Server instance ─────────────────────────────────────────────────────────

mcp: FastMCP = FastMCP(
    name="aliexpress-dropshipping",
    instructions=(
        "AliExpress Drop Shipping sourcing tools. "
        "Prefer `search_and_normalize` for most queries — it returns "
        "high-ticket eligible products with SKU, shipping, and store "
        "metadata already filtered and enriched. The three raw tools "
        "(`search_products_raw`, `get_product_detail`, `get_shipping_cost`) "
        "expose the underlying IOP endpoints untouched for debugging."
    ),
)


# ── Shared client (lazy singleton) ──────────────────────────────────────────

_config: AppConfig | None = None
_client: AliExpressClient | None = None


def _get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


async def _get_client() -> AliExpressClient:
    """Lazily create the shared client on first tool call.

    We can't instantiate at import time because `load_config` reads
    `.env` and we want import to stay side-effect-free for tests.
    """
    global _client
    if _client is None:
        cfg = _get_config()
        _client = AliExpressClient(cfg.aliexpress)
        log.info("mcp.client.ready", app_key=cfg.aliexpress.app_key[:6])
    return _client


def set_client_for_testing(client: AliExpressClient) -> None:
    """Test hook: inject a mocked client. Also marks config as loaded
    so the server doesn't try to read the real `.env`."""
    global _client, _config
    _client = client
    # Config is not strictly needed when the client is injected, but
    # seeding it stops `_get_config` from touching disk.
    if _config is None:
        from .config import AliExpressConfig, AppConfig, DropPilotRules, ServerConfig
        _config = AppConfig(
            aliexpress=AliExpressConfig(
                app_key="test", app_secret="test", access_token="test",
                refresh_token="test", callback_url="https://example.test/cb",
                default_language="FR", default_currency="EUR", tracking_id="default",
            ),
            rules=DropPilotRules(
                price_multiplier=3.0, min_margin_pct=40.0,
                vat_fr=0.20, vat_be=0.21, vat_ch=0.081, vat_lu=0.17,
                min_orders_pass=100, min_rating_pass=4.3,
                min_orders_watch=50, min_rating_watch=4.0,
            ),
            server=ServerConfig(
                host="127.0.0.1", port=8080, log_level="INFO",
                cache_max_size=500, cache_ttl_search=3600,
                cache_ttl_product=21600, cache_ttl_shipping=86400,
            ),
        )


def reset_for_testing() -> None:
    """Test hook: clear singletons between tests."""
    global _client, _config
    _client = None
    _config = None


# ── Tool-call instrumentation ───────────────────────────────────────────────


def _tool_log_start(tool_name: str, **params: Any) -> float:
    log.info("mcp.tool.call", tool=tool_name, status="start", **params)
    return time.monotonic()


def _tool_log_done(
    tool_name: str, start: float, *, status: str = "success", **extra: Any
) -> None:
    duration_ms = int((time.monotonic() - start) * 1000)
    log.info(
        "mcp.tool.call",
        tool=tool_name,
        status=status,
        duration_ms=duration_ms,
        **extra,
    )


def _format_iop_error(exc: IOPError) -> str:
    """Short, scout-friendly error string. The scout agent reads this
    and decides to retry / give up / skip the product."""
    kind = type(exc).__name__
    pieces = [kind, str(exc)]
    if exc.request_id:
        pieces.append(f"request_id={exc.request_id}")
    return " | ".join(pieces)


# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool
async def search_and_normalize(
    query: str,
    max_results: int = 20,
    target_country: str = "FR",
) -> list[dict[str, Any]]:
    """Search the AliExpress Drop Shipping catalogue and return high-ticket
    eligible products enriched with full details (SKU, shipping, store).

    Only products that pass the passe-1 filters (rating ≥ 4.5, orders
    ≥ 300, store ≥ 4.5 on three dimensions, cheapest in-stock SKU
    ≥ €25, weight ≤ 3 kg, longest dim ≤ 60 cm, shipping to target
    country available with ≤ 15 days delivery) are returned. This is
    the tool the scout agent should call for normal sourcing requests.

    Args:
        query: search keywords, in the target-country language if
            possible (e.g. "cave à vin thermoélectrique", "robot
            aspirateur", "déshumidificateur 12L").
        max_results: how many raw results to fetch from text.search
            before filtering. 1-50 (AE page size cap).
        target_country: ISO country code for shipping calculation —
            FR, BE, CH, LU are the DropPilot markets.

    Returns:
        A list of `DropPilotProduct` dicts, each containing product
        identity, SKU reference (the cheapest in-stock variant with
        its numeric `sku_id`), all SKUs, store info, shipping info
        (cost, delay, tracked, EU warehouse flag), package dimensions,
        and the list of filters passed. Empty list when nothing
        survives filtering.
    """
    start = _tool_log_start(
        "search_and_normalize",
        query=query,
        max_results=max_results,
        target_country=target_country,
    )
    try:
        client = await _get_client()
        raw_items = await client.search_products(
            query=query,
            max_results=max_results,
            target_country=target_country,
            sort_by="orders",
        )
        products = await normalize_search_results(
            client=client,
            raw_items=raw_items,
            target_country=target_country,
        )
        payload = [serialize_product(p) for p in products]
        _tool_log_done(
            "search_and_normalize",
            start,
            raw_count=len(raw_items),
            passed_count=len(payload),
        )
        return payload
    except IOPError as exc:
        _tool_log_done(
            "search_and_normalize", start,
            status="error", error=_format_iop_error(exc),
        )
        raise RuntimeError(_format_iop_error(exc)) from exc


@mcp.tool
async def search_products_raw(
    query: str,
    max_results: int = 20,
    target_country: str = "FR",
    sort_by: str = "orders",
) -> list[dict[str, Any]]:
    """Raw passthrough to `aliexpress.ds.text.search`.

    Returns the unfiltered list of item dicts as AE serves them, with
    keys like `itemId`, `title`, `targetSalePrice`, `score`, `orders`,
    `itemMainPic`, `itemUrl`. Use this for debugging or exotic queries
    the normalizer's filters would drop — for normal sourcing, prefer
    `search_and_normalize`.

    Args:
        query: search keywords.
        max_results: 1-50.
        target_country: ISO country code.
        sort_by: "orders" | "price_asc" | "price_desc" | "latest".
    """
    start = _tool_log_start(
        "search_products_raw",
        query=query, max_results=max_results,
        target_country=target_country, sort_by=sort_by,
    )
    try:
        client = await _get_client()
        items = await client.search_products(
            query=query,
            max_results=max_results,
            target_country=target_country,
            sort_by=sort_by,
        )
        _tool_log_done("search_products_raw", start, result_count=len(items))
        return items
    except IOPError as exc:
        _tool_log_done(
            "search_products_raw", start,
            status="error", error=_format_iop_error(exc),
        )
        raise RuntimeError(_format_iop_error(exc)) from exc


@mcp.tool
async def get_product_detail(product_id: str) -> dict[str, Any]:
    """Raw passthrough to `aliexpress.ds.product.get`.

    Returns the full `result` dict with `ae_item_base_info_dto`,
    `ae_item_sku_info_dtos` (with the critical `sku_id` numeric field,
    see the warning on `get_shipping_cost`), `ae_store_info`,
    `logistics_info_dto`, `package_info_dto`, `ae_multimedia_info_dto`,
    `ae_item_properties`, etc. Use for single-product deep-dive.
    """
    start = _tool_log_start("get_product_detail", product_id=product_id)
    try:
        client = await _get_client()
        result = await client.get_product_details(product_id)
        _tool_log_done("get_product_detail", start)
        return result
    except IOPError as exc:
        _tool_log_done(
            "get_product_detail", start,
            status="error", error=_format_iop_error(exc),
        )
        raise RuntimeError(_format_iop_error(exc)) from exc


@mcp.tool
async def get_shipping_cost(
    product_id: str,
    sku_id: str,
    country_code: str = "FR",
    quantity: int = 1,
) -> dict[str, Any]:
    """Raw passthrough to `aliexpress.ds.freight.query`.

    WARNING: `sku_id` must be the NUMERIC identifier (e.g.
    `"12000044126059467"`), not the `sku_attr` property string (e.g.
    `"14:29#Bear;183:200007741"`). To get a valid `sku_id`, call
    `get_product_detail` first and extract
    ``result["ae_item_sku_info_dtos"]["ae_item_sku_info_d_t_o"][i]["sku_id"]``.

    Passing the wrong field yields a silent business error
    ``{code: 501, msg: "DELIVERY_INFO_EMPTY"}`` — the call returns
    HTTP 200 but the result carries `success: false`.
    """
    start = _tool_log_start(
        "get_shipping_cost",
        product_id=product_id, sku_id=sku_id,
        country_code=country_code, quantity=quantity,
    )
    try:
        client = await _get_client()
        result = await client.get_shipping_cost(
            product_id=product_id,
            sku_id=sku_id,
            country_code=country_code,
            quantity=quantity,
        )
        success = bool(result.get("success"))
        _tool_log_done("get_shipping_cost", start, ae_success=success)
        return result
    except IOPError as exc:
        _tool_log_done(
            "get_shipping_cost", start,
            status="error", error=_format_iop_error(exc),
        )
        raise RuntimeError(_format_iop_error(exc)) from exc


# ── Entrypoint ──────────────────────────────────────────────────────────────


def main() -> None:
    cfg = _get_config()
    logging.basicConfig(level=getattr(logging, cfg.server.log_level, logging.INFO))
    log.info(
        "mcp.server.start",
        host=cfg.server.host,
        port=cfg.server.port,
        transport="http",
    )
    mcp.run(transport="http", host=cfg.server.host, port=cfg.server.port)


if __name__ == "__main__":
    main()
