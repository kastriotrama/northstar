import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { Subject, debounceTime, of, switchMap } from 'rxjs';
import { catchError, forkJoin } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import { ResolverPanel } from '../../components/resolver-panel';
import { FilterState, OPERATORS } from '../../core/filter-state';
import type { EditableCondition } from '../../core/filter-state';
import type {
  GapGroup,
  GapGroupReport,
  GapGroupingMode,
  RuleOperator,
  UnresolvedFieldCount,
  VehicleDetail,
  VehicleFacet,
  VehicleFilterRequest,
  VehicleRow,
} from '../../core/models';

/**
 * Fields that carry identity. While any of these still varies inside a filter, the
 * matched cars are not one thing and no single value can be asserted over them.
 */
const IDENTITY_FIELDS: readonly string[] = ['brand', 'model', 'variant', 'type_text'];

/** Fields worth offering as facets, cheapest and most discriminating first. */
const FACET_FIELDS: string[] = [
  'brand',
  'model',
  'variant',
  'type_text',
  'fab_code',
  'vehicle_year',
  'body_code',
  'fuel1',
  'gearbox',
  'is_4wd',
  'kw',
  'euro_class',
];

/**
 * TS records: the explorer.
 *
 * Filter the whole vehicle population, see what the matched set still cannot say about
 * itself, and hand that filter to the resolver rather than retyping it there. The list
 * is one row per car -- the projection is deduplicated, so a car ingested thirteen times
 * appears once.
 */
@Component({
  selector: 'ns-ts-records',
  imports: [
    DecimalPipe,
    FormsModule,
    ButtonModule,
    InputTextModule,
    SelectModule,
    TableModule,
    TagModule,
    ResolverPanel,
  ],
  templateUrl: './ts-records.html',
  styleUrl: './ts-records.scss',
})
export class TsRecordsPage {
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  protected readonly filter = inject(FilterState);

  protected readonly operators = OPERATORS;
  protected readonly facetFields = FACET_FIELDS;

  protected readonly rows = signal<VehicleRow[]>([]);
  protected readonly matched = signal<number | null>(null);
  protected readonly total = signal<number | null>(null);
  protected readonly unresolved = signal<UnresolvedFieldCount[]>([]);
  protected readonly facet = signal<VehicleFacet | null>(null);
  protected readonly facetField = signal<string>('brand');
  protected readonly loading = signal(false);
  protected readonly summaryLoading = signal(false);
  protected readonly error = signal<string | null>(null);

  /** Restrict the list to cars still missing one field, so the gap is the subject. */
  protected readonly unresolvedField = signal<string | null>(null);

  // --- adding a condition by hand -------------------------------------------------------
  protected readonly draftField = signal<string>('brand');
  protected readonly draftOperator = signal<RuleOperator>('equals');
  protected readonly draftValue = signal<string>('');

  // --- where the gap lives, as opposed to which cars are in it ---------------------------
  // Two different questions. An exact-value list can only answer the second, and
  // answering only the second is how a structural problem across 608,251 cars read as
  // six thousand unrelated populations.
  protected readonly view = signal<'cars' | 'gaps'>('cars');
  protected readonly groupField = signal<string>('brand');
  protected readonly groupMode = signal<GapGroupingMode>('leading_token');
  protected readonly groups = signal<GapGroupReport | null>(null);
  protected readonly groupsLoading = signal(false);

  protected readonly groupModes: ReadonlyArray<{ value: GapGroupingMode; label: string }> = [
    { value: 'leading_token', label: 'first word' },
    { value: 'character_shape', label: 'character shape' },
    { value: 'exact', label: 'exact value' },
  ];

  /** Grouping needs a gap to group; without one the question has no subject. */
  protected readonly groupTarget = computed(
    () => this.unresolvedField() ?? this.unresolved()[0]?.field ?? null,
  );

  // --- the resolver, as a panel rather than a page ---------------------------------------
  protected readonly resolving = signal<string | null>(null);
  protected readonly buildId = signal<string | null>(null);

  /** How many matched cars lack the field being resolved. */
  protected readonly resolvingUnresolved = computed(() => {
    const field = this.resolving();
    return this.unresolved().find((gap) => gap.field === field)?.unresolved ?? 0;
  });

  /**
   * Identity fields the current filter has not pinned. The resolver refuses to write
   * while any of these still varies, so the filter must narrow until none do.
   */
  protected readonly varyingIdentity = computed(() => {
    const pinned = new Set(this.filter.conditions().map((item) => item.field));
    return IDENTITY_FIELDS.filter((field) => !pinned.has(field));
  });

  // --- record panel ---------------------------------------------------------------------
  protected readonly detail = signal<VehicleDetail | null>(null);
  protected readonly detailLoading = signal(false);

  private readonly cursors = signal<number[]>([0]);
  protected readonly pageIndex = signal(0);
  protected readonly hasMore = signal(false);
  protected readonly canPrevious = computed(() => this.pageIndex() > 0);

  protected readonly filterLabel = computed(() => {
    const conditions = this.filter.conditions();
    if (conditions.length === 0) {
      return 'every car';
    }
    return conditions
      .map((condition) => {
        const operator =
          OPERATORS.find((entry) => entry.value === condition.operator)?.label ??
          condition.operator;
        return `${condition.field} ${operator} ${condition.values.join(' or ')}`;
      })
      .join(' AND ');
  });

  private readonly requests = new Subject<void>();
  private readonly summaryRequests = new Subject<void>();

  constructor() {
    this.requests
      .pipe(
        debounceTime(200),
        switchMap(() => {
          this.loading.set(true);
          const request = this.request();
          // The gaps summary is deliberately not in here. It aggregates eight fields
          // across the matched set, which costs ~9.5s unfiltered, and holding the list
          // and facets behind it made the whole screen wait on a sidebar.
          return forkJoin({
            count: this.api.countVehicles(request),
            page: this.api.vehiclePage(request, { cursor: 0, limit: 100 }),
            facet: this.api.vehicleFacet(request, this.facetField(), 12),
          }).pipe(
            catchError((err: unknown) => {
              this.error.set(TsRecordsPage.describe(err, 'Could not read the population.'));
              return of(null);
            }),
          );
        }),
        takeUntilDestroyed(),
      )
      .subscribe((result) => {
        this.loading.set(false);
        if (!result) {
          return;
        }
        this.error.set(null);
        this.matched.set(result.count.matched_rows);
        this.total.set(result.count.total_rows);
        this.rows.set(result.page.items);
        this.hasMore.set(result.page.has_more);
        this.facet.set(result.facet);
        this.cursors.set([0]);
        this.pageIndex.set(0);
      });

    this.summaryRequests
      .pipe(
        debounceTime(200),
        switchMap(() => {
          this.summaryLoading.set(true);
          return this.api
            .unresolvedSummary({ conditions: this.filter.payload() })
            .pipe(catchError(() => of(null)));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((summary) => {
        this.summaryLoading.set(false);
        this.unresolved.set(summary?.fields ?? []);
      });

    this.api.listMatchChunkBuilds().subscribe({
      next: (builds) => this.buildId.set(builds[0]?.build_id ?? null),
      error: () => this.buildId.set(null),
    });

    this.reload();
  }

  private request(): VehicleFilterRequest {
    return {
      conditions: this.filter.payload(),
      unresolved_field: this.unresolvedField(),
    };
  }

  protected reload(): void {
    this.requests.next();
    this.summaryRequests.next();
    if (this.view() === 'gaps') {
      this.loadGroups();
    }
  }

  protected showView(view: 'cars' | 'gaps'): void {
    this.view.set(view);
    if (view === 'gaps') {
      this.loadGroups();
    }
  }

  protected onGroupField(field: string): void {
    this.groupField.set(field);
    this.loadGroups();
  }

  protected onGroupMode(mode: GapGroupingMode): void {
    this.groupMode.set(mode);
    this.loadGroups();
  }

  private loadGroups(): void {
    const target = this.groupTarget();
    if (!target) {
      this.groups.set(null);
      return;
    }
    this.groupsLoading.set(true);
    this.api
      .gapGroups(
        { conditions: this.filter.payload(), unresolved_field: target },
        { field: this.groupField(), mode: this.groupMode(), limit: 25 },
      )
      .subscribe({
        next: (report) => {
          this.groups.set(report);
          this.groupsLoading.set(false);
        },
        error: (err: unknown) => {
          this.groupsLoading.set(false);
          this.groups.set(null);
          this.error.set(TsRecordsPage.describe(err, 'Could not group this gap.'));
        },
      });
  }

  /**
   * Take a group into the filter and go back to the cars.
   *
   * A shape becomes a `starts_with`, since that is what the grouping means; an exact
   * group becomes an equality, since that is what it means instead.
   */
  protected drillInto(group: GapGroup): void {
    const conditions = [...this.filter.conditions()];
    conditions.push({
      field: this.groupField(),
      operator: this.groupMode() === 'exact' ? 'equals' : 'starts_with',
      layer: 'source',
      values: [group.label],
      locked: false,
    });
    this.filter.conditions.set(conditions);
    this.unresolvedField.set(this.groupTarget());
    this.view.set('cars');
    this.reload();
  }

  protected groupShare(group: GapGroup): number {
    const total = this.groups()?.total_rows ?? 0;
    return total ? (group.rows / total) * 100 : 0;
  }

  // --- filter editing -------------------------------------------------------------------

  /** The first facet field the filter has not already pinned. */
  private firstFreeField(): string {
    const pinned = new Set(this.filter.conditions().map((item) => item.field));
    return FACET_FIELDS.find((field) => !pinned.has(field)) ?? FACET_FIELDS[0];
  }

  protected addDraft(): void {
    const value = this.draftValue().trim();
    if (!value) {
      return;
    }
    const conditions = [...this.filter.conditions()];
    conditions.push({
      field: this.draftField(),
      operator: this.draftOperator(),
      layer: 'source',
      values: [value],
      locked: false,
    });
    this.filter.conditions.set(conditions);
    this.draftValue.set('');
    // Move off the field just pinned, so the empty row never mirrors a live clause.
    this.draftField.set(this.firstFreeField());
    this.reload();
  }

  protected removeCondition(condition: EditableCondition): void {
    this.filter.removeCondition(condition);
    this.reload();
  }

  protected setOperator(condition: EditableCondition, operator: RuleOperator): void {
    this.filter.setOperator(condition, operator);
    this.reload();
  }

  /**
   * Add or remove one value of the faceted field.
   *
   * Values of the same field are OR-ed, so picking several is how "all of these
   * models are rear-wheel drive" gets said. The facet deliberately stays put
   * afterwards: an earlier version advanced to the next unpinned field as soon
   * as one value was chosen, which made selecting a second value impossible.
   * The backend lifts this field's own clause when counting, so its siblings
   * stay visible and their counts stay honest.
   */
  protected toggleFacetValue(value: string): void {
    this.filter.toggleTerm(this.facetField(), value);
    if (this.filter.conditions().some((item) => item.field === this.draftField())) {
      this.draftField.set(this.firstFreeField());
    }
    this.reload();
  }

  /** Values of the faceted field the filter already covers, in picking order. */
  protected readonly facetSelection = computed(() => {
    const field = this.facetField();
    return (
      this.filter
        .conditions()
        .find((item) => item.field === field && item.layer === 'source')?.values ?? []
    );
  });

  protected clearFacetSelection(): void {
    const field = this.facetField();
    for (const value of [...this.facetSelection()]) {
      this.filter.removeTerm(field, value);
    }
    this.reload();
  }

  protected covers(value: string): boolean {
    return this.filter.covers(this.facetField(), value);
  }

  protected onFacetField(field: string): void {
    this.facetField.set(field);
    this.reload();
  }

  protected onUnresolvedField(field: string | null): void {
    this.unresolvedField.set(field);
    this.reload();
  }

  protected clearFilter(): void {
    this.filter.reset();
    this.unresolvedField.set(null);
    this.reload();
  }

  // --- paging ---------------------------------------------------------------------------

  protected next(): void {
    const last = this.rows()[this.rows().length - 1];
    if (!last) {
      return;
    }
    this.loadPage(last.source_record_id, this.pageIndex() + 1);
  }

  protected previous(): void {
    const index = this.pageIndex() - 1;
    if (index < 0) {
      return;
    }
    this.loadPage(this.cursors()[index] ?? 0, index);
  }

  private loadPage(cursor: number, index: number): void {
    this.loading.set(true);
    this.api.vehiclePage(this.request(), { cursor, limit: 100 }).subscribe({
      next: (page) => {
        this.rows.set(page.items);
        this.hasMore.set(page.has_more);
        this.pageIndex.set(index);
        const cursors = [...this.cursors()];
        cursors[index] = cursor;
        this.cursors.set(cursors);
        this.loading.set(false);
      },
      error: (err: unknown) => {
        this.loading.set(false);
        this.error.set(TsRecordsPage.describe(err, 'Could not load that page.'));
      },
    });
  }

  // --- record panel ---------------------------------------------------------------------

  protected open(row: VehicleRow): void {
    this.resolving.set(null);
    this.detailLoading.set(true);
    this.api.vehicleDetail(row.source_record_id).subscribe({
      next: (detail) => {
        this.detail.set(detail);
        this.detailLoading.set(false);
      },
      error: () => this.detailLoading.set(false),
    });
  }

  protected closeDetail(): void {
    this.detail.set(null);
  }

  /** Step through the loaded page without closing the panel. */
  protected step(offset: number): void {
    const current = this.detail();
    if (!current) {
      return;
    }
    const rows = this.rows();
    const index = rows.findIndex((row) => row.source_record_id === current.source_record_id);
    const next = rows[index + offset];
    if (next) {
      this.open(next);
    }
  }

  protected statusSeverity(status: string): 'success' | 'info' | 'warn' {
    if (status === 'resolved') {
      return 'success';
    }
    return status === 'rule_resolved' ? 'info' : 'warn';
  }

  // --- the handoff ----------------------------------------------------------------------

  /**
   * Open the resolver on this field.
   *
   * There is no navigation and nothing to carry: the filter that produced the list is
   * already the rule's predicate, so the panel simply opens beside it.
   */
  protected resolve(field: string): void {
    this.detail.set(null);
    this.resolving.set(field);
  }

  protected closeResolver(): void {
    this.resolving.set(null);
  }

  /** From the record panel: adopt this car's value for the field, then resolve it. */
  protected resolveFrom(
    field: string,
    sourceField: string | null,
    sourceValue: string | null,
  ): void {
    if (sourceField && sourceValue) {
      this.filter.addTerm(sourceField, sourceValue);
      this.reload();
    }
    this.resolve(field);
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
