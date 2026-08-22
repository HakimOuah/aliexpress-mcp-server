from types import SimpleNamespace

from src.candidate_economics import economics_for_candidate
from src.opportunity_sourcing import _select_reference_sku
from src.sourcing_relevance import is_relevant_search_item


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def _sku(
    sku_id: str,
    price: float,
    value: str,
    *,
    stock: int = 10,
) -> dict:
    return {
        "sku_id": sku_id,
        "sku_attr": value,
        "offer_sale_price": str(price),
        "sku_price": str(price),
        "currency_code": "EUR",
        "sku_available_stock": stock,
        "ae_sku_property_dtos": {
            "ae_sku_property_d_t_o": [
                {
                    "sku_property_name": "Variant",
                    "sku_property_value": value,
                    "property_value_definition_name": value,
                }
            ]
        },
    }


def test_reference_sku_ignores_cheaper_accessory_variant() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [
            _sku("1001", 2.0, "5pcs needle threader"),
            _sku("1002", 58.0, "AK-V tufting gun"),
            _sku("1003", 79.0, "AK-V tufting gun with trimmer"),
        ]
    )

    assert chosen is not None
    assert chosen.sku_id == "1002"
    assert chosen.offer_sale_price_eur == 58.0
    assert meta["excluded_accessory_sku_count"] == 1
    assert warnings == []


def test_threader_listing_is_not_relevant_product() -> None:
    assert not is_relevant_search_item(
        {
            "itemId": "1",
            "title": "5Pcs New Tufting Gun Needle Threader Colorful Plastic Handle",
        },
        TARGETS,
    )


def test_good_margin_with_quality_warnings_stays_watch() -> None:
    rules = SimpleNamespace(
        vat_fr=0.20,
        vat_be=0.21,
        vat_ch=0.081,
        vat_lu=0.17,
        min_margin_pct=40.0,
    )
    candidate = {
        "product_id": "1",
        "landed_cost_eur": 60.0,
        "quality_verdict": "WATCH",
        "quality_score": 74,
        "orders": 12,
    }

    result = economics_for_candidate(
        candidate,
        market_price_ttc=187.0,
        country_code="FR",
        rules=rules,
    )

    assert result["unit_economics_verdict"] == "GO"
    assert result["supplier_verdict"] == "WATCH"
    assert result["economics_verdict"] == "WATCH"
