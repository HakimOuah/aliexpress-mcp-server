from __future__ import annotations

from src.market_analysis import analyze_serps


def test_analyze_serps_aggregates_domains_ads_shopping_and_prices() -> None:
    serps = [
        {
            "se_results_count": 1000,
            "cost": 0.002,
            "items": [
                {"type": "organic", "domain": "competitor.fr"},
                {"type": "organic", "domain": "amazon.fr"},
                {"type": "paid", "domain": "competitor.fr"},
                {
                    "type": "shopping",
                    "items": [
                        {
                            "type": "shopping_element",
                            "domain": "shop-a.fr",
                            "seller": "Shop A",
                            "price": 79.0,
                        },
                        {
                            "type": "shopping_element",
                            "domain": "amazon.fr",
                            "seller": "Amazon.fr - Seller",
                            "price": 129.0,
                        },
                    ],
                },
            ],
        },
        {
            "se_results_count": 2000,
            "cost": 0.002,
            "items": [
                {"type": "organic", "domain": "competitor.fr"},
                {"type": "paid", "domain": "ad-shop.fr"},
                {
                    "type": "popular_products",
                    "items": [
                        {
                            "type": "popular_products_element",
                            "seller": "Cdiscount",
                            "price": {"current": 199.0, "currency": "EUR"},
                        }
                    ],
                },
            ],
        },
    ]

    result = analyze_serps(serps)

    assert result["queries_analyzed"] == 2
    assert result["paid_presence"] is True
    assert result["shopping_presence"] is True
    assert result["marketplace_presence"] is True
    assert result["top_organic_domains"][0] == {
        "domain": "competitor.fr",
        "appearances": 2,
    }
    assert result["unique_shopping_merchants"] == 3
    assert result["shopping_merchants"][:3] == [
        {"merchant": "Shop A", "appearances": 1},
        {"merchant": "Amazon.fr - Seller", "appearances": 1},
        {"merchant": "Cdiscount", "appearances": 1},
    ]
    assert {m["merchant"] for m in result["marketplace_merchants"]} == {
        "Amazon.fr - Seller",
        "Cdiscount",
    }
    assert result["shopping_price_eur"] == {
        "count": 3,
        "min": 79.0,
        "median": 129.0,
        "max": 199.0,
    }
    assert result["dataforseo_cost"] == 0.004
