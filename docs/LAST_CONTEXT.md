# Last Context

Keep the latest 10 task entries only.

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

## 2026-08-06 — Alpha review-required analysis

- Analyzed all 67 review-required records in isolated batch `normalization-external-test-alpha-1000-v1` without changing rules. Reasons are 40 missing manufacturers, 21 unknown legal entities, 3 unresolved corporate groups, 1 compound Brand, 1 motorhome evidence gap, and 1 bodywork conflict. Strong reviewed-rule candidates include recurring Brand prefixes (Chevrolet 11, Suzuki 6, Cadillac/Oldsmobile 3 each), legal-entity-to-Brand mappings (Citroën, Kia, Mazda, SAIC/MG, etc.), and PSA→Peugeot/FCA US→Jeep. Quattro/Audi, Rapido, Hymer, LMC, Renault/Adria, Volkswagen California, and Volkswagen Multivan need separate evidence or policy review.

## 2026-08-06 — Alpha 1,000-car isolated import

- Inspected `northstar_ts_normalization_alpha_1000_2026-08-06.xlsx`, confirmed its portable public-safe bundle contract, and imported it into the disposable `northstar_bundle_test` PostgreSQL database. Exact verification reproduced all 1,000 results: 43 resolved, 890 provisional, 67 review-required, and 0 failed. The running review dashboard and batch-filtered API return HTTP 200 with matching totals; the existing `app` database was not changed.

## 2026-08-06 — Three portable 1,000-car cohorts

- Added three mutually exclusive 1,000-passenger-car Excel bundles (`alpha`, `bravo`, and `charlie`) from distinct database batches. Every workbook contains sanitized TS source rows, its exact 1,000 normalized results, the active `ts-review-20260805T184254528647Z` rule version, all translation/manufacturer catalogs, and active overrides. All three imported together into a clean PostgreSQL database and reproduced 3,000/3,000 results with `verified=true`; ZIP integrity, synthetic plate/VIN checks, source/workbook non-overlap checks, and visual review pass.

## 2026-08-06 — Portable normalization test bundle

- Exported and visually verified `outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_normalization_test_bundle_2026-08-06.xlsx`: 250 sanitized TS staging records with deterministic synthetic plate/VIN values, 250 expected normalized results, 99 effective translation rules, the complete 184-row built-in manufacturer catalog, the 189-row effective runtime manufacturer view, 18 active database overrides, exact immutable rule-version metadata, and explicit Adria/Dethleffs exclusions. Added `import-normalization-bundle` to validate the workbook, populate an isolated PostgreSQL database, normalize, and compare every generated result; clean-database import and retry both reproduced 37 resolved, 212 provisional, 1 review-required, and 0 failed with `verified=true`. Validation: 371 tests, Ruff, strict mypy, compilation, XLSX archive checks, 205 golden cases, and a 500-identifier leak scan all pass.
