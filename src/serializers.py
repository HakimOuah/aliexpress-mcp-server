"""Serializers for transporting dataclass instances over MCP.

Everything the scout agent receives must be a plain JSON-compatible
dict (strings, numbers, bools, lists, dicts, nulls). The only
non-trivial transformation is `datetime` → ISO 8601 string; the rest
of the work is `dataclasses.asdict`, which recurses into nested
dataclasses automatically.

Field names are preserved exactly (`itemId`, `sku_id`, `ship_from_country`,
…) — the scout expects these keys in its prompt/tool schema.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .models import DropPilotProduct, ItemDiagnostic


def serialize_product(product: DropPilotProduct) -> dict[str, Any]:
    """Convert a `DropPilotProduct` into a JSON-ready dict.

    The MCP transport JSON-serializes tool return values, so all
    values must be primitives or plain containers. `dataclasses.asdict`
    does the heavy lifting (nested dataclasses recurse, lists/dicts
    pass through); we only post-process `datetime` → ISO string.
    """
    data = asdict(product)
    fetched_at = data.get("fetched_at")
    if isinstance(fetched_at, datetime):
        data["fetched_at"] = fetched_at.isoformat()
    return data


def serialize_diagnostic(diag: ItemDiagnostic) -> dict[str, Any]:
    """Convert an `ItemDiagnostic` into a JSON-ready dict.

    Deliberately excludes the embedded `product` payload: this
    serializer is used by `search_and_diagnose`, whose purpose is
    concise calibration output (hundreds of bytes per candidate).
    Callers who need the full product for PASS items should call
    `search_and_normalize` instead.
    """
    return {
        "product_id": diag.product_id,
        "title": diag.title,
        "verdict": diag.verdict,
        "passed_filters": list(diag.passed_filters),
        "failed_filters": list(diag.failed_filters),
        "offer_sale_price_eur": diag.offer_sale_price_eur,
        "rating": diag.rating,
        "order_count": diag.order_count,
        "store_ratings": dict(diag.store_ratings) if diag.store_ratings is not None else None,
    }
