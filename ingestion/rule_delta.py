"""Deterministic export of immutable normalization-rule versions as guarded SQL."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from psycopg import Connection

from ingestion.normalization_migrations import TRANSLATION_RULE_VERSIONS_TABLE


class RuleDeltaError(ValueError):
    """Raised when two immutable rule versions cannot form a safe SQL delta."""


@dataclass(frozen=True)
class RuleVersionSnapshot:
    version: str
    base_rule_version: str
    overrides: dict[str, dict[str, Any]]
    activation_note: str
    activated_at: datetime


@dataclass(frozen=True)
class RuleDeltaExportSummary:
    baseline_version: str
    target_version: str
    base_rule_version: str
    total_overrides: int
    delta_definitions: int
    target_checksum: str
    output_path: str


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _dollar_quoted(value: str, *, label: str) -> str:
    suffix = ""
    while True:
        tag = f"$northstar_{label}{suffix}$"
        if tag not in value:
            return f"{tag}{value}{tag}"
        suffix = f"_{len(suffix) + 1}"


def compute_rule_delta(
    baseline: RuleVersionSnapshot,
    target: RuleVersionSnapshot,
) -> dict[str, dict[str, Any]]:
    """Return every added or changed definition needed to reproduce target."""

    if baseline.version == target.version:
        raise RuleDeltaError("baseline_and_target_versions_must_differ")
    if baseline.base_rule_version != target.base_rule_version:
        raise RuleDeltaError("base_rule_versions_do_not_match")
    removed = set(baseline.overrides).difference(target.overrides)
    if removed:
        raise RuleDeltaError("target_rule_version_removes_existing_definitions")
    delta = {
        rule_id: definition
        for rule_id, definition in target.overrides.items()
        if baseline.overrides.get(rule_id) != definition
    }
    if not delta:
        raise RuleDeltaError("target_rule_version_has_no_changes")
    reproduced = {**baseline.overrides, **delta}
    if reproduced != target.overrides:
        raise RuleDeltaError("rule_delta_does_not_reproduce_target")
    return delta


def render_rule_delta_sql(
    baseline: RuleVersionSnapshot,
    target: RuleVersionSnapshot,
) -> str:
    """Render an idempotent SQL artifact with exact-content safety checks."""

    delta = compute_rule_delta(baseline, target)
    delta_json = _canonical_json(delta)
    target_json = _canonical_json(target.overrides)
    checksum = sha256(target_json.encode()).hexdigest()
    baseline_literal = _sql_literal(baseline.version)
    target_literal = _sql_literal(target.version)
    base_literal = _sql_literal(target.base_rule_version)
    note_literal = _sql_literal(target.activation_note)
    activated_literal = _sql_literal(target.activated_at.isoformat())
    delta_value = _dollar_quoted(delta_json, label="delta")

    return f"""-- NorthStar normalization reviewed-rule delta
-- Generated deterministically from immutable database versions.
-- Baseline: {baseline.version}
-- Target: {target.version}
-- Base catalog: {target.base_rule_version}
-- Delta definitions: {len(delta)}
-- Target overrides: {len(target.overrides)}
-- Target SHA-256: {checksum}
-- Apply only to local, CI, or explicitly approved environments; never production by default.

\\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE {TRANSLATION_RULE_VERSIONS_TABLE} IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_rules$
DECLARE
    baseline_version CONSTANT text := {baseline_literal};
    target_version CONSTANT text := {target_literal};
    expected_base_version CONSTANT text := {base_literal};
    expected_activation_note CONSTANT text := {note_literal};
    expected_activated_at CONSTANT timestamptz := {activated_literal};
    delta CONSTANT jsonb := {delta_value}::jsonb;
    baseline_base text;
    baseline_overrides jsonb;
    current_latest text;
    existing_base text;
    existing_overrides jsonb;
    existing_note text;
    existing_activated_at timestamptz;
    expected_overrides jsonb;
BEGIN
    SELECT base_rule_version, overrides
    INTO baseline_base, baseline_overrides
    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
    WHERE version = baseline_version;

    IF baseline_overrides IS NULL THEN
        RAISE EXCEPTION 'Required baseline rule version % is missing', baseline_version;
    END IF;
    IF baseline_base <> expected_base_version THEN
        RAISE EXCEPTION 'Baseline catalog mismatch: expected %, found %',
            expected_base_version, baseline_base;
    END IF;

    expected_overrides := baseline_overrides || delta;

    SELECT base_rule_version, overrides, activation_note, activated_at
    INTO existing_base, existing_overrides, existing_note, existing_activated_at
    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
    WHERE version = target_version;

    IF existing_overrides IS NOT NULL THEN
        IF existing_base <> expected_base_version
           OR existing_overrides IS DISTINCT FROM expected_overrides
           OR existing_note IS DISTINCT FROM expected_activation_note
           OR existing_activated_at IS DISTINCT FROM expected_activated_at THEN
            RAISE EXCEPTION 'Target version % exists with conflicting content', target_version;
        END IF;
        RAISE NOTICE 'Target version % is already installed and verified', target_version;
        RETURN;
    END IF;

    SELECT version INTO current_latest
    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
    ORDER BY activated_at DESC, version DESC
    LIMIT 1;

    IF current_latest <> baseline_version THEN
        RAISE EXCEPTION 'Refusing activation: expected latest version %, found %',
            baseline_version, current_latest;
    END IF;

    INSERT INTO {TRANSLATION_RULE_VERSIONS_TABLE} (
        version,
        base_rule_version,
        overrides,
        activation_note,
        activated_at
    ) VALUES (
        target_version,
        expected_base_version,
        expected_overrides,
        expected_activation_note,
        expected_activated_at
    );
END
$northstar_rules$;

COMMIT;

WITH baseline AS (
    SELECT overrides
    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
    WHERE version = {baseline_literal}
), target AS (
    SELECT version, base_rule_version, overrides, activation_note, activated_at
    FROM {TRANSLATION_RULE_VERSIONS_TABLE}
    WHERE version = {target_literal}
)
SELECT
    target.version,
    target.base_rule_version,
    (SELECT count(*) FROM jsonb_object_keys(target.overrides)) AS total_overrides,
    count(changed.key) AS exported_delta_definitions,
    target.activated_at
FROM target
CROSS JOIN baseline
CROSS JOIN LATERAL jsonb_each(target.overrides) AS changed
WHERE baseline.overrides->changed.key IS DISTINCT FROM changed.value
GROUP BY target.version, target.base_rule_version, target.overrides,
         target.activation_note, target.activated_at;
"""


def fetch_rule_version(
    connection: Connection[Any],
    version: str | None,
) -> RuleVersionSnapshot:
    """Fetch one named version, or the latest active version when omitted."""

    query = (
        f"SELECT version, base_rule_version, overrides, activation_note, activated_at "
        f"FROM {TRANSLATION_RULE_VERSIONS_TABLE} "
    )
    parameters: tuple[str, ...] = ()
    if version is None:
        query += "ORDER BY activated_at DESC, version DESC LIMIT 1"
    else:
        query += "WHERE version = %s"
        parameters = (version.strip(),)
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        row = cursor.fetchone()
    if row is None:
        raise RuleDeltaError("rule_version_not_found")
    return RuleVersionSnapshot(
        version=str(row[0]),
        base_rule_version=str(row[1]),
        overrides={str(key): dict(value) for key, value in dict(row[2]).items()},
        activation_note=str(row[3]),
        activated_at=row[4],
    )


def export_rule_delta(
    connection: Connection[Any],
    *,
    baseline_version: str,
    target_version: str | None,
    output_path: Path,
) -> RuleDeltaExportSummary:
    """Export a deterministic baseline-to-target SQL artifact."""

    if output_path.suffix.lower() != ".sql":
        raise RuleDeltaError("rule_delta_output_must_be_sql")
    baseline = fetch_rule_version(connection, baseline_version)
    target = fetch_rule_version(connection, target_version)
    sql = render_rule_delta_sql(baseline, target)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sql, encoding="utf-8")
    checksum = sha256(_canonical_json(target.overrides).encode()).hexdigest()
    delta = compute_rule_delta(baseline, target)
    return RuleDeltaExportSummary(
        baseline_version=baseline.version,
        target_version=target.version,
        base_rule_version=target.base_rule_version,
        total_overrides=len(target.overrides),
        delta_definitions=len(delta),
        target_checksum=checksum,
        output_path=str(output_path),
    )
