"""How a rule family's keys become SQL, and which bar each family learns at."""

from ingestion.vehicle_core_rules import (
    DEFAULT_MIN_AGREEMENT,
    DEFAULT_MIN_SUPPORT,
    FAMILIES_BY_ID,
    RULE_FAMILIES,
    key_sql,
    learn_statement,
)


def test_a_column_key_is_the_column_and_a_computed_key_its_expression() -> None:
    assert key_sql("variant_code") == "variant_code"
    assert key_sql("variant_code", "v") == "v.variant_code"
    assert key_sql("vin_descriptor") == "(CASE WHEN length(vin) = 17 THEN left(vin, 8) END)"
    assert key_sql("vin_descriptor", "v") == (
        "(CASE WHEN length(v.vin) = 17 THEN left(v.vin, 8) END)"
    )


def test_the_model_year_key_is_the_tenth_vin_character_of_a_full_vin() -> None:
    assert key_sql("vin_year", "v") == "(CASE WHEN length(v.vin) = 17 THEN substr(v.vin, 10, 1) END)"


def test_learning_groups_and_filters_on_the_computed_key() -> None:
    statement = learn_statement(FAMILIES_BY_ID["MOD-VIN"])

    assert "(CASE WHEN length(vin) = 17 THEN left(vin, 8) END)::text AS k1" in statement
    assert "(CASE WHEN length(vin) = 17 THEN left(vin, 8) END) IS NOT NULL" in statement
    assert "vin_descriptor" not in statement


def test_model_families_learn_from_ts_at_a_higher_bar_most_specific_first() -> None:
    model = [family for family in RULE_FAMILIES if family.target_field == "model_family"]

    assert [family.family for family in model] == [
        "MOD-VV", "MOD-VIN", "MOD-VINL", "MOD-VINY", "MOD-TP", "MOD-VAR", "MOD-BR", "MOD-BT", "MOD-MT", "MOD-BRT",
        "MOD-PAT",
    ]
    learned, patterns = model[:-3], model[-1]
    assert all(family.learned_from == "transportstyrelsen" for family in learned)
    assert all(family.purpose == "enrichment" for family in model)
    assert all((family.min_support, family.min_agreement) == (10, 0.98) for family in learned)
    assert all(family.learner == "statistics" for family in learned)
    # Patterns propose; the known vehicles under each key can overrule them.
    assert (patterns.learner, patterns.learned_from) == ("patterns", "reviewed-pattern")
    assert (patterns.min_support, patterns.min_agreement) == (1, 0.98)
    text = FAMILIES_BY_ID["MOD-MT"]
    assert (text.learner, text.learned_from, text.key_fields) == (
        "patterns", "reviewed-pattern", ("manufacturer", "model_text")
    )
    engine = FAMILIES_BY_ID["ENG-VV"]
    assert (engine.min_support, engine.min_agreement) == (DEFAULT_MIN_SUPPORT, DEFAULT_MIN_AGREEMENT)


def test_the_model_text_key_is_the_trimmed_upper_case_text_or_nothing() -> None:
    assert key_sql("model_text", "v") == (
        "(CASE WHEN btrim(v.registry_model_text) <> '' THEN upper(btrim(v.registry_model_text)) END)"
    )


def test_the_manufacturer_is_learned_from_the_brand_texts_first_word() -> None:
    family = FAMILIES_BY_ID["MFR-BW"]
    word = key_sql("brand_make_word", "v")

    assert (family.target_field, family.key_fields, family.learned_from) == (
        "manufacturer", ("brand_make_word",), "transportstyrelsen"
    )
    assert (family.min_support, family.min_agreement) == (10, 0.98)
    assert "substring(upper(btrim(v.registry_brand_text)) from '^[^ ,&/+]+')" in word
    assert FAMILIES_BY_ID["MOD-BRT"].key_fields == ("manufacturer", "brand_text")
    # The make comes first: every model family is keyed on it or on its code.
    order = [family.family for family in RULE_FAMILIES]
    assert order.index("MFR-BW") < min(i for i, name in enumerate(order) if name.startswith("MOD-"))


def test_brand_keys_ignore_text_that_names_only_the_make() -> None:
    token = key_sql("brand_token", "v")

    assert "strpos(upper(btrim(v.registry_brand_text)), ' ') > 0" in token
    assert "<> split_part(upper(btrim(v.registry_brand_text)), ' ', 1)" in token
    assert "strpos" in key_sql("brand_text")
    # A `%` would collide with the statement's own parameters.
    assert "%" not in learn_statement(FAMILIES_BY_ID["MOD-BT"]).replace("%s", "")


def test_a_reviewed_era_follows_its_rule_by_content() -> None:
    from ingestion.vehicle_core_rules import (
        REVIEWED_ERAS_BY_RULE_ID,
        REVIEWED_RULE_ERAS,
        rule_id_for,
    )

    # The ids the local copy of live holds for these rules (2026-10-01): the era is
    # keyed by the content the id is hashed from, so it reaches the same rule.
    assert {rule_id_for(*content) for content in REVIEWED_RULE_ERAS} == set(REVIEWED_ERAS_BY_RULE_ID) == {
        "MOD-BR-8bbb3f8aa1ac58dd",  # MERCEDES-BENZ 230 -> SL
        "MOD-BR-fc8c68605ba8a940",  # MERCEDES BENZ 230 -> SL, the sister key without the hyphen
        "MOD-BT-67cc9912dedd1248",  # AUDI S4 -> A4
        "MOD-BT-2aa9eae9c0a0585f",  # CITROEN B -> C4
    }


def test_a_fill_outside_its_rules_reviewed_era_is_refused() -> None:
    from ingestion.vehicle_core_rules import outside_reviewed_era

    for mb_230 in ("MOD-BR-8bbb3f8aa1ac58dd", "MOD-BR-fc8c68605ba8a940"):
        # The R230 SL is an SL; every 230 before it is judged a saloon, the 1963-67
        # years too (mostly W110/W111 saloons). The start of a range is inside it.
        assert not outside_reviewed_era(mb_230, 2001)
        assert not outside_reviewed_era(mb_230, 2005)
        assert outside_reviewed_era(mb_230, 1966)
        assert outside_reviewed_era(mb_230, 1978)
        assert outside_reviewed_era(mb_230, 2000)
        assert outside_reviewed_era(mb_230, 1939)
    # The S4 of 1992-94 is an Audi 100; the 1998 one an A4.
    assert outside_reviewed_era("MOD-BT-67cc9912dedd1248", 1993)
    assert not outside_reviewed_era("MOD-BT-67cc9912dedd1248", 1998)
    # No build year, or a rule without a reviewed era, is not judged.
    assert not outside_reviewed_era("MOD-BR-8bbb3f8aa1ac58dd", None)
    assert not outside_reviewed_era("MOD-BT-0000000000000000", 1900)


def test_a_familys_reviewed_era_holds_for_every_rule_that_answers_with_it() -> None:
    from ingestion.vehicle_core_rules import outside_family_era

    # The 1950s Saab 93, inclusive at both ends; "SAAB 93 AERO" of 2001 is a 9-3.
    assert not outside_family_era("Saab", "93", 1955)
    assert not outside_family_era("Saab", "93", 1960)
    assert outside_family_era("Saab", "93", 1961)
    assert outside_family_era("Saab", "93", 2001)
    # The 4CV before 1961, the Renault 4 after; the Clio from 1990.
    assert outside_family_era("Renault", "4", 1958)
    assert not outside_family_era("Renault", "4", 1961)
    assert outside_family_era("Renault", "Clio", 1989)
    # "MERCEDES B 170 S" of 1950 and "M B 250 CE" of 1972 are no B-Class.
    assert outside_family_era("Mercedes-Benz", "B-Class", 1972)
    assert not outside_family_era("Mercedes-Benz", "B-Class", 2005)
    assert outside_family_era("Mercedes-Benz", "A-Class", 1951)
    # The A4 Cabriolet of 2002 is not Audi's 1991-2000 Cabriolet.
    assert outside_family_era("Audi", "Cabriolet", 2002)
    assert not outside_family_era("Audi", "Cabriolet", 2000)
    # The Fiat Coupé of 1993-2000, not the 130 Coupé of 1971.
    assert outside_family_era("Fiat", "Coupe", 1971)
    assert not outside_family_era("Fiat", "Coupe", 1998)
    # The make matters; no year, no make or no reviewed era is not judged.
    assert not outside_family_era("Fiat", "4", 1958)
    assert not outside_family_era("Saab", "93", None)
    assert not outside_family_era(None, "93", 2001)
    assert not outside_family_era("Saab", "9-3", 1950)


def test_the_learned_era_is_reported_before_the_reviewed_one() -> None:
    from ingestion.vehicle_core_rules import _era_refusal

    mb_230 = "MOD-BR-8bbb3f8aa1ac58dd"
    assert _era_refusal({mb_230: (2001, 2015)}, mb_230, 1978) == "outside_learned_era"
    assert _era_refusal({}, mb_230, 1978) == "outside_reviewed_era"
    assert _era_refusal({}, mb_230, 2005) is None
    # A family's era reaches any rule, under any key a re-learn gives it.
    any_rule = "MOD-PAT-e3b4ed79da40404b"
    assert _era_refusal({}, any_rule, 2001, "Saab", "93") == "outside_reviewed_era"
    assert _era_refusal({}, any_rule, 1957, "Saab", "93") is None
    assert _era_refusal({any_rule: (1995, 2005)}, any_rule, 1957, "Saab", "93") == "outside_learned_era"


def test_a_reading_tolerates_the_disagreements_the_learner_does() -> None:
    from ingestion.vehicle_core_rules import tolerated_disagreements

    # One stray car, or 2% of many.
    assert tolerated_disagreements(0, 0.98) == 1
    assert tolerated_disagreements(60, 0.98) == 1
    assert tolerated_disagreements(500, 0.98) == 10


def test_the_vin_key_families_are_the_ones_keyed_on_the_descriptor() -> None:
    from ingestion.vehicle_core_rules import VIN_KEY_FAMILIES

    assert all("vin_descriptor" in FAMILIES_BY_ID[name].key_fields for name in VIN_KEY_FAMILIES)
    assert {
        family.family
        for family in RULE_FAMILIES
        if "vin_descriptor" in family.key_fields and family.target_field == "model_family"
    } == set(VIN_KEY_FAMILIES)


def test_the_vocabulary_is_ts_spelling_plus_reviewed_names_without_non_families() -> None:
    from ingestion.vehicle_core_rules import vocabulary_of

    stated = [("Renault", "Clio", 49579), ("Renault", "B", 5), ("Renault", "Mégane", 3), ("Renault", "Megane", 900)]
    vocabulary = vocabulary_of(stated)

    # Renault "B" is the Clio II's body code TS misread on five cars.
    assert "B" not in vocabulary["Renault"] and {"Clio", "Megane", "Scenic"} <= vocabulary["Renault"]
    assert "Mégane" not in vocabulary["Renault"]
    # Only what TS states, for the guard's question whether TS uses a name at all.
    assert vocabulary_of(stated, reviewed=False) == {"Renault": {"Clio", "Megane"}}
