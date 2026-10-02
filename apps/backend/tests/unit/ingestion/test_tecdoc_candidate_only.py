import pytest

from ingestion.tecdoc.canonical_promotion import _candidate_only_vehicle_candidates
from ingestion.tecdoc.dat_extraction import TecDocHierarchyRecord


def record() -> TecDocHierarchyRecord:
    return TecDocHierarchyRecord(
        manufacturer_id="000005",
        manufacturer_name="AUDI",
        manufacturer_groups=("PC",),
        model_id="00001",
        model_name="A4",
        ktype_id="000012345",
        ktype_name="2.0 TFSI",
        year_from="202001",
        year_to=None,
        power_kw=140,
        displacement_cc=1984,
        fuel_type_code="001",
        engine_type_code="001",
        drive_type_code="001",
        transmission_type_code="002",
        body_type_code="003",
        engines=(),
        source_row_refs=("100:1", "110:1", "120:1"),
    )


def test_candidate_only_retains_identity_and_marks_promotion_boundary() -> None:
    candidates = _candidate_only_vehicle_candidates(
        record(), reason="engine_ambiguous", vehicle_fuel_type="lpg"
    )

    assert [candidate.entity_type for candidate in candidates] == [
        "manufacturer",
        "model_family",
        "vehicle_variant",
        "alias",
    ]
    variant = candidates[2]
    alias = candidates[3]
    assert variant.attributes["candidate_only_reason"] == "engine_ambiguous"
    assert variant.attributes["promotion_status"] == "candidate_only"
    assert variant.attributes["year_from"] == 2020
    assert variant.attributes["displacement_cc"] == 1984
    assert variant.attributes["vehicle_fuel_type"] == "lpg"
    assert alias.attributes["alias_text"] == "000012345"
    assert alias.attributes["target_source_key"] == "variant:000012345"


def test_candidate_only_never_fabricates_engine_identity() -> None:
    candidates = _candidate_only_vehicle_candidates(
        record(), reason="displacement_unresolved"
    )

    assert all(candidate.entity_type != "engine" for candidate in candidates)
    assert "engine_source_key" not in candidates[2].attributes


def test_production_months_are_kept_beside_the_years() -> None:
    from dataclasses import replace

    from ingestion.tecdoc.canonical_promotion import _vehicle_candidates

    dated = replace(record(), year_from="200804", year_to="201312")
    promoted = _vehicle_candidates(
        dated, year_from=2008, engine=None, displacement_cc=1984, displacement_source="table_120",
        fuel_type="petrol", fuel_components=(), engine_fuel_code=None, engine_fuel_label=None,
        fuel_representation="single", vehicle_fuel_type="petrol", engine_link_status="allocation_missing",
        bodywork_labels=None, bodywork_canonical=None, transmission_type_labels=None,
        drive_labels=None, drive_canonical=None,
    )
    candidate_only = _candidate_only_vehicle_candidates(dated, reason="engine_ambiguous", vehicle_fuel_type="petrol")

    for candidates in (promoted, candidate_only):
        variant = next(item for item in candidates if item.entity_type == "vehicle_variant")
        assert (variant.attributes["year_from"], variant.attributes["year_to"]) == (2008, 2013)
        assert (variant.attributes["month_from"], variant.attributes["month_to"]) == (200804, 201312)


def test_a_date_without_a_real_month_keeps_no_month() -> None:
    from ingestion.tecdoc.canonical_promotion import _year_month

    assert _year_month("200804") == 200804
    assert _year_month("200800") is None
    assert _year_month("200813") is None
    assert _year_month("2008") is None
    assert _year_month(None) is None


# --- Promotion gates: electric motors, Table 155 'from' only, petrol-plus-gas ---

_FUEL_LABELS = {
    "001": "Petrol", "011": "Electric", "020": "Petrol/Gas", "021": "Petrol/Alcohol/Gas",
}
_ENGINE_FUELS = {"001": "petrol", "011": "electric"}
_VEHICLE_FUELS = {
    "001": "petrol", "002": "lpg", "003": "cng", "004": "ethanol",
    "005": "hybrid_petrol", "006": "electric", "007": "hydrogen",
}


def _engine(**changes: object):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from ingestion.tecdoc.dat_extraction import EngineAllocation

    base = EngineAllocation(
        engine_id="900", engine_code="SYN", manufacturer_id="000005", fuel_type_code="001",
        displacement_cc_from=None, displacement_cc_to=None, deleted=False,
        applicability=(), engine_source_row_ref="155:synthetic",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _prepare(monkeypatch, *records: TecDocHierarchyRecord):  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from ingestion.tecdoc import canonical_promotion

    written: list[dict] = []  # type: ignore[type-arg]
    monkeypatch.setattr(canonical_promotion, "get_or_mint_node_id", lambda *args: "id")
    monkeypatch.setattr(
        canonical_promotion, "write_candidate", lambda *args, **kw: written.append(kw) or True,
    )
    summary = canonical_promotion.prepare_canonical_promotions(
        MagicMock(), batch_id="synthetic", records=records,
        engine_fuels=_ENGINE_FUELS, engine_fuel_labels=_FUEL_LABELS,
        vehicle_fuels=_VEHICLE_FUELS, complete_source=True, retain_candidate_only=True,
    )
    return summary, [row["candidate"] for row in written]


def _ktype(**changes: object) -> TecDocHierarchyRecord:
    from dataclasses import replace

    return replace(record(), **changes)  # type: ignore[arg-type]


def test_a_single_battery_electric_motor_promotes_without_a_displacement(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ev = _ktype(
        displacement_cc=None, fuel_type_code="006", engine_type_code="040",
        engines=(_engine(fuel_type_code="011"),),
    )
    summary, candidates = _prepare(monkeypatch, ev)

    assert summary.skipped_by_reason == {}
    promotion = summary.promotions[0]
    assert promotion.displacement_cc is None
    assert promotion.displacement_source == "not_applicable_electric"
    assert promotion.fuel_type == "electric"
    assert promotion.engine_link_status == "linked"
    variant = next(item for item in candidates if item.entity_type == "vehicle_variant")
    assert "promotion_status" not in variant.attributes
    assert variant.attributes["displacement_source"] == "not_applicable_electric"


def test_a_petrol_engine_without_a_displacement_stays_unresolved(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    petrol = _ktype(displacement_cc=None, engines=(_engine(),))
    summary, candidates = _prepare(monkeypatch, petrol)

    assert summary.promotions == ()
    assert summary.skipped_by_reason == {"displacement_unresolved": 1}
    variant = next(item for item in candidates if item.entity_type == "vehicle_variant")
    assert variant.attributes["candidate_only_reason"] == "displacement_unresolved"


@pytest.mark.parametrize(
    "changes",
    [
        # The electric fuel label alone is not enough: the engine type decides.
        {"engine_type_code": "001"},
        {"engine_type_code": None},
        # Hydrogen fuel cell: engine type 040, motor labelled Electric.
        {"fuel_type_code": "007"},
    ],
)
def test_the_electric_exemption_needs_engine_type_040_and_an_electric_vehicle(
    monkeypatch, changes: dict[str, object],  # type: ignore[no-untyped-def]
) -> None:
    base = {"displacement_cc": None, "fuel_type_code": "006", "engine_type_code": "040"}
    ev = _ktype(**{**base, **changes}, engines=(_engine(fuel_type_code="011"),))
    summary, _ = _prepare(monkeypatch, ev)

    assert summary.promotions == ()
    assert summary.skipped_by_reason == {"displacement_unresolved": 1}


def test_an_electric_ktype_that_states_a_displacement_keeps_the_normal_gate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    motor = _engine(fuel_type_code="011")
    stated = _ktype(
        displacement_cc=647, fuel_type_code="006", engine_type_code="040", engines=(motor,),
    )
    other = _ktype(
        ktype_id="000054321", displacement_cc=998, fuel_type_code="006",
        engine_type_code="040", engines=(motor,),
    )
    summary, _ = _prepare(monkeypatch, stated, other)

    assert summary.promotions == ()
    assert summary.skipped_by_reason == {"displacement_unresolved": 2}


def test_table_155_from_only_promotes_a_shared_engine(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    shared = _engine(displacement_cc_from=1984)
    first = _ktype(engines=(shared,))
    # The same engine on a KType whose Table 120 says 1968: no consensus.
    second = _ktype(ktype_id="000054321", displacement_cc=1968, engines=(shared,))
    summary, _ = _prepare(monkeypatch, first, second)

    assert [p.alias_text for p in summary.promotions] == ["000012345"]
    assert summary.promotions[0].displacement_cc == 1984
    assert summary.promotions[0].displacement_source == "table_155_from_only"
    # The KType whose own displacement contradicts the engine's stays out.
    assert summary.skipped_by_reason == {"displacement_unresolved": 1}


def test_consensus_and_an_exact_range_still_come_before_from_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    consensus = _ktype(engines=(_engine(displacement_cc_from=1984),))
    exact = _ktype(
        ktype_id="000054321",
        engines=(_engine(engine_id="901", displacement_cc_from=1984, displacement_cc_to=1984),),
    )
    summary, _ = _prepare(monkeypatch, consensus, exact)

    assert [p.displacement_source for p in summary.promotions] == [
        "table_120_complete_source_consensus", "table_155_exact",
    ]


def test_a_real_table_155_range_is_not_read_as_one_value(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ranged = _engine(displacement_cc_from=1984, displacement_cc_to=1998)
    first = _ktype(engines=(ranged,))
    second = _ktype(ktype_id="000054321", displacement_cc=1998, engines=(ranged,))
    summary, _ = _prepare(monkeypatch, first, second)

    assert summary.promotions == ()
    assert summary.skipped_by_reason == {"displacement_unresolved": 2}


@pytest.mark.parametrize(
    ("label_code", "vehicle_code", "fuel"),
    [
        ("020", "001", "petrol"), ("020", "002", "lpg"), ("020", "003", "cng"),
        ("021", "004", "ethanol"), ("021", "002", "lpg"),
    ],
)
def test_petrol_gas_engine_promotes_with_the_vehicle_fuel_it_contains(
    monkeypatch, label_code: str, vehicle_code: str, fuel: str,  # type: ignore[no-untyped-def]
) -> None:
    ktype = _ktype(fuel_type_code=vehicle_code, engines=(_engine(fuel_type_code=label_code),))
    summary, candidates = _prepare(monkeypatch, ktype)

    assert summary.skipped_by_reason == {}
    promotion = summary.promotions[0]
    assert promotion.fuel_type == fuel
    # The engine's own evidence stays unmapped: no components are invented.
    assert promotion.fuel_components == ()
    assert promotion.fuel_representation == "unmapped"
    assert promotion.engine_fuel_label == _FUEL_LABELS[label_code]
    variant = next(item for item in candidates if item.entity_type == "vehicle_variant")
    engine = next(item for item in candidates if item.entity_type == "engine")
    assert variant.attributes["fuel_type"] == fuel
    assert variant.attributes["fuel_components"] == []
    assert engine.attributes["fuel_type"] is None
    assert engine.attributes["fuel_components"] == []


@pytest.mark.parametrize(
    ("label_code", "vehicle_code"),
    [("020", "004"), ("020", "005"), ("021", "005"), ("020", None), ("020", "999")],
)
def test_petrol_gas_engine_stays_out_without_a_contained_vehicle_fuel(
    monkeypatch, label_code: str, vehicle_code: str | None,  # type: ignore[no-untyped-def]
) -> None:
    ktype = _ktype(fuel_type_code=vehicle_code, engines=(_engine(fuel_type_code=label_code),))
    summary, _ = _prepare(monkeypatch, ktype)

    assert summary.promotions == ()
    assert summary.skipped_by_reason == {"fuel_unresolved": 1}


def test_the_petrol_gas_labels_stay_unmapped_engine_evidence() -> None:
    from ingestion.tecdoc.reference_data import engine_fuel_evidence

    for label in ("Petrol/Gas", "Petrol/Alcohol/Gas"):
        evidence = engine_fuel_evidence("x", {"x": label})
        assert (evidence.representation, evidence.components) == ("unmapped", ())
