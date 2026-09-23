/**
 * The record panel's KType section: what one car could be, and why not the others.
 * Mocked: the matcher's verdict is the fixture; the point is how it reads.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type { KTypeCandidate, VehicleMatchLookup } from '../core/models';
import { KTypeCandidates } from './ktype-candidates';

function candidate(ktype: string, overrides: Partial<KTypeCandidate> = {}): KTypeCandidate {
  return {
    ktype,
    candidate_only: false,
    confidence: 0.98,
    manufacturer: 'VOLVO',
    model: 'V70 III (135)',
    year_from: 2010,
    year_to: 2015,
    fuels: ['diesel'],
    engine_codes: ['D 5204 T2'],
    displacement_cc: 1984,
    power_kw: 120,
    drive_type: 'fwd',
    bodyworks: ['estate'],
    matched_fields: ['model', 'power_kw'],
    missing_fields: [],
    conflicting_fields: [],
    compatible: true,
    ...overrides,
  };
}

function lookup(overrides: Partial<VehicleMatchLookup> = {}): VehicleMatchLookup {
  return {
    source_record_id: 769,
    plate: 'FLT946',
    vin: null,
    catalog_batch: 'tecdoc-v5',
    terminal: 'provisional',
    bucket: 'several',
    confidence: 0.98,
    top_ktype: '000010064',
    reason_codes: ['match:automatic_candidate_threshold_met'],
    rule_filled: [],
    inputs: null,
    candidates: [
      candidate('000010064'),
      candidate('000011508', { engine_codes: ['D 5204 T3'] }),
      candidate('000059385', { power_kw: 100, conflicting_fields: ['power_kw'], compatible: false }),
    ],
    candidate_limit: 5,
    separating_fields: ['engine_code'],
    missing_separating_fields: ['engine_code'],
    decision_trace: [],
    other_source_record_ids: [],
    ...overrides,
  };
}

function render(sourceRecordId = 769) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(KTypeCandidates);
  fixture.componentRef.setInput('sourceRecordId', sourceRecordId);
  fixture.detectChanges();
  return fixture;
}

async function answer(fixture: ReturnType<typeof render>, respond: (url: string) => [object, number]) {
  await fixture.whenStable();
  const http = TestBed.inject(HttpTestingController);
  for (const request of http.match(() => true)) {
    const [body, status] = respond(request.request.urlWithParams);
    if (status === 200) request.flush(body);
    else request.flush(body, { status, statusText: 'err' });
  }
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('KTypeCandidates', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('asks for the exact record open in the panel, not whichever shares its plate', async () => {
    const fixture = render(769);
    let asked = '';
    await answer(fixture, (url) => {
      asked = url;
      return [lookup(), 200];
    });

    expect(asked).toContain('/v1/vehicles/matching/lookup?source_record_id=769');
  });

  it('says where the gap is: several fit, and the car lacks what separates them', async () => {
    const fixture = render();
    await answer(fixture, () => [lookup(), 200]);

    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Several KTypes');
    expect(text).toContain('2 KTypes fit');
    expect(text).toContain('this car has no engine_code');
  });

  it('marks the value that rules a candidate out', async () => {
    const fixture = render();
    await answer(fixture, () => [lookup(), 200]);

    const host = fixture.nativeElement as HTMLElement;
    const ruledOut = [...host.querySelectorAll('.candidate--out')];
    expect(ruledOut.length).toBe(1);
    expect(ruledOut[0].querySelector('.chip--conflict')?.textContent?.trim()).toBe('100 kW');
    expect(ruledOut[0].textContent).toContain('Ruled out: power_kw');
  });

  it('shows the server’s reason when the car cannot be looked up', async () => {
    const fixture = render();
    await answer(fixture, () => [{ detail: 'No vehicle with record id 769.' }, 404]);

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'No vehicle with record id 769.',
    );
  });
});
