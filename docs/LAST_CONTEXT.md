# Last Context

Keep the latest 10 task entries only.

## 2026-08-06 — Three 5,000-car passenger batches loaded

- Loaded and normalized three disjoint passenger-only cohorts with latest immutable rules `ts-review-20260806T170328350936Z`: Atlas produced 345 resolved, 4,600 provisional, 55 review-required; Borealis 331/4,560/109; Charlie-plus 351/4,539/110; every batch has 5,000 completed records and 0 failed. Charlie-plus contains all 1,000 original Charlie workbook vehicles plus 4,000 fresh vehicles; exact plate/VIN membership and zero cross-batch overlap were verified. Exported all three as public-safe portable Excel bundles containing sanitized TS evidence, exact normalized results, 99 translation rules, 184 base manufacturer entities, 302 effective entities, and 67 active overrides; each workbook passed a clean-database import with exact 5,000-result verification.

## 2026-08-06 — Bravo manufacturer review reduced to four

- Activated immutable version `ts-review-20260806T170328350936Z` with 16 approved general manufacturer definitions: Brand-confirmed Peugeot/JLR/Maxus/FCA mappings, Magyar Suzuki, Adria converter/base handling, reviewed Brand parents, and an explicitly enabled compact-prefix mode for joined TS names. Disposable and local 1,000-car reprocessing both changed exactly 22 review cases, preserved every prior non-review status, and produced 67 resolved, 929 provisional, 4 review-required, and 0 failed. The remaining Acadian, BG-Hot, Miller-Maryland, and Stefans Ford Roadster records stay held for evidence.

## 2026-08-06 — Reproducible reviewed-rule SQL pattern

- Added `northstar-ingest export-rule-delta` so immutable PostgreSQL rule versions—not hand-edited SQL—generate deterministic baseline-to-latest deployment artifacts. The generator emits canonical JSON, SHA-256, exact version/note/timestamp, catalog and removal guards, locking, idempotent conflict checks, and verification output. Generated `northstar_latest_reviewed_rule_delta.sql` for 33 definitions/53 total overrides; fresh-database install, idempotent retry, and Bravo reprocessing reproduce 62 resolved, 912 provisional, 26 review-required, and 0 failed. All 376 tests, Ruff, strict mypy, compilation, and 205 golden cases pass.

## 2026-08-06 — Alpha rule delta applied to Bravo

- Fast-forwarded PR #27 to collaborator commit `4ddc06b`, reviewed its 33-definition idempotent SQL delta, and validated it against Bravo in a disposable database before installing immutable version `ts-review-20260806T133621914615Z` locally. Reprocessing Bravo improved 59/855/86/0 to 62 resolved, 912 provisional, 26 review-required, and 0 failed; 60 review cases cleared with zero status regressions outside the review queue. The live dashboard is running on port 8765 with the new batch; 15 missing, 10 unknown, and 1 FCA Italy corporate-group case remain.

## 2026-08-06 — Renault/Adria decision activated

- Activated isolated immutable version `ts-review-20260806T133621914615Z` for exact compound Brand `RENAULT ADRIA MOBIL`: Renault is the normalized manufacturer and Adria is retained as converter. Reprocessing changed only that record, made it resolved, and moved alpha totals to 70 resolved, 926 provisional, 4 review-required, and 0 failed. Updated the portable SQL delta to 33 definitions/53 total overrides and verified its idempotent target content; remaining stakeholder decisions are Great Wall/ORA, Rapido, Volkswagen California, and Volkswagen Multivan.

## 2026-08-06 — Portable reviewed-rule SQL delta

- Exported `northstar_alpha_reviewed_rule_delta_2026-08-06.sql`, a reviewable idempotent delta from workbook baseline `ts-review-20260805T184254528647Z` to consolidated version `ts-review-20260806T132835180824Z`. It contains exactly 32 added/extended definitions, validates baseline/target content, refuses unexpected newer versions, and includes verification output. Clean disposable-database validation imported the original 1,000-row workbook, installed the SQL, verified 52 total overrides/32 delta definitions, and safely reprocessed to 69 resolved, 926 provisional, 5 review-required, and 0 failed; idempotent retry also passed.

## 2026-08-06 — Hymer, LMC, and Quattro rules

- Activated isolated immutable version `ts-review-20260806T132835180824Z` with Hymer and LMC converter rules using explicit base manufacturers plus an exact reviewed `QUATTRO 42` → Audi exception supported by Audi R8/WUA evidence. Reprocessed alpha into the `...-rules-20260806T132841820522Z` batch: Hymer now yields Mercedes-Benz + Hymer converter, LMC yields Fiat + LMC converter, and Quattro 42 yields Audi; all three became provisional, review-required fell 8→5, and exactly those three records changed. Multivan already has Volkswagen and needs bodywork policy; Renault/Adria remains pending the main-versus-base manufacturer decision.

## 2026-08-06 — Safe alpha manufacturer rules activated

- Activated isolated immutable version `ts-review-20260806T132146001149Z`: 15 reviewed whole-token Brand parents, 9 punctuation-tolerant legal-manufacturer prefixes, PSA→Peugeot/FCA US→Jeep child allow-listing, and two SAIC→MG evidence-gated corporate rules. Reprocessed alpha into `normalization-external-test-alpha-1000-v1-rules-20260806T131616282916Z-rules-20260806T132156657810Z`: 50→69 resolved, 894→923 provisional, 56→8 review-required, and 0 failed. Exactly the intended 48 prior review records changed; no other-status record changed. Eight ambiguous converter, compound, bodywork, motorhome, Quattro/Audi, and Great Wall/ORA cases remain intentionally unresolved.

## 2026-08-06 — Chevrolet parent rule activated

- Directly activated immutable isolated-DB version `ts-review-20260806T131607358843Z` with reviewed Brand parent `CHEVROLET` → Chevrolet using complete whole-token-prefix matching and 13 distinct reviewed values covering 14 observed rows. Reprocessed alpha into `normalization-external-test-alpha-1000-v1-rules-20260806T131616282916Z`: 43→50 resolved, 890→894 provisional, 67→56 review-required, and 0 failed. All 11 formerly blocked Chevrolet rows now normalize to Chevrolet; exactly those 11 changed and zero non-Chevrolet records changed.

## 2026-08-06 — Chevrolet general-rule analysis

- Inspected active entities and every Chevrolet Brand row in `northstar_bundle_test` without changing rules. The database has only three exact built-in Chevrolet mappings (`CHEVROLET IMPALA`, `CHEVROLET KL1T`, `CHEVROLET VAN`); 11 alpha rows remain review-required despite all 14 observed values beginning with the complete `CHEVROLET` token and none containing it elsewhere. Recommended replacing scattered exact handling with one reviewed `CHEVROLET` → Chevrolet parent Brand entity using whole-token-prefix matching: reviewed examples resolve, unseen prefixed values remain provisional, and arbitrary contains/substring matching stays disallowed.
