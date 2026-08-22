from __future__ import annotations

from src.opportunity_diagnostics import summarize_filter_failures


def test_summarize_filter_failures_counts_failures() -> None:
    diagnostics = [
        {"failed_filters": ["rating_min", "orders_min"]},
        {"failed_filters": ["orders_min"]},
        {"failed_filters": ["store_shipping_rating_min", "orders_min"]},
    ]

    result = summarize_filter_failures(diagnostics)

    assert result["total_candidates"] == 3
    assert result["failure_counts"][0] == {"filter": "orders_min", "count": 3}
    assert result["failure_counts"][1] == {"filter": "rating_min", "count": 1}
    assert result["failure_counts"][2] == {"filter": "store_shipping_rating_min", "count": 1}
