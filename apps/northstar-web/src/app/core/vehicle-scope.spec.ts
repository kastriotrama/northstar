import { describe, expect, it } from 'vitest';

import { vehicleScopeCondition, vehicleScopeCounts, vehicleTypeLabel } from './vehicle-scope';

describe('vehicle scope', () => {
  it('passenger keeps rows the backfill has not reached', () => {
    // not_equals compiles to `IS NULL OR NOT IN`, so NULL rows still show.
    expect(vehicleScopeCondition('passenger')).toEqual({
      field: 'vehicle_scope',
      layer: 'normalized',
      operator: 'not_equals',
      values: ['motorhome', 'special_modified', 'test_record', 'goods', 'trailer', 'bus', 'other', 'other_category'],
    });
  });

  it('all vehicles applies no condition', () => {
    expect(vehicleScopeCondition('all')).toBeNull();
  });

  it('one category is an exact match', () => {
    expect(vehicleScopeCondition('motorhome')).toEqual({
      field: 'vehicle_scope',
      layer: 'normalized',
      operator: 'equals',
      values: ['motorhome'],
    });
  });

  it('all vehicles never shows a partial sum of the scopes', () => {
    const counts = vehicleScopeCounts([
      { value: 'passenger', count: 2_700_000 },
      { value: 'motorhome', count: 42_000 },
    ]);
    expect(vehicleTypeLabel({ value: 'all', label: 'All vehicles' }, counts)).toBe('All vehicles');
  });

  it('other vehicles selects every non-passenger registry type, old value included', () => {
    expect(vehicleScopeCondition('other_vehicles')?.values).toEqual([
      'goods',
      'trailer',
      'bus',
      'other',
      'other_category',
    ]);
  });

  it('a vehicle whose type was never recorded stays in the passenger view', () => {
    expect(vehicleScopeCondition('passenger')?.values).not.toContain('unknown');
    expect(vehicleScopeCondition('unknown')?.values).toEqual(['unknown']);
  });

  it('a grouped option counts every scope it selects', () => {
    const counts = vehicleScopeCounts([
      { value: 'other', count: 30 },
      { value: 'goods', count: 15 },
    ]);
    expect(
      vehicleTypeLabel(
        { value: 'other_vehicles', label: 'Other', scopes: ['goods', 'trailer', 'other'] },
        counts,
      ),
    ).toBe(`Other (${(45).toLocaleString()})`);
  });

  it('labels carry counts only once the column is computed', () => {
    const option = { value: 'motorhome' as const, label: 'Motorhomes' };
    expect(vehicleTypeLabel(option, {})).toBe('Motorhomes');
    expect(vehicleTypeLabel(option, vehicleScopeCounts([{ value: 'motorhome', count: 38629 }]))).toBe(
      `Motorhomes (${(38629).toLocaleString()})`,
    );
  });
});
