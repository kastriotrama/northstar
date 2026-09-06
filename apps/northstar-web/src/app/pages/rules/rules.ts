import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import type { TableLazyLoadEvent } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import type { RuleCatalogEntry, RuleCatalogResponse } from '../../core/models';

@Component({
  selector: 'ns-rules',
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
  templateUrl: './rules.html',
})
export class RulesPage {
  private readonly api = inject(Api);

  protected readonly catalog = signal<RuleCatalogResponse | null>(null);
  protected readonly loading = signal(false);
  protected readonly rows = 100;
  protected readonly first = signal(0);

  protected readonly query = signal('');
  protected readonly area = signal<string | null>(null);
  protected readonly canonicalField = signal<string | null>(null);
  protected readonly decision = signal<string | null>(null);

  protected readonly detail = signal<RuleCatalogEntry | null>(null);
  protected readonly detailOpen = signal(false);

  protected readonly areaOptions = computed(() =>
    (this.catalog()?.areas ?? []).map((area) => ({ label: area, value: area })),
  );
  protected readonly fieldOptions = computed(() =>
    (this.catalog()?.canonical_fields ?? []).map((field) => ({ label: field, value: field })),
  );
  protected readonly decisionOptions = [
    { label: 'accepted', value: 'accepted' },
    { label: 'proposed', value: 'proposed' },
  ];

  protected readonly optionsForDetail = computed(() => {
    const rule = this.detail();
    const catalog = this.catalog();
    if (!rule || !catalog) {
      return [];
    }
    return catalog.canonical_options_by_field[rule.canonical_field] ?? [];
  });

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.api
      .listRules({
        query: this.query(),
        area: this.area(),
        canonicalField: this.canonicalField(),
        decision: this.decision(),
        limit: this.rows,
        offset: this.first(),
      })
      .subscribe({
        next: (catalog) => {
          this.catalog.set(catalog);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      });
  }

  protected onLazyLoad(event: TableLazyLoadEvent): void {
    this.first.set(event.first ?? 0);
    this.load();
  }

  protected search(): void {
    this.first.set(0);
    this.load();
  }

  protected clear(): void {
    this.query.set('');
    this.area.set(null);
    this.canonicalField.set(null);
    this.decision.set(null);
    this.search();
  }

  protected open(rule: RuleCatalogEntry): void {
    this.detail.set(rule);
    this.detailOpen.set(true);
  }

  protected decisionSeverity(decision: string): 'success' | 'warn' {
    return decision === 'accepted' ? 'success' : 'warn';
  }
}
