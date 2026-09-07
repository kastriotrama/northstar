import { DecimalPipe } from '@angular/common';
import {
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { catchError, interval, of, switchMap, takeWhile } from 'rxjs';
import { AutoCompleteModule } from '@openng/optimus-ui/autocomplete';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { TagModule } from '@openng/optimus-ui/tag';
import { TextareaModule } from '@openng/optimus-ui/textarea';

import { Api } from '../core/api';
import { FilterState, OPERATORS } from '../core/filter-state';
import type {
  FieldValueCount,
  ResolutionRule,
  RuleAdvice,
  RulePreview,
} from '../core/models';

const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

/**
 * Say what a filtered population means.
 *
 * This was its own screen, which forced a reviewer who had already narrowed a
 * population on the browse screen to rebuild that narrowing by hand here. It is a
 * panel now: the filter is the rule's predicate, so there is nothing to carry across
 * and nothing to retype -- picking a gap is the whole handoff.
 *
 * What the browse screen already answers is deliberately absent. It shows which values
 * still vary and how large the population is, so this asks only the question it exists
 * for: what should the field be, and is the population coherent enough to say so.
 */
@Component({
  selector: 'ns-resolver-panel',
  imports: [
    DecimalPipe,
    FormsModule,
    AutoCompleteModule,
    ButtonModule,
    InputTextModule,
    TagModule,
    TextareaModule,
  ],
  templateUrl: './resolver-panel.html',
  styleUrl: './resolver-panel.scss',
})
export class ResolverPanel {
  private readonly api = inject(Api);
  protected readonly filter = inject(FilterState);

  /** The field this population cannot say, and the rule is being written to fill. */
  readonly targetField = input.required<string>();
  /** Rules still record the build they were authored against, though matching ignores it. */
  readonly buildId = input.required<string | null>();
  /** How many matched cars still lack the field, counted by the screen around this one. */
  readonly unresolvedRows = input<number>(0);
  /** Identity-bearing fields the browse screen sees still varying in this population. */
  readonly varyingFields = input<string[]>([]);

  readonly closed = output<void>();
  /** A rule was saved, run or retired: the counts this panel sits beside have moved. */
  readonly changed = output<void>();

  protected readonly operators = OPERATORS;

  protected readonly targetValue = signal('');
  protected readonly vocabulary = signal<{ closed: boolean; values: FieldValueCount[] } | null>(
    null,
  );
  protected readonly suggestions = signal<FieldValueCount[]>([]);

  protected readonly author = signal(ResolverPanel.rememberedReviewer());
  protected readonly note = signal('');
  protected readonly advice = signal<RuleAdvice | null>(null);
  protected readonly advising = signal(false);
  protected readonly preview = signal<RulePreview | null>(null);
  protected readonly previewing = signal(false);
  protected readonly saving = signal(false);
  protected readonly applying = signal(false);
  protected readonly appliedRows = signal(0);
  protected readonly savedRules = signal<ResolutionRule[]>([]);
  protected readonly busyRuleId = signal<string | null>(null);
  protected readonly flash = signal<{ text: string; bad: boolean } | null>(null);

  protected readonly targetProblem = computed<string | null>(() => {
    const value = this.targetValue().trim();
    if (!value) {
      return 'Enter a value for the rule to assign.';
    }
    const vocabulary = this.vocabulary();
    if (vocabulary?.closed && !vocabulary.values.some((entry) => entry.value === value)) {
      return `"${value}" is not a canonical ${this.targetField()}.`;
    }
    return null;
  });

  /**
   * A rule may only be written once the population means one thing. While an
   * identity-bearing field still varies, the reviewer is being asked to assert a single
   * value over cars that are not the same car.
   */
  protected readonly coherent = computed(() => this.varyingFields().length === 0);

  protected readonly canWrite = computed(
    () => this.unresolvedRows() > 0 && !this.targetProblem() && !this.filter.isEmpty(),
  );

  protected readonly statement = computed(() => {
    const clauses = this.filter.conditions().map((condition) => {
      const operator =
        OPERATORS.find((entry) => entry.value === condition.operator)?.label ??
        condition.operator;
      return `${condition.field} ${operator} ${condition.values.join(' or ')}`;
    });
    const value = this.targetValue().trim() || '…';
    return `IF ${clauses.join(' AND ')} THEN ${this.targetField()} = ${value}`;
  });

  constructor() {
    effect(() => {
      const field = this.targetField();
      const build = this.buildId();
      this.targetValue.set('');
      this.vocabulary.set(null);
      this.advice.set(null);
      this.preview.set(null);
      if (build) {
        this.loadVocabulary(build, field);
        this.loadSavedRules(build);
      }
    });
  }

  private loadVocabulary(buildId: string, field: string): void {
    this.api.targetVocabulary(buildId, field).subscribe({
      next: (vocabulary) =>
        this.vocabulary.set({ closed: vocabulary.closed, values: vocabulary.values }),
      error: () => this.vocabulary.set(null),
    });
  }

  private loadSavedRules(buildId: string): void {
    const first = this.filter.conditions()[0];
    this.api
      .listResolutionRules({
        buildId,
        sourceField: first?.field ?? null,
        sourceValue: first?.values[0] ?? null,
      })
      .subscribe({
        next: (rules) => this.savedRules.set(rules),
        error: () => this.savedRules.set([]),
      });
  }

  protected complete(query: string): void {
    const vocabulary = this.vocabulary();
    const needle = query.trim().toLowerCase();
    this.suggestions.set(
      (vocabulary?.values ?? []).filter((entry) =>
        entry.value.toLowerCase().includes(needle),
      ),
    );
  }

  protected onTargetValue(value: FieldValueCount | string | null): void {
    this.targetValue.set(
      value === null ? '' : typeof value === 'string' ? value : value.value,
    );
    this.preview.set(null);
  }

  protected askAdvisor(): void {
    if (this.filter.isEmpty()) {
      return;
    }
    this.advising.set(true);
    this.api
      .adviseForFilter({
        conditions: this.filter.payload(),
        target_field: this.targetField(),
      })
      .subscribe({
        next: (advice) => {
          this.advising.set(false);
          this.advice.set(advice);
          if (advice.target_value) {
            this.targetValue.set(advice.target_value);
          }
        },
        error: (err: unknown) => {
          this.advising.set(false);
          this.showFlash(
            ResolverPanel.describe(err, 'The advisor could not answer.'),
            true,
          );
        },
      });
  }

  /**
   * Names the advisor that actually replied, and why, if it was not the model.
   *
   * "No suggestion" is not a failure and must not read like one: the model
   * answered and had nothing to propose, usually because the filter leaves
   * nothing to separate the population by. Reporting that as unavailable sends
   * a reviewer hunting for a broken key.
   */
  protected advisorLabel(advice: RuleAdvice): string {
    if (advice.advisor.startsWith('llm:')) {
      return advice.advisor;
    }
    if (advice.advisor.includes('no suggestion')) {
      return 'statistical advisor — the model had nothing to suggest for this population';
    }
    if (advice.advisor.includes('rejected reply')) {
      return 'statistical advisor — the model answered outside the allowed fields';
    }
    return advice.advisor.includes('llm unavailable')
      ? 'statistical advisor — the model could not be reached'
      : 'statistical advisor (no AI key configured)';
  }

  protected runPreview(): void {
    const build = this.buildId();
    const problem = this.targetProblem();
    if (!build) {
      return;
    }
    if (problem) {
      this.showFlash(problem, true);
      return;
    }
    this.previewing.set(true);
    this.api
      .previewRule({
        build_id: build,
        conditions: this.filter.payload(),
        target_field: this.targetField(),
        target_value: this.targetValue().trim(),
      })
      .subscribe({
        next: (preview) => {
          this.preview.set(preview);
          this.previewing.set(false);
        },
        error: (err: unknown) => {
          this.previewing.set(false);
          this.showFlash(ResolverPanel.describe(err, 'The preview failed.'), true);
        },
      });
  }

  protected saveRule(run: boolean): void {
    const build = this.buildId();
    const first = this.filter.conditions()[0];
    const problem = this.targetProblem();
    if (!build || !first) {
      return;
    }
    if (problem) {
      this.showFlash(problem, true);
      return;
    }
    const author = this.requireAuthor();
    if (!author) {
      return;
    }
    this.saving.set(true);
    this.api
      .saveResolutionRule({
        build_id: build,
        source_field: first.field,
        source_value: first.values[0],
        conditions: this.filter.payload(),
        target_field: this.targetField(),
        target_value: this.targetValue().trim(),
        author,
        note: this.note().trim() || null,
      })
      .subscribe({
        next: (rule) => {
          this.saving.set(false);
          this.note.set('');
          if (!run) {
            this.showFlash(
              `Rule saved — it would resolve ${rule.would_resolve.toLocaleString()} cars when you run it.`,
              false,
            );
            this.afterChange(build);
            return;
          }
          this.appliedRows.set(0);
          this.api.applyResolutionRule(rule.rule_id, author).subscribe({
            next: () => this.follow(rule.rule_id, build),
            error: (err: unknown) => {
              this.showFlash(
                ResolverPanel.describe(err, 'The rule was saved but could not be run.'),
                true,
              );
              this.afterChange(build);
            },
          });
        },
        error: (err: unknown) => {
          this.saving.set(false);
          this.showFlash(ResolverPanel.describe(err, 'The rule could not be saved.'), true);
        },
      });
  }

  protected runSavedRule(rule: ResolutionRule): void {
    const build = this.buildId();
    const reviewer = this.requireAuthor();
    if (!build || !reviewer) {
      return;
    }
    this.busyRuleId.set(rule.rule_id);
    this.appliedRows.set(0);
    this.api.applyResolutionRule(rule.rule_id, reviewer).subscribe({
      next: () => this.follow(rule.rule_id, build),
      error: (err: unknown) => {
        this.busyRuleId.set(null);
        this.showFlash(ResolverPanel.describe(err, 'The rule could not be run.'), true);
      },
    });
  }

  protected retireSavedRule(rule: ResolutionRule): void {
    const build = this.buildId();
    const reviewer = this.requireAuthor();
    if (!build || !reviewer) {
      return;
    }
    this.busyRuleId.set(rule.rule_id);
    this.api.retireResolutionRule(rule.rule_id, reviewer).subscribe({
      next: (retired) => {
        this.busyRuleId.set(null);
        this.showFlash(
          `Retired — ${(retired.superseded_rows ?? 0).toLocaleString()} cars reopened.`,
          false,
        );
        this.afterChange(build);
      },
      error: (err: unknown) => {
        this.busyRuleId.set(null);
        this.showFlash(ResolverPanel.describe(err, 'The rule could not be retired.'), true);
      },
    });
  }

  /** Applying is a background job, so the panel reports progress rather than freezing. */
  private follow(ruleId: string, buildId: string): void {
    this.applying.set(true);
    interval(1500)
      .pipe(
        switchMap(() => this.api.ruleApplication(ruleId).pipe(catchError(() => of(null)))),
        takeWhile((application) => application?.status === 'running', true),
      )
      .subscribe((application) => {
        if (!application) {
          return;
        }
        this.appliedRows.set(application.rows_written);
        if (application.status === 'running') {
          return;
        }
        this.applying.set(false);
        this.busyRuleId.set(null);
        this.showFlash(
          application.status === 'failed'
            ? (application.error_summary ?? 'The run failed part-way through.')
            : application.rows_written
              ? `Resolved ${application.rows_written.toLocaleString()} cars.`
              : 'Nothing left to resolve — every car this rule covers already has a value.',
          application.status === 'failed',
        );
        this.afterChange(buildId);
      });
  }

  private afterChange(buildId: string): void {
    this.preview.set(null);
    this.loadSavedRules(buildId);
    this.changed.emit();
  }

  protected ruleMeta(rule: ResolutionRule): string {
    const when = new Date(rule.created_at).toLocaleString();
    if (rule.status === 'applied') {
      return `${rule.resolved_rows.toLocaleString()} cars resolved · run by ${rule.applied_by} · saved by ${rule.author}, ${when}`;
    }
    if (rule.status === 'retired') {
      return `retired by ${rule.retired_by} — no cars resolved · saved by ${rule.author}, ${when}`;
    }
    return `would resolve ${rule.would_resolve.toLocaleString()} cars · saved by ${rule.author}, ${when}`;
  }

  protected ruleSeverity(rule: ResolutionRule): 'success' | 'secondary' | 'warn' {
    if (rule.status === 'applied') {
      return 'success';
    }
    return rule.status === 'retired' ? 'secondary' : 'warn';
  }

  protected targetHint(): string {
    const vocabulary = this.vocabulary();
    if (!vocabulary) {
      return '';
    }
    return vocabulary.closed
      ? `Fixed vocabulary — must be one of ${vocabulary.values.length} canonical values.`
      : 'Open vocabulary — any value is accepted; the list shows values already in use.';
  }

  protected showFlash(text: string, bad: boolean): void {
    this.flash.set({ text, bad });
  }

  protected dismissFlash(): void {
    this.flash.set(null);
  }

  /**
   * Rules are attributed, so the panel needs a name. Remembering it is a convenience
   * only: storage throws outright where a browser blocks site data, and a throw here
   * would take the save handlers with it.
   */
  private static rememberedReviewer(): string {
    try {
      return localStorage.getItem(REVIEWER_STORAGE_KEY) ?? '';
    } catch {
      return '';
    }
  }

  private requireAuthor(): string | null {
    const name = this.author().trim();
    if (!name) {
      this.showFlash('Add your name — rules are recorded with their author.', true);
      return null;
    }
    try {
      localStorage.setItem(REVIEWER_STORAGE_KEY, name);
    } catch {
      /* not remembering the name costs a retype, nothing more */
    }
    return name;
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
