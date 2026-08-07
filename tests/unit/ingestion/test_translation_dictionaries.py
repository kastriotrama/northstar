from types import MappingProxyType

import pytest

from ingestion.normalization_rules import normalize_manufacturer_entity, normalize_ts_record
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    RULE_SET_VERSION_V2,
    RULE_SET_VERSION_V3,
    RULE_SET_VERSION_V4,
    RULE_SET_VERSION_V5,
    RULE_SET_VERSION_V6,
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
    assert rules.get("BDY-110").canonical_value == "estate"
    assert rules.get("BDY-117").canonical_value == "cargo_estate"
    assert rules.get("DRV-008").canonical_value == "awd"
    assert rules.get("DRV-001").manufacturers == ("Mercedes-Benz",)
    assert rules.get("DRV-002").manufacturers == ("BMW",)
    assert rules.get("DRV-003").manufacturers == ("Audi",)
    assert rules.get("DRV-004").manufacturers == ("Volkswagen",)
    assert len([rule for rule in rules.rules if rule.area == "model_family"]) == 127
    assert rules.get("MOD-003").canonical_value == "XC60"
    assert rules.get("MOD-024").canonical_value == "Model Y"


def test_previous_rule_set_remains_available_for_exact_replay() -> None:
    version_2 = load_translation_rule_set(RULE_SET_VERSION_V2)
    version_3 = load_translation_rule_set(RULE_SET_VERSION_V3)
    version_4 = load_translation_rule_set(RULE_SET_VERSION_V4)
    version_5 = load_translation_rule_set(RULE_SET_VERSION_V5)
    version_6 = load_translation_rule_set(RULE_SET_VERSION_V6)
    current = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)

    assert version_2.get("BDY-010").canonical_value == "multi_purpose_vehicle"
    assert version_2.get("BDY-110").canonical_value == "wagon"
    assert version_3.get("BDY-010").canonical_value == "passenger_van"
    assert version_3.get("BDY-110").canonical_value == "wagon"
    assert "DRV-008" not in version_4.by_id
    assert "DRV-008" in version_5.by_id
    assert "MOD-001" not in version_5.by_id
    assert "MOD-001" in version_6.by_id
    assert "MOD-101" not in version_6.by_id
    assert current.version == "ts-translation-v7"
    assert current.get("BDY-010").canonical_value == "passenger_van"
    assert current.get("BDY-110").canonical_value == "estate"


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


@pytest.mark.parametrize(
    ("manufacturer", "model", "rule_id"),
    [
        ("Mercedes-Benz", "E 350 4MATIC", "DRV-001"),
        ("BMW", "X3 xDrive30e", "DRV-002"),
        ("Audi", "A6 quattro", "DRV-003"),
        ("Volkswagen", "Passat 4Motion", "DRV-004"),
    ],
)
def test_drive_marketing_rules_are_manufacturer_scoped(
    manufacturer: str, model: str, rule_id: str
) -> None:
    outcome = normalize_ts_record({"manufacturer": manufacturer, "model": model})

    assert outcome.normalized["drive_type"] == "awd"
    assert rule_id in outcome.applied_rule_ids


def test_drive_marketing_term_outside_manufacturer_scope_requires_review() -> None:
    outcome = normalize_ts_record({"manufacturer": "BMW", "model": "A6 quattro"})

    assert "drive_type" not in outcome.normalized
    assert "DRV-003" in outcome.candidate_rule_ids
    assert "drive_marketing_scope_unresolved" in outcome.review_reasons


def test_model_family_rule_accepts_prefix_and_keeps_manufacturer_scope() -> None:
    volkswagen = normalize_ts_record({"manufacturer": "Volkswagen", "model": "PASSAT GTE BUSINESS"})
    ford = normalize_ts_record({"manufacturer": "Ford", "model": "PASSAT GTE BUSINESS"})

    assert volkswagen.normalized["model_family"] == "Passat"
    assert "MOD-001" in volkswagen.applied_rule_ids
    assert "model_family" not in volkswagen.candidates
    assert ford.candidates["model_family"] == "PASSAT GTE BUSINESS"
    assert "model_family" not in ford.normalized


def test_model_family_rule_uses_complete_token_boundary() -> None:
    outcome = normalize_ts_record({"manufacturer": "Volvo", "model": "V600"})

    assert outcome.candidates["model_family"] == "V600"
    assert "model_family" not in outcome.normalized


@pytest.mark.parametrize(
    ("manufacturer", "model", "expected", "rule_id"),
    [
        ("Audi", "A3 SPORTBACK", "A3", "MOD-189"),
        ("Volkswagen", "ID.4 GTX 220 KW", "ID.4", "MOD-158"),
        ("BMW", "320D XDRIVE", "3 Series", "MOD-197"),
        ("BMW", "520D XDRIVE", "5 Series", "MOD-198"),
        ("Mercedes-Benz", "GLC 220 D 4MATIC", "GLC", "MOD-202"),
        ("Nissan", "LEAF 40KWH", "Leaf", "MOD-175"),
        ("Škoda", "ENYAQ 80X", "Enyaq", "MOD-134"),
        ("Hyundai", "I 30 CW", "i30", "MOD-147"),
        ("Mazda", "MAZDA3", "Mazda3", "MOD-167"),
    ],
)
def test_phase_two_model_rules_separate_family_from_suffix_evidence(
    manufacturer: str, model: str, expected: str, rule_id: str
) -> None:
    source_term = normalize_manufacturer_entity(manufacturer)
    assert source_term is not None
    outcome = normalize_ts_record(
        {"manufacturer": manufacturer, "model": model},
        manufacturer_entity_rules={
            f"manufacturer:{source_term}": {
                "entity_id": f"TEST-{rule_id}",
                "source_field": "manufacturer",
                "source_term": source_term,
                "canonical_name": manufacturer,
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
            }
        },
    )

    assert outcome.normalized["model_family"] == expected
    assert rule_id in outcome.applied_rule_ids
    assert "model_family" not in outcome.candidates


@pytest.mark.parametrize("model", ["SL", "ED"])
def test_unverified_kia_internal_codes_remain_candidates(model: str) -> None:
    outcome = normalize_ts_record({"manufacturer": "Kia", "model": model})

    assert outcome.candidates["model_family"] == model
    assert "model_family" not in outcome.normalized


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
