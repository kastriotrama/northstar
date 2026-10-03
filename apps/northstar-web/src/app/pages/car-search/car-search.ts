import { DatePipe, DecimalPipe, PercentPipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, debounceTime, map, of, switchMap } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import { CorrectionDecisions } from '../../components/correction-decisions';
import { KTypeCandidates } from '../../components/ktype-candidates';
import { MatchingSummary } from '../../components/matching-summary';
import type {
  NorVehicleRecord,
  NorVehicleRow,
  VehicleCondition,
  VehicleMatchLookup,
  VehicleSearchRequest,
} from '../../core/models';
import {
  fieldGroups,
  fieldSourceLabel,
  showFieldValue,
  showValue,
  sourceDetail,
  sourceLabel,
} from '../../core/vehicle-record';
import {
  VEHICLE_TYPES,
  type VehicleType,
  vehicleScopeCondition,
  vehicleScopeCounts,
  vehicleTypeLabel,
} from '../../core/vehicle-scope';

interface FacetDef {
  key: string;
  label: string;
}

/** Every dropdown filter: each reads the vehicle's merged value, whichever source won it. */
const FACETS: readonly FacetDef[] = [
  { key: 'manufacturer', label: 'Manufacturer' },
  { key: 'model_family', label: 'Model family' },
  { key: 'fuel', label: 'Fuel' },
  { key: 'transmission', label: 'Transmission' },
  { key: 'drive_type', label: 'Drive type' },
  { key: 'bodywork_form', label: 'Bodywork' },
  { key: 'engine_code', label: 'Engine code' },
  { key: 'emission_standard', label: 'Euro class' },
  { key: 'colour', label: 'Colour' },
];

const RANGES: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'production_year', label: 'Production year' },
  { key: 'power_kw', label: 'Power (kW)' },
  { key: 'displacement_cc', label: 'Displacement (cc)' },
  { key: 'seats', label: 'Seats' },
];

export type RegistryStatus = 'any' | 'registered' | 'deregistered';

export const REGISTRY_STATUSES: ReadonlyArray<{ value: RegistryStatus; label: string }> = [
  { value: 'any', label: 'Any' },
  { value: 'registered', label: 'In the register' },
  { value: 'deregistered', label: 'Deregistered' },
];

/** Cars a person decided the KType for: `core.vehicles.match_state`, kept by the choice. */
export type KTypeChoiceFilter = 'any' | 'decided' | 'chosen' | 'none';

export const KTYPE_CHOICE_FILTERS: ReadonlyArray<{
  value: KTypeChoiceFilter;
  label: string;
  states: readonly string[];
}> = [
  { value: 'any', label: 'Any', states: [] },
  { value: 'decided', label: 'Decided by a person', states: ['manual', 'manual_none'] },
  { value: 'chosen', label: 'KType chosen', states: ['manual'] },
  { value: 'none', label: '“None of these”', states: ['manual_none'] },
];

/** The list's columns a person may correct, by how the row holds them. */
const ROW_TEXT = ['manufacturer', 'model_family', 'engine_code', 'drive_type', 'bodywork_form'] as const;
const ROW_NUMBERS = ['production_year', 'power_kw', 'displacement_cc'] as const;

/** A row's values a correction touched: what the matcher uses for each field after it. */
function correctedValues(lookup: VehicleMatchLookup): Partial<NorVehicleRow> {
  const values: Partial<NorVehicleRow> = {};
  // Only fields a person's correction stands on, or stood on: the rest is the row's own.
  const touched = new Set((lookup?.corrections ?? []).map((head) => head.field));
  for (const field of lookup?.correctable_fields ?? []) {
    if (!touched.has(field.field)) continue;
    const text = ROW_TEXT.find((name) => name === field.field);
    const number = ROW_NUMBERS.find((name) => name === field.field);
    if (text) values[text] = field.current_value;
    if (number) {
      const parsed = field.current_value === null ? null : Number(field.current_value);
      values[number] = parsed === null || Number.isFinite(parsed) ? parsed : null;
    }
  }
  return values;
}

const PAGE_SIZE = 50;

/**
 * Vehicles: NorthStar vehicles (`core.vehicles`), one per physical car, keyed by NOR ID.
 *
 * Every value is the one that won across the providers -- Transportstyrelsen, AIS, a
 * reviewer's rule, a learned rule -- and the record panel says which source that was,
 * what lost, and which plates the car has carried. A plate or VIN is an identifier the
 * car holds for a while, so a previous plate still finds it. Read-only: rules are written
 * from TS data, not here.
 */
@Component({
  selector: 'ns-car-search',
  imports: [
    DatePipe,
    DecimalPipe,
    PercentPipe,
    FormsModule,
    ButtonModule,
    InputTextModule,
    TableModule,
    TagModule,
    CorrectionDecisions,
    KTypeCandidates,
    MatchingSummary,
  ],
  templateUrl: './car-search.html',
  styleUrl: './car-search.scss',
})
export class CarSearchPage implements OnInit {
  private readonly api = inject(Api);

  protected readonly facets = FACETS;
  protected readonly ranges = RANGES;
  protected readonly vehicleTypes = VEHICLE_TYPES;
  protected readonly registryStatuses = REGISTRY_STATUSES;
  protected readonly ktypeChoiceFilters = KTYPE_CHOICE_FILTERS;
  /** Narrow the list to cars whose KType a person decided. */
  protected readonly ktypeChoice = signal<KTypeChoiceFilter>('any');
  /** Passenger cars first: the default view, still just a filter anyone can change. */
  protected readonly vehicleType = signal<VehicleType>('passenger');
  /** Deregistered cars stay visible by default: a plate lookup must still find them. */
  protected readonly registryStatus = signal<RegistryStatus>('any');
  /** Vehicles per `vehicle_scope`, for the option labels. */
  protected readonly scopeCounts = signal<Record<string, number>>({});
  protected readonly scopeLoaded = signal(false);
  /** No vehicle on this server has a type yet: `core.vehicles` has not been backfilled. */
  protected readonly notBackfilled = computed(
    () => this.scopeLoaded() && Object.keys(this.scopeCounts()).length === 0,
  );

  protected readonly text = signal('');
  protected readonly selected = signal<Record<string, string>>(
    Object.fromEntries(FACETS.map((facet) => [facet.key, ''])),
  );
  protected readonly options = signal<Record<string, { value: string; count: number }[]>>(
    Object.fromEntries(FACETS.map((facet) => [facet.key, []])),
  );
  protected readonly rangeFrom = signal<Record<string, string>>(
    Object.fromEntries(RANGES.map((range) => [range.key, ''])),
  );
  protected readonly rangeTo = signal<Record<string, string>>(
    Object.fromEntries(RANGES.map((range) => [range.key, ''])),
  );

  protected readonly rows = signal<NorVehicleRow[]>([]);
  protected readonly matched = signal<number | null>(null);
  protected readonly nextCursor = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  /** Cars list, or the matching summary over the same filter. */
  protected readonly view = signal<'cars' | 'matching' | 'decisions'>('cars');
  /** The filter exactly as the car list is queried with it, for the matching view. */
  protected readonly currentConditions = computed(() => this.request().conditions);
  protected readonly currentText = computed(() => this.request().text);

  protected readonly openId = signal<string | null>(null);
  protected readonly record = signal<NorVehicleRecord | null>(null);
  protected readonly recordLoading = signal(false);
  protected readonly recordError = signal<string | null>(null);

  /** The open vehicle's fields in their groups, empty ones folded away. */
  protected readonly groups = computed(() => fieldGroups(this.record()));
  /** The open vehicle's current plate: from its record once loaded, else from its row. */
  protected readonly openPlate = computed(() => {
    const current = this.plates().find((plate) => plate.current);
    if (current) return current.value;
    return this.rows().find((row) => row.vehicle_id === this.openId())?.plate ?? null;
  });
  protected readonly plates = computed(
    () => this.record()?.identifiers.filter((identifier) => identifier.kind === 'plate') ?? [],
  );
  protected readonly otherIdentifiers = computed(
    () => this.record()?.identifiers.filter((identifier) => identifier.kind !== 'plate') ?? [],
  );

  protected readonly hasFilter = computed(
    () =>
      this.vehicleType() !== 'passenger' ||
      this.registryStatus() !== 'any' ||
      this.ktypeChoice() !== 'any' ||
      this.text().trim() !== '' ||
      Object.values(this.selected()).some((value) => value !== '') ||
      Object.values(this.rangeFrom()).some((value) => value.trim() !== '') ||
      Object.values(this.rangeTo()).some((value) => value.trim() !== ''),
  );

  private readonly search$ = new Subject<void>();
  private readonly open$ = new Subject<string>();

  constructor() {
    // switchMap drops a slow earlier response the moment a newer search starts.
    this.search$
      .pipe(
        debounceTime(250),
        switchMap(() => {
          this.loading.set(true);
          this.error.set(null);
          return this.api.searchVehicles(this.request(), { limit: PAGE_SIZE }).pipe(
            catchError((err: { error?: { detail?: unknown } }) => {
              this.error.set(this.detail(err) ?? 'Vehicle search is unavailable right now.');
              return of(null);
            }),
          );
        }),
      )
      .subscribe((page) => {
        this.loading.set(false);
        if (!page) return;
        this.rows.set(page.items);
        this.matched.set(page.matched_rows);
        this.nextCursor.set(page.next_cursor);
      });

    this.open$
      .pipe(
        switchMap((vehicleId) =>
          this.api.vehicleRecord(vehicleId).pipe(
            map((record) => ({ record, error: null as string | null })),
            catchError((err: { error?: { detail?: unknown } }) =>
              of({ record: null, error: this.detail(err) ?? 'Could not load this vehicle.' }),
            ),
          ),
        ),
      )
      .subscribe(({ record, error }) => {
        this.record.set(record);
        this.recordError.set(error);
        this.recordLoading.set(false);
      });
  }

  ngOnInit(): void {
    this.loadScopeCounts();
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

  protected onVehicleType(value: VehicleType): void {
    this.vehicleType.set(value);
    this.search$.next();
  }

  protected onRegistryStatus(value: RegistryStatus): void {
    this.registryStatus.set(value);
    this.search$.next();
  }

  protected onKtypeChoice(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    const known = KTYPE_CHOICE_FILTERS.find((option) => option.value === value);
    this.ktypeChoice.set(known ? known.value : 'any');
    this.search$.next();
  }

  protected scopeLabel(option: (typeof VEHICLE_TYPES)[number]): string {
    return vehicleTypeLabel(option, this.scopeCounts());
  }

  protected reset(): void {
    this.vehicleType.set('passenger');
    this.registryStatus.set('any');
    this.ktypeChoice.set('any');
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
    this.api.searchVehicles(this.request(), { cursor, limit: PAGE_SIZE }).subscribe({
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

  protected open(vehicleId: string): void {
    this.openId.set(vehicleId);
    this.record.set(null);
    this.recordError.set(null);
    this.recordLoading.set(true);
    this.open$.next(vehicleId);
  }

  /**
   * A person's KType choice changed: show it on the car's row at once, and reload the
   * record so its KType and source show it. The record is not cleared first, so the
   * candidates panel stays mounted with its notice.
   */
  protected onChoiceChanged(vehicleId: string, lookup: VehicleMatchLookup): void {
    const choice = lookup?.choice ?? null;
    const ktype = choice?.status === 'chosen' ? choice.ktype : null;
    const state =
      choice?.status === 'chosen' ? 'manual' : choice?.status === 'none' ? 'manual_none' : null;
    // A correction was saved or undone: the row shows the corrected values at once too.
    const values = correctedValues(lookup);
    this.rows.update((rows) =>
      rows.map((row) =>
        row.vehicle_id === vehicleId
          ? {
              ...row,
              ...values,
              // Without a choice, now or before, the KType on the row is not a person's to change.
              ...(choice
                ? {
                    ktype,
                    match_state: state,
                    review_fields: [
                      ...row.review_fields.filter((field) => field !== 'ktype'),
                      ...(ktype ? ['ktype'] : []),
                    ],
                  }
                : {}),
            }
          : row,
      ),
    );
    if (this.openId() === vehicleId) this.open$.next(vehicleId);
  }

  /** An example from the matching view: show that vehicle in the list and open it. */
  protected openExample(vehicleId: string): void {
    if (!vehicleId) return;
    this.view.set('cars');
    this.onText(vehicleId);
    this.open(vehicleId);
  }

  protected close(): void {
    this.openId.set(null);
    this.record.set(null);
  }

  /** A decision for several cars was undone: the open car may be one of them, so read it again. */
  protected onDecisionUndone(): void {
    const open = this.openId();
    if (open) this.open$.next(open);
  }

  protected isAsserted(row: NorVehicleRow, field: string): 'review' | 'rule' | null {
    if (row.review_fields.includes(field)) return 'review';
    if (row.rule_fields.includes(field)) return 'rule';
    return null;
  }

  // The record's wording is shared with the Matched cars dialog (`core/vehicle-record`).
  protected readonly sourceLabel = sourceLabel;
  protected readonly fieldSourceLabel = fieldSourceLabel;
  protected readonly showField = showFieldValue;
  protected readonly sourceDetail = sourceDetail;
  protected readonly show = showValue;

  private request(): VehicleSearchRequest {
    return { conditions: this.conditions(), text: this.text().trim() };
  }

  private conditions(): VehicleCondition[] {
    const conditions: VehicleCondition[] = [];
    const scope = vehicleScopeCondition(this.vehicleType());
    if (scope) {
      conditions.push({ field: scope.field, operator: scope.operator, values: scope.values ?? [] });
    }
    if (this.registryStatus() !== 'any') {
      conditions.push({
        field: 'registry_status',
        operator: 'equals',
        values: [this.registryStatus()],
      });
    }
    const decided = KTYPE_CHOICE_FILTERS.find((option) => option.value === this.ktypeChoice());
    if (decided && decided.states.length) {
      conditions.push({ field: 'match_state', operator: 'equals', values: [...decided.states] });
    }
    for (const facet of this.facets) {
      const value = this.selected()[facet.key];
      if (value) conditions.push({ field: facet.key, operator: 'equals', values: [value] });
    }
    for (const range of this.ranges) {
      const from = this.rangeFrom()[range.key]?.trim() ?? '';
      const to = this.rangeTo()[range.key]?.trim() ?? '';
      if (/^\d+$/.test(from)) conditions.push({ field: range.key, operator: 'gte', values: [from] });
      if (/^\d+$/.test(to)) conditions.push({ field: range.key, operator: 'lte', values: [to] });
    }
    return conditions;
  }

  private detail(err: { error?: { detail?: unknown } }): string | null {
    const detail = err?.error?.detail;
    return typeof detail === 'string' ? detail : null;
  }

  private loadScopeCounts(): void {
    this.api.vehicleValues({ conditions: [], text: '' }, 'vehicle_scope', 20).subscribe({
      next: (facet) => {
        this.scopeCounts.set(vehicleScopeCounts(facet.values));
        this.scopeLoaded.set(true);
      },
      error: () => this.scopeLoaded.set(true),
    });
  }

  private loadFacet(key: string): void {
    // Each dropdown is counted with its own clause lifted (the server does that), so
    // choosing a value never hides its siblings. Model families narrow by manufacturer.
    const conditions: VehicleCondition[] = [];
    const manufacturer = this.selected()['manufacturer'];
    if (key === 'model_family' && manufacturer) {
      conditions.push({ field: 'manufacturer', operator: 'equals', values: [manufacturer] });
    }
    this.api.vehicleValues({ conditions, text: '' }, key, 100).subscribe({
      next: (facet) => this.options.update((current) => ({ ...current, [key]: facet.values })),
      error: () => undefined,
    });
  }
}
