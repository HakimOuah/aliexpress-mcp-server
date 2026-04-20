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
    IOPAuthError,
    IOPError,
    IOPPermissionError,
    IOPRateLimitError,
    IOPUpstreamError,
    _classify_ae_error,
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
