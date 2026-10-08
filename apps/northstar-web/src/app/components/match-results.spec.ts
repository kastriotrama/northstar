/**
 * The matching results above the car list: counts per state from one request,
 * the breakdown only when opened, and every number a filter of the list.
 * Mocked: the counts and the overview are fixtures; the point is what is asked
 * for and what a click hands the page.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type {
  MatchResultCause,
  MatchResultCounts,
  MatchResultOverview,
  MatchResultState,
  VehicleCondition,
} from '../core/models';
import { countsFixture, overviewFixture } from './match-result-fixtures';
import { MatchResults } from './match-results';

const COUNTS = 'http://api.test/v1/vehicles/match-results/counts';
const OVERVIEW = 'http://api.test/v1/vehicles/match-results/overview';
const REFRESH = 'http://api.test/v1/vehicles/match-results/refresh';
const VOLVO: VehicleCondition[] = [
  { field: 'manufacturer', operator: 'equals', values: ['Volvo'] },
];

function render(selected: MatchResultState | '' = '') {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(MatchResults);
  fixture.componentRef.setInput('conditions', VOLVO);
  fixture.componentRef.setInput('text', '');
  fixture.componentRef.setInput('selected', selected);
  const states: Array<MatchResultState | ''> = [];
  const causes: MatchResultCause[] = [];
  fixture.componentInstance.stateChange.subscribe((state) => states.push(state));
  fixture.componentInstance.causeChange.subscribe((cause) => causes.push(cause));
  fixture.detectChanges();
  return { fixture, states, causes };
}

type Rendered = ReturnType<typeof render>['fixture'];

async function answer<T>(fixture: Rendered, url: string, body: T | null): Promise<unknown> {
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne(url);
  if (body) request.flush(body);
  else request.flush('boom', { status: 503, statusText: 'Unavailable' });
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return request.request.body;
}

const counts = (fixture: Rendered, body: MatchResultCounts | null = countsFixture()) =>
  answer(fixture, COUNTS, body);

/** Opens the breakdown and answers its request. */
async function openBreakdown(
  fixture: Rendered,
  body: MatchResultOverview | null = overviewFixture(),
): Promise<unknown> {
  const details = (fixture.nativeElement as HTMLElement).querySelector('details') as HTMLDetailsElement;
  details.open = true;
  details.dispatchEvent(new Event('toggle'));
  fixture.detectChanges();
  return answer(fixture, OVERVIEW, body);
}

function text(fixture: Rendered): string {
  return ((fixture.nativeElement as HTMLElement).textContent ?? '').replace(/\s+/g, ' ');
}

function state(fixture: Rendered, label: string): HTMLButtonElement {
  return [...(fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.state')].find(
    (item) => item.textContent?.includes(label),
  ) as HTMLButtonElement;
}

function click(fixture: Rendered, ariaLabel: string): void {
  (fixture.nativeElement as HTMLElement)
    .querySelector<HTMLButtonElement>(`button[aria-label="${ariaLabel}"]`)
    ?.click();
}

describe('MatchResults', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('counts the cars of the filter per state without asking for the breakdown', async () => {
    const { fixture } = render();
    const asked = await counts(fixture);

    expect(asked).toEqual({ conditions: VOLVO, text: '' });
    expect(state(fixture, 'Resolved').textContent).toContain('7,016');
    expect(state(fixture, 'Resolved').textContent).toContain('70.2%');
    expect(state(fixture, 'Several KTypes').textContent).toContain('1,646');
    expect(state(fixture, 'One, not confirmed').textContent).toContain('397');
    // States nobody is in are left out, except the matcher's own.
    expect(state(fixture, 'Chosen by a person')).toBeUndefined();
    TestBed.inject(HttpTestingController).expectNone(OVERVIEW);
  });

  it('a state narrows the list to its cars, and picking it again shows all', async () => {
    const { fixture, states } = render();
    await counts(fixture);

    state(fixture, 'Several KTypes').click();
    expect(states).toEqual(['several']);

    fixture.componentRef.setInput('selected', 'several');
    fixture.detectChanges();
    expect(state(fixture, 'Several KTypes').getAttribute('aria-pressed')).toBe('true');
    state(fixture, 'Several KTypes').click();
    expect(states).toEqual(['several', '']);
  });

  it('reads the counts again when the filter changes or a car was decided', async () => {
    const { fixture } = render();
    await counts(fixture);

    fixture.componentRef.setInput('text', 'V70');
    fixture.detectChanges();
    expect(state(fixture, 'Resolved').textContent).toContain('7,016');
    expect(await counts(fixture, countsFixture({ total: 42 }))).toEqual({
      conditions: VOLVO,
      text: 'V70',
    });

    fixture.componentRef.setInput('turn', 1);
    fixture.detectChanges();
    await counts(fixture);
  });

  it('says so when cars have no stored result or changed since', async () => {
    const { fixture } = render();
    const base = countsFixture();
    await counts(
      fixture,
      countsFixture({
        total: 500000,
        changed_since_matched: 12,
        states: base.states.map((item) =>
          item.state === 'not_evaluated' ? { ...item, cars: 490000 } : item,
        ),
      }),
    );

    expect(text(fixture)).toContain('490,000 cars have no stored result yet');
    // Shares are of the cars that have a result, so a running fill reads as the finished one.
    expect(text(fixture)).toContain('percentages are of the 10,000 cars that have one');
    expect(state(fixture, 'Resolved').textContent).toContain('70.2%');
    expect(state(fixture, 'Not evaluated yet').textContent).toContain('98%');
    expect(text(fixture)).toContain('12 cars changed after they were matched');
  });

  it('reads the breakdown when it is opened and names what stands in the way', async () => {
    const { fixture } = render();
    await counts(fixture);
    const asked = await openBreakdown(fixture);

    expect(asked).toEqual({ conditions: VOLVO, text: '' });
    const words = text(fixture);
    expect(words).toContain('engine code 640 120');
    expect(words).toContain('year 1,100 0');
    expect(words).toContain('2: 1,064');
    expect(words).toContain('power 222');
    expect(words).toMatch(/no candidate above threshold ?72/);
    expect(words).toContain('The car has no model the matcher can use');
    expect(words).toContain('the matcher is not run here');
  });

  it('each count of the breakdown narrows the list to exactly those cars', async () => {
    const { fixture, causes } = render();
    await counts(fixture);
    await openBreakdown(fixture);

    click(fixture, 'Cars with no engine code');
    click(fixture, 'Cars whose KTypes differ on year');
    click(fixture, 'Cars conflicting on power');
    click(fixture, 'Cars not matchable because: model_evidence_missing');
    click(fixture, 'Cars with 5+ possible KTypes');
    click(fixture, 'Cars matched to the only KType that fits');

    expect(causes.map(({ state: of, field, value }) => [of, field, value])).toEqual([
      ['several', 'match_missing_field', 'engine_code'],
      ['several', 'match_separating_field', 'year'],
      ['none', 'match_conflicting_field', 'power_kw'],
      ['not_matchable', 'match_reason', 'model_evidence_missing'],
      ['several', 'match_candidate_count', '5'],
      ['resolved', 'match_reason', 'candidate_only_sole_fit'],
    ]);
    expect(causes[0].label).toBe('the car has no engine code');
  });

  it('keeps the numbers on screen when reading them again fails', async () => {
    const { fixture } = render();
    await counts(fixture);

    fixture.componentRef.setInput('turn', 1);
    fixture.detectChanges();
    await counts(fixture, null);

    expect(text(fixture)).toContain('Could not load the matching results.');
    expect(state(fixture, 'Resolved').textContent).toContain('7,016');
  });

  it('has the server match the changed cars again and follows it until it is done', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { fixture } = render();
      let refreshed = 0;
      fixture.componentInstance.refreshed.subscribe(() => refreshed++);
      await counts(fixture, countsFixture({ changed_since_matched: 1888 }));
      const http = TestBed.inject(HttpTestingController);
      const again = () =>
        [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find((item) =>
          item.textContent?.includes('Match them again now'),
        ) as HTMLButtonElement | undefined;

      again()?.click();
      fixture.detectChanges();
      http.expectOne({ method: 'POST', url: REFRESH }).flush({ started: true, refreshing: true });
      fixture.detectChanges();
      expect(text(fixture)).toContain('Matching them again');
      expect(again()).toBeUndefined();

      // It reads the counts again while the server works ...
      await vi.advanceTimersByTimeAsync(4100);
      await counts(fixture, countsFixture({ changed_since_matched: 900, refreshing: true }));
      expect(text(fixture)).toContain('900 cars changed after they were matched');
      expect(refreshed).toBe(0);

      // ... and stops, and says so, when it is done.
      await vi.advanceTimersByTimeAsync(4100);
      await counts(fixture, countsFixture({ changed_since_matched: 0, refreshing: false }));
      expect(text(fixture)).not.toContain('changed after they were matched');
      expect(refreshed).toBe(1);
      await vi.advanceTimersByTimeAsync(9000);
      http.expectNone(COUNTS);
    } finally {
      vi.useRealTimers();
    }
  });
  it('says when the cars were counted and reads the new count once it is there', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { fixture } = render();
      // A kept count of every car, behind since cars were matched: a new one is on its way.
      await counts(
        fixture,
        countsFixture({ counted_at: '2026-10-08T09:30:00', updating: true }),
      );
      expect(text(fixture)).toContain('counted 09:30');
      expect(text(fixture)).toContain('counting again');
      expect(state(fixture, 'Resolved').textContent).toContain('7,016');

      await vi.advanceTimersByTimeAsync(4100);
      await counts(
        fixture,
        countsFixture({ total: 9000, counted_at: '2026-10-08T09:31:00', updating: false }),
      );
      expect(text(fixture)).toContain('All 9,000 cars of this filter, counted 09:31');
      expect(text(fixture)).not.toContain('counting again');
      await vi.advanceTimersByTimeAsync(9000);
      TestBed.inject(HttpTestingController).expectNone(COUNTS);
    } finally {
      vi.useRealTimers();
    }
  });
});

