# Last Context

Keep the latest 10 task entries only.

## 2026-09-21 — Correcting a value the TS data screen already has

- Added an override mode to resolution rules so a reviewer can fix a wrong value, not
  only fill a missing one: the record panel on `/ts-data` now offers **Edit** beside
  every resolved field, which pins the car's brand and model into the filter and opens
  the same resolver panel in correction mode. An override rule selects cars whose
  effective value differs from the one asserted, supersedes the resolution they carried,
  and writes its own; the projection now reads `coalesce(r_x, n_x)`, so a reviewer's
  assertion outranks the derivation it corrects, and Retire puts the derived value back.
  Correcting is opt-in, stored immutably on the rule (`core.match_resolution_rules.override`),
  and refuses to run until the exact value has been previewed. Validation: ruff, strict
  mypy, 1213 backend unit tests, 18 web tests, Angular build. Remaining step: the flipped
  effective-value index ships with the next `refresh-vehicle-facts` run, which runs the
  vehicle-facts migrations first; until then a `normalized manufacturer` filter is
  unindexed. Nothing in production was changed.

## 2026-09-06 — Corrected unresolved-fields ownership

- Confirmed from historical implementation `25cc983` that the intended rule generator is the population-first **Unresolved fields** workflow: unresolved field/value populations, discriminators, rule preview, save, and save-and-run. Corrected Angular navigation and copy so `/coverage` is **Unresolved fields** and `/chunks` is **Match review** for TS-to-TecDoc blockers. The population-first backend endpoints (`/v1/match-review/unresolved`, `/discriminators`, `/rule-preview`, resolution-rule save/apply) are not yet present in the current backend branch; only the coverage shell is currently wired. Angular build passes.
## 2026-09-06 — Completed Angular unresolved-pattern rule review

- Replaced the non-existent Angular advisor/chunk endpoints with the live match-review contract: operation summary, blocker patterns, evidence, and versioned `accept_pattern` / `keep_blocked` / `change_rule` decisions. The Angular Match review page now owns the unresolved TS-to-TecDoc rule-proposal workflow; the Rules page remains the catalog browser. Validation: Nx Angular development build and backend compile pass. The current local API reports no active match-review operation, so pattern data remains empty until an audit run is started.
## 2026-09-06 — Angular agent and MCP standards

- Added repository-level Angular frontend guidance covering standalone components, signals, strict typing, modern template control flow, DI, observable lifetimes, accessibility, focused tests, and CLI validation. Added tracked `.vscode/mcp.json` to start the installed Angular CLI MCP server from `apps/northstar-web`, with `.vscode` otherwise remaining local-only. Validation: confirmed the installed CLI contains the `mcp` command; Angular build/test remain blocked by local Node 18 versus Angular CLI 22's Node 22.22.3 minimum.
## 2026-08-31 — Exhaustive blocker-pattern inventory

- Added a plate-free `core.match_run_pattern_inventory` aggregate keyed by operation and deterministic pattern, idempotent batch markers, and a paginated `core.match_run_pattern_members` drill-down. Local and remote audit batches now record every blocker pattern with occurrence totals, safe manufacturer/model/KType examples, and source-row membership; the API/frontend label persisted entries `exhaustive` and let stakeholders page through every member vehicle while retaining plates only in the restricted local view. Added `scripts/backfill_match_pattern_inventory.py` for rows processed by an older audit process. Validation: Ruff, strict mypy, Node syntax check, and 23 focused/API tests pass. The audit and historical backfill remain active; no rules, decisions, aliases, Neo4j state, or push changed.
## 2026-08-31 — Full 6.5M audit and stakeholder blocker workspace

- Started a resumable, release-pinned audit of all 6,515,471 local passenger rows against the 72,570-candidate v6 catalog. Added mutually exclusive blocker aggregation, a bounded review sampler, API endpoints and a pattern-first frontend for recurring category triage plus plate-level evidence. Each item now explains why matching stopped, compares TS/TecDoc fields, lists evidence gaps, and states the stakeholder decision required; category proposals remain append-only and cannot persist match decisions, attach aliases or write Neo4j. The latest exact checkpoint is 175,000 rows (2.686%), with 17,717 resolved, 15,723 provisional, 125,825 review-required, 20 unmatched, and 13,165 hard conflicts. The full audit remains running. See `docs/TS_TECDOC_FULL_AUDIT_REVIEW_WORKSPACE_2026-08-31.md`.
## 2026-08-31 — Remote qualifier-loss work merged and reconciled

- Merged remote commit `36a29b0` locally as merge `d1470fd`; no push. Added a digest-pinned, read-only v6 qualifier-loss audit and tests. On the frozen 20k cohort, 1,195 rows lose a trailing qualifier; 430 name a unique specific catalog family, of which 189 already resolve, 59 are provisional, 172 remain review-required and 10 are hard conflicts. C3 Picasso mostly already recovers from raw model evidence; C4 Picasso is commonly blocked by bodywork/conflict gates. No rule, decision, alias, PostgreSQL or Neo4j state changed. See `docs/TS_MODEL_QUALIFIER_LOSS_V6_RECONCILIATION_2026-08-31.md`.
## 2026-08-31 — SCRUM-170/171 promotion cohort dry-run

- Added `scripts/prepare_controlled_match_promotion_cohort.py` and focused tests. It reads PostgreSQL decision heads and the pinned v6 replay/catalog, computes planned v6 immutable decision IDs, excludes aliases requiring retirement, and runs the existing Neo4j promotion preflight in `DRY_RUN` mode. The corrected private evidence packet is `outputs/scrum170-171-controlled-promotion-cohort-v2-20260831.json`: 9,122 heads, 4,650 changed in replay, 4,472 eligible, 4,005 without an active alias, 467 requiring retirement, 1,000 selected, and 1,000/1,000 Neo4j-preflighted with planned v6 IDs. PostgreSQL writes, ledger persistence, aliases, and Neo4j writes are all zero. Focused tests (6), Ruff and strict mypy pass. No push or production activation; explicit approval is still required before any write.
## 2026-08-31 — Mixed-fuel hard-conflict adjudication

- Applied the product-owner authorization to all 28 v5→v6 changes touching a hard-conflict terminal. Approved removal of 11 false fuel conflicts where TS single fuel is a component of the TecDoc mixed set, while preserving provisional/review routing and no score. Rejected four Peugeot 3008 III hybrid and 13 MINI petrol candidate-derived hard conflicts; all 17 remain unresolved with no identity approval. Added a versioned plate-free reviewed manifest, exact checksum/count audit and tests. The other 504 changed cases and full v6 policy remain unapproved; no runtime rule, decision, alias, Neo4j state or push changed. See `docs/TS_TECDOC_HARD_CONFLICT_ADJUDICATION_2026-08-31.md`.
## 2026-08-31 — Persisted-decision replay and safe alias retirement

- Replayed all 9,122 current TS decision heads against the v6 complete catalog, active normalization rules and approved Volvo context policy: 4,472 remain resolved on the same KType, 4,533 become review-required, 116 provisional and one a hard year conflict; all 356 KType identity changes are non-resolved. Reconciled 1,000 graph aliases: 467 fresh, 518 now review-required and 15 provisional; 35/105 promoted variants have only stale support, while 4,005 resolved decisions remain unpromoted. Added privacy-safe read-only audit tools and immutable, idempotent alias retirement that preserves historical targets and restores `:Provisional` after the last active assertion. Focused unit/integration tests, Ruff and strict mypy pass. No existing decision, alias, graph edge, push or production state changed. See `docs/SCRUM_170_171_SUPERSESSION_AUDIT_2026-08-31.md`.
