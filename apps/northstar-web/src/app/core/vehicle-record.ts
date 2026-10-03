import type { NorVehicleRecord, ValueSource, VehicleFieldValue } from './models';

/**
 * How one vehicle's record reads: its fields in their groups, values as plain text, and
 * where each value came from. Pure, so the Vehicles record panel and the Matched cars
 * dialog say the same thing in the same words.
 */

export const GROUP_LABELS: Record<string, string> = {
  identity: 'Identity and status',
  make: 'Make and model',
  technical: 'Technical',
  dates: 'Dates',
  physical: 'Physical',
  match: 'TecDoc match',
  normalization: 'Normalization',
};

export const SOURCE_LABELS: Record<string, string> = {
  transportstyrelsen: 'TS',
  ais: 'AIS',
  review: 'Review',
  rule: 'Learned rule',
  derived: 'Derived',
  correction: "A person's correction",
};

/** What the stored match state means, in the words the matching panel uses. */
export const MATCH_STATE_LABELS: Record<string, string> = {
  manual: 'KType chosen by a person',
  manual_none: '“None of these”, decided by a person',
};

/** The two fields a person's KType choice writes; their "review" source is that choice. */
const CHOICE_FIELDS: readonly string[] = ['ktype', 'match_state'];

/** One group of a record: the fields that have a value, and how many do not. */
export interface VehicleFieldGroup {
  group: string;
  label: string;
  filled: VehicleFieldValue[];
  empty: number;
}

export function isEmptyValue(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    value === '' ||
    (Array.isArray(value) && value.length === 0)
  );
}

/** Values are arbitrary JSON; show them as plain text. */
export function showValue(value: unknown): string {
  if (isEmptyValue(value)) return '—';
  if (Array.isArray(value)) return value.map((item) => showValue(item)).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** A field's value in words where the stored code would not be understood. */
export function showFieldValue(field: VehicleFieldValue): string {
  if (field.field === 'match_state' && typeof field.value === 'string') {
    return MATCH_STATE_LABELS[field.value] ?? field.value;
  }
  return showValue(field.value);
}

export function sourceLabel(source: ValueSource): string {
  return SOURCE_LABELS[source.source] ?? source.source;
}

/** A field's source badge: a person's KType choice is not a reviewer's rule. */
export function fieldSourceLabel(field: VehicleFieldValue): string {
  if (!field.source) return '';
  if (field.source.source === 'review' && CHOICE_FIELDS.includes(field.field)) {
    return "A person's choice";
  }
  return sourceLabel(field.source);
}

/** The source's own reference when it says something a reader can use. */
export function sourceDetail(source: ValueSource): string {
  const parts: string[] = [];
  if (source.source === 'rule' && source.ref) parts.push(source.ref);
  if (source.observed_on) parts.push(source.observed_on);
  return parts.join(' · ');
}

/** Where the record that created the vehicle came from, as the screens abbreviate it. */
export function originLabel(source: string): string {
  return source === 'transportstyrelsen' ? 'TS' : source.toUpperCase();
}

/** A record's fields in their groups, in the order the record lists them. */
export function fieldGroups(record: NorVehicleRecord | null): VehicleFieldGroup[] {
  if (!record) return [];
  const order: string[] = [];
  const byGroup = new Map<string, VehicleFieldValue[]>();
  for (const field of record.fields) {
    if (!byGroup.has(field.group)) {
      byGroup.set(field.group, []);
      order.push(field.group);
    }
    byGroup.get(field.group)?.push(field);
  }
  return order.map((group) => {
    const fields = byGroup.get(group) ?? [];
    return {
      group,
      label: GROUP_LABELS[group] ?? group,
      filled: fields.filter((field) => !isEmptyValue(field.value)),
      empty: fields.filter((field) => isEmptyValue(field.value)).length,
    };
  });
}
