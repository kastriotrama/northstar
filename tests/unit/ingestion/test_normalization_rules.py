from dataclasses import replace

from ingestion.normalization_rules import normalize_ts_record
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRuleSet,
    load_translation_rule_set,
)


def test_accepted_values_are_normalized_without_identifiers() -> None:
    outcome = normalize_ts_record(
        {
            "plate": "ABC123",
            "vin": "SENSITIVEVIN123456",
            "manufacturer": "Volvo Car Corporation",
            "model": "V60",
            "model_year": 2024,
            "eu_category": "M1",
            "body_code": "AC",
            "gearbox": "Z",
        }
    )

    assert outcome.status == "provisional"
    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.candidates["model_family"] == "V60"
    assert outcome.pipeline_version == "normalization-pipeline-v4"
    assert [entry.sequence for entry in outcome.decision_trace] == list(
        range(1, len(outcome.decision_trace) + 1)
    )
    traced_fields = {
        (entry.transformer_id, entry.field, entry.rule_ids) for entry in outcome.decision_trace
    }
    assert ("ts.manufacturer", "manufacturer", ("MFR-102",)) in traced_fields
    assert ("ts.bodywork", "bodywork_form", ("BDY-110",)) in traced_fields
    assert ("ts.transmission", "transmission_type", ("TRN-008",)) in traced_fields
    manufacturer_trace = next(
        entry
        for entry in outcome.decision_trace
        if entry.transformer_id == "ts.manufacturer" and entry.field == "manufacturer"
    )
    assert manufacturer_trace.before == "Volvo Car Corporation"
    assert manufacturer_trace.after == "Volvo"
    assert "ABC123" not in str(outcome.to_payload())
    assert "SENSITIVEVIN123456" not in str(outcome.to_payload())


def test_converter_uses_recognized_base_manufacturer_and_is_retained() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Brabus GmbH", "base_manufacturer": "Mercedes-Benz AG"}
    )
    assert outcome.normalized["manufacturer"] == "Mercedes-Benz"
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"
    assert outcome.normalized["builder_converter_names"] == ["Brabus"]


def test_kabe_converter_uses_fiat_base_and_retains_builder() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "KABE AB BOX 14 561 06 TENHULT",
            "base_manufacturer": "FCA ITALY S.P.A. C. SO G. AGNELLI 200",
        }
    )

    assert outcome.normalized["manufacturer"] == "Fiat"
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"
    assert outcome.normalized["builder_converter_names"] == ["KABE"]


def test_kia_legal_entity_is_a_recognized_manufacturer() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "KIAMOTORSCORPORATION",
            "brand": "KIA",
            "model": "NIRO",
            "vin": "KNA00000000000000",
        }
    )

    assert outcome.normalized["manufacturer"] == "Kia"
    assert "manufacturer_unknown" not in outcome.review_reasons


def test_mini_requires_agreeing_parent_brand_model_and_vin_evidence() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "BAYERISCHE MOTOREN WERKE AG, DE-80788 MÜNCHEN",
            "brand": "MINI",
            "model": "COOPER",
            "vin": "WMW00000000000000",
        }
    )

    assert outcome.normalized["manufacturer"] == "MINI"
    assert {"MFR-PARENT-MARKETED", "MFR-BRAND-MODEL", "MFR-BRAND-VIN-WMI"} <= set(
        outcome.applied_rule_ids
    )


def test_multibrand_groups_require_marketed_brand_evidence() -> None:
    peugeot = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "PEUGEOT", "model": "2008"}
    )
    jeep = normalize_ts_record(
        {"manufacturer": "FCA ITALY S.P.A.", "brand": "JEEP", "model": "COMPASS"}
    )
    unresolved = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "PEUGEOT", "model": "UNKNOWN"}
    )

    assert peugeot.normalized["manufacturer"] == "Peugeot"
    assert jeep.normalized["manufacturer"] == "Jeep"
    assert "manufacturer" not in unresolved.normalized
    assert "manufacturer_corporate_group_unresolved" in unresolved.review_reasons


def test_missing_manufacturer_accepts_brand_only_with_corroborating_model() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "model": "V70"})

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert "manufacturer_missing_compare_brand" not in outcome.review_reasons


def test_missing_manufacturer_accepts_brand_with_matching_ktype() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "ktype_manufacturer": "Volvo"})

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert "MFR-BRAND-KTYPE" in outcome.applied_rule_ids


def test_conflicting_ktype_prevents_brand_model_confirmation() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "model": "V70", "ktype_manufacturer": "BMW"})

    assert outcome.status == "review_required"
    assert "manufacturer" not in outcome.normalized
    assert outcome.candidates["manufacturer"] == "Volvo"
    assert "manufacturer_evidence_conflict" in outcome.review_reasons


def test_unknown_manufacturer_never_falls_back_to_populated_base() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Unknown Coach Company", "base_manufacturer": "Volvo"}
    )
    assert outcome.status == "review_required"
    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_unknown" in outcome.review_reasons


def test_unknown_manufacturer_is_not_replaced_by_agreeing_brand_and_model() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Unknown Coach Company",
            "brand": "Volvo",
            "model": "V70",
            "base_manufacturer": "BMW",
        }
    )

    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_unknown" in outcome.review_reasons


def test_recognized_brand_is_only_a_candidate_when_manufacturer_is_missing() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "base_manufacturer": "BMW"})

    assert outcome.status == "review_required"
    assert outcome.candidates["manufacturer"] == "Volvo"
    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_missing_compare_brand" in outcome.review_reasons


def test_bodywork_codes_are_vehicle_category_scoped() -> None:
    passenger = normalize_ts_record(
        {"manufacturer": "Volvo", "eu_category": "M1", "body_code": "AC"}
    )
    goods = normalize_ts_record({"manufacturer": "Volvo", "eu_category": "N1", "body_code": "AC"})
    assert passenger.normalized["bodywork_form"] == "estate"
    assert goods.status == "review_required"
    assert "bodywork_form" not in goods.normalized


def test_runtime_rule_set_applies_an_activated_override() -> None:
    base = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    rules = tuple(
        replace(rule, canonical_value="sedan") if rule.rule_id == "BDY-110" else rule
        for rule in base.rules
    )
    override = TranslationRuleSet(version="ts-review-test", rules=rules)

    outcome = normalize_ts_record(
        {"manufacturer": "Volvo", "eu_category": "M1", "body_code": "AC"},
        rule_set=override,
    )

    assert outcome.normalized["bodywork_form"] == "sedan"


def test_reviewed_fuel_is_accepted_while_drive_remains_a_candidate() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "fuel1": "01", "fuel2": "03", "is_4wd": "1"}
    )
    assert outcome.status == "provisional"
    assert outcome.normalized["energy_sources"] == ["petrol", "electricity"]
    assert outcome.candidates["drive_type"] == "awd"
    assert "drive_type" not in outcome.normalized
    assert {match.rule_id for match in outcome.rule_matches} >= {"FUEL-001", "FUEL-003"}


def test_explicit_hybrid_marker_adds_electricity_to_petrol_carrier() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Audi", "fuel1": "01", "fuel2": "0", "fuel3": "0", "ev_config": "ELHYBRID"}
    )

    assert outcome.normalized["energy_sources"] == ["petrol", "electricity"]
    assert outcome.normalized["electrification_type"] == "hybrid"
    assert "electrification_fuel_evidence_conflict" not in outcome.review_reasons


def test_explicit_hybrid_marker_preserves_underlying_diesel() -> None:
    outcome = normalize_ts_record({"manufacturer": "Audi", "fuel1": "02", "ev_config": "ELHYBRID"})

    assert outcome.normalized["energy_sources"] == ["diesel", "electricity"]
    assert outcome.normalized["electrification_type"] == "hybrid"


def test_hybrid_without_combustion_carrier_still_routes_to_review() -> None:
    outcome = normalize_ts_record({"manufacturer": "Audi", "fuel1": "03", "ev_config": "ELHYBRID"})

    assert "electrification_type" not in outcome.normalized
    assert "electrification_fuel_evidence_conflict" in outcome.review_reasons


def test_malformed_source_values_route_to_review() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "build_date": "20241341", "gearbox": "?", "fuel1": "99"}
    )
    assert outcome.status == "review_required"
    assert set(outcome.review_reasons) >= {
        "build_date_malformed",
        "transmission_code_unknown",
        "fuel1_code_unknown",
    }


def test_non_object_raw_record_fails_without_raising() -> None:
    outcome = normalize_ts_record("not-an-object")
    assert outcome.status == "failed"
    assert outcome.review_reasons == ("raw_record_not_object",)
    assert outcome.decision_trace[0].transformer_id == "ts.input-contract"
    assert outcome.decision_trace[0].confidence_effect == -1.0


def test_text_canonicalization_runs_before_translation_rules() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "  Volvo\u00a0Car   Corporation  ",
            "model": " Ｖ６０\u2003Recharge ",
            "eu_category": "m1",
            "body_code": "ac",
            "gearbox": "z",
        }
    )

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.candidates["model_family"] == "V60 Recharge"
    assert any(
        entry.transformer_id == "ts.text-canonicalization"
        and entry.target == "canonical"
        and entry.field == "model"
        and entry.before == " Ｖ６０\u2003Recharge "
        and entry.after == "V60 Recharge"
        for entry in outcome.decision_trace
    )


def test_dates_are_extracted_with_explicit_precision_and_original_evidence() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "registration_date": "20240131",
            "build_month": "202312",
        }
    )

    assert outcome.normalized["registration_date"] == "2024-01-31"
    assert outcome.normalized["production_date"] == "2023-12"
    assert outcome.normalized["production_year"] == 2023
    assert outcome.normalized["production_date_precision"] == "month"
    assert {"DATE-REGISTRATION-V1", "DATE-PRODUCTION-MONTH-V1"} <= set(outcome.applied_rule_ids)
    registration_trace = next(
        entry
        for entry in outcome.decision_trace
        if entry.transformer_id == "ts.dates" and entry.field == "registration_date"
    )
    assert registration_trace.before == {
        "registration_date": "20240131",
        "build_month": "202312",
    }
    assert registration_trace.after == "2024-01-31"


def test_explicit_open_and_closed_production_ranges_are_supported() -> None:
    closed = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2019-05", "production_to": "2024"}
    )
    open_ended = normalize_ts_record({"manufacturer": "Volvo", "production_from": "20230101"})
    mixed_precision = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2024-06", "production_to": "2024"}
    )

    assert closed.normalized["production_from"] == "2019-05"
    assert closed.normalized["production_year_from"] == 2019
    assert closed.normalized["production_to"] == "2024"
    assert closed.normalized["production_year_to"] == 2024
    assert open_ended.normalized["production_from"] == "2023-01-01"
    assert "production_to" not in open_ended.normalized
    assert mixed_precision.normalized["production_from"] == "2024-06"
    assert mixed_precision.normalized["production_to"] == "2024"


def test_malformed_or_reversed_date_ranges_are_not_partially_normalized() -> None:
    malformed = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2020", "production_to": "202413"}
    )
    reversed_range = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2024", "production_to": "2020"}
    )

    assert "production_from" not in malformed.normalized
    assert "production_to_malformed" in malformed.review_reasons
    assert "production_from" not in reversed_range.normalized
    assert "production_to" not in reversed_range.normalized
    assert "production_range_reversed" in reversed_range.review_reasons


def test_engine_power_and_displacement_are_structured_without_marketing_inference() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "engine_code": " b4204t ",
            "engine_family_code": " vea ",
            "engine_family_name": " Volvo Engine Architecture ",
            "kw": 145,
            "ccm": 1969,
        }
    )

    assert outcome.normalized["engine_code"] == "B4204T"
    assert outcome.normalized["engine_family_code"] == "VEA"
    assert outcome.normalized["engine_family_name"] == "Volvo Engine Architecture"
    assert outcome.normalized["power_kw"] == 145
    assert outcome.normalized["power_source_unit"] == "kw"
    assert outcome.normalized["displacement_cc"] == 1969
    assert outcome.normalized["displacement_source_unit"] == "ccm"


def test_metric_power_and_litre_displacement_convert_using_documented_units() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "power_ps": "200", "displacement_l": "1,998"}
    )

    assert outcome.normalized["power_kw"] == 147
    assert outcome.normalized["power_source_unit"] == "metric_hp"
    assert outcome.normalized["displacement_cc"] == 1998
    assert outcome.normalized["displacement_source_unit"] == "litre"


def test_measurement_boundaries_and_ambiguous_sources_route_to_review() -> None:
    malformed = normalize_ts_record({"manufacturer": "Volvo", "kw": 0, "ccm": "not-a-number"})
    ambiguous = normalize_ts_record(
        {"manufacturer": "Volvo", "kw": 100, "power_ps": 136, "ccm": 1969}
    )

    assert "power_kw" not in malformed.normalized
    assert "displacement_cc" not in malformed.normalized
    assert {"kw_malformed", "ccm_malformed"} <= set(malformed.review_reasons)
    assert "power_kw" not in ambiguous.normalized
    assert "power_source_ambiguous" in ambiguous.review_reasons


def test_hybrid_and_dual_fuel_keep_each_underlying_fuel() -> None:
    plug_in_hybrid = normalize_ts_record(
        {"manufacturer": "Volvo", "fuel1": "01", "fuel2": "03", "ev_config": "Laddhybrid"}
    )
    bi_fuel = normalize_ts_record(
        {"manufacturer": "Volvo", "fuel1": "01", "fuel2": "09", "fuel_combo": "B"}
    )

    assert plug_in_hybrid.normalized["energy_sources"] == ["petrol", "electricity"]
    assert plug_in_hybrid.normalized["electrification_type"] == "plug_in_hybrid"
    assert bi_fuel.normalized["energy_sources"] == ["petrol", "cng"]
    assert bi_fuel.normalized["fuel_combination"] == "bi_fuel"


def test_all_three_ts_fuel_fields_are_retained() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "fuel1": "01",
            "fuel2": "09",
            "fuel3": "03",
            "fuel_combo": "T",
        }
    )

    assert outcome.normalized["energy_sources"] == ["petrol", "cng", "electricity"]
    assert outcome.normalized["fuel_combination"] == "tri_fuel"
