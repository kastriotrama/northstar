from ingestion.normalization_rules import normalize_ts_record


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
    assert outcome.normalized["bodywork_form"] == "wagon"
    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.candidates["model_family"] == "V60"
    assert "ABC123" not in str(outcome.to_payload())
    assert "SENSITIVEVIN123456" not in str(outcome.to_payload())


def test_converter_uses_recognized_base_manufacturer_and_is_retained() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Brabus GmbH", "base_manufacturer": "Mercedes-Benz AG"}
    )
    assert outcome.normalized["manufacturer"] == "Mercedes-Benz"
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"
    assert outcome.normalized["builder_converter_names"] == ["Brabus"]


def test_unknown_manufacturer_never_falls_back_to_populated_base() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Unknown Coach Company", "base_manufacturer": "Volvo"}
    )
    assert outcome.status == "review_required"
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
    goods = normalize_ts_record(
        {"manufacturer": "Volvo", "eu_category": "N1", "body_code": "AC"}
    )
    assert passenger.normalized["bodywork_form"] == "wagon"
    assert goods.status == "review_required"
    assert "bodywork_form" not in goods.normalized


def test_proposed_fuel_and_drive_values_remain_candidates() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "fuel1": "01", "fuel2": "03", "is_4wd": "1"}
    )
    assert outcome.status == "provisional"
    assert outcome.candidates["energy_sources"] == ["petrol", "electricity"]
    assert outcome.candidates["drive_type"] == "awd"
    assert "energy_sources" not in outcome.normalized
    assert "drive_type" not in outcome.normalized


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
