"""Async DataForSEO client for Google SERP market research."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from .config import DataForSEOConfig


class DataForSEOError(RuntimeError):
    """Base error for DataForSEO requests."""


class DataForSEOAuthError(DataForSEOError):
    """Invalid or missing DataForSEO credentials."""


class DataForSEOUpstreamError(DataForSEOError):
    """DataForSEO returned an API/task error."""


_COUNTRY_LOCATION_NAMES: dict[str, str] = {
    "FR": "France",
    "BE": "Belgium",
    "CH": "Switzerland",
    "LU": "Luxembourg",
    "DE": "Germany",
    "ES": "Spain",
    "IT": "Italy",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "US": "United States",
}

_DEFAULT_LANGUAGES: dict[str, str] = {
    "FR": "fr",
    "BE": "fr",
    "CH": "fr",
    "LU": "fr",
    "DE": "de",
    "ES": "es",
    "IT": "it",
    "GB": "en",
    "UK": "en",
    "US": "en",
}


class DataForSEOClient:
    """Thin async wrapper around DataForSEO SERP API v3."""

    def __init__(
        self,
        config: DataForSEOConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.enabled:
            raise DataForSEOAuthError(
                "DataForSEO credentials missing: set DATAFORSEO_LOGIN and "
                "DATAFORSEO_PASSWORD"
            )
        self.config = config
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            auth=(config.login, config.password),
            headers={"Content-Type": "application/json"},
            timeout=config.timeout_seconds,
        )

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def google_serp_live(
        self,
        keyword: str,
        *,
        country_code: str = "FR",
        language_code: str | None = None,
        device: str = "desktop",
        depth: int = 20,
        location_name: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a live Google organic SERP in Advanced format.

        Advanced results include organic/paid/shopping and other SERP
        elements when Google displays them.
        """
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("keyword must not be empty")
        if not 1 <= depth <= 100:
            raise ValueError("depth must be between 1 and 100")
        if device not in {"desktop", "mobile"}:
            raise ValueError("device must be 'desktop' or 'mobile'")

        cc = country_code.upper()
        resolved_location = location_name or _COUNTRY_LOCATION_NAMES.get(cc, cc)
        resolved_language = language_code or _DEFAULT_LANGUAGES.get(cc, "en")
        payload: dict[str, Any] = {
            "keyword": keyword,
            "location_name": resolved_location,
            "language_code": resolved_language,
            "device": device,
            "depth": depth,
        }
        return await self._post_live(
            "/v3/serp/google/organic/live/advanced",
            payload,
        )

    async def _post_live(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(path, json=[payload])
        except httpx.HTTPError as exc:
            raise DataForSEOUpstreamError(f"DataForSEO network error: {exc}") from exc

        if response.status_code in {401, 403}:
            raise DataForSEOAuthError(
                f"DataForSEO authentication failed (HTTP {response.status_code})"
            )
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DataForSEOUpstreamError(
                f"Invalid DataForSEO response (HTTP {response.status_code})"
            ) from exc

        status_code = body.get("status_code")
        if status_code != 20000:
            raise DataForSEOUpstreamError(
                f"DataForSEO API error {status_code}: {body.get('status_message', 'unknown')}"
            )

        tasks = body.get("tasks") or []
        if not tasks:
            raise DataForSEOUpstreamError("DataForSEO response contains no task")
        task = tasks[0]
        if task.get("status_code") != 20000:
            raise DataForSEOUpstreamError(
                f"DataForSEO task error {task.get('status_code')}: "
                f"{task.get('status_message', 'unknown')}"
            )

        result = task.get("result") or []
        if not result:
            return {
                "keyword": payload.get("keyword"),
                "items": [],
                "item_types": [],
                "se_results_count": 0,
                "cost": task.get("cost"),
            }

        first = dict(result[0])
        first["cost"] = task.get("cost")
        first["task_id"] = task.get("id")
        return first


def iter_serp_items(items: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """Yield SERP items recursively, including nested Shopping elements."""
    for item in items:
        yield item
        nested = item.get("items")
        if isinstance(nested, list):
            child_items = [child for child in nested if isinstance(child, dict)]
            yield from iter_serp_items(child_items)


def extract_shopping_items(serp: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract Shopping/product result elements from an Advanced SERP."""
    shopping_types = {
        "shopping",
        "shopping_element",
        "popular_products",
        "popular_products_element",
        "commercial_units",
        "commercial_units_element",
        "refine_products",
        "refine_products_element",
    }
    items = serp.get("items") or []
    if not isinstance(items, list):
        return []
    return [
        item
        for item in iter_serp_items(i for i in items if isinstance(i, dict))
        if str(item.get("type", "")) in shopping_types
    ]
