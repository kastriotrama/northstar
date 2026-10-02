/**
 * Correcting one car's data: what each row says, the editor per kind of value, what goes
 * over the wire, how a failure reads, the history, and the hook into the candidates panel.
 * Mocked: the lookups are fixtures with made-up identifiers.
 */

import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { API_BASE_URL } from '../core/api-config';
import type {
  CorrectableField,
  FactCorrectionHistory,
  FactCorrectionHistoryEntry,
  FactCorrectionState,
  KTypeCandidate,
  VehicleMatchLookup,
} from '../core/models';
import { FactCorrections } from './fact-corrections';
import { KTypeCandidates } from './ktype-candidates';

const VEHICLE_ID = 'NOR-01J8Z3Y5W2QK4T7B9C1D3E5F7G';
const FINGERPRINT = 'f'.repeat(64);
const CORRECTION_URL = `http://api.test/v1/vehicles/${VEHICLE_ID}/corrections`;
const HEAD = '22222222-2222-4222-8222-222222222222';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const PROVIDERS = [
  provideHttpClient(),
  provideHttpClientTesting(),
  { provide: API_BASE_URL, useValue: 'http://api.test' },
];

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

/** Eight correctable fields in the server's order, as a car without an engine code has them. */
function fields(overrides: Record<string, Partial<CorrectableField>> = {}): CorrectableField[] {
  const text = { type: 'text' as const, values: [], suggestions: [] };
  const whole = { type: 'integer' as const, values: [], suggestions: [] };
  const base: CorrectableField[] = [
    { ...text, field: 'manufacturer', label: 'Manufacturer', evidence_keys: ['manufacturer'], current_value: 'VOLVO', current_source: 'registry' },
    { ...text, field: 'model_family', label: 'Model family', evidence_keys: ['model', 'model_series'], current_value: 'V70', current_source: 'rule' },
    { ...text, field: 'engine_code', label: 'Engine code', evidence_keys: ['engine_code'], current_value: null, current_source: 'registry', suggestions: ['D 5204 T2', 'D 5204 T3'] },
    { ...whole, field: 'power_kw', label: 'Power (kW)', evidence_keys: ['power_kw'], current_value: '120', current_source: 'registry', suggestions: ['100'] },
    { ...whole, field: 'displacement_cc', label: 'Displacement (cc)', evidence_keys: ['displacement_cc'], current_value: '1984', current_source: 'ais' },
    { ...text, field: 'drive_type', label: 'Drive type', values: ['fwd', 'rwd', 'awd'], evidence_keys: ['drive_type'], current_value: null, current_source: 'registry', suggestions: ['fwd', '4x4'] },
    { ...text, field: 'bodywork_form', label: 'Bodywork', values: ['estate', 'saloon', 'multi_purpose_vehicle'], evidence_keys: ['bodywork'], current_value: 'estate', current_source: 'review' },
    { ...whole, field: 'production_year', label: 'Production year', evidence_keys: ['year'], current_value: '2012', current_source: 'derived' },
  ];
  return base.map((item) => ({ ...item, ...overrides[item.field] }));
}

/** A month (a whole number in a range), a closed list of words, and a list of several. */
function more(overrides: Record<string, Partial<CorrectableField>> = {}): CorrectableField[] {
  const base: CorrectableField[] = [
    {
      field: 'production_month',
      label: 'Build month',
      type: 'integer',
      values: ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12'],
      evidence_keys: ['build_month'],
      current_value: '3',
      current_source: 'registry',
      suggestions: [],
    },
    {
      field: 'electrification_type',
      label: 'Electrification',
      type: 'text',
      values: ['battery_electric', 'fuel_cell_hybrid', 'hybrid', 'plug_in_hybrid'],
      evidence_keys: ['electrification', 'match_guard:plug_in'],
      current_value: 'plug_in_hybrid',
      current_source: 'registry',
      suggestions: ['battery_electric'],
    },
    {
      field: 'fuel',
      label: 'Fuel',
      type: 'list',
      values: ['petrol', 'diesel', 'electricity', 'e85', 'cng', 'lpg', 'hydrogen'],
      evidence_keys: ['fuels'],
      current_value: 'petrol,electricity',
      current_source: 'registry',
      suggestions: ['diesel', 'cng,petrol', 'petrol,kerosene'],
    },
  ];
  return base.map((item) => ({ ...item, ...overrides[item.field] }));
}

function correction(overrides: Partial<FactCorrectionState> = {}): FactCorrectionState {
  return {
    field: 'engine_code',
    status: 'set',
    correction_id: HEAD,
    value: 'D5204T3',
    reviewer: 'Anna',
    reason: null,
    created_at: '2026-10-01T09:30:00Z',
    previous_value: 'D5204T2',
    previous_source: 'registry',
    group_id: null,
    history_count: 1,
    ...overrides,
  };
}

/** Two KTypes fit and differ on the engine code the car lacks; a third is ruled out on power. */
function lookup(overrides: Partial<VehicleMatchLookup> = {}): VehicleMatchLookup {
  return {
    vehicle_id: VEHICLE_ID,
    source_record_id: 769,
    plate: null,
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
    evidence_fingerprint: FINGERPRINT,
    choice: null,
    corrections: [],
    correctable_fields: fields(),
    ...overrides,
  };
}

/** One KType and nothing the matcher is unsure of: no row has anything to say. */
const SETTLED = {
  candidates: [candidate('000010064')],
  separating_fields: [],
  missing_separating_fields: [],
};

/** The car once its engine code is corrected: one KType is left. */
function corrected(value = 'D5204T3', reviewer = 'Bea'): VehicleMatchLookup {
  return lookup({
    bucket: 'one',
    top_ktype: '000011508',
    evidence_fingerprint: 'e'.repeat(64),
    candidates: [
      candidate('000011508', { engine_codes: ['D 5204 T3'] }),
      candidate('000010064', { conflicting_fields: ['engine_code'], compatible: false }),
      candidate('000059385', { power_kw: 100, conflicting_fields: ['power_kw'], compatible: false }),
    ],
    separating_fields: [],
    missing_separating_fields: [],
    overlaid_fields: { engine_code: 'correction' },
    corrections: [correction({ value, reviewer, previous_value: null, previous_source: null })],
    correctable_fields: fields({
      engine_code: { current_value: value, current_source: 'correction', suggestions: ['D 5204 T2'] },
    }),
  });
}

function entry(overrides: Partial<FactCorrectionHistoryEntry> = {}): FactCorrectionHistoryEntry {
  return {
    correction_id: HEAD,
    action: 'set',
    value: 'D5204T3',
    reviewer: 'Bea',
    reason: null,
    created_at: '2026-10-01T09:30:00Z',
    supersedes_correction_id: null,
    previous_value: null,
    previous_source: 'correction',
    group_id: null,
    catalog_batch: 'tecdoc-v5',
    automatic_terminal: 'provisional',
    automatic_ktype: null,
    code_version: 'abc',
    ...overrides,
  };
}

function squash(element: Element | null | undefined): string {
  return (element?.textContent ?? '').replace(/\s+/g, ' ').trim();
}

function findButton(within: Element, label: string): HTMLButtonElement | undefined {
  return [...within.querySelectorAll('button')].find((item) => item.textContent?.trim() === label);
}

/** What a screen reader calls a button. */
function nameOf(button: HTMLButtonElement): string {
  return button.getAttribute('aria-label') ?? squash(button);
}

/** The panel on its own; `corrected` is fed back as the lookup, which is the container's part. */
function render(inputs: { lookup?: VehicleMatchLookup; reviewer?: string; reason?: string } = {}) {
  TestBed.configureTestingModule({ providers: PROVIDERS });
  const fixture = TestBed.createComponent(FactCorrections);
  fixture.componentRef.setInput('lookup', inputs.lookup ?? lookup());
  fixture.componentRef.setInput('reviewer', inputs.reviewer ?? 'Bea');
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
  const items = () => [...host.querySelectorAll('ul > li')];
  const labels = () => items().map((item) => squash(item.querySelector('.row > span')));
  const row = (label: string) => {
    const found = items().find((item) => squash(item.querySelector('.row > span')) === label);
    expect(found, `row "${label}"`).toBeTruthy();
    return found as HTMLLIElement;
  };
  /** The value with where it came from, and the hint when there is one. */
  const says = (label: string) => squash(row(label).querySelectorAll('.row > span')[1]);
  const hint = (label: string) => squash(row(label).querySelector('.hint'));
  const click = async (label: string, name: string) => {
    const found = findButton(row(label), name);
    expect(found, `button "${name}" of "${label}"`).toBeTruthy();
    found?.click();
    await settle();
  };
  const control = (label: string) =>
    row(label).querySelector('form input, form select') as HTMLInputElement | HTMLSelectElement;
  const enter = async (label: string, value: string) => {
    const field = control(label);
    field.value = value;
    field.dispatchEvent(new Event(field.tagName === 'SELECT' ? 'change' : 'input'));
    await settle();
  };
  /** Tick or untick one box of a list's editor, by the word beside it. */
  const tick = async (label: string, word: string) => {
    const box = [...row(label).querySelectorAll('fieldset label')].find((item) => squash(item) === word);
    expect(box, `box "${word}" of "${label}"`).toBeTruthy();
    (box?.querySelector('input') as HTMLInputElement).click();
    await settle();
  };
  /** Open the row's editor, give it a value and save. */
  const correct = async (label: string, value: string) => {
    await click(label, 'Correct…');
    await enter(label, value);
    await click(label, 'Save');
  };
  const status = (label: string) => squash(row(label).querySelector('[role="status"]'));
  const alert = (label: string) => row(label).querySelector('[role="alert"]');
  const history = () => host.querySelector('details') as HTMLDetailsElement;
  const openHistory = async (open = true) => {
    history().open = open;
    history().dispatchEvent(new Event('toggle'));
    await settle();
  };
  /** The history's lines without their timestamps, which read in the local time zone. */
  const lines = () =>
    [...history().querySelectorAll('ol li')].map((item) =>
      squash(item).replace(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2} · /, ''),
    );
  return {
    fixture, host, http, emitted, settle, labels, row, says, hint, click, control, enter, tick, correct,
    status, alert, history, openHistory, lines,
  };
}

describe('FactCorrections', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  it('lists what can be corrected: each value, where it came from, and a way to correct it', () => {
    const page = render({
      lookup: lookup({
        ...SETTLED,
        correctable_fields: fields({ engine_code: { current_value: 'D5204T2', current_source: 'correction' } }),
      }),
    });

    expect(page.host.querySelector('h4')?.textContent).toBe("Correct this car's data");
    expect(squash(page.host)).toContain('For this car only. The car is matched again after each change.');
    // Nothing blocks this car, so the rows keep the server's order.
    expect(page.labels()).toEqual([
      'Manufacturer',
      'Model family',
      'Engine code',
      'Power (kW)',
      'Displacement (cc)',
      'Drive type',
      'Bodywork',
      'Production year',
    ]);
    expect(page.labels().map(page.says)).toEqual([
      'VOLVO from the registry',
      'V70 filled by a rule',
      'D5204T2 corrected by a person',
      '120 from the registry',
      '1984 from AIS',
      '—',
      'estate set by a reviewer rule',
      "2012 worked out from the car's other data",
    ]);
    expect(page.host.querySelector('.hint')).toBeNull();

    const openers = [...page.host.querySelectorAll('button')];
    expect(openers.map((item) => item.textContent?.trim())).toEqual(Array(8).fill('Correct…'));
    expect(openers.every((item) => item.getAttribute('aria-expanded') === 'false')).toBe(true);
    expect(page.host.querySelector('form')).toBeNull();
    // Nobody corrected this car, so there is no history to open.
    expect(page.host.querySelector('details')).toBeNull();
  });

  it('puts the fields behind the car’s result first and says how each is involved', () => {
    const tie = render();
    expect(tie.labels().slice(0, 3)).toEqual(['Engine code', 'Power (kW)', 'Manufacturer']);
    expect(tie.says('Engine code')).toBe('— candidates differ on this and the car has no value');
    expect(tie.says('Power (kW)')).toBe('120 from the registry conflicts with some candidates');
    expect(tie.host.querySelectorAll('.hint').length).toBe(2);

    TestBed.resetTestingModule();
    // The matcher's own keys: `year` is the production year, `bodywork` the bodywork, and
    // `model_series` / `engine_code_unverified` belong to their field by prefix.
    const blocked = render({
      lookup: lookup({
        bucket: 'none',
        candidates: [
          candidate('000010064', { conflicting_fields: ['year', 'model_series'], compatible: false }),
          candidate('000011508', { conflicting_fields: ['year'], missing_fields: ['engine_code_unverified'], compatible: false }),
        ],
        separating_fields: ['bodywork', 'drive_type'],
        missing_separating_fields: ['drive_type'],
      }),
    });
    expect(blocked.labels()).toEqual([
      'Production year',
      'Drive type',
      'Model family',
      'Bodywork',
      'Engine code',
      'Manufacturer',
      'Power (kW)',
      'Displacement (cc)',
    ]);
    expect(blocked.hint('Production year')).toBe('conflicts with every candidate');
    expect(blocked.hint('Drive type')).toBe('candidates differ on this and the car has no value');
    expect(blocked.hint('Model family')).toBe('conflicts with some candidates');
    expect(blocked.hint('Bodywork')).toBe('candidates differ on this');
    expect(blocked.hint('Engine code')).toBe('the matcher could not compare this');
    expect(blocked.row('Manufacturer').querySelector('.hint')).toBeNull();
  });

  it('counts a reason code as involvement: the matcher flagged the value', () => {
    const page = render({
      lookup: lookup({
        ...SETTLED,
        candidates: [candidate('000010064', { missing_fields: ['fuels_compatible_not_confirmed'] })],
        // A code is a field's when it equals one of its keys or starts with one and "_".
        reason_codes: [
          'electrification_conflict',
          'match:engine_code_unverified',
          'power_kwh_other',
          'displacement_cc',
        ],
        correctable_fields: [...fields(), ...more()],
      }),
    });

    expect(page.labels().slice(0, 4)).toEqual(['Displacement (cc)', 'Electrification', 'Fuel', 'Manufacturer']);
    expect(page.hint('Electrification')).toBe('the matcher flagged this value');
    expect(page.hint('Displacement (cc)')).toBe('the matcher flagged this value');
    expect(page.hint('Fuel')).toBe('the matcher could not compare this');
    expect(page.host.querySelectorAll('.hint').length).toBe(3);

    TestBed.resetTestingModule();
    // A key may carry the guard's own prefix: "match_guard:plug_in" takes "…:plug_in_power_unverified".
    const guarded = render({
      lookup: lookup({
        ...SETTLED,
        reason_codes: ['match_guard:plug_in_power_unverified'],
        correctable_fields: more(),
      }),
    });
    expect(guarded.labels()).toEqual(['Electrification', 'Build month', 'Fuel']);
    expect(guarded.hint('Electrification')).toBe('the matcher flagged this value');
  });

  it('opens an editor that fits the value: free text, a whole number, or a closed list', async () => {
    const page = render();

    await page.click('Engine code', 'Correct…');
    const form = page.row('Engine code').querySelector('form');
    expect(form?.getAttribute('aria-label')).toBe('Correct Engine code');
    expect(squash(form?.querySelector('label'))).toBe('New value');
    const code = page.control('Engine code') as HTMLInputElement;
    expect([code.tagName, code.type, code.getAttribute('inputmode'), code.value]).toEqual(['INPUT', 'text', null, '']);
    expect(document.activeElement).toBe(code);
    expect(findButton(page.row('Engine code'), 'Correct…')?.getAttribute('aria-expanded')).toBe('true');
    // The car has no engine code, so there is nothing to mark as wrong.
    expect(findButton(page.row('Engine code'), 'Mark the present value as wrong')).toBeUndefined();
    expect(squash(form?.querySelector('[role="group"]'))).toBe('The candidates have: D 5204 T2 D 5204 T3');
    await page.click('Engine code', 'D 5204 T3');
    expect(code.value).toBe('D 5204 T3');

    // One editor at a time; it opens on the value the car has.
    await page.click('Power (kW)', 'Correct…');
    expect(page.host.querySelectorAll('form').length).toBe(1);
    const power = page.control('Power (kW)') as HTMLInputElement;
    expect([power.tagName, power.getAttribute('inputmode'), power.value]).toEqual(['INPUT', 'numeric', '120']);
    expect(document.activeElement).toBe(power);
    expect(findButton(page.row('Power (kW)'), 'Mark the present value as wrong')).toBeTruthy();
    expect(squash(page.row('Power (kW)').querySelector('form'))).toContain(
      'A value marked as wrong is no longer used to match this car.',
    );

    await page.click('Drive type', 'Correct…');
    const drive = page.control('Drive type') as HTMLSelectElement;
    expect(drive.tagName).toBe('SELECT');
    expect([...drive.options].map((option) => option.textContent?.trim())).toEqual(['Choose…', 'fwd', 'rwd', 'awd']);
    expect(drive.value).toBe('');
    // Only a suggestion the closed list knows is offered.
    expect(squash(page.row('Drive type').querySelector('[role="group"]'))).toBe('The candidates have: fwd');
    await page.click('Drive type', 'fwd');
    expect(drive.value).toBe('fwd');

    await page.click('Bodywork', 'Correct…');
    expect((page.control('Bodywork') as HTMLSelectElement).value).toBe('estate');
  });

  it('says a closed list’s values in words and sends them as they are', async () => {
    const page = render({ lookup: lookup({ ...SETTLED, correctable_fields: more() }) });
    expect(page.says('Electrification')).toBe('plug in hybrid from the registry');

    await page.click('Electrification', 'Correct…');
    const select = page.control('Electrification') as HTMLSelectElement;
    expect([...select.options].map((option) => [option.value, option.textContent?.trim()])).toEqual([
      ['', 'Choose…'],
      ['battery_electric', 'battery electric'],
      ['fuel_cell_hybrid', 'fuel cell hybrid'],
      ['hybrid', 'hybrid'],
      ['plug_in_hybrid', 'plug in hybrid'],
    ]);
    expect(select.value).toBe('plug_in_hybrid');
    const suggestion = findButton(page.row('Electrification'), 'battery electric');
    expect(suggestion?.getAttribute('aria-label')).toBe('Use battery electric for Electrification');
    await page.click('Electrification', 'battery electric');
    await page.click('Electrification', 'Save');

    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.body).toMatchObject({ field: 'electrification_type', action: 'set', value: 'battery_electric' });
    request.flush(
      lookup({
        ...SETTLED,
        corrections: [
          correction({ field: 'electrification_type', value: 'battery_electric', reviewer: 'Bea', previous_value: 'plug_in_hybrid' }),
        ],
        correctable_fields: more({ electrification_type: { current_value: 'battery_electric', current_source: 'correction' } }),
      }),
      { status: 201, statusText: 'Created' },
    );
    await page.settle();

    expect(page.status('Electrification')).toBe(
      'Saved. Electrification is now battery electric. The car was matched again: one KType fits.',
    );
    expect(page.says('Electrification')).toBe(
      'battery electric corrected by Bea on 2026-10-01: plug in hybrid → battery electric',
    );
  });

  it('corrects a list with one to three ticks, sent comma-joined in the list’s own order', async () => {
    const page = render({ lookup: lookup({ ...SETTLED, correctable_fields: more() }) });
    expect(page.says('Fuel')).toBe('petrol, electricity from the registry');

    await page.click('Fuel', 'Correct…');
    const fieldset = page.row('Fuel').querySelector('form fieldset') as HTMLFieldSetElement;
    const boxes = () => [...fieldset.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    const ticked = () => [...fieldset.querySelectorAll('label')].filter((item) => item.querySelector('input')?.checked).map(squash);
    const save = () => findButton(page.row('Fuel'), 'Save') as HTMLButtonElement;
    expect(squash(fieldset.querySelector('legend'))).toBe('New value (choose 1 to 3)');
    expect([...fieldset.querySelectorAll('label')].map(squash)).toEqual([
      'petrol',
      'diesel',
      'electricity',
      'e85',
      'cng',
      'lpg',
      'hydrogen',
    ]);
    expect(ticked()).toEqual(['petrol', 'electricity']);
    expect(document.activeElement).toBe(boxes()[0]);
    expect(save().disabled).toBe(true);

    // A third tick fills the list: the other boxes are off until one is unticked.
    await page.tick('Fuel', 'hydrogen');
    expect(boxes().map((box) => box.disabled)).toEqual([false, true, false, true, true, true, false]);
    expect(save().disabled).toBe(false);
    for (const word of ['petrol', 'electricity', 'hydrogen']) await page.tick('Fuel', word);
    expect(ticked()).toEqual([]);
    expect(boxes().every((box) => !box.disabled)).toBe(true);
    expect(save().disabled).toBe(true);

    // A suggestion is a whole value; one with a word the list does not have is not offered.
    expect(squash(page.row('Fuel').querySelector('[role="group"]'))).toBe('The candidates have: diesel cng, petrol');
    expect(findButton(page.row('Fuel'), 'cng, petrol')?.getAttribute('aria-label')).toBe('Use cng, petrol for Fuel');
    await page.click('Fuel', 'cng, petrol');
    expect(ticked()).toEqual(['petrol', 'cng']);
    await page.tick('Fuel', 'diesel');
    await page.click('Fuel', 'Save');

    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.body).toMatchObject({ field: 'fuel', action: 'set', value: 'petrol,diesel,cng' });
    request.flush(
      lookup({
        ...SETTLED,
        corrections: [correction({ field: 'fuel', value: 'petrol,diesel,cng', reviewer: 'Bea', previous_value: 'petrol,electricity' })],
        correctable_fields: more({ fuel: { current_value: 'petrol,diesel,cng', current_source: 'correction' } }),
      }),
      { status: 201, statusText: 'Created' },
    );
    await page.settle();
    expect(page.status('Fuel')).toBe(
      'Saved. Fuel is now petrol, diesel, cng. The car was matched again: one KType fits.',
    );
    expect(page.says('Fuel')).toBe(
      'petrol, diesel, cng corrected by Bea on 2026-10-01: petrol, electricity → petrol, diesel, cng',
    );
  });

  it('takes a whole number within the range its vocabulary gives, such as a month', async () => {
    const page = render({ lookup: lookup({ ...SETTLED, correctable_fields: more() }) });

    await page.click('Build month', 'Correct…');
    expect(squash(page.row('Build month').querySelector('form label'))).toBe('New value (1 to 12)');
    const month = page.control('Build month') as HTMLInputElement;
    expect([month.tagName, month.getAttribute('inputmode'), month.value]).toEqual(['INPUT', 'numeric', '3']);
    for (const wrong of ['13', '0', 'May']) {
      await page.enter('Build month', wrong);
      await page.click('Build month', 'Save');
      expect(squash(page.alert('Build month')), wrong).toBe('Enter a whole number from 1 to 12.');
    }
    page.http.expectNone(CORRECTION_URL);
    await page.enter('Build month', '11');
    await page.click('Build month', 'Save');
    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      field: 'production_month',
      action: 'set',
      value: '11',
    });

    TestBed.resetTestingModule();
    // Without a vocabulary the number is open and the server says what it accepts;
    // whole numbers with gaps between them are picked, not typed.
    const other = render({
      lookup: lookup({
        ...SETTLED,
        correctable_fields: more({ production_month: { values: [] }, fuel: { type: 'integer', values: ['2', '4', '5'], current_value: '4' } }),
      }),
    });
    await other.click('Build month', 'Correct…');
    expect(squash(other.row('Build month').querySelector('form label'))).toBe('New value');
    await other.click('Fuel', 'Correct…');
    const seats = other.control('Fuel') as HTMLSelectElement;
    expect([seats.tagName, seats.value]).toEqual(['SELECT', '4']);
  });

  it('closes the editor on Cancel, Escape or its own button, and gives focus back', async () => {
    const page = render();
    const opener = findButton(page.row('Power (kW)'), 'Correct…');

    await page.click('Power (kW)', 'Correct…');
    await page.enter('Power (kW)', '110');
    await page.click('Power (kW)', 'Cancel');
    expect(page.host.querySelector('form')).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(opener?.getAttribute('aria-expanded')).toBe('false');

    // What was typed is not kept: the editor opens on the car's value again.
    await page.click('Power (kW)', 'Correct…');
    expect(page.control('Power (kW)').value).toBe('120');
    page.row('Power (kW)').querySelector('form')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await page.settle();
    expect(page.host.querySelector('form')).toBeNull();

    await page.click('Power (kW)', 'Correct…');
    await page.click('Power (kW)', 'Correct…');
    expect(page.host.querySelector('form')).toBeNull();
    page.http.verify();
  });

  it('gives every button a name of its own that carries the field', async () => {
    const page = render({
      lookup: lookup({
        corrections: [correction(), correction({ field: 'power_kw', value: '120', previous_value: '110' })],
        correctable_fields: [
          ...fields({
            engine_code: { current_value: 'D5204T3', current_source: 'correction' },
            power_kw: { current_value: '120', current_source: 'correction', suggestions: ['100', '110'] },
          }),
          ...more(),
        ],
      }),
    });
    await page.correct('Power (kW)', '105');
    page.http.expectOne(CORRECTION_URL).error(new ProgressEvent('error'));
    await page.settle();

    const row = page.row('Power (kW)');
    expect([...row.querySelectorAll('button')].map(nameOf)).toEqual([
      'Correct Power (kW)',
      'Undo correction of Power (kW)',
      'Use 100 for Power (kW)',
      'Use 110 for Power (kW)',
      'Save Power (kW)',
      'Mark the present value as wrong: Power (kW)',
      'Cancel correcting Power (kW)',
      'Try again: Power (kW)',
    ]);
    // What is seen on a button is part of what it is called, so it can be asked for by voice.
    for (const button of row.querySelectorAll('button')) {
      expect(nameOf(button)).toContain(squash(button).replace('…', ''));
    }
    const names = [...page.host.querySelectorAll('button')].map(nameOf);
    expect(names.length).toBe(19);
    expect(new Set(names).size).toBe(names.length);
  });

  it('saves a value with the operation id, the head and the fingerprint, and hands on the lookup', async () => {
    const page = render({ reviewer: '  Bea ', reason: ' Registration papers ' });

    await page.correct('Engine code', ' D5204T3 ');

    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      field: 'engine_code',
      action: 'set',
      value: 'D5204T3',
      reviewer: 'Bea',
      reason: 'Registration papers',
      supersedes_correction_id: null,
      evidence_fingerprint: FINGERPRINT,
    });
    expect(page.status('Engine code')).toBe('Saving and matching the car again…');
    expect([...page.host.querySelectorAll('button')].every((item) => item.disabled)).toBe(true);

    const after = corrected();
    request.flush(after, { status: 201, statusText: 'Created' });
    await page.settle();

    expect(page.emitted).toEqual([after]);
    expect(page.status('Engine code')).toBe(
      'Saved. Engine code is now D5204T3. The car was matched again: one KType fits.',
    );
    expect(page.says('Engine code')).toBe(
      'D5204T3 corrected by Bea on 2026-10-01: — → D5204T3 conflicts with some candidates',
    );
    const row = page.row('Engine code');
    expect(row.querySelector('form')).toBeNull();
    expect(findButton(row, 'Undo correction')).toBeTruthy();
    // The editor is gone, so focus goes to the row's own button rather than nowhere.
    expect(document.activeElement).toBe(findButton(row, 'Correct…'));
    expect(localStorage.getItem('match-review-reviewer')).toBe('Bea');
    // The reason was for this correction: the next action starts without one.
    expect(page.fixture.componentInstance.reason()).toBe('');
    page.http.verify();
  });

  it('leaves focus where the person put it while the save was on its way', async () => {
    const page = render();
    const elsewhere = document.body.appendChild(document.createElement('input'));
    await page.correct('Engine code', 'D5204T3');
    elsewhere.focus();
    page.http.expectOne(CORRECTION_URL).flush(corrected(), { status: 201, statusText: 'Created' });
    await page.settle();

    expect(page.row('Engine code').querySelector('form')).toBeNull();
    expect(document.activeElement).toBe(elsewhere);
    elsewhere.remove();
  });

  it('keeps Save off until the value is new, and asks for digits in a number', async () => {
    const page = render();
    await page.click('Power (kW)', 'Correct…');
    const save = () => findButton(page.row('Power (kW)'), 'Save') as HTMLButtonElement;
    expect(save().disabled).toBe(true);

    for (const same of ['', '  ', ' 120 ']) {
      await page.enter('Power (kW)', same);
      expect(save().disabled, `"${same}"`).toBe(true);
    }

    await page.enter('Power (kW)', '11o');
    await page.click('Power (kW)', 'Save');
    expect(squash(page.alert('Power (kW)'))).toBe('Enter a whole number, using digits only.');
    page.http.expectNone(CORRECTION_URL);

    await page.enter('Power (kW)', '110');
    await page.click('Power (kW)', 'Save');
    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      field: 'power_kw',
      action: 'set',
      value: '110',
    });
    expect(page.alert('Power (kW)')).toBeNull();
  });

  it('marks the present value as wrong, so the matcher no longer uses it', async () => {
    const page = render();
    await page.click('Power (kW)', 'Correct…');
    await page.click('Power (kW)', 'Mark the present value as wrong');

    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      field: 'power_kw',
      action: 'ignore',
      value: null,
      reviewer: 'Bea',
      reason: null,
      supersedes_correction_id: null,
      evidence_fingerprint: FINGERPRINT,
    });
    request.flush(
      lookup({
        candidates: [candidate('000010064'), candidate('000011508'), candidate('000059385', { power_kw: 100 })],
        overlaid_fields: { power_kw: 'correction' },
        corrections: [
          correction({ field: 'power_kw', status: 'ignored', value: null, reviewer: 'Bea', previous_value: '120' }),
        ],
        correctable_fields: fields({ power_kw: { current_value: null, current_source: 'correction' } }),
      }),
      { status: 201, statusText: 'Created' },
    );
    await page.settle();

    expect(page.status('Power (kW)')).toBe(
      'Saved. Power (kW) is marked as wrong and no longer used. The car was matched again: 3 KTypes fit.',
    );
    expect(page.says('Power (kW)')).toBe('— marked wrong by Bea on 2026-10-01 (was 120)');
    expect(findButton(page.row('Power (kW)'), 'Undo correction')).toBeTruthy();
    // With no value in use there is nothing left to mark as wrong, only a value to give.
    await page.click('Power (kW)', 'Correct…');
    expect(page.control('Power (kW)').value).toBe('');
    expect(findButton(page.row('Power (kW)'), 'Mark the present value as wrong')).toBeUndefined();
  });

  it('shows a correction in force with who, when and why, and undoes it', async () => {
    const page = render({
      lookup: lookup({
        ...SETTLED,
        corrections: [correction({ reason: 'Registration papers' })],
        correctable_fields: fields({ engine_code: { current_value: 'D5204T3', current_source: 'correction' } }),
      }),
    });
    expect(page.says('Engine code')).toBe(
      'D5204T3 corrected by Anna on 2026-10-01: D5204T2 → D5204T3 — “Registration papers”',
    );

    await page.click('Engine code', 'Undo correction');

    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      field: 'engine_code',
      action: 'withdraw',
      value: null,
      reviewer: 'Bea',
      reason: null,
      supersedes_correction_id: HEAD,
      evidence_fingerprint: FINGERPRINT,
    });
    request.flush(
      lookup({
        ...SETTLED,
        bucket: 'not_matchable',
        candidates: [],
        corrections: [correction({ status: 'withdrawn', value: null, reviewer: 'Bea', previous_value: 'D5204T3' })],
        correctable_fields: fields({ engine_code: { current_value: 'D5204T2', current_source: 'registry' } }),
      }),
    );
    await page.settle();

    expect(page.status('Engine code')).toBe(
      "Correction undone. Engine code is back to the car's own data. The car was checked again and cannot be matched as it is.",
    );
    expect(page.says('Engine code')).toBe(
      'D5204T2 from the registry · an earlier correction was withdrawn by Bea on 2026-10-01',
    );
    const row = page.row('Engine code');
    expect(findButton(row, 'Undo correction')).toBeUndefined();
    expect(document.activeElement).toBe(findButton(row, 'Correct…'));
  });

  it('supersedes the correction the row shows, a withdrawn one too', async () => {
    const replaced = render({
      lookup: lookup({
        corrections: [correction()],
        correctable_fields: fields({ engine_code: { current_value: 'D5204T3', current_source: 'correction' } }),
      }),
    });
    await replaced.correct('Engine code', 'D5204T4');
    expect(replaced.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      action: 'set',
      value: 'D5204T4',
      supersedes_correction_id: HEAD,
    });

    TestBed.resetTestingModule();
    const withdrawn = render({
      lookup: lookup({ corrections: [correction({ status: 'withdrawn', value: null })] }),
    });
    expect(withdrawn.says('Engine code')).toBe(
      '— an earlier correction was withdrawn by Anna on 2026-10-01 candidates differ on this and the car has no value',
    );
    await withdrawn.correct('Engine code', 'D5204T3');
    expect(withdrawn.http.expectOne(CORRECTION_URL).request.body.supersedes_correction_id).toBe(HEAD);

    // Another field of the same car has its own chain.
    TestBed.resetTestingModule();
    const other = render({ lookup: lookup({ corrections: [correction()] }) });
    await other.correct('Power (kW)', '110');
    expect(other.http.expectOne(CORRECTION_URL).request.body.supersedes_correction_id).toBeNull();
  });

  it('needs a name before anything can be corrected', async () => {
    const page = render({
      reviewer: '  ',
      lookup: lookup({
        corrections: [correction()],
        correctable_fields: fields({ engine_code: { current_value: 'D5204T3', current_source: 'correction' } }),
      }),
    });

    const hint = page.host.querySelector('p[id]');
    expect(hint?.textContent).toBe("Enter your name to correct this car's data.");
    const buttons = [...page.host.querySelectorAll('button')];
    expect(buttons.map((item) => item.textContent?.trim())).toContain('Undo correction');
    expect(buttons.every((item) => item.disabled)).toBe(true);
    expect(buttons.every((item) => item.getAttribute('aria-describedby') === hint?.id)).toBe(true);

    page.fixture.componentRef.setInput('reviewer', 'Bea');
    await page.settle();

    expect(page.host.querySelector('p[id]')).toBeNull();
    expect(buttons.every((item) => !item.disabled && !item.hasAttribute('aria-describedby'))).toBe(true);
  });

  it('retries a failed save with the same operation id and body; a new action mints a new id', async () => {
    const page = render({ reason: 'Registration papers' });
    await page.correct('Power (kW)', '110');
    const first = page.http.expectOne(CORRECTION_URL);
    const body = { ...first.request.body };
    first.flush({ detail: { code: 'unavailable', message: 'Database unavailable.' } }, { status: 503, statusText: 'x' });
    await page.settle();
    expect(squash(page.alert('Power (kW)'))).toBe('Not saved. Try again. Try again');
    // The editor stays open on what was typed.
    expect(page.control('Power (kW)').value).toBe('110');

    await page.click('Power (kW)', 'Try again');
    const second = page.http.expectOne(CORRECTION_URL);
    expect(second.request.body).toEqual(body);
    second.error(new ProgressEvent('error'));
    await page.settle();
    expect(squash(page.alert('Power (kW)'))).toBe('Not saved. Try again. Try again');

    await page.click('Power (kW)', 'Save');
    const third = page.http.expectOne(CORRECTION_URL);
    expect(third.request.body.operation_id).not.toBe(body.operation_id);
    expect({ ...third.request.body, operation_id: body.operation_id }).toEqual(body);
    third.flush({ detail: { code: 'vehicle_busy', message: 'Lock timeout.' } }, { status: 503, statusText: 'x' });
    await page.settle();
    expect(squash(page.alert('Power (kW)'))).toBe(
      'Not saved. Someone else is changing this car right now. Try again. Try again',
    );

    // Nothing was recorded, so the reason is still there for the next attempt.
    expect(third.request.body.reason).toBe('Registration papers');
    expect(page.fixture.componentInstance.reason()).toBe('Registration papers');

    // Giving up drops the operation: nothing is left to resend.
    await page.click('Power (kW)', 'Cancel');
    expect(page.host.querySelector('[role="alert"]')).toBeNull();
    page.http.verify();
  });

  it.each([
    [409, 'correction_changed', 'Someone else corrected this value while you were looking.'],
    [409, 'evidence_changed', "This car's data or matching changed since you opened it."],
    [422, 'nothing_to_ignore', 'This car has no value here to mark as wrong.'],
    [422, 'nothing_to_withdraw', 'There is no correction to undo.'],
  ])('on %i %s says the screen was out of date and shows the car as it is now', async (status, code, message) => {
    const page = render({ reason: 'Registration papers' });
    await page.correct('Power (kW)', '110');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code, message: 'power_kw: server words' } }, { status, statusText: 'x' });
    await page.settle();

    const said = `${message} What is in force now is shown.`;
    expect(squash(page.alert('Power (kW)'))).toBe(said);
    expect(page.emitted).toEqual([]);
    const reload = page.http.expectOne((request) => request.url.endsWith('/v1/vehicles/matching/lookup'));
    expect(reload.request.params.get('vehicle_id')).toBe(VEHICLE_ID);
    const current = lookup({
      evidence_fingerprint: 'e'.repeat(64),
      corrections: [correction({ field: 'power_kw', value: '115', reviewer: 'Carl', previous_value: '120' })],
      correctable_fields: fields({ power_kw: { current_value: '115', current_source: 'correction' } }),
    });
    reload.flush(current);
    await page.settle();

    expect(page.emitted).toEqual([current]);
    expect(page.says('Power (kW)')).toContain('115 corrected by Carl on 2026-10-01: 120 → 115');
    // The message stays, and so does what was typed: saving again uses what is shown now.
    expect(squash(page.alert('Power (kW)'))).toBe(said);
    expect(page.control('Power (kW)').value).toBe('110');
    await page.click('Power (kW)', 'Save');
    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      value: '110',
      reason: 'Registration papers',
      supersedes_correction_id: HEAD,
      evidence_fingerprint: 'e'.repeat(64),
    });
  });

  it('does not claim to show the current car when it could not be read again', async () => {
    const page = render();
    await page.correct('Power (kW)', '110');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code: 'correction_changed', message: 'x' } }, { status: 409, statusText: 'x' });
    await page.settle();
    page.http
      .expectOne((request) => request.url.endsWith('/v1/vehicles/matching/lookup'))
      .flush({ detail: 'Matching is unavailable. Nothing was saved.' }, { status: 503, statusText: 'x' });
    await page.settle();

    expect(squash(page.alert('Power (kW)'))).toBe(
      'Someone else corrected this value while you were looking. The car could not be read again: close it and open it once more.',
    );
    expect(page.emitted).toEqual([]);
  });

  it.each([
    [422, 'value_unchanged', 'x', 'Not saved. That is the value this car already has.'],
    // Only the server knows what a field accepts, so its sentence is the one shown.
    [422, 'invalid_value', 'Power (kW) must be between 1 and 2000.', 'Not saved. Power (kW) must be between 1 and 2000.'],
    [422, 'invalid_value', null, 'Not saved. That is not a valid value for Power (kW).'],
    [422, 'field_not_correctable', "'power_kw' is not a field.", 'Not saved. Power (kW) cannot be corrected here.'],
    [404, 'vehicle_not_found', 'No such vehicle.', 'Not saved. This car is no longer in NorthStar.'],
    [409, 'operation_id_reused', 'x', 'Not saved. Please do it once more.'],
    [422, 'something_new', 'Server words.', 'Not saved. Server words.'],
  ])('on %i %s (%s) says why in plain words, without a retry', async (status, code, said, message) => {
    const page = render();
    await page.correct('Power (kW)', '110');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code, message: said } }, { status, statusText: 'x' });
    await page.settle();

    expect(squash(page.alert('Power (kW)'))).toBe(message);
    expect(page.alert('Power (kW)')?.querySelector('button')).toBeNull();
    expect(page.emitted).toEqual([]);
    page.http.verify();
  });

  it.each([
    // FastAPI's own check of the body answers with a list of what it rejected.
    [
      [{ type: 'value_error', loc: ['body', 'reviewer'], msg: 'Value error, reviewer must not contain control characters' }],
      'Not saved. Your name must not contain control characters.',
    ],
    [
      [{ type: 'string_too_long', loc: ['body', 'reason'], msg: 'String should have at most 4000 characters' }],
      'Not saved. The server did not accept the reason.',
    ],
    [
      [{ type: 'missing', loc: ['body', 'operation_id'], msg: 'Field required' }],
      'Not saved. The server did not accept this request.',
    ],
    [null, 'Not saved. The server did not accept this request.'],
  ])('reads a 422 whose detail is %j as a sentence', async (detail, message) => {
    const page = render();
    await page.correct('Power (kW)', '110');
    page.http.expectOne(CORRECTION_URL).flush({ detail }, { status: 422, statusText: 'x' });
    await page.settle();

    expect(squash(page.alert('Power (kW)'))).toBe(message);
    expect(page.alert('Power (kW)')?.querySelector('button')).toBeNull();
  });

  it('shows the car’s correction history when it is opened, the newest first', async () => {
    const page = render({
      lookup: lookup({
        corrections: [
          correction({ history_count: 2 }),
          correction({ field: 'power_kw', status: 'withdrawn', value: null, history_count: 2 }),
          correction({ field: 'bodywork_form', value: 'multi_purpose_vehicle', history_count: 1 }),
        ],
      }),
    });
    expect(squash(page.history().querySelector('summary'))).toBe('Correction history (5)');
    page.http.expectNone(CORRECTION_URL);

    await page.openHistory();
    expect(squash(page.history())).toContain('Loading…');
    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.method).toBe('GET');
    const history: FactCorrectionHistory = {
      vehicle_id: VEHICLE_ID,
      fields: [
        {
          field: 'bodywork_form',
          current_correction_id: 'c1',
          entries: [entry({ correction_id: 'c1', value: 'multi_purpose_vehicle', previous_value: 'estate', created_at: '2026-09-28T09:00:00Z' })],
        },
        {
          field: 'engine_code',
          current_correction_id: 'e2',
          entries: [
            entry({ correction_id: 'e2', reason: 'Registration papers', created_at: '2026-10-01T09:30:00Z' }),
            entry({ correction_id: 'e1', action: 'ignore', value: null, reviewer: 'Anna', previous_value: 'D5204T2', created_at: '2026-09-30T08:00:00Z' }),
          ],
        },
        // A field this car can no longer be corrected on still reads by a name, not a key.
        {
          field: 'first_registration',
          current_correction_id: 'f1',
          entries: [entry({ correction_id: 'f1', value: '2012', created_at: '2026-09-27T09:00:00Z' })],
        },
        {
          field: 'power_kw',
          current_correction_id: 'p2',
          entries: [
            entry({ correction_id: 'p2', action: 'withdraw', value: null, reviewer: 'Carl', previous_value: '110', created_at: '2026-10-01T10:00:00Z' }),
            entry({ correction_id: 'p1', value: '110', reviewer: 'Carl', previous_value: '120', created_at: '2026-09-29T09:00:00Z' }),
          ],
        },
      ],
    };
    request.flush(history);
    await page.settle();

    expect(page.lines()).toEqual([
      'Carl undid the correction of Power (kW)',
      'Bea set Engine code to D5204T3 (was —) — “Registration papers”',
      'Anna marked Engine code as wrong (was D5204T2)',
      'Carl set Power (kW) to 110 (was 120)',
      'Bea set Bodywork to multi purpose vehicle (was estate)',
      'Bea set first registration to 2012 (was —)',
    ]);
    expect(squash(page.history())).not.toContain('Loading…');
    expect(squash(page.history().querySelector('ol li'))).toMatch(/^2026-10-01 \d\d:\d\d · Carl/);
  });

  it('says so when the history cannot be loaded, and loads it on Try again', async () => {
    const page = render({ lookup: lookup({ corrections: [correction()] }) });
    await page.openHistory();
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code: 'unavailable', message: 'Nothing was saved; try again.' } }, { status: 503, statusText: 'x' });
    await page.settle();

    // A failed read is said as a failed read, never in the words of a failed save.
    const alert = page.history().querySelector('[role="alert"]');
    expect(squash(alert)).toBe('The correction history could not be loaded. Try again');
    expect(squash(page.history())).not.toContain('Loading…');
    const again = alert?.querySelector('button') as HTMLButtonElement;
    expect(nameOf(again)).toBe('Try again to load the correction history');

    again.click();
    await page.settle();
    expect(squash(page.history())).toContain('Loading…');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ vehicle_id: VEHICLE_ID, fields: [{ field: 'engine_code', current_correction_id: HEAD, entries: [entry({ previous_value: 'D5204T2' })] }] });
    await page.settle();
    expect(page.lines()).toEqual(['Bea set Engine code to D5204T3 (was D5204T2)']);
    expect(page.history().querySelector('[role="alert"]')).toBeNull();
  });

  it('reads an open history again after a save, and leaves a closed one alone', async () => {
    const page = render({ lookup: lookup({ corrections: [correction()] }) });
    const chain = (...entries: FactCorrectionHistoryEntry[]): FactCorrectionHistory => ({
      vehicle_id: VEHICLE_ID,
      fields: [{ field: 'engine_code', current_correction_id: entries[0].correction_id, entries }],
    });
    const first = entry({ previous_value: 'D5204T2', created_at: '2026-10-01T09:30:00Z' });
    await page.openHistory();
    page.http.expectOne(CORRECTION_URL).flush(chain(first));
    await page.settle();
    expect(page.lines()).toEqual(['Bea set Engine code to D5204T3 (was D5204T2)']);

    await page.click('Engine code', 'Undo correction');
    const requests = () => page.http.match(CORRECTION_URL);
    const [save] = requests();
    expect(save.request.method).toBe('POST');
    save.flush(lookup({ corrections: [correction({ status: 'withdrawn', value: null, history_count: 2 })] }));
    await page.settle();

    // The list on screen is the one before the save until the new one arrives: never "Loading…" for good.
    const [read] = requests();
    expect(read.request.method).toBe('GET');
    expect(page.lines()).toEqual(['Bea set Engine code to D5204T3 (was D5204T2)']);
    read.flush(
      chain(entry({ correction_id: 'u1', action: 'withdraw', value: null, previous_value: 'D5204T3', created_at: '2026-10-02T09:30:00Z' }), first),
    );
    await page.settle();
    expect(squash(page.history().querySelector('summary'))).toBe('Correction history (2)');
    expect(page.lines()).toEqual([
      'Bea undid the correction of Engine code',
      'Bea set Engine code to D5204T3 (was D5204T2)',
    ]);

    await page.openHistory(false);
    await page.correct('Engine code', 'D5204T4');
    const [again] = requests();
    expect(again.request.method).toBe('POST');
    again.flush(lookup({ corrections: [correction({ value: 'D5204T4', history_count: 3 })] }));
    await page.settle();
    page.http.verify();
  });

  it('shows nothing for a car with nothing to correct', () => {
    const none = render({ lookup: lookup({ correctable_fields: [] }) });
    expect(none.host.hidden).toBe(true);
    expect(none.host.textContent?.trim()).toBe('');

    TestBed.resetTestingModule();
    // A lookup from an API that does not send the fields yet.
    const older = render({ lookup: lookup({ correctable_fields: undefined, corrections: undefined }) });
    expect(older.host.hidden).toBe(true);
  });
});

const RELEASE = '33333333-3333-4333-8333-333333333333';
const WHY = 'tyre size could not be read, no manufacturer';

/** A car whose record was stopped before matching: no KType was compared with it. */
function stopped(overrides: Partial<VehicleMatchLookup> = {}): VehicleMatchLookup {
  return lookup({
    terminal: 'review_required',
    bucket: 'not_matchable',
    confidence: null,
    top_ktype: null,
    reason_codes: ['normalization_review_required'],
    verdict: null,
    candidates: [],
    separating_fields: [],
    missing_separating_fields: [],
    stop_reasons: ['tyre_size_unrecognized', 'manufacturer_missing'],
    ...overrides,
  });
}

/** The same car let through by a person: it is matched, and still says why it was stopped. */
function released(overrides: Partial<FactCorrectionState> = {}): VehicleMatchLookup {
  return lookup({
    stop_reasons: ['tyre_size_unrecognized', 'manufacturer_missing'],
    corrections: [
      correction({
        field: 'normalization_stop',
        status: 'ignored',
        correction_id: RELEASE,
        value: null,
        reviewer: 'Bea',
        reason: 'The tyre size plays no part',
        previous_value: null,
        previous_source: null,
        ...overrides,
      }),
    ],
  });
}

describe('FactCorrections: a car stopped before matching', () => {
  beforeEach(() => {
    TestBed.resetTestingModule();
    localStorage.clear();
  });

  const block = (page: ReturnType<typeof render>) => page.host.querySelector('.stop') as HTMLElement;
  const press = async (page: ReturnType<typeof render>, label: string) => {
    const found = findButton(block(page), label);
    expect(found, `button "${label}"`).toBeTruthy();
    found?.click();
    await page.settle();
  };

  it('says why the car was stopped, in plain words, above the rows', () => {
    const page = render({
      reason: 'x',
      lookup: stopped({
        stop_reasons: [
          'tyre_size_unrecognized',
          'type_approval_format_unrecognized',
          'manufacturer_missing',
          'normalization_review_required',
          'engine_code_missing',
        ],
      }),
    });

    expect(squash(block(page).querySelector('p'))).toBe(
      'This car was stopped before matching: tyre size could not be read, type approval number could not be read, no manufacturer, sent to review without a stated reason, engine code missing. It has not been compared with any KType.',
    );
    expect(findButton(block(page), 'Match this car anyway')?.disabled).toBe(false);
    const rows = page.host.querySelector('ul') as HTMLElement;
    expect(block(page).compareDocumentPosition(rows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(page.labels().length).toBe(8);

    TestBed.resetTestingModule();
    // A car that was never stopped says nothing about it, with or without the field.
    expect(render({ lookup: lookup({ stop_reasons: [] }) }).host.querySelector('.stop')).toBeNull();
    TestBed.resetTestingModule();
    expect(render().host.querySelector('.stop')).toBeNull();

    TestBed.resetTestingModule();
    // The release is offered even when the car has no field to correct.
    const bare = render({ reason: 'x', lookup: stopped({ correctable_fields: [] }) });
    expect(bare.host.hidden).toBe(false);
    expect(bare.host.querySelector('h4')?.textContent).toBe("Correct this car's data");
    expect(findButton(block(bare), 'Match this car anyway')).toBeTruthy();
    expect(bare.labels()).toEqual([]);
  });

  it('needs a reason as well as a name to match the car anyway', async () => {
    const page = render({ reviewer: '', reason: '  ', lookup: stopped() });
    const button = findButton(block(page), 'Match this car anyway') as HTMLButtonElement;
    const hints = () => (button.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean);
    const said = () => hints().map((id) => page.host.querySelector(`#${id}`)?.textContent);

    expect(button.disabled).toBe(true);
    expect(said()).toEqual([
      "Enter your name to correct this car's data.",
      'Give a reason to match this car anyway.',
    ]);

    page.fixture.componentRef.setInput('reviewer', 'Bea');
    await page.settle();
    expect(button.disabled).toBe(true);
    expect(said()).toEqual(['Give a reason to match this car anyway.']);

    page.fixture.componentRef.setInput('reason', 'The tyre size plays no part');
    await page.settle();
    expect(button.disabled).toBe(false);
    expect(button.hasAttribute('aria-describedby')).toBe(false);
    expect(squash(block(page))).not.toContain('Give a reason');
  });

  it('asks before releasing, sends nothing when cancelled, and releases on Confirm', async () => {
    const page = render({ reason: ' The tyre size plays no part ', lookup: stopped() });
    const button = findButton(block(page), 'Match this car anyway') as HTMLButtonElement;

    await press(page, 'Match this car anyway');
    const confirm = block(page).querySelector('[role="group"]') as HTMLElement;
    expect(confirm.getAttribute('aria-label')).toBe('Confirm matching this car anyway');
    expect(squash(confirm.querySelector('p'))).toBe(
      `Match this car even though it was stopped for: ${WHY}? The matcher's checks still apply.`,
    );
    expect([...confirm.querySelectorAll('button')].map(nameOf)).toEqual([
      'Confirm: match this car anyway',
      'Cancel: match this car anyway',
    ]);
    expect(document.activeElement).toBe(findButton(confirm, 'Confirm'));
    expect(button.getAttribute('aria-expanded')).toBe('true');

    await press(page, 'Cancel');
    expect(block(page).querySelector('[role="group"]')).toBeNull();
    expect(document.activeElement).toBe(button);
    page.http.expectNone(CORRECTION_URL);

    await press(page, 'Match this car anyway');
    await press(page, 'Confirm');
    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      field: 'normalization_stop',
      action: 'ignore',
      value: null,
      reviewer: 'Bea',
      reason: 'The tyre size plays no part',
      supersedes_correction_id: null,
      evidence_fingerprint: FINGERPRINT,
    });
    expect(block(page).querySelector('[role="group"]')).toBeNull();
    expect(squash(block(page).querySelector('[role="status"]'))).toBe('Saving and matching the car again…');

    const after = released();
    request.flush(after, { status: 201, statusText: 'Created' });
    await page.settle();

    expect(page.emitted).toEqual([after]);
    expect(squash(block(page).querySelector('p'))).toBe(
      `Released for matching by Bea on 2026-10-01: “The tyre size plays no part” (was stopped for: ${WHY})`,
    );
    expect(squash(block(page).querySelector('[role="status"]'))).toBe(
      'Released for matching. The car was matched again: 2 KTypes fit.',
    );
    expect(findButton(block(page), 'Match this car anyway')).toBeUndefined();
    expect(document.activeElement).toBe(findButton(block(page), 'Undo release'));
    expect(page.fixture.componentInstance.reason()).toBe('');
    // Matched now, the car's rows say what stands between it and one KType.
    expect(page.hint('Engine code')).toBe('candidates differ on this and the car has no value');
  });

  it('closes a release still waiting for Confirm when something else is recorded', async () => {
    const page = render({ reason: 'x', lookup: stopped() });
    await press(page, 'Match this car anyway');
    expect(block(page).querySelector('[role="group"]')).toBeTruthy();

    await page.correct('Power (kW)', '110');

    expect(page.http.expectOne(CORRECTION_URL).request.body.field).toBe('power_kw');
    expect(block(page).querySelector('[role="group"]')).toBeNull();
  });

  it('undoes a release, and the next one supersedes the undone one', async () => {
    const page = render({ reason: 'Stopped for a good reason after all', lookup: released() });

    await press(page, 'Undo release');
    const request = page.http.expectOne(CORRECTION_URL);
    expect(request.request.body).toEqual({
      operation_id: expect.stringMatching(UUID),
      field: 'normalization_stop',
      action: 'withdraw',
      value: null,
      reviewer: 'Bea',
      reason: 'Stopped for a good reason after all',
      supersedes_correction_id: RELEASE,
      evidence_fingerprint: FINGERPRINT,
    });
    const undone = stopped({ corrections: released({ status: 'withdrawn', reviewer: 'Carl', reason: null }).corrections });
    request.flush(undone, { status: 201, statusText: 'Created' });
    await page.settle();

    expect(squash(block(page))).toContain(`This car was stopped before matching: ${WHY}. It has not been compared with any KType.`);
    expect(squash(block(page))).toContain('An earlier release was undone by Carl on 2026-10-01.');
    expect(squash(block(page).querySelector('[role="status"]'))).toBe(
      'Release undone. The car was checked again and cannot be matched as it is.',
    );
    expect(findButton(block(page), 'Undo release')).toBeUndefined();
    // The reason went with the undo, so the button waits for a new one and cannot take focus:
    // focus goes to the words about the stop instead of nowhere.
    expect(findButton(block(page), 'Match this car anyway')?.disabled).toBe(true);
    expect(document.activeElement).toBe(block(page));
    expect(block(page).getAttribute('aria-label')).toBe('Stop before matching');

    page.fixture.componentRef.setInput('reason', 'Seen the car');
    await page.settle();
    await press(page, 'Match this car anyway');
    await press(page, 'Confirm');
    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      action: 'ignore',
      reason: 'Seen the car',
      supersedes_correction_id: RELEASE,
    });
  });

  it.each([
    [422, 'reason_required', 'Not saved. Give a reason to match this car anyway.', false],
    [422, 'nothing_to_ignore', 'This car is no longer stopped before matching. What is in force now is shown.', true],
    [409, 'correction_changed', "Someone else changed this car's release while you were looking. What is in force now is shown.", true],
    [422, 'nothing_to_withdraw', 'There is no release to undo. What is in force now is shown.', true],
    [409, 'evidence_changed', "This car's data or matching changed since you opened it. What is in force now is shown.", true],
  ])('on %i %s says what happened to the release', async (status, code, message, reloads) => {
    const page = render({ reason: 'x', lookup: stopped() });
    await press(page, 'Match this car anyway');
    await press(page, 'Confirm');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code, message: 'server words' } }, { status, statusText: 'x' });
    await page.settle();

    const alert = block(page).querySelector('[role="alert"]');
    expect(squash(alert)).toBe(message);
    expect(alert?.querySelector('button')).toBeNull();
    const reload = page.http.match((request) => request.url.endsWith('/v1/vehicles/matching/lookup'));
    expect(reload.length).toBe(reloads ? 1 : 0);
  });

  it('retries a failed release with the same operation id and body', async () => {
    const page = render({ reason: 'x', lookup: stopped() });
    await press(page, 'Match this car anyway');
    await press(page, 'Confirm');
    const first = page.http.expectOne(CORRECTION_URL);
    const body = { ...first.request.body };
    first.flush({ detail: { code: 'unavailable', message: 'x' } }, { status: 503, statusText: 'x' });
    await page.settle();

    const again = block(page).querySelector('[role="alert"] button') as HTMLButtonElement;
    expect(squash(block(page).querySelector('[role="alert"]'))).toBe('Not saved. Try again. Try again');
    expect(nameOf(again)).toBe('Try again: the release');
    again.click();
    await page.settle();
    expect(page.http.expectOne(CORRECTION_URL).request.body).toEqual(body);
  });

  it('reads a release and its undoing in the history', async () => {
    const page = render({ lookup: released({ history_count: 3 }) });
    expect(squash(page.history().querySelector('summary'))).toBe('Correction history (3)');
    await page.openHistory();
    page.http.expectOne(CORRECTION_URL).flush({
      vehicle_id: VEHICLE_ID,
      fields: [
        {
          field: 'normalization_stop',
          current_correction_id: 'r3',
          entries: [
            entry({ correction_id: 'r3', action: 'ignore', value: null, reason: 'The tyre size plays no part', created_at: '2026-10-01T09:30:00Z' }),
            entry({ correction_id: 'r2', action: 'withdraw', value: null, reviewer: 'Carl', created_at: '2026-09-30T09:30:00Z' }),
            entry({ correction_id: 'r1', action: 'ignore', value: null, reviewer: 'Anna', reason: 'Seen the car', created_at: '2026-09-29T09:30:00Z' }),
          ],
        },
      ],
    });
    await page.settle();

    expect(page.lines()).toEqual([
      'Bea released the car for matching — “The tyre size plays no part”',
      'Carl undid the release',
      'Anna released the car for matching — “Seen the car”',
    ]);
  });
});

/** The candidates panel with its lookup answered, and a reviewer name remembered. */
async function open(result: VehicleMatchLookup, reviewer = 'Bea') {
  TestBed.resetTestingModule();
  localStorage.clear();
  if (reviewer) localStorage.setItem('match-review-reviewer', reviewer);
  TestBed.configureTestingModule({ providers: PROVIDERS });
  const fixture = TestBed.createComponent(KTypeCandidates);
  fixture.componentRef.setInput('vehicleId', VEHICLE_ID);
  const changed: VehicleMatchLookup[] = [];
  fixture.componentInstance.choiceChanged.subscribe((value) => changed.push(value));
  const host = fixture.nativeElement as HTMLElement;
  const http = TestBed.inject(HttpTestingController);
  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
  };
  await settle();
  http.expectOne((request) => request.url.endsWith('/v1/vehicles/matching/lookup')).flush(result);
  await settle();
  const panel = () => host.querySelector('ns-fact-corrections') as HTMLElement | null;
  const row = (label: string) =>
    [...host.querySelectorAll('ns-fact-corrections ul > li')].find(
      (item) => squash(item.querySelector('.row > span')) === label,
    ) as HTMLLIElement;
  const click = async (within: Element, label: string) => {
    const found = findButton(within, label);
    expect(found, `button "${label}"`).toBeTruthy();
    found?.click();
    await settle();
  };
  const enter = async (field: HTMLInputElement | HTMLTextAreaElement, value: string) => {
    field.value = value;
    field.dispatchEvent(new Event('input'));
    await settle();
  };
  return { fixture, host, http, changed, settle, panel, row, click, enter };
}

describe('KTypeCandidates: correcting the car’s data', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('offers the corrections above the choice and the candidates, for a vehicle that has them', async () => {
    const page = await open(lookup());
    const panel = page.panel() as HTMLElement;
    const before = (other: Element | null) =>
      !!other && !!(panel.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(panel.hidden).toBe(false);
    expect(panel.querySelector('h4')?.textContent).toBe("Correct this car's data");
    expect(before(page.host.querySelector('.gap'))).toBe(false);
    expect(before(page.host.querySelector('ns-ktype-choice'))).toBe(true);
    expect(before(page.host.querySelector('.candidate'))).toBe(true);

    // A lookup that is not a vehicle takes neither a choice nor a correction.
    const record = await open(lookup({ vehicle_id: null }));
    expect(record.panel()).toBeNull();

    const bare = await open(lookup({ correctable_fields: [] }));
    expect(bare.panel()?.hidden).toBe(true);
    expect(bare.host.querySelector('ns-ktype-choice')).toBeTruthy();
  });

  it('takes the name and reason from the choice’s fields, and unlocks with the name', async () => {
    const page = await open(lookup(), '');
    const opener = findButton(page.row('Engine code'), 'Correct…') as HTMLButtonElement;
    expect(opener.disabled).toBe(true);
    expect(squash(page.panel())).toContain("Enter your name to correct this car's data.");

    await page.enter(page.host.querySelector('ns-ktype-choice input') as HTMLInputElement, 'Dan');
    await page.enter(page.host.querySelector('ns-ktype-choice textarea') as HTMLTextAreaElement, 'Seen the car');
    expect(opener.disabled).toBe(false);
    expect(squash(page.panel())).not.toContain('Enter your name');

    await page.click(page.row('Engine code'), 'Correct…');
    await page.enter(page.row('Engine code').querySelector('form input') as HTMLInputElement, 'D5204T3');
    await page.click(page.row('Engine code'), 'Save');

    expect(page.http.expectOne(CORRECTION_URL).request.body).toMatchObject({
      reviewer: 'Dan',
      reason: 'Seen the car',
      evidence_fingerprint: FINGERPRINT,
    });
  });

  it('shows the car as matched again after a correction, and lets the record reload', async () => {
    const page = await open(lookup());
    expect(squash(page.host.querySelector('.bucket'))).toBe('Several KTypes');

    const reason = page.host.querySelector('ns-ktype-choice textarea') as HTMLTextAreaElement;
    await page.enter(reason, 'Registration papers');
    await page.click(page.row('Engine code'), 'Correct…');
    await page.enter(page.row('Engine code').querySelector('form input') as HTMLInputElement, 'D5204T3');
    await page.click(page.row('Engine code'), 'Save');
    const after = corrected();
    page.http.expectOne(CORRECTION_URL).flush(after, { status: 201, statusText: 'Created' });
    await page.settle();

    // As after a choice: the reason was for what was just recorded.
    expect(reason.value).toBe('');
    expect(squash(page.host.querySelector('.bucket'))).toBe('One KType');
    expect(page.host.querySelector('.gap')).toBeNull();
    expect(page.host.querySelectorAll('.candidate--out').length).toBe(2);
    expect(page.changed).toEqual([after]);
    // The panel is the same one, so what it says about the save is still there.
    expect(squash(page.row('Engine code').querySelector('[role="status"]'))).toBe(
      'Saved. Engine code is now D5204T3. The car was matched again: one KType fits.',
    );
    page.http.verify();
  });

  it('shows the current car when a correction is refused as out of date', async () => {
    const page = await open(lookup());
    const reason = page.host.querySelector('ns-ktype-choice textarea') as HTMLTextAreaElement;
    await page.enter(reason, 'Registration papers');
    await page.click(page.row('Power (kW)'), 'Correct…');
    await page.click(page.row('Power (kW)'), 'Mark the present value as wrong');
    page.http
      .expectOne(CORRECTION_URL)
      .flush({ detail: { code: 'evidence_changed', message: 'x' } }, { status: 409, statusText: 'x' });
    await page.settle();

    const current = corrected('D5204T3', 'Carl');
    page.http.expectOne((request) => request.url.endsWith('/v1/vehicles/matching/lookup')).flush(current);
    await page.settle();

    expect(squash(page.host.querySelector('.bucket'))).toBe('One KType');
    expect(squash(page.row('Engine code'))).toContain('corrected by Carl');
    expect(squash(page.row('Power (kW)').querySelector('[role="alert"]'))).toBe(
      "This car's data or matching changed since you opened it. What is in force now is shown.",
    );
    expect(page.changed).toEqual([current]);
    // Nothing was recorded: the reason stays for the next attempt.
    expect(reason.value).toBe('Registration papers');
  });

  it('drops a choice still waiting for Confirm once the car’s data is corrected', async () => {
    const page = await open(lookup());
    await page.click(page.host, 'Choose anyway…');
    expect(squash(page.host.querySelector('.confirm'))).toContain('conflicts with this car on power');

    await page.click(page.row('Power (kW)'), 'Correct…');
    await page.enter(page.row('Power (kW)').querySelector('form input') as HTMLInputElement, '100');
    await page.click(page.row('Power (kW)'), 'Save');
    page.http.expectOne(CORRECTION_URL).flush(corrected(), { status: 201, statusText: 'Created' });
    await page.settle();

    // It was asked about the matching as it was; confirming it now would only be refused.
    expect(page.host.querySelector('.confirm')).toBeNull();
    page.http.verify();
  });

  it('says a corrected value was corrected by a person in what the matcher saw', async () => {
    const inputs = {
      manufacturer: 'VOLVO',
      model_values: ['V70'],
      production_year: 2012,
      fuels: ['diesel'],
      engine_code: 'D5204T3',
      displacement_cc: 1984,
      power_kw: 120,
      drive_type: null,
      bodywork_form: 'estate',
      model_recovered_from: null,
    };
    const page = await open(lookup({ inputs, overlaid_fields: { displacement_cc: 'ais', engine_code: 'correction' } }));

    expect(squash(page.host.querySelector('.note'))).toBe(
      "Underlined: the vehicle record's value, not the TS derivation — displacement cc from AIS, engine code corrected by a person.",
    );
    expect(page.host.querySelector('.ruled')?.textContent?.trim()).toBe('D5204T3');
  });
});
