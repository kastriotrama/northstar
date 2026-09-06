from dataclasses import replace
from pathlib import Path

from scripts.audit_tecdoc_fuel_source import audit_source, source_checksums
from tests.unit.ingestion.test_tecdoc_fuel_evidence import mixed_record


def test_consensus_uses_non_target_records_and_excludes_deleted_engines():
    record = mixed_record()
    other = replace(record, ktype_id="other", displacement_cc=2000)
    deleted = replace(record, ktype_id="deleted", displacement_cc=999,
                      engines=(replace(record.engines[0], deleted=True),))
    report = audit_source((record, other, deleted), {"026": "Petrol/Alcohol"}, {record.ktype_id, "missing"})
    target = report["targets"][0]
    assert target["engines"][0]["complete_source_displacements"] == [1998, 2000]
    assert target["engines"][0]["unique_source_displacement"] is None
    assert target["engines"][0]["consensus_within_engine_bounds"] is False
    assert report["missing_targets"] == ["missing"]
    assert report["fuel_distribution"][0]["count"] == 2
    assert target["ready_to_promote"] is False


def test_unique_source_consensus_does_not_approve_mixed_fuel():
    record = mixed_record()
    report = audit_source((record,), {"026": "Petrol/Alcohol"}, {record.ktype_id})
    engine = report["targets"][0]["engines"][0]
    assert engine["unique_source_displacement"] == 1998
    assert engine["fuel_evidence"]["scalar_fuel_type"] is None
    assert report["targets"][0]["ready_to_promote"] is False


def test_checksum_covers_present_source_and_reference_tables(tmp_path: Path):
    (tmp_path / "155.dat").write_bytes(b"engine source")
    (tmp_path / "030.dat").write_bytes(b"labels")
    first = source_checksums(tmp_path)
    assert set(first) == {"155.dat", "030.dat"}
    (tmp_path / "030.dat").write_bytes(b"changed labels")
    assert source_checksums(tmp_path) != first
