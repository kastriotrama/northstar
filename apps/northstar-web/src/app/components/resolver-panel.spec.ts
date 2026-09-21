/**
 * Correcting a value a car already has is the same panel as filling a gap, told
 * which of the two it is doing. What must not blur is the difference: a correction
 * overwrites decisions, so it has to say so on the wire and refuse to run blind.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import { FilterState } from '../core/filter-state';
import { ResolverPanel } from './resolver-panel';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const globals = globalThis as Record<string, unknown>;
globals['ResizeObserver'] ??= ResizeObserverStub;
globals['IntersectionObserver'] ??= ResizeObserverStub;

const API_BASE = 'http://api.test';
const BUILD_ID = '11111111-1111-1111-1111-111111111111';

function panel(options: { override: boolean }) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideOptimus({ theme: { preset: Aura } }),
      { provide: API_BASE_URL, useValue: API_BASE },
    ],
  });

  const filter = TestBed.inject(FilterState);
  filter.set([
    { field: 'brand', operator: 'equals', layer: 'source', values: ['VOLVO'], locked: false },
    { field: 'model', operator: 'equals', layer: 'source', values: ['XC40'], locked: false },
  ]);

  const fixture = TestBed.createComponent(ResolverPanel);
  fixture.componentRef.setInput('targetField', 'bodywork_form');
  fixture.componentRef.setInput('buildId', BUILD_ID);
  fixture.componentRef.setInput('unresolvedRows', 0);
  fixture.componentRef.setInput('matchedRows', 412);
  fixture.componentRef.setInput('override', options.override);
  fixture.componentRef.setInput('varyingFields', []);
  fixture.detectChanges();

  const http = TestBed.inject(HttpTestingController);
  // The panel loads its vocabulary and the population's saved rules on open.
  for (const request of http.match(() => true)) {
    request.flush(request.request.method === 'GET' ? { closed: false, values: [] } : {});
  }
  return { fixture, component: fixture.componentInstance as unknown as Record<string, any>, http };
}

describe('ResolverPanel in correction mode', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('can write when nothing is unresolved, because the gap is not the subject', () => {
    const { component } = panel({ override: true });
    component['targetValue'].set('suv');

    expect(component['canWrite']()).toBe(true);
  });

  it('has nothing to do when it is only filling gaps and there are none', () => {
    const { component } = panel({ override: false });
    component['targetValue'].set('suv');

    expect(component['canWrite']()).toBe(false);
  });

  it('refuses to rewrite until this exact value has been previewed', () => {
    const { component, http } = panel({ override: true });
    component['targetValue'].set('suv');

    expect(component['canRun']()).toBe(false);

    component['runPreview']();
    const request = http.expectOne(`${API_BASE}/v1/match-review/rule-preview`);
    expect(request.request.body.override).toBe(true);
    request.flush({
      conditions: [],
      target_field: 'bodywork_form',
      target_value: 'suv',
      matched_rows: 412,
      would_resolve: 0,
      already_resolved: 412,
      would_overwrite: 403,
      sample_plates: [],
    });

    expect(component['canRun']()).toBe(true);

    // Changing the value invalidates the count that justified running it.
    component['onTargetValue']('estate');
    expect(component['canRun']()).toBe(false);
  });

  it('saves the mode with the rule, so running it later still corrects', () => {
    const { component, http } = panel({ override: true });
    component['targetValue'].set('suv');
    component['author'].set('valon');

    component['saveRule'](false);

    const request = http.expectOne(`${API_BASE}/v1/match-review/resolution-rules`);
    expect(request.request.body.override).toBe(true);
    expect(request.request.body.target_value).toBe('suv');
  });

  it('never overrides by omission when the panel is filling a gap', () => {
    const { component, http } = panel({ override: false });
    component['targetValue'].set('suv');
    component['author'].set('valon');

    component['saveRule'](false);

    const request = http.expectOne(`${API_BASE}/v1/match-review/resolution-rules`);
    expect(request.request.body.override).toBe(false);
  });
});
