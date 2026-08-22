from src.sourcing_relevance import is_relevant_search_item


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def item(title: str) -> dict[str, str]:
    return {"itemId": "1", "title": title}


def test_accepts_real_tufting_devices() -> None:
    assert is_relevant_search_item(item("2 in 1 Rug Tufting Gun Cut Loop Pile"), TARGETS)
    assert is_relevant_search_item(item("Machine à tufting électrique pour tapis"), TARGETS)
    assert is_relevant_search_item(item("Pistolet à touffeter électrique pour fabrication de tapis"), TARGETS)


def test_rejects_generic_guns() -> None:
    assert not is_relevant_search_item(item("Pistolet de pulvérisation pneumatique peinture automobile"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet à eau haute pression pour voiture"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet de massage électrique tissus profonds"), TARGETS)
    assert not is_relevant_search_item(item("Pistolet à agrafes pour tapisserie"), TARGETS)


def test_rejects_tufting_accessory_without_device() -> None:
    assert not is_relevant_search_item(
        item("Fil de touffetage acrylique spécial tufting 400g pour tapis"), TARGETS
    )
