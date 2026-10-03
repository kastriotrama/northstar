/**
 * The cars behind a Matching run's counts: one bucket at a time, the picked car beside them.
 * Mocked: the pages of cars are fixtures; the point is which page is asked for and how it reads.
 */

import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type {
  MatchBucket,
  MatchCarPage,
  MatchCarRow,
  MatchSummaryJob,
  NorVehicleRecord,
} from '../core/models';
import { KTypeCandidates } from './ktype-candidates';
import { MatchCarsDialog } from './match-cars-dialog';

const CARS = 'http://api.test/v1/vehicles/matching/summary/job-1/cars';
const RECORD = 'http://api.test/v1/vehicles/';

/** A car's own record, as the Vehicles tab's record panel reads it. */
function record(vehicleId: string, engine = 'B4204T'): NorVehicleRecord {
  const origin = { source: 'transportstyrelsen', ref: null, observed_on: null, origin: true };
  return {
    vehicle_id: vehicleId,
    origin_source: 'transportstyrelsen',
    origin_observed_on: '2026-08-07',
    ts_record_id: 7,
    registry_status: 'registered',
    created_at: '2026-09-01T08:00:00Z',
    updated_at: '2026-10-01T09:30:00Z',
    fields: [
      { field: 'manufacturer', label: 'Manufacturer', group: 'make', value: 'Volvo', source: origin, alternatives: [] },
      {
        field: 'engine_code',
        label: 'Engine code',
        group: 'technical',
        value: engine,
        source: { source: 'ais', ref: null, observed_on: '2026-09-19', origin: false },
        alternatives: [],
      },
      { field: 'power_kw', label: 'Power (kW)', group: 'technical', value: 180, source: origin, alternatives: [] },
    ],
    identifiers: [],
    source_links: [],
    source_link_count: 1,
    rules: [],
  };
}

/** Answers the picked car's record request and lets the screen settle. */
async function answerRecord(
  fixture: ReturnType<typeof render>,
  vehicleId: string,
  body: NorVehicleRecord | null,
): Promise<void> {
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne(`${RECORD}${vehicleId}`);
  if (body) request.flush(body);
  else request.flush('boom', { status: 500, statusText: 'Server Error' });
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

/** The words of the car information block, a space between one piece of text and the next. */
function info(): string {
  const block = document.body.querySelector('details.info');
  if (!block) return '';
  const parts: string[] = [];
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const piece = (walker.currentNode.textContent ?? '').replace(/\s+/g, ' ').trim();
    if (piece) parts.push(piece);
  }
  return parts.join(' ');
}

function row(plate: string, overrides: Partial<MatchCarRow> = {}): MatchCarRow {
  return {
    vehicle_id: `NOR-${plate}`,
    source_record_id: 1,
    plate,
    manufacturer: 'Volvo',
    model_family: 'V70',
    bucket: 'several',
    terminal: 'review_required',
    candidates: 2,
    top_ktype: '000010064',
    verdict: 'The top candidates are too close to separate safely.',
    separating_fields: ['engine_code', 'power_kw'],
    missing_fields: ['engine_code'],
    conflicting_fields: [],
    reason_codes: ['route:candidate_margin_below_gate'],
    ...overrides,
  };
}

function page(bucket: MatchBucket, total: number, offset: number, cars: MatchCarRow[]): MatchCarPage {
  return { job_id: 'job-1', bucket, total, offset, cars };
}

function job(buckets: Partial<Record<MatchBucket, number>> = {}): MatchSummaryJob {
  return {
    job_id: 'job-1',
    status: 'done',
    target: 200,
    evaluated: 200,
    seconds_elapsed: 20,
    error: null,
    filter: null,
    summary: {
      catalog_batch: 'tecdoc-v5',
      population: 3722,
      evaluated: 200,
      sampled: true,
      buckets: { one: 90, several: 3, none: 12, not_matchable: 22, ...buckets },
      terminals: {},
      several_candidate_counts: {},
      candidate_limit: 5,
      none_conflicting_fields: [],
      none_without_candidates: 0,
      several_separating_fields: [],
      several_missing_separating_fields: [],
      not_matchable_reasons: [],
      examples: { one: [], several: [], none: [], not_matchable: [] },
    },
  };
}

function render(bucket: MatchBucket = 'several') {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(MatchCarsDialog);
  fixture.componentRef.setInput('job', job());
  fixture.componentRef.setInput('bucket', bucket);
  fixture.componentRef.setInput('visible', true);
  fixture.detectChanges();
  return fixture;
}

/** Answers the pending cars request; the picked car's own lookup is left unanswered. */
async function answer(fixture: ReturnType<typeof render>, body: MatchCarPage): Promise<string> {
  await fixture.whenStable();
  const request = TestBed.inject(HttpTestingController).expectOne((req) => req.url === CARS);
  request.flush(body);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return request.request.urlWithParams;
}

function text(): string {
  return (document.body.textContent ?? '').replace(/\s+/g, ' ');
}

describe('MatchCarsDialog', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('lists the bucket it opened on, saying what would decide each car', async () => {
    const fixture = render('several');
    const asked = await answer(fixture, page('several', 3, 0, [row('ABC123'), row('DEF456', { missing_fields: [] })]));

    expect(asked).toBe(`${CARS}?bucket=several&offset=0&limit=100`);
    const rows = [...document.body.querySelectorAll('tr.row')].map((item) => item.textContent?.replace(/\s+/g, ' ').trim());
    expect(rows[0]).toContain('ABC123');
    expect(rows[0]).toContain('car has no engine code');
    expect(rows[1]).toContain('differ on engine code, power');
    expect(text()).toContain('2 of 3 shown');
  });

  it('opens the first car beside the list, matched on its NOR ID', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123')]));
    await fixture.whenStable();

    TestBed.inject(HttpTestingController).expectOne(
      'http://api.test/v1/vehicles/matching/lookup?vehicle_id=NOR-ABC123',
    );
    expect(document.body.querySelector('ns-ktype-candidates')).toBeTruthy();
  });

  it('loads the next page after the cars already shown', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 3, 0, [row('ABC123'), row('DEF456')]));
    const http = TestBed.inject(HttpTestingController);
    http.match((req) => req.url.endsWith('/lookup'));

    const more = [...document.body.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.includes('Load more'));
    more?.click();
    const asked = await answer(fixture, page('several', 3, 2, [row('GHI789')]));

    expect(asked).toBe(`${CARS}?bucket=several&offset=2&limit=100`);
    expect(document.body.querySelectorAll('tr.row').length).toBe(3);
  });

  it('switches bucket in place and names what the cars conflict on', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123')]));

    const none = [...document.body.querySelectorAll<HTMLButtonElement>('button.tab')].find((item) => item.textContent?.includes('No KType'));
    none?.click();
    fixture.detectChanges();
    const asked = await answer(
      fixture,
      page('none', 1, 0, [row('XYZ999', { bucket: 'none', candidates: 0, conflicting_fields: ['bodywork'] })]),
    );

    expect(asked).toBe(`${CARS}?bucket=none&offset=0&limit=100`);
    expect(text()).toContain('Conflicts on');
    expect(text()).toContain('body type');
  });

  it('shows the picked car’s own information beside the list, without opening the car', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123')]));
    expect(info()).toContain('Loading…');

    await answerRecord(fixture, 'NOR-ABC123', record('NOR-ABC123'));

    expect(info()).toContain('Car information');
    expect(info()).toContain('NOR ID NOR-ABC123');
    expect(info()).toContain('Manufacturer Volvo');
    expect(info()).toContain('Engine code B4204T AIS');
    expect(info()).toContain('Power (kW) 180');
    // The information sits above the car's KTypes, in the same pane.
    const detail = document.body.querySelector('section.detail') as HTMLElement;
    const order = [...detail.children].map((item) => item.tagName.toLowerCase());
    expect(order.indexOf('details')).toBeLessThan(order.indexOf('ns-ktype-candidates'));
  });

  it('reads the other car’s information when another row is picked', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 2, 0, [row('ABC123'), row('DEF456')]));
    await answerRecord(fixture, 'NOR-ABC123', record('NOR-ABC123'));

    document.body.querySelectorAll<HTMLTableRowElement>('tr.row')[1].click();
    fixture.detectChanges();
    // The first car's information is not left on screen under the second car's name.
    expect(info()).not.toContain('NOR-ABC123');
    expect(info()).toContain('Loading…');

    await answerRecord(fixture, 'NOR-DEF456', record('NOR-DEF456', 'D5244T'));
    expect(info()).toContain('NOR ID NOR-DEF456');
    expect(info()).toContain('Engine code D5244T');
  });

  it('says so when the car’s information cannot be loaded, and still shows its KTypes', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123')]));

    await answerRecord(fixture, 'NOR-ABC123', null);

    expect(info()).toContain("Could not load this car's information.");
    expect(document.body.querySelector('details.info [role="alert"]')).toBeTruthy();
    expect(document.body.querySelector('ns-ktype-candidates')).toBeTruthy();
  });

  it('reads the information again after a choice or correction, keeping it on screen meanwhile', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123')]));
    await answerRecord(fixture, 'NOR-ABC123', record('NOR-ABC123'));

    fixture.debugElement.query(By.directive(KTypeCandidates)).triggerEventHandler('choiceChanged', {});
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    // The old values stay until the new record arrives: no flash of "Loading…".
    expect(info()).toContain('Engine code B4204T');
    expect(info()).not.toContain('Loading…');

    await answerRecord(fixture, 'NOR-ABC123', record('NOR-ABC123', 'B4204T9'));
    expect(info()).toContain('Engine code B4204T9');
  });

  it('asks for no record for a car that has no NorthStar vehicle', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 1, 0, [row('ABC123', { vehicle_id: null })]));
    await fixture.whenStable();

    TestBed.inject(HttpTestingController).expectNone((req) => req.url.startsWith(RECORD) && !req.url.includes('/matching/'));
    expect(document.body.querySelector('details.info')).toBeNull();
  });

  it('offers a refresh once the run has evaluated more cars of the bucket', async () => {
    const fixture = render('several');
    await answer(fixture, page('several', 3, 0, [row('ABC123')]));

    fixture.componentRef.setInput('job', job({ several: 5 }));
    fixture.detectChanges();

    expect(text()).toContain('2 more evaluated since');
    // A new job object for the same run does not reload by itself.
    TestBed.inject(HttpTestingController).expectNone((req) => req.url === CARS);
  });
});
