"""Sync-only tests for pure helpers in `src.aliexpress_client`.

Kept in a separate module so they don't inherit the `pytest.mark.asyncio`
module-mark from `test_aliexpress_client.py`.
"""

from __future__ import annotations

import pytest

from src.aliexpress_client import (
    METHOD_FREIGHT_QUERY,
    METHOD_PRODUCT_GET,
    METHOD_TEXT_SEARCH,
    SORT_MAP,
    _SUCCESS_CODES,
    IOPAuthError,
    IOPError,
    IOPPermissionError,
    IOPRateLimitError,
    IOPUpstreamError,
    _classify_ae_error,
    _parse_eur,
    _parse_float,
    _parse_order_count,
    _response_key_for_method,
)


def test_response_key_for_method() -> None:
    assert (
        _response_key_for_method(METHOD_TEXT_SEARCH)
        == "aliexpress_ds_text_search_response"
    )
    assert (
        _response_key_for_method(METHOD_PRODUCT_GET)
        == "aliexpress_ds_product_get_response"
    )
    assert (
        _response_key_for_method(METHOD_FREIGHT_QUERY)
        == "aliexpress_ds_freight_query_response"
    )


def test_classify_unknown_error_defaults_to_upstream() -> None:
    exc = _classify_ae_error({"code": "999", "msg": "Weird edge case"})
    assert isinstance(exc, IOPUpstreamError)
    assert not isinstance(
        exc, (IOPAuthError, IOPRateLimitError, IOPPermissionError)
    )


# Classification robustness: the live IOP gateway may return error
# strings we haven't seen yet. These cases cover plausible variants
# that _must not_ fall through to the catch-all.
@pytest.mark.parametrize(
    "err,expected",
    [
        # --- auth variants ---
        ({"msg": "IllegalAccessToken"}, IOPAuthError),
        ({"msg": "AccessTokenExpired"}, IOPAuthError),
        ({"msg": "InvalidSession"}, IOPAuthError),
        ({"msg": "SessionExpired"}, IOPAuthError),
        ({"sub_code": "isv.invalid-access-token"}, IOPAuthError),
        ({"sub_msg": "The session has expired, please re-authorize."}, IOPAuthError),
        ({"code": "27", "msg": "Some weird phrasing"}, IOPAuthError),
        ({"code": "41", "msg": "Token related error"}, IOPAuthError),
        # --- rate limit variants ---
        ({"msg": "FlowControlLimited"}, IOPRateLimitError),
        ({"sub_code": "isp.top-flow-control-limited"}, IOPRateLimitError),
        ({"msg": "APP_CALL_LIMITED"}, IOPRateLimitError),
        ({"msg": "api_call_limited"}, IOPRateLimitError),
        ({"msg": "Too Many Requests"}, IOPRateLimitError),
        ({"msg": "Throttled by upstream"}, IOPRateLimitError),
        ({"msg": "Daily quota exceeded"}, IOPRateLimitError),
        ({"code": "7", "msg": "obscure"}, IOPRateLimitError),
        # --- permission variants ---
        ({"msg": "InsufficientPermission"}, IOPPermissionError),
        ({"sub_code": "isv.permission-api-package-user-permission-not-granted"},
         IOPPermissionError),
        ({"msg": "Forbidden"}, IOPPermissionError),
        ({"msg": "AccessDenied"}, IOPPermissionError),
        ({"msg": "Permission denied for this endpoint"}, IOPPermissionError),
        ({"msg": "Not authorized"}, IOPPermissionError),
        ({"code": "15", "msg": "opaque"}, IOPPermissionError),
        # --- catch-all sanity ---
        ({"msg": "InvalidParameter", "code": "11006"}, IOPUpstreamError),
        ({"msg": "ServerInternalError"}, IOPUpstreamError),
        ({}, IOPUpstreamError),
    ],
)
def test_classify_ae_error_handles_variants(
    err: dict[str, str], expected: type[IOPError]
) -> None:
    exc = _classify_ae_error(err)
    assert isinstance(exc, expected), (
        f"{err!r} classified as {type(exc).__name__}, expected {expected.__name__}"
    )


def test_classify_ae_error_respects_check_order() -> None:
    """Auth markers short-circuit before rate-limit markers fire.

    A message like 'Session limited' contains the word 'limited' (rate
    limit marker) but is clearly an auth issue. Because auth is checked
    first, we route correctly.
    """
    exc = _classify_ae_error({"msg": "Session expired, call limited"})
    assert isinstance(exc, IOPAuthError)


def test_sort_map_covers_expected_keys() -> None:
    assert set(SORT_MAP) == {"orders", "price_asc", "price_desc", "latest"}
    for value in SORT_MAP.values():
        assert "," in value, f"sort value {value!r} must use comma syntax"


# --- success code set --------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, True),
        ("0", True),
        ("00", True),
        ("200", True),
        (200, True),
        ("1", False),
        ("500", False),
        ("error", False),
    ],
)
def test_success_codes_accepts_expected_forms(raw: object, expected: bool) -> None:
    assert (str(raw) in _SUCCESS_CODES) is expected


# --- _parse_order_count ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5,000+", 5000),
        ("1000", 1000),
        ("1,284", 1284),
        ("10+", 10),
        ("0", 0),
        ("", 0),
        (None, 0),
        ("abc", 0),
        ("5 000", 5000),
        (500, 500),  # int passthrough
        ("   5,000+   ", 5000),  # surrounding whitespace
        ("+", 0),  # degenerate: empty after strip
    ],
)
def test_parse_order_count(raw: object, expected: int) -> None:
    assert _parse_order_count(raw) == expected


# --- _parse_float ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4.5", 4.5),
        ("0", 0.0),
        ("0.0", 0.0),
        ("10", 10.0),
        ("", 0.0),
        (None, 0.0),
        ("abc", 0.0),
        (4.5, 4.5),  # float passthrough via str()
        ("  4.5  ", 4.5),
    ],
)
def test_parse_float(raw: object, expected: float) -> None:
    assert _parse_float(raw) == pytest.approx(expected)


def test_parse_float_rejects_comma_decimal() -> None:
    """French-locale format like '4,5' is rejected — use `salePriceFormat`
    for display, `targetSalePrice` (dotted) for math."""
    assert _parse_float("4,5") == 0.0


# --- _parse_eur --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3.29", 3.29),
        ("10", 10.0),
        ("0", 0.0),
        ("", 0.0),
        (None, 0.0),
        ("abc", 0.0),
    ],
)
def test_parse_eur(raw: object, expected: float) -> None:
    assert _parse_eur(raw) == pytest.approx(expected)
