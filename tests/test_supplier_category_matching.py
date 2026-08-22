from types import SimpleNamespace

from src.offer_classifier import classify_offer
from src.product_factory_server import _build_category_matched_economics


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def _rules():
    return SimpleNamespace(
        vat_fr=0.20,
        vat_be=0.21,
        vat_ch=0.081,
        vat_lu=0.17,
        min_margin_pct=40.0,
    )


def _candidate(title: str, cost: float) -> dict:
    return {
        "product_id": title,
        "title": title,
        "store": "Supplier",
        "landed_cost_eur": cost,
        "orders": 10,
        "quality_verdict": "WATCH",
        "quality_score": 70,
    }


def test_manual_tufting_tool_is_not_comparable_to_machine_target() -> None:
    assert (
        classify_offer(
            "Speed Tufting Tool Manual tufting tool DIY carpet gun",
            "Supplier",
            TARGETS,
        )
        == "IRRELEVANT"
    )


def test_manual_target_can_still_classify_manual_product() -> None:
    assert (
        classify_offer(
            "Manual Tufting Tool for DIY Rugs",
            "Supplier",
            ["manual tufting tool"],
        )
        == "PRODUCT"
    )


def test_requested_product_and_bundle_use_different_market_prices() -> None:
    market = {
        "pricing_by_category": {
            "PRODUCT": {"median": 187.0},
            "BUNDLE": {"median": 249.0},
        }
    }
    qualified = [
        _candidate("2 In 1 Tufting Gun Cut Loop Rug Machine Electric Carpet", 107.0),
        _candidate("Electric Tufting Gun 2 in 1 Set with Fabric and Carpet Trimmer", 86.0),
    ]

    primary, alternate, unpriced = _build_category_matched_economics(
        qualified,
        market=market,
        target_terms=TARGETS,
        requested_category="PRODUCT",
        country_code="FR",
        rules=_rules(),
    )

    assert len(primary) == 1
    assert primary[0]["supplier_category"] == "PRODUCT"
    assert primary[0]["market_price_ttc_eur"] == 187.0
    assert len(alternate) == 1
    assert alternate[0]["supplier_category"] == "BUNDLE"
    assert alternate[0]["market_price_ttc_eur"] == 249.0
    assert unpriced == []


def test_irrelevant_supplier_cannot_drive_requested_category_status() -> None:
    market = {"pricing_by_category": {"PRODUCT": {"median": 187.0}}}
    qualified = [
        _candidate("Speed Tufting Tool Manual tufting tool DIY carpet gun", 38.0),
    ]

    primary, alternate, unpriced = _build_category_matched_economics(
        qualified,
        market=market,
        target_terms=TARGETS,
        requested_category="PRODUCT",
        country_code="FR",
        rules=_rules(),
    )

    assert primary == []
    assert alternate == []
    assert len(unpriced) == 1
    assert unpriced[0]["supplier_category"] == "IRRELEVANT"
