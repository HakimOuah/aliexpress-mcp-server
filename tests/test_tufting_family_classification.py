from src.offer_classifier import classify_offer


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def test_french_tufting_machine_is_product() -> None:
    assert classify_offer(
        "Machine à touffeter électrique AK-V 120W",
        "Supplier",
        TARGETS,
    ) == "PRODUCT"


def test_french_brushless_tufting_gun_is_product() -> None:
    assert classify_offer(
        "AK-V pistolet à touffeter sans brosse 2 en 1",
        "Supplier",
        TARGETS,
    ) == "PRODUCT"


def test_tufting_starter_kit_is_bundle() -> None:
    assert classify_offer(
        "Kit démarrage pistolet à touffeter AK-V 120W 1000-6000 tr/min",
        "Supplier",
        TARGETS,
    ) == "BUNDLE"


def test_tufting_set_is_bundle() -> None:
    assert classify_offer(
        "Ensemble de pistolets à touffeter électrique 2 en 1",
        "Supplier",
        TARGETS,
    ) == "BUNDLE"


def test_tufting_cotton_stays_accessory() -> None:
    assert classify_offer(
        "Tufting gun special eight strands lover cotton 400g large yarn",
        "Supplier",
        TARGETS,
    ) == "ACCESSORY"


def test_manual_tufting_tool_stays_irrelevant_for_powered_target() -> None:
    assert classify_offer(
        "Speed Tufting Tool Manual tufting tool DIY carpet gun",
        "Supplier",
        TARGETS,
    ) == "IRRELEVANT"
