/**
 * Vehicles reads NorthStar vehicles by NOR ID: merged values, where each came from,
 * and the plates a car has carried. Mocked: the point is what reaches the screen and
 * what goes over the wire.
 */

import { By } from '@angular/platform-browser';
import { KTypeCandidates } from '../../components/ktype-candidates';
import type { VehicleMatchLookup } from '../../core/models';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../../core/api-config';
import type { NorVehiclePage, NorVehicleRecord } from '../../core/models';
import { CarSearchPage as CarSearchComponent } from './car-search';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const globals = globalThis as Record<string, unknown>;
globals['ResizeObserver'] ??= ResizeObserverStub;
globals['IntersectionObserver'] ??= ResizeObserverStub;

const API_BASE = 'http://api.test';

const VEHICLE_ID = 'NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G';

const PAGE: NorVehiclePage = {
  matched_rows: 1,
  next_cursor: null,
  has_more: false,
  items: [
    {
      vehicle_id: VEHICLE_ID,
      plate: 'ABC123',
      vin: 'YV1XXXXXXXX000001',
      registry_status: 'deregistered',
      vehicle_scope: 'passenger',
      manufacturer: 'Volvo',
      model_family: 'V70',
      production_year: 2008,
      power_kw: 120,
      displacement_cc: 2401,
      engine_code: 'D5244T',
      fuel: 'diesel',
      transmission: 'automatic',
      drive_type: null,
      bodywork_form: 'suv',
      colour: 'SVART',
      ktype: null,
      review_fields: ['bodywork_form'],
      rule_fields: ['engine_code'],
    },
  ],
};

const RECORD: NorVehicleRecord = {
  vehicle_id: VEHICLE_ID,
  origin_source: 'transportstyrelsen',
  origin_observed_on: '2023-12-01',
  ts_record_id: 7,
  registry_status: 'deregistered',
  created_at: '2026-09-26T10:00:00Z',
  updated_at: '2026-09-26T10:00:00Z',
  fields: [
    {
      field: 'manufacturer',
      label: 'Manufacturer',
      group: 'make',
      value: 'Volvo',
      source: { source: 'transportstyrelsen', ref: null, observed_on: '2023-12-01', origin: true },
      alternatives: [],
    },
    {
      field: 'engine_code',
      label: 'Engine code',
      group: 'technical',
      value: 'D5244T',
      source: { source: 'ais', ref: 'x', observed_on: '2026-09-19', origin: false },
      alternatives: [
        {
          value: 'D5244T4',
          source: { source: 'rule', ref: 'ENG-1', observed_on: null, origin: false },
        },
      ],
    },
    {
      field: 'kerb_weight_kg',
      label: 'Kerb weight (kg)',
      group: 'physical',
      value: null,
      source: null,
      alternatives: [],
    },
  ],
  identifiers: [
    {
      kind: 'plate',
      value: 'ABC123',
      valid_from: '2016-01-01',
      valid_to: null,
      current: true,
      source: 'transportstyrelsen',
      source_ref: '7',
    },
    {
      kind: 'plate',
      value: 'TPD118',
      valid_from: '2015-03-01',
      valid_to: '2016-01-01',
      current: false,
      source: 'transportstyrelsen',
      source_ref: '3',
    },
  ],
  source_links: [],
  source_link_count: 0,
  rules: [],
};

function render() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideRouter([]),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: API_BASE },
    ],
  });
  const fixture = TestBed.createComponent(CarSearchComponent);
  fixture.detectChanges();
  return fixture;
}

async function settle(fixture: ReturnType<typeof render>) {
  await new Promise((resolve) => setTimeout(resolve, 300));
  const http = TestBed.inject(HttpTestingController);
  http
    .match((request) => request.url.endsWith('/v1/vehicles/facets'))
    .forEach((request) => request.flush({ field: 'x', values: [] }));
  const searches = http.match((request) => request.url.endsWith('/v1/vehicles/search'));
  searches.forEach((request) => request.flush(PAGE));
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return searches;
}

describe('CarSearchPage', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('lists vehicles by NOR ID and marks reviewed and rule-filled values apart', async () => {
    const fixture = render();
    await settle(fixture);

    const row = (fixture.nativeElement as HTMLElement).querySelector('tbody tr');
    const text = row?.textContent ?? '';
    expect(text).toContain(VEHICLE_ID);
    expect(text).toContain('V70');
    expect(text).toContain('deregistered');
    expect(row?.querySelector('.review')?.textContent).toContain('suv');
    expect(row?.querySelector('.rule')?.textContent).toContain('D5244T');
  });

  it('opens a vehicle with its plate history and where each value came from', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;

    host.querySelector<HTMLTableRowElement>('tbody tr')?.click();
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush(RECORD);
    http.match(() => true).forEach((request) => request.flush({}, { status: 503, statusText: 'x' }));
    fixture.detectChanges();

    const panel = host.querySelector('aside.record')?.textContent ?? '';
    expect(panel).toContain('Deregistered');
    expect(panel).toContain('TPD118');
    expect(panel).toContain('2016-01-01');
    expect(panel).toContain('AIS');
    expect(panel).toMatch(/Learned rule said\s+D5244T4/);
    // An unknown value is counted, not listed.
    expect(panel).not.toContain('Kerb weight');
    expect(panel).toContain('1 unknown');
  });

  it('reloads the record after a KType choice without unmounting the candidates panel', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    host.querySelector<HTMLTableRowElement>('tbody tr')?.click();
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush(RECORD);
    fixture.detectChanges();
    http.match(() => true).forEach((request) => request.flush({}, { status: 503, statusText: 'x' }));
    const panel = fixture.debugElement.query(By.directive(KTypeCandidates));

    (panel.componentInstance as KTypeCandidates).choiceChanged.emit({} as VehicleMatchLookup);
    fixture.detectChanges();

    expect(host.querySelector('ns-ktype-candidates')).toBe(panel.nativeElement);
    http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush(RECORD);
    fixture.detectChanges();
    expect(host.querySelector('ns-ktype-candidates')).toBe(panel.nativeElement);
  });

  it('shows a person’s correction on the car’s row and in its record as soon as it is saved or undone', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    const rowText = () => host.querySelector('tbody tr')?.textContent?.replace(/\s+/g, ' ') ?? '';
    expect(rowText()).toContain('D5244T');
    host.querySelector<HTMLTableRowElement>('tbody tr')?.click();
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush(RECORD);
    fixture.detectChanges();
    http.match(() => true).forEach((request) => request.flush({}, { status: 503, statusText: 'x' }));
    const panel = fixture.debugElement.query(By.directive(KTypeCandidates))
      .componentInstance as KTypeCandidates;
    const field = (name: string, value: string | null) => ({
      field: name,
      label: name,
      type: 'text' as const,
      values: [],
      evidence_keys: [],
      current_value: value,
      current_source: 'correction',
      suggestions: [],
    });
    const say = (engine: string | null, power: string | null, fields = RECORD.fields) => {
      panel.choiceChanged.emit({
        choice: null,
        corrections: [{ field: 'engine_code' }, { field: 'power_kw' }],
        correctable_fields: [field('engine_code', engine), field('power_kw', power), field('drive_type', 'awd')],
      } as unknown as VehicleMatchLookup);
      fixture.detectChanges();
      http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush({ ...RECORD, fields });
      fixture.detectChanges();
    };

    say('D5244T9', '136', [
      {
        field: 'engine_code',
        label: 'Engine code',
        group: 'technical',
        value: 'D5244T9',
        source: { source: 'correction', ref: 'c1', observed_on: '2026-10-02', origin: false },
        alternatives: [],
      },
    ]);
    expect(rowText()).toContain('D5244T9');
    expect(rowText()).toContain('136');
    // A field nobody corrected keeps what the row has.
    expect(rowText()).not.toContain('awd');
    expect(host.querySelector('.record')?.textContent).toContain("A person's correction");

    // Undone: the car's own data is back, and a value marked as wrong reads as none.
    say('D5244T', null);
    expect(rowText()).toContain('D5244T');
    expect(rowText()).not.toContain('D5244T9');
    expect(rowText()).not.toContain('136');
  });

  it('shows a person’s choice on the car’s row and in its record as soon as it is made', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    const cell = () => host.querySelector('tbody tr td:last-child')?.textContent?.replace(/\s+/g, ' ').trim();
    expect([...host.querySelectorAll('thead th')].at(-1)?.textContent).toBe('KType');
    expect(cell()).toBe('—');
    host.querySelector<HTMLTableRowElement>('tbody tr')?.click();
    fixture.detectChanges();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush(RECORD);
    fixture.detectChanges();
    http.match(() => true).forEach((request) => request.flush({}, { status: 503, statusText: 'x' }));
    const panel = fixture.debugElement.query(By.directive(KTypeCandidates))
      .componentInstance as KTypeCandidates;
    const answer = (choice: object | null, fields: NorVehicleRecord['fields'] = RECORD.fields) => {
      panel.choiceChanged.emit({ choice } as VehicleMatchLookup);
      fixture.detectChanges();
      http.expectOne(`${API_BASE}/v1/vehicles/${VEHICLE_ID}`).flush({ ...RECORD, fields });
      fixture.detectChanges();
    };
    const source = { source: 'review', ref: 'c1', observed_on: '2026-10-02', origin: false };

    answer({ status: 'chosen', ktype: '000010064' }, [
      { field: 'ktype', label: 'KType', group: 'match', value: '000010064', source, alternatives: [] },
      { field: 'match_state', label: 'Match state', group: 'match', value: 'manual', source, alternatives: [] },
    ]);
    expect(cell()).toBe('000010064');
    expect(host.querySelector('tbody tr td:last-child .review')?.getAttribute('title')).toBe('Chosen by a person');
    const record = host.querySelector('.record')?.textContent?.replace(/\s+/g, ' ') ?? '';
    expect(record).toContain('Match stateKType chosen by a person');
    expect(record).toContain("A person's choice");
    expect(record).not.toContain('manual');

    answer({ status: 'none', ktype: null }, [
      { field: 'match_state', label: 'Match state', group: 'match', value: 'manual_none', source, alternatives: [] },
    ]);
    expect(cell()).toBe('none of these');
    expect(host.querySelector('.record')?.textContent).toContain('“None of these”, decided by a person');

    answer({ status: 'withdrawn', ktype: null });
    expect(cell()).toBe('—');
  });

  it('filters to cars a person decided, and Clear takes the filter away', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    const select = host.querySelector('select[aria-label="KType choice"]') as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent?.trim())).toEqual([
      'Any', 'Decided by a person', 'KType chosen', '“None of these”',
    ]);
    const choose = async (value: string) => {
      select.value = value;
      select.dispatchEvent(new Event('change'));
      const [request] = await settle(fixture);
      return (request.request.body.conditions as { field: string }[]).filter(
        (condition) => condition.field === 'match_state',
      );
    };

    expect(await choose('decided')).toEqual([
      { field: 'match_state', operator: 'equals', values: ['manual', 'manual_none'] },
    ]);
    expect(await choose('chosen')).toEqual([
      { field: 'match_state', operator: 'equals', values: ['manual'] },
    ]);
    expect(await choose('none')).toEqual([
      { field: 'match_state', operator: 'equals', values: ['manual_none'] },
    ]);

    const clear = host.querySelector('.search__filters p-button button') as HTMLButtonElement;
    expect(clear.disabled).toBe(false);
    clear.click();
    const [request] = await settle(fixture);
    expect(JSON.stringify(request.request.body.conditions)).not.toContain('match_state');
    expect(select.value).toBe('any');
  });

  it('lists the decisions made for several cars on their own tab', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;

    const tab = [...host.querySelectorAll<HTMLButtonElement>('button[role="tab"]')].find(
      (item) => item.textContent?.trim() === 'Decisions',
    );
    tab?.click();
    fixture.detectChanges();

    expect(tab?.getAttribute('aria-selected')).toBe('true');
    TestBed.inject(HttpTestingController)
      .expectOne((request) => request.url.endsWith('/v1/vehicle-corrections/decisions'))
      .flush({ decisions: [] });
    fixture.detectChanges();
    expect(host.querySelector('ns-correction-decisions')?.textContent).toContain(
      'No correction has been applied to several cars yet.',
    );
    // The car list gives way to the overview, as it does for the Matching view.
    expect(host.querySelector('tbody tr')).toBeNull();
  });

  it('filters by registry status', async () => {
    const fixture = render();
    await settle(fixture);

    const select = (fixture.nativeElement as HTMLElement).querySelector(
      'select[aria-label="Registry status"]',
    ) as HTMLSelectElement;
    select.value = 'registered';
    select.dispatchEvent(new Event('change'));
    const [request] = await settle(fixture);

    expect(request.request.body.conditions).toContainEqual({
      field: 'registry_status',
      operator: 'equals',
      values: ['registered'],
    });
  });

  it('shows passenger cars first, keeping rows the backfill has not reached', async () => {
    const fixture = render();
    const [request] = await settle(fixture);

    expect(request.request.body).toEqual({
      conditions: [
        {
          field: 'vehicle_scope',
          operator: 'not_equals',
          values: ['motorhome', 'special_modified', 'test_record', 'goods', 'trailer', 'bus', 'other', 'other_category'],
        },
      ],
      text: '',
    });
  });

  it('drops the passenger filter when the reviewer asks for all vehicles', async () => {
    const fixture = render();
    await settle(fixture);

    const select = (fixture.nativeElement as HTMLElement).querySelector(
      'select[aria-label="Vehicle type"]',
    ) as HTMLSelectElement;
    select.value = 'all';
    select.dispatchEvent(new Event('change'));
    const [request] = await settle(fixture);

    expect(request.request.body).toEqual({ conditions: [], text: '' });
  });

  it('narrows to one excluded category, e.g. motorhomes', async () => {
    const fixture = render();
    await settle(fixture);

    const select = (fixture.nativeElement as HTMLElement).querySelector(
      'select[aria-label="Vehicle type"]',
    ) as HTMLSelectElement;
    select.value = 'motorhome';
    select.dispatchEvent(new Event('change'));
    const [request] = await settle(fixture);

    expect(request.request.body.conditions).toEqual([
      { field: 'vehicle_scope', operator: 'equals', values: ['motorhome'] },
    ]);
  });

  it('says so when no vehicle on this server has been backfilled yet', async () => {
    const fixture = render();
    await settle(fixture);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('NorthStar vehicles appear once');
  });

  it('filters to cars in one stored matching state, and shows the stored KType or state', async () => {
    const fixture = render();
    await settle(fixture);
    const host = fixture.nativeElement as HTMLElement;
    const select = host.querySelector('select[aria-label="Matching result"]') as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent?.trim())).toEqual([
      'Any', 'Resolved', 'Several KTypes', 'One, not confirmed', 'No KType', 'Not matchable',
      'Chosen by a person', 'None of these', 'Not evaluated yet',
    ]);

    select.value = 'several';
    select.dispatchEvent(new Event('change'));
    const [request] = await settle(fixture);
    expect(
      (request.request.body.conditions as { field: string }[]).filter(
        (condition) => condition.field === 'match_result',
      ),
    ).toEqual([{ field: 'match_result', operator: 'equals', values: ['several'] }]);

    const clear = host.querySelector('.search__filters p-button button') as HTMLButtonElement;
    clear.click();
    const [cleared] = await settle(fixture);
    expect(JSON.stringify(cleared.request.body.conditions)).not.toContain('match_result');
    expect(select.value).toBe('');
  });

  it('counts the states of the filter above the list, and a state narrows the list', async () => {
    const fixture = render();
    await settle(fixture);
    const http = TestBed.inject(HttpTestingController);
    const host = fixture.nativeElement as HTMLElement;
    const [counted] = http.match((request) => request.url.endsWith('/v1/vehicles/match-results/counts'));
    // The counts are of every state: the list's own matching clauses are left out.
    expect(JSON.stringify(counted.request.body.conditions)).not.toContain('match_');
    counted.flush({
      total: 10,
      changed_since_matched: 0,
      states: [
        { state: 'resolved', cars: 7 },
        { state: 'several', cars: 3 },
      ],
    });
    fixture.detectChanges();
    expect(host.textContent).not.toContain('Run matching');
    expect([...host.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent?.trim())).toEqual([
      'Cars', 'Decisions',
    ]);

    const several = [...host.querySelectorAll<HTMLButtonElement>('ns-match-results .state')].find(
      (button) => button.textContent?.includes('Several KTypes'),
    ) as HTMLButtonElement;
    several.click();
    const [request] = await settle(fixture);
    expect(request.request.body.conditions).toContainEqual({
      field: 'match_result', operator: 'equals', values: ['several'],
    });
    const select = host.querySelector('select[aria-label="Matching result"]') as HTMLSelectElement;
    expect(select.value).toBe('several');
    // Narrowing the list does not change what the counts are of.
    expect(http.match((req) => req.url.endsWith('/v1/vehicles/match-results/counts'))).toEqual([]);
  });

  it('finds cars by a KType that is accepted or possible for them', async () => {
    const fixture = render();
    await settle(fixture);
    const input = (fixture.nativeElement as HTMLElement).querySelector(
      'input[aria-label="KType"]',
    ) as HTMLInputElement;
    input.value = ' 000010064 ';
    input.dispatchEvent(new Event('input'));
    const [request] = await settle(fixture);
    expect(request.request.body.conditions).toContainEqual({
      field: 'match_ktype', operator: 'equals', values: ['000010064'],
    });
  });

  it('shows the possible KTypes of a car the matcher could not decide', async () => {
    const fixture = render();
    PAGE.items[0] = {
      ...PAGE.items[0],
      match_result: 'several',
      candidate_ktypes: ['000010064', '000010065', '000010066'],
      candidate_confidences: [0.91, 0.9, 0.7],
    };
    try {
      await settle(fixture);
      const cell = (fixture.nativeElement as HTMLElement).querySelector('tbody tr td:last-child');
      const words = (cell?.textContent ?? '').replace(/\s+/g, ' ');
      expect(words).toContain('000010064');
      expect(words).toContain('000010065');
      expect(words).not.toContain('000010066');
      expect(words).toContain('+1');
      expect(words).toContain('several');
      expect(cell?.querySelector('.ktypes')?.getAttribute('title')).toBe(
        'Possible KTypes, none accepted: 000010064 (91%), 000010065 (90%), 000010066 (70%)',
      );
    } finally {
      const { match_result: _state, candidate_ktypes: _k, candidate_confidences: _c, ...rest } =
        PAGE.items[0];
      PAGE.items[0] = rest;
    }
  });

  it('reads the rows on screen again when a correction changed several cars', async () => {
    const fixture = render();
    await settle(fixture);
    const http = TestBed.inject(HttpTestingController);
    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('tbody tr')?.textContent).toContain('120');

    (fixture.componentInstance as unknown as { refreshRows(): void }).refreshRows();
    await new Promise((resolve) => setTimeout(resolve, 50));
    const [again] = http.match((request) => request.url.endsWith('/v1/vehicles/search'));
    // As many rows as are loaded, from the start: the list stays where it was.
    expect(again.request.params.get('limit')).toBe('1');
    expect(again.request.params.has('cursor')).toBe(false);
    again.flush({ ...PAGE, items: [{ ...PAGE.items[0], power_kw: 133, match_result: 'resolved', automatic_ktype: '000010064' }] });
    fixture.detectChanges();

    const row = host.querySelector('tbody tr')?.textContent ?? '';
    expect(row).toContain('133');
    expect(row).toContain('000010064');
    // The counts above the list are read again too.
    expect(http.match((request) => request.url.endsWith('/match-results/counts')).length).toBeGreaterThan(0);
    fixture.destroy();
  });
});
