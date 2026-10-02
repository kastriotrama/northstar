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
});
