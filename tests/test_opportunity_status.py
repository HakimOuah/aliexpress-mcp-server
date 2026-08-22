from src.product_factory_server import _build_sku_review_queue, _resolve_requested_category_status


def test_no_go_supplier_produces_no_go_not_watch() -> None:
    ranked = [
        {
            "supplier_verdict": "NO_GO",
            "economics_verdict": "NO_GO",
            "title": "Supplier",
        }
    ]

    assert (
        _resolve_requested_category_status(
            ranked,
            [],
            requested_category="PRODUCT",
            discovery_mode="google_serp_aliexpress_ids",
        )
        == "NO_GO"
    )


def test_watch_supplier_produces_watch() -> None:
    ranked = [{"supplier_verdict": "WATCH"}]
    assert (
        _resolve_requested_category_status(
            ranked,
            [],
            requested_category="PRODUCT",
            discovery_mode="google_serp_aliexpress_ids",
        )
        == "WATCH"
    )


def test_go_supplier_produces_go() -> None:
    ranked = [{"supplier_verdict": "GO"}]
    assert (
        _resolve_requested_category_status(
            ranked,
            [],
            requested_category="PRODUCT",
            discovery_mode="google_serp_aliexpress_ids",
        )
        == "GO"
    )


def test_requested_opaque_bundle_is_inconclusive() -> None:
    unpriced = [
        {
            "supplier_category": "BUNDLE",
            "comparison_status": "SKU_CONTENT_UNVERIFIED",
        }
    ]
    assert (
        _resolve_requested_category_status(
            [],
            unpriced,
            requested_category="BUNDLE",
            discovery_mode="google_serp_aliexpress_ids",
        )
        == "SOURCING_INCONCLUSIVE"
    )


def test_alternate_opaque_bundle_does_not_make_product_inconclusive() -> None:
    unpriced = [
        {
            "supplier_category": "BUNDLE",
            "comparison_status": "SKU_CONTENT_UNVERIFIED",
        }
    ]
    assert (
        _resolve_requested_category_status(
            [],
            unpriced,
            requested_category="PRODUCT",
            discovery_mode="google_serp_aliexpress_ids",
        )
        == "NO_QUALIFYING_SUPPLIER"
    )


def test_review_queue_exposes_selected_image_and_reason() -> None:
    rows = [
        {
            "product_id": "123",
            "title": "Opaque set",
            "product_url": "https://www.aliexpress.com/item/123.html",
            "listing_category": "BUNDLE",
            "comparison_status": "SKU_CONTENT_UNVERIFIED",
            "comparison_reason": "inspect image",
            "sku_id": "456",
            "sku_image_url": "https://img/set.jpg",
            "sku_selection": {
                "selected_sku_properties": {"Couleur": "SET D"},
                "selected_sku_semantics": "OPAQUE",
                "bundle_configuration_status": "OPAQUE",
            },
        }
    ]

    queue = _build_sku_review_queue(rows)
    assert len(queue) == 1
    assert queue[0]["product_id"] == "123"
    assert queue[0]["sku_image_url"] == "https://img/set.jpg"
    assert queue[0]["review_reason"] == "inspect image"
