from types import SimpleNamespace

from src.opportunity_sourcing import _select_reference_sku
from src.product_factory_server import _build_category_matched_economics


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def _sku(sku_id: str, price: float, value: str, *, stock: int = 10, image: str | None = None) -> dict:
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
                    "sku_property_name": "Couleur",
                    "sku_property_value": value,
                    "property_value_definition_name": value,
                    "sku_image": image,
                }
            ]
        },
    }


def _rules():
    return SimpleNamespace(
        vat_fr=0.20,
        vat_be=0.21,
        vat_ch=0.081,
        vat_lu=0.17,
        min_margin_pct=40.0,
    )


def test_bundle_selection_rejects_cheaper_trimmer_only_sku() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [
            _sku("1", 30.19, "EU 220V Trimmer"),
            _sku("2", 134.39, "EU 220V with Trimmer", image="https://img/bundle.jpg"),
            _sku("3", 152.69, "EU 220V with Trimmer"),
        ],
        expected_category="BUNDLE",
    )

    assert chosen is not None
    assert chosen.sku_id == "2"
    assert chosen.offer_sale_price_eur == 134.39
    assert meta["bundle_configuration_status"] == "VERIFIED"
    assert meta["selected_sku_semantics"] == "BUNDLE"
    assert meta["selected_sku_image_url"] == "https://img/bundle.jpg"
    assert warnings == []


def test_opaque_set_is_not_treated_as_verified_bundle() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [
            _sku("1", 85.69, "SET D"),
            _sku("2", 110.99, "SET A"),
        ],
        expected_category="BUNDLE",
    )

    assert chosen is not None
    assert chosen.sku_id == "1"
    assert meta["bundle_configuration_status"] == "OPAQUE"
    assert meta["selected_sku_semantics"] == "OPAQUE"
    assert "bundle_sku_content_unverified" in warnings


def test_bundle_title_with_product_only_sku_is_marked_mismatch() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [_sku("1", 76.25, "2in1 tufting gun")],
        expected_category="BUNDLE",
    )

    assert chosen is not None
    assert meta["bundle_configuration_status"] == "MISMATCH"
    assert meta["selected_sku_semantics"] == "PRODUCT"
    assert "bundle_sku_category_mismatch" in warnings


def test_product_only_sku_refines_bundle_listing_to_product_economics() -> None:
    market = {
        "pricing_by_category": {
            "PRODUCT": {"median": 199.0},
            "BUNDLE": {"median": 208.84},
        }
    }
    candidate = {
        "product_id": "1",
        "title": "Pistolet à touffeter 2 en 1, ensemble de poils de coupe et de boucle",
        "listing_category": "BUNDLE",
        "store": "Supplier",
        "landed_cost_eur": 76.25,
        "orders": 1,
        "quality_verdict": "WATCH",
        "quality_score": 60,
        "sku_selection": {
            "bundle_configuration_status": "MISMATCH",
            "selected_sku_semantics": "PRODUCT",
        },
    }

    primary, alternate, unpriced = _build_category_matched_economics(
        [candidate],
        market=market,
        target_terms=TARGETS,
        requested_category="PRODUCT",
        country_code="FR",
        rules=_rules(),
    )

    assert len(primary) == 1
    assert primary[0]["supplier_category"] == "PRODUCT"
    assert primary[0]["market_price_ttc_eur"] == 199.0
    assert primary[0]["category_refinement"] == "BUNDLE_TITLE_TO_PRODUCT_SKU"
    assert alternate == []
    assert unpriced == []


def test_opaque_bundle_is_returned_unpriced_for_review() -> None:
    market = {"pricing_by_category": {"BUNDLE": {"median": 208.84}}}
    candidate = {
        "product_id": "1",
        "title": "Electric Tufting Gun Set",
        "listing_category": "BUNDLE",
        "store": "Supplier",
        "landed_cost_eur": 85.69,
        "quality_verdict": "WATCH",
        "quality_score": 60,
        "sku_image_url": "https://img/set-d.jpg",
        "sku_selection": {
            "bundle_configuration_status": "OPAQUE",
            "selected_sku_semantics": "OPAQUE",
        },
    }

    primary, alternate, unpriced = _build_category_matched_economics(
        [candidate],
        market=market,
        target_terms=TARGETS,
        requested_category="PRODUCT",
        country_code="FR",
        rules=_rules(),
    )

    assert primary == []
    assert alternate == []
    assert len(unpriced) == 1
    assert unpriced[0]["comparison_status"] == "SKU_CONTENT_UNVERIFIED"
