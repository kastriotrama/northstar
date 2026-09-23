import type { RuleCondition } from './models';

/**
 * The Vehicle type filter, shared by every screen that offers it.
 *
 * `vehicle_scope` is projected from the pipeline's own exclusion decisions
 * (`record_route`, `parts_matching_exclusion_reason`) plus non-M1 EU categories --
 * never from TecDoc's `is_pc`, which marks every model series, motorcycles
 * included, as a passenger car.
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
  | 'other_category';

export const EXCLUDED_SCOPES: readonly string[] = [
  'motorhome',
  'special_modified',
  'test_record',
  'other_category',
];

export const VEHICLE_TYPES: ReadonlyArray<{ value: VehicleType; label: string }> = [
  { value: 'passenger', label: 'Passenger cars' },
  { value: 'all', label: 'All vehicles' },
  { value: 'motorhome', label: 'Motorhomes' },
  { value: 'special_modified', label: 'Special / modified' },
  { value: 'test_record', label: 'Test records' },
  { value: 'other_category', label: 'Trucks, trailers, buses' },
];

/**
 * The predicate for one choice, or null for "all".
 *
 * "Passenger cars" is "not a known non-passenger category" rather than
 * "= passenger": a row the backfill has not reached yet holds NULL and must still
 * show, or a fresh deploy would present an empty screen until the backfill finished.
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
  return { field: 'vehicle_scope', layer: 'normalized', operator: 'equals', values: [type] };
}

/**
 * Option text with its car count, when the column has been computed.
 *
 * "All vehicles" carries no count: summing the scopes misses every row a backfill
 * has not reached yet (live showed 2.7M against 6.5M matched mid-backfill), and the
 * screen's own matched count already states the real total.
 */
export function vehicleTypeLabel(
  option: { value: VehicleType; label: string },
  counts: Record<string, number>,
): string {
  if (option.value === 'all') return option.label;
  const count = counts[option.value];
  return count === undefined ? option.label : `${option.label} (${count.toLocaleString()})`;
}

/** Facet values of `vehicle_scope` as a lookup of counts. */
export function vehicleScopeCounts(
  values: ReadonlyArray<{ value: string; count: number | null }>,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const { value, count } of values) counts[value] = count ?? 0;
  return counts;
}
