from __future__ import annotations

import httpx
import pytest

from src.config import DataForSEOConfig
from src.dataforseo_client import DataForSEOClient


def _config() -> DataForSEOConfig:
    return DataForSEOConfig(login="login", password="password")


@pytest.mark.asyncio
async def test_transient_40101_is_retried_then_succeeds() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "status_code": 20000,
                    "tasks": [
                        {
                            "status_code": 40101,
                            "status_message": "Internal SE Server Error.",
                            "cost": 0,
                            "result": None,
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "id": "retry-success",
                        "status_code": 20000,
                        "status_message": "Ok.",
                        "cost": 0.002,
                        "result": [
                            {
                                "keyword": "site:aliexpress.com tufting gun",
                                "items": [],
                                "item_types": ["organic"],
                                "se_results_count": 10,
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
    ) as http_client:
        client = DataForSEOClient(
            _config(),
            http_client=http_client,
            retry_attempts=2,
            retry_backoff_seconds=0,
        )
        result = await client.google_serp_live("site:aliexpress.com tufting gun")

    assert calls == 2
    assert result["task_id"] == "retry-success"
    assert result["cost"] == 0.002


@pytest.mark.asyncio
async def test_40102_no_results_returns_empty_serp() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 20000,
                "tasks": [
                    {
                        "id": "no-results",
                        "status_code": 40102,
                        "status_message": "No Search Results.",
                        "cost": 0,
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
        client = DataForSEOClient(
            _config(),
            http_client=http_client,
            retry_backoff_seconds=0,
        )
        result = await client.google_serp_live("site:aliexpress.com impossible-product")

    assert result["items"] == []
    assert result["no_results"] is True
    assert result["task_id"] == "no-results"
