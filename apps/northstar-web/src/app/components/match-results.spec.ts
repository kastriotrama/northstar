/**
 * Matching statistics read from stored results: one request per filter, every
 * number a way into the cars behind it. Mocked: the overview is a fixture; the
 * point is what is asked for and how it reads.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type { MatchResultOverview, VehicleCondition } from '../core/models';
import { overviewFixture } from './match-result-fixtures';
import { MatchResults } from './match-results';

const OVERVIEW = 'http://api.test/v1/vehicles/match-results/overview';
const CARS = 'http://api.test/v1/vehicles/match-results/cars';
const VOLVO: VehicleCondition[] = [
  { field: 'manufacturer', operator: 'equals', values: ['Volvo'] },
];

function render() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(MatchResults);
  fixture.componentRef.setInput('conditions', VOLVO);
  fixture.componentRef.setInput('text', '');
  fixture.detectChanges();
  return fixture;
}

async function answer(
  fixture: ReturnType<typeof render>,
  body: MatchResultOverview | null,
): Promise<unknown> {
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne(OVERVIEW);
  if (body) request.flush(body);
  else request.flush('boom', { status: 503, statusText: 'Unavailable' });
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return request.request.body;
}

function text(fixture: ReturnType<typeof render>): string {
  return ((fixture.nativeElement as HTMLElement).textContent ?? '').replace(/\s+/g, ' ');
}

function tile(fixture: ReturnType<typeof render>, label: string): HTMLButtonElement {
  return [...(fixture.nativeElement as HTMLElement).querySelectorAll<HTMLButtonElement>('.tile')].find(
    (item) => item.textContent?.includes(label),
  ) as HTMLButtonElement;
}

/** The body of the cars request the dialog sent after a click. */
async function carsAsked(fixture: ReturnType<typeof render>): Promise<Record<string, unknown>> {
  fixture.detectChanges();
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne(CARS);
  return request.request.body as Record<string, unknown>;
}

describe('MatchResults', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('reads the overview for exactly the filter the car list is showing', async () => {
    const fixture = render();
    const asked = await answer(fixture, overviewFixture());

    expect(asked).toEqual({ conditions: VOLVO, text: '' });
    expect(text(fixture)).toContain('10,000 cars in this filter');
    expect(tile(fixture, 'Resolved').textContent).toContain('7,016');
    expect(tile(fixture, 'Resolved').textContent).toContain('70.2%');
    expect(tile(fixture, 'Several KTypes').textContent).toContain('1,646');
    expect(tile(fixture, 'One, not confirmed').textContent).toContain('397');
    expect(text(fixture)).toContain('the matcher is not run here');
  });

  it('names what would decide the tied cars and what the others conflict on', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    const words = text(fixture);
    expect(words).toContain('engine code 640 120');
    expect(words).toContain('year 1,100 0');
    expect(words).toContain('2: 1,064');
    expect(words).toContain('power 222');
    expect(words).toMatch(/no candidate above threshold ?72/);
    expect(words).toContain('The car has no model the matcher can use');
  });

  it('reads again when the filter changes, keeping the numbers until the new ones arrive', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    fixture.componentRef.setInput('text', 'V70');
    fixture.detectChanges();
    expect(tile(fixture, 'Resolved').textContent).toContain('7,016');
    const asked = await answer(fixture, overviewFixture({ total: 42 }));

    expect(asked).toEqual({ conditions: VOLVO, text: 'V70' });
    expect(text(fixture)).toContain('42 cars in this filter');
  });

  it('says so when cars have no stored result or changed since', async () => {
    const fixture = render();
    const base = overviewFixture();
    await answer(
      fixture,
      overviewFixture({
        changed_since_matched: 12,
        total: 500000,
        states: base.states.map((item) =>
          item.state === 'not_evaluated' ? { ...item, cars: 490000 } : item,
        ),
      }),
    );

    expect(text(fixture)).toContain('490,000 cars have no stored result yet');
    // Shares are of the cars that have a result, so a running fill reads as the finished one.
    expect(text(fixture)).toContain('percentages are of the 10,000 cars that have one');
    expect(tile(fixture, 'Resolved').textContent).toContain('70.2%');
    expect(tile(fixture, 'Not evaluated yet').textContent).toContain('98%');
    expect(text(fixture)).toContain('12 cars changed after they were matched');
  });

  it('opens the cars of a state from its tile', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    tile(fixture, 'Several KTypes').click();
    const asked = await carsAsked(fixture);

    expect(asked).toMatchObject({ conditions: VOLVO, text: '', state: 'several', limit: 100 });
    expect(asked['missing_field']).toBeUndefined();
  });

  it('opens only the cars lacking a field from its count', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    const link = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      'button[aria-label="Cars with no engine code"]',
    );
    link?.click();
    const asked = await carsAsked(fixture);

    expect(asked).toMatchObject({ state: 'several', missing_field: 'engine_code' });
  });

  it('opens the cars conflicting on a field and the cars stopped for a reason', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());
    const host = fixture.nativeElement as HTMLElement;

    host.querySelector<HTMLButtonElement>('button[aria-label="Cars conflicting on power"]')?.click();
    expect(await carsAsked(fixture)).toMatchObject({ state: 'none', conflicting_field: 'power_kw' });

    host
      .querySelector<HTMLButtonElement>(
        'button[aria-label="Cars not matchable because: model_evidence_missing"]',
      )
      ?.click();
    expect(await carsAsked(fixture)).toMatchObject({
      state: 'not_matchable',
      reason: 'model_evidence_missing',
    });
  });

  it('a state without cars cannot be opened', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    expect(tile(fixture, 'Chosen by a person').disabled).toBe(true);
    expect(tile(fixture, 'None of these')).toBeUndefined();
  });

  it('keeps the numbers on screen when a refresh fails', async () => {
    const fixture = render();
    await answer(fixture, overviewFixture());

    const refresh = [...(fixture.nativeElement as HTMLElement).querySelectorAll('button')].find(
      (item) => item.textContent?.includes('Refresh'),
    ) as HTMLButtonElement;
    refresh.click();
    await answer(fixture, null);

    expect(text(fixture)).toContain('Could not load the matching results.');
    expect(tile(fixture, 'Resolved').textContent).toContain('7,016');
  });
});
