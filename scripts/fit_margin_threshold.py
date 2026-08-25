"""Fit `minimum_candidate_margin` from adjudicated margin-calibration items.

Reads the human verdicts recorded against a calibration batch in
`core.review_queue`, reweights them by the per-band population histogram the
sampler emitted, and reports weighted precision and recall at every observed
margin. The fitting logic lives in `ingestion.margin_calibration`; this script
only supplies database IO and the command line.

It never changes policy. Applying a threshold is a separate, deliberate edit
that must also bump `policy_version`.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict

import psycopg

from ingestion.margin_calibration import (
    ACCEPT,
    REJECT,
    UNSURE,
    VERDICTS,
    LabelledPair,
    band_weights,
    choose_threshold,
    sweep_thresholds,
)

VERDICT_KEY = "verdict"


def load_verdicts(
    connection: psycopg.Connection, *, batch_label: str
) -> tuple[list[tuple[float, str, str]], int]:
    """Return (margin, band, verdict) triples plus a count of unlabelled items."""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT reason_detail, status, resolution FROM core.review_queue "
            "WHERE source_batch_id = %s ORDER BY source_record_id",
            (batch_label,),
        )
        rows = cursor.fetchall()

    verdicts: list[tuple[float, str, str]] = []
    unlabelled = 0
    for reason_detail, status, resolution in rows:
        if status != "resolved" or not isinstance(resolution, dict):
            unlabelled += 1
            continue
        verdict = str(resolution.get(VERDICT_KEY, "")).strip().lower()
        if verdict not in VERDICTS:
            unlabelled += 1
            continue
        detail = json.loads(reason_detail)
        verdicts.append(
            (float(detail["separation_margin"]), str(detail["band"]), verdict)
        )
    return verdicts, unlabelled


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-label", required=True)
    parser.add_argument("--band-weights", required=True, help="Path from --weights-out.")
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument(
        "--minimum-effective-sample",
        type=float,
        default=30.0,
        help="Refuse to choose a threshold backed by less information than this.",
    )
    args = parser.parse_args()

    with open(args.band_weights, encoding="utf-8") as handle:
        weights_payload = json.load(handle)
    band_population = {
        band: int(count)
        for band, count in weights_payload["per_band_population"].items()
    }

    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        verdicts, unlabelled = load_verdicts(connection, batch_label=args.batch_label)

    labelled_per_band: dict[str, int] = {}
    for _, band, verdict in verdicts:
        if verdict in {ACCEPT, REJECT}:
            labelled_per_band[band] = labelled_per_band.get(band, 0) + 1
    weights = band_weights(
        band_population=band_population, labelled_per_band=labelled_per_band
    )

    pairs = tuple(
        LabelledPair(margin=margin, band=band, verdict=verdict, weight=weights[band])
        for margin, band, verdict in verdicts
        if verdict != UNSURE and band in weights
    )
    entries = sweep_thresholds(
        pairs,
        confidence=args.confidence,
        minimum_effective_sample=args.minimum_effective_sample,
    )
    chosen = choose_threshold(entries, target_precision=args.target_precision)

    output: dict[str, object] = {
        "batch_label": args.batch_label,
        "labelled_accept": sum(1 for pair in pairs if pair.verdict == ACCEPT),
        "labelled_reject": sum(1 for pair in pairs if pair.verdict == REJECT),
        "unsure": sum(1 for _, _, verdict in verdicts if verdict == UNSURE),
        "unlabelled": unlabelled,
        "labelled_per_band": labelled_per_band,
        "bands_without_labels": sorted(
            band for band in band_population if band not in labelled_per_band
        ),
        "target_precision": args.target_precision,
        "confidence": args.confidence,
        "sweep": [asdict(entry) for entry in entries],
        "recommended_threshold": None if chosen is None else chosen.threshold,
    }
    if chosen is None:
        output["verdict"] = (
            "No threshold clears the target precision lower bound with enough "
            "support. Adjudicate more items, especially in any band listed under "
            "bands_without_labels, or lower the target deliberately."
        )
    else:
        output["recommended_threshold_detail"] = asdict(chosen)
        output["verdict"] = (
            "Smallest threshold whose weighted precision lower bound clears the "
            "target. Review the full sweep before changing policy, and bump "
            "policy_version when applying it."
        )

    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
