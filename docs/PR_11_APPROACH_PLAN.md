# PR #11 Approach Plan

## Recommendation

Keep PR #11 open, request one focused revision, and merge it only as the
SCRUM-12 node-schema contract. Do not expand it into relationship
implementation, ID utilities, or Neo4j migrations; those belong to
SCRUM-13, SCRUM-14, and SCRUM-15. Merge/split mechanics belong to SCRUM-68.

## Status (updated 2026-07-13)

Jira has been aligned with this plan and the Phase 1 plan:

- SCRUM-68 (Story 2.5 - node merge & split strategy) created with subtasks
  SCRUM-69 to SCRUM-72; it owns the merge/split mechanics this document and
  the schema doc reference.
- SCRUM-42 restored to the six core query patterns (was three) with the
  3-hops-from-an-indexed-entry-point criterion.
- SCRUM-44 prefix list completed to all eight labels (FAM and TRN added).

Open items: the PR #11 revision itself (sections 1-2 below) and the
`PHASE_1_PLAN.md` decision in section 1.B.

## 1. Required changes in PR #11

### A. Add document control

Add a small block near the top of `docs/graph-schema-design.md`:

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Draft node contract |
| Owner | Named NorthStar team or person |
| Jira story | SCRUM-12 |
| Last reviewed | Current review date |
| Scope | Node labels, properties, and invariants only |

This makes it clear that the document is canonical but incomplete until later
Epic 2 stories extend it.

### B. Fix the broken plan reference (decision required)

The schema document references `docs/PHASE_1_PLAN.md`, which is not committed
to the repository, so the link is broken in PR #11. The document does exist:
it sits untracked in the working tree and is the full Phase 1 roadmap the
schema was derived from. Two valid fixes — pick one:

1. **Commit `docs/PHASE_1_PLAN.md`** (in its own small PR or alongside this
   revision). The reference becomes valid and the roadmap gains repo
   visibility. This is not "creating a document to satisfy a reference" —
   the document predates the reference and is actively used for planning.
2. **Remove the reference** and replace it with explicit ownership language:

   > Relationship names used in examples are provisional. SCRUM-13 owns
   > relationship names, direction, cardinality, and edge properties and will
   > update this canonical document when those decisions are accepted.

Either way, do not leave the dead link, and do not write a new planning
document from scratch.

### C. Correct the alias identity model

This is the merge-blocking data-integrity issue.

Remove the current uniqueness rule:

```text
(alias_type, source_system, alias_text)
```

The same engine code or model name can legitimately occur more than once
within a source.

Use the following model instead:

- Keep `alias_text` as a normalized, non-unique lookup value.
- Rename or replace `external_code` with required `source_record_key`.
- Define logical uniqueness as:

  ```text
  (source_system, alias_type, source_record_key, alias_text)
  ```

- When the provider has a stable identifier, use it as `source_record_key`.
- For manual assertions, mint a source-local assertion key.
- Continue requiring each Alias node to have exactly one outgoing
  `REFERS_TO` relationship.
- State that the exact Neo4j constraint and lookup indexes are owned by
  SCRUM-15.

This permits two source records to expose the same text without forcing them
to resolve to the same canonical node.

### D. Clarify and correct the ID decision

Separate the two decisions:

- Prefixes such as `ENG-` and `VEH-`: accepted by SCRUM-12.
- ULID payload and generation behavior: proposed here and finalized by
  SCRUM-14.

Replace the malformed 14-character example with a valid 26-character ULID:

```text
ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV
```

The shortened IDs in the example graph can remain, provided the document
explicitly says they are illustrative and invalid for actual writes.

### E. Add a decision-ownership table

Add this after the document-control block:

| Decision | Status | Owner |
|---|---|---|
| Eight primary node labels | Accepted | SCRUM-12 |
| Node properties and nullability | Accepted | SCRUM-12 |
| ID prefixes | Accepted | SCRUM-12 |
| ULID payload and minting | Proposed | SCRUM-14 |
| Relationship names and direction | Proposed | SCRUM-13 |
| Alias constraints and indexes | Proposed | SCRUM-15 |
| Migration execution | Deferred | SCRUM-15 |
| Merge/split mechanics and :Superseded lifecycle | Proposed | SCRUM-68 |

This prevents later stories from accidentally treating examples as
already-finalized implementation contracts.

## 2. Recommended regression protection

Add a dependency-free test:

```text
tests/unit/docs/test_local_markdown_links.py
```

It should scan local Markdown links under `docs/` and fail when a referenced
repository file does not exist. Convert local path references in the schema
document into real Markdown links so the test can validate them.

This directly prevents recurrence of the `PHASE_1_PLAN.md` defect without
adding a new package.

## 3. Explicitly exclude from this PR

Do not add these to PR #11:

- Relationship direction or cardinality implementation
- Cypher migrations
- Neo4j constraints or indexes
- ID-generation code
- Graph repositories or writers
- Resolve-query changes
- Production data-loading behavior

Documenting ownership is sufficient. Implementing these items here would make
SCRUM-12 oversized and blur its acceptance criteria.

## 4. Re-review and merge gates

After the author updates the existing PR branch:

1. Confirm all three original findings are resolved.
2. Run:

   ```bash
   git diff --check
   python -m compileall api ingestion
   python -m ruff check .
   python -m mypy api ingestion
   python -m pytest
   ```

3. Confirm every local documentation link resolves.
4. Review the updated Alias examples against the new identity rule.
5. Validate the GitHub PR merge ref against the latest `develop`, not only the
   PR head.
6. Require both GitHub CI jobs to pass.
7. Approve only when there are no unresolved data-integrity or contract
   ambiguities.

## 5. After PR #11 merges

Proceed in this order:

1. **SCRUM-13:** Extend the same schema document with relationship names,
   direction, cardinality, edge properties, and the six core traversal
   examples (per the corrected SCRUM-42).
2. **SCRUM-14:** Implement the prefixed-ID utility with format, prefix,
   source-independence, and uniqueness tests, covering all eight prefixes
   (per the corrected SCRUM-44).
3. **SCRUM-15:** Add idempotent Neo4j migrations, primary-label ID constraints,
   Alias identity constraints, and lookup indexes.
4. **SCRUM-68:** Document and implement merge/split mechanics with invariant
   tests. Critical-path: must land before Epic 6 bulk reconciliation
   completes.
5. **Operational gate:** Run migrations twice against a clean Neo4j instance
   and add graph-writer and resolve-safety tests before real ingestion work
   merges.

## Final position

Request changes on PR #11 now, fix the document contract in the same PR, and
do not merge until the Alias identity issue is resolved. Continue using
`docs/graph-schema-design.md` as the single canonical Epic 2 schema contract,
extending it through SCRUM-13, SCRUM-14, SCRUM-15, and SCRUM-68 rather than
creating disconnected specifications.
