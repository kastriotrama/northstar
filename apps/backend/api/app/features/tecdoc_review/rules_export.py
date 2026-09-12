"""Export/import every manually-authored rule (TecDoc + TS) as one JSON bundle.

A reviewer ruling on a gap value or saving a resolution rule writes straight
to this database and nowhere else. Getting that ruling onto another machine
(a teammate's local DB, CI, a fresh clone) meant running
`scripts/export_resolution_rules.py` / `scripts/export_ts_resolution_rules.py`
by hand, copying the file, then `scripts/load_resolution_rules.py` /
`scripts/load_ts_resolution_rules.py --commit` there. This module is that same
round trip, reused as-is (not reimplemented) so the two paths can never drift,
wired to one button: export downloads the bundle, import applies one back.

Two independent shapes travel in one bundle for convenience only:
`tecdoc_rules` are plain upserts into `core.tecdoc_resolution_rules` (see
`load_resolution_rules.load_rules`); `ts_rules` instead replay each rule
through `MatchReviewService.save_resolution_rule` against *this* database's
own latest completed build, since a TS rule's counts and build reference are
only meaningful there (see `load_ts_resolution_rules.load_rules`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.chunk_repository import MatchReviewRepository
from api.app.features.match_review.chunk_service import MatchReviewService
from api.app.features.match_review.integrations import UnconfiguredOemVinProvider
from ingestion.config import IngestionSettings, get_ingestion_settings
from ingestion.datastores import DatastoreClients
from scripts.export_resolution_rules import export_rules as _export_tecdoc_rules
from scripts.export_ts_resolution_rules import export_rules as _export_ts_rules
from scripts.load_resolution_rules import load_rules as _load_tecdoc_rules
from scripts.load_ts_resolution_rules import load_rules as _load_ts_rules


class NoCompletedBuildError(RuntimeError):
    """No completed TS build exists here yet, so a TS rule has nothing to attach to."""


@dataclass(frozen=True)
class RulesBundle:
    exported_at: str
    tecdoc_rules: list[dict[str, Any]]
    ts_rules: list[dict[str, Any]]


@dataclass(frozen=True)
class RulesImportResult:
    tecdoc_single_target: int
    tecdoc_compatible: int
    ts_created: int
    ts_already_present: int
    ts_skipped_invalid: int
    ts_target_build: str | None
    #: Newly-inserted TS rule ids -- the caller (the import endpoint) schedules
    #: each one's own apply job against just the rows its condition matches,
    #: the same run a reviewer gets by clicking Apply after saving a rule by
    #: hand. A rule already present is left alone: it either already ran or is
    #: mid-run, and re-queuing it would duplicate that work.
    ts_created_rule_ids: tuple[str, ...] = ()


class SettingsFactory(Protocol):
    def __call__(self) -> IngestionSettings: ...


class RulesBundleService:
    """Runs the existing export/load scripts against this database."""

    def __init__(self, settings_factory: SettingsFactory = get_ingestion_settings) -> None:
        self._settings_factory = settings_factory

    def export(self) -> RulesBundle:
        settings = self._settings_factory()
        datastores = DatastoreClients.from_settings(settings)
        with datastores.postgres.connect() as connection:
            tecdoc_rules = _export_tecdoc_rules(connection)
            ts_rules = _export_ts_rules(connection)
        return RulesBundle(
            exported_at=datetime.now(UTC).isoformat(),
            tecdoc_rules=tecdoc_rules,
            ts_rules=ts_rules,
        )

    def import_bundle(
        self, *, tecdoc_rules: list[dict[str, Any]], ts_rules: list[dict[str, Any]]
    ) -> RulesImportResult:
        settings = self._settings_factory()
        datastores = DatastoreClients.from_settings(settings)

        with datastores.postgres.connect() as connection:
            tecdoc_counts = _load_tecdoc_rules(connection, tecdoc_rules, commit=True)

        repository = MatchReviewRepository(datastores.postgres.connect)
        latest_build = repository.fetch_latest_build()
        ts_counts = {"created": 0, "already_present": 0, "skipped_invalid": 0}
        target_build: str | None = None
        if ts_rules:
            if latest_build is None:
                raise NoCompletedBuildError(
                    "No completed TS build exists here yet -- import the TecDoc rules "
                    "on their own, or run a TS build first."
                )
            target_build = str(latest_build["build_id"])
            service = MatchReviewService(
                repository,
                oem_provider=UnconfiguredOemVinProvider(),
                adjudicator=HeuristicAdjudicator(),
            )
            ts_counts = _load_ts_rules(
                service,
                repository,
                ts_rules,
                build_id=latest_build["build_id"],
                commit=True,
            )

        return RulesImportResult(
            tecdoc_single_target=tecdoc_counts["single_target"],
            tecdoc_compatible=tecdoc_counts["compatible"],
            ts_created=ts_counts["created"],
            ts_already_present=ts_counts["already_present"],
            ts_skipped_invalid=ts_counts["skipped_invalid"],
            ts_target_build=target_build,
        )
