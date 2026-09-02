# Chunk Review Dashboard — leverage-first TS-to-TecDoc match resolution

| Field | Value |
|---|---|
| Version | `0.1` |
| Status | Accepted design; increment 1 implemented |
| Owner | NorthStar backend team |
| Scope | `core.match_chunk_*`, `staging.oem_vin_evidence`, `api/app/features/match_review`, `/match-review` screen |
| Last reviewed | 2026-08-31 |

## 1. Problem and principle

The full write-free audit of 6,515,471 passenger TS rows produced ~6.04M
`review_required` outcomes. Row-by-row review cannot close that gap. The same
profiling showed the cohort collapses into roughly 45,103 unique technical
signatures, so the unit of human work must be the **chunk** — all rows sharing
one normalized technical signature — not the row.

Core principle: **one decision, many rows; one paid evidence acquisition,
permanent reuse.** Every verified fact is stored keyed by its signature or VIN
so it multiplies across the cohort and across future re-runs.

## 2. Chunk model

A chunk is the set of source rows in one build that share a deterministic
technical signature. The signature mirrors the matcher's evaluation key:

```text
signature_version, manufacturer, model_family, production_year,
energy_sources (sorted), engine_code, displacement_cc, power_kw,
drive_type, bodywork_form
```

`signature_key` is the SHA-256 of the canonical JSON encoding. Chunks are
scoped to a `build_id` (one immutable chunking pass over one source batch and
status filter), so rebuilding after a rule change never mutates an earlier
build. `chunk_id` is `uuid5(build_id, signature_key)`: deterministic,
collision-safe, retry-stable.

Members carry only `source_record_id`, `source_batch_id`, normalization
status, and review reasons — never plates or VINs. Member counts and the
chunk's reason profile are recomputed from the members table at finalize time
so interrupted builds resume idempotently.

### Chunk sources

Increment 1 builds chunks from the latest normalization result per source
record (`core.normalization_results`), filtered by status. When persisted
per-row routing decisions exist (SCRUM-171 runs in `persist` mode), the
builder can additionally consume `core.match_routing_decisions` reasons; the
audit runs so far were count-only, so normalization evidence is the available
substrate today.

## 3. OEM evidence — pay once, reuse forever

A paid VIN lookup against the OEM data provider (provider name/contract to be
confirmed) is triggered manually from the dashboard for a sampled member of a
chunk. Rules:

- The raw response is stored **immutably** in `staging.oem_vin_evidence`
  before anything interprets it, keyed by `(provider, vin, dataset_version)`
  with a caller-issued `request_id` reused across retries. A repeat request
  returns the cached row; the provider is never billed twice for one VIN.
- VINs live only in the `staging` schema, consistent with the existing
  sensitive-identifier discipline. API responses expose a masked VIN
  (first three and last three characters) and the evidence payload summary;
  `core.match_chunk_samples` links chunk to evidence by id only.
- Outbound VIN transmission to the provider requires a contract/legal check
  before production use; VINs must not appear in request logs.

## 4. Sampling and extrapolation honesty

An OEM response verifies **one vehicle**. Applying it to a chunk is an
inference. The rules encoded in the adjudicator and future apply step:

- Large chunks require at least two **concordant** samples before a
  chunk-wide decision may be proposed as safe.
- Sample disagreement (with each other or with the signature) is routed as a
  `split_chunk` recommendation — the signature is too coarse; splitting is a
  successful outcome, not a failure.
- Extrapolated member decisions carry a distinct decision basis
  (`oem_sample_extrapolation`) with the sample decision ids as evidence, and a
  confidence tier below directly verified rows. They flow through the
  existing SCRUM-171 lifecycle unchanged.

## 5. Adjudication lifecycle

```text
open chunk
  -> fetch OEM samples (manual button, cached forever)
  -> generate proposal (adjudicator sees TS signature + TecDoc candidates + OEM evidence)
  -> human approves / rejects / splits (chunk level)
  -> apply step mints SCRUM-171 decisions per member  [increment 2]
  -> normal promotion pipeline (SCRUM-170) unchanged
```

Proposals are append-only rows in `core.match_chunk_proposals` with an
explicit `proposal_source` (`heuristic`, `agent`, `human`), model/policy
version, recommendation, cited evidence, and reasoning. A proposal never
writes decisions directly; approval records reviewer identity and note.

### Adjudicator boundary

`MatchAdjudicator` is a constructor-injected protocol. Increment 1 ships a
deterministic heuristic implementation (evidence sufficiency + sample
concordance). The LLM agent adapter (increment 2) implements the same
protocol: it receives the assembled evidence bundle (chunk signature, reason
profile, sampled TS rows sanitized, TecDoc candidate summaries, OEM evidence)
and returns the same structured proposal shape. Agent proposals that humans
consistently approve for a reason-code category may later graduate to
auto-apply for that category — a separately gated, evidence-backed step.

## 6. Increments

| Increment | Content | Status |
|---|---|---|
| 1 | Chunk schema + builder CLI, OEM evidence cache + provider adapter boundary, heuristic adjudicator, proposal/review lifecycle, `/match-review` screen | Implemented |
| 2 | LLM agent adjudicator adapter, TecDoc candidate generation in evidence bundle, apply step minting SCRUM-171 member decisions with `oem_sample_extrapolation` basis | Planned |
| 3 | Chunk splitting UX, auto-apply graduation per reason-code category, candidate graph projection feedback signal | Planned |

The candidate graph projection (scratch-graph analysis of candidate edges
feeding scores back into PostgreSQL as an evidence signal) is intentionally
deferred; the canonical Neo4j graph remains 100% verified.

## 7. API surface (increment 1)

| Route | Purpose |
|---|---|
| `GET /v1/match-review/builds` | List chunk builds, newest first |
| `GET /v1/match-review/chunks` | Page chunks in a build, biggest leverage first |
| `GET /v1/match-review/chunks/{chunk_id}` | Signature, reason profile, sanitized member sample, OEM samples, proposals |
| `GET /v1/match-review/chunks/{chunk_id}/members/{source_record_id}` | Field-by-field source comparison for one vehicle: TS evidence, normalized signature, OEM evidence, and per-field conflict flags |
| `POST /v1/match-review/chunks/{chunk_id}/oem-samples` | Fetch-or-reuse OEM evidence for one member (idempotent via `request_id`) |
| `POST /v1/match-review/chunks/{chunk_id}/proposals` | Generate and persist an adjudicator proposal |
| `POST /v1/match-review/proposals/{proposal_id}/review` | Approve or reject a proposal (chunk-level human decision) |
| `GET /match-review` | Dashboard screen |

The chunk builder runs as
`northstar-ingest build-match-chunks --batch-id <id> [--status review_required ...]`.

## 8. Vehicle-level review context

A chunk decision is still made by looking at a concrete car, so the detail pane
opens with the first member selected by default and shows it as the "checking
vehicle". Vehicles are identified by plate plus source manufacturer, model and
year (`ABS455 · VOLVO 131341 M · 1967`), never by bare record id; the plate is
already reviewer-visible on the normalization screen, while VINs stay masked.

The source comparison table places Transportstyrelsen evidence, the normalized
signature and OEM evidence side by side per field. A field is flagged
`conflict` only when normalized and OEM values both exist and disagree; with no
OEM evidence the flag is null rather than false, so "unknown" is never rendered
as "agrees". TecDoc becomes a fourth column in increment 2 once catalog
candidates are generated — the restored production dump contains no TecDoc
staging rows.

## 9. Source spread — the extrapolation gate

A chunk is uniform in its *normalized* signature by construction, but its raw
Transportstyrelsen evidence may not be. `GET /chunks/{chunk_id}/field-profile`
counts distinct raw values per field across the chunk's members (bounded to
5,000 scanned rows) and reports which fields vary.

Fields in `IDENTITY_FIELDS` (`brand`, `model`, `model_no`, `variant`,
`type_text`) carry model identity. When they vary, the signature grouped
materially different vehicles and no single decision is safe: the heuristic
adjudicator returns `split_chunk` immediately, **before** any OEM lookup is
paid for, and the screen marks the chunk `mixed identity`.

The first production chunk demonstrates why this gate exists: 1,564 rows share
the signature `Volvo / unknown model / 1967 / petrol / 55 kW / covered_body`,
yet carry 26 distinct `brand` strings (`VOLVO 131341 M`, `VOLVO 211341 M`,
`VOLVO 121341 M`, …) and 10 distinct `model_no` values — different Volvo
Amazon variants collapsed together because TS supplied no model field. Sampling
one vehicle there would have extrapolated a wrong decision across 1,564 rows.

Variance is therefore also a **splitting key**: the differing identity field is
the dimension the next signature version should include.

## 10. Field resolution: unresolved is not missing

Transportstyrelsen often sends a value NorthStar cannot interpret rather than
no value. `is_4wd = 0` says only "not four-wheel drive", leaving FWD and RWD
indistinguishable — 191,921 rows in the current build, and ~77% of the whole
fleet. Every field therefore carries one of three states per vehicle:

| Status | Meaning | Reviewer action |
|---|---|---|
| `resolved` | normalization produced a canonical value | none |
| `unresolved` | the source stated something we cannot interpret | **author a rule** |
| `missing` | the source stated nothing | acquire evidence (OEM/TecDoc) |

The distinction matters because only `unresolved` is fixable by reasoning over
data already held.

### Resolution rules

A resolution rule asserts that a *conjunction of source conditions* implies a
canonical value:

```text
IF is_4wd = 0 AND fab_code = VO  THEN drive_type = fwd
```

Authoring is population-first, not row-first:

1. `GET /v1/match-review/unresolved` ranks unresolved (field, value)
   populations by row count.
2. `GET /v1/match-review/unresolved/discriminators` breaks one population down
   by every candidate predicate field and ranks them (§11).
3. The reviewer clicks values to build a predicate.
4. `POST /v1/match-review/rule-preview` dry-runs it: matched rows, how many
   would be newly resolved, how many already carry a value (and so would be
   an assertion over an existing decision), plus sample plates to spot-check.

Target values are constrained to a canonical vocabulary (`drive_type` accepts
only `fwd`/`rwd`/`awd`), so a rule cannot introduce a new spelling. Preview
writes nothing.

**Not yet implemented:** persisting a previewed rule. That must reuse the
existing immutable translation-rule machinery — draft → reviewed activation →
new immutable version → re-normalize with before/after comparison — rather
than a parallel store, so resolution rules inherit the same auditability. This
is the next increment.

## 10b. Rule expressiveness, attribute picking, and advice

**Boolean shape.** Values inside a condition are OR-ed; conditions are AND-ed.
This is conjunctive normal form restricted to one field per clause — enough for
`brand starts_with 'MERCEDES-BENZ 204' OR 'MERCEDES-BENZ 212'`, while staying
readable enough to audit an immutable rule at a glance. Arbitrary boolean
nesting is deliberately unsupported: a rule nobody can read is a rule nobody
can review.

**Operators.** `equals`, `not_equals`, `starts_with`, `contains`, `gte`, `lte`.
Numeric comparisons cast the registry's text behind a regex guard, so a
non-numeric value yields NULL and simply fails to match instead of aborting the
query.

**All-attributes picker.** The ranked discriminator list shows only fields
NorthStar considers candidates. `GET /unresolved/attributes` returns *every*
key present on the population — in the current build that is 30 attributes, 16
of which the ranked list never shows (`gearbox`, `fuel2`, `ev_config`,
`build_month` …). Counts come from a bounded scan and the response says so via
`sampled` and `scanned_members`; the UI must repeat that caveat rather than
presenting sampled figures as totals.

**Pattern discovery.** `GET /unresolved/patterns` finds shared token prefixes,
so `MERCEDES-BENZ 204` surfaces as one condition covering 926 cars across two
spellings. Prefixes are ranked by `coverage × (1 − coverage)`; a prefix
matching the whole population scores zero because it divides nothing.

**Rule advice.** `POST /unresolved/advise` proposes a rule. The division of
labour is deliberate and load-bearing: statistics can find a *homogeneous
block*, but cannot say what the block means. So the advisor proposes conditions
freely and only fills in `target_value` when OEM samples concordantly support
one — `confident` reports which happened. `TARGET_FIELD_PRIORS` encodes the one
piece of explicit domain knowledge: drive type is a property of the model, so
identity fields outrank `vehicle_year` even when year scores higher.

`PatternRuleAdvisor` is deterministic and always available. `LlmRuleAdvisor`
activates only when `OPENAI_API_KEY` is set, sends the same evidence bundle,
and falls back to the deterministic advisor on any transport or parsing
failure. Neither writes anything: advice still goes through preview and human
approval.

## 11. Ranking discriminators

For an unresolved population, each candidate field is scored on three
explainable factors rather than information gain, so a reviewer can see why a
field was suggested:

- **coverage** — share of the population that has any value for the field;
- **separation** — distance from constant, `1 − dominant_value_share`;
- **concision** — cardinality penalty, `1 / (1 + log10(distinct))`.

Separation is what disqualifies `eu_category`: it covers 100% of rows but is
`M1` for 191,884 of 191,921, so it splits nothing. Concision is what demotes
`kw`: 420 distinct values would separate the population cleanly but demand 420
rules. `fab_code` (151 manufacturer codes, `VO`/`SA`/`TO`) scores well on all
three and is the field a human would in fact reach for.

The score ranks suggestions; the human still decides. No rule is inferred
automatically.

## 12. Screen design direction

The `/match-review` screen intentionally departs from the serif/editorial
styling of the normalization review screen: a single clean sans-serif family,
higher information density with generous whitespace, a leverage-sorted chunk
worklist on the left and a full-height detail pane (signature grid, reason
chips, evidence timeline, proposal review actions) on the right. Future
restyling of the older screens should converge on this direction.

## 13. Transportstyrelsen code sources

TS publishes its code lists under
`transportstyrelsen.se/.../koder-for-fordonsuppgifter/`, which contains exactly
three sections: **Dispenskoder**, **Textkoder** and **Karosserikoder** (the
latter split per vehicle type, plus *Fordon för särskilda ändamål* and
*Tilläggskoder*).

Checked against the repository on 2026-08-31:

- **Passenger karosserikoder** (17 codes) — 15 already mapped in
  `translation_dictionaries.py`. `96` (Polisbil) is a *purpose*, already held
  in `_PRIMARY_SPECIAL_PURPOSE_CODES`; `98` (Övrigt) carries no bodywork
  meaning. Neither is mapped to a shape, deliberately.
- **Fordon för särskilda ändamål** — `SA`/`SE`/`SJ` and the rest were already
  mapped; `SF`, `SK`, `SL`, `SM` were added to `_SECONDARY_PURPOSE_CODES`.
  `85` (Dolly) remains unmapped: see below.
- **No fabrikatkod list is published.** The `fab_code` catalogue is therefore
  derived from the register itself (§14).

**Adding a translation rule is a versioned change.** `BDY-85` was written and
then reverted because `test_bundle_loader_validates_complete_portable_contract`
correctly failed: the committed portable bundle pins an exact rule catalogue,
so a new rule must go through the draft → activation → immutable version flow
(and a regenerated bundle), not a direct edit. The guard worked as designed.

## 14. The derived fabrikatkod catalogue

`_FAB_CODE_MANUFACTURERS` grew from 7 hand-written entries to 121 derived ones.
Method: join the register to its normalization results, take the dominant
normalized manufacturer per code, and keep only codes where that manufacturer
covers **≥95%** of the code's rows (≥20 rows). Across the 688,828 rows covered,
`fab_code` disagrees with the normalized manufacturer on **145 rows (0.021%)** —
which is precisely what the `MFR-BRAND-FAB-CODE` corroborator exists to surface.

Thirteen codes were excluded as ambiguous rather than guessed: `ÖV` is *Övrigt*,
a catch-all rather than a make; `LQ` (Lexus, 72%) and `POL` (Polestar, 78%)
bleed into Toyota and Volvo and need a decision, not a default.

`fab_code` is materially cleaner than the free-text `brand` field, which
carries 17,995 distinct spellings where `fab_code` carries ~151 codes — worth
remembering when choosing which layer a resolution rule should key on (§10).

## 15. Enabling the LLM advisor

Set `OPENAI_API_KEY` (optionally `RULE_ADVISOR_MODEL`, `RULE_ADVISOR_BASE_URL`,
`RULE_ADVISOR_TIMEOUT_SECONDS`) and restart. Nothing else changes:
`_build_rule_advisor` returns `LlmRuleAdvisor` when a key is present and
`PatternRuleAdvisor` otherwise. The screen names whichever actually answered,
so "AI Suggest Rule" never implies a model ran when it did not.

**What the model receives**: the task in words, the unresolved field/value, the
target field with its `allowed_target_values`, the population size,
`semantically_relevant_fields_in_order` (the domain priors), the allow-lists of
condition fields and operators, the top six scored discriminators, real
`value_distributions` (40 values per field for the three strongest fields, so
prefixes like `MERCEDES-BENZ 204` are visible), and any cached `oem_evidence`
for cars in that population — VIN-masked through the same sanitizer the API
uses. Requests run at `temperature: 0` with a 900-token cap.

**Model output is untrusted.** A reply is rejected — falling back to the
deterministic advisor — when it names a field or operator outside the
allow-lists, uses an unknown layer, returns no usable conditions, is not valid
JSON, or gives a `target_value` outside the canonical vocabulary. A value
asserted without any OEM evidence is dropped and `confident` forced false: the
model cannot talk itself into a fact about cars the evidence does not contain.
Transport failures degrade the same way. Twelve tests in
`test_llm_rule_advisor.py` pin this behaviour without a live key.

**Still open**: no OEM evidence exists yet (the provider is unconfigured), so
`oem_evidence` is empty in practice and `confident` stays false regardless of
advisor. Rule persistence is also still unbuilt — advice leads to preview, and
preview writes nothing.

## 16. Target vocabularies: closed sets and free text

Most unresolved populations target `model_family`, which has no canonical list
— so a fixed dropdown locked reviewers out of exactly the cases that matter
most. `GET /target-vocabulary` now answers per target field:

- **Closed** (`closed: true`, `source: reviewed_rules`) — `drive_type`
  (fwd/rwd/awd), `bodywork_form` (29 values), `energy_sources`,
  `transmission_type`. Values are **derived from the active reviewed rule set**,
  not restated, so the picker cannot drift from the rules normalization
  applies. Note the active set spells it `estate`, while older rule tuples in
  the same module still say `wagon` — a second hand-written list would have
  captured the wrong one.
- **Open** (`closed: false`, `source: observed`) — `model_family`,
  `manufacturer`. Free text is accepted; suggestions are the values already
  present in this build's signatures, with car counts (`Clio` 2,494, `Octavia`
  1,899 …), spelled exactly as normalization spells them so an authored rule
  matches existing data instead of inventing a near-miss variant.

The screen renders an `<input list=…>` combobox: suggestions where they exist,
free typing where the vocabulary is open. Closed vocabularies are enforced on
both sides — the client blocks before previewing, and the service returns 422.

## 17. Live refinement: narrow until coherent

Adding a condition used to leave the facets below describing the *original*
population, so a reviewer narrowed blind — the counts under the cursor stopped
being true the moment they were used. `POST /unresolved/refine` now answers the
whole loop in one call, on every edit:

- **how many** — matched rows, split into would-resolve and already-resolved;
- **what is left to split on** — facets recomputed against the current
  predicate, with fields the reviewer has already constrained removed;
- **whether to stop** — `homogeneous` is true when no identity-bearing field
  (`brand`, `model`, `model_no`, `variant`, `type_text`) still varies among the
  matched rows. That is the same question the chunk source-spread gate asks;
- **which term did the work** — the narrowing trail, computed in a single pass
  with cumulative `FILTER` aggregates (`is_4wd = 0` → 191,921 → 938).

A field named in the predicate is excluded from the homogeneity check: grouping
`MERCEDES-BENZ 204` with `204 K` is a deliberate statement that those spellings
mean the same car, so it must not keep blocking. Without that exclusion a
`starts_with` condition could never converge.

Selecting a population uses the same endpoint, so the initial state and every
later edit follow one code path, and the pane no longer issues a duplicate
facet query on open.

Scope note: what the reviewer isolates is a **predicate, not a sub-chunk**. The
resulting rule resolves matching cars wherever they live, across chunks and
future imports — which is why the count shown is the population's, not the
chunk's.

## 18. Blockers as a lens, chunks as the decision key

`develop` gained a blocker review workspace (SCRUM-101) at the same
`api/app/features/match_review/` path. It answers a question this dashboard
never did — *what is stopping this run, and what recurs* — by grouping matcher
audit items into **patterns** keyed on `pattern_key`, a hash over evidence
fields, and recording rulings against that key.

Two grouping keys over the same rows, both claiming "one decision covers every
member", is not a duplicated feature — it is a correctness hazard. `chunk_id`
earns that claim: since `SIGNATURE_VERSION` 2 the signature is derived from
`TecDocDryRunEvaluator.evaluation_key`, so chunk grouping equals match grouping
by construction. `pattern_key` mirrors matcher semantics by hand — the same
shape of mirror that over-grouped 143 chunks across 726 rows before the
signature was aligned. A ruling recorded against a pattern key inherits no
guarantee that the matcher evaluates its members alike.

The resolution keeps both and orders them:

- **A blocker selects. A chunk decides.** Patterns stay as the triage front
  door — the taxonomy (`why_blocked`, `decision_question`, `evidence_gaps`) and
  the category rollup are how a reviewer picks what to work on. They no longer
  terminate in a decision.
- **The bridge is a join, not a translation.** Both sides key members on
  `source_record_id` in `staging.transportstyrelsen_raw`, so
  `GET /v1/match-review/patterns/{pattern_key}/chunks` resolves a pattern onto
  the chunks holding its rows, ordered by overlap. No second grouping is
  computed and nothing is re-derived.
- **Partial coverage is reported, not hidden.** Rows the selected build never
  chunked are returned as `unmatched_rows`. A pattern is only fully actionable
  when `matched_rows == pattern_rows`; the screen says so rather than showing a
  scope that silently omits rows.
- **Pattern rulings are soft-retired.** `POST /patterns/{pattern_key}/decision`
  is marked `deprecated` and still serves the blocker workspace. Existing
  decisions are surfaced read-only in the blocker inspector as history. Nothing
  is migrated or deleted; making chunk decisions the only path is a team
  decision, and this change makes it reversible.

On the screen this is a third view, `Blockers`, beside `Chunks` and
`Unresolved fields`. Choosing a blocker scopes the chunk list to the chunks it
reaches — a dismissable banner names the population and its row counts — and
the reviewer continues into the rule builder, OEM sampling and proposal review
already described above. The scope travels to SQL as repeated `chunk_id`
parameters on `GET /chunks`; an empty scope returns zero chunks rather than
falling back to the full build.

The blocker tables (`core.match_run_*`) are migrated on the matcher's own path,
not this one. Where they do not exist yet the bridge returns an empty result
instead of failing, so the dashboard runs against a database that has only ever
built chunks.
