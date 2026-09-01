from api.app.features.match_review.field_resolution import (
    FieldStatus,
    field_status,
    score_discriminator,
)


def test_present_but_unmapped_value_is_unresolved_not_missing() -> None:
    """`is_4wd = 0` states only 'not 4WD' — FWD and RWD stay indistinguishable."""

    assert field_status("0", None) is FieldStatus.UNRESOLVED
    assert field_status(None, None) is FieldStatus.MISSING
    assert field_status("0", "fwd") is FieldStatus.RESOLVED
    assert field_status("   ", None) is FieldStatus.MISSING


def test_near_constant_field_is_not_a_usable_discriminator() -> None:
    """eu_category is M1 for ~every passenger car, so it separates nothing."""

    score = score_discriminator(
        field="eu_category",
        population=191_921,
        present_count=191_921,
        distinct_count=2,
        top_counts=[191_884, 5],
    )

    assert score.usable is False
    assert score.score == 0.0


def test_balanced_moderate_cardinality_field_outranks_high_cardinality() -> None:
    manufacturer_code = score_discriminator(
        field="fab_code",
        population=191_921,
        present_count=191_921,
        distinct_count=151,
        top_counts=[44_253, 14_957, 14_519],
    )
    free_text_brand = score_discriminator(
        field="brand",
        population=191_921,
        present_count=191_921,
        distinct_count=17_995,
        top_counts=[3_582, 2_270, 2_078],
    )

    assert manufacturer_code.usable and free_text_brand.usable
    assert manufacturer_code.score > free_text_brand.score


def test_sparse_field_is_penalised_by_coverage() -> None:
    sparse = score_discriminator(
        field="variant",
        population=191_921,
        present_count=97_000,
        distinct_count=20,
        top_counts=[10_000, 9_000],
    )
    dense = score_discriminator(
        field="body_code",
        population=191_921,
        present_count=191_921,
        distinct_count=20,
        top_counts=[10_000, 9_000],
    )

    assert dense.score > sparse.score


def test_empty_population_does_not_divide_by_zero() -> None:
    score = score_discriminator(
        field="brand",
        population=0,
        present_count=0,
        distinct_count=0,
        top_counts=[],
    )

    assert score.usable is False
    assert score.coverage == 0.0
