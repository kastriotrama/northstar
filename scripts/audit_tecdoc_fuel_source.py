"""Read-only full-source fuel/displacement audit; never prepares or promotes nodes."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ingestion.tecdoc.dat_extraction import TecDocHierarchyRecord, extract_dat_hierarchy
from ingestion.tecdoc.reference_data import engine_fuel_evidence, load_key_table_labels
from scripts.validate_local_matcher_cohort import digest, write_private_json


def audit_source(
    records: Sequence[TecDocHierarchyRecord], labels: dict[str, str], targets: set[str],
) -> dict[str, Any]:
    """Caller supplies the complete source, not a slice or matcher winners."""
    displacements: dict[str, set[int]] = defaultdict(set)
    appearances: Counter[str] = Counter()
    distribution: Counter[tuple[str | None, str | None, str]] = Counter()
    found = []
    for record in records:
        for engine in record.engines:
            if engine.deleted:
                continue
            appearances[engine.engine_id] += 1
            if record.displacement_cc is not None:
                displacements[engine.engine_id].add(record.displacement_cc)
            evidence = engine_fuel_evidence(engine.fuel_type_code, labels)
            distribution[(evidence.source_code, evidence.official_label, evidence.representation)] += 1
    for record in records:
        if record.ktype_id not in targets:
            continue
        engines = []
        for engine in record.engines:
            values = sorted(displacements[engine.engine_id])
            lower, upper = engine.displacement_cc_from, engine.displacement_cc_to
            engines.append({
                "allocation": asdict(engine),
                "fuel_evidence": engine_fuel_evidence(engine.fuel_type_code, labels).as_attributes(),
                "complete_source_displacements": values,
                "complete_source_active_ktype_count": appearances[engine.engine_id],
                "unique_source_displacement": values[0] if len(values) == 1 else None,
                "consensus_within_engine_bounds": bool(values) and all(
                    (lower is None or value >= lower) and (upper is None or value <= upper)
                    for value in values
                ),
            })
        found.append({
            "ktype": record.ktype_id, "manufacturer": record.manufacturer_name,
            "model": record.model_name, "vehicle_fuel_code": record.fuel_type_code,
            "source_row_refs": record.source_row_refs, "engines": engines,
            "active_engine_count": sum(not e.deleted for e in record.engines),
            "ready_to_promote": False,
        })
    return {
        "read_only": True, "source_ktypes": len(records), "target_count": len(targets),
        "missing_targets": sorted(targets - {row["ktype"] for row in found}),
        "engine_fuel_label_digest": digest(labels), "targets": found,
        "fuel_distribution_unit": "active KType-engine relationships, not vehicles or unique engines",
        "fuel_distribution": [
            {"code": code, "label": label, "representation": representation, "count": count}
            for (code, label, representation), count in sorted(distribution.items(), key=lambda item: str(item[0]))
        ],
        "limitations": [
            "Source consistency is not independent confirmation of an individual TS vehicle",
            "Mixed-fuel descriptors do not prove a specific fuel or support scalar Engine promotion",
            "Multiple engine allocations remain ambiguous; this audit selects none",
        ],
    }


def source_checksums(directory: Path) -> dict[str, str]:
    result = {}
    for table in ("012", "020", "030", "052", "100", "110", "120", "125", "155", "544", "547"):
        path = directory / f"{table}.dat"
        if path.is_file():
            with path.open("rb") as handle:
                result[path.name] = hashlib.file_digest(handle, "sha256").hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--ktype", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checksums = source_checksums(args.source_directory)
    labels = load_key_table_labels(args.source_directory, key_table_id="088")
    records = tuple(extract_dat_hierarchy(args.source_directory))
    report = audit_source(records, labels, set(args.ktype))
    if checksums != source_checksums(args.source_directory):
        raise ValueError("source files changed during audit")
    report["source_files_sha256"] = checksums
    report["source_files_digest"] = digest(checksums)
    write_private_json(args.output, report)
    print({"source_ktypes": len(records), "targets": len(report["targets"]),
           "missing_targets": report["missing_targets"], "output": str(args.output)})


if __name__ == "__main__":
    main()
