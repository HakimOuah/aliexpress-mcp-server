"""Tests for `src.iop_signature`.

Strategy: every test either
* Recomputes the expected value with the stdlib (hmac + hashlib) from
  the documented algorithm — catches accidental divergence from spec, or
* Pins a hand-generated "golden" hex vector — catches silent algo
  changes (hash family swap, prefix removal, case flip, ...).
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from src.iop_signature import (
    RESPONSE_FORMAT,
    SIGN_METHOD,
    build_business_system_params,
    sign_business_request,
    sign_system_request,
)


# Golden vectors — generated once via `.venv/bin/python` with the
# documented algorithm. Pinned here so any accidental change to the
# signing code or its inputs trips these tests.
GOLDEN_SECRET = "TEST_SECRET_123"

GOLDEN_BIZ_PARAMS: dict[str, str] = {
    "app_key": "APPK",
    "method": "aliexpress.ds.text.search",
    "sign_method": "sha256",
    "timestamp": "1700000000000",
    "keyWord": "yoga mat",
    "countryCode": "FR",
    "session": "ATOK",
    "format": "json",
}
GOLDEN_BIZ_SIG = "22ACCEEA01395E49C9A1964220A7147F247086AC1619303DAD6A951DCCA54DD2"

GOLDEN_SYS_PATH = "/auth/token/create"
GOLDEN_SYS_PARAMS: dict[str, str] = {
    "app_key": "APPK",
    "code": "DUMMYCODE",
    "sign_method": "sha256",
    "simplify": "true",
    "timestamp": "1700000000000",
}
GOLDEN_SYS_SIG = "FF93C8DAC68C269E7557712F5F631C7AA2F0A49C5FCC58B9E00A17512DE02170"


def _reference_hmac(secret: str, base: str) -> str:
    return (
        hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256)
        .hexdigest()
        .upper()
    )


# --- business signing --------------------------------------------------------


def test_sign_business_matches_reference_hmac() -> None:
    base = (
        "app_keyAPPKcountryCodeFRformatjsonkeyWordyoga mat"
        "methodaliexpress.ds.text.searchsessionATOKsign_methodsha256"
        "timestamp1700000000000"
    )
    assert sign_business_request(GOLDEN_SECRET, GOLDEN_BIZ_PARAMS) == _reference_hmac(
        GOLDEN_SECRET, base
    )


def test_sign_business_golden_vector() -> None:
    assert sign_business_request(GOLDEN_SECRET, GOLDEN_BIZ_PARAMS) == GOLDEN_BIZ_SIG


def test_sign_business_is_order_independent() -> None:
    shuffled = {k: GOLDEN_BIZ_PARAMS[k] for k in reversed(list(GOLDEN_BIZ_PARAMS))}
    assert sign_business_request(GOLDEN_SECRET, shuffled) == GOLDEN_BIZ_SIG


def test_sign_business_skips_none_and_empty_values() -> None:
    padded = {
        **GOLDEN_BIZ_PARAMS,
        "empty_string": "",
        "null_value": None,  # type: ignore[dict-item]
    }
    assert sign_business_request(GOLDEN_SECRET, padded) == GOLDEN_BIZ_SIG


def test_sign_business_is_uppercase_hex_64_chars() -> None:
    sig = sign_business_request(GOLDEN_SECRET, GOLDEN_BIZ_PARAMS)
    assert len(sig) == 64
    assert sig == sig.upper()
    int(sig, 16)  # must parse as hex


def test_sign_business_differs_when_secret_changes() -> None:
    a = sign_business_request("SECRET_A", GOLDEN_BIZ_PARAMS)
    b = sign_business_request("SECRET_B", GOLDEN_BIZ_PARAMS)
    assert a != b


# --- system signing ----------------------------------------------------------


def test_sign_system_matches_reference_hmac() -> None:
    base = (
        "/auth/token/createapp_keyAPPKcodeDUMMYCODE"
        "sign_methodsha256simplifytruetimestamp1700000000000"
    )
    assert sign_system_request(
        GOLDEN_SECRET, GOLDEN_SYS_PATH, GOLDEN_SYS_PARAMS
    ) == _reference_hmac(GOLDEN_SECRET, base)


def test_sign_system_golden_vector() -> None:
    assert (
        sign_system_request(GOLDEN_SECRET, GOLDEN_SYS_PATH, GOLDEN_SYS_PARAMS)
        == GOLDEN_SYS_SIG
    )


def test_sign_system_differs_from_business_on_same_params() -> None:
    """Identical params but one carries a path prefix must not collide."""
    biz = sign_business_request(GOLDEN_SECRET, GOLDEN_SYS_PARAMS)
    sys_ = sign_system_request(GOLDEN_SECRET, GOLDEN_SYS_PATH, GOLDEN_SYS_PARAMS)
    assert biz != sys_


def test_sign_system_differs_when_path_changes() -> None:
    a = sign_system_request(GOLDEN_SECRET, "/auth/token/create", GOLDEN_SYS_PARAMS)
    b = sign_system_request(GOLDEN_SECRET, "/auth/token/refresh", GOLDEN_SYS_PARAMS)
    assert a != b


def test_sign_system_is_order_independent() -> None:
    shuffled = {k: GOLDEN_SYS_PARAMS[k] for k in reversed(list(GOLDEN_SYS_PARAMS))}
    assert (
        sign_system_request(GOLDEN_SECRET, GOLDEN_SYS_PATH, shuffled)
        == GOLDEN_SYS_SIG
    )


# --- ae_oauth.py parity — regression shield for the refactor -----------------


def test_sign_system_matches_legacy_ae_oauth_implementation() -> None:
    """Reproduce `scripts/ae_oauth.py::sign_request` to lock parity.

    The refactor will have `ae_oauth.py` call `sign_system_request`. This
    test mirrors the exact concat formula the legacy inline function used
    (no empty-value skip, but that's behaviourally identical here since
    the real OAuth call never passes empty values).
    """
    sorted_params = sorted(GOLDEN_SYS_PARAMS.items())
    legacy_base = GOLDEN_SYS_PATH + "".join(f"{k}{v}" for k, v in sorted_params)
    legacy_sig = _reference_hmac(GOLDEN_SECRET, legacy_base)

    assert (
        sign_system_request(GOLDEN_SECRET, GOLDEN_SYS_PATH, GOLDEN_SYS_PARAMS)
        == legacy_sig
    )


# --- build_business_system_params --------------------------------------------


def test_build_business_system_params_returns_all_required_keys() -> None:
    params = build_business_system_params(
        app_key="APPK", access_token="ATOK", method="aliexpress.ds.text.search"
    )
    assert set(params.keys()) == {
        "app_key",
        "session",
        "timestamp",
        "sign_method",
        "format",
        "method",
    }


def test_build_business_system_params_fixed_values() -> None:
    params = build_business_system_params(
        app_key="APPK",
        access_token="ATOK",
        method="aliexpress.ds.text.search",
        timestamp_ms=1700000000000,
    )
    assert params["app_key"] == "APPK"
    assert params["session"] == "ATOK"
    assert params["method"] == "aliexpress.ds.text.search"
    assert params["sign_method"] == SIGN_METHOD == "sha256"
    assert params["format"] == RESPONSE_FORMAT == "json"
    assert params["timestamp"] == "1700000000000"


def test_build_business_system_params_does_not_include_sign_key() -> None:
    """The `sign` field is added AFTER signing; the builder must not leak one."""
    params = build_business_system_params("APPK", "ATOK", "x")
    assert "sign" not in params


def test_build_business_system_params_uses_wall_clock_when_ts_omitted() -> None:
    params = build_business_system_params("APPK", "ATOK", "x")
    assert params["timestamp"].isdigit()
    assert len(params["timestamp"]) == 13  # ms since epoch, ~2020+


def test_build_business_system_params_end_to_end_with_signing() -> None:
    """Full flow: build system params, merge business params, sign."""
    system = build_business_system_params(
        app_key="APPK",
        access_token="ATOK",
        method="aliexpress.ds.text.search",
        timestamp_ms=1700000000000,
    )
    business = {"keyWord": "yoga mat", "countryCode": "FR"}
    merged = {**system, **business}

    sig = sign_business_request(GOLDEN_SECRET, merged)

    assert sig == GOLDEN_BIZ_SIG


# --- misc --------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"only_empty": ""},
        {"only_none": None},  # type: ignore[dict-item]
    ],
)
def test_sign_business_with_no_effective_params_is_hmac_of_empty(
    params: dict[str, str],
) -> None:
    """Edge case: hashing an empty base is legal and deterministic."""
    sig = sign_business_request("SECRET", params)
    expected = _reference_hmac("SECRET", "")
    assert sig == expected
