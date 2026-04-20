"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from src.aliexpress_client import AliExpressClient
from src.config import AliExpressConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as fp:
        return json.load(fp)


def make_httpx_response(
    payload: dict[str, Any] | str, status_code: int = 200
) -> httpx.Response:
    """Build a real httpx.Response so `.json()` / `.text` / `.status_code` all work."""
    if isinstance(payload, dict):
        content = json.dumps(payload).encode("utf-8")
    else:
        content = payload.encode("utf-8")
    request = httpx.Request("POST", "https://api-sg.aliexpress.com/sync")
    return httpx.Response(
        status_code=status_code, content=content, request=request
    )


@pytest.fixture
def ae_config() -> AliExpressConfig:
    return AliExpressConfig(
        app_key="test-key",
        app_secret="test-secret",
        access_token="test-access",
        refresh_token="test-refresh",
        callback_url="https://example.test/cb",
        default_language="FR",
        default_currency="EUR",
        tracking_id="default",
    )


@pytest.fixture
def mock_http() -> AsyncMock:
    """AsyncMock standing in for httpx.AsyncClient.

    Tests override `.post.return_value` (or `.side_effect`) per scenario.
    """
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def client(
    ae_config: AliExpressConfig, mock_http: AsyncMock
) -> AliExpressClient:
    return AliExpressClient(config=ae_config, http_client=mock_http)
