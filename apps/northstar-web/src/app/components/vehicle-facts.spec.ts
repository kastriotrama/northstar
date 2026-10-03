/**
 * A car's own information as the Matched cars dialog shows it: who the car is, its values
 * in their groups, and where each value came from.
 */

import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import type { NorVehicleRecord, ValueSource, VehicleFieldValue } from '../core/models';
import { VehicleFacts } from './vehicle-facts';

function source(name: string, overrides: Partial<ValueSource> = {}): ValueSource {
  return { source: name, ref: null, observed_on: null, origin: false, ...overrides };
}

function field(
  name: string,
  label: string,
  group: string,
  value: unknown,
  overrides: Partial<VehicleFieldValue> = {},
): VehicleFieldValue {
  return {
    field: name,
    label,
    group,
    value,
    source: source('transportstyrelsen', { origin: true }),
    alternatives: [],
    ...overrides,
  };
}

function record(overrides: Partial<NorVehicleRecord> = {}): NorVehicleRecord {
  return {
    vehicle_id: 'NOR-TEST',
    origin_source: 'transportstyrelsen',
    origin_observed_on: '2026-08-07',
    ts_record_id: 7,
    registry_status: 'registered',
    created_at: '2026-09-01T08:00:00Z',
    updated_at: '2026-10-01T09:30:00Z',
    fields: [
      field('colour', 'Colour', 'identity', 'blue'),
      field('manufacturer', 'Manufacturer', 'make', 'Volvo'),
      field('model_family', 'Model family', 'make', 'V70', {
        source: source('rule', { ref: 'MOD-VV-1a2b3c' }),
      }),
      field('registry_model_text', 'Registry model text', 'make', null),
      field('engine_code', 'Engine code', 'technical', 'B4204T', {
        source: source('ais', { observed_on: '2026-09-19' }),
        alternatives: [{ value: 'B4204', source: source('rule') }],
      }),
      field('power_kw', 'Power (kW)', 'technical', 180, {
        source: source('correction', { ref: 'c0ffee' }),
        alternatives: [{ value: 187, source: source('transportstyrelsen') }],
      }),
      field('fuel_match_tokens', 'Fuel match tokens', 'technical', ['petrol', 'electricity']),
      field('production_year', 'Production year', 'dates', 2014),
      field('kerb_weight_kg', 'Kerb weight (kg)', 'physical', 1650),
      field('ktype', 'KType', 'match', '000010064', { source: source('review', { ref: 'choice-1' }) }),
      field('match_state', 'Match state', 'match', 'manual', { source: source('review') }),
      field('normalization_status', 'Normalization status', 'normalization', 'resolved'),
    ],
    identifiers: [
      { kind: 'plate', value: 'ABC123', valid_from: null, valid_to: null, current: true, source: 'transportstyrelsen', source_ref: null },
      { kind: 'vin', value: 'YV1TESTVIN0000001', valid_from: null, valid_to: null, current: true, source: 'transportstyrelsen', source_ref: null },
      { kind: 'vin', value: 'YV1OLDVIN00000000', valid_from: null, valid_to: '2020-01-01', current: false, source: 'ais', source_ref: null },
    ],
    source_links: [],
    source_link_count: 1,
    rules: [],
    ...overrides,
  };
}

function render(car: NorVehicleRecord = record()) {
  const fixture = TestBed.createComponent(VehicleFacts);
  fixture.componentRef.setInput('record', car);
  fixture.detectChanges();
  return fixture;
}

/** The element's words, with a space where one piece of text ends and the next begins. */
function text(element: Element): string {
  const parts: string[] = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const piece = (walker.currentNode.textContent ?? '').replace(/\s+/g, ' ').trim();
    if (piece) parts.push(piece);
  }
  return parts.join(' ');
}

/** The group's `<details>`, found by the words of its summary. */
function group(host: HTMLElement, label: string): HTMLDetailsElement {
  const found = [...host.querySelectorAll<HTMLDetailsElement>('details')].find((item) =>
    text(item.querySelector('summary') as Element).startsWith(label),
  );
  if (!found) throw new Error(`no group "${label}"`);
  return found;
}

describe('VehicleFacts', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('says who the car is: its id, status, origin and the VIN it carries today', () => {
    const host = render().nativeElement as HTMLElement;
    const ids = text(host.querySelector('.ids') as Element);

    expect(ids).toContain('NOR ID NOR-TEST');
    expect(ids).toContain('Status In the register');
    expect(ids).toContain('Created from TS · 2026-08-07');
    expect(ids).toContain('VIN YV1TESTVIN0000001');
    // A VIN the car no longer carries and its plates are not repeated here.
    expect(ids).not.toContain('YV1OLDVIN00000000');
    expect(ids).not.toContain('ABC123');
  });

  it('names a deregistered car and an AIS origin in the same short words as the Vehicles tab', () => {
    const host = render(record({ registry_status: 'deregistered', origin_source: 'ais', origin_observed_on: null }))
      .nativeElement as HTMLElement;
    const ids = text(host.querySelector('.ids') as Element);

    expect(ids).toContain('Status Deregistered');
    expect(ids).toContain('Created from AIS');
  });

  it('lists the values in their groups, in the record’s own order, counting what is unknown', () => {
    const host = render().nativeElement as HTMLElement;
    const summaries = [...host.querySelectorAll('details > summary')].map(text);

    expect(summaries).toEqual([
      'Identity and status 1 set',
      'Make and model 2 set · 1 unknown',
      'Technical 3 set',
      'Dates 1 set',
      'Physical 1 set',
      'TecDoc match 2 set',
      'Normalization 1 set',
    ]);
    expect(text(group(host, 'Make and model'))).toContain('Manufacturer Volvo');
    expect(text(group(host, 'Technical'))).toContain('Fuel match tokens petrol, electricity');
    // A field without a value is counted, not listed.
    expect(text(group(host, 'Make and model'))).not.toContain('Registry model text');
  });

  it('opens what a reader looks at first and folds the rest', () => {
    const host = render().nativeElement as HTMLElement;
    const open = [...host.querySelectorAll<HTMLDetailsElement>('details')]
      .filter((item) => item.open)
      .map((item) => text(item.querySelector('summary') as Element).replace(/ \d+ set.*/, ''));

    expect(open).toEqual(['Make and model', 'Technical', 'Dates']);
  });

  it('names the source that won, and nothing for the record that created the car', () => {
    const host = render().nativeElement as HTMLElement;
    const badges = (label: string) =>
      [...group(host, label).querySelectorAll('.field')].map((item) => [
        text(item.querySelector('dt') as Element),
        text(item.querySelector('.source') ?? document.createElement('i')),
      ]);

    expect(badges('Make and model')).toEqual([
      ['Manufacturer', ''],
      ['Model family', 'Learned rule'],
    ]);
    expect(badges('Technical')).toEqual([
      ['Engine code', 'AIS'],
      ['Power (kW)', "A person's correction"],
      ['Fuel match tokens', ''],
    ]);
    // The learned rule's id and an observation date are kept as the badge's tooltip.
    const model = group(host, 'Make and model').querySelector<HTMLElement>('.source');
    expect(model?.title).toBe('MOD-VV-1a2b3c');
    expect(group(host, 'Technical').querySelector<HTMLElement>('.source')?.title).toBe('2026-09-19');
  });

  it('shows what lost to the value in force', () => {
    const technical = text(group(render().nativeElement as HTMLElement, 'Technical'));

    expect(technical).toContain('Engine code B4204T AIS Learned rule said B4204');
    expect(technical).toContain("Power (kW) 180 A person's correction TS said 187");
  });

  it('reads a KType chosen by a person in words, not as a stored code or a reviewer rule', () => {
    const match = text(group(render().nativeElement as HTMLElement, 'TecDoc match'));

    expect(match).toContain("KType 000010064 A person's choice");
    expect(match).toContain("Match state KType chosen by a person A person's choice");
  });
});
