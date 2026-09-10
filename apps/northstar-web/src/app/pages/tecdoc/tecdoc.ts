import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, debounceTime, forkJoin, of, switchMap } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import type { TableLazyLoadEvent } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';
import { TextareaModule } from '@openng/optimus-ui/textarea';

import { Api } from '../../core/api';
import { FilterState, OPERATORS } from '../../core/filter-state';
import type { EditableCondition } from '../../core/filter-state';
import type {
  RuleOperator,
  TecDocEntityPage,
  TecDocGapValue,
  TecDocGapValuesResponse,
  TecDocPage as TecDocVehiclePage,
  TecDocResolvableField,
  TecDocUnresolvedField,
  TecDocVehicleDetail,
  TecDocVehicleFacet,
  TecDocVehicleFieldStatus,
  TecDocVehicleFilter,
} from '../../core/models';

const ENTITY_KINDS = [
  'manufacturer',
  'model_family',
  'engine',
  'fuel',
  'bodywork',
  'transmission',
  'drive',
] as const;

/** Every field a condition, facet or the "add a condition" picker can name. */
const FACET_FIELDS: string[] = [
  'manufacturer',
  'model_family',
  'fuel_type',
  'bodywork_name',
  'bodywork_status',
  'drive_type',
  'drive_status',
  'transmission_type_name',
  'transmission_link_status',
  'engine_link_status',
  'year_from',
  'year_to',
];

/** The row-level gaps with a closed vocabulary -- the only ones a value can be
 * resolved toward. `engine` and `transmission` report a missing allocation, not
 * an unmapped value, so they stay a "show only these" filter with no Resolve. */
const RESOLVABLE_GAP_FIELDS: ReadonlySet<string> = new Set<TecDocResolvableField>([
  'energy_sources',
  'bodywork_form',
  'drive_type',
]);

/** Shared with `ResolverPanel`: one remembered name for whoever is reviewing,
 * across both the TS and TecDoc resolve flows. */
const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

type View = 'vehicles' | 'entities';

/**
 * TecDoc: the catalogue explorer, ported from `ts-data`.
 *
 * Filter the promoted KType population the same way the TS register is filtered --
 * conditions, real-value facets, a count of what still lacks each canonical field -- so
 * the two sides of the match read as one system rather than one workflow and one table
 * dump. Entities stay a plain browser: manufacturer and model_family are open vocabulary,
 * so there is no gap to narrow toward.
 */
@Component({
  selector: 'ns-tecdoc',
  imports: [
    DecimalPipe,
    FormsModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    SelectModule,
    TableModule,
    TagModule,
    TextareaModule,
  ],
  providers: [FilterState],
  templateUrl: './tecdoc.html',
  styleUrl: './tecdoc.scss',
})
export class TecDocPage {
  private readonly api = inject(Api);
  protected readonly filter = inject(FilterState);

  protected readonly viewOptions = [
    { label: 'Vehicles (ktypes)', value: 'vehicles' as View },
    { label: 'Entities', value: 'entities' as View },
  ];
  protected readonly kindOptions = ENTITY_KINDS.map((kind) => ({ label: kind, value: kind }));
  protected readonly operators = OPERATORS;
  protected readonly facetFields = FACET_FIELDS;

  protected readonly view = signal<View>('vehicles');
  protected readonly kind = signal<string>('manufacturer');
  protected readonly query = signal('');
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly vehiclePage = signal<TecDocVehiclePage | null>(null);
  protected readonly entityPage = signal<TecDocEntityPage | null>(null);
  protected readonly detail = signal<Record<string, unknown> | null>(null);
  protected readonly detailOpen = signal(false);

  /** One row's canonical fields, each with its outcome -- fetched whenever a row is
   * opened, same idea as `ts-data`'s record panel: the fields still missing a value
   * are the ones with a Resolve action next to them. */
  protected readonly vehicleDetail = signal<TecDocVehicleDetail | null>(null);
  protected readonly vehicleDetailLoading = signal(false);
  /** Whether the open Resolve dialog was opened from a row (vs. the field-level
   * "browse gap values" list) -- decides whether cancelling or saving returns to
   * the row's detail dialog. */
  protected readonly resolvingFromRow = signal(false);

  protected readonly rows = 100;
  protected readonly first = signal(0);

  // --- the filter: which KTypes ----------------------------------------------------------
  protected readonly matched = signal<number | null>(null);
  protected readonly totalKtypes = signal<number | null>(null);
  protected readonly draftField = signal<string>('manufacturer');
  protected readonly draftOperator = signal<RuleOperator>('equals');
  protected readonly draftValue = signal<string>('');
  protected readonly facet = signal<TecDocVehicleFacet | null>(null);

  /** Restrict the list to KTypes still missing one canonical field. */
  protected readonly unresolvedField = signal<string | null>(null);
  protected readonly unresolved = signal<TecDocUnresolvedField[]>([]);
  protected readonly summaryLoading = signal(false);
  protected readonly resolvableGapFields = RESOLVABLE_GAP_FIELDS;

  // --- the value-level gap: which raw codes/labels have no canonical target -------------
  protected readonly gapField = signal<string | null>(null);
  protected readonly gapValues = signal<TecDocGapValuesResponse | null>(null);
  protected readonly gapValuesLoading = signal(false);

  // --- resolving one value ----------------------------------------------------------
  protected readonly resolving = signal<TecDocGapValue | null>(null);
  protected readonly resolveDecision = signal<'accepted' | 'excluded'>('accepted');
  protected readonly resolveTarget = signal<string>('');
  protected readonly resolveNote = signal<string>('');
  protected readonly reviewerName = signal<string>(TecDocPage.rememberedReviewer());
  protected readonly resolveSaving = signal(false);
  protected readonly resolveError = signal<string | null>(null);

  protected readonly filterLabel = computed(() => {
    const conditions = this.filter.conditions();
    if (conditions.length === 0) {
      return 'every promoted ktype';
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

  /** Every field the current rows carry, so nothing in the payload is hidden. */
  protected readonly vehicleColumns = computed(() => {
    const items = this.vehiclePage()?.items ?? [];
    const keys = new Set<string>();
    for (const item of items) {
      Object.keys(item).forEach((key) => keys.add(key));
    }
    return [...keys];
  });

  protected readonly entityColumns = computed(() => {
    const items = this.entityPage()?.items ?? [];
    const keys = new Set<string>();
    for (const item of items) {
      Object.keys(item).forEach((key) => keys.add(key));
    }
    return [...keys];
  });

  protected readonly total = computed(() =>
    this.view() === 'vehicles'
      ? (this.vehiclePage()?.filtered_total ?? 0)
      : (this.entityPage()?.filtered_total ?? 0),
  );

  private readonly requests = new Subject<void>();
  private readonly summaryRequests = new Subject<void>();

  constructor() {
    // Only the vehicles view has a filter to react to; entities stay a plain paged
    // browser and are fetched directly by `load()`, the same as before this screen had
    // conditions at all.
    this.requests
      .pipe(
        debounceTime(200),
        switchMap(() => {
          this.loading.set(true);
          const request = this.vehicleRequest();
          return forkJoin({
            count: this.api.countTecDocVehicles(request),
            page: this.api.tecdocVehiclePage(request, { limit: this.rows, offset: this.first() }),
            facet: this.api.tecdocVehicleFacet(request, this.draftField(), 12),
          }).pipe(
            catchError((err: unknown) => {
              this.error.set(TecDocPage.describe(err, 'Could not read the ktype population.'));
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
        this.totalKtypes.set(result.count.total_rows);
        this.vehiclePage.set(result.page);
        this.facet.set(result.facet);
      });

    this.summaryRequests
      .pipe(
        debounceTime(200),
        switchMap(() => {
          this.summaryLoading.set(true);
          return this.api
            .tecdocUnresolvedSummary({ query: this.query(), conditions: this.filter.payload() })
            .pipe(catchError(() => of(null)));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((summary) => {
        this.summaryLoading.set(false);
        this.unresolved.set(summary?.fields ?? []);
      });

    this.reload();
  }

  private vehicleRequest(): TecDocVehicleFilter {
    return {
      query: this.query(),
      conditions: this.filter.payload(),
      unresolved_field: this.unresolvedField(),
    };
  }

  /** Filter, count, facet and gap summary all change together -- back to page one. */
  protected reload(): void {
    this.first.set(0);
    if (this.view() === 'vehicles') {
      this.requests.next();
      this.summaryRequests.next();
    } else {
      this.load();
    }
  }

  /** Paging keeps the same filter, so only the page itself needs to be refetched. */
  protected onLazyLoad(event: TableLazyLoadEvent): void {
    this.first.set(event.first ?? 0);
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    if (this.view() === 'vehicles') {
      this.api
        .tecdocVehiclePage(this.vehicleRequest(), { limit: this.rows, offset: this.first() })
        .subscribe({
          next: (page) => {
            this.vehiclePage.set(page);
            this.loading.set(false);
          },
          error: (err: unknown) => {
            this.loading.set(false);
            this.error.set(TecDocPage.describe(err, 'Could not load that page.'));
          },
        });
      return;
    }
    this.api
      .listTecDocEntities({
        kind: this.kind(),
        query: this.query(),
        limit: this.rows,
        offset: this.first(),
      })
      .subscribe({
        next: (page) => {
          this.entityPage.set(page);
          this.loading.set(false);
        },
        error: (err: unknown) => {
          this.loading.set(false);
          this.error.set(TecDocPage.describe(err, 'Could not read TecDoc entities.'));
        },
      });
  }

  protected search(): void {
    this.reload();
  }

  protected onViewChange(view: View): void {
    this.view.set(view);
    this.reload();
  }

  protected onKindChange(kind: string): void {
    this.kind.set(kind);
    this.reload();
  }

  // --- filter editing (ported from `ts-data`) --------------------------------------------

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

  protected toggleFacetValue(value: string): void {
    this.filter.toggleTerm(this.draftField(), value, 'source', this.draftOperator());
    this.reload();
  }

  protected readonly facetSelection = computed(() => {
    const field = this.draftField();
    return (
      this.filter
        .conditions()
        .find((item) => item.field === field && item.layer === 'source')?.values ?? []
    );
  });

  protected clearFacetSelection(): void {
    const field = this.draftField();
    for (const value of [...this.facetSelection()]) {
      this.filter.removeTerm(field, value);
    }
    this.reload();
  }

  protected covers(value: string): boolean {
    return this.filter.covers(this.draftField(), value);
  }

  protected onDraftField(field: string): void {
    this.draftField.set(field);
    this.reload();
  }

  protected onUnresolvedField(field: string | null): void {
    this.unresolvedField.set(this.unresolvedField() === field ? null : field);
    this.reload();
  }

  protected clearFilter(): void {
    this.filter.reset();
    this.unresolvedField.set(null);
    this.reload();
  }

  // --- the value-level gap: browse, then resolve -----------------------------------------

  /** Open (or close) the list of raw values behind one canonical field's gap. */
  protected browseGapValues(field: string): void {
    if (this.gapField() === field) {
      this.gapField.set(null);
      this.gapValues.set(null);
      return;
    }
    this.resolving.set(null);
    this.gapField.set(field);
    this.gapValuesLoading.set(true);
    this.api.tecdocGapValues(field).subscribe({
      next: (response) => {
        this.gapValues.set(response);
        this.gapValuesLoading.set(false);
      },
      error: (err: unknown) => {
        this.gapValuesLoading.set(false);
        this.error.set(TecDocPage.describe(err, 'Could not read that gap.'));
      },
    });
  }

  protected openResolve(value: TecDocGapValue): void {
    this.resolvingFromRow.set(false);
    this.resolving.set(value);
    this.resolveDecision.set(value.resolution?.decision ?? 'accepted');
    this.resolveTarget.set(value.resolution?.canonical_value ?? '');
    this.resolveNote.set(value.resolution?.note ?? '');
    this.resolveError.set(null);
  }

  /** Resolve one field from the row detail dialog, the same act as resolving it
   * from "browse gap values" -- just scoped to the one term this row carries,
   * rather than picking it out of the whole population's list.
   *
   * Still needs that field's `canonical_options` for the dialog's picker, which
   * only `/gaps` carries -- the row detail endpoint reports outcomes, not the
   * target vocabulary. Fetched here, exactly what "browse gap values" already
   * has loaded by the time it opens the same dialog. */
  protected resolveFieldFromDetail(entry: TecDocVehicleFieldStatus): void {
    if (entry.source_term === null || entry.blocked_reason) {
      return;
    }
    const field = entry.canonical_field;
    const sourceTerm = entry.source_term;
    const label = entry.label;
    this.gapField.set(field);
    this.detailOpen.set(false);
    this.api.tecdocGapValues(field).subscribe({
      next: (response) => {
        this.gapValues.set(response);
        this.openResolve({
          source_term: sourceTerm,
          label,
          key_table: response.key_table,
          support: 0,
          blocked_reason: entry.blocked_reason,
          resolution: null,
        });
        this.resolvingFromRow.set(true);
      },
      error: (err: unknown) => {
        this.detailOpen.set(true);
        this.error.set(
          TecDocPage.describe(err, 'Could not load the canonical options for this field.'),
        );
      },
    });
  }

  protected closeResolve(): void {
    this.resolving.set(null);
    this.resolveError.set(null);
    if (this.resolvingFromRow()) {
      this.resolvingFromRow.set(false);
      this.detailOpen.set(true);
    }
  }

  protected onResolveDecision(decision: 'accepted' | 'excluded'): void {
    this.resolveDecision.set(decision);
    if (decision === 'excluded') {
      this.resolveTarget.set('');
    }
  }

  protected submitResolve(): void {
    const value = this.resolving();
    const field = this.gapField();
    if (!value || !field) {
      return;
    }
    const reviewedBy = this.reviewerName().trim();
    if (!reviewedBy) {
      this.resolveError.set('Add your name — a ruling is recorded with who made it.');
      return;
    }
    const decision = this.resolveDecision();
    if (decision === 'accepted' && !this.resolveTarget()) {
      this.resolveError.set('Pick the canonical value this resolves to.');
      return;
    }
    if (decision === 'excluded' && !this.resolveNote().trim()) {
      this.resolveError.set('Excluding a value needs a note saying why.');
      return;
    }
    TecDocPage.rememberReviewer(reviewedBy);
    this.resolveSaving.set(true);
    this.resolveError.set(null);
    this.api
      .resolveTecDocGap({
        canonical_field: field as TecDocResolvableField,
        source_term: value.source_term,
        decision,
        canonical_value: decision === 'accepted' ? this.resolveTarget() : null,
        note: this.resolveNote(),
        reviewed_by: reviewedBy,
      })
      .subscribe({
        next: (resolution) => {
          this.resolveSaving.set(false);
          this.resolving.set(null);
          const current = this.gapValues();
          if (current) {
            this.gapValues.set({
              ...current,
              values: current.values.map((item) =>
                item.source_term === value.source_term ? { ...item, resolution } : item,
              ),
            });
          }
          if (this.resolvingFromRow()) {
            this.resolvingFromRow.set(false);
            const openRow = this.vehicleDetail();
            if (openRow) {
              this.loadVehicleDetail(openRow.source_key);
            }
            this.detailOpen.set(true);
          }
          // A resolved value stops being a gap; the row-level count it fed reflects that
          // once the KType population is reloaded.
          this.reload();
        },
        error: (err: unknown) => {
          this.resolveSaving.set(false);
          this.resolveError.set(TecDocPage.describe(err, 'Could not save that ruling.'));
        },
      });
  }

  private static rememberedReviewer(): string {
    try {
      return localStorage.getItem(REVIEWER_STORAGE_KEY) ?? '';
    } catch {
      return '';
    }
  }

  private static rememberReviewer(name: string): void {
    try {
      localStorage.setItem(REVIEWER_STORAGE_KEY, name);
    } catch {
      /* not remembering the name costs a retype, nothing more */
    }
  }

  // --- table & detail ----------------------------------------------------------------

  protected open(row: Record<string, unknown>): void {
    this.detail.set(row);
    this.detailOpen.set(true);
    const sourceKeys = row['source_keys'] as Record<string, string> | undefined;
    const sourceKey = sourceKeys?.['alias'];
    if (sourceKey) {
      this.loadVehicleDetail(sourceKey);
    } else {
      this.vehicleDetail.set(null);
    }
  }

  private loadVehicleDetail(sourceKey: string): void {
    this.vehicleDetailLoading.set(true);
    this.api.tecdocVehicleDetail(sourceKey).subscribe({
      next: (detail) => {
        this.vehicleDetail.set(detail);
        this.vehicleDetailLoading.set(false);
      },
      error: () => {
        this.vehicleDetail.set(null);
        this.vehicleDetailLoading.set(false);
      },
    });
  }

  protected cell(row: Record<string, unknown>, column: string): string {
    const value = row[column];
    if (value === null || value === undefined) {
      return '';
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }

  protected entries(source: Record<string, unknown> | null): Array<[string, string]> {
    if (!source) {
      return [];
    }
    return Object.entries(source).map(([key, value]) => [
      key,
      value === null || value === undefined
        ? ''
        : typeof value === 'object'
          ? JSON.stringify(value, null, 2)
          : String(value),
    ]);
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
