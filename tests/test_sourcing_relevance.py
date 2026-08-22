from __future__ import annotations

from src.sourcing_relevance import build_sourcing_queries, dedupe_and_filter, is_relevant_search_item


def test_tufting_relevance_rejects_generic_guns() -> None:
    terms = ["tufting gun", "pistolet tufting", "machine tufting"]
    assert is_relevant_search_item({"title": "2 in 1 Rug Tufting Gun Carpet Machine"}, terms)
    assert not is_relevant_search_item({"title": "High pressure water gun for car washing"}, terms)
    assert not is_relevant_search_item({"title": "Massage gun with 8 heads"}, terms)


def test_query_expansion_adds_rug_and_carpet_context() -> None:
    queries = build_sourcing_queries("tufting gun", ["machine tufting"])
    assert "tufting gun" in queries
    assert any("carpet" in q for q in queries)
    assert any("rug" in q for q in queries)


def test_dedupe_and_filter_keeps_unique_relevant_ids() -> None:
    items = [
        {"itemId": "1", "title": "Tufting gun for rug making"},
        {"itemId": "1", "title": "Tufting gun duplicate"},
        {"itemId": "2", "title": "Water spray gun"},
    ]
    result = dedupe_and_filter(items, ["tufting gun"])
    assert [x["itemId"] for x in result] == ["1"]
