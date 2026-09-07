/**
 * The Rules page must show code rules beside catalog rules.
 *
 * Mocked rather than live: the point is that a transform compiled into the pipeline
 * reaches the screen and is marked unmistakably, which a fixture can prove without a
 * database behind it.
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
import type { RuleCatalogResponse } from '../../core/models';
import { RulesPage } from './rules';

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
const globals = globalThis as Record<string, unknown>;
globals['ResizeObserver'] ??= ResizeObserverStub;
globals['IntersectionObserver'] ??= ResizeObserverStub;

const API_BASE = 'http://api.test';

const CATALOG: RuleCatalogResponse = {
  base_version: 'ts-translation-v7',
  active_version: 'ts-translation-v7',
  draft_count: 0,
  total: 2,
  filtered_total: 2,
  catalog_total: 1,
  code_total: 1,
  pipeline_version: 'normalization-pipeline-v10',
  limit: 100,
  offset: 0,
  areas: ['model_family', 'tyre_construction'],
  canonical_fields: ['model_family', 'construction'],
  canonical_options_by_field: { model_family: ['C-Class'] },
  transformers: [
    {
      transformer_id: 'ts.tyres',
      order: 36,
      default_rule_id: 'TYRE-SIZE-V1',
      summary: 'Decomposes the per-axle tyre size.',
      source_fields: ['tyre_front', 'tyre_rear'],
      writes: ['rim_diameter_in', 'tyre_staggered'],
      rule_areas: [],
      code_areas: ['tyre_construction'],
      catalog_rule_count: 0,
      code_rule_count: 9,
      review_reasons: ['tyre_size_unrecognized'],
    },
  ],
  items: [
    {
      rule_id: 'MOD-205',
      area: 'model_family',
      source_fields: ['model'],
      source_terms: ['C 220'],
      canonical_field: 'model_family',
      base_canonical_value: 'C-Class',
      effective_canonical_value: 'C-Class',
      effective_decision: 'accepted',
      effective_display_value: null,
      vehicle_scopes: [],
      manufacturers: ['Mercedes-Benz'],
      has_draft: false,
      change_note: null,
      origin: 'catalog',
      transformer_id: null,
      editable: true,
      notes: null,
    },
    {
      rule_id: 'CODE:TYC:ZR',
      area: 'tyre_construction',
      source_fields: ['tyre_front', 'tyre_rear'],
      source_terms: ['ZR'],
      canonical_field: 'construction',
      base_canonical_value: 'radial',
      effective_canonical_value: 'radial',
      effective_decision: 'accepted',
      effective_display_value: null,
      vehicle_scopes: [],
      manufacturers: [],
      has_draft: false,
      change_note: null,
      origin: 'code',
      transformer_id: 'ts.tyres',
      editable: false,
      notes: 'Construction letter inside a tyre size.',
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
  const fixture = TestBed.createComponent(RulesPage);
  fixture.detectChanges();
  return fixture;
}

function flush(url = `${API_BASE}/v1/normalization-review/rules/catalog`) {
  const http = TestBed.inject(HttpTestingController);
  const requests = http.match((request) => request.url === url);
  requests[requests.length - 1].flush(CATALOG);
  return requests[requests.length - 1];
}

describe('RulesPage', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('lists a compiled-in transform next to a catalog rule', async () => {
    const fixture = render();
    flush();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('MOD-205');
    expect(text).toContain('CODE:TYC:ZR');
  });

  it('marks which rules come from the pipeline rather than the catalog', async () => {
    const fixture = render();
    flush();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const host = fixture.nativeElement as HTMLElement;
    const codeTags = host.querySelectorAll('.origin-tag--code');
    expect(codeTags.length).toBe(1);
    expect((codeTags[0].textContent ?? '').trim()).toBe('code');
  });

  it('counts catalog and pipeline rules separately in the header', async () => {
    const fixture = render();
    flush();
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Reviewed catalog');
    expect(text).toContain('In the pipeline');
    expect(text).toContain('normalization-pipeline-v10');
  });

  it('shows every pipeline stage with what it reads and writes', async () => {
    const fixture = render();
    flush();
    fixture.detectChanges();
    await fixture.whenStable();

    // The panel is collapsed until asked for, so the table stays the first thing seen.
    expect((fixture.nativeElement as HTMLElement).querySelector('.stage')).toBeNull();

    const toggle = (fixture.nativeElement as HTMLElement).querySelector(
      '.pipeline__head',
    ) as HTMLButtonElement;
    toggle.click();
    fixture.detectChanges();

    const stage = (fixture.nativeElement as HTMLElement).querySelector('.stage');
    const text = stage?.textContent ?? '';
    expect(text).toContain('ts.tyres');
    expect(text).toContain('tyre_front');
    expect(text).toContain('rim_diameter_in');
    expect(text).toContain('tyre_size_unrecognized');
  });

  it('filters the list to one stage when that stage is selected', async () => {
    const fixture = render();
    flush();
    fixture.detectChanges();
    await fixture.whenStable();

    (
      (fixture.nativeElement as HTMLElement).querySelector(
        '.pipeline__head',
      ) as HTMLButtonElement
    ).click();
    fixture.detectChanges();
    ((fixture.nativeElement as HTMLElement).querySelector('.stage') as HTMLButtonElement).click();
    fixture.detectChanges();

    const request = flush();
    expect(request.request.params.get('transformer_id')).toBe('ts.tyres');
  });
});
