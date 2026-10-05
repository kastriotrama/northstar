import { DatePipe, DecimalPipe, PercentPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { catchError, map, of, startWith, switchMap, timer } from 'rxjs';

import { Api } from '../core/api';
import { describeReason, fieldName } from '../core/match-reasons';
import { MATCH_RESULT_STATES } from '../core/match-result-states';
import type {
  MatchResultCause,
  MatchResultCounts,
  MatchResultOverview,
  MatchResultState,
  VehicleCondition,
} from '../core/models';

/** How often the counts are read again while the server matches the changed cars. */
const REFRESH_POLL_MS = 4000;

interface Loaded<T> {
  loading: boolean;
  error: string | null;
  value: T | null;
}

/**
 * Where the cars of the Vehicles filter stand with matching, above the car list.
 *
 * Read from stored results: nothing here runs the matcher. Each state is a
 * button that narrows the list to its cars; the breakdown below names what
 * stands between the open cars and one KType, and each of its counts narrows
 * the list to exactly those cars. The breakdown is read only when opened.
 */
@Component({
  selector: 'ns-match-results',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe, PercentPipe],
  template: `
    @if (error(); as text) {
      <p class="error" role="alert">{{ text }}</p>
    }

    @if (counts(); as c) {
      <div class="states" role="group" aria-label="Matching result">
        @for (item of states; track item.key) {
          @if (countOf(c, item.key) || item.key === selected() || always(item.key)) {
            <button
              type="button"
              class="state state--{{ item.key }}"
              [class.state--on]="selected() === item.key"
              [attr.aria-pressed]="selected() === item.key"
              [title]="item.hint + (selected() === item.key ? ' — click to show all cars' : ' — click to list these cars')"
              [disabled]="!countOf(c, item.key) && selected() !== item.key"
              (click)="pick(item.key)"
            >
              <span class="state__label">{{ item.label }}</span>
              <span class="state__value">{{ countOf(c, item.key) | number }}</span>
              @if (shareOf(c, item.key); as share) {
                <span class="muted">{{ share | percent: '1.0-1' }}</span>
              }
            </button>
          }
        }
      </div>

      @if (countOf(c, 'not_evaluated') || c.changed_since_matched) {
        <p class="fresh" role="status">
          @if (countOf(c, 'not_evaluated')) {
            {{ countOf(c, 'not_evaluated') | number }} cars have no stored result yet; the
            percentages are of the {{ c.total - countOf(c, 'not_evaluated') | number }} cars that
            have one.
          }
          @if (c.changed_since_matched) {
            {{ c.changed_since_matched | number }} cars changed after they were matched; their
            stored result may be out of date.
            @if (c.refreshing || asked()) {
              <span class="working">Matching them again… this number falls as it goes.</span>
            } @else {
              <button type="button" class="again" (click)="matchAgain()">Match them again now</button>
            }
          }
        </p>
        @if (refreshError(); as text) {
          <p class="error" role="alert">{{ text }}</p>
        }
      }

      <details class="why" (toggle)="onToggle($event)">
        <summary>What stands between the open cars and one KType</summary>
        @if (overviewError(); as text) {
          <p class="error" role="alert">{{ text }}</p>
        }
        @if (overview(); as o) {
          <div class="gaps">
            <section class="gap">
              <h4>Several KTypes — what would decide</h4>
              @if (o.several_separating_fields.length) {
                <table>
                  <thead>
                    <tr><th>Field</th><th class="num">Separates</th><th class="num">Car lacks it</th></tr>
                  </thead>
                  <tbody>
                    @for (item of o.several_separating_fields; track item.field) {
                      <tr>
                        <td>{{ name(item.field) }}</td>
                        <td class="num">
                          <button
                            type="button"
                            class="link"
                            [attr.aria-label]="'Cars whose KTypes differ on ' + name(item.field)"
                            (click)="narrow('several', 'match_separating_field', item.field, 'the KTypes differ on ' + name(item.field))"
                          >
                            {{ item.cars | number }}
                          </button>
                        </td>
                        <td class="num strong">
                          @if (lacks(o, item.field); as count) {
                            <button
                              type="button"
                              class="link"
                              [attr.aria-label]="'Cars with no ' + name(item.field)"
                              (click)="narrow('several', 'match_missing_field', item.field, 'the car has no ' + name(item.field))"
                            >
                              {{ count | number }}
                            </button>
                          } @else {
                            0
                          }
                        </td>
                      </tr>
                    }
                  </tbody>
                </table>
                <p class="muted note">
                  Possible KTypes per car:
                  @for (entry of entries(o.several_candidate_counts); track entry[0]) {
                    <button
                      type="button"
                      class="pill"
                      [attr.aria-label]="'Cars with ' + entry[0] + ' possible KTypes'"
                      (click)="narrow('several', 'match_candidate_count', countValue(entry[0]), entry[0] + ' KTypes are possible')"
                    >
                      {{ entry[0] }}: {{ entry[1] | number }}
                    </button>
                  }
                </p>
              } @else {
                <p class="muted">None.</p>
              }
            </section>

            <section class="gap">
              <h4>No KType — what conflicts</h4>
              @if (o.none_conflicting_fields.length || o.none_without_candidates) {
                <table>
                  <thead><tr><th>Field</th><th class="num">Cars</th></tr></thead>
                  <tbody>
                    @for (item of o.none_conflicting_fields; track item.field) {
                      <tr>
                        <td>{{ name(item.field) }}</td>
                        <td class="num">
                          <button
                            type="button"
                            class="link"
                            [attr.aria-label]="'Cars conflicting on ' + name(item.field)"
                            (click)="narrow('none', 'match_conflicting_field', item.field, 'the car conflicts on ' + name(item.field))"
                          >
                            {{ item.cars | number }}
                          </button>
                        </td>
                      </tr>
                    }
                    @if (o.none_without_candidates) {
                      <tr>
                        <td class="muted">no candidate above threshold</td>
                        <td class="num">{{ o.none_without_candidates | number }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              } @else {
                <p class="muted">None.</p>
              }
            </section>

            <section class="gap">
              <h4>Not matchable — why</h4>
              @for (item of o.not_matchable_reasons; track item.reason) {
                <div class="reason">
                  <span [title]="item.reason">{{ reason(item.reason) }}</span>
                  <button
                    type="button"
                    class="link"
                    [attr.aria-label]="'Cars not matchable because: ' + item.reason"
                    (click)="narrow('not_matchable', 'match_reason', item.reason, reason(item.reason))"
                  >
                    {{ item.cars | number }}
                  </button>
                </div>
              } @empty {
                <p class="muted">None.</p>
              }
            </section>

            <section class="gap">
              <h4>Matcher outcome</h4>
              @for (item of o.terminals; track item.value) {
                <div class="reason">
                  <span>{{ item.value.replace('_', ' ') }}</span><span>{{ item.cars | number }}</span>
                </div>
              } @empty {
                <p class="muted">No stored results in this filter.</p>
              }
            </section>
          </div>

          <p class="muted note">
            Read from stored results; the matcher is not run here.
            @if (o.latest_run; as run) {
              Last run: {{ run.mode }}, {{ run.status }},
              {{ run.evaluated | number }} matched and {{ run.unchanged | number }} unchanged of
              {{ run.target | number }}, started {{ run.started_at | date: 'yyyy-MM-dd HH:mm' }}.
            }
            @for (batch of o.catalog_batches; track batch.value) {
              Catalog <span class="mono">{{ batch.value }}</span> ({{ batch.cars | number }}).
            }
            @if (o.matcher_versions.length > 1) {
              Results come from {{ o.matcher_versions.length }} matcher versions; a rebuild brings
              them to one.
            }
          </p>
        } @else if (overviewLoading()) {
          <p class="muted">Loading…</p>
        }
      </details>
    } @else if (loading()) {
      <p class="muted">Loading matching results…</p>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.5rem; font-size: 0.84rem; margin-bottom: 0.7rem; }
    .states { display: flex; flex-wrap: wrap; gap: 0.4rem; }
    .state {
      display: flex; align-items: baseline; gap: 0.4rem; font: inherit; font-size: 0.8rem;
      padding: 0.3rem 0.65rem; border-radius: 8px; cursor: pointer; color: inherit;
      border: 1px solid var(--p-surface-200); border-left: 4px solid var(--p-surface-400);
      background: var(--p-surface-0);
    }
    .state:hover:not(:disabled) { background: var(--p-surface-50); }
    .state:disabled { cursor: default; opacity: 0.6; }
    .state--on { background: var(--p-surface-100); border-color: var(--p-surface-500); font-weight: 650; }
    .state__label { font-weight: 600; }
    .state__value { font-weight: 700; font-size: 0.95rem; }
    .state--resolved, .state--chosen { border-left-color: #2e8b57; }
    .state--several, .state--one_unconfirmed { border-left-color: #d99a00; }
    .state--none, .state--chosen_none { border-left-color: #c0392b; }
    .fresh { margin: 0; font-size: 0.78rem; color: #7a5300; }
    .again { margin-left: 0.3rem; font: inherit; cursor: pointer; }
    .working { margin-left: 0.3rem; font-style: italic; }
    .why > summary { cursor: pointer; font-weight: 600; font-size: 0.8rem; }
    .gaps { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.8rem; margin-top: 0.5rem; }
    @media (max-width: 900px) { .gaps { grid-template-columns: 1fr; } }
    .gap { border: 1px solid var(--p-surface-200); border-radius: 8px; padding: 0.6rem 0.7rem; }
    h4 { margin: 0 0 0.4rem; font-size: 0.82rem; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; font-weight: 600; color: var(--p-text-muted-color); font-size: 0.74rem; }
    td, th { padding: 0.2rem 0.3rem; border-bottom: 1px solid var(--p-surface-100); }
    .num { text-align: right; }
    .strong { font-weight: 700; }
    .reason { display: flex; justify-content: space-between; gap: 0.6rem; padding: 0.15rem 0; }
    .link {
      border: 0; background: none; padding: 0; font: inherit; font-weight: inherit; cursor: pointer;
      color: var(--p-primary-color, #2563eb); text-decoration: underline;
    }
    .pill {
      margin-left: 0.3rem; padding: 0.05rem 0.4rem; border-radius: 999px; border: 0; font: inherit;
      background: var(--p-surface-100); color: inherit; cursor: pointer;
    }
    .pill:hover { background: var(--p-surface-200); }
    .note { margin: 0.5rem 0 0; font-size: 0.74rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class MatchResults {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);
  private looking = false;
  private wasWorking = false;

  /** The Vehicles filter without its matching clauses: the counts are of all states. */
  readonly conditions = input.required<VehicleCondition[]>();
  readonly text = input.required<string>();
  /** The state the car list is narrowed to; '' when it shows every state. */
  readonly selected = input<MatchResultState | ''>('');
  /** Bumped by the page when a car was decided or corrected: the counts are read again. */
  readonly turn = input(0);
  /** A state was picked ('' to show all again). */
  readonly stateChange = output<MatchResultState | ''>();
  /** The server finished matching the changed cars again: what is on screen may be older. */
  readonly refreshed = output<void>();
  /** A count of the breakdown was picked: narrow the list to those cars. */
  readonly causeChange = output<MatchResultCause>();

  protected readonly states = MATCH_RESULT_STATES;
  private readonly opened = signal(false);
  /** "Match them again now" was pressed and the server has not said it is done. */
  protected readonly asked = signal(false);
  protected readonly refreshError = signal<string | null>(null);
  /** Bumped to read the counts again while a refresh is running. */
  private readonly tick = signal(0);
  private readonly countsState = signal<Loaded<MatchResultCounts>>({
    loading: true,
    error: null,
    value: null,
  });
  private readonly overviewState = signal<Loaded<MatchResultOverview>>({
    loading: false,
    error: null,
    value: null,
  });
  protected readonly counts = computed(() => this.countsState().value);
  protected readonly loading = computed(() => this.countsState().loading);
  protected readonly error = computed(() => this.countsState().error);
  protected readonly overview = computed(() => this.overviewState().value);
  protected readonly overviewLoading = computed(() => this.overviewState().loading);
  protected readonly overviewError = computed(() => this.overviewState().error);

  constructor() {
    const filter = computed(() => ({
      conditions: this.conditions(),
      text: this.text(),
      turn: this.turn(),
      tick: this.tick(),
    }));
    // The numbers on screen stay until the new ones arrive.
    toObservable(filter)
      .pipe(
        switchMap(({ conditions, text }) =>
          this.api.matchResultCounts({ conditions, text }).pipe(
            map((value): Loaded<MatchResultCounts> => ({ loading: false, error: null, value })),
            catchError(() =>
              of<Loaded<MatchResultCounts>>({
                loading: false,
                error: 'Could not load the matching results.',
                value: this.countsState().value,
              }),
            ),
            startWith<Loaded<MatchResultCounts>>({
              loading: true,
              error: null,
              value: this.countsState().value,
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((next) => {
        this.countsState.set(next);
        if (next.loading || !next.value) return;
        // While the server is matching the changed cars again, look again in a moment.
        const working = !!next.value.refreshing && next.value.changed_since_matched > 0;
        if (working) {
          this.wasWorking = true;
          this.lookAgain();
          return;
        }
        this.asked.set(false);
        if (this.wasWorking) {
          // The cars were matched again: the list's rows are older than their results now.
          this.wasWorking = false;
          this.refreshed.emit();
        }
      });

    // The breakdown costs more than the counts: read only while it is open.
    const breakdown = computed(() => (this.opened() ? filter() : null));
    toObservable(breakdown)
      .pipe(
        switchMap((request) => {
          if (request === null) return of(this.overviewState());
          return this.api
            .matchResultOverview({ conditions: request.conditions, text: request.text })
            .pipe(
              map((value): Loaded<MatchResultOverview> => ({ loading: false, error: null, value })),
              catchError(() =>
                of<Loaded<MatchResultOverview>>({
                  loading: false,
                  error: 'Could not load the breakdown.',
                  value: this.overviewState().value,
                }),
              ),
              startWith<Loaded<MatchResultOverview>>({
                loading: true,
                error: null,
                value: this.overviewState().value,
              }),
            );
        }),
        takeUntilDestroyed(),
      )
      .subscribe((next) => this.overviewState.set(next));
  }

  /** Have the server match the changed cars again; the counts follow as it works. */
  protected matchAgain(): void {
    this.refreshError.set(null);
    this.asked.set(true);
    this.api
      .refreshMatchResults()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.lookAgain(),
        error: () => {
          this.asked.set(false);
          this.refreshError.set('Could not start matching them again.');
        },
      });
  }

  private lookAgain(): void {
    if (this.looking) return;
    this.looking = true;
    timer(REFRESH_POLL_MS)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.looking = false;
        this.tick.update((value) => value + 1);
      });
  }

  protected onToggle(event: Event): void {
    this.opened.set((event.target as HTMLDetailsElement).open);
  }

  /** Picking the state already selected shows every state again. */
  protected pick(state: MatchResultState): void {
    this.stateChange.emit(this.selected() === state ? '' : state);
  }

  protected narrow(state: MatchResultState, field: string, value: string, label: string): void {
    this.causeChange.emit({ state, field, value, label });
  }

  /** The matcher's own states are always shown, a zero included; the others only when used. */
  protected always(state: MatchResultState): boolean {
    return !['chosen', 'chosen_none', 'not_evaluated'].includes(state);
  }

  protected countOf(counts: MatchResultCounts, state: MatchResultState): number {
    return counts.states.find((item) => item.state === state)?.cars ?? 0;
  }

  /**
   * A state's share of the cars that have a result, so a fill that is still
   * running reads as it will when it is done; `not_evaluated` is a share of all.
   */
  protected shareOf(counts: MatchResultCounts, state: MatchResultState): number | null {
    const pending = this.countOf(counts, 'not_evaluated');
    const base = state === 'not_evaluated' ? counts.total : counts.total - pending;
    return base > 0 ? this.countOf(counts, state) / base : null;
  }

  protected lacks(overview: MatchResultOverview, field: string): number {
    return overview.several_missing_fields.find((item) => item.field === field)?.cars ?? 0;
  }

  /** "5+" is the matcher's cap: the stored count of such a car is the cap itself. */
  protected countValue(label: string): string {
    return label.replace('+', '');
  }

  protected name(field: string): string {
    return fieldName(field);
  }

  protected reason(code: string): string {
    return describeReason(code) ?? code;
  }

  protected entries(counts: Record<string, number>): Array<[string, number]> {
    return Object.entries(counts);
  }
}
