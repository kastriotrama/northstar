/**
 * The record panel's KType section: what one car could be, and why not the others.
 * Mocked: the matcher's verdict is the fixture; the point is how it reads.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import type { KTypeChoiceState } from '../core/models';

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

const FINGERPRINT = 'f'.repeat(64);
const CHOICE_URL = `http://api.test/v1/vehicles/${VEHICLE_ID}/ktype-choices`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function stored(overrides: Partial<KTypeChoiceState> = {}): KTypeChoiceState {
  return {
    status: 'chosen',
    choice_id: '11111111-1111-4111-8111-111111111111',
    ktype: '000010064',
    reviewer: 'Anna',
    reason: null,
    created_at: '2026-10-01T09:30:00Z',
    catalog_batch: 'tecdoc-v5',
    automatic_terminal: 'provisional',
    automatic_ktype: null,
    chosen_candidate: candidate('000010064'),
    needs_review: false,
    stale_reasons: [],
    changed_inputs: [],
    history_count: 1,
    ...overrides,
  };
}

/** The panel with a lookup that can take a choice, and a reviewer name remembered. */
async function open(overrides: Partial<VehicleMatchLookup> = {}, reviewer = 'Bea') {
  TestBed.resetTestingModule();
  localStorage.clear();
  if (reviewer) localStorage.setItem('match-review-reviewer', reviewer);
  const fixture = render();
  await answer(fixture, () => [lookup({ evidence_fingerprint: FINGERPRINT, choice: null, ...overrides }), 200]);
  const host = fixture.nativeElement as HTMLElement;
  const http = TestBed.inject(HttpTestingController);
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const click = async (label: string, index = 0) => {
    const found = [...host.querySelectorAll('button')].filter((item) => item.textContent?.trim() === label);
    expect(found.length, `button "${label}"`).toBeGreaterThan(index);
    found[index].click();
    await settle();
  };
  const labels = () => [...host.querySelectorAll('.candidate .pick')].map((item) => item.textContent?.trim());
  const text = () => (host.textContent ?? '').replace(/\s+/g, ' ');
  return { fixture, host, http, settle, click, labels, text };
}

describe('KTypeCandidates: a person’s choice', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('records a fitting candidate in one click, with the operation id, head and fingerprint', async () => {
    const page = await open();
    const emitted: VehicleMatchLookup[] = [];
    page.fixture.componentInstance.choiceChanged.subscribe((value) => emitted.push(value));
    expect(page.labels()).toEqual(['Choose this KType', 'Choose this KType', 'Choose anyway…']);

    await page.click('Choose this KType', 1);

    const request = page.http.expectOne(CHOICE_URL);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      action: 'choose',
      ktype: '000011508',
      reviewer: 'Bea',
      reason: null,
      supersedes_choice_id: null,
      evidence_fingerprint: FINGERPRINT,
    });
    const saved = lookup({
      evidence_fingerprint: FINGERPRINT,
      choice: stored({ ktype: '000011508', reviewer: 'Bea' }),
      effective_ktype: '000011508',
      effective_source: 'person',
    });
    request.flush(saved, { status: 201, statusText: 'Created' });
    await page.settle();

    expect(page.text()).toContain('Saved. KType 000011508 chosen for this car.');
    expect(page.text()).toContain('KType 000011508 · chosen by a person — Bea');
    const chosen = [...page.host.querySelectorAll('.candidate')][1];
    expect(chosen.querySelector('.tag--person')?.textContent).toBe('chosen by a person');
    expect(chosen.querySelector('.pick')).toBeNull();
    expect(page.labels()).toEqual(['Choose this instead', 'Choose anyway…']);
    // The automatic verdict stays on screen next to the person's choice.
    expect(page.host.querySelector('.verdict')?.textContent).toContain('provisional');
    expect(emitted).toEqual([saved]);
  });

  it('asks first for a ruled-out candidate, and sends nothing when cancelled', async () => {
    const page = await open();

    await page.click('Choose anyway…');
    page.http.expectNone(CHOICE_URL);
    expect(page.text()).toContain('KType 000059385 conflicts with this car on power. Choose it anyway?');
    await page.click('Cancel');
    page.http.expectNone(CHOICE_URL);
    expect(page.host.querySelector('.confirm')).toBeNull();

    await page.click('Choose anyway…');
    await page.click('Confirm');
    expect(page.http.expectOne(CHOICE_URL).request.body.ktype).toBe('000059385');
  });

  it('asks first when the matcher resolved the car to another KType', async () => {
    const page = await open({ terminal: 'resolved' });

    await page.click('Choose this KType', 0);
    expect(page.http.expectOne(CHOICE_URL).request.body.ktype).toBe('000010064');

    const other = await open({ terminal: 'resolved' });
    await other.click('Choose this KType', 1);
    other.http.expectNone(CHOICE_URL);
    expect(other.text()).toContain('The matcher resolved this car to KType 000010064.');
  });

  it('replaces, withdraws and records none through a confirm step, superseding the shown choice', async () => {
    const page = await open({ choice: stored() });

    await page.click('Choose this instead');
    expect(page.text()).toContain("Replace Anna's choice of KType 000010064 with KType 000011508?");
    await page.click('Confirm');
    const replace = page.http.expectOne(CHOICE_URL).request.body;
    expect(replace).toMatchObject({ action: 'choose', ktype: '000011508', supersedes_choice_id: stored().choice_id });
    page.http.verify();

    const withdrawal = await open({ choice: stored() });
    await withdrawal.click('Withdraw choice');
    withdrawal.http.expectNone(CHOICE_URL);
    await withdrawal.click('Confirm');
    const request = withdrawal.http.expectOne(CHOICE_URL);
    expect(request.request.body).toMatchObject({ action: 'withdraw', ktype: null, supersedes_choice_id: stored().choice_id });
    request.flush(
      lookup({ evidence_fingerprint: FINGERPRINT, choice: stored({ status: 'withdrawn', ktype: null, reviewer: 'Bea' }) }),
    );
    await withdrawal.settle();
    expect(withdrawal.text()).toContain('Choice withdrawn.');
    expect(withdrawal.text()).toContain('An earlier choice was withdrawn by Bea');
    expect(withdrawal.labels()).toEqual(['Choose this KType', 'Choose this KType', 'Choose anyway…']);

    // A withdrawn head is still the head: the next choice supersedes it.
    const none = await open({ choice: stored({ status: 'withdrawn', ktype: null }) });
    await none.click('None of these');
    await none.click('Confirm');
    expect(none.http.expectOne(CHOICE_URL).request.body).toMatchObject({
      action: 'none',
      ktype: null,
      supersedes_choice_id: stored().choice_id,
      evidence_fingerprint: FINGERPRINT,
    });
  });

  it('keeps a flagged choice in one click, as the same KType on today’s evidence', async () => {
    const page = await open({
      choice: stored({ needs_review: true, stale_reasons: ['catalog_batch_changed'], catalog_batch: 'tecdoc-v4' }),
    });

    await page.click('Keep this choice');

    expect(page.http.expectOne(CHOICE_URL).request.body).toMatchObject({
      action: 'choose',
      ktype: '000010064',
      supersedes_choice_id: stored().choice_id,
    });
  });

  it('sends the reason with the choice and trims the name', async () => {
    const page = await open({}, '  Bea  ');
    const reason = page.host.querySelector('textarea') as HTMLTextAreaElement;
    reason.value = ' Papers say AWD ';
    reason.dispatchEvent(new Event('input'));
    await page.settle();

    await page.click('Choose this KType', 0);

    expect(page.http.expectOne(CHOICE_URL).request.body).toMatchObject({ reviewer: 'Bea', reason: 'Papers say AWD' });
  });

  it('retries a failed save with the same operation id and body; a new action mints a new id', async () => {
    const page = await open();
    await page.click('Choose this KType', 0);
    const first = page.http.expectOne(CHOICE_URL);
    const body = first.request.body;
    first.flush({ detail: { code: 'unavailable', message: 'Database unavailable.' } }, { status: 503, statusText: 'x' });
    await page.settle();
    expect(page.host.querySelector('[role="alert"]')?.textContent).toContain('Not saved. Try again.');

    await page.click('Try again');
    const second = page.http.expectOne(CHOICE_URL);
    expect(second.request.body).toEqual(body);
    second.error(new ProgressEvent('error'));
    await page.settle();

    await page.click('Choose this KType', 0);
    const third = page.http.expectOne(CHOICE_URL).request.body;
    expect(third.operation_id).not.toBe(body.operation_id);
    expect({ ...third, operation_id: body.operation_id }).toEqual(body);
  });

  it.each([
    [409, 'choice_changed', "Someone else changed this car's choice while you were looking. The current one is shown."],
    [409, 'evidence_changed', "This car's matching changed since you opened it. Check the candidates and choose again."],
    [422, 'ktype_not_a_candidate', "That KType is no longer among this car's candidates."],
    [422, 'nothing_to_withdraw', 'There is no choice to withdraw.'],
  ])('on %i %s says what happened and shows the current state', async (status, code, message) => {
    const page = await open();
    await page.click('Choose this KType', 0);
    page.http
      .expectOne(CHOICE_URL)
      .flush({ detail: { code, message: 'server words' } }, { status, statusText: 'x' });
    await page.settle();

    const alert = page.host.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(message);
    expect(alert?.querySelector('button')).toBeNull();
    // The lookup is fetched again in place; the message stays while it loads and after.
    const reload = page.http.expectOne((request) => request.url.endsWith('/v1/vehicles/matching/lookup'));
    reload.flush(lookup({ evidence_fingerprint: 'e'.repeat(64), choice: stored({ reviewer: 'Carl' }) }));
    await page.settle();
    expect(page.text()).toContain('chosen by a person — Carl');
    expect(page.host.querySelector('[role="alert"]')?.textContent).toContain(message);
  });

  it('says so, without a retry, when the operation id was already used for something else', async () => {
    const page = await open();
    await page.click('Choose this KType', 0);
    page.http
      .expectOne(CHOICE_URL)
      .flush({ detail: { code: 'operation_id_reused', message: 'Already used.' } }, { status: 409, statusText: 'x' });
    await page.settle();

    const alert = page.host.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Not saved. Already used.');
    expect(alert?.querySelector('button')).toBeNull();
  });

  it('disables the choose buttons, with a hint, until a name is entered', async () => {
    const page = await open({}, '');
    const picks = [...page.host.querySelectorAll<HTMLButtonElement>('.candidate .pick')];
    expect(picks.every((item) => item.disabled)).toBe(true);
    const hint = page.host.querySelector(`#${picks[0].getAttribute('aria-describedby')}`);
    expect(hint?.textContent).toContain('Enter your name to record a choice.');

    const name = page.host.querySelector('ns-ktype-choice input') as HTMLInputElement;
    name.value = 'Dan';
    name.dispatchEvent(new Event('input'));
    await page.settle();
    await page.click('Choose this KType', 0);
    const request = page.http.expectOne(CHOICE_URL);
    expect(request.request.body.reviewer).toBe('Dan');
    request.flush(lookup({ evidence_fingerprint: FINGERPRINT, choice: stored({ reviewer: 'Dan' }) }), {
      status: 201,
      statusText: 'Created',
    });
    await page.settle();
    expect(localStorage.getItem('match-review-reviewer')).toBe('Dan');
  });

  it('offers no choice controls for a lookup that is not a vehicle', async () => {
    const page = await open({ vehicle_id: null });

    expect(page.host.querySelector('ns-ktype-choice')).toBeNull();
    expect(page.labels()).toEqual([]);
    expect(page.host.querySelectorAll('.candidate').length).toBe(3);
  });

  it('loads the history when it is opened', async () => {
    const page = await open({ choice: stored() });
    const details = page.host.querySelector('ns-ktype-choice details') as HTMLDetailsElement;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await page.settle();

    const request = page.http.expectOne(CHOICE_URL);
    expect(request.request.method).toBe('GET');
    request.flush({ vehicle_id: VEHICLE_ID, current_choice_id: stored().choice_id, entries: [] });
  });

  it('leads with the person’s choice and labels the matcher’s own result as the matcher’s', async () => {
    const plain = await open();
    expect(plain.host.querySelector('.effective')).toBeNull();
    expect(plain.host.querySelector('.head')?.textContent).not.toContain('Matcher:');

    const chosen = await open({
      bucket: 'none',
      choice: stored(),
      effective_ktype: '000010064',
      effective_source: 'person',
    });
    const top = chosen.host.querySelector('.effective') as HTMLElement;
    const head = chosen.host.querySelector('.head') as HTMLElement;
    expect(top.textContent?.replace(/\s+/g, ' ')).toContain("This car's KType: 000010064 · chosen by a person");
    expect(head.textContent?.replace(/\s+/g, ' ')).toMatch(/Matcher: ?No KType/);
    // The person's choice comes first on screen, the matcher's badge after it.
    expect(top.compareDocumentPosition(head) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const none = await open({
      choice: stored({ status: 'none', ktype: null, chosen_candidate: null }),
      effective_ktype: null,
      effective_source: 'person',
    });
    expect(none.host.querySelector('.effective')?.textContent).toContain('a person recorded “none of these”');
  });

  it('gives each choose button its own accessible name', async () => {
    const page = await open({
      candidates: [
        candidate('000010064'),
        candidate('000059385', { conflicting_fields: ['power_kw'], compatible: false }),
        candidate('000059386', { conflicting_fields: ['power_kw'], compatible: false }),
      ],
    });

    const names = [...page.host.querySelectorAll('.candidate .pick')].map((item) => item.getAttribute('aria-label'));

    expect(names).toEqual([
      'Choose this KType — KType 000010064',
      'Choose anyway — KType 000059385',
      'Choose anyway — KType 000059386',
    ]);
    expect(new Set(names).size).toBe(3);
  });

  it('reloads an open history after a save, and says so when it cannot be loaded', async () => {
    const page = await open({ choice: stored() });
    const details = page.host.querySelector('ns-ktype-choice details') as HTMLDetailsElement;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));
    await page.settle();
    const entry = {
      choice_id: stored().choice_id, action: 'choose', ktype: '000010064', reviewer: 'Anna', reason: null,
      created_at: '2026-10-01T09:30:00Z', supersedes_choice_id: null, catalog_batch: 'tecdoc-v5',
      automatic_terminal: 'provisional', automatic_ktype: null, code_version: 'abc',
    };
    page.http
      .expectOne(CHOICE_URL)
      .flush({ vehicle_id: VEHICLE_ID, current_choice_id: entry.choice_id, entries: [entry] });
    await page.settle();
    expect(page.text()).toContain('Anna chose KType 000010064');

    await page.click('Withdraw choice');
    await page.click('Confirm');
    const [save] = page.http.match((request) => request.method === 'POST');
    save.flush(
      lookup({ evidence_fingerprint: FINGERPRINT, choice: stored({ status: 'withdrawn', ktype: null, reviewer: 'Bea', history_count: 2 }) }),
      { status: 201, statusText: 'Created' },
    );
    await page.settle();

    // Still open: it is fetched again rather than left on "Loading…".
    const again = page.http.expectOne((request) => request.method === 'GET' && request.url === CHOICE_URL);
    again.flush({ detail: { code: 'unavailable', message: 'x' } }, { status: 503, statusText: 'x' });
    await page.settle();
    expect(page.text()).toContain('The history could not be loaded.');
    expect(page.text()).not.toContain('Loading…');

    await page.click('Try again');
    page.http
      .expectOne((request) => request.method === 'GET' && request.url === CHOICE_URL)
      .flush({ vehicle_id: VEHICLE_ID, current_choice_id: null, entries: [{ ...entry, action: 'withdraw', ktype: null, reviewer: 'Bea' }, entry] });
    await page.settle();
    expect(page.text()).toContain('Bea withdrew');

    // Closed: a later change does not fetch it.
    details.open = false;
    details.dispatchEvent(new Event('toggle'));
    await page.settle();
    await page.click('Choose this KType', 0);
    const posts = page.http.match((request) => request.method === 'POST');
    expect(posts.length).toBe(1);
    posts[0].flush(lookup({ evidence_fingerprint: FINGERPRINT, choice: stored({ reviewer: 'Bea', history_count: 3 }) }), {
      status: 201,
      statusText: 'Created',
    });
    await page.settle();
    page.http.expectNone((request) => request.method === 'GET' && request.url === CHOICE_URL);
  });

  it('remembers a typed name before any save, so it survives opening another car', async () => {
    const page = await open({}, '');
    const name = page.host.querySelector('ns-ktype-choice input') as HTMLInputElement;
    name.value = ' Dan ';
    name.dispatchEvent(new Event('input'));
    await page.settle();

    expect(localStorage.getItem('match-review-reviewer')).toBe('Dan');
  });

  it('does not offer a retry for a refusal that would be refused again, and shows what was wrong', async () => {
    const page = await open();
    await page.click('Choose this KType', 0);
    page.http
      .expectOne(CHOICE_URL)
      .flush({ detail: { code: 'not_storable', message: 'The request holds a value that cannot be stored. Nothing was saved.' } }, { status: 422, statusText: 'x' });
    await page.settle();
    let alert = page.host.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Not saved. The request holds a value that cannot be stored.');
    expect(alert?.querySelector('button')).toBeNull();

    // A malformed request: FastAPI answers a list of problems, each with its message.
    await page.click('Choose this KType', 0);
    page.http
      .expectOne(CHOICE_URL)
      .flush({ detail: [{ loc: ['body', 'reviewer'], msg: 'Value error, reviewer must not contain control characters' }] }, { status: 422, statusText: 'x' });
    await page.settle();
    alert = page.host.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Not saved. Value error, reviewer must not contain control characters');
    expect(alert?.querySelector('button')).toBeNull();
  });

  it('reads the server’s reason from the new error shape too', async () => {
    const fixture = render();
    await answer(fixture, () => [{ detail: { code: 'unavailable', message: 'Choices are unavailable.' } }, 503]);

    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Choices are unavailable.');
  });
});
