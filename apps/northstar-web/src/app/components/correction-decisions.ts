import { DatePipe, DecimalPipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { Api } from '../core/api';
import type { CorrectionDecisionSummary, CorrectionWithdrawRequest } from '../core/models';

/** The name a person typed anywhere on a review screen; shared so it is typed once. */
const REVIEWER_STORAGE_KEY = 'match-review-reviewer';

/** The undo a person is confirming, or one that failed and can be sent again. */
interface PendingUndo {
  decisionId: string;
  /** Minted once; "Try again" resends the same body. */
  body: CorrectionWithdrawRequest | null;
}

function count(measurement: Record<string, unknown>, key: string): number {
  const value = measurement[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/**
 * Every correction that was applied to several cars at once: what was set, for which
 * cars, by whom and why, what its check found, and where it stands. An applied decision
 * can be undone here as one, without first finding a car that belongs to it.
 */
@Component({
  selector: 'ns-correction-decisions',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, DecimalPipe],
  template: `
    <div class="head">
      <p class="muted">
        Corrections applied to several cars at once. Each can be undone as one; a car a person
        has changed since is left as it is.
      </p>
      <button type="button" [disabled]="loading()" (click)="load()">Refresh</button>
    </div>

    <label class="name">
      Your name
      <input
        type="text"
        maxlength="120"
        autocomplete="name"
        [value]="reviewer()"
        (input)="onReviewer($event)"
      />
    </label>
    @if (!named()) {
      <p class="muted hint" id="decisions-name-hint">Enter your name to undo a decision.</p>
    }

    @if (notice(); as text) {
      <p class="saved" role="status">{{ text }}</p>
    }
    @if (loadError()) {
      <p class="error" role="alert">
        The decisions could not be loaded.
        <button type="button" (click)="load()">Try again</button>
      </p>
    } @else if (loading() && !decisions().length) {
      <p class="muted" role="status">Loading…</p>
    } @else if (!decisions().length) {
      <p class="muted">No correction has been applied to several cars yet.</p>
    }

    @for (decision of decisions(); track decision.decision_id) {
      <article class="decision" [class.decision--off]="decision.status !== 'applied'">
        <h4>
          {{ what(decision) }}
          <span class="state state--{{ decision.status }}">{{ state(decision) }}</span>
        </h4>
        <p>{{ decision.scope_label }}</p>
        <p class="muted">
          {{ decision.reviewer }}, {{ decision.created_at | date: 'yyyy-MM-dd HH:mm' }}
          @if (decision.reason) {
            — “{{ decision.reason }}”
          }
        </p>
        <p class="muted">{{ found(decision) }}</p>
        @if (undone(decision); as last) {
          <p class="muted">
            Undone by {{ last.reviewer }} on {{ last.created_at | date: 'yyyy-MM-dd HH:mm' }}
            @if (last.reason) {
              — “{{ last.reason }}”
            }
          </p>
        }

        @if (decision.status === 'applied') {
          @if (pending()?.decisionId === decision.decision_id) {
            <div class="confirm" role="group" [attr.aria-label]="'Confirm undoing: ' + what(decision)">
              <p>
                Undo this for all {{ decision.member_count | number }} cars? Each goes back to its
                own data. A car a person has changed since is left as it is.
              </p>
              <label>
                Reason
                <input
                  type="text"
                  maxlength="1000"
                  [value]="reason()"
                  (input)="onReason($event)"
                  [attr.aria-describedby]="reason().trim() ? null : 'decisions-reason-hint'"
                />
              </label>
              @if (!reason().trim()) {
                <p class="muted hint" id="decisions-reason-hint">Give a reason to undo this for several cars.</p>
              }
              @if (undoError(); as failure) {
                <p class="error" role="alert">
                  {{ failure.message }}
                  @if (failure.retry) {
                    <button type="button" [disabled]="saving()" (click)="send()">Try again</button>
                  }
                </p>
              }
              <div class="row">
                <button
                  type="button"
                  [disabled]="saving() || !named() || !reason().trim()"
                  [attr.aria-busy]="saving()"
                  (click)="confirm()"
                >
                  Confirm
                </button>
                <button type="button" [disabled]="saving()" (click)="cancel()">Cancel</button>
              </div>
            </div>
          } @else {
            <button
              type="button"
              class="undo"
              [disabled]="!named() || saving()"
              [attr.aria-describedby]="named() ? null : 'decisions-name-hint'"
              [attr.aria-label]="'Undo for all ' + decision.member_count + ' cars: ' + what(decision)"
              (click)="ask(decision)"
            >
              Undo for all {{ decision.member_count | number }} cars…
            </button>
          }
        }
      </article>
    }
  `,
  styles: `
    :host { display: flex; flex-direction: column; gap: 0.6rem; font-size: 0.85rem; }
    p, h4 { margin: 0; }
    .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; }
    .name, .confirm label { display: flex; flex-direction: column; gap: 0.15rem; font-weight: 600; max-width: 22rem; }
    input { font: inherit; font-weight: 400; padding: 0.2rem 0.35rem; }
    button { font: inherit; cursor: pointer; }
    button:disabled { cursor: not-allowed; }
    .decision {
      display: flex; flex-direction: column; gap: 0.25rem; align-items: flex-start;
      padding: 0.6rem 0.75rem; border: 1px solid var(--p-surface-200); border-radius: 8px;
    }
    .decision--off { background: var(--p-surface-50); }
    h4 { font-size: 0.92rem; display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
    .state { font-size: 0.7rem; font-weight: 600; padding: 0.05rem 0.45rem; border-radius: 999px; }
    .state--applied { background: #e3f3e8; color: #1c5a2e; }
    .state--withdrawn { background: var(--p-surface-200); color: var(--p-text-muted-color); }
    .state--proposed { background: #fff3d6; color: #7a5300; }
    .confirm {
      display: flex; flex-direction: column; gap: 0.35rem; align-self: stretch;
      padding: 0.5rem 0.6rem; border-left: 3px solid #d99a00; background: #fffaf0;
    }
    .row { display: flex; gap: 0.5rem; }
    .hint { font-size: 0.75rem; }
    .muted { color: var(--p-text-muted-color); }
    .error { color: #8a2020; }
    .saved { color: #1c5a2e; }
  `,
})
export class CorrectionDecisions {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  /** A decision was undone: cars shown elsewhere on the page may have changed. */
  readonly changed = output<void>();

  protected readonly decisions = signal<CorrectionDecisionSummary[]>([]);
  protected readonly loading = signal(false);
  protected readonly loadError = signal(false);
  protected readonly reviewer = signal(this.storedReviewer());
  protected readonly reason = signal('');
  protected readonly pending = signal<PendingUndo | null>(null);
  protected readonly saving = signal(false);
  protected readonly undoError = signal<{ message: string; retry: boolean } | null>(null);
  protected readonly notice = signal<string | null>(null);
  protected readonly named = computed(() => this.reviewer().trim().length > 0);

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.loadError.set(false);
    this.api
      .correctionDecisions()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (list) => {
          this.decisions.set(list.decisions);
          this.loading.set(false);
        },
        error: () => {
          this.loading.set(false);
          this.loadError.set(true);
        },
      });
  }

  /** "Engine code set to DFGA" / "Engine code marked as wrong". */
  protected what(decision: CorrectionDecisionSummary): string {
    const label = decision.field_label || decision.field.replace(/_/g, ' ');
    if (decision.action === 'ignore') return `${label} marked as wrong`;
    return `${label} set to ${this.words(decision.value)}`;
  }

  protected state(decision: CorrectionDecisionSummary): string {
    const cars = decision.member_count.toLocaleString('en-US');
    if (decision.status === 'applied') return `Applied to ${cars} ${decision.member_count === 1 ? 'car' : 'cars'}`;
    if (decision.status === 'withdrawn') return 'Undone';
    return 'Proposal · no car changed';
  }

  /** What the check behind the decision found, in one sentence. */
  protected found(decision: CorrectionDecisionSummary): string {
    const measured = decision.measurement;
    const checked = count(measured, 'checked');
    const parts = [`${count(measured, 'gained').toLocaleString('en-US')} would resolve`];
    const harmed = count(measured, 'lost') + count(measured, 'moved') + count(measured, 'worse');
    parts.push(
      harmed
        ? `${harmed.toLocaleString('en-US')} would lose or change their match or get harder to match`
        : 'none would lose or change its match',
    );
    return `The check looked at ${checked.toLocaleString('en-US')} ${checked === 1 ? 'car' : 'cars'}: ${parts.join(', ')}.`;
  }

  /** The event that undid the decision, when it was undone. */
  protected undone(decision: CorrectionDecisionSummary) {
    if (decision.status !== 'withdrawn') return null;
    return [...decision.events].reverse().find((event) => event.event === 'withdraw') ?? null;
  }

  protected ask(decision: CorrectionDecisionSummary): void {
    this.reason.set('');
    this.undoError.set(null);
    this.notice.set(null);
    this.pending.set({ decisionId: decision.decision_id, body: null });
  }

  protected cancel(): void {
    this.pending.set(null);
    this.undoError.set(null);
  }

  /** A new undo, hence a new operation id; "Try again" goes through `send` with the same one. */
  protected confirm(): void {
    const waiting = this.pending();
    const reviewer = this.reviewer().trim();
    const reason = this.reason().trim();
    if (!waiting || !reviewer || !reason) return;
    this.pending.set({
      decisionId: waiting.decisionId,
      body: { operation_id: crypto.randomUUID(), reviewer, reason },
    });
    this.send();
  }

  protected send(): void {
    const waiting = this.pending();
    if (!waiting?.body || this.saving()) return;
    this.saving.set(true);
    this.undoError.set(null);
    this.api
      .withdrawCorrectionDecision(waiting.decisionId, waiting.body)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.saving.set(false);
          this.pending.set(null);
          this.storeReviewer(waiting.body?.reviewer ?? '');
          const left = result.left_changed
            ? ` ${result.left_changed.toLocaleString('en-US')} ${result.left_changed === 1 ? 'was' : 'were'} changed by a person since and ${result.left_changed === 1 ? 'was' : 'were'} left as ${result.left_changed === 1 ? 'it is' : 'they are'}.`
            : '';
          this.notice.set(
            `Undone for ${result.withdrawn.toLocaleString('en-US')} ${result.withdrawn === 1 ? 'car' : 'cars'}.${left}`,
          );
          this.changed.emit();
          this.load();
        },
        error: (err: { status?: number; error?: { detail?: unknown } }) => {
          this.saving.set(false);
          const status = err?.status ?? 0;
          const detail = err?.error?.detail;
          const code =
            detail && typeof detail === 'object' && 'code' in detail ? String(detail.code) : null;
          if (status === 0 || status >= 500) {
            this.undoError.set({
              message:
                code === 'vehicles_busy'
                  ? 'Not undone. Someone else is changing some of these cars right now. Try again.'
                  : 'Not undone. Try again.',
              retry: true,
            });
          } else if (code === 'decision_changed' || code === 'nothing_to_withdraw') {
            this.pending.set(null);
            this.notice.set('This decision was already undone. The list was read again.');
            this.load();
          } else {
            this.undoError.set({ message: 'Not undone. The server did not accept this request.', retry: false });
          }
        },
      });
  }

  protected onReviewer(event: Event): void {
    this.reviewer.set((event.target as HTMLInputElement).value);
  }

  protected onReason(event: Event): void {
    this.reason.set((event.target as HTMLInputElement).value);
  }

  /** A stored value in words: "plug_in_hybrid" reads "plug in hybrid", a list with commas. */
  private words(value: string | null): string {
    if (value === null || value === '') return '—';
    return value
      .split(',')
      .map((part) => (/^[a-z_]+$/.test(part) ? part.replace(/_/g, ' ') : part))
      .join(', ');
  }

  private storedReviewer(): string {
    try {
      return localStorage.getItem(REVIEWER_STORAGE_KEY) ?? '';
    } catch {
      return '';
    }
  }

  private storeReviewer(name: string): void {
    try {
      if (name) localStorage.setItem(REVIEWER_STORAGE_KEY, name);
    } catch {
      // A blocked storage only costs retyping the name.
    }
  }
}
