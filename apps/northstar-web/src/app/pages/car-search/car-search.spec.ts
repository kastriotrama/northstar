/**
 * Car search reads canonical values and says which of them a live rule supplied.
 * Mocked: the point is what reaches the screen and what goes over the wire.
 */

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
import type { CarSearchPage } from '../../core/models';
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

const PAGE: CarSearchPage = {
  matched_rows: 1,
  next_cursor: null,
  has_more: false,
  items: [
    {
      source_record_id: 7,
      plate: 'ABC123',
      vin: 'YV1XXXXXXXX000001',
      manufacturer: 'Volvo',
      model_family: 'V70',
      drive_type: null,
      bodywork_form: 'estate',
      engine_code: 'D5244T',
      power_kw: 120,
      displacement_cc: 2401,
      production_year: 2008,
      rule_filled: ['power_kw'],
      fuel: 'diesel',
      transmission: 'automatic',
      euro_class: 'Euro 6',
      vehicle_scope: 'passenger',
      norm_status: 'resolved',
    },
  ],
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
    .forEach((request) => request.flush({ field: 'x', matched_rows: 0, values: [] }));
  const searches = http.match((request) => request.url.endsWith('/v1/vehicles/search'));
  searches.forEach((request) => request.flush(PAGE));
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return searches;
}

describe('CarSearchPage', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('shows canonical values and marks the one a rule supplied', async () => {
    const fixture = render();
    await settle(fixture);

    const host = fixture.nativeElement as HTMLElement;
    const row = host.querySelector('tbody tr');
    const text = row?.textContent ?? '';
    expect(text).toContain('Volvo');
    expect(text).toContain('V70');
    expect(text).toContain('2401');
    expect(row?.querySelectorAll('.rule').length).toBe(1);
    expect(row?.querySelector('.rule')?.textContent).toContain('120');
  });

  it('shows passenger cars first, keeping rows the backfill has not reached', async () => {
    const fixture = render();
    const [request] = await settle(fixture);

    expect(request.request.body).toEqual({
      conditions: [
        {
          field: 'vehicle_scope',
          layer: 'normalized',
          operator: 'not_equals',
          values: ['motorhome', 'special_modified', 'test_record', 'other_category'],
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
      { field: 'vehicle_scope', layer: 'normalized', operator: 'equals', values: ['motorhome'] },
    ]);
  });

  it('says so when the vehicle type has not been computed on this server', async () => {
    const fixture = render();
    await settle(fixture);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Vehicle type has not been computed on this server yet');
  });
});
