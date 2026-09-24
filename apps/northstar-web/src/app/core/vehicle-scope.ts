import type { RuleCondition } from './models';

/**
 * The Vehicle type filter, shared by every screen that offers it.
 *
 * `vehicle_scope` is normalization's own classification
 * (`normalization_rules.classify_vehicle_scope`), copied into the projection:
 * its exclusion decisions first (motorhome, special/modified, test record), then
 * the registry's EU category or, where it recorded none, its vehicle type. Never
 * TecDoc's `is_pc`, which marks every model series, motorcycles included, as a car.
 *
 * It narrows what a screen *shows*. It is deliberately never part of a rule's
 * conditions: the rule endpoints reject it, and a rule exported to an environment
 * whose projection has not been backfilled would silently match nothing.
 */
export type VehicleType =
  | 'passenger'
  | 'all'
  | 'motorhome'
  | 'special_modified'
  | 'test_record'
  | 'other_vehicles'
  | 'unknown';

/**
 * Scopes that are known not to be passenger cars. `other_category` is the value an
 * earlier projection wrote for goods/trailer/bus/other; kept until the scope
 * backfill has replaced it everywhere. `unknown` is deliberately absent: a vehicle
 * the registry did not classify stays in the passenger view rather than vanishing.
 */
export const EXCLUDED_SCOPES: readonly string[] = [
  'motorhome',
  'special_modified',
  'test_record',
  'goods',
  'trailer',
  'bus',
  'other',
  'other_category',
];

export const VEHICLE_TYPES: ReadonlyArray<{
  value: VehicleType;
  label: string;
  /** The `vehicle_scope` values this choice selects; empty for passenger and all. */
  scopes: readonly string[];
}> = [
  { value: 'passenger', label: 'Passenger cars', scopes: [] },
  { value: 'all', label: 'All vehicles', scopes: [] },
  { value: 'motorhome', label: 'Motorhomes', scopes: ['motorhome'] },
  { value: 'special_modified', label: 'Special / modified', scopes: ['special_modified'] },
  { value: 'test_record', label: 'Test records', scopes: ['test_record'] },
  {
    value: 'other_vehicles',
    label: 'Other vehicles (motorcycles, trucks, trailers…)',
    scopes: ['goods', 'trailer', 'bus', 'other', 'other_category'],
  },
  { value: 'unknown', label: 'Type not recorded', scopes: ['unknown'] },
];

/**
 * The predicate for one choice, or null for "all".
 *
 * "Passenger cars" is "not a known non-passenger scope" rather than
 * "= passenger": a row the backfill has not reached yet holds NULL, and a row the
 * registry did not classify holds `unknown`; both must still show.
 */
export function vehicleScopeCondition(type: VehicleType): RuleCondition | null {
  if (type === 'all') return null;
  if (type === 'passenger') {
    return {
      field: 'vehicle_scope',
      layer: 'normalized',
      operator: 'not_equals',
      values: [...EXCLUDED_SCOPES],
    };
  }
  const option = VEHICLE_TYPES.find((entry) => entry.value === type);
  return {
    field: 'vehicle_scope',
    layer: 'normalized',
    operator: 'equals',
    values: [...(option?.scopes ?? [type])],
  };
}

/**
 * Option text with its car count, when the column has been computed.
 *
 * "All vehicles" carries no count: summing the scopes misses every row a backfill
 * has not reached yet (live showed 2.7M against 6.5M matched mid-backfill), and the
 * screen's own matched count already states the real total.
 */
export function vehicleTypeLabel(
  option: { value: VehicleType; label: string; scopes?: readonly string[] },
  counts: Record<string, number>,
): string {
  if (option.value === 'all') return option.label;
  const scopes = option.value === 'passenger' ? ['passenger'] : (option.scopes ?? [option.value]);
  const present = scopes.filter((scope) => counts[scope] !== undefined);
  if (!present.length) return option.label;
  const count = present.reduce((sum, scope) => sum + counts[scope], 0);
  return `${option.label} (${count.toLocaleString()})`;
}

/** Facet values of `vehicle_scope` as a lookup of counts. */
export function vehicleScopeCounts(
  values: ReadonlyArray<{ value: string; count: number | null }>,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const { value, count } of values) counts[value] = count ?? 0;
  return counts;
}
