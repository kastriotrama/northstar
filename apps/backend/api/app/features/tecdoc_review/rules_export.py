"""Export/import every manually-authored rule (TecDoc + TS) as one JSON bundle.

A reviewer ruling on a gap value or saving a resolution rule writes straight
to this database and nowhere else. Getting that ruling onto another machine
(a teammate's local DB, CI, a fresh clone, or the online deployment) meant
running `scripts/export_resolution_rules.py` / `scripts/export_ts_resolution_rules.py`
by hand, copying the file, then `scripts/load_resolution_rules.py` /
`scripts/load_ts_resolution_rules.py --commit` there. This module is that same
round trip, reused as-is (not reimplemented) so the two paths can never drift,
wired to one pair of buttons: export downloads the bundle, import applies one
back.

Two independent shapes travel in one bundle for convenience only:
`tecdoc_rules` are plain upserts into `core.tecdoc_resolution_rules` (see
`load_resolution_rules.load_rules`), refused per-row when this database's own
copy was edited more recently than the one arriving (`tecdoc_conflicts`
below); `ts_rules` instead replay each rule through
`MatchReviewService.save_resolution_rule` against *this* database's own
latest completed build, since a TS rule's counts and build reference are
only meaningful there, and a TS rule is only ever added, never overwritten
(see `load_ts_resolution_rules.load_rules`) -- so it carries no such conflict.

`pull_from`/`push_to` extend the same round trip across a network instead of
a file: pulling calls another server's own export endpoint and imports the
result here; pushing exports here and calls the other server's own import
endpoint. Both reuse the identical HTTP contract a person clicking Export
then Import by hand would use -- there is no separate "sync protocol", just
this endpoint pair called twice, once in each direction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from api.app.features.match_review.adjudicator import HeuristicAdjudicator
from api.app.features.match_review.chunk_repository import MatchReviewRepository
from api.app.features.match_review.chunk_service import MatchReviewService
from api.app.features.match_review.integrations import UnconfiguredOemVinProvider
from ingestion.config import IngestionSettings, get_ingestion_settings
from ingestion.datastores import DatastoreClients
from scripts.export_resolution_rules import export_rules as _export_tecdoc_rules
from scripts.export_ts_resolution_rules import export_rules as _export_ts_rules
from scripts.load_resolution_rules import load_rules as _load_tecdoc_rules
from scripts.load_ts_resolution_rules import TsRuleLoadResult
from scripts.load_ts_resolution_rules import load_rules as _load_ts_rules

_EXPORT_PATH = "/v1/normalization-review/tecdoc/resolution-rules/export"
_IMPORT_PATH = "/v1/normalization-review/tecdoc/resolution-rules/import"


class NoCompletedBuildError(RuntimeError):
    """No completed TS build exists here yet, so a TS rule has nothing to attach to."""


class RemoteSyncError(RuntimeError):
    """The other server could not be reached, or answered with an error."""


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
    #: TecDoc rows this database's own copy was newer than -- left untouched.
    #: A non-empty list here is the whole point of timestamp-aware sync: a
    #: real edit collision surfaced instead of one side silently winning.
    tecdoc_conflicts: tuple[dict[str, Any], ...] = ()


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
            tecdoc_result = _load_tecdoc_rules(connection, tecdoc_rules, commit=True)

        repository = MatchReviewRepository(datastores.postgres.connect)
        latest_build = repository.fetch_latest_build()
        ts_result = TsRuleLoadResult(
            created=0, already_present=0, skipped_invalid=0, created_rule_ids=()
        )
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
            ts_result = _load_ts_rules(
                service,
                repository,
                ts_rules,
                build_id=latest_build["build_id"],
                commit=True,
            )

        return RulesImportResult(
            tecdoc_single_target=tecdoc_result.single_target,
            tecdoc_compatible=tecdoc_result.compatible,
            ts_created=ts_result.created,
            ts_already_present=ts_result.already_present,
            ts_skipped_invalid=ts_result.skipped_invalid,
            ts_target_build=target_build,
            ts_created_rule_ids=tuple(str(rid) for rid in ts_result.created_rule_ids),
            tecdoc_conflicts=tecdoc_result.conflicts,
        )

    def pull_from(self, live_base_url: str, token: str | None = None) -> RulesImportResult:
        """Fetch the other server's bundle and import it here."""

        payload = _get_json(live_base_url, _EXPORT_PATH, token=token)
        return self.import_bundle(
            tecdoc_rules=payload.get("tecdoc_rules", []),
            ts_rules=payload.get("ts_rules", []),
        )

    def push_to(self, live_base_url: str, token: str | None = None) -> RulesImportResult:
        """Export this database's bundle and hand it to the other server's own
        import endpoint -- the conflict check runs *there*, against *its* data,
        exactly as it would if that server imported a file pulled from here."""

        bundle = self.export()
        result = _post_json(
            live_base_url,
            _IMPORT_PATH,
            {
                "tecdoc_rules": _jsonable_rows(bundle.tecdoc_rules),
                "ts_rules": bundle.ts_rules,
            },
            token=token,
        )
        return RulesImportResult(
            tecdoc_single_target=result.get("tecdoc_single_target", 0),
            tecdoc_compatible=result.get("tecdoc_compatible", 0),
            ts_created=result.get("ts_created", 0),
            ts_already_present=result.get("ts_already_present", 0),
            ts_skipped_invalid=result.get("ts_skipped_invalid", 0),
            ts_target_build=result.get("ts_target_build"),
            ts_created_rule_ids=tuple(result.get("ts_rules_queued_for_apply", [])),
            tecdoc_conflicts=tuple(result.get("tecdoc_conflicts", [])),
        )


def _jsonable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`export_rules` returns `updated_at` as a real `datetime` for the
    in-process import path; a network hop needs it as the same ISO string
    every other consumer (a downloaded file, a browser response) already
    gets -- plain `json.dumps` cannot encode a `datetime` on its own."""

    return [
        {key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in row.items()}
        for row in rows
    ]


def _token_headers(token: str | None) -> dict[str, str]:
    return {"X-Rules-Sync-Token": token} if token else {}


def _get_json(base_url: str, path: str, *, token: str | None = None) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    try:
        response = httpx.get(url, headers=_token_headers(token), timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise RemoteSyncError(f"Could not reach {url}: {error}") from error
    payload = response.json()
    if not isinstance(payload, dict):
        raise RemoteSyncError(f"{url} returned {type(payload).__name__}, expected a JSON object")
    return payload


def _post_json(
    base_url: str, path: str, body: dict[str, Any], *, token: str | None = None
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    try:
        response = httpx.post(url, json=body, headers=_token_headers(token), timeout=60.0)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise RemoteSyncError(f"Could not reach {url}: {error}") from error
    payload = response.json()
    if not isinstance(payload, dict):
        raise RemoteSyncError(f"{url} returned {type(payload).__name__}, expected a JSON object")
    return payload
