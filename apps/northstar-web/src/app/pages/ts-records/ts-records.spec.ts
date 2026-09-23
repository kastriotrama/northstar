/**
 * TS data's Vehicle type narrows what is browsed and never what a rule says.
 * Mocked: the point is which requests carry the scope, which a fixture can prove.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import type { TestRequest } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../../core/api-config';
import { FilterState } from '../../core/filter-state';
import type { RuleCondition } from '../../core/models';
import { TsRecordsPage } from './ts-records';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const globals = globalThis as Record<string, unknown>;
globals['ResizeObserver'] ??= ResizeObserverStub;
globals['IntersectionObserver'] ??= ResizeObserverStub;

function render() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideRouter([]),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(TsRecordsPage);
  fixture.detectChanges();
  return fixture;
}

function reply(request: TestRequest): void {
  const url = request.request.url;
  if (url.endsWith('/count')) request.flush({ matched_rows: 1, total_rows: 1 });
  else if (url.endsWith('/page')) request.flush({ items: [], next_cursor: null, has_more: false });
  else if (url.endsWith('/facets')) request.flush({ field: 'x', matched_rows: 0, values: [] });
  else if (url.endsWith('/unresolved-summary')) request.flush({ matched_rows: 0, fields: [] });
  else if (url.endsWith('/builds')) request.flush([]);
  else request.flush({});
}

/** Answer everything in flight and return the vehicle-filter bodies that went out. */
async function settle(): Promise<RuleCondition[][]> {
  await new Promise((resolve) => setTimeout(resolve, 260));
  const http = TestBed.inject(HttpTestingController);
  const bodies: RuleCondition[][] = [];
  for (const request of http.match(() => true)) {
    const body = request.request.body as { conditions?: RuleCondition[] } | null;
    const params = request.request.params.get('field');
    if (body?.conditions && params !== 'vehicle_scope') bodies.push(body.conditions);
    reply(request);
  }
  return bodies;
}

const scoped = (conditions: RuleCondition[]) =>
  conditions.some((condition) => condition.field === 'vehicle_scope');

describe('TsRecordsPage vehicle type', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('shows passenger cars by default, on every browsing request', async () => {
    render();
    const bodies = await settle();

    // count, page, facet, status facet and the unresolved summary
    expect(bodies.length).toBeGreaterThanOrEqual(5);
    expect(bodies.every(scoped)).toBe(true);
  });

  it('drops the scope when the reviewer asks for all vehicles', async () => {
    const fixture = render();
    await settle();

    const select = (fixture.nativeElement as HTMLElement).querySelector(
      'select[aria-label="Vehicle type"]',
    ) as HTMLSelectElement;
    select.value = 'all';
    select.dispatchEvent(new Event('change'));
    const bodies = await settle();

    expect(bodies.length).toBeGreaterThanOrEqual(5);
    expect(bodies.some(scoped)).toBe(false);
  });

  it('never writes the vehicle type into the rule filter', async () => {
    const fixture = render();
    await settle();
    // Passenger cars is on by default; it must already be absent from the rule filter.
    expect(scoped(TestBed.inject(FilterState).payload())).toBe(false);

    const select = (fixture.nativeElement as HTMLElement).querySelector(
      'select[aria-label="Vehicle type"]',
    ) as HTMLSelectElement;
    select.value = 'motorhome';
    select.dispatchEvent(new Event('change'));
    await settle();

    expect(scoped(TestBed.inject(FilterState).payload())).toBe(false);
  });
});
