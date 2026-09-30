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

const VEHICLE_ID = 'NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G';

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
    vehicle_id: VEHICLE_ID,
    source_record_id: 769,
    plate: 'FLT946',
    vin: null,
    catalog_batch: 'tecdoc-v5',
    terminal: 'provisional',
    bucket: 'several',
    confidence: 0.98,
    top_ktype: '000010064',
    reason_codes: ['match:automatic_candidate_threshold_met'],
    verdict: 'Composite confidence meets only the provisional threshold.',
    rule_filled: [],
    overlaid_fields: {},
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
    other_vehicle_ids: [],
    ...overrides,
  };
}

function render(vehicleId = VEHICLE_ID) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: 'http://api.test' },
    ],
  });
  const fixture = TestBed.createComponent(KTypeCandidates);
  fixture.componentRef.setInput('vehicleId', vehicleId);
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

  it('asks for the vehicle open in the panel by its NOR ID', async () => {
    const fixture = render();
    let asked = '';
    await answer(fixture, (url) => {
      asked = url;
      return [lookup(), 200];
    });

    expect(asked).toContain(`/v1/vehicles/matching/lookup?vehicle_id=${VEHICLE_ID}`);
  });

  it('says which inputs came from the vehicle record rather than the TS derivation', async () => {
    const fixture = render();
    const inputs = {
      manufacturer: 'VOLVO',
      model_values: ['V70'],
      production_year: 2015,
      fuels: ['diesel'],
      engine_code: 'D5204T3',
      displacement_cc: 1984,
      power_kw: 120,
      drive_type: null,
      bodywork_form: 'estate',
      model_recovered_from: null,
    };
    await answer(fixture, () => [lookup({ inputs, overlaid_fields: { engine_code: 'ais' } }), 200]);

    const host = fixture.nativeElement as HTMLElement;
    expect(host.querySelector('.ruled')?.textContent?.trim()).toBe('D5204T3');
    expect(host.textContent).toContain('engine code from AIS');
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

  it('leads with why the pipeline ended where it did, then the reasons in words', async () => {
    const fixture = render();
    await answer(fixture, () => [
      lookup({
        terminal: 'review_required',
        verdict: 'The top candidates are too close to separate safely.',
        reason_codes: ['route:candidate_margin_below_gate', 'context_conflict:bodywork', 'policy:new'],
      }),
      200,
    ]);

    const host = fixture.nativeElement as HTMLElement;
    const verdict = host.querySelector('.verdict');
    expect(verdict?.classList).toContain('verdict--review_required');
    expect(verdict?.textContent).toContain('review required:');
    expect(verdict?.textContent).toContain('too close to separate safely');
    const why = [...host.querySelectorAll('.why li')].map((item) => item.textContent?.trim());
    expect(why).toEqual([
      'The top KTypes are too close to separate safely.',
      'Conflicts with the best KType on body type.',
    ]);
    // A code without a reading stays in the raw list only.
    expect(host.querySelector('.reasons')?.textContent).toContain('policy:new');
  });

  it('says how a KType that fits still lost to the top candidate', async () => {
    const fixture = render();
    await answer(fixture, () => [lookup({ verdict: null }), 200]);

    const host = fixture.nativeElement as HTMLElement;
    const standing = [...host.querySelectorAll('.candidate__why--fits')].map((item) =>
      item.textContent?.trim(),
    );
    expect(standing).toEqual([
      'Also fits, but lost to the top candidate. It differs from the top on engine code — and this car has no engine code to tell them apart.',
    ]);
    expect(host.querySelector('.verdict')).toBeNull();
  });

  it('shows the server’s reason when the car cannot be looked up', async () => {
    const fixture = render();
    await answer(fixture, () => [{ detail: `No vehicle '${VEHICLE_ID}'.` }, 404]);

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      `No vehicle '${VEHICLE_ID}'.`,
    );
  });
});
