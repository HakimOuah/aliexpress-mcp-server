"""Smoke test for AliExpressClient — real IOP Drop Shipping calls.

Usage (from repo root):
    python scripts/smoke_test.py

Exercises the three Drop Shipping endpoints in sequence:
    1. aliexpress.ds.text.search   (query="yoga mat")
    2. aliexpress.ds.product.get   (on the first search hit)
    3. aliexpress.ds.freight.query (on the first SKU of the first hit)

A tee on the httpx layer writes the raw response body to
`tests/fixtures/real_*.json` **before** any parsing or classification,
so the dump is preserved even when the client raises (permission
denied, missing envelope, JSON decode failure, ...).

Each step is independent: step 2 runs only if step 1 succeeded, step 3
only if step 2 succeeded. The script is idempotent — re-running
overwrites the fixture files.
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any

import httpx

# Make `src.*` importable when running as `python scripts/smoke_test.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.aliexpress_client import (  # noqa: E402
    METHOD_FREIGHT_QUERY,
    METHOD_PRODUCT_GET,
    METHOD_TEXT_SEARCH,
    SORT_MAP,
    AliExpressClient,
    IOPError,
    _extract_items,
)
from src.config import load_config  # noqa: E402


QUERY = "yoga mat"
MAX_RESULTS = 3
TARGET_COUNTRY = "FR"

FIXTURE_DIR = ROOT / "tests" / "fixtures"
FIXTURE_SEARCH = FIXTURE_DIR / "real_text_search_response.json"
FIXTURE_DETAIL = FIXTURE_DIR / "real_product_get_response.json"
FIXTURE_FREIGHT = FIXTURE_DIR / "real_freight_query_response.json"

SEP = "=" * 80


# ── HTTP tee ────────────────────────────────────────────────────────────────


class TeeingAsyncClient:
    """Duck-types `httpx.AsyncClient` for `AliExpressClient._call_iop`.

    On every `post()` call, if `tee_path` is set, writes the raw response
    body to that file **before** returning the response. The write
    happens immediately after the HTTP round-trip completes, so any
    downstream parsing / classification failure cannot prevent the
    dump. JSON bodies are pretty-printed; anything else is written
    verbatim.
    """

    def __init__(self, inner: httpx.AsyncClient) -> None:
        self._inner = inner
        self.tee_path: Path | None = None

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        response = await self._inner.post(*args, **kwargs)
        path = self.tee_path
        if path is not None:
            raw_bytes = response.content or b""
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
                payload = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = response.text or raw_bytes.decode(
                    "utf-8", errors="replace"
                )
            path.write_text(payload, encoding="utf-8")
            print(f"  → response dumped to {path.relative_to(ROOT)}")
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


# ── Helpers ─────────────────────────────────────────────────────────────────


def banner(title: str) -> None:
    print()
    print(SEP)
    print(f"  {title}")
    print(SEP)


def truncate(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def print_error(label: str, exc: BaseException) -> None:
    print(f"\n  ✗ {label}")
    if isinstance(exc, IOPError):
        print(f"    type         : {type(exc).__name__}")
        print(f"    ae_code      : {exc.ae_code}")
        print(f"    ae_msg       : {exc.ae_msg}")
        print(f"    ae_sub_code  : {exc.ae_sub_code}")
        print(f"    ae_sub_msg   : {exc.ae_sub_msg}")
        print(f"    request_id   : {exc.request_id}")
    print(f"    message      : {exc}")
    print("\n  Full traceback:")
    traceback.print_exc()


def _load_dumped(path: Path) -> dict[str, Any] | None:
    """Re-read the just-dumped fixture so analysis works off the same
    bytes that landed on disk. Returns None if the file isn't JSON."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _inner_envelope(outer: dict[str, Any], method: str) -> dict[str, Any]:
    """Unwrap `{aliexpress_<method>_response: {...}}` into `{...}`.
    Defensive — returns `{}` if the outer shape is unexpected."""
    response_key = method.replace(".", "_") + "_response"
    inner = outer.get(response_key)
    return inner if isinstance(inner, dict) else {}


def _extract_items_permissive(
    envelope: dict[str, Any], *, max_depth: int = 6
) -> list[dict[str, Any]]:
    """Recursive fallback — returns the first list-of-dicts found at any
    depth in the envelope.

    Only used when the canonical extraction (`_extract_items`) returns
    an empty list despite a populated envelope: typically means AE
    changed the response shape. Walking the tree surfaces something
    actionable on the smoke test instead of a blank result."""

    def walk(node: Any, depth: int) -> list[dict[str, Any]] | None:
        if depth > max_depth:
            return None
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            return dicts if dicts else None
        if isinstance(node, dict):
            for value in node.values():
                found = walk(value, depth + 1)
                if found:
                    return found
        return None

    return walk(envelope, 0) or []


def _pick_items(
    envelope: dict[str, Any], method: str
) -> list[dict[str, Any]]:
    """Canonical extraction first; permissive walker as diagnostic fallback."""
    canonical = _extract_items(envelope, method)
    if canonical:
        return canonical
    fallback = _extract_items_permissive(envelope)
    if fallback:
        print(
            f"  ⚠ canonical path missed — permissive walker "
            f"found {len(fallback)} items (investigate shape drift)"
        )
    return fallback


def _describe_text_search_path(envelope: dict[str, Any]) -> None:
    """Print which steps of `data.products.selection_search_product` exist
    in the envelope. Makes shape mismatches obvious at a glance."""
    print(f"  envelope keys                            : {sorted(envelope.keys())}")
    data = envelope.get("data")
    if not isinstance(data, dict):
        kind = type(data).__name__ if data is not None else "missing"
        print(f"  envelope['data']                         : {kind}")
        return
    print(f"  envelope['data'] keys                    : {sorted(data.keys())}")

    products = data.get("products")
    if not isinstance(products, dict):
        kind = type(products).__name__ if products is not None else "missing"
        print(f"  envelope['data']['products']             : {kind}")
        return
    print(f"  envelope['data']['products'] keys        : {sorted(products.keys())}")

    items = products.get("selection_search_product")
    if isinstance(items, list):
        print(
            f"  envelope[...]['selection_search_product']: "
            f"list with {len(items)} items"
        )
    else:
        kind = type(items).__name__ if items is not None else "missing"
        print(f"  envelope[...]['selection_search_product']: {kind}")


# ── Steps ───────────────────────────────────────────────────────────────────


async def step_text_search(
    client: AliExpressClient, tee: TeeingAsyncClient
) -> dict[str, Any] | None:
    """Returns the INNER envelope (contents of aliexpress_ds_text_search_response),
    or None if we should abort the smoke test chain."""
    banner("STEP 1 — aliexpress.ds.text.search")
    print(f"  query          : {QUERY!r}")
    print(f"  max_results    : {MAX_RESULTS}")
    print(f"  target_country : {TARGET_COUNTRY}")
    print(f"  sortBy         : {SORT_MAP['orders']}")

    business_params = {
        "keyWord": QUERY,
        "local": "fr_FR",
        "countryCode": TARGET_COUNTRY,
        "currency": "EUR",
        "pageSize": str(MAX_RESULTS),
        "pageIndex": "1",
        "sortBy": SORT_MAP["orders"],
    }

    # Tee fires BEFORE _call_iop's parsing / classification. Whatever
    # happens next, the raw body is on disk.
    tee.tee_path = FIXTURE_SEARCH
    call_exc: BaseException | None = None
    try:
        await client._call_iop(METHOD_TEXT_SEARCH, business_params)
    except BaseException as exc:  # noqa: BLE001
        call_exc = exc
    finally:
        tee.tee_path = None

    # Re-read the dumped file for analysis. This decouples analysis
    # from what the client saw — useful when the client raised before
    # returning a parsed envelope.
    outer = _load_dumped(FIXTURE_SEARCH)
    if outer is None:
        if call_exc is not None:
            print_error(
                "text.search FAILED and no response was dumped "
                "(likely a network / transport error)",
                call_exc,
            )
        else:
            print("\n  ✗ text.search returned no body — see fixture file.")
        return None

    print(f"\n  top-level keys : {sorted(outer.keys())}")

    envelope = _inner_envelope(outer, METHOD_TEXT_SEARCH)
    if not envelope:
        print(
            "  ✗ inner envelope 'aliexpress_ds_text_search_response' "
            "missing — smoke chain cannot continue."
        )
        if call_exc is not None:
            print_error("Client raised while processing the response", call_exc)
        return None

    print(f"  inner keys     : {sorted(envelope.keys())}")

    # Debug: show which expected path steps exist in the envelope so a
    # shape mismatch is visible without diffing JSON files.
    _describe_text_search_path(envelope)

    if call_exc is not None:
        # HTTP round-trip succeeded and body was captured, but the
        # client raised (permission denied, etc.). We still expose the
        # exception but return None so the chain stops.
        print_error(
            "Client raised on text.search response "
            "(envelope captured to fixture anyway)",
            call_exc,
        )
        return None

    items = _pick_items(envelope, METHOD_TEXT_SEARCH)
    print(f"\n  items found    : {len(items)}")
    if not items:
        print(
            "  → inspect tests/fixtures/real_text_search_response.json "
            "to update _extract_items in src/aliexpress_client.py"
        )
        return None

    first = items[0]
    print(f"  keys on [0]    : {sorted(first.keys())}")
    print(f"  itemId         : {first.get('itemId')}")
    print(f"  sample fields:")
    for key in (
        "title", "itemId", "targetSalePrice", "salePriceFormat",
        "score", "evaluateRate", "orders", "discount",
        "itemMainPic", "itemUrl",
    ):
        if key in first:
            print(f"    {key:26s} = {truncate(first[key], 70)}")

    return envelope


async def step_product_get(
    client: AliExpressClient,
    tee: TeeingAsyncClient,
    product_id: str,
) -> dict[str, Any] | None:
    banner(f"STEP 2 — aliexpress.ds.product.get (product_id={product_id})")

    business_params = {
        "product_id": str(product_id),
        "ship_to_country": TARGET_COUNTRY,
        "target_currency": "EUR",
        "target_language": "fr",
        "remove_personal_benefit": "false",
    }

    tee.tee_path = FIXTURE_DETAIL
    call_exc: BaseException | None = None
    try:
        await client._call_iop(METHOD_PRODUCT_GET, business_params)
    except BaseException as exc:  # noqa: BLE001
        call_exc = exc
    finally:
        tee.tee_path = None

    outer = _load_dumped(FIXTURE_DETAIL)
    if outer is None:
        if call_exc is not None:
            print_error(
                "product.get FAILED and no response was dumped", call_exc
            )
        return None

    print(f"\n  top-level keys : {sorted(outer.keys())}")

    envelope = _inner_envelope(outer, METHOD_PRODUCT_GET)
    if not envelope:
        print("  ✗ inner envelope 'aliexpress_ds_product_get_response' missing.")
        if call_exc is not None:
            print_error("Client raised while processing the response", call_exc)
        return None

    print(f"  inner keys     : {sorted(envelope.keys())}")

    if call_exc is not None:
        print_error(
            "Client raised on product.get response "
            "(envelope captured to fixture anyway)",
            call_exc,
        )
        return None

    result = envelope.get("result") or {}
    if not isinstance(result, dict):
        print(f"  ✗ unexpected result shape: {type(result).__name__}")
        return None

    base = result.get("ae_item_base_info_dto") or {}
    multimedia = result.get("ae_multimedia_info_dto") or {}
    logistics = result.get("logistics_info_dto") or {}
    sku_wrapper = result.get("ae_item_sku_info_dtos") or {}
    if isinstance(sku_wrapper, dict):
        sku_list = sku_wrapper.get("ae_item_sku_info_d_t_o") or []
    elif isinstance(sku_wrapper, list):
        sku_list = sku_wrapper
    else:
        sku_list = []

    print(f"\n  result keys    : {sorted(result.keys())}")
    print(f"  product_id     : {base.get('product_id') or '(missing)'}")
    print(f"  subject        : {truncate(base.get('subject'), 80)}")
    print(f"  category_id    : {base.get('category_id')}")
    print(f"  currency_code  : {base.get('currency_code')}")
    print(f"  image_urls     : {truncate(multimedia.get('image_urls'), 80)}")
    print(f"  delivery_time  : {logistics.get('delivery_time')}")
    print(f"  ship_to        : {logistics.get('ship_to_country')}")
    print(f"  SKUs count     : {len(sku_list)}")
    if sku_list and isinstance(sku_list[0], dict):
        first_sku = sku_list[0]
        print(f"  SKU[0] keys    : {sorted(first_sku.keys())}")
        print(f"  SKU[0] id      : {first_sku.get('id')}")
        print(f"  SKU[0] price   : {first_sku.get('sku_price')}")

    return envelope


async def step_freight(
    client: AliExpressClient,
    tee: TeeingAsyncClient,
    product_id: str,
    sku_id: str,
) -> dict[str, Any] | None:
    banner(
        f"STEP 3 — aliexpress.ds.freight.query "
        f"(product_id={product_id}, sku_id={sku_id})"
    )

    query_req = {
        "quantity": "1",
        "shipToCountry": TARGET_COUNTRY,
        "productId": str(product_id),
        "provinceCode": "",
        "cityCode": "",
        "selectedSkuId": str(sku_id),
        "language": "fr_FR",
        "currency": "EUR",
        "locale": "fr_FR",
    }
    business_params = {"queryDeliveryReq": json.dumps(query_req)}

    tee.tee_path = FIXTURE_FREIGHT
    call_exc: BaseException | None = None
    try:
        await client._call_iop(METHOD_FREIGHT_QUERY, business_params)
    except BaseException as exc:  # noqa: BLE001
        call_exc = exc
    finally:
        tee.tee_path = None

    outer = _load_dumped(FIXTURE_FREIGHT)
    if outer is None:
        if call_exc is not None:
            print_error(
                "freight.query FAILED and no response was dumped", call_exc
            )
        return None

    print(f"\n  top-level keys : {sorted(outer.keys())}")

    envelope = _inner_envelope(outer, METHOD_FREIGHT_QUERY)
    if not envelope:
        print("  ✗ inner envelope 'aliexpress_ds_freight_query_response' missing.")
        if call_exc is not None:
            print_error("Client raised while processing the response", call_exc)
        return None

    print(f"  inner keys     : {sorted(envelope.keys())}")

    if call_exc is not None:
        print_error(
            "Client raised on freight.query response "
            "(envelope captured to fixture anyway)",
            call_exc,
        )
        return None

    result = envelope.get("result") or {}
    if isinstance(result, dict):
        print(f"  result keys    : {sorted(result.keys())}")

    methods: list[Any] = []
    if isinstance(result, dict):
        for key in (
            "shipping_methods",
            "aeop_freight_calculate_result_for_buyer_dtolist",
            "aeop_freight_calculate_result_for_buyer_d_t_o_list",
            "freight_methods",
            "delivery_options",
        ):
            candidate = result.get(key)
            if isinstance(candidate, list):
                methods = candidate
                break

    print(f"  shipping opts  : {len(methods)}")
    for idx, m in enumerate(methods[:5], start=1):
        if not isinstance(m, dict):
            continue
        service = (
            m.get("service_name")
            or m.get("serviceName")
            or m.get("shipping_method")
        )
        eta = m.get("estimated_delivery_time") or m.get("estimatedDeliveryTime")
        freight = m.get("freight") or {}
        amount = (
            (freight.get("amount") if isinstance(freight, dict) else None)
            or m.get("amount")
            or m.get("cost")
        )
        tracked = m.get("tracking_available") or m.get("trackingAvailable")
        print(
            f"    [{idx}] {truncate(service, 45):45s}  {amount}  "
            f"ETA={eta}  tracked={tracked}"
        )

    return envelope


# ── main ────────────────────────────────────────────────────────────────────


async def main() -> int:
    config = load_config()

    banner("SMOKE TEST — AliExpress IOP Drop Shipping client")
    print(f"  gateway    : https://api-sg.aliexpress.com/sync")
    print(f"  app_key    : {config.aliexpress.app_key[:8]}…")
    print(f"  token      : {config.aliexpress.access_token[:10]}…")
    print(f"  currency   : {config.aliexpress.default_currency}")
    print(f"  language   : {config.aliexpress.default_language}")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=30.0) as inner:
        tee = TeeingAsyncClient(inner)
        async with AliExpressClient(
            config.aliexpress, http_client=tee  # type: ignore[arg-type]
        ) as client:
            search_env = await step_text_search(client, tee)
            if search_env is None:
                return 1

            items = _pick_items(search_env, METHOD_TEXT_SEARCH)
            if not items:
                return 1
            # Real AE text.search items key is `itemId` (not `product_id`).
            first_product_id = items[0].get("itemId") or items[0].get("product_id")
            if not first_product_id:
                print("\n  ✗ first item has no itemId — aborting chain.")
                return 1

            detail_env = await step_product_get(
                client, tee, str(first_product_id)
            )
            if detail_env is None:
                return 2

            detail_result = detail_env.get("result") or {}
            sku_wrapper = detail_result.get("ae_item_sku_info_dtos") or {}
            if isinstance(sku_wrapper, dict):
                sku_list = sku_wrapper.get("ae_item_sku_info_d_t_o") or []
            elif isinstance(sku_wrapper, list):
                sku_list = sku_wrapper
            else:
                sku_list = []

            first_sku_id: str | None = None
            if sku_list and isinstance(sku_list[0], dict):
                first_sku_id = (
                    sku_list[0].get("id") or sku_list[0].get("sku_id")
                )

            if not first_sku_id:
                print("\n  ✗ no SKU id extracted — skipping freight step.")
                return 2

            freight_env = await step_freight(
                client, tee, str(first_product_id), str(first_sku_id)
            )
            if freight_env is None:
                return 3

    banner("SMOKE TEST — ALL THREE STEPS OK")
    print("  fixtures written:")
    print(f"    {FIXTURE_SEARCH.relative_to(ROOT)}")
    print(f"    {FIXTURE_DETAIL.relative_to(ROOT)}")
    print(f"    {FIXTURE_FREIGHT.relative_to(ROOT)}")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        print("\n" + SEP, file=sys.stderr)
        print("  SMOKE TEST CRASHED — unhandled exception:", file=sys.stderr)
        print(SEP, file=sys.stderr)
        traceback.print_exc()
        sys.exit(99)
