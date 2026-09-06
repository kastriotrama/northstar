import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import type {
  SourceBatch,
  SourceFieldStat,
  SourceRecord,
  SourceRecordDetail,
  SourceRecordPage,
} from '../../core/models';

@Component({
  selector: 'ns-ts-records',
  imports: [
    FormsModule,
    DecimalPipe,
    ButtonModule,
    DialogModule,
    InputTextModule,
    SelectModule,
    TableModule,
    TagModule,
  ],
  templateUrl: './ts-records.html',
})
export class TsRecordsPage {
  private readonly api = inject(Api);

  protected readonly page = signal<SourceRecordPage | null>(null);
  protected readonly loading = signal(false);
  protected readonly batches = signal<SourceBatch[]>([]);
  protected readonly fieldStats = signal<SourceFieldStat[]>([]);
  protected readonly detail = signal<SourceRecordDetail | null>(null);
  protected readonly detailOpen = signal(false);

  // Filters
  protected readonly query = signal('');
  protected readonly batchId = signal<string | null>(null);
  protected readonly field = signal<string | null>(null);
  protected readonly value = signal('');

  /**
   * Cursor stack for keyset paging.
   *
   * The staging table has 7.25M rows and only a primary-key index; `OFFSET 5000000`
   * measured at ~7.3s against it while the keyset read is ~0.01s. So "previous page" is
   * implemented by remembering the cursors we came through rather than by seeking back.
   */
  private readonly cursors = signal<Array<number | null>>([null]);
  protected readonly pageIndex = signal(0);

  protected readonly columns = computed(() => this.page()?.summary_fields ?? []);
  protected readonly canPrevious = computed(() => this.pageIndex() > 0);
  protected readonly canNext = computed(() => this.page()?.has_more ?? false);

  protected readonly fieldOptions = computed(() =>
    this.fieldStats().map((stat) => ({ label: stat.field, value: stat.field })),
  );

  constructor() {
    this.api.listSourceBatches().subscribe({
      next: (response) => this.batches.set(response.items),
      error: () => this.batches.set([]),
    });
    this.api.sourceFieldInventory().subscribe({
      next: (response) => this.fieldStats.set(response.fields),
      error: () => this.fieldStats.set([]),
    });
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    const cursor = this.cursors()[this.pageIndex()] ?? null;
    this.api
      .listSourceRecords({
        query: this.query(),
        field: this.field(),
        value: this.field() ? this.value() : null,
        batchId: this.batchId(),
        cursor,
        limit: 100,
      })
      .subscribe({
        next: (page) => {
          this.page.set(page);
          this.loading.set(false);
        },
        error: () => {
          this.page.set(null);
          this.loading.set(false);
        },
      });
  }

  /** Any filter change invalidates the cursor stack -- paging restarts from the top. */
  protected applyFilters(): void {
    this.cursors.set([null]);
    this.pageIndex.set(0);
    this.load();
  }

  protected clearFilters(): void {
    this.query.set('');
    this.batchId.set(null);
    this.field.set(null);
    this.value.set('');
    this.applyFilters();
  }

  protected next(): void {
    const cursor = this.page()?.next_cursor ?? null;
    if (cursor === null) {
      return;
    }
    const stack = [...this.cursors()];
    stack[this.pageIndex() + 1] = cursor;
    this.cursors.set(stack);
    this.pageIndex.update((index) => index + 1);
    this.load();
  }

  protected previous(): void {
    if (!this.canPrevious()) {
      return;
    }
    this.pageIndex.update((index) => index - 1);
    this.load();
  }

  protected open(record: SourceRecord): void {
    this.detail.set(null);
    this.detailOpen.set(true);
    this.api.getSourceRecord(record.id).subscribe({
      next: (detail) => this.detail.set(detail),
      error: () => this.detailOpen.set(false),
    });
  }

  protected cell(record: SourceRecord, column: string): string {
    const raw = record.raw_record?.[column];
    return raw === null || raw === undefined ? '' : String(raw);
  }

  protected entries(source: Record<string, unknown> | undefined): Array<[string, string]> {
    if (!source) {
      return [];
    }
    return Object.entries(source).map(([key, value]) => [
      key,
      typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value),
    ]);
  }

  protected statusSeverity(status: string): 'success' | 'info' | 'warn' | 'danger' {
    switch (status) {
      case 'resolved':
        return 'success';
      case 'provisional':
        return 'info';
      case 'review_required':
        return 'warn';
      default:
        return 'danger';
    }
  }
}
