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
  }

  // --- filter editing -------------------------------------------------------------------

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

  protected toggleFacetValue(value: string): void {
    const field = this.facetField();
    const adding = !this.filter.covers(field, value);
    this.filter.toggleTerm(field, value);
    if (adding) {
      // Constraining a field usually leaves it showing one value at 100%, which
      // is true and useless. Move to the next field that can still split the set.
      this.facetField.set(this.nextUnconstrainedField(field));
    }
    this.reload();
  }

  /** The next facet field the filter does not already pin, wrapping around. */
  private nextUnconstrainedField(current: string): string {
    const constrained = new Set(this.filter.conditions().map((item) => item.field));
    const start = FACET_FIELDS.indexOf(current);
    for (let step = 1; step <= FACET_FIELDS.length; step += 1) {
      const candidate = FACET_FIELDS[(start + step) % FACET_FIELDS.length];
      if (!constrained.has(candidate)) {
        return candidate;
      }
    }
    return current;
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
