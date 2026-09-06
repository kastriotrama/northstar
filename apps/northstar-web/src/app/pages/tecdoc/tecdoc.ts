import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import type { TableLazyLoadEvent } from '@openng/optimus-ui/table';

import { Api } from '../../core/api';
import type { TecDocEntityPage, TecDocPage as TecDocVehiclePage } from '../../core/models';

const ENTITY_KINDS = [
  'manufacturer',
  'model_family',
  'engine',
  'fuel',
  'bodywork',
  'transmission',
  'drive',
] as const;

type View = 'vehicles' | 'entities';

@Component({
  selector: 'ns-tecdoc',
  imports: [
    FormsModule,
    DecimalPipe,
    ButtonModule,
    DialogModule,
    InputTextModule,
    SelectModule,
    TableModule,
  ],
  templateUrl: './tecdoc.html',
})
export class TecDocPage {
  private readonly api = inject(Api);

  protected readonly viewOptions = [
    { label: 'Vehicles (ktypes)', value: 'vehicles' as View },
    { label: 'Entities', value: 'entities' as View },
  ];
  protected readonly kindOptions = ENTITY_KINDS.map((kind) => ({ label: kind, value: kind }));

  protected readonly view = signal<View>('vehicles');
  protected readonly kind = signal<string>('manufacturer');
  protected readonly query = signal('');
  protected readonly loading = signal(false);

  protected readonly vehiclePage = signal<TecDocVehiclePage | null>(null);
  protected readonly entityPage = signal<TecDocEntityPage | null>(null);
  protected readonly detail = signal<Record<string, unknown> | null>(null);
  protected readonly detailOpen = signal(false);

  protected readonly rows = 100;
  protected readonly first = signal(0);

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

  /**
   * Offset paging is safe here: the TecDoc endpoints page over ~56k promoted ktypes,
   * not the 7.25M-row staging table, and they already limit in the database.
   */
  protected onLazyLoad(event: TableLazyLoadEvent): void {
    this.first.set(event.first ?? 0);
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    const offset = this.first();
    if (this.view() === 'vehicles') {
      this.api.listTecDocVehicles({ query: this.query(), limit: this.rows, offset }).subscribe({
        next: (page) => {
          this.vehiclePage.set(page);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      });
    } else {
      this.api
        .listTecDocEntities({ kind: this.kind(), query: this.query(), limit: this.rows, offset })
        .subscribe({
          next: (page) => {
            this.entityPage.set(page);
            this.loading.set(false);
          },
          error: () => this.loading.set(false),
        });
    }
  }

  protected search(): void {
    this.first.set(0);
    this.load();
  }

  protected onViewChange(view: View): void {
    this.view.set(view);
    this.search();
  }

  protected onKindChange(kind: string): void {
    this.kind.set(kind);
    this.search();
  }

  protected open(row: Record<string, unknown>): void {
    this.detail.set(row);
    this.detailOpen.set(true);
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
}
