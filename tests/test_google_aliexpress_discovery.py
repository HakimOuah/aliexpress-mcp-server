from src.google_aliexpress_discovery import (
    discover_from_serps,
    extract_aliexpress_product_id,
    google_discovery_queries,
)


def test_extracts_item_ids_from_aliexpress_urls() -> None:
    assert extract_aliexpress_product_id(
        "https://www.aliexpress.com/item/1005001234567890.html"
    ) == "1005001234567890"
    assert extract_aliexpress_product_id(
        "https://fr.aliexpress.com/item/1005009876543210.html?spm=x"
    ) == "1005009876543210"
    assert extract_aliexpress_product_id("https://amazon.fr/foo") is None


def test_discovers_nested_aliexpress_results_and_deduplicates() -> None:
    serps = [
        {
            "keyword": "tufting gun",
            "items": [
                {
                    "type": "organic",
                    "title": "AK-V Tufting Gun",
                    "url": "https://fr.aliexpress.com/item/1005001234567890.html",
                },
                {
                    "type": "popular_products",
                    "items": [
                        {
                            "type": "popular_products_element",
                            "title": "AK-V Tufting Gun",
                            "url": "https://www.aliexpress.com/item/1005001234567890.html?x=1",
                        },
                        {
                            "type": "popular_products_element",
                            "title": "Other",
                            "url": "https://www.aliexpress.com/item/1005001111111111.html",
                        },
                    ],
                },
            ],
        }
    ]
    rows = discover_from_serps(serps)
    assert {row["itemId"] for row in rows} == {
        "1005001234567890",
        "1005001111111111",
    }


def test_tufting_google_queries_include_model_specific_searches() -> None:
    queries = google_discovery_queries(["tufting gun", "machine tufting"])
    assert 'site:aliexpress.com/item "AK-V tufting gun"' in queries
    assert 'site:aliexpress.com/item "cut loop pile tufting gun"' in queries
