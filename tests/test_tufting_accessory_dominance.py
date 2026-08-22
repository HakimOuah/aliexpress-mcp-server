from src.offer_classifier import classify_offer


TARGETS = ["tufting gun", "pistolet tufting", "machine tufting"]


def test_cotton_listing_with_tufting_gun_words_is_accessory() -> None:
    assert (
        classify_offer(
            "Tufting gun special eight strands lover cotton 400g large yarn",
            "Supplier",
            TARGETS,
        )
        == "ACCESSORY"
    )


def test_real_electric_tufting_gun_remains_product() -> None:
    assert (
        classify_offer(
            "2 In 1 Electric Tufting Gun Cut Loop Rug Machine",
            "Supplier",
            TARGETS,
        )
        == "PRODUCT"
    )
