"""Smoke test for AliExpressClient — real AE API call.

Usage (from repo root):
    python scripts/smoke_test.py

Throwaway dev tool: exercises a real `search_products` call, prints a few
fields per product so we can eyeball that the SDK + credentials + OAuth
token chain actually work end-to-end. Not committed by default.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

# Make `src.*` importable when running as `python scripts/smoke_test.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.aliexpress_client import AliExpressClient  # noqa: E402
from src.config import load_config  # noqa: E402

QUERY = "yoga mat"
MAX_RESULTS = 3
TARGET_COUNTRY = "FR"

SEP = "=" * 80
SUB = "-" * 80


def truncate(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_rating(raw: object) -> str:
    if raw in (None, ""):
        return "n/a"
    try:
        pct = float(str(raw).rstrip("%"))
    except ValueError:
        return f"{raw} (unparsed)"
    return f"{pct / 20:.2f}/5  ({pct}%)"


async def main() -> None:
    config = load_config()
    print(SEP)
    print(f"Smoke test — search_products")
    print(f"  query          : {QUERY!r}")
    print(f"  max_results    : {MAX_RESULTS}")
    print(f"  target_country : {TARGET_COUNTRY}")
    print(f"  currency       : {config.aliexpress.default_currency}")
    print(f"  language       : {config.aliexpress.default_language}")
    print(f"  tracking_id    : {config.aliexpress.tracking_id}")
    print(SEP)

    client = AliExpressClient(config.aliexpress)

    products = await client.search_products(
        query=QUERY,
        max_results=MAX_RESULTS,
        target_country=TARGET_COUNTRY,
    )

    print(f"\nReceived: {len(products)} product(s)\n")

    for idx, p in enumerate(products, start=1):
        title = getattr(p, "product_title", "")
        product_id = getattr(p, "product_id", "")
        price = getattr(p, "target_sale_price", "")
        currency = getattr(p, "target_sale_price_currency", "")
        rating = getattr(p, "evaluate_rate", None)
        orders = getattr(p, "lastest_volume", None)
        image_url = getattr(p, "product_main_image_url", "")
        product_url = getattr(p, "product_detail_url", "")

        print(SUB)
        print(f"[{idx}] {truncate(title, 80)}")
        print(f"     product_id : {product_id}")
        print(f"     price      : {price} {currency}")
        print(f"     rating     : {format_rating(rating)}")
        print(f"     orders     : {orders if orders is not None else 'n/a'}")
        print(f"     image      : {truncate(image_url, 60)}")
        print(f"     url        : {truncate(product_url, 60)}")

    print(SUB)
    print("\nDone.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("\n" + SEP, file=sys.stderr)
        print("SMOKE TEST FAILED — full traceback:", file=sys.stderr)
        print(SEP, file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
