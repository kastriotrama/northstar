from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ingestion.tecdoc import canonical_promotion, hierarchy_persistence, promotion_job
from ingestion.tecdoc.canonical_promotion import PromotionPreparationSummary
from ingestion.tecdoc.dat_extraction import EngineAllocation, TecDocHierarchyRecord
from ingestion.tecdoc.reference_data import canonical_engine_fuels, engine_fuel_evidence


def mixed_record() -> TecDocHierarchyRecord:
    return TecDocHierarchyRecord(
        manufacturer_id="000099", manufacturer_name="SAAB", manufacturer_groups=("PC",),
        model_id="00099", model_name="9-3 Estate", ktype_id="synthetic-mixed",
        ktype_name="1.8 t", year_from="200501", year_to=None, power_kw=110,
        displacement_cc=1998, fuel_type_code="001", engine_type_code=None,
        drive_type_code=None, transmission_type_code=None, body_type_code=None,
        engines=(EngineAllocation(
            engine_id="18081", engine_code="B207E", manufacturer_id="000099",
            fuel_type_code="026", displacement_cc_from=1998, displacement_cc_to=1998,
            deleted=False, applicability=(), engine_source_row_ref="155:synthetic",
        ),), source_row_refs=("120:synthetic",),
    )


def test_generic_alcohol_is_preserved_without_guessing_ethanol_or_a_scalar() -> None:
    value = engine_fuel_evidence("026", {"026": "Petrol/Alcohol"})
    assert value.representation == "mixed"
    assert value.components == ("petrol", "alcohol_unspecified")
    assert value.scalar_fuel_type is None
    assert value.as_attributes()["key_table"] == "088"
    assert value.as_attributes()["components"] == ["petrol", "alcohol_unspecified"]
    with pytest.raises(FrozenInstanceError):
        value.source_code = "001"  # type: ignore[misc]


@pytest.mark.parametrize("label", [
    "Petrol/Ethanol", "Petrol/Electric", "Diesel/Electro", "Flexfuel/Electric",
    "Petrol/Ethanol/Electric", "Petrol/Electric/Liquefied Petroleum Gas (LPG)",
    "Petrol/Liquified Petroleum Gas (LPG)", "Petrol/Liquefied Petroleum Gas (LPG)",
])
def test_mixed_engine_descriptors_never_become_scalar_fuels(label: str) -> None:
    assert engine_fuel_evidence("x", {"x": label}).representation == "mixed"
    assert canonical_engine_fuels(Path("unused"), labels={"x": label}) == {}


@pytest.mark.parametrize(("code", "labels", "status"), [
    (None, {}, "missing"), ("026", {}, "missing"),
    ("026", {"026": "Unrecognized blend"}, "unmapped"),
    ("026", {"026": "petrol/alcohol"}, "unmapped"),
])
def test_missing_or_changed_official_labels_are_not_guessed(
    code: str | None, labels: dict[str, str], status: str,
) -> None:
    evidence = engine_fuel_evidence(code, labels)
    assert evidence.representation == status
    assert evidence.scalar_fuel_type is None
    assert evidence.components == ()


def test_octane_slash_is_not_mistaken_for_mixed_fuel() -> None:
    labels = {"005": "Diesel", "003": "Superplus (98/99) Unleaded", "026": "Petrol/Alcohol"}
    assert canonical_engine_fuels(Path("unused"), labels=labels) == {
        "005": "diesel", "003": "petrol",
    }


@pytest.mark.parametrize("fuel_mapping", [{}, {"026": "petrol"}, {"026": "ethanol"}])
def test_mixed_fuel_candidate_is_retained_but_never_promoted(
    monkeypatch: pytest.MonkeyPatch, fuel_mapping: dict[str, str],
) -> None:
    connection = MagicMock()
    written: list[Any] = []
    monkeypatch.setattr(canonical_promotion, "get_or_mint_node_id", lambda *args: "id")
    monkeypatch.setattr(canonical_promotion, "write_candidate", lambda *args, **kw: written.append(kw) or True)
    summary = canonical_promotion.prepare_canonical_promotions(
        connection, batch_id="synthetic", records=(mixed_record(),),
        engine_fuels=fuel_mapping, engine_fuel_labels={"026": "Petrol/Alcohol"},
        vehicle_fuels={"001": "petrol"}, complete_source=True, retain_candidate_only=True,
    )
    assert not summary.promotions
    assert summary.skipped_by_reason == {"fuel_unresolved": 1}
    variant = next(row["candidate"] for row in written if row["candidate"].entity_type == "vehicle_variant")
    assert variant.attributes["vehicle_fuel_type"] == "petrol"
    assert variant.attributes["promotion_status"] == "candidate_only"
    assert "engine_source_key" not in variant.attributes
    evidence = variant.attributes["engine_fuel_evidence"][0]
    assert evidence["engine_source_row_ref"] == "155:synthetic"
    assert evidence["fuel"]["components"] == ["petrol", "alcohol_unspecified"]


def test_ambiguous_engines_keep_separate_fuel_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    record = mixed_record()
    other = replace(record.engines[0], engine_id="different", fuel_type_code="005")
    record = replace(record, engines=(*record.engines, other))
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(hierarchy_persistence, "get_or_mint_node_id", lambda *args: "id")
    monkeypatch.setattr(hierarchy_persistence, "write_relationship_candidate", lambda *args, **kw: written.append(kw) or True)
    monkeypatch.setattr(hierarchy_persistence, "count_relationship_candidates", lambda *args, **kw: 2)
    result = hierarchy_persistence.persist_engine_relationship_candidates(
        MagicMock(), batch_id="synthetic", records=(record,),
        engine_fuel_labels={"026": "Petrol/Alcohol", "005": "Diesel"},
    )
    assert result.ambiguous_ktypes == 1
    assert written[0]["attributes"]["engine_fuel_evidence"]["scalar_fuel_type"] is None
    assert written[1]["attributes"]["engine_fuel_evidence"]["scalar_fuel_type"] == "diesel"
    candidates = canonical_promotion._candidate_only_vehicle_candidates(
        record, reason="engine_ambiguous", engine_fuel_labels={"026": "Petrol/Alcohol", "005": "Diesel"},
    )
    assert len(candidates[2].attributes["engine_fuel_evidence"]) == 2
    assert "engine_source_key" not in candidates[2].attributes


def test_full_job_passes_official_fuel_evidence_to_both_persistence_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    labels = {"026": "Petrol/Alcohol", "005": "Diesel"}
    records = (mixed_record(),)
    monkeypatch.setattr(promotion_job, "extract_dat_hierarchy", lambda *_: records)
    for name in ("run_tecdoc_migrations", "register_batch", "complete_batch"):
        monkeypatch.setattr(promotion_job, name, MagicMock())
    for name in ("canonical_vehicle_fuels", "official_bodywork_labels", "official_drive_type_labels", "official_transmission_type_labels"):
        monkeypatch.setattr(promotion_job, name, lambda *_: {})
    monkeypatch.setattr(promotion_job, "load_key_table_labels", lambda *args, **kw: labels)
    relationships = MagicMock()
    preparation = MagicMock(return_value=PromotionPreparationSummary((), {"fuel_unresolved": 1}, 0))
    monkeypatch.setattr(promotion_job, "persist_engine_relationship_candidates", relationships)
    monkeypatch.setattr(promotion_job, "prepare_canonical_promotions", preparation)
    graph = MagicMock(return_value=(0, 0))
    monkeypatch.setattr(promotion_job, "promote_graph_in_chunks", graph)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
    promotion_job.run_full_canonical_promotion(
        conn, MagicMock(), source_directory=Path("unused"), reference_directory=Path("unused"),
        batch_id="synthetic", source_version="0326", format_version="2.70", source_checksum="x",
    )
    assert relationships.call_args.kwargs["engine_fuel_labels"] == labels
    assert preparation.call_args.kwargs["engine_fuel_labels"] == labels
    assert preparation.call_args.kwargs["engine_fuels"] == {"005": "diesel"}
    assert graph.call_args.args[1] == ()
