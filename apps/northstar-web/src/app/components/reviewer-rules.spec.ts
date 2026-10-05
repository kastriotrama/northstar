/**
 * The reviewer rules in the Decisions tab: what each changed and whether the
 * cars' match results are older than the change. Mocked: the list is a fixture.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type { ReviewerRuleChange } from '../core/models';
import { ReviewerRules } from './reviewer-rules';

const URL = 'http://api.test/v1/vehicles/match-results/reviewer-rules?limit=50';

function rule(overrides: Partial<ReviewerRuleChange> = {}): ReviewerRuleChange {
  return {
    rule_id: 'rule-1',
    status: 'applied',
    author: 'Erik Christensen',
    applied_by: 'Erik Christensen',
    retired_by: null,
    created_at: '2026-10-05T14:59:00Z',
    applied_at: '2026-10-05T15:00:35Z',
    retired_at: null,
    conditions: 'brand contains KIA and brand contains NIRO',
    target_field: 'power_kw',
    target_value: '100',
    override: false,
    note: null,
    records_written: 1376,
    vehicles: 1374,
    out_of_date: 1374,
    ...overrides,
  };
}

function render() {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(ReviewerRules);
  fixture.detectChanges();
  return fixture;
}

function text(fixture: ReturnType<typeof render>): string {
  return ((fixture.nativeElement as HTMLElement).textContent ?? '').replace(/\s+/g, ' ');
}

describe('ReviewerRules', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('lists who made each rule, what it sets, how many cars and how many are out of date', () => {
    const fixture = render();
    TestBed.inject(HttpTestingController)
      .expectOne(URL)
      .flush({
        rules: [
          rule(),
          rule({
            rule_id: 'rule-2',
            status: 'retired',
            retired_by: 'Erik Christensen',
            retired_at: '2026-10-05T17:46:45Z',
            conditions: 'type_text contains TLE',
            target_field: 'engine_code',
            target_value: 'G4FT',
            override: true,
            vehicles: 258,
            out_of_date: 0,
          }),
        ],
      });
    fixture.detectChanges();

    const rows = [...(fixture.nativeElement as HTMLElement).querySelectorAll('tbody tr')].map((row) =>
      (row.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    expect(rows[0]).toContain('Erik Christensen');
    expect(rows[0]).toContain('brand contains KIA and brand contains NIRO');
    expect(rows[0]).toContain('power = 100');
    expect(rows[0]).toContain('1,374 1,374 applied');
    expect(rows[1]).toContain('engine code = G4FT');
    expect(rows[1]).toContain('replaces');
    expect(rows[1]).toContain('retired by Erik Christensen');
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('td.stale')).toHaveLength(1);
  });

  it('says when there are none and when the list cannot be read', () => {
    const fixture = render();
    const http = TestBed.inject(HttpTestingController);
    http.expectOne(URL).flush({ rules: [] });
    fixture.detectChanges();
    expect(text(fixture)).toContain('No reviewer rules yet.');

    const refresh = (fixture.nativeElement as HTMLElement).querySelector('button') as HTMLButtonElement;
    refresh.click();
    http.expectOne(URL).flush('boom', { status: 503, statusText: 'Unavailable' });
    fixture.detectChanges();
    expect(text(fixture)).toContain('Could not load the reviewer rules.');
  });
});
