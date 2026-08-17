# Last Context

Keep the latest 10 task entries only.

## 2026-08-17 — SCRUM-170 controlled KType promotion

- Added dry-run, controlled-cohort and production graph modes that consume only current resolved SCRUM-171 decision heads. Preflight requires one TecDoc KType target, rejects conflicting or multi-target TS aliases, then atomically removes `Provisional` and attaches a collision-safe evidence alias with the immutable decision ID. Replays are idempotent and a PostgreSQL-to-Neo4j reconciliation query reports missing or divergent assertions. Live Neo4j/PostgreSQL integration tests and Ruff pass.

## 2026-08-17 — SCRUM-171 immutable match decision lifecycle

- Added an explicit dry-run/persist boundary for TS-to-KType match decisions, one current decision head per stable source identity/version, and append-only supersession evidence while retaining deterministic immutable decision rows. Dry runs validate source provenance without writes; persisted retries are idempotent and conflicting supersession histories stop safely. PostgreSQL integration and migration contract tests pass; this layer does not promote KTypes or attach Neo4j aliases.

## 2026-08-17 — Reviewed technical tail activated

- Merged PR #28 head `d4cd10d`, adding database-backed `reviewed_record_policy` support keyed by stable VIN plus Brand and activating immutable version `ts-review-20260817T073842135705Z` with 30 reviewed manufacturer-conflict, electrification, bodywork, malformed-power, and transmission decisions. The full 25,295-car audit found zero canonical conflicts/regressions; reprocessing as `normalization-remote-passenger-cars-only-v323-20260817` produced 10,483 resolved, 12,704 provisional, 2,108 review-required, and 0 failed. The established SQL delta now contains 684 cumulative definitions/905 overrides; 97 focused tests and Ruff pass. Auditing the 704 non-generic manual tail found no safe bulk rule: 678 have generic fab code `ÖV` and 687 Brand values are singletons; two GMC rows remain candidates for explicit manual approval.

## 2026-08-14 — Started local review frontend

- Started the FastAPI-hosted normalization review frontend from the SCRUM-95-98 worktree at `http://127.0.0.1:8000/normalization-review`. Verified the page returns HTTP 200 and `/health` reports PostgreSQL, Redis, Neo4j and Elasticsearch healthy. The Uvicorn process remains running locally; no repository or database changes were made.

## 2026-08-14 — Added TS-to-KType persistence and promotion Jira tasks

- Created SCRUM-171 under SCRUM-83 for immutable, evidence-gated TS-to-TecDoc KType decision persistence with versioning, idempotency, dry-run separation and integration coverage. Created SCRUM-170 for audited KType promotion and safe TS alias attachment with transactional uniqueness, PostgreSQL/Neo4j reconciliation, failure recovery and a controlled cohort; explicitly recorded its dependency on SCRUM-171. Both tasks are To Do and unassigned. No repository, database or graph behavior changed.

## 2026-08-14 — Integrated TS-to-KType technical-gate rerun

- Extended fuzzy KType candidates and TS queries with exact displacement/power evidence and made both numeric conflicts hard confidence-routing gates. Re-ran the retained 24,389 cohort read-only against all 55,808 KTypes using guarded manufacturer aliases plus normalized/raw model candidates: 2,366 normalization-review and 1,149 policy-excluded rows left 20,874 eligible; 5,728 were manufacturer-unmatched, 2 manufacturer-conflicted, 12,161 model-missing, and 2,983 were evaluated. Routing produced 222 resolved, 121 provisional and 2,640 review-required; hard gates retained 126 power, 49 year, 27 displacement, 9 fuel and 7 model-series conflicts. Forty-three focused unit/integration tests, Ruff and diff checks pass. No match decisions, aliases, commits or pushes were made.

## 2026-08-14 — Full-local model-family evidence audit

- Replayed the 127 accepted `MOD-*` rules (186 source terms) against all 568,469 latest locally retained vehicles after guarded TecDoc manufacturer resolution. Of 553,766 manufacturer-resolved vehicles, 311,011 retain raw model text and 199,408 match a reviewed model-family rule; 10,176 match a TecDoc family label exactly and 171,831 have compatible family-prefix evidence, while 17,401 rule hits remain unlinked. All 182,007 compatible vehicles reach at least one of the 55,808 KTypes through their compatible family: 2,363 have one family-stage KType candidate and 179,644 remain multi-candidate before technical gates. In the retained 24,389 review cohort specifically, 15,527 resolve manufacturer scope, only 3,196 retain raw model text, and reviewed model-family rules add 12 KType-compatible vehicles, all still multi-candidate. Another 111,603 model-bearing vehicles in the full-local set have no reviewed family rule, and 242,755 lack raw model text. Nine Škoda rule scopes still need a reviewed `Škoda` → TecDoc `SKODA` bridge. This was a read-only audit; no aliases or database rows were written.

## 2026-08-14 — Reviewed TS-to-TecDoc manufacturer mapping

- Added a guarded SCRUM-148 manufacturer index that combines native TecDoc names with unconditional reviewed TS aliases, creates canonical bridges such as TS `Volkswagen` to TecDoc `VW`, uses longest whole-token matching, preserves native catalog identity over cross-canonical rule collisions, retains field-level evidence, rejects conflicting source fields, and never broadens evidence-guarded rules. Across all 568,469 distinct locally retained vehicles it resolved 553,766 manufacturer scopes (97.41%), retained 3,864 conflicts and 10,839 unmatched; on the 20,874 matcher-eligible validation cohort it resolved 15,144 manufacturer scopes, while 16,572 records separately lacked model evidence. Fifty-seven focused matching/persistence/Neo4j tests and Ruff pass. No database mappings, aliases, commits or pushes were made.

## 2026-08-14 — Controlled TS-to-KType safety validation

- Ran the production fuzzy/phonetic/confidence gates as a read-only dry run over retained batch `normalization-local-review-24389-v322-20260813T180554Z` against 55,808 promoted KTypes. Of 24,389 records, 2,366 remained in normalization review, 1,149 were policy-excluded, and 20,874 were matcher-eligible; 228 routed resolved, 222 provisional, 3,071 review, and 17,353 lacked an exact TecDoc manufacturer/model scope. Hard gates blocked 205 year, 22 fuel and 2 model-series conflicts; all decisions had complete evidence and no plate mapped to multiple targets. Sixty focused PostgreSQL/Neo4j/unit tests passed. No aliases were written because all 55,808 KType targets are still `Provisional`, leaving zero live-resolvable targets under the documented safe alias contract.

## 2026-08-14 — Production normalization gaps documented

- Added `docs/NORMALIZATION_PRODUCTION_GAPS.md` as a concise handoff of completed normalization assets and remaining production work. It identifies the background worker, resumable checkpoints, incremental reprocessing, deployment ordering, manually triggered GitHub Action, and operational visibility as the remaining implementation, while preserving the 2,138 ambiguous records for restricted/manual review. No pipeline, database, or workflow behavior changed.
