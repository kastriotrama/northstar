import { DatePipe, DecimalPipe, PercentPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { Subscription, catchError, map, of, startWith, switchMap } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';

import { Api } from '../core/api';
import { describeReason, fieldName } from '../core/match-reasons';
import { MATCH_RESULT_STATES } from '../core/match-result-states';
import type {
  MatchResultCar,
  MatchResultNarrowing,
  MatchResultOverview,
  MatchResultState,
  NorVehicleRecord,
  VehicleCondition,
} from '../core/models';
import { KTypeCandidates } from './ktype-candidates';
import { VehicleFacts } from './vehicle-facts';

interface RecordState {
  loading: boolean;
  error: string | null;
  record: NorVehicleRecord | null;
}

const NO_RECORD: RecordState = { loading: false, error: null, record: null };
const PAGE = 100;

/**
 * The cars behind one number of the stored matching overview, beside the car
 * picked from the list.
 *
 * The list is read from stored results: state, the accepted or possible KTypes
 * and the short reason. Only the picked car is matched live, by the candidates
 * panel, so it shows every candidate's values and the decision in full.
 */
@Component({
  selector: 'ns-match-result-cars-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe, PercentPipe, ButtonModule, DialogModule, KTypeCandidates, VehicleFacts],
  template: `
    <p-dialog
      header="Cars by matching result"
      [visible]="visible()"
      (visibleChange)="visible.set($event)"
      [modal]="true"
      [style]="{ width: '1240px', maxWidth: '96vw' }"
      [dismissableMask]="true"
    >
      <div class="tabs" role="tablist" aria-label="Matching result">
        @for (item of states; track item.key) {
          <button
            type="button"
            role="tab"
            class="tab tab--{{ item.key }}"
            [class.tab--on]="state() === item.key"
            [attr.aria-selected]="state() === item.key"
            [title]="item.hint"
            (click)="switchState(item.key)"
          >
            {{ item.label }}
            <span class="tab__count">{{ countOf(item.key) | number }}</span>
          </button>
        }
      </div>

      @if (narrowingLabel(); as label) {
        <p class="narrowed">
          Only cars where {{ label }}
          <p-button label="Show all" size="small" [text]="true" (onClick)="narrowing.set({})" />
        </p>
      }

      <div class="body">
        <section class="list" aria-label="Cars">
          <p class="muted note">{{ cars().length | number }} of {{ total() | number }} shown</p>
          @if (error(); as text) {
            <p class="error" role="alert">{{ text }}</p>
          }
          <table>
            <colgroup>
              <col class="c-plate" /><col class="c-car" /><col class="c-ktypes" /><col />
            </colgroup>
            <thead>
              <tr>
                <th>Plate</th>
                <th>Car</th>
                <th>{{ ktypeHeading() }}</th>
                <th>{{ gapHeading() }}</th>
              </tr>
            </thead>
            <tbody>
              @for (car of cars(); track car.vehicle_id) {
                <tr
                  class="row"
                  [class.row--on]="selected()?.vehicle_id === car.vehicle_id"
                  tabindex="0"
                  (click)="selected.set(car)"
                  (keydown.enter)="selected.set(car)"
                >
                  <td class="mono">{{ car.plate ?? car.vehicle_id }}</td>
                  <td>
                    {{ car.manufacturer }} {{ car.model_family }}
                    @if (car.production_year) {
                      <span class="muted">{{ car.production_year }}</span>
                    }
                  </td>
                  <td class="mono">
                    @for (entry of ktypesOf(car); track entry.ktype) {
                      <span class="ktype" [class.ktype--accepted]="entry.accepted">
                        {{ entry.ktype }}
                        @if (entry.confidence !== null) {
                          <span class="muted">{{ entry.confidence | percent: '1.0-0' }}</span>
                        }
                      </span>
                    } @empty {
                      <span class="muted">—</span>
                    }
                  </td>
                  <td class="gap">
                    {{ gapOf(car) }}
                    @if (car.changed_since_matched) {
                      <span class="stale" title="The car changed after it was matched">may be out of date</span>
                    }
                  </td>
                </tr>
              } @empty {
                @if (!loading()) {
                  <tr><td colspan="4" class="muted">No cars here.</td></tr>
                }
              }
            </tbody>
          </table>
          @if (loading()) {
            <p class="muted">Loading…</p>
          } @else if (nextAfter()) {
            <p-button label="Load more" size="small" [outlined]="true" (onClick)="load(false)" />
          }
        </section>

        <section class="detail" aria-label="Selected car">
          @if (selected(); as car) {
            <div class="detail__head">
              <span class="mono">{{ car.plate ?? car.vehicle_id }}</span>
              <span>{{ car.manufacturer }} {{ car.model_family }}</span>
              <p-button label="Open car" size="small" [text]="true" (onClick)="pick.emit(car.vehicle_id)" />
            </div>
            @if (car.evaluated_at) {
              <p class="muted note">
                Stored result from {{ car.evaluated_at | date: 'yyyy-MM-dd HH:mm' }}; below, the car
                as the matcher sees it now.
              </p>
            }
            <details class="info" open>
              <summary>Car information</summary>
              @if (record().record; as found) {
                <ns-vehicle-facts [record]="found" />
              } @else if (record().error; as text) {
                <p class="error" role="alert">{{ text }}</p>
              } @else if (record().loading) {
                <p class="muted">Loading…</p>
              }
            </details>
            <ns-ktype-candidates [vehicleId]="car.vehicle_id" (choiceChanged)="onChoice()" />
          } @else {
            <p class="muted">Pick a car to see its information, its KTypes and how each one fared.</p>
          }
        </section>
      </div>
    </p-dialog>
  `,
  styles: `
    .tabs { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.7rem; }
    .tab {
      display: flex; align-items: center; gap: 0.4rem; font: inherit; font-size: 0.8rem;
      padding: 0.3rem 0.7rem; border-radius: 999px; cursor: pointer;
      border: 1px solid var(--p-surface-300); background: var(--p-surface-0); color: inherit;
    }
    .tab--on { font-weight: 650; border-width: 2px; border-color: var(--p-surface-500); }
    .tab--resolved.tab--on, .tab--chosen.tab--on { border-color: #2e8b57; }
    .tab--several.tab--on, .tab--one_unconfirmed.tab--on { border-color: #d99a00; }
    .tab--none.tab--on, .tab--chosen_none.tab--on { border-color: #c0392b; }
    .tab__count { color: var(--p-text-muted-color); }
    .narrowed { margin: 0 0 0.6rem; font-size: 0.8rem; display: flex; align-items: center; gap: 0.3rem; }
    .body { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 1rem; }
    @media (max-width: 900px) { .body { grid-template-columns: 1fr; } }
    .list {
      display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.8rem; min-width: 0;
      max-height: 70vh; overflow: auto;
    }
    .detail {
      border-left: 1px solid var(--p-surface-200); padding-left: 1rem; min-width: 0;
      max-height: 70vh; overflow: auto;
    }
    @media (max-width: 900px) { .detail { border-left: 0; padding-left: 0; } }
    .detail__head { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 0.5rem; font-weight: 600; }
    .info { margin-bottom: 0.7rem; padding-bottom: 0.6rem; border-bottom: 1px solid var(--p-surface-200); }
    .info > summary { cursor: pointer; font-weight: 600; font-size: 0.85rem; margin-bottom: 0.35rem; }
    table { width: 100%; border-collapse: collapse; table-layout: fixed; }
    .c-plate { width: 5.5rem; }
    .c-car { width: 24%; }
    .c-ktypes { width: 30%; }
    td { overflow-wrap: break-word; }
    th { text-align: left; font-weight: 600; color: var(--p-text-muted-color); font-size: 0.74rem; }
    td, th { padding: 0.25rem 0.35rem; border-bottom: 1px solid var(--p-surface-100); vertical-align: top; }
    .row { cursor: pointer; }
    .row:hover, .row:focus-visible { background: var(--p-surface-50); outline: none; }
    .row--on { background: var(--p-surface-100); }
    .ktype { display: inline-block; margin: 0 0.4rem 0.1rem 0; white-space: nowrap; }
    .ktype--accepted { font-weight: 700; }
    .gap { color: #7a5300; }
    .stale { display: block; color: #8a2020; font-size: 0.72rem; }
    .note { margin: 0; font-size: 0.74rem; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class MatchResultCarsDialog {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  /** The Vehicles filter the overview was read with. */
  readonly conditions = input.required<VehicleCondition[]>();
  readonly text = input.required<string>();
  /** The overview on screen; its counts label the state tabs. */
  readonly overview = input.required<MatchResultOverview>();
  readonly visible = model(false);
  readonly state = model<MatchResultState>('several');
  /** One cause within the state, set by the overview row that was clicked. */
  readonly narrowing = model<MatchResultNarrowing>({});
  /** "Open car": the NOR ID, for the Vehicles record panel. */
  readonly pick = output<string>();
  /** A person decided a car here: the overview's counts may have moved. */
  readonly changed = output<void>();

  protected readonly states = MATCH_RESULT_STATES;
  protected readonly cars = signal<MatchResultCar[]>([]);
  protected readonly total = signal(0);
  protected readonly nextAfter = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly selected = signal<MatchResultCar | null>(null);
  protected readonly record = signal<RecordState>(NO_RECORD);
  private readonly recordTurn = signal(0);
  private readonly recordAsk = computed(() => ({
    id: this.selected()?.vehicle_id ?? null,
    turn: this.recordTurn(),
  }));
  private request: Subscription | null = null;

  protected readonly ktypeHeading = computed(() =>
    this.state() === 'resolved' || this.state() === 'chosen' ? 'KType' : 'Possible KTypes',
  );

  protected readonly gapHeading = computed(() => {
    switch (this.state()) {
      case 'several': return 'What would decide';
      case 'none': return 'Conflicts on';
      case 'not_matchable': return 'Why not matched';
      default: return 'Note';
    }
  });

  protected readonly narrowingLabel = computed(() => {
    const narrowing = this.narrowing();
    if (narrowing.missing_field) return `the car has no ${fieldName(narrowing.missing_field)}`;
    if (narrowing.separating_field) {
      return `the KTypes differ on ${fieldName(narrowing.separating_field)}`;
    }
    if (narrowing.conflicting_field) {
      return `the car conflicts on ${fieldName(narrowing.conflicting_field)}`;
    }
    if (narrowing.reason) return `the reason is ${narrowing.reason}`;
    if (narrowing.ktype) return `KType ${narrowing.ktype} is accepted or possible`;
    if (narrowing.candidate_count !== undefined) {
      return `there are ${narrowing.candidate_count} possible KTypes`;
    }
    return null;
  });

  constructor() {
    this.destroyRef.onDestroy(() => this.request?.unsubscribe());
    toObservable(this.recordAsk)
      .pipe(
        switchMap(({ id }) => {
          if (id === null) return of(NO_RECORD);
          const read = this.api.vehicleRecord(id).pipe(
            map((record): RecordState => ({ loading: false, error: null, record })),
            catchError(() =>
              of<RecordState>({
                loading: false,
                error: "Could not load this car's information.",
                record: null,
              }),
            ),
          );
          return this.record().record?.vehicle_id === id
            ? read
            : read.pipe(startWith<RecordState>({ loading: true, error: null, record: null }));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((state) => this.record.set(state));
    // Opening, switching state or narrowing starts the list over.
    effect(() => {
      const open = this.visible();
      this.state();
      this.narrowing();
      if (open) untracked(() => this.load(true));
    });
  }

  protected countOf(state: MatchResultState): number {
    return this.overview().states.find((item) => item.state === state)?.cars ?? 0;
  }

  /** A tab shows the whole state: a narrowing belongs to the state it was set for. */
  protected switchState(state: MatchResultState): void {
    if (state === this.state()) return;
    this.narrowing.set({});
    this.state.set(state);
  }

  load(reset: boolean): void {
    if (reset) {
      this.cars.set([]);
      this.total.set(0);
      this.nextAfter.set(null);
      this.selected.set(null);
    }
    this.error.set(null);
    this.loading.set(true);
    this.request?.unsubscribe();
    this.request = this.api
      .matchResultCars({
        conditions: this.conditions(),
        text: this.text(),
        state: this.state(),
        ...this.narrowing(),
        after: reset ? null : this.nextAfter(),
        limit: PAGE,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          this.cars.update((shown) => (reset ? page.cars : [...shown, ...page.cars]));
          this.total.set(page.total);
          this.nextAfter.set(page.next_after);
          this.loading.set(false);
          if (reset && page.cars.length && !this.selected()) this.selected.set(page.cars[0]);
        },
        error: () => {
          this.loading.set(false);
          this.error.set('Could not load the cars.');
        },
      });
  }

  /** The KTypes a row shows: the one in force first, then the other possible ones. */
  protected ktypesOf(
    car: MatchResultCar,
  ): Array<{ ktype: string; confidence: number | null; accepted: boolean }> {
    const confidences = new Map(
      car.candidate_ktypes.map((ktype, index) => [ktype, car.candidate_confidences[index] ?? null]),
    );
    if (car.ktype) {
      return [{ ktype: car.ktype, confidence: confidences.get(car.ktype) ?? null, accepted: true }];
    }
    if (car.candidate_ktypes.length) {
      return car.candidate_ktypes.map((ktype) => ({
        ktype,
        confidence: confidences.get(ktype) ?? null,
        accepted: false,
      }));
    }
    return car.best_candidate_ktype
      ? [{ ktype: car.best_candidate_ktype, confidence: car.confidence, accepted: false }]
      : [];
  }

  /** The short answer per row: what stands between this car and one KType. */
  protected gapOf(car: MatchResultCar): string {
    const state = car.automatic_state ?? car.state;
    if (car.state === 'chosen') return 'chosen by a person';
    if (car.state === 'chosen_none') return 'a person said none of these';
    if (car.state === 'not_evaluated') return 'not matched yet';
    if (state === 'several') {
      if (car.missing_fields.length) {
        return `car has no ${car.missing_fields.map(fieldName).join(', ')}`;
      }
      if (car.separating_fields.length) {
        return `differ on ${car.separating_fields.map(fieldName).join(', ')}`;
      }
      return 'identical on every compared field';
    }
    if (state === 'none') {
      return car.conflicting_fields.length
        ? car.conflicting_fields.map(fieldName).join(', ')
        : 'no candidate above threshold';
    }
    if (state === 'not_matchable') {
      return car.reason_codes.map((code) => describeReason(code) ?? code).join(' ');
    }
    if (state === 'one_unconfirmed') {
      return (car.terminal ?? '').replace(/_/g, ' ');
    }
    return car.candidate_ktypes.length > 1
      ? `${car.candidate_ktypes.length} KTypes fit; the matcher accepted this one`
      : '';
  }

  /** A choice or a correction was saved for the picked car. */
  protected onChoice(): void {
    this.recordTurn.update((turn) => turn + 1);
    this.changed.emit();
  }
}
