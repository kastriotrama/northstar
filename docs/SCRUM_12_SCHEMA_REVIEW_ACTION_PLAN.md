# SCRUM-12 Schema Review Action Plan

## Purpose

This document defines the concrete path for completing
[SCRUM-12](https://northstarmasterdata.atlassian.net/browse/SCRUM-12) through
[PR #11](https://github.com/kastriotrama/northstar/pull/11), while preventing
[PR #12](https://github.com/kastriotrama/northstar/pull/12) from becoming a
second, competing graph-schema specification.

This is an execution and review plan. The canonical node contract remains
`docs/graph-schema-design.md` in PR #11.

## Decision summary

1. Do not merge PR #11 until its Alias identity model and document defects are
   corrected.
2. Do not treat PR #12 as the SCRUM-12 deliverable. Use it only as a temporary
   review artifact and close it after its accepted decisions land in PR #11 and
   Jira.
3. Align SCRUM-37, SCRUM-38, SCRUM-39, and SCRUM-15 with one final Alias
   identity model before declaring Jira aligned.
4. Keep relationship implementation, ID-generation code, Neo4j migrations,
   and merge/split utilities in their owning future stories.
5. Validate the PR #11 merge result against the latest `develop`, not only the
   branch head.

## Current state snapshot

| Item | State | Meaning |
|---|---|---|
| SCRUM-12 | To Do; current sprint | The outstanding node-schema story |
| SCRUM-36 to SCRUM-39 | To Do | The four acceptance-criteria groups for SCRUM-12 |
| PR #11 | Open; CI passing | Contains the actual schema document but needs revision |
| PR #12 | Draft | Contains a review plan, not the schema deliverable |
| SCRUM-13 to SCRUM-15 | To Do | Own deferred relationship, ID, and migration work |
| SCRUM-68 | To Do; outside current open sprint | Own future node merge/split mechanics |

The failed `images` check observed on PR #12 was caused by GitHub Actions
returning `Service Unavailable` while resolving action downloads. It occurred
before repository build steps and should be rerun rather than fixed in source.

## 1. Freeze the merge path

Until the decisions below are applied:

- Keep PR #11 open and request a focused revision.
- Keep PR #12 in draft state.
- Do not mark SCRUM-12 or its subtasks Done.
- Do not begin SCRUM-13, SCRUM-14, SCRUM-15, or SCRUM-68 merely because their
  ownership is referenced in the schema.

## 2. Finalize Alias identity

### Problem in PR #11

PR #11 currently defines Alias uniqueness as:

```text
(alias_type, source_system, alias_text)
```

This can incorrectly merge two independent source assertions that expose the
same normalized text. Engine codes, model names, and other external values are
not guaranteed to be unique within a provider.

### Problem in the first proposed replacement

PR #12 proposes:

```text
(source_system, alias_type, source_record_key, alias_text)
```

This avoids some collisions, but it includes mutable normalized text in the
identity. A normalization change or provider correction could create a second
Alias for the same source assertion instead of updating the existing Alias.

### Recommended Alias contract

| Property | Type | Required | Purpose |
|---|---|---:|---|
| `id` | string | yes | Opaque internal `ALI-<ULID>` identifier |
| `alias_text` | string | yes | Normalized, non-unique lookup value |
| `alias_type` | enum | yes | Meaning of the alias, such as `k_type`, `engine_code`, `plate`, `vin`, or `model_name` |
| `source_system` | enum | yes | Provider that made the assertion |
| `source_record_key` | string | yes* | Stable provider record containing the assertion |
| `source_assertion_key` | string | yes | Stable source-local identity for this individual alias assertion |

\* For `source_system = manual` there is no provider record; use the
assertion key itself (or another synthetic stable value) as
`source_record_key` so the requirement holds without inventing fake
provider records.
| `confidence` | float | yes | Mapping confidence in the range `0.0` to `1.0` |
| `created_at` | datetime | yes | First write timestamp |
| `updated_at` | datetime | yes | Last write timestamp |

Logical uniqueness should be:

```text
(source_system, source_assertion_key)
```

When the provider supplies a stable identifier for an individual assertion,
use it directly. Otherwise derive the assertion key deterministically from
stable source-local components, for example:

```text
<source-record-key>:<field-name>:<value-position>
```

Positional derivation (`<value-position>`) is only valid when the source
guarantees stable value order within a record across dumps. If order is not
guaranteed, positions shift and assertion identity silently changes —
reintroducing the instability this model exists to remove. In that case use
the provider's stable per-value identifier, or a hash of stable value
content, and document the chosen derivation per source in the ingestion
service.

Examples:

```text
tecdoc:vehicle-82931:k_type:0
transportstyrelsen:vehicle-abc123:plate:0
manual:assertion-01ARZ3NDEKTSV4RRFFQ69G5FAV
```

### Alias invariants

- `alias_text` is indexed but not unique.
- External source codes never become canonical node IDs.
- Every Alias has exactly one outgoing `REFERS_TO` relationship.
- Alias does not duplicate the target in a `target_node_id` property.
- Source assertion identity remains stable when normalized text changes.
- Two source assertions may expose identical text without being forced into
  the same Alias node or canonical target.
- A k-type is always an Alias and never a node label or VehicleVariant
  property.

### Minimum alternative

If `source_assertion_key` is deferred, the minimum acceptable identity is:

```text
(source_system, source_record_key, alias_type)
```

This is less flexible when one source record contains multiple values of the
same alias type, so the assertion-key model is preferred.

## 3. Align Jira acceptance criteria

Jira should be updated only after the Alias contract above is accepted.

### SCRUM-37: VehicleVariant and Alias labels

Replace the `external_code` requirement with acceptance criteria that require:

- `alias_text`, `alias_type`, `source_system`, `source_record_key`,
  `source_assertion_key`, and `confidence`;
- non-unique normalized Alias text;
- no `target_node_id` property;
- `REFERS_TO` as the single Alias-to-node mapping;
- no external source value used as an internal node ID.

### SCRUM-38: property types and nullability

Require explicit types and required/nullable behavior for all new Alias
properties. The story must identify immutable identity fields separately from
mutable lookup and confidence fields.

### SCRUM-39: examples and review checklist

Require examples for:

1. two records from the same source exposing identical Alias text but mapping
   to different targets;
2. identical text asserted by two different sources;
3. corrected normalized text retaining the same source assertion identity;
4. a k-type Alias targeting a VehicleVariant;
5. an engine-code Alias targeting an Engine;
6. exactly one live `REFERS_TO` relationship per Alias.

### SCRUM-15: constraints and indexes

Replace the ambiguous external-code index requirement with:

- unique internal IDs for every primary label, including Alias;
- a non-unique lookup index on `Alias.alias_text`;
- lookup indexes on `Alias.alias_type` and `Alias.source_system` as justified
  by query patterns;
- logical uniqueness for `(source_system, source_assertion_key)`;
- idempotent migration execution verified by running migrations twice.

## 4. Revise PR #11 directly

PR #11 is the correct implementation location for SCRUM-12. Its author should
make one focused revision containing the following changes.

### 4.1 Add document control

Add a block near the top of `docs/graph-schema-design.md`:

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Draft node contract |
| Owner | Named NorthStar team or person |
| Jira story | SCRUM-12 |
| Scope | Node labels, properties, examples, and invariants |
| Last reviewed | Current review date |

### 4.2 Remove the unresolved roadmap dependency

The schema currently references `docs/PHASE_1_PLAN.md`, which is absent from
the PR merge result. For the smallest sprint-aligned change, remove the
reference and state:

> Relationship names shown in examples are provisional. SCRUM-13 owns
> relationship names, direction, cardinality, and edge properties and will
> update this canonical document after those decisions are accepted.

Do not pull an entire roadmap into the current schema PR merely to satisfy the
reference.

### 4.3 Apply the accepted Alias contract

Update all of the following together:

- Alias property table;
- identity and uniqueness explanation;
- Alias examples;
- schema review checklist;
- future constraint and index ownership language.

Partial updates are not acceptable because examples and prose could otherwise
contradict the property table.

### 4.4 Correct ID examples and ownership

Use a valid 26-character ULID example, such as:

```text
ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV
```

Short diagram IDs may remain only when explicitly marked illustrative and
invalid for real writes. SCRUM-12 may accept label prefixes, while SCRUM-14
owns the final ULID generation behavior and utility.

### 4.5 Add a decision-ownership table

| Decision | Owner |
|---|---|
| Labels, properties, types, and nullability | SCRUM-12 |
| Alias node semantics and invariants | SCRUM-12 |
| Relationship names, direction, and cardinality | SCRUM-13 |
| ID minting implementation | SCRUM-14 |
| Constraints, indexes, and migrations | SCRUM-15 |
| Merge/split mechanics and `:Superseded` lifecycle | SCRUM-68 |

### 4.6 Add documentation regression protection

Add a dependency-free test that scans local Markdown links under `docs/` and
fails when a referenced repository file does not exist.

## 5. Map PR #11 to the sprint work

| Jira item | Evidence required in PR #11 |
|---|---|
| SCRUM-36 | Manufacturer, ModelFamily, Platform, Engine, Transmission, and BodyType properties, types, nullability, prefixes, and examples |
| SCRUM-37 | VehicleVariant and the corrected Alias contract |
| SCRUM-38 | Explicit type and required/nullable status for every property |
| SCRUM-39 | Representative examples and a complete review checklist |
| SCRUM-12 | All four subtask outcomes combined in one canonical node contract |

No subtask should be marked Done merely because a schema document exists.
Every acceptance criterion must map to a specific section in the revised
document and must be validated in the PR merge result.

## 6. Resolve PR #12 after the revision

Recommended handling:

1. Apply accepted decisions from PR #12 to PR #11 and Jira.
2. Add a final comment to PR #12 linking to the resulting PR #11 commit and
   Jira updates.
3. Close PR #12 without merging it.

This prevents a temporary review plan from becoming a second schema contract.
If a permanent decision trail is required, replace it with a short ADR that
records only the final Alias identity decision and story ownership boundaries.

## 7. Validation and merge gates

Run against the updated PR #11 merge result:

```bash
git diff --check
python -m compileall api ingestion
python -m ruff check .
python -m mypy api ingestion
python -m pytest
```

Also confirm:

- all local Markdown links resolve;
- every property has an explicit type and nullability decision;
- Alias examples satisfy the accepted identity model;
- identical Alias text from independent source assertions is permitted;
- every Alias has one live `REFERS_TO` target;
- no future-story implementation leaked into SCRUM-12;
- both `backend` and `images` GitHub checks pass;
- the latest `develop` merge ref, not only the PR head, was validated.

## 8. Follow-up order

After SCRUM-12 and PR #11 are complete:

1. SCRUM-13 extends the canonical schema with relationships and traversal
   examples.
2. SCRUM-14 finalizes and implements prefixed opaque ID generation.
3. SCRUM-15 adds idempotent Neo4j constraints, indexes, and migrations.
4. SCRUM-68 documents and implements merge/split behavior separately from the
   current sprint merge gate.

## Completion definition

This plan is complete when:

- the Alias identity decision is accepted;
- affected Jira acceptance criteria use the same model;
- PR #11 contains the corrected canonical contract;
- PR #11 passes merge-result validation;
- SCRUM-36 through SCRUM-39 are individually mapped and verified;
- PR #12 is closed or reduced to a short permanent decision record;
- future work remains in its owning story.
