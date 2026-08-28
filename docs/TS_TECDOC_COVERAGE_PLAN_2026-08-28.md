# TS → TecDoc: coverage-first execution plan

Execution authorized by the user on 2026-08-28: record this plan in Jira, implement the local repair package and reprocess the same retained 20k rows. Disputed semantic rules, production activation, pushes and graph writes remain unapproved. Based on the retained integrated 20k report, current matcher code and Jira descriptions checked on 2026-08-28. Performance improvements are not acceptance-coverage improvements.

## Jira execution map

- SCRUM-172: ranked repair diagnostics and private evidence report.
- SCRUM-173: source-aware bodywork/drive comparison contract.
- SCRUM-174: catalog-scoped model recovery, rule proposals and approval evidence.
- SCRUM-175: pinned 20k replay and independent held-out validation.

These four new subtasks belong to SCRUM-101. Existing SCRUM-148/149 own candidate ranking and conflict gates; SCRUM-150/171 already cover decision persistence and SCRUM-170 owns promotion/attachment. No duplicate persistence or promotion ticket was created. Independent adjudication and disputed rule activation cannot be completed by inventing reviewer approval; ticket comments must distinguish implemented support from those remaining gates.

## What the evidence actually says

Recorded 20k terminals: 1,510 resolved, 1,551 provisional, 15,519 review-required, 1,307 hard conflicts, 112 policy exclusions and one normalization review. This sample is not representative national coverage and is not independently labeled ground truth.

Within the review-required population, 7,905 have missing model evidence and 5,053 have bodywork conflict evidence. These sets are disjoint: together 12,958 / 15,519 = 83.5% of reviews. Total bodywork occurrences are 5,762 because another 709 are in hard-conflict records. Do not count all 5,762 as removable reviews.

Other primary review routes: non-hard context conflicts 5,055 (including the 5,053 bodywork cases), margin 789, partial model 765, no candidate 677, phonetic evidence 306 and below-provisional threshold 22. These routes plus missing models account for all 15,519 reviews.

Separately, 1,338 provisional records flag candidate-only graph safety; 1,290 of those reached the matcher resolved threshold before being downgraded. These are records, not distinct KTypes, and not independently verified matches. Do not change their route simply to improve a metric.

Hard-conflict field occurrences overlap: power 914, year 198, displacement 146, model series 55 and fuel 14. Investigate power semantics and candidate selection before proposing wider tolerances.

## Execution order and deliverables

### 1. Explain the highest-volume cohorts before changing acceptance

Owner: engineering; SCRUM-101 / SCRUM-148 / SCRUM-149.

Extend the existing diagnostics rather than build another matcher. Group the same 20k rows by manufacturer, raw model, recovered family, type approval + variant/version, source body code, TecDoc body type and rejection reason. Keep raw values, normalized values and candidate facts separate.

For each repeated group, produce positive and counterexamples, source/rule/catalog versions, top alternatives and the exact blocking fields. Distinguish parser/normalization defects, vocabulary differences, incorrect family selection, catalog omissions, real contradictions and genuinely insufficient evidence. Run diagnostic counterfactuals only to estimate which downstream gates would remain after a proposed correction; never persist those hypothetical resolutions.

First output: ranked repair groups, exact affected counts, candidate ambiguity after each hypothetical repair, and replay of the 24 changed-resolved cases (4 gained, 9 lost, 11 changed KType). Review every changed accepted identity; no self-generated correctness labels. Start with bodywork groups and missing-model fingerprint groups, not isolated rare examples.

### 2. Implement source-aware compatibility, starting with bodywork

Owner: engineering for behavior; named domain reviewer for semantic rule approval. SCRUM-149, under SCRUM-101.

Current code compares normalized bodywork and drive strings directly. The new pinned alignment loader is consumed for fuel, not a completed bodywork/drive solution. Implement a tested comparison contract with four states: confirmed equivalent, compatible but non-confirming, unknown, and conflicting. Keep source raw facts unchanged.

- Bodywork: use reviewed, directional rules scoped by the evidence needed to distinguish affected manufacturer/family/type-approval cohorts. AC/estate must not become a universal SUV synonym. Where broad TS body classification cannot distinguish estate from SUV, compatibility removes only an unjustified contradiction; it contributes no positive match evidence and does not choose between candidates.
- Drive: only after checking the source definition and null/default behavior, represent verified `is_4wd=0` as the allowed set {FWD, RWD}, not an exact FWD assertion. It must never decide FWD versus RWD by itself. Missing or untrusted zero remains unknown.
- Fuel/power/year: audit source meanings and transformations before adjusting thresholds. In particular, separate hybrid system power from combustion-engine power when source evidence supports that distinction; unknown semantics remain unknown.

Deliverable: versioned reviewed rules, known-positive/negative fixtures, an isolated test proving the matcher actually consumes the pinned version, and an A/B report. Engineering can prepare comparison support and evidence packs without waiting for approval; activating disputed semantic mappings requires explicit review.

### 3. Recover model identity from existing raw evidence

Owner: engineering and rule reviewer; SCRUM-148.

Partition the 7,905 missing-model rows into recoverable explicit text, repeated type-approval/variant/version signatures, unresolved manufacturer scope, and no usable identity evidence. First reuse existing reviewed aliases and fingerprints; establish why they did not fire before adding duplicates.

Fix deterministic field extraction/tokenization defects first. Propose manufacturer-scoped exact aliases and fingerprint rules only when repeated source evidence identifies a unique catalog family, with support and counterexamples. Freeze development and held-out groups before learning rules; repeated plates or identical fingerprint groups must not leak into both sets. Candidate-derived engine allocations are not independent TS observations.

Deliverable: each rule lists affected rows, evidence origin, applicable scope, competing families and validation results. A recovered model is a candidate-generation improvement, not an accepted KType. Rows without enough evidence stay unresolved and become a specific data-acquisition backlog.

### 4. Resolve remaining candidates using distinguishing evidence

Owner: engineering; SCRUM-148 / SCRUM-149.

Audit whether the current top-ranked conflicting candidate hides a valid alternative. Within a defensibly established manufacturer/family, classify all retained plausible candidates by compatible, missing and conflicting evidence before selecting a winner. Preserve eliminated alternatives and their reasons. Validate this change against the existing policy; do not silently choose a weaker family because it has fewer conflicts.

Target the 789 margin, 765 partial-model and 306 phonetic-only review routes, plus the 677 no-candidate route through reviewed exact aliases/catalog gap investigation. Require evidence that actually distinguishes the remaining KTypes. Shared fuel, year and power values are not automatically sufficient; one candidate left in an incomplete index is not proof of identity. Multiple valid candidates remain unresolved. Never pick an arbitrary engine from a multi-engine KType.

For hard conflicts, start with the 914 power occurrences, then year/displacement/model-series groups. Correct demonstrated unit/source-semantic/series parsing defects; do not blanket-widen tolerances or remove hard gates. True TS–TecDoc contradictions require source correction or independent evidence.

### 5. Prove coverage gains before scale or activation

Owner: engineering and independent reviewer; SCRUM-101. Product acceptance risk remains an explicitly owned choice.

Run each repair separately on the same pinned 20k, then combine only changes that pass. Freeze a separate, stratified validation population before tuning (proposed next size: 50k), spanning manufacturers, age, fuel, bodywork and source completeness. Keep repeated vehicle/fingerprint groups from contaminating both rule development and validation; record inclusion probabilities if estimating population coverage. The existing 200 labels and 24 changed cases are diagnostic, not representative accuracy estimates.

Publish independently confirmed new matches, lost valid matches, changed KTypes, wrong accepted matches, unresolved ambiguities, conflicts and graph-ineligible outcomes. A review→hard-conflict or review→provisional move is not accepted-coverage uplift. Check the pre-agreed precision criterion with uncertainty; do not invent a target from this unlabeled sample. No observed regression in tests is not proof of zero population error.

Deliverable: per-rule and combined acceptance/rejection decisions backed by held-out evidence. Only then run the full 6,515,471-row matcher with pinned versions and durable operational readiness. No new raw import is needed for this plan.

### 6. Turn validated matches into real customer lookups

Engineering can audit graph readiness alongside steps 2–5, but writes remain separately gated. Inventory the distinct KTypes behind the 1,290 candidate-only downgrades and separate missing graph target, incomplete canonical relationships, provisional target, collision and evidence problems. Repair ingestion/catalog gaps without inferring a vehicle-specific engine.

Reuse existing components and close tested gaps: SCRUM-164/165 resumability → SCRUM-171 immutable PostgreSQL decisions → SCRUM-170 explicit KType promotion and controlled alias attachment. Before graph writes, independently establish target eligibility, revalidate the current decision, and check plate collisions. Prove retry/idempotency and PostgreSQL/Neo4j reconciliation in a small approved cohort before broader use. No automatic promotion because the matcher scores highly.

Frontend/API should separately expose: evaluated only, decision persisted, target eligible, alias attached, reconciliation passed. Show raw TS facts, normalized facts, TecDoc alternatives and why the selected KType won. Do not present the existing showcase as proof of a graph attachment.

## Immediate work package

Stop spending the next iteration on scoring-weight tweaks, additional speed tuning, repeated national runs or blanket aliases. Deliver the ranked bodywork/missing-model repair report, implement the comparison contract with regression tests, and submit a small, evidence-complete set of scoped rules for review. Measure accepted-match gains before expanding scope. Jira scope remains SCRUM-101/148/149 first; SCRUM-171/170 address durable delivery, not missing matching evidence. No Jira status or issue changes were made during this planning review.
