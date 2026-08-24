from ingestion.tecdoc.manufacturer_mapping import TecDocManufacturerIndex


def _rule(
    source: str,
    canonical: str,
    **extra: object,
) -> dict[str, object]:
    return {
        "kind": "manufacturer_entity",
        "entity_role": "vehicle_manufacturer",
        "source_term": source,
        "canonical_name": canonical,
        "match_type": "whole_token_prefix",
        **extra,
    }


def test_bridges_reviewed_ts_canonical_name_to_tecdoc_name() -> None:
    index = TecDocManufacturerIndex(
        ("VW", "VOLVO"),
        {"brand:VW": _rule("VW", "Volkswagen")},
    )

    decision = index.resolve(brand="VOLKSWAGEN, VW 3C")

    assert decision.status == "resolved"
    assert decision.manufacturer == "VW"
    assert decision.evidence[0].rule_ids == ("TS-CANONICAL-BRIDGE",)


def test_native_catalog_alias_wins_over_cross_canonical_review_rule() -> None:
    index = TecDocManufacturerIndex(
        ("AUDI", "VW"),
        {
            "brand:VW": _rule("VW", "Volkswagen"),
            "brand:AUDI": _rule("AUDI", "Volkswagen"),
        },
    )

    decision = index.resolve(brand="AUDI B8")

    assert decision.status == "resolved"
    assert decision.manufacturer == "AUDI"
    assert decision.evidence[0].native_catalog_match is True


def test_longest_whole_token_alias_prevents_fordson_from_matching_ford() -> None:
    index = TecDocManufacturerIndex(("FORD", "FORDSON"), {})

    decision = index.resolve(brand="FORDSON DEXTA")

    assert decision.status == "resolved"
    assert decision.manufacturer == "FORDSON"


def test_conflicting_source_fields_do_not_select_a_manufacturer() -> None:
    index = TecDocManufacturerIndex(("ALPINA", "BMW"), {})

    decision = index.resolve(brand="ALPINA", manufacturer="BMW")

    assert decision.status == "conflict"
    assert decision.manufacturer is None
    assert decision.conflicting_manufacturers == ("ALPINA", "BMW")


def test_guarded_rule_is_not_broadened_into_unconditional_alias() -> None:
    index = TecDocManufacturerIndex(
        ("AUDI",),
        {
            "guarded": _rule(
                "QUATTRO",
                "Audi",
                requires_model_manufacturer="Audi",
            )
        },
    )

    assert index.resolve(brand="QUATTRO B8").status == "unmatched"


def test_substring_does_not_match_catalog_manufacturer() -> None:
    index = TecDocManufacturerIndex(("AC",), {})

    assert index.resolve(brand="RACING SPECIAL").status == "unmatched"


def test_reviewed_short_name_rule_bridges_canonical_name_to_catalog_name() -> None:
    """TS canonicalises to "Volkswagen" while the TecDoc catalog registers "VW".

    A reviewed rule stating that the TS brand token "VW" means Volkswagen also
    bridges the canonical name onto the catalog name, so these rows are scoped
    and scored instead of being reviewed as global-scope matches. The bridge is
    reviewed data in the rule set, never a hardcoded equivalence.
    """

    index = TecDocManufacturerIndex(
        ("VW", "VOLVO"),
        {"MFE-BRAND-VW-SHORT-NAME": _rule("VW", "Volkswagen")},
    )

    for brand in ("Volkswagen", "VOLKSWAGEN, VW", "VW"):
        decision = index.resolve(brand=brand)
        assert decision.status == "resolved", brand
        assert decision.manufacturer == "VW", brand


def test_canonical_name_stays_unmatched_without_a_reviewed_bridge_rule() -> None:
    index = TecDocManufacturerIndex(("VW", "VOLVO"), {})

    assert index.resolve(brand="Volkswagen").status == "unmatched"
