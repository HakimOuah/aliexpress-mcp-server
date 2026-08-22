from src.opportunity_sourcing import _select_reference_sku


def _sku(sku_id: str, price: float, value: str, image: str | None = None) -> dict:
    return {
        "sku_id": sku_id,
        "offer_sale_price": str(price),
        "sku_price": str(price),
        "currency_code": "EUR",
        "sku_available_stock": 10,
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


def test_live_bundle1_prefers_with_trimmer_over_trimmer_only() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [
            _sku("cheap", 30.19, "EU 220V Trimmer"),
            _sku("bundle", 134.39, "EU 220V with Trimmer", "https://img/bundle.jpg"),
        ],
        expected_category="BUNDLE",
    )
    assert chosen is not None
    assert chosen.sku_id == "bundle"
    assert meta["bundle_configuration_status"] == "VERIFIED"
    assert meta["selected_sku_image_url"] == "https://img/bundle.jpg"
    assert warnings == []


def test_live_bundle2_no_number_is_opaque() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [_sku("no2", 53.39, "NO.2")],
        expected_category="BUNDLE",
    )
    assert chosen is not None
    assert meta["bundle_configuration_status"] == "OPAQUE"
    assert "bundle_sku_content_unverified" in warnings


def test_live_bundle3_gun_only_is_category_mismatch() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [_sku("gun", 76.25, "2in1 tufting gun")],
        expected_category="BUNDLE",
    )
    assert chosen is not None
    assert meta["bundle_configuration_status"] == "MISMATCH"
    assert meta["selected_sku_semantics"] == "PRODUCT"
    assert "bundle_sku_category_mismatch" in warnings


def test_live_bundle4_set_letter_is_opaque() -> None:
    chosen, meta, warnings = _select_reference_sku(
        [_sku("setd", 85.69, "SET D", "https://img/set-d.jpg")],
        expected_category="BUNDLE",
    )
    assert chosen is not None
    assert meta["bundle_configuration_status"] == "OPAQUE"
    assert meta["selected_sku_image_url"] == "https://img/set-d.jpg"
    assert "bundle_sku_content_unverified" in warnings
