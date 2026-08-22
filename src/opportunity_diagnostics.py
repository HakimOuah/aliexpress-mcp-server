"""Diagnostics for AliExpress qualification failures in opportunity analysis."""

from __future__ import annotations

from collections import Counter
from typing import Any


def summarize_filter_failures(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for item in diagnostics:
        failed = item.get("failed_filters") or []
        if isinstance(failed, list):
            counts.update(str(name) for name in failed)

    return {
        "total_candidates": len(diagnostics),
        "failure_counts": [
            {"filter": name, "count": count}
            for name, count in counts.most_common()
        ],
    }
