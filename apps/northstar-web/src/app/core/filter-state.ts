import { Injectable, computed, signal } from '@angular/core';

import type { RuleCondition, RuleOperator } from './models';

/** A clause while it is being edited. `locked` marks one the screen owns. */
export interface EditableCondition {
  field: string;
  operator: RuleOperator;
  layer: 'source' | 'normalized';
  values: string[];
  locked: boolean;
}

export const OPERATORS: ReadonlyArray<{ value: RuleOperator; label: string }> = [
  { value: 'equals', label: '=' },
  { value: 'not_equals', label: '≠' },
  { value: 'starts_with', label: 'starts' },
  { value: 'contains', label: 'has' },
  { value: 'gte', label: '≥' },
  { value: 'lte', label: '≤' },
];

/** Operators comparing a single number, so they can never hold OR-ed terms. */
const SINGLE_VALUE_OPERATORS: ReadonlySet<RuleOperator> = new Set<RuleOperator>([
  'gte',
  'lte',
]);

/**
 * The filter, shared between the screen that builds it and the screen that turns it into
 * a rule.
 *
 * This exists because a filter and a rule predicate are the same expression: the browse
 * screen narrows a population, and the resolver says what that population means. Holding
 * the conditions in one place is what removes the translation step -- there is no
 * "convert filter to rule", there is one object handed across.
 */
@Injectable({ providedIn: 'root' })
export class FilterState {
  readonly conditions = signal<EditableCondition[]>([]);

  /** Which field the reviewer went to the resolver to fill in, if any. */
  readonly targetField = signal<string | null>(null);

  readonly isEmpty = computed(() => this.conditions().length === 0);

  /** The wire shape: what both the filter endpoints and the rule endpoints accept. */
  readonly payload = computed<RuleCondition[]>(() =>
    this.conditions().map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      layer: condition.layer,
      values: condition.values,
    })),
  );

  reset(): void {
    this.conditions.set([]);
    this.targetField.set(null);
  }

  set(conditions: EditableCondition[], targetField: string | null = null): void {
    this.conditions.set(conditions);
    this.targetField.set(targetField);
  }

  /**
   * Same field clicked again means OR, not a replacement.
   *
   * `operator` only takes effect when this is the field's first value -- an existing
   * clause keeps whatever operator it already has, since that is what the values being
   * OR-ed into it actually mean.
   */
  addTerm(
    field: string,
    value: string,
    layer: 'source' | 'normalized' = 'source',
    operator: RuleOperator = 'equals',
  ): void {
    const conditions = [...this.conditions()];
    const existing = conditions.find(
      (item) => item.field === field && item.layer === layer && !item.locked,
    );
    if (existing) {
      const values = SINGLE_VALUE_OPERATORS.has(existing.operator)
        ? [value]
        : existing.values.includes(value)
          ? existing.values
          : [...existing.values, value];
      conditions[conditions.indexOf(existing)] = { ...existing, values };
    } else {
      conditions.push({ field, operator, layer, values: [value], locked: false });
    }
    this.conditions.set(conditions);
  }

  /**
   * Clicking a covered value takes it back out, so a wrong pick costs one click rather
   * than rebuilding the clause. The clause goes when its last value does.
   */
  removeTerm(field: string, value: string, layer: 'source' | 'normalized' = 'source'): void {
    const conditions = [...this.conditions()];
    const existing = conditions.find(
      (item) => item.field === field && item.layer === layer && !item.locked,
    );
    if (!existing) {
      return;
    }
    const values = existing.values.filter((item) => item !== value);
    this.conditions.set(
      values.length === 0
        ? conditions.filter((item) => item !== existing)
        : conditions.map((item) => (item === existing ? { ...item, values } : item)),
    );
  }

  covers(field: string, value: string, layer: 'source' | 'normalized' = 'source'): boolean {
    return this.conditions().some(
      (item) => item.field === field && item.layer === layer && item.values.includes(value),
    );
  }

  toggleTerm(
    field: string,
    value: string,
    layer: 'source' | 'normalized' = 'source',
    operator: RuleOperator = 'equals',
  ): void {
    if (this.covers(field, value, layer)) {
      this.removeTerm(field, value, layer);
    } else {
      this.addTerm(field, value, layer, operator);
    }
  }

  removeCondition(condition: EditableCondition): void {
    this.conditions.set(this.conditions().filter((item) => item !== condition));
  }

  setOperator(condition: EditableCondition, operator: RuleOperator): void {
    this.conditions.set(
      this.conditions().map((item) =>
        item === condition
          ? {
              ...item,
              operator,
              // A numeric comparison takes exactly one value; drop OR-ed extras.
              values: SINGLE_VALUE_OPERATORS.has(operator)
                ? item.values.slice(0, 1)
                : item.values,
            }
          : item,
      ),
    );
  }
}
