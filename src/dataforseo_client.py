"""Async DataForSEO client for Google SERP market research."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx

from .config import DataForSEOConfig


class DataForSEOError(RuntimeError):
    """Base error for DataForSEO requests."""


class DataForSEOAuthError(DataForSEOError):
    """Invalid or missing DataForSEO credentials."""


class DataForSEOUpstreamError(DataForSEOError):
    """DataForSEO returned an API/task error.

    ``status_code`` is the DataForSEO internal code when one is available.
    ``retryable`` marks transient search-engine/network failures that can be
    resubmitted safely by callers or by the built-in retry loop.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        status_message: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.status_message = status_message
        self.retryable = retryable


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

_TRANSIENT_STATUS_CODES = {40101, 40103}
_NO_RESULTS_STATUS_CODE = 40102


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class DataForSEOClient:
    """Thin async wrapper around DataForSEO SERP API v3."""

    def __init__(
        self,
        config: DataForSEOConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.75,
    ) -> None:
        if not config.enabled:
            raise DataForSEOAuthError(
                "DataForSEO credentials missing: set DATAFORSEO_LOGIN and "
                "DATAFORSEO_PASSWORD"
            )
        if retry_attempts < 1:
            raise ValueError("retry_attempts must be >= 1")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be >= 0")

        self.config = config
        self._owns_http_client = http_client is None
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
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
        elements when Google displays them. Transient DataForSEO task codes
        40101/40103 and transport failures are retried with bounded backoff.
        A 40102 "No Search Results" task is returned as an empty SERP rather
        than raised as an exception.
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
        for attempt in range(self._retry_attempts):
            try:
                return await self._post_live_once(path, payload)
            except DataForSEOUpstreamError as exc:
                is_last_attempt = attempt >= self._retry_attempts - 1
                if not exc.retryable or is_last_attempt:
                    raise
                delay = self._retry_backoff_seconds * (2**attempt)
                if delay > 0:
                    await asyncio.sleep(delay)

        raise AssertionError("unreachable DataForSEO retry state")

    async def _post_live_once(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._http.post(path, json=[payload])
        except httpx.TransportError as exc:
            raise DataForSEOUpstreamError(
                f"DataForSEO network error: {exc}",
                retryable=True,
            ) from exc

        if response.status_code in {401, 403}:
            raise DataForSEOAuthError(
                f"DataForSEO authentication failed (HTTP {response.status_code})"
            )
        if response.status_code >= 500:
            raise DataForSEOUpstreamError(
                f"DataForSEO HTTP server error {response.status_code}",
                status_code=response.status_code,
                retryable=True,
            )

        try:
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise DataForSEOUpstreamError(
                f"DataForSEO HTTP error {response.status_code}",
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            ) from exc
        except ValueError as exc:
            raise DataForSEOUpstreamError(
                f"Invalid DataForSEO response (HTTP {response.status_code})"
            ) from exc

        status_code = _as_int(body.get("status_code"))
        status_message = str(body.get("status_message") or "unknown")
        if status_code != 20000:
            raise DataForSEOUpstreamError(
                f"DataForSEO API error {status_code}: {status_message}",
                status_code=status_code,
                status_message=status_message,
                retryable=status_code in _TRANSIENT_STATUS_CODES,
            )

        tasks = body.get("tasks") or []
        if not tasks:
            raise DataForSEOUpstreamError("DataForSEO response contains no task")
        task = tasks[0]
        task_code = _as_int(task.get("status_code"))
        task_message = str(task.get("status_message") or "unknown")

        if task_code == _NO_RESULTS_STATUS_CODE:
            return self._empty_result(payload, task, no_results=True)
        if task_code != 20000:
            raise DataForSEOUpstreamError(
                f"DataForSEO task error {task_code}: {task_message}",
                status_code=task_code,
                status_message=task_message,
                retryable=task_code in _TRANSIENT_STATUS_CODES,
            )

        result = task.get("result") or []
        if not result:
            return self._empty_result(payload, task)

        first = dict(result[0])
        first["cost"] = task.get("cost")
        first["task_id"] = task.get("id")
        return first

    @staticmethod
    def _empty_result(
        payload: dict[str, Any],
        task: dict[str, Any],
        *,
        no_results: bool = False,
    ) -> dict[str, Any]:
        return {
            "keyword": payload.get("keyword"),
            "items": [],
            "item_types": [],
            "se_results_count": 0,
            "cost": task.get("cost"),
            "task_id": task.get("id"),
            "no_results": no_results,
        }


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
