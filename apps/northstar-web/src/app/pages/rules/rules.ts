import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { CheckboxModule } from '@openng/optimus-ui/checkbox';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import type { TableLazyLoadEvent } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import type {
  RuleCatalogEntry,
  RuleCatalogResponse,
  TransformerStage,
} from '../../core/models';

@Component({
  selector: 'ns-rules',
  imports: [
    FormsModule,
    DecimalPipe,
    ButtonModule,
    CheckboxModule,
    DialogModule,
    InputTextModule,
    SelectModule,
    TableModule,
    TagModule,
  ],
  templateUrl: './rules.html',
  styleUrl: './rules.scss',
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
  protected readonly origin = signal<string | null>(null);
  protected readonly source = signal<string | null>(null);
  /** TecDoc model names and manufacturers -- open vocabulary, no target ever, and
   * ~40x every other rule. Hidden by default so they do not bury what a reviewer
   * can act on; one checkbox away. */
  protected readonly includeInventory = signal(false);
  protected readonly transformerId = signal<string | null>(null);

  protected readonly detail = signal<RuleCatalogEntry | null>(null);
  protected readonly detailOpen = signal(false);
  protected readonly pipelineOpen = signal(false);

  protected readonly areaOptions = computed(() =>
    (this.catalog()?.areas ?? []).map((area) => ({ label: area, value: area })),
  );
  protected readonly fieldOptions = computed(() =>
    (this.catalog()?.canonical_fields ?? []).map((field) => ({ label: field, value: field })),
  );
  protected readonly decisionOptions = [
    { label: 'accepted', value: 'accepted' },
    { label: 'proposed', value: 'proposed' },
    { label: 'applied', value: 'applied' },
    { label: 'saved', value: 'saved' },
    { label: 'retired', value: 'retired' },
    { label: 'excluded', value: 'excluded' },
  ];
  protected readonly originOptions = [
    { label: 'Reviewed catalog', value: 'catalog' },
    { label: 'Compiled into the pipeline', value: 'code' },
    { label: 'Authored live (TS or TecDoc)', value: 'resolution' },
    { label: 'TecDoc: reviewed mapping', value: 'reviewed_mapping' },
    { label: 'TecDoc: generated proposal', value: 'generated' },
  ];
  protected readonly sourceOptions = [
    { label: 'Transportstyrelsen', value: 'transportstyrelsen' },
    { label: 'TecDoc', value: 'tecdoc' },
  ];

  /** The pipeline stages, kept in execution order as the API returns them. */
  protected readonly stages = computed<TransformerStage[]>(
    () => this.catalog()?.transformers ?? [],
  );

  protected readonly selectedStage = computed(() =>
    this.stages().find((stage) => stage.transformer_id === this.transformerId()) ?? null,
  );

  protected readonly optionsForDetail = computed(() => {
    const rule = this.detail();
    const catalog = this.catalog();
    if (!rule || !catalog || rule.origin === 'code') {
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
        origin: this.origin(),
        source: this.source(),
        includeInventory: this.includeInventory(),
        transformerId: this.transformerId(),
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

  protected onSourceChange(source: string | null): void {
    this.source.set(source);
    // A field with a target vocabulary can differ between sources, and an
    // origin like 'reviewed_mapping' only exists for TecDoc; both stay valid
    // to leave set, but area/canonical field usually do not carry across.
    this.search();
  }

  protected onIncludeInventoryChange(include: boolean): void {
    this.includeInventory.set(include);
    this.search();
  }

  protected clear(): void {
    this.query.set('');
    this.area.set(null);
    this.canonicalField.set(null);
    this.decision.set(null);
    this.origin.set(null);
    this.source.set(null);
    this.includeInventory.set(false);
    this.transformerId.set(null);
    this.search();
  }

  /** Clicking a stage narrows the table to exactly what that stage applies. */
  protected showStage(stage: TransformerStage): void {
    this.transformerId.set(
      this.transformerId() === stage.transformer_id ? null : stage.transformer_id,
    );
    this.area.set(null);
    this.origin.set(null);
    this.search();
  }

  protected open(rule: RuleCatalogEntry): void {
    this.detail.set(rule);
    this.detailOpen.set(true);
  }

  /**
   * A rule's state, across every kind. Catalog and TecDoc rules are accepted or
   * proposed; projection rules are applied, saved or retired; a live TecDoc
   * ruling can also be excluded. Retired and excluded read as neither live nor
   * pending, so both are greyed rather than coloured.
   */
  protected decisionSeverity(decision: string): 'success' | 'warn' | 'secondary' {
    if (decision === 'accepted' || decision === 'applied') {
      return 'success';
    }
    return decision === 'retired' || decision === 'excluded' ? 'secondary' : 'warn';
  }

  protected originLabel(origin: string): string {
    switch (origin) {
      case 'resolution':
        return 'live';
      case 'reviewed_mapping':
        return 'reviewed';
      case 'generated':
        return 'generated';
      default:
        return origin;
    }
  }

  protected sourceLabel(source: string): string {
    return source === 'tecdoc' ? 'TecDoc' : 'TS';
  }
}
