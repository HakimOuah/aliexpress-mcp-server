from __future__ import annotations

from typing import Any

import pytest

import src.product_factory_server as product_factory
from src.dataforseo_client import DataForSEOUpstreamError


class PartialFailureClient:
    async def google_serp_live(self, keyword: str, **kwargs: Any) -> dict[str, Any]:
        if keyword == "bad-query":
            raise DataForSEOUpstreamError(
                "DataForSEO task error 40101: Internal SE Server Error.",
                status_code=40101,
                status_message="Internal SE Server Error.",
                retryable=True,
            )
        return {
            "keyword": keyword,
            "cost": 0.002,
            "items": [
                {
                    "type": "organic",
                    "title": "AK-V Tufting Gun Cut Loop Pile Rug Machine",
                    "url": "https://fr.aliexpress.com/item/1005001234567890.html",
                }
            ],
        }


@pytest.mark.asyncio
async def test_discovery_skips_failed_query_and_keeps_successes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        product_factory,
        "google_discovery_queries",
        lambda target_terms: ["bad-query", "good-query"],
    )
    product_factory.set_dataforseo_client_for_testing(PartialFailureClient())  # type: ignore[arg-type]

    try:
        queries, items, cost, diagnostics = (
            await product_factory._discover_aliexpress_via_google(
                target_terms=["tufting gun", "machine tufting"],
                country_code="FR",
            )
        )
    finally:
        product_factory.reset_dataforseo_for_testing()

    assert queries == ["bad-query", "good-query"]
    assert [item["itemId"] for item in items] == ["1005001234567890"]
    assert cost == 0.002
    assert diagnostics["queries_total"] == 2
    assert diagnostics["queries_succeeded"] == 1
    assert diagnostics["queries_failed"] == 1
    assert diagnostics["partial"] is True
    assert diagnostics["failed"] is False
    assert diagnostics["errors"][0]["status_code"] == 40101
