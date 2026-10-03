import { DecimalPipe } from '@angular/common';
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
import type { MatchBucket, MatchCarRow, MatchSummaryJob, NorVehicleRecord } from '../core/models';
import { KTypeCandidates } from './ktype-candidates';
import { VehicleFacts } from './vehicle-facts';

/** The picked car's own record: what the car is, beside what it could be matched to. */
interface RecordState {
  loading: boolean;
  error: string | null;
  record: NorVehicleRecord | null;
}

const NO_RECORD: RecordState = { loading: false, error: null, record: null };

const PAGE = 100;

const BUCKETS: ReadonlyArray<{ key: MatchBucket; label: string }> = [
  { key: 'one', label: 'One KType' },
  { key: 'several', label: 'Several KTypes' },
  { key: 'none', label: 'No KType' },
  { key: 'not_matchable', label: 'Not matchable' },
];

/**
 * The cars behind a Matching run's counts, one bucket at a time, beside the car
 * picked from the list: the car's own information, which KTypes it could be, and
 * how each one lost.
 *
 * Buckets switch in place, so one, several and none read on the same screen.
 */
@Component({
  selector: 'ns-match-cars-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DecimalPipe, ButtonModule, DialogModule, KTypeCandidates, VehicleFacts],
  template: `
    <p-dialog
      header="Matched cars"
      [visible]="visible()"
      (visibleChange)="visible.set($event)"
      [modal]="true"
      [style]="{ width: '1200px', maxWidth: '96vw' }"
      [dismissableMask]="true"
    >
      <div class="tabs" role="tablist" aria-label="Bucket">
        @for (item of buckets; track item.key) {
          <button
            type="button"
            role="tab"
            class="tab tab--{{ item.key }}"
            [class.tab--on]="bucket() === item.key"
            [attr.aria-selected]="bucket() === item.key"
            (click)="bucket.set(item.key)"
          >
            {{ item.label }}
            <span class="tab__count">{{ job().summary.buckets[item.key] | number }}</span>
          </button>
        }
      </div>

      <div class="body">
        <section class="list" aria-label="Cars">
          <p class="muted note">
            {{ cars().length | number }} of {{ total() | number }} shown
            @if (behind() > 0) {
              · {{ behind() | number }} more evaluated since
              <p-button label="Refresh" size="small" [text]="true" (onClick)="load(true)" />
            } @else if (job().status === 'running') {
              · still running
            }
          </p>
          @if (error(); as text) {
            <p class="error" role="alert">{{ text }}</p>
          }
          <table>
            <colgroup>
              <col class="c-plate" /><col class="c-car" /><col class="c-num" />
              <col class="c-outcome" /><col />
            </colgroup>
            <thead>
              <tr>
                <th>Plate</th>
                <th>Car</th>
                <th class="num">KTypes</th>
                <th>Outcome</th>
                <th>{{ gapHeading() }}</th>
              </tr>
            </thead>
            <tbody>
              @for (car of cars(); track rowKey(car)) {
                <tr
                  class="row"
                  [class.row--on]="selected()?.vehicle_id === car.vehicle_id"
                  tabindex="0"
                  (click)="selected.set(car)"
                  (keydown.enter)="selected.set(car)"
                >
                  <td class="mono">{{ car.plate ?? car.vehicle_id }}</td>
                  <td>{{ car.manufacturer }} {{ car.model_family }}</td>
                  <td class="num">{{ car.candidates }}</td>
                  <td>{{ car.terminal.replace('_', ' ') }}</td>
                  <td class="gap">{{ gapOf(car) }}</td>
                </tr>
              } @empty {
                @if (!loading()) {
                  <tr><td colspan="5" class="muted">No cars in this bucket.</td></tr>
                }
              }
            </tbody>
          </table>
          @if (loading()) {
            <p class="muted">Loading…</p>
          } @else if (cars().length < total()) {
            <p-button label="Load more" size="small" [outlined]="true" (onClick)="load(false)" />
          }
        </section>

        <section class="detail" aria-label="Selected car">
          @if (selected(); as car) {
            <div class="detail__head">
              <span class="mono">{{ car.plate ?? car.vehicle_id }}</span>
              <span>{{ car.manufacturer }} {{ car.model_family }}</span>
              @if (car.vehicle_id) {
                <p-button label="Open car" size="small" [text]="true" (onClick)="openCar(car)" />
              }
            </div>
            @if (car.vehicle_id) {
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
              <ns-ktype-candidates [vehicleId]="car.vehicle_id" (choiceChanged)="refreshRecord()" />
            } @else {
              <p class="muted">This car has no NorthStar vehicle to match on its own.</p>
            }
          } @else {
            <p class="muted">Pick a car to see its information, its KTypes, what each is missing and how it lost.</p>
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
    .tab--on { font-weight: 650; border-width: 2px; }
    .tab--one.tab--on { border-color: #2e8b57; }
    .tab--several.tab--on { border-color: #d99a00; }
    .tab--none.tab--on { border-color: #c0392b; }
    .tab--not_matchable.tab--on { border-color: var(--p-surface-500); }
    .tab__count { color: var(--p-text-muted-color); }
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
    .c-car { width: 22%; }
    .c-num { width: 3.6rem; }
    .c-outcome { width: 7.4rem; }
    td { overflow-wrap: break-word; }
    th { text-align: left; font-weight: 600; color: var(--p-text-muted-color); font-size: 0.74rem; }
    td, th { padding: 0.25rem 0.35rem; border-bottom: 1px solid var(--p-surface-100); vertical-align: top; }
    .row { cursor: pointer; }
    .row:hover, .row:focus-visible { background: var(--p-surface-50); outline: none; }
    .row--on { background: var(--p-surface-100); }
    .num { text-align: right; }
    .gap { color: #7a5300; }
    .note { margin: 0; font-size: 0.74rem; display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; }
    .muted { color: var(--p-text-muted-color); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .error { margin: 0; color: #8a2020; }
  `,
})
export class MatchCarsDialog {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  /** The run whose cars are listed; its counts label the bucket tabs. */
  readonly job = input.required<MatchSummaryJob>();
  readonly visible = model(false);
  readonly bucket = model<MatchBucket>('several');
  /** "Open car": the NOR ID, for the Vehicles record panel. */
  readonly pick = output<string>();

  protected readonly buckets = BUCKETS;
  protected readonly cars = signal<MatchCarRow[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly selected = signal<MatchCarRow | null>(null);
  /** The picked car's record, so its information reads here without opening the car. */
  protected readonly record = signal<RecordState>(NO_RECORD);
  /** Bumped when the picked car was corrected or decided here: its record is read again. */
  private readonly recordTurn = signal(0);
  private readonly recordAsk = computed(() => ({
    id: this.selected()?.vehicle_id ?? null,
    turn: this.recordTurn(),
  }));
  private request: Subscription | null = null;
  /** The job object is replaced on every poll; only a different run should reload. */
  private readonly jobId = computed(() => this.job().job_id);

  /** Cars of this bucket the run has evaluated since the list was loaded. */
  protected readonly behind = computed(() =>
    this.loading() ? 0 : this.job().summary.buckets[this.bucket()] - this.total(),
  );

  protected readonly gapHeading = computed(() => {
    switch (this.bucket()) {
      case 'several': return 'What would decide';
      case 'none': return 'Conflicts on';
      case 'not_matchable': return 'Why not matched';
      default: return 'Why';
    }
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
          // Reading the same car again keeps what is shown until the new record arrives.
          return this.record().record?.vehicle_id === id
            ? read
            : read.pipe(startWith<RecordState>({ loading: true, error: null, record: null }));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((state) => this.record.set(state));
    // Opening, switching bucket or switching run starts the list over.
    effect(() => {
      const open = this.visible();
      const bucket = this.bucket();
      const jobId = this.jobId();
      if (open && bucket && jobId) untracked(() => this.load(true));
    });
  }

  load(reset: boolean): void {
    const offset = reset ? 0 : this.cars().length;
    if (reset) {
      this.cars.set([]);
      this.total.set(0);
      this.selected.set(null);
    }
    this.error.set(null);
    this.loading.set(true);
    this.request?.unsubscribe();
    this.request = this.api
      .matchSummaryCars(this.job().job_id, this.bucket(), offset, PAGE)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (page) => {
          this.cars.update((shown) => (reset ? page.cars : [...shown, ...page.cars]));
          this.total.set(page.total);
          this.loading.set(false);
          if (reset && page.cars.length && !this.selected()) this.selected.set(page.cars[0]);
        },
        error: (err: { status?: number }) => {
          this.loading.set(false);
          this.error.set(
            err?.status === 404
              ? 'The API no longer holds this run (it restarted). Run matching again.'
              : 'Could not load the cars.',
          );
        },
      });
  }

  protected rowKey(car: MatchCarRow): string {
    return car.vehicle_id ?? String(car.source_record_id);
  }

  /** The short answer per row: what stands between this car and one KType. */
  protected gapOf(car: MatchCarRow): string {
    if (car.bucket === 'several') {
      if (car.missing_fields.length) return `car has no ${car.missing_fields.map(fieldName).join(', ')}`;
      if (car.separating_fields.length) return `differ on ${car.separating_fields.map(fieldName).join(', ')}`;
      return car.verdict ?? '';
    }
    if (car.bucket === 'none') {
      return car.conflicting_fields.length
        ? car.conflicting_fields.map(fieldName).join(', ')
        : 'no candidate above threshold';
    }
    if (car.bucket === 'not_matchable') {
      return car.reason_codes.map((code) => describeReason(code) ?? code).join(' ');
    }
    return car.terminal === 'resolved' ? '' : (car.verdict ?? '');
  }

  protected openCar(car: MatchCarRow): void {
    if (car.vehicle_id) this.pick.emit(car.vehicle_id);
  }

  /** A choice or a correction was saved for the picked car: its values may have changed. */
  protected refreshRecord(): void {
    this.recordTurn.update((turn) => turn + 1);
  }
}
