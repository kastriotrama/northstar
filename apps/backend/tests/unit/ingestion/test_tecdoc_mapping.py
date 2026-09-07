import pytest

from ingestion.tecdoc.mapping import candidates_for_row, deduplicate_candidates
from ingestion.tecdoc.models import TecDocVehicleRow


def vehicle_row(**overrides: object) -> TecDocVehicleRow:
    values: dict[str, object] = {
        "ktype_id": "12345",
        "manufacturer_id": "5",
        "manufacturer_name": "  Volvo  Cars ",
        "model_id": "50",
        "model_name": "XC60",
        "variant_id": "500",
        "variant_name": "D4 AWD",
        "year_from": 2018,
        "platform_id": "P1",
        "platform_code": "SPA",
        "platform_year_from": 2017,
        "engine_id": "E1",
        "engine_code": "D4204T14",
        "displacement_cc": 1969,
        "fuel_type": "diesel",
        "transmission_id": "T1",
        "transmission_code": "TG-81SC",
        "transmission_type": "automatic",
        "gears": 8,
        "bodywork_id": "B1",
        "bodywork_name": "suv",
        "door_count": 5,
    }
    values.update(overrides)
    return TecDocVehicleRow(**values)  # type: ignore[arg-type]


def test_maps_complete_vehicle_tree_to_shared_canonical_candidates() -> None:
    candidates = candidates_for_row(vehicle_row())
    by_type = {candidate.entity_type: candidate for candidate in candidates}

    assert set(by_type) == {
        "manufacturer", "model_family", "platform", "vehicle_variant", "alias",
        "engine", "transmission", "bodywork",
    }
    assert by_type["manufacturer"].attributes["canonical_name"] == "Volvo Cars"
    assert by_type["alias"].attributes["target_source_key"] == "variant:500"
    assert by_type["vehicle_variant"].attributes["market"] == []


def test_deduplicates_components_shared_by_multiple_ktypes() -> None:
    candidates = deduplicate_candidates(
        (
            vehicle_row(),
            vehicle_row(ktype_id="12346", variant_id="501", variant_name="D4 AWD Pro"),
        )
    )

    assert sum(candidate.entity_type == "engine" for candidate in candidates) == 1
    assert sum(candidate.entity_type == "transmission" for candidate in candidates) == 1
    assert sum(candidate.entity_type == "alias" for candidate in candidates) == 2


def test_does_not_invent_optional_component_candidates() -> None:
    candidates = candidates_for_row(
        vehicle_row(
            engine_id=None,
            engine_code=None,
            displacement_cc=None,
            fuel_type=None,
            transmission_id=None,
            transmission_code=None,
            transmission_type=None,
            bodywork_id=None,
            bodywork_name=None,
            platform_id=None,
            platform_code=None,
        )
    )
    assert {candidate.entity_type for candidate in candidates} == {
        "manufacturer", "model_family", "vehicle_variant", "alias"
    }


def test_rejects_conflicting_attributes_for_same_source_key() -> None:
    with pytest.raises(ValueError, match="Conflicting TecDoc rows"):
        deduplicate_candidates((vehicle_row(), vehicle_row(ktype_id="2", engine_code="OTHER")))


def test_source_row_requires_stable_core_keys() -> None:
    with pytest.raises(ValueError, match="manufacturer_name"):
        TecDocVehicleRow.from_mapping(
            {
                "ktype_id": "1", "manufacturer_id": "2", "manufacturer_name": "",
                "model_id": "3", "model_name": "Model", "variant_id": "4",
                "variant_name": "Variant", "year_from": 2020,
            }
        )
