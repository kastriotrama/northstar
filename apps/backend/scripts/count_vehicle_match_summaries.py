"""Count the cars by match state again and keep the counts the Vehicles screen opens with.

The screen answers from kept counts (`vehicle_match_results/summaries.py`) and
counts again behind an answer when cars were matched since. This counts the
unfiltered view now, so the first look after a load or a long run does not wait
for it and does not show the numbers from before:

    python -m scripts.count_vehicle_match_summaries

Reads the vehicles and their stored results; writes two rows of kept counts.
"""

from __future__ import annotations

import time
from typing import Any

from api.app.features.vehicle_match_results.repository import (
    MatchResultRepository,
    MatchSummaryRepository,
)
from api.app.features.vehicle_match_results.service import MatchResultService
from api.app.features.vehicle_match_results.summaries import StoredSummaries
from api.app.features.vehicles.schemas import VehicleFilter
from ingestion.config import get_ingestion_settings
from ingestion.datastores import DatastoreClients


def recount_unfiltered(connect: Any) -> str:
    """Count every car now, keep both counts, and say what was counted."""

    service = MatchResultService(
        MatchResultRepository(connect),
        StoredSummaries(MatchSummaryRepository(connect), run_in_background=False),
    )
    started = time.monotonic()
    counts, _ = service.recount(VehicleFilter())
    states = ", ".join(f"{item.state} {item.cars}" for item in counts.states if item.cars)
    return f"counted {counts.total} cars in {time.monotonic() - started:.0f}s: {states}"


def main() -> int:
    connect = DatastoreClients.from_settings(get_ingestion_settings()).postgres.connect
    print(recount_unfiltered(connect))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
