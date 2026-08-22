from __future__ import annotations

import httpx
import pytest

from src.config import DataForSEOConfig
from src.dataforseo_client import (
    DataForSEOAuthError,
    DataForSEOClient,
    DataForSEOUpstreamError,
    extract_shopping_items,
)


def _config() -> DataForSEOConfig:
    return DataForSEOConfig(login="login", password="password")


@pytest.mark.asyncio
async def test_google_serp_live_returns_first_result_and_cost() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/serp/google/organic/live/advanced"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "status_message": "Ok.",
                "tasks": [
                    {
                        "id": "task-1",
                        "status_code": 20000,
                        "status_message": "Ok.",
                        "cost": 0.002,
                        "result": [
                            {
                                "keyword": "tufting gun",
                                "item_types": ["organic", "paid", "shopping"],
                                "se_results_count": 12345,
                                "items": [],
                            }
                        ],
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.dataforseo.com",
        auth=("login", "password"),
    ) as http_client:
        client = DataForSEOClient(_config(), http_client=http_client)
        result = await client.google_serp_live("tufting gun", country_code="FR")

    assert result["keyword"] == "tufting gun"
    assert result["cost"] == 0.002
    assert result["task_id"] == "task-1"


def test_missing_credentials_are_rejected() -> None:
    with pytest.raises(DataForSEOAuthError):
        DataForSEOClient(DataForSEOConfig())


@pytest.mark.asyncio
async def test_task_error_is_raised() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "status_code": 40501,
                        "status_message": "Invalid Field",
                        "result": None,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://api.dataforseo.com",
    ) as http_client:
        client = DataForSEOClient(_config(), http_client=http_client)
        with pytest.raises(DataForSEOUpstreamError, match="40501"):
            await client.google_serp_live("test")


def test_extract_shopping_items_recurses() -> None:
    serp = {
        "items": [
            {"type": "organic", "domain": "example.com"},
            {
                "type": "shopping",
                "items": [
                    {
                        "type": "shopping_element",
                        "title": "Product A",
                        "price": 99.9,
                        "domain": "merchant.fr",
                    }
                ],
            },
        ]
    }
    result = extract_shopping_items(serp)
    assert [item["type"] for item in result] == ["shopping", "shopping_element"]
