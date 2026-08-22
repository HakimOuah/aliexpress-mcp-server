from __future__ import annotations

from src.offer_classifier import classify_offer, summarize_classified_offers


def test_classify_offer_buckets() -> None:
    terms = ["tufting gun", "pistolet tufting", "machine tufting"]
    assert classify_offer("Tufting gun AK-V", "Shop", terms) == "PRODUCT"
    assert classify_offer("Kit de démarrage tufting gun avec tondeuse", "Shop", terms) == "BUNDLE"
    assert classify_offer("Tondeuse pour tufting gun", "Shop", terms) == "ACCESSORY"
    assert classify_offer("Tufting gun occasion", "Leboncoin", terms) == "USED"
    assert classify_offer("Professional industrial tufting gun", "Shop", terms) == "PROFESSIONAL"
    assert classify_offer("Machine à coudre", "Shop", terms) == "IRRELEVANT"


def test_summary_has_quartiles() -> None:
    offers = [
        {"title": "Tufting gun", "seller": "A", "price": {"current": 100}},
        {"title": "Tufting gun AK", "seller": "B", "price": {"current": 150}},
        {"title": "Tufting gun V", "seller": "C", "price": {"current": 200}},
    ]
    result = summarize_classified_offers(offers, ["tufting gun"])
    pricing = result["pricing_by_category"]["PRODUCT"]
    assert pricing["count"] == 3
    assert pricing["p25"] == 125.0
    assert pricing["median"] == 150
    assert pricing["p75"] == 175.0


def test_summary_deduplicates_same_offer_seen_on_multiple_queries() -> None:
    offers = [
        {
            "title": "AK-V Tufting Gun 2 in 1",
            "seller": "Example Shop",
            "price": {"current": 149.99},
            "product_url": "https://shop.example.com/products/ak-v?utm_source=google",
        },
        {
            "title": "AK-V Tufting Gun 2 in 1",
            "seller": "Example Shop",
            "price": {"current": 149.99},
            "product_url": "https://shop.example.com/products/ak-v?utm_source=other",
        },
        {
            "title": "Tufting Gun Pro",
            "seller": "Other Shop",
            "price": {"current": 199.99},
            "product_url": "https://other.example.com/tufting/pro",
        },
    ]

    result = summarize_classified_offers(offers, ["tufting gun"])
    pricing = result["pricing_by_category"]["PRODUCT"]

    assert result["raw_total_offers"] == 3
    assert result["unique_total_offers"] == 2
    assert result["duplicates_removed"] == 1
    assert pricing["count"] == 2
    assert pricing["median"] == 174.99


def test_fallback_dedupe_keeps_same_title_from_different_merchants() -> None:
    offers = [
        {"title": "Tufting Gun AK-V", "seller": "Shop A", "price": 150},
        {"title": "Tufting Gun AK-V", "seller": "Shop A", "price": 150},
        {"title": "Tufting Gun AK-V", "seller": "Shop B", "price": 150},
    ]

    result = summarize_classified_offers(offers, ["tufting gun"])

    assert result["raw_total_offers"] == 3
    assert result["unique_total_offers"] == 2
    assert result["duplicates_removed"] == 1
    assert result["pricing_by_category"]["PRODUCT"]["count"] == 2
