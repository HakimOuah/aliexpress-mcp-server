"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.aliexpress_client import AliExpressClient
from src.config import AliExpressConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> object:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as fp:
        return json.load(fp)


def _to_namespace(obj: object) -> object:
    """Recursively convert dicts/lists into SimpleNamespace, matching the SDK."""
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_namespace(item) for item in obj]
    return obj


@pytest.fixture
def fake_search_products() -> list[object]:
    raw = _load_fixture("products_search.json")
    assert isinstance(raw, list)
    return [_to_namespace(item) for item in raw]


@pytest.fixture
def fake_product_detail() -> object:
    return _to_namespace(_load_fixture("product_details.json"))


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
def mock_sdk(fake_search_products: list[object], fake_product_detail: object) -> MagicMock:
    """A MagicMock standing in for `AliexpressApi`.

    Default behaviour returns the fixture data; individual tests can override
    `.get_products.side_effect` etc. as needed.
    """
    sdk = MagicMock()
    sdk.get_products.return_value = SimpleNamespace(
        current_page_no=1,
        current_record_count=len(fake_search_products),
        total_record_count=len(fake_search_products),
        products=fake_search_products,
    )
    sdk.get_products_details.return_value = [fake_product_detail]
    return sdk


@pytest.fixture
def client(ae_config: AliExpressConfig, mock_sdk: MagicMock) -> AliExpressClient:
    return AliExpressClient(config=ae_config, sdk=mock_sdk)
