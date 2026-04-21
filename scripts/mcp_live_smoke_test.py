"""Live smoke test for the deployed MCP server.

Runs after `docker compose up -d` to verify the full chain works end-
to-end with a real AliExpress backend: MCP protocol → client →
normalizer → IOP gateway.

Usage (inside the container, default target http://127.0.0.1:8080/mcp)::

    docker exec aliexpress-mcp python /app/scripts/mcp_live_smoke_test.py

Usage (from the Hermès container, reaching via Docker DNS)::

    docker exec hermes-agent-hjft-hermes-agent-1 \\
      env MCP_URL=http://aliexpress-mcp:8080/mcp \\
      python3 /path/to/mcp_live_smoke_test.py

Exit codes:
    0  — all checks passed
    1  — MCP server unreachable or list_tools missing expected tools
    2  — raw search_products_raw returned zero items (AE upstream issue
         or bad query — not a pipeline bug)
    3  — search_and_normalize returned zero eligible products (filters
         too strict for the current AE inventory, or pipeline bug)
    4  — any tool raised an unexpected error
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import Client


MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8080/mcp")
QUERY = os.environ.get("MCP_QUERY", "cave à vin")
MAX_RESULTS = int(os.environ.get("MCP_MAX_RESULTS", "3"))
TARGET_COUNTRY = os.environ.get("MCP_COUNTRY", "FR")

EXPECTED_TOOLS = {
    "search_and_normalize",
    "search_products_raw",
    "get_product_detail",
    "get_shipping_cost",
}

SEP = "=" * 72
SUB = "-" * 72


def log(msg: str) -> None:
    print(msg, flush=True)


def truncate(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def print_product(idx: int, product: dict[str, Any]) -> None:
    log(SUB)
    sku_ref = product.get("sku_ref") or {}
    shipping = product.get("shipping_fr") or {}
    package = product.get("package") or {}
    store = product.get("store") or {}
    log(f"[{idx}] {truncate(product.get('title'), 80)}")
    log(f"     product_id    : {product.get('product_id')}")
    log(
        f"     price (SKU ref): {sku_ref.get('offer_sale_price_eur')}€ "
        f"(stock {sku_ref.get('available_stock')})"
    )
    log(
        f"     rating / reviews / orders: {product.get('rating')} / "
        f"{product.get('evaluation_count')} / {product.get('order_count')}"
    )
    log(
        f"     AE Choice / cheapest abs : "
        f"{product.get('is_aliexpress_choice')} / "
        f"{product.get('sku_ref_is_cheapest_absolute')}"
    )
    log(
        f"     shipping      : {shipping.get('cost_format')} via "
        f"{truncate(shipping.get('company'), 30)} "
        f"({shipping.get('min_delivery_days')}-"
        f"{shipping.get('max_delivery_days')}j, "
        f"ship_from={shipping.get('ship_from_country')}, "
        f"EU={shipping.get('is_eu_warehouse')}, "
        f"tracked={shipping.get('tracking')})"
    )
    log(
        f"     package       : {package.get('weight_kg')}kg, "
        f"{package.get('length_cm')}×{package.get('width_cm')}"
        f"×{package.get('height_cm')}cm"
    )
    log(
        f"     store         : {truncate(store.get('store_name'), 30)} "
        f"({store.get('store_country_code')}, "
        f"ratings {store.get('shipping_speed_rating')}/"
        f"{store.get('communication_rating')}/"
        f"{store.get('item_as_described_rating')})"
    )
    log(f"     filters passed: {len(product.get('passed_filters', []))}")


async def main() -> int:
    log(SEP)
    log(f"  MCP live smoke test — target: {MCP_URL}")
    log(f"  query={QUERY!r}  max_results={MAX_RESULTS}  country={TARGET_COUNTRY}")
    log(SEP)

    # ── Step 1: connect + list_tools ────────────────────────────────
    try:
        async with Client(MCP_URL) as client:
            tools = await client.list_tools()
            names = {t.name for t in tools}
            log(f"\n  ✅ connected — {len(tools)} tool(s): {sorted(names)}")
            missing = EXPECTED_TOOLS - names
            if missing:
                log(f"  ✗ missing expected tools: {sorted(missing)}")
                return 1

            # ── Step 2: raw search — verify AE gateway is reachable ─
            log(f"\n  → search_products_raw({QUERY!r}, {MAX_RESULTS})")
            raw_result = await client.call_tool(
                "search_products_raw",
                {
                    "query": QUERY,
                    "max_results": MAX_RESULTS,
                    "target_country": TARGET_COUNTRY,
                },
            )
            raw_items = raw_result.data or []
            log(f"  ✅ search_products_raw — {len(raw_items)} raw item(s)")
            if not raw_items:
                log(
                    "  ✗ AE returned zero items for this query — either a "
                    "bad keyword or an AE-side issue. Try MCP_QUERY=... env."
                )
                return 2
            first = raw_items[0]
            log(
                f"     first: itemId={first.get('itemId')} "
                f"price={first.get('targetSalePrice')}€ "
                f"orders={first.get('orders')}"
            )

            # ── Step 3: normalized search — full pipeline ───────────
            log(f"\n  → search_and_normalize({QUERY!r}, {MAX_RESULTS})")
            norm_result = await client.call_tool(
                "search_and_normalize",
                {
                    "query": QUERY,
                    "max_results": MAX_RESULTS,
                    "target_country": TARGET_COUNTRY,
                },
            )
            products = norm_result.data or []
            log(
                f"  {'✅' if products else '✗'} "
                f"search_and_normalize — {len(products)} passe-1 product(s)"
            )
            if not products:
                log(
                    "\n  Pipeline ran without error but all candidates "
                    "were filtered out (high-ticket floor, rating, store "
                    "ratings, package size, or shipping). This is an "
                    "inventory signal, not a bug. Change MCP_QUERY to a "
                    "query with more €25+ compact items (e.g. 'aspirateur "
                    "robot', 'déshumidificateur compact', 'cafetière "
                    "espresso')."
                )
                return 3

            for idx, product in enumerate(products, start=1):
                print_product(idx, product)

            # ── Step 4: confirm the numeric sku_id can drive freight ─
            sample = products[0]
            sku_id = (sample.get("sku_ref") or {}).get("sku_id")
            product_id = sample.get("product_id")
            log(
                f"\n  → get_shipping_cost(product_id={product_id}, "
                f"sku_id={sku_id}, country={TARGET_COUNTRY})"
            )
            freight_result = await client.call_tool(
                "get_shipping_cost",
                {
                    "product_id": product_id,
                    "sku_id": sku_id,
                    "country_code": TARGET_COUNTRY,
                    "quantity": 1,
                },
            )
            freight = freight_result.data or {}
            log(
                f"  {'✅' if freight.get('success') else '⚠'}"
                f" get_shipping_cost — success={freight.get('success')}"
            )
            if not freight.get("success"):
                log(
                    f"     business error: code={freight.get('code')} "
                    f"msg={freight.get('msg')}"
                )

            # ── Step 5: spot-check get_product_detail on the same id ─
            log(f"\n  → get_product_detail({product_id})")
            detail_result = await client.call_tool(
                "get_product_detail", {"product_id": product_id}
            )
            detail = detail_result.data or {}
            base = detail.get("ae_item_base_info_dto") or {}
            log(
                f"  ✅ get_product_detail — subject="
                f"{truncate(base.get('subject'), 60)}, "
                f"rating={base.get('avg_evaluation_rating')}"
            )

    except Exception as exc:  # noqa: BLE001
        log("\n" + SEP)
        log(f"  UNEXPECTED ERROR — {type(exc).__name__}: {exc}")
        log(SEP)
        import traceback
        traceback.print_exc()
        return 4

    log("\n" + SEP)
    log("  ✅ ALL CHECKS PASSED")
    log(SEP)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
