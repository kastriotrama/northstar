import { DecimalPipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';
import type { MatchReviewPattern, MatchRunSummary } from '../../core/models';

@Component({
  selector: 'ns-chunks',
  imports: [FormsModule, DecimalPipe, ButtonModule, DialogModule, InputTextModule, SelectModule, TableModule, TagModule],
  templateUrl: './chunks.html',
})
export class ChunksPage {
  private readonly api = inject(Api);
  protected readonly summary = signal<MatchRunSummary | null>(null);
  protected readonly patterns = signal<MatchReviewPattern[]>([]);
  protected readonly selected = signal<MatchReviewPattern | null>(null);
  protected readonly loading = signal(false);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly decisionOpen = signal(false);
  protected readonly reviewer = signal('');
  protected readonly action = signal<'accept_pattern' | 'keep_blocked' | 'change_rule'>('accept_pattern');
  protected readonly selectedValues = signal('');
  protected readonly reason = signal('');
  protected readonly category = signal<string | null>(null);

  protected readonly categoryOptions = [
    { label: 'All blocker patterns', value: null },
    { label: 'Bodywork conflicts', value: 'bodywork_conflict' },
    { label: 'Hard technical conflicts', value: 'hard_technical_conflict' },
    { label: 'Candidate margin', value: 'candidate_margin' },
    { label: 'Model source conflicts', value: 'model_source_conflict' },
  ];

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.matchReviewSummary().subscribe({
      next: (summary) => {
        this.summary.set(summary);
        if (!summary.operation_id) {
          this.patterns.set([]);
          this.loading.set(false);
          return;
        }
        this.loadPatterns(summary.operation_id);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Match review could not be loaded.');
      },
    });
  }

  protected loadPatterns(operationId = this.summary()?.operation_id): void {
    if (!operationId) return;
    this.loading.set(true);
    this.api.listMatchReviewPatterns(operationId, this.category()).subscribe({
      next: (page) => {
        this.patterns.set(page.patterns);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Review patterns could not be loaded.');
      },
    });
  }

  protected openDecision(pattern: MatchReviewPattern): void {
    this.selected.set(pattern);
    this.reviewer.set('');
    this.action.set('accept_pattern');
    this.selectedValues.set('');
    this.reason.set('');
    this.decisionOpen.set(true);
  }

  protected saveDecision(): void {
    const operationId = this.summary()?.operation_id;
    const pattern = this.selected();
    const reviewer = this.reviewer().trim();
    const reason = this.reason().trim();
    const values = this.selectedValues().split(',').map((value) => value.trim()).filter(Boolean);
    if (!operationId || !pattern || !reviewer || reason.length < 5) return;
    this.saving.set(true);
    this.api.decideMatchReviewPattern(operationId, pattern.pattern_key, {
      action: this.action(), reviewer, reason, selected_values: values,
    }).subscribe({
      next: () => {
        this.saving.set(false);
        this.decisionOpen.set(false);
        this.loadPatterns(operationId);
      },
      error: (error: { error?: { detail?: string } }) => {
        this.saving.set(false);
        this.error.set(error.error?.detail ?? 'The rule choice could not be saved.');
      },
    });
  }

  protected statusSeverity(pattern: MatchReviewPattern): 'success' | 'warn' | 'secondary' {
    return pattern.decision ? 'success' : pattern.coverage === 'exhaustive' ? 'warn' : 'secondary';
  }

  protected statusLabel(pattern: MatchReviewPattern): string {
    return pattern.decision ? 'Decided' : pattern.coverage === 'exhaustive' ? 'Needs review' : 'Sample';
  }

  protected values(values: Record<string, unknown>): string {
    return Object.entries(values).map(([key, value]) => `${key}: ${String(value)}`).join(' · ');
  }
}
