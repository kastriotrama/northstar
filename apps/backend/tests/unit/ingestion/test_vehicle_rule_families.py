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


def test_learning_groups_and_filters_on_the_computed_key() -> None:
    statement = learn_statement(FAMILIES_BY_ID["MOD-VIN"])

    assert "(CASE WHEN length(vin) = 17 THEN left(vin, 8) END)::text AS k1" in statement
    assert "(CASE WHEN length(vin) = 17 THEN left(vin, 8) END) IS NOT NULL" in statement
    assert "vin_descriptor" not in statement


def test_model_families_learn_from_ts_at_a_higher_bar_most_specific_first() -> None:
    model = [family for family in RULE_FAMILIES if family.target_field == "model_family"]

    assert [family.family for family in model] == [
        "MOD-VV", "MOD-VIN", "MOD-TP", "MOD-VAR", "MOD-BR", "MOD-BT", "MOD-PAT"
    ]
    learned, patterns = model[:-1], model[-1]
    assert all(family.learned_from == "transportstyrelsen" for family in learned)
    assert all(family.purpose == "enrichment" for family in model)
    assert all((family.min_support, family.min_agreement) == (10, 0.98) for family in learned)
    assert all(family.learner == "statistics" for family in learned)
    # Patterns propose; the known vehicles under each key can overrule them.
    assert (patterns.learner, patterns.learned_from) == ("patterns", "reviewed-pattern")
    assert (patterns.min_support, patterns.min_agreement) == (1, 0.98)
    engine = FAMILIES_BY_ID["ENG-VV"]
    assert (engine.min_support, engine.min_agreement) == (DEFAULT_MIN_SUPPORT, DEFAULT_MIN_AGREEMENT)


def test_brand_keys_ignore_text_that_names_only_the_make() -> None:
    token = key_sql("brand_token", "v")

    assert "strpos(upper(btrim(v.registry_brand_text)), ' ') > 0" in token
    assert "<> split_part(upper(btrim(v.registry_brand_text)), ' ', 1)" in token
    assert "strpos" in key_sql("brand_text")
    # A `%` would collide with the statement's own parameters.
    assert "%" not in learn_statement(FAMILIES_BY_ID["MOD-BT"]).replace("%s", "")
