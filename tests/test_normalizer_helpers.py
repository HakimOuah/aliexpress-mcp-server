"""Sync-only tests for pure helpers in `src.normalizer`.

Kept in a separate module so they don't inherit the
`pytest.mark.asyncio` module-mark from `test_normalizer.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.models import SkuRef
from src.normalizer import (
    CONCURRENCY_LIMIT,
    EU_COUNTRIES,
    FILTERS_PASSE_1,
    _build_shipping_info,
    _is_aliexpress_choice,
    _is_cheapest_absolute,
    _normalize_url,
    _parse_int_safe,
    _parse_weight_kg,
    _pick_best_option,
    _select_cheapest_in_stock,
    _split_images,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _real_freight_error_result() -> dict[str, Any]:
    data = _load("real_freight_query_error_response.json")
    return data["aliexpress_ds_freight_query_response"]["result"]


def _real_freight_success_result() -> dict[str, Any]:
    data = _load("real_freight_query_success_response.json")
    return data["aliexpress_ds_freight_query_response"]["result"]


# ── _parse_weight_kg ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.213", 0.213),
        ("1.0", 1.0),
        ("", 0.0),
        (None, 0.0),
        ("abc", 0.0),
        (0.5, 0.5),
    ],
)
def test_parse_weight_kg(raw: object, expected: float) -> None:
    assert _parse_weight_kg(raw) == pytest.approx(expected)


# ── _parse_int_safe ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("27", 27),
        ("0", 0),
        ("", 0),
        (None, 0),
        ("abc", 0),
        (42, 42),
        ("  12  ", 12),
    ],
)
def test_parse_int_safe(raw: object, expected: int) -> None:
    assert _parse_int_safe(raw) == expected


def test_parse_int_safe_custom_default() -> None:
    assert _parse_int_safe("bad", default=99) == 99


# ── _normalize_url ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("//www.aliexpress.com/item/1.html", "https://www.aliexpress.com/item/1.html"),
        ("https://example.com", "https://example.com"),
        ("http://example.com", "http://example.com"),
        ("", ""),
        (None, ""),
        ("  //a.b/c  ", "https://a.b/c"),
    ],
)
def test_normalize_url(raw: object, expected: str) -> None:
    assert _normalize_url(raw) == expected


# ── _split_images ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a.jpg;b.jpg;c.jpg", ["a.jpg", "b.jpg", "c.jpg"]),
        ("a.jpg", ["a.jpg"]),
        ("", []),
        (None, []),
        ("a.jpg; ;b.jpg", ["a.jpg", "b.jpg"]),
    ],
)
def test_split_images(raw: object, expected: list[str]) -> None:
    assert _split_images(raw) == expected


# ── _is_aliexpress_choice ──────────────────────────────────────────────────


def test_is_aliexpress_choice_positive() -> None:
    props = {
        "ae_item_property": [
            {"attr_name": "Nom de marque", "attr_value": "NONE"},
            {"attr_name": "Choice", "attr_value": "yes"},
        ]
    }
    assert _is_aliexpress_choice(props) is True


def test_is_aliexpress_choice_negative() -> None:
    props = {
        "ae_item_property": [
            {"attr_name": "Nom de marque", "attr_value": "NONE"},
        ]
    }
    assert _is_aliexpress_choice(props) is False


def test_is_aliexpress_choice_accepts_list_form() -> None:
    assert _is_aliexpress_choice([{"attr_name": "Choice", "attr_value": "yes"}]) is True


def test_is_aliexpress_choice_handles_none() -> None:
    assert _is_aliexpress_choice(None) is False


# ── _select_cheapest_in_stock ──────────────────────────────────────────────


def _mk_sku(sku_id: str, price: float, stock: int) -> SkuRef:
    return SkuRef(
        sku_id=sku_id,
        sku_attr="",
        offer_sale_price_eur=price,
        sku_price_eur=price,
        currency_code="EUR",
        available_stock=stock,
        sku_properties={},
        sku_image_url=None,
    )


def test_select_cheapest_ignores_out_of_stock() -> None:
    picked = _select_cheapest_in_stock([
        _mk_sku("A", 3.00, 0),
        _mk_sku("B", 5.00, 10),
        _mk_sku("C", 4.00, 5),
    ])
    assert picked is not None
    assert picked.sku_id == "C"


def test_select_cheapest_returns_none_when_all_out_of_stock() -> None:
    assert _select_cheapest_in_stock(
        [_mk_sku("A", 3.00, 0), _mk_sku("B", 5.00, 0)]
    ) is None


def test_select_cheapest_returns_none_when_list_empty() -> None:
    assert _select_cheapest_in_stock([]) is None


def test_select_cheapest_ignores_zero_priced() -> None:
    picked = _select_cheapest_in_stock(
        [_mk_sku("A", 0.0, 10), _mk_sku("B", 5.0, 10)]
    )
    assert picked is not None
    assert picked.sku_id == "B"


# ── _build_shipping_info ──────────────────────────────────────────────────


def _shape(options: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap a list of option dicts in the canonical live shape."""
    return {
        "msg": "Call succeeds",
        "code": 200,
        "success": True,
        "delivery_options": {"delivery_option_d_t_o": options},
    }


def test_build_shipping_info_parses_real_freight_response() -> None:
    """Golden test against the committed live success capture."""
    info = _build_shipping_info(_real_freight_success_result(), "FR")
    assert info is not None
    assert info.cost_eur == pytest.approx(1.99)
    assert info.cost_format == "1,99€"
    assert info.currency == "EUR"
    assert info.min_delivery_days == 6
    assert info.max_delivery_days == 8
    assert info.delivery_date_desc == "avr. 27 - 29"
    assert info.ship_from_country == "CN"
    assert info.is_eu_warehouse is False
    assert info.tracking is True
    assert info.company == "AliExpress Selection Standard"
    assert info.shipping_code == "CAINIAO_FULFILLMENT_STD"
    assert info.free_shipping is False


def test_build_shipping_info_returns_none_on_error_result() -> None:
    assert _build_shipping_info(_real_freight_error_result(), "FR") is None


def test_build_shipping_info_returns_none_on_empty_options() -> None:
    assert _build_shipping_info(_shape([]), "FR") is None


def test_build_shipping_info_returns_none_on_missing_delivery_options() -> None:
    # `success: true` but no delivery_options wrapper — can't build anything.
    assert _build_shipping_info(
        {"msg": "Call succeeds", "code": 200, "success": True}, "FR"
    ) is None


def test_build_shipping_info_returns_none_on_wrong_success_flag() -> None:
    assert _build_shipping_info(_shape([{"code": "X"}]) | {"success": False}, "FR") is None


def _option(**overrides: Any) -> dict[str, Any]:
    base = {
        "code": "TEST",
        "shipping_fee_currency": "EUR",
        "shipping_fee_cent": "1.99",
        "shipping_fee_format": "1,99€",
        "free_shipping": False,
        "min_delivery_days": 6,
        "max_delivery_days": 8,
        "delivery_date_desc": "avr. 27 - 29",
        "company": "Cainiao Fulfillment",
        "ship_from_country": "CN",
        "tracking": True,
    }
    base.update(overrides)
    return base


def test_build_shipping_info_prefers_eu_warehouse_over_cn() -> None:
    """Priority 1: EU wins even when CN is cheaper/faster."""
    info = _build_shipping_info(
        _shape([
            _option(code="CN_CHEAP", ship_from_country="CN",
                    shipping_fee_cent="0.50", max_delivery_days=6),
            _option(code="ES_PRICIER", ship_from_country="ES",
                    shipping_fee_cent="3.99", max_delivery_days=10),
        ]),
        "FR",
    )
    assert info is not None
    assert info.shipping_code == "ES_PRICIER"
    assert info.is_eu_warehouse is True
    assert info.ship_from_country == "ES"


def test_build_shipping_info_picks_cheapest_within_same_region() -> None:
    """Priority 2: within a tier, the cheapest wins."""
    info = _build_shipping_info(
        _shape([
            _option(code="EXPENSIVE_CN", ship_from_country="CN",
                    shipping_fee_cent="4.99", max_delivery_days=6),
            _option(code="CHEAP_CN", ship_from_country="CN",
                    shipping_fee_cent="1.99", max_delivery_days=8),
        ]),
        "FR",
    )
    assert info is not None
    assert info.shipping_code == "CHEAP_CN"
    assert info.cost_eur == pytest.approx(1.99)


def test_build_shipping_info_picks_fastest_on_price_tie() -> None:
    """Priority 3: same tier + same price → fastest wins."""
    info = _build_shipping_info(
        _shape([
            _option(code="SLOW", ship_from_country="CN",
                    shipping_fee_cent="1.99", max_delivery_days=15),
            _option(code="FAST", ship_from_country="CN",
                    shipping_fee_cent="1.99", max_delivery_days=8),
        ]),
        "FR",
    )
    assert info is not None
    assert info.shipping_code == "FAST"
    assert info.max_delivery_days == 8


def test_build_shipping_info_free_shipping_flag_trusted() -> None:
    info = _build_shipping_info(
        _shape([_option(shipping_fee_cent="0.00", free_shipping=True)]),
        "FR",
    )
    assert info is not None
    assert info.cost_eur == 0.0
    assert info.free_shipping is True


# ── _pick_best_option (sort key coverage) ───────────────────────────────────


def test_pick_best_option_returns_none_on_empty() -> None:
    assert _pick_best_option([]) is None


def test_pick_best_option_eu_beats_non_eu_regardless_of_price() -> None:
    picked = _pick_best_option([
        _option(code="CN", ship_from_country="CN", shipping_fee_cent="0.10"),
        _option(code="PL", ship_from_country="PL", shipping_fee_cent="5.00"),
    ])
    assert picked is not None
    assert picked["code"] == "PL"


def test_pick_best_option_zero_priced_without_free_shipping_loses() -> None:
    """AE occasionally returns 0 for bad data; without `free_shipping: True`
    we treat it as +inf so it doesn't accidentally win."""
    picked = _pick_best_option([
        _option(code="ZERO_BAD", ship_from_country="CN",
                shipping_fee_cent="0.00", free_shipping=False),
        _option(code="REAL", ship_from_country="CN",
                shipping_fee_cent="1.99", free_shipping=False),
    ])
    assert picked is not None
    assert picked["code"] == "REAL"


# ── Sanity ─────────────────────────────────────────────────────────────────


def test_concurrency_limit_is_reasonable() -> None:
    assert 1 <= CONCURRENCY_LIMIT <= 10


def test_eu_countries_contains_expected_markets() -> None:
    for code in ("FR", "ES", "PL", "DE", "IT", "NL", "BE", "AT", "PT", "CZ"):
        assert code in EU_COUNTRIES


def test_high_ticket_floor_is_set_to_25eur() -> None:
    assert FILTERS_PASSE_1["offer_sale_price_min_eur"] == 25.0


# ── _is_cheapest_absolute ──────────────────────────────────────────────────


def test_is_cheapest_absolute_true_when_selected_is_min() -> None:
    skus = [_mk_sku("A", 10.0, 5), _mk_sku("B", 5.0, 5), _mk_sku("C", 8.0, 5)]
    assert _is_cheapest_absolute(skus[1], skus) is True


def test_is_cheapest_absolute_false_when_selected_is_not_min() -> None:
    skus = [_mk_sku("A", 10.0, 5), _mk_sku("B", 5.0, 0), _mk_sku("C", 8.0, 5)]
    # C was selected because B is OOS — absolute min remains B at 5.0.
    assert _is_cheapest_absolute(skus[2], skus) is False


def test_is_cheapest_absolute_true_when_multiple_skus_share_min() -> None:
    skus = [_mk_sku("A", 5.0, 5), _mk_sku("B", 5.0, 5), _mk_sku("C", 8.0, 5)]
    # Either A or B equals the min — selection is arbitrary but flag True.
    assert _is_cheapest_absolute(skus[0], skus) is True
    assert _is_cheapest_absolute(skus[1], skus) is True


def test_is_cheapest_absolute_ignores_zero_priced() -> None:
    """AE sometimes returns price 0 for unavailable variants — don't
    let them win the 'absolute min' comparison."""
    skus = [_mk_sku("A", 0.0, 5), _mk_sku("B", 5.0, 5), _mk_sku("C", 8.0, 5)]
    assert _is_cheapest_absolute(skus[1], skus) is True


def test_is_cheapest_absolute_false_when_no_positive_prices() -> None:
    skus = [_mk_sku("A", 0.0, 5)]
    assert _is_cheapest_absolute(skus[0], skus) is False
