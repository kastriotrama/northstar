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
import type { MatchSummaryJob, VehicleCondition } from '../core/models';
import { MatchingSummary } from './matching-summary';

const VOLVO: VehicleCondition[] = [
  { field: 'manufacturer', operator: 'equals', values: ['Volvo'] },
];
const LIST = 'http://api.test/v1/vehicles/matching/summary';

function job(
  status: MatchSummaryJob['status'],
  evaluated: number,
  overrides: Partial<MatchSummaryJob> = {},
): MatchSummaryJob {
  return {
    job_id: 'job-1',
    status,
    target: 1000,
    evaluated,
    seconds_elapsed: 12,
    error: null,
    // The API's own key order, not the one the page builds conditions in.
    filter: { conditions: [{ field: 'manufacturer', values: ['Volvo'], operator: 'equals' }], text: '' },
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
          {
            vehicle_id: 'NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G',
            source_record_id: 769,
            plate: 'FLT946',
            manufacturer: 'Volvo',
            model_family: 'V70',
            candidates: 2,
          },
        ],
        none: [],
        not_matchable: [],
      },
    },
    ...overrides,
  };
}

/** Opens the view; it first asks the API for runs already going. */
function render(serverJobs: MatchSummaryJob[] = []) {
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
  TestBed.inject(HttpTestingController).expectOne({ method: 'GET', url: LIST }).flush(serverJobs);
  fixture.detectChanges();
  return fixture;
}

function buttons(fixture: ReturnType<typeof render>, label: string): HTMLButtonElement[] {
  return [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].filter((item) =>
    item.textContent?.includes(label),
  );
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
    expect(text.replace(/\s+/g, ' ')).toContain('these are a random sample of');
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

  it('opens an example car by its NOR ID', () => {
    const fixture = render();
    const picked: string[] = [];
    fixture.componentInstance.pick.subscribe((plate) => picked.push(plate));
    run(fixture);
    TestBed.inject(HttpTestingController)
      .expectOne('http://api.test/v1/vehicles/matching/summary')
      .flush(job('done', 1000));
    fixture.detectChanges();

    (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>('button.example')?.click();

    expect(picked).toEqual(['NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G']);
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

  it('picks up a run already going when the view opens, and keeps polling it', async () => {
    const fixture = render([job('running', 400), job('done', 1000, { job_id: 'job-0' })]);
    const host = fixture.nativeElement as HTMLElement;
    await fixture.whenStable();

    expect(host.querySelector<HTMLInputElement>('input[type=number]')?.value).toBe('1000');
    expect(host.textContent).toContain('Picked up a run already going on the server');
    expect(host.textContent).toContain('400 of 1,000 evaluated');
    // Same filter, whatever key order the API echoed it in.
    expect(host.textContent).not.toContain('The filter has changed');

    vi.advanceTimersByTime(1500);
    TestBed.inject(HttpTestingController)
      .expectOne('http://api.test/v1/vehicles/matching/summary/job-1')
      .flush(job('running', 600));
    fixture.detectChanges();
    expect(host.textContent).toContain('600 of 1,000 evaluated');
  });

  it('when the running cap is hit, lists the runs holding it and cancels one', () => {
    const fixture = render();
    run(fixture);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne({ method: 'POST', url: LIST }).flush(
      { detail: '2 summaries are already running; wait for one or cancel it.' },
      { status: 429, statusText: 'Too Many Requests' },
    );
    const bmw = { conditions: [{ field: 'manufacturer', operator: 'equals' as const, values: ['BMW'] }], text: '' };
    http
      .expectOne({ method: 'GET', url: LIST })
      .flush([
        job('running', 300, { job_id: 'job-a', filter: bmw }),
        job('running', 900, { job_id: 'job-b' }),
      ]);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('2 summaries are already running');
    expect(host.textContent).toContain('Also running on the server');
    expect(host.textContent).toContain('manufacturer equals BMW');
    expect(host.textContent).toContain('300 of 1,000');

    const cancelA = host.querySelector<HTMLButtonElement>(
      'button[aria-label="Cancel the run on manufacturer equals BMW"]',
    );
    cancelA?.click();
    http.expectOne(`${LIST}/job-a`).flush(job('cancelled', 300, { job_id: 'job-a', filter: bmw }));
    http.expectOne({ method: 'GET', url: LIST }).flush([job('running', 950, { job_id: 'job-b' })]);
    fixture.detectChanges();

    expect(host.textContent).not.toContain('2 summaries are already running');
    expect(host.textContent).not.toContain('BMW');
    expect(buttons(fixture, 'Show')).toHaveLength(1);
  });

  it('shows a run from the list in place of the empty view', () => {
    const fixture = render();
    run(fixture);
    const http = TestBed.inject(HttpTestingController);
    http.expectOne({ method: 'POST', url: LIST }).flush(
      { detail: 'busy' },
      { status: 429, statusText: 'Too Many Requests' },
    );
    http.expectOne({ method: 'GET', url: LIST }).flush([job('running', 500, { job_id: 'job-b' })]);
    fixture.detectChanges();

    buttons(fixture, 'Show')[0].click();
    http.expectOne({ method: 'GET', url: LIST }).flush([job('running', 520, { job_id: 'job-b' })]);
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    expect(host.textContent).toContain('500 of 1,000 evaluated');
    expect(host.textContent).not.toContain('Also running on the server');
    expect(host.textContent).not.toContain('busy');
  });
});
