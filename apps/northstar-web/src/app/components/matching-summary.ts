import { DecimalPipe, PercentPipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, timer } from 'rxjs';
import { switchMap, takeWhile } from 'rxjs/operators';
import { ButtonModule } from '@openng/optimus-ui/button';
import { InputTextModule } from '@openng/optimus-ui/inputtext';

import { Api } from '../core/api';
import type {
  MatchBucket,
  MatchSummaryJob,
  RuleCondition,
} from '../core/models';

const BUCKETS: ReadonlyArray<{ key: MatchBucket; label: string; hint: string }> = [
  { key: 'one', label: 'One KType', hint: 'exactly one compatible candidate' },
  { key: 'several', label: 'Several KTypes', hint: 'two or more fit — ambiguous' },
  { key: 'none', label: 'No KType', hint: 'every candidate conflicts, or none found' },
  { key: 'not_matchable', label: 'Not matchable', hint: 'stopped before matching' },
];

/**
 * Runs the real matcher over the Vehicles filter and says where it ends.
 *
 * A background job on the server -- the matcher spends ~0.1s a car -- polled
 * here, with counts filling in as it runs. The gap sections answer "why": the
 * field `none` cars conflict on, and the field that would separate `several`.
 */
@Component({
  selector: 'ns-matching-summary',
  imports: [DecimalPipe, PercentPipe, FormsModule, ButtonModule, InputTextModule],
  template: `
    <div class="controls">
      <label class="controls__limit">
        Evaluate the first
        <input
          pInputText
          type="number"
          min="1"
          max="20000"
          aria-label="Cars to evaluate"
          [ngModel]="limit()"
          (ngModelChange)="limit.set(+$event)"
        />
        cars of this filter
      </label>
      @if (running()) {
        <p-button label="Cancel" size="small" [outlined]="true" (onClick)="cancel()" />
      } @else {
        <p-button label="Run matching" size="small" (onClick)="run()" />
      }
      @if (stale()) {
        <span class="stale">The filter has changed since this run.</span>
      }
    </div>

    @if (error(); as text) {
      <p class="error" role="alert">{{ text }}</p>
    }

    @if (job(); as current) {
      <div class="progress">
        <div class="progress__track">
          <div class="progress__fill" [style.width.%]="progressPct()"></div>
        </div>
        <span class="muted">
          @switch (current.status) {
            @case ('running') { Matching… }
            @case ('done') { Done. }
            @case ('cancelled') { Cancelled — counts so far kept. }
            @case ('failed') { Failed: {{ current.error }} }
          }
          {{ current.evaluated | number }} of {{ current.target | number }} evaluated
          · {{ current.seconds_elapsed | number: '1.0-0' }}s
          @if (current.summary.sampled) {
            · the filter matches {{ current.summary.population | number }}; these are the first
            {{ current.target | number }} by record id, not a random sample
          }
        </span>
      </div>

      @if (current.summary; as s) {
        <div class="buckets">
          @for (bucket of buckets; track bucket.key) {
            <div class="tile tile--{{ bucket.key }}" [title]="bucket.hint">
              <span class="tile__label">{{ bucket.label }}</span>
              <span class="tile__value">{{ s.buckets[bucket.key] | number }}</span>
              <span class="muted">
                @if (s.evaluated) {
                  {{ s.buckets[bucket.key] / s.evaluated | percent: '1.0-1' }}
                }
              </span>
            </div>
          }
        </div>

        <div class="gaps">
          <section class="gap">
            <h4>Several KTypes — what would decide</h4>
            @if (s.several_separating_fields.length) {
              <table>
                <thead>
                  <tr><th>Field</th><th class="num">Separates</th><th class="num">Car lacks it</th></tr>
                </thead>
                <tbody>
                  @for (item of s.several_separating_fields; track item.field) {
                    <tr>
                      <td>{{ item.field }}</td>
                      <td class="num">{{ item.cars | number }}</td>
                      <td class="num strong">{{ lacks(item.field) | number }}</td>
                    </tr>
                  }
                </tbody>
              </table>
              <p class="muted note">
                Candidates per car:
                @for (entry of countEntries(s.several_candidate_counts); track entry[0]) {
                  <span class="pill">{{ entry[0] }}: {{ entry[1] | number }}</span>
                }
              </p>
            } @else {
              <p class="muted">None so far.</p>
            }
          </section>

          <section class="gap">
            <h4>No KType — what conflicts</h4>
            @if (s.none_conflicting_fields.length || s.none_without_candidates) {
              <table>
                <thead><tr><th>Field</th><th class="num">Cars</th></tr></thead>
                <tbody>
                  @for (item of s.none_conflicting_fields; track item.field) {
                    <tr><td>{{ item.field }}</td><td class="num">{{ item.cars | number }}</td></tr>
                  }
                  @if (s.none_without_candidates) {
                    <tr>
                      <td class="muted">no candidate above threshold</td>
                      <td class="num">{{ s.none_without_candidates | number }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            } @else {
              <p class="muted">None so far.</p>
            }
          </section>

          <section class="gap">
            <h4>Not matchable — why</h4>
            @for (item of s.not_matchable_reasons; track item.reason) {
              <div class="reason">
                <span class="mono">{{ item.reason }}</span><span>{{ item.cars | number }}</span>
              </div>
            } @empty {
              <p class="muted">None so far.</p>
            }
          </section>

          <section class="gap">
            <h4>Pipeline outcome</h4>
            @for (entry of countEntries(s.terminals); track entry[0]) {
              <div class="reason"><span>{{ entry[0] }}</span><span>{{ entry[1] | number }}</span></div>
            }
          </section>
        </div>

        <section class="examples">
          <h4>Examples <span class="muted">— click to open the car</span></h4>
          @for (bucket of buckets; track bucket.key) {
            @if (s.examples[bucket.key].length) {
              <div class="examples__row">
                <span class="examples__label">{{ bucket.label }}</span>
                @for (example of s.examples[bucket.key]; track example.source_record_id) {
                  <button type="button" class="example" (click)="pick.emit(example.plate ?? '')">
                    <span class="mono">{{ example.plate }}</span>
                    {{ example.manufacturer }} {{ example.model_family }}
                  </button>
                }
              </div>
            }
          }
        </section>

        <p class="muted note">Catalog: <span class="mono">{{ s.catalog_batch }}</span></p>
      }
    } @else {
      <p class="muted">
        Runs the TS-to-TecDoc matcher over the cars this filter shows and counts how many match
        exactly one KType, several, or none — and which field makes the difference. About 0.1s
        a car, so a couple of thousand take a few minutes; counts fill in as it runs.
      </p>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.8rem; font-size: 0.84rem; }
    /* Not .bar: that is the app-wide coverage bar in styles.scss (7px tall, clipped). */
    .controls { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; }
    .controls__limit { display: flex; align-items: center; gap: 0.4rem; }
    .controls__limit input { width: 90px; }
    .stale { color: #7a5300; font-size: 0.78rem; }
    .progress { display: flex; flex-direction: column; gap: 0.3rem; }
    .progress__track { height: 6px; border-radius: 3px; background: var(--p-surface-200); overflow: hidden; }
    .progress__fill { height: 100%; background: var(--p-primary-color, #3b82f6); transition: width 0.4s; }
    .buckets { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.6rem; }
    @media (max-width: 700px) { .buckets { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    .tile {
      display: flex; flex-direction: column; gap: 0.1rem;
      padding: 0.6rem 0.7rem; border-radius: 8px; border: 1px solid var(--p-surface-200);
    }
    .tile__label { font-size: 0.74rem; font-weight: 600; }
    .tile__value { font-size: 1.4rem; font-weight: 700; }
    .tile--one { border-left: 4px solid #2e8b57; }
    .tile--several { border-left: 4px solid #d99a00; }
    .tile--none { border-left: 4px solid #c0392b; }
    .tile--not_matchable { border-left: 4px solid var(--p-surface-400); }
    .gaps { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.8rem; }
    @media (max-width: 900px) { .gaps { grid-template-columns: 1fr; } }
    .gap { border: 1px solid var(--p-surface-200); border-radius: 8px; padding: 0.6rem 0.7rem; }
    h4 { margin: 0 0 0.4rem; font-size: 0.82rem; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; font-weight: 600; color: var(--p-text-muted-color); font-size: 0.74rem; }
    td, th { padding: 0.2rem 0.3rem; border-bottom: 1px solid var(--p-surface-100); }
    .num { text-align: right; }
    .strong { font-weight: 700; }
    .reason { display: flex; justify-content: space-between; gap: 0.6rem; padding: 0.15rem 0; }
    .pill { margin-left: 0.3rem; padding: 0.05rem 0.4rem; border-radius: 999px; background: var(--p-surface-100); }
    .examples__row { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.3rem; }
    .examples__label { min-width: 110px; font-weight: 600; font-size: 0.76rem; }
    .example {
      border: 1px solid var(--p-surface-300); border-radius: 6px; background: var(--p-surface-0);
      padding: 0.15rem 0.45rem; cursor: pointer; font: inherit; font-size: 0.76rem;
    }
    .example:hover { background: var(--p-surface-100); }
    .note { margin: 0; font-size: 0.74rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class MatchingSummary {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  /** The Vehicles filter, exactly as the car list is queried with it. */
  readonly conditions = input.required<RuleCondition[]>();
  readonly text = input.required<string>();
  /** A car picked from the examples: its plate. */
  readonly pick = output<string>();

  protected readonly buckets = BUCKETS;
  protected readonly limit = signal(2000);
  protected readonly job = signal<MatchSummaryJob | null>(null);
  protected readonly error = signal<string | null>(null);
  private readonly ranWith = signal<string | null>(null);
  private polling: Subscription | null = null;

  protected readonly running = computed(() => this.job()?.status === 'running');
  protected readonly progressPct = computed(() => {
    const current = this.job();
    return current && current.target ? (100 * current.evaluated) / current.target : 0;
  });
  /** A result describes the filter it ran on; say so once that is no longer on screen. */
  protected readonly stale = computed(() => {
    const ran = this.ranWith();
    return ran !== null && ran !== this.requestKey();
  });

  constructor() {
    this.destroyRef.onDestroy(() => this.polling?.unsubscribe());
  }

  protected run(): void {
    this.error.set(null);
    const limit = Math.min(Math.max(Math.round(this.limit() || 1), 1), 20000);
    this.limit.set(limit);
    this.api
      .startMatchSummary({ conditions: this.conditions(), text: this.text(), limit })
      .subscribe({
        next: (started) => {
          this.job.set(started);
          this.ranWith.set(this.requestKey());
          this.poll(started.job_id);
        },
        error: (err: { error?: { detail?: string } }) =>
          this.error.set(err?.error?.detail ?? 'Could not start matching.'),
      });
  }

  protected cancel(): void {
    const current = this.job();
    if (!current) return;
    this.api.cancelMatchSummary(current.job_id).subscribe({
      next: (job) => this.job.set(job),
      error: () => this.error.set('Could not cancel matching.'),
    });
  }

  /** Count by field name for the "Car lacks it" column. */
  protected lacks(field: string): number {
    return (
      this.job()?.summary.several_missing_separating_fields.find((item) => item.field === field)
        ?.cars ?? 0
    );
  }

  protected countEntries(counts: Record<string, number>): Array<[string, number]> {
    return Object.entries(counts);
  }

  private poll(jobId: string): void {
    this.polling?.unsubscribe();
    this.polling = timer(1500, 2000)
      .pipe(
        switchMap(() => this.api.matchSummaryJob(jobId)),
        // Emit the settling snapshot too, then stop.
        takeWhile((job) => job.status === 'running', true),
      )
      .subscribe({
        next: (job) => this.job.set(job),
        error: () => this.error.set('Lost track of the matching job — the API may have restarted.'),
      });
  }

  private requestKey(): string {
    return JSON.stringify({ conditions: this.conditions(), text: this.text() });
  }
}
