import { DatePipe, DecimalPipe, PercentPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { Subject, catchError, combineLatest, map, of, startWith, switchMap } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';

import { Api } from '../core/api';
import { describeReason, fieldName } from '../core/match-reasons';
import { MATCH_RESULT_STATES } from '../core/match-result-states';
import type {
  MatchResultNarrowing,
  MatchResultOverview,
  MatchResultState,
  VehicleCondition,
} from '../core/models';
import { MatchResultCarsDialog } from './match-result-cars-dialog';

interface OverviewState {
  loading: boolean;
  error: string | null;
  overview: MatchResultOverview | null;
}

/**
 * Matching statistics of the Vehicles filter, read from stored results.
 *
 * Nothing here runs the matcher: the counts come from
 * `core.vehicle_match_results`, so they cover every car of the filter at once
 * and follow the filter as it changes. Every number opens the cars behind it.
 */
@Component({
  selector: 'ns-match-results',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe, PercentPipe, ButtonModule, MatchResultCarsDialog],
  template: `
    <div class="head">
      <span class="head__total">
        @if (overview(); as o) {
          {{ o.total | number }} cars in this filter
        } @else {
          Matching results
        }
      </span>
      <p-button label="Refresh" size="small" [text]="true" [disabled]="loading()" (onClick)="reload()" />
      @if (loading()) {
        <span class="muted">Loading…</span>
      }
    </div>

    @if (error(); as text) {
      <p class="error" role="alert">{{ text }}</p>
    }

    @if (overview(); as o) {
      <div class="tiles">
        @for (item of states; track item.key) {
          @if (item.key !== 'chosen_none' || countOf(o, item.key)) {
            <button
              type="button"
              class="tile tile--{{ item.key }}"
              [title]="item.hint + ' — click to see the cars'"
              [disabled]="!countOf(o, item.key)"
              (click)="openCars(item.key)"
            >
              <span class="tile__label">{{ item.label }}</span>
              <span class="tile__value">{{ countOf(o, item.key) | number }}</span>
              <span class="muted">
                @if (shareOf(o, item.key); as share) {
                  {{ share | percent: '1.0-1' }}
                }
                · see cars
              </span>
            </button>
          }
        }
      </div>

      @if (countOf(o, 'not_evaluated') || o.changed_since_matched) {
        <p class="fresh fresh--warn" role="status">
          @if (countOf(o, 'not_evaluated')) {
            {{ countOf(o, 'not_evaluated') | number }} cars have no stored result yet; the
            percentages are of the {{ o.total - countOf(o, 'not_evaluated') | number }} cars that
            have one.
          }
          @if (o.changed_since_matched) {
            {{ o.changed_since_matched | number }} cars changed after they were matched; their
            stored result may be out of date until the next refresh run.
          }
        </p>
      }

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
                        (click)="openCars('several', { separating_field: item.field })"
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
                          (click)="openCars('several', { missing_field: item.field })"
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
                <span class="pill">{{ entry[0] }}: {{ entry[1] | number }}</span>
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
                        (click)="openCars('none', { conflicting_field: item.field })"
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
                (click)="openCars('not_matchable', { reason: item.reason })"
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
          Results come from {{ o.matcher_versions.length }} matcher versions; a rebuild brings them
          to one.
        }
      </p>

      <ns-match-result-cars-dialog
        [conditions]="conditions()"
        [text]="text()"
        [overview]="o"
        [(visible)]="carsOpen"
        [(state)]="carsState"
        [(narrowing)]="carsNarrowing"
        (pick)="pick.emit($event)"
        (changed)="reload()"
      />
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.8rem; font-size: 0.84rem; }
    .head { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
    .head__total { font-weight: 650; font-size: 0.95rem; }
    .tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.6rem; }
    @media (max-width: 900px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    .tile {
      display: flex; flex-direction: column; gap: 0.1rem; text-align: left;
      padding: 0.6rem 0.7rem; border-radius: 8px; border: 1px solid var(--p-surface-200);
      border-left: 4px solid var(--p-surface-400);
      background: var(--p-surface-0); color: inherit; font: inherit; cursor: pointer;
    }
    .tile:hover:not(:disabled) { background: var(--p-surface-50); }
    .tile:disabled { cursor: default; }
    .tile__label { font-size: 0.74rem; font-weight: 600; }
    .tile__value { font-size: 1.4rem; font-weight: 700; }
    .tile--resolved, .tile--chosen { border-left-color: #2e8b57; }
    .tile--several, .tile--one_unconfirmed { border-left-color: #d99a00; }
    .tile--none, .tile--chosen_none { border-left-color: #c0392b; }
    .fresh { margin: 0; font-size: 0.78rem; }
    .fresh--warn { color: #7a5300; }
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
    .link {
      border: 0; background: none; padding: 0; font: inherit; font-weight: inherit; cursor: pointer;
      color: var(--p-primary-color, #2563eb); text-decoration: underline;
    }
    .pill { margin-left: 0.3rem; padding: 0.05rem 0.4rem; border-radius: 999px; background: var(--p-surface-100); }
    .note { margin: 0; font-size: 0.74rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class MatchResults {
  private readonly api = inject(Api);

  /** The Vehicles filter, exactly as the car list is queried with it. */
  readonly conditions = input.required<VehicleCondition[]>();
  readonly text = input.required<string>();
  /** A car opened from a list: its NOR ID. */
  readonly pick = output<string>();

  protected readonly states = MATCH_RESULT_STATES;
  protected readonly carsOpen = signal(false);
  protected readonly carsState = signal<MatchResultState>('several');
  protected readonly carsNarrowing = signal<MatchResultNarrowing>({});

  private readonly reloads = new Subject<void>();
  private readonly state = signal<OverviewState>({ loading: true, error: null, overview: null });
  protected readonly overview = computed(() => this.state().overview);
  protected readonly loading = computed(() => this.state().loading);
  protected readonly error = computed(() => this.state().error);

  constructor() {
    const filter = computed(() => ({ conditions: this.conditions(), text: this.text() }));
    // The filter changing, or Refresh, reads the overview again; the numbers on
    // screen stay until the new ones arrive.
    combineLatest([toObservable(filter), this.reloads.pipe(startWith(undefined))])
      .pipe(
        switchMap(([request]) =>
          this.api.matchResultOverview(request).pipe(
            map((overview): OverviewState => ({ loading: false, error: null, overview })),
            catchError(() =>
              of<OverviewState>({
                loading: false,
                error: 'Could not load the matching results.',
                overview: this.state().overview,
              }),
            ),
            startWith<OverviewState>({
              loading: true,
              error: null,
              overview: this.state().overview,
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((next) => this.state.set(next));
  }

  protected reload(): void {
    this.reloads.next();
  }

  protected countOf(overview: MatchResultOverview, state: MatchResultState): number {
    return overview.states.find((item) => item.state === state)?.cars ?? 0;
  }

  /**
   * A state's share of the cars that have a result, so a fill that is still
   * running reads as it will when it is done; `not_evaluated` is a share of all.
   */
  protected shareOf(overview: MatchResultOverview, state: MatchResultState): number | null {
    const pending = this.countOf(overview, 'not_evaluated');
    const base = state === 'not_evaluated' ? overview.total : overview.total - pending;
    return base > 0 ? this.countOf(overview, state) / base : null;
  }

  protected lacks(overview: MatchResultOverview, field: string): number {
    return overview.several_missing_fields.find((item) => item.field === field)?.cars ?? 0;
  }

  protected openCars(state: MatchResultState, narrowing: MatchResultNarrowing = {}): void {
    this.carsNarrowing.set(narrowing);
    this.carsState.set(state);
    this.carsOpen.set(true);
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
