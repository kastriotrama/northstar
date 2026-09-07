import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, switchMap } from 'rxjs';
import { catchError, of } from 'rxjs';
import { interval, takeWhile } from 'rxjs';
import { AutoCompleteModule } from '@openng/optimus-ui/autocomplete';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { SelectModule } from '@openng/optimus-ui/select';
import { TagModule } from '@openng/optimus-ui/tag';
import { TextareaModule } from '@openng/optimus-ui/textarea';

import { Api } from '../../core/api';
import { FilterState } from '../../core/filter-state';
import type {
  DiscriminatorField,
  FieldValueCount,
  MatchChunkBuild,
  PopulationAttribute,
  PopulationAttributes,
  RefineResult,
  ResolutionRule,
  RuleAdvice,
  RuleCondition,
  RuleOperator,
  RulePreview,
  UnresolvedPopulation,
} from '../../core/models';

/** A clause while it is being edited. `locked` marks the population's own predicate. */
interface EditableCondition {
  field: string;
  operator: RuleOperator;
  layer: 'source' | 'normalized';
  values: string[];
  locked: boolean;
}

const OPERATORS: ReadonlyArray<{ value: RuleOperator; label: string }> = [
  { value: 'equals', label: '=' },
  { value: 'not_equals', label: '≠' },
  { value: 'starts_with', label: 'starts' },
  { value: 'contains', label: 'has' },
  { value: 'gte', label: '≥' },
  { value: 'lte', label: '≤' },
];

const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

/** Operators that compare a single number, so they can never hold OR-ed terms. */
const SINGLE_VALUE_OPERATORS: ReadonlySet<RuleOperator> = new Set<RuleOperator>(['gte', 'lte']);

/**
 * Unresolved fields: the population-first rule authoring screen.
 *
 * The unit of work is a `(source_field, source_value)` population -- every car whose
 * register value NorthStar cannot interpret -- rather than a record or a report line.
 * Pick the population with the most leverage, narrow it with conditions until nothing
 * identity-bearing still varies, then say what the field should be.
 *
 * Distinct from `/chunks`, which reviews TS-to-TecDoc match blockers.
 */
@Component({
  selector: 'ns-coverage',
  imports: [
    DecimalPipe,
    FormsModule,
    AutoCompleteModule,
    ButtonModule,
    DialogModule,
    InputTextModule,
    SelectModule,
    TagModule,
    TextareaModule,
  ],
  templateUrl: './coverage.html',
  styleUrl: './coverage.scss',
})
export class CoveragePage {
  private readonly api = inject(Api);
  private readonly handoff = inject(FilterState);

  protected readonly operators = OPERATORS;

  // --- build + worklist ----------------------------------------------------------------
  protected readonly builds = signal<MatchChunkBuild[]>([]);
  protected readonly buildId = signal<string | null>(null);
  protected readonly populations = signal<UnresolvedPopulation[]>([]);
  protected readonly populationsLoading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly flash = signal<{ text: string; bad: boolean } | null>(null);
  /** True when this screen was entered from a filter rather than the worklist. */
  protected readonly arrivedWithFilter = signal(false);

  /** Scales each worklist bar against the biggest population, so leverage is visible. */
  protected readonly maxRowCount = computed(() =>
    this.populations().reduce((max, item) => Math.max(max, item.row_count), 0),
  );

  // --- selected population -------------------------------------------------------------
  protected readonly selected = signal<UnresolvedPopulation | null>(null);
  protected readonly conditions = signal<EditableCondition[]>([]);
  protected readonly refine = signal<RefineResult | null>(null);
  protected readonly refining = signal(false);

  // --- target --------------------------------------------------------------------------
  protected readonly targetField = signal<string>('');
  protected readonly targetValue = signal<string>('');
  protected readonly vocabulary = signal<{
    closed: boolean;
    values: FieldValueCount[];
  } | null>(null);
  protected readonly targetSuggestions = signal<FieldValueCount[]>([]);

  // --- authoring -----------------------------------------------------------------------
  protected readonly author = signal(CoveragePage.rememberedReviewer());
  protected readonly note = signal('');
  protected readonly advice = signal<RuleAdvice | null>(null);
  protected readonly advising = signal(false);
  protected readonly preview = signal<RulePreview | null>(null);
  protected readonly previewing = signal(false);
  protected readonly saving = signal(false);
  protected readonly savedRules = signal<ResolutionRule[]>([]);
  protected readonly busyRuleId = signal<string | null>(null);
  protected readonly applying = signal(false);
  /** Rows the running job has written so far, so a long run visibly moves. */
  protected readonly appliedRows = signal(0);

  // --- all-attributes dialog -----------------------------------------------------------
  protected readonly attributesOpen = signal(false);
  protected readonly attributes = signal<PopulationAttributes | null>(null);
  protected readonly attributesLoading = signal(false);
  protected readonly attributeFilter = signal('');

  protected readonly visibleAttributes = computed<PopulationAttribute[]>(() => {
    const loaded = this.attributes();
    if (!loaded) {
      return [];
    }
    const needle = this.attributeFilter().trim().toLowerCase();
    if (!needle) {
      return loaded.attributes;
    }
    return loaded.attributes.filter(
      (attribute) =>
        attribute.field.toLowerCase().includes(needle) ||
        attribute.top_values.some((entry) => entry.value.toLowerCase().includes(needle)),
    );
  });

  /**
   * A rule cannot be assigned a value until the population is one coherent block, so the
   * write actions stay disabled while an identity-bearing field still varies.
   */
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

  protected readonly canWrite = computed(() => {
    const result = this.refine();
    return Boolean(result && result.would_resolve > 0 && !this.targetProblem());
  });

  /**
   * Refinement runs on a debounced stream rather than per click: chips are added in
   * bursts, and `switchMap` drops a slower earlier response so it can never overwrite a
   * newer one.
   */
  private readonly refineRequests = new Subject<void>();

  constructor() {
    this.refineRequests
      .pipe(
        debounceTime(250),
        switchMap(() => {
          const population = this.selected();
          const buildId = this.buildId();
          if (!population || !buildId || this.conditions().length === 0) {
            return of(null);
          }
          this.refining.set(true);
          return this.api
            .refineRule({
              build_id: buildId,
              source_field: population.source_field,
              source_value: population.source_value,
              conditions: this.rulePayloadConditions(),
            })
            .pipe(
              catchError((err: unknown) => {
                this.showFlash(CoveragePage.describe(err, 'Could not refine the rule.'), true);
                return of(null);
              }),
            );
        }),
        takeUntilDestroyed(),
      )
      .subscribe((result) => {
        this.refining.set(false);
        if (!result) {
          return;
        }
        this.refine.set(result);
        if (result.signature_field !== this.targetField()) {
          this.loadVocabulary(result.signature_field);
        }
      });

    this.api.listMatchChunkBuilds().subscribe({
      next: (builds) => {
        this.builds.set(builds);
        const first = builds[0]?.build_id ?? null;
        this.buildId.set(first);
        if (first) {
          this.loadPopulations(first);
          this.adoptHandoff();
        } else {
          this.error.set('No match-chunk build exists yet. Run `build-match-chunks` first.');
        }
      },
      error: (err: unknown) =>
        this.error.set(CoveragePage.describe(err, 'Could not load the build list.')),
    });
  }

  /**
   * Take up a filter the explorer handed over.
   *
   * Nothing is translated on the way in: the conditions that produced the list on TS
   * records are the conditions the rule is authored with, which is the whole point of
   * the two screens sharing one filter rather than each holding its own.
   */
  private adoptHandoff(): void {
    const conditions = this.handoff.conditions();
    const field = this.handoff.targetField();
    if (conditions.length === 0 && !field) {
      return;
    }
    this.arrivedWithFilter.set(true);
    // A handed-over population has no single (source_field, source_value) to name it, so
    // it stands on its own conditions rather than borrowing a worklist entry's identity.
    this.selected.set({
      source_field: conditions[0]?.field ?? (field ?? ''),
      source_value: conditions[0]?.values[0] ?? '',
      signature_field: field ?? '',
      row_count: 0,
    });
    this.conditions.set(
      conditions.map((condition, index) => ({ ...condition, locked: index === 0 })),
    );
    this.advice.set(null);
    this.preview.set(null);
    this.savedRules.set([]);
    if (field) {
      this.targetField.set(field);
      this.loadVocabulary(field);
    }
    this.runRefine();
    this.handoff.reset();
  }

  // --- worklist ------------------------------------------------------------------------

  protected onBuildChange(buildId: string): void {
    this.buildId.set(buildId);
    this.clearSelection();
    this.loadPopulations(buildId);
  }

  protected loadPopulations(buildId: string): void {
    this.populationsLoading.set(true);
    this.error.set(null);
    this.api.unresolvedOverview(buildId).subscribe({
      next: (overview) => {
        this.populations.set(overview.populations);
        this.populationsLoading.set(false);
      },
      error: (err: unknown) => {
        this.populations.set([]);
        this.populationsLoading.set(false);
        this.error.set(CoveragePage.describe(err, 'Could not load unresolved populations.'));
      },
    });
  }

  protected isSelected(population: UnresolvedPopulation): boolean {
    const current = this.selected();
    return (
      current?.source_field === population.source_field &&
      current?.source_value === population.source_value
    );
  }

  protected barWidth(rowCount: number): string {
    const max = this.maxRowCount();
    return `${max > 0 ? Math.max(3, (rowCount / max) * 100) : 3}%`;
  }

  protected selectPopulation(population: UnresolvedPopulation): void {
    this.arrivedWithFilter.set(false);
    this.selected.set(population);
    this.attributes.set(null);
    this.advice.set(null);
    this.preview.set(null);
    this.savedRules.set([]);
    this.refine.set(null);
    this.targetField.set('');
    this.targetValue.set('');
    this.vocabulary.set(null);
    // The population's own predicate is the rule's first clause and cannot be removed:
    // a rule that stopped testing it would no longer describe this population.
    this.conditions.set([
      {
        field: population.source_field,
        operator: 'equals',
        layer: 'source',
        values: [population.source_value],
        locked: true,
      },
    ]);
    this.runRefine();
    this.loadSavedRules();
  }

  private clearSelection(): void {
    this.selected.set(null);
    this.conditions.set([]);
    this.refine.set(null);
    this.preview.set(null);
    this.advice.set(null);
    this.savedRules.set([]);
    this.attributes.set(null);
  }

  // --- conditions ----------------------------------------------------------------------

  /** Same field clicked again means OR, not a replacement. */
  protected addTerm(field: string, value: string): void {
    const conditions = [...this.conditions()];
    const existing = conditions.find((item) => item.field === field && !item.locked);
    if (existing) {
      const next = SINGLE_VALUE_OPERATORS.has(existing.operator)
        ? [value]
        : existing.values.includes(value)
          ? existing.values
          : [...existing.values, value];
      conditions[conditions.indexOf(existing)] = { ...existing, values: next };
    } else {
      conditions.push({ field, operator: 'equals', layer: 'source', values: [value], locked: false });
    }
    this.conditions.set(conditions);
    this.preview.set(null);
    this.runRefine();
  }

  /**
   * Clicking a value the rule already covers takes it back out, so a wrong pick costs one
   * click instead of rebuilding the clause. The clause goes when its last value does.
   */
  protected removeTerm(field: string, value: string): void {
    const conditions = [...this.conditions()];
    const existing = conditions.find((item) => item.field === field && !item.locked);
    if (!existing) {
      return;
    }
    const values = existing.values.filter((item) => item !== value);
    this.conditions.set(
      values.length === 0
        ? conditions.filter((item) => item !== existing)
        : conditions.map((item) => (item === existing ? { ...item, values } : item)),
    );
    this.preview.set(null);
    this.runRefine();
  }

  protected removeCondition(condition: EditableCondition): void {
    this.conditions.set(this.conditions().filter((item) => item !== condition));
    this.preview.set(null);
    this.runRefine();
  }

  protected setOperator(condition: EditableCondition, operator: RuleOperator): void {
    this.conditions.set(
      this.conditions().map((item) =>
        item === condition
          ? {
              ...item,
              operator,
              // A numeric comparison takes exactly one value; drop any OR-ed extras.
              values: SINGLE_VALUE_OPERATORS.has(operator) ? item.values.slice(0, 1) : item.values,
            }
          : item,
      ),
    );
    this.preview.set(null);
    this.runRefine();
  }

  protected isCovered(field: DiscriminatorField, value: string): boolean {
    return field.selected_values.includes(value);
  }

  /** Values the rule covers that the counted list no longer reaches still need a way out. */
  protected unlistedSelected(field: DiscriminatorField): string[] {
    const listed = new Set(field.top_values.map((entry) => entry.value));
    return field.selected_values.filter((value) => !listed.has(value));
  }

  protected toggleValue(field: DiscriminatorField, value: string): void {
    if (this.isCovered(field, value)) {
      this.removeTerm(field.field, value);
    } else {
      this.addTerm(field.field, value);
    }
  }

  private runRefine(): void {
    this.refineRequests.next();
  }

  private rulePayloadConditions(): RuleCondition[] {
    return this.conditions().map((condition) => ({
      field: condition.field,
      operator: condition.operator,
      layer: condition.layer,
      values: condition.values,
    }));
  }

  // --- target vocabulary ---------------------------------------------------------------

  private loadVocabulary(signatureField: string): void {
    this.targetField.set(signatureField);
    this.vocabulary.set(null);
    const buildId = this.buildId();
    if (!buildId) {
      return;
    }
    this.api.targetVocabulary(buildId, signatureField).subscribe({
      next: (vocabulary) =>
        this.vocabulary.set({ closed: vocabulary.closed, values: vocabulary.values }),
      error: (err: unknown) =>
        this.showFlash(CoveragePage.describe(err, 'Could not load the target vocabulary.'), true),
    });
  }

  protected completeTarget(query: string): void {
    const vocabulary = this.vocabulary();
    if (!vocabulary) {
      this.targetSuggestions.set([]);
      return;
    }
    const needle = query.trim().toLowerCase();
    this.targetSuggestions.set(
      vocabulary.values.filter((entry) => entry.value.toLowerCase().includes(needle)),
    );
  }

  /** The autocomplete emits the whole entry on pick and a raw string on free text. */
  protected onTargetValue(value: FieldValueCount | string | null): void {
    if (value === null) {
      this.targetValue.set('');
      return;
    }
    this.targetValue.set(typeof value === 'string' ? value : value.value);
    this.preview.set(null);
  }

  protected targetHint(): string {
    const vocabulary = this.vocabulary();
    if (!vocabulary) {
      return '';
    }
    return vocabulary.closed
      ? `Fixed vocabulary — must be one of ${vocabulary.values.length} canonical values.`
      : 'Open vocabulary — any value is accepted; the list shows values already in this build.';
  }

  // --- advisor -------------------------------------------------------------------------

  protected askAdvisor(): void {
    const population = this.selected();
    const buildId = this.buildId();
    if (!population || !buildId) {
      return;
    }
    this.advising.set(true);
    this.api
      .adviseRule({
        build_id: buildId,
        source_field: population.source_field,
        source_value: population.source_value,
      })
      .subscribe({
        next: (advice) => {
          this.advising.set(false);
          this.applyAdvice(advice);
        },
        error: (err: unknown) => {
          this.advising.set(false);
          this.showFlash(CoveragePage.describe(err, 'The advisor could not answer.'), true);
        },
      });
  }

  private applyAdvice(advice: RuleAdvice): void {
    this.advice.set(advice);
    this.conditions.set(
      advice.conditions.map((condition, index) => ({
        field: condition.field,
        operator: condition.operator,
        layer: condition.layer ?? 'source',
        values:
          condition.values && condition.values.length > 0
            ? condition.values
            : condition.value
              ? [condition.value]
              : [],
        locked: index === 0,
      })),
    );
    if (advice.target_value) {
      this.targetValue.set(advice.target_value);
    }
    this.preview.set(null);
    this.runRefine();
  }

  /**
   * Names the advisor that actually answered. The button says "AI", but without a
   * configured key the deterministic statistical advisor replies instead.
   */
  protected advisorLabel(advice: RuleAdvice): string {
    if (advice.advisor.startsWith('llm:')) {
      return advice.advisor;
    }
    return advice.advisor.includes('llm unavailable')
      ? 'statistical advisor (AI unavailable, fell back)'
      : 'statistical advisor (no AI key configured)';
  }

  /**
   * Follow a started run to its end.
   *
   * Applying is a background job because a rule over the whole population takes about a
   * minute, so the screen reports progress rather than freezing on a request.
   */
  private followApplication(ruleId: string, onDone: () => void): void {
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
        if (application.status === 'failed') {
          this.showFlash(
            application.error_summary ?? 'The run failed part-way through.',
            true,
          );
        } else {
          this.showFlash(
            application.rows_written
              ? `Resolved ${application.rows_written.toLocaleString()} cars.`
              : 'Nothing left to resolve — every car this rule covers already has a value.',
            false,
          );
        }
        onDone();
      });
  }

  // --- preview, save, run --------------------------------------------------------------

  protected runPreview(): void {
    const buildId = this.buildId();
    const problem = this.targetProblem();
    if (!buildId) {
      return;
    }
    if (problem) {
      this.showFlash(problem, true);
      return;
    }
    this.previewing.set(true);
    this.api
      .previewRule({
        build_id: buildId,
        conditions: this.rulePayloadConditions(),
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
          this.showFlash(CoveragePage.describe(err, 'The preview failed.'), true);
        },
      });
  }

  protected saveRule(run: boolean): void {
    const population = this.selected();
    const buildId = this.buildId();
    if (!population || !buildId) {
      return;
    }
    const problem = this.targetProblem();
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
        build_id: buildId,
        source_field: population.source_field,
        source_value: population.source_value,
        conditions: this.rulePayloadConditions(),
        target_field: this.targetField(),
        target_value: this.targetValue().trim(),
        author,
        note: this.note().trim() || null,
      })
      .subscribe({
        next: (rule) => {
          if (!run) {
            this.saving.set(false);
            this.note.set('');
            this.showFlash(
              `Rule saved — it would resolve ${rule.would_resolve.toLocaleString()} cars when you run it.`,
              false,
            );
            this.afterRuleChange();
            return;
          }
          this.api.applyResolutionRule(rule.rule_id, author).subscribe({
            next: () => {
              this.saving.set(false);
              this.note.set('');
              this.appliedRows.set(0);
              this.followApplication(rule.rule_id, () => this.afterRuleChange());
            },
            error: (err: unknown) => {
              this.saving.set(false);
              this.showFlash(
                CoveragePage.describe(err, 'The rule was saved but could not be run.'),
                true,
              );
              this.afterRuleChange();
            },
          });
        },
        error: (err: unknown) => {
          this.saving.set(false);
          this.showFlash(CoveragePage.describe(err, 'The rule could not be saved.'), true);
        },
      });
  }

  protected runSavedRule(rule: ResolutionRule): void {
    const reviewer = this.requireAuthor();
    if (!reviewer) {
      return;
    }
    this.busyRuleId.set(rule.rule_id);
    this.appliedRows.set(0);
    this.api.applyResolutionRule(rule.rule_id, reviewer).subscribe({
      next: () => this.followApplication(rule.rule_id, () => this.afterRuleChange()),
      error: (err: unknown) => {
        this.busyRuleId.set(null);
        this.showFlash(CoveragePage.describe(err, 'The rule could not be run.'), true);
      },
    });
  }

  protected retireSavedRule(rule: ResolutionRule): void {
    const reviewer = this.requireAuthor();
    if (!reviewer) {
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
        this.afterRuleChange();
      },
      error: (err: unknown) => {
        this.busyRuleId.set(null);
        this.showFlash(CoveragePage.describe(err, 'The rule could not be retired.'), true);
      },
    });
  }

  /**
   * Running a rule moves the very numbers this screen is made of, so the counts, the
   * facets and the worklist are all re-read rather than left stale.
   */
  private afterRuleChange(): void {
    this.preview.set(null);
    this.loadSavedRules();
    this.runRefine();
    const buildId = this.buildId();
    if (buildId) {
      this.loadPopulations(buildId);
    }
  }

  private loadSavedRules(): void {
    const population = this.selected();
    const buildId = this.buildId();
    if (!population || !buildId) {
      return;
    }
    this.api
      .listResolutionRules({
        buildId,
        sourceField: population.source_field,
        sourceValue: population.source_value,
      })
      .subscribe({
        next: (rules) => this.savedRules.set(rules),
        error: (err: unknown) =>
          this.showFlash(CoveragePage.describe(err, 'Could not load saved rules.'), true),
      });
  }

  protected ruleStatement(rule: ResolutionRule): string {
    const clauses = rule.conditions.map((condition) => {
      const operator =
        OPERATORS.find((entry) => entry.value === condition.operator)?.label ?? condition.operator;
      const values =
        condition.values && condition.values.length > 0
          ? condition.values
          : condition.value
            ? [condition.value]
            : [];
      return `${condition.field} ${operator} ${values.join(' or ')}`;
    });
    return `IF ${clauses.join(' AND ')} THEN ${rule.target_field} = ${rule.target_value}`;
  }

  protected ruleMeta(rule: ResolutionRule): string {
    const when = new Date(rule.created_at).toLocaleString();
    if (rule.status === 'applied') {
      return (
        `${rule.resolved_rows.toLocaleString()} cars resolved · run by ${rule.applied_by} · ` +
        `saved by ${rule.author}, ${when}`
      );
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

  // --- all-attributes dialog -----------------------------------------------------------

  protected openAttributes(): void {
    const population = this.selected();
    const buildId = this.buildId();
    if (!population || !buildId) {
      return;
    }
    this.attributesOpen.set(true);
    if (this.attributes()) {
      return;
    }
    this.attributesLoading.set(true);
    this.api
      .unresolvedAttributes({
        buildId,
        sourceField: population.source_field,
        sourceValue: population.source_value,
      })
      .subscribe({
        next: (attributes) => {
          this.attributes.set(attributes);
          this.attributesLoading.set(false);
        },
        error: (err: unknown) => {
          this.attributesLoading.set(false);
          this.showFlash(CoveragePage.describe(err, 'Could not scan the population.'), true);
        },
      });
  }

  protected pickAttributeValue(field: string, value: string): void {
    this.addTerm(field, value);
    this.attributesOpen.set(false);
  }

  // --- reviewer identity ---------------------------------------------------------------

  /**
   * Rules are attributed, so the screen needs a name. It is remembered per browser as a
   * convenience only: storage throws outright where a browser blocks site data, and a
   * throw here would take the save handlers with it.
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

  // --- messaging -----------------------------------------------------------------------

  protected showFlash(text: string, bad: boolean): void {
    this.flash.set({ text, bad });
  }

  protected dismissFlash(): void {
    this.flash.set(null);
  }

  private static describe(error: unknown, fallback: string): string {
    const detail = (error as { error?: { detail?: unknown } } | null)?.error?.detail;
    return typeof detail === 'string' && detail.length > 0 ? detail : fallback;
  }
}
