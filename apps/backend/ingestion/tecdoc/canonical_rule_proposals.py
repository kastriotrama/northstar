"""Generate TecDoc normalization rules by scanning the values the release holds.

The TS side learns its rules from its own data: `model_fingerprint_proposals`
and `exact_model_alias_proposals` read what the registry actually contains and
propose rules for what repeats. TecDoc had no equivalent. Its mappings were
written by hand from the key-table documentation, so a value present in the
catalog but absent from the dictionary produced no rule, no error and no
review item -- it simply arrived in the graph unnormalized.

This module closes that by making the release itself the input. It reads every
distinct value TecDoc holds for each registered attribute, resolves it against
the canonical NorthStar vocabulary, and emits one rule per observed value --
including, and especially, the values nothing maps.

The invariant that makes it safe to run unattended: **generation never
accepts.** A rule is `accepted` only when a human-reviewed mapping in
`reference_data` already covers it. Everything the scan discovers is `proposed`,
and an unresolvable value is `proposed` with a null target rather than a guess.
An unmapped value is a finding, not a failure -- it is the coverage gap made
countable, which is the thing the hand-written dictionaries could never report.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from psycopg import Connection

from ingestion.tecdoc import reference_data
from ingestion.tecdoc.canonical_rule_migrations import (
    TECDOC_RULE_VERSIONS_TABLE,
    TECDOC_RULES_TABLE,
)
from ingestion.tecdoc.canonical_vocabulary import canonical_values

RULE_ID_PREFIX = "TD"

#: Promoted TecDoc candidates: the release as this database holds it.
CANDIDATES_TABLE = "core.tecdoc_canonical_candidates"

_SEPARATORS = re.compile(r"[^A-Z0-9]+")


def comparison_key(value: object) -> str:
    """Accent- and punctuation-tolerant key for comparing two spellings.

    Used only to decide whether an observed value and a canonical token are the
    same word. It never becomes the stored term: rules record what TecDoc
    actually holds, so a reviewer sees the catalog's own spelling.
    """

    text = unicodedata.normalize("NFKD", str(value or "").upper())
    stripped = "".join(c for c in text if not unicodedata.combining(c))
    return _SEPARATORS.sub("_", stripped).strip("_")


@dataclass(frozen=True)
class FieldSource:
    """One TecDoc attribute that steers a canonical value, and where it comes from.

    Registering an attribute here is what makes it visible to the generator. A
    TecDoc attribute that reaches the graph without an entry produces no rules
    and no coverage number, which is the original defect -- and it fails
    silently, because an unregistered attribute is indistinguishable from an
    attribute no row carries. Both writers of
    `core.tecdoc_canonical_candidates` (`tecdoc.mapping.candidates_for_row` and
    `tecdoc.canonical_promotion`) must therefore have every canonical-bearing
    attribute they emit listed here, under the exact name they write it with.
    `tests/unit/ingestion/test_tecdoc_canonical_rules.py` checks the names the
    writers actually use against this registry.
    """

    entity_type: str
    source_field: str
    canonical_field: str
    area: str
    key_table: str | None = None
    #: True when the attribute is free text with no closed vocabulary (a model
    #: name). Such a field is scanned and counted but never proposed a target:
    #: there is no canonical list to resolve against, and inventing one from
    #: frequency is how a matcher learns a wrong identity.
    open_vocabulary: bool = False


#: Every TecDoc attribute normalized into the canonical vocabulary.
FIELD_SOURCES: tuple[FieldSource, ...] = (
    FieldSource("engine", "fuel_type", "energy_sources", "fuel", key_table="088"),
    FieldSource("vehicle_variant", "fuel_type", "energy_sources", "fuel", key_table="182"),
    FieldSource("bodywork", "canonical_name", "bodywork_form", "bodywork", key_table="086"),
    FieldSource("vehicle_variant", "drive_type", "drive_type", "drive", key_table="082"),
    # Two writers fill `core.tecdoc_canonical_candidates` and they do not agree
    # on this attribute's name: `tecdoc.mapping.candidates_for_row` writes the
    # raw extract value as `type`, while `tecdoc.canonical_promotion` writes the
    # official KT085 English label as `transmission_type_name`. Registering only
    # one silently reports zero transmission coverage for batches written by the
    # other -- and it was the KT085 label, the one this key_table names, that
    # went unscanned.
    FieldSource("transmission", "type", "transmission_type", "transmission", key_table="085"),
    FieldSource(
        "transmission", "transmission_type_name", "transmission_type", "transmission",
        key_table="085",
    ),
    FieldSource(
        "manufacturer", "canonical_name", "manufacturer", "manufacturer",
        open_vocabulary=True,
    ),
    FieldSource(
        "model_family", "canonical_name", "model_family", "model_family",
        open_vocabulary=True,
    ),
)


@dataclass(frozen=True)
class ObservedValue:
    """One distinct value found in the release, with how many rows carry it."""

    entity_type: str
    source_field: str
    source_term: str
    support: int

    def __post_init__(self) -> None:
        if self.support < 1:
            raise ValueError("an observed value must be carried by at least one row")
        if not self.source_term.strip():
            raise ValueError("an observed value must not be blank")


@dataclass(frozen=True)
class TecDocRule:
    """One generated rule, in the shape `core.tecdoc_rules` stores."""

    rule_id: str
    area: str
    entity_type: str
    source_field: str
    source_term: str
    canonical_field: str
    canonical_value: str | None
    decision: str
    derivation: str
    support: int
    key_table: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision == "accepted" and self.canonical_value is None:
            raise ValueError("an accepted rule must name a canonical value")
        if self.derivation == "generated" and self.support < 1:
            raise ValueError("a generated rule must carry the evidence it came from")

    @property
    def is_open_vocabulary(self) -> bool:
        """True when the field has no closed canonical list to resolve against."""

        return self.evidence.get("reason") == "open_vocabulary"

    @property
    def is_unmapped(self) -> bool:
        """True when nothing resolves this value -- the coverage gap, per row.

        An open-vocabulary value is not counted: a model name has no canonical
        list, so it is untargeted by design rather than uncovered.
        """

        return self.canonical_value is None and not self.is_open_vocabulary


@dataclass(frozen=True)
class GenerationReport:
    """What one scan produced, in the terms a reviewer decides on."""

    rules: tuple[TecDocRule, ...]
    #: Reviewed mappings that matched nothing in the scanned release. Not an
    #: error -- a key table carries labels no vehicle uses -- but a mapping that
    #: stops matching after a repin is how a silent regression looks.
    unused_reviewed_mappings: tuple[tuple[str, str], ...] = ()

    @property
    def accepted(self) -> tuple[TecDocRule, ...]:
        return tuple(r for r in self.rules if r.decision == "accepted")

    @property
    def proposed(self) -> tuple[TecDocRule, ...]:
        return tuple(r for r in self.rules if r.decision == "proposed")

    @property
    def unmapped(self) -> tuple[TecDocRule, ...]:
        return tuple(r for r in self.rules if r.is_unmapped)

    def coverage(self, canonical_field: str | None = None) -> float:
        """Share of scanned rows whose value resolves to a canonical token.

        Weighted by support rather than by distinct value: one unmapped label on
        two million KTypes matters more than forty labels on one row each, and a
        count of distinct values hides exactly that.

        Open-vocabulary fields are excluded from both sides of the ratio. Model
        names have no canonical list, so including them would report a coverage
        gap that no rule could ever close and drag every field's number down
        with the size of the model catalog.
        """

        scope = [
            rule
            for rule in self.rules
            if not rule.is_open_vocabulary
            and (canonical_field is None or rule.canonical_field == canonical_field)
        ]
        total = sum(rule.support for rule in scope)
        if total == 0:
            return 0.0
        resolved = sum(rule.support for rule in scope if not rule.is_unmapped)
        return resolved / total

    def coverage_by_field(self) -> dict[str, float]:
        """Coverage per canonical field, omitting fields where it is undefined.

        `coverage` returns 0.0 when nothing is in scope, which is right for a
        ratio over an empty set but wrong to publish: a field whose every rule
        is open-vocabulary has no measurable coverage, and reporting it as 0.0
        is indistinguishable from a field where nothing maps. Such a field is
        left out rather than given a number that invites the wrong reading.
        """

        measurable = sorted(
            {rule.canonical_field for rule in self.rules if not rule.is_open_vocabulary}
        )
        return {field: self.coverage(field) for field in measurable}


def _rule_id(observation: ObservedValue, canonical_field: str) -> str:
    """A stable id for one observed value, identical across runs and machines.

    Keyed on the term exactly as the catalog spells it, never on its comparison
    key. Distinct spellings routinely share a key -- the 0326 catalog carries
    both "COLT Coupe" and "Colt Coupe", both "164 (164_)" and "164 (164)" -- and
    they are different values a reviewer must be able to rule on separately.
    """

    payload = json.dumps(
        [
            observation.entity_type,
            observation.source_field,
            observation.source_term,
            canonical_field,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"{RULE_ID_PREFIX}:{observation.entity_type}.{observation.source_field}:{digest}"


def rules_fingerprint(rules: Sequence[TecDocRule]) -> str:
    """Content hash over every stored field, ordered by rule id.

    Keyed on content and not on declaration order, so two scans of the same
    release agree, and an edited target changes the hash even though the rule
    count does not.
    """

    payload = [
        [
            rule.rule_id,
            rule.area,
            rule.entity_type,
            rule.source_field,
            rule.source_term,
            rule.canonical_field,
            rule.canonical_value,
            rule.decision,
            rule.derivation,
            rule.support,
            rule.key_table,
            json.dumps(dict(rule.evidence), sort_keys=True, separators=(",", ":")),
        ]
        for rule in sorted(rules, key=lambda rule: rule.rule_id)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def generate_rules(
    observations: Iterable[ObservedValue],
    *,
    field_sources: Sequence[FieldSource] = FIELD_SOURCES,
) -> GenerationReport:
    """Turn every distinct value the release holds into one reviewable rule.

    Four outcomes, in the order they are tried:

    1. a reviewed scalar mapping covers the label      -> accepted
    2. a reviewed *mixed* descriptor covers it         -> proposed, no target
    3. the value already spells a canonical token      -> proposed, that target
    4. nothing covers it                               -> proposed, no target

    Case 2 looks like a resolution and deliberately is not one. A mixed TecDoc
    descriptor names a capability -- "Petrol/Electric" -- and `EngineFuelEvidence`
    is explicit that it is never authority to select one fuel. Emitting it as a
    scalar rule would be the generator quietly overruling a reviewed decision, so
    it is carried as a proposal with its components attached as evidence.
    """

    by_attribute = {(s.entity_type, s.source_field): s for s in field_sources}
    scalar_labels = reference_data.reviewed_engine_fuel_labels()
    mixed_labels = reference_data.reviewed_mixed_engine_fuel_labels()
    bodywork = reference_data.canonical_bodywork_by_kt086()
    drive = reference_data.canonical_drive_by_kt082()

    reviewed_by_field: dict[str, dict[str, str]] = {
        "energy_sources": {comparison_key(k): v for k, v in scalar_labels.items()},
        "bodywork_form": {comparison_key(k): v for k, v in bodywork.items()},
        "drive_type": {comparison_key(k): v for k, v in drive.items()},
    }
    mixed_by_key = {comparison_key(k): v for k, v in mixed_labels.items()}

    rules: list[TecDocRule] = []
    seen: set[tuple[str, str, str, str]] = set()
    matched_reviewed: set[tuple[str, str]] = set()

    for observation in observations:
        source = by_attribute.get((observation.entity_type, observation.source_field))
        if source is None:
            # Not registered, so not something we claim to normalize. Silently
            # skipping is correct here and is exactly why the registry has a
            # completeness test rather than being inferred.
            continue

        key = comparison_key(observation.source_term)
        # Identity is the exact term, not its comparison key. Two spellings that
        # normalize alike are two real values the catalog holds, and each needs
        # its own rule; only the same term arriving twice is a caller error.
        # Keying this on the comparison key rejected the real 0326 catalog
        # outright -- it carries 9,870 distinct values and many collide.
        identity = (
            observation.entity_type,
            observation.source_field,
            observation.source_term,
            source.canonical_field,
        )
        if identity in seen:
            raise ValueError(
                f"duplicate observation for {observation.source_term!r} on "
                f"{observation.entity_type}.{observation.source_field}"
            )
        seen.add(identity)

        canonical_value: str | None = None
        decision = "proposed"
        derivation = "generated"
        evidence: dict[str, object] = {"observed_rows": observation.support}

        reviewed = reviewed_by_field.get(source.canonical_field, {})
        if source.open_vocabulary:
            evidence["reason"] = "open_vocabulary"
            evidence["note"] = (
                "No closed canonical list exists for this field; the value is "
                "counted for coverage but never assigned a target by generation."
            )
        elif (components := mixed_by_key.get(key)) is not None:
            # Mixed is tested before scalar because `engine_fuel_evidence` tests
            # it first, and several labels are in both tables. "Petrol/Electric"
            # is one: the scalar table calls it `hybrid_petrol`, the mixed table
            # decomposes it, and the pipeline takes the mixed reading and refuses
            # a scalar fuel. Testing scalar first here would mint an accepted
            # rule asserting a resolution the pipeline declines to make.
            evidence["reason"] = "mixed_descriptor"
            evidence["components"] = list(components)
            evidence["note"] = (
                "A mixed descriptor names a capability, not the fuel this vehicle "
                "uses. Selecting one component requires a reviewer."
            )
            matched_reviewed.add((source.canonical_field, key))
        elif (mapped := reviewed.get(key)) is not None:
            canonical_value = mapped
            decision = "accepted"
            derivation = "reviewed_mapping"
            evidence["reason"] = "reviewed_mapping"
            matched_reviewed.add((source.canonical_field, key))
        elif key in {comparison_key(v) for v in canonical_values(source.canonical_field)}:
            canonical_value = next(
                v for v in canonical_values(source.canonical_field)
                if comparison_key(v) == key
            )
            evidence["reason"] = "exact_canonical_spelling"
            evidence["note"] = (
                "The catalog value already spells a canonical token. Still a "
                "proposal: matching spellings is not evidence of matching meaning."
            )
        else:
            evidence["reason"] = "unmapped"
            evidence["note"] = (
                "No reviewed mapping and no canonical token covers this value. "
                "It reaches the graph unnormalized until a reviewer rules on it."
            )

        rules.append(
            TecDocRule(
                rule_id=_rule_id(observation, source.canonical_field),
                area=source.area,
                entity_type=observation.entity_type,
                source_field=observation.source_field,
                source_term=observation.source_term,
                canonical_field=source.canonical_field,
                canonical_value=canonical_value,
                decision=decision,
                derivation=derivation,
                support=observation.support,
                key_table=source.key_table,
                evidence=evidence,
            )
        )

    unused = _unused_reviewed_mappings(reviewed_by_field, mixed_by_key, matched_reviewed)
    return GenerationReport(
        rules=tuple(sorted(rules, key=lambda rule: rule.rule_id)),
        unused_reviewed_mappings=unused,
    )


def _unused_reviewed_mappings(
    reviewed_by_field: Mapping[str, Mapping[str, str]],
    mixed_by_key: Mapping[str, tuple[str, ...]],
    matched: set[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Reviewed mappings the scan never hit, as (canonical_field, term key)."""

    unused: list[tuple[str, str]] = []
    for canonical_field, mapping in reviewed_by_field.items():
        unused.extend(
            (canonical_field, key) for key in mapping if (canonical_field, key) not in matched
        )
    unused.extend(
        ("energy_sources", key)
        for key in mixed_by_key
        if ("energy_sources", key) not in matched
    )
    return tuple(sorted(set(unused)))


def scan_observations(
    connection: Connection,
    *,
    batch_id: str,
    field_sources: Sequence[FieldSource] = FIELD_SOURCES,
) -> tuple[ObservedValue, ...]:
    """Read every distinct registered value in one promoted TecDoc batch.

    Aggregates in PostgreSQL rather than streaming rows into Python: a release
    is millions of candidates and only the distinct values are wanted, so the
    row count returned is bounded by vocabulary size, not by catalog size.
    """

    observations: list[ObservedValue] = []
    with connection.cursor() as cursor:
        for source in field_sources:
            cursor.execute(
                f"""
                SELECT attributes ->> %s AS source_term, count(*) AS support
                FROM {CANDIDATES_TABLE}
                WHERE batch_id = %s
                  AND entity_type = %s
                  AND btrim(coalesce(attributes ->> %s, '')) <> ''
                GROUP BY 1
                ORDER BY 2 DESC, 1
                """,
                (source.source_field, batch_id, source.entity_type, source.source_field),
            )
            observations.extend(
                ObservedValue(
                    entity_type=source.entity_type,
                    source_field=source.source_field,
                    source_term=str(row[0]),
                    support=int(row[1]),
                )
                for row in cursor.fetchall()
            )
    return tuple(observations)


_INSERT_COLUMNS = (
    "rule_version, rule_id, area, entity_type, source_field, source_term, key_table, "
    "canonical_field, canonical_value, decision, derivation, support, evidence"
)


class TecDocRuleImportError(RuntimeError):
    """Raised when a generated rule version cannot be stored safely."""


def store_rules(
    connection: Connection,
    rules: Sequence[TecDocRule],
    *,
    rule_version: str,
    tecdoc_release: str,
    generated_by: str,
    source_note: str,
) -> dict[str, int]:
    """Store one generated rule set under a new version and seal it.

    Re-storing an identical set is a no-op, so a scan can be repeated safely. A
    sealed version whose content differs is an error rather than a silent
    divergence: a run pinned to it would otherwise mean two different things.
    Mirrors `rule_definitions.import_rule_set` deliberately -- one set of habits
    should cover both sources.
    """

    if not rules:
        raise TecDocRuleImportError("refusing to store an empty rule set")

    fingerprint = rules_fingerprint(rules)
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {TECDOC_RULE_VERSIONS_TABLE} (rule_version, tecdoc_release, source_note, "
            "generated_by, rule_count, content_fingerprint, sealed) "
            "VALUES (%s, %s, %s, %s, %s, %s, FALSE) "
            "ON CONFLICT (rule_version) DO NOTHING",
            (
                rule_version,
                tecdoc_release,
                source_note,
                generated_by,
                len(rules),
                fingerprint,
            ),
        )
        created = cursor.rowcount == 1

        cursor.execute(
            f"SELECT sealed, content_fingerprint, tecdoc_release FROM {TECDOC_RULE_VERSIONS_TABLE} "
            "WHERE rule_version = %s FOR UPDATE",
            (rule_version,),
        )
        row = cursor.fetchone()
        if row is None:
            raise TecDocRuleImportError("rule version disappeared during import")
        sealed, stored_fingerprint, stored_release = bool(row[0]), str(row[1]), str(row[2])

        if sealed:
            if stored_fingerprint != fingerprint:
                raise TecDocRuleImportError(
                    f"tecdoc rule version {rule_version!r} is sealed with different "
                    f"content (stored {stored_fingerprint[:12]}, incoming "
                    f"{fingerprint[:12]}); generate under a new version instead"
                )
            return {"version_created": 0, "rules_inserted": 0, "sealed": 1}

        if stored_release != tecdoc_release:
            raise TecDocRuleImportError(
                f"tecdoc rule version {rule_version!r} was opened for release "
                f"{stored_release!r} and cannot take rules from {tecdoc_release!r}"
            )

        inserted = 0
        for rule in rules:
            cursor.execute(
                f"INSERT INTO {TECDOC_RULES_TABLE} ({_INSERT_COLUMNS}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (rule_version, rule_id) DO NOTHING",
                (
                    rule_version,
                    rule.rule_id,
                    rule.area,
                    rule.entity_type,
                    rule.source_field,
                    rule.source_term,
                    rule.key_table,
                    rule.canonical_field,
                    rule.canonical_value,
                    rule.decision,
                    rule.derivation,
                    rule.support,
                    json.dumps(dict(rule.evidence)),
                ),
            )
            inserted += cursor.rowcount

        cursor.execute(
            f"UPDATE {TECDOC_RULE_VERSIONS_TABLE} SET sealed = TRUE WHERE rule_version = %s",
            (rule_version,),
        )
    connection.commit()
    return {"version_created": int(created), "rules_inserted": inserted, "sealed": 1}
