/**
 * The cars behind one number of the stored matching overview. Mocked: the pages
 * of cars are fixtures; the point is which page is asked for and how a row reads.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type {
  MatchResultCarPage,
  MatchResultNarrowing,
  MatchResultState,
  VehicleCondition,
} from '../core/models';
import { MatchResultCarsDialog } from './match-result-cars-dialog';
import { carFixture, carPageFixture, overviewFixture } from './match-result-fixtures';

const CARS = 'http://api.test/v1/vehicles/match-results/cars';
const VOLVO: VehicleCondition[] = [
  { field: 'manufacturer', operator: 'equals', values: ['Volvo'] },
];

function render(state: MatchResultState = 'several', narrowing: MatchResultNarrowing = {}) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(MatchResultCarsDialog);
  fixture.componentRef.setInput('conditions', VOLVO);
  fixture.componentRef.setInput('text', '');
  fixture.componentRef.setInput('overview', overviewFixture());
  fixture.componentRef.setInput('state', state);
  fixture.componentRef.setInput('narrowing', narrowing);
  fixture.componentRef.setInput('visible', true);
  fixture.detectChanges();
  return fixture;
}

/** Answers the pending cars request; the picked car's own requests are left unanswered. */
async function answer(
  fixture: ReturnType<typeof render>,
  body: MatchResultCarPage,
): Promise<Record<string, unknown>> {
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne(CARS);
  request.flush(body);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return request.request.body as Record<string, unknown>;
}

function rows(): string[] {
  return [...document.body.querySelectorAll('tr.row')].map((item) =>
    (item.textContent ?? '').replace(/\s+/g, ' ').trim(),
  );
}

function text(): string {
  return (document.body.textContent ?? '').replace(/\s+/g, ' ');
}

describe('MatchResultCarsDialog', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('lists the state it opened on with each car and its possible KTypes', async () => {
    const fixture = render('several');
    const asked = await answer(
      fixture,
      carPageFixture('several', 3, [carFixture('ABC123'), carFixture('DEF456', { missing_fields: [] })]),
    );

    expect(asked).toEqual({ conditions: VOLVO, text: '', state: 'several', after: null, limit: 100 });
    const shown = rows();
    expect(shown[0]).toContain('ABC123');
    expect(shown[0]).toContain('000010064 91%');
    expect(shown[0]).toContain('000010065 90%');
    expect(shown[0]).toContain('car has no engine code');
    expect(shown[1]).toContain('differ on engine code, power');
    expect(text()).toContain('2 of 3 shown');
  });

  it('asks only for the cars of the cause it was opened with', async () => {
    const fixture = render('several', { missing_field: 'drive_type' });
    const asked = await answer(fixture, carPageFixture('several', 0, []));

    expect(asked).toMatchObject({ state: 'several', missing_field: 'drive_type' });
    expect(text()).toContain('Only cars where the car has no drive type');
    expect(text()).toContain('No cars here.');
  });

  it('shows the accepted KType of a resolved car, and that others fit too', async () => {
    const fixture = render('resolved');
    await answer(
      fixture,
      carPageFixture('resolved', 1, [
        carFixture('GLF001', {
          state: 'resolved',
          automatic_state: 'resolved',
          terminal: 'resolved',
          ktype: '000010064',
          automatic_ktype: '000010064',
        }),
      ]),
    );

    const shown = rows()[0];
    expect(shown).toContain('000010064 91%');
    expect(shown).not.toContain('000010065');
    expect(shown).toContain('2 KTypes fit; the matcher accepted this one');
  });

  it('says what a car without a KType conflicts on, and when a result may be out of date', async () => {
    const fixture = render('none');
    await answer(
      fixture,
      carPageFixture('none', 2, [
        carFixture('AAA111', {
          state: 'none',
          automatic_state: 'none',
          terminal: 'hard_conflict',
          candidate_ktypes: [],
          candidate_confidences: [],
          conflicting_fields: ['power_kw'],
          changed_since_matched: true,
        }),
        carFixture('BBB222', {
          state: 'none',
          automatic_state: 'none',
          candidate_ktypes: [],
          candidate_confidences: [],
          best_candidate_ktype: null,
          confidence: null,
        }),
      ]),
    );

    const shown = rows();
    expect(shown[0]).toContain('power');
    expect(shown[0]).toContain('may be out of date');
    expect(shown[1]).toContain('no candidate above threshold');
  });

  it('loads the next page after the last car shown', async () => {
    const fixture = render('several');
    await answer(
      fixture,
      carPageFixture('several', 3, [carFixture('ABC123'), carFixture('DEF456')], 'NOR-DEF456'),
    );
    const more = [...document.body.querySelectorAll('button')].find((item) =>
      item.textContent?.includes('Load more'),
    ) as HTMLButtonElement;
    more.click();
    const asked = await answer(fixture, carPageFixture('several', 3, [carFixture('GHI789')]));

    expect(asked).toMatchObject({ state: 'several', after: 'NOR-DEF456' });
    expect(rows()).toHaveLength(3);
    expect(text()).toContain('3 of 3 shown');
  });

  it('switching state shows the whole state, without the earlier cause', async () => {
    const fixture = render('several', { missing_field: 'drive_type' });
    await answer(fixture, carPageFixture('several', 0, []));

    const tab = [...document.body.querySelectorAll<HTMLButtonElement>('button[role="tab"]')].find(
      (item) => item.textContent?.includes('No KType'),
    ) as HTMLButtonElement;
    expect(tab.textContent).toContain('573');
    tab.click();
    fixture.detectChanges();
    const asked = await answer(fixture, carPageFixture('none', 0, []));

    expect(asked).toMatchObject({ state: 'none' });
    expect(asked['missing_field']).toBeUndefined();
  });

  it('matches only the picked car live, on its NOR ID', async () => {
    const fixture = render('several');
    await answer(fixture, carPageFixture('several', 1, [carFixture('ABC123')]));
    await fixture.whenStable();

    TestBed.inject(HttpTestingController).expectOne(
      'http://api.test/v1/vehicles/matching/lookup?vehicle_id=NOR-ABC123',
    );
    expect(document.body.querySelector('ns-ktype-candidates')).toBeTruthy();
  });
});
