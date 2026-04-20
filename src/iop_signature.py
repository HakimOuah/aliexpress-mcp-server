"""AliExpress IOP request signing (HMAC-SHA256).

Two signing schemes exist on the IOP gateway:

* **Business endpoints** (`https://api-sg.aliexpress.com/sync`, e.g.
  `aliexpress.ds.*`) — the API method is passed as the `method` form
  parameter and takes part in the signature. The signed base string is
  the alphabetically-sorted concatenation of `key+value` pairs. No path
  prefix.

* **System endpoints** (`https://api-sg.aliexpress.com/rest`, e.g.
  `/auth/token/create`) — the API path is prefixed to the sorted
  concatenation of `key+value` pairs, and the path is NOT itself a
  parameter.

Reference implementations cross-checked:
    https://github.com/richardyanhao/AEDropshipSimpleSDK-Python
    https://github.com/Indigoiamlove/AEDropShipperPHPDemoCode

Callers must NOT include the `sign` field in the params dict passed to
the signing functions. `sign_method` and `timestamp`, however, MUST be
part of the signed params (they are business/system params too).
Empty or `None` values are skipped, matching the official PHP/Python
reference SDKs.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping


SIGN_METHOD = "sha256"
RESPONSE_FORMAT = "json"


def _canonical_base(params: Mapping[str, str]) -> str:
    """Return the alphabetically-sorted concat of `key+value` pairs.

    Skips entries whose value is `None` or empty string — this matches
    the reference PHP/Python SDKs and avoids signing noise.
    """
    return "".join(
        f"{k}{v}" for k, v in sorted(params.items()) if v not in (None, "")
    )


def _hmac_sha256_hex_upper(secret: str, base: str) -> str:
    return (
        hmac.new(
            secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        )
        .hexdigest()
        .upper()
    )


def sign_business_request(app_secret: str, params: Mapping[str, str]) -> str:
    """Sign a business-endpoint request (gateway `/sync`).

    The signed string is the alphabetically-sorted concatenation of
    `key+value` pairs over ALL params (system + business), with no path
    prefix. The `method` parameter is part of `params` and therefore
    part of the signature.
    """
    return _hmac_sha256_hex_upper(app_secret, _canonical_base(params))


def sign_system_request(
    app_secret: str, api_path: str, params: Mapping[str, str]
) -> str:
    """Sign a system-endpoint request (gateway `/rest`).

    The signed string is `api_path` + alphabetically-sorted concatenation
    of `key+value` pairs. The path is NOT itself a parameter and `method`
    is not part of the params.
    """
    return _hmac_sha256_hex_upper(app_secret, api_path + _canonical_base(params))


def build_business_system_params(
    app_key: str,
    access_token: str,
    method: str,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    """Build the system-level parameters for a business request.

    Returns a fresh dict with the six system params required on every
    `/sync` call. Merge the business-specific params into this dict
    before signing, then add the resulting `sign` last.

    `timestamp_ms` is injectable so tests can pin a known value. When
    omitted, the current wall clock is used.
    """
    ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
    return {
        "app_key": app_key,
        "session": access_token,
        "timestamp": ts,
        "sign_method": SIGN_METHOD,
        "format": RESPONSE_FORMAT,
        "method": method,
    }
