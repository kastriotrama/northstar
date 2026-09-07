"""The TecDoc rule generator must never accept what a reviewer has not.

Two of these tests are completeness guards, in the spirit of
`test_normalization_catalog`: one fails when a reviewed TecDoc lookup is added
without being registered, the other when the generator's precedence drifts away
from the pipeline's. Both exist because the failure they catch is silent -- a
value reaches the graph normalized differently from how the matcher expects,
and nothing raises.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from ingestion.tecdoc import reference_data
from ingestion.tecdoc.canonical_rule_proposals import (
    FIELD_SOURCES,
    GenerationReport,
    ObservedValue,
    TecDocRule,
    comparison_key,
    generate_rules,
    rules_fingerprint,
)
from ingestion.tecdoc.canonical_vocabulary import (
    canonical_terms,
    canonical_values,
    coverage_report,
)


def _observation(term: str, *, entity: str = "engine", field: str = "fuel_type", support: int = 1):
    return ObservedValue(
        entity_type=entity, source_field=field, source_term=term, support=support
    )


class TestGenerationNeverAccepts:
    """The invariant that makes the generator safe to run unattended."""

    def test_a_value_no_reviewed_mapping_covers_is_proposed_without_a_target(self) -> None:
        report = generate_rules([_observation("Hydrogen Fuel Cell", support=12)])

        (rule,) = report.rules
        assert rule.decision == "proposed"
        assert rule.canonical_value is None
        assert rule.evidence["reason"] == "unmapped"

    def test_only_a_reviewed_mapping_produces_an_accepted_rule(self) -> None:
        report = generate_rules([_observation("Petrol", support=5)])

        (rule,) = report.rules
        assert rule.decision == "accepted"
        assert rule.derivation == "reviewed_mapping"
        assert rule.canonical_value == "petrol"

    def test_a_value_spelling_a_canonical_token_is_still_only_proposed(self) -> None:
        """Matching spellings is not evidence of matching meaning."""

        report = generate_rules(
            [_observation("fwd", entity="vehicle_variant", field="drive_type", support=9)]
        )

        (rule,) = report.rules
        assert rule.canonical_value == "fwd"
        assert rule.decision == "proposed"
        assert rule.derivation == "generated"

    def test_no_generated_rule_is_ever_accepted(self) -> None:
        report = generate_rules(
            [
                _observation("Petrol", support=3),
                _observation("Something Unknown", support=3),
                _observation("Diesel", support=3),
            ]
        )

        assert all(r.derivation == "reviewed_mapping" for r in report.accepted)

    def test_an_accepted_rule_cannot_be_built_without_a_target(self) -> None:
        with pytest.raises(ValueError, match="must name a canonical value"):
            TecDocRule(
                rule_id="TD:x", area="fuel", entity_type="engine",
                source_field="fuel_type", source_term="X", canonical_field="energy_sources",
                canonical_value=None, decision="accepted", derivation="reviewed_mapping",
                support=1,
            )


class TestMixedDescriptorsFollowThePipeline:
    """A mixed fuel descriptor names a capability, never a resolved fuel."""

    def test_a_mixed_descriptor_is_proposed_with_its_components_as_evidence(self) -> None:
        report = generate_rules([_observation("Petrol/Electric", support=4200)])

        (rule,) = report.rules
        assert rule.decision == "proposed"
        assert rule.canonical_value is None
        assert rule.evidence["reason"] == "mixed_descriptor"
        assert rule.evidence["components"] == ["petrol", "electric"]

    @pytest.mark.parametrize(
        "label", sorted(reference_data.reviewed_mixed_engine_fuel_labels())
    )
    def test_generator_precedence_matches_engine_fuel_evidence(self, label: str) -> None:
        """The guard against the generator overruling a reviewed decision.

        Several labels sit in both the scalar and the mixed table.
        `engine_fuel_evidence` reads mixed first and refuses a scalar fuel; a
        generator that read scalar first would mint an accepted rule asserting a
        resolution the pipeline declines to make. Pinning one to the other means
        adding a label to either table cannot silently split them.
        """

        evidence = reference_data.engine_fuel_evidence("code", {"code": label})
        (rule,) = generate_rules([_observation(label)]).rules

        assert evidence.representation == "mixed"
        assert evidence.scalar_fuel_type is None
        assert rule.canonical_value is None, (
            f"{label!r} resolves to a scalar in generation but not in the pipeline"
        )
        assert rule.evidence["components"] == list(evidence.components)


class TestOpenVocabularyFields:
    def test_a_model_name_is_counted_but_never_targeted(self) -> None:
        report = generate_rules(
            [
                _observation(
                    "Golf VII", entity="model_family", field="canonical_name", support=900
                )
            ]
        )

        (rule,) = report.rules
        assert rule.is_open_vocabulary
        assert rule.canonical_value is None
        assert not rule.is_unmapped, "an open field is untargeted by design, not uncovered"

    def test_open_vocabulary_rows_are_excluded_from_coverage(self) -> None:
        """A million model names must not drag every field's coverage down."""

        report = generate_rules(
            [
                _observation("Petrol", support=100),
                _observation(
                    "Golf VII", entity="model_family", field="canonical_name", support=1_000_000
                ),
            ]
        )

        assert report.coverage() == 1.0


class TestCoverage:
    def test_coverage_is_weighted_by_rows_not_by_distinct_values(self) -> None:
        report = generate_rules(
            [
                _observation("Petrol", support=999),
                _observation("Unknown Label", support=1),
            ]
        )

        assert report.coverage("energy_sources") == pytest.approx(0.999)

    def test_a_field_with_no_reviewed_mapping_reports_zero_coverage(self) -> None:
        """KT 085 transmission labels have no reviewed mapping at all."""

        report = generate_rules(
            [
                _observation(
                    "Manual gearbox", entity="transmission", field="type", support=88_000
                )
            ]
        )

        assert report.coverage("transmission_type") == 0.0

    def test_unused_reviewed_mappings_are_reported(self) -> None:
        report = generate_rules([_observation("Petrol", support=1)])

        assert ("energy_sources", comparison_key("Diesel")) in report.unused_reviewed_mappings


class TestDeterminism:
    def test_the_fingerprint_follows_content_not_order(self) -> None:
        rules = generate_rules(
            [_observation("Petrol", support=2), _observation("Diesel", support=3)]
        ).rules

        assert rules_fingerprint(rules) == rules_fingerprint(list(reversed(rules)))

    def test_an_edited_target_changes_the_fingerprint_though_the_count_does_not(self) -> None:
        rules = generate_rules([_observation("Petrol", support=2)]).rules
        edited = (
            TecDocRule(**{**rules[0].__dict__, "canonical_value": "diesel"}),
        )

        assert len(edited) == len(rules)
        assert rules_fingerprint(edited) != rules_fingerprint(rules)

    def test_rule_ids_are_stable_across_runs(self) -> None:
        first = generate_rules([_observation("Petrol", support=2)]).rules
        second = generate_rules([_observation("Petrol", support=7)]).rules

        assert first[0].rule_id == second[0].rule_id

    def test_the_same_term_observed_twice_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate observation"):
            generate_rules([_observation("Petrol"), _observation("Petrol")])

    def test_distinct_spellings_that_normalize_alike_each_get_a_rule(self) -> None:
        """The 0326 catalog carries both spellings of many model names.

        Keying the duplicate check on the comparison key rejected the real
        catalog outright: "COLT Coupe" and "Colt Coupe" are two values TecDoc
        holds, and collapsing them would silently drop one from review.
        """

        report = generate_rules(
            [
                _observation("COLT Coupe", entity="model_family", field="canonical_name"),
                _observation("Colt Coupe", entity="model_family", field="canonical_name"),
            ]
        )

        assert len(report.rules) == 2
        assert len({rule.rule_id for rule in report.rules}) == 2
        assert {rule.source_term for rule in report.rules} == {"COLT Coupe", "Colt Coupe"}

    def test_colliding_spellings_of_a_closed_field_resolve_identically(self) -> None:
        """Two spellings of one reviewed label must not disagree on the target."""

        report = generate_rules(
            [_observation("Petrol"), _observation("PETROL"), _observation("petrol")]
        )

        assert {rule.canonical_value for rule in report.rules} == {"petrol"}


class TestRegistryCompleteness:
    """Guards against a TecDoc lookup that steers a value without being listed."""

    def test_every_reviewed_mapping_is_registered(self) -> None:
        """A mapping dict added to `reference_data` must appear in REVIEWED_MAPPINGS.

        This is the TecDoc equivalent of the TS catalog's hidden-transformer
        test. Without it, a new key-table dictionary changes a stored value with
        nothing in any listing to show for it.
        """

        source = Path(inspect.getfile(reference_data)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        lookups = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
            and target.id.startswith("_")
            and isinstance(node.value, ast.Dict)
        }
        registered = {mapping.constant for mapping in reference_data.REVIEWED_MAPPINGS}

        assert lookups == registered, (
            "every module-level lookup in reference_data must be registered in "
            f"REVIEWED_MAPPINGS. Unregistered: {sorted(lookups - registered)}. "
            f"Registered but missing: {sorted(registered - lookups)}."
        )

    def test_every_registered_constant_exists(self) -> None:
        for mapping in reference_data.REVIEWED_MAPPINGS:
            assert hasattr(reference_data, mapping.constant), (
                f"{mapping.name} names {mapping.constant}, which does not exist"
            )

    def test_every_registered_mapping_contributes_canonical_values(self) -> None:
        values = reference_data.reviewed_mapping_values()

        for mapping in reference_data.REVIEWED_MAPPINGS:
            assert values.get(mapping.canonical_field), (
                f"{mapping.name} is registered against {mapping.canonical_field} "
                "but contributes no canonical values"
            )

    def test_both_candidate_writers_have_their_transmission_attribute_registered(
        self,
    ) -> None:
        """The two writers disagree on the attribute name; both must be listed.

        `tecdoc.mapping.candidates_for_row` writes the raw extract value as
        `type`; `tecdoc.canonical_promotion` writes the official KT085 English
        label as `transmission_type_name`. Registering one name only reports
        zero transmission coverage on batches produced by the other writer, and
        reports it as an honest-looking number rather than as an error.
        """

        from ingestion.tecdoc.mapping import candidates_for_row
        from tests.unit.ingestion.test_tecdoc_mapping import vehicle_row

        registered = {
            source.source_field
            for source in FIELD_SOURCES
            if source.entity_type == "transmission"
        }
        written = [
            set(candidate.attributes)
            for candidate in candidates_for_row(vehicle_row())
            if candidate.entity_type == "transmission"
        ]
        assert written, "the mapping writer no longer emits a transmission candidate"
        for keys in written:
            assert registered & keys, (
                f"tecdoc.mapping writes transmission attributes {sorted(keys)}, "
                f"none of which FIELD_SOURCES registers ({sorted(registered)})"
            )
        assert "transmission_type_name" in registered, (
            "tecdoc.canonical_promotion writes the KT085 label as "
            "transmission_type_name; without it that batch reports no "
            "transmission coverage at all"
        )

    def test_every_field_source_targets_a_known_canonical_field(self) -> None:
        for source in FIELD_SOURCES:
            if source.open_vocabulary:
                continue
            assert canonical_values(source.canonical_field), (
                f"{source.entity_type}.{source.source_field} targets "
                f"{source.canonical_field}, which has no canonical values"
            )


class TestCanonicalVocabulary:
    """The vocabulary belongs to the graph, not to either source."""

    def test_a_tecdoc_only_token_is_canonical(self) -> None:
        """`fwd` is produced by TecDoc alone and must still be a legal value.

        Deriving the vocabulary from the TS rule set alone excluded it, which is
        why `TARGET_VOCABULARIES` had to restate the drive values by hand.
        """

        assert "fwd" in canonical_values("drive_type")
        assert "rwd" in canonical_values("drive_type")

    def test_a_ts_only_token_is_canonical(self) -> None:
        assert "motorhome" in canonical_values("bodywork_form")

    def test_every_term_records_who_vouches_for_it(self) -> None:
        for term in canonical_terms():
            assert term.vouched_by
            assert set(term.vouched_by) <= {"transportstyrelsen", "tecdoc"}

    def test_the_known_fuel_divergence_is_reported_rather_than_hidden(self) -> None:
        (fuel,) = [c for c in coverage_report() if c.canonical_field == "energy_sources"]

        assert "electric" in fuel.tecdoc_only
        assert "electricity" in fuel.transportstyrelsen_only
        assert not fuel.is_aligned

    def test_bodywork_tecdoc_values_are_a_subset_of_the_shared_vocabulary(self) -> None:
        (bodywork,) = [c for c in coverage_report() if c.canonical_field == "bodywork_form"]

        assert bodywork.tecdoc_only == ()


class TestReportShape:
    def test_an_empty_scan_reports_zero_coverage_rather_than_dividing_by_zero(self) -> None:
        assert GenerationReport(rules=()).coverage() == 0.0

    def test_an_observation_must_be_carried_by_at_least_one_row(self) -> None:
        with pytest.raises(ValueError, match="at least one row"):
            _observation("Petrol", support=0)

    def test_a_blank_observation_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be blank"):
            _observation("   ")

    def test_unregistered_attributes_are_skipped(self) -> None:
        report = generate_rules(
            [_observation("whatever", entity="platform", field="generation")]
        )

        assert report.rules == ()
