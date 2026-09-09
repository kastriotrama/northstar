"""The value-level TecDoc gap, and what a reviewer is allowed to rule on.

The safety property here is the same one `generate_rules` enforces one layer
down: a mixed fuel descriptor names a capability, not a fuel, and no one-click
Resolve may quietly turn it into a single canonical target. `blocked_reason` is
the only thing standing between the gap browser and that mistake, so it is
pinned against the reviewed mixed table rather than against a copied list.
"""

from __future__ import annotations

import pytest

from api.app.features.tecdoc_review.gaps import (
    GAP_VALUE_SPECS,
    RESOLVABLE_FIELDS,
    blocked_reason,
)
from ingestion.tecdoc import reference_data
from ingestion.tecdoc.canonical_rule_proposals import comparison_key
from ingestion.tecdoc.canonical_vocabulary import canonical_values


class TestMixedDescriptorsCannotBeResolved:
    @pytest.mark.parametrize(
        "label", sorted(reference_data.reviewed_mixed_engine_fuel_labels())
    )
    def test_every_reviewed_mixed_label_is_blocked(self, label: str) -> None:
        assert blocked_reason("energy_sources", label) == "mixed_descriptor"

    @pytest.mark.parametrize(
        "label", sorted(reference_data.reviewed_mixed_engine_fuel_labels())
    )
    def test_the_block_agrees_with_the_pipeline(self, label: str) -> None:
        """`engine_fuel_evidence` refuses a scalar fuel for exactly these labels.

        Pinning one to the other means adding a label to the mixed table cannot
        leave the gap browser willing to resolve something the pipeline will not.
        """

        evidence = reference_data.engine_fuel_evidence("code", {"code": label})

        assert evidence.representation == "mixed"
        assert evidence.scalar_fuel_type is None
        assert blocked_reason("energy_sources", label) is not None

    def test_the_block_ignores_spelling_and_punctuation(self) -> None:
        """A reviewer's spelling must not be a way around the block."""

        assert blocked_reason("energy_sources", "petrol/electric") == "mixed_descriptor"
        assert blocked_reason("energy_sources", "PETROL / ELECTRIC") == "mixed_descriptor"

    def test_a_scalar_fuel_label_is_not_blocked(self) -> None:
        assert blocked_reason("energy_sources", "Diesel") is None

    def test_an_unknown_fuel_value_is_not_blocked(self) -> None:
        """An unmapped value is the ordinary thing a reviewer is here to rule on."""

        assert blocked_reason("energy_sources", "Hydrogen Fuel Cell") is None

    @pytest.mark.parametrize("field", ["bodywork_form", "drive_type"])
    def test_only_fuel_has_mixed_descriptors(self, field: str) -> None:
        assert blocked_reason(field, "Petrol/Electric") is None


class TestResolvableFields:
    def test_only_fields_with_a_closed_vocabulary_are_resolvable(self) -> None:
        for field in RESOLVABLE_FIELDS:
            assert canonical_values(field), f"{field} has no canonical values to resolve toward"

    def test_open_vocabulary_fields_are_not_resolvable(self) -> None:
        """There is no canonical list of model names to resolve a value toward."""

        assert "model_family" not in RESOLVABLE_FIELDS
        assert "manufacturer" not in RESOLVABLE_FIELDS

    def test_transmission_is_not_resolvable_while_it_has_no_canonical_dict(self) -> None:
        """Documents a real gap: KT085 labels are observed but have no mapping.

        `canonical_rule_proposals` scans `transmission_type` and reports 0%
        coverage on it. Until a reviewed KT085 mapping exists in `reference_data`,
        there is nothing to resolve toward, and offering the button would be
        offering an empty target list. This test fails when that mapping lands,
        as the prompt to make the field resolvable here too.
        """

        assert "transmission_type" not in RESOLVABLE_FIELDS
        assert not reference_data.reviewed_mapping_values().get("transmission_type")

    def test_every_resolvable_field_has_a_gap_spec(self) -> None:
        assert set(RESOLVABLE_FIELDS) == set(GAP_VALUE_SPECS)


class TestGapValueSpecs:
    @pytest.mark.parametrize("field", sorted(GAP_VALUE_SPECS))
    def test_the_spec_names_the_field_it_is_keyed_under(self, field: str) -> None:
        assert GAP_VALUE_SPECS[field].canonical_field == field

    @pytest.mark.parametrize("field", sorted(GAP_VALUE_SPECS))
    def test_the_spec_declares_a_key_table(self, field: str) -> None:
        assert GAP_VALUE_SPECS[field].key_table.strip()

    @pytest.mark.parametrize("field", sorted(GAP_VALUE_SPECS))
    def test_no_spec_expression_can_close_a_quote_or_comment(self, field: str) -> None:
        """These expressions are interpolated into SQL, so they are a boundary."""

        spec = GAP_VALUE_SPECS[field]
        expressions = [spec.value_expr, spec.unresolved_sql, spec.label_expr or ""]

        for expression in expressions:
            assert expression.count("'") % 2 == 0
            assert "--" not in expression
            assert ";" not in expression

    @pytest.mark.parametrize("field", sorted(GAP_VALUE_SPECS))
    def test_a_gap_requires_the_raw_value_to_exist(self, field: str) -> None:
        """A KType with no raw code at all is missing data, not an unmapped value.

        Both are unresolved, but only one is something a reviewer can rule on, and
        mixing them would put rows in the list with nothing to click.
        """

        assert "IS NOT NULL" in GAP_VALUE_SPECS[field].unresolved_sql

    def test_bodywork_reads_the_code_off_the_variant_not_the_bodywork_entity(self) -> None:
        """An unmapped body code never gets a bodywork node, so the entity is empty.

        Reading the raw code off the bodywork entity would make exactly the
        unmapped values -- the ones this screen exists to surface -- invisible.
        """

        spec = GAP_VALUE_SPECS["bodywork_form"]

        assert "variant_attributes" in spec.value_expr
        assert "bodywork_attributes" not in spec.value_expr

    def test_fuel_falls_back_to_the_kt182_code_when_no_label_exists(self) -> None:
        """The KT088 label is only written when an engine actually links."""

        spec = GAP_VALUE_SPECS["energy_sources"]

        assert "tecdoc_engine_fuel_label" in spec.value_expr
        assert "tecdoc_fuel_code" in spec.value_expr


class TestComparisonKeyIsShared:
    def test_the_gap_and_the_generator_key_a_value_identically(self) -> None:
        """`tecdoc_resolution_rules` is keyed on this, so the two must agree.

        A resolution row and the generated rule it supersedes are only provably
        the same value because both sides compute the key the same way.
        """

        for spelling in ("Petrol/Electric", "petrol / electric", "PETROL-ELECTRIC"):
            assert comparison_key(spelling) == comparison_key("Petrol/Electric")
