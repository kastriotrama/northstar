# SCRUM-101: remove repeated matcher work

Local changes on `feature/SCRUM-101-integrated-matcher-validation`.

## Software changes

- Skip the second bodywork scoring pass when its configured multiplier is 1.0, or when the query has no bodywork. The first pass still records bodywork matches, missing evidence and conflicts with the existing bonuses/penalties. Non-unit configured weights retain the two-pass behavior.
- Cache only model-label text comparisons, bounded to 100,000 entries. Keys include query model, candidate model and aliases, edit/token weights and discovery threshold. Different catalog labels or scoring settings cannot reuse an incompatible entry. Year, fuel, engine, displacement, power, drive, bodywork, margins and final decisions remain outside this cache.

These are execution optimizations, not new matching rules. They are not expected to reduce review-required counts or authorize new aliases. No manufacturer/model/bodywork rules, acceptance thresholds, PostgreSQL decisions or Neo4j data were changed.

## Verification

- 598 unit tests passed, including 26 new parameterized regression cases. Ruff, strict mypy over 112 files and whitespace checks passed.
- Tests verify bodywork scoring-call reduction from four to two for two plausible candidates at unit weight, unchanged conflict evidence, retained non-unit rescoring, cache key isolation, and cached/uncached result equivalence.
- Read-only replay of the 24 priority changed-resolved cases against the pinned 72,570-candidate catalog returned identical complete evaluations between reference and optimized implementations. All terminals and KTypes also agree with the earlier private review packet.
- The small 24-case timing was 2.187 seconds reference versus 2.247 seconds optimized: no measured speed improvement in that small sample. Do not extrapolate it to full-scale throughput.
- Reference benchmarks use the current source with just the new model-label cache and unit-weight rescore bypass disabled. This isolates these optimizations from the earlier integration changes. Local reports are ignored by Git and contain checksums, counts and timings, not plates.

The first 1,000 retained TS rows also returned identical complete evaluations in all four passes (reference, optimized, optimized, reference). Initial passes took 64.400 seconds reference and 53.109 seconds optimized: 17.5% less elapsed time in this local measurement. Sequential execution and shared process caches can influence timings; this is not a controlled full-scale throughput guarantee. Repeat passes took 2.503 and 2.313 seconds, respectively, but include the evaluator's existing decision-cache hits and must not be interpreted as uncached scoring throughput.

Evidence: `outputs/scrum101-work-reuse-benchmark-24-20260828.json` and `outputs/scrum101-work-reuse-benchmark-1000-20260828.json`. Neither sample is independent accuracy adjudication. Database integration and full-scale 6.5M processing were not rerun in this task because there are no persistence changes.

## Coverage work still required

The previous 20k result remains 1,510 resolved, 1,551 provisional, 15,519 review-required, 1,307 hard conflicts, 112 policy exclusions and one normalization review. Do not describe fewer repeated computations as additional accepted vehicles. Improving coverage still requires evidence-backed model recovery and scoped bodywork decisions; the pending estate/SUV ontology pairs and 24 changed-resolved cases have not been approved.
