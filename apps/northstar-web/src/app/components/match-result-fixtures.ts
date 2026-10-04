/** Fixtures shared by the stored match result specs. Test-only. */

import type { MatchResultCounts, MatchResultOverview } from '../core/models';

export function overviewFixture(overrides: Partial<MatchResultOverview> = {}): MatchResultOverview {
  return {
    total: 10000,
    states: [
      { state: 'resolved', cars: 7016 },
      { state: 'several', cars: 1646 },
      { state: 'one_unconfirmed', cars: 397 },
      { state: 'none', cars: 573 },
      { state: 'not_matchable', cars: 368 },
      { state: 'chosen', cars: 0 },
      { state: 'chosen_none', cars: 0 },
      { state: 'not_evaluated', cars: 0 },
    ],
    terminals: [
      { value: 'resolved', cars: 7016 },
      { value: 'review_required', cars: 2100 },
    ],
    several_candidate_counts: { '2': 1064, '3': 329, '5+': 130 },
    candidate_limit: 5,
    several_separating_fields: [
      { field: 'year', cars: 1100 },
      { field: 'engine_code', cars: 640 },
    ],
    several_missing_fields: [{ field: 'engine_code', cars: 120 }],
    none_conflicting_fields: [{ field: 'power_kw', cars: 222 }],
    none_without_candidates: 72,
    not_matchable_reasons: [{ reason: 'model_evidence_missing', cars: 232 }],
    changed_since_matched: 0,
    catalog_batches: [{ value: 'tecdoc-v4', cars: 10000 }],
    matcher_versions: [{ value: 'build-1', cars: 10000 }],
    latest_run: {
      run_id: 'run-1',
      mode: 'stale',
      status: 'completed',
      catalog_batch: 'tecdoc-v4',
      matcher_version: 'build-1',
      target: 10000,
      evaluated: 10000,
      unchanged: 0,
      started_at: '2026-10-03T14:00:00Z',
      finished_at: '2026-10-03T14:08:00Z',
    },
    ...overrides,
  };
}

export function countsFixture(overrides: Partial<MatchResultCounts> = {}): MatchResultCounts {
  return { total: 10000, states: overviewFixture().states, changed_since_matched: 0, ...overrides };
}
