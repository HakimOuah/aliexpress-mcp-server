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

from .models import DropPilotProduct


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
