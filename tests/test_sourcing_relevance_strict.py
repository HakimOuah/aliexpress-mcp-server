from src.sourcing_relevance import build_sourcing_queries, is_relevant_search_item


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def item(title: str) -> dict[str, str]:
    return {"itemId": "1", "title": title}


def test_accepts_real_tufting_devices() -> None:
    assert is_relevant_search_item(item("2 in 1 Rug Tufting Gun Cut Loop Pile"), TARGETS)
    assert is_relevant_search_item(item("Machine à tufting électrique pour tapis"), TARGETS)
    assert is_relevant_search_item(item("Pistolet à touffeter électrique pour fabrication de tapis"), TARGETS)
    assert is_relevant_search_item(item("AK-V Tufting Gun Cut Loop Pile Rug Machine"), TARGETS)


def test_rejects_generic_guns() -> None:
    assert not is_relevant_search_item(item("Pistolet de pulvérisation pneumatique peinture automobile"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet à eau haute pression pour voiture"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet de massage électrique tissus profonds"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet à agrafes pour tapisserie"), TARGETS)


def test_rejects_tufting_accessories_even_with_bad_machine_translation() -> None:
    assert not is_relevant_search_item(
        item("Fil de touffetage acrylique spécial tufting 400g pour tapis"), TARGETS
    )
    assert not is_relevant_search_item(
        item("Pistolet à touffeter spécial 400g, 8 brins de coton, fil de touffetage personnalisé"),
        TARGETS,
    )


def test_query_expansion_adds_discriminating_tufting_models() -> None:
    queries = build_sourcing_queries("tufting gun", TARGETS)
    assert "AK-V tufting gun" in queries
    assert "AK-I tufting gun" in queries
    assert "cut loop pile tufting gun" in queries
    assert "carpet tufting machine" in queries
