/**
 * One correction for several cars, as the corrections panel drives it: whom it applies to,
 * the check as a job, apply and proposal, both ways to undo -- and the question the server
 * asks before one car loses or changes its KType. Mocked; every identifier is made up.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type {
  CorrectableField,
  CorrectionPreviewCounts,
  CorrectionPreviewJob,
  CorrectionScopeOption,
  FactCorrectionState,
  VehicleMatchLookup,
} from '../core/models';
import { CORRECTION_TIMING, FactCorrections } from './fact-corrections';

const VEHICLE_ID = 'NOR-TEST0000000000000000000001';
const OTHER_ID = 'NOR-TEST0000000000000000000002';
const FINGERPRINT = 'a'.repeat(64);
const DECISION_ID = '33333333-3333-4333-8333-333333333333';
const PREVIEW_ID = 'preview-0001';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const BASE = 'http://api.test';
const CORRECTION_URL = `${BASE}/v1/vehicles/${VEHICLE_ID}/corrections`;
const SCOPES_URL = `${CORRECTION_URL}/scopes`;
const START_URL = `${CORRECTION_URL}/preview`;
const PREVIEW_URL = `${BASE}/v1/vehicle-corrections/previews/${PREVIEW_ID}`;
const DECISIONS_URL = `${BASE}/v1/vehicle-corrections/decisions`;
const WITHDRAW_URL = `${DECISIONS_URL}/${DECISION_ID}/withdraw`;
const LOOKUP_URL = `${BASE}/v1/vehicles/matching/lookup`;

const SAME: CorrectionScopeOption = {
  kind: 'same_data',
  label: 'The 4 cars with exactly the same data',
  count: 4,
};
const LIKE: CorrectionScopeOption = {
  kind: 'like_this',
  label: 'All Volvo V70 cars with no drive type',
  count: 212,
  rung: 0,
  conditions: [
    { field: 'manufacturer', operator: 'equals', values: ['VOLVO'] },
    { field: 'drive_type', operator: 'is_empty', values: [] },
  ],
  narrowable: [{ field: 'engine_code', label: 'Engine code', value: 'B4204T' }],
};
const SCOPES = { scopes: [{ kind: 'this_car', label: 'Only this car' }, SAME, LIKE] };

function fields(): CorrectableField[] {
  const text = { type: 'text' as const, values: [] as string[], suggestions: [] as string[] };
  return [
    { ...text, field: 'engine_code', label: 'Engine code', evidence_keys: ['engine_code'], current_value: 'B4204T', current_source: 'registry' },
    { ...text, field: 'drive_type', label: 'Drive type', values: ['fwd', 'rwd', 'awd'], evidence_keys: ['drive_type'], current_value: null, current_source: null },
  ];
}

function lookup(overrides: Partial<VehicleMatchLookup> = {}): VehicleMatchLookup {
  return {
    vehicle_id: VEHICLE_ID,
    source_record_id: 1,
    plate: null,
    vin: null,
    catalog_batch: 'catalog-test',
    terminal: 'review_required',
    bucket: 'several',
    confidence: 0.9,
    top_ktype: null,
    reason_codes: [],
    verdict: null,
    rule_filled: [],
    overlaid_fields: {},
    inputs: null,
    candidates: [],
    candidate_limit: 5,
    separating_fields: [],
    missing_separating_fields: [],
    decision_trace: [],
    other_vehicle_ids: [],
    evidence_fingerprint: FINGERPRINT,
    choice: null,
    corrections: [],
    correctable_fields: fields(),
    ...overrides,
  };
}

function counts(overrides: Partial<CorrectionPreviewCounts> = {}): CorrectionPreviewCounts {
  return {
    gained: 0, lost: 0, moved: 0, same: 0, worse: 0, still_unresolved: 0, no_effect: 0,
    already_corrected: 0, not_like_this: 0, with_choice: 0, choice_would_disagree: 0,
    still_unresolved_by_terminal: {},
    ...overrides,
  };
}

function job(overrides: Partial<CorrectionPreviewJob> = {}): CorrectionPreviewJob {
  return {
    preview_id: PREVIEW_ID,
    status: 'done',
    field: 'drive_type',
    action: 'set',
    value: 'fwd',
    scope: { kind: 'like_this', label: 'All Volvo V70 cars with no drive type' },
    affected: 212,
    cap: 500,
    checked: 212,
    complete: true,
    stopped_by: null,
    counts: counts({ gained: 171, moved: 2, same: 9, still_unresolved: 30 }),
    engine_check: { agree: 0, differ: 0, unchecked: 171 },
    would_write: 210,
    can_apply: true,
    blocked_by: [],
    ...overrides,
  };
}

const RUNNING = job({ status: 'running', checked: 0, complete: false, counts: counts(), would_write: 0, can_apply: false });

/** The car once a decision for 59 cars set its drive type. */
function decided(): VehicleMatchLookup {
  const head: FactCorrectionState = {
    field: 'drive_type',
    status: 'set',
    correction_id: '22222222-2222-4222-8222-222222222222',
    value: 'fwd',
    reviewer: 'Anna',
    reason: 'Registration papers',
    created_at: '2026-10-01T09:30:00Z',
    previous_value: null,
    previous_source: null,
    group_id: DECISION_ID,
    decision: {
      decision_id: DECISION_ID,
      scope_label: 'All Volvo V70 cars with no drive type',
      member_count: 59,
      reviewer: 'Anna',
    },
    history_count: 1,
  };
  const corrected = fields().map((field) =>
    field.field === 'drive_type' ? { ...field, current_value: 'fwd', current_source: 'correction' } : field,
  );
  return lookup({ corrections: [head], correctable_fields: corrected, evidence_fingerprint: 'b'.repeat(64) });
}

function squash(element: Element | null | undefined): string {
  return (element?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function refusal(code: string, message = 'x') {
  return { detail: { code, message } };
}

function render(inputs: { lookup?: VehicleMatchLookup; reason?: string } = {}) {
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: API_BASE_URL, useValue: BASE },
      // No waiting for the value to settle, and a poll every few milliseconds.
      { provide: CORRECTION_TIMING, useValue: { pollMs: 5, settleMs: 0 } },
    ],
  });
  const fixture = TestBed.createComponent(FactCorrections);
  fixture.componentRef.setInput('lookup', inputs.lookup ?? lookup());
  fixture.componentRef.setInput('reviewer', 'Bea');
  fixture.componentRef.setInput('reason', inputs.reason ?? '');
  const emitted: VehicleMatchLookup[] = [];
  fixture.componentInstance.corrected.subscribe((next) => {
    emitted.push(next);
    fixture.componentRef.setInput('lookup', next);
  });
  fixture.detectChanges();

  const host = fixture.nativeElement as HTMLElement;
  const http = TestBed.inject(HttpTestingController);
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  /** Let the timers run: the wait before the scopes are asked, and the next poll. */
  const pause = async () => {
    await new Promise((resolve) => setTimeout(resolve, 25));
    await settle();
  };
  const row = (label: string) => {
    const found = [...host.querySelectorAll('ul > li')].find(
      (item) => squash(item.querySelector('.row > span')) === label,
    );
    expect(found, `row "${label}"`).toBeTruthy();
    return found as HTMLLIElement;
  };
  const button = (label: string, name: string) =>
    [...row(label).querySelectorAll('button')].find((item) => item.textContent?.trim() === name);
  const click = async (label: string, name: string) => {
    expect(button(label, name), `button "${name}" of "${label}"`).toBeTruthy();
    button(label, name)?.click();
    await settle();
  };
  const status = (label: string) => squash(row(label).querySelector(':scope > [role="status"]'));
  const alert = (label: string) => squash(row(label).querySelector(':scope > [role="alert"]'));
  const radios = (label: string) =>
    [...row(label).querySelectorAll<HTMLInputElement>('ns-correction-preview input[type="radio"]')];
  const type = async (field: HTMLTextAreaElement | HTMLInputElement, value: string) => {
    field.value = value;
    field.dispatchEvent(new Event('input'));
    await settle();
  };

  /** Open the drive type's editor, choose "fwd" and answer whom it could apply to. */
  const enter = async (scopes: object = SCOPES) => {
    await click('Drive type', 'Correct…');
    const select = row('Drive type').querySelector('form select') as HTMLSelectElement;
    select.value = 'fwd';
    select.dispatchEvent(new Event('change'));
    await pause();
    const asked = http.expectOne(SCOPES_URL);
    asked.flush(scopes);
    await settle();
    return asked.request;
  };
  /** ... pick the wide group and press "Check"; the check starts running. */
  const startCheck = async (started: CorrectionPreviewJob = RUNNING) => {
    await enter();
    radios('Drive type')[2].click();
    await settle();
    await click('Drive type', 'Check');
    const start = http.expectOne(START_URL);
    start.flush(started, { status: 202, statusText: 'Accepted' });
    await settle();
    return start.request;
  };
  /** ... and the check ends with this result. */
  const checked = async (result: CorrectionPreviewJob = job()) => {
    await startCheck();
    await pause();
    http.expectOne(PREVIEW_URL).flush(result);
    await settle();
  };
  const giveReason = (text: string) =>
    type(row('Drive type').querySelector('ns-correction-preview textarea') as HTMLTextAreaElement, text);

  return {
    fixture, host, http, emitted, settle, pause, row, button, click, status, alert, radios, type,
    enter, startCheck, checked, giveReason,
  };
}

describe('FactCorrections: whom a correction applies to', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('asks whom a valid new value could apply to, and saves for this car alone by default', async () => {
    const page = render();
    await page.click('Drive type', 'Correct…');
    // Nothing is entered yet: there is nothing to apply to anyone.
    expect(page.row('Drive type').querySelector('ns-correction-preview')).toBeNull();

    const asked = await (async () => {
      const select = page.row('Drive type').querySelector('form select') as HTMLSelectElement;
      select.value = 'fwd';
      select.dispatchEvent(new Event('change'));
      await page.pause();
      return page.http.expectOne(SCOPES_URL);
    })();
    expect(asked.request.method).toBe('POST');
    expect(asked.request.body).toEqual({ field: 'drive_type', action: 'set', value: 'fwd' });
    expect(squash(page.row('Drive type'))).toContain('Looking for other cars like this one…');
    asked.flush(SCOPES);
    await page.settle();

    expect(squash(page.row('Drive type').querySelector('fieldset legend'))).toBeTruthy();
    expect(page.radios('Drive type').map((radio) => squash(radio.closest('label')))).toEqual([
      'Only this car',
      'The 4 cars with exactly the same data as this one',
      'All 212 Volvo V70 cars with no drive type',
    ]);
    expect(page.button('Drive type', 'Check')).toBeUndefined();

    await page.click('Drive type', 'Save');
    const saved = page.http.expectOne(CORRECTION_URL);
    expect(saved.request.body).toMatchObject({ field: 'drive_type', action: 'set', value: 'fwd' });
    expect(saved.request.body.confirm_change).toBeUndefined();
    page.http.verify();
  });

  it('asks again for the present value marked as wrong, and checks that for a group', async () => {
    const page = render();
    await page.click('Engine code', 'Correct…');
    await page.click('Engine code', 'Mark the present value as wrong');
    expect(page.button('Engine code', 'Mark the present value as wrong')?.getAttribute('aria-pressed')).toBe('true');
    await page.pause();

    const asked = page.http.expectOne(`${CORRECTION_URL}/scopes`);
    expect(asked.request.body).toEqual({ field: 'engine_code', action: 'ignore', value: null });
    asked.flush({ scopes: [{ kind: 'this_car', label: 'Only this car' }, SAME] });
    await page.settle();

    page.radios('Engine code')[1].click();
    await page.settle();
    await page.click('Engine code', 'Check');
    const start = page.http.expectOne(START_URL);
    expect(start.request.body).toEqual({
      field: 'engine_code',
      action: 'ignore',
      value: null,
      scope: { kind: 'same_data', rung: null, conditions: null, narrow: [] },
      evidence_fingerprint: FINGERPRINT,
    });
    start.flush(job({ scope: { kind: 'same_data', label: 'The 4 cars with exactly the same data' }, affected: 4, checked: 4, counts: counts({ gained: 4 }), would_write: 4 }), {
      status: 202,
      statusText: 'Accepted',
    });
    await page.settle();
    // Already ended when it was answered: nothing is polled.
    expect(squash(page.row('Engine code').querySelector('h5'))).toBe('What would change for the 4 cars');
    expect(squash(page.row('Engine code'))).toContain('With Engine code marked as wrong:');
    page.http.verify();
  });

  it('does not ask for a value that is not valid, and drops the question when the editor closes', async () => {
    const page = render();
    await page.click('Engine code', 'Correct…');
    await page.type(page.row('Engine code').querySelector('form input') as HTMLInputElement, 'B4204T');
    await page.pause();
    // The value the car already has.
    page.http.expectNone(SCOPES_URL);

    await page.type(page.row('Engine code').querySelector('form input') as HTMLInputElement, 'B4204T2');
    await page.click('Engine code', 'Cancel');
    await page.pause();
    page.http.verify();
  });
});

describe('FactCorrections: the check of a correction for several cars', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('starts the check for the picked group, polls it, shows progress and then the result', async () => {
    const page = render();
    const start = await page.startCheck();

    expect(start.method).toBe('POST');
    expect(start.body).toEqual({
      field: 'drive_type',
      action: 'set',
      value: 'fwd',
      scope: { kind: 'like_this', rung: 0, conditions: LIKE.conditions, narrow: [] },
      evidence_fingerprint: FINGERPRINT,
    });
    const preview = () => page.row('Drive type').querySelector('ns-correction-preview') as HTMLElement;
    expect(squash(preview().querySelector('[role="status"]'))).toBe('Checking 0 of 212 cars…');
    // What is being checked cannot be changed under the check.
    expect((page.row('Drive type').querySelector('form select') as HTMLSelectElement).disabled).toBe(true);
    expect(page.button('Drive type', 'Check')).toBeUndefined();

    await page.pause();
    const first = page.http.expectOne(PREVIEW_URL);
    expect(first.request.method).toBe('GET');
    first.flush({ ...RUNNING, checked: 120 });
    await page.settle();
    expect(squash(preview().querySelector('[role="status"]'))).toBe('Checking 120 of 212 cars…');
    expect((preview().querySelector('progress') as HTMLProgressElement).value).toBe(120);

    await page.pause();
    page.http.expectOne(PREVIEW_URL).flush(job());
    await page.settle();
    const heading = preview().querySelector('h5') as HTMLElement;
    expect(squash(heading)).toBe('What would change for the 212 cars');
    expect(document.activeElement).toBe(heading);

    // The check has ended: nothing more is asked.
    await page.pause();
    page.http.verify();
  });

  it('sends the conditions that narrow the group', async () => {
    const page = render();
    await page.enter();
    page.radios('Drive type')[2].click();
    await page.settle();
    await page.click('Drive type', 'Narrow it…');
    (page.row('Drive type').querySelector('ns-correction-preview input[type="checkbox"]') as HTMLInputElement).click();
    await page.settle();
    await page.click('Drive type', 'Check');

    expect(page.http.expectOne(START_URL).request.body.scope.narrow).toEqual([
      { field: 'engine_code', operator: 'equals', values: ['B4204T'] },
    ]);
  });

  it('stops a running check on Stop and shows what it found so far', async () => {
    const page = render();
    await page.startCheck();
    await page.click('Drive type', 'Stop');

    const stop = page.http.expectOne(PREVIEW_URL);
    expect(stop.request.method).toBe('DELETE');
    stop.flush({});
    await page.pause();
    page.http.expectOne(PREVIEW_URL).flush(
      job({ status: 'cancelled', checked: 40, complete: false, stopped_by: 'stopped', counts: counts({ gained: 40 }), would_write: 40, can_apply: false, blocked_by: ['not_all_cars_checked'] }),
    );
    await page.settle();

    expect(squash(page.row('Drive type').querySelector('h5'))).toBe(
      'What would change for the 40 cars that were checked',
    );
    expect(squash(page.row('Drive type'))).toContain('The check was stopped after 40 of 212 cars');
    await page.pause();
    page.http.verify();
  });

  it.each([
    [429, 'busy', 'Another check is running. Try again in a moment.'],
    [422, 'scope_too_broad', 'This group is too large to check. Narrow it and check again.'],
    [503, 'unavailable', 'The check could not be started. Try again.'],
  ])('says why a check did not start (%i %s) and gives the Check button back', async (code, name, said) => {
    const page = render();
    await page.enter();
    page.radios('Drive type')[2].click();
    await page.settle();
    await page.click('Drive type', 'Check');
    page.http.expectOne(START_URL).flush(refusal(name), { status: code, statusText: 'x' });
    await page.settle();

    expect(page.alert('Drive type')).toBe(said);
    const check = page.button('Drive type', 'Check') as HTMLButtonElement;
    expect(check.disabled).toBe(false);
    expect(document.activeElement).toBe(check);
    // The group stays picked, so "Check" asks again.
    expect(page.radios('Drive type')[2].checked).toBe(true);
    page.http.verify();
  });

  it('asks a lost poll again, and gives up on a check that is gone', async () => {
    const page = render();
    await page.startCheck();
    await page.pause();
    page.http.expectOne(PREVIEW_URL).flush('', { status: 502, statusText: 'x' });
    await page.pause();
    page.http.expectOne(PREVIEW_URL).flush(refusal('preview_expired'), { status: 409, statusText: 'x' });
    await page.settle();

    expect(page.alert('Drive type')).toBe('The check is too old. Check again.');
    expect(page.button('Drive type', 'Check')).toBeTruthy();
    // Nothing is polled any more; the last known state was "running", so the server is told to stop.
    await page.pause();
    expect(page.http.expectOne(PREVIEW_URL).request.method).toBe('DELETE');
    page.http.verify();
  });

  it('stops polling, and the check, when the panel goes away', async () => {
    const page = render();
    await page.startCheck();
    page.fixture.destroy();

    expect(page.http.expectOne(PREVIEW_URL).request.method).toBe('DELETE');
    await new Promise((resolve) => setTimeout(resolve, 25));
    page.http.verify();
  });

  it('stops polling, and the check, when another car is shown', async () => {
    const page = render();
    await page.startCheck();
    page.fixture.componentRef.setInput('lookup', lookup({ vehicle_id: OTHER_ID }));
    await page.settle();

    expect(page.http.expectOne(PREVIEW_URL).request.method).toBe('DELETE');
    expect(page.host.querySelector('form')).toBeNull();
    await page.pause();
    page.http.verify();
  });

  it('reads the cars behind a count from the check', async () => {
    const page = render();
    await page.checked();
    const list = page.row('Drive type').querySelector('details') as HTMLDetailsElement;
    list.open = true;
    list.dispatchEvent(new Event('toggle'));
    await page.settle();

    const cars = page.http.expectOne((request) => request.url === `${PREVIEW_URL}/cars`);
    expect(cars.request.params.get('outcome')).toBe('gained');
    expect(cars.request.params.get('limit')).toBe('50');
    cars.flush({
      preview_id: PREVIEW_ID,
      outcome: 'gained',
      total: 171,
      offset: 0,
      cars: [
        { vehicle_id: OTHER_ID, plate: 'TST002', outcome: 'gained', before: { terminal: 'review_required', ktype: null }, after: { terminal: 'resolved', ktype: '000010064' } },
      ],
    });
    await page.settle();
    expect(squash(list.querySelector('ul.cars li'))).toBe('TST002 tie → KType 000010064');
  });

  it('goes back to the group on Cancel, with focus on Check, and to its conditions on Narrow it…', async () => {
    const page = render();
    await page.checked(job({ affected: 4275, checked: 500, complete: false, stopped_by: 'cap', can_apply: false, blocked_by: ['not_all_cars_checked'] }));

    await page.click('Drive type', 'Narrow it…');
    expect(page.row('Drive type').querySelector('h5')).toBeNull();
    expect(page.radios('Drive type')[2].checked).toBe(true);
    const box = page.row('Drive type').querySelector('ns-correction-preview input[type="checkbox"]');
    expect(document.activeElement).toBe(box);

    await page.click('Drive type', 'Check');
    page.http.expectOne(START_URL).flush(job(), { status: 202, statusText: 'Accepted' });
    await page.settle();
    const cancel = [...page.row('Drive type').querySelectorAll<HTMLButtonElement>('ns-correction-preview button')].find(
      (item) => squash(item) === 'Cancel',
    );
    cancel?.click();
    await page.settle();
    expect(document.activeElement).toBe(page.button('Drive type', 'Check'));
    page.http.verify();
  });
});

describe('FactCorrections: applying a checked correction', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('applies with a reason and one operation id, says what was done and reads the car again', async () => {
    const page = render();
    await page.checked();
    expect((page.button('Drive type', 'Apply to 210 cars') as HTMLButtonElement).disabled).toBe(true);
    await page.giveReason(' Registration papers ');
    await page.click('Drive type', 'Apply to 210 cars');

    const apply = page.http.expectOne(DECISIONS_URL);
    expect(apply.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      preview_id: PREVIEW_ID,
      event: 'apply',
      include_changed: false,
      reviewer: 'Bea',
      reason: 'Registration papers',
    });
    expect(page.status('Drive type')).toBe('Applying this and matching the car again…');
    apply.flush(
      { decision_id: DECISION_ID, status: 'applied', written: 210, skipped: { changed_since_check: 0, corrected_meanwhile: 0 }, counts: counts({ gained: 171 }), scope_label: LIKE.label, written_by_outcome: { gained: 171, same: 9, still_unresolved: 30 } },
      { status: 201, statusText: 'Created' },
    );
    await page.settle();

    expect(page.status('Drive type')).toBe('Applied to 210 cars. 171 now resolve.');
    expect(page.row('Drive type').querySelector('form')).toBeNull();
    expect(page.fixture.componentInstance.reason()).toBe('');
    expect(localStorage.getItem('match-review-reviewer')).toBe('Bea');

    const read = page.http.expectOne((request) => request.url === LOOKUP_URL);
    expect(read.request.params.get('vehicle_id')).toBe(VEHICLE_ID);
    read.flush(decided());
    await page.settle();
    expect(page.emitted).toEqual([decided()]);
    expect(squash(page.row('Drive type').querySelectorAll('.row > span')[1])).toBe(
      'fwd set for 59 cars like this by Anna: “All Volvo V70 cars with no drive type” on 2026-10-01 (was —) — “Registration papers”',
    );
    // What was said about the apply is still there.
    expect(page.status('Drive type')).toBe('Applied to 210 cars. 171 now resolve.');
    page.http.verify();
  });

  it('includes the cars that would move only when they are ticked', async () => {
    const page = render({ reason: 'Seen' });
    await page.checked();
    const box = page.row('Drive type').querySelector('ns-correction-preview .note input') as HTMLInputElement;
    expect(squash(box.closest('label'))).toBe('Include these 2 cars; I looked at them');
    box.click();
    await page.settle();
    await page.click('Drive type', 'Apply to 212 cars');

    expect(page.http.expectOne(DECISIONS_URL).request.body).toMatchObject({ event: 'apply', include_changed: true });
  });

  it('sends a busy apply again with the same operation id and body', async () => {
    const page = render({ reason: 'Seen' });
    await page.checked();
    await page.click('Drive type', 'Apply to 210 cars');
    const first = page.http.expectOne(DECISIONS_URL);
    first.flush(refusal('vehicles_busy'), { status: 503, statusText: 'x' });
    await page.settle();

    expect(page.alert('Drive type')).toBe(
      'Not saved. Someone else is changing some of these cars right now. Try again. Try again',
    );
    const again = page.row('Drive type').querySelector(':scope > [role="alert"] button') as HTMLButtonElement;
    expect(again.getAttribute('aria-label')).toBe('Try again: Drive type');
    again.click();
    await page.settle();
    const second = page.http.expectOne(DECISIONS_URL);
    expect(second.request.body).toEqual(first.request.body);
    // The result is still on screen: the check was not lost.
    expect(page.row('Drive type').querySelector('h5')).toBeTruthy();
  });

  it.each([
    [409, 'preview_expired', 'The check is too old. Check again.', false],
    [409, 'preview_incomplete', 'Not saved. The check did not reach every car. Check again.', false],
    [422, 'nothing_to_apply', 'Not saved. No car in this group would take the correction.', true],
    [
      422,
      'harms_more_than_it_fixes',
      'Not saved. This would harm at least as many cars as it fixes. Narrow the group, or save it as a proposal.',
      true,
    ],
    [422, 'reason_required', 'Not saved. Give a reason first.', true],
  ])('says why an apply was refused (%i %s)', async (code, name, said, resultStays) => {
    const page = render({ reason: 'Seen' });
    await page.checked();
    await page.click('Drive type', 'Apply to 210 cars');
    page.http.expectOne(DECISIONS_URL).flush(refusal(name), { status: code, statusText: 'x' });
    await page.settle();

    expect(page.alert('Drive type')).toBe(said);
    expect(!!page.row('Drive type').querySelector('h5')).toBe(resultStays);
    // A check that no longer holds gives the Check button back.
    expect(!!page.button('Drive type', 'Check')).toBe(!resultStays);
    // The reason was for an apply that did not happen: it is kept.
    expect(page.fixture.componentInstance.reason()).toBe('Seen');
    page.http.verify();
  });

  it('saves a check that did not reach every car as a proposal, which writes no car', async () => {
    const page = render();
    await page.checked(job({ affected: 4275, checked: 500, complete: false, stopped_by: 'cap', can_apply: false, blocked_by: ['not_all_cars_checked'] }));
    await page.click('Drive type', 'Save as a proposal');

    const propose = page.http.expectOne(DECISIONS_URL);
    expect(propose.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      preview_id: PREVIEW_ID,
      event: 'propose',
      include_changed: false,
      reviewer: 'Bea',
      reason: null,
    });
    expect(page.status('Drive type')).toBe('Saving the proposal…');
    propose.flush({ decision_id: DECISION_ID, status: 'proposed', written: 0 }, { status: 201, statusText: 'Created' });
    await page.settle();

    expect(page.status('Drive type')).toBe(
      'Saved as a proposal. No car was changed: it is to be measured on all cars first.',
    );
    expect(page.emitted).toEqual([]);
    // No car was written, so this one is not read again.
    page.http.verify();
  });

  it('says so when the car could not be read again after an apply', async () => {
    const page = render({ reason: 'Seen' });
    await page.checked();
    await page.click('Drive type', 'Apply to 210 cars');
    page.http.expectOne(DECISIONS_URL).flush({ decision_id: DECISION_ID, status: 'applied', written: 210 });
    await page.settle();
    page.http.expectOne((request) => request.url === LOOKUP_URL).flush('', { status: 503, statusText: 'x' });
    await page.settle();

    expect(page.status('Drive type')).toBe(
      'Applied to 210 cars. The car could not be read again: close it and open it once more.',
    );
  });
});

describe('FactCorrections: undoing a correction that came from a decision', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('undoes it for this car alone with the one-car withdrawal', async () => {
    const page = render({ lookup: decided() });
    expect(page.button('Drive type', 'Undo correction')).toBeUndefined();
    await page.click('Drive type', 'Undo for this car');

    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      field: 'drive_type',
      action: 'withdraw',
      value: null,
      supersedes_correction_id: '22222222-2222-4222-8222-222222222222',
    });
    page.http.expectNone(WITHDRAW_URL);
  });

  it('asks for a reason and a confirmation before undoing it for all cars', async () => {
    const page = render({ lookup: decided() });
    const all = page.button('Drive type', 'Undo for all 59 cars…') as HTMLButtonElement;
    expect(all.getAttribute('aria-label')).toBe('Undo for all 59 cars: Drive type');
    all.click();
    await page.settle();

    expect(all.getAttribute('aria-expanded')).toBe('true');
    const ask = page.row('Drive type').querySelector('.ask') as HTMLElement;
    expect(squash(ask.querySelector('p'))).toBe(
      'Undo this for all 59 cars? Each goes back to its own data. A car a person has changed since is left as it is.',
    );
    const reason = ask.querySelector('textarea') as HTMLTextAreaElement;
    expect(document.activeElement).toBe(reason);
    const confirm = page.button('Drive type', 'Confirm') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(squash(ask.querySelector(`#${confirm.getAttribute('aria-describedby')}`))).toBe(
      'Give a reason to undo this for several cars.',
    );

    // Cancelled: nothing is sent, and focus is back on the button that asked.
    await page.click('Drive type', 'Cancel');
    expect(page.row('Drive type').querySelector('.ask')).toBeNull();
    expect(document.activeElement).toBe(all);
    page.http.verify();
  });

  it('undoes it for all cars, retries with the same operation id, and says what was left', async () => {
    const page = render({ lookup: decided() });
    await page.click('Drive type', 'Undo for all 59 cars…');
    await page.type(page.row('Drive type').querySelector('.ask textarea') as HTMLTextAreaElement, 'Wrong group');
    await page.click('Drive type', 'Confirm');

    const first = page.http.expectOne(WITHDRAW_URL);
    expect(first.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      reviewer: 'Bea',
      reason: 'Wrong group',
    });
    expect(page.status('Drive type')).toBe('Undoing this for all 59 cars…');
    first.flush(refusal('vehicles_busy'), { status: 503, statusText: 'x' });
    await page.settle();
    (page.row('Drive type').querySelector(':scope > [role="alert"] button') as HTMLButtonElement).click();
    await page.settle();

    const second = page.http.expectOne(WITHDRAW_URL);
    expect(second.request.body).toEqual(first.request.body);
    second.flush({ decision_id: DECISION_ID, status: 'withdrawn', withdrawn: 58, left_changed: 1, member_count: 59 });
    await page.settle();

    expect(page.status('Drive type')).toBe(
      'Undone for 58 cars. 1 was changed by a person since and was left as it is.',
    );
    expect(page.row('Drive type').querySelector('.ask')).toBeNull();
    page.http.expectOne((request) => request.url === LOOKUP_URL).flush(lookup());
    await page.settle();
    expect(page.emitted).toEqual([lookup()]);
    expect(page.button('Drive type', 'Undo for this car')).toBeUndefined();
    expect(page.fixture.componentInstance.reason()).toBe('');
    page.http.verify();
  });
});

describe('FactCorrections: a correction that would take a resolved car’s KType', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  const refused = (after: { terminal: string; ktype: string | null }) => ({
    detail: {
      code: 'confirmation_required',
      message: 'x',
      before: { terminal: 'resolved', ktype: '000010064' },
      after,
    },
  });

  async function save(page: ReturnType<typeof render>) {
    await page.click('Engine code', 'Correct…');
    await page.type(page.row('Engine code').querySelector('form input') as HTMLInputElement, 'B4204T9');
    await page.click('Engine code', 'Save');
    return page.http.expectOne(CORRECTION_URL);
  }

  it('asks before saving, and “Save anyway” resends the same body confirmed', async () => {
    const page = render({ reason: 'Seen' });
    const first = await save(page);
    first.flush(refused({ terminal: 'resolved', ktype: '000011508' }), { status: 409, statusText: 'x' });
    await page.settle();

    const ask = page.row('Engine code').querySelector('.ask') as HTMLElement;
    expect(squash(ask.querySelector('p'))).toBe(
      'This car is resolved to KType 000010064 today. With Engine code B4204T9 it would resolve to KType 000011508.',
    );
    const anyway = page.button('Engine code', 'Save anyway') as HTMLButtonElement;
    expect(document.activeElement).toBe(anyway);
    expect(anyway.getAttribute('aria-label')).toBe('Save anyway: Engine code');
    expect(page.row('Engine code').querySelector(':scope > [role="alert"]')).toBeNull();

    anyway.click();
    await page.settle();
    const second = page.http.expectOne(CORRECTION_URL);
    expect(second.request.body).toEqual({ ...first.request.body, confirm_change: true });
    expect(first.request.body.confirm_change).toBeUndefined();
    second.flush(lookup(), { status: 201, statusText: 'Created' });
    await page.settle();
    expect(page.status('Engine code')).toContain('Saved. Engine code is now B4204T9.');
    expect(page.row('Engine code').querySelector('.ask')).toBeNull();
    page.http.verify();
  });

  it('says when the car would no longer resolve, and saves nothing on Cancel', async () => {
    const page = render();
    (await save(page)).flush(refused({ terminal: 'review_required', ktype: null }), { status: 409, statusText: 'x' });
    await page.settle();

    expect(squash(page.row('Engine code').querySelector('.ask p'))).toBe(
      'This car is resolved to KType 000010064 today. With Engine code B4204T9 it would no longer resolve.',
    );
    const cancel = [...page.row('Engine code').querySelectorAll<HTMLButtonElement>('.ask button')].find(
      (item) => squash(item) === 'Cancel',
    );
    cancel?.click();
    await page.settle();

    expect(page.row('Engine code').querySelector('.ask')).toBeNull();
    // The editor is still open with what was typed, and focus is on its Save.
    expect((page.row('Engine code').querySelector('form input') as HTMLInputElement).value).toBe('B4204T9');
    expect(document.activeElement).toBe(page.button('Engine code', 'Save'));
    page.http.verify();
  });

  it('says a value that cannot be stored was not saved', async () => {
    const page = render();
    (await save(page)).flush(refusal('not_storable', 'The request holds a value that cannot be stored.'), {
      status: 422,
      statusText: 'x',
    });
    await page.settle();

    expect(page.alert('Engine code')).toBe('Not saved. This value cannot be stored.');
    expect(page.row('Engine code').querySelector(':scope > [role="alert"] button')).toBeNull();
  });
});
