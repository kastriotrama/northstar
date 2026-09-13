"""Propose a canonical target for a TecDoc value nothing has mapped yet.

The generator states the problem but cannot solve it: `generate_rules` emits a
rule for every distinct value the catalog holds and resolves it against the
reviewed mappings, so a value nobody has mapped arrives with `canonical_value`
NULL and stays there until a person rules on it. That is correct -- generation
"never accepts a rule it did not read from a reviewed mapping" -- and it is also
why the gap list only ever shrinks at human speed.

This module fills the step in between, in the shape `match_review.adjudicator`
already established: a `Protocol`, a deterministic implementation, and a model
behind the same interface whose output is validated rather than trusted.

Two kinds of answer, and the difference matters more than the answer:

* **reuse** -- the vocabulary already holds this term and the value is merely
  spelled differently. `canonical_vocabulary.canonical_values` is the oracle,
  because it assembles terms from every source that feeds the graph rather than
  from TS alone, and `comparison_key` decides sameness the same way the
  generator does. This is decidable without a model and carries no invention.
* **mint** -- no existing term fits, so a new one is proposed. This is the only
  place judgment enters, and the proposal is checked against the convention the
  field's own existing values follow before it is allowed to be stored.

Nothing here writes a canonical term into the vocabulary. A suggestion is a row
in `tecdoc_gap_suggestions`; promoting one into a live ruling is a separate,
recorded act.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from psycopg import Connection

from ingestion.tecdoc import reference_data
from ingestion.tecdoc.canonical_rule_migrations import (
    TECDOC_RULE_VERSIONS_TABLE,
    TECDOC_RULES_TABLE,
)
from ingestion.tecdoc.canonical_rule_proposals import comparison_key
from ingestion.tecdoc.canonical_vocabulary import canonical_values
from ingestion.tecdoc.gap_suggestion_migrations import TECDOC_GAP_SUGGESTIONS_TABLE
from ingestion.tecdoc.resolution_migrations import TECDOC_RESOLUTION_RULES_TABLE

#: `manufacturer` and `model_family` are open vocabularies -- a release simply
#: brings new model names, and no target exists to resolve them to. They are
#: excluded here for the same reason the review screen hides them by default.
OPEN_VOCABULARY_FIELDS = frozenset({"manufacturer", "model_family"})


class SuggestionError(ValueError):
    """A suggestion was refused before it could be stored."""


@dataclass(frozen=True)
class GapValue:
    """One unmapped value, as the sealed rule set records it."""

    canonical_field: str
    source_term: str
    key_table: str | None
    support: int

    @property
    def comparison_key(self) -> str:
        return comparison_key(self.source_term)


@dataclass(frozen=True)
class Suggestion:
    """A proposed answer for one gap, with the reasoning that produced it."""

    canonical_field: str
    source_term: str
    key_table: str | None
    decision: str
    suggested_value: str | None
    origin: str
    rationale: str
    convention: str
    confidence: float
    support: int
    suggested_by: str

    @property
    def comparison_key(self) -> str:
        return comparison_key(self.source_term)


class GapSuggester(Protocol):
    """One gap in, at most one proposal out."""

    @property
    def version(self) -> str: ...

    def suggest(self, gap: GapValue) -> Suggestion | None: ...


def blocked_reason(canonical_field: str, source_term: str) -> str | None:
    """Why this value must not be answered with a single target, if any.

    The same refusal `tecdoc_review.gaps` applies at the API boundary, restated
    here rather than imported: `ingestion` is the lower layer and must not
    depend on `api`. Both read the one source of truth, `reference_data`.

    A mixed descriptor -- "Petrol/Electric" -- names a capability, not the fuel
    a vehicle uses. Suggesting a scalar target for it would be proposing exactly
    what the pipeline already refuses to infer.
    """

    if canonical_field != "energy_sources":
        return None
    mixed = {comparison_key(label) for label in reference_data.reviewed_mixed_engine_fuel_labels()}
    return "mixed_descriptor" if comparison_key(source_term) in mixed else None


def naming_convention(values: Sequence[str]) -> str:
    """Describe, in one line, the shape a field's existing values share.

    Derived rather than declared, because the convention differs per field and
    the vocabulary is the only honest record of it: `energy_sources` is
    lowercase snake (`hybrid_petrol`), `drive_type` is a bare lowercase token
    (`fwd`). A minted term is checked against this before it may be stored, so a
    model cannot introduce a spelling the field has never used.
    """

    if not values:
        return "no existing values; convention cannot be derived"
    lowered = all(value == value.lower() for value in values)
    snake = any("_" in value for value in values)
    spaced = any(" " in value for value in values)
    parts = ["lowercase" if lowered else "mixed case"]
    if snake:
        parts.append("underscore-separated")
    elif spaced:
        parts.append("space-separated")
    else:
        parts.append("single token")
    longest = max(len(value) for value in values)
    parts.append(f"longest existing {longest} chars")
    return ", ".join(parts)


def conforms(value: str, existing: Sequence[str]) -> bool:
    """Does a minted term match the shape the field's own values use?

    Everything past the model boundary is untrusted input, so a proposal is
    validated the way `adjudicator` validates its own: against a rule derived
    from data already in hand, not against the model's claim about itself.
    """

    if not value or value != value.strip():
        return False
    if not existing:
        return bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", value))
    if all(item == item.lower() for item in existing) and value != value.lower():
        return False
    separators = {"_" if "_" in item else " " if " " in item else "" for item in existing}
    if separators == {"_"}:
        return bool(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", value))
    if separators == {""}:
        return bool(re.fullmatch(r"[a-z0-9]+", value))
    return bool(re.fullmatch(r"[A-Za-z0-9]+(?:[ _][A-Za-z0-9]+)*", value))


class ReuseSuggester:
    """Answer a gap only when the vocabulary already holds the term.

    Decidable without a model: two spellings are the same value when they share
    a `comparison_key`. Confidence is 1.0 because this is an identity claim, not
    an estimate -- if it is wrong, the key function is wrong, and every rule the
    generator ever resolved is wrong with it.
    """

    @property
    def version(self) -> str:
        return "reuse-v1"

    def suggest(self, gap: GapValue) -> Suggestion | None:
        if gap.canonical_field in OPEN_VOCABULARY_FIELDS:
            return None
        if blocked_reason(gap.canonical_field, gap.source_term):
            return None
        existing = canonical_values(gap.canonical_field)
        wanted = gap.comparison_key
        for value in existing:
            if comparison_key(value) != wanted:
                continue
            return Suggestion(
                canonical_field=gap.canonical_field,
                source_term=gap.source_term,
                key_table=gap.key_table,
                decision="accepted",
                suggested_value=value,
                origin="reuse",
                rationale=(
                    f"'{gap.source_term}' and the existing canonical term '{value}' share "
                    f"comparison key '{wanted}', so they are the same value spelled "
                    "differently. No new vocabulary is introduced."
                ),
                convention=naming_convention(existing),
                confidence=1.0,
                support=gap.support,
                suggested_by=self.version,
            )
        return None


def field_context(canonical_field: str) -> dict[str, Any]:
    """What a suggester needs to answer a gap in this field.

    Handed to whatever does the minting -- a model, or a person reading the
    loop's output -- so the proposal is anchored in the field's own vocabulary
    rather than in general knowledge about cars.
    """

    existing = canonical_values(canonical_field)
    return {
        "canonical_field": canonical_field,
        "existing_values": list(existing),
        "convention": naming_convention(existing),
        "open_vocabulary": canonical_field in OPEN_VOCABULARY_FIELDS,
    }


def newest_sealed_version(connection: Connection) -> str | None:
    """The rule version a gap list should be read from, or None.

    Only a sealed version, for the reason `fetch_tecdoc_rules` gives: an
    unsealed one is a generation still in progress, and suggesting against rules
    that are still moving produces proposals about values that may not survive.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT rule_version FROM {TECDOC_RULE_VERSIONS_TABLE} "
            "WHERE sealed IS TRUE ORDER BY generated_at DESC, rule_version DESC LIMIT 1"
        )
        row = cursor.fetchone()
    return str(row[0]) if row else None


def read_open_gaps(
    connection: Connection,
    *,
    rule_version: str,
    limit: int = 100,
    include_suggested: bool = False,
) -> tuple[GapValue, ...]:
    """Unmapped values still waiting for an answer, highest impact first.

    Excludes what a reviewer has already ruled and, by default, what has already
    been suggested -- so a loop that runs repeatedly converges instead of
    re-proposing the same values every pass.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT canonical_field, source_term, key_table, support
            FROM {TECDOC_RULES_TABLE}
            WHERE rule_version = %s
              AND canonical_value IS NULL
              AND canonical_field <> ALL(%s)
            ORDER BY support DESC, source_term
            """,
            (rule_version, sorted(OPEN_VOCABULARY_FIELDS)),
        )
        rows = cursor.fetchall()

    gaps: list[GapValue] = []
    ruled = _ruled_keys(connection)
    suggested = set() if include_suggested else _suggested_keys(connection)
    for canonical_field, source_term, key_table, support in rows:
        key = (str(canonical_field), comparison_key(source_term))
        if key in ruled or key in suggested:
            continue
        gaps.append(
            GapValue(
                canonical_field=str(canonical_field),
                source_term=str(source_term),
                key_table=str(key_table) if key_table is not None else None,
                support=int(support),
            )
        )
        if len(gaps) >= limit:
            break
    return tuple(gaps)


def canonical_field_candidates() -> tuple[str, ...]:
    """Fields a gap may be suggested for at all."""

    from ingestion.tecdoc.canonical_rule_proposals import FIELD_SOURCES

    return tuple(sorted({source.canonical_field for source in FIELD_SOURCES}))


def _ruled_keys(connection: Connection) -> set[tuple[str, str]]:
    # The ruling table also holds TS-side vocabulary rows since it replaced
    # `vocabulary_alignments`; a TS ruling says nothing about a TecDoc gap.
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT canonical_field, comparison_key FROM {TECDOC_RESOLUTION_RULES_TABLE} "
            "WHERE source_system = 'tecdoc'"
        )
        return {(str(field), str(key)) for field, key in cursor.fetchall()}


def _suggested_keys(connection: Connection) -> set[tuple[str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT canonical_field, comparison_key FROM {TECDOC_GAP_SUGGESTIONS_TABLE}"
        )
        return {(str(field), str(key)) for field, key in cursor.fetchall()}


def validate(suggestion: Suggestion) -> None:
    """Refuse a proposal that must not be stored, before it is stored.

    The checks a database constraint cannot express: that an open vocabulary is
    not being closed, that a refused value is not being answered anyway, and
    that a minted term follows the field's own convention.
    """

    if suggestion.canonical_field in OPEN_VOCABULARY_FIELDS:
        raise SuggestionError(
            f"{suggestion.canonical_field} is an open vocabulary; it has no target to suggest"
        )
    reason = blocked_reason(suggestion.canonical_field, suggestion.source_term)
    if reason:
        raise SuggestionError(
            f"'{suggestion.source_term}' is refused as {reason}; it names a capability, "
            "not a single canonical value"
        )
    if suggestion.origin not in ("reuse", "mint", "exclude"):
        raise SuggestionError(f"unknown origin {suggestion.origin!r}")
    if not 0.0 <= suggestion.confidence <= 1.0:
        raise SuggestionError("confidence must be between 0.0 and 1.0")
    if suggestion.decision == "accepted" and not suggestion.suggested_value:
        raise SuggestionError("an accepted suggestion must name a value")
    if suggestion.decision == "excluded" and suggestion.suggested_value:
        raise SuggestionError("an excluded suggestion must not name a value")
    if (suggestion.origin == "exclude") != (suggestion.decision == "excluded"):
        raise SuggestionError(
            "origin 'exclude' and decision 'excluded' are the same claim; set both or neither"
        )
    if suggestion.origin == "exclude":
        return

    existing = canonical_values(suggestion.canonical_field)
    if suggestion.origin == "reuse":
        if suggestion.suggested_value not in existing:
            raise SuggestionError(
                f"'{suggestion.suggested_value}' is not in the {suggestion.canonical_field} "
                "vocabulary, so it cannot be a reuse; propose it as a mint instead"
            )
        return
    if suggestion.suggested_value is None:
        return
    if suggestion.suggested_value in existing:
        raise SuggestionError(
            f"'{suggestion.suggested_value}' already exists; record it as a reuse"
        )
    if not conforms(suggestion.suggested_value, existing):
        raise SuggestionError(
            f"'{suggestion.suggested_value}' does not follow the {suggestion.canonical_field} "
            f"convention ({naming_convention(existing)})"
        )


def store_suggestion(
    connection: Connection,
    suggestion: Suggestion,
    *,
    auto_apply_at: float | None = None,
    applied_by: str | None = None,
) -> dict[str, Any]:
    """Record a proposal, and apply it when it clears the confidence bar.

    Applying writes the ruling table, which is what "live on TecDoc" counts.
    That row stays attributable -- `reviewed_by` names the agent, `note` carries
    the rationale -- so every value an agent put into the graph can be listed,
    and withdrawn, with one query.
    """

    validate(suggestion)
    applied = (
        auto_apply_at is not None
        and suggestion.confidence >= auto_apply_at
        and applied_by is not None
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {TECDOC_GAP_SUGGESTIONS_TABLE} (
                    canonical_field, comparison_key, source_term, key_table, decision,
                    suggested_value, origin, rationale, convention, confidence, support,
                    suggested_by, applied_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN now() ELSE NULL END)
                ON CONFLICT (canonical_field, comparison_key) DO UPDATE SET
                    source_term = EXCLUDED.source_term,
                    decision = EXCLUDED.decision,
                    suggested_value = EXCLUDED.suggested_value,
                    origin = EXCLUDED.origin,
                    rationale = EXCLUDED.rationale,
                    convention = EXCLUDED.convention,
                    confidence = EXCLUDED.confidence,
                    support = EXCLUDED.support,
                    suggested_by = EXCLUDED.suggested_by,
                    updated_at = now(),
                    applied_at = COALESCE(
                        {TECDOC_GAP_SUGGESTIONS_TABLE}.applied_at, EXCLUDED.applied_at
                    )
                """,
                (
                    suggestion.canonical_field,
                    suggestion.comparison_key,
                    suggestion.source_term,
                    suggestion.key_table,
                    suggestion.decision,
                    suggestion.suggested_value,
                    suggestion.origin,
                    suggestion.rationale,
                    suggestion.convention,
                    suggestion.confidence,
                    suggestion.support,
                    suggestion.suggested_by,
                    applied,
                ),
            )
            if applied:
                cursor.execute(
                    f"""
                    INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE} (
                        canonical_field, source_system, comparison_key, source_term, key_table,
                        decision, canonical_value, relation, note, reviewed_by
                    )
                    VALUES (%s, 'tecdoc', %s, %s, %s, %s, %s, 'equivalent', %s, %s)
                    ON CONFLICT (canonical_field, source_system, comparison_key)
                        WHERE relation <> 'compatible' DO NOTHING
                    """,
                    (
                        suggestion.canonical_field,
                        suggestion.comparison_key,
                        suggestion.source_term,
                        suggestion.key_table,
                        suggestion.decision,
                        suggestion.suggested_value,
                        f"[{suggestion.origin} @ {suggestion.confidence:.2f}] "
                        f"{suggestion.rationale}",
                        applied_by,
                    ),
                )
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return {
        "canonical_field": suggestion.canonical_field,
        "source_term": suggestion.source_term,
        "suggested_value": suggestion.suggested_value,
        "origin": suggestion.origin,
        "confidence": suggestion.confidence,
        "applied": bool(applied),
    }


def suggest_all(
    gaps: Iterable[GapValue], suggesters: Sequence[GapSuggester]
) -> tuple[tuple[GapValue, Suggestion | None], ...]:
    """Run each gap past the suggesters in order, first answer wins."""

    answered: list[tuple[GapValue, Suggestion | None]] = []
    for gap in gaps:
        proposal: Suggestion | None = None
        for suggester in suggesters:
            proposal = suggester.suggest(gap)
            if proposal is not None:
                break
        answered.append((gap, proposal))
    return tuple(answered)


def apply_pending(
    connection: Connection,
    *,
    at_least: float,
    applied_by: str,
    limit: int = 100,
) -> tuple[dict[str, Any], ...]:
    """Promote already-recorded suggestions at or above a confidence into rulings.

    Separate from `store_suggestion` because promoting is its own act: a
    suggestion may be recorded under one confidence bar and promoted later under
    another, once a reviewer has seen how the earlier ones turned out. Rows
    already applied are skipped rather than rewritten, so the timestamp keeps
    saying when the value actually went live.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT canonical_field, comparison_key, source_term, key_table,
                   decision, suggested_value, origin, rationale, confidence
            FROM {TECDOC_GAP_SUGGESTIONS_TABLE}
            WHERE applied_at IS NULL AND confidence >= %s
            ORDER BY confidence DESC, support DESC
            LIMIT %s
            """,
            (at_least, limit),
        )
        rows = cursor.fetchall()

    promoted: list[dict[str, Any]] = []
    try:
        with connection.cursor() as cursor:
            for (
                canonical_field,
                key,
                source_term,
                key_table,
                decision,
                suggested_value,
                origin,
                rationale,
                confidence,
            ) in rows:
                cursor.execute(
                    f"""
                    INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE} (
                        canonical_field, source_system, comparison_key, source_term, key_table,
                        decision, canonical_value, relation, note, reviewed_by
                    )
                    VALUES (%s, 'tecdoc', %s, %s, %s, %s, %s, 'equivalent', %s, %s)
                    ON CONFLICT (canonical_field, source_system, comparison_key)
                        WHERE relation <> 'compatible' DO NOTHING
                    """,
                    (
                        canonical_field,
                        key,
                        source_term,
                        key_table,
                        decision,
                        suggested_value,
                        f"[{origin} @ {float(confidence):.2f}] {rationale}",
                        applied_by,
                    ),
                )
                cursor.execute(
                    f"""
                    UPDATE {TECDOC_GAP_SUGGESTIONS_TABLE}
                    SET applied_at = now(), updated_at = now()
                    WHERE canonical_field = %s AND comparison_key = %s
                    """,
                    (canonical_field, key),
                )
                promoted.append(
                    {
                        "canonical_field": str(canonical_field),
                        "source_term": str(source_term),
                        "canonical_value": suggested_value,
                        "origin": str(origin),
                        "confidence": float(confidence),
                    }
                )
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return tuple(promoted)
