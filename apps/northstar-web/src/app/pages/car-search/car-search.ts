import { DecimalPipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, debounceTime, of, switchMap } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import type {
  CanonicalVehicleRow,
  CarSearchRequest,
  RuleCondition,
  FullVehicleRecord,
} from '../../core/models';

interface FacetDef {
  key: string;
  label: string;
  /** `normalized` reads the canonical value; `source` reads the registry column verbatim. */
  layer: 'source' | 'normalized';
}

/**
 * Every dropdown filter the screen offers. Most are canonical -- what normalization
 * concluded, or failing that a live rule -- so "diesel" finds a car the registry logged
 * as fuel code "1". Color and vehicle class have no canonical form in the projection yet,
 * so those two read the registry column as-is, codes included.
 */
const FACETS: readonly FacetDef[] = [
  { key: 'manufacturer', label: 'Manufacturer', layer: 'normalized' },
  { key: 'model_family', label: 'Model family', layer: 'normalized' },
  { key: 'drive_type', label: 'Drive type', layer: 'normalized' },
  { key: 'bodywork_form', label: 'Bodywork', layer: 'normalized' },
  { key: 'engine_code', label: 'Engine code', layer: 'normalized' },
  { key: 'canonical_fuel', label: 'Fuel', layer: 'normalized' },
  { key: 'canonical_transmission', label: 'Transmission', layer: 'normalized' },
  { key: 'canonical_euro_class', label: 'Euro class', layer: 'normalized' },
  { key: 'color', label: 'Color (registry)', layer: 'source' },
  { key: 'vehicle_class', label: 'Vehicle class (registry)', layer: 'source' },
];

/** Numeric range filters, beyond production year and power. */
const RANGES: ReadonlyArray<{ key: string; label: string; layer: 'source' | 'normalized' }> = [
  { key: 'production_year', label: 'Production year', layer: 'normalized' },
  { key: 'power_kw', label: 'Power (kW)', layer: 'normalized' },
  { key: 'displacement_cc', label: 'Displacement (cc)', layer: 'normalized' },
  { key: 'passengers', label: 'Passengers', layer: 'source' },
];

const FIELD_LABELS: Record<string, string> = {
  manufacturer: 'Manufacturer',
  model_family: 'Model family',
  drive_type: 'Drive type',
  bodywork_form: 'Bodywork',
  engine_code: 'Engine code',
  power_kw: 'Power (kW)',
  displacement_cc: 'Displacement (cc)',
  production_year: 'Production year',
};

const PAGE_SIZE = 50;

/**
 * Vehicles: look a car up by what normalization concluded about it, with the registry's own
 * columns available too for the fields no canonical form exists for yet (fuel, gearbox,
 * color).
 *
 * The four identity filters (manufacturer, model family, drive type, bodywork) and engine
 * code read the canonical value: derived by normalization, or failing that filled by a live
 * rule. The rest read the registry column verbatim -- there is no canonical fuel or gearbox
 * in the projection yet, so those filters are the registry's own codes. The record panel
 * always shows registry-versus-canonical, whichever filter found the car. Read-only: rules
 * are written from TS data, not here.
 */
@Component({
  selector: 'ns-car-search',
  imports: [DecimalPipe, FormsModule, ButtonModule, InputTextModule, TableModule, TagModule],
  templateUrl: './car-search.html',
  styleUrl: './car-search.scss',
})
export class CarSearchPage implements OnInit {
  private readonly api = inject(Api);

  protected readonly facets = FACETS;
  protected readonly ranges = RANGES;
  protected readonly fieldLabels = FIELD_LABELS;

  protected readonly text = signal('');
  protected readonly selected = signal<Record<string, string>>(
    Object.fromEntries(FACETS.map((facet) => [facet.key, ''])),
  );
  protected readonly options = signal<Record<string, { value: string; count: number | null }[]>>(
    Object.fromEntries(FACETS.map((facet) => [facet.key, []])),
  );
  protected readonly rangeFrom = signal<Record<string, string>>(
    Object.fromEntries(RANGES.map((range) => [range.key, ''])),
  );
  protected readonly rangeTo = signal<Record<string, string>>(
    Object.fromEntries(RANGES.map((range) => [range.key, ''])),
  );

  protected readonly rows = signal<CanonicalVehicleRow[]>([]);
  protected readonly matched = signal<number | null>(null);
  protected readonly nextCursor = signal<number | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly detail = signal<FullVehicleRecord | null>(null);
  protected readonly openRow = signal<CanonicalVehicleRow | null>(null);
  protected readonly detailLoading = signal(false);

  protected readonly hasFilter = computed(
    () =>
      this.text().trim() !== '' ||
      Object.values(this.selected()).some((value) => value !== '') ||
      Object.values(this.rangeFrom()).some((value) => value.trim() !== '') ||
      Object.values(this.rangeTo()).some((value) => value.trim() !== ''),
  );

  private readonly search$ = new Subject<void>();
  /** Guards the row list against a slow earlier response landing after a newer one. */
  private generation = 0;

  constructor() {
    this.search$
      .pipe(
        debounceTime(250),
        switchMap(() => {
          this.loading.set(true);
          this.error.set(null);
          const generation = ++this.generation;
          return this.api.searchCars(this.request(), { limit: PAGE_SIZE }).pipe(
            catchError(() => {
              this.error.set('Vehicle search is unavailable right now.');
              return of(null);
            }),
            switchMap((page) => of({ page, generation })),
          );
        }),
      )
      .subscribe(({ page, generation }) => {
        if (generation !== this.generation) return;
        this.loading.set(false);
        if (!page) return;
        this.rows.set(page.items);
        this.matched.set(page.matched_rows);
        this.nextCursor.set(page.next_cursor);
      });
  }

  ngOnInit(): void {
    for (const facet of this.facets) this.loadFacet(facet.key);
    this.search$.next();
  }

  protected onText(value: string): void {
    this.text.set(value);
    this.search$.next();
  }

  protected onFacet(key: string, value: string): void {
    this.selected.update((current) => ({ ...current, [key]: value }));
    if (key === 'manufacturer') {
      // A model family only means something inside its manufacturer.
      this.selected.update((current) => ({ ...current, model_family: '' }));
      this.loadFacet('model_family');
    }
    this.search$.next();
  }

  protected onRangeFrom(key: string, value: string): void {
    this.rangeFrom.update((current) => ({ ...current, [key]: value }));
    this.search$.next();
  }

  protected onRangeTo(key: string, value: string): void {
    this.rangeTo.update((current) => ({ ...current, [key]: value }));
    this.search$.next();
  }

  protected reset(): void {
    this.text.set('');
    this.selected.set(Object.fromEntries(this.facets.map((facet) => [facet.key, ''])));
    this.rangeFrom.set(Object.fromEntries(this.ranges.map((range) => [range.key, ''])));
    this.rangeTo.set(Object.fromEntries(this.ranges.map((range) => [range.key, ''])));
    this.loadFacet('model_family');
    this.search$.next();
  }

  protected loadMore(): void {
    const cursor = this.nextCursor();
    if (cursor === null || this.loading()) return;
    this.loading.set(true);
    this.api.searchCars(this.request(), { cursor, limit: PAGE_SIZE }).subscribe({
      next: (page) => {
        this.rows.update((current) => [...current, ...page.items]);
        this.nextCursor.set(page.next_cursor);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Vehicle search is unavailable right now.');
        this.loading.set(false);
      },
    });
  }

  protected open(row: CanonicalVehicleRow): void {
    this.openRow.set(row);
    this.detail.set(null);
    this.detailLoading.set(true);
    this.api.fullVehicle(row.source_record_id).subscribe({
      next: (detail) => {
        if (this.openRow()?.source_record_id !== row.source_record_id) return;
        this.detail.set(detail);
        this.detailLoading.set(false);
      },
      error: () => this.detailLoading.set(false),
    });
  }

  protected close(): void {
    this.openRow.set(null);
    this.detail.set(null);
  }

  protected isRuleFilled(row: CanonicalVehicleRow, field: string): boolean {
    return row.rule_filled.includes(field);
  }

  /** Registry and normalized values are arbitrary JSON; show them as plain text. */
  protected show(value: unknown): string {
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) {
      return value.length ? value.map((item) => this.show(item)).join(', ') : '—';
    }
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  protected entries(record: Record<string, unknown>): Array<[string, unknown]> {
    return Object.entries(record).sort(([a], [b]) => a.localeCompare(b));
  }

  protected filled(record: Record<string, unknown>): number {
    return Object.values(record).filter(
      (value) => value !== null && value !== undefined && value !== '',
    ).length;
  }

  protected statusLabel(status: 'resolved' | 'unresolved' | 'rule_resolved'): string {
    return { resolved: 'Normalized', rule_resolved: 'Filled by rule', unresolved: 'Unresolved' }[
      status
    ];
  }

  private request(): CarSearchRequest {
    return { conditions: this.conditions(), text: this.text().trim() };
  }

  private conditions(): RuleCondition[] {
    const conditions: RuleCondition[] = [];
    for (const facet of this.facets) {
      const value = this.selected()[facet.key];
      if (value) {
        conditions.push({
          field: facet.key,
          layer: facet.layer,
          operator: 'equals',
          values: [value],
        });
      }
    }
    for (const range of this.ranges) {
      const from = this.rangeFrom()[range.key]?.trim() ?? '';
      const to = this.rangeTo()[range.key]?.trim() ?? '';
      if (/^\d+$/.test(from)) {
        conditions.push({ field: range.key, layer: range.layer, operator: 'gte', values: [from] });
      }
      if (/^\d+$/.test(to)) {
        conditions.push({ field: range.key, layer: range.layer, operator: 'lte', values: [to] });
      }
    }
    return conditions;
  }

  private loadFacet(key: string): void {
    // Each dropdown is faceted with its own clause lifted (the server does that), so
    // choosing a value never hides its siblings. Model families narrow by manufacturer.
    const conditions: RuleCondition[] = [];
    const manufacturer = this.selected()['manufacturer'];
    if (key === 'model_family' && manufacturer) {
      conditions.push({
        field: 'manufacturer',
        layer: 'normalized',
        operator: 'equals',
        values: [manufacturer],
      });
    }
    this.api.vehicleFacet({ conditions }, key, 100).subscribe({
      next: (facet) =>
        this.options.update((current) => ({
          ...current,
          [key]: facet.values.map(({ value, count }) => ({ value, count })),
        })),
      error: () => undefined,
    });
  }
}
