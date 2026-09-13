"""What an agent may and may not propose for an unmapped TecDoc value.

The suggester's whole job is to be refusable. These pin the refusals, because a
proposal that slips past them can reach `tecdoc_resolution_rules`, and a row
there is live: it decides a stored canonical value exactly as a reviewed one
does. The confidence number is advisory; the guards are not.
"""

from __future__ import annotations

import pytest

from ingestion.tecdoc.gap_suggestions import (
    GapValue,
    ReuseSuggester,
    Suggestion,
    SuggestionError,
    blocked_reason,
    conforms,
    naming_convention,
    validate,
)


def _suggestion(**overrides: object) -> Suggestion:
    base: dict[str, object] = {
        "canonical_field": "transmission_type",
        "source_term": "Manual Transmission",
        "key_table": "085",
        "decision": "accepted",
        "suggested_value": "manual",
        "origin": "reuse",
        "rationale": "same value, different spelling",
        "convention": "lowercase, single token",
        "confidence": 0.9,
        "support": 385,
        "suggested_by": "test",
    }
    base.update(overrides)
    return Suggestion(**base)  # type: ignore[arg-type]


class TestNamingConvention:
    def test_reads_the_shape_off_the_values_themselves(self) -> None:
        assert "underscore-separated" in naming_convention(["box_body", "chassis_cab"])
        assert "single token" in naming_convention(["fwd", "rwd", "awd"])

    def test_says_so_when_there_is_nothing_to_derive_from(self) -> None:
        assert "cannot be derived" in naming_convention([])


class TestConforms:
    def test_accepts_a_term_shaped_like_the_field_s_own_values(self) -> None:
        assert conforms("hybrid_lpg", ["petrol", "hybrid_petrol"])
        assert conforms("cvt", ["amt", "automatic", "manual"])

    def test_rejects_a_spelling_the_field_has_never_used(self) -> None:
        # The field is single-token; an underscore introduces a shape that no
        # existing value has, which is how a vocabulary quietly drifts.
        assert not conforms("electric_drive", ["amt", "automatic", "manual"])
        assert not conforms("Manual", ["amt", "automatic", "manual"])
        assert not conforms("manual gearbox", ["amt", "automatic", "manual"])

    def test_rejects_untrimmed_and_empty(self) -> None:
        assert not conforms(" manual", ["manual"])
        assert not conforms("", ["manual"])


class TestBlockedReason:
    def test_refuses_a_mixed_fuel_descriptor(self) -> None:
        # "Petrol/Electric" names a capability, not the fuel a vehicle uses.
        # `generate_rules` already refuses to read it as a scalar; so must this.
        assert blocked_reason("energy_sources", "Petrol/Electric") == "mixed_descriptor"

    def test_leaves_other_fields_alone(self) -> None:
        assert blocked_reason("bodywork_form", "Petrol/Electric") is None
        assert blocked_reason("energy_sources", "Diesel") is None


class TestReuseSuggester:
    def test_answers_only_when_the_vocabulary_already_holds_the_term(self) -> None:
        gap = GapValue("drive_type", "FWD", key_table="081", support=12)
        proposal = ReuseSuggester().suggest(gap)
        assert proposal is not None
        assert proposal.suggested_value == "fwd"
        assert proposal.origin == "reuse"
        # An identity claim, not an estimate.
        assert proposal.confidence == 1.0

    def test_declines_a_value_needing_judgement(self) -> None:
        # 'Manual Transmission' and 'manual' are the same concept but not the
        # same key, so the deterministic matcher must not claim them.
        gap = GapValue("transmission_type", "Manual Transmission", key_table="085", support=385)
        assert ReuseSuggester().suggest(gap) is None

    def test_declines_an_open_vocabulary_field(self) -> None:
        gap = GapValue("model_family", "COLT Saloon", key_table=None, support=12)
        assert ReuseSuggester().suggest(gap) is None

    def test_declines_a_blocked_value(self) -> None:
        gap = GapValue("energy_sources", "Petrol/Electric", key_table="088", support=9)
        assert ReuseSuggester().suggest(gap) is None


class TestValidate:
    def test_accepts_a_reuse_naming_an_existing_term(self) -> None:
        validate(_suggestion())

    def test_refuses_a_reuse_naming_a_term_that_does_not_exist(self) -> None:
        with pytest.raises(SuggestionError, match="cannot be a reuse"):
            validate(_suggestion(suggested_value="stickshift"))

    def test_refuses_a_mint_that_breaks_the_field_convention(self) -> None:
        with pytest.raises(SuggestionError, match="does not follow"):
            validate(_suggestion(origin="mint", suggested_value="Manual Gearbox"))

    def test_refuses_a_mint_of_a_term_that_already_exists(self) -> None:
        with pytest.raises(SuggestionError, match="already exists"):
            validate(_suggestion(origin="mint", suggested_value="manual"))

    def test_refuses_to_close_an_open_vocabulary(self) -> None:
        with pytest.raises(SuggestionError, match="open vocabulary"):
            validate(
                _suggestion(
                    canonical_field="model_family",
                    source_term="COLT Saloon",
                    origin="mint",
                    suggested_value="colt",
                )
            )

    def test_refuses_a_blocked_value_however_it_is_dressed(self) -> None:
        with pytest.raises(SuggestionError, match="capability"):
            validate(
                _suggestion(
                    canonical_field="energy_sources",
                    source_term="Petrol/Electric",
                    suggested_value="petrol",
                )
            )

    def test_requires_exclude_and_excluded_to_agree(self) -> None:
        with pytest.raises(SuggestionError, match="same claim"):
            validate(_suggestion(origin="exclude", decision="accepted"))
        with pytest.raises(SuggestionError, match="same claim"):
            validate(
                _suggestion(origin="reuse", decision="excluded", suggested_value=None)
            )

    def test_accepts_a_well_formed_exclusion(self) -> None:
        validate(
            _suggestion(
                canonical_field="bodywork_form",
                source_term="Motorcycle",
                origin="exclude",
                decision="excluded",
                suggested_value=None,
            )
        )

    def test_refuses_an_accepted_suggestion_with_no_target(self) -> None:
        with pytest.raises(SuggestionError, match="must name a value"):
            validate(_suggestion(suggested_value=None, origin="mint"))

    def test_refuses_confidence_outside_the_unit_interval(self) -> None:
        with pytest.raises(SuggestionError, match="between 0.0 and 1.0"):
            validate(_suggestion(confidence=1.4))
