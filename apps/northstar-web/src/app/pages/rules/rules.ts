import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePipe, DecimalPipe } from '@angular/common';
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
  VocabularyAlignmentDraft,
  VocabularyAlignmentVersion,
} from '../../core/models';

@Component({
  selector: 'ns-rules',
  imports: [
    FormsModule,
    DatePipe,
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
  protected readonly vocabularyOpen = signal(true);

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

  protected readonly vocabularyAlignments = signal<VocabularyAlignmentVersion[]>([]);
  protected readonly vocabularyDrafts = signal<VocabularyAlignmentDraft[]>([]);
  protected readonly vocabularyError = signal<string | null>(null);
  /** Who to credit for an approve/decline/activate action. Remembered locally
   * only -- there is no login here, this just saves retyping a name. */
  protected readonly reviewerName = signal(
    (() => {
      try {
        return localStorage.getItem('ns-reviewer-name') ?? '';
      } catch {
        return '';
      }
    })(),
  );

  /** One group per vocabulary that has anything left to review or activate.
   * Declined drafts stay in the database as a record but never appear here --
   * this is a queue of what still needs a decision, not an audit log. */
  protected readonly draftGroups = computed(() => {
    const proposed = new Map<string, VocabularyAlignmentDraft[]>();
    const approvedCount = new Map<string, number>();
    for (const draft of this.vocabularyDrafts()) {
      if (draft.status === 'proposed') {
        const list = proposed.get(draft.vocabulary) ?? [];
        list.push(draft);
        proposed.set(draft.vocabulary, list);
      } else if (draft.status === 'approved') {
        approvedCount.set(draft.vocabulary, (approvedCount.get(draft.vocabulary) ?? 0) + 1);
      }
    }
    const vocabularies = new Set([...proposed.keys(), ...approvedCount.keys()]);
    return [...vocabularies].sort().map((vocabulary) => ({
      vocabulary,
      proposed: proposed.get(vocabulary) ?? [],
      approvedCount: approvedCount.get(vocabulary) ?? 0,
    }));
  });

  constructor() {
    this.load();
    this.loadVocabularyAlignments();
    this.loadVocabularyDrafts();
  }

  protected onReviewerNameChange(value: string): void {
    this.reviewerName.set(value);
    try {
      localStorage.setItem('ns-reviewer-name', value);
    } catch {
      // Per-viewer convenience only; fine to lose across a private window.
    }
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

  private loadVocabularyAlignments(): void {
    // Independent of the filtered rule table above and rarely changes, so it
    // loads once rather than on every search/page change.
    this.api.vocabularyAlignments().subscribe({
      next: (response) => this.vocabularyAlignments.set(response.versions),
      error: () => this.vocabularyAlignments.set([]),
    });
  }

  private loadVocabularyDrafts(): void {
    this.api.vocabularyAlignmentDrafts().subscribe({
      next: (response) => this.vocabularyDrafts.set(response.drafts),
      error: () => this.vocabularyDrafts.set([]),
    });
  }

  protected reviewDraft(draft: VocabularyAlignmentDraft, status: 'approved' | 'declined'): void {
    const reviewer = this.reviewerName().trim();
    if (!reviewer) {
      this.vocabularyError.set('Enter your name before approving or declining a proposal.');
      return;
    }
    this.vocabularyError.set(null);
    this.api.reviewVocabularyAlignmentDraft(draft.id, status, reviewer).subscribe({
      next: () => this.loadVocabularyDrafts(),
      error: (err: unknown) =>
        this.vocabularyError.set(RulesPage.describeError(err, 'Could not save that review.')),
    });
  }

  protected activateApproved(vocabulary: string, approvedCount: number): void {
    const reviewer = this.reviewerName().trim();
    if (!reviewer) {
      this.vocabularyError.set('Enter your name before activating a version.');
      return;
    }
    const suffix = new Date().toISOString().replace(/[:.]/g, '-');
    const alignmentVersion = `align-${suffix}-${vocabulary}-v1`;
    const note = `${approvedCount} approved ${vocabulary} pair(s), activated from the review queue.`;
    this.vocabularyError.set(null);
    this.api
      .activateVocabularyAlignmentDrafts({
        vocabulary,
        alignmentVersion,
        activatedBy: reviewer,
        note,
      })
      .subscribe({
        next: () => {
          this.loadVocabularyDrafts();
          this.loadVocabularyAlignments();
        },
        error: (err: unknown) =>
          this.vocabularyError.set(
            RulesPage.describeError(err, 'Could not activate those drafts.'),
          ),
      });
  }

  private static describeError(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
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
