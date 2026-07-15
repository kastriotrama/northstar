# Stakeholder Feedback and Identity Decision Guide

| Field | Value |
|---|---|
| Status | Working decision guide |
| Primary scope | Story 2.3 (`SCRUM-14`) and future identity-related work |
| Related schema | [Graph schema design](graph-schema-design.md) |
| Audience | Product, data, backend, support, operations, and domain stakeholders |
| Last reviewed | 2026-07-15 |

## 1. Purpose

Use this guide when a story affects permanent IDs, external identifiers,
Alias nodes, re-import behavior, data corrections, or later merge/split
workflows. It preserves the reasoning behind current recommendations and
helps the team ask stakeholders only questions that can materially affect
product behavior.

The guide separates three kinds of decisions:

1. **Technical invariants** that protect data integrity and should not be
   delegated as preference questions.
2. **Operational choices** where daily users can provide valuable feedback.
3. **Future-story concerns** that should be recorded without expanding the
   current story.

## 2. Established context

The accepted graph contract already establishes that:

- Every graph node has an opaque, source-independent internal ID.
- Internal IDs use the `<PREFIX>-<ULID>` format.
- External identifiers such as plates, TecDoc k-types, engine codes, and VINs
  are represented by `Alias` nodes, not used as canonical node IDs.
- `alias_text` is a non-unique lookup value.
- Each Alias represents one source assertion and points to one live canonical
  target through `REFERS_TO`.
- Internal IDs are never reused.

Story 2.3 is responsible for finalizing the ID-generation strategy and
utility. It should not reopen already accepted Alias or relationship
semantics unless implementation reveals a direct contradiction.

## 3. Plate and k-type behavior

When an accepted plate-to-k-type mapping is already available, it should lead
to one existing canonical `VehicleVariant` rather than a second variant:

```text
Plate ABC123
  -> plate Alias ALI-...
  -> accepted k-type mapping 13902
  -> k-type Alias ALI-...
  -> VehicleVariant VEH-...
```

The plate and k-type receive separate Alias IDs because they are separate
source assertions. Both aliases can point to the same `VehicleVariant`.
Different Alias IDs do not imply duplicate vehicle IDs.

The normal ingestion order is **lookup, reconcile, then mint**:

1. Look up the stable source assertion.
2. Reuse its existing Alias and canonical target when present.
3. Follow an accepted plate-to-k-type mapping to an existing variant.
4. Reconcile candidate matches according to the matching policy.
5. Mint a new canonical ID only when no canonical match exists.

Re-importing the same accepted mapping must not create another
`VehicleVariant`. ID generation provides uniqueness; lookup and reconciliation
provide idempotency.

## 4. Technical invariants

Treat the following as engineering and data-integrity requirements:

- Never embed or hash an external source code into a canonical node ID.
- Never use a plate, k-type, VIN, or engine code as a canonical node ID.
- Never mint before checking stable source assertion identity and accepted
  mappings.
- Never recycle an internal ID after correction, supersession, merge, or
  deletion from active views.
- Preserve source assertion identity separately from normalized lookup text.
- Keep ID generation independent of provider names and provider-specific
  formats.
- Make parse and validation rules identical across ingestion, graph writes,
  APIs, tests, and documentation.

Stakeholders can validate the operational consequences of these rules, but
they should not be asked to choose entropy size, ULID encoding, parser
implementation, or collision-handling algorithms.

## 5. Useful stakeholder questions

Ask daily users about behavior they can observe or depend on:

1. When an external identifier is corrected or replaced, should the previous
   value remain searchable?
2. Which roles need to see, copy, or search NorthStar internal IDs?
3. Should internal IDs appear in normal screens, technical details, URLs,
   exports, logs, support tickets, or integrations?
4. What evidence must be visible when plate, k-type, or another source gives
   multiple candidates?
5. Which historical references must remain understandable after corrections?
6. Which existing reports or integrations currently use plate, k-type, VIN,
   or another external code as a long-lived key?

Use concrete examples and ask what the user expects to happen. Do not ask
stakeholders to approve an implementation mechanism they do not interact
with.

## 6. Duplicate records and merge discussions

A duplicate canonical ID is not expected merely because a plate and k-type
are separate identifiers. When their accepted mapping is available, both
aliases should resolve to the same canonical variant.

Duplicates can still occur exceptionally because of incomplete evidence,
concurrent ingestion, provider disagreement, historical data, manual imports,
or matching defects. Those cases are reconciliation concerns rather than the
normal Story 2.3 flow.

Merge/split mechanics belong to their separately accepted story (currently
`SCRUM-68`). If a stakeholder discussion uncovers requirements for old-ID
redirects, merge visibility, reversal, audit history, or moving aliases to a
surviving node:

- Record the feedback and the affected workflow.
- Do not expand Story 2.3 automatically.
- Link the decision to the merge/split owner after approval.
- Preserve the foundational rule that IDs are never reused.

Until merge behavior is implemented, describe duplicate handling as an
exception and future recovery workflow, not as the expected outcome of a
plate-to-k-type import.

## 7. Decision classification

Classify feedback before turning it into work:

| Classification | Example | Treatment |
|---|---|---|
| Technical invariant | External codes never become node IDs | Implement and test in the owning story |
| Operational choice | Support users need a copyable `VEH-...` ID | Validate with affected roles and assign an owner |
| Data-quality rule | Re-import must reuse an existing source assertion | Implement in ingestion/repository work with regression tests |
| Future-story concern | Old IDs should redirect after a merge | Record for `SCRUM-68`; do not expand Story 2.3 |
| New requirement | An export must include canonical IDs | Create or update the owning Jira issue only after approval |

Feedback is evidence, not automatically an accepted requirement. A decision
becomes implementation scope only after it has an owner and is accepted into
the appropriate story.

## 8. Lightweight discussion format

For an identity-related review, use a short written or 15-minute session:

1. State the proposed user-visible rule in one sentence.
2. Show one normal example and one ambiguous or corrected example.
3. Ask no more than five operational questions.
4. Record the answer, rationale, affected roles, and example data.
5. Classify the answer using the table above.
6. Assign accepted work to the correct story instead of silently expanding
   the current sprint.

Recommended record:

```md
### Decision: <short title>

- Date:
- Participants / affected roles:
- Workflow and example:
- Current behavior:
- Feedback:
- Classification: invariant | operational | data quality | future story
- Decision and rationale:
- Owning Jira issue:
- Validation or follow-up:
```

## 9. Current recommendations for Story 2.3

- Keep prefixed ULIDs as the source-independent internal ID format.
- Centralize mint, parse, and validate behavior in one shared utility.
- Use lookup-before-mint in every future graph write path.
- Treat plate and k-type as separate aliases that can share one canonical
  target.
- Keep internal IDs available to APIs, logs, audit records, integrations, and
  support tooling.
- Hide internal IDs from primary user-facing screens unless stakeholder
  feedback shows a daily operational need; provide a copyable technical detail
  where useful.
- Preserve historical external identifiers according to an explicitly owned
  correction/audit policy.
- Defer merge redirects and supersession workflows to their owning story while
  retaining the no-ID-reuse invariant now.

## 10. Review checklist

Before implementing feedback related to identity:

- [ ] Confirm whether the point is already an accepted schema invariant.
- [ ] Confirm the normal plate-to-k-type lookup path before describing a
      duplicate scenario.
- [ ] Identify the affected user role and real workflow.
- [ ] Separate lookup/idempotency behavior from ID generation.
- [ ] Identify whether the work belongs to the current story or a future owner.
- [ ] Add tests for accepted technical and data-quality rules.
- [ ] Obtain approval before creating or changing Jira scope.
- [ ] Update this guide when a decision materially changes the recommended
      approach.
