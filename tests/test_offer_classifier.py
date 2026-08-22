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
