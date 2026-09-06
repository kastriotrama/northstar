import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TabsModule } from '@openng/optimus-ui/tabs';
import { TagModule } from '@openng/optimus-ui/tag';
import { ToggleSwitchModule } from '@openng/optimus-ui/toggleswitch';

import { Api } from '../../core/api';
import type {
  CoverageBatch,
  FieldCoverage,
  TecDocCoverageReport,
  TsCoverageReport,
} from '../../core/models';

@Component({
  selector: 'ns-coverage',
  imports: [
    FormsModule,
    DecimalPipe,
    ButtonModule,
    SelectModule,
    TableModule,
    TabsModule,
    TagModule,
    ToggleSwitchModule,
  ],
  templateUrl: './coverage.html',
  styleUrl: './coverage.scss',
})
export class CoveragePage {
  private readonly api = inject(Api);

  protected readonly batches = signal<CoverageBatch[]>([]);
  protected readonly batchId = signal<string | null>(null);
  protected readonly report = signal<TsCoverageReport | null>(null);
  protected readonly tecdoc = signal<TecDocCoverageReport | null>(null);
  protected readonly loading = signal(false);
  protected readonly tecdocLoading = signal(false);
  protected readonly error = signal<string | null>(null);

  /**
   * Hides fields that are simply rare rather than unresolved.
   *
   * Sorting purely by missing-count puts fields that only ever apply to a handful of
   * records (sub_brand, base_model) above fields the rules genuinely fail on. The
   * actionable set is: the rules reached the field but could not confirm it
   * (candidates_only), or the field is normalized for some records but not all.
   */
  protected readonly onlyActionable = signal(true);

  /**
   * A field normalized on 1 of 15,471 records is an optional field, not a gap the rules
   * failed at. Below this share of the batch a field is treated as rare and hidden from
   * the actionable view -- unless it carries unconfirmed candidates, which always means
   * the rules reached it and stopped short.
   */
  private static readonly RARE_FIELD_THRESHOLD = 0.02;

  protected readonly fields = computed<FieldCoverage[]>(() => {
    const report = this.report();
    if (!report) {
      return [];
    }
    const fields = this.onlyActionable()
      ? report.fields.filter(
          (field) =>
            field.candidates_only > 0 ||
            (field.coverage >= CoveragePage.RARE_FIELD_THRESHOLD && field.coverage < 1),
        )
      : [...report.fields];

    // Most actionable first: fields the rules reached but could not confirm, then the
    // largest remaining gaps. The table stays sortable for anything else.
    return fields.sort(
      (a, b) => b.candidates_only - a.candidates_only || b.missing - a.missing,
    );
  });

  protected readonly gapCount = computed(() => this.fields().filter((f) => f.missing > 0).length);

  protected readonly unresolvedRecords = computed(() => {
    const status = this.report()?.status;
    return status ? status.provisional + status.review_required + status.failed : 0;
  });

  constructor() {
    this.api.listCoverageBatches().subscribe({
      next: (response) => {
        this.batches.set(response.items);
        const first = response.items[0]?.batch_id ?? null;
        this.batchId.set(first);
        if (first) {
          this.load(first);
        }
      },
      error: () => this.error.set('Could not load the batch list.'),
    });
    this.loadTecDoc();
  }

  protected onBatchChange(batchId: string): void {
    this.batchId.set(batchId);
    this.load(batchId);
  }

  protected load(batchId: string): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.tsCoverage(batchId).subscribe({
      next: (report) => {
        this.report.set(report);
        this.loading.set(false);
      },
      error: (err) => {
        this.report.set(null);
        this.loading.set(false);
        this.error.set(
          err?.error?.detail ?? 'Coverage could not be computed for this batch.',
        );
      },
    });
  }

  protected loadTecDoc(): void {
    this.tecdocLoading.set(true);
    this.api.tecdocCoverage().subscribe({
      next: (report) => {
        this.tecdoc.set(report);
        this.tecdocLoading.set(false);
      },
      error: () => this.tecdocLoading.set(false),
    });
  }

  protected barClass(coverage: number): string {
    if (coverage >= 0.99) {
      return 'bar__fill';
    }
    return coverage >= 0.75 ? 'bar__fill bar__fill--mid' : 'bar__fill bar__fill--low';
  }

  protected percent(value: number): string {
    return `${(value * 100).toFixed(1)}%`;
  }
}
