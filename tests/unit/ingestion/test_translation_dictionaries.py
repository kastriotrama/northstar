from types import MappingProxyType

import pytest

from ingestion.normalization_rules import normalize_ts_record
from ingestion.translation_dictionaries import (
    PREVIOUS_RULE_SET_VERSION,
    REVIEWED_RULE_SET_VERSION,
    RuleSetNotFoundError,
    load_translation_rule_set,
)


def test_reviewed_rule_set_loads_deterministically_by_exact_version() -> None:
    first = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    second = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)

    assert first is second
    assert [rule.rule_id for rule in first.rules] == sorted(rule.rule_id for rule in first.rules)
    assert isinstance(first.by_id, MappingProxyType)
    assert all(rule.decision == "accepted" for rule in first.accepted_rules)
    assert [rule.rule_id for rule in first.proposed_rules] == ["FUEL-000"]
    with pytest.raises(TypeError):
        first.by_id["TRN-001"] = first.get("TRN-002")  # type: ignore[index]
    with pytest.raises(RuleSetNotFoundError):
        load_translation_rule_set("ts-translation-latest")


def test_stakeholder_changes_are_captured_without_concept_conflation() -> None:
    rules = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)

    assert rules.get("FUEL-003").canonical_value == "electricity"
    assert rules.get("FUEL-003").display_value == "EV / Electric / El"
    assert rules.get("FUEL-006").canonical_value == "gengas"
    assert rules.get("FUEL-009").canonical_value == "cng"
    assert rules.get("FUEL-010").canonical_value == "rme"
    assert rules.get("FUEL-012").canonical_value == "cng"
    assert rules.get("FUEL-013").canonical_value == "renewable_cng"
    assert rules.get("FUEL-019").canonical_value == "diesel"
    assert rules.get("BDY-115").canonical_value == "truck"
    assert rules.get("BDY-010").canonical_value == "passenger_van"


def test_previous_rule_set_remains_available_for_exact_replay() -> None:
    previous = load_translation_rule_set(PREVIOUS_RULE_SET_VERSION)
    current = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)

    assert previous.version == "ts-translation-v2"
    assert previous.get("BDY-010").canonical_value == "multi_purpose_vehicle"
    assert current.version == "ts-translation-v3"
    assert current.get("BDY-010").canonical_value == "passenger_van"


def test_undecided_rule_is_proposed_and_cannot_create_output() -> None:
    rules = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    missing = rules.get("FUEL-000")

    assert missing.decision == "proposed"
    assert missing.canonical_value is None
    outcome = normalize_ts_record({"manufacturer": "Volvo", "fuel1": "0"})
    assert "energy_sources" not in outcome.normalized
    assert "energy_sources" not in outcome.candidates
    assert not outcome.rule_matches


def test_accepted_rules_record_source_term_value_and_rule_identity() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "fuel1": "01",
            "fuel2": "03",
            "ev_config": "Laddhybrid",
        }
    )

    assert outcome.normalized["energy_sources"] == ["petrol", "electricity"]
    assert outcome.normalized["electrification_type"] == "plug_in_hybrid"
    fuel_match = next(match for match in outcome.rule_matches if match.rule_id == "FUEL-001")
    assert fuel_match.rule_set_version == REVIEWED_RULE_SET_VERSION
    assert fuel_match.source_field == "fuel1"
    assert fuel_match.source_term == "01"
    assert fuel_match.canonical_value == "petrol"


def test_unknown_and_conflicting_rules_never_silently_win() -> None:
    unknown = normalize_ts_record({"manufacturer": "Volvo", "fuel1": "99"})
    conflict = normalize_ts_record(
        {"manufacturer": "Volvo", "model": "V60 Geartronic", "gearbox": "M"}
    )

    assert "energy_sources" not in unknown.normalized
    assert "fuel1_code_unknown" in unknown.review_reasons
    assert conflict.normalized["transmission_type"] == "manual"
    assert "transmission_structured_marketing_conflict" in conflict.review_reasons
    assert "TRN-004A" in conflict.candidate_rule_ids


def test_marketing_rules_require_the_reviewed_manufacturer_scope() -> None:
    volvo = normalize_ts_record({"manufacturer": "Volvo", "model": "V60 Geartronic"})
    bmw = normalize_ts_record({"manufacturer": "BMW", "model": "V60 Geartronic"})

    assert volvo.normalized["transmission_type"] == "automatic"
    assert "TRN-004A" in volvo.applied_rule_ids
    assert "transmission_type" not in bmw.normalized
    assert "transmission_marketing_scope_unresolved" in bmw.review_reasons


def test_reviewed_bodywork_change_stores_ba_as_truck() -> None:
    outcome = normalize_ts_record({"manufacturer": "Volvo", "eu_category": "N1", "body_code": "BA"})

    assert outcome.normalized["bodywork_form"] == "truck"
    assert outcome.normalized["bodywork_registry_label_sv"] == "Lastbil"
    assert "BDY-115" in outcome.applied_rule_ids


def test_reviewed_van_terms_keep_distinct_internal_forms() -> None:
    goods_van = normalize_ts_record(
        {"manufacturer": "Ford", "eu_category": "N1", "model": "Transit Cargo Van"}
    )
    passenger_van = normalize_ts_record(
        {"manufacturer": "Volkswagen", "eu_category": "M1", "model": "Multivan"}
    )

    assert goods_van.normalized["bodywork_form"] == "van"
    assert "BDY-009" in goods_van.applied_rule_ids
    assert passenger_van.normalized["bodywork_form"] == "passenger_van"
    assert "BDY-010" in passenger_van.applied_rule_ids


def test_official_af_keeps_multi_purpose_vehicle_over_compatible_marketing_term() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volkswagen",
            "eu_category": "M1",
            "body_code": "AF",
            "model": "Multivan",
        }
    )

    assert outcome.normalized["bodywork_form"] == "multi_purpose_vehicle"
    assert {"BDY-010", "BDY-113"} <= set(outcome.applied_rule_ids)
    assert "bodywork_structured_marketing_conflict" not in outcome.review_reasons
