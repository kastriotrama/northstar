from typing import Any

from api.app.features.match_review.field_resolution import suggest_value_patterns
from api.app.features.match_review.rule_advisor import PatternRuleAdvisor

# Real shapes observed in the production population is_4wd=0.
MERCEDES_VALUES = [
    ("MERCEDES-BENZ", 3582),
    ("MERCEDES-BENZ 204 K", 493),
    ("MERCEDES-BENZ 204", 433),
    ("MERCEDES-BENZ 245 G", 424),
    ("MERCEDES-BENZ 212 K", 396),
]


def test_prefix_discovery_isolates_chassis_code_blocks() -> None:
    patterns = suggest_value_patterns(MERCEDES_VALUES, population=6550)
    prefixes = {pattern.prefix for pattern in patterns}

    assert "MERCEDES-BENZ 204" in prefixes
    chassis = next(p for p in patterns if p.prefix == "MERCEDES-BENZ 204")
    assert chassis.row_count == 926  # 493 + 433
    assert chassis.distinct_values == 2


def test_prefix_covering_everything_scores_zero() -> None:
    """A prefix matching the whole population divides it no better than nothing."""

    patterns = suggest_value_patterns(
        [("VOLVO 240", 10), ("VOLVO 940", 10)], population=20
    )

    assert all(pattern.prefix != "VOLVO" or pattern.score == 0 for pattern in patterns)
    assert "VOLVO" not in {p.prefix for p in patterns}


def test_single_token_values_yield_no_patterns() -> None:
    assert suggest_value_patterns([("VO", 100), ("SA", 50)], population=150) == []


def test_empty_population_is_safe() -> None:
    assert suggest_value_patterns(MERCEDES_VALUES, population=0) == []


def _discriminator(field: str, score: float, top: str, count: int) -> dict[str, Any]:
    return {
        "field": field,
        "usable": True,
        "score": score,
        "top_values": [{"value": top, "count": count}],
    }


def test_advisor_prefers_semantically_relevant_field_over_top_score() -> None:
    """Year splits the population best, but drive type is a property of the model."""

    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=191_921,
        discriminators=[
            _discriminator("vehicle_year", 0.31, "2006", 10_663),
            _discriminator("fab_code", 0.24, "VO", 44_253),
        ],
        field_values={},
        oem_samples=[],
    )

    assert [c.field for c in advice.conditions] == ["is_4wd", "fab_code"]
    assert advice.evidence["chosen_by"] == "domain prior"


def test_advisor_withholds_a_value_without_evidence() -> None:
    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=191_921,
        discriminators=[_discriminator("fab_code", 0.24, "VO", 44_253)],
        field_values={},
        oem_samples=[],
    )

    assert advice.confident is False
    assert advice.target_value is None
    assert "fact about cars" in advice.reasoning


def test_advisor_asserts_a_value_when_oem_samples_agree() -> None:
    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=1_000,
        discriminators=[_discriminator("fab_code", 0.24, "VO", 800)],
        field_values={},
        oem_samples=[{"drive": "FWD"}, {"drive": "fwd"}],
    )

    assert advice.confident is True
    assert advice.target_value == "fwd"


def test_advisor_stays_unconfident_when_oem_samples_disagree() -> None:
    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=1_000,
        discriminators=[_discriminator("fab_code", 0.24, "VO", 800)],
        field_values={},
        oem_samples=[{"drive": "FWD"}, {"drive": "RWD"}],
    )

    assert advice.confident is False
    assert advice.target_value is None


def test_advisor_prefers_a_prefix_pattern_when_one_exists() -> None:
    """A prefix beats an exact value because it groups spellings together.

    The advisor takes the highest-scoring prefix, which is the make-level one;
    the narrower chassis-level prefixes remain available in the pattern list
    for the reviewer to pick.
    """

    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=191_921,
        discriminators=[_discriminator("brand", 0.19, "MERCEDES-BENZ", 3_582)],
        field_values={"brand": MERCEDES_VALUES},
        oem_samples=[],
    )

    narrowing = advice.conditions[1]
    assert narrowing.operator == "starts_with"
    assert narrowing.values[0].startswith("MERCEDES-BENZ")
    assert advice.evidence["pattern"]["distinct_values"] >= 2


def test_advisor_reports_when_nothing_separates_the_population() -> None:
    advice = PatternRuleAdvisor().advise(
        source_field="is_4wd",
        source_value="0",
        target_field="drive_type",
        population=500,
        discriminators=[{"field": "eu_category", "usable": False, "score": 0.0}],
        field_values={},
        oem_samples=[],
    )

    assert advice.confident is False
    assert len(advice.conditions) == 1
    assert "No field separates" in advice.reasoning
