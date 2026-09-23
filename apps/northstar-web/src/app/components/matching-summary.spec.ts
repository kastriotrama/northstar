/**
 * The Matching view: start a job for the Vehicles filter, poll it, read the gaps.
 * Mocked: the job's counts are fixtures; the point is the request and the lifecycle.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type { MatchSummaryJob, RuleCondition } from '../core/models';
import { MatchingSummary } from './matching-summary';

const VOLVO: RuleCondition[] = [
  { field: 'manufacturer', layer: 'normalized', operator: 'equals', values: ['Volvo'] },
];

function job(status: MatchSummaryJob['status'], evaluated: number): MatchSummaryJob {
  return {
    job_id: 'job-1',
    status,
    target: 1000,
    evaluated,
    seconds_elapsed: 12,
    error: null,
    summary: {
      catalog_batch: 'tecdoc-v5',
      population: 3722,
      evaluated,
      sampled: true,
      buckets: { one: 588, several: 246, none: 166, not_matchable: 0 },
      terminals: { resolved: 397 },
      several_candidate_counts: { '2': 213 },
      candidate_limit: 5,
      none_conflicting_fields: [{ field: 'bodywork', cars: 145 }],
      none_without_candidates: 0,
      several_separating_fields: [{ field: 'engine_code', cars: 137 }],
      several_missing_separating_fields: [{ field: 'engine_code', cars: 137 }],
      not_matchable_reasons: [],
      examples: {
        one: [],
        several: [
          { source_record_id: 769, plate: 'FLT946', manufacturer: 'Volvo', model_family: 'V70', candidates: 2 },
        ],
        none: [],
        not_matchable: [],
      },
    },
  };
}

function render() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(MatchingSummary);
  fixture.componentRef.setInput('conditions', VOLVO);
  fixture.componentRef.setInput('text', '');
  fixture.detectChanges();
  return fixture;
}

function run(fixture: ReturnType<typeof render>): void {
  const button = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
    (item) => item.textContent?.includes('Run matching'),
  ) as HTMLButtonElement;
  button.click();
  fixture.detectChanges();
}

describe('MatchingSummary', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('starts a job for exactly the filter the car list is showing', () => {
    const fixture = render();
    run(fixture);

    const start = TestBed.inject(HttpTestingController).expectOne(
      'http://api.test/v1/vehicles/matching/summary',
    );
    expect(start.request.method).toBe('POST');
    expect(start.request.body).toEqual({ conditions: VOLVO, text: '', limit: 2000 });
  });

  it('polls until the job settles, then stops', () => {
    const fixture = render();
    run(fixture);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('http://api.test/v1/vehicles/matching/summary').flush(job('running', 0));

    vi.advanceTimersByTime(1500);
    http.expectOne('http://api.test/v1/vehicles/matching/summary/job-1').flush(job('running', 400));
    vi.advanceTimersByTime(2000);
    http.expectOne('http://api.test/v1/vehicles/matching/summary/job-1').flush(job('done', 1000));
    vi.advanceTimersByTime(10_000);
    http.expectNone('http://api.test/v1/vehicles/matching/summary/job-1');

    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Done.');
    expect(text).toContain('not a random sample');
  });

  it('names the gaps, and says when the filter has moved on since the run', () => {
    const fixture = render();
    run(fixture);
    TestBed.inject(HttpTestingController)
      .expectOne('http://api.test/v1/vehicles/matching/summary')
      .flush(job('done', 1000));
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('engine_code');
    expect(host.textContent).toContain('bodywork');
    expect(host.textContent).not.toContain('The filter has changed');

    fixture.componentRef.setInput('text', 'xc60');
    fixture.detectChanges();
    expect(host.textContent).toContain('The filter has changed since this run.');
  });

  it('opens an example car by its plate', () => {
    const fixture = render();
    const picked: string[] = [];
    fixture.componentInstance.pick.subscribe((plate) => picked.push(plate));
    run(fixture);
    TestBed.inject(HttpTestingController)
      .expectOne('http://api.test/v1/vehicles/matching/summary')
      .flush(job('done', 1000));
    fixture.detectChanges();

    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('button.example')?.click();

    expect(picked).toEqual(['FLT946']);
  });

  it('cancels the running job', () => {
    const fixture = render();
    run(fixture);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne('http://api.test/v1/vehicles/matching/summary').flush(job('running', 10));
    fixture.detectChanges();

    const cancel = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (item) => item.textContent?.includes('Cancel'),
    ) as HTMLButtonElement;
    cancel.click();

    const request = http.expectOne('http://api.test/v1/vehicles/matching/summary/job-1');
    expect(request.request.method).toBe('DELETE');
  });
});
